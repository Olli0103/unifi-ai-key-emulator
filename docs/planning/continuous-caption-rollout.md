# Two-family continuous-caption rollout (#12): reviewable plan

Status: **proposal, not activated.** Writing this changed no setting, scope, service, archive or provider state. Figures are counts, fixed codes, digests and placeholder IDs. Captions and transcripts stay private.

Each action below is **one approval**. Each lists exactly what it reads, writes, how to check it and how to undo it. Nothing else is written. Steps run in order, and each starts only after the previous one's check passed.

## What the gates are (traced 27 Sep)

- **The caption preflight is advisory.** No runtime path reads its `ready` flag; only the CLI and the control-site page display it. It cannot deadlock activation. Each blocker carries a phase (`blocker_phases`), and `ready_to_activate` is true when no *precondition* blocker remains:

  | Blocker | Phase | Cleared by |
  |---|---|---|
  | `uncertain_callbacks_pending`, `ledger_above_80_percent`, `budget_journal_needs_review`, `key_health_missing` | precondition | before activation |
  | `continuous_not_configured`, `one_use_scopes_configured` | activation | the reviewed config change itself (Action 4) |
  | `native_readback_missing` | acceptance | native Protect readback after activation |

- **Hard runtime gates** (code, enforced on every task):
  - config validation;
  - a fresh camera inventory with the model family and the camera-ID pin;
  - the controller-reported version in {7.3.60, 7.3.68} (36db45b);
  - the global budget: 12 per rolling hour, fair hold, reservation before media or inference;
  - the job and archive replay guards.

  No new first-trial path is needed: nothing deadlocks.
- **No double charge:**
  - a failed caption is refused on replay in continuous mode, including after rollover to a `failed` tombstone;
  - a `callback_uncertain` caption is refused and never archived;
  - a reservation without a journal record is refused.

  Proven by `tests/test_caption_no_double_charge.py` (4 tests: no new provider request, callback or reservation) and the existing orphaned-reservation test. Uncertain attempts are not refunded.

## Current state (read-only, 27 Sep)

- **AI Key:** adopted and connected to Protect 7.3.68, which `setConsoleInfo` reports. The live image is the 9de93fd lineage, without 36db45b.
- **Preflight:** 2 one-use scopes configured, all 5 permits consumed; no budget journal (12/12 available); journal 597/1024.
  - Blockers: `continuous_not_configured` (activation), `one_use_scopes_configured` (activation), `uncertain_callbacks_pending` (precondition), `native_readback_missing` (acceptance).
- **The 54 `callback_uncertain` records:** all `indexImages` (local CLIP, no provider cost), updated 21–22 h ago.
  - A fixed read-only SELECT on the search database finds embedded rows for all 54 job hashes, which means Protect stored their results.
  - `aikey-uncertain-resolution plan` answers: 54 `archive_as_completed`, digest **`92bc7c3b948ca7596ccfd961b31d191acc5dfb3a78e846956ffa39454a90a223`**.
  - The digest covers only the uncertain records. The Key's normal journal activity doesn't change it; a new or changed uncertain record does.

## Event provenance of the two families (#9, 27 Sep)

- **G6 Instant (Wohnzimmer):** unpaired. Protect lists its own hardware set (5 smart and 9 audio types), and its smart events are onboard. Its earlier native caption (26 Sep) used the same path this rollout would use.
- **G5 Flex (Büro):** AI Port-paired since 23–24 Sep. While paired, Protect lists the AI Port's set for it (4 smart, 0 audio types), and its smart events come from the AI Port. Every paired camera saved none during the 26–27 Sep provider outage, and the Mac port's entered-event count equals Protect's saved count on its cameras.
- **The G5 Flex caption evidence (22 Sep) predates the pairing.** A caption on an **AI Port-sourced** event has not been read back natively for any family; this rollout's G5 Flex half would be the first test of that path.
- **Dispatch is proven:** since the restore, Protect sent key-frame tasks for Büro's AI Port events, and 2 index rows were created from 2 events.
- **Exact-event check (read-only, 27 Sep; counts and event-ID hash prefixes only).** 21 Sep to now, 1,932 Büro events, no capped slice:
  - **exactly one persisted caption ever**, `05b4e75d` (22 Sep 16:56 UTC), before the pairing;
  - after Büro joined the AI Port pool (24 Sep 07:03 UTC): 128 smart events, all with a RAM task (`ramState` done 16, failed 112), and **0 persisted captions**. No caption scope covered Büro then.

  So Protect dispatches RAM tasks for AI Port-sourced G5 events, but a caption saved from one is **unproven**.
- **G6 control:** exactly one persisted caption, `e7fb569b` (26 Sep 05:05 UTC), the same onboard path this rollout uses.

## Budget, ceiling and abort checks (reconciled)

- **Budget:** 12 reservations per rolling hour across the pinned cameras, so at most **288 per 24 h** (`DAILY_CEILING`). The budget journal keeps 24 h and holds up to 300 records.
- **Fairness:** with two cameras, one can take at most 11 while the other is unserved; its 12th attempt is `deferred_fair_share`. After both are served, the 13th in the hour is `exhausted`, before media or inference.
- **Replay of the last 24 h** of real smart events (192) through this rule: **109 admitted** (G6 91, G5 18), 34 exhausted, 49 deferred. This is an upper bound: one caption task per smart event is unverified.
- **Stage 4a (G6 only):** with a single eligible camera the fair hold is zero, so G6 may use all 12. The same 24 h replay for G6 alone (186 events): 112 admitted, 74 exhausted, at most 12 per rolling hour.
- **Each admission** is one request to the pinned OpenAI `gpt-6-luna`, at most 4,096 output tokens.
- **Abort checks come from the budget journal and health, not process counters.** Health's `captions.admitted` counts since process start and is not per hour. The preflight's `abort` section (read-only) reports:
  - `budget_last_hour` / 12, raising `hourly_budget_exceeded`;
  - `budget_24h` / 288, raising `daily_ceiling_exceeded`;
  - `caption_jobs_without_reservation`, raising `caption_without_reservation`;
  - `eligible_cameras` versus `pinned_cameras`, raising `scope_wider_than_pin`.

  Any abort code means roll back (Action 5 rollback).

## Action 1: archive the 54 reviewed index callbacks

**Status: DONE, 28 Sep 2026 04:01 UTC, approved by Olli.**
- `apply` returned `{"archived": 54, "already_archived": 0, "records": 54}` against digest `92bc7c3b…a223`, with the Key held, stopped (independently verified) and restarted on the same container and image.
- Backup: `state/apple-uncertain-backup-20260928T040127Z/` (0700; 54 files at 0600 plus a checksummed manifest; git-ignored).
- **Verified afterwards:**
  - 54 valid `completed` tombstones and 0 of the records left active; journal 531 → 475 (plus natural rollover), tombstones 2,693 → 2,749;
  - preflight `uncertain_callbacks_pending` gone, leaving only activation and acceptance blockers;
  - Key adopted, connected, search connected and idle;
  - `config.json` and `search-profile.json` unchanged; index rows 4,645 unchanged; native class search 100/78/78/7 unchanged;
  - supervisor pin unchanged and hold released; four AI Ports connected (3/2/2/2), nine streams decoding.
- `device-state.json` changed only through Protect's routine reconnect credential rotation (`changeUserPassword`, result 0, as on every connection): no schema backup, and the same identity keys.
- Rollback remains available with that backup (`rollback --key-stopped`).

The checklist below is kept as the record of what was run.

**Current preflight (read-only, 27 Sep ~21:26 UTC); the digest is still valid.**
- `aikey-uncertain-resolution plan` recomputed against the live journal, archive and search DB:
  - 54 records, all `indexImages` (local CLIP, no provider cost), all `archive_as_completed`;
  - digest **`92bc7c3b948ca7596ccfd961b31d191acc5dfb3a78e846956ffa39454a90a223`**, identical to the reviewed one, so the set has not changed;
  - evidence: 2,412 job hashes with embedded rows (it grows naturally), which include all 54;
  - 0 of the 54 already have a tombstone; their 49 archive buckets exist and are private.
- **Key:** adopted, connected, search connected; worker queued, active and pending 0; journal 567 entries (completed 401, failed 112, `callback_uncertain` 54).
- **Filesystem:** `worker-jobs/` and `worker-archive/` are 0700, owned by the user, not symlinks; 256 buckets, 0 unsafe, 2,638 tombstones; 420 GiB free.
- **Supervisor:** `aikey-mac` running, no hold.
- **If any of this differs at approval time, do not apply.** Re-run `plan`: a new digest makes this plan stale and needs a new review.

**Approve:** exactly this sequence. Each step must pass before the next; any failure stops with nothing written by the tool.

| # | Step | Pass condition | On failure |
|---|---|---|---|
| 1 | `aikey-uncertain-resolution plan --state state/apple --evidence-container local-postgres-search` | digest = `92bc7c3b…a223`, 54 × `archive_as_completed` | stop; plan is stale |
| 2 | Key health | adopted, connected, worker 0/0/0 | wait; do not stop a busy Key |
| 3 | `local-apple-supervise hold aikey-mac --minutes 20`, then `container stop local-aikey-mac-findanything18` | hold recorded; stop exit 0; `container ls -a` shows it **stopped** (`--key-stopped` is an attestation the tool does not verify) | release the hold; stop |
| 4 | `aikey-uncertain-resolution apply --state state/apple --evidence-container local-postgres-search --backup state/apple-uncertain-backup-<stamp> --approve 92bc7c3b…a223 --key-stopped` | output `{"archived": 54, "already_archived": 0, "records": 54}` | restart the Key (step 5) with the journal unchanged, or resume or roll back as below |
| 5 | `container start local-aikey-mac-findanything18`, then `local-apple-supervise release aikey-mac` | ready: adopted, connected, search connected | roll back (below), then start |
| 6 | Preflight and health | `uncertain_callbacks_pending` gone; journal 567 → 513; 54 new tombstones; worker idle | roll back |

**What is written, and only this:**
1. **Backed up first:** a new directory `state/apple-uncertain-backup-<stamp>/` (0700) with 54 byte copies of the journal records (0600) plus `manifest.json` (digest, job hash, SHA-256 per file), all fsynced before any journal change.
2. **Created:** 54 tombstones `worker-archive/<xx>/<job>.json`: `state: completed`, the same shape the worker writes and reads.
3. **Removed:** the same 54 files from `worker-jobs/`, each only after its tombstone is linked and fsynced.

Nothing in Protect, the search index, the budget, the config, the AI Ports or any other journal record changes.

**Aborts before any write** (fixed codes):
- `key_running` (the flag is missing);
- `plan_changed` (digest or any file checksum differs from approval);
- `backup_not_empty`;
- `journal_missing`, `unsafe_file`, `unsafe_directory`;
- `evidence_query_failed`;
- caption or other paid jobs are never selected (`not_local_index`).

**Stops mid-run with a partial result:** `tombstone_conflict`, `record_changed`, `record_missing_without_tombstone`, `backup_corrupt`. Resume or roll back as below.

**Retry:** rerun step 4 with the same backup directory. Finished records count as `already_archived`, and a record interrupted between tombstone and unlink is completed. A different digest against that backup is refused (`backup_belongs_to_another_plan`).

**Restore (undo):** with the Key stopped and held, run `aikey-uncertain-resolution rollback --state state/apple --backup state/apple-uncertain-backup-<stamp> --key-stopped`, then start and release.
- It restores the 54 records byte for byte (checksums verified), removes only tombstones equal to the ones it wrote, and is idempotent.
- It stops on `backup_corrupt` or a foreign `tombstone_conflict`.

## Action 2: deploy the admission and #1 fixes to the AI Key (no config change)

**Status: DONE, 28 Sep 2026 04:47 UTC, approved by Olli.**
- **Recheck:** the live image and the recomputed 10-module delta matched this plan exactly.
- **Image:** `local-aikey:parity-a2-20260928`, `FROM local-aikey:findanything18-rollover-20260927`. Exactly those 10 modules differ, each equals HEAD, and the entry points import.
- **Swap:** `local-apple-upgrade swap --kind aikey` → `swapped`, after waiting about 1 min for idle. It derived the documented run arguments. The pin is `local-aikey-mac-parity-a2`; `local-aikey-mac-findanything18` is kept stopped for rollback. No abort condition occurred.
- **After the swap:**
  - Key adopted, connected, search connected; manifest `.15`;
  - `config.json` and `search-profile.json` unchanged; `device-state.json` changed only through Protect's routine reconnect `changeUserPassword`, with no schema backup;
  - index rows 4,671 → 4,673 (natural); class search 100/86/78/7 → 100/86/79/7;
  - 0 `callback_uncertain`; no caption budget journal; permits unchanged (5), so no caption or provider charge;
  - 4 AI Ports connected (3/2/2/2), 9 streams decoding.
- **B2 native evidence:** G6 event `dd25a62f` (05:24:44) was answered by a face job under its own `recognizeFaces` ID and ended RAM `done`. `job_identity_conflict` and `video_contract` stayed at 0 over 40 minutes.
- **B1 still needs_evidence:**
  - Giebel Vorn had no natural smart event yet.
  - **The G6 cannot gain index rows with this code:** as the only face-recognition camera, its tasks carrying face or person regions go to the face path, which answers faces but builds no Find Anything tags. That is a separate code gap (#1 follow-up), not a deploy failure.


**Live image today:** `local-aikey:findanything18-rollover-20260927`, container `local-aikey-mac-findanything18`, pinned by the supervisor. Its `aikey` modules hash-match **9de93fd**, except `worker.py` (plus the cookie and rollover hunks) and some AI Port, control-site and Whisper modules the Key process does not import.

**Deploy delta** (the Key runtime's import closure from `aikey.cli`/`aikey.runtime`, including lazy imports, that differs from the live image). Exactly these modules at the approved commit:
`aiport_ingest`, `camera_inventory`, `camera_registry`, `caption_budget`, `config`, `device`, `protocol`, `state_schema` (new), `worker`, `worker_archive` (new).

`caption_preflight` and `uncertain_resolution` are host-side operator tools; they are not part of the image. `tests/test_key_deploy_delta.py` pins this list against the runtime closure.

**Behavior changes with the current config** (continuous off, 2 consumed one-use scopes, faces on the G6, 10 index cameras, speech on 1 camera):

| # | Change | Effect today | Expected readback |
|---|---|---|---|
| B1 | Index-only key-moment tasks (`postVLM` not true) of an index camera go to local CLIP even when the camera has a caption scope (8a6cbaf) | **Restores Find Anything indexing for the G6 Instant and Giebel Vorn.** Both have 0 live index rows today because their tasks were refused as `video_contract`. Local CLIP and search DB only; no provider call, permit or budget. | New `ramDetections` rows for both cameras after natural events; `recognize_key_frames.worker_rejection_counts.video_contract` stops rising; those events end RAM `done` |
| B2 | Face and index jobs get per-operation IDs; an identical resend of a job under the old shared ID still dedupes (627c1a9) | A second, different task for an event with a face job is no longer refused. Today: 18 `job_identity_conflict`, 19 G6 events RAM `failed` despite a completed face job. | `job_identity_conflict` stays flat; new G6 face-job events do not end RAM `failed` from a second task; identical resends count as duplicates |
| B3 | Health labels the deliberate refusals `image_variant_refused` and `unindexed_camera` (8a6cbaf) | Label only; result code stays 5 | `unclassified_worker_error` stops rising for these; `image_variant_refused` rises at the old rate |
| B4 | `device.status.unlisted` (61369b5) | Health field only; without the Action 2b window it counts documented candidates and `not_recorded` | Sum equals the growth of the `unknown` command count |
| B5 | Worker tombstone validation moved to `worker_archive.valid_tombstone` (39961d7) | Same accepted shape; reads the existing 2,482+ tombstones | Duplicate replays still `already_completed`; no "Invalid archived worker result" |
| B6 | Device state loads through `state_schema` (3a6376d) | Live state is schema 1, so it loads byte for byte with no migration and no backup file. Saves now also fsync the directory. | `device-state.json` unchanged at start; no `.schema-*.bak` created |
| — | Inert with this config: continuous gates (36db45b, 5215e61), face enhancement (0e2b682, 1253079; not configured), reverification batching (059ceeb; off), camera-registry reasons (71b3ff3; no registry without continuous), `camera_inventory` text, the `aiport_ingest` geometry helper | none | — |

**Before the swap, record read-only baselines:**
- Key health: `adopted`, `connected`, `search.connected`; `control_commands` counts; `recognize_key_frames.worker_rejection_counts` (today `video_contract` 67, `job_identity_conflict` 18, unclassified 263, `permit_consumed` 4); worker queued, active and pending (must be 0).
- `ramDetections` total and per hashed camera (today 0 live rows for `135fe784` and `0b9cb6bf`).
- Native class-search counts (`detection-nls`; last read person 100, vehicle 72, animal 72, package 3).
- `config.json` sha256 prefix (`82390fffd714`) and `device-state.json` sha256.
- The four AI Ports' adoption and replies (must be untouched).

**Build and swap** (the approval covers these writes only):
1. Build an overlay image `FROM local-aikey:findanything18-rollover-20260927` that copies exactly the 10 modules from the approved commit.
2. Check the image: only those 10 modules differ from the live image, and `aikey.cli` imports cleanly.
3. Swap with `local-apple-upgrade swap --kind aikey --health-url …`: idle-gated, supervisor hold, readiness (adopted, connected, search connected), automatic re-pin of the old container on failure.
4. Delete the build helper and any throwaway container afterwards.

**Abort and roll back** (the tool's re-pin, or `local-apple-upgrade swap` back to `local-aikey:findanything18-rollover-20260927`):
- not ready within the tool's timeout;
- `config.json` or `device-state.json` changed, or a `.schema-*.bak` appeared;
- index rows decrease, or class-search counts fall below baseline;
- any provider call, caption permit or budget reservation from B1 index tasks;
- new `callback_uncertain` records, or the journal above 80%;
- an AI Port loses adoption or replies.

**Native acceptance within 24 h** (read-only; natural activity only): B1–B6 readbacks as in the table.

**Still needs_evidence (not changed by this deploy):** the six failed index jobs, five with Protect export HTTP 404 (four on Garage) and one Flur Wi-Fi frame gap. Whether the 404s come from Protect or from our verified `ubv`→`mp4` format switch is unresolved; after the deploy, count further 404s per camera and per export format.

## Action 2b (optional): name the unhandled controller commands (#1)

The Action 2 image also carries a bounded diagnostic (`device.status.unlisted`, tested code). Without this step it counts only:
- documented candidates by fixed name (for example `setDbCredential`, or the `vlm_inference` target);
- everything else as `not_recorded`, `malformed` or `overflow`.

- **Approve:** add one field to `config.json`, `device.diagnostic_command_fingerprints_until` = a Unix time **at most 7 days ahead** (config accepts up to 14), then one same-image restart.
- **Records:** while the window is open, the first 8 distinct unhandled command names and 8 unsupported RequestAI targets as 16-digit SHA-256 fingerprints with counts. It never keeps the name, target text, body, credentials, paths, media or reply.
- **Readback** (read-only, from `/healthz`): `device.unlisted.command` and `device.unlisted.request_ai_target`. Compare a fingerprint only against a documented candidate name, offline: `python -c "from aikey.device import command_fingerprint; print(command_fingerprint('<name>'))"`. An unmatched fingerprint identifies nothing and stays needs_evidence.
- **Acceptance:** after 24 h, the sum of candidates, fingerprints, `not_recorded`, `malformed` and `overflow` equals the growth of `control_commands.unknown.count` (and of the RequestAI 95 count).
- **Undo:** remove the field (byte restore of the Action 2 `config.json`) and restart once. The window also closes on its own.

## Action 3: backups and baseline (writes backups only)

- **Approve:** a 0600 archive of `config.json`, `device-state.json`, `worker-jobs/`, `worker-test-scopes/` and `worker-archive/` with digests, plus a verified search backup (`search_backup` with scratch restore), plus recorded class-search counts.
- **Writes:** backup files only.
- **Undo:** not needed (additive).

## What is tested code and what is a live trial

- **Tested code (synthetic, CI green; nothing live):** all of the following.
  - camera-ID pin, controller-version gate, budget and fair hold;
  - no double charge for failed or uncertain captions;
  - the uncertain-resolution tool;
  - the preflight phases and abort checks;
  - the upgrade tool.
- **Live trials (first time on this installation):**
  - **4a:** continuous captions on the unpaired G6 Instant. The *path* has one-use evidence (26 Sep); *continuous* operation does not.
  - **4b:** the **first caption on an AI Port-sourced event** (G5 Flex, paired). No native evidence exists that Protect saves such a caption.

## Action 4a: activate the G6 control only

- **Approve:** write `config.json` with continuous mode for **one** camera, then one same-image restart.

```json
{
  "controller": {"protect_version": "7.3.68"},
  "worker": {
    "test_scopes": "<removed: 2 consumed one-use scopes>",
    "continuous": {
      "enabled": true,
      "api_key_file": "/state/protect-api-key",
      "web_trust_file": "/state/protect-web-trust.json",
      "web_cert_file": "/state/candidate-controller-443.pem",
      "refresh_seconds": 60,
      "camera_models": ["UVC G6 Instant"],
      "camera_ids": ["<G6 Instant camera id>"]
    }
  }
}
```

- **Dry run** (in memory; live config sha256 prefix `82390fffd714` unchanged):
  - valid with the Action 2 code;
  - preflight then leaves only `native_readback_missing` (acceptance) once Action 1 is done;
  - `camera_ids_pinned` = 1.
- **Writes:** `config.json` only. The budget journal and archive grow from normal operation.
- **Accept 4a**, needed before 4b:
  1. `camera_registry.eligible_cameras` = 1 and fresh;
  2. the first G6 caption: exact-event GET shows `metadata.ramState` done and a non-empty `metadata.ramDescription` within **15 min** of its callback being accepted, and again in the native panel after a full reload;
  3. preflight `abort.codes` empty; search and all four AI Ports healthy.
- **Abort 4a** (restore the Action 3 `config.json` byte for byte, then one same-image restart):
  - any abort code;
  - no persisted G6 caption within 15 min of an accepted callback;
  - a caption `callback_uncertain`;
  - any of the health regressions in Action 5.

## Action 4b: add G5 Flex, the first AI Port-sourced caption trial

- **Approve:** after 4a is accepted, change **only** `worker.continuous.camera_models` to `["UVC G6 Instant", "UVC G5 Flex"]` and `camera_ids` to `["<G6 id>", "<G5 id>"]`, then one same-image restart. The dry run confirms these are the only changed paths and that the config is valid.
- **Budget:** unchanged, 12 per rolling hour shared, with the fair hold (G6 at most 11 while G5 is unserved). No per-camera cap is added.
- **The first natural G5 task is the trial.** Expected, in order:
  1. health `captions.admitted` rises;
  2. one budget reservation for G5;
  3. one provider request;
  4. the callback is accepted;
  5. within **15 min**, the exact Protect event shows `ramState` done and a non-empty `ramDescription`, confirmed after a full reload.

  Report the event-ID hash prefix and counts only.
- **If G5 dispatches but its caption is not saved within 15 min** (`ramState` failed, empty description, or no change), or the callback ends `callback_uncertain`:
  - roll back to the **4a** `config.json` byte for byte, keep G6, and restart once;
  - the task is not retried: the worker refuses replays of failed and uncertain captions, and a completed one answers `already_completed`, so there is no second charge (`tests/test_caption_no_double_charge.py`);
  - record "AI Port-sourced caption persistence: not saved" as needs_evidence.
- **If no G5 task arrives within 24 h** (no natural activity): leave 4b as is or roll back to 4a; neither is a failure.
- **Accept 4b:** one persisted G5 caption as above; the G6 control still persisting; a busy hour showing 12 reservations, a refused 13th and a fair deferral while G5 is unserved; preflight `abort.codes` empty.

## Action 5: observe 24 h and accept (read-only)

Run the preflight at least hourly. Roll back one stage (4b→4a, or 4a→Action 3 config) on any `abort.codes`, or on:
- a new `callback_uncertain` from a caption;
- the journal above 80% after rollover;
- the Key not adopted or connected, or search not connected;
- class-search counts below baseline or index rows decreasing;
- an AI Port losing adoption or replies, or new quota/rate 429s on the shared key.

Also accept:
- [ ] a Key restart inside the window keeps the rolling hour, and no permit is re-spent;
- [ ] completed and failed caption records roll into tombstones after 24 h, and uncertain ones stay.

## Approvals for Olli (one at a time; tested code vs live trial as stated)

1. **Archive the 54 reviewed index callbacks.** Tool: tested code. Digest `92bc7c3b…a223`, Key stopped, backup first, rollback available.
2. **Deploy the 10-module delta to the AI Key** (tested code; no config change). This *does* change live behavior: it restores G6 and Giebel Vorn indexing (B1) and stops the face/index identity collisions (B2). Idle-gated swap with automatic re-pin.
2b. *(Optional)* **Open a ≤7-day command-fingerprint window.** One config field plus a restart. Tested code; the first native readback of unhandled command names.
3. **Backups and baseline.** Backup files only.
4a. **Live trial of continuous captions on the G6 Instant only** (onboard events). `config.json` plus one restart, up to 12 captions per rolling hour (288 per day) on the pinned key. Undo: byte restore of the Action 3 config.
4b. **First live trial of a caption on an AI Port-sourced event** (G5 Flex added). Only `camera_models` and `camera_ids` change, and the budget is shared. Undo: byte restore of the 4a config. A missing save within 15 min is an expected possible outcome, recorded as evidence, not retried.
5. **Observe 24 h** (read-only).

## Remaining needs_evidence

- Whether Protect saves a caption produced from an **AI Port-sourced** event (0 of 128 post-pairing Büro events carry one; none was in scope). Action 4b is the first trial.
- Whether Protect dispatches one caption task per smart event on these families under continuous admission.
- Native persistence under continuous operation, a restart and multi-day endurance.
- The effect on events that already carry native tags.
