import re
import sys
import copy
import types
import inspect
import keyword
import itertools
import annotationlib
import abc
from reprlib import recursive_repr


__all__ = ['dataclass',
           'field',
           'Field',
           'FrozenInstanceError',
           'InitVar',
           'KW_ONLY',
           'MISSING',

           # Helper functions.
           'fields',
           'asdict',
           'astuple',
           'make_dataclass',
           'replace',
           'is_dataclass',
           ]

# Conditions for adding methods.  The boxes indicate what action the
# dataclass decorator takes.  For all of these tables, when I talk
# about init=, repr=, eq=, order=, unsafe_hash=, or frozen=, I'm
# referring to the arguments to the @dataclass decorator.  When
# checking if a dunder method already exists, I mean check for an
# entry in the class's __dict__.  I never check to see if an attribute
# is defined in a base class.

# Key:
# +=========+=========================================+
# + Value   | Meaning                                 |
# +=========+=========================================+
# | <blank> | No action: no method is added.          |
# +---------+-----------------------------------------+
# | add     | Generated method is added.              |
# +---------+-----------------------------------------+
# | raise   | TypeError is raised.                    |
# +---------+-----------------------------------------+
# | None    | Attribute is set to None.               |
# +=========+=========================================+

# __init__
#
#   +--- init= parameter
#   |
#   v     |       |       |
#         |  no   |  yes  |  <--- class has __init__ in __dict__?
# +=======+=======+=======+
# | False |       |       |
# +-------+-------+-------+
# | True  | add   |       |  <- the default
# +=======+=======+=======+

# __repr__
#
#    +--- repr= parameter
#    |
#    v    |       |       |
#         |  no   |  yes  |  <--- class has __repr__ in __dict__?
# +=======+=======+=======+
# | False |       |       |
# +-------+-------+-------+
# | True  | add   |       |  <- the default
# +=======+=======+=======+


# __setattr__
# __delattr__
#
#    +--- frozen= parameter
#    |
#    v    |       |       |
#         |  no   |  yes  |  <--- class has __setattr__ or __delattr__ in __dict__?
# +=======+=======+=======+
# | False |       |       |  <- the default
# +-------+-------+-------+
# | True  | add   | raise |
# +=======+=======+=======+
# Raise because not adding these methods would break the "frozen-ness"
# of the class.

# __eq__
#
#    +--- eq= parameter
#    |
#    v    |       |       |
#         |  no   |  yes  |  <--- class has __eq__ in __dict__?
# +=======+=======+=======+
# | False |       |       |
# +-------+-------+-------+
# | True  | add   |       |  <- the default
# +=======+=======+=======+

# __lt__
# __le__
# __gt__
# __ge__
#
#    +--- order= parameter
#    |
#    v    |       |       |
#         |  no   |  yes  |  <--- class has any comparison method in __dict__?
# +=======+=======+=======+
# | False |       |       |  <- the default
# +-------+-------+-------+
# | True  | add   | raise |
# +=======+=======+=======+
# Raise because to allow this case would interfere with using
# functools.total_ordering.

# __hash__

#    +------------------- unsafe_hash= parameter
#    |       +----------- eq= parameter
#    |       |       +--- frozen= parameter
#    |       |       |
#    v       v       v    |        |        |
#                         |   no   |  yes   |  <--- class has explicitly defined __hash__
# +=======+=======+=======+========+========+
# | False | False | False |        |        | No __eq__, use the base class __hash__
# +-------+-------+-------+--------+--------+
# | False | False | True  |        |        | No __eq__, use the base class __hash__
# +-------+-------+-------+--------+--------+
# | False | True  | False | None   |        | <-- the default, not hashable
# +-------+-------+-------+--------+--------+
# | False | True  | True  | add    |        | Frozen, so hashable, allows override
# +-------+-------+-------+--------+--------+
# | True  | False | False | add    | raise  | Has no __eq__, but hashable
# +-------+-------+-------+--------+--------+
# | True  | False | True  | add    | raise  | Has no __eq__, but hashable
# +-------+-------+-------+--------+--------+
# | True  | True  | False | add    | raise  | Not frozen, but hashable
# +-------+-------+-------+--------+--------+
# | True  | True  | True  | add    | raise  | Frozen, so hashable
# +=======+=======+=======+========+========+
# For boxes that are blank, __hash__ is untouched and therefore
# inherited from the base class.  If the base is object, then
# id-based hashing is used.
#
# Note that a class may already have __hash__=None if it specified an
# __eq__ method in the class body (not one that was created by
# @dataclass).
#
# See _hash_action (below) for a coded version of this table.

# __match_args__
#
#    +--- match_args= parameter
#    |
#    v    |       |       |
#         |  no   |  yes  |  <--- class has __match_args__ in __dict__?
# +=======+=======+=======+
# | False |       |       |
# +-------+-------+-------+
# | True  | add   |       |  <- the default
# +=======+=======+=======+
# __match_args__ is always added unless the class already defines it. It is a
# tuple of __init__ parameter names; non-init fields must be matched by keyword.


# Raised when an attempt is made to modify a frozen class.
class FrozenInstanceError(AttributeError): pass

# A sentinel object for default values to signal that a default
# factory will be used.  This is given a nice repr() which will appear
# in the function signature of dataclasses' constructors.
class _HAS_DEFAULT_FACTORY_CLASS:
    def __repr__(self):
        return '<factory>'
_HAS_DEFAULT_FACTORY = _HAS_DEFAULT_FACTORY_CLASS()

# A sentinel object to detect if a parameter is supplied or not.  Use
# a class to give it a better repr.
class _MISSING_TYPE:
    pass
MISSING = _MISSING_TYPE()

# A sentinel object to indicate that following fields are keyword-only by
# default.  Use a class to give it a better repr.
class _KW_ONLY_TYPE:
    pass
KW_ONLY = _KW_ONLY_TYPE()

# Since most per-field metadata will be unused, create an empty
# read-only proxy that can be shared among all fields.
_EMPTY_METADATA = types.MappingProxyType({})

# Markers for the various kinds of fields and pseudo-fields.
class _FIELD_BASE:
    def __init__(self, name):
        self.name = name
    def __repr__(self):
        return self.name
_FIELD = _FIELD_BASE('_FIELD')
_FIELD_CLASSVAR = _FIELD_BASE('_FIELD_CLASSVAR')
_FIELD_INITVAR = _FIELD_BASE('_FIELD_INITVAR')

# The name of an attribute on the class where we store the Field
# objects.  Also used to check if a class is a Data Class.
_FIELDS = '__dataclass_fields__'

# The name of an attribute on the class that stores the parameters to
# @dataclass.
_PARAMS = '__dataclass_params__'

# The name of the function, that if it exists, is called at the end of
# __init__.
_POST_INIT_NAME = '__post_init__'

# String regex that string annotations for ClassVar or InitVar must match.
# Allows "identifier.identifier[" or "identifier[".
# https://bugs.python.org/issue33453 for details.
_MODULE_IDENTIFIER_RE = re.compile(r'^(?:\s*(\w+)\s*\.)?\s*(\w+)')

# Atomic immutable types which don't require any recursive handling and for which deepcopy
# returns the same object. We can provide a fast-path for these types in asdict and astuple.
_ATOMIC_TYPES = frozenset({
    # Common JSON Serializable types
    types.NoneType,
    bool,
    int,
    float,
    str,
    # Other common types
    complex,
    bytes,
    # Other types that are also unaffected by deepcopy
    types.EllipsisType,
    types.NotImplementedType,
    types.CodeType,
    types.BuiltinFunctionType,
    types.FunctionType,
    type,
    range,
    property,
})

# Any marker is used in `make_dataclass` to mark unannotated fields as `Any`
# without importing `typing` module.
_ANY_MARKER = object()


class InitVar:
    __slots__ = ('type', )

    def __init__(self, type):
        self.type = type

    def __repr__(self):
        if isinstance(self.type, type):
            type_name = self.type.__name__
        else:
            # typing objects, e.g. List[int]
            type_name = repr(self.type)
        return f'dataclasses.InitVar[{type_name}]'

    def __class_getitem__(cls, type):
        return InitVar(type)

# Instances of Field are only ever created from within this module,
# and only from the field() function, although Field instances are
# exposed externally as (conceptually) read-only objects.
#
# name and type are filled in after the fact, not in __init__.
# They're not known at the time this class is instantiated, but it's
# convenient if they're available later.
#
# When cls._FIELDS is filled in with a list of Field objects, the name
# and type fields will have been populated.
class Field:
    __slots__ = ('name',
                 'type',
                 'default',
                 'default_factory',
                 'repr',
                 'hash',
                 'init',
                 'compare',
                 'metadata',
                 'kw_only',
                 'doc',
                 '_field_type',  # Private: not to be used by user code.
                 )

    def __init__(self, default, default_factory, init, repr, hash, compare,
                 metadata, kw_only, doc):
        self.name = None
        self.type = None
        self.default = default
        self.default_factory = default_factory
        self.init = init
        self.repr = repr
        self.hash = hash
        self.compare = compare
        self.metadata = (_EMPTY_METADATA
                         if metadata is None else
                         types.MappingProxyType(metadata))
        self.kw_only = kw_only
        self.doc = doc
        self._field_type = None

    @recursive_repr()
    def __repr__(self):
        return ('Field('
                f'name={self.name!r},'
                f'type={self.type!r},'
                f'default={self.default!r},'
                f'default_factory={self.default_factory!r},'
                f'init={self.init!r},'
                f'repr={self.repr!r},'
                f'hash={self.hash!r},'
                f'compare={self.compare!r},'
                f'metadata={self.metadata!r},'
                f'kw_only={self.kw_only!r},'
                f'doc={self.doc!r},'
                f'_field_type={self._field_type}'
                ')')

    # This is used to support the PEP 487 __set_name__ protocol in the
    # case where we're using a field that contains a descriptor as a
    # default value.  For details on __set_name__, see
    # https://peps.python.org/pep-0487/#implementation-details.
    #
    # Note that in _process_class, this Field object is overwritten
    # with the default value, so the end result is a descriptor that
    # had __set_name__ called on it at the right time.
    def __set_name__(self, owner, name):
        func = getattr(type(self.default), '__set_name__', None)
        if func:
            # There is a __set_name__ method on the descriptor, call
            # it.
            func(self.default, owner, name)

    __class_getitem__ = classmethod(types.GenericAlias)


class _DataclassParams:
    __slots__ = ('init',
                 'repr',
                 'eq',
                 'order',
                 'unsafe_hash',
                 'frozen',
                 'match_args',
                 'kw_only',
                 'slots',
                 'weakref_slot',
                 )

    def __init__(self,
                 init, repr, eq, order, unsafe_hash, frozen,
                 match_args, kw_only, slots, weakref_slot):
        self.init = init
        self.repr = repr
        self.eq = eq
        self.order = order
        self.unsafe_hash = unsafe_hash
        self.frozen = frozen
        self.match_args = match_args
        self.kw_only = kw_only
        self.slots = slots
        self.weakref_slot = weakref_slot

    def __repr__(self):
        return ('_DataclassParams('
                f'init={self.init!r},'
                f'repr={self.repr!r},'
                f'eq={self.eq!r},'
                f'order={self.order!r},'
                f'unsafe_hash={self.unsafe_hash!r},'
                f'frozen={self.frozen!r},'
                f'match_args={self.match_args!r},'
                f'kw_only={self.kw_only!r},'
                f'slots={self.slots!r},'
                f'weakref_slot={self.weakref_slot!r}'
                ')')


# This function is used instead of exposing Field creation directly,
# so that a type checker can be told (via overloads) that this is a
# function whose type depends on its parameters.
def field(*, default=MISSING, default_factory=MISSING, init=True, repr=True,
          hash=None, compare=True, metadata=None, kw_only=MISSING, doc=None):
    """Return an object to identify dataclass fields.

    default is the default value of the field.  default_factory is a
    0-argument function called to initialize a field's value.  If init
    is true, the field will be a parameter to the class's __init__()
    function.  If repr is true, the field will be included in the
    object's repr().  If hash is true, the field will be included in the
    object's hash().  If compare is true, the field will be used in
    comparison functions.  metadata, if specified, must be a mapping
    which is stored but not otherwise examined by dataclass.  If kw_only
    is true, the field will become a keyword-only parameter to
    __init__().  doc is an optional docstring for this field.

    It is an error to specify both default and default_factory.
    """

    if default is not MISSING and default_factory is not MISSING:
        raise ValueError('cannot specify both default and default_factory')
    return Field(default, default_factory, init, repr, hash, compare,
                 metadata, kw_only, doc)


def _fields_in_init_order(fields):
    # Returns the fields as __init__ will output them.  It returns 2 tuples:
    # the first for normal args, and the second for keyword args.

    return (tuple(f for f in fields if f.init and not f.kw_only),
            tuple(f for f in fields if f.init and f.kw_only)
            )


def _tuple_str(obj_name, fields):
    # Return a string representing each field of obj_name as a tuple
    # member.  So, if fields is ['x', 'y'] and obj_name is "self",
    # return "(self.x,self.y)".

    # Special case for the 0-tuple.
    if not fields:
        return '()'
    # Note the trailing comma, needed if this turns out to be a 1-tuple.
    return f'({",".join([f"{obj_name}.{f.name}" for f in fields])},)'


class _FuncBuilder:
    def __init__(self, globals):
        self.names = []
        self.src = []
        self.globals = globals
        self.locals = {}
        self.overwrite_errors = {}
        self.unconditional_adds = {}
        self.method_annotations = {}

    def add_fn(self, name, args, body, *, locals=None, return_type=MISSING,
               overwrite_error=False, unconditional_add=False, decorator=None,
               annotation_fields=None):
        if locals is not None:
            self.locals.update(locals)

        # Keep track if this method is allowed to be overwritten if it already
        # exists in the class.  The error is method-specific, so keep it with
        # the name.  We'll use this when we generate all of the functions in
        # the add_fns_to_class call.  overwrite_error is either True, in which
        # case we'll raise an error, or it's a string, in which case we'll
        # raise an error and append this string.
        if overwrite_error:
            self.overwrite_errors[name] = overwrite_error

        # Should this function always overwrite anything that's already in the
        # class?  The default is to not overwrite a function that already
        # exists.
        if unconditional_add:
            self.unconditional_adds[name] = True

        self.names.append(name)

        if annotation_fields is not None:
            self.method_annotations[name] = (annotation_fields, return_type)

        args = ','.join(args)
        body = '\n'.join(body)

