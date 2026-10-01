# D-0027: Scanning binary attachments for secrets before storage

- **Status:** accepted (owner, 2026-10-01) with the decisions below
- **Tier:** D3 (what may be stored; detection behaviour). Amends D-0008 amendment 6. Resolves A-0021.
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0021, A-0041, A-0042 (new); A-0010

## Owner decisions at acceptance (2026-10-01)
1. **Approved (D3): unscannable binaries are rejected by default.**
   - **There is no opt-out in Phase 3.** The per-scope `allow_unscanned_binaries` proposed in §2 is **removed**.
   - Any opt-out is a **future, separate D3 ADR**.
2. **Approved:** the extraction limits; I1 and its target (≥ 95% at ≥ 12 px).
3. **OCR engine:** RapidOCR on `onnxruntime`, following the D-0024 runtime decision (the §3 recommendation).

## Amendment 1 (owner, 2026-10-01): I1 measurement definitions, fixed before any measurement
1. **Success per secret = "the secret is not stored":** the attachment is rejected for a detected secret, or
   rejected as unscannable. A stored attachment that contains the secret is a miss.
2. **Wrapped secrets count toward the 95% target** (65 of the gated secrets are split across lines).
3. **The size threshold uses physical pixels:** gated when `font_px × dpi_scale ≥ 12`.
   - **463 of the 548 secrets are gated** under this definition; the other 85 are reported only.
   - The manifest's sealed `gated` field (logical px, 361 secrets) is superseded for measurement. The manifest
     itself is not edited.
4. **Pillow 12.3.0 is approved as test-only** (D-0006 amendment 2).
   - The **committed images in `tests/ledger/secret_corpus/i1_images/` plus their manifest sha256s are the source
     of truth**. The pre-commit hook accepts only those exact bytes there.
   - Regeneration is checked only on the pinned platform.

## Context
- **Owner's Phase 3 gate item (2026-09-30):**
  - extract text from binaries (unpack archives, PDF text, OCR for images) before storage;
  - if a secret is found, reject the attachment with a clear error.
- **Today:** text attachments are decided by content and secret-stripped. Binaries are stored as given, marked
  `scan = "unscanned"` (D-0008 amendment 6, A-0021).
- **Why it matters now:** the interface (D-0026) lets real agents attach screenshots, logs inside archives and PDFs,
  the usual places a credential leaks.

## Options considered
1. **Extract, then scan with the existing detector; reject on any finding; fail closed on anything unscannable
   (recommended).**
2. **Extract and redact inside the binary** (black boxes on images, rewrite the PDF).
   - Rejected: rewriting arbitrary formats is fragile, and partial redaction gives false assurance. Rejecting is
     what the owner asked for.
3. **Keep binaries unscanned and rely on encryption plus shredding** (the Phase 1 position).
   - Rejected by the owner's gate item.
4. **Send binaries to a hosted OCR or vision model.**
   - Rejected: it ships unscanned (possibly secret-bearing) content to a provider, the very thing being prevented.

## Decision (proposed)

### 1. Extraction (in process, bounded, no network)
- **Archives:** zip, tar, gzip, bzip2, xz.
  - Recursive to depth 3.
  - Limits: ≤ 10,000 members, ≤ 64 MiB total uncompressed, expansion ratio ≤ 100×. Exceeding any limit = a
    zip-bomb guard → reject.
  - Member names are never used as paths: everything is extracted in memory, never to disk.
- **PDF:** the text layer of every page. A page with no text layer is rendered to an image and goes to OCR.
- **Images** (PNG, JPEG, GIF, WebP, TIFF, BMP): OCR with a local engine.
- **Text found anywhere** (members, PDF text, OCR output) is routed through the existing `strip_secrets` detector,
  **detection only**. It is never rewritten into the binary.
- **Office formats** (docx, xlsx, pptx) are zip containers: their XML text parts are scanned as text members.

### 2. The verdict
- **Any finding → reject** with `attachment_rejected: secret_detected`, plus the rule id and the location
  (member path, page, or image region). **The matched value is never included.**
- **Unscannable → reject by default:** an unknown binary format, an encrypted archive or PDF, a corrupt file,
  an extraction limit, or OCR failure. Error `attachment_rejected: unscannable(<reason>)`.
  - ~~Per-scope opt-in to store unscannable files as `unscanned`.~~ **Removed by the owner at acceptance:** no
    opt-out in Phase 3; any opt-out needs a separate D3 ADR.
- **Clean → stored**, marked `scan = "binary-scanned"` with the extractor versions. A new value of the D-0008 `scan`
  field.

### 3. OCR engine (dependency, D2)
- **(i) Tesseract** through `pytesseract`: mature, but a system binary outside pip, and its version is hard to pin.
- **(ii) RapidOCR on `onnxruntime`** (shares the runtime proposed in D-0024 (ii)): pip-pinned models, hash-checked
  like the embedder.
- **Recommendation: (ii)**, if D-0024 picks onnxruntime; otherwise (i).
- **Either way, OCR recall on secrets is measured:**
  - a new sealed set **I1**: credential strings rendered into screenshots (terminal, IDE, browser, at several font
    sizes and DPIs), built by a separate session (A-0042);
  - target (proposed): **≥ 95% of rendered secrets detected at ≥ 12 px**, measured per secret.

### 4. PDF dependency (D2)
- `pypdf` (pure Python, pinned) for the text layer.
- `pypdfium2` (pinned) to render image-only pages for OCR.

## Why this one
- It is exactly the owner's rule.
- Failing closed on the unscannable is the only setting that makes "reject if a secret is found" mean anything.
- Without it, an attacker or a careless agent zips, encrypts or rasterises the secret to get past the check.

## Consequences
- **New:**
  - `ledger/scan_binary_attachment.py`: extraction plus verdict. One functionality; an archive walker module only
    if the file passes 300 lines;
  - the call site moves into `store_attachment` before encryption;
  - the `scan` enum gains `binary-scanned`.
- **Latency:** OCR is about 0.2–1 s per image on CPU (to be measured). It is on the write path, but attachments are
  rare relative to events.
- **A-0021 is resolved** for scanned formats. It stays true only in scopes that opt into unscanned binaries.

## Tests (Phase 3 gate items)
1. **Each carrier with a runtime-built secret is rejected:** a zip (nested 3 deep), a tar.gz, a PDF text layer, an
   image-only PDF page, a PNG screenshot, a docx.
   - The error names rule and location, never the value.
2. **Clean files of every kind are stored** with `scan = binary-scanned`.
3. **Every bomb guard rejects:** an expansion-ratio bomb, a member-count bomb, a depth-4 nesting.
4. **Encrypted zip or PDF, and an unknown format, are rejected as unscannable.** No setting can make the interface
   store them.
5. **Relabelling:** a PNG declared as text/plain, or text declared as image/png, is still decided by content
   (existing D-0008 test, extended).
6. **I1 OCR recall** meets the target. Measured once, frozen; the evidence is recorded.

## How we'd know it was wrong
- I1 recall falls below target.
- Real agents hit `unscannable` often enough to push operators to opt out wholesale. That would be a usability
  signal to add formats, not to relax the default.

## Questions for the owner
1. **(D3)** Approve rejecting **unscannable** binaries by default, with a recorded per-scope opt-in to store them
   unscanned?
2. Approve the extraction limits (depth 3; 10k members; 64 MiB; 100× ratio)?
3. OCR engine: RapidOCR on onnxruntime (recommended, if D-0024 (ii)) or Tesseract?
4. Approve the I1 sealed OCR set and the ≥ 95% at ≥ 12 px target?
