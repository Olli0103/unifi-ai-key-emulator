# Two-family continuous-caption rollout (#12): dry-run proposal

Status: **proposal, not activated.** No setting, scope, service or provider call was changed to write this. Everything below is counts, fixed reason codes and placeholder IDs. Captions and transcripts stay private.

## Current state (27 Sep 2026, read-only)

- **AI Key:** adopted and connected to Protect 7.3.68, which `setConsoleInfo` reports; the evidence class is `live_partial`. The worker is idle (queued, active and pending all 0).
- **Caption preflight** (`python -m aikey.caption_preflight`):
  - continuous mode not configured; 2 one-use scopes configured;
  - all 5 permits consumed;
  - no budget journal yet (12 of 12 available);
  - job journal 597/1024 (58%): completed 434, failed 109, `callback_uncertain` 54; 0 due for rollover.
  - Blockers: `continuous_not_configured`, `one_use_scopes_configured`, `uncertain_callbacks_pending`, `native_readback_missing`.
- **The 54 uncertain callbacks** are Find Anything `indexImages` jobs. All 54 were classified terminal: 619 rows with 768-dim embeddings are stored (#12, 27 Sep). They still sit in the active journal as `callback_uncertain` until an operator archives them. That review is the owner's step, and it clears `uncertain_callbacks_pending`.
- **Pinned caption provider:** `inference` = OpenAI `gpt-6-luna`, `max_output_tokens` 4096. The shared account had run out of credit until 27 Sep 15:38 UTC; it has worked since.

## Families chosen

| Family | Cameras eligible now | Source of smart events | Earlier native caption evidence | Smart events, last 24 h | Peak hour |
|---|---|---|---|---|---|
| UVC G6 Instant | 1 | onboard | yes (26 Sep, re-read on 7.3.68) | 171 | 28 |
| UVC G5 Flex | 1 | AI Port-paired | yes (22 Sep, re-read on 7.3.68) | 22 | 12 |

- **Different families:** one onboard and one AI Port-supplied camera, so both dispatch paths are exercised.
- **Budget coverage:** G6's busy hours (7 of the last 24 above 12 events) exercise the 12th and 13th request; G5 exercises the permit held for fairness.
- **Scope pinned:** both models currently have exactly one connected eligible camera. `camera_ids` (below) keeps it at exactly two even if a same-model camera is added or reconnects.

## Admission gaps fixed with this proposal (code, synthetic tests only)

1. **Model-only scope could widen silently.** `camera_models` admits every connected smart camera of a model. Example: the second G4 Instant is offline today and would join on reconnect. New optional `worker.continuous.camera_ids`: a camera must match both a model and a listed ID. An unlisted camera reports `camera_not_listed`.
2. **The controller version was trusted from a config label.** Continuous mode was gated only on `controller.protect_version == "7.3.60"`, a hand-written label. The live label still reads 7.3.60 while the controller reports 7.3.68. Changes:
   - admission now also requires the version the controller itself reported (`setConsoleInfo.protectVersion`) to be in `CONTINUOUS_CAPTION_VERSIONS` = {7.3.60, 7.3.68}, the versions with native caption readback;
   - otherwise `recognizeKeyFrames` is answered 95 before the worker, budget or media, and `controller_version_unverified` is counted;
   - the config gate accepts the true `"7.3.68"` label.

Tests: `tests/test_continuous_scope_gates.py` (19 cases; 16 fail without the fix) and the updated device-gate test.

## Exact minimal config change (dry run)

Applied in memory to the live `config.json` (sha256 prefix `82390fffd714`, unchanged):

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

- **Changed paths:** `controller.protect_version`, `worker.test_scopes` (removed), and `worker.continuous.*` (added). Nothing else: search, Find Anything, speech, faces, provider, key and AI Ports are untouched.
- **Validation:** `validate_config` accepts it with this code. The **currently deployed Key image rejects it**, because it predates `camera_ids` and the version set. The gate code has to be deployed first.
- **Preflight on the proposed config:** only `uncertain_callbacks_pending` and `native_readback_missing` remain.

## Expected request envelope

- **Hard ceiling:** 12 new caption attempts per rolling hour across both cameras, at most 288 per 24 h. Budget journal retention is 24 h and its cap of 300 records covers that.
- **Fairness:** while one camera has had no caption in the hour, the other can take at most 11; its 12th attempt is deferred (`deferred_fair_share`). After both are served, the 13th attempt in the hour is refused (`exhausted`) before media or inference.
- **Replay of the last 24 h of real smart events** (192) through the same rule:
  - 109 admitted (G6 91, G5 18);
  - 34 exhausted;
  - 49 deferred.

  This is an upper bound: it assumes Protect sends one caption task per smart event, which is unverified.
- **Each admitted attempt** is one provider request with the event's key frames, at most 4096 output tokens. Uncertain attempts are not refunded.

## Procedure (each step needs Olli's approval)

1. **Owner review:** archive the 54 terminal `indexImages` uncertain records. Preflight then drops `uncertain_callbacks_pending`.
2. **Deploy the gate code to the Key.**
   - The live Key is 9de93fd plus two hunks. The new code touches `camera_registry.py`, `config.py`, `device.py`, `protocol.py` and `caption_preflight.py`. Rebase them onto the live lineage in a worktree and run the full suite.
   - Build an overlay image and swap with `local-apple-upgrade swap --kind aikey` (idle-gated, automatic rollback). No config change in this step.
   - Check: adopted, connected, search connected, and the same 4,550+ index rows.
3. **Backup:**
   - copy `config.json`, `device-state.json`, `worker-jobs/`, `worker-test-scopes/` and `worker-archive/` into a 0600 archive, with digests;
   - take a verified search backup (`search_backup` with scratch-restore verification);
   - record the native search baseline counts (class queries).
4. **Activate:** write the config above with the two real camera IDs, then restart the Key once through `local-apple-upgrade swap` on the same image. No other change.
5. **Observe** for 24 h, read-only: Key health `worker.captions` (admitted, exhausted, deferred_fair_share), `registry.status()` and the preflight.

## Abort criteria (roll back at once)

- Any caption admitted for a camera other than the two pinned IDs, or `camera_registry.eligible_cameras` > 2.
- `captions.admitted` above 12 in any rolling hour, or any provider call without a budget reservation.
- A new `callback_uncertain` from a caption job, or the journal above 80% after rollover.
- Key not adopted or connected, search not connected, native class-search counts falling below baseline, or index rows decreasing.
- Any AI Port losing adoption or detections, or new provider 429/quota failures on the shared key.
- Provider cost above the agreed daily ceiling (default: the 288 upper bound).

## Rollback

1. Restore the backed-up `config.json` byte for byte. This removes `continuous` and restores the 2 consumed one-use scopes and the 7.3.60 label.
2. Restart the Key on the same image through `local-apple-upgrade swap`.
3. The budget journal and job archive stay in place for audit. With continuous off they don't admit anything.
4. If the gate deploy itself misbehaves, the tool re-pins the previous Key container.

## Native Protect acceptance (after activation)

- [ ] A fresh exact-event persisted caption on **both** families: exact-event GET shows `metadata.ramState` done and a non-empty `metadata.ramDescription`, confirmed in the native panel after a full page reload. Report event-ID hash prefixes and counts only.
- [ ] Budget: within one busy hour, `captions.admitted` reaches 12, the 13th attempt is refused `exhausted`, and a deferral occurs while G5 is unserved. No duplicate charge for a repeated task.
- [ ] Reconnect: after a Key restart during the window, both cameras still admit, the budget journal carries the rolling hour over, and no permit is re-spent.
- [ ] Journal rollover: completed caption records leave after 24 h (continuous), failed after 24 h, and uncertain records stay.
- [ ] Live search: class-search counts are at least the baseline, and index rows grow naturally.
- [ ] AI Ports: all four stay adopted with replies rising and no new 429s.

## Decision needed from Olli

Approve, in order:
1. archiving the 54 reviewed uncertain index callbacks;
2. deploying the gate code to the AI Key, with no config change;
3. activating continuous captions for exactly the G6 Instant and G5 Flex camera IDs above, capped at 12 per rolling hour (at most 288 per day) on the pinned OpenAI `gpt-6-luna` key, with the abort criteria and rollback above.

Until then, continuous captions stay **needs_evidence**.
