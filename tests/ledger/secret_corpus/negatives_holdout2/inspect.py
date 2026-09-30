"""Get useful information from live Python objects.

This module encapsulates the interface provided by the internal special
attributes (co_*, im_*, tb_*, etc.) in a friendlier fashion.
It also provides some help for examining source code and class layout.

Here are some of the useful functions provided by this module:

    ismodule(), isclass(), ismethod(), ispackage(), isfunction(),
        isgeneratorfunction(), isgenerator(), istraceback(), isframe(),
        iscode(), isbuiltin(), isroutine() - check object types
    getmembers() - get members of an object that satisfy a given condition

    getfile(), getsourcefile(), getsource() - find an object's source code
    getdoc(), getcomments() - get documentation on an object
    getmodule() - determine the module that an object came from
    getclasstree() - arrange classes so as to represent their hierarchy

    getargvalues(), getcallargs() - get info about function arguments
    getfullargspec() - same, with support for Python 3 features
    formatargvalues() - format an argument spec
    getouterframes(), getinnerframes() - get info about frames
    currentframe() - get the current stack frame
    stack(), trace() - get info about frames on the stack or in a traceback

    signature() - get a Signature object for the callable
"""

# This module is in the public domain.  No warranties.

__author__ = ('Ka-Ping Yee <ping@lfw.org>',
              'Yury Selivanov <yselivanov@sprymix.com>')

__all__ = [
    "AGEN_CLOSED",
    "AGEN_CREATED",
    "AGEN_RUNNING",
    "AGEN_SUSPENDED",
    "ArgInfo",
    "Arguments",
    "Attribute",
    "BlockFinder",
    "BoundArguments",
    "BufferFlags",
    "CORO_CLOSED",
    "CORO_CREATED",
    "CORO_RUNNING",
    "CORO_SUSPENDED",
    "CO_ASYNC_GENERATOR",
    "CO_COROUTINE",
    "CO_GENERATOR",
    "CO_ITERABLE_COROUTINE",
    "CO_NESTED",
    "CO_NEWLOCALS",
    "CO_NOFREE",
    "CO_OPTIMIZED",
    "CO_VARARGS",
    "CO_VARKEYWORDS",
    "CO_HAS_DOCSTRING",
    "CO_METHOD",
    "ClassFoundException",
    "ClosureVars",
    "EndOfBlock",
    "FrameInfo",
    "FullArgSpec",
    "GEN_CLOSED",
    "GEN_CREATED",
    "GEN_RUNNING",
    "GEN_SUSPENDED",
    "Parameter",
    "Signature",
    "TPFLAGS_IS_ABSTRACT",
    "Traceback",
    "classify_class_attrs",
    "cleandoc",
    "currentframe",
    "findsource",
    "formatannotation",
    "formatannotationrelativeto",
    "formatargvalues",
    "get_annotations",
    "getabsfile",
    "getargs",
    "getargvalues",
    "getasyncgenlocals",
    "getasyncgenstate",
    "getattr_static",
    "getblock",
    "getcallargs",
    "getclasstree",
    "getclosurevars",
    "getcomments",
    "getcoroutinelocals",
    "getcoroutinestate",
    "getdoc",
    "getfile",
    "getframeinfo",
    "getfullargspec",
    "getgeneratorlocals",
    "getgeneratorstate",
    "getinnerframes",
    "getlineno",
    "getmembers",
    "getmembers_static",
    "getmodule",
    "getmodulename",
    "getmro",
    "getouterframes",
    "getsource",
    "getsourcefile",
    "getsourcelines",
    "indentsize",
    "isabstract",
    "isasyncgen",
    "isasyncgenfunction",
    "isawaitable",
    "isbuiltin",
    "isclass",
    "iscode",
    "iscoroutine",
    "iscoroutinefunction",
    "isdatadescriptor",
    "isframe",
    "isfunction",
    "isgenerator",
    "isgeneratorfunction",
    "isgetsetdescriptor",
    "ismemberdescriptor",
    "ismethod",
    "ismethoddescriptor",
    "ismethodwrapper",
    "ismodule",
    "ispackage",
    "isroutine",
    "istraceback",
    "markcoroutinefunction",
    "signature",
    "stack",
    "trace",
    "unwrap",
    "walktree",
]


import abc
from annotationlib import Format, ForwardRef
from annotationlib import get_annotations  # re-exported
import ast
import dis
import collections.abc
import enum
import importlib.machinery
import itertools
import linecache
import os
import re
import sys
import tokenize
import token
import types
import functools
import builtins
from keyword import iskeyword
from operator import attrgetter
from collections import namedtuple, OrderedDict
from weakref import ref as make_weakref

# Create constants for the compiler flags in Include/code.h
# We try to get them from dis to avoid duplication
mod_dict = globals()
for k, v in dis.COMPILER_FLAG_NAMES.items():
    mod_dict["CO_" + v] = k
del k, v, mod_dict

# See Include/object.h
TPFLAGS_IS_ABSTRACT = 1 << 20


# ----------------------------------------------------------- type-checking
def ismodule(object):
    """Return true if the object is a module."""
    return isinstance(object, types.ModuleType)

def isclass(object):
    """Return true if the object is a class."""
    return isinstance(object, type)

def ismethod(object):
    """Return true if the object is an instance method."""
    return isinstance(object, types.MethodType)

def ispackage(object):
    """Return true if the object is a package."""
    return ismodule(object) and hasattr(object, "__path__")

def ismethoddescriptor(object):
    """Return true if the object is a method descriptor.

    But not if ismethod() or isclass() or isfunction() are true.

    This is new in Python 2.2, and, for example, is true of int.__add__.
    An object passing this test has a __get__ attribute, but not a
    __set__ attribute or a __delete__ attribute. Beyond that, the set
    of attributes varies; __name__ is usually sensible, and __doc__
    often is.

    Methods implemented via descriptors that also pass one of the other
    tests return false from the ismethoddescriptor() test, simply because
    the other tests promise more -- you can, e.g., count on having the
    __func__ attribute (etc) when an object passes ismethod()."""
    if isclass(object) or ismethod(object) or isfunction(object):
        # mutual exclusion
        return False
    tp = type(object)
    return (hasattr(tp, "__get__")
            and not hasattr(tp, "__set__")
            and not hasattr(tp, "__delete__"))

def isdatadescriptor(object):
    """Return true if the object is a data descriptor.

    Data descriptors have a __set__ or a __delete__ attribute.  Examples are
    properties (defined in Python) and getsets and members (defined in C).
    Typically, data descriptors will also have __name__ and __doc__ attributes
    (properties, getsets, and members have both of these attributes), but this
    is not guaranteed."""
    if isclass(object) or ismethod(object) or isfunction(object):
        # mutual exclusion
        return False
    tp = type(object)
    return hasattr(tp, "__set__") or hasattr(tp, "__delete__")

if hasattr(types, 'MemberDescriptorType'):
    # CPython and equivalent
    def ismemberdescriptor(object):
        """Return true if the object is a member descriptor.

        Member descriptors are specialized descriptors defined in extension
        modules."""
        return isinstance(object, types.MemberDescriptorType)
else:
    # Other implementations
    def ismemberdescriptor(object):
        """Return true if the object is a member descriptor.

        Member descriptors are specialized descriptors defined in extension
        modules."""
        return False

if hasattr(types, 'GetSetDescriptorType'):
    # CPython and equivalent
    def isgetsetdescriptor(object):
        """Return true if the object is a getset descriptor.

        getset descriptors are specialized descriptors defined in extension
        modules."""
        return isinstance(object, types.GetSetDescriptorType)
else:
    # Other implementations
    def isgetsetdescriptor(object):
        """Return true if the object is a getset descriptor.

        getset descriptors are specialized descriptors defined in extension
        modules."""
        return False

def isfunction(object):
    """Return true if the object is a user-defined function.

    Function objects provide these attributes:
        __doc__         documentation string
        __name__        name with which this function was defined
        __qualname__    qualified name of this function
        __module__      name of the module the function was defined in or None
        __code__        code object containing compiled function bytecode
        __defaults__    tuple of any default values for arguments
        __globals__     global namespace in which this function was defined
        __annotations__ dict of parameter annotations
        __kwdefaults__  dict of keyword only parameters with defaults
        __dict__        namespace which is supporting arbitrary function attributes
        __closure__     a tuple of cells or None
        __type_params__ tuple of type parameters"""
    return isinstance(object, types.FunctionType)

def _has_code_flag(f, flag):
    """Return true if ``f`` is a function (or a method or functools.partial
    wrapper wrapping a function or a functools.partialmethod wrapping a
    function) whose code object has the given ``flag``
    set in its flags."""
    f = functools._unwrap_partialmethod(f)
    while ismethod(f):
        f = f.__func__
    f = functools._unwrap_partial(f)
    if not (isfunction(f) or _signature_is_functionlike(f)):
        return False
    return bool(f.__code__.co_flags & flag)

def isgeneratorfunction(obj):
    """Return true if the object is a user-defined generator function.

    Generator function objects provide the same attributes as functions.
    See help(isfunction) for a list of attributes."""
    return _has_code_flag(obj, CO_GENERATOR)

# A marker for markcoroutinefunction and iscoroutinefunction.
_is_coroutine_mark = object()

def _has_coroutine_mark(f):
    while ismethod(f):
        f = f.__func__
    f = functools._unwrap_partial(f)
    return getattr(f, "_is_coroutine_marker", None) is _is_coroutine_mark

def markcoroutinefunction(func):
    """
    Decorator to ensure callable is recognised as a coroutine function.
    """
    if hasattr(func, '__func__'):
        func = func.__func__
    func._is_coroutine_marker = _is_coroutine_mark
    return func

def iscoroutinefunction(obj):
    """Return true if the object is a coroutine function.

    Coroutine functions are normally defined with "async def" syntax, but may
    be marked via markcoroutinefunction.
    """
    return _has_code_flag(obj, CO_COROUTINE) or _has_coroutine_mark(obj)

def isasyncgenfunction(obj):
    """Return true if the object is an asynchronous generator function.

    Asynchronous generator functions are defined with "async def"
    syntax and have "yield" expressions in their body.
    """
    return _has_code_flag(obj, CO_ASYNC_GENERATOR)

def isasyncgen(object):
    """Return true if the object is an asynchronous generator."""
    return isinstance(object, types.AsyncGeneratorType)

def isgenerator(object):
    """Return true if the object is a generator.

    Generator objects provide these attributes:
        gi_code         code object
        gi_frame        frame object or possibly None once the generator has
                        been exhausted
        gi_running      set to 1 when generator is executing, 0 otherwise
        gi_suspended    set to 1 when the generator is suspended at a yield point, 0 otherwise
        gi_yieldfrom    object being iterated by yield from or None

        __iter__()      defined to support iteration over container
        close()         raises a new GeneratorExit exception inside the
                        generator to terminate the iteration
        send()          resumes the generator and "sends" a value that becomes
                        the result of the current yield-expression
        throw()         used to raise an exception inside the generator"""
    return isinstance(object, types.GeneratorType)

def iscoroutine(object):
    """Return true if the object is a coroutine."""
    return isinstance(object, types.CoroutineType)

def isawaitable(object):
    """Return true if object can be passed to an ``await`` expression."""
    return (isinstance(object, types.CoroutineType) or
            isinstance(object, types.GeneratorType) and
                bool(object.gi_code.co_flags & CO_ITERABLE_COROUTINE) or
            isinstance(object, collections.abc.Awaitable))

def istraceback(object):
    """Return true if the object is a traceback.

    Traceback objects provide these attributes:
        tb_frame        frame object at this level
        tb_lasti        index of last attempted instruction in bytecode
        tb_lineno       current line number in Python source code
        tb_next         next inner traceback object (called by this level)"""
    return isinstance(object, types.TracebackType)

def isframe(object):
    """Return true if the object is a frame object.

    Frame objects provide these attributes:
        f_back          next outer frame object (this frame's caller)
        f_builtins      built-in namespace seen by this frame
        f_code          code object being executed in this frame
        f_globals       global namespace seen by this frame
        f_lasti         index of last attempted instruction in bytecode
        f_lineno        current line number in Python source code
        f_locals        local namespace seen by this frame
        f_trace         tracing function for this frame, or None
        f_trace_lines   is a tracing event triggered for each source line?
        f_trace_opcodes are per-opcode events being requested?

        clear()          used to clear all references to local variables"""
    return isinstance(object, types.FrameType)

def iscode(object):
    """Return true if the object is a code object.

    Code objects provide these attributes:
        co_argcount         number of arguments (not including *, ** args
                            or keyword only arguments)
        co_code             string of raw compiled bytecode
        co_cellvars         tuple of names of cell variables
        co_consts           tuple of constants used in the bytecode
        co_filename         name of file in which this code object was created
        co_firstlineno      number of first line in Python source code
        co_flags            bitmap: 1=optimized | 2=newlocals | 4=*arg | 8=**arg
                            | 16=nested | 32=generator | 64=nofree | 128=coroutine
                            | 256=iterable_coroutine | 512=async_generator
                            | 0x4000000=has_docstring
        co_freevars         tuple of names of free variables
        co_posonlyargcount  number of positional only arguments
        co_kwonlyargcount   number of keyword only arguments (not including ** arg)
        co_lnotab           encoded mapping of line numbers to bytecode indices
        co_name             name with which this code object was defined
        co_names            tuple of names other than arguments and function locals
        co_nlocals          number of local variables
        co_stacksize        virtual machine stack space required
        co_varnames         tuple of names of arguments and local variables
        co_qualname         fully qualified function name

        co_lines()          returns an iterator that yields successive bytecode ranges
        co_positions()      returns an iterator of source code positions for each bytecode instruction
        replace()           returns a copy of the code object with a new values"""
    return isinstance(object, types.CodeType)

def isbuiltin(object):
    """Return true if the object is a built-in function or method.

    Built-in functions and methods provide these attributes:
        __doc__         documentation string
        __name__        original name of this function or method
        __self__        instance to which a method is bound, or None"""
    return isinstance(object, types.BuiltinFunctionType)

def ismethodwrapper(object):
    """Return true if the object is a method wrapper."""
    return isinstance(object, types.MethodWrapperType)

def isroutine(object):
    """Return true if the object is any kind of function or method."""
    return (isbuiltin(object)
            or isfunction(object)
            or ismethod(object)
            or ismethoddescriptor(object)
            or ismethodwrapper(object)
            or isinstance(object, functools._singledispatchmethod_get))

def isabstract(object):
