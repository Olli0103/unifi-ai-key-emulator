# Provider key save recovery — 29 October 2026

Security/transactional progress for #3, #13 and #17. Not a claim that their full
acceptance scopes are complete or that a running deployment was changed.

## Reproduced defect

Two synthetic cases fail on main at `3a15b45`: for both AI Key vision and AI Port
detection, a store error after a successful atomic config replacement causes
the form handler to delete the new key file. The active config then references
a missing key. This is a local error-path reproduction, not evidence of an
observed production incident or credential disclosure.

Reading the current config before deletion is still insufficient. A later save
can archive the newly committed reference before the first caller handles its
error. Deleting the key then breaks exact rollback, even though live config no
longer references it.

## Changed boundary

One shared cleanup rule covers AI Key vision, AI Port detection and AI Key
speech. Input/preview rejections before apply and store revision conflicts prove
the key unused by that attempt and may discard it. A generic error after apply
begins is uncertain: keep the file, emit a fixed `uncertain` audit result and
tell the operator to reload before retrying. Do not automatically replay the
save, infer that it failed or describe a failed acknowledgement as rollback.

This deliberately permits an unused private key file after an uncertain save.
There is no automatic expiry/pruning or provider-side revocation. Active and
archived references must be reconciled before manual deletion. It does not
solve host filesystem compromise, obsolete credential cleanup or index restore.

## Verification

Six vision/detection tests cover committed keys, later archived references with
exact undo, and known revision conflicts after preview. Existing speech tests
exercise both pre-commit and post-commit generic errors. Targeted configuration,
rollback and secret-form suite: 46 passed. Ruff and diff checks passed.
No real credentials, media, provider calls or controller changes are used.
