# Versioned native short-query handling (5 November 2026)

The documented AI Key 2.2.8 text path adds `a photo of ` for queries shorter
than three words ([source evidence](search-evidence.md#older-768-dimensional-path)).
The emulator's CLIP server used only literal query strings. This change makes
that preprocessing explicit and opt-in, with a distinct tokenizer contract.
It is not a threshold reduction or a silent change to existing indexes.

The `literal` default remains unchanged. Server health and vectors, client
checks, staging, generation fingerprints, writer-lock reconstruction, runtime
identity and config admission agree on the selected policy. A legacy profile
without a tokenizer identity cannot silently adopt the new policy. Rollback
checks the retained old contract, including same-weight/different-policy swaps.

Development evaluation used the original 24 owned drawings/10 queries, plus
one previously confirmed real hallway object; private media/identifiers are
not published. The existing LAION2B ViT-L/14 checkpoint with unchanged center
crop and the short-query policy produced positive MRR@5 1, nDCG@5 0.960893,
recall@5 0.819444 and zero results for all four absent queries at cosine 0.25.
All six positive first results were relevant. The hallway score was 0.283442;
the train query on that image was 0.134462. Prior literal-query and full-image
resize failures remain retained, not relabeled successful.

Exported ONNX and Torch rankings matched for all 24 images/10 queries;
maximum normalized vector-component difference was 1.184106e-6. Three text
length boundaries and a 16-image batch passed. Actual loopback HTTP verified
the short-query vector against Torch, response/health identity, and invalid
input rejection. An isolated NAS sidecar independently reproduced the real
image scores. None of these results proves held-out quality, all-history
relevance, a completed migration, or native Protect readback after cutover.

476 focused index/CLIP/search tests passed, with 70 existing optional database
or media skips. A subsequent same-weight rollback check and affected groups
passed 62 tests. These are local CPU checks, not substitutes for the remaining
native rollout. Production routes, profiles and vectors were not changed by
these source/sidecar tests.

## Private deployment follow-through

A subsequent owner-authorized deployment replaced 9,655 real image vectors.
The frozen original-image archive was fully covered; the actual staged SQL
quality gate returned the expected hallway object and no train result in the
original fixed interval. After the atomic swap and runtime restart, pinned
HTTPS reported the target full encoder identity. Authenticated Protect queries
then returned that same object (including an exact-event reload) and no train
result. This is evidence for that fixed case, not all-corpus relevance. A subsequent fresh-session MFA readback passed both frozen native queries,
confirmed identical runtime identity before/after, and acknowledged logout.
The independent persisted receipt and generation journal agree on `cut_over`
and `native_verified=true`; the disposable readback container is absent.
No export, staging or cutover writer was repeated for that finalizer.
This completes the bounded migration, not representative all-history quality
or the separate native UI auto-broadening behavior.

Operational corrections found on this path:

- PostgreSQL sharing the Key container's network namespace loses network
  interfaces when the namespace owner is stopped. Prepare a non-writing
  namespace holder, preserve the original database volume, and recreate the
  namespace-dependent database with its new owner. Restore the ordinary
  application entrypoint after the swap; never leave `sleep` as the runtime.
- Atomic vector updates maintain indexes and need a different statement budget
  from reads. The first 15-second attempt rolled back. Cutover/rollback now
  have a bounded 300-second statement budget; lock timeout and transactional
  coverage/identity guards are unchanged. Reconcile profile, journal, and
  snapshot existence before a retry.
- Frozen camera/time filters must be sent to the native endpoint. Protect
  7.3.74 echoed explicit time bounds but omitted `cameraIds` in
  `searchCondition`. The pinned client records the actual outgoing camera
  scope separately; readback never fabricates a server acknowledgment and
  still rejects conflicting echoes and every wrong-camera/wrong-time row.
