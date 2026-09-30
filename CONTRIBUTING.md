# Contributing

The aim is a community-maintained local AI processor compatible with UniFi
Protect. Read [release readiness](docs/legal/release-readiness.md) first.
This draft PR proposes Apache-2.0 for project-authored material. Read the root
`LICENSE` on the branch you use. Main is unchanged until the proposal is merged.
Do not assume it grants rights to vendor software, model weights or material
whose origin has not been established.

For code submitted for consideration, describe its origin, any third-party
material and relevant licenses. The maintainer must verify permission to include
it under Apache-2.0 before merging it. Contributors should attest their own
rights using the
[Developer Certificate of Origin 1.1](https://developercertificate.org/) and a
`Signed-off-by` line produced by `git commit -s`. Sign only if you can truthfully
make that certification. A bot/agent must not sign for you; this policy is not
retroactive evidence for existing commits.

Do not contribute manufacturer binaries, extracted/translated functions,
disassemblies, keys, tokens, private footage, real transcripts or model weights.
Protocol fixtures must be independently written and synthetic. Use required
interface facts with source references, and establish permission and necessity
before further firmware analysis. We make no organizational clean-room claim.

Run `python -m pytest -q`, `python -m ruff check .` and the audit-only release
check before proposing changes. Report native behavior separately from synthetic
tests. A firmware version, camera name or callback HTTP success does not prove
saved Protect results. Preserve existing identities and pairings during tests.
Do not deploy, trigger real inference, change providers or merge without the
owner's authorization for that concrete action.
