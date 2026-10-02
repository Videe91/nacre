"""
Functionality: Extract every piece of text from one binary attachment, in memory and within fixed limits, end to end.
Owns: format sniffing by content, the archive walk (zip, tar, gzip, bzip2, xz; office files as zips), the PDF text
  layer and page rendering, image decoding, the extraction limits, and refusing what cannot be scanned.
Public entry: extract_binary_text(), TextPiece, ExtractionRefused, EXTRACTOR_VERSIONS, MAX_DEPTH, MAX_MEMBERS,
  MAX_TOTAL_BYTES, MAX_RATIO, RATIO_FLOOR_BYTES
Decisions: D-0027, D-0008, D-0006
Assumptions: A-0021, A-0041
Notes: D-0027 §1. Nothing is written to disk: member names are text to scan, never paths. The kind is decided by
  magic bytes, never by a name or a declared media type (D-0008 amendment 6).
  - Limits (owner-approved): nesting <= 3, <= 10,000 members, <= 64 MiB produced by decompression, produced bytes
    <= 100 x the attachment's size. Bytes are counted as decompressed, never read from (lying) headers.
  - D-0027 amendment 2: the 100x ratio applies only once more than RATIO_FLOOR_BYTES have been produced (a tiny
    tar.gz is legitimately > 100x: tar pads to 10 KiB). D1: "1 MB" is read as 1,000,000 bytes (the stricter reading).
    The absolute caps (64 MiB, 10,000 members, depth 3) always apply.
  - Runs inside the isolated scan child (D-0006 amendment 3, ledger/run_binary_scan_child.py), never in the
    process that holds database credentials.
  - TextPiece.extractor is a label; EXTRACTOR_VERSIONS maps it to the names and versions recorded on a clean
    attachment (D-0008 amendment 7).
  - D1 counting: every archive or compression layer is one nesting level, except that a tar directly inside
    gzip/bzip2/xz shares its compressor's level (a .tar.gz is one archive). Tar members are slices of bytes already
    counted, so only zip members and compressor output count toward the byte limits. PDF pages count as members.
  - D1 extra limits on the OCR path (not in D-0027, fail closed, flagged to the owner): an image may have at most
    MAX_IMAGE_PIXELS pixels and MAX_FRAMES frames, and one attachment may send at most MAX_OCR_IMAGES images to OCR.
  - Fail closed: an unknown binary (top level or member), an encrypted zip member or PDF, any parser error, or a
    limit -> ExtractionRefused. Reasons are fixed text plus locations; a library's message is never echoed. D1: valid
    text that only looks like BMP/bzip2 (2-3 ASCII magic bytes) and fails to parse is scanned as text instead.
  - D1 additive detection beyond D-0027's list (detection only): member names, tar link targets, PDF metadata,
    form-field values, annotation text and embedded files, image text metadata (PNG text chunks, EXIF strings),
    tag-stripped text of XML members (office runs split a word across tags), and the printable runs of every
    binary blob (catches appended trailers and uncompressed metadata). PDF pages that have a text layer but also
    an image XObject are rendered and OCR'd too, so a screenshot pasted into a report is read.
  - A location names a member only after the caller has scanned its name: a secret name reads "member #n name".
"""
import bz2
import gzip
import html
import io
import lzma
import platform
import re
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from importlib.metadata import version

import pypdf
import pypdfium2
from PIL import Image

from nacre.ledger.ocr_image_text import OCR_ENGINE_ID, OCR_VERSIONS, ocr_image_text

MAX_DEPTH, MAX_MEMBERS, MAX_TOTAL_BYTES, MAX_RATIO, RATIO_FLOOR_BYTES = 3, 10_000, 64 * 1024 * 1024, 100, 1_000_000
MAX_IMAGE_PIXELS, MAX_FRAMES, MAX_OCR_IMAGES, PDF_RENDER_SCALE, PDF_RENDER_MAX_SIDE = 25_000_000, 16, 64, 2.0, 4000
_CHUNK, _PY = 1024 * 1024, "python"
PYPDF_ID, PDFIUM_ID, PILLOW_ID = "pypdf", "pypdfium2", "Pillow"
EXTRACTOR_VERSIONS = {_PY: {"python": platform.python_version()}, PYPDF_ID: {"pypdf": version("pypdf")},
                      PDFIUM_ID: {"pypdfium2": version("pypdfium2")}, PILLOW_ID: {"Pillow": version("Pillow")},
                      OCR_ENGINE_ID: OCR_VERSIONS}
_MAGIC = ((b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"), (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"),
          (b"\xfd7zXZ\x00", "xz"), (b"\x89PNG\r\n\x1a\n", "PNG"), (b"\xff\xd8\xff", "JPEG"), (b"GIF87a", "GIF"),
          (b"GIF89a", "GIF"), (b"II*\x00", "TIFF"), (b"MM\x00*", "TIFF"), (b"BM", "BMP"))
_WEAK_MAGIC = ("BMP", "bzip2")      # short ASCII magics: text that merely starts with "BM" or "BZh" stays text
_RAW_RUNS = re.compile(rb"[\x20-\x7e\t]{8,}")
_XML_BREAK = re.compile(r"</(?:w:p|a:p|si|row|c|text:p)>|<w:br/>|<w:tab/>")
_XML_TAG = re.compile(r"<[^<>]*>")
_PDFIUM_LOCK = threading.Lock()     # pdfium is not thread-safe


class ExtractionRefused(ValueError):
    """The attachment cannot be scanned (unknown format, encrypted, corrupt, or over a limit)."""


@dataclass(frozen=True, slots=True)
class TextPiece:
    location: str                                   # where it came from; never holds unscanned names
    text: str
    extractor: str                                  # a key of EXTRACTOR_VERSIONS
    regions: tuple[tuple[int, str], ...] = field(default=())   # (start offset in text, finer location)


@dataclass
class _Budget:
    cap: int
    members: int = 0
    produced: int = 0
    ocr_images: int = 0

    def member(self, n=1):
        self.members += n
        _refuse_if(self.members > MAX_MEMBERS, f"more than {MAX_MEMBERS} members (archive-bomb guard)")

    def produce(self, n):
        self.produced += n
        _refuse_if(self.produced > MAX_TOTAL_BYTES, f"more than {MAX_TOTAL_BYTES} bytes uncompressed (archive-bomb guard)")
        _refuse_if(self.produced > RATIO_FLOOR_BYTES and self.produced > self.cap,
                   f"expansion ratio above {MAX_RATIO}x (archive-bomb guard)")

    def ocr(self):
        self.ocr_images += 1
        _refuse_if(self.ocr_images > MAX_OCR_IMAGES, f"more than {MAX_OCR_IMAGES} images to OCR")


def _refuse_if(condition: bool, reason: str):
    if condition:
        raise ExtractionRefused(reason)


def extract_binary_text(data: bytes) -> Iterator[TextPiece]:
    """Yield every text piece in `data`; raise ExtractionRefused if any part cannot be scanned."""
    if type(data) is not bytes:
        raise TypeError("attachments are bytes")
    budget = _Budget(cap=MAX_RATIO * max(len(data), 1))
    yield from _walk(data, "attachment", 0, budget)


def _kind(data: bytes) -> str | None:
    if data[257:262] == b"ustar":
        return "tar"
    if data.startswith(b"%PDF-") or (b"%PDF-" in data[:1024] and _as_text(data) is None):
        return "pdf"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return next((kind for magic, kind in _MAGIC if data.startswith(magic)), None)


def _as_text(data: bytes) -> str | None:
    """Text = strictly valid UTF-8 (or UTF-16 with a byte-order mark); anything else is binary."""
    try:
        return data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
    except UnicodeDecodeError:
        return None


def _safe(name: str) -> str:
    """A scanned member name made safe to print: no control characters, at most 120 characters."""
    return "".join(c if c.isprintable() else "?" for c in name)[:120]


def _walk(data: bytes, loc: str, level: int, budget: _Budget) -> Iterator[TextPiece]:
    kind = _kind(data)
    if kind is None:
        text = _as_text(data)
        if text is None:
            raise ExtractionRefused(f"unknown binary format at {loc}")
        yield TextPiece(loc, text, _PY)
        if text.lstrip().startswith("<"):
            plain = html.unescape(_XML_TAG.sub("", _XML_BREAK.sub("\n", text)))
            yield TextPiece(f"{loc} (markup removed)", plain, _PY)
        return
    try:
        if kind in ("zip", "tar", "gzip", "bzip2", "xz"):
            _refuse_if(level + 1 > MAX_DEPTH, f"archives nested deeper than {MAX_DEPTH} at {loc} (archive-bomb guard)")
            walker = {"zip": _zip, "tar": _tar}.get(kind, _compressed)
            yield from walker(data, loc, level + 1, budget, kind)
        elif kind == "pdf":
            yield from _pdf(data, loc, level, budget)
        else:
            yield from _image(data, loc, budget, kind)
    except ExtractionRefused:
        raise
    except Exception as exc:     # any parser failure: fail closed, without echoing the library's message
        if kind in _WEAK_MAGIC and _as_text(data) is not None:
            yield TextPiece(loc, _as_text(data), _PY)
            return
        raise ExtractionRefused(f"corrupt or unsupported {kind} at {loc} ({type(exc).__name__})") from None
    # Last, so a finding inside the structure is reported with its finer location first.
    yield TextPiece(f"{loc} (raw bytes)", "\n".join(m.decode("ascii") for m in _RAW_RUNS.findall(data)), _PY)


def _members(names_and_data, loc, level, budget, extractor) -> Iterator[TextPiece]:
    """Scan each member's name first, then its content; a location names a member only once its name is clean."""
    for i, (name, read) in enumerate(names_and_data, 1):
        yield TextPiece(f"{loc} member #{i} name", name, extractor)
        member_loc = f"{loc} > {_safe(name)}"
        content = read(member_loc)
        if content is not None:
            yield from _walk(content, member_loc, level, budget)


def _read_counted(stream, budget: _Budget) -> bytes:
    out = bytearray()
    while chunk := stream.read(_CHUNK):
        budget.produce(len(chunk))
        out += chunk
    return bytes(out)


def _zip(data, loc, level, budget, kind) -> Iterator[TextPiece]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = zf.infolist()
    budget.member(len(infos))

    def read(info, member_loc):
        _refuse_if(info.flag_bits & 0x1, f"encrypted archive member at {member_loc}")
        if info.is_dir():
            return None
        with zf.open(info) as stream:
            return _read_counted(stream, budget)
    yield from _members(((i.filename, partial(read, i)) for i in infos), loc, level, budget, _PY)


def _tar(data, loc, level, budget, kind) -> Iterator[TextPiece]:
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:")     # no transparent decompression: we count it
    members = tf.getmembers()
    budget.member(len(members))

    def read(m, member_loc):
        if not m.isfile():
            return m.linkname.encode() if m.linkname else None      # a link target is text to scan
        stream = tf.extractfile(m)        # a sparse member expands holes: count it like decompression
        return _read_counted(stream, budget) if m.issparse() else stream.read()
    yield from _members(((m.name, partial(read, m)) for m in members), loc, level, budget, _PY)


def _compressed(data, loc, level, budget, kind) -> Iterator[TextPiece]:
    opener = {"gzip": lambda f: gzip.GzipFile(fileobj=f), "bzip2": bz2.BZ2File, "xz": lzma.LZMAFile}[kind]
    with opener(io.BytesIO(data)) as stream:     # each read decompresses at most _CHUNK bytes
        payload = _read_counted(stream, budget)
    inner = f"{loc} ({kind}-decompressed)"
    yield TextPiece(inner, "", _PY)
    if _kind(payload) == "tar":
        yield from _tar(payload, inner, level, budget, "tar")         # .tar.gz: one archive, one level
    else:
        yield from _walk(payload, inner, level, budget)


def _pdf(data, loc, level, budget) -> Iterator[TextPiece]:
    reader = pypdf.PdfReader(io.BytesIO(data))
    _refuse_if(reader.is_encrypted, f"encrypted PDF at {loc}")
    meta = reader.metadata or {}
    yield TextPiece(f"{loc} metadata", "\n".join(str(v) for v in meta.values()), PYPDF_ID)
    fields = reader.get_fields() or {}
    yield TextPiece(f"{loc} form fields", "\n".join(f"{k} {f.get('/V', '')}" for k, f in fields.items()), PYPDF_ID)
    budget.member(len(reader.pages))
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        annots = page.get("/Annots")
        notes = [str(a.get_object().get("/Contents", "")) for a in (annots.get_object() if annots else [])]
        yield TextPiece(f"{loc} page {number}", "\n".join([text, *notes]), PYPDF_ID)
        if not text.strip() or _has_image(page):
            budget.ocr()
            yield TextPiece(f"{loc} page {number} (rendered)", "", PDFIUM_ID)
            yield from _ocr(_render(data, number - 1), f"{loc} page {number} (rendered)")
    embedded = [(name, blob) for name, blobs in reader.attachments.items() for blob in blobs]
    if embedded:
        budget.member(len(embedded))
        _refuse_if(level + 1 > MAX_DEPTH, f"embedded files nested deeper than {MAX_DEPTH} at {loc} (archive-bomb guard)")
        budget.produce(sum(len(blob) for _, blob in embedded))
        yield from _members(((name, lambda _loc, b=blob: b) for name, blob in embedded),
                            f"{loc} embedded file", level + 1, budget, PYPDF_ID)


def _has_image(page, depth=0) -> bool:
    resources = page.get("/Resources")
    xobjects = resources.get_object().get("/XObject") if resources is not None else None
    if xobjects is None:
        return False
    for ref in xobjects.get_object().values():
        obj = ref.get_object()
        kind = obj.get("/Subtype")
        if kind == "/Image" or (kind == "/Form" and depth < 3 and _has_image(obj, depth + 1)):
            return True
    return False


def _render(data, index) -> Image.Image:
    with _PDFIUM_LOCK:
        doc = pypdfium2.PdfDocument(data)
        try:
            page = doc[index]
            scale = min(PDF_RENDER_SCALE, PDF_RENDER_MAX_SIDE / max(*page.get_size(), 1))
            return page.render(scale=scale).to_pil().convert("RGB")
        finally:
            doc.close()


def _image(data, loc, budget, kind) -> Iterator[TextPiece]:
    with Image.open(io.BytesIO(data), formats=[kind]) as img:     # only the sniffed decoder is ever tried
        frames = getattr(img, "n_frames", 1)
        _refuse_if(img.width * img.height > MAX_IMAGE_PIXELS, f"image larger than {MAX_IMAGE_PIXELS} pixels at {loc}")
        _refuse_if(frames > MAX_FRAMES, f"image with more than {MAX_FRAMES} frames at {loc}")
        meta = [v for v in img.info.values() if isinstance(v, str)]
        meta += [v.decode("utf-8", "replace") if isinstance(v, bytes) else v
                 for v in img.getexif().values() if isinstance(v, (str, bytes))]
        yield TextPiece(f"{loc} image metadata", "\n".join(meta), PILLOW_ID)
        for frame in range(frames):
            img.seek(frame)
            budget.ocr()
            yield from _ocr(img.copy(), loc if frames == 1 else f"{loc} frame {frame + 1}")


def _ocr(image: Image.Image, loc: str) -> Iterator[TextPiece]:
    """The OCR'd lines as one piece, then adjacent lines joined (a secret wrapped across two lines)."""
    try:
        lines = ocr_image_text(image)
    except Exception as exc:
        raise ExtractionRefused(f"OCR failed at {loc} ({type(exc).__name__})") from None
    single = [(line.text, line.region) for line in lines]
    pairs = [(a.text + b.text, a.region) for a, b in zip(lines, lines[1:])]
    for rows, label in ((single, ""), (pairs, " (adjacent lines joined)")):
        regions, offset = [], 0
        for text, (x0, y0, x1, y1) in rows:
            regions.append((offset, f"region x={x0}..{x1}, y={y0}..{y1}"))
            offset += len(text) + 1
        if rows or not label:
            yield TextPiece(f"{loc}{label}", "\n".join(t for t, _ in rows), OCR_ENGINE_ID, tuple(regions))
