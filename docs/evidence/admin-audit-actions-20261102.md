# Administration audit action admission — 2 November 2026

Part of [#13](https://github.com/Olli0103/unifi-ai-key-emulator/issues/13)
and [#17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17).

## Reproduced defect

The caption-camera additions and processing-limit pages emit fixed action names
`caption_enrollment` and `worker_limits`. Neither was registered in the private
audit log's action allowlist. With auditing configured, `AuditLog.record` raised
`ValueError`, producing HTTP 500 on preview, explicit rejection, saved success
and uncertain-save reporting. In the success cases the configuration had already
committed before audit reporting failed. This is not proof that every real 500
was caused by this defect; any uncertain real save still requires read-back.

Eight actual synthetic HTTP cases fail on the parent and pass with the two
fixed action names admitted. The first corrected run had seven passes and one
test expectation error: the existing caption success renders HTTP 200, not a
303 redirect. Correcting that assertion did not change production behavior.
The preceding combined run passed 133 other checks. The full eight-case rerun
passes. Failure logs are retained privately outside the source tree.

The only runtime change adds those two constants. It does not catch/ignore audit
errors, accept arbitrary names, change serialization/file permissions, retry a
save or modify configuration/admission/worker behavior. The existing browser
runners now enable a real private audit log and assert the actual preview/save
entries. Fictional desktop/mobile/keyboard flows for both controls pass with
zero foreign requests and no real camera, provider, processor or media action.

Broader native administration, independent installation and complete feature
acceptance remain open. This correction does not activate the live Admin or
grant real processing/audio/camera permissions.
