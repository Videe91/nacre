# mypy: allow-untyped-defs
"""local path implementation."""

from __future__ import annotations

import atexit
from collections.abc import Callable
from contextlib import contextmanager
import fnmatch
import importlib.util
import io
import os
from os.path import abspath
from os.path import dirname
from os.path import exists
from os.path import isabs
from os.path import isdir
from os.path import isfile
from os.path import islink
from os.path import normpath
import posixpath
from stat import S_ISDIR
from stat import S_ISLNK
from stat import S_ISREG
import sys
from typing import Any
from typing import cast
from typing import Literal
from typing import overload
from typing import TYPE_CHECKING
import uuid
import warnings

from . import error


# Moved from local.py.
iswin32 = sys.platform == "win32" or (getattr(os, "_name", False) == "nt")


class Checkers:
    _depend_on_existence = "exists", "link", "dir", "file"

    def __init__(self, path):
        self.path = path

    def dotfile(self):
        return self.path.basename.startswith(".")

    def ext(self, arg):
        if not arg.startswith("."):
            arg = "." + arg
        return self.path.ext == arg

    def basename(self, arg):
        return self.path.basename == arg

    def basestarts(self, arg):
        return self.path.basename.startswith(arg)

    def relto(self, arg):
        return self.path.relto(arg)

    def fnmatch(self, arg):
        return self.path.fnmatch(arg)

    def endswith(self, arg):
        return str(self.path).endswith(arg)

    def _evaluate(self, kw):
        from .._code.source import getrawcode

        for name, value in kw.items():
            invert = False
            meth = None
            try:
                meth = getattr(self, name)
            except AttributeError:
                if name[:3] == "not":
                    invert = True
                    try:
                        meth = getattr(self, name[3:])
                    except AttributeError:
                        pass
            if meth is None:
                raise TypeError(f"no {name!r} checker available for {self.path!r}")
            try:
                if getrawcode(meth).co_argcount > 1:
                    if (not meth(value)) ^ invert:
                        return False
                else:
                    if bool(value) ^ bool(meth()) ^ invert:
                        return False
            except (error.ENOENT, error.ENOTDIR, error.EBUSY):
                # EBUSY feels not entirely correct,
                # but its kind of necessary since ENOMEDIUM
                # is not accessible in python
                for name in self._depend_on_existence:
                    if name in kw:
                        if kw.get(name):
                            return False
                    name = "not" + name
                    if name in kw:
                        if not kw.get(name):
                            return False
        return True

    _statcache: Stat

    def _stat(self) -> Stat:
        try:
            return self._statcache
        except AttributeError:
            try:
                self._statcache = self.path.stat()
            except error.ELOOP:
                self._statcache = self.path.lstat()
            return self._statcache

    def dir(self):
        return S_ISDIR(self._stat().mode)

    def file(self):
        return S_ISREG(self._stat().mode)

    def exists(self):
        return self._stat()

    def link(self):
        st = self.path.lstat()
        return S_ISLNK(st.mode)


class NeverRaised(Exception):
    pass


class Visitor:
    def __init__(self, fil, rec, ignore, bf, sort):
        if isinstance(fil, (str, bytes)):
            fil = FNMatcher(fil)
        if isinstance(rec, str):
            self.rec: Callable[[LocalPath], bool] = FNMatcher(rec)
        elif not hasattr(rec, "__call__") and rec:
            self.rec = lambda path: True
        else:
            self.rec = rec
        self.fil = fil
        self.ignore = ignore
        self.breadthfirst = bf
        self.optsort = cast(Callable[[Any], Any], sorted) if sort else (lambda x: x)

    def gen(self, path):
        try:
            entries = path.listdir()
        except self.ignore:
            return
        rec = self.rec
        dirs = self.optsort(
            [p for p in entries if p.check(dir=1) and (rec is None or rec(p))]
        )
        if not self.breadthfirst:
            for subdir in dirs:
                yield from self.gen(subdir)
        for p in self.optsort(entries):
            if self.fil is None or self.fil(p):
                yield p
        if self.breadthfirst:
            for subdir in dirs:
                yield from self.gen(subdir)


class FNMatcher:
    def __init__(self, pattern):
        self.pattern = pattern

    def __call__(self, path):
        pattern = self.pattern

        if (
            pattern.find(path.sep) == -1
            and iswin32
            and pattern.find(posixpath.sep) != -1
        ):
            # Running on Windows, the pattern has no Windows path separators,
            # and the pattern has one or more Posix path separators. Replace
            # the Posix path separators with the Windows path separator.
            pattern = pattern.replace(posixpath.sep, path.sep)

        if pattern.find(path.sep) == -1:
            name = path.basename
        else:
            name = str(path)  # path.strpath # XXX svn?
            if not os.path.isabs(pattern):
                pattern = "*" + path.sep + pattern
        return fnmatch.fnmatch(name, pattern)


def map_as_list(func, iter):
    return list(map(func, iter))


class Stat:
    if TYPE_CHECKING:

        @property
        def size(self) -> int: ...

        @property
        def mtime(self) -> float: ...

    def __getattr__(self, name: str) -> Any:
        return getattr(self._osstatresult, "st_" + name)

    def __init__(self, path, osstatresult):
        self.path = path
        self._osstatresult = osstatresult

    @property
    def owner(self):
        if iswin32:
            raise NotImplementedError("XXX win32")
        import pwd

        entry = error.checked_call(pwd.getpwuid, self.uid)  # type:ignore[attr-defined,unused-ignore]
        return entry[0]

    @property
    def group(self):
        """Return group name of file."""
        if iswin32:
            raise NotImplementedError("XXX win32")
        import grp

        entry = error.checked_call(grp.getgrgid, self.gid)  # type:ignore[attr-defined,unused-ignore]
        return entry[0]

    def isdir(self):
        return S_ISDIR(self._osstatresult.st_mode)

    def isfile(self):
        return S_ISREG(self._osstatresult.st_mode)

    def islink(self):
        self.path.lstat()
        return S_ISLNK(self._osstatresult.st_mode)


def getuserid(user):
    import pwd

    if not isinstance(user, int):
        user = pwd.getpwnam(user)[2]  # type:ignore[attr-defined,unused-ignore]
    return user


def getgroupid(group):
    import grp

    if not isinstance(group, int):
        group = grp.getgrnam(group)[2]  # type:ignore[attr-defined,unused-ignore]
    return group


class LocalPath:
    """Object oriented interface to os.path and other local filesystem
    related information.
    """

    class ImportMismatchError(ImportError):
        """raised on pyimport() if there is a mismatch of __file__'s"""

    sep = os.sep

    def __init__(self, path=None, expanduser=False):
        """Initialize and return a local Path instance.

        Path can be relative to the current directory.
        If path is None it defaults to the current working directory.
        If expanduser is True, tilde-expansion is performed.
        Note that Path instances always carry an absolute path.
        Note also that passing in a local path object will simply return
        the exact same path object. Use new() to get a new copy.
        """
        if path is None:
            self.strpath = error.checked_call(os.getcwd)
        else:
            try:
                path = os.fspath(path)
            except TypeError:
                raise ValueError(
                    "can only pass None, Path instances "
                    "or non-empty strings to LocalPath"
                )
            if expanduser:
                path = os.path.expanduser(path)
            self.strpath = abspath(path)

    if sys.platform != "win32":

        def chown(self, user, group, rec=0):
            """Change ownership to the given user and group.
            user and group may be specified by a number or
            by a name.  if rec is True change ownership
            recursively.
            """
            uid = getuserid(user)
            gid = getgroupid(group)
            if rec:
                for x in self.visit(rec=lambda x: x.check(link=0)):
                    if x.check(link=0):
                        error.checked_call(os.chown, str(x), uid, gid)
            error.checked_call(os.chown, str(self), uid, gid)

        def readlink(self) -> str:
            """Return value of a symbolic link."""
            # https://github.com/python/mypy/issues/12278
            return error.checked_call(os.readlink, self.strpath)  # type: ignore[arg-type,return-value,unused-ignore]

        def mklinkto(self, oldname):
            """Posix style hard link to another name."""
            error.checked_call(os.link, str(oldname), str(self))

        def mksymlinkto(self, value, absolute=1):
            """Create a symbolic link with the given value (pointing to another name)."""
            if absolute:
                error.checked_call(os.symlink, str(value), self.strpath)
            else:
                base = self.common(value)
                # with posix local paths '/' is always a common base
                relsource = self.__class__(value).relto(base)
                reldest = self.relto(base)
                n = reldest.count(self.sep)
                target = self.sep.join(("..",) * n + (relsource,))
                error.checked_call(os.symlink, target, self.strpath)

    def __div__(self, other):
        return self.join(os.fspath(other))

    __truediv__ = __div__  # py3k

    @property
    def basename(self):
        """Basename part of path."""
        return self._getbyspec("basename")[0]

    @property
    def dirname(self):
        """Dirname part of path."""
        return self._getbyspec("dirname")[0]

    @property
    def purebasename(self):
        """Pure base name of the path."""
        return self._getbyspec("purebasename")[0]

    @property
    def ext(self):
        """Extension of the path (including the '.')."""
        return self._getbyspec("ext")[0]

    def read_binary(self):
        """Read and return a bytestring from reading the path."""
        with self.open("rb") as f:
            return f.read()

    def read_text(self, encoding):
        """Read and return a Unicode string from reading the path."""
        with self.open("r", encoding=encoding) as f:
            return f.read()

    def read(self, mode="r"):
        """Read and return a bytestring from reading the path."""
        with self.open(mode) as f:
            return f.read()

    def readlines(self, cr=1):
        """Read and return a list of lines from the path. if cr is False, the
        newline will be removed from the end of each line."""
        mode = "r"

        if not cr:
            content = self.read(mode)
            return content.split("\n")
        else:
            f = self.open(mode)
            try:
                return f.readlines()
            finally:
                f.close()

    def load(self):
        """(deprecated) return object unpickled from self.read()"""
        f = self.open("rb")
        try:
            import pickle

            return error.checked_call(pickle.load, f)
        finally:
            f.close()

    def move(self, target):
        """Move this path to target."""
        if target.relto(self):
            raise error.EINVAL(target, "cannot move path into a subdirectory of itself")
        try:
            self.rename(target)
        except error.EXDEV:  # invalid cross-device link
            self.copy(target)
            self.remove()

    def fnmatch(self, pattern):
        """Return true if the basename/fullname matches the glob-'pattern'.

        valid pattern characters::

            *       matches everything
            ?       matches any single character
            [seq]   matches any character in seq
            [!seq]  matches any char not in seq

        If the pattern contains a path-separator then the full path
        is used for pattern matching and a '*' is prepended to the
        pattern.

        if the pattern doesn't contain a path-separator the pattern
        is only matched against the basename.
        """
        return FNMatcher(pattern)(self)

    def relto(self, relpath):
        """Return a string which is the relative part of the path
        to the given 'relpath'.
        """
        if not isinstance(relpath, str | LocalPath):
            raise TypeError(f"{relpath!r}: not a string or path object")
        strrelpath = str(relpath)
        if strrelpath and strrelpath[-1] != self.sep:
            strrelpath += self.sep
        # assert strrelpath[-1] == self.sep
        # assert strrelpath[-2] != self.sep
        strself = self.strpath
        if sys.platform == "win32" or getattr(os, "_name", None) == "nt":
            if os.path.normcase(strself).startswith(os.path.normcase(strrelpath)):
                return strself[len(strrelpath) :]
        elif strself.startswith(strrelpath):
            return strself[len(strrelpath) :]
        return ""

    def ensure_dir(self, *args):
        """Ensure the path joined with args is a directory."""
        return self.ensure(*args, dir=True)

    def bestrelpath(self, dest):
        """Return a string which is a relative path from self
        (assumed to be a directory) to dest such that
        self.join(bestrelpath) == dest and if not such
        path can be determined return dest.
        """
        try:
            if self == dest:
                return os.curdir
            base = self.common(dest)
            if not base:  # can be the case on windows
                return str(dest)
            self2base = self.relto(base)
            reldest = dest.relto(base)
            if self2base:
                n = self2base.count(self.sep) + 1
            else:
                n = 0
            lst = [os.pardir] * n
            if reldest:
                lst.append(reldest)
            target = dest.sep.join(lst)
            return target
        except AttributeError:
            return str(dest)

    def exists(self):
        return self.check()

    def isdir(self):
        return self.check(dir=1)

    def isfile(self):
        return self.check(file=1)

    def parts(self, reverse=False):
        """Return a root-first list of all ancestor directories
        plus the path itself.
        """
        current = self
        lst = [self]
        while 1:
            last = current
            current = current.dirpath()
            if last == current:
                break
            lst.append(current)
        if not reverse:
            lst.reverse()
        return lst

    def common(self, other):
        """Return the common part shared with the other path
        or None if there is no common part.
        """
        last = None
        for x, y in zip(self.parts(), other.parts()):
            if x != y:
                return last
            last = x
        return last

    def __add__(self, other):
        """Return new path object with 'other' added to the basename"""
        return self.new(basename=self.basename + str(other))

    def visit(self, fil=None, rec=None, ignore=NeverRaised, bf=False, sort=False):
        """Yields all paths below the current one

        fil is a filter (glob pattern or callable), if not matching the
        path will not be yielded, defaulting to None (everything is
        returned)

        rec is a filter (glob pattern or callable) that controls whether
        a node is descended, defaulting to None

        ignore is an Exception class that is ignoredwhen calling dirlist()
        on any of the paths (by default, all exceptions are reported)

