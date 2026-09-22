# One-camera, one-job test

An adopted emulator normally accepts every supported request from its configured controller. Queue limits only limit concurrency. For an explicitly scoped trial, add this fragment to the worker configuration:

```json
{
  "worker": {
    "test_scope": {
      "permit_id": "approved-camera-test-1",
      "camera_id": "VERIFIED_PROTECT_CAMERA_ID"
    },
    "request_mp4_exports": true,
    "ffmpeg_path": "/usr/bin/ffmpeg"
  }
}
```

Use the camera's actual Protect ID, verified against its name and IP in Protect. A camera name or IP does not substitute for this ID. The example contains placeholders and enables no live test by itself.

The scope admits only `:7968/on_demand_inference` for that exact `payload.cameraId`. It requires one export from `/internal/aiprocessors/video/export`. The export query must contain exactly the fields observed in the native on-demand request:

- `camera` equals `payload.cameraId`, and `event` equals `payload.eventId`.
- `channel=0`, `type=rotating`, `mute=true`, and `createEvent=false`.
- `format` is `ubv` or `mp4`. The existing opt-in MP4 adapter still governs UBV requests.
- `start` and `end` are integer milliseconds, span at most ten seconds, and contain the requested timestamp. Repeated, missing, or unknown query fields are rejected.

Task descriptions, other cameras, different channels, malformed requests, and mismatched export identifiers fail before any media fetch or model call. The whole admitted job has a maximum fifteen-second deadline, or a shorter configured/requested deadline. This covers queueing, download, frame extraction, inference, and callback. Expiry cancels the local request; it does not guarantee that a remote provider stops computation already underway.

Before work enters the queue, the worker atomically saves a private reservation in `state/worker-test-scopes/`, keyed by the permit ID. It records the camera, job identity and input fingerprint, without footage or credentials. Only one process can consume that permit. The permit remains consumed after failure, timeout, cancellation, restart, or an uncertain callback. A failed attempt does not automatically trigger another model request.

An identical in-flight request shares its existing job. A completed duplicate returns the saved result without fetching footage, calling the model, or repeating the callback. If the process stopped before a result was durably recorded, that reserved job requires review and does not resume automatically. Retargeting a used permit to another camera or loading corrupt reservation state fails closed.

A new permit ID enables another one-job attempt after the operator reviews the prior outcome. Do not automatically change it or delete reservation files to replay an uncertain result. Keep the same state directory across container rebuilds and restarts. Removing `test_scope` restores ordinary worker behavior, so leave it configured throughout the trial.

With no `test_scope` field, existing configurations retain their behavior. Malformed supplied scope settings fail configuration validation. The scope does not alter adoption, enable deep-mode capabilities, enable search, or make an on-demand description persistent in Protect. `callback_mode=disabled` still runs inference and consumes the permit; it only suppresses the callback.

Local tests use synthetic media and actual loopback HTTP requests to verify the camera gate, query binding, reservation-before-fetch order, duplicate handling, restart safety, multi-process exclusion, storage failure, and deadlines. They do not establish native Protect acceptance or real model latency.
