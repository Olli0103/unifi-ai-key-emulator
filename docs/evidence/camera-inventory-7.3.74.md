# Camera inventory contract on Protect 7.3.74

Read-only contract check, 29 October 2026. This extends only the exact inventory
version allowlist; it is not a blanket compatibility claim for Protect 7.3.74.

The existing dedicated inventory credential had expired. After replacing that
credential in the existing consumer, the unchanged exact-leaf-pinned client read
`meta/info` and `cameras` successfully. The production version gate was not
bypassed for admission: a separate diagnostic used the same pinned connector,
fixed GET paths, response bounds and strict parser.

The complete integration response passed the existing parser. Its camera-ID set
was compared by SHA-256 with an independently authenticated native Protect
bootstrap read; the digests matched. The inventory contains 11 cameras, 10
connected, across eight model families. The per-model counts also match:

| Model | Count |
| --- | ---: |
| UVC G3 Instant | 2 |
| UVC G4 Bullet | 2 |
| UVC G4 Dome | 1 |
| UVC G4 Doorbell Pro | 1 |
| UVC G4 Instant | 2 |
| UVC G4 Pro | 1 |
| UVC G5 Flex | 1 |
| UVC G6 Instant | 1 |

No names, IDs, addresses, API keys or camera media are included in this record.
No camera policy, pairing, model or inference destination was changed.

## Regression evidence

- The exact 7.3.74 pinned-read case and a synthetic eleven-camera/eight-family
  inventory both failed before the allowlist change.
- The same cases pass after adding only `7.3.74`; the report still has
  `processing_enabled: false` and preserves the offline camera.
- Neighbouring/unobserved versions (`7.3.73`, `7.3.75`, `7.4.0`, a preview suffix
  and a missing version) stop before the camera read. No range/wildcard is used.
- Existing wrong-pin, changed-leaf, expired-auth, redirect and camera-policy
  admission tests remain required. Registry eligibility remains constrained by
  the existing model and camera-ID policy; accepting an inventory does not add
  cameras to that policy.

Source support, deployed runtime and native health are separate observations.
The contract check itself does not establish deployment or restored readiness.
