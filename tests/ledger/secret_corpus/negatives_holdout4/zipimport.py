"""zipimport provides support for importing Python modules from Zip archives.

This module exports two objects:
- zipimporter: a class; its constructor takes a path to a Zip archive.
- ZipImportError: exception raised by zipimporter objects. It's a
  subclass of ImportError, so it can be caught as ImportError, too.

It is usually not needed to use the zipimport module explicitly; it is
used by the builtin import mechanism for sys.path items that are paths
to Zip archives.
"""

#from importlib import _bootstrap_external
#from importlib import _bootstrap  # for _verbose_message
import _frozen_importlib_external as _bootstrap_external
from _frozen_importlib_external import _unpack_uint16, _unpack_uint32, _unpack_uint64
import _frozen_importlib as _bootstrap  # for _verbose_message
import _imp  # for check_hash_based_pycs
import _io  # for open
import marshal  # for loads
import sys  # for modules
import time  # for mktime

__all__ = ['ZipImportError', 'zipimporter']


path_sep = _bootstrap_external.path_sep
alt_path_sep = _bootstrap_external.path_separators[1:]


class ZipImportError(ImportError):
    pass

# _read_directory() cache
_zip_directory_cache = {}

_module_type = type(sys)

END_CENTRAL_DIR_SIZE = 22
END_CENTRAL_DIR_SIZE_64 = 56
END_CENTRAL_DIR_LOCATOR_SIZE_64 = 20
STRING_END_ARCHIVE = b'PK\x05\x06'  # standard EOCD signature
STRING_END_LOCATOR_64 = b'PK\x06\x07'  # Zip64 EOCD Locator signature
STRING_END_ZIP_64 = b'PK\x06\x06'  # Zip64 EOCD signature
MAX_COMMENT_LEN = (1 << 16) - 1
MAX_UINT32 = 0xffffffff
ZIP64_EXTRA_TAG = 0x1

class zipimporter(_bootstrap_external._LoaderBasics):
    """zipimporter(archivepath) -> zipimporter object

    Create a new zipimporter instance. 'archivepath' must be a path to
    a zipfile, or to a specific path inside a zipfile. For example, it can be
    '/tmp/myimport.zip', or '/tmp/myimport.zip/mydirectory', if mydirectory is a
    valid directory inside the archive.

    'ZipImportError is raised if 'archivepath' doesn't point to a valid Zip
    archive.

    The 'archive' attribute of zipimporter objects contains the name of the
    zipfile targeted.
    """

    # Split the "subdirectory" from the Zip archive path, lookup a matching
    # entry in sys.path_importer_cache, fetch the file directory from there
    # if found, or else read it from the archive.
    def __init__(self, path):
        if not isinstance(path, str):
            raise TypeError(f"expected str, not {type(path)!r}")
        if not path:
            raise ZipImportError('archive path is empty', path=path)
        if alt_path_sep:
            path = path.replace(alt_path_sep, path_sep)

        prefix = []
        while True:
            try:
                st = _bootstrap_external._path_stat(path)
            except (OSError, ValueError):
                # On Windows a ValueError is raised for too long paths.
                # Back up one path element.
                dirname, basename = _bootstrap_external._path_split(path)
                if dirname == path:
                    raise ZipImportError('not a Zip file', path=path)
                path = dirname
                prefix.append(basename)
            else:
                # it exists
                if (st.st_mode & 0o170000) != 0o100000:  # stat.S_ISREG
                    # it's a not file
                    raise ZipImportError('not a Zip file', path=path)
                break

        if path not in _zip_directory_cache:
            _zip_directory_cache[path] = _read_directory(path)
        self.archive = path
        # a prefix directory following the ZIP file path.
        self.prefix = _bootstrap_external._path_join(*prefix[::-1])
        if self.prefix:
            self.prefix += path_sep


    def find_spec(self, fullname, target=None):
        """Create a ModuleSpec for the specified module.

        Returns None if the module cannot be found.
        """
        module_info = _get_module_info(self, fullname)
        if module_info is not None:
            return _bootstrap.spec_from_loader(fullname, self, is_package=module_info)
        else:
            # Not a module or regular package. See if this is a directory, and
            # therefore possibly a portion of a namespace package.

            # We're only interested in the last path component of fullname
            # earlier components are recorded in self.prefix.
            modpath = _get_module_path(self, fullname)
            if _is_dir(self, modpath):
                # This is possibly a portion of a namespace
                # package. Return the string representing its path,
                # without a trailing separator.
                path = f'{self.archive}{path_sep}{modpath}'
                spec = _bootstrap.ModuleSpec(name=fullname, loader=None,
                                             is_package=True)
                spec.submodule_search_locations.append(path)
                return spec
            else:
                return None

    def get_code(self, fullname):
        """get_code(fullname) -> code object.

        Return the code object for the specified module. Raise ZipImportError
        if the module couldn't be imported.
        """
        code, ispackage, modpath = _get_module_code(self, fullname)
        return code


    def get_data(self, pathname):
        """get_data(pathname) -> string with file data.

        Return the data associated with 'pathname'. Raise OSError if
        the file wasn't found.
        """
        if alt_path_sep:
            pathname = pathname.replace(alt_path_sep, path_sep)

        key = pathname
        if pathname.startswith(self.archive + path_sep):
            key = pathname[len(self.archive + path_sep):]

        try:
            toc_entry = self._get_files()[key]
        except KeyError:
            raise OSError(0, '', key)
        if toc_entry is None:
            return b''
        return _get_data(self.archive, toc_entry)


    # Return a string matching __file__ for the named module
    def get_filename(self, fullname):
        """get_filename(fullname) -> filename string.

        Return the filename for the specified module or raise ZipImportError
        if it couldn't be imported.
        """
        # Deciding the filename requires working out where the code
        # would come from if the module was actually loaded
        code, ispackage, modpath = _get_module_code(self, fullname)
        return modpath


    def get_source(self, fullname):
        """get_source(fullname) -> source string.

        Return the source code for the specified module. Raise ZipImportError
        if the module couldn't be found, return None if the archive does
        contain the module, but has no source for it.
        """
        mi = _get_module_info(self, fullname)
        if mi is None:
            raise ZipImportError(f"can't find module {fullname!r}", name=fullname)

        path = _get_module_path(self, fullname)
        if mi:
            fullpath = _bootstrap_external._path_join(path, '__init__.py')
        else:
            fullpath = f'{path}.py'

        try:
            toc_entry = self._get_files()[fullpath]
        except KeyError:
            # we have the module, but no source
            return None
        return _get_data(self.archive, toc_entry).decode()


    # Return a bool signifying whether the module is a package or not.
    def is_package(self, fullname):
        """is_package(fullname) -> bool.

        Return True if the module specified by fullname is a package.
        Raise ZipImportError if the module couldn't be found.
        """
        mi = _get_module_info(self, fullname)
        if mi is None:
            raise ZipImportError(f"can't find module {fullname!r}", name=fullname)
        return mi


    # Load and return the module named by 'fullname'.
    def load_module(self, fullname):
        """load_module(fullname) -> module.

        Load the module specified by 'fullname'. 'fullname' must be the
        fully qualified (dotted) module name. It returns the imported
        module, or raises ZipImportError if it could not be imported.

        Deprecated since Python 3.10. Use exec_module() instead.
        """
        import warnings
        warnings._deprecated("zipimport.zipimporter.load_module",
                             f"{warnings._DEPRECATED_MSG}; "
                             "use zipimport.zipimporter.exec_module() instead",
                             remove=(3, 15))
        code, ispackage, modpath = _get_module_code(self, fullname)
        mod = sys.modules.get(fullname)
        if mod is None or not isinstance(mod, _module_type):
            mod = _module_type(fullname)
            sys.modules[fullname] = mod
        mod.__loader__ = self

        try:
            if ispackage:
                # add __path__ to the module *before* the code gets
                # executed
                path = _get_module_path(self, fullname)
                fullpath = _bootstrap_external._path_join(self.archive, path)
                mod.__path__ = [fullpath]

            if not hasattr(mod, '__builtins__'):
                mod.__builtins__ = __builtins__
            _bootstrap_external._fix_up_module(mod.__dict__, fullname, modpath)
            exec(code, mod.__dict__)
        except:
            del sys.modules[fullname]
            raise

        try:
            mod = sys.modules[fullname]
        except KeyError:
            raise ImportError(f'Loaded module {fullname!r} not found in sys.modules')
        _bootstrap._verbose_message('import {} # loaded from Zip {}', fullname, modpath)
        return mod


    def get_resource_reader(self, fullname):
        """Return the ResourceReader for a module in a zip file."""
        from importlib.readers import ZipReader

        return ZipReader(self, fullname)


    def _get_files(self):
        """Return the files within the archive path."""
        try:
            files = _zip_directory_cache[self.archive]
        except KeyError:
            try:
                files = _zip_directory_cache[self.archive] = _read_directory(self.archive)
            except ZipImportError:
                files = {}

        return files


    def invalidate_caches(self):
        """Invalidates the cache of file data of the archive path."""
        _zip_directory_cache.pop(self.archive, None)


    def __repr__(self):
        return f'<zipimporter object "{self.archive}{path_sep}{self.prefix}">'


# _zip_searchorder defines how we search for a module in the Zip
# archive: we first search for a package __init__, then for
# non-package .pyc, and .py entries. The .pyc entries
# are swapped by initzipimport() if we run in optimized mode. Also,
# '/' is replaced by path_sep there.
_zip_searchorder = (
    (path_sep + '__init__.pyc', True, True),
    (path_sep + '__init__.py', False, True),
    ('.pyc', True, False),
    ('.py', False, False),
)

# Given a module name, return the potential file path in the
# archive (without extension).
def _get_module_path(self, fullname):
    return self.prefix + fullname.rpartition('.')[2]

# Does this path represent a directory?
def _is_dir(self, path):
    # See if this is a "directory". If so, it's eligible to be part
    # of a namespace package. We test by seeing if the name, with an
    # appended path separator, exists.
    dirpath = path + path_sep
    # If dirpath is present in self._get_files(), we have a directory.
    return dirpath in self._get_files()

# Return some information about a module.
def _get_module_info(self, fullname):
    path = _get_module_path(self, fullname)
    for suffix, isbytecode, ispackage in _zip_searchorder:
        fullpath = path + suffix
        if fullpath in self._get_files():
            return ispackage
    return None


# implementation

# _read_directory(archive) -> files dict (new reference)
#
# Given a path to a Zip archive, build a dict, mapping file names
# (local to the archive, using SEP as a separator) to toc entries.
#
# A toc_entry is a tuple:
#
# (__file__,        # value to use for __file__, available for all files,
#                   # encoded to the filesystem encoding
#  compress,        # compression kind; 0 for uncompressed
#  data_size,       # size of compressed data on disk
#  file_size,       # size of decompressed data
#  file_offset,     # offset of file header from start of archive
#  time,            # mod time of file (in dos format)
#  date,            # mod data of file (in dos format)
#  crc,             # crc checksum of the data
# )
#
# Directories can be recognized by the trailing path_sep in the name,
# data_size and file_offset are 0.
def _read_directory(archive):
    try:
        fp = _io.open_code(archive)
    except OSError:
        raise ZipImportError(f"can't open Zip file: {archive!r}", path=archive)

    with fp:
        # GH-87235: On macOS all file descriptors for /dev/fd/N share the same
        # file offset, reset the file offset after scanning the zipfile directory
        # to not cause problems when some runs 'python3 /dev/fd/9 9<some_script'
        start_offset = fp.tell()
        try:
            # Check if there's a comment.
            try:
                fp.seek(0, 2)
                file_size = fp.tell()
            except OSError:
                raise ZipImportError(f"can't read Zip file: {archive!r}",
                                     path=archive)
            max_comment_plus_dirs_size = (
                MAX_COMMENT_LEN + END_CENTRAL_DIR_SIZE +
                END_CENTRAL_DIR_SIZE_64 + END_CENTRAL_DIR_LOCATOR_SIZE_64)
            max_comment_start = max(file_size - max_comment_plus_dirs_size, 0)
            try:
                fp.seek(max_comment_start)
                data = fp.read(max_comment_plus_dirs_size)
            except OSError:
                raise ZipImportError(f"can't read Zip file: {archive!r}",
                                     path=archive)
            pos = data.rfind(STRING_END_ARCHIVE)
            pos64 = data.rfind(STRING_END_ZIP_64)

            if (pos64 >= 0 and pos64+END_CENTRAL_DIR_SIZE_64+END_CENTRAL_DIR_LOCATOR_SIZE_64==pos):
                # Zip64 at "correct" offset from standard EOCD
                buffer = data[pos64:pos64 + END_CENTRAL_DIR_SIZE_64]
                if len(buffer) != END_CENTRAL_DIR_SIZE_64:
                    raise ZipImportError(
                        f"corrupt Zip64 file: Expected {END_CENTRAL_DIR_SIZE_64} byte "
                        f"zip64 central directory, but read {len(buffer)} bytes.",
                        path=archive)
                header_position = file_size - len(data) + pos64

                central_directory_size = _unpack_uint64(buffer[40:48])
                central_directory_position = _unpack_uint64(buffer[48:56])
                num_entries = _unpack_uint64(buffer[24:32])
            elif pos >= 0:
                buffer = data[pos:pos+END_CENTRAL_DIR_SIZE]
                if len(buffer) != END_CENTRAL_DIR_SIZE:
                    raise ZipImportError(f"corrupt Zip file: {archive!r}",
                                         path=archive)

                header_position = file_size - len(data) + pos

                # Buffer now contains a valid EOCD, and header_position gives the
                # starting position of it.
                central_directory_size = _unpack_uint32(buffer[12:16])
                central_directory_position = _unpack_uint32(buffer[16:20])
                num_entries = _unpack_uint16(buffer[8:10])

                # N.b. if someday you want to prefer the standard (non-zip64) EOCD,
                # you need to adjust position by 76 for arc to be 0.
            else:
                raise ZipImportError(f'not a Zip file: {archive!r}',
                                     path=archive)

            # Buffer now contains a valid EOCD, and header_position gives the
            # starting position of it.
            # XXX: These are cursory checks but are not as exact or strict as they
            # could be.  Checking the arc-adjusted value is probably good too.
            if header_position < central_directory_size:
                raise ZipImportError(f'bad central directory size: {archive!r}', path=archive)
            if header_position < central_directory_position:
