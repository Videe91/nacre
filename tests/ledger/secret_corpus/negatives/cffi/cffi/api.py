import sys, types
from .lock import allocate_lock
from .error import CDefError
from . import model

try:
    basestring
except NameError:
    # Python 3.x
    basestring = str

_unspecified = object()



class FFI:
    r'''
    The main top-level class that you instantiate once, or once per module.

    Example usage:

        ffi = FFI()
        ffi.cdef("""
            int printf(const char *, ...);
        """)

        C = ffi.dlopen(None)   # standard library
        -or-
        C = ffi.verify()  # use a C compiler: verify the decl above is right

        C.printf("hello, %s!\n", ffi.new("char[]", "world"))
    '''

    def __init__(self, backend=None):
        """Create an FFI instance.  The 'backend' argument is used to
        select a non-default backend, mostly for tests.
        """
        if backend is None:
            # You need PyPy (>= 2.0 beta), or a CPython (>= 2.6) with
            # _cffi_backend.so compiled.
            import _cffi_backend as backend
            from . import __version__
            if backend.__version__ != __version__:
                # bad version!  Try to be as explicit as possible.
                if hasattr(backend, '__file__'):
                    # CPython
                    raise Exception("Version mismatch: this is the 'cffi' package version %s, located in %r.  When we import the top-level '_cffi_backend' extension module, we get version %s, located in %r.  The two versions should be equal; check your installation." % (
                        __version__, __file__,
                        backend.__version__, backend.__file__))
                else:
                    # PyPy
                    raise Exception("Version mismatch: this is the 'cffi' package version %s, located in %r.  This interpreter comes with a built-in '_cffi_backend' module, which is version %s.  The two versions should be equal; check your installation." % (
                        __version__, __file__, backend.__version__))
            # (If you insist you can also try to pass the option
            # 'backend=backend_ctypes.CTypesBackend()', but don't
            # rely on it!  It's probably not going to work well.)

        from . import cparser
        self._backend = backend
        self._lock = allocate_lock()
        self._parser = cparser.Parser()
        self._cached_btypes = {}
        self._parsed_types = types.ModuleType('parsed_types').__dict__
        self._new_types = types.ModuleType('new_types').__dict__
        self._function_caches = []
        self._libraries = []
        self._cdefsources = []
        self._included_ffis = []
        self._windows_unicode = None
        self._init_once_cache = {}
        self._cdef_version = None
        self._embedding = None
        self._typecache = model.get_typecache(backend)
        if hasattr(backend, 'set_ffi'):
            backend.set_ffi(self)
        for name in list(backend.__dict__):
            if name.startswith('RTLD_'):
                setattr(self, name, getattr(backend, name))
        #
        with self._lock:
            self.BVoidP = self._get_cached_btype(model.voidp_type)
            self.BCharA = self._get_cached_btype(model.char_array_type)
        if isinstance(backend, types.ModuleType):
            # _cffi_backend: attach these constants to the class
            if not hasattr(FFI, 'NULL'):
                FFI.NULL = self.cast(self.BVoidP, 0)
                FFI.CData, FFI.CType = backend._get_types()
        else:
            # ctypes backend: attach these constants to the instance
            self.NULL = self.cast(self.BVoidP, 0)
            self.CData, self.CType = backend._get_types()
        self.buffer = backend.buffer

    def cdef(self, csource, override=False, packed=False, pack=None):
        """Parse the given C source.  This registers all declared functions,
        types, and global variables.  The functions and global variables can
        then be accessed via either 'ffi.dlopen()' or 'ffi.verify()'.
        The types can be used in 'ffi.new()' and other functions.
        If 'packed' is specified as True, all structs declared inside this
        cdef are packed, i.e. laid out without any field alignment at all.
        Alternatively, 'pack' can be a small integer, and requests for
        alignment greater than that are ignored (pack=1 is equivalent to
        packed=True).
        """
        self._cdef(csource, override=override, packed=packed, pack=pack)

    def embedding_api(self, csource, packed=False, pack=None):
        self._cdef(csource, packed=packed, pack=pack, dllexport=True)
        if self._embedding is None:
            self._embedding = ''

    def _cdef(self, csource, override=False, **options):
        if not isinstance(csource, str):    # unicode, on Python 2
            if not isinstance(csource, basestring):
                raise TypeError("cdef() argument must be a string")
            csource = csource.encode('ascii')
        with self._lock:
            self._cdef_version = object()
            self._parser.parse(csource, override=override, **options)
            self._cdefsources.append(csource)
            if override:
                for cache in self._function_caches:
                    cache.clear()
            finishlist = self._parser._recomplete
            if finishlist:
                self._parser._recomplete = []
                for tp in finishlist:
                    tp.finish_backend_type(self, finishlist)

    def dlopen(self, name, flags=0):
        """Load and return a dynamic library identified by 'name'.
        The standard C library can be loaded by passing None.
        Note that functions and types declared by 'ffi.cdef()' are not
        linked to a particular library, just like C headers; in the
        library we only look for the actual (untyped) symbols.
        """
        if not (isinstance(name, basestring) or
                name is None or
                isinstance(name, self.CData)):
            raise TypeError("dlopen(name): name must be a file name, None, "
                            "or an already-opened 'void *' handle")
        with self._lock:
            lib, function_cache = _make_ffi_library(self, name, flags)
            self._function_caches.append(function_cache)
            self._libraries.append(lib)
        return lib

    def dlclose(self, lib):
        """Close a library obtained with ffi.dlopen().  After this call,
        access to functions or variables from the library will fail
        (possibly with a segmentation fault).
        """
        type(lib).__cffi_close__(lib)

    def _typeof_locked(self, cdecl):
        # call me with the lock!
        key = cdecl
        if key in self._parsed_types:
            return self._parsed_types[key]
        #
        if not isinstance(cdecl, str):    # unicode, on Python 2
            cdecl = cdecl.encode('ascii')
        #
        type = self._parser.parse_type(cdecl)
        really_a_function_type = type.is_raw_function
        if really_a_function_type:
            type = type.as_function_pointer()
        btype = self._get_cached_btype(type)
        result = btype, really_a_function_type
        self._parsed_types[key] = result
        return result

    def _typeof(self, cdecl, consider_function_as_funcptr=False):
        # string -> ctype object
        try:
            result = self._parsed_types[cdecl]
        except KeyError:
            with self._lock:
                result = self._typeof_locked(cdecl)
        #
        btype, really_a_function_type = result
        if really_a_function_type and not consider_function_as_funcptr:
            raise CDefError("the type %r is a function type, not a "
                            "pointer-to-function type" % (cdecl,))
        return btype

    def typeof(self, cdecl):
        """Parse the C type given as a string and return the
        corresponding <ctype> object.
        It can also be used on 'cdata' instance to get its C type.
        """
        if isinstance(cdecl, basestring):
            return self._typeof(cdecl)
        if isinstance(cdecl, self.CData):
            return self._backend.typeof(cdecl)
        if isinstance(cdecl, types.BuiltinFunctionType):
            res = _builtin_function_type(cdecl)
            if res is not None:
                return res
        if (isinstance(cdecl, types.FunctionType)
                and hasattr(cdecl, '_cffi_base_type')):
            with self._lock:
                return self._get_cached_btype(cdecl._cffi_base_type)
        raise TypeError(type(cdecl))

    def sizeof(self, cdecl):
        """Return the size in bytes of the argument.  It can be a
        string naming a C type, or a 'cdata' instance.
        """
        if isinstance(cdecl, basestring):
            BType = self._typeof(cdecl)
            return self._backend.sizeof(BType)
        else:
            return self._backend.sizeof(cdecl)

    def alignof(self, cdecl):
        """Return the natural alignment size in bytes of the C type
        given as a string.
        """
        if isinstance(cdecl, basestring):
            cdecl = self._typeof(cdecl)
        return self._backend.alignof(cdecl)

    def offsetof(self, cdecl, *fields_or_indexes):
        """Return the offset of the named field inside the given
        structure or array, which must be given as a C type name.
        You can give several field names in case of nested structures.
        You can also give numeric values which correspond to array
        items, in case of an array type.
        """
        if isinstance(cdecl, basestring):
            cdecl = self._typeof(cdecl)
        return self._typeoffsetof(cdecl, *fields_or_indexes)[1]

    def new(self, cdecl, init=None):
        """Allocate an instance according to the specified C type and
        return a pointer to it.  The specified C type must be either a
        pointer or an array: ``new('X *')`` allocates an X and returns
        a pointer to it, whereas ``new('X[n]')`` allocates an array of
        n X'es and returns an array referencing it (which works
        mostly like a pointer, like in C).  You can also use
        ``new('X[]', n)`` to allocate an array of a non-constant
        length n.

        The memory is initialized following the rules of declaring a
        global variable in C: by default it is zero-initialized, but
        an explicit initializer can be given which can be used to
        fill all or part of the memory.

        When the returned <cdata> object goes out of scope, the memory
        is freed.  In other words the returned <cdata> object has
        ownership of the value of type 'cdecl' that it points to.  This
        means that the raw data can be used as long as this object is
        kept alive, but must not be used for a longer time.  Be careful
        about that when copying the pointer to the memory somewhere
        else, e.g. into another structure.
        """
        if isinstance(cdecl, basestring):
            cdecl = self._typeof(cdecl)
        return self._backend.newp(cdecl, init)

    def new_allocator(self, alloc=None, free=None,
                      should_clear_after_alloc=True):
        """Return a new allocator, i.e. a function that behaves like ffi.new()
        but uses the provided low-level 'alloc' and 'free' functions.

        'alloc' is called with the size as argument.  If it returns NULL, a
        MemoryError is raised.  'free' is called with the result of 'alloc'
        as argument.  Both can be either Python function or directly C
        functions.  If 'free' is None, then no free function is called.
        If both 'alloc' and 'free' are None, the default is used.

        If 'should_clear_after_alloc' is set to False, then the memory
        returned by 'alloc' is assumed to be already cleared (or you are
        fine with garbage); otherwise CFFI will clear it.
        """
        compiled_ffi = self._backend.FFI()
        allocator = compiled_ffi.new_allocator(alloc, free,
                                               should_clear_after_alloc)
        def allocate(cdecl, init=None):
            if isinstance(cdecl, basestring):
                cdecl = self._typeof(cdecl)
            return allocator(cdecl, init)
        return allocate

    def cast(self, cdecl, source):
        """Similar to a C cast: returns an instance of the named C
        type initialized with the given 'source'.  The source is
        casted between integers or pointers of any type.
        """
        if isinstance(cdecl, basestring):
            cdecl = self._typeof(cdecl)
        return self._backend.cast(cdecl, source)

    def string(self, cdata, maxlen=-1):
        """Return a Python string (or unicode string) from the 'cdata'.
        If 'cdata' is a pointer or array of characters or bytes, returns
        the null-terminated string.  The returned string extends until
        the first null character, or at most 'maxlen' characters.  If
        'cdata' is an array then 'maxlen' defaults to its length.

        If 'cdata' is a pointer or array of wchar_t, returns a unicode
        string following the same rules.

        If 'cdata' is a single character or byte or a wchar_t, returns
        it as a string or unicode string.

        If 'cdata' is an enum, returns the value of the enumerator as a
        string, or 'NUMBER' if the value is out of range.
        """
        return self._backend.string(cdata, maxlen)

    def unpack(self, cdata, length):
        """Unpack an array of C data of the given length,
        returning a Python string/unicode/list.

        If 'cdata' is a pointer to 'char', returns a byte string.
        It does not stop at the first null.  This is equivalent to:
        ffi.buffer(cdata, length)[:]

        If 'cdata' is a pointer to 'wchar_t', returns a unicode string.
        'length' is measured in wchar_t's; it is not the size in bytes.

        If 'cdata' is a pointer to anything else, returns a list of
        'length' items.  This is a faster equivalent to:
        [cdata[i] for i in range(length)]
        """
        return self._backend.unpack(cdata, length)

   #def buffer(self, cdata, size=-1):
   #    """Return a read-write buffer object that references the raw C data
   #    pointed to by the given 'cdata'.  The 'cdata' must be a pointer or
   #    an array.  Can be passed to functions expecting a buffer, or directly
   #    manipulated with:
   #
   #        buf[:]          get a copy of it in a regular string, or
   #        buf[idx]        as a single character
   #        buf[:] = ...
   #        buf[idx] = ...  change the content
   #    """
   #    note that 'buffer' is a type, set on this instance by __init__

    def from_buffer(self, cdecl, python_buffer=_unspecified,
                    require_writable=False):
        """Return a cdata of the given type pointing to the data of the
        given Python object, which must support the buffer interface.
        Note that this is not meant to be used on the built-in types
        str or unicode (you can build 'char[]' arrays explicitly)
        but only on objects containing large quantities of raw data
        in some other format, like 'array.array' or numpy arrays.

        The first argument is optional and default to 'char[]'.
        """
        if python_buffer is _unspecified:
            cdecl, python_buffer = self.BCharA, cdecl
        elif isinstance(cdecl, basestring):
            cdecl = self._typeof(cdecl)
        return self._backend.from_buffer(cdecl, python_buffer,
                                         require_writable)

    def memmove(self, dest, src, n):
        """ffi.memmove(dest, src, n) copies n bytes of memory from src to dest.

        Like the C function memmove(), the memory areas may overlap;
        apart from that it behaves like the C function memcpy().

        'src' can be any cdata ptr or array, or any Python buffer object.
        'dest' can be any cdata ptr or array, or a writable Python buffer
        object.  The size to copy, 'n', is always measured in bytes.

        Unlike other methods, this one supports all Python buffer including
        byte strings and bytearrays---but it still does not support
        non-contiguous buffers.
        """
        return self._backend.memmove(dest, src, n)

