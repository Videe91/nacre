from __future__ import annotations

import contextlib
import ctypes
import logging
import math
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from functools import partial
from hashlib import md5
from importlib.metadata import version
from typing import IO, TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import pathlib
    from typing import TypeGuard

    from fsspec.spec import AbstractFileSystem


DEFAULT_BLOCK_SIZE = 5 * 2**20

T = TypeVar("T")


def infer_storage_options(
    urlpath: str, inherit_storage_options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Infer storage options from URL path and merge it with existing storage
    options.

    Parameters
    ----------
    urlpath: str or unicode
        Either local absolute file path or URL (hdfs://namenode:8020/file.csv)
    inherit_storage_options: dict (optional)
        Its contents will get merged with the inferred information from the
        given path

    Returns
    -------
    Storage options dict.

    Examples
    --------
    >>> infer_storage_options('/mnt/datasets/test.csv')  # doctest: +SKIP
    {"protocol": "file", "path", "/mnt/datasets/test.csv"}
    >>> infer_storage_options(
    ...     'hdfs://username:pwd@node:123/mnt/datasets/test.csv?q=1',
    ...     inherit_storage_options={'extra': 'value'},
    ... )  # doctest: +SKIP
    {"protocol": "hdfs", "username": "username", "password": "pwd",
    "host": "node", "port": 123, "path": "/mnt/datasets/test.csv",
    "url_query": "q=1", "extra": "value"}
    """

    # Discover Windows paths including disk name in this special case.
    is_filesystem = re.match(r"^[a-zA-Z]:[\\/]", urlpath)

    # Discover URI according to RFC 3986: Scheme names consist of a
    # sequence of characters beginning with a letter and followed by
    # any combination of letters, digits, plus ("+"), period ("."),
    # or hyphen ("-").
    # https://datatracker.ietf.org/doc/html/rfc3986#section-3.1
    is_uri = re.match(r"^[a-zA-Z0-9+.-]+://", urlpath)

    if is_filesystem or is_uri is None:
        return {"protocol": "file", "path": urlpath}

    parsed_path = urlsplit(urlpath)
    protocol = parsed_path.scheme or "file"
    if parsed_path.fragment:
        path = "#".join([parsed_path.path, parsed_path.fragment])
    else:
        path = parsed_path.path
    if protocol == "file":
        # Special case parsing file protocol URL on Windows according to:
        # https://msdn.microsoft.com/en-us/library/jj710207.aspx
        windows_path = re.match(r"^/([a-zA-Z])[:|]([\\/].*)$", path)
        if windows_path:
            drive, path = windows_path.groups()
            path = f"{drive}:{path}"

    if protocol in ["http", "https"]:
        # for HTTP, we don't want to parse, as requests will anyway
        return {"protocol": protocol, "path": urlpath}

    options: dict[str, Any] = {"protocol": protocol, "path": path}

    if parsed_path.netloc:
        # Parse `hostname` from netloc manually because `parsed_path.hostname`
        # lowercases the hostname which is not always desirable (e.g. in S3):
        # https://github.com/dask/dask/issues/1417
        options["host"] = parsed_path.netloc.rsplit("@", 1)[-1].rsplit(":", 1)[0]

        if protocol in ("s3", "s3a", "gcs", "gs"):
            options["path"] = options["host"] + options["path"]
        else:
            options["host"] = options["host"]
        if parsed_path.port:
            options["port"] = parsed_path.port
        if parsed_path.username:
            options["username"] = parsed_path.username
        if parsed_path.password:
            options["password"] = parsed_path.password

    if parsed_path.query:
        options["url_query"] = parsed_path.query
    if parsed_path.fragment:
        options["url_fragment"] = parsed_path.fragment

    if inherit_storage_options:
        update_storage_options(options, inherit_storage_options)

    return options


def update_storage_options(
    options: dict[str, Any], inherited: dict[str, Any] | None = None
) -> None:
    if not inherited:
        inherited = {}
    collisions = set(options) & set(inherited)
    if collisions:
        for collision in collisions:
            if options.get(collision) != inherited.get(collision):
                raise KeyError(
                    f"Collision between inferred and specified storage "
                    f"option:\n{collision}"
                )
    options.update(inherited)


# Compression extensions registered via fsspec.compression.register_compression
compressions: dict[str, str] = {}


def infer_compression(filename: str) -> str | None:
    """Infer compression, if available, from filename.

    Infer a named compression type, if registered and available, from filename
    extension. This includes builtin (gz, bz2, zip) compressions, as well as
    optional compressions. See fsspec.compression.register_compression.
    """
    extension = os.path.splitext(filename)[-1].strip(".").lower()
    if extension in compressions:
        return compressions[extension]
    return None


def build_name_function(max_int: float) -> Callable[[int], str]:
    """Returns a function that receives a single integer
    and returns it as a string padded by enough zero characters
    to align with maximum possible integer

    >>> name_f = build_name_function(57)

    >>> name_f(7)
    '07'
    >>> name_f(31)
    '31'
    >>> build_name_function(1000)(42)
    '0042'
    >>> build_name_function(999)(42)
    '042'
    >>> build_name_function(0)(0)
    '0'
    """
    # handle corner cases max_int is 0 or exact power of 10
    max_int += 1e-8

    pad_length = int(math.ceil(math.log10(max_int)))

    def name_function(i: int) -> str:
        return str(i).zfill(pad_length)

    return name_function


def seek_delimiter(file: IO[bytes], delimiter: bytes, blocksize: int) -> bool:
    r"""Seek current file to file start, file end, or byte after delimiter seq.

    Seeks file to next chunk delimiter, where chunks are defined on file start,
    a delimiting sequence, and file end. Use file.tell() to see location afterwards.
    Note that file start is a valid split, so must be at offset > 0 to seek for
    delimiter.

    Parameters
    ----------
    file: a file
    delimiter: bytes
        a delimiter like ``b'\n'`` or message sentinel, matching file .read() type
    blocksize: int
        Number of bytes to read from the file at once.


    Returns
    -------
    Returns True if a delimiter was found, False if at file start or end.

    """

    if file.tell() == 0:
        # beginning-of-file, return without seek
        return False

    # Interface is for binary IO, with delimiter as bytes, but initialize last
    # with result of file.read to preserve compatibility with text IO.
    last: bytes | None = None
    while True:
        current = file.read(blocksize)
        if not current:
            # end-of-file without delimiter
            return False
        full = last + current if last else current
        try:
            if delimiter in full:
                i = full.index(delimiter)
                file.seek(file.tell() - (len(full) - i) + len(delimiter))
                return True
            elif len(current) < blocksize:
                # end-of-file without delimiter
                return False
        except (OSError, ValueError):
            pass
        last = full[-len(delimiter) :]


def read_block(
    f: IO[bytes],
    offset: int,
    length: int | None,
    delimiter: bytes | None = None,
    split_before: bool = False,
) -> bytes:
    """Read a block of bytes from a file

    Parameters
    ----------
    f: File
        Open file
    offset: int
        Byte offset to start read
    length: int
        Number of bytes to read, read through end of file if None
    delimiter: bytes (optional)
        Ensure reading starts and stops at delimiter bytestring
    split_before: bool (optional)
        Start/stop read *before* delimiter bytestring.


    If using the ``delimiter=`` keyword argument we ensure that the read
    starts and stops at delimiter boundaries that follow the locations
    ``offset`` and ``offset + length``.  If ``offset`` is zero then we
    start at zero, regardless of delimiter.  The bytestring returned WILL
    include the terminating delimiter string.

    Examples
    --------

    >>> from io import BytesIO  # doctest: +SKIP
    >>> f = BytesIO(b'Alice, 100\\nBob, 200\\nCharlie, 300')  # doctest: +SKIP
    >>> read_block(f, 0, 13)  # doctest: +SKIP
    b'Alice, 100\\nBo'

    >>> read_block(f, 0, 13, delimiter=b'\\n')  # doctest: +SKIP
    b'Alice, 100\\nBob, 200\\n'

    >>> read_block(f, 10, 10, delimiter=b'\\n')  # doctest: +SKIP
    b'Bob, 200\\nCharlie, 300'
    """
    if delimiter:
        f.seek(offset)
        found_start_delim = seek_delimiter(f, delimiter, 2**16)
        if length is None:
            return f.read()
        start = f.tell()
        length -= start - offset

        f.seek(start + length)
        found_end_delim = seek_delimiter(f, delimiter, 2**16)
        end = f.tell()

        # Adjust split location to before delimiter if seek found the
        # delimiter sequence, not start or end of file.
        if found_start_delim and split_before:
            start -= len(delimiter)

        if found_end_delim and split_before:
            end -= len(delimiter)

        offset = start
        length = end - start

    f.seek(offset)

    # TODO: allow length to be None and read to the end of the file?
    assert length is not None
    b = f.read(length)
    return b


def tokenize(*args: Any, **kwargs: Any) -> str:
    """Deterministic token

    (modified from dask.base)

    >>> tokenize([1, 2, '3'])
    '9d71491b50023b06fc76928e6eddb952'

    >>> tokenize('Hello') == tokenize('Hello')
    True
    """
    if kwargs:
        args += (kwargs,)
    try:
        h = md5(str(args).encode())
    except ValueError:
        # FIPS systems: https://github.com/fsspec/filesystem_spec/issues/380
        h = md5(str(args).encode(), usedforsecurity=False)
    return h.hexdigest()


def stringify_path(filepath: str | os.PathLike[str] | pathlib.Path) -> str:
    """Attempt to convert a path-like object to a string.

    Parameters
    ----------
    filepath: object to be converted

    Returns
    -------
    filepath_str: maybe a string version of the object

    Notes
    -----
    Objects supporting the fspath protocol are coerced according to its
    __fspath__ method.

    For backwards compatibility with older Python version, pathlib.Path
    objects are specially coerced.

    Any other object is passed through unchanged, which includes bytes,
    strings, buffers, or anything else that's not even path-like.
    """
    if isinstance(filepath, str):
        return filepath
    elif hasattr(filepath, "__fspath__"):
        return filepath.__fspath__()
    elif hasattr(filepath, "path"):
        return filepath.path
    else:
        return filepath  # type: ignore[return-value]


def make_instance(
    cls: Callable[..., T], args: Sequence[Any], kwargs: dict[str, Any]
) -> T:
    inst = cls(*args, **kwargs)
    inst._determine_worker()  # type: ignore[attr-defined]
    return inst


def common_prefix(paths: Iterable[str]) -> str:
    """For a list of paths, find the shortest prefix common to all"""
    parts = [p.split("/") for p in paths]
    lmax = min(len(p) for p in parts)
    end = 0
    for i in range(lmax):
        end = all(p[i] == parts[0][i] for p in parts)
        if not end:
            break
    i += end
    return "/".join(parts[0][:i])


def other_paths(
    paths: list[str],
    path2: str | list[str],
    exists: bool = False,
    flatten: bool = False,
) -> list[str]:
    """In bulk file operations, construct a new file tree from a list of files

    Parameters
    ----------
    paths: list of str
        The input file tree
    path2: str or list of str
        Root to construct the new list in. If this is already a list of str, we just
        assert it has the right number of elements.
    exists: bool (optional)
        For a str destination, it is already exists (and is a dir), files should
        end up inside.
    flatten: bool (optional)
        Whether to flatten the input directory tree structure so that the output files
        are in the same directory.

    Returns
    -------
    list of str
    """

    if isinstance(path2, str):
        path2 = path2.rstrip("/")

        if flatten:
            path2 = ["/".join((path2, p.split("/")[-1])) for p in paths]
        else:
            cp = common_prefix(paths)
            if exists:
                cp = cp.rsplit("/", 1)[0]
            if not cp and all(not s.startswith("/") for s in paths):
                path2 = ["/".join([path2, p]) for p in paths]
            else:
                path2 = [p.replace(cp, path2, 1) for p in paths]
    else:
        assert len(paths) == len(path2)
    return path2


def check_contained(root: str, paths: list[str]) -> None:
    """Raise if any of ``paths`` lies outside the destination ``root``.

    Bulk copies build their destination names by joining source names onto a
    destination root. Those names come from the source listing, so a name
    holding ".." segments resolves above the root and writes outside the
    destination the caller asked for.

    Parameters
    ----------
    root: str
        The destination the caller passed.
    paths: list of str
        The destination names built for that root.
    """
    root_abs = os.path.abspath(root)
    # normcase so that a case-insensitive platform does not report a false
    # escape, while the message keeps the paths as the caller would see them.
    root_key = os.path.normcase(root_abs)
    prefix = root_key.rstrip(os.sep) + os.sep
    for path in paths:
        path_abs = os.path.abspath(path)
        path_key = os.path.normcase(path_abs)
        if path_key != root_key and not path_key.startswith(prefix):
            raise ValueError(
                f"path {path!r} would be copied to {path_abs!r}, which is "
                f"outside the destination {root!r}"
            )


def is_exception(obj: Any) -> bool:
    return isinstance(obj, BaseException)


def isfilelike(f: Any) -> TypeGuard[IO[bytes]]:
    return all(hasattr(f, attr) for attr in ["read", "close", "tell"])


def get_protocol(url: str) -> str:
    url = stringify_path(url)
    parts = re.split(r"(\:\:|\://)", url, maxsplit=1)
    if len(parts) > 1:
        return parts[0]
    return "file"


def get_file_extension(url: str) -> str:
    url = stringify_path(url)
    # Only consider the final path component: a "." in a parent directory name
    # (e.g. "/path/to.dir/file") is not the file's extension.
    ext_parts = url.rsplit("/", 1)[-1].rsplit(".", 1)
    if len(ext_parts) > 1:
        return ext_parts[-1]
    return ""


def can_be_local(path: str) -> bool:
    """Can the given URL be used with open_local?"""
    from fsspec import get_filesystem_class

    try:
        return getattr(get_filesystem_class(get_protocol(path)), "local_file", False)
    except (ValueError, ImportError):
        # not in registry or import failed
        return False


def get_package_version_without_import(name: str) -> str | None:
    """For given package name, try to find the version without importing it

    Import and package.__version__ is still the backup here, so an import
    *might* happen.

    Returns either the version string, or None if the package
    or the version was not readily  found.
    """
    if name in sys.modules:
        mod = sys.modules[name]
        if hasattr(mod, "__version__"):
            return mod.__version__
    try:
        return version(name)
    except:  # noqa: E722
        pass
    try:
        import importlib

        mod = importlib.import_module(name)
        return mod.__version__
    except (ImportError, AttributeError):
        return None


def setup_logging(
    logger: logging.Logger | None = None,
    logger_name: str | None = None,
    level: str = "DEBUG",
    clear: bool = True,
) -> logging.Logger:
    if logger is None and logger_name is None:
        raise ValueError("Provide either logger object or logger name")
