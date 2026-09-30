"""Disassembler of Python byte code into mnemonics."""

import sys
import types
import collections
import io

from opcode import *
from opcode import (
    __all__ as _opcodes_all,
    _cache_format,
    _inline_cache_entries,
    _nb_ops,
    _common_constants,
    _intrinsic_1_descs,
    _intrinsic_2_descs,
    _special_method_names,
    _specializations,
    _specialized_opmap,
)

from _opcode import get_executor

__all__ = ["code_info", "dis", "disassemble", "distb", "disco",
           "findlinestarts", "findlabels", "show_code",
           "get_instructions", "Instruction", "Bytecode"] + _opcodes_all
del _opcodes_all

_have_code = (types.MethodType, types.FunctionType, types.CodeType,
              classmethod, staticmethod, type)

CONVERT_VALUE = opmap['CONVERT_VALUE']

SET_FUNCTION_ATTRIBUTE = opmap['SET_FUNCTION_ATTRIBUTE']
FUNCTION_ATTR_FLAGS = ('defaults', 'kwdefaults', 'annotations', 'closure', 'annotate')

ENTER_EXECUTOR = opmap['ENTER_EXECUTOR']
LOAD_GLOBAL = opmap['LOAD_GLOBAL']
LOAD_SMALL_INT = opmap['LOAD_SMALL_INT']
BINARY_OP = opmap['BINARY_OP']
JUMP_BACKWARD = opmap['JUMP_BACKWARD']
FOR_ITER = opmap['FOR_ITER']
SEND = opmap['SEND']
LOAD_ATTR = opmap['LOAD_ATTR']
LOAD_SUPER_ATTR = opmap['LOAD_SUPER_ATTR']
CALL_INTRINSIC_1 = opmap['CALL_INTRINSIC_1']
CALL_INTRINSIC_2 = opmap['CALL_INTRINSIC_2']
LOAD_COMMON_CONSTANT = opmap['LOAD_COMMON_CONSTANT']
LOAD_SPECIAL = opmap['LOAD_SPECIAL']
LOAD_FAST_LOAD_FAST = opmap['LOAD_FAST_LOAD_FAST']
LOAD_FAST_BORROW_LOAD_FAST_BORROW = opmap['LOAD_FAST_BORROW_LOAD_FAST_BORROW']
STORE_FAST_LOAD_FAST = opmap['STORE_FAST_LOAD_FAST']
STORE_FAST_STORE_FAST = opmap['STORE_FAST_STORE_FAST']
IS_OP = opmap['IS_OP']
CONTAINS_OP = opmap['CONTAINS_OP']
END_ASYNC_FOR = opmap['END_ASYNC_FOR']

CACHE = opmap["CACHE"]

_all_opname = list(opname)
_all_opmap = dict(opmap)
for name, op in _specialized_opmap.items():
    # fill opname and opmap
    assert op < len(_all_opname)
    _all_opname[op] = name
    _all_opmap[name] = op

deoptmap = {
    specialized: base for base, family in _specializations.items() for specialized in family
}

def _try_compile(source, name):
    """Attempts to compile the given source, first as an expression and
       then as a statement if the first approach fails.

       Utility function to accept strings in functions that otherwise
       expect code objects
    """
    try:
        return compile(source, name, 'eval')
    except SyntaxError:
        pass
    return compile(source, name, 'exec')

def dis(x=None, *, file=None, depth=None, show_caches=False, adaptive=False,
        show_offsets=False, show_positions=False):
    """Disassemble classes, methods, functions, and other compiled objects.

    With no argument, disassemble the last traceback.

    Compiled objects currently include generator objects, async generator
    objects, and coroutine objects, all of which store their code object
    in a special attribute.
    """
    if x is None:
        distb(file=file, show_caches=show_caches, adaptive=adaptive,
              show_offsets=show_offsets, show_positions=show_positions)
        return
    # Extract functions from methods.
    if hasattr(x, '__func__'):
        x = x.__func__
    # Extract compiled code objects from...
    if hasattr(x, '__code__'):  # ...a function, or
        x = x.__code__
    elif hasattr(x, 'gi_code'):  #...a generator object, or
        x = x.gi_code
    elif hasattr(x, 'ag_code'):  #...an asynchronous generator object, or
        x = x.ag_code
    elif hasattr(x, 'cr_code'):  #...a coroutine.
        x = x.cr_code
    # Perform the disassembly.
    if hasattr(x, '__dict__'):  # Class or module
        items = sorted(x.__dict__.items())
        for name, x1 in items:
            if isinstance(x1, _have_code):
                print("Disassembly of %s:" % name, file=file)
                try:
                    dis(x1, file=file, depth=depth, show_caches=show_caches, adaptive=adaptive, show_offsets=show_offsets, show_positions=show_positions)
                except TypeError as msg:
                    print("Sorry:", msg, file=file)
                print(file=file)
    elif hasattr(x, 'co_code'): # Code object
        _disassemble_recursive(x, file=file, depth=depth, show_caches=show_caches, adaptive=adaptive, show_offsets=show_offsets, show_positions=show_positions)
    elif isinstance(x, (bytes, bytearray)): # Raw bytecode
        labels_map = _make_labels_map(x)
        label_width = 4 + len(str(len(labels_map)))
        formatter = Formatter(file=file,
                              offset_width=len(str(max(len(x) - 2, 9999))) if show_offsets else 0,
                              label_width=label_width,
                              show_caches=show_caches)
        arg_resolver = ArgResolver(labels_map=labels_map)
        _disassemble_bytes(x, arg_resolver=arg_resolver, formatter=formatter)
    elif isinstance(x, str):    # Source code
        _disassemble_str(x, file=file, depth=depth, show_caches=show_caches, adaptive=adaptive, show_offsets=show_offsets, show_positions=show_positions)
    else:
        raise TypeError("don't know how to disassemble %s objects" %
                        type(x).__name__)

def distb(tb=None, *, file=None, show_caches=False, adaptive=False, show_offsets=False, show_positions=False):
    """Disassemble a traceback (default: last traceback)."""
    if tb is None:
        try:
            if hasattr(sys, 'last_exc'):
                tb = sys.last_exc.__traceback__
            else:
                tb = sys.last_traceback
        except AttributeError:
            raise RuntimeError("no last traceback to disassemble") from None
        while tb.tb_next: tb = tb.tb_next
    disassemble(tb.tb_frame.f_code, tb.tb_lasti, file=file, show_caches=show_caches, adaptive=adaptive, show_offsets=show_offsets, show_positions=show_positions)

# The inspect module interrogates this dictionary to build its
# list of CO_* constants. It is also used by pretty_flags to
# turn the co_flags field into a human readable list.
COMPILER_FLAG_NAMES = {
            1: "OPTIMIZED",
            2: "NEWLOCALS",
            4: "VARARGS",
            8: "VARKEYWORDS",
           16: "NESTED",
           32: "GENERATOR",
           64: "NOFREE",
          128: "COROUTINE",
          256: "ITERABLE_COROUTINE",
          512: "ASYNC_GENERATOR",
    0x4000000: "HAS_DOCSTRING",
    0x8000000: "METHOD",
}

def pretty_flags(flags):
    """Return pretty representation of code flags."""
    names = []
    for i in range(32):
        flag = 1<<i
        if flags & flag:
            names.append(COMPILER_FLAG_NAMES.get(flag, hex(flag)))
            flags ^= flag
            if not flags:
                break
    else:
        names.append(hex(flags))
    return ", ".join(names)

class _Unknown:
    def __repr__(self):
        return "<unknown>"

# Sentinel to represent values that cannot be calculated
UNKNOWN = _Unknown()

def _get_code_object(x):
    """Helper to handle methods, compiled or raw code objects, and strings."""
    # Extract functions from methods.
    if hasattr(x, '__func__'):
        x = x.__func__
    # Extract compiled code objects from...
    if hasattr(x, '__code__'):  # ...a function, or
        x = x.__code__
    elif hasattr(x, 'gi_code'):  #...a generator object, or
        x = x.gi_code
    elif hasattr(x, 'ag_code'):  #...an asynchronous generator object, or
        x = x.ag_code
    elif hasattr(x, 'cr_code'):  #...a coroutine.
        x = x.cr_code
    # Handle source code.
    if isinstance(x, str):
        x = _try_compile(x, "<disassembly>")
    # By now, if we don't have a code object, we can't disassemble x.
    if hasattr(x, 'co_code'):
        return x
    raise TypeError("don't know how to disassemble %s objects" %
                    type(x).__name__)

def _deoptop(op):
    name = _all_opname[op]
    return _all_opmap[deoptmap[name]] if name in deoptmap else op

def _get_code_array(co, adaptive):
    if adaptive:
        code = co._co_code_adaptive
        res = []
        found = False
        for i in range(0, len(code), 2):
            op, arg = code[i], code[i+1]
            if op == ENTER_EXECUTOR:
                try:
                    ex = get_executor(co, i)
                except (ValueError, RuntimeError):
                    ex = None

                if ex:
                    op, arg = ex.get_opcode(), ex.get_oparg()
                    found = True

            res.append(op.to_bytes())
            res.append(arg.to_bytes())
        return code if not found else b''.join(res)
    else:
        return co.co_code

def code_info(x):
    """Formatted details of methods, functions, or code."""
    return _format_code_info(_get_code_object(x))

def _format_code_info(co):
    lines = []
    lines.append("Name:              %s" % co.co_name)
    lines.append("Filename:          %s" % co.co_filename)
    lines.append("Argument count:    %s" % co.co_argcount)
    lines.append("Positional-only arguments: %s" % co.co_posonlyargcount)
    lines.append("Kw-only arguments: %s" % co.co_kwonlyargcount)
    lines.append("Number of locals:  %s" % co.co_nlocals)
    lines.append("Stack size:        %s" % co.co_stacksize)
    lines.append("Flags:             %s" % pretty_flags(co.co_flags))
    if co.co_consts:
        lines.append("Constants:")
        for i_c in enumerate(co.co_consts):
            lines.append("%4d: %r" % i_c)
    if co.co_names:
        lines.append("Names:")
        for i_n in enumerate(co.co_names):
            lines.append("%4d: %s" % i_n)
    if co.co_varnames:
        lines.append("Variable names:")
        for i_n in enumerate(co.co_varnames):
            lines.append("%4d: %s" % i_n)
    if co.co_freevars:
        lines.append("Free variables:")
        for i_n in enumerate(co.co_freevars):
            lines.append("%4d: %s" % i_n)
    if co.co_cellvars:
        lines.append("Cell variables:")
        for i_n in enumerate(co.co_cellvars):
            lines.append("%4d: %s" % i_n)
    return "\n".join(lines)

def show_code(co, *, file=None):
    """Print details of methods, functions, or code to *file*.

    If *file* is not provided, the output is printed on stdout.
    """
    print(code_info(co), file=file)

Positions = collections.namedtuple(
    'Positions',
    [
        'lineno',
        'end_lineno',
        'col_offset',
        'end_col_offset',
    ],
    defaults=[None] * 4
)

_Instruction = collections.namedtuple(
    "_Instruction",
    [
        'opname',
        'opcode',
        'arg',
        'argval',
        'argrepr',
        'offset',
        'start_offset',
        'starts_line',
        'line_number',
        'label',
        'positions',
        'cache_info',
    ],
    defaults=[None, None, None]
)

_Instruction.opname.__doc__ = "Human readable name for operation"
_Instruction.opcode.__doc__ = "Numeric code for operation"
_Instruction.arg.__doc__ = "Numeric argument to operation (if any), otherwise None"
_Instruction.argval.__doc__ = "Resolved arg value (if known), otherwise same as arg"
_Instruction.argrepr.__doc__ = "Human readable description of operation argument"
_Instruction.offset.__doc__ = "Start index of operation within bytecode sequence"
_Instruction.start_offset.__doc__ = (
    "Start index of operation within bytecode sequence, including extended args if present; "
    "otherwise equal to Instruction.offset"
)
_Instruction.starts_line.__doc__ = "True if this opcode starts a source line, otherwise False"
_Instruction.line_number.__doc__ = "source line number associated with this opcode (if any), otherwise None"
_Instruction.label.__doc__ = "A label (int > 0) if this instruction is a jump target, otherwise None"
_Instruction.positions.__doc__ = "dis.Positions object holding the span of source code covered by this instruction"
_Instruction.cache_info.__doc__ = "list of (name, size, data), one for each cache entry of the instruction"

_ExceptionTableEntryBase = collections.namedtuple("_ExceptionTableEntryBase",
    "start end target depth lasti")

class _ExceptionTableEntry(_ExceptionTableEntryBase):
    pass

_OPNAME_WIDTH = 20
_OPARG_WIDTH = 5

def _get_cache_size(opname):
    return _inline_cache_entries.get(opname, 0)

def _get_jump_target(op, arg, offset):
    """Gets the bytecode offset of the jump target if this is a jump instruction.

    Otherwise return None.
    """
    deop = _deoptop(op)
    caches = _get_cache_size(_all_opname[deop])
    if deop in hasjrel:
        if _is_backward_jump(deop):
            arg = -arg
        target = offset + 2 + arg*2
        target += 2 * caches
    elif deop in hasjabs:
        target = arg*2
    else:
        target = None
    return target

class Instruction(_Instruction):
    """Details for a bytecode operation.

       Defined fields:
         opname - human readable name for operation
         opcode - numeric code for operation
         arg - numeric argument to operation (if any), otherwise None
         argval - resolved arg value (if known), otherwise same as arg
         argrepr - human readable description of operation argument
         offset - start index of operation within bytecode sequence
         start_offset - start index of operation within bytecode sequence including extended args if present;
                        otherwise equal to Instruction.offset
         starts_line - True if this opcode starts a source line, otherwise False
         line_number - source line number associated with this opcode (if any), otherwise None
         label - A label if this instruction is a jump target, otherwise None
         positions - Optional dis.Positions object holding the span of source code
                     covered by this instruction
         cache_info - information about the format and content of the instruction's cache
                        entries (if any)
    """

    @staticmethod
    def make(
        opname, arg, argval, argrepr, offset, start_offset, starts_line,
        line_number, label=None, positions=None, cache_info=None
    ):
        return Instruction(opname, _all_opmap[opname], arg, argval, argrepr, offset,
                           start_offset, starts_line, line_number, label, positions, cache_info)

    @property
    def oparg(self):
        """Alias for Instruction.arg."""
        return self.arg

    @property
    def baseopcode(self):
        """Numeric code for the base operation if operation is specialized.

        Otherwise equal to Instruction.opcode.
        """
        return _deoptop(self.opcode)

    @property
    def baseopname(self):
        """Human readable name for the base operation if operation is specialized.

        Otherwise equal to Instruction.opname.
        """
        return opname[self.baseopcode]

    @property
    def cache_offset(self):
        """Start index of the cache entries following the operation."""
        return self.offset + 2

    @property
    def end_offset(self):
        """End index of the cache entries following the operation."""
        return self.cache_offset + _get_cache_size(_all_opname[self.opcode])*2

    @property
    def jump_target(self):
        """Bytecode index of the jump target if this is a jump operation.

        Otherwise return None.
        """
        return _get_jump_target(self.opcode, self.arg, self.offset)

    @property
    def is_jump_target(self):
        """True if other code jumps to here, otherwise False"""
        return self.label is not None

    def __str__(self):
        output = io.StringIO()
        formatter = Formatter(file=output)
        formatter.print_instruction(self, False)
        return output.getvalue()


class Formatter:

    def __init__(self, file=None, lineno_width=0, offset_width=0, label_width=0,
                 line_offset=0, show_caches=False, *, show_positions=False):
        """Create a Formatter

        *file* where to write the output
        *lineno_width* sets the width of the source location field (0 omits it).
        Should be large enough for a line number or full positions (depending
        on the value of *show_positions*).
        *offset_width* sets the width of the instruction offset field
        *label_width* sets the width of the label field
        *show_caches* is a boolean indicating whether to display cache lines
