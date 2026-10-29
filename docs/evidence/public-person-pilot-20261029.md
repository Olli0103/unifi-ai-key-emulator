# Public person-presence adapter pilot — 29 October 2026

**Seven correct usable responses out of twelve scheduled cases (58.3%);
five errors. This is not a quality PASS or an AI Key equivalence result.**
The [machine-readable record](public-person-pilot-20261029.json) retains all
case IDs, public source/attribution URLs, labels, statuses, times, diagnostic
counts and frozen receipt hashes. No images, raw replies, weights, private
camera identifiers or credentials are published.

## Corpus frozen before predictions

Existing COCO val2017 annotations were used, not a generated custom corpus.
Within image-metadata license ID 4 (CC BY 2.0), minimum side 240 pixels, select
ascending image IDs: six with zero person annotations and six with one to
three non-crowd person annotations and a largest person annotation covering
at least 5% of the image. Interleave the first six of each group. The frozen
selection digest is in the record; no replacements or exclusions followed
prediction. A fresh model-context reviewer inspected all twelve decoded
originals without access to predictions and agreed with the person/none labels.
This is not independent human annotation or a held-out training-contamination
claim: COCO may be present in model training.

COCO's [terms](https://cocodataset.org/#termsofuse) distinguish CC BY 4.0
annotations from separately copyrighted Flickr images. Each selected image's
metadata declares CC BY 2.0; original Flickr URLs remain in the record. These
metadata checks are not independent legal clearance. Images remain local.
The original COCO HTTPS image endpoint failed certificate validation before
returning bytes; the official named bucket was read over verified-TLS AWS S3
instead. No TLS check was disabled. The 252,907,541-byte annotation archive
SHA-256 is `113a836d90195ee1f884e704da6304dfaaecff1f023f49b6ca93c4aaae470268`;
selected annotation JSON SHA-256 is
`e8c7f7908f1d7278341fae127d0da654f102f11bd7b21d8aeefa635b8c810b6f`.
All twelve image byte hashes and decoded dimensions matched their receipts.

## Exact inference scope

The existing local converted `Qwen3-VL-8B-Instruct-int4-ov` snapshot was served
as alias `qwen3-vl-8b` by OpenVINO Model Server 2026.4.1 GPU. All 28 model files
(5,462,523,036 bytes) were hashed before/after and remained identical. The
upstream checkpoint revision is **unknown**, not inferred from a directory
name. The model server was not restarted. This is a hash-bound converted
snapshot, not attestation of an upstream checkpoint.

The actual Key candidate image from source `9df7896` ran the existing **primary
fraction-coordinate prompt** and provider adapter against the local server,
with 1,024 output tokens, threshold 0.5, concurrency one, no warmups, no retries
and twelve client attempts. The six involved source modules match source
`a13ade9` byte-for-byte. Inputs and prompt were frozen before calls; gold labels
were not mounted in the runner or sent to the model. No cloud/fallback route,
private camera frames or credentials were used.

The benchmark normalizer rejects duplicate/nonfinite JSON and any malformed
item. It is intentionally stricter than production's valid-sibling retention.
This tests person presence, not box localization or all detection classes.
In particular, it does **not** test the deployed local fallback's separate
0–1000-grid prompt. Do not transfer these scores to that route.

## Complete results

| Measure | Observed |
| --- | --- |
| Planned / terminal / client attempts | 12 / 12 / 12 |
| Usable and correct | 7 (two person, five none) |
| Errors, counted incorrect in all-case denominator | 5 (four person, one none) |
| All-case correct / coverage | 7/12 (58.3%) / 7/12 |
| Person usable coverage | 2/6 |
| None usable coverage | 5/6 |
| Valid-only accuracy | 7/7; excludes all five errors |
| API plus parser nearest-rank p50 / p95 | 4,676 / 8,404 ms |
| Provider-reported charge | unknown (`null`), not zero |

The scorer's answered-only calibration covers just the two non-`none`
responses: mean confidence 0.99, ECE 0.01, Brier 0.0001. It excludes five valid
negative responses and five errors; it establishes no calibrated probability
quality. Latency is client API-plus-parser time on a shared existing server,
not cold inference, native event latency or a hardware comparison.

Error diagnostics preserve rather than repair the original results: one
unsupported animal label with grid-scale coordinates; two wholly malformed
items with grid-scale coordinates; one malformed item alongside a valid
sibling; and one duplicate `box` key. A separate fresh model-context arithmetic
review recomputed 109 checks, including receipt hashes, class coverage,
calibration scope and quantiles, without mismatches. It is not external human
or independent installation acceptance.

## Production isolation and parser finding

All six production container IDs/restarts/images and core configuration hashes
were unchanged before/after. Native identity/pairing read-back still matched
the original one Key, four Ports and nine pairings. The owned helper was
removed and independently confirmed absent. No production image/config/model
update occurred during this pilot. These checks are not recording-playback
or native event-quality proof.

Offline replay of the saved ambiguous reply showed that the old primary
parser rejected its grid-scale box, while the existing fallback grid
normalizer erased the duplicate key and then accepted one observation. This
was a replay only: no fallback inference or Protect event was issued. The
[strict detection JSON fix](detection-json-ambiguity-20261029.md) now refuses
that same reply in both paths. It does not rewrite the frozen pilot, improve
its reported 7/12 result or claim new model quality.

Issue #7 remains open for representative consented footage, pre-tuning targets,
night/IR/occlusion/weather coverage, other modalities and an actual matched
reference. No thresholds, prompts, labels or models were tuned after this run.
