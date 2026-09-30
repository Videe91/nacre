# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

import logging
import operator
import platform
import re
import struct
import subprocess
import sys
import sysconfig
from collections.abc import Iterable, Iterator, Sequence
from importlib.machinery import EXTENSION_SUFFIXES
from typing import (
    TYPE_CHECKING,
    TypeVar,
    cast,
)

from . import _manylinux, _musllinux

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Set as AbstractSet


__all__ = [
    "INTERPRETER_SHORT_NAMES",
    "AppleVersion",
    "InvalidTag",
    "PythonVersion",
    "Tag",
    "TooManyTagsError",
    "UnsortedTagsError",
    "android_platforms",
    "compatible_tags",
    "cpython_tags",
    "create_compatible_tags_selector",
    "generic_tags",
    "interpreter_name",
    "interpreter_version",
    "ios_platforms",
    "mac_platforms",
    "parse_tag",
    "platform_tags",
    "pure_python_tags",
    "sys_tags",
]


def __dir__() -> list[str]:
    return __all__


logger = logging.getLogger(__name__)

PythonVersion = Sequence[int]
"""
A sequence of integers describing a Python version, e.g. ``(3, 13)``.

.. versionadded:: 20.0
"""

AppleVersion = tuple[int, int]
"""
A ``(major, minor)`` integer pair describing an Apple OS version.

.. versionadded:: 24.2
"""
_T = TypeVar("_T")

INTERPRETER_SHORT_NAMES: dict[str, str] = {
    "python": "py",  # Generic.
    "cpython": "cp",
    "pypy": "pp",
    "ironpython": "ip",
    "jython": "jy",
}


# This function can be unit tested without reloading the module
# (Unlike _32_BIT_INTERPRETER)
def _compute_32_bit_interpreter() -> bool:
    return struct.calcsize("P") == 4


_32_BIT_INTERPRETER = _compute_32_bit_interpreter()


class UnsortedTagsError(ValueError):
    """
    Raised when a tag component is not in sorted order per PEP 425.

    .. versionadded:: 26.1
    """


class InvalidTag(ValueError):
    """
    Raised when an interpreter component is not an identifier, a tag component
    is empty, or a tag does not have exactly three components.

    .. versionadded:: 26.3
    """


class TooManyTagsError(ValueError):
    """
    Raised when a compressed tag set exceeds the configured limit.

    .. versionadded:: 26.3
    """


class Tag:
    """
    A representation of the tag triple for a wheel.

    Instances are considered immutable and thus are hashable. Equality checking
    is also supported.

    Instances are safe to serialize with :mod:`pickle`. They use a stable
    format so the same pickle can be loaded in future packaging releases.

    .. versionchanged:: 26.2

        Added a stable pickle format. Pickles created with packaging 26.2+ can
        be unpickled with future releases.  Backward compatibility with pickles
        from packaging < 26.2 is supported but may be removed in a future
        release.
    """

    __slots__ = ["_abi", "_hash", "_interpreter", "_platform"]

    def __init__(self, interpreter: str, abi: str, platform: str) -> None:
        """
        :param str interpreter: The interpreter name, e.g. ``"py"``
                                (see :attr:`INTERPRETER_SHORT_NAMES` for mapping
                                well-known interpreter names to their short names).
        :param str abi: The ABI that a wheel supports, e.g. ``"cp37m"``.
        :param str platform: The OS/platform the wheel supports,
                            e.g. ``"win_amd64"``.
        """
        self._interpreter = interpreter.lower()
        self._abi = abi.lower()
        self._platform = platform.lower()
        # The __hash__ of every single element in a Set[Tag] will be evaluated each time
        # that a set calls its `.disjoint()` method, which may be called hundreds of
        # times when scanning a page of links for packages with tags matching that
        # Set[Tag]. Pre-computing the value here produces significant speedups for
        # downstream consumers.
        self._hash = hash((self._interpreter, self._abi, self._platform))

    @property
    def interpreter(self) -> str:
        """
        The interpreter name, e.g. ``"py"`` (see
        :attr:`INTERPRETER_SHORT_NAMES` for mapping well-known interpreter
        names to their short names).
        """
        return self._interpreter

    @property
    def abi(self) -> str:
        """
        The supported ABI.
        """
        return self._abi

    @property
    def platform(self) -> str:
        """
        The OS/platform.
        """
        return self._platform

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Tag):
            return NotImplemented

        return (
            (self._hash == other._hash)  # Short-circuit ASAP for perf reasons.
            and (self._platform == other._platform)
            and (self._abi == other._abi)
            and (self._interpreter == other._interpreter)
        )

    def __hash__(self) -> int:
        return self._hash

    def __str__(self) -> str:
        return f"{self._interpreter}-{self._abi}-{self._platform}"

    def __repr__(self) -> str:
        return f"<{self} @ {id(self)}>"

    def __getstate__(self) -> tuple[str, str, str]:
        # Return state as a 3-item tuple: (interpreter, abi, platform).
        # Cache member _hash is excluded and will be recomputed.
        return (self._interpreter, self._abi, self._platform)

    def __setstate__(self, state: object) -> None:
        if isinstance(state, tuple):
            if len(state) == 3 and all(isinstance(s, str) for s in state):
                # New format (26.2+): (interpreter, abi, platform)
                self._interpreter, self._abi, self._platform = state
                self._hash = hash((self._interpreter, self._abi, self._platform))
                return
            if len(state) == 2 and isinstance(state[1], dict):
                # Old format (packaging <= 26.1, __slots__): (None, {slot: value}).
                _, slots = state
                try:
                    interpreter = slots["_interpreter"]
                    abi = slots["_abi"]
                    platform = slots["_platform"]
                except KeyError:
                    raise TypeError(f"Cannot restore Tag from {state!r}") from None
                if not all(
                    isinstance(value, str) for value in (interpreter, abi, platform)
                ):
                    raise TypeError(f"Cannot restore Tag from {state!r}")
                self._interpreter = interpreter.lower()
                self._abi = abi.lower()
                self._platform = platform.lower()
                self._hash = hash((self._interpreter, self._abi, self._platform))
                return
        raise TypeError(f"Cannot restore Tag from {state!r}")


def parse_tag(
    tag: str, *, validate_order: bool = False, limit: int | None = None
) -> frozenset[Tag]:
    """
    Parses the provided tag (e.g. `py3-none-any`) into a frozenset of
    :class:`Tag` instances.

    Returning a set is required due to the possibility that the tag is a
    `compressed tag set`_, e.g. ``"py2.py3-none-any"`` which supports both
    Python 2 and Python 3.

    If **validate_order** is true, compressed tag set components are checked
    to be in sorted order as required by PEP 425.

    If **limit** is not ``None``, the compressed tag set can generate at most
    that many tags.

    :param str tag: The tag to parse, e.g. ``"py3-none-any"``.
    :param bool validate_order: Check whether compressed tag set components
        are in sorted order.
    :param int | None limit: The maximum number of tags to parse.
    :raises UnsortedTagsError: If **validate_order** is true and any compressed tag
        set component is not in sorted order.
    :raises InvalidTag: If the interpreter field is not an identifier; if the
        interpreter, ABI, or platform field (or any member of a compressed tag
        set) is empty; or if the tag does not have exactly three components.
    :raises TooManyTagsError: If **limit** is not ``None`` and the compressed tag
        set would generate more than **limit** tags.
    :raises ValueError: If **limit** is negative.

    .. versionadded:: 26.1
       The *validate_order* parameter.

    .. versionadded:: 26.3
       Raises :class:`InvalidTag` when an interpreter component is not an
       identifier, a tag component is empty, or a tag does not have exactly
       three components.
       Added the *limit* parameter. Raises :class:`TooManyTagsError` if the compressed
       tag set would generate more than *limit* tags.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    component_parts = [component.split(".") for component in tag.split("-")]
    for parts in component_parts:
        if "" in parts:
            component = ".".join(parts)
            raise InvalidTag(f"Tag {tag!r} has an empty component: {component!r}")
        if validate_order and parts != sorted(parts):
            component = ".".join(parts)
            raise UnsortedTagsError(
                f"Tag component {component!r} is not in sorted order per PEP 425"
            )

    tag_count = 1
    for parts in component_parts:
        tag_count *= len(parts)

    if limit is not None and tag_count > limit:
        raise TooManyTagsError(
            f"Compressed tag set would generate {tag_count} tags, exceeding "
            f"limit {limit}"
        )

    try:
        interpreters, abis, platforms = component_parts
    except ValueError as exc:
        raise InvalidTag(f"Tag {tag!r} must have exactly three components") from exc
    for interpreter in interpreters:
        if not interpreter.isidentifier():
            raise InvalidTag(f"Tag {tag!r} has an invalid interpreter: {interpreter!r}")
    return frozenset(
        Tag(interpreter, abi, platform_)
        for interpreter in interpreters
        for abi in abis
        for platform_ in platforms
    )


def _get_config_var(name: str, warn: bool = False) -> int | str | None:
    value: int | str | None = sysconfig.get_config_var(name)
    if value is None and warn:
        logger.debug(
            "Config variable '%s' is unset, Python ABI tag may be incorrect", name
        )
    return value


def _normalize_string(string: str) -> str:
    return string.replace(".", "_").replace("-", "_").replace(" ", "_")


def _is_threaded_cpython(abis: list[str]) -> bool:
    """
    Determine if the ABI corresponds to a threaded (`--disable-gil`) build.

    The threaded builds are indicated by a "t" in the abiflags.
    """
    if len(abis) == 0:
        return False
    # expect e.g., cp313
    m = re.match(r"cp\d+(.*)", abis[0])
    if not m:
        return False
    abiflags = m.group(1)
    return "t" in abiflags


def _abi3_applies(python_version: PythonVersion, threading: bool) -> bool:
    """
    Determine if the Python version supports abi3.

    PEP 384 was first implemented in Python 3.2. The free-threaded
    builds do not support abi3.
    """
    return len(python_version) > 1 and tuple(python_version) >= (3, 2) and not threading


def _abi3t_applies(python_version: PythonVersion, threading: bool) -> bool:
    """
    Determine if the Python version supports abi3t.

    PEP 803 was first implemented in Python 3.15 but, per PEP 803, this
    returns tags going back to Python 3.2 to mirror the abi3
    implementation and leave open the possibility of abi3t wheels
    supporting older Python versions.

    """
    return len(python_version) > 1 and tuple(python_version) >= (3, 2) and threading


def _cpython_abis(py_version: PythonVersion, warn: bool = False) -> list[str]:
    py_version = tuple(py_version)  # To allow for version comparison.
    abis = []
    version = _version_nodot(py_version[:2])
    threading = debug = pymalloc = ucs4 = ""
    with_debug = _get_config_var("Py_DEBUG", warn)
    has_refcount = hasattr(sys, "gettotalrefcount")
    # Windows doesn't set Py_DEBUG, so checking for support of debug-compiled
    # extension modules is the best option.
    # https://github.com/pypa/pip/issues/3383#issuecomment-173267692
    has_ext = "_d.pyd" in EXTENSION_SUFFIXES
    if with_debug or (with_debug is None and (has_refcount or has_ext)):
        debug = "d"
    if py_version >= (3, 13) and _get_config_var("Py_GIL_DISABLED", warn):
        threading = "t"
    if py_version < (3, 8):
        with_pymalloc = _get_config_var("WITH_PYMALLOC", warn)
        if with_pymalloc or with_pymalloc is None:
            pymalloc = "m"
        if py_version < (3, 3):
            unicode_size = _get_config_var("Py_UNICODE_SIZE", warn)
            if unicode_size == 4 or (
                unicode_size is None and sys.maxunicode == 0x10FFFF
            ):
                ucs4 = "u"
    elif debug:
        # Debug builds can also load "normal" extension modules.
        # We can also assume no UCS-4 or pymalloc requirement.
        abis.append(f"cp{version}{threading}")
    abis.insert(0, f"cp{version}{threading}{debug}{pymalloc}{ucs4}")
    return abis


def cpython_tags(
    python_version: PythonVersion | None = None,
    abis: Iterable[str] | None = None,
    platforms: Iterable[str] | None = None,
    *,
    warn: bool = False,
) -> Iterator[Tag]:
    """
    Yields the tags for the CPython interpreter.

    The specific tags generated are:

    - ``cp<python_version>-<abi>-<platform>``
    - ``cp<python_version>-<stable_abi>-<platform>``
    - ``cp<python_version>-none-<platform>``
    - ``cp<older version>-<stable_abi>-<platform>`` where "older version" is all older
      minor versions down to Python 3.2 (when ``abi3`` was introduced)

    If ``python_version`` only provides a major-only version then only
    user-provided ABIs via ``abis`` and the ``none`` ABI will be used.

    The ``stable_abi`` will be either ``abi3`` or ``abi3t`` if `abi` is a
    GIL-enabled ABI like `"cp315"` or a free-threaded ABI like `"cp315t"`,
    respectively.

    :param Sequence python_version: A one- or two-item sequence representing the
                                 targeted Python version. Defaults to
                                 ``sys.version_info[:2]``.
    :param Iterable abis: Iterable of compatible ABIs. Defaults to the ABIs
                          compatible with the current system.
    :param Iterable platforms: Iterable of compatible platforms. Defaults to the
                               platforms compatible with the current system.
    :param bool warn: Whether warnings should be logged. Defaults to ``False``.

    .. versionadded:: 20.0
    """
    if not python_version:
        python_version = sys.version_info[:2]

    interpreter = f"cp{_version_nodot(python_version[:2])}"

    if abis is None:
        abis = _cpython_abis(python_version, warn) if len(python_version) > 1 else []
    abis = list(abis)
    threading = _is_threaded_cpython(abis)
    # Stable ABIs and 'none' are explicitly handled later.
    explicit_abis = ("abi3", "abi3t", "none") if threading else ("abi3", "none")
    for explicit_abi in explicit_abis:
        try:
            abis.remove(explicit_abi)
        except ValueError:  # noqa: PERF203
            pass

    platforms = list(platforms or platform_tags())
    for abi in abis:
        for platform_ in platforms:
            yield Tag(interpreter, abi, platform_)

    use_abi3 = _abi3_applies(python_version, threading)
    use_abi3t = _abi3t_applies(python_version, threading)
    if use_abi3:
        yield from (Tag(interpreter, "abi3", platform_) for platform_ in platforms)
    if use_abi3t:
        yield from (Tag(interpreter, "abi3t", platform_) for platform_ in platforms)

    yield from (Tag(interpreter, "none", platform_) for platform_ in platforms)

    if use_abi3 or use_abi3t:
        for minor_version in range(python_version[1] - 1, 1, -1):
            for platform_ in platforms:
                version = _version_nodot((python_version[0], minor_version))
                interpreter = f"cp{version}"
                if use_abi3:
                    yield Tag(interpreter, "abi3", platform_)
                if use_abi3t:
