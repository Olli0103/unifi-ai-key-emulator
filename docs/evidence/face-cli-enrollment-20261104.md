# Local face CLI enrollment boundary — 4 November 2026

Source/fictional qualification toward #20. This is not native Protect grouping,
consented named recognition, model quality or completion of the broader issue.
The separate 512-value AI Port model space and all camera permissions are unchanged.

## Reproduced behavior

On parent `7cf8f7f18f646024eb021f319b1827a76554f67a`, the CLI reads its photo with
unbounded `Path.read_bytes()`. A FIFO blocks the actual CLI process; the bounded
regression harness kills and collects only its own child. Missing/directory
errors expose the supplied filename/path. Empty, oversized and symlink inputs
reach a fictional analyzer and can create a fictional template when that analyzer
answers successfully. Invalid labels are not checked until after work admission.

The existing model-work lease closes after the synchronous analyzer returns but
**before** `FaceStore.enroll` persists its result. An actual kernel-lock observer
can obtain model-migration exclusivity at the start of that save. The initial
group reproduced 15 negative failures and one valid control, not 15 independent
production incidents. All supplied bytes, names, vectors and analyzers are fictional.

Further counterexamples show that delete, purge or sibling enrollment during an
analysis can be overwritten by a stale CLI result. A legacy unbound store or known
full store also performs analysis before refusing admission. Separate argument-
parser failures echo unknown commands/options/extra values before the normal
error handler runs. These failures remain separate from the corrected checks.
No real biometric record, photo, model inference or native setting was used.
The expanded replay of the current behavioral suite against the unchanged parent
verifies imported-module and child-process origins: **30 negative cases fail,
one valid control passes**. Two tests specific to the new bounded reader are
explicitly deselected there, not treated as parent results. The separate corrected
suite includes those reader tests.

## Complete local operation

- Validate presence and the existing trimmed 64-character label contract before
  constructing a model-work fence. No naming rule, matching threshold or vector
  normalization is changed.
- Acquire the existing model-work lease before reading the store or photo. A
  blocked/expired/malformed fence still prevents photo access and model calls.
  An absent marker retains legacy behavior; this does not create/disable a guard.
- Read the existing store's validated metadata/revision. Nonempty unbound legacy
  state and known identity/template capacity limits are refused before analysis.
- Open the explicitly supplied photo once with no-follow/nonblocking flags.
  Require a nonempty regular file of at most **10 MiB**; bound the actual read and
  refuse a changed size/modification identity during that descriptor read.
  Leaf symlinks, devices, directories and FIFOs are not input streams. Ordinary readable
  photo permissions/hard links are not rewritten into private-store rules.
- Pass the original captured bytes to the existing private/local face endpoint.
  This file boundary does not decode, convert, crop or certify image content. The
  existing analyzer/transport still requires exactly one face, a valid 128-value
  template and its explicit model/pipeline identity. No cloud fallback is added.
- Use the captured **byte-level store revision** at `FaceStore.enroll`, under its
  existing writer lock. A changed/missing/purged store or sibling write is not
  silently undone. No automatic reanalysis, retry, vector relabel or state restore
  follows a conflict. This is not a monotonic history/hostile-owner guarantee.
- Hold the model-work lease through the store operation, including atomic save,
  fsync/read-back and its terminal error. Then release it in `finally`, including
  store failure or KeyboardInterrupt. It does not establish arbitrary remote
  backend cancellation or native daemon quiescence.

The original photo is neither saved to state nor modified/deleted/chmodded.
Captured input is transient memory, not a physical-erasure promise. Regular
filesystem I/O has a byte/type bound, not a hostile-filesystem wall-clock promise.

## Private outcomes and recovery

Successful stdout shapes remain unchanged: explicit list, enrollment count,
delete result or purge result. Explicit list output and shell/process arguments
are private operator surfaces, not public diagnostics. The private browser
administration flow remains separate and unchanged.

Runtime errors use an allowlist of fixed reason codes. Unknown exception text,
filenames, labels, vectors and provider content are not echoed; an unknown error
retains only a maintained `faces.py` line location. Argument syntax errors print
static usage plus `invalid_arguments`, not supplied values.

A post-commit failure may report `face_store_write_uncertain`: the template may
already exist. Re-read private metadata and reconcile the count/revision before
another explicit action; do not blindly repeat analysis/save or restore old
state. A generic/transport failure is not proof of zero effects either.
Deletion/purge do not erase recordings, native groups, photos/server copies,
backups, process arguments or an already-read in-flight match.

## Qualification scope

The source tests cover regular bytes/mode preservation, absent/existing model
fences, FIFO subprocess completion, malformed/oversized/changing files, private
parser/runtime errors, invalid labels before admission, full/legacy-store refusal,
revision-bound deletion/purge/sibling conflicts with and without a marker, a real
store commit under the kernel lease, uncertain committed writes and interruption
cleanup. Existing storage, enrollment transport, model-space, worker and UI tests
remain separate checks; no mocked response is native biometric-quality evidence.

`FaceStore`, model-space derivation, matching, private storage schema, transport,
thresholds, permissions and native callbacks are not replaced by this CLI change.
No deployment or real enrollment is part of this source qualification.
