import io, os, sys, sysconfig
from . import ffiplatform, model
from .error import VerificationError
from .cffi_opcode import *

VERSION_BASE = 0x2601
VERSION_EMBEDDED = 0x2701
VERSION_CHAR16CHAR32 = 0x2801

FREE_THREADED_BUILD = sysconfig.get_config_var("Py_GIL_DISABLED")
USE_LIMITED_API = ((sys.platform != 'win32' or sys.version_info < (3, 0) or
                   sys.version_info >= (3, 5)) and
                   # free-threaded build doesn't support the stable ABI until Python 3.15
                   (not FREE_THREADED_BUILD or sys.version_info >= (3, 15)))

class GlobalExpr:
    def __init__(self, name, address, type_op, size=0, check_value=0):
        self.name = name
        self.address = address
        self.type_op = type_op
        self.size = size
        self.check_value = check_value

    def as_c_expr(self):
        return '  { "%s", (void *)%s, %s, (void *)%s },' % (
            self.name, self.address, self.type_op.as_c_expr(), self.size)

    def as_python_expr(self):
        return "b'%s%s',%d" % (self.type_op.as_python_bytes(), self.name,
                               self.check_value)

class FieldExpr:
    def __init__(self, name, field_offset, field_size, fbitsize, field_type_op):
        self.name = name
        self.field_offset = field_offset
        self.field_size = field_size
        self.fbitsize = fbitsize
        self.field_type_op = field_type_op

    def as_c_expr(self):
        spaces = " " * len(self.name)
        return ('  { "%s", %s,\n' % (self.name, self.field_offset) +
                '     %s   %s,\n' % (spaces, self.field_size) +
                '     %s   %s },' % (spaces, self.field_type_op.as_c_expr()))

    def as_python_expr(self):
        raise NotImplementedError

    def as_field_python_expr(self):
        if self.field_type_op.op == OP_NOOP:
            size_expr = ''
        elif self.field_type_op.op == OP_BITFIELD:
            size_expr = format_four_bytes(self.fbitsize)
        else:
            raise NotImplementedError
        return "b'%s%s%s'" % (self.field_type_op.as_python_bytes(),
                              size_expr,
                              self.name)

class StructUnionExpr:
    def __init__(self, name, type_index, flags, size, alignment, comment,
                 first_field_index, c_fields):
        self.name = name
        self.type_index = type_index
        self.flags = flags
        self.size = size
        self.alignment = alignment
        self.comment = comment
        self.first_field_index = first_field_index
        self.c_fields = c_fields

    def as_c_expr(self):
        return ('  { "%s", %d, %s,' % (self.name, self.type_index, self.flags)
                + '\n    %s, %s, ' % (self.size, self.alignment)
                + '%d, %d ' % (self.first_field_index, len(self.c_fields))
                + ('/* %s */ ' % self.comment if self.comment else '')
                + '},')

    def as_python_expr(self):
        flags = eval(self.flags, G_FLAGS)
        fields_expr = [c_field.as_field_python_expr()
                       for c_field in self.c_fields]
        return "(b'%s%s%s',%s)" % (
            format_four_bytes(self.type_index),
            format_four_bytes(flags),
            self.name,
            ','.join(fields_expr))

class EnumExpr:
    def __init__(self, name, type_index, size, signed, allenums):
        self.name = name
        self.type_index = type_index
        self.size = size
        self.signed = signed
        self.allenums = allenums

    def as_c_expr(self):
        lines = ['  { "%s", %d, _cffi_prim_int(%s, %s),' % (self.name, self.type_index,
                                                            self.size, self.signed,)]
        pending = 0
        while len(self.allenums) > pending + 110:
            j = self.allenums.find(',', pending + 100)
            if j < 0:
                break
            j += 1
            lines.append('    "%s"' % (self.allenums[pending:j],))
            pending = j
        lines.append('    "%s" },' % (self.allenums[pending:],))
        return '\n'.join(lines)

    def as_python_expr(self):
        prim_index = {
            (1, 0): PRIM_UINT8,  (1, 1):  PRIM_INT8,
            (2, 0): PRIM_UINT16, (2, 1):  PRIM_INT16,
            (4, 0): PRIM_UINT32, (4, 1):  PRIM_INT32,
            (8, 0): PRIM_UINT64, (8, 1):  PRIM_INT64,
            }[self.size, self.signed]
        return "b'%s%s%s\\x00%s'" % (format_four_bytes(self.type_index),
                                     format_four_bytes(prim_index),
                                     self.name, self.allenums)

class TypenameExpr:
    def __init__(self, name, type_index):
        self.name = name
        self.type_index = type_index

    def as_c_expr(self):
        return '  { "%s", %d },' % (self.name, self.type_index)

    def as_python_expr(self):
        return "b'%s%s'" % (format_four_bytes(self.type_index), self.name)


# ____________________________________________________________


class Recompiler:
    _num_externpy = 0

    def __init__(self, ffi, module_name, target_is_python=False):
        self.ffi = ffi
        self.module_name = module_name
        self.target_is_python = target_is_python
        self._version = VERSION_BASE

    def needs_version(self, ver):
        self._version = max(self._version, ver)

    def collect_type_table(self):
        self._typesdict = {}
        self._generate("collecttype")
        #
        all_decls = sorted(self._typesdict, key=str)
        #
        # prepare all FUNCTION bytecode sequences first
        self.cffi_types = []
        for tp in all_decls:
            if tp.is_raw_function:
                assert self._typesdict[tp] is None
                self._typesdict[tp] = len(self.cffi_types)
                self.cffi_types.append(tp)     # placeholder
                for tp1 in tp.args:
                    assert isinstance(tp1, (model.VoidType,
                                            model.BasePrimitiveType,
                                            model.PointerType,
                                            model.StructOrUnionOrEnum,
                                            model.FunctionPtrType))
                    if self._typesdict[tp1] is None:
                        self._typesdict[tp1] = len(self.cffi_types)
                    self.cffi_types.append(tp1)   # placeholder
                self.cffi_types.append('END')     # placeholder
        #
        # prepare all OTHER bytecode sequences
        for tp in all_decls:
            if not tp.is_raw_function and self._typesdict[tp] is None:
                self._typesdict[tp] = len(self.cffi_types)
                self.cffi_types.append(tp)        # placeholder
                if tp.is_array_type and tp.length is not None:
                    self.cffi_types.append('LEN') # placeholder
        assert None not in self._typesdict.values()
        #
        # collect all structs and unions and enums
        self._struct_unions = {}
        self._enums = {}
        for tp in all_decls:
            if isinstance(tp, model.StructOrUnion):
                self._struct_unions[tp] = None
            elif isinstance(tp, model.EnumType):
                self._enums[tp] = None
        for i, tp in enumerate(sorted(self._struct_unions,
                                      key=lambda tp: tp.name)):
            self._struct_unions[tp] = i
        for i, tp in enumerate(sorted(self._enums,
                                      key=lambda tp: tp.name)):
            self._enums[tp] = i
        #
        # emit all bytecode sequences now
        for tp in all_decls:
            method = getattr(self, '_emit_bytecode_' + tp.__class__.__name__)
            method(tp, self._typesdict[tp])
        #
        # consistency check
        for op in self.cffi_types:
            assert isinstance(op, CffiOp)
        self.cffi_types = tuple(self.cffi_types)    # don't change any more

    def _enum_fields(self, tp):
        # When producing C, expand all anonymous struct/union fields.
        # That's necessary to have C code checking the offsets of the
        # individual fields contained in them.  When producing Python,
        # don't do it and instead write it like it is, with the
        # corresponding fields having an empty name.  Empty names are
        # recognized at runtime when we import the generated Python
        # file.
        expand_anonymous_struct_union = not self.target_is_python
        return tp.enumfields(expand_anonymous_struct_union)

    def _do_collect_type(self, tp):
        if not isinstance(tp, model.BaseTypeByIdentity):
            if isinstance(tp, tuple):
                for x in tp:
                    self._do_collect_type(x)
            return
        if tp not in self._typesdict:
            self._typesdict[tp] = None
            if isinstance(tp, model.FunctionPtrType):
                self._do_collect_type(tp.as_raw_function())
            elif isinstance(tp, model.StructOrUnion):
                if tp.fldtypes is not None and (
                        tp not in self.ffi._parser._included_declarations):
                    for name1, tp1, _, _ in self._enum_fields(tp):
                        self._do_collect_type(self._field_type(tp, name1, tp1))
            else:
                for _, x in tp._get_items():
                    self._do_collect_type(x)

    def _generate(self, step_name):
        lst = self.ffi._parser._declarations.items()
        for name, (tp, quals) in sorted(lst):
            kind, realname = name.split(' ', 1)
            try:
                method = getattr(self, '_generate_cpy_%s_%s' % (kind,
                                                                step_name))
            except AttributeError:
                raise VerificationError(
                    "not implemented in recompile(): %r" % name)
            try:
                self._current_quals = quals
                method(tp, realname)
            except Exception as e:
                model.attach_exception_info(e, name)
                raise

    # ----------

    ALL_STEPS = ["global", "field", "struct_union", "enum", "typename"]

    def collect_step_tables(self):
        # collect the declarations for '_cffi_globals', '_cffi_typenames', etc.
        self._lsts = {}
        for step_name in self.ALL_STEPS:
            self._lsts[step_name] = []
        self._seen_struct_unions = set()
        self._generate("ctx")
        self._add_missing_struct_unions()
        #
        for step_name in self.ALL_STEPS:
            lst = self._lsts[step_name]
            if step_name != "field":
                lst.sort(key=lambda entry: entry.name)
            self._lsts[step_name] = tuple(lst)    # don't change any more
        #
        # check for a possible internal inconsistency: _cffi_struct_unions
        # should have been generated with exactly self._struct_unions
        lst = self._lsts["struct_union"]
        for tp, i in self._struct_unions.items():
            assert i < len(lst)
            assert lst[i].name == tp.name
        assert len(lst) == len(self._struct_unions)
        # same with enums
        lst = self._lsts["enum"]
        for tp, i in self._enums.items():
            assert i < len(lst)
            assert lst[i].name == tp.name
        assert len(lst) == len(self._enums)

    # ----------

    def _prnt(self, what=''):
        self._f.write(what + '\n')

    def write_source_to_f(self, f, preamble):
        if self.target_is_python:
            assert preamble is None
            self.write_py_source_to_f(f)
        else:
            assert preamble is not None
            self.write_c_source_to_f(f, preamble)

    def _rel_readlines(self, filename):
        with open(os.path.join(os.path.dirname(__file__), filename), 'r') as g:
            return g.readlines()

    def write_c_source_to_f(self, f, preamble):
        self._f = f
        prnt = self._prnt
        if self.ffi._embedding is not None:
            prnt('#define _CFFI_USE_EMBEDDING')
        if not USE_LIMITED_API:
            prnt('#define _CFFI_NO_LIMITED_API')
        #
        # first the '#include' (actually done by inlining the file's content)
        lines = self._rel_readlines('_cffi_include.h')
        i = lines.index('#include "parse_c_type.h"\n')
        lines[i:i+1] = self._rel_readlines('parse_c_type.h')
        prnt(''.join(lines))
        #
        # if we have ffi._embedding != None, we give it here as a macro
        # and include an extra file
        base_module_name = self.module_name.split('.')[-1]
        if self.ffi._embedding is not None:
            prnt('#define _CFFI_MODULE_NAME  "%s"' % (self.module_name,))
            prnt('static const char _CFFI_PYTHON_STARTUP_CODE[] = {')
            self._print_string_literal_in_array(self.ffi._embedding)
            prnt('0 };')
            prnt('#ifdef PYPY_VERSION')
            prnt('# define _CFFI_PYTHON_STARTUP_FUNC  _cffi_pypyinit_%s' % (
                base_module_name,))
            prnt('#else')
            prnt('# define _CFFI_PYTHON_STARTUP_FUNC  PyInit_%s' % (
                base_module_name,))
            prnt('#endif')
            lines = self._rel_readlines('_embedding.h')
            i = lines.index('#include "_cffi_errors.h"\n')
            lines[i:i+1] = self._rel_readlines('_cffi_errors.h')
            prnt(''.join(lines))
            self.needs_version(VERSION_EMBEDDED)
        #
        # then paste the C source given by the user, verbatim.
        prnt('/************************************************************/')
        prnt()
        prnt(preamble)
        prnt()
        prnt('/************************************************************/')
        prnt()
        #
        # the declaration of '_cffi_types'
        prnt('static void *_cffi_types[] = {')
        typeindex2type = dict([(i, tp) for (tp, i) in self._typesdict.items()])
        for i, op in enumerate(self.cffi_types):
            comment = ''
            if i in typeindex2type:
                comment = ' // ' + typeindex2type[i]._get_c_name()
            prnt('/* %2d */ %s,%s' % (i, op.as_c_expr(), comment))
        if not self.cffi_types:
            prnt('  0')
        prnt('};')
        prnt()
        #
        # call generate_cpy_xxx_decl(), for every xxx found from
        # ffi._parser._declarations.  This generates all the functions.
        self._seen_constants = set()
        self._generate("decl")
        #
        # the declaration of '_cffi_globals' and '_cffi_typenames'
        nums = {}
        for step_name in self.ALL_STEPS:
            lst = self._lsts[step_name]
            nums[step_name] = len(lst)
            if nums[step_name] > 0:
                prnt('static const struct _cffi_%s_s _cffi_%ss[] = {' % (
                    step_name, step_name))
                for entry in lst:
                    prnt(entry.as_c_expr())
                prnt('};')
                prnt()
        #
        # the declaration of '_cffi_includes'
        if self.ffi._included_ffis:
            prnt('static const char * const _cffi_includes[] = {')
            for ffi_to_include in self.ffi._included_ffis:
                try:
                    included_module_name, included_source = (
                        ffi_to_include._assigned_source[:2])
                except AttributeError:
                    raise VerificationError(
                        "ffi object %r includes %r, but the latter has not "
                        "been prepared with set_source()" % (
                            self.ffi, ffi_to_include,))
                if included_source is None:
                    raise VerificationError(
                        "not implemented yet: ffi.include() of a Python-based "
                        "ffi inside a C-based ffi")
                prnt('  "%s",' % (included_module_name,))
            prnt('  NULL')
            prnt('};')
            prnt()
        #
        # the declaration of '_cffi_type_context'
        prnt('static const struct _cffi_type_context_s _cffi_type_context = {')
        prnt('  _cffi_types,')
        for step_name in self.ALL_STEPS:
            if nums[step_name] > 0:
                prnt('  _cffi_%ss,' % step_name)
            else:
                prnt('  NULL,  /* no %ss */' % step_name)
        for step_name in self.ALL_STEPS:
            if step_name != "field":
