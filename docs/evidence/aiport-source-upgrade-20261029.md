# AI Port source upgrade and native continuity — 29 October 2026

All four existing NAS AI Ports were individually upgraded to reviewed source
`de24c9300ec1a6bfc09ee8a69fe55276d5c5d14a` (through #152). This proves bounded
deployment/reconnect, not recognition quality, native event persistence,
complete restore, release approval or sustained operation.

## Candidate before live changes

- Linux AMD64 image:
  `sha256:1d115b2d86d553669043977d0bc529e51386829e8f8b91653a73063e7e9b83f5`.
- Public-source archive SHA-256:
  `151636ce6ada27833ab54c20e5e0d36f63190eba4ba1a074b2412441eac98d12`.
  Build context contained no private state, media or model weights. No weights
  were downloaded; build limits were one CPU and 4 GiB memory.
- Network-less, read-only configuration/identity, application-constructor and
  API-factory checks passed for all four slots; no services or API requests
  were started. Existing local face and sound ONNX graphs loaded for each slot.
  Graph loading is not recognition quality or enrollment acceptance.
- Runtime: aiohttp 3.14.5, Pillow 13.0.0, cryptography 50.0.1, ONNX Runtime
  1.28.0, NumPy 2.5.4. Installed project license matches the root Apache-2.0
  text; other provenance/model-license gates remain separate.
- Existing OpenAI primary, private-IP fallback and uncapped primary policy
  were preserved. No standalone paid/provider test was run; ordinary
  existing-camera processing continued.

## Serialized transaction and backups

Each effective Compose stage differed only in the selected service's image.
Other services, identities, addresses, camera assignments, mounts, UID/GID,
capabilities, read-only root, resource limits and restart policy were preserved.

The old worker can retain threaded provider work after asyncio cancellation.
Therefore an empty pending queue alone was not a stop gate. Only the selected
container's FFmpeg producers were paused by exact PID/start-time identity;
stdout pipes were checked empty without consuming bytes, decoded frames stayed
stable for at least one second and model work finished. Then only that Port
was stopped, its complete state cold-copied, and its service recreated with
`--no-deps --pull never`. Application and controller checks preceded the next slot.

A temporary bounded watchdog resumed matching owned producers on owner loss or
lease expiry. Separate network-less/state-less/model-less synthetic FFmpeg tests
verified pause/drain/resume, wrong-start-time refusal and both recovery paths.
This is not host-crash or general unattended-recovery qualification.

| Slot | Paired cameras | Cold archive bytes | Regular files | Uncompressed file bytes |
| --- | ---: | ---: | ---: | ---: |
| 1 | 3 | 7,578 | 32 | 22,255 |
| 2 | 2 | 335,561,004 | 65 | 366,316,847 |
| 3 | 2 | 9,843 | 61 | 29,631 |
| 4 | 2 | 9,971 | 64 | 31,353 |

All archive members/file bytes were read and gzip CRC verified. A later
independent read rechecked archive hashes, sizes and mode 0600. Original file
ownership, including one readable root-owned file, was retained. No application
state was chowned. Archives and old images remain private and retained.

## Failed validator and executed rollback

The first slot-1 attempt adopted/reconnected and processed all three cameras,
then the physical comparator rejected a changed `aikey_backend.MacAddress`.
The original failure and cold backup are preserved. The old image was restored
with **current** state/credentials, not an old credential snapshot; all three
cameras processed again. Original Compose bytes, core hashes and native
identities/pairings matched. Other Ports and the AI Key stayed active.

Inspection proved that this is an unconfigured Docker-generated internal-bridge
MAC: original, candidate and recreated **old** container each had different
values. LAN macvlan MAC, IPs and other checked physical fields were unchanged.
The corrected validator excludes only this one field after proving bridge driver
and absence of a per-network MAC pin. Four negative controls still reject LAN
MAC, backend IP, UID or capability changes. A separately recorded second slot-1
attempt and the other slots passed. This proves one **old-image/current-state**
rollback, not full backup restoration or host/controller reboot.

## Independent final read-back

- All four exact candidate images; eight installed modules each match reviewed
  source: candidate, inference, API detection, camera engine, smart settings,
  ingest, provider HTTP and network handling.
- Original configuration, certificate, private key and controller-CA hashes
  match. Declared physical settings and mounts match; only the unconfigured
  generated bridge MAC is excluded.
- Four adopted/controller-connected Ports; all nine assigned cameras have
  active streams, enabled policies and successful processing. No failed camera,
  model-load failure, OOM, restart loop or paused producer at this snapshot.
  Processing-success counters include motion-gated empty results; they are
  neither paid-request counts nor proof of detections for every camera.
- Authenticated Protect **7.3.74** bootstrap after each slot matches the original
  in-browser baseline: same four Port IDs/MACs, nine pairing strings and AI Key
  ID/MAC; four Ports and one Key adopted/connected. Eleven cameras / ten connected
  is unchanged. Eleven `isRecording` flags do **not** prove playback continuity.
- AI Key/PostgreSQL remain running with the existing image and shared namespace;
  neither service was redeployed in this transaction. See [Key upgrade](nas-source-upgrade-20261029.md).

Raw IDs, credentials, recordings, captions, recognition templates and private
receipts were not exported. The native identity baseline remained in the
authenticated browser. Private receipts retain original failures, rollback,
per-slot transactions and independent read-back.

## Still `needs_evidence`

Native event issuer/camera association and persistence, native test alarm,
inside/outside-zone ground truth, representative pre-tuning quality gates,
recognition/enrollment quality, full restoration, host/controller recovery,
72-hour soak and seven-day pilot remain unproved. Historic missed-visit causes
are not inferred from this upgrade. Per-minute diagnostics are available for
future bounded observations; no sensitivity change or staged paid test occurred.
ONVIF remains open by owner instruction because no test camera is available.
