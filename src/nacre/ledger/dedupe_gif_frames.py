"""
Functionality: Find the unique frames of one animated GIF (identical or near-identical frames collapsed), end to end.
Owns: normalising each composited frame, the exact-duplicate check, the near-duplicate measure, which frame stands
  for a group, and stopping as soon as the unique count exceeds the caller's cap.
Public entry: unique_gif_frames(), NEAR_DUPLICATE_TOLERANCE
Decisions: D-0027
Assumptions: A-0041
Notes: D-0027 amendment 3 (owner): de-duplicate identical or near-identical GIF frames first, THEN apply the 16-frame
  limit to the unique frames; never sample. Called by ledger/extract_binary_text.py inside the isolated scan child.
  - D1 normalisation: each frame is taken as Pillow composites it after seek() (disposal and transparency applied,
    so a delta-encoded frame is compared as it is displayed), converted to RGBA. Frames of another size are never
    duplicates.
  - D1 measure, deterministic and conservative (a dropped frame must never hide text another frame lacks):
      exact duplicate: the sha256 of the RGBA bytes (and size) equals a kept frame's;
      near duplicate: every channel of every pixel differs from one kept frame by at most NEAR_DUPLICATE_TOLERANCE
        (2 of 255). This absorbs palette/quantisation rounding between re-encoded frames, and nothing else: one
        glyph, one cursor, even one changed pixel with a visible contrast makes the frame unique.
    Each frame is compared with EVERY kept frame, not just the previous one, so A,B,A,B (a blinking cursor) is two
    unique frames. No blur, downscale, perceptual hash or changed-pixel-count threshold is used: each of those can
    merge a frame that adds one character of a secret (e.g. a terminal recording typing it), which then is never read.
  - The kept frame of a group is its FIRST occurrence; with the measure above every member of a group shows the same
    text to within 2/255 per channel.
  - Consequence (flagged to the owner): dithered or lossy-recompressed recordings, whose frames differ by more than
    the tolerance in many pixels, are not merged, and fail closed (unscannable) above 16 unique frames.
  - Cost: every frame is decoded once (no new limit: the scan child's wall-clock timeout bounds a GIF with a huge
    frame count, which then is unscannable(timeout)); at most cap + 1 frames are held, each width * height * 4 bytes.
"""
import hashlib

import numpy as np
from PIL import Image

NEAR_DUPLICATE_TOLERANCE = 2


def unique_gif_frames(img: Image.Image, cap: int) -> list[int]:
    """The 0-based indices of the unique frames of `img`, in order; stops at cap + 1 (the caller refuses then)."""
    if not isinstance(img, Image.Image):
        raise TypeError("unique_gif_frames takes a PIL image")
    seen: set[bytes] = set()
    kept: list[np.ndarray] = []
    indices: list[int] = []
    for index in range(getattr(img, "n_frames", 1)):
        img.seek(index)
        frame = np.asarray(img.convert("RGBA"))
        digest = hashlib.sha256(repr(frame.shape).encode() + frame.tobytes()).digest()
        if digest in seen or any(_near(frame, other) for other in kept):
            continue
        seen.add(digest)
        kept.append(frame)
        indices.append(index)
        if len(indices) > cap:
            break
    img.seek(0)
    return indices


def _near(a: np.ndarray, b: np.ndarray) -> bool:
    if a.shape != b.shape:
        return False
    return int((np.maximum(a, b) - np.minimum(a, b)).max()) <= NEAR_DUPLICATE_TOLERANCE
