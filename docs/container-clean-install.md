# Native Linux ARM64/AMD64 image installation checks

The [container-install workflow](../.github/workflows/container-install.yml)
builds and tests both processor images on separate native Linux AMD64 and ARM64
GitHub-hosted VMs. It does not use QEMU, publish images, create a Git tag, sign a
release, connect to Protect, or replace an independent tester. Standard native
Linux runners are free for this public repository according to
[GitHub's runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
checked on 4 November 2026. This is not a pricing promise for private forks.

This fills a source/CI gap in
[issue 16](https://github.com/Olli0103/unifi-ai-key-emulator/issues/16), not its
whole signed-release acceptance. Actual Apple-container networking/recovery,
native Protect installation/results, rights clearance and public release remain
separate gates in [release readiness](legal/release-readiness.md),
[Apple deployment](apple-container.md) and [NAS deployment](aiport-nas-compose.md).

## Exact inputs, installed code and image scope

Both recipes use the same immutable official Python Linux image index, resolving
to separate AMD64 and ARM64 manifests. The descriptive tag is not a floating
build input. The image build backend is pinned in
[requirements-build.lock](../requirements-build.lock); application dependencies
remain pinned separately in [requirements-runtime.lock](../requirements-runtime.lock).
The source distribution includes both Dockerfiles and both lock files. Before
this change, the actual source distribution omitted the AI Port recipe.
An actual fresh wheel installation also exposed an omitted transitive runtime
dependency: `typing_extensions`. The pinned version was already used by the
previous successful runtime CI, but the resolver had supplied it implicitly.
Images and the fresh runtime/SBOM environment now install the explicit lock with
`--no-deps` and run `pip check`; an incomplete lock cannot silently fetch an
unreviewed extra dependency.

Docker's maintained Git-context support fetches the complete immutable source
commit from one validated public GitHub repository identity. The workflow never
builds from an ignored local state directory or passes build secrets. The host
architecture and Docker server architecture must match the requested platform;
a Linux ARM64 result is **not** an Apple-container/macOS result.
The built image's OS/architecture, normal user and own source/attempt labels
must match before any image command runs. All test runs use `--pull=never` so
an absent local candidate cannot silently fall back to a registry image.

The stdlib-only [probe](../tools/container_install_check.py) receives public
source module digests, version, license digest and probe digest from the checked
out commit. Inside each image it requires the installed package to originate in
the interpreter's actual site-packages directory, not an editable checkout.
Every Python module, the distribution version and packaged LICENSE must match.
Extra/missing/changed modules, checkout shadowing and a different probe fail
before fixture construction. A help command or successful import alone would
not establish this boundary.

These defaults do not install detector/ONNX extras or model weights. Optional
model variants need their own image/platform/license qualification. Debian APT
packages are still resolved at build time: pinned base/backend/dependencies and
exact output/source provenance are not a claim of byte-identical image rebuilds
across later Debian updates. The separate package reproducibility job continues
to check wheel/sdist reproducibility within its pinned build environment.

## Isolated fixture execution

The actual installed FFmpeg executable and OpenSSL SHA-512-crypt operation are
exercised in each image. The password input is an explicitly synthetic constant
passed on stdin, never an operator credential. The image's ordinary process runs
as numeric UID/GID 10001, with a read-only root, all capabilities dropped,
no-new-privileges and `--network none`. The probe checks the observed kernel
state: only loopback exists, capabilities are empty and privilege gain is
disabled. It does not infer isolation from requested Docker flags alone.

Only one newly created, labeled test volume per profile is writable. A bounded
root initialization container has only the CHOWN capability and changes the
empty volume root to the normal image user. Probe creation refuses any
pre-existing content. It cannot initialize over an adopted installation.

Each profile then has four separately recorded processes:

| Case | Required observation |
| --- | --- |
| Create | Construct an unadopted saved profile, retain a private byte-hash baseline, run and close the existing synthetic loopback integration lab |
| Wrong source | A changed expected module digest is refused before state construction |
| Wrong architecture | A contradictory architecture is refused before state construction |
| Reopen | A new container process reuses the same volume, validates the baseline and preserves all saved identity/configuration bytes |

The local lab uses its own temporary TLS controller/model doubles, fabricated
media and a fake adoption token. It exercises application startup, authenticated
protocol/media/callback paths and saved job deduplication. Its sockets stay on
loopback inside `--network none`. The separately saved profile is never started
against a native controller; the Port constructor must remain unadopted with no
control task/listener. Local fake inference is labeled synthetic, not a model
quality or hardware-equivalence measurement.

Generated private identities, keys, credentials, their hash baseline and any
intermediate fixture material remain inside the disposable volume. They are not
uploaded. Public phase output contains only bounded source/version/count/status
metadata, not those private baseline values.

## Acknowledgement, cleanup and final receipts

Requested execution is not success. Each Docker exit code and complete typed
response must agree before its case record can be published. Unknown fields,
duplicate/nonfinite JSON, contradictory roles/revisions/platforms, booleans in
numeric fields and stale results fail closed. The two profiles must agree on
version/module count; each create/reopen pair must agree on saved-file count.

Cleanup runs even after failure, but independently validates this attempt's
scope first. It removes only labeled container IDs, the exact expected volume
names and owned local image tags. It never runs a global prune, removes a
foreign image tag or touches a deployment. A fresh successful query must show
zero remaining own containers, volumes and image tags. An empty stdout from a
failed query does not count as absence.

The final `aikey-container-install-summary/1` file is create-only and requires
all eight bound case records plus the matching zero-resource cleanup receipt.
No final passing metadata is published from a partial run, wrong exit/body
combination, missing case or unverified cleanup. Only that complete sanitized
JSON is retained by the artifact action; no image archive, fixture directory,
private key, camera frame or log bundle is uploaded.

GitHub owns job process containment and VM teardown; the job has a 25-minute
outer timeout. This tool is not a new host process supervisor and does not claim
cleanup after an abruptly lost runner. Interrupted/failed jobs without terminal
cleanup evidence remain unqualified, even if earlier cases passed. Build cache
and the runner's unrelated preinstalled images are outside the own-fixture
cleanup claim.

## Reproduce the source contracts locally

The helper's manifest/record/summary modes require only Python 3.12+. `probe` is
for the isolated Linux image, not a command to replay against real device state.
Run source tests from the normal development environment:

```sh
python -m pytest -q tests/test_container_install_check.py
python -m ruff check tools/container_install_check.py tests/test_container_install_check.py
```

Unit fixtures and the separately run Bash/fake-Docker boundary matrix do not
count as actual ARM64/AMD64 image execution. For actual evidence, read the
completed native-architecture workflow jobs and their exact-commit JSON
artifacts. They remain same-project automated installation fixtures, not the
independent real-user/native acceptance required by issues 25 and 27.

No successful installation report approves a release. Image/OS/FFmpeg notices,
corresponding-source obligations, model/dependency inventories, provenance
signing, the maintainer's signing-method decision and the exact human-approved
release snapshot must still be completed before public package/image upload.
