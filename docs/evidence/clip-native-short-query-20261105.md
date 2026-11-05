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
