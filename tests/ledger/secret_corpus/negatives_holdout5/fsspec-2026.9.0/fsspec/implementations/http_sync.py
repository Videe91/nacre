"""This file is largely copied from http.py"""

import io
import logging
import re
import urllib.error
import urllib.parse
from copy import copy
from json import dumps, loads
from urllib.parse import urlparse

try:
    import yarl
except (ImportError, ModuleNotFoundError, OSError):
    yarl = False

from fsspec.callbacks import _DEFAULT_CALLBACK
from fsspec.registry import register_implementation
from fsspec.spec import AbstractBufferedFile, AbstractFileSystem
from fsspec.utils import DEFAULT_BLOCK_SIZE, isfilelike, nullcontext, tokenize

from ..caching import AllBytes

# https://stackoverflow.com/a/15926317/3821154
ex = re.compile(r"""<(a|A)\s+(?:[^>]*?\s+)?(href|HREF)=["'](?P<url>[^"']+)""")
ex2 = re.compile(r"""(?P<url>http[s]?://[-a-zA-Z0-9@:%_+.~#?&/=]+)""")
logger = logging.getLogger("fsspec.http")


class JsHttpException(urllib.error.HTTPError): ...


class StreamIO(io.BytesIO):
    # fake class, so you can set attributes on it
    # will eventually actually stream
    ...


class ResponseProxy:
    """Looks like a requests response"""

    def __init__(self, req, stream=False):
        self.request = req
        self.stream = stream
        self._data = None
        self._headers = None

    @property
    def raw(self):
        if self._data is None:
            b = self.request.response.to_bytes()
            if self.stream:
                self._data = StreamIO(b)
            else:
                self._data = b
        return self._data

    def close(self):
        if hasattr(self, "_data"):
            del self._data

    @property
    def headers(self):
        if self._headers is None:
            self._headers = dict(
                [
                    _.split(": ")
                    for _ in self.request.getAllResponseHeaders().strip().split("\r\n")
                ]
            )
        return self._headers

    @property
    def status_code(self):
        return int(self.request.status)

    def raise_for_status(self):
        if not self.ok:
            raise JsHttpException(
                self.url, self.status_code, self.reason, self.headers, None
            )

    def iter_content(self, chunksize, *_, **__):
        while True:
            out = self.raw.read(chunksize)
            if out:
                yield out
            else:
                break

    @property
    def reason(self):
        return self.request.statusText

    @property
    def ok(self):
        return self.status_code < 400

    @property
    def url(self):
        return self.request.response.responseURL

    @property
    def text(self):
        # TODO: encoding from headers
        return self.content.decode()

    @property
    def content(self):
        self.stream = False
        return self.raw

    def json(self):
        return loads(self.text)


class RequestsSessionShim:
    def __init__(self):
        self.headers = {}

    def request(
        self,
        method,
        url,
        params=None,
        data=None,
        headers=None,
        cookies=None,
        files=None,
        auth=None,
        timeout=None,
        allow_redirects=None,
        proxies=None,
        hooks=None,
        stream=None,
        verify=None,
        cert=None,
        json=None,
    ):
        from js import Blob, XMLHttpRequest

        logger.debug("JS request: %s %s", method, url)

        if cert or verify or proxies or files or cookies or hooks:
            raise NotImplementedError
        if data and json:
            raise ValueError("Use json= or data=, not both")
        req = XMLHttpRequest.new()
        extra = auth if auth else ()
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req.open(method, url, False, *extra)
        if timeout:
            req.timeout = timeout
        if headers:
            for k, v in headers.items():
                req.setRequestHeader(k, v)

        req.setRequestHeader("Accept", "application/octet-stream")
        req.responseType = "arraybuffer"
        if json:
            blob = Blob.new([dumps(data)], {type: "application/json"})
            req.send(blob)
        elif data:
            if isinstance(data, io.IOBase):
                data = data.read()
            blob = Blob.new([data], {type: "application/octet-stream"})
            req.send(blob)
        else:
            req.send(None)
        return ResponseProxy(req, stream=stream)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST}", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


class HTTPFileSystem(AbstractFileSystem):
    """
    Simple File-System for fetching data via HTTP(S)

    This is the BLOCKING version of the normal HTTPFileSystem. It uses
    requests in normal python and the JS runtime in pyodide.

    ***This implementation is extremely experimental, do not use unless
    you are testing pyodide/pyscript integration***
    """

    protocol = ("http", "https", "sync-http", "sync-https")
    sep = "/"

    def __init__(
        self,
        simple_links=True,
        block_size=None,
        same_scheme=True,
        cache_type="readahead",
        cache_options=None,
        client_kwargs=None,
        encoded=False,
        **storage_options,
    ):
        """

        Parameters
        ----------
        block_size: int
            Blocks to read bytes; if 0, will default to raw requests file-like
            objects instead of HTTPFile instances
        simple_links: bool
            If True, will consider both HTML <a> tags and anything that looks
            like a URL; if False, will consider only the former.
        same_scheme: True
            When doing ls/glob, if this is True, only consider paths that have
            http/https matching the input URLs.
        size_policy: this argument is deprecated
        client_kwargs: dict
            Passed to aiohttp.ClientSession, see
            https://docs.aiohttp.org/en/stable/client_reference.html
            For example, ``{'auth': aiohttp.BasicAuth('user', 'pass')}``
        storage_options: key-value
            Any other parameters passed on to requests
        cache_type, cache_options: defaults used in open
        """
        super().__init__(self, **storage_options)
        self.block_size = block_size if block_size is not None else DEFAULT_BLOCK_SIZE
        self.simple_links = simple_links
        self.same_schema = same_scheme
        self.cache_type = cache_type
        self.cache_options = cache_options
        self.client_kwargs = client_kwargs or {}
        self.encoded = encoded
        self.kwargs = storage_options

        try:
            import js  # noqa: F401

            logger.debug("Starting JS session")
            self.session = RequestsSessionShim()
            self.js = True
        except Exception as e:
            import requests

            logger.debug("Starting cpython session because of: %s", e)
            self.session = requests.Session(**(client_kwargs or {}))
            self.js = False

        request_options = copy(storage_options)
        self.use_listings_cache = request_options.pop("use_listings_cache", False)
        request_options.pop("listings_expiry_time", None)
        request_options.pop("max_paths", None)
        request_options.pop("skip_instance_cache", None)
        self.kwargs = request_options

    @property
    def fsid(self):
        return "sync-http"

    def encode_url(self, url):
        if yarl:
            return yarl.URL(url, encoded=self.encoded)
        return url

    @classmethod
    def _strip_protocol(cls, path: str) -> str:
        """For HTTP, we always want to keep the full URL"""
        path = path.replace("sync-http://", "http://").replace(
            "sync-https://", "https://"
        )
        return path

    @classmethod
    def _parent(cls, path):
        # override, since _strip_protocol is different for URLs
        par = super()._parent(path)
        if len(par) > 7:  # "http://..."
            return par
        return ""

    def _ls_real(self, url, detail=True, **kwargs):
        # ignoring URL-encoded arguments
        kw = self.kwargs.copy()
        kw.update(kwargs)
        logger.debug(url)
        r = self.session.get(self.encode_url(url), **self.kwargs)
        self._raise_not_found_for_status(r, url)
        text = r.text
        if self.simple_links:
            links = ex2.findall(text) + [u[2] for u in ex.findall(text)]
        else:
            links = [u[2] for u in ex.findall(text)]
        out = set()
        parts = urlparse(url)
        for l in links:
            if isinstance(l, tuple):
                l = l[1]
            if l.startswith("/") and len(l) > 1:
                # absolute URL on this server
                l = parts.scheme + "://" + parts.netloc + l
            if l.startswith("http"):
                if self.same_schema and l.startswith(url.rstrip("/") + "/"):
                    out.add(l)
                elif l.replace("https", "http").startswith(
                    url.replace("https", "http").rstrip("/") + "/"
                ):
                    # allowed to cross http <-> https
                    out.add(l)
            else:
                if l not in ["..", "../"]:
                    # Ignore FTP-like "parent"
                    out.add("/".join([url.rstrip("/"), l.lstrip("/")]))
        if not out and url.endswith("/"):
            out = self._ls_real(url.rstrip("/"), detail=False)
        if detail:
            return [
                {
                    "name": u,
                    "size": None,
                    "type": "directory" if u.endswith("/") else "file",
                }
                for u in out
            ]
        else:
            return sorted(out)

    def ls(self, url, detail=True, **kwargs):
        if self.use_listings_cache and url in self.dircache:
            out = self.dircache[url]
        else:
            out = self._ls_real(url, detail=detail, **kwargs)
            self.dircache[url] = out
        return out

    def _raise_not_found_for_status(self, response, url):
        """
        Raises FileNotFoundError for 404s, otherwise uses raise_for_status.
        """
        if response.status_code == 404:
            raise FileNotFoundError(url)
        response.raise_for_status()

    def cat_file(self, url, start=None, end=None, **kwargs):
        kw = self.kwargs.copy()
        kw.update(kwargs)
        logger.debug(url)

        if start is not None or end is not None:
            if start == end:
                return b""
            headers = kw.pop("headers", {}).copy()

            headers["Range"] = self._process_limits(url, start, end)
            kw["headers"] = headers
        r = self.session.get(self.encode_url(url), **kw)
        self._raise_not_found_for_status(r, url)
        return r.content

    def get_file(
        self, rpath, lpath, chunk_size=5 * 2**20, callback=_DEFAULT_CALLBACK, **kwargs
    ):
        kw = self.kwargs.copy()
        kw.update(kwargs)
        logger.debug(rpath)
        r = self.session.get(self.encode_url(rpath), **kw)
        try:
            size = int(
                r.headers.get("content-length", None)
                or r.headers.get("Content-Length", None)
            )
        except (ValueError, KeyError, TypeError):
            size = None

        callback.set_size(size)
        self._raise_not_found_for_status(r, rpath)
        if not isfilelike(lpath):
            lpath = open(lpath, "wb")
        for chunk in r.iter_content(chunk_size, decode_unicode=False):
            lpath.write(chunk)
            callback.relative_update(len(chunk))

    def put_file(
        self,
        lpath,
        rpath,
        chunk_size=5 * 2**20,
        callback=_DEFAULT_CALLBACK,
        method="post",
        **kwargs,
    ):
        def gen_chunks():
            # Support passing arbitrary file-like objects
            # and use them instead of streams.
            if isinstance(lpath, io.IOBase):
                context = nullcontext(lpath)
                use_seek = False  # might not support seeking
            else:
                context = open(lpath, "rb")
                use_seek = True

            with context as f:
                if use_seek:
                    callback.set_size(f.seek(0, 2))
                    f.seek(0)
                else:
                    callback.set_size(getattr(f, "size", None))

                chunk = f.read(chunk_size)
                while chunk:
                    yield chunk
                    callback.relative_update(len(chunk))
                    chunk = f.read(chunk_size)

        kw = self.kwargs.copy()
        kw.update(kwargs)

        method = method.lower()
        if method not in ("post", "put"):
            raise ValueError(
                f"method has to be either 'post' or 'put', not: {method!r}"
            )

        meth = getattr(self.session, method)
        resp = meth(rpath, data=gen_chunks(), **kw)
        self._raise_not_found_for_status(resp, rpath)

    def _process_limits(self, url, start, end):
        """Helper for "Range"-based _cat_file"""
        size = None
        suff = False
        if start is not None and start < 0:
            # if start is negative and end None, end is the "suffix length"
            if end is None:
                end = -start
                start = ""
                suff = True
            else:
                size = size or self.info(url)["size"]
                start = size + start
        elif start is None:
            start = 0
        if not suff:
            if end is not None and end < 0:
                if start is not None:
                    size = size or self.info(url)["size"]
                    end = size + end
            elif end is None:
                end = ""
            if isinstance(end, int):
                end -= 1  # bytes range is inclusive
        return f"bytes={start}-{end}"

    def exists(self, path, strict=False, **kwargs):
        kw = self.kwargs.copy()
        kw.update(kwargs)
        try:
            logger.debug(path)
            r = self.session.get(self.encode_url(path), **kw)
            if strict:
                self._raise_not_found_for_status(r, path)
            return r.status_code < 400
        except FileNotFoundError:
            return False
        except Exception:
            if strict:
                raise
            return False

    def isfile(self, path, **kwargs):
        return self.exists(path, **kwargs)

    def _open(
        self,
        path,
        mode="rb",
        block_size=None,
        autocommit=None,  # XXX: This differs from the base class.
        cache_type=None,
        cache_options=None,
        size=None,
        **kwargs,
    ):
        """Make a file-like object

        Parameters
        ----------
        path: str
            Full URL with protocol
        mode: string
            must be "rb"
        block_size: int or None
            Bytes to download in one request; use instance value if None. If
            zero, will return a streaming Requests file-like instance.
        kwargs: key-value
            Any other parameters, passed to requests calls
        """
        if mode != "rb":
            raise NotImplementedError
        block_size = block_size if block_size is not None else self.block_size
        kw = self.kwargs.copy()
        kw.update(kwargs)
        size = size or self.info(path, **kwargs)["size"]
        if block_size and size:
            return HTTPFile(
                self,
                path,
                session=self.session,
                block_size=block_size,
                mode=mode,
                size=size,
                cache_type=cache_type or self.cache_type,
                cache_options=cache_options or self.cache_options,
                **kw,
            )
        else:
            return HTTPStreamFile(
                self,
                path,
