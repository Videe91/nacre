"""
Sealed OCR evaluation set I1 (D-0027 §3, A-0042). Test code only; never imported by src/.

Sealed I1, generated 2026-10-01 by a separate session that did not read detector code (strip_secrets.py, its
rule data, the staged-secrets scanner, the detector tests or the measurement script). Never consult I1 samples
or results while writing or tuning OCR or rules.

What it measures: OCR recall on secrets rendered into screenshots. Owner target (D-0027): >= 95% of rendered
secrets detected at font size >= 12 px, measured per secret. 10 and 11 px are reported, not gated.

No image and no secret-shaped string is committed. Images are RENDERED at measurement time, deterministically
from I1_SEED, with the vendored DejaVu fonts (fonts/, Bitstream Vera licence) and the exact Pillow version
pinned in I1_MANIFEST.json, which holds the sha256 of every PNG (and of its raw pixels) so a measurement can
prove it rendered exactly the sealed set: scripts/check_i1_set.py. Pillow is NOT a Nacre dependency.

Values: the existing generators with expected == "secret" in the "provider" and "credential-slot"
categories (providers.py, credential_slot.py). The expected value is the generator's secret (secret_of when it
defines one, e.g. the secret part of a legacy Sentry DSN). Public-by-design credentials are not used.
Clean cases render the same scenes with benign high-entropy text only (commit hashes, UUIDs, sha256 digests),
never in a slot that names a credential; they are for false-reject reporting.

Scenes: terminal, ide (line numbers, syntax colours, soft wrap), browser (settings page with an API-key field,
or a JSON response viewer), log (log viewer), chat (message bubbles, proportional font, code blocks).
Every scene has a dark and a light theme. font_px is the logical (CSS) size; dpi_scale 2 renders at 2x pixels.
Mild realism only: FreeType anti-aliasing, and a JPEG round trip (quality 80-92) for about a quarter of cases.
Lines longer than the 1920-logical-px canvas (full-HD width) wrap (terminals hard-wrap, editors soft-wrap); a value split over
two or more lines is flagged `wrapped` per secret in the manifest so it can be reported separately.

Public entry: iter_cases() -> (case_id, png_bytes, expected_values, font_px, scene, has_secret);
cases() -> full Case records (incl. dpi_scale, theme, jpeg_quality, kinds, wrapped, raw-pixel sha256).
The manifest is written once and verified by scripts/check_i1_set.py (--seal / default check).
"""
import hashlib
import io
import itertools
import random
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from secret_corpus import credential_slot, providers  # noqa: E402,F401  (registers generators)
from secret_corpus.corpus import GENERATORS, HEX, rand  # noqa: E402

I1_SEED = 11_894_077_189_068_673_097          # os.urandom(8), drawn once on 2026-10-01
I1_DATE = "2026-10-01"
N_SECRET, N_CLEAN = 400, 100
FONT_PX = (10, 11, 12, 14, 16, 20)
GATED_MIN_PX = 12
DPI_SCALES = (1, 2)
SCENES = ("terminal", "ide", "browser", "log", "chat")
THEMES = ("dark", "light")
MAX_W = 1920                                     # logical px (a full-HD screen width)
HERE = Path(__file__).resolve().parent
FONT_DIR = HERE / "fonts"
FONTS = {"mono": "DejaVuSansMono.ttf", "sans": "DejaVuSans.ttf"}

PALETTE = {
    "dark": dict(bg=(30, 31, 36), fg=(214, 218, 224), dim=(110, 116, 128), bar=(44, 46, 54), box=(22, 23, 27),
                 border=(80, 84, 96), kw=(198, 120, 221), str=(152, 195, 121), num=(209, 154, 102),
                 key=(97, 175, 239), ok=(152, 195, 121), warn=(229, 192, 123), err=(224, 108, 117),
                 bubble=(44, 47, 56), code=(24, 25, 30)),
    "light": dict(bg=(252, 252, 250), fg=(36, 41, 47), dim=(140, 145, 152), bar=(232, 233, 236), box=(255, 255, 255),
                  border=(196, 200, 206), kw=(166, 38, 164), str=(80, 161, 79), num=(152, 104, 1),
                  key=(64, 120, 242), ok=(40, 140, 60), warn=(176, 120, 0), err=(202, 18, 67),
                  bubble=(238, 241, 245), code=(246, 248, 250)),
}


@dataclass
class Row:
    segs: list                     # [(text, colour key, secret index or None)]
    font: str = "mono"
    gutter: str | None = None      # line number
    wrap: str = "char"             # "char" (terminal hard wrap) | "word" (proportional text)
    box: str | None = None         # palette key of a filled, bordered box behind the row (field, bubble, code)
    gap: float = 0.0               # extra space above, in lines


@dataclass
class Case:
    case_id: str
    png: bytes
    expected_values: list
    font_px: int
    scene: str
    has_secret: bool
    dpi_scale: int
    theme: str
    jpeg_quality: int | None
    kinds: list = field(default_factory=list)      # generator names, one per expected value
    wrapped: list = field(default_factory=list)
    size: tuple = (0, 0)
    pixels_sha256: str = ""


def _rng(*parts) -> random.Random:
    key = "|".join(str(p) for p in (I1_SEED, *parts)).encode()
    return random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))


def value_generators() -> list[str]:
    return sorted(n for n, g in GENERATORS.items()
                  if g.category in ("provider", "credential-slot") and g.expected == "secret" and g.embed)


def _draw_value(name: str, rng: random.Random) -> tuple[str, str]:
    g = GENERATORS[name]
    value = g.make(rng)
    extract = getattr(g.make, "secret_of", None)
    return value, (extract(value) if extract else value)


# ---- benign high-entropy text ------------------------------------------------------------------------
def _uuid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _benign(rng):
    k = rng.randrange(4)
    return _uuid(rng) if k == 0 else ("sha256:" if k == 3 else "") + rand(rng, HEX, (7, 40, 64, 64)[k])


# ---- scenes: each returns (title, rows); `vals` are the rendered strings, index i is secret i -----------
ENV_NAMES = ["API_TOKEN", "SERVICE_KEY", "AUTH_TOKEN", "DEPLOY_TOKEN", "ACCESS_KEY", "CLIENT_SECRET"]


def _terminal(rng, vals):
    user, host = rng.choice(["dev", "ana", "ci", "root"]), rng.choice(["build-01", "mbp", "staging", "box"])
    proj = rng.choice(["api", "web", "infra", "worker"])

    def cmd(text):
        return Row([(f"{user}@{host}", "ok", None), (":", "fg", None), (f"~/{proj}", "key", None),
                    ("$ ", "fg", None), (text, "fg", None)])
    rows = [cmd("git log --oneline -2")]
    rows += [Row([(rand(rng, HEX, 7), "warn", None), (" " + rng.choice(["fix: retry", "chore: deps", "docs"]), "fg", None)])
             for _ in range(2)]
    for i, v in enumerate(vals):
        name, k = rng.choice(ENV_NAMES), rng.randrange(5)
        if k == 0:
            rows += [cmd(f"echo ${name}"), Row([(v, "fg", i)])]
        elif k == 1:
            rows += [cmd("cat .env"), Row([("NODE_ENV=production", "fg", None)]), Row([(f"{name}=", "fg", None), (v, "fg", i)])]
        elif k == 2:
            rows += [Row(cmd("").segs[:-1] + [(f'export {name}="', "fg", None), (v, "fg", i), ('"', "fg", None)])]
        elif k == 3:
            rows += [cmd("grep -rn token config/"), Row([("config/app.yml:12:  token: ", "fg", None), (v, "fg", i)])]
        else:
            rows += [Row(cmd("").segs[:-1] + [('curl -s -H "Authorization: Bearer ', "fg", None), (v, "fg", i),
                                              ('" https://api.example.com/v1/me', "fg", None)]),
                     Row([('{"id": %d, "ok": true}' % rng.randint(1, 9999), "fg", None)])]
    if not vals:
        rows += [cmd("uuidgen"), Row([(_uuid(rng).upper(), "fg", None)]), cmd("sha256sum dist/app.tar.gz"),
                 Row([(rand(rng, HEX, 64) + "  dist/app.tar.gz", "fg", None)]), cmd("git rev-parse HEAD"),
                 Row([(rand(rng, HEX, 40), "fg", None)])]
    rows.append(cmd(""))
    return f"{user}@{host}: ~/{proj}", rows


def _ide(rng, vals):
    lang = rng.choice(["py", "js", "yaml", "go", "toml"])
    names = ["api_key", "token", "secret", "auth_token", "client_secret"]
    rows = []
    if lang == "py":
        rows += [[("import", "kw", None), (" os", "fg", None)], [], [("# staging settings", "dim", None)]]
        for i, v in enumerate(vals):
            rows += [[(rng.choice(names).upper(), "fg", None), (" = ", "fg", None), (f'"{v}"', "str", i)]]
        rows += [[("TIMEOUT", "fg", None), (" = ", "fg", None), (str(rng.randint(5, 60)), "num", None)],
                 [("BUILD", "fg", None), (" = ", "fg", None), (f'"{rand(rng, HEX, 40)}"', "str", None)]]
    elif lang == "js":
        rows += [[("const", "kw", None), (" config = {", "fg", None)]]
        for i, v in enumerate(vals):
            rows += [[("  apiKey: ", "fg", None), (f"'{v}'", "str", i), (",", "fg", None)]]
        rows += [[("  requestId: ", "fg", None), (f"'{_uuid(rng)}'", "str", None), (",", "fg", None)],
                 [("  retries: ", "fg", None), (str(rng.randint(1, 5)), "num", None)], [("};", "fg", None)]]
    elif lang == "yaml":
        rows += [[("service:", "key", None)], [("  name: ", "key", None), ("worker", "str", None)]]
        for i, v in enumerate(vals):
            rows += [[(f"  {rng.choice(names)}: ", "key", None), (v, "str", i)]]
        rows += [[("  image: ", "key", None), (f"app@sha256:{rand(rng, HEX, 64)}", "str", None)]]
    elif lang == "go":
        rows += [[("func", "kw", None), (" main() {", "fg", None)]]
        for i, v in enumerate(vals):
            rows += [[(f"\t{rng.choice(['token', 'key', 'secret'])} := ", "fg", None), (f'"{v}"', "str", i)]]
        rows += [[("\tcommit := ", "fg", None), (f'"{rand(rng, HEX, 40)}"', "str", None)], [("}", "fg", None)]]
    else:
        rows += [[("[auth]", "kw", None)]]
        for i, v in enumerate(vals):
            rows += [[(f"{rng.choice(names)} = ", "key", None), (f'"{v}"', "str", i)]]
        rows += [[("trace_id = ", "key", None), (f'"{_uuid(rng)}"', "str", None)]]
    start = rng.randint(1, 300)
    out = [Row([(t.replace("\t", "    "), c, s) for t, c, s in segs], gutter=str(start + n))
           for n, segs in enumerate(rows)]
    return f"{rng.choice(['settings', 'config', 'main', 'client'])}.{lang} - editor", out


def _browser(rng, vals):
    rows = []
    if rng.random() < 0.5:
        rows.append(Row([("API keys", "fg", None)], font="sans"))
        rows.append(Row([("Keys let your services call the API. Keep them private.", "dim", None)], font="sans", wrap="word"))
        for i, v in enumerate(vals):
            rows.append(Row([(rng.choice(["Secret key", "API key", "Access token"]), "dim", None)], font="sans", gap=0.6))
            rows.append(Row([(v, "fg", i)], font=rng.choice(["mono", "sans"]), box="box"))
        if not vals:
            for label in ["Request ID", "Build commit", "Bundle digest"]:
                rows.append(Row([(label, "dim", None)], font="sans", gap=0.6))
                rows.append(Row([(_benign(rng), "fg", None)], font="mono", box="box"))
        rows.append(Row([("Created " + f"2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}", "dim", None)], font="sans", gap=0.6))
        return "https://dashboard.example.com/settings/api-keys", rows
    rows.append(Row([("{", "fg", None)]))
    rows.append(Row([('  "id": ', "key", None), (f'"{_uuid(rng)}"', "str", None), (",", "fg", None)]))
    for i, v in enumerate(vals):
        rows.append(Row([(f'  "{rng.choice(["token", "secret", "api_key", "access_token"])}": ', "key", None),
                         (f'"{v}"', "str", i), (",", "fg", None)]))
    if not vals:
        rows.append(Row([('  "commit": ', "key", None), (f'"{rand(rng, HEX, 40)}"', "str", None), (",", "fg", None)]))
        rows.append(Row([('  "etag": ', "key", None), (f'"{rand(rng, HEX, 32)}"', "str", None), (",", "fg", None)]))
    rows.append(Row([('  "expires_in": ', "key", None), (str(rng.randint(60, 86400)), "num", None)]))
    rows.append(Row([("}", "fg", None)]))
    return f"https://api.example.com/v1/{rng.choice(['tokens', 'credentials', 'session'])}", rows


def _log(rng, vals):
    lines = list(range(rng.randint(3, 7) + len(vals)))
    slots = sorted(rng.sample(lines, len(vals)))
    rows, sec = [], 0
    for n in lines:
        ts = f"2026-09-30 {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}.{rng.randint(0, 999):03d}"
        head = [(ts + " ", "dim", None)]
        if sec < len(vals) and n == slots[sec]:
            lvl, msg = rng.choice([("DEBUG", "http > Authorization: Bearer "), ("INFO ", "client configured with token "),
                                   ("WARN ", "using fallback credential "), ("DEBUG", "auth: key=")])
            rows.append(Row(head + [(lvl + " ", "warn" if lvl == "WARN " else "key", None), (msg, "fg", None), (vals[sec], "fg", sec)]))
            sec += 1
        else:
            lvl = rng.choice(["INFO ", "INFO ", "ERROR"])
            msg = rng.choice([f"request_id={_uuid(rng)} status=200 latency={rng.randint(2, 900)}ms",
                              f"deployed commit {rand(rng, HEX, 40)}", f"cache miss key={rand(rng, HEX, 16)}",
                              f"pulled image sha256:{rand(rng, HEX, 64)}"])
            rows.append(Row(head + [(lvl + " ", "err" if lvl == "ERROR" else "ok", None), (msg, "fg", None)]))
    return "Logs - worker - last 15 minutes", rows


def _chat(rng, vals):
    people = rng.sample(["Ana", "Ben", "Chen", "Dee", "Eli", "Fox"], 2)
    rows = [Row([(people[1], "key", None), ("  10:4%d" % rng.randint(0, 9), "dim", None)], font="sans"),
            Row([(rng.choice(["can you send me the staging creds?", "deploy is failing on auth again"]), "fg", None)],
                font="sans", wrap="word")]
    for i, v in enumerate(vals):
        rows.append(Row([(people[0], "key", None), ("  10:5%d" % rng.randint(0, 9), "dim", None)], font="sans", gap=0.6))
        if rng.random() < 0.5:
            rows.append(Row([(rng.choice(["here you go: ", "use this one for now ", "the key is "]), "fg", None), (v, "fg", i)],
                            font="sans", wrap="word", box="bubble"))
        else:
            rows.append(Row([("paste:", "fg", None)], font="sans"))
            rows.append(Row([(v, "fg", i)], font="mono", box="code"))
    if not vals:
        rows.append(Row([(people[0], "key", None), ("  10:52", "dim", None)], font="sans", gap=0.6))
        rows.append(Row([(f"broken since {rand(rng, HEX, 7)}, request id {_uuid(rng)}", "fg", None)],
                        font="sans", wrap="word", box="bubble"))
        rows.append(Row([(rand(rng, HEX, 40), "fg", None)], font="mono", box="code"))
    return "# " + rng.choice(["deploy", "backend", "ops", "incident-42"]), rows


SCENE_FN = {"terminal": _terminal, "ide": _ide, "browser": _browser, "log": _log, "chat": _chat}


# ---- rendering ------------------------------------------------------------------------------------------
_FONT_CACHE = {}


def _font(kind, px):
    if (kind, px) not in _FONT_CACHE:
        _FONT_CACHE[kind, px] = ImageFont.truetype(str(FONT_DIR / FONTS[kind]), px)
    return _FONT_CACHE[kind, px]


def _fit(text, font, avail, word):
    lo, hi = 1, len(text)                       # largest prefix that fits; always at least 1 char
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if font.getlength(text[:mid]) <= avail else (lo, mid - 1)
    if word and lo < len(text) and " " in text[1:lo]:
        lo = text.rindex(" ", 1, lo) + 1
    return lo


def _wrap(row, font, avail):
    """Split a row into lines of [(text, colour, secret)] that fit `avail` px."""
    chars = [(ch, c, s) for t, c, s in row.segs for ch in t]
    lines = []
    while True:
        text = "".join(ch for ch, _, _ in chars)
        n = _fit(text, font, avail, row.wrap == "word") if text else 0
        head, chars = chars[:n], chars[n:]
        lines.append([(ch, c, s) for ch, c, s in head])
        if not chars:
            return lines


def _render(scene, theme, font_px, dpi, rng, vals):
    title, rows = SCENE_FN[scene](rng, vals)
    p, s = PALETTE[theme], dpi
    lh = round(font_px * 1.45) * s
    pad, bar_h = 16 * s, round(font_px * 2.4) * s
    gutter_w = round(_font("mono", font_px * s).getlength("0000") + 12 * s) if any(r.gutter for r in rows) else 0
    x0 = pad + gutter_w
    avail = MAX_W * s - x0 - pad - 12 * s
    laid, where = [], {}
    for r in rows:
        f = _font(r.font, font_px * s)
        lines = _wrap(r, f, avail)
        for j, line in enumerate(lines):
            for _, _, sec in line:
                if sec is not None:
                    where.setdefault(sec, set()).add(len(laid) + j)
        laid.append((r, f, lines))
    flat = [(r, f, j, line) for r, f, lines in laid for j, line in enumerate(lines)]
    widest = max((f.getlength("".join(ch for ch, _, _ in line)) for _, f, _, line in flat), default=0)
    w = int(max(640 * s, min(MAX_W * s, x0 + widest + pad + 12 * s)))
    y, ys = bar_h + pad, []
    for r, f, lines in laid:
        y += round(r.gap * lh) + (6 * s if r.box else 0)
        for _ in lines:
            ys.append(y)
            y += lh
        y += 6 * s if r.box else 0
    h = int(y + pad)
    img = Image.new("RGB", (w, h), p["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, bar_h], fill=p["bar"])
    if scene == "browser":
        d.rounded_rectangle([90 * s, bar_h // 5, w - 40 * s, bar_h - bar_h // 5], radius=6 * s, fill=p["box"])
        d.text((100 * s, bar_h // 2), title, fill=p["fg"], font=_font("sans", font_px * s), anchor="lm")
    else:
        d.text((90 * s, bar_h // 2), title, fill=p["dim"], font=_font("sans", font_px * s), anchor="lm")
    for k, col in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        cx = 20 * s + k * 20 * s
        d.ellipse([cx - 6 * s, bar_h // 2 - 6 * s, cx + 6 * s, bar_h // 2 + 6 * s], fill=col)
    i = 0
    for r, f, lines in laid:                     # boxes first, behind their text
        if r.box:
            top, bot = ys[i] - 5 * s, ys[i + len(lines) - 1] + lh + 5 * s
            right = min(w - pad, x0 + max(f.getlength("".join(ch for ch, _, _ in ln)) for ln in lines) + 14 * s)
            d.rounded_rectangle([x0 - 8 * s, top, right, bot], radius=5 * s, fill=p[r.box], outline=p["border"], width=s)
        i += len(lines)
    i = 0
    for r, f, lines in laid:
        for j, line in enumerate(lines):
            yy = ys[i] + lh // 2
            if r.gutter and j == 0:
                d.text((pad + gutter_w - 12 * s, yy), r.gutter, fill=p["dim"], font=f, anchor="rm")
            x = x0
            for (c, sec), group in itertools.groupby(line, key=lambda t: (t[1], t[2])):
                text = "".join(ch for ch, _, _ in group)
                d.text((x, yy), text, fill=p[c], font=f, anchor="lm")
                x += f.getlength(text)
            i += 1
    wrapped = [len(where.get(k, ())) > 1 for k in range(len(vals))]
    return img, wrapped


def cases():
    """Every I1 case, in sealed order. Deterministic for I1_SEED, the fonts and the pinned Pillow."""
    gens = value_generators()
    combos = list(itertools.product(THEMES, DPI_SCALES, FONT_PX, SCENES))   # scene varies fastest
    for n in range(N_SECRET + N_CLEAN):
        has_secret = n < N_SECRET
        k = n if has_secret else n - N_SECRET
        theme, dpi, font_px, scene = combos[k % len(combos)]
        case_id = f"i1-{'s' if has_secret else 'c'}{k:03d}"
        rng = _rng("case", case_id)
        names, rendered, expected = [], [], []
        if has_secret:
            count = rng.choices([1, 2, 3], weights=[70, 20, 10])[0]
            names = [gens[k % len(gens)]] + [rng.choice(gens) for _ in range(count - 1)]
            for j, name in enumerate(names):
                value, secret = _draw_value(name, _rng("value", case_id, j))
                rendered.append(value)
                expected.append(secret)
        img, wrapped = _render(scene, theme, font_px, dpi, rng, rendered)
        quality = rng.randint(80, 92) if rng.random() < 0.25 else None
        if quality:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, subsampling=0)
            img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        out = io.BytesIO()
        img.save(out, "PNG", compress_level=6)
        yield Case(case_id, out.getvalue(), expected, font_px, scene, has_secret, dpi, theme, quality,
                   names, wrapped, img.size,
                   hashlib.sha256(img.tobytes()).hexdigest())


def iter_cases():
    for c in cases():
        yield c.case_id, c.png, c.expected_values, c.font_px, c.scene, c.has_secret
