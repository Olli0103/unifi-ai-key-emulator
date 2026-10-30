# Bounded retention-page archive scan — 30 October 2026

## Finding and scope

A separate netless administration candidate could authenticate and render the
five current NAS profiles, but the next read-only page sequence exceeded its
12-second response limit and then its eight-second clean-shutdown limit. The
owned candidate was killed/collected; no persistent administration listener was
enabled. The original helper did not retain which page in that sequence timed
out, so the exact original failing page and complete cause remain unknown.

A bounded metadata-only inspection counted 20,000 archive entries in the first
43 shard directories before stopping at its declared limit. That is a lower
bound, not a full archive total. Source inspection found that the retention page
called the full archive inventory, opening every retained marker and traversing
references. A synthetic 2,100-marker regression independently demonstrates that
the old page scans past a 2,048-entry allowance. This establishes the unbounded
status-read defect; it does not retroactively supply the missing native trace.

## Change

Only the retention-page archive call opts into a cooperative 2,048-entry/250-ms
monotonic budget. The scanner checks between entries/file operations and uses
lazy `scandir` rather than first materializing a whole large directory. Archive
markers, active-job references, test permits and budget-reference files share
the entry budget. Incomplete reference information is unknown, never an empty
proof of no references. The page hides partial marker/problem counts and states
that the scan is incomplete. An incomplete inventory is refused by the expiry
planner, including when controller-absence evidence is supplied.

The full CLI inventory remains the default, preserving its validation and
replay-marker retention rules. No worker admission, model request, paid-caption
reservation, callback, deletion, cleanup timer or live processor setting changes.
This is not a hard I/O deadline or a full same-user/ancestor-filesystem audit;
one blocked filesystem syscall can still exceed the cooperative clock budget.

## Verification and remaining evidence

Initial new group: seven failures on baseline, including the 2,100 actual reads
and absent bounded-API/refusal behavior. Final focused archive/retention/control/
private-file group: 57 passed. Cases include incomplete-planner refusal,
monotonic cutoff, invalid budgets, reference-only overflow, lazy enumeration,
small complete inventories and unchanged marker files. All names/records are
fictional; no archive was deleted or truncated to make the status page pass.

Native candidate requalification and exact-head CI are separate gates. No
browser/mobile, native full administration, sustained-operation or complete
archive-expiry acceptance is established by these source/fixture checks.
