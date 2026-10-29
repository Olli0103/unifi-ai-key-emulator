# AI Port Person indexing acceptance, 29 October 2026

Issue [#99](https://github.com/Olli0103/unifi-ai-key-emulator/issues/99) concerns native class indexing, not detection accuracy or full AI Port parity.

## Native readback

Protect 7.3.74. Four adopted AI Ports remain connected with nine paired cameras, including two legacy G3 Instant cameras. The existing AI Ports run in `live_pool` mode; their recorded-probe claim counters are zero. All nine streams report decoded frames. Model-backed enter, moving, leave and snapshot-upload counters are non-zero on every instance. No trial emission or configuration change was performed for this readback.

The frozen window is **29 October 2026, 03:58:30.949–09:58:30.949 UTC**. For each paired camera, complete paginated native smart-event results were compared by exact event identity with that same camera's native Person-filtered results. The API query used the same frozen bounds for every page and filter. A complete browser-page reload was followed by an independent repeat of the reads.

| Scope | Smart events | Events carrying Person | Person-filter results | Non-Person events excluded |
| --- | --- | --- | --- | --- |
| All nine paired cameras | 209 | 192 | 192 | 17 |
| Two legacy G3 Instant cameras | 44 | 41 | 41 | 3 |

Both reads returned identical per-camera totals. There were zero missing or extra Person-filter results, zero cross-camera results and zero repeated event IDs. On the reload read, zero distinct event IDs shared an identical start/end/class signature within one camera. Eight cameras had Person events in the window; the ninth had no smart event and is not positive detection evidence.

The native stored event and class-filter resources provide authoritative saved-class evidence for the same original-camera event, including events several hours old. This is not a thumbnail-rendering or video-playback test. No event identifiers, camera identifiers, payloads or media are retained in this public report.

## Acceptance mapping

1. Original-camera Smart event and Person filter while still paired: the current native resource comparison proves the same stored event identities and Person class after reload. The earlier subscription add/update evidence is in [the issue's September readback](https://github.com/Olli0103/unifi-ai-key-emulator/issues/99#issuecomment-5843562015).
2. No duplicate or cross-camera event in the bounded comparison: the checks above pass. There is no test configuration to roll back; production pairings and recording settings were unchanged. This does not establish semantic duplicate-detection accuracy.
3. Existing synthetic tests cover enter, moving, leave, stream stop, active-event shutdown, per-camera policy revocation, one-use expiry and count-only subscription output. A targeted run on source `343bedf` passed **13 tests**, with 174 deselected. The full exact-head Linux run passed 1,937 tests, with 109 skipped and five existing warnings.
4. Live detection, distinct from historical replay: the current legacy-camera events and live-pool counters corroborate the earlier [end-to-end live acceptance](https://github.com/Olli0103/unifi-ai-key-emulator/issues/99#issuecomment-5851341199). No historical probe was run in this validation.

## Limits

This closes the specific missing-native-Person-class regression. Detection precision/recall, all feature classes, alarm behavior, future-version compatibility, zone controls, retrieval quality and overall product parity remain with their own issues. No capability flag or compatibility-manifest status was broadened.
