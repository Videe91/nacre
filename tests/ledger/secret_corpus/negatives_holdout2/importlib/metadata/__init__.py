from __future__ import annotations

import os
import re
import abc
import sys
import json
import email
import types
import inspect
import pathlib
import zipfile
import operator
import textwrap
import warnings
import functools
import itertools
import posixpath
import collections

from . import _meta
from ._collections import FreezableDefaultDict, Pair
from ._functools import method_cache, pass_none
from ._itertools import always_iterable, unique_everseen
from ._meta import PackageMetadata, SimplePath

from contextlib import suppress
from importlib import import_module
from importlib.abc import MetaPathFinder
from itertools import starmap
from typing import Any, Iterable, List, Mapping, Match, Optional, Set, cast

__all__ = [
    'Distribution',
    'DistributionFinder',
    'PackageMetadata',
    'PackageNotFoundError',
    'distribution',
    'distributions',
    'entry_points',
    'files',
    'metadata',
    'packages_distributions',
    'requires',
    'version',
]


class PackageNotFoundError(ModuleNotFoundError):
    """The package was not found."""

    def __str__(self) -> str:
        return f"No package metadata was found for {self.name}"

    @property
    def name(self) -> str:  # type: ignore[override]
        (name,) = self.args
        return name


class Sectioned:
    """
    A simple entry point config parser for performance

    >>> for item in Sectioned.read(Sectioned._sample):
    ...     print(item)
    Pair(name='sec1', value='# comments ignored')
    Pair(name='sec1', value='a = 1')
    Pair(name='sec1', value='b = 2')
    Pair(name='sec2', value='a = 2')

    >>> res = Sectioned.section_pairs(Sectioned._sample)
    >>> item = next(res)
    >>> item.name
    'sec1'
    >>> item.value
    Pair(name='a', value='1')
    >>> item = next(res)
    >>> item.value
    Pair(name='b', value='2')
    >>> item = next(res)
    >>> item.name
    'sec2'
    >>> item.value
    Pair(name='a', value='2')
    >>> list(res)
    []
    """

    _sample = textwrap.dedent(
        """
        [sec1]
        # comments ignored
        a = 1
        b = 2

        [sec2]
        a = 2
        """
    ).lstrip()

    @classmethod
    def section_pairs(cls, text):
        return (
            section._replace(value=Pair.parse(section.value))
            for section in cls.read(text, filter_=cls.valid)
            if section.name is not None
        )

    @staticmethod
    def read(text, filter_=None):
        lines = filter(filter_, map(str.strip, text.splitlines()))
        name = None
        for value in lines:
            section_match = value.startswith('[') and value.endswith(']')
            if section_match:
                name = value.strip('[]')
                continue
            yield Pair(name, value)

    @staticmethod
    def valid(line: str):
        return line and not line.startswith('#')


class EntryPoint:
    """An entry point as defined by Python packaging conventions.

    See `the packaging docs on entry points
    <https://packaging.python.org/specifications/entry-points/>`_
    for more information.

    >>> ep = EntryPoint(
    ...     name=None, group=None, value='package.module:attr [extra1, extra2]')
    >>> ep.module
    'package.module'
    >>> ep.attr
    'attr'
    >>> ep.extras
    ['extra1', 'extra2']
    """

    pattern = re.compile(
        r'(?P<module>[\w.]+)\s*'
        r'(:\s*(?P<attr>[\w.]+)\s*)?'
        r'((?P<extras>\[.*\])\s*)?$'
    )
    """
    A regular expression describing the syntax for an entry point,
    which might look like:

        - module
        - package.module
        - package.module:attribute
        - package.module:object.attribute
        - package.module:attr [extra1, extra2]

    Other combinations are possible as well.

    The expression is lenient about whitespace around the ':',
    following the attr, and following any extras.
    """

    name: str
    value: str
    group: str

    dist: Optional[Distribution] = None

    def __init__(self, name: str, value: str, group: str) -> None:
        vars(self).update(name=name, value=value, group=group)

    def load(self) -> Any:
        """Load the entry point from its definition. If only a module
        is indicated by the value, return that module. Otherwise,
        return the named object.
        """
        match = cast(Match, self.pattern.match(self.value))
        module = import_module(match.group('module'))
        attrs = filter(None, (match.group('attr') or '').split('.'))
        return functools.reduce(getattr, attrs, module)

    @property
    def module(self) -> str:
        match = self.pattern.match(self.value)
        assert match is not None
        return match.group('module')

    @property
    def attr(self) -> str:
        match = self.pattern.match(self.value)
        assert match is not None
        return match.group('attr')

    @property
    def extras(self) -> List[str]:
        match = self.pattern.match(self.value)
        assert match is not None
        return re.findall(r'\w+', match.group('extras') or '')

    def _for(self, dist):
        vars(self).update(dist=dist)
        return self

    def matches(self, **params):
        """
        EntryPoint matches the given parameters.

        >>> ep = EntryPoint(group='foo', name='bar', value='bing:bong [extra1, extra2]')
        >>> ep.matches(group='foo')
        True
        >>> ep.matches(name='bar', value='bing:bong [extra1, extra2]')
        True
        >>> ep.matches(group='foo', name='other')
        False
        >>> ep.matches()
        True
        >>> ep.matches(extras=['extra1', 'extra2'])
        True
        >>> ep.matches(module='bing')
        True
        >>> ep.matches(attr='bong')
        True
        """
        attrs = (getattr(self, param) for param in params)
        return all(map(operator.eq, params.values(), attrs))

    def _key(self):
        return self.name, self.value, self.group

    def __lt__(self, other):
        return self._key() < other._key()

    def __eq__(self, other):
        return self._key() == other._key()

    def __setattr__(self, name, value):
        raise AttributeError("EntryPoint objects are immutable.")

    def __repr__(self):
        return (
            f'EntryPoint(name={self.name!r}, value={self.value!r}, '
            f'group={self.group!r})'
        )

    def __hash__(self) -> int:
        return hash(self._key())


class EntryPoints(tuple):
    """
    An immutable collection of selectable EntryPoint objects.
    """

    __slots__ = ()

    def __getitem__(self, name: str) -> EntryPoint:  # type: ignore[override]
        """
        Get the EntryPoint in self matching name.
        """
        try:
            return next(iter(self.select(name=name)))
        except StopIteration:
            raise KeyError(name)

    def __repr__(self):
        """
        Repr with classname and tuple constructor to
        signal that we deviate from regular tuple behavior.
        """
        return '%s(%r)' % (self.__class__.__name__, tuple(self))

    def select(self, **params) -> EntryPoints:
        """
        Select entry points from self that match the
        given parameters (typically group and/or name).
        """
        return EntryPoints(ep for ep in self if ep.matches(**params))

    @property
    def names(self) -> Set[str]:
        """
        Return the set of all names of all entry points.
        """
        return {ep.name for ep in self}

    @property
    def groups(self) -> Set[str]:
        """
        Return the set of all groups of all entry points.
        """
        return {ep.group for ep in self}

    @classmethod
    def _from_text_for(cls, text, dist):
        return cls(ep._for(dist) for ep in cls._from_text(text))

    @staticmethod
    def _from_text(text):
        return (
            EntryPoint(name=item.value.name, value=item.value.value, group=item.name)
            for item in Sectioned.section_pairs(text or '')
        )


class PackagePath(pathlib.PurePosixPath):
    """A reference to a path in a package"""

    hash: Optional[FileHash]
    size: int
    dist: Distribution

    def read_text(self, encoding: str = 'utf-8') -> str:  # type: ignore[override]
        return self.locate().read_text(encoding=encoding)

    def read_binary(self) -> bytes:
        return self.locate().read_bytes()

    def locate(self) -> SimplePath:
        """Return a path-like object for this path"""
        return self.dist.locate_file(self)


class FileHash:
    def __init__(self, spec: str) -> None:
        self.mode, _, self.value = spec.partition('=')

    def __repr__(self) -> str:
        return f'<FileHash mode: {self.mode} value: {self.value}>'


class DeprecatedNonAbstract:
    # Required until Python 3.14
    def __new__(cls, *args, **kwargs):
        all_names = {
            name for subclass in inspect.getmro(cls) for name in vars(subclass)
        }
        abstract = {
            name
            for name in all_names
            if getattr(getattr(cls, name), '__isabstractmethod__', False)
        }
        if abstract:
            warnings.warn(
                f"Unimplemented abstract methods {abstract}",
                DeprecationWarning,
                stacklevel=2,
            )
        return super().__new__(cls)


class Distribution(DeprecatedNonAbstract):
    """
    An abstract Python distribution package.

    Custom providers may derive from this class and define
    the abstract methods to provide a concrete implementation
    for their environment. Some providers may opt to override
    the default implementation of some properties to bypass
    the file-reading mechanism.
    """

    @abc.abstractmethod
    def read_text(self, filename) -> Optional[str]:
        """Attempt to load metadata file given by the name.

        Python distribution metadata is organized by blobs of text
        typically represented as "files" in the metadata directory
        (e.g. package-1.0.dist-info). These files include things
        like:

        - METADATA: The distribution metadata including fields
          like Name and Version and Description.
        - entry_points.txt: A series of entry points as defined in
          `the entry points spec <https://packaging.python.org/en/latest/specifications/entry-points/#file-format>`_.
        - RECORD: A record of files according to
          `this recording spec <https://packaging.python.org/en/latest/specifications/recording-installed-packages/#the-record-file>`_.

        A package may provide any set of files, including those
        not listed here or none at all.

        :param filename: The name of the file in the distribution info.
        :return: The text if found, otherwise None.
        """

    @abc.abstractmethod
    def locate_file(self, path: str | os.PathLike[str]) -> SimplePath:
        """
        Given a path to a file in this distribution, return a SimplePath
        to it.
        """

    @classmethod
    def from_name(cls, name: str) -> Distribution:
        """Return the Distribution for the given package name.

        :param name: The name of the distribution package to search for.
        :return: The Distribution instance (or subclass thereof) for the named
            package, if found.
        :raises PackageNotFoundError: When the named package's distribution
            metadata cannot be found.
        :raises ValueError: When an invalid value is supplied for name.
        """
        if not name:
            raise ValueError("A distribution name is required.")
        try:
            return next(iter(cls.discover(name=name)))
        except StopIteration:
            raise PackageNotFoundError(name)

    @classmethod
    def discover(
        cls, *, context: Optional[DistributionFinder.Context] = None, **kwargs
    ) -> Iterable[Distribution]:
        """Return an iterable of Distribution objects for all packages.

        Pass a ``context`` or pass keyword arguments for constructing
        a context.

        :context: A ``DistributionFinder.Context`` object.
        :return: Iterable of Distribution objects for packages matching
          the context.
        """
        if context and kwargs:
            raise ValueError("cannot accept context and kwargs")
        context = context or DistributionFinder.Context(**kwargs)
        return itertools.chain.from_iterable(
            resolver(context) for resolver in cls._discover_resolvers()
        )

    @staticmethod
    def at(path: str | os.PathLike[str]) -> Distribution:
        """Return a Distribution for the indicated metadata path.

        :param path: a string or path-like object
        :return: a concrete Distribution instance for the path
        """
        return PathDistribution(pathlib.Path(path))

    @staticmethod
    def _discover_resolvers():
        """Search the meta_path for resolvers (MetadataPathFinders)."""
        declared = (
            getattr(finder, 'find_distributions', None) for finder in sys.meta_path
        )
        return filter(None, declared)

    @property
    def metadata(self) -> _meta.PackageMetadata:
        """Return the parsed metadata for this Distribution.

        The returned object will have keys that name the various bits of
        metadata per the
        `Core metadata specifications <https://packaging.python.org/en/latest/specifications/core-metadata/#core-metadata>`_.

        Custom providers may provide the METADATA file or override this
        property.
        """
        # deferred for performance (python/cpython#109829)
        from . import _adapters

        opt_text = (
            self.read_text('METADATA')
            or self.read_text('PKG-INFO')
            # This last clause is here to support old egg-info files.  Its
            # effect is to just end up using the PathDistribution's self._path
            # (which points to the egg-info file) attribute unchanged.
            or self.read_text('')
        )
        text = cast(str, opt_text)
        return _adapters.Message(email.message_from_string(text))

    @property
    def name(self) -> str:
        """Return the 'Name' metadata for the distribution package."""
        return self.metadata['Name']

    @property
    def _normalized_name(self):
        """Return a normalized version of the name."""
        return Prepared.normalize(self.name)

    @property
    def version(self) -> str:
        """Return the 'Version' metadata for the distribution package."""
        return self.metadata['Version']

    @property
    def entry_points(self) -> EntryPoints:
        """
        Return EntryPoints for this distribution.

        Custom providers may provide the ``entry_points.txt`` file
        or override this property.
        """
        return EntryPoints._from_text_for(self.read_text('entry_points.txt'), self)

    @property
    def files(self) -> Optional[List[PackagePath]]:
        """Files in this distribution.

        :return: List of PackagePath for this distribution or None

        Result is `None` if the metadata file that enumerates files
        (i.e. RECORD for dist-info, or installed-files.txt or
        SOURCES.txt for egg-info) is missing.
        Result may be empty if the metadata exists but is empty.

        Custom providers are recommended to provide a "RECORD" file (in
        ``read_text``) or override this property to allow for callers to be
        able to resolve filenames provided by the package.
        """

        def make_file(name, hash=None, size_str=None):
            result = PackagePath(name)
            result.hash = FileHash(hash) if hash else None
            result.size = int(size_str) if size_str else None
            result.dist = self
            return result

        @pass_none
        def make_files(lines):
            # Delay csv import, since Distribution.files is not as widely used
            # as other parts of importlib.metadata
            import csv

            return starmap(make_file, csv.reader(lines))

        @pass_none
        def skip_missing_files(package_paths):
            return list(filter(lambda path: path.locate().exists(), package_paths))

        return skip_missing_files(
            make_files(
                self._read_files_distinfo()
                or self._read_files_egginfo_installed()
