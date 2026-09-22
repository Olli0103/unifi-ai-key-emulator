# Bounded automatic event descriptions

The worker can accept one native `recognizeKeyFrames` video command and return a real vision-model caption through the adopted device's callback. This is an experimental basic-mode path. It does not implement deep understanding, object indexing, ReID, face or plate recognition, audio, or semantic search.

The command and persistence contract below come from Protect 7.2.105 source. Native acceptance and durable display on another Protect version must be verified separately. Loopback tests prove the emulator's request handling and callback format, not Protect's database writes.

The separate native on-demand route has completed successfully on Protect 7.3.60: the worker generated a real OpenAI caption, Protect accepted its callback, and the requesting API returned the description. That result does not verify this automatic path or persistent event storage.

## Scope and trigger

Enable the worker path only with an explicit single-camera, single-use scope:

```json
{
  "worker": {
    "request_mp4_exports": true,
    "test_scope": {
      "kind": "recognizeKeyFrames",
      "camera_id": "SELECTED_CAMERA_ID",
      "permit_id": "REVIEWED_SINGLE_EVENT_TEST"
    }
  }
}
```

Existing scopes without `kind` retain their on-demand-only behavior. This scope accepts no on-demand or deep-mode jobs. The reservation is durable before the first media fetch, survives restart, and cannot be reused after a failure or uncertain callback. Repeated delivery of a completed identical job returns the existing result without another inference or upload.

In the examined controller, a new smart-detection event passes through native key-moment selection and a short coalescing delay. The basic dispatcher requires `recognizeAnythingSettings.enabled` and the selected camera in its camera list, or `allCameras`. It sets `postVLM` from `aiSummarySettings.enabled`. Configure only the intended camera through the normal Protect UI. These settings are controller policies, distinct from the worker's local permit.

The UI summary capability is `supportAiSummary.enabled`. It remains an explicit opt-in and is reported as disabled if the local model, video decoder, callback mode or caption scope is not configured. Normal runtime construction also validates the inference provider. A caption test does not establish object indexing or any search capability. Keep `supportDeepMode`, `supportVlm`, and unsupported recognition capabilities disabled, with `aiMode: "basic"`. This implementation does not enable those flags or change Protect policies automatically. Opening the summary action on an older event can invoke the separate on-demand route; it does not prove this automatic path works. Retroactive jobs use other media forms and remain unsupported here.

## Accepted command

The UCP command is `recognizeKeyFrames`, not a fabricated `RequestAI` target. The device passes its body to the worker in an internal `{command, payload}` wrapper and acknowledges only after queue admission and permit reservation.

Only the observed basic `ramType: "video"` form with `postVLM: true` is accepted. It must have the exact permitted camera, an event identifier, channel `0`, rotating video, `mute: true`, and `createEvent: false`. Its start and end must span no more than ten seconds. Every key-moment timestamp must be a distinct absolute millisecond timestamp inside that interval, with at most `worker.max_images` frames.

The media URL must use the configured controller origin and `/internal/aiprocessors/video/export`. Its camera, event, interval, channel, format and remaining query fields must exactly match the command. Duplicate or additional query fields are rejected. MP4 adaptation changes only the evidenced `format=ubv` field. The worker downloads once, then decodes the requested key moments relative to the export's `x-start-timestamp`, falling back to its requested start. It sends only those decoded images to the configured vision provider.

The result URL must be `/internal/aiprocessors/recognize-anything` on a configured controller origin. Redirects are not followed. Existing controller TLS verification and client identity are unchanged. Recognition variants, image/multiple-image variants, audio, unrelated cameras, unsupported fields and longer video exports fail before media or inference.

## Callback and persistence

The callback is multipart with one JSON file field named `ram`. The full RAM event-tagging envelope contains:

- The command's `cameraId` and `eventId`, the generated `description`, and `status: "success"`.
- `keyMomentsTags: []`, explicitly reporting no tag results. No snapshots, detections or embeddings are invented.
- Measured `preProcessMs`, `inferTxtMs`, and `timeElapsedMs`. `inferBoxMs` and `inferTagMs` are zero because those stages do not run.

This differs materially from a description-only RAM response. In the examined source, a description-only response updates existing `ramDetections` rows but does not create the initial row. The full event-tagging branch calls the console event-level insert/upsert even when tags and embeddings are absent. This permits description persistence without an external PostgreSQL service or ReID. It also updates the event's RAM tags to an empty list, so this first test should target a fresh event rather than replace an existing enriched result.

Protect responds HTTP 200 before completing its asynchronous persistence work. Worker status `http_accepted` therefore means only that the upload was accepted. Confirm the description on the correct event after a page reload, and verify that no other camera or second event was processed, before claiming native persistence.

## Diagnostics and source evidence

Device health includes process-local `control_commands` counters and the latest result code for a fixed command allowlist. Unknown command names share one aggregate entry. No command bodies, request IDs, URLs, credentials or tokens are included. Cached duplicate requests are not counted again. Counters reset when the process restarts.

Relevant Protect 7.2.105 `service.js` modules:

| Module | Evidence |
| --- | --- |
| `28884`, `26673` | Native key-moment trigger and coalescing. |
| `42220` | Camera policy selection, `recognizeKeyFrames` body, and `postVLM` source. |
| `13944` | Full RAM event-tagging schema and separate description-only schema. |
| `68823` | Adopted-device multipart callback; acknowledgment precedes persistence. |
| `60814` | Existing task/event lookup and per-camera policy check before saving. |
| `5335`, `40207` | Initial console event-level RAM description insert/upsert. |
| `24160` | Description-only update, without an initial insert. |

AI Key 2.2.8 `syswrapper.sh` extracts absolute key-moment timestamps relative to the video start and honors `x-start-timestamp`. No firmware program is executed or included by this implementation.
