# Apple instance operator: source and synthetic qualification, 4 November 2026

Scope: the missing bounded Mac create/start path for
[issue 45](https://github.com/Olli0103/unifi-protect-local-ai/issues/45), on top of
merged control-workflow source `572b492827aef462f17a9a4d8509ec2def559d49`.
This record is same-agent source/fixture evidence, not an independent review.

## Implementation

The [operator guide](../aiport-apple-reconcile.md) describes private exact
manifests, live pinned inventory, shared Mac/NAS capacity guards, image/runtime
inspection, exact-address host checks, create/start, own-start-only rollback and
explicit positive reconciliation. Planning changes no identity. No existing
healthy service, camera pairing, address, firewall, model or provider is changed
by these source tests.

The per-config startup reservation closes a source-derived lost-owner race:
an in-memory/configuration mutex alone cannot fence writers after a host owner
exits while the daemon may still complete startup. Supported writers now honor
the persisted reservation; new workers reject unreviewed bytes before runtime
construction. New images must declare that contract and newly started workers
must report the contract and exact loaded config hash over pinned TLS health.
This is a coordinated source contract, not a claim that deployed images or Admin
writers have been upgraded.

## Observed synthetic checks

- The initial **48** existing NAS tests passed after extracting the shared guard.
- An expanded group passed **207** tests across the operator, plans, host checks,
  existing NAS actor, pinned Admin runtime reader and backup contracts.
- **569** operator/documentation cases passed after adding numeric receipt
  rejection. Documentation examples go through actual argument parsers.
- A real second Python writer process could not acquire the held configuration
  OS lock during startup. After injected lost start acknowledgement, it was
  blocked by the durable reservation. Only explicit positive reconciliation
  permitted it again. The daemon/health side of this test is synthetic.
- Four separate old/new subprocess comparisons covered both Key and Port:
  parent source admitted ordinary writers despite an invented prepared marker
  and admitted valid but unreviewed startup bytes; corrected source refused
  both. No model/controller/runtime was contacted by those comparisons.

These are overlapping test groups, not additive totals or a full-suite result.
Further review reproduced apply accepting a host authority journal inside the
guest-writable state mount. Authority roots are now required to be disjoint
from all selected mounts and Key roots before even a daemon read command or
lock/journal creation. Eight read-only/apply cases cover equal, child, ancestor
and Key paths: all eight fail on the preceding source. The corrected combined
authority/CLI/startup/documentation group passes **595** cases. The first full
Mac run was intentionally interrupted for this
source correction; its partial counts are not a full-suite pass.

Original failures are retained privately: a final journal publication failure
was initially reported too optimistically as ordinary rollback; it now remains
explicitly uncertain. An initial Key test expected the wrong exception family;
the actual writer guard already refused correctly. A mistyped compatibility
test filename ran zero tests and was corrected, not counted as a passing run.

Actual CLI replay also reproduced lost admission diagnostics: a stale inventory
was safely rejected but surfaced only as a generic operation failure. Shared
inventory/slot failures now map to a bounded allowlist of actionable codes;
unknown errors are never echoed. Four regressions fail on the previous source,
and the corrected seven-case CLI group passes. The second full Mac run was
intentionally interrupted for this acceptance correction (3,449 cases passed
before interruption, not a full-suite pass).

## Native and original-acceptance boundary

| Original acceptance | What this change establishes | Still required |
| --- | --- | --- |
| Fresh inventory and verified instance/bind count | Shared plan reconstruction, freshness/source/capacity checks; read-only live fetch option | Fresh target-host inventory and complete native listener verification |
| Mac/NAS stable starts/restarts with Key online | Bounded Mac actor and unchanged NAS contracts; synthetic health/identity preservation | Actual Mac 1.6.1 and NAS lifecycle with native Key continuity |
| Add/remove without identity or recording loss | New starts use existing stable prepared slots; no automatic move/delete/pair | Real added slot, removed-camera/recording continuity and native paired set |
| Refusals and failed startup preserve healthy deployment | Synthetic stale/missing/conflicting/unsupported/ACK/rollback cases | Actual host failure/recovery with native prior deployment continuity |
| Native verification and unproved stream ports | Explicit `needs_evidence`; no guessed UDP/stream publications | Fresh Protect read-back for every started instance and intended pairing |

No original acceptance checkbox is completed by this record. No native apply,
runtime installation, address alias, firewall change, adoption, pairing,
recording playback or camera/model inference was performed. The current Mac
has no Apple `container` executable at the checked supported locations, so actual
Mac qualification remains blocked on a qualified runtime/transport environment.
Official 1.6.1 docs/release metadata were read; older privileged-bind issue
reports are not treated as a measured 1.6.1 failure. The Camera A alarm test remains
paused and unrelated to these source checks.

## Origin

Project-authored, AI-assisted Python, tests and documentation. No vendor source,
binaries, traces, models, private device data or third-party implementation was
copied. Apple command/interface documentation informed typed argv and invented
inspection fixtures. This record is not a human DCO, legal signoff, public image
publication, release approval or independent security/quality assessment.
