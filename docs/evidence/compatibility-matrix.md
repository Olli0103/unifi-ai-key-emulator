# AI Key compatibility matrix

<!-- Generated from compatibility-manifest.json (ai-key/2026-09-27.4). Run `python tests/test_compatibility_manifest.py --write`; do not edit by hand. -->

Manifest `ai-key/2026-09-27.4` for the `ai-key` profile, based on commit `7241dcb`. AI Key profile only. AI Port is a separate profile (issue #6). Statuses describe this repository's behavior, not vendor parity.

- `native-verified`: Observed on a live Protect controller; see the per-version live results. Applies only to those versions and conditions.
- `fixture-tested`: Implemented and covered by synthetic tests; native behavior is not individually verified.
- `implemented`: Code exists without a dedicated synthetic test.
- `unsupported`: Not provided by this build. The per-feature rejection field describes whether it is rejected, disabled, empty, ignored or simply unavailable.
- `needs_evidence`: The native contract or behavior is not established; nothing may be claimed.

Live columns show each live trial separately. `indirect` means the behavior was necessarily exercised by another verified workflow but not individually recorded. Static references are source inspection of other versions and never count as native evidence.

| Feature | Status | Live 7.3.56 | Live 7.3.60 | Live 7.3.68 | Static references | Activation |
| --- | --- | --- | --- | --- | --- | --- |
| **adoption** | |  |  |  | | |
| `discovery.udp_v1_query`: Read-only UDP discovery: v1 information query and v1 command 4 MAC query | fixture-tested | indirect | not_observed | not_observed | AI Key 2.2.8 | explicit_opt_in |
| `discovery.other_opcodes`: Discovery v0/v2, mutation opcodes, GUID/controller UUID, DDC and Wi-Fi fields | unsupported | — | — | — | AI Key 2.2.8 | default |
| `adoption.management_info_post`: Credentialed HTTPS POST /api/info before adoption | fixture-tested | indirect | not_observed | not_observed | Protect 7.2.105, AI Key 2.2.8 | default |
| `adoption.management_adopt`: HTTPS POST /api/adopt with controller token, WSS mode 0 and configured controller host | native-verified | native-verified | not_observed | not_observed | Protect 7.2.105, AI Key 2.2.8 | default |
| `adoption.layer3_host_adoption`: Layer-3 host adoption route (/api/adopt_layer3 in firmware) | unsupported | — | — | — | AI Key 2.2.8 | default |
| `adoption.factory_enrollment`: Bounded factory-credential enrollment window (at most ten minutes) | native-verified | native-verified | not_observed | not_observed | Protect 7.2.105 | experimental_opt_in |
| `adoption.generated_password_flow`: Credentialed adoption with the generated private management password | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105 | default |
| `adoption.readoption_refused`: Re-adoption of an already adopted device is refused until an explicit local reset | fixture-tested | not_observed | not_observed | not_observed | — | default |
| `control.connection_headers`: WSS control connection with client certificate and x-ident/x-type/x-sysid/x-ip/x-version/x-mode/x-adopted/x-token | fixture-tested | indirect | indirect | indirect | Protect 7.2.105, AI Key 2.2.8 | default |
| `control.profile_ucp4_negotiated`: Strict ucp4 control profile requiring a negotiated Sec-WebSocket-Protocol | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105 | default |
| `control.profile_device_service`: device-service control profile: absent subprotocol accepted only with a token or confirmed adoption and an explicit pin | native-verified | native-verified | not_observed | not_observed | — | default |
| `adoption.time_sync_confirmation`: Adoption is confirmed only by a matching timeSync response on the current token connection | native-verified | native-verified | not_observed | not_observed | Protect 7.2.105, AI Key 2.2.8 | default |
| **lifecycle** | |  |  |  | | |
| `lifecycle.credential_rotation`: changeUserPassword rotates the management password, stored only as a PBKDF2 hash | native-verified | native-verified | not_observed | not_observed | Protect 7.2.105, AI Key 2.2.8 | default |
| `lifecycle.factory_login_disabled`: Factory credentials rejected after rotation or enrollment expiry | native-verified | native-verified | not_observed | not_observed | — | default |
| `lifecycle.reconnect_after_restart`: Restart reuses identity and certificate and reconnects adopted without a token | native-verified | native-verified | native-verified | native-verified | Protect 7.2.105, AI Key 2.2.8 | default |
| `lifecycle.abnormal_closure_backoff`: Abnormal transport loss preserves adoption and reconnects with capped exponential backoff | fixture-tested | not_observed | not_observed | not_observed | — | default |
| `lifecycle.protocol_violation_close`: Malformed or unsupported frames close the control socket with 1002 (1003 for text) without changing adoption | fixture-tested | not_observed | not_observed | not_observed | — | default |
| `lifecycle.unknown_controller_version`: Unknown or unrecognized Protect versions in setConsoleInfo are reported as fixed categories; adoption and the baseline continue | native-verified | not_observed | not_observed | native-verified | — | default |
| `lifecycle.controller_upgrade`: Adoption survives an in-place Protect upgrade | needs_evidence | not_observed | not_observed | not_observed | — | default |
| `lifecycle.emulator_upgrade`: Adoption and identity survive an emulator version upgrade | needs_evidence | — | — | — | — | default |
| **control** | |  |  |  | | |
| `control.get_info`: getInfo returns type, sysid, version, MAC, uptime, poeType, storageSize and featureFlags | native-verified | indirect | indirect | native-verified | Protect 7.2.105, AI Key 2.2.8 | default |
| `control.get_task_queue_info`: getTaskQueueInfo reports the six observed queue fields | native-verified | not_observed | not_observed | native-verified | Protect 7.2.105, AI Key 2.2.8 | default |
| `control.set_console_info`: setConsoleInfo stores consoleName, id, protectVersion and supportsDbCredential locally | native-verified | not_observed | not_observed | native-verified | Protect 7.2.105, AI Key 2.2.8 | default |
| `control.set_info`: setInfo accepts only {hostname} as logical metadata | fixture-tested | not_observed | not_observed | not_observed | AI Key 2.2.8 | default |
| `control.update_timezone`: updateTimezone accepts only {timezone} as logical metadata | native-verified | not_observed | not_observed | native-verified | AI Key 2.2.8 | default |
| `control.request_ai.on_demand_inference`: RequestAI :7968/on_demand_inference, admitted before inference | native-verified | not_observed | native-verified | not_observed | Protect 7.2.105, AI Key 2.2.8 | experimental_opt_in |
| `control.request_ai.describe`: RequestAI :7968/describe session task with image or video inputs | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105 | default |
| `control.request_ai.unknown_target`: RequestAI with an unimplemented or malformed targetUri | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105, AI Key 2.2.8 | default |
| `control.request_ai.second_stage_verification`: RequestAI second_verifier_mlabel classify (Second Stage Verification) answered by local CLIP zero-shot | fixture-tested | — | — | not_observed | Protect 7.3.60 bundle | explicit_opt_in |
| `control.recognize_key_frames`: recognizeKeyFrames video caption command within explicit one-use camera scopes | native-verified | not_observed | native-verified | native-verified | Protect 7.2.105, AI Key 2.2.8 | experimental_opt_in |
| `control.continuous_caption_admission`: Opt-in automatic captions admitted by fresh Protect inventory, model-family policy and a durable global budget | fixture-tested | not_observed | not_observed | not_observed | — | experimental_opt_in |
| **worker** | |  |  |  | | |
| `worker.private_journal_rollover`: Private terminal-job tombstones keep duplicate protection while bounding the active journal | fixture-tested | not_observed | not_observed | not_observed | — | experimental_opt_in |
| **control** | |  |  |  | | |
| `control.recognize_key_frames.other_variants`: recognizeKeyFrames image variant (one cropped audio-event thumbnail) | unsupported | — | — | indirect | Protect 7.3.60 bundle, Protect 7.2.105, AI Key 2.2.8 | default |
| `control.recognize_key_frames.multiple_images`: recognizeKeyFrames multipleImages (retroactive backfill of saved object crops) | native-verified | — | — | native-verified | Protect 7.3.60 bundle | explicit_opt_in |
| `control.host_management`: reboot, factoryReset, firmware install, SSH management, support upload and hardware statistics | unsupported | — | — | — | AI Key 2.2.8 | default |
| `control.ai_settings_commands`: changeAiInferAgentSettings, changeDescribePrompts, networkStatus and sshService | unsupported | — | — | indirect | — | default |
| `control.unknown_command`: Any other command name | unsupported | — | — | indirect | — | default |
| **framing** | |  |  |  | | |
| `framing.ucp_two_record`: Binary two-record JSON framing (type, format 1, uncompressed) | fixture-tested | indirect | indirect | indirect | AI Key 2.2.8 | default |
| `framing.compressed_or_other_format`: Compressed records or record format versions other than 1 | unsupported | — | — | — | AI Key 2.2.8 | default |
| `framing.request_deduplication`: Identical request IDs share a result; a reused ID with changed content is a protocol violation | fixture-tested | — | — | — | — | default |
| `framing.binary_only`: Text WebSocket frames are rejected | fixture-tested | — | — | — | — | default |
| `framing.event_messages`: Controller event messages | unsupported | — | — | — | — | default |
| **media** | |  |  |  | | |
| `media.video_export_mp4`: Opt-in MP4 adaptation of the verified AI processor video export route | native-verified | not_observed | native-verified | native-verified | Protect 7.2.105 | explicit_opt_in |
| `media.ubv_decoding`: Decoding raw UBV | unsupported | — | — | — | Protect 7.2.105 | default |
| `media.images_and_snapshots`: Image and snapshot inputs for session descriptions | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105 | default |
| `media.origin_and_redirect_policy`: Exact controller-origin allowlist, known paths only, no redirects | fixture-tested | — | — | — | — | default |
| **callbacks** | |  |  |  | | |
| `callback.on_demand_camera_upload`: On-demand result JSON at /internal/camera-upload/<token> | native-verified | not_observed | native-verified | not_observed | Protect 7.2.105, AI Key 2.2.8 | default |
| `callback.ram_full_event_tagging`: Full RAM event-tagging multipart callback with keyMomentsTags [] | native-verified | not_observed | native-verified | native-verified | Protect 7.2.105, AI Key 2.2.8 | default |
| `callback.ram_description_only`: Legacy description-only multipart profiles key-2.2.8 and protect-7.2.105 | unsupported | not_observed | not_observed | not_observed | Protect 7.2.105, AI Key 2.2.8 | explicit_opt_in |
| `callback.task_description`: Session description JSON at /internal/aiprocessors/descriptions/<taskId> | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105 | default |
| `callback.origin_allowlist`: Callbacks only to configured controller origins and known routes | fixture-tested | — | — | — | — | default |
| `callback.journal_and_uncertain_delivery`: Private job journal; completed callbacks deduplicated and uncertain callbacks never replayed | fixture-tested | — | — | — | — | default |
| **capabilities** | |  |  |  | | |
| `capability.explicit_disabled_flags`: Capability flags derived from served features; unserved ones sent explicitly disabled | fixture-tested | — | — | indirect | Protect 7.2.105 | default |
| `capability.ai_mode_basic`: aiMode reported as basic | fixture-tested | — | — | — | Protect 7.2.105 | default |
| `capability.support_ai_summary`: supportAiSummary advertised only with explicit opt-in and a configured caption path | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105 | experimental_opt_in |
| `capability.deep_mode_vlm`: supportDeepMode / supportVlm | unsupported | — | — | — | Protect 7.2.105 | default |
| `capability.face_recognition`: supportFaceRecognition advertised with local face recognition | fixture-tested | — | — | indirect | Protect 7.2.105 | explicit_opt_in |
| `capability.license_plate_recognition`: License-plate recognition | unsupported | — | — | indirect | Protect 7.2.105 | default |
| `capability.face_enhancement`: Automatic and manual face enhancement | unsupported | — | — | — | Protect 7.2.105, AI Key 2.2.8 | default |
| `capability.retroactive_processing`: supportRetroactiveProcessing opt-in for Find Anything backfill | fixture-tested | — | — | indirect | Protect 7.3.60 bundle | explicit_opt_in |
| `capability.recognize_anything_tagging`: Recognize Anything tags, detections and key-moment snapshots | unsupported | — | — | — | Protect 7.2.105, AI Key 2.2.8 | default |
| `capability.audio_speech`: Speech transcription (speechToText) | fixture-tested | — | — | indirect | Protect 7.3.60 bundle, AI Key 2.2.8 | explicit_opt_in |
| **callbacks** | |  |  |  | | |
| `callback.speech_to_text_transcriptions`: speechToText callback saved as native transcription rows | native-verified | — | — | native-verified | Protect 7.3.60 bundle, AI Key 2.2.8 | explicit_opt_in |
| `callback.face_recognition_part`: face multipart callback saved as native face thumbnails linked to person trackers | native-verified | — | — | native-verified | Protect 7.3.60 bundle | explicit_opt_in |
| **capabilities** | |  |  |  | | |
| `capability.ai_alarms`: AI query matches in Alarm Manager | unsupported | — | — | — | — | default |
| **database** | |  |  |  | | |
| `database.credential_rotation_hook`: PostgreSQL unifi-protect role rotated before management-password rotation | native-verified | — | — | native-verified | Protect 7.3.60 bundle, Protect 7.2.105, AI Key 2.2.8 | explicit_opt_in |
| `database.supports_db_credential_handoff`: supportsDbCredential console capability and controller access-rule update | needs_evidence | — | — | indirect | Protect 7.2.105, AI Key 2.2.8 | default |
| `database.controller_migrations`: Protect migrations and extensions applied to the processor database | native-verified | — | — | native-verified | Protect 7.3.60 bundle, Protect 7.2.105 | explicit_opt_in |
| `database.bm25_rerank`: pg_tokenizer, vchord_bm25 and rerank function | unsupported | — | — | — | Protect 7.2.105 | default |
| **search** | |  |  |  | | |
| `search.e5_nl_parse`: NL_PARSE with multilingual-e5-small returns a 384-value query embedding | fixture-tested | not_observed | not_observed | not_observed | Protect 7.2.105 | explicit_opt_in |
| `search.description_embedding`: 384-value passage embedding attached to task descriptions | fixture-tested | — | — | — | Protect 7.2.105 | explicit_opt_in |
| `search.legacy_clip_image`: 768-value CLIP ViT-L/14 NL_PARSE text vectors and IMAGE_SEARCH image vectors (basic Find Anything) | native-verified | — | — | native-verified | Protect 7.3.60 bundle, Protect 7.2.105, AI Key 2.2.8 | explicit_opt_in |
| `search.tags_and_time_filters`: NL_PARSE objectTypes and time window from the query text (keyTags stay empty) | native-verified | — | — | native-verified | Protect 7.3.60 bundle, Protect 7.2.105, AI Key 2.2.8 | explicit_opt_in |
| `search.native_retrieval`: Find Anything retrieval of processor results in Protect | native-verified | — | — | native-verified | Protect 7.3.60 bundle, Protect 7.2.105 | explicit_opt_in |
| `index.search_snapshots`: Key-moment regions indexed as keyMomentsTags search snapshots with local CLIP crop embeddings | native-verified | — | — | native-verified | Protect 7.3.60 bundle | explicit_opt_in |
| `index.thumbnail_tags`: thumbnailTags for existing smart-detect objects (thumbnailMeta and retroactive crops) | fixture-tested | — | — | not_observed | Protect 7.3.60 bundle | explicit_opt_in |
| `search.index_recovery`: Search index recovery after restart or model change | needs_evidence | — | — | — | Protect 7.2.105 | default |

## Missing evidence

- `discovery.udp_v1_query`: The discovered-device workflow used the macOS host companion; its exact UDP exchange was not individually recorded, and multicast through container networking is unverified
- `discovery.udp_v1_query`: NAS and other network layouts
- `discovery.udp_v1_query`: Exact native UDP query opcode and response were not recorded in public evidence
- `discovery.udp_v1_query`: Full UDP packet schema and the layer-2 discovery-to-record path
- `discovery.other_opcodes`: Whether Protect 7.3.x requires any of these fields
- `adoption.management_info_post`: The individual 7.3.56 request and response were not recorded; only the surrounding adoption succeeded
- `adoption.management_adopt`: Fresh adoption on 7.3.60
- `adoption.management_adopt`: Adoption after removing the device from Protect (forget and re-adopt)
- `adoption.layer3_host_adoption`: Whether any tested Protect flow uses layer-3 adoption for AI Key
- `adoption.factory_enrollment`: Enrollment on 7.3.60
- `adoption.generated_password_flow`: A Protect UI or API flow that submits a custom password; the tested 7.3.56 UI had no such field
- `adoption.readoption_refused`: Controller behavior when an adopted processor is forgotten and offered again
- `control.connection_headers`: The actual accepted header set and its validation were not separately recorded on either version
- `control.profile_ucp4_negotiated`: A live controller that returns ucp4; the 7.3.56 frontend returned no subprotocol
- `control.profile_device_service`: Which control profile the 7.3.60 trial configuration used is not in the public records
- `lifecycle.credential_rotation`: Rotation with search enabled and a real PostgreSQL role
- `lifecycle.reconnect_after_restart`: Reconnect after a controller restart or controller upgrade
- `lifecycle.reconnect_after_restart`: Longer outage and recovery testing
- `lifecycle.abnormal_closure_backoff`: Real network interruption against Protect
- `lifecycle.protocol_violation_close`: How Protect reacts to a 1002 close from an AI processor
- `lifecycle.unknown_controller_version`: The exact protectVersion value, if any, sent by 7.3.56 or 7.3.60 in setConsoleInfo was not recorded
- `lifecycle.controller_upgrade` (needs_evidence): The public records do not state whether the 7.3.60 trial used an adoption carried over from 7.3.56 or a fresh adoption
- `lifecycle.controller_upgrade` (needs_evidence): A deliberate upgrade test with before/after adoption state
- `lifecycle.emulator_upgrade` (needs_evidence): No release-to-release upgrade exists yet; state schema 1 has no migration path (issue #24)
- `control.get_info`: The getInfo exchange and the capability state Protect stored were not recorded on either live version
- `control.get_task_queue_info`: Whether Protect schedules differently from these counts
- `control.set_console_info`: The live body shape; a 7.3.x controller sending an extra field would currently receive errorCode 22
- `control.set_info`: Live body shape
- `control.update_timezone`: Live body shape
- `control.request_ai.on_demand_inference`: Unscoped or repeated on-demand operation
- `control.request_ai.on_demand_inference`: Other camera families
- `control.request_ai.describe`: Whether 7.3.60 dispatches /describe at all (issue #10)
- `control.request_ai.describe`: promptProfile session-v1 behavior is not reproduced
- `control.request_ai.unknown_target`: Protect's retry treatment of errorCode 95 versus 5 for RequestAI
- `control.request_ai.second_stage_verification`: A live reverification task and its saved result: enabling it changes live event classification (saveReverification retypes matched thumbnails and objects), so it needs the owner's approval
- `control.request_ai.second_stage_verification`: Zero-shot accuracy on real low-confidence crops is not measured
- `control.recognize_key_frames`: Continuous and all-camera operation (issue #12)
- `control.recognize_key_frames`: Persistence after a controller restart
- `control.continuous_caption_admission`: Native Protect dispatch and persistence under continuous admission
- `control.continuous_caption_admission`: Preservation of existing native recognition tags on additional model families
- `control.continuous_caption_admission`: Fair scheduling and a multi-day live endurance run
- `worker.private_journal_rollover`: Multi-day live endurance and disk-full recovery
- `worker.private_journal_rollover`: Operator retention and backup policy for private tombstones
- `control.recognize_key_frames.other_variants`: Dispatched live on 7.3.68 for audio events (216 in one morning), but the thumbnail has no tracker ID, so no object match for an embedding is established
- `control.recognize_key_frames.multiple_images`: Crops of older events can be gone (HTTP 404); those events stay unindexed
- `control.recognize_key_frames.multiple_images`: 39 events whose tasks were dropped at a mid-run Key swap stay unindexed; Protect does not retry timed-out tasks
- `control.ai_settings_commands`: These names appear in the device diagnostic allowlist, but no public record states their source or when Protect sends them
- `framing.ucp_two_record`: No raw native frame capture or independently recorded two-record layout
- `framing.compressed_or_other_format`: Whether any Protect version sends compressed or other-format records
- `framing.request_deduplication`: Whether Protect ever retries with the same request ID
- `framing.event_messages`: Which events Protect sends to AI processors and whether any need handling
- `media.video_export_mp4`: The public record confirms MP4 for the on-demand job; the automatic event's export format is not recorded
- `media.images_and_snapshots`: Any live image or snapshot job
- `callback.ram_full_event_tagging`: Persistence after a controller restart
- `callback.ram_full_event_tagging`: Repeated or simultaneous jobs under continuous operation
- `callback.ram_full_event_tagging`: Effect on events that already carry native tags
- `callback.task_description`: A live controller task ledger entry; unknown task IDs are dropped after HTTP 200
- `callback.journal_and_uncertain_delivery`: Exactly-once delivery is not claimed
- `callback.journal_and_uncertain_delivery`: Controller-side retry behavior for AI Key tasks
- `capability.explicit_disabled_flags`: The capability state Protect displayed or stored on either live version
- `capability.ai_mode_basic`: How 7.3.x interprets aiMode
- `capability.support_ai_summary`: Whether supportAiSummary was advertised as enabled during the 7.3.60 caption trial is not in the public records
- `capability.deep_mode_vlm`: Whether 7.3.x still couples the two flags
- `capability.face_recognition`: Fresh native readback after the native-face skip (27 Sep 00:21 UTC): new Wohnzimmer events with camera faces and no new AI Key faces
- `capability.face_recognition`: AI Key faces carry no faceEmbed, so Protect puts each in its own group (65 singleton groups on 26 Sep); grouping needs Protect's camera-model embedding, which the Key cannot produce
- `capability.face_recognition`: Named recognition needs an owner-named group and an eligible legacy camera; none is eligible today
- `capability.license_plate_recognition`: An eligible camera and an owner decision (issue #19)
- `capability.face_enhancement`: Native contract (issue #23)
- `capability.retroactive_processing`: The stored run completed on an empty batch query at an 18 Sep 19:10 cursor although 2109 older tracker-crop events have no RAM task state; the cause needs Protect's aiprocessorTasks rows or 7.3.68 runner source
- `capability.retroactive_processing`: Protect does not allow continuing older events once a run is completed without re-adopting the AI Key (start requires not_started on every processor; nothing resets it)
- `capability.recognize_anything_tagging`: Structured object results (issue #14)
- `capability.audio_speech`: Speech on AI Port-paired legacy cameras, which raise no alrmSpeak
- `capability.audio_speech`: The capability flag itself is never native-verified by the matrix rules; the transcripts it enables are (callback.speech_to_text_transcriptions)
- `callback.speech_to_text_transcriptions`: Persistence after a controller restart
- `callback.speech_to_text_transcriptions`: Non-German speech, noise and overlapping speakers on live events
- `callback.speech_to_text_transcriptions`: Search by transcript text in the Protect UI
- `callback.face_recognition_part`: Named matches (owner must name a face group)
- `callback.face_recognition_part`: Persistence after a controller restart
- `capability.ai_alarms`: Native contract (issue #26)
- `database.credential_rotation_hook`: Protect only re-sent the current password; a change to a genuinely new value was not observed
- `database.credential_rotation_hook`: Pending/uncertain rotation recovery on a live controller
- `database.supports_db_credential_handoff` (needs_evidence): setConsoleInfo stored supportsDbCredential true on 7.3.68, but the access-rule behavior 7.3.x expects from it is not established
- `database.controller_migrations`: Migration replay after a controller upgrade or restart
- `database.controller_migrations`: Hybrid search objects (pg_tokenizer, vchord_bm25) are not installed
- `database.bm25_rerank`: Whether 7.3.x requires hybrid search
- `search.e5_nl_parse`: On 7.3.68 basic Find Anything uses CLIP (verified); E5 is used only by deep session search (encodeSessionSearchQuery), not observed live
- `search.e5_nl_parse`: Encoder compatibility with vendor vectors
- `search.description_embedding`: Document preprocessing used by the vendor
- `search.description_embedding`: Native retrieval with positive and negative examples
- `search.legacy_clip_image`: Vectors come from the project's own local CLIP ViT-L/14 export; equivalence with the vendor encoder is not established, so only indexes built by this Key are compatible
- `search.legacy_clip_image`: Protect 7.3.56 and 7.3.60 not observed
- `search.legacy_clip_image`: Tag extraction and time filters remain unsupported
- `search.tags_and_time_filters`: keyTags need Protect's ramTags vocabulary, which is not established; none are sent
- `search.tags_and_time_filters`: Only English and German phrases; no model-based parsing
- `search.native_retrieval`: Retrieval quality benchmark with labelled positives and negatives (issue #7)
- `search.native_retrieval`: Persistence of the search host across a controller restart
- `search.native_retrieval`: Deep (E5 session) and hybrid retrieval
- `search.native_retrieval`: A real host reboot of the Mac search host (relay kill and login reload were tested)
- `index.search_snapshots`: Face-camera recognition tasks go to local faces and are not indexed
- `index.search_snapshots`: Audio-event image tasks are not indexed
- `index.search_snapshots`: Effect of extra snapshot thumbnails on event presentation is not reviewed by the owner
- `index.thumbnail_tags`: A live task with in-export thumbnailMeta objects or a retroactive multipleImages task
- `search.index_recovery` (needs_evidence): Controller reconciliation writes null embeddings and does not regenerate them (7.2.105 static)
- `search.index_recovery` (needs_evidence): Live recovery test after a controller restart (only the Mac relay restart was tested on 7.3.68)
- `search.index_recovery` (needs_evidence): A live staged rebuild needs an approved object-crop source; the AI Key receives crops only inside Protect tasks
- `search.index_recovery` (needs_evidence): Native validation of a rebuilt index is possible only after cutover (Protect searches only public.ramDetections)

## Contradictions and stale records

- **C1** (target_version): Several records name Protect 7.3.56 as the target, while issue #2, PLAN.md and the caption trial use 7.3.60. No controller package for either 7.3.56 or 7.3.60 was inspected; static evidence is from 7.2.105. Resolution: Manifest records results per version. Neither live version is a static reference.
- **C2** (stale_record): Earlier docs/device-contract.md text said native camera descriptions and persistence were unverified; later Protect 7.3.60 trials recorded one on-demand description and two automatic captions on distinct camera families after page reload. Resolution: The device contract now separates the earlier adoption evidence from the later caption acceptance record.
- **C3** (stale_record): adoption-evidence.md says acceptance by an unmodified running controller remains needs_evidence; native adoption was later observed on 7.3.56. Resolution: The static record is dated and version-scoped; the live 7.3.56 source supersedes it for adoption only.
- **C4** (version_scope): worker-contract.md says MP4 export adaptation has not been tested against 7.3.56; README.md says 7.3.60 accepted it for the on-demand job. Resolution: Both can be true. Recorded as native-verified on 7.3.60 only.
- **C5** (static_contract): AI Key 2.2.8's description-only sender omits cameraId; the Protect 7.2.105 description-only schema requires it. Resolution: Kept as two explicit callback profiles. Runtime rejection by the middleware was never exercised.
- **C6** (static_contract): AI Key 2.2.8 NL_PARSE returns 768-value CLIP embeddings and has no /describe route; Protect 7.2.105 session search requests multilingual-e5-small and rejects non-384 vectors. Resolution: The two packages are not a confirmed matched pair. Search status stays needs_evidence pending issue #10.
- **C7** (static_vs_live): The 7.2.105 verifier accepts only ucp4 or updates as the requested subprotocol, but the live 7.3.56 frontend returned no selected subprotocol, so the strict ucp4 profile would fail there. Resolution: Separate device-service profile, verified on 7.3.56 with a required pin.
- **C8** (static_internal): An inference-gateway comment calls the device certificate NVR-issued; syswrapper.sh generates a self-signed RSA-2048 certificate. Resolution: The generation code is treated as evidence; the comment is not.
- **C9** (vendor_docs): The AI Key FAQ lists 1,000 detections per hour; the technical specifications list 1,800. Resolution: No throughput claim is made. Measure actual behavior.
- **C10** (vendor_docs): Audio eligibility differs between the AI Key FAQ and the camera capability guide. Resolution: Audio is unsupported; eligibility needs evidence per camera.
- **C11** (misleading_label): lab-results.json reports native_protect_7_3_56 as needs_evidence although native adoption was observed on 7.3.56. The field describes the synthetic lab run, not project evidence. Resolution: Treat lab output as synthetic only. The lab's schema is outside this issue.
- **C12** (static_contract): Protect 7.2.105 fills missing capability flags with enabled values, so an absent flag reads as support. Resolution: Unsupported capabilities are sent explicitly disabled. A capability flag is never treated as evidence.
- **C13** (stale_record): The 26 Sep morning record said basic Find Anything was blocked on a search host and listed search as needs_evidence; the same day the Key became Protect's search host and native text and image search results were read back. Resolution: The record keeps the blocker as history; the manifest now records search on 7.3.68 only.
- **C14** (static_contract): Sending face, plate and object-indexing flags explicitly disabled (C12) made Protect show Speech to Text, Face and License Plate Recognition as Off although native transcripts and face results existed; supportTts, not a speech flag, drives Speech to Text. Resolution: Flags follow the configured served features; unserved ones stay explicitly disabled. Flags remain configuration, never evidence.

## Experimental activation during live trials

Every live trial must record each experimental activation below that was in effect, with the exact Protect version, camera family and permit ID. A capability flag or UI toggle is configuration, not acceptance evidence.

- `live-protect-7.3.56`: `device.factory_enrollment_until`: enabled for a bounded window, then disabled after pairing.
- `live-protect-7.3.56`: `controller.control_profile`: device-service.
- `live-protect-7.3.60`: `worker.test_scope (on_demand)`: single-use permit for one camera, consumed.
- `live-protect-7.3.60`: `worker.test_scope (recognizeKeyFrames)`: single-use permit for one G5 Flex camera, consumed.
- `live-protect-7.3.60`: `worker.test_scopes (recognizeKeyFrames)`: separate single-use G4 Instant permit consumed; separate G4 Bullet permit remained unused.
- `live-protect-7.3.60`: `worker.request_mp4_exports`: enabled for the on-demand job.
- `live-protect-7.3.60`: `device.feature_flags.supportAiSummary`: not in public records (needs_evidence).
- `live-protect-7.3.60`: `Protect recognizeAnythingSettings / aiSummarySettings for the test camera`: not in public records (needs_evidence).
- `live-protect-7.3.60`: `controller.control_profile`: not in public records (needs_evidence).
- `live-protect-7.3.60`: `vision model identifier`: not in public records (needs_evidence).

## Remaining native tests for the integration owner

- **N1** (`lifecycle.unknown_controller_version`, `control.set_console_info`, `database.supports_db_credential_handoff`): On the adopted test processor, read device health after connection and record compatibility.controller_version_evidence plus the setConsoleInfo result code from control_commands. Confirm whether 7.3.60 sends only the four allowlisted controller fields.
- **N2** (`control.get_info`, `capability.explicit_disabled_flags`, `capability.support_ai_summary`, `capability.deep_mode_vlm`): Record the capability state Protect shows for the processor, and the exact device.feature_flags in effect, before any caption trial.
- **N3** (`lifecycle.controller_upgrade`): Record adoption state before and after an in-place Protect upgrade; confirm reconnect without re-adoption.
- **N4** (`adoption.management_adopt`, `adoption.time_sync_confirmation`, `lifecycle.credential_rotation`): Fresh adoption of a separately identified test processor on 7.3.60, including rotation and planned restart.
- **N5** (`lifecycle.protocol_violation_close`, `control.request_ai.unknown_target`): Only if a malformed or unsupported command occurs naturally: record the close or error code and whether Protect retries. Do not inject traffic into the production controller.
- **N6** (`control.recognize_key_frames`, `callback.ram_full_event_tagging`): Repeat the persisted-caption check after a controller restart with a new reviewed permit. The second camera-family check passed on G4 Instant by native panel and full page reload; an exact-event GET remains open for that family.
- **N7** (`control.request_ai.describe`, `search.e5_nl_parse`, `search.native_retrieval`): Issue #10: determine whether 7.3.60 dispatches /describe and which search path it uses, before any search trial.
- **N8** (`lifecycle.abnormal_closure_backoff`): Interrupt the network path briefly and record reconnect timing and adoption state.
- **N9** (`control.continuous_caption_admission`, `worker.private_journal_rollover`): In an isolated, reviewed rollout, confirm fresh model-family inventory, Protect-side dispatch, persistence, budget exhaustion, additions/removals, reconnect and journal rollover. Keep richer native-AI models excluded until tag preservation is verified.
- **N10** (`capability.retroactive_processing`, `control.recognize_key_frames.multiple_images`, `index.thumbnail_tags`): Completion path established (empty batch query at 19:44-19:45 UTC on 26 Sep; 84 failed pushes, none in the final cycle). Remaining: explain why 2109 older tracker-crop events without RAM task state were not selected (needs Protect's aiprocessorTasks rows or 7.3.68 runner source). Continuing them requires a new processor record (re-adoption), which is out of scope.
- **N11** (`search.native_retrieval`, `search.index_recovery`, `database.controller_migrations`): After a planned controller restart, confirm isSearchHost true, that existing ramDetections rows survive, and that a known positive text search still returns its object.
- **N13** (`control.request_ai.second_stage_verification`): Owner decision: enable find_anything.reverification for a bounded window, then read back one reverified event's detectedThumbnails (preReverificationObjectType and confidence) and confirm unsure verdicts left events unchanged.
