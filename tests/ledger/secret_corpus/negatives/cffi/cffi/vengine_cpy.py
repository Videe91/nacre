#
# DEPRECATED: implementation for ffi.verify()
#
import sys
from . import model
from .error import VerificationError
from . import _imp_emulation as imp


class VCPythonEngine:
    _class_key = 'x'
    _gen_python_module = True

    def __init__(self, verifier):
        self.verifier = verifier
        self.ffi = verifier.ffi
        self._struct_pending_verification = {}
        self._types_of_builtin_functions = {}

    def patch_extension_kwds(self, kwds):
        pass

    def find_module(self, module_name, path, so_suffixes):
        try:
            f, filename, descr = imp.find_module(module_name, path)
        except ImportError:
            return None
        if f is not None:
            f.close()
        # Note that after a setuptools installation, there are both .py
        # and .so files with the same basename.  The code here relies on
        # imp.find_module() locating the .so in priority.
        if descr[0] not in so_suffixes:
            return None
        return filename

    def collect_types(self):
        self._typesdict = {}
        self._generate("collecttype")

    def _prnt(self, what=''):
        self._f.write(what + '\n')

    def _gettypenum(self, type):
        # a KeyError here is a bug.  please report it! :-)
        return self._typesdict[type]

    def _do_collect_type(self, tp):
        if ((not isinstance(tp, model.PrimitiveType)
             or tp.name == 'long double')
                and tp not in self._typesdict):
            num = len(self._typesdict)
            self._typesdict[tp] = num

    def write_source_to_f(self):
        self.collect_types()
        #
        # The new module will have a _cffi_setup() function that receives
        # objects from the ffi world, and that calls some setup code in
        # the module.  This setup code is split in several independent
        # functions, e.g. one per constant.  The functions are "chained"
        # by ending in a tail call to each other.
        #
        # This is further split in two chained lists, depending on if we
        # can do it at import-time or if we must wait for _cffi_setup() to
        # provide us with the <ctype> objects.  This is needed because we
        # need the values of the enum constants in order to build the
        # <ctype 'enum'> that we may have to pass to _cffi_setup().
        #
        # The following two 'chained_list_constants' items contains
        # the head of these two chained lists, as a string that gives the
        # call to do, if any.
        self._chained_list_constants = ['((void)lib,0)', '((void)lib,0)']
        #
        prnt = self._prnt
        # first paste some standard set of lines that are mostly '#define'
        prnt(cffimod_header)
        prnt()
        # then paste the C source given by the user, verbatim.
        prnt(self.verifier.preamble)
        prnt()
        #
        # call generate_cpy_xxx_decl(), for every xxx found from
        # ffi._parser._declarations.  This generates all the functions.
        self._generate("decl")
        #
        # implement the function _cffi_setup_custom() as calling the
        # head of the chained list.
        self._generate_setup_custom()
        prnt()
        #
        # produce the method table, including the entries for the
        # generated Python->C function wrappers, which are done
        # by generate_cpy_function_method().
        prnt('static PyMethodDef _cffi_methods[] = {')
        self._generate("method")
        prnt('  {"_cffi_setup", _cffi_setup, METH_VARARGS, NULL},')
        prnt('  {NULL, NULL, 0, NULL}    /* Sentinel */')
        prnt('};')
        prnt()
        #
        # standard init.
        modname = self.verifier.get_module_name()
        constants = self._chained_list_constants[False]
        prnt('static struct PyModuleDef _cffi_module_def = {')
        prnt('  PyModuleDef_HEAD_INIT,')
        prnt('  "%s",' % modname)
        prnt('  NULL,')
        prnt('  -1,')
        prnt('  _cffi_methods,')
        prnt('  NULL, NULL, NULL, NULL')
        prnt('};')
        prnt()
        prnt('PyMODINIT_FUNC')
        prnt('PyInit_%s(void)' % modname)
        prnt('{')
        prnt('  PyObject *lib;')
        prnt('  lib = PyModule_Create(&_cffi_module_def);')
        prnt('  if (lib == NULL)')
        prnt('    return NULL;')
        prnt('  if (%s < 0 || _cffi_init() < 0) {' % (constants,))
        prnt('    Py_DECREF(lib);')
        prnt('    return NULL;')
        prnt('  }')
        prnt('#if Py_GIL_DISABLED')
        prnt('  PyUnstable_Module_SetGIL(lib, Py_MOD_GIL_NOT_USED);')
        prnt('#endif')
        prnt('  return lib;')
        prnt('}')
        prnt()

    def load_library(self, flags=None):
        # XXX review all usages of 'self' here!
        # import it as a new extension module
        imp.acquire_lock()
        try:
            if hasattr(sys, "getdlopenflags"):
                previous_flags = sys.getdlopenflags()
            try:
                if hasattr(sys, "setdlopenflags") and flags is not None:
                    sys.setdlopenflags(flags)
                module = imp.load_dynamic(self.verifier.get_module_name(),
                                          self.verifier.modulefilename)
            except ImportError as e:
                error = "importing %r: %s" % (self.verifier.modulefilename, e)
                raise VerificationError(error)
            finally:
                if hasattr(sys, "setdlopenflags"):
                    sys.setdlopenflags(previous_flags)
        finally:
            imp.release_lock()
        #
        # call loading_cpy_struct() to get the struct layout inferred by
        # the C compiler
        self._load(module, 'loading')
        #
        # the C code will need the <ctype> objects.  Collect them in
        # order in a list.
        revmapping = dict([(value, key)
                           for (key, value) in self._typesdict.items()])
        lst = [revmapping[i] for i in range(len(revmapping))]
        lst = list(map(self.ffi._get_cached_btype, lst))
        #
        # build the FFILibrary class and instance and call _cffi_setup().
        # this will set up some fields like '_cffi_types', and only then
        # it will invoke the chained list of functions that will really
        # build (notably) the constant objects, as <cdata> if they are
        # pointers, and store them as attributes on the 'library' object.
        class FFILibrary:
            _cffi_python_module = module
            _cffi_ffi = self.ffi
            _cffi_dir = []
            def __dir__(self):
                return FFILibrary._cffi_dir + list(self.__dict__)
        library = FFILibrary()
        if module._cffi_setup(lst, VerificationError, library):
            import warnings
            warnings.warn("reimporting %r might overwrite older definitions"
                          % (self.verifier.get_module_name()))
        #
        # finally, call the loaded_cpy_xxx() functions.  This will perform
        # the final adjustments, like copying the Python->C wrapper
        # functions from the module to the 'library' object, and setting
        # up the FFILibrary class with properties for the global C variables.
        self._load(module, 'loaded', library=library)
        module._cffi_original_ffi = self.ffi
        module._cffi_types_of_builtin_funcs = self._types_of_builtin_functions
        return library

    def _get_declarations(self):
        lst = [(key, tp) for (key, (tp, qual)) in
                                self.ffi._parser._declarations.items()]
        lst.sort()
        return lst

    def _generate(self, step_name):
        for name, tp in self._get_declarations():
            kind, realname = name.split(' ', 1)
            try:
                method = getattr(self, '_generate_cpy_%s_%s' % (kind,
                                                                step_name))
            except AttributeError:
                raise VerificationError(
                    "not implemented in verify(): %r" % name)
            try:
                method(tp, realname)
            except Exception as e:
                model.attach_exception_info(e, name)
                raise

    def _load(self, module, step_name, **kwds):
        for name, tp in self._get_declarations():
            kind, realname = name.split(' ', 1)
            method = getattr(self, '_%s_cpy_%s' % (step_name, kind))
            try:
                method(tp, realname, module, **kwds)
            except Exception as e:
                model.attach_exception_info(e, name)
                raise

    def _generate_nothing(self, tp, name):
        pass

    def _loaded_noop(self, tp, name, module, **kwds):
        pass

    # ----------

    def _convert_funcarg_to_c(self, tp, fromvar, tovar, errcode):
        extraarg = ''
        if isinstance(tp, model.PrimitiveType):
            if tp.is_integer_type() and tp.name != '_Bool':
                converter = '_cffi_to_c_int'
                extraarg = ', %s' % tp.name
            elif tp.is_complex_type():
                raise VerificationError(
                    "not implemented in verify(): complex types")
            else:
                converter = '(%s)_cffi_to_c_%s' % (tp.get_c_name(''),
                                                   tp.name.replace(' ', '_'))
            errvalue = '-1'
        #
        elif isinstance(tp, model.PointerType):
            self._convert_funcarg_to_c_ptr_or_array(tp, fromvar,
                                                    tovar, errcode)
            return
        #
        elif isinstance(tp, (model.StructOrUnion, model.EnumType)):
            # a struct (not a struct pointer) as a function argument
            self._prnt('  if (_cffi_to_c((char *)&%s, _cffi_type(%d), %s) < 0)'
                      % (tovar, self._gettypenum(tp), fromvar))
            self._prnt('    %s;' % errcode)
            return
        #
        elif isinstance(tp, model.FunctionPtrType):
            converter = '(%s)_cffi_to_c_pointer' % tp.get_c_name('')
            extraarg = ', _cffi_type(%d)' % self._gettypenum(tp)
            errvalue = 'NULL'
        #
        else:
            raise NotImplementedError(tp)
        #
        self._prnt('  %s = %s(%s%s);' % (tovar, converter, fromvar, extraarg))
        self._prnt('  if (%s == (%s)%s && PyErr_Occurred())' % (
            tovar, tp.get_c_name(''), errvalue))
        self._prnt('    %s;' % errcode)

    def _extra_local_variables(self, tp, localvars, freelines):
        if isinstance(tp, model.PointerType):
            localvars.add('Py_ssize_t datasize')
            localvars.add('struct _cffi_freeme_s *large_args_free = NULL')
            freelines.add('if (large_args_free != NULL)'
                          ' _cffi_free_array_arguments(large_args_free);')

    def _convert_funcarg_to_c_ptr_or_array(self, tp, fromvar, tovar, errcode):
        self._prnt('  datasize = _cffi_prepare_pointer_call_argument(')
        self._prnt('      _cffi_type(%d), %s, (char **)&%s);' % (
            self._gettypenum(tp), fromvar, tovar))
        self._prnt('  if (datasize != 0) {')
        self._prnt('    %s = ((size_t)datasize) <= 640 ? '
                   'alloca((size_t)datasize) : NULL;' % (tovar,))
        self._prnt('    if (_cffi_convert_array_argument(_cffi_type(%d), %s, '
                   '(char **)&%s,' % (self._gettypenum(tp), fromvar, tovar))
        self._prnt('            datasize, &large_args_free) < 0)')
        self._prnt('      %s;' % errcode)
        self._prnt('  }')

    def _convert_expr_from_c(self, tp, var, context):
        if isinstance(tp, model.PrimitiveType):
            if tp.is_integer_type() and tp.name != '_Bool':
                return '_cffi_from_c_int(%s, %s)' % (var, tp.name)
            elif tp.name != 'long double':
                return '_cffi_from_c_%s(%s)' % (tp.name.replace(' ', '_'), var)
            else:
                return '_cffi_from_c_deref((char *)&%s, _cffi_type(%d))' % (
                    var, self._gettypenum(tp))
        elif isinstance(tp, (model.PointerType, model.FunctionPtrType)):
            return '_cffi_from_c_pointer((char *)%s, _cffi_type(%d))' % (
                var, self._gettypenum(tp))
        elif isinstance(tp, model.ArrayType):
            return '_cffi_from_c_pointer((char *)%s, _cffi_type(%d))' % (
                var, self._gettypenum(model.PointerType(tp.item)))
        elif isinstance(tp, model.StructOrUnion):
            if tp.fldnames is None:
                raise TypeError("'%s' is used as %s, but is opaque" % (
                    tp._get_c_name(), context))
            return '_cffi_from_c_struct((char *)&%s, _cffi_type(%d))' % (
                var, self._gettypenum(tp))
        elif isinstance(tp, model.EnumType):
            return '_cffi_from_c_deref((char *)&%s, _cffi_type(%d))' % (
                var, self._gettypenum(tp))
        else:
            raise NotImplementedError(tp)

    # ----------
    # typedefs: generates no code so far

    _generate_cpy_typedef_collecttype = _generate_nothing
    _generate_cpy_typedef_decl   = _generate_nothing
    _generate_cpy_typedef_method = _generate_nothing
    _loading_cpy_typedef         = _loaded_noop
    _loaded_cpy_typedef          = _loaded_noop

    # ----------
    # function declarations

    def _generate_cpy_function_collecttype(self, tp, name):
        assert isinstance(tp, model.FunctionPtrType)
        if tp.ellipsis:
            self._do_collect_type(tp)
        else:
            # don't call _do_collect_type(tp) in this common case,
            # otherwise test_autofilled_struct_as_argument fails
            for type in tp.args:
                self._do_collect_type(type)
            self._do_collect_type(tp.result)

    def _generate_cpy_function_decl(self, tp, name):
        assert isinstance(tp, model.FunctionPtrType)
        if tp.ellipsis:
            # cannot support vararg functions better than this: check for its
            # exact type (including the fixed arguments), and build it as a
            # constant function pointer (no CPython wrapper)
            self._generate_cpy_const(False, name, tp)
            return
        prnt = self._prnt
        numargs = len(tp.args)
        if numargs == 0:
            argname = 'noarg'
        elif numargs == 1:
            argname = 'arg0'
        else:
            argname = 'args'
        prnt('static PyObject *')
        prnt('_cffi_f_%s(PyObject *self, PyObject *%s)' % (name, argname))
        prnt('{')
        #
        context = 'argument of %s' % name
        for i, type in enumerate(tp.args):
            prnt('  %s;' % type.get_c_name(' x%d' % i, context))
        #
        localvars = set()
        freelines = set()
        for type in tp.args:
            self._extra_local_variables(type, localvars, freelines)
        for decl in sorted(localvars):
            prnt('  %s;' % (decl,))
        #
        if not isinstance(tp.result, model.VoidType):
            result_code = 'result = '
            context = 'result of %s' % name
            prnt('  %s;' % tp.result.get_c_name(' result', context))
            prnt('  PyObject *pyresult;')
        else:
            result_code = ''
        #
        if len(tp.args) > 1:
            rng = range(len(tp.args))
            for i in rng:
                prnt('  PyObject *arg%d;' % i)
            prnt()
            prnt('  if (!PyArg_ParseTuple(args, "%s:%s", %s))' % (
                'O' * numargs, name, ', '.join(['&arg%d' % i for i in rng])))
            prnt('    return NULL;')
        prnt()
        #
        for i, type in enumerate(tp.args):
            self._convert_funcarg_to_c(type, 'arg%d' % i, 'x%d' % i,
                                       'return NULL')
            prnt()
        #
        prnt('  Py_BEGIN_ALLOW_THREADS')
        prnt('  _cffi_restore_errno();')
        prnt('  { %s%s(%s); }' % (
            result_code, name,
            ', '.join(['x%d' % i for i in range(len(tp.args))])))
        prnt('  _cffi_save_errno();')
        prnt('  Py_END_ALLOW_THREADS')
        prnt()
        #
        prnt('  (void)self; /* unused */')
        if numargs == 0:
            prnt('  (void)noarg; /* unused */')
        if result_code:
            prnt('  pyresult = %s;' %
