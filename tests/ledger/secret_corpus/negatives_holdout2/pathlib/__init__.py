"""Object-oriented filesystem paths.

This module provides classes to represent abstract paths and concrete
paths with operations that have semantics appropriate for different
operating systems.
"""

import io
import ntpath
import operator
import os
import posixpath
import sys
from errno import *
from glob import _StringGlobber, _no_recurse_symlinks
from itertools import chain
from stat import S_ISDIR, S_ISREG, S_ISSOCK, S_ISBLK, S_ISCHR, S_ISFIFO
from _collections_abc import Sequence

try:
    import pwd
except ImportError:
    pwd = None
try:
    import grp
except ImportError:
    grp = None

from pathlib._os import (
    PathInfo, DirEntryInfo,
    ensure_different_files, ensure_distinct_paths,
    copyfile2, copyfileobj, magic_open, copy_info,
)


__all__ = [
    "UnsupportedOperation",
    "PurePath", "PurePosixPath", "PureWindowsPath",
    "Path", "PosixPath", "WindowsPath",
    ]


class UnsupportedOperation(NotImplementedError):
    """An exception that is raised when an unsupported operation is attempted.
    """
    pass


class _PathParents(Sequence):
    """This object provides sequence-like access to the logical ancestors
    of a path.  Don't try to construct it yourself."""
    __slots__ = ('_path', '_drv', '_root', '_tail')

    def __init__(self, path):
        self._path = path
        self._drv = path.drive
        self._root = path.root
        self._tail = path._tail

    def __len__(self):
        return len(self._tail)

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            return tuple(self[i] for i in range(*idx.indices(len(self))))

        if idx >= len(self) or idx < -len(self):
            raise IndexError(idx)
        if idx < 0:
            idx += len(self)
        return self._path._from_parsed_parts(self._drv, self._root,
                                             self._tail[:-idx - 1])

    def __repr__(self):
        return "<{}.parents>".format(type(self._path).__name__)


class PurePath:
    """Base class for manipulating paths without I/O.

    PurePath represents a filesystem path and offers operations which
    don't imply any actual filesystem I/O.  Depending on your system,
    instantiating a PurePath will return either a PurePosixPath or a
    PureWindowsPath object.  You can also instantiate either of these classes
    directly, regardless of your system.
    """

    __slots__ = (
        # The `_raw_paths` slot stores unjoined string paths. This is set in
        # the `__init__()` method.
        '_raw_paths',

        # The `_drv`, `_root` and `_tail_cached` slots store parsed and
        # normalized parts of the path. They are set when any of the `drive`,
        # `root` or `_tail` properties are accessed for the first time. The
        # three-part division corresponds to the result of
        # `os.path.splitroot()`, except that the tail is further split on path
        # separators (i.e. it is a list of strings), and that the root and
        # tail are normalized.
        '_drv', '_root', '_tail_cached',

        # The `_str` slot stores the string representation of the path,
        # computed from the drive, root and tail when `__str__()` is called
        # for the first time. It's used to implement `_str_normcase`
        '_str',

        # The `_str_normcase_cached` slot stores the string path with
        # normalized case. It is set when the `_str_normcase` property is
        # accessed for the first time. It's used to implement `__eq__()`
        # `__hash__()`, and `_parts_normcase`
        '_str_normcase_cached',

        # The `_parts_normcase_cached` slot stores the case-normalized
        # string path after splitting on path separators. It's set when the
        # `_parts_normcase` property is accessed for the first time. It's used
        # to implement comparison methods like `__lt__()`.
        '_parts_normcase_cached',

        # The `_hash` slot stores the hash of the case-normalized string
        # path. It's set when `__hash__()` is called for the first time.
        '_hash',
    )
    parser = os.path

    def __new__(cls, *args, **kwargs):
        """Construct a PurePath from one or several strings and or existing
        PurePath objects.  The strings and path objects are combined so as
        to yield a canonicalized path, which is incorporated into the
        new PurePath object.
        """
        if cls is PurePath:
            cls = PureWindowsPath if os.name == 'nt' else PurePosixPath
        return object.__new__(cls)

    def __init__(self, *args):
        paths = []
        for arg in args:
            if isinstance(arg, PurePath):
                if arg.parser is not self.parser:
                    # GH-103631: Convert separators for backwards compatibility.
                    paths.append(arg.as_posix())
                else:
                    paths.extend(arg._raw_paths)
            else:
                try:
                    path = os.fspath(arg)
                except TypeError:
                    path = arg
                if not isinstance(path, str):
                    raise TypeError(
                        "argument should be a str or an os.PathLike "
                        "object where __fspath__ returns a str, "
                        f"not {type(path).__name__!r}")
                paths.append(path)
        self._raw_paths = paths

    def with_segments(self, *pathsegments):
        """Construct a new path object from any number of path-like objects.
        Subclasses may override this method to customize how new path objects
        are created from methods like `iterdir()`.
        """
        return type(self)(*pathsegments)

    def joinpath(self, *pathsegments):
        """Combine this path with one or several arguments, and return a
        new path representing either a subpath (if all arguments are relative
        paths) or a totally different path (if one of the arguments is
        anchored).
        """
        return self.with_segments(self, *pathsegments)

    def __truediv__(self, key):
        try:
            return self.with_segments(self, key)
        except TypeError:
            return NotImplemented

    def __rtruediv__(self, key):
        try:
            return self.with_segments(key, self)
        except TypeError:
            return NotImplemented

    def __reduce__(self):
        return self.__class__, tuple(self._raw_paths)

    def __repr__(self):
        return "{}({!r})".format(self.__class__.__name__, self.as_posix())

    def __fspath__(self):
        return str(self)

    def __bytes__(self):
        """Return the bytes representation of the path.  This is only
        recommended to use under Unix."""
        return os.fsencode(self)

    @property
    def _str_normcase(self):
        # String with normalized case, for hashing and equality checks
        try:
            return self._str_normcase_cached
        except AttributeError:
            if self.parser is posixpath:
                self._str_normcase_cached = str(self)
            else:
                self._str_normcase_cached = str(self).lower()
            return self._str_normcase_cached

    def __hash__(self):
        try:
            return self._hash
        except AttributeError:
            self._hash = hash(self._str_normcase)
            return self._hash

    def __eq__(self, other):
        if not isinstance(other, PurePath):
            return NotImplemented
        return self._str_normcase == other._str_normcase and self.parser is other.parser

    @property
    def _parts_normcase(self):
        # Cached parts with normalized case, for comparisons.
        try:
            return self._parts_normcase_cached
        except AttributeError:
            self._parts_normcase_cached = self._str_normcase.split(self.parser.sep)
            return self._parts_normcase_cached

    def __lt__(self, other):
        if not isinstance(other, PurePath) or self.parser is not other.parser:
            return NotImplemented
        return self._parts_normcase < other._parts_normcase

    def __le__(self, other):
        if not isinstance(other, PurePath) or self.parser is not other.parser:
            return NotImplemented
        return self._parts_normcase <= other._parts_normcase

    def __gt__(self, other):
        if not isinstance(other, PurePath) or self.parser is not other.parser:
            return NotImplemented
        return self._parts_normcase > other._parts_normcase

    def __ge__(self, other):
        if not isinstance(other, PurePath) or self.parser is not other.parser:
            return NotImplemented
        return self._parts_normcase >= other._parts_normcase

    def __str__(self):
        """Return the string representation of the path, suitable for
        passing to system calls."""
        try:
            return self._str
        except AttributeError:
            self._str = self._format_parsed_parts(self.drive, self.root,
                                                  self._tail) or '.'
            return self._str

    @classmethod
    def _format_parsed_parts(cls, drv, root, tail):
        if drv or root:
            return drv + root + cls.parser.sep.join(tail)
        elif tail and cls.parser.splitdrive(tail[0])[0]:
            tail = ['.'] + tail
        return cls.parser.sep.join(tail)

    def _from_parsed_parts(self, drv, root, tail):
        path = self._from_parsed_string(self._format_parsed_parts(drv, root, tail))
        path._drv = drv
        path._root = root
        path._tail_cached = tail
        return path

    def _from_parsed_string(self, path_str):
        path = self.with_segments(path_str)
        path._str = path_str or '.'
        return path

    @classmethod
    def _parse_path(cls, path):
        if not path:
            return '', '', []
        sep = cls.parser.sep
        altsep = cls.parser.altsep
        if altsep:
            path = path.replace(altsep, sep)
        drv, root, rel = cls.parser.splitroot(path)
        if not root and drv.startswith(sep) and not drv.endswith(sep):
            drv_parts = drv.split(sep)
            if len(drv_parts) == 4 and drv_parts[2] not in '?.':
                # e.g. //server/share
                root = sep
            elif len(drv_parts) == 6:
                # e.g. //?/unc/server/share
                root = sep
        return drv, root, [x for x in rel.split(sep) if x and x != '.']

    @classmethod
    def _parse_pattern(cls, pattern):
        """Parse a glob pattern to a list of parts. This is much like
        _parse_path, except:

        - Rather than normalizing and returning the drive and root, we raise
          NotImplementedError if either are present.
        - If the path has no real parts, we raise ValueError.
        - If the path ends in a slash, then a final empty part is added.
        """
        drv, root, rel = cls.parser.splitroot(pattern)
        if root or drv:
            raise NotImplementedError("Non-relative patterns are unsupported")
        sep = cls.parser.sep
        altsep = cls.parser.altsep
        if altsep:
            rel = rel.replace(altsep, sep)
        parts = [x for x in rel.split(sep) if x and x != '.']
        if not parts:
            raise ValueError(f"Unacceptable pattern: {str(pattern)!r}")
        elif rel.endswith(sep):
            # GH-65238: preserve trailing slash in glob patterns.
            parts.append('')
        return parts

    def as_posix(self):
        """Return the string representation of the path with forward (/)
        slashes."""
        return str(self).replace(self.parser.sep, '/')

    @property
    def _raw_path(self):
        paths = self._raw_paths
        if len(paths) == 1:
            return paths[0]
        elif paths:
            # Join path segments from the initializer.
            return self.parser.join(*paths)
        else:
            return ''

    @property
    def drive(self):
        """The drive prefix (letter or UNC path), if any."""
        try:
            return self._drv
        except AttributeError:
            self._drv, self._root, self._tail_cached = self._parse_path(self._raw_path)
            return self._drv

    @property
    def root(self):
        """The root of the path, if any."""
        try:
            return self._root
        except AttributeError:
            self._drv, self._root, self._tail_cached = self._parse_path(self._raw_path)
            return self._root

    @property
    def _tail(self):
        try:
            return self._tail_cached
        except AttributeError:
            self._drv, self._root, self._tail_cached = self._parse_path(self._raw_path)
            return self._tail_cached

    @property
    def anchor(self):
        """The concatenation of the drive and root, or ''."""
        return self.drive + self.root

    @property
    def parts(self):
        """An object providing sequence-like access to the
        components in the filesystem path."""
        if self.drive or self.root:
            return (self.drive + self.root,) + tuple(self._tail)
        else:
            return tuple(self._tail)

    @property
    def parent(self):
        """The logical parent of the path."""
        drv = self.drive
        root = self.root
        tail = self._tail
        if not tail:
            return self
        return self._from_parsed_parts(drv, root, tail[:-1])

    @property
    def parents(self):
        """A sequence of this path's logical parents."""
        # The value of this property should not be cached on the path object,
        # as doing so would introduce a reference cycle.
        return _PathParents(self)

    @property
    def name(self):
        """The final path component, if any."""
        tail = self._tail
        if not tail:
            return ''
        return tail[-1]

    def with_name(self, name):
        """Return a new path with the file name changed."""
        p = self.parser
        if not name or p.sep in name or (p.altsep and p.altsep in name) or name == '.':
            raise ValueError(f"Invalid name {name!r}")
        tail = self._tail.copy()
        if not tail:
            raise ValueError(f"{self!r} has an empty name")
        tail[-1] = name
        return self._from_parsed_parts(self.drive, self.root, tail)

    def with_stem(self, stem):
        """Return a new path with the stem changed."""
        suffix = self.suffix
        if not suffix:
            return self.with_name(stem)
        elif not stem:
            # If the suffix is non-empty, we can't make the stem empty.
            raise ValueError(f"{self!r} has a non-empty suffix")
        else:
            return self.with_name(stem + suffix)

    def with_suffix(self, suffix):
        """Return a new path with the file suffix changed.  If the path
        has no suffix, add given suffix.  If the given suffix is an empty
        string, remove the suffix from the path.
        """
        stem = self.stem
        if not stem:
            # If the stem is empty, we can't make the suffix non-empty.
            raise ValueError(f"{self!r} has an empty name")
        elif suffix and not suffix.startswith('.'):
            raise ValueError(f"Invalid suffix {suffix!r}")
        else:
            return self.with_name(stem + suffix)

    @property
    def stem(self):
        """The final path component, minus its last suffix."""
        name = self.name
        i = name.rfind('.')
        if i != -1:
            stem = name[:i]
            # Stem must contain at least one non-dot character.
            if stem.lstrip('.'):
                return stem
        return name

    @property
    def suffix(self):
        """
        The final component's last suffix, if any.

        This includes the leading period. For example: '.txt'
        """
        name = self.name.lstrip('.')
        i = name.rfind('.')
        if i != -1:
            return name[i:]
        return ''

    @property
    def suffixes(self):
        """
        A list of the final component's suffixes, if any.

        These include the leading periods. For example: ['.tar', '.gz']
        """
        return ['.' + ext for ext in self.name.lstrip('.').split('.')[1:]]

    def relative_to(self, other, *, walk_up=False):
        """Return the relative path to another path identified by the passed
