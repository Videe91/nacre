# Redact-instead-of-reject study on I1 (working data), 2026-10-02

**For:** the proposed D-0027 amendment 4 (owner request 2026-10-02). I1 is working data since its one gated measurement.
**Status:** research prototype only. No product code changed.

## Method
- **Ground truth.** I1 was re-rendered with a copy of the I1 generator patched only to record where each secret was
  drawn. All 500 PNGs came out byte-identical to the committed ones.
  - "Ink uncovered" counts the secret's glyph pixels that were left unpainted.
  - This is the primary measure, because OCR not reading a secret does not mean a human cannot read it.
- **Detection.** Production OCR settings (`ocr_image_text`) and `strip_secrets`, run in-process. Every finding was
  kept, not only the first. The per-image verdicts matched the official I1 run on all 500 images (0 mismatches).
- **Painting.** A solid fill over the OCR box of each line that holds part of a finding.
  - **Margin:** a multiple of the box height, on every side.
  - **`nbr`:** also paints the OCR lines just before and after.
  - **`band`:** paints the full image width.
- **Re-check.** OCR and detection were run again on every redacted image.
  - **Fragment:** 8 or more consecutive secret characters can still be read.
  - **Whole:** the full value can still be read.

## Results: 463 gated secrets (12 physical px or more) in 400 images
Today's rejection keeps **408/463 (88.1%)** out of storage.

| Setting | Targeted secrets fully covered (of 367) | Targeted with an OCR fragment left | Collateral secrets covered (of 41) | **Gated secrets fully covered** | Same, with rescan-and-reject | Non-secret lines kept | Clean false positives: lines lost (of 115) |
|---|---|---|---|---|---|---|---|
| m0 | 341 | 17 | 0 | 341 (73.7%) | 76.7% | 82.4% | 41 |
| m25 | 350 | 14 | 0 | 350 (75.6%) | 77.5% | 78.2% | 40 |
| m50 | 350 | 10 | 0 | 350 (75.6%) | 77.3% | 67.1% | 52 |
| m100 | 358 | 8 | 3 | 361 (78.0%) | 80.8% | 58.2% | 70 |
| m50_nbr | 356 | 2 | 6 | 362 (78.2%) | 79.3% | 46.3% | 70 |
| band_nbr | 359 | 1 | 8 | 367 (79.3%) | 80.6% | 43.8% | 79 |

- **Targeted:** a secret whose own line was detected.
- **Collateral:** an undetected secret in an image where something else was detected. Rejection protects it today;
  redaction stores it.

## Findings
1. **The 55 gated secrets OCR missed are stored unchanged under any redaction setting.** The 11-point gap to
   rejection comes from these collateral and missed secrets.
2. **Wrapped secrets leak.** Every targeted secret that kept ink was wrapped. Plain margins leave the wrapped tail
   readable; painting the neighbouring lines fixes most of these.
3. **Margins hit neighbouring lines** (line pitch is 1.45× the font size). m25 and m50 give identical security,
   and m50 costs more.
4. **OCR lines are not visual rows.** A log row split into two boxes was only half painted.
5. **Painting does not reliably clear the detector:** 7–24 redacted images still trigger it. A product would need
   a second scan, which roughly doubles scan latency on flagged images (about +1.2 s median).
6. **Clean images:** the 11 falsely rejected log screenshots would be stored instead, with 26–49% of their lines
   painted. The other 89 clean images are untouched.
7. **Cost:** painting plus encoding takes a median of 4–8 ms per image.

## Conclusion
- **Redaction is strictly less safe than rejection on this data:** at best 79.3% (80.6% with a rescan) of gated
  secrets fully covered, against 88.1% kept out of storage today.
- **The usability gain is small:** 11 damaged log screenshots stored instead of rejected.
- **Not measured:** a hybrid that redacts only when exactly one finding exists and no other line looks like a
  credential slot.
