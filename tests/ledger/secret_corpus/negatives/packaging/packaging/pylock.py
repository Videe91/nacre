from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Protocol,
    TypeVar,
    cast,
)
from urllib.parse import unquote, urlparse

from .markers import (
    Environment,
    Marker,
    _pep440_python_full_version,
    default_environment,
)
from .specifiers import SpecifierSet
from .tags import create_compatible_tags_selector, sys_tags
from .utils import (
    NormalizedName,
    is_normalized_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from .version import Version

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Collection, Iterator
    from pathlib import Path

    from typing_extensions import Self

    from .tags import Tag

_logger = logging.getLogger(__name__)

__all__ = [
    "Package",
    "PackageArchive",
    "PackageDirectory",
    "PackageSdist",
    "PackageVcs",
    "PackageWheel",
    "Pylock",
    "PylockSelectError",
    "PylockUnsupportedVersionError",
    "PylockValidationError",
    "is_valid_pylock_path",
]


def __dir__() -> list[str]:
    return __all__


_T = TypeVar("_T")
_T2 = TypeVar("_T2")


class _FromMappingProtocol(Protocol):  # pragma: no cover
    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self: ...


_FromMappingProtocolT = TypeVar("_FromMappingProtocolT", bound=_FromMappingProtocol)


_PYLOCK_FILE_NAME_RE = re.compile(r"^pylock\.([^.]+)\.toml$")


def is_valid_pylock_path(path: Path) -> bool:
    """Check if the given path is a valid pylock file path."""
    return path.name == "pylock.toml" or bool(_PYLOCK_FILE_NAME_RE.match(path.name))


def _toml_key(key: str) -> str:
    return key.replace("_", "-")


def _toml_value(key: str, value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, (Version, Marker, SpecifierSet)):
        return str(value)
    if isinstance(value, Sequence) and key == "environments":
        return [str(v) for v in value]
    return value


def _toml_dict_factory(data: list[tuple[str, Any]]) -> dict[str, Any]:
    return {
        _toml_key(key): _toml_value(key, value)
        for key, value in data
        if value is not None
    }


def _get(d: Mapping[str, Any], expected_type: type[_T], key: str) -> _T | None:
    """Get a value from the dictionary and verify it's the expected type."""
    if (value := d.get(key)) is None:
        return None
    if not isinstance(value, expected_type) or (
        # Special case: bool is a subclass of int, but TOML distinguishes the two
        expected_type is int and isinstance(value, bool)
    ):
        raise PylockValidationError(
            f"Unexpected type {type(value).__name__} "
            f"(expected {expected_type.__name__})",
            context=key,
        )
    return value


def _get_required(d: Mapping[str, Any], expected_type: type[_T], key: str) -> _T:
    """Get a required value from the dictionary and verify it's the expected type."""
    if (value := _get(d, expected_type, key)) is None:
        raise _PylockRequiredKeyError(key)
    return value


def _get_sequence(
    d: Mapping[str, Any], expected_item_type: type[_T], key: str
) -> Sequence[_T] | None:
    """Get a list value from the dictionary and verify it's the expected items type."""
    if (value := _get(d, Sequence, key)) is None:  # type: ignore[type-abstract]
        return None
    if isinstance(value, (str, bytes)):
        # special case: str and bytes are Sequences, but we want to reject it
        raise PylockValidationError(
            f"Unexpected type {type(value).__name__} (expected Sequence)",
            context=key,
        )
    for i, item in enumerate(value):
        if not isinstance(item, expected_item_type):
            raise PylockValidationError(
                f"Unexpected type {type(item).__name__} "
                f"(expected {expected_item_type.__name__})",
                context=f"{key}[{i}]",
            )
    return value


def _get_as(
    d: Mapping[str, Any],
    expected_type: type[_T],
    target_type: Callable[[_T], _T2],
    key: str,
) -> _T2 | None:
    """Get a value from the dictionary, verify it's the expected type,
    and convert to the target type.

    This assumes the target_type constructor accepts the value.
    """
    if (value := _get(d, expected_type, key)) is None:
        return None
    try:
        return target_type(value)
    except Exception as e:
        raise PylockValidationError(e, context=key) from e


def _get_required_as(
    d: Mapping[str, Any],
    expected_type: type[_T],
    target_type: Callable[[_T], _T2],
    key: str,
) -> _T2:
    """Get a required value from the dict, verify it's the expected type,
    and convert to the target type."""
    if (value := _get_as(d, expected_type, target_type, key)) is None:
        raise _PylockRequiredKeyError(key)
    return value


def _get_sequence_as(
    d: Mapping[str, Any],
    expected_item_type: type[_T],
    target_item_type: Callable[[_T], _T2],
    key: str,
) -> list[_T2] | None:
    """Get list value from dictionary and verify expected items type."""
    if (value := _get_sequence(d, expected_item_type, key)) is None:
        return None
    result = []
    try:
        for item in value:
            typed_item = target_item_type(item)
            result.append(typed_item)
    except Exception as e:
        raise PylockValidationError(e, context=f"{key}[{len(result)}]") from e
    return result


def _get_object(
    d: Mapping[str, Any], target_type: type[_FromMappingProtocolT], key: str
) -> _FromMappingProtocolT | None:
    """Get a dictionary value from the dictionary and convert it to a dataclass."""
    if (value := _get(d, Mapping, key)) is None:  # type: ignore[type-abstract]
        return None
    try:
        return target_type._from_dict(value)
    except Exception as e:
        raise PylockValidationError(e, context=key) from e


def _get_sequence_of_objects(
    d: Mapping[str, Any], target_item_type: type[_FromMappingProtocolT], key: str
) -> list[_FromMappingProtocolT] | None:
    """Get a list value from the dictionary and convert its items to a dataclass."""
    if (value := _get_sequence(d, Mapping, key)) is None:  # type: ignore[type-abstract]
        return None
    result: list[_FromMappingProtocolT] = []
    try:
        for item in value:
            typed_item = target_item_type._from_dict(item)
            result.append(typed_item)
    except Exception as e:
        raise PylockValidationError(e, context=f"{key}[{len(result)}]") from e
    return result


def _get_required_sequence_of_objects(
    d: Mapping[str, Any], target_item_type: type[_FromMappingProtocolT], key: str
) -> Sequence[_FromMappingProtocolT]:
    """Get a required list value from the dictionary and convert its items to a
    dataclass."""
    if (result := _get_sequence_of_objects(d, target_item_type, key)) is None:
        raise _PylockRequiredKeyError(key)
    return result


def _validate_normalized_name(name: str) -> NormalizedName:
    """Validate that a string is a NormalizedName."""
    if not is_normalized_name(name):
        raise PylockValidationError(f"Name {name!r} is not normalized")
    return NormalizedName(name)


def _validate_path_url(path: str | None, url: str | None) -> None:
    if not path and not url:
        raise PylockValidationError("path or url must be provided")


def _path_name(path: str | None) -> str | None:
    if not path:
        return None
    # If the path is relative it MAY use POSIX-style path separators explicitly
    # for portability
    if "/" in path:
        return path.rsplit("/", 1)[-1]
    elif "\\" in path:
        return path.rsplit("\\", 1)[-1]
    else:
        return path


def _url_name(url: str | None) -> str | None:
    if not url:
        return None
    url_path = urlparse(url).path
    # The last path component is percent-encoded, so decode it to the file name
    return unquote(url_path.rsplit("/", 1)[-1])


def _validate_hashes(hashes: Mapping[str, Any]) -> Mapping[str, Any]:
    if not hashes:
        raise PylockValidationError("At least one hash must be provided")
    if not all(isinstance(hash_val, str) for hash_val in hashes.values()):
        raise PylockValidationError("Hash values must be strings")
    return hashes


class PylockValidationError(Exception):
    """Raised when when input data is not spec-compliant."""

    context: str | None = None
    message: str

    def __init__(
        self,
        cause: str | Exception,
        *,
        context: str | None = None,
    ) -> None:
        if isinstance(cause, PylockValidationError):
            if cause.context:
                self.context = (
                    f"{context}.{cause.context}" if context else cause.context
                )
            else:
                self.context = context
            self.message = cause.message
        else:
            self.context = context
            self.message = str(cause)

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} in {self.context!r}"
        return self.message


class _PylockRequiredKeyError(PylockValidationError):
    def __init__(self, key: str) -> None:
        super().__init__("Missing required value", context=key)


class PylockUnsupportedVersionError(PylockValidationError):
    """Raised when encountering an unsupported `lock_version`."""


class PylockSelectError(Exception):
    """Base exception for errors raised by :meth:`Pylock.select`.

    .. versionadded:: 26.1
    """


@dataclass(frozen=True, init=False)
class PackageVcs:
    type: str
    url: str | None = None
    path: str | None = None
    requested_revision: str | None = None
    commit_id: str  # type: ignore[misc]
    subdirectory: str | None = None

    def __init__(
        self,
        *,
        type: str,
        url: str | None = None,
        path: str | None = None,
        requested_revision: str | None = None,
        commit_id: str,
        subdirectory: str | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "requested_revision", requested_revision)
        object.__setattr__(self, "commit_id", commit_id)
        object.__setattr__(self, "subdirectory", subdirectory)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        package_vcs = cls(
            type=_get_required(d, str, "type"),
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            requested_revision=_get(d, str, "requested-revision"),
            commit_id=_get_required(d, str, "commit-id"),
            subdirectory=_get(d, str, "subdirectory"),
        )
        _validate_path_url(package_vcs.path, package_vcs.url)
        return package_vcs


@dataclass(frozen=True, init=False)
class PackageDirectory:
    path: str
    editable: bool | None = None
    subdirectory: str | None = None

    def __init__(
        self,
        *,
        path: str,
        editable: bool | None = None,
        subdirectory: str | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "editable", editable)
        object.__setattr__(self, "subdirectory", subdirectory)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            path=_get_required(d, str, "path"),
            editable=_get(d, bool, "editable"),
            subdirectory=_get(d, str, "subdirectory"),
        )


@dataclass(frozen=True, init=False)
class PackageArchive:
    url: str | None = None
    path: str | None = None
    size: int | None = None
    upload_time: datetime | None = None
    hashes: Mapping[str, str]  # type: ignore[misc]
    subdirectory: str | None = None

    def __init__(
        self,
        *,
        url: str | None = None,
        path: str | None = None,
        size: int | None = None,
        upload_time: datetime | None = None,
        hashes: Mapping[str, str],
        subdirectory: str | None = None,
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "upload_time", upload_time)
        object.__setattr__(self, "hashes", hashes)
        object.__setattr__(self, "subdirectory", subdirectory)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        package_archive = cls(
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            size=_get(d, int, "size"),
            upload_time=_get(d, datetime, "upload-time"),
            hashes=_get_required_as(d, Mapping, _validate_hashes, "hashes"),  # type: ignore[type-abstract]
            subdirectory=_get(d, str, "subdirectory"),
        )
        _validate_path_url(package_archive.path, package_archive.url)
        return package_archive


@dataclass(frozen=True, init=False)
class PackageSdist:
    name: str | None = None
    upload_time: datetime | None = None
    url: str | None = None
    path: str | None = None
    size: int | None = None
    hashes: Mapping[str, str]  # type: ignore[misc]

    def __init__(
        self,
        *,
        name: str | None = None,
        upload_time: datetime | None = None,
        url: str | None = None,
        path: str | None = None,
        size: int | None = None,
        hashes: Mapping[str, str],
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "upload_time", upload_time)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "hashes", hashes)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        package_sdist = cls(
            name=_get(d, str, "name"),
            upload_time=_get(d, datetime, "upload-time"),
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            size=_get(d, int, "size"),
            hashes=_get_required_as(d, Mapping, _validate_hashes, "hashes"),  # type: ignore[type-abstract]
        )
        _validate_path_url(package_sdist.path, package_sdist.url)
        return package_sdist

    @property
    def filename(self) -> str:
        """Get the filename of the sdist.

        .. versionadded:: 26.1
        """
        filename = self.name or _path_name(self.path) or _url_name(self.url)
        if not filename:
            raise PylockValidationError("Cannot determine sdist filename")
        return filename


@dataclass(frozen=True, init=False)
class PackageWheel:
    name: str | None = None
    upload_time: datetime | None = None
    url: str | None = None
    path: str | None = None
    size: int | None = None
    hashes: Mapping[str, str]  # type: ignore[misc]

    def __init__(
        self,
        *,
        name: str | None = None,
        upload_time: datetime | None = None,
        url: str | None = None,
        path: str | None = None,
        size: int | None = None,
        hashes: Mapping[str, str],
    ) -> None:
        # In Python 3.10+ make dataclass kw_only=True and remove __init__
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "upload_time", upload_time)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "hashes", hashes)

    @classmethod
    def _from_dict(cls, d: Mapping[str, Any]) -> Self:
        package_wheel = cls(
            name=_get(d, str, "name"),
            upload_time=_get(d, datetime, "upload-time"),
            url=_get(d, str, "url"),
            path=_get(d, str, "path"),
            size=_get(d, int, "size"),
