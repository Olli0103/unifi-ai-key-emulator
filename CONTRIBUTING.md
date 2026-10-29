# Contributing

Thanks for helping. This project emulates the UniFi AI Key and AI Port so they work with UniFi Protect. It handles controller credentials, camera media and provider keys, so a few rules matter more than usual.

## Never post private material

Do not put credentials, API keys, device passwords, camera images, recordings, transcripts, face or plate data, private hostnames, IP plans or controller support files in an issue, pull request, test or fixture. Security problems go through [SECURITY.md](SECURITY.md), never a public issue.

## Development prerequisites

Use Python 3.12 or newer and an OpenSSL executable that supports `passwd -6`. Credential tests use SHA-512-crypt, as does the AI Port management login. The `openssl` command included with macOS may be LibreSSL and reject `-6`; installing the Python `cryptography` package does not replace that command.

On macOS with Homebrew OpenSSL installed, select it for the current shell before running the tests:

```sh
export PATH="$(brew --prefix openssl)/bin:$PATH"
openssl version
```

FFmpeg must also be on PATH for the real video and audio integration tests. Tests that need an unavailable optional decoder or model report a skip; a passing subset is not full media or model coverage. The unsupported-UBV rejection tests use a non-executable placeholder and forbid subprocess execution, so they need no decoder.

## Run the lab and checks from a clean checkout

Everything below is synthetic: the lab uses loopback controller, media, model and embedding services, and it never contacts Protect or an external model.

```sh
git clone https://github.com/Olli0103/unifi-ai-key-emulator.git
cd unifi-ai-key-emulator
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,database]'
.venv/bin/local-aikey lab --output lab-results.json
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
```

To match CI's locked runtime dependencies, replace the install step with `.venv/bin/python -m pip install -r requirements-runtime.lock '.[dev,database]'`.

CI runs the same `ruff` and `pytest` commands, plus a Gitleaks history scan and a dependency and SBOM check. A pull request needs all of them green.

## Tests and fixtures

- Write synthetic fixtures only: generated images and audio, invented IDs, loopback doubles. Never commit vendor files, device state or captured traffic.
- Every behaviour change needs a positive test and a negative test. The negative test must fail on the code before your change.
- A test that stands in for a remote service uses a local double. Tests never call an external provider.
- Fake credentials in tests must not look like real secrets: build them at run time instead of writing a `*_KEY = "..."` literal, or Gitleaks will flag the history.

## Evidence levels

Keep these separate in issues, pull requests and docs:

- **implemented**: the code exists.
- **fixture-tested**: synthetic tests pass in CI.
- **native-verified**: read back from a real Protect controller (an exact-event GET, a native search, the Protect UI after a reload). A callback HTTP 200, a counter or a capability flag alone is not native verification.
- **needs_evidence**: what is still missing, and who can supply it.

A parity or quality claim ("works like the AI Key", "as good as") needs native or comparative evidence. The benchmark runner (`docs/benchmark.md`) refuses comparisons between different corpora.

## Compatibility reports

Use the compatibility report template. Include the Protect version, the emulator commit, the feature, and how you read the result back. Describe what you saw in words and counts; do not attach frames, captions or identifiers.

## AI coding assistants

Assistants are welcome. You are responsible for every line you submit:

- read and run what it wrote;
- keep private data out of its inputs;
- say in the pull request that an assistant helped.

Contributor-specific handoff notes live in [docs/planning/claude-handoff.md](docs/planning/claude-handoff.md).

## Maintenance and reporting

Read [maintenance and support](MAINTAINERS.md) for review ownership, experimental
version support and triage. [Contributor conduct](CODE_OF_CONDUCT.md) applies to
maintainers, contributors and assistants. Security issues use the private route
in [SECURITY.md](SECURITY.md), never a public issue. No response SLA or stable
release support is implied by accepting a contribution.

## Origin, licensing and contribution rights

The aim is a community-maintained local AI processor compatible with UniFi
Protect. Read [release readiness](docs/legal/release-readiness.md) first.
Project-authored material is licensed under Apache-2.0. Read the root
`LICENSE` on the branch you use.
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
