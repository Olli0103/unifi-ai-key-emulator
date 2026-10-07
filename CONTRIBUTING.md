# Contributing

Thanks for helping. This project emulates the UniFi AI Key and AI Port so they work with UniFi Protect. It handles controller credentials, camera media and provider keys, so a few rules matter more than usual.

## Never post private material

Do not put credentials, API keys, device passwords, camera images, recordings, transcripts, face or plate data, private hostnames, IP plans or controller support files in an issue, pull request, test or fixture. Security problems go through [SECURITY.md](SECURITY.md), never a public issue.

## Run the lab and checks from a clean checkout

Python 3.12 or newer. Everything below is synthetic: the lab uses loopback controller, media, model and embedding services, and it never contacts Protect or an external model.

```sh
git clone https://github.com/Olli0103/unifi-ai-key-emulator.git
cd unifi-ai-key-emulator
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,database]'
.venv/bin/local-aikey lab --output lab-results.json
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
```

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

## Still to be decided by the maintainer

These are open in issue #11 and are not promises yet:

- the licence and contribution sign-off (issue #4);
- the code of conduct;
- review ownership, the support and version policy, and triage labels;
- the private vulnerability-reporting route.
