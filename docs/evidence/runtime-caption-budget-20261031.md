# Running caption policy and budget observations — 31 October 2026

Part of the remaining Processing/Budgets scope in #13/#17. The broader issues
remain OPEN; this does not establish native event quality or billing totals.

## Change

The Key's bounded health now distinguishes the policy loaded by its worker from
later saved settings: metered continuous captions, explicitly unmetered captions,
one-use scopes, and disabled continuous captions. A metered worker separately
samples its own durable global rolling-hour journal. The aggregate contains no
camera/job/event ID, fingerprint, provider route, media, private path or error.

The reader opens only an existing validated private directory and small regular
file, using a held directory descriptor, no-follow/nonblocking file opens and the
shared journal validation. Strict observation parsing additionally rejects
duplicate/nonfinite JSON, boolean schema, unsafe owner/mode and impossible hourly
counts. A missing file is empty under the existing admission contract; a missing
directory, unsafe/corrupt journal or backwards wall clock is unavailable. It does
not create a directory/lock/journal, acquire a reservation lock, reserve/refund,
prune/repair, change admission or weaken any failed-admission guard.

A single off-loop read is shared by health callers. Each caller waits at most
150 ms; timeout or caller cancellation does not create another read while that
one is outstanding. Successful data expires two seconds after read admission;
an overdue completion is not relabelled fresh. Errors are fixed unavailable
observations and are cached briefly without exposing private diagnostics. A
filesystem that does not return can retain this one read; it does not have an
enforced thread-abort deadline. No universal filesystem/shutdown guarantee is
claimed. Unmetered/noncontinuous modes perform no journal read.

The authenticated Processing JSON/page allowlists and validates this observation.
Older peers remain unknown. Actual sample time is shown, separately from health
observation/cache time. Unreserved global slots are not a per-camera admission
promise, provider balance or count of paid requests: fair sharing/concurrent work
can refuse an attempt. Failed/uncertain reserved attempts remain charged under
the unchanged reservation contract. Speech, Port and other operations retain
their separate policies. There is no automatic retry, reset or recovery action.

## Verification and activation boundary

Synthetic tests cover rolling expiry/restart without refund or mutation,
missing/unsafe/corrupt/duplicate/overfull journals, clock rollback, symlink/FIFO,
directory-inode replacement, slow/single-flight and cancelled health callers,
late-sample rejection, error recovery, all loaded modes versus later config edits,
actual health handler assembly and strict normalized API output. The existing
multi-process reservation and continuous-caption regressions remain required.

Real isolated Chrome desktop/mobile verifies the sampled metered policy/table,
explicit unmetered mode, keyboard details, no body overflow, unavailable peers,
expired sessions, no browser storage/private echo/foreign requests and unchanged
fictional configs. Screenshots are fictional. Exact-head full-suite/CI, outside
checks and native activation are recorded in the PR; source integration alone
does not install a new health producer on the real Key or change its policy.
