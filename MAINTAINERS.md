# Maintenance and support

## Ownership and decisions

The repository owner, [Olli0103](https://github.com/Olli0103), holds the final
review, merge and release authority. An assistant may integrate work within an
explicit owner mandate; CI or an automated review status is not that mandate.
Do not generate contributor attestations or sign-offs for another person.

Changes need an issue/scope, review of the actual diff, relevant checks and
the required CI jobs on the integrated commit. Evidence labels distinguish
implemented, fixture-tested and native-verified behavior. An integration must
preserve identities, private state and the last known-good configuration.
Deployments and publishing are distinct from merging source.

## Supported versions

This is experimental pre-alpha software. There is no stable release line or
LTS/security-support promise yet. `main` is the integration baseline; bug reports
must include the exact commit and Protect version. A feature branch is not a
supported deployment just because its tests pass.

Compatibility is feature- and version-specific: use the
[matrix](docs/evidence/compatibility-matrix.md) and dated evidence. For example,
the verified 7.3.74 inventory contract is not approval of every Protect feature
on that version. Unknown contracts remain disabled or `needs_evidence`.

Until a signed release is approved, no tag, package or image should be described
as production-ready. Future releases use semantic versions, a changelog, exact
source/artifact digests, SBOM/provenance and a documented rollback path. A
breaking change to identity, configuration or index formats requires an explicit
migration; never silently regenerate an adopted identity.

## Triage

Use the public issue forms for sanitized facts only:

- `bug`: a reproducible defect, with expected/observed behavior and exact versions;
- `enhancement`: a proposed behavior with scope and acceptance criteria;
- `documentation`: incorrect or missing public instructions;
- `compatibility`: a version/platform/native-contract report;
- `needs-evidence`: the exact missing fixture, source, consented baseline or native readback;
- `blocked:hardware`: required test equipment is unavailable;
- `maintenance`: packaging, CI, dependency or operational maintenance.

Keep existing labels and issue history. Check assignees, comments, active workers
and related PRs before claiming work. Do not close native requirements on mock
results or drop unmet requirements to improve the closed-issue count. An issue
can remain open with completed software work and a precisely named native gate.

## Reports and response expectations

Security reports use the private route in [SECURITY.md](SECURITY.md), never a
public issue. Conduct is covered by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
There is no staffed support desk or guaranteed response time. Acknowledgement,
fix timing, compatibility coverage and disclosure dates must be agreed by the
maintainer handling the actual report, not promised by an assistant.

No default telemetry or camera-media upload is collected. A reproduction must
work with synthetic fixtures or separately approved local material, not somebody
else's controller credentials or footage. Published reports use counts and
states, not private recordings, transcripts, names or biometric templates.
