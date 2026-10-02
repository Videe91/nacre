"""Tests for ledger/scan_binary_attachment.py, extract_binary_text.py, ocr_image_text.py and the append_event
integration (D-0027 tests 1-5). Secrets are built at runtime; images are synthetic, rendered here (never I1)."""
import bz2
import gzip
import io
import lzma
import random
import socket
import string
import tarfile
import uuid
import zipfile
from pathlib import Path

import psycopg
import pytest
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

import nacre.ledger.extract_binary_text as extract
import nacre.ledger.ocr_image_text as ocr
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, AttachmentRejected, append_event
from nacre.ledger.local_disk_blob_store import LocalDiskBlobStore
from nacre.ledger.read_attachment import read_attachment
from nacre.ledger.read_stream import read_stream
from nacre.ledger.scan_binary_attachment import (Clean, SecretDetected, Unscannable, rejection_message,
                                                 scan_binary_attachment)

FONT = Path(__file__).parent / "secret_corpus" / "fonts" / "DejaVuSansMono.ttf"
rng = random.Random(27)


def _token():
    return "gh" + "p_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


# ---- carriers, built at runtime -------------------------------------------------------------------------
def image(lines=(), fmt="PNG", size=(640, 120), px=18, **save):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((10, 10 + i * int(px * 1.6)), line, fill="black", font=ImageFont.truetype(str(FONT), px))
    out = io.BytesIO()
    img.save(out, fmt, **save)
    return out.getvalue()


def zipped(files: dict, method=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def tarred(files: dict, mode="w:gz"):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode=mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return out.getvalue()


def pdf_text(lines):
    """A one-page PDF whose text layer holds `lines` (Helvetica, no embedded font)."""
    content = "BT /F1 12 Tf 14 TL 72 720 Td " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
               "/Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"]
    out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []          # the usual binary marker line
    for n, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def office(kind, xml: str, media: bytes | None = None):
    part = {"docx": "word/document.xml", "xlsx": "xl/sharedStrings.xml", "pptx": "ppt/slides/slide1.xml"}[kind]
    files = {"[Content_Types].xml": '<?xml version="1.0"?><Types/>', part: f'<?xml version="1.0"?><root>{xml}</root>'}
    if media is not None:
        files["word/media/image1.png"] = media
    return zipped(files)


def docx_runs(*runs):
    return office("docx", "<w:p>" + "".join(f"<w:r><w:t>{r}</w:t></w:r>" for r in runs) + "</w:p>")


def assert_secret(verdict, token, *where):
    assert isinstance(verdict, SecretDetected), verdict
    message = rejection_message(verdict)
    assert message.startswith("attachment_rejected: secret_detected") and verdict.rule_id in message
    assert token not in message and token[4:20] not in message           # never the value, nor a part of it
    for part in where:
        assert part in verdict.location, verdict.location


def assert_unscannable(verdict, reason):
    assert isinstance(verdict, Unscannable), verdict
    assert reason in verdict.reason and rejection_message(verdict).startswith("attachment_rejected: unscannable(")


# ---- test 1: every carrier with a secret is rejected, naming rule and location ----------------------------
def test_zip_nested_three_deep():
    token = _token()
    data = zipped({"creds.env": f"GITHUB_TOKEN={token}\n"})
    for level in (2, 3):
        data = zipped({f"level{level}.zip": data})
    assert_secret(scan_binary_attachment(data, "application/zip"), token,
                  "level3.zip > level2.zip > creds.env", "line 1")


def test_tar_gz_and_other_compressors():
    token = _token()
    files = {"logs/ok.log": b"all good\n", "logs/deploy.log": f"step 2\nauth token={token}\n".encode()}
    assert_secret(scan_binary_attachment(tarred(files)), token, "deploy.log", "line 2")
    for mode in ("w:bz2", "w:xz", "w:"):
        assert isinstance(scan_binary_attachment(tarred(files, mode)), SecretDetected)
    plain = f"GITHUB_TOKEN={token}\n".encode()
    for packed in (bz2.compress(plain), lzma.compress(plain)):
        assert_secret(scan_binary_attachment(packed), token, "decompressed")


def test_pdf_text_layer():
    token = _token()
    assert_secret(scan_binary_attachment(pdf_text(["release notes", f"GITHUB_TOKEN={token}"])), token, "page 1")


def test_pdf_image_only_page_is_rendered_and_read():
    token = _token()
    page = image([f"GITHUB_TOKEN={token}"], fmt="PDF", size=(760, 80))
    assert_secret(scan_binary_attachment(page), token, "page 1 (rendered)", "region")


def test_png_screenshot_and_jpeg():
    token = _token()
    shot = image(["$ git push origin main", f"export GITHUB_TOKEN={token}"])
    assert_secret(scan_binary_attachment(shot, "image/png"), token, "region x=")
    assert isinstance(scan_binary_attachment(image([f"GITHUB_TOKEN={token}"], fmt="JPEG", quality=95)),
                      SecretDetected)


def test_docx_including_a_secret_split_across_runs_and_a_pasted_screenshot():
    token = _token()
    assert_secret(scan_binary_attachment(docx_runs(f"GITHUB_TOKEN={token}")), token, "word/document.xml")
    split = scan_binary_attachment(docx_runs(token[:18], token[18:]))     # Word split the token into two runs
    assert_secret(split, token, "word/document.xml (markup removed)")
    pasted = office("docx", "<w:p><w:r><w:t>see screenshot</w:t></w:r></w:p>", media=image([f"TOKEN={token}"]))
    assert_secret(scan_binary_attachment(pasted), token, "word/media/image1.png", "region")


def test_secrets_outside_the_pixels_are_found_too():
    token = _token()
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", f"GITHUB_TOKEN={token}")
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(out, "PNG", pnginfo=meta)
    assert_secret(scan_binary_attachment(out.getvalue()), token, "image metadata")
    trailer = image() + f"\nGITHUB_TOKEN={token}\n".encode()                   # bytes appended after IEND
    assert_secret(scan_binary_attachment(trailer), token, "raw bytes")
    assert_secret(scan_binary_attachment(zipped({f"{token}.txt": b"hello"})), token, "member #1 name")


def test_pdf_extras_a_pasted_screenshot_on_a_text_page_and_an_embedded_file():
    import pypdf
    token = _token()
    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(pdf_text(CLEAN_TEXT))))
    shot = pypdf.PdfReader(io.BytesIO(image([f"GITHUB_TOKEN={token}"], fmt="PDF", size=(760, 80))))
    writer.pages[0].merge_page(shot.pages[0])                            # a text page with a pasted screenshot
    out = io.BytesIO()
    writer.write(out)
    assert_secret(scan_binary_attachment(out.getvalue()), token, "page 1 (rendered)")
    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(pdf_text(CLEAN_TEXT))))
    writer.add_attachment("creds.env", f"GITHUB_TOKEN={token}\n".encode())
    out = io.BytesIO()
    writer.write(out)
    assert_secret(scan_binary_attachment(out.getvalue()), token, "embedded file > creds.env")


def test_tar_link_targets_are_scanned():
    token = _token()
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tf:
        link = tarfile.TarInfo("current")
        link.type, link.linkname = tarfile.SYMTYPE, f"GITHUB_TOKEN={token}"
        tf.addfile(link)
        tf.addfile(tarfile.TarInfo("pad"), io.BytesIO(b""))
    assert_secret(scan_binary_attachment(out.getvalue()), token, "current")


def test_text_that_merely_starts_like_bmp_or_bzip2_is_scanned_as_text():
    token = _token()
    assert isinstance(scan_binary_attachment(zipped({"cars.txt": b"BMW sales report", "b.txt": b"BZh notes"})), Clean)
    assert_secret(scan_binary_attachment(zipped({"cars.txt": f"BMW GITHUB_TOKEN={token}".encode()})), token, "cars.txt")


def test_a_secret_wrapped_across_two_lines_is_caught():
    token = _token()
    assert isinstance(scan_binary_attachment(image(["GITHUB_TOKEN=" + token[:22], token[22:]])), SecretDetected)


# ---- test 2: clean files of every kind ------------------------------------------------------------------
CLEAN_TEXT = ["build 42 passed", "see the attached log"]
LOG = "".join(rng.choice(string.ascii_lowercase + "   \n") for _ in range(4000)).encode()   # compresses < 100x


def _clean_carriers():
    blank = {"PNG": image(fmt="PNG"), "JPEG": image(fmt="JPEG"), "GIF": image(fmt="GIF"), "WEBP": image(fmt="WEBP"),
             "TIFF": image(fmt="TIFF"), "BMP": image(fmt="BMP")}
    xml = "<w:p><w:r><w:t>quarterly plan</w:t></w:r></w:p>"
    return {**blank, "PNG text": image(CLEAN_TEXT), "zip": zipped({"a.txt": b"hello\n", "b/c.md": b"# notes\n"}),
            "tar.gz": tarred({"a.log": LOG}), "tar.bz2": tarred({"a.log": LOG}, "w:bz2"),
            "tar.xz": tarred({"a.log": LOG}, "w:xz"), "log.gz": gzip.compress(LOG), "pdf text": pdf_text(CLEAN_TEXT),
            "pdf image": image(CLEAN_TEXT, fmt="PDF"), "docx": office("docx", xml), "xlsx": office("xlsx", "<si><t>q3</t></si>"),
            "pptx": office("pptx", "<a:p><a:r><a:t>roadmap</a:t></a:r></a:p>")}


def test_clean_files_of_every_kind_are_clean_with_extractor_versions():
    for kind, data in _clean_carriers().items():
        verdict = scan_binary_attachment(data)
        assert isinstance(verdict, Clean), (kind, verdict)
    assert ocr.OCR_ENGINE_ID in scan_binary_attachment(image(CLEAN_TEXT)).extractors
    assert any(e.startswith("pypdf==") for e in scan_binary_attachment(pdf_text(CLEAN_TEXT)).extractors)


def test_a_tar_gz_inside_two_zips_is_within_the_depth_limit():
    assert isinstance(scan_binary_attachment(zipped({"b.zip": zipped({"c.tar.gz": tarred({"x": b"ok"})})})), Clean)


# ---- test 3: every bomb guard rejects ---------------------------------------------------------------------
def test_limits_are_the_owner_approved_values():
    assert (extract.MAX_DEPTH, extract.MAX_MEMBERS, extract.MAX_TOTAL_BYTES, extract.MAX_RATIO) == (
        3, 10_000, 64 * 1024 * 1024, 100)


def test_expansion_ratio_bomb():
    assert_unscannable(scan_binary_attachment(zipped({"zeros.bin": b"\0" * 4_000_000})), "expansion ratio")
    assert_unscannable(scan_binary_attachment(lzma.compress(b"a" * 4_000_000)), "expansion ratio")


def test_member_count_bomb():
    assert_unscannable(scan_binary_attachment(zipped({f"{i}": b"" for i in range(10_001)}, zipfile.ZIP_STORED)),
                       "more than 10000 members")


def test_depth_four_nesting():
    data = zipped({"x.txt": b"ok"})
    for level in range(3):
        data = zipped({f"l{level}.zip": data})
    assert_unscannable(scan_binary_attachment(data), "nested deeper than 3")
    assert_unscannable(scan_binary_attachment(bz2.compress(lzma.compress(bz2.compress(lzma.compress(b"ok"))))),
                       "nested deeper than 3")


def test_total_uncompressed_bytes(monkeypatch):
    monkeypatch.setattr(extract, "MAX_TOTAL_BYTES", 1 << 20)          # the real 64 MiB, scaled down for speed
    text = "".join(rng.choice(string.ascii_letters) for _ in range(1_200_000)).encode()
    assert_unscannable(scan_binary_attachment(zipped({"big.txt": text})), "bytes uncompressed")


def test_ocr_guards(monkeypatch):
    monkeypatch.setattr(extract, "MAX_IMAGE_PIXELS", 10_000)
    assert_unscannable(scan_binary_attachment(image(size=(200, 200))), "larger than")
    monkeypatch.setattr(extract, "MAX_IMAGE_PIXELS", 25_000_000)
    frames = [Image.new("RGB", (8, 8), (i, 0, 0)) for i in range(extract.MAX_FRAMES + 1)]
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
    assert_unscannable(scan_binary_attachment(out.getvalue()), "frames")
    monkeypatch.setattr(extract, "MAX_OCR_IMAGES", 1)
    assert_unscannable(scan_binary_attachment(zipped({"a.png": image(), "b.png": image(size=(64, 64))})), "to OCR")


# ---- test 4: encrypted and unknown files are unscannable ---------------------------------------------------
def test_encrypted_zip():
    data = bytearray(zipped({"secret.txt": b"hello"}, zipfile.ZIP_STORED))
    data[6] |= 1                                                         # local header: encrypted flag
    data[data.index(b"PK\x01\x02") + 8] |= 1                             # central directory: encrypted flag
    assert_unscannable(scan_binary_attachment(bytes(data)), "encrypted archive member")


def test_encrypted_pdf():
    import pypdf
    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(pdf_text(CLEAN_TEXT))))
    writer.encrypt("owner-only", algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    assert_unscannable(scan_binary_attachment(out.getvalue()), "encrypted PDF")


def test_unknown_and_corrupt_formats():
    assert_unscannable(scan_binary_attachment(rng.randbytes(300)), "unknown binary format")
    assert_unscannable(scan_binary_attachment(b"\x7fELF" + rng.randbytes(300)), "unknown binary format")
    assert_unscannable(scan_binary_attachment(zipped({"tool.bin": b"\x7fELF\xff\xfe"})), "unknown binary format at")
    assert_unscannable(scan_binary_attachment(image()[:60]), "corrupt or unsupported PNG")
    assert_unscannable(scan_binary_attachment(b"PK\x03\x04" + rng.randbytes(100)), "corrupt or unsupported zip")


# ---- the OCR engine: pinned, offline ------------------------------------------------------------------------
def test_ocr_loads_and_runs_with_the_network_blocked(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("OCR touched the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    ocr._engine.cache_clear()
    try:
        lines = ocr.ocr_image_text(Image.open(io.BytesIO(image(["hello world 123"]))))
    finally:
        ocr._engine.cache_clear()
    assert [line.text for line in lines] == ["hello world 123"]


def test_ocr_refuses_a_model_that_does_not_match_its_pin(monkeypatch):
    name, _ = ocr._PINNED["Rec"]
    monkeypatch.setitem(ocr._PINNED, "Rec", (name, "0" * 64))
    ocr._engine.cache_clear()
    try:
        with pytest.raises(ocr.OcrPinError, match="pinned sha256"):
            ocr.ocr_image_text(Image.new("RGB", (8, 8)))
        assert_unscannable(scan_binary_attachment(image()), "OCR failed")
    finally:
        ocr._engine.cache_clear()


def test_tall_images_are_read_in_bands():
    token = _token()
    tall = image([f"GITHUB_TOKEN={token}"], size=(700, 4000))
    img = Image.open(io.BytesIO(tall))
    canvas = Image.new("RGB", (700, 4000), "white")
    canvas.paste(img.crop((0, 0, 700, 60)), (0, 3500))                  # the secret only near the bottom
    out = io.BytesIO()
    canvas.save(out, "PNG")
    assert_secret(scan_binary_attachment(out.getvalue()), token, "y=35")


# ---- through append_event: rejected before storage; clean stored as binary-scanned (test 5: relabelling) --------
def _req(stream, data, media_type):
    return AppendRequest(stream_id=stream, event_type=EventType.RESULT, payload_type=PayloadType.IMAGE,
                         actor_kind=ActorKind.TOOL, actor_id=uuid.UUID(int=9), source=Source.TOOL,
                         idempotency_key=str(uuid.uuid4()), content=None, attachment=data,
                         attachment_media_type=media_type)


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"]], write=[streams["a"]])


@pytest.mark.parametrize("declared", ["image/png", "text/plain", "application/octet-stream"])
def test_a_png_with_a_secret_is_rejected_before_storage_whatever_its_label(rw, provider, streams, tmp_path, declared):
    token = _token()
    blobs = LocalDiskBlobStore(tmp_path / "blobs")
    with rw() as s, pytest.raises(AttachmentRejected) as caught:
        append_event(s, provider, _req(streams["a"], image([f"GITHUB_TOKEN={token}"]), declared), blob_store=blobs)
    assert "secret_detected" in str(caught.value) and "github-pat" in str(caught.value)
    assert token not in str(caught.value)
    assert not any(p.is_file() for p in (tmp_path / "blobs").rglob("*"))           # nothing was written
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        assert c.execute("SELECT count(*) FROM ledger.events").fetchone()[0] == 0


@pytest.mark.parametrize("bad, reason", [("encrypted", "encrypted"), ("unknown", "unknown binary format")])
def test_unscannable_attachments_are_rejected_with_no_way_to_store_them(rw, provider, streams, tmp_path, bad, reason):
    data = bytearray(zipped({"x.txt": b"hello"}, zipfile.ZIP_STORED))
    data[6] |= 1
    data[data.index(b"PK\x01\x02") + 8] |= 1
    payload = bytes(data) if bad == "encrypted" else b"\x00\x01\x02" + rng.randbytes(64)
    with rw() as s, pytest.raises(AttachmentRejected, match=r"attachment_rejected: unscannable\(.*" + reason):
        append_event(s, provider, _req(streams["a"], payload, "application/zip"),
                     blob_store=LocalDiskBlobStore(tmp_path / "blobs"))


@pytest.mark.parametrize("kind", ["PNG", "GIF", "zip", "tar.gz", "pdf text", "pdf image", "docx"])
def test_clean_binaries_are_stored_as_binary_scanned(rw, provider, streams, tmp_path, kind):
    data = _clean_carriers()[kind]
    blobs = LocalDiskBlobStore(tmp_path / "blobs")
    with rw() as s:
        env = append_event(s, provider, _req(streams["a"], data, "application/octet-stream"), blob_store=blobs).envelope
    with rw() as s:
        (e,) = read_stream(s, provider, streams["a"])
        assert read_attachment(s, provider, blobs, env) == data
    assert e.body["attachment"]["scan"] == "binary-scanned"
