"""
Functionality: Read the text lines of one image with the pinned local OCR engine (RapidOCR on onnxruntime), end to end.
Owns: the model pins (file sha256s), verifying the model files before loading, offline loading (explicit model
  paths, so the engine never downloads), tiling tall images into overlapping bands, and returning each line with
  its pixel region.
Public entry: ocr_image_text(), OcrLine, OcrPinError, OCR_ENGINE_ID, OCR_VERSIONS
Decisions: D-0027, D-0024
Assumptions: A-0041
Notes: D-0027 owner decision 3: RapidOCR on onnxruntime (the D-0024 runtime). onnxruntime is imported by rapidocr,
  never by this file (D-0024's import rule is about Nacre's own imports).
  - Models: the three ONNX files shipped INSIDE the pinned rapidocr wheel (PP-OCRv6 det small, PP-OCRv6 rec small,
    ch_ppocr_mobile_v2.0 cls). Their sha256s below match rapidocr's own default_models.yaml for v3.9.2. They are
    checked before loading; a mismatch refuses to start. Every model path is given explicitly, so rapidocr's
    download branch (taken only when model_path is None) is unreachable; the rec model carries its own character
    table, so the dictionary download branch is unreachable too. Tests run it with the network blocked.
  - The engine is created once per process and calls are serialised by a lock: rapidocr keeps per-call
    parameters on the instance, so concurrent calls would race.
  - D1 tiling: images taller than BAND_PX are cut into horizontal bands with BAND_OVERLAP_PX overlap (a text line
    on a cut appears whole in one band). Width is never cut, so a long line is never split; widths above
    MAX_SIDE_PX are downscaled by the engine (recorded limit). Duplicate lines from overlaps are harmless: the
    caller only detects.
  - OCR_VERSIONS (D-0008 amendment 7): the engine, runtime and pinned model files, recorded on a clean attachment.
  - D1 engine settings: rapidocr defaults (text_score 0.5), except max_side_len = MAX_SIDE_PX and log level error.
    Not tuned on I1 (sealed; this session never read it).
"""
import hashlib
import threading
from dataclasses import dataclass
from functools import cache
from importlib.metadata import version
from pathlib import Path

import rapidocr
from PIL import Image

_MODEL_DIR = Path(rapidocr.__file__).resolve().parent / "models"
_PINNED = {   # role: (file name, sha256)
    "Det": ("PP-OCRv6_det_small.onnx", "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    "Rec": ("PP-OCRv6_rec_small.onnx", "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    "Cls": ("ch_ppocr_mobile_v2.0_cls_mobile.onnx", "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
}
OCR_ENGINE_ID = (f"rapidocr=={version('rapidocr')} (PP-OCRv6 det/rec small, cls mobile v2.0) "
                 f"onnxruntime=={version('onnxruntime')}")
OCR_VERSIONS = {"rapidocr": version("rapidocr"), "onnxruntime": version("onnxruntime"),
                **{f"rapidocr-model-{role.lower()}": f"{name} sha256:{digest}" for role, (name, digest) in _PINNED.items()}}
BAND_PX, BAND_OVERLAP_PX, MAX_SIDE_PX = 1600, 120, 4096
_LOCK = threading.Lock()


class OcrPinError(RuntimeError):
    """A model file is missing or does not match its pinned sha256."""


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    region: tuple[int, int, int, int]     # x0, y0, x1, y1 in the image's pixels


@cache
def _engine():
    params = {"Global.log_level": "error", "Global.max_side_len": MAX_SIDE_PX}
    for role, (name, want) in _PINNED.items():
        path = _MODEL_DIR / name
        if not path.is_file():
            raise OcrPinError(f"OCR model {name} is missing from {_MODEL_DIR}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != want:
            raise OcrPinError(f"OCR model {name} does not match its pinned sha256; refusing to load")
        params[f"{role}.model_path"] = str(path)
    return rapidocr.RapidOCR(params=params)


def ocr_image_text(image: Image.Image) -> list[OcrLine]:
    """Every text line the engine reads in `image`, top to bottom, with its region."""
    if not isinstance(image, Image.Image):
        raise TypeError("ocr_image_text takes a PIL image")
    # Transparency is kept: rapidocr flattens RGBA onto a background chosen for contrast. Dropping alpha instead
    # would turn dark text on a transparent background into dark-on-black.
    alpha = "A" in image.mode or "transparency" in image.info
    pic = image if image.mode in ("RGB", "RGBA") else image.convert("RGBA" if alpha else "RGB")
    width, height = pic.size
    engine, lines, top = _engine(), [], 0
    while True:
        band = pic if height <= BAND_PX else pic.crop((0, top, width, min(height, top + BAND_PX)))
        with _LOCK:
            out = engine(band)      # a PIL image: never a str, which rapidocr would treat as a path or URL
        for text, box in zip(out.txts or (), out.boxes if out.boxes is not None else ()):
            xs, ys = [int(p[0]) for p in box], [int(p[1]) + top for p in box]
            lines.append(OcrLine(text, (min(xs), min(ys), max(xs), max(ys))))
        if height <= BAND_PX or top + BAND_PX >= height:
            return lines
        top += BAND_PX - BAND_OVERLAP_PX
