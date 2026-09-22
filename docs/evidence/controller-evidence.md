# Controller-side validation evidence

Observed on 22 September 2026 by static inspection. No controller was contacted and no vendor program was executed. This records the receiving side of the protocol, which the first feasibility pass had not inspected.

## Version and source boundaries

The public firmware service supplied Protect **7.2.105**, AI feature controller **2.0.17**, and device service **7.2.14**. Protect's package control file pins the latter two versions, so these are a matching controller package set. The public API documentation route examined earlier was **7.3.60**. Neither source establishes the target installed version.

| Public source | SHA-256 verified after download |
| --- | --- |
| [Protect 7.2.105 metadata](https://fw-update.ui.com/api/firmware/67518b21-bd28-40df-8b3a-e21098ecc6e2) | `d6ee4477bf09e353cbbf3e963ee3d3a9451735e671066fc477ac4a8efe77317e` |
| [AI feature controller 2.0.17 metadata](https://fw-update.ui.com/api/firmware/083a0b4c-16a8-4bb0-b333-16614e7b4588) | `654f5d91d9f74ebd2ab7406a13a0c9c7a6bd0caf22f19c76f939f5c51ae4fe7e` |
| [Device service 7.2.14 metadata](https://fw-update.ui.com/api/firmware/a0ddfc8d-2c9e-439b-8a6a-2486e1b63b37) | `deadb1a749301f17061363c1b220180bd05eea4712c9ae2c83a0b757d7df668d` |

The principal evidence is `usr/share/unifi-protect/app/service.js` inside the Protect package. Its SHA-256 is `a7370e9a1b35db67d56104268841b9c2ba16342befb750ffef6654ee2b07bfdb`. It is bundled on one line, so references below use module numbers and function names rather than misleading line ranges. Modules were separated as text for inspection, never imported or evaluated.

## Description upload authentication

Module **68823**, `aiprocessorsInternalRouter` and `authenticator`, looks up an AI processor by normalized device identity, requires an adopted record, and checks the presented certificate against the stored fingerprint. An advertised mTLS capability makes a matching certificate mandatory where a fingerprint is recorded. A legitimate implementation should use the normal adoption flow and keep its certificate stable.

The router supplies the authenticated processor record to subsequent handlers. Reproducing the HTTP body alone is insufficient to establish accepted native enrichment.

## Legacy RAM descriptions and tagging

The same module exposes the multipart description/tagging callback. After parsing the upload it sends HTTP 200 and then publishes the result for asynchronous processing. **HTTP success does not prove persistence or searchability.**

Module **13944**, `ramInferenceSchema`, distinguishes a full tagging result from a description-only result. The description-only schema includes `cameraId`, `eventId`, `status`, and a nonempty `description`.

There is a source contract discrepancy: AI Key 2.2.8's inspected VLM sender constructs a three-field result without `cameraId`. The prototype must preserve this difference explicitly and provide a separate receiver-aligned profile. The discrepancy alone is not a runtime rejection finding; the exact middleware behavior has not been exercised.

Module **60814**, `waitByEventIdAndSaveRecognizeAnything`, resolves the existing event and camera and checks the processor's camera-specific Recognize Anything settings. It serializes saves by event ID. Different handlers process descriptions, tagging, faces, and plates.

Module **24160**, `saveRamDescriptionEnhancement`, updates `ramDescription` on existing `ramDetections` rows and sends a client update. It does not insert the initial RAM search record. A visible transient update therefore must not be mistaken for a persistent, searchable event.

Module **5335**, `saveEventTagging`, handles the fuller result: it transforms tags, updates the event's metadata, associates detected objects, and creates RAM records through module **40207**. Object-level vector records can be routed to the AI processor's PostgreSQL database. Tagging and description enhancement are separate obligations.

## A smaller on-demand milestone

Module **76776**, `visionToLanguage`, chooses a connected external AI processor, builds a short video request around a timestamp, and invokes module **41386**, `requestAi`. That sends a `RequestAI` job to the worker's `:7968/on_demand_inference` target and waits for a description through a controller-provided callback.

This matches the on-demand service found in AI Key 2.2.8 and is a more focused first integration experiment than implementing all search storage. The result is returned to the caller. This function alone does not establish persistent event indexing or the corresponding native UI behavior.

## Newer session-description path

Module **42220** can dispatch description jobs to `:7968/describe`, with a controller task ID in the callback URL and image/video inputs. Module **68823** accepts a description result with optional labels and a description embedding. Its HTTP acknowledgement also precedes asynchronous handling.

The independent search investigation traced the task ledger and storage requirements in [search-evidence.md](search-evidence.md). The inspected AI Key 2.2.8 VLM file does not implement this target. This is a profile difference; it does not by itself prove the installed combination is broken, since feature flags and other versions can select different paths.

## What this validates

Observed receiving code supports an implementable path for an independently written processor. It supplies concrete registration, job, callback, and storage contracts. It gives a reason to build a lab prototype, rather than merely hoping arbitrary event PATCH requests work.

The remaining integration checks are normal adoption on the target version, advertised capabilities and settings, delivery of a real job, successful authenticated callback, persistence after refresh/restart, native search retrieval, and bounded failure/retry behavior. These remain `needs_evidence` until exercised.
