# Live AI Key evidence on Protect 7.3.68 (26 Sep 2026)

Adopted software AI Key (UP-AI-KEY profile) on a UDM Pro Max running Protect 7.3.68, Linux ARM64 image under Apple container. Values below are content-free device health counters and exact-event readbacks; no captions, frames or identifiers are published.

## Observed

- The adopted processor reconnected after three container replacements on the same state volume and stayed adopted and connected. No re-adoption was needed.
- Before this manifest listed 7.3.68, device health classified the reported controller version as `unknown`. The supported baseline stayed active, which is the fail-clear path.
- Control commands answered with result 0, counted individually in health: `getInfo` (4), `getTaskQueueInfo` (27,771), `setConsoleInfo` (2, so only allowlisted controller fields were sent), `updateTimezone` (2).
- Unsupported commands answered 95: `changeAiInferAgentSettings` (3), `networkStatus` (2), `sshService` (2), 470 unknown function names, and 54 `RequestAI` commands.
- `recognizeKeyFrames`, 635 commands before 26 Sep 06:40. 300 were for cameras outside the two-camera scope (95). The 335 in scope were refused by the worker: consumed one-use permit (237), key moments (48), video interval (50).
- **Key-moment timing** (39 later requests): moments all inside the interval 23; all outside 13, and every one sat exactly at the interval end; no moment before start or after end. Intervals: zero-length 3, over the configured 120 s bound 16. Protect 7.3.60's `frameSelectionSchema` does not bind key moments to the interval, and the worker now accepts a moment at the end (bd3e002).
- **Persisted caption.** One Wohnzimmer (G6 Instant) smart event at 07:05 used a fresh one-use permit. It completed with the full RAM callback (`keyMomentsTags: []`) and HTTP 200. Protect's exact-event GET then returned `ramState: "done"` and a non-empty `ramDescription` (140 characters), and the event kept the camera's own detections (5 detected thumbnails and areas).
- **Description-only callback** (23 Sep G6 event, read back on 7.3.68): `ramState: "done"` with an empty `ramDescription`. The 7.3.60 bundle routes results without `keyMomentsTags` to a handler that only updates an existing RAM row. The profile is now refused (64ff570).

## Not observed

- Fresh adoption, credential rotation or factory-login changes on 7.3.68.
- Search, database, deep mode, `/describe` tasks, or any on-demand `RequestAI` target.
- Continuous multi-camera admission, or persistence across a controller restart.
- A caption on the second scoped camera (no smart events in 48 h).

## Limits

One persisted caption on one camera family, under a single-use permit. Not continuous operation. Pull request #123 records the runs.
