# Audio permission lifetime correction — 6 November 2026

Related original work: [AI Port lifecycle](https://github.com/Olli0103/unifi-protect-local-ai/issues/28)
and [camera-policy continuity](https://github.com/Olli0103/unifi-protect-local-ai/issues/74).
Both broader issues remain OPEN.

## Reproduced failure

Two synthetic regressions failed against merged source `50a292f`: after an
enabled camera's stream-stop status or `ResetAIPortStreams`, feeding another
speech episode emitted a new enter/leave pair without fresh
`ChangeAudioEventsSettings`. The prior permission flags and settings-seen state
survived the stream lifetime. Control disconnect also left these flags intact.

This is a local permission-lifetime defect. It does **not** establish why Protect
cleared the native camera's selected audio types during the earlier pairing
trial. No vendor code, private controller payload, camera image or recording is
included here.

## Correction

- A stopped camera loses its cached audio grant and readiness retry state before
  status delivery. Other cameras retain their grants. Invalid reset requests do
  not change permissions.
- A valid all-stream reset and a new/lost control session revoke affected grants.
  Control loss discards old local open-event bookkeeping instead of replaying it
  into a new session. The existing decoder reconnect grace remains unchanged.
- Fresh controller settings are required to resume speech/sound events.
  Configured camera scope and per-hour event budgets remain unchanged; a stop is
  not a way to reset the rate budget.
- An observer captures the audio-policy generation and control socket. Results
  from an old lifetime cannot publish after stop/regrant. Audio enter/close
  bookkeeping is serialized so an already-writing enter can be followed by the
  stop's terminal leave.
- An off-thread sound feed retains ownership when its decoder reader is
  cancelled. Its reset is deferred until that actual worker completes; a new
  decoder cannot concurrently feed/reset the same detector. Current permissions
  are rechecked before publishing a returned sound enter.

## Checks and limits

`tests/test_aiport_audio_lifecycle.py` covers successful stop/reset/new-control
boundaries, fresh regrant, other-camera isolation, invalid reset, failed status
delivery, unchanged rate history, a real loopback TLS/WebSocket close, a concurrent
enter/stop, and blocked-classifier completion with and without reader cancellation.
All media/model inputs are synthetic; the loopback controller uses a generated
fixture certificate and the normal pinned client transport.

The initial adjacent test run had two fixture failures because Apple's default
OpenSSL lacks `passwd -6`. The corrected run uses the existing Homebrew OpenSSL;
no authentication implementation or TLS validation was weakened. A macOS
subprocess-transport cleanup warning from the existing decoder fixture is retained
separately from assertion results.

The corrected local group completed **215 passed, one cleanup warning** in
53.09 seconds (audio lifecycle, speech, sounds, candidate, adoption and ingest).
Static checks and whitespace checks passed. Linux CI remains a separate gate.

This source change neither restores nor patches native camera settings. No
production activation, new camera operation, alarm or owner walkthrough is part
of these tests. Native pairing-setting preservation still requires evidence.
