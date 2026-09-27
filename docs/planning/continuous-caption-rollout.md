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

## Budget, ceiling and abort checks (reconciled)

- **Budget:** 12 reservations per rolling hour across the pinned cameras, so at most **288 per 24 h** (`DAILY_CEILING`). The budget journal keeps 24 h and holds up to 300 records.
- **Fairness:** with two cameras, one can take at most 11 while the other is unserved; its 12th attempt is `deferred_fair_share`. After both are served, the 13th in the hour is `exhausted`, before media or inference.
- **Replay of the last 24 h** of real smart events (192) through this rule: **109 admitted** (G6 91, G5 18), 34 exhausted, 49 deferred. This is an upper bound: one caption task per smart event is unverified.
- **Each admission** is one request to the pinned OpenAI `gpt-6-luna`, at most 4,096 output tokens.
- **Abort checks come from the budget journal and health, not process counters.** Health's `captions.admitted` counts since process start and is not per hour. The preflight's `abort` section (read-only) reports:
  - `budget_last_hour` / 12, raising `hourly_budget_exceeded`;
  - `budget_24h` / 288, raising `daily_ceiling_exceeded`;
  - `caption_jobs_without_reservation`, raising `caption_without_reservation`;
  - `eligible_cameras` versus `pinned_cameras`, raising `scope_wider_than_pin`.

  Any abort code means roll back (Action 5 rollback).

## Action 1: archive the 54 reviewed index callbacks

- **Approve:** `aikey-uncertain-resolution apply` with digest `92bc7c3b…a223` while the AI Key container is stopped.
- **Reads:** `worker-jobs/*.json`, `worker-archive/`, and one SELECT (job hashes with embedded rows) on `local-postgres-search`.
- **Writes, only these:**
  1. a new private backup directory with 54 byte copies (0600) plus `manifest.json` (digest, job hash, file SHA-256), written and fsynced before any change;
  2. 54 tombstones `worker-archive/<xx>/<job>.json` (`state: completed`, same shape the worker writes; 49 buckets);
  3. removal of the same 54 active records.

  Nothing in Protect, the search index, the budget or the config changes.
- **Refuses:**
  - `key_running` if the Key is up;
  - `plan_changed` if any of the 54 changed after approval;
  - `backup_not_empty`;
  - `tombstone_conflict`;
  - paid (caption) jobs, which are never eligible (`not_local_index`).
- **Retry:** rerunning with the same backup resumes. Finished records count as `already_archived`, and a partly done one is completed.
- **Check:** after restarting the same Key image, the preflight shows `uncertain_callbacks_pending` gone and journal entries down by 54; Key adopted, connected and search connected.
- **Undo:** `aikey-uncertain-resolution rollback --backup … --key-stopped` restores the 54 records byte for byte and removes only matching tombstones. It is idempotent.
- **Needs two service stops**, stop and start, of the Key container on the same image, using `local-apple-upgrade`-style idle gating (worker queued, active and pending = 0).

## Action 2: deploy the admission code to the AI Key (no config change)

- **Approve:** an overlay image of the live lineage plus the 36db45b and this pass's modules (`camera_registry`, `config`, `device`, `protocol`, `caption_preflight`, `uncertain_resolution`), swapped with `local-apple-upgrade swap --kind aikey`.
- **Writes:** a new container and image only. The supervisor pin moves to the new container, and the old one is kept stopped. `config.json` is unchanged.
- **Behavior change with the current config:** none. Continuous mode is off, and the one-use scopes are consumed.
- **Check:** adopted, connected, search connected; index rows ≥ before; native class-search counts ≥ baseline.
- **Undo:** automatic re-pin of the old container if not ready; otherwise the same tool back to the previous image.

## Action 3: backups and baseline (writes backups only)

- **Approve:** a 0600 archive of `config.json`, `device-state.json`, `worker-jobs/`, `worker-test-scopes/` and `worker-archive/` with digests, plus a verified search backup (`search_backup` with scratch restore), plus recorded class-search counts.
- **Writes:** backup files only.
- **Undo:** not needed (additive).

## Action 4: activate two pinned cameras

- **Approve:** replace `config.json` with the reviewed version below, containing the two real camera IDs, then one same-image restart.

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
      "camera_models": ["UVC G6 Instant", "UVC G5 Flex"],
      "camera_ids": ["<G6 Instant camera id>", "<G5 Flex camera id>"]
    }
  }
}
```

- **Dry run on an in-memory copy of the live config** (sha256 prefix `82390fffd714`, unchanged):
  - changed paths: exactly `controller.protect_version`, `worker.test_scopes` (removed) and `worker.continuous.*`;
  - `validate_config` accepts it with the Action 2 code (the current image rejects it);
  - preflight afterwards: only `native_readback_missing` (acceptance) remains, and `ready_to_activate` is true.
- **Writes:** `config.json` only. The budget journal `caption-budget.json` and the archive grow from normal operation.
- **Check within 15 min:** health `camera_registry.eligible_cameras` = 2 and fresh; preflight `abort.codes` empty.
- **Undo:** restore the Action 3 `config.json` byte for byte plus a same-image restart. The budget journal and archive stay for audit and admit nothing with continuous off.

## Action 5: observe 24 h and accept (read-only)

Run the preflight at least hourly. Abort (Action 4 undo) on any `abort.codes`, or on:
- a new `callback_uncertain` from a caption;
- the journal above 80% after rollover;
- the Key not adopted or connected, or search not connected;
- class-search counts below baseline or index rows decreasing;
- an AI Port losing adoption or replies, or new quota/rate 429s on the shared key.

Native acceptance (event-ID hash prefixes and counts only):
- [ ] A fresh exact-event persisted caption on **both** families: `metadata.ramState` done and a non-empty `metadata.ramDescription`, confirmed in the native panel after a full reload.
- [ ] A busy hour shows 12 reservations, a refused 13th (`exhausted`) and a `deferred_fair_share` while G5 is unserved. No reservation without a journal record.
- [ ] A Key restart inside the window keeps the rolling hour; no permit is re-spent.
- [ ] Completed and failed caption records roll into tombstones after 24 h; uncertain ones stay.
- [ ] Search class counts ≥ baseline; all four AI Ports keep adoption and rising replies.

## Remaining needs_evidence

- Whether Protect dispatches one caption task per smart event on these families under continuous admission.
- Native persistence under continuous operation, a restart and multi-day endurance.
- The effect on events that already carry native tags.
