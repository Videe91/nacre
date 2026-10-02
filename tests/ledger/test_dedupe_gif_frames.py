"""Tests for ledger/dedupe_gif_frames.py and its use in extract_binary_text (D-0027 amendment 3): identical and
near-identical GIF frames are collapsed before the 16-frame limit, which then counts unique frames; never sampled.
Secrets are built at runtime; images are synthetic, rendered here. End-to-end cases go through the real isolated scan
child (scan_binary_attachment)."""
import io
import random
import string
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from nacre.ledger.dedupe_gif_frames import NEAR_DUPLICATE_TOLERANCE, unique_gif_frames
from nacre.ledger.extract_binary_text import MAX_FRAMES
from nacre.ledger.scan_binary_attachment import Clean, SecretDetected, Unscannable, scan_binary_attachment

FONT = ImageFont.truetype(str(Path(__file__).parent / "secret_corpus" / "fonts" / "DejaVuSansMono.ttf"), 18)
rng = random.Random(327)


def _token():
    return "gh" + "p_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


def screen(lines, cursor=False, size=(720, 110)):
    """A terminal-like frame: light text on a dark background, an optional block cursor after the last line."""
    img = Image.new("RGB", size, (24, 24, 24))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((10, 10 + i * 30), line, fill=(230, 230, 230), font=FONT)
    if cursor:
        x = 10 + int(draw.textlength(lines[-1], font=FONT)) + 2
        y = 10 + (len(lines) - 1) * 30
        draw.rectangle((x, y, x + 9, y + 20), fill=(230, 230, 230))
    return img


def gif(frames):
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=80, loop=0)
    return out.getvalue()


def tiff(arrays):
    """Exact-pixel multi-frame image (TIFF keeps RGB values; GIF would quantise them) for the measure itself."""
    images = [Image.fromarray(a.astype(np.uint8), "RGB") for a in arrays]
    out = io.BytesIO()
    images[0].save(out, "TIFF", save_all=True, append_images=images[1:])
    return Image.open(io.BytesIO(out.getvalue()))


# ---- the measure --------------------------------------------------------------------------------------------
def test_identical_frames_collapse_to_their_first_occurrence_even_when_not_adjacent():
    a, b = np.zeros((20, 30, 3)), np.full((20, 30, 3), 200)
    assert unique_gif_frames(tiff([a, b, a, b, a, a, b]), cap=16) == [0, 1]


def test_differences_within_the_tolerance_are_near_duplicates():
    base = np.full((20, 30, 3), 100)
    noisy = base + np.random.default_rng(1).integers(-NEAR_DUPLICATE_TOLERANCE, NEAR_DUPLICATE_TOLERANCE + 1,
                                                       base.shape)
    assert unique_gif_frames(tiff([base, noisy, base + NEAR_DUPLICATE_TOLERANCE]), cap=16) == [0]


def test_one_pixel_beyond_the_tolerance_is_a_unique_frame():
    base = np.full((20, 30, 3), 100)
    one = base.copy()
    one[7, 9, 1] += NEAR_DUPLICATE_TOLERANCE + 1
    assert unique_gif_frames(tiff([base, one]), cap=16) == [0, 1]


def test_the_tolerance_is_the_recorded_value_and_three_levels_is_unique():
    assert NEAR_DUPLICATE_TOLERANCE == 2                                      # D1 in dedupe_gif_frames.py
    base = np.full((20, 30, 3), 100)
    assert unique_gif_frames(tiff([base, base + 2, base + 3]), cap=16) == [0, 2]


def test_a_near_duplicate_of_an_earlier_non_adjacent_frame_collapses():
    a, b = np.full((20, 30, 3), 100), np.full((20, 30, 3), 180)
    assert unique_gif_frames(tiff([a, b, a + 1, b - 1, a]), cap=16) == [0, 1]


def test_frames_of_another_size_are_never_duplicates():
    images = [Image.new("RGB", (20, 20), "black"), Image.new("RGB", (21, 20), "black")]
    out = io.BytesIO()
    images[0].save(out, "TIFF", save_all=True, append_images=images[1:])
    assert unique_gif_frames(Image.open(io.BytesIO(out.getvalue())), cap=16) == [0, 1]


def test_one_more_typed_character_is_a_unique_frame():
    typed = [screen(["$ export X=abc"[:n]]) for n in range(10, 15)]      # each prefix adds one visible glyph
    assert unique_gif_frames(Image.open(io.BytesIO(gif(typed))), cap=16) == list(range(5))


def test_counting_stops_one_past_the_cap():
    arrays = [np.full((8, 8, 3), 10 * i) for i in range(25)]
    assert unique_gif_frames(tiff(arrays), cap=16) == list(range(17))


def test_delta_encoded_gif_frames_are_compared_as_displayed():
    # Pillow writes later GIF frames as the changed rectangle only; the composite must still match frame 0.
    a, b = screen(["$ ls"]), screen(["$ ls"], cursor=True)
    assert unique_gif_frames(Image.open(io.BytesIO(gif([a, b] * 20))), cap=16) == [0, 1]


def test_non_images_are_refused():
    with pytest.raises(TypeError):
        unique_gif_frames(b"GIF89a", cap=16)


# ---- end to end, through the isolated scan child ------------------------------------------------------------
def test_a_long_terminal_recording_with_repeated_frames_passes():
    command = "$ make test && make deploy"
    screens = [[command[:n]] for n in (2, 6, 10, 14, 18, 22, 26)] + [[command, "ok: 412 passed"]]
    frames = [screen(s, cursor=c) for s in screens for _ in range(12) for c in (True, False)]   # 192 frames
    assert len(frames) == 192 and MAX_FRAMES == 16
    data = gif(frames)
    with Image.open(io.BytesIO(data)) as img:
        assert img.n_frames == 192 and unique_gif_frames(img, MAX_FRAMES) == [i * 24 + c for i in range(8)
                                                                               for c in (0, 1)]
    assert isinstance(scan_binary_attachment(data), Clean)


def test_more_than_sixteen_genuinely_different_frames_are_still_unscannable():
    frames = [screen([f"$ step {i}: building module {chr(97 + i) * 3}"]) for i in range(MAX_FRAMES + 1)]
    verdict = scan_binary_attachment(gif(frames * 3))
    assert isinstance(verdict, Unscannable) and "more than 16 unique frames" in verdict.reason, verdict


def test_a_secret_in_one_unique_frame_among_many_repeats_is_found():
    token = _token()
    idle = [screen(["$ ./deploy.sh"], cursor=c) for c in (True, False)] * 40
    frames = idle[:71] + [screen(["$ ./deploy.sh", f"GITHUB_TOKEN={token}"])] * 3 + idle[71:]
    verdict = scan_binary_attachment(gif(frames))
    assert isinstance(verdict, SecretDetected), verdict
    assert "frame 72" in verdict.location and token[4:20] not in verdict.location
