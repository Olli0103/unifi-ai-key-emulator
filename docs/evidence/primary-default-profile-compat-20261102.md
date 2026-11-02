# Keep new primary defaults out of legacy profiles — 2 November 2026

Part of [#13](https://github.com/Olli0103/unifi-ai-key-emulator/issues/13) and
[#17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17).

Before native Admin activation, source review found a concrete mixed-version
boundary: a new provider form submitted default `structured_output=false` and
`request_timeout_s=15`, and the newer store serialized them even when absent
from the existing profile. The running older Port parser deliberately refuses
unknown keys; ordinary provider edits could therefore create a profile it could
not load on its next restart. The parser must stay strict.

A network-none/read-only isolated instance of the actual existing Port image
accepted a wholly fictional old profile and rejected either/both new default
fields. Its parser hash matched frozen source222. No real state mount,
provider/model request or running processor mutation was used. A first probe
omitted Docker stdin forwarding and returned no fixture output; this pre-fixture
failure is retained separately. The corrected probe forwards stdin and passes
four cases; the existing Port process identity remained unchanged.

The store now writes a new optional field only for an explicit nondefault or
when the field was already present. Thus ordinary displayed-default saves keep
legacy profiles free of new unknown fields. Explicit schema enable/nondefault
budget still require a compatible updated Port; explicit reset of an existing
field retains it, and exact provider rollback stays intact. It does not silently
remove a previously configured field or expand an old parser's vocabulary.

The UI states that the option requires updated AI Port software and that saving
does not upgrade or restart it. This is not a capability handshake or an
automatic rolling upgrade. An operator must not enable a new nondefault on an
old runtime; native activation and mixed-version operational qualification
remain separate.

Six focused regressions cover all four default/nondefault combinations,
existing-field reset/exact undo and a real authenticated provider form using the
unchanged rendered defaults. Four fail on the exact parent and two controls
already pass; all six pass after the correction. No transport, deadline,
request/parser, fallback, quota or runtime-factory code changes.

The [complete twelve-case public model result](public-person-structured-30s-20261102.md)
was measured separately using already qualified PR242 source, not this store/UI
change; no new inference is needed to validate this compatibility fix.
