# Shared camera inventory presentation — 30 October 2026

## Native reproduction

After #218's Admin update, the separate terminal check twice found fewer than
four score-gate forms after reading the plate page. The image update itself had
passed both views. All processing containers and saved profiles stayed intact;
this was not permission revocation or an inference failure.

A bounded diagnostic read verified all four saved groups against the same
validated 11-camera source: no missing cameras, names or ambiguous addresses.
A normal HTTPS read then showed the ordering effect: the first camera page had
four forms and nine choices; the second had one form and three choices.
Finally, a bounded two-page-equivalent reproduction recorded five successful
profile observations followed by **HTTP 429** for the remaining three. This
proves the observed quota refusal, not a universal vendor rate/window value.
No camera data, model request, controller setting or live permission was changed.

Both pages had fetched the complete inventory once **per profile**. The source
performs two fixed GETs for each observation. Normal navigation could therefore
exhaust Protect's allowance and present partially unavailable forms.

## Correction

The score and plate pages now use one complete observation per page, shared
with the private administration camera API for at most two seconds. A private
single-flight lock coalesces simultaneous GETs. Copies prevent one reader from
modifying another's observation. Known schema, version, completeness and age
checks still run on cache hits. Expired data is discarded before refresh; a
failed refresh never falls back to old good data. A two-second failure cooldown
prevents concurrent readers from immediately repeating a rejected source read.
Replacing the configured loader invalidates the old source; cancellation does
not become a cached failure.

This is a server-side read observation, not browser storage or a grant of
processing scope. Each POST preview and apply still obtains a **new** inventory
through the original source, rechecks camera identity and configuration revision,
and uses the existing session, CSRF, signature and single-use rules. Rollout
planning and execution authority do not use this GET cache. The legacy remove-only
permission page retains its existing name-only source contract so privacy
revocation does not acquire a new full-inventory prerequisite.

## Qualification scope

The two multi-profile navigation regressions fail on parent `fbc3e3a` with one
form rather than four. The focused matrix covers both page orders, coherent
outages without repeated profile fetches, expired observations, fresh POST
refusal after camera renaming, API sharing, concurrent readers, caller mutation,
source replacement and cancelled-leader recovery. Existing plate, score and
remove-only permission cases remain included. Exact totals, full platform and
browser checks are reported in the PR when complete.

No real permission, provider, model, identity, pairings or processor change is
part of these fixtures. The original failed terminal receipts and the separate
native HTTP 429 evidence are retained privately. The extra existing-key native
bootstrap read returned HTTP 500; the Integration API remained 7.3.74 with 11
cameras and no recording or PTZ fields. That source limitation is not fixed or
reclassified as successful by this presentation correction. Broader #17/#19 and
native quality acceptance remain OPEN.
