# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.
"""
.. testsetup::

    from packaging.specifiers import Specifier, SpecifierSet, InvalidSpecifier
    from packaging.version import Version
"""

from __future__ import annotations

import abc
import re
import typing
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Final,
    TypeVar,
    Union,
)

from ._ranges import (
    FULL_RANGE,
    bounds_for_spec,
    coerce_version,
    filter_by_ranges,
    intersect_specifier_bounds,
    matches_bounds_only,
    ranges_are_prerelease_only,
    resolve_prereleases,
    trim_release,
)
from .utils import canonicalize_version
from .version import Version

if TYPE_CHECKING:
    import sys
    from collections.abc import Iterable, Iterator, Sequence

    if sys.version_info >= (3, 10):
        from typing import TypeGuard
    else:
        from typing_extensions import TypeGuard

    from . import ranges
    from ._ranges import Interval


__all__ = [
    "BaseSpecifier",
    "InvalidSpecifier",
    "Specifier",
    "SpecifierSet",
]


def __dir__() -> list[str]:
    return __all__


def _validate_spec(spec: object, /) -> TypeGuard[tuple[str, str]]:
    return (
        isinstance(spec, tuple)
        and len(spec) == 2
        and isinstance(spec[0], str)
        and isinstance(spec[1], str)
    )


def _validate_pre(pre: object, /) -> TypeGuard[bool | None]:
    return pre is None or isinstance(pre, bool)


T = TypeVar("T")
UnparsedVersion = Union[Version, str]
UnparsedVersionVar = TypeVar("UnparsedVersionVar", bound=UnparsedVersion)


# Operators whose result is just a direct Version comparison, given a parsed
# item with no local. ``<=``/``==``/``!=`` need that no-local guard because
# PEP 440 strips locals on those; ``>=`` works regardless.
_DIRECT_COMPARE_OPS: dict[str, Callable[[Version, Version], bool]] = {
    ">=": Version.__ge__,
    "<=": Version.__le__,
    "==": Version.__eq__,
    "!=": Version.__ne__,
}


def _fast_match(specifier: Specifier, parsed: Version) -> bool | None:
    """Match ``parsed`` against ``specifier`` without building a range.

    Handles ``>=``, ``<=``, ``==``, ``!=``, ``<``, ``>`` when the spec is
    not a wildcard and ``parsed`` has no local. Returns ``None`` when the
    range path must be used. Pre-release policy is left to the caller.
    """
    op_str, ver_str = specifier._spec
    if ver_str.endswith(".*") or parsed.local is not None:
        return None

    direct_compare = _DIRECT_COMPARE_OPS.get(op_str)
    if direct_compare is not None:
        return direct_compare(parsed, specifier._require_spec_version(ver_str))

    if op_str in ("<", ">"):
        spec_v = specifier._require_spec_version(ver_str)
        # ``<V``/``>V`` carve out V's family (pre/dev/post); that only
        # matters when parsed shares V's epoch and trimmed release.
        # Otherwise a direct cmpkey comparison is correct.
        if parsed.epoch != spec_v.epoch or trim_release(parsed.release) != trim_release(
            spec_v.release
        ):
            return parsed < spec_v if op_str == "<" else parsed > spec_v
        return None

    return None


class InvalidSpecifier(ValueError):
    """
    Raised when attempting to create a :class:`Specifier` with a specifier
    string that is invalid.

    >>> Specifier("lolwat")
    Traceback (most recent call last):
        ...
    packaging.specifiers.InvalidSpecifier: Invalid specifier: 'lolwat'
    """


class BaseSpecifier(metaclass=abc.ABCMeta):
    """
    Abstract base class for :class:`Specifier` and :class:`SpecifierSet`.
    """

    __slots__ = ()
    __match_args__ = ("_str",)

    @property
    def _str(self) -> str:
        """Internal property for match_args"""
        return str(self)

    @abc.abstractmethod
    def __str__(self) -> str:
        """
        Returns the str representation of this Specifier-like object. This
        should be representative of the Specifier itself.
        """

    @abc.abstractmethod
    def __hash__(self) -> int:
        """
        Returns a hash value for this Specifier-like object.
        """

    @abc.abstractmethod
    def __eq__(self, other: object) -> bool:
        """
        Returns a boolean representing whether or not the two Specifier-like
        objects are equal.

        :param other: The other object to check against.
        """

    @property
    @abc.abstractmethod
    def prereleases(self) -> bool | None:
        """Whether or not pre-releases as a whole are allowed.

        This can be set to either ``True`` or ``False`` to explicitly enable or disable
        prereleases or it can be set to ``None`` (the default) to use default semantics.
        """

    @prereleases.setter  # noqa: B027
    def prereleases(self, value: bool) -> None:
        """Setter for :attr:`prereleases`.

        :param value: The value to set.
        """

    @abc.abstractmethod
    def contains(self, item: str, prereleases: bool | None = None) -> bool:
        """
        Determines if the given item is contained within this specifier.
        """

    @typing.overload
    def filter(
        self,
        iterable: Iterable[UnparsedVersionVar],
        prereleases: bool | None = None,
        key: None = ...,
    ) -> Iterator[UnparsedVersionVar]: ...

    @typing.overload
    def filter(
        self,
        iterable: Iterable[T],
        prereleases: bool | None = None,
        key: Callable[[T], UnparsedVersion] = ...,
    ) -> Iterator[T]: ...

    @abc.abstractmethod
    def filter(
        self,
        iterable: Iterable[Any],
        prereleases: bool | None = None,
        key: Callable[[Any], UnparsedVersion] | None = None,
    ) -> Iterator[Any]:
        """
        Takes an iterable of items and filters them so that only items which
        are contained within this specifier are allowed in it.
        """


class Specifier(BaseSpecifier):
    """This class abstracts handling of version specifiers.

    .. tip::

        It is generally not required to instantiate this manually. You should instead
        prefer to work with :class:`SpecifierSet` instead, which can parse
        comma-separated version specifiers (which is what package metadata contains).

    Instances are safe to serialize with :mod:`pickle`. They use a stable
    format so the same pickle can be loaded in future packaging releases.

    .. versionchanged:: 26.2

        Added a stable pickle format. Pickles created with packaging 26.2+ can
        be unpickled with future releases.  Backward compatibility with pickles
        from packaging < 26.2 is supported but may be removed in a future
        release.
    """

    __slots__ = (
        "_prereleases",
        "_ranges",
        "_spec",
        "_spec_version",
    )

    _specifier_regex_str = r"""
        (?:
            (?:
                # The identity operators allow for an escape hatch that will
                # do an exact string match of the version you wish to install.
                # This will not be parsed by PEP 440 and we cannot determine
                # any semantic meaning from it. This operator is discouraged
                # but included entirely as an escape hatch.
                ===  # Only match for the identity operator
                \s*
                [^\s;)]*  # The arbitrary version can be just about anything,
                          # we match everything except for whitespace, a
                          # semi-colon for marker support, and a closing paren
                          # since versions can be enclosed in them.
            )
            |
            (?:
                # The (non)equality operators allow for wild card and local
                # versions to be specified so we have to define these two
                # operators separately to enable that.
                (?:==|!=)            # Only match for equals and not equals

                \s*
                v?
                (?:[0-9]+!)?          # epoch
                [0-9]+(?:\.[0-9]+)*   # release

                # You cannot use a wild card and a pre-release, post-release, a dev or
                # local version together so group them with a | and make them optional.
                (?:
                    \.\*  # Wild card syntax of .*
                    |
                    (?a:                                  # pre release
                        [-_\.]?
                        (alpha|beta|preview|pre|a|b|c|rc)
                        [-_\.]?
                        [0-9]*
                    )?
                    (?a:                                  # post release
                        (?:-[0-9]+)|(?:[-_\.]?(post|rev|r)[-_\.]?[0-9]*)
                    )?
                    (?a:[-_\.]?dev[-_\.]?[0-9]*)?         # dev release
                    (?a:\+[a-z0-9]+(?:[-_\.][a-z0-9]+)*)? # local
                )?
            )
            |
            (?:
                # The compatible operator requires at least two digits in the
                # release segment.
                (?:~=)               # Only match for the compatible operator

                \s*
                v?
                (?:[0-9]+!)?          # epoch
                [0-9]+(?:\.[0-9]+)+   # release  (We have a + instead of a *)
                (?:                   # pre release
                    [-_\.]?
                    (alpha|beta|preview|pre|a|b|c|rc)
                    [-_\.]?
                    [0-9]*
                )?
                (?:                                   # post release
                    (?:-[0-9]+)|(?:[-_\.]?(post|rev|r)[-_\.]?[0-9]*)
                )?
                (?:[-_\.]?dev[-_\.]?[0-9]*)?          # dev release
            )
            |
            (?:
                # All other operators only allow a sub set of what the
                # (non)equality operators do. Specifically they do not allow
                # local versions to be specified nor do they allow the prefix
                # matching wild cards.
                (?:<=|>=|<|>)

                \s*
                v?
                (?:[0-9]+!)?          # epoch
                [0-9]+(?:\.[0-9]+)*   # release
                (?a:                   # pre release
                    [-_\.]?
                    (alpha|beta|preview|pre|a|b|c|rc)
                    [-_\.]?
                    [0-9]*
                )?
                (?a:                                   # post release
                    (?:-[0-9]+)|(?:[-_\.]?(post|rev|r)[-_\.]?[0-9]*)
                )?
                (?a:[-_\.]?dev[-_\.]?[0-9]*)?          # dev release
            )
        )
        """

    _regex = re.compile(
        r"\s*" + _specifier_regex_str + r"\s*", re.VERBOSE | re.IGNORECASE
    )

    # Legacy unused attribute, kept for backward compatibility
    _operators: Final = {
        "~=": "compatible",
        "==": "equal",
        "!=": "not_equal",
        "<=": "less_than_equal",
        ">=": "greater_than_equal",
        "<": "less_than",
        ">": "greater_than",
        "===": "arbitrary",
    }

    def __init__(self, spec: str = "", prereleases: bool | None = None) -> None:
        """Initialize a Specifier instance.

        :param spec:
            The string representation of a specifier which will be parsed and
            normalized before use.
        :param prereleases:
            This tells the specifier if it should accept prerelease versions if
            applicable or not. The default of ``None`` will autodetect it from the
            given specifiers.
        :raises InvalidSpecifier:
            If the given specifier is invalid (i.e. bad syntax).
        """
        if not self._regex.fullmatch(spec):
            raise InvalidSpecifier(f"Invalid specifier: {spec!r}")

        spec = spec.strip()
        if spec.startswith("==="):
            operator, version = spec[:3], spec[3:].strip()
        elif spec.startswith(("~=", "==", "!=", "<=", ">=")):
            operator, version = spec[:2], spec[2:].strip()
        else:
            operator, version = spec[:1], spec[1:].strip()

        self._spec: tuple[str, str] = (operator, version)

        # Store whether or not this Specifier should accept prereleases
        self._prereleases = prereleases

        # Specifier version cache
        self._spec_version: tuple[str, Version] | None = None

        # Version range cache (populated by _to_ranges)
        self._ranges: Sequence[Interval] | None = None

    def _get_spec_version(self, version: str) -> Version | None:
        """One element cache, as only one spec Version is needed per Specifier."""
        if self._spec_version is not None and self._spec_version[0] == version:
            return self._spec_version[1]

        version_specifier = coerce_version(version)
        if version_specifier is None:
            return None

        self._spec_version = (version, version_specifier)
        return version_specifier

    def _require_spec_version(self, version: str) -> Version:
        """Get spec version, asserting it's valid (not for === operator).

        This method should only be called for operators where version
        strings are guaranteed to be valid PEP 440 versions (not ===).
        """
        spec_version = self._get_spec_version(version)
        assert spec_version is not None
        return spec_version

    def _to_ranges(self) -> Sequence[Interval]:
        """Convert this specifier to sorted, non-overlapping version ranges.

        Each standard operator maps to one or two ranges.  ``===`` is
        modeled as full range (actual check done separately).  Cached.
        """
        if self._ranges is not None:
            return self._ranges

        op = self.operator
        ver_str = self.version

        if op == "===":
            result: Sequence[Interval] = FULL_RANGE
        else:
            version = self._require_spec_version(ver_str.removesuffix(".*"))
            result = bounds_for_spec(op, ver_str, version)

        self._ranges = result
        return result

    @property
    def prereleases(self) -> bool | None:
        # If there is an explicit prereleases set for this, then we'll just
        # blindly use that.
        if self._prereleases is not None:
            return self._prereleases

        # Only the "!=" operator does not imply prereleases when
        # the version in the specifier is a prerelease.
        operator, version_str = self._spec
        if operator == "!=":
            return False

        # The == specifier with trailing .* cannot include prereleases
        # e.g. "==1.0a1.*" is not valid.
        if operator == "==" and version_str.endswith(".*"):
            return False

        # "===" can have arbitrary string versions, so we cannot parse
        # those, we take prereleases as unknown (None) for those.
        version = self._get_spec_version(version_str)
        if version is None:
            return None

        # For all other operators, use the check if spec Version
        # object implies pre-releases.
        return version.is_prerelease

    @prereleases.setter
    def prereleases(self, value: bool | None) -> None:
        self._prereleases = value

    def __getstate__(self) -> tuple[tuple[str, str], bool | None]:
        # Return state as a 2-item tuple for compactness:
        #   ((operator, version), prereleases)
        # Cache members are excluded and will be recomputed on demand.
        return (self._spec, self._prereleases)

    def __setstate__(self, state: object) -> None:
        # Always discard cached values - they will be recomputed on demand.
        self._spec_version = None
        self._ranges = None

        if isinstance(state, tuple):
            if len(state) == 2:
                # New format (26.2+): ((operator, version), prereleases)
                spec, prereleases = state
                if _validate_spec(spec) and _validate_pre(prereleases):
                    self._spec = spec
                    self._prereleases = prereleases
                    return
            if len(state) == 2 and isinstance(state[1], dict):
                # Format (packaging 26.0-26.1): (None, {slot: value}).
                _, slot_dict = state
