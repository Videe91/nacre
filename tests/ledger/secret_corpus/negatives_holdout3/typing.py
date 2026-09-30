"""
The typing module: Support for gradual typing as defined by PEP 484 and subsequent PEPs.

Among other things, the module includes the following:
* Generic, Protocol, and internal machinery to support generic aliases.
  All subscripted types like X[int], Union[int, str] are generic aliases.
* Various "special forms" that have unique meanings in type annotations:
  NoReturn, Never, ClassVar, Self, Concatenate, Unpack, and others.
* Classes whose instances can be type arguments to generic classes and functions:
  TypeVar, ParamSpec, TypeVarTuple.
* Public helper functions: get_type_hints, overload, cast, final, and others.
* Several protocols to support duck-typing:
  SupportsFloat, SupportsIndex, SupportsAbs, and others.
* Special types: NewType, NamedTuple, TypedDict.
* Deprecated aliases for builtin types and collections.abc ABCs.

Any name not present in __all__ is an implementation detail
that may be changed without notice. Use at your own risk!
"""

from abc import abstractmethod, ABCMeta
import collections
from collections import defaultdict
import collections.abc
import copyreg
import functools
import operator
import sys
import types
from types import GenericAlias

from _typing import (
    _idfunc,
    TypeVar,
    ParamSpec,
    TypeVarTuple,
    ParamSpecArgs,
    ParamSpecKwargs,
    TypeAliasType,
    Generic,
    Union,
    NoDefault,
)

# Please keep __all__ alphabetized within each category.
__all__ = [
    # Super-special typing primitives.
    'Annotated',
    'Any',
    'Callable',
    'ClassVar',
    'Concatenate',
    'Final',
    'ForwardRef',
    'Generic',
    'Literal',
    'Optional',
    'ParamSpec',
    'Protocol',
    'Tuple',
    'Type',
    'TypeVar',
    'TypeVarTuple',
    'Union',

    # ABCs (from collections.abc).
    'AbstractSet',  # collections.abc.Set.
    'ByteString',
    'Container',
    'ContextManager',
    'Hashable',
    'ItemsView',
    'Iterable',
    'Iterator',
    'KeysView',
    'Mapping',
    'MappingView',
    'MutableMapping',
    'MutableSequence',
    'MutableSet',
    'Sequence',
    'Sized',
    'ValuesView',
    'Awaitable',
    'AsyncIterator',
    'AsyncIterable',
    'Coroutine',
    'Collection',
    'AsyncGenerator',
    'AsyncContextManager',

    # Structural checks, a.k.a. protocols.
    'Reversible',
    'SupportsAbs',
    'SupportsBytes',
    'SupportsComplex',
    'SupportsFloat',
    'SupportsIndex',
    'SupportsInt',
    'SupportsRound',

    # Concrete collection types.
    'ChainMap',
    'Counter',
    'Deque',
    'Dict',
    'DefaultDict',
    'List',
    'OrderedDict',
    'Set',
    'FrozenSet',
    'NamedTuple',  # Not really a type.
    'TypedDict',  # Not really a type.
    'Generator',

    # Other concrete types.
    'BinaryIO',
    'IO',
    'Match',
    'Pattern',
    'TextIO',

    # One-off things.
    'AnyStr',
    'assert_type',
    'assert_never',
    'cast',
    'clear_overloads',
    'dataclass_transform',
    'evaluate_forward_ref',
    'final',
    'get_args',
    'get_origin',
    'get_overloads',
    'get_protocol_members',
    'get_type_hints',
    'is_protocol',
    'is_typeddict',
    'LiteralString',
    'Never',
    'NewType',
    'no_type_check',
    'no_type_check_decorator',
    'NoDefault',
    'NoReturn',
    'NotRequired',
    'overload',
    'override',
    'ParamSpecArgs',
    'ParamSpecKwargs',
    'ReadOnly',
    'Required',
    'reveal_type',
    'runtime_checkable',
    'Self',
    'Text',
    'TYPE_CHECKING',
    'TypeAlias',
    'TypeGuard',
    'TypeIs',
    'TypeAliasType',
    'Unpack',
]

class _LazyAnnotationLib:
    def __getattr__(self, attr):
        global _lazy_annotationlib
        import annotationlib
        _lazy_annotationlib = annotationlib
        return getattr(annotationlib, attr)

_lazy_annotationlib = _LazyAnnotationLib()


def _type_convert(arg, module=None, *, allow_special_forms=False, owner=None):
    """For converting None to type(None), and strings to ForwardRef."""
    if arg is None:
        return type(None)
    if isinstance(arg, str):
        return _make_forward_ref(arg, module=module, is_class=allow_special_forms, owner=owner)
    return arg


def _type_check(arg, msg, is_argument=True, module=None, *, allow_special_forms=False, owner=None):
    """Check that the argument is a type, and return it (internal helper).

    As a special case, accept None and return type(None) instead. Also wrap strings
    into ForwardRef instances. Consider several corner cases, for example plain
    special forms like Union are not valid, while Union[int, str] is OK, etc.
    The msg argument is a human-readable error message, e.g.::

        "Union[arg, ...]: arg should be a type."

    We append the repr() of the actual value (truncated to 100 chars).
    """
    invalid_generic_forms = (Generic, Protocol)
    if not allow_special_forms:
        invalid_generic_forms += (ClassVar,)
        if is_argument:
            invalid_generic_forms += (Final,)

    arg = _type_convert(arg, module=module, allow_special_forms=allow_special_forms, owner=owner)
    if (isinstance(arg, _GenericAlias) and
            arg.__origin__ in invalid_generic_forms):
        raise TypeError(f"{arg} is not valid as type argument")
    if arg in (Any, LiteralString, NoReturn, Never, Self, TypeAlias):
        return arg
    if allow_special_forms and arg in (ClassVar, Final):
        return arg
    if isinstance(arg, _SpecialForm) or arg in (Generic, Protocol):
        raise TypeError(f"Plain {arg} is not valid as type argument")
    if type(arg) is tuple:
        raise TypeError(f"{msg} Got {arg!r:.100}.")
    return arg


def _is_param_expr(arg):
    return arg is ... or isinstance(arg,
            (tuple, list, ParamSpec, _ConcatenateGenericAlias))


def _should_unflatten_callable_args(typ, args):
    """Internal helper for munging collections.abc.Callable's __args__.

    The canonical representation for a Callable's __args__ flattens the
    argument types, see https://github.com/python/cpython/issues/86361.

    For example::

        >>> import collections.abc
        >>> P = ParamSpec('P')
        >>> collections.abc.Callable[[int, int], str].__args__ == (int, int, str)
        True
        >>> collections.abc.Callable[P, str].__args__ == (P, str)
        True

    As a result, if we need to reconstruct the Callable from its __args__,
    we need to unflatten it.
    """
    return (
        typ.__origin__ is collections.abc.Callable
        and not (len(args) == 2 and _is_param_expr(args[0]))
    )


def _type_repr(obj):
    """Return the repr() of an object, special-casing types (internal helper).

    If obj is a type, we return a shorter version than the default
    type.__repr__, based on the module and qualified name, which is
    typically enough to uniquely identify a type.  For everything
    else, we fall back on repr(obj).
    """
    if isinstance(obj, tuple):
        # Special case for `repr` of types with `ParamSpec`:
        return '[' + ', '.join(_type_repr(t) for t in obj) + ']'
    return _lazy_annotationlib.type_repr(obj)


def _collect_type_parameters(args, *, enforce_default_ordering: bool = True):
    """Collect all type parameters in args
    in order of first appearance (lexicographic order).

    For example::

        >>> P = ParamSpec('P')
        >>> T = TypeVar('T')
        >>> _collect_type_parameters((T, Callable[P, T]))
        (~T, ~P)
    """
    # required type parameter cannot appear after parameter with default
    default_encountered = False
    # or after TypeVarTuple
    type_var_tuple_encountered = False
    parameters = []
    for t in args:
        if isinstance(t, type):
            # We don't want __parameters__ descriptor of a bare Python class.
            pass
        elif isinstance(t, tuple):
            # `t` might be a tuple, when `ParamSpec` is substituted with
            # `[T, int]`, or `[int, *Ts]`, etc.
            for x in t:
                for collected in _collect_type_parameters([x]):
                    if collected not in parameters:
                        parameters.append(collected)
        elif hasattr(t, '__typing_subst__'):
            if t not in parameters:
                if enforce_default_ordering:
                    if type_var_tuple_encountered and t.has_default():
                        raise TypeError('Type parameter with a default'
                                        ' follows TypeVarTuple')

                    if t.has_default():
                        default_encountered = True
                    elif default_encountered:
                        raise TypeError(f'Type parameter {t!r} without a default'
                                        ' follows type parameter with a default')

                parameters.append(t)
        else:
            if _is_unpacked_typevartuple(t):
                type_var_tuple_encountered = True
            for x in getattr(t, '__parameters__', ()):
                if x not in parameters:
                    parameters.append(x)
    return tuple(parameters)


def _check_generic_specialization(cls, arguments):
    """Check correct count for parameters of a generic cls (internal helper).

    This gives a nice error message in case of count mismatch.
    """
    expected_len = len(cls.__parameters__)
    if not expected_len:
        raise TypeError(f"{cls} is not a generic class")
    actual_len = len(arguments)
    if actual_len != expected_len:
        # deal with defaults
        if actual_len < expected_len:
            # If the parameter at index `actual_len` in the parameters list
            # has a default, then all parameters after it must also have
            # one, because we validated as much in _collect_type_parameters().
            # That means that no error needs to be raised here, despite
            # the number of arguments being passed not matching the number
            # of parameters: all parameters that aren't explicitly
            # specialized in this call are parameters with default values.
            if cls.__parameters__[actual_len].has_default():
                return

            expected_len -= sum(p.has_default() for p in cls.__parameters__)
            expect_val = f"at least {expected_len}"
        else:
            expect_val = expected_len

        raise TypeError(f"Too {'many' if actual_len > expected_len else 'few'} arguments"
                        f" for {cls}; actual {actual_len}, expected {expect_val}")


def _unpack_args(*args):
    newargs = []
    for arg in args:
        subargs = getattr(arg, '__typing_unpacked_tuple_args__', None)
        if subargs is not None and not (subargs and subargs[-1] is ...):
            newargs.extend(subargs)
        else:
            newargs.append(arg)
    return newargs

def _deduplicate(params, *, unhashable_fallback=False):
    # Weed out strict duplicates, preserving the first of each occurrence.
    try:
        return dict.fromkeys(params)
    except TypeError:
        if not unhashable_fallback:
            raise
        # Happens for cases like `Annotated[dict, {'x': IntValidator()}]`
        new_unhashable = []
        for t in params:
            if t not in new_unhashable:
                new_unhashable.append(t)
        return new_unhashable

def _flatten_literal_params(parameters):
    """Internal helper for Literal creation: flatten Literals among parameters."""
    params = []
    for p in parameters:
        if isinstance(p, _LiteralGenericAlias):
            params.extend(p.__args__)
        else:
            params.append(p)
    return tuple(params)


_cleanups = []
_caches = {}


def _tp_cache(func=None, /, *, typed=False):
    """Internal wrapper caching __getitem__ of generic types.

    For non-hashable arguments, the original function is used as a fallback.
    """
    def decorator(func):
        # The callback 'inner' references the newly created lru_cache
        # indirectly by performing a lookup in the global '_caches' dictionary.
        # This breaks a reference that can be problematic when combined with
        # C API extensions that leak references to types. See GH-98253.

        cache = functools.lru_cache(typed=typed)(func)
        _caches[func] = cache
        _cleanups.append(cache.cache_clear)
        del cache

        @functools.wraps(func)
        def inner(*args, **kwds):
            try:
                return _caches[func](*args, **kwds)
            except TypeError:
                pass  # All real errors (not unhashable args) are raised below.
            return func(*args, **kwds)
        return inner

    if func is not None:
        return decorator(func)

    return decorator


def _deprecation_warning_for_no_type_params_passed(funcname: str) -> None:
    import warnings

    depr_message = (
        f"Failing to pass a value to the 'type_params' parameter "
        f"of {funcname!r} is deprecated, as it leads to incorrect behaviour "
        f"when calling {funcname} on a stringified annotation "
        f"that references a PEP 695 type parameter. "
        f"It will be disallowed in Python 3.15."
    )
    warnings.warn(depr_message, category=DeprecationWarning, stacklevel=3)


class _Sentinel:
    __slots__ = ()
    def __repr__(self):
        return '<sentinel>'


_sentinel = _Sentinel()


def _eval_type(t, globalns, localns, type_params=_sentinel, *, recursive_guard=frozenset(),
               format=None, owner=None, parent_fwdref=None, prefer_fwd_module=False):
    """Evaluate all forward references in the given type t.

    For use of globalns and localns see the docstring for get_type_hints().
    recursive_guard is used to prevent infinite recursion with a recursive
    ForwardRef.
    """
    if type_params is _sentinel:
        _deprecation_warning_for_no_type_params_passed("typing._eval_type")
        type_params = ()
    if isinstance(t, _lazy_annotationlib.ForwardRef):
        # If the forward_ref has __forward_module__ set, evaluate() infers the globals
        # from the module, and it will probably pick better than the globals we have here.
        # We do this only for calls from get_type_hints() (which opts in through the
        # prefer_fwd_module flag), so that the default behavior remains more straightforward.
        if prefer_fwd_module and t.__forward_module__ is not None:
            globalns = None
            # If there are type params on the owner, we need to add them back, because
            # annotationlib won't.
            if owner_type_params := getattr(owner, "__type_params__", None):
                globalns = getattr(
                    sys.modules.get(t.__forward_module__, None), "__dict__", None
                )
                if globalns is not None:
                    globalns = dict(globalns)
                    for type_param in owner_type_params:
                        globalns[type_param.__name__] = type_param
        return evaluate_forward_ref(t, globals=globalns, locals=localns,
                                    type_params=type_params, owner=owner,
                                    _recursive_guard=recursive_guard, format=format)
    if isinstance(t, (_GenericAlias, GenericAlias, Union)):
        if isinstance(t, GenericAlias):
            args = tuple(
                _make_forward_ref(arg, parent_fwdref=parent_fwdref) if isinstance(arg, str) else arg
                for arg in t.__args__
            )
            is_unpacked = t.__unpacked__
            if _should_unflatten_callable_args(t, args):
                t = t.__origin__[(args[:-1], args[-1])]
            else:
                t = t.__origin__[args]
            if is_unpacked:
                t = Unpack[t]

        ev_args = tuple(
            _eval_type(
                a, globalns, localns, type_params, recursive_guard=recursive_guard,
                format=format, owner=owner, prefer_fwd_module=prefer_fwd_module,
            )
            for a in t.__args__
        )
        if ev_args == t.__args__:
            return t
        if isinstance(t, GenericAlias):
            return GenericAlias(t.__origin__, ev_args)
        if isinstance(t, Union):
            return functools.reduce(operator.or_, ev_args)
        else:
            return t.copy_with(ev_args)
    return t


class _Final:
    """Mixin to prohibit subclassing."""

    __slots__ = ('__weakref__',)

