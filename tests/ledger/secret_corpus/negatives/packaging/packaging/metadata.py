from __future__ import annotations

import email.header
import email.message
import email.parser
import email.policy
import keyword
import pathlib
import re
import typing
from typing import (
    Any,
    Callable,
    Generic,
    Literal,
    TypedDict,
    cast,
)

from . import licenses, requirements, specifiers, utils
from . import version as version_module
from .errors import ExceptionGroup, _ErrorCollector

if typing.TYPE_CHECKING:
    from .licenses import NormalizedLicenseExpression
    from .version import Version

T = typing.TypeVar("T")


__all__ = [
    "ExceptionGroup",  # Keep this for a bit (makes mypy happy w/ 26.0 compat)
    "InvalidMetadata",
    "Metadata",
    "RFC822Message",
    "RFC822Policy",
    "RawMetadata",
    "parse_email",
]


def __dir__() -> list[str]:
    return __all__


class InvalidMetadata(ValueError):
    """A metadata field contains invalid data.

    .. versionadded:: 23.2
    """

    field: str
    """The name of the field that contains invalid data."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)

    # Support pickling; ``args`` does not match ``__init__``'s signature.
    def __reduce__(self) -> tuple[type[InvalidMetadata], tuple[str, str]]:
        return (self.__class__, (self.field, self.args[0]))


# The RawMetadata class attempts to make as few assumptions about the underlying
# serialization formats as possible. The idea is that as long as a serialization
# formats offer some very basic primitives in *some* way then we can support
# serializing to and from that format.
class RawMetadata(TypedDict, total=False):
    """A dictionary of raw core metadata.

    Each field in core metadata maps to a key of this dictionary (when data is
    provided). The key is lower-case and underscores are used instead of dashes
    compared to the equivalent core metadata field. Any core metadata field that
    can be specified multiple times or can hold multiple values in a single
    field have a key with a plural name. See :class:`Metadata` whose attributes
    match the keys of this dictionary.

    Core metadata fields that can be specified multiple times are stored as a
    list or dict depending on which is appropriate for the field. Any fields
    which hold multiple values in a single field are stored as a list. All fields
    are considered optional.
    """

    # Metadata 1.0 - PEP 241
    metadata_version: str
    name: str
    version: str
    platforms: list[str]
    summary: str
    description: str
    keywords: list[str]
    home_page: str
    author: str
    author_email: str
    license: str

    # Metadata 1.1 - PEP 314
    supported_platforms: list[str]
    download_url: str
    classifiers: list[str]
    requires: list[str]
    provides: list[str]
    obsoletes: list[str]

    # Metadata 1.2 - PEP 345
    maintainer: str
    maintainer_email: str
    requires_dist: list[str]
    provides_dist: list[str]
    obsoletes_dist: list[str]
    requires_python: str
    requires_external: list[str]
    project_urls: dict[str, str]

    # Metadata 2.0
    # PEP 426 attempted to completely revamp the metadata format
    # but got stuck without ever being able to build consensus on
    # it and ultimately ended up withdrawn.
    #
    # However, a number of tools had started emitting METADATA with
    # `2.0` Metadata-Version, so for historical reasons, this version
    # was skipped.

    # Metadata 2.1 - PEP 566
    description_content_type: str
    provides_extra: list[str]

    # Metadata 2.2 - PEP 643
    dynamic: list[str]

    # Metadata 2.3 - PEP 685
    # No new fields were added in PEP 685, just some edge case were
    # tightened up to provide better interoperability.

    # Metadata 2.4 - PEP 639
    license_expression: str
    """.. versionadded:: 24.2"""
    license_files: list[str]
    """.. versionadded:: 24.2"""

    # Metadata 2.5 - PEP 794
    import_names: list[str]
    """.. versionadded:: 26.0"""
    import_namespaces: list[str]
    """.. versionadded:: 26.0"""

    # Metadata 2.6 - PEP 808 (no new fields, behavior change for Dynamic)


# 'keywords' is special as it's a string in the core metadata spec, but we
# represent it as a list.
_STRING_FIELDS = {
    "author",
    "author_email",
    "description",
    "description_content_type",
    "download_url",
    "home_page",
    "license",
    "license_expression",
    "maintainer",
    "maintainer_email",
    "metadata_version",
    "name",
    "requires_python",
    "summary",
    "version",
}

_LIST_FIELDS = {
    "classifiers",
    "dynamic",
    "license_files",
    "obsoletes",
    "obsoletes_dist",
    "platforms",
    "provides",
    "provides_dist",
    "provides_extra",
    "requires",
    "requires_dist",
    "requires_external",
    "supported_platforms",
    "import_names",
    "import_namespaces",
}

_DICT_FIELDS = {
    "project_urls",
}


def _parse_keywords(data: str) -> list[str]:
    """Split a string of comma-separated keywords into a list of keywords."""
    return [k.strip() for k in data.split(",")]


def _parse_project_urls(data: list[str]) -> dict[str, str]:
    """Parse a list of label/URL string pairings separated by a comma."""
    urls = {}
    for pair in data:
        # Our logic is slightly tricky here as we want to try and do
        # *something* reasonable with malformed data.
        #
        # The main thing that we have to worry about, is data that does
        # not have a ',' at all to split the label from the Value. There
        # isn't a singular right answer here, and we will fail validation
        # later on (if the caller is validating) so it doesn't *really*
        # matter, but since the missing value has to be an empty str
        # and our return value is dict[str, str], if we let the key
        # be the missing value, then they'd have multiple '' values that
        # overwrite each other in a accumulating dict.
        #
        # The other potential issue is that it's possible to have the
        # same label multiple times in the metadata, with no solid "right"
        # answer with what to do in that case. As such, we'll do the only
        # thing we can, which is treat the field as unparsable and add it
        # to our list of unparsed fields.
        #
        # TODO: The spec doesn't say anything about if the keys should be
        #       considered case sensitive or not... logically they should
        #       be case-preserving and case-insensitive, but doing that
        #       would open up more cases where we might have duplicate
        #       entries.
        label, _, url = (s.strip() for s in pair.partition(","))

        if label in urls:
            # The label already exists in our set of urls, so this field
            # is unparsable, and we can just add the whole thing to our
            # unparsable data and stop processing it.
            raise KeyError("duplicate labels in project urls")
        urls[label] = url

    return urls


def _get_payload(msg: email.message.Message, source: bytes | str) -> str:
    """Get the body of the message."""
    # If our source is a str, then our caller has managed encodings for us,
    # and we don't need to deal with it.
    if isinstance(source, str):
        payload = msg.get_payload()
        # A multipart payload makes get_payload() return a list of messages
        # rather than a str; route it to ``unparsed``.
        if not isinstance(payload, str):
            raise ValueError("payload is not a string")  # noqa: TRY004
        return payload
    # If our source is a bytes, then we're managing the encoding and we need
    # to deal with it.
    else:
        bpayload = msg.get_payload(decode=True)
        # A multipart payload makes get_payload(decode=True) return None;
        # route it to ``unparsed``.
        if not isinstance(bpayload, bytes):
            raise ValueError("payload in an invalid encoding")  # noqa: TRY004
        try:
            return bpayload.decode("utf8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("payload in an invalid encoding") from exc


# The various parse_FORMAT functions here are intended to be as lenient as
# possible in their parsing, while still returning a correctly typed
# RawMetadata.
#
# To aid in this, we also generally want to do as little touching of the
# data as possible, except where there are possibly some historic holdovers
# that make valid data awkward to work with.
#
# While this is a lower level, intermediate format than our ``Metadata``
# class, some light touch ups can make a massive difference in usability.

# Map METADATA fields to RawMetadata.
_EMAIL_TO_RAW_MAPPING = {
    "author": "author",
    "author-email": "author_email",
    "classifier": "classifiers",
    "description": "description",
    "description-content-type": "description_content_type",
    "download-url": "download_url",
    "dynamic": "dynamic",
    "home-page": "home_page",
    "import-name": "import_names",
    "import-namespace": "import_namespaces",
    "keywords": "keywords",
    "license": "license",
    "license-expression": "license_expression",
    "license-file": "license_files",
    "maintainer": "maintainer",
    "maintainer-email": "maintainer_email",
    "metadata-version": "metadata_version",
    "name": "name",
    "obsoletes": "obsoletes",
    "obsoletes-dist": "obsoletes_dist",
    "platform": "platforms",
    "project-url": "project_urls",
    "provides": "provides",
    "provides-dist": "provides_dist",
    "provides-extra": "provides_extra",
    "requires": "requires",
    "requires-dist": "requires_dist",
    "requires-external": "requires_external",
    "requires-python": "requires_python",
    "summary": "summary",
    "supported-platform": "supported_platforms",
    "version": "version",
}
_RAW_TO_EMAIL_MAPPING = {raw: email for email, raw in _EMAIL_TO_RAW_MAPPING.items()}


# A bare "\r" makes the email generator raise ``HeaderWriteError``, and on
# CPython releases without the CVE-2024-6923 fix any ``str.splitlines``
# boundary ends the header line, so fold all of them, not just "\n".
_LINE_BOUNDARY_RE = re.compile(r"\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")


# This class is for writing RFC822 messages
class RFC822Policy(email.policy.EmailPolicy):
    """
    This is :class:`email.policy.EmailPolicy`, but with a simple ``header_store_parse``
    implementation that handles multi-line values, and some nice defaults.

    .. versionadded:: 26.0
    """

    utf8 = True
    mangle_from_ = False
    max_line_length = 0

    def header_store_parse(self, name: str, value: str) -> tuple[str, str]:
        size = len(name) + 2
        value = _LINE_BOUNDARY_RE.sub("\n" + " " * size, value)
        return (name, value)


# This class is for writing RFC822 messages
class RFC822Message(email.message.EmailMessage):
    """
    This is :class:`email.message.EmailMessage` with two small changes: it defaults to
    our `RFC822Policy`, and it correctly writes unicode when being called
    with `bytes()`.

    .. versionadded:: 26.0
    """

    def __init__(self) -> None:
        super().__init__(policy=RFC822Policy())

    def as_bytes(
        self, unixfrom: bool = False, policy: email.policy.Policy | None = None
    ) -> bytes:
        """
        Return the bytes representation of the message.

        This handles unicode encoding.
        """
        return self.as_string(unixfrom, policy=policy).encode("utf-8")


def parse_email(data: bytes | str) -> tuple[RawMetadata, dict[str, list[str]]]:
    """Parse a distribution's metadata stored as email headers (e.g. from ``METADATA``).

    This function returns a two-item tuple of dicts. The first dict is of
    recognized fields from the core metadata specification. Fields that can be
    parsed and translated into Python's built-in types are converted
    appropriately. All other fields are left as-is. Fields that are allowed to
    appear multiple times are stored as lists.

    The second dict contains all other fields from the metadata. This includes
    any unrecognized fields. It also includes any fields which are expected to
    be parsed into a built-in type but were not formatted appropriately. Finally,
    any fields that are expected to appear only once but are repeated are
    included in this dict.

    """
    raw: dict[str, str | list[str] | dict[str, str]] = {}
    unparsed: dict[str, list[str]] = {}

    if isinstance(data, str):
        parsed = email.parser.Parser(policy=email.policy.compat32).parsestr(data)
    else:
        parsed = email.parser.BytesParser(policy=email.policy.compat32).parsebytes(data)

    # We have to wrap parsed.keys() in a set, because in the case of multiple
    # values for a key (a list), the key will appear multiple times in the
    # list of keys, but we're avoiding that by using get_all().
    for name_with_case in frozenset(parsed.keys()):
        # Header names in RFC are case insensitive, so we'll normalize to all
        # lower case to make comparisons easier.
        name = name_with_case.lower()

        # We use get_all() here, even for fields that aren't multiple use,
        # because otherwise someone could have e.g. two Name fields, and we
        # would just silently ignore it rather than doing something about it.
        headers = parsed.get_all(name) or []

        # The way the email module works when parsing bytes is that it
        # unconditionally decodes the bytes as ascii using the surrogateescape
        # handler. When you pull that data back out (such as with get_all() ),
        # it looks to see if the str has any surrogate escapes, and if it does
        # it wraps it in a Header object instead of returning the string.
        #
        # As such, we'll look for those Header objects, and fix up the encoding.
        value = []
        # Flag if we have run into any issues processing the headers, thus
        # signalling that the data belongs in 'unparsed'.
        valid_encoding = True
        for h in headers:
            # It's unclear if this can return more types than just a Header or
            # a str, so we'll just assert here to make sure.
            assert isinstance(h, (email.header.Header, str))

            # If it's a header object, we need to do our little dance to get
            # the real data out of it. In cases where there is invalid data
            # we're going to end up with mojibake, but there's no obvious, good
            # way around that without reimplementing parts of the Header object
            # ourselves.
            #
            # That should be fine since, if mojibacked happens, this key is
            # going into the unparsed dict anyways.
            if isinstance(h, email.header.Header):
                # The Header object stores it's data as chunks, and each chunk
                # can be independently encoded, so we'll need to check each
                # of them.
                chunks: list[tuple[bytes, str | None]] = []
                for binary, _encoding in email.header.decode_header(h):
                    try:
                        binary.decode("utf8", "strict")
                    except UnicodeDecodeError:
                        # Enable mojibake.
                        encoding = "latin1"
                        valid_encoding = False
                    else:
                        encoding = "utf8"
                    chunks.append((binary, encoding))

                # Turn our chunks back into a Header object, then let that
                # Header object do the right thing to turn them into a
                # string for us.
                value.append(str(email.header.make_header(chunks)))
            # This is already a string, so just add it.
            else:
                value.append(h)

        # We've processed all of our values to get them into a list of str,
        # but we may have mojibake data, in which case this is an unparsed
        # field.
        if not valid_encoding:
            unparsed[name] = value
            continue

        raw_name = _EMAIL_TO_RAW_MAPPING.get(name)
        if raw_name is None:
            # This is a bit of a weird situation, we've encountered a key that
