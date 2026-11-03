# Local Key face model-space binding (3 November 2026)

Source/fictional qualification toward the model-migration requirement in #20.
Not native face grouping, consented enrollment, model quality or issue completion.
The separate AI Port 512-value adapter and Protect's own groups are unchanged.

## Confirmed gap

On unchanged parent `43f292487bcd6e12a4a7814bfd90cc7032ce4538`, a synthetic
128-value template is saved without any encoder identity and matches the same
vector without checking its origin. Both enrollment and worker transports accept
a response without model-space metadata. These demonstrate missing provenance
checks, not an observed private misidentification or four independent old bugs.
Dimension and unit norm alone cannot establish that vectors share a model space.

## Contract

The production local Face server derives `embedding_space` from a domain-separated
SHA-256 manifest containing the exact YuNet and SFace graph digests, 128 dimensions,
a fixed alignment/feature pipeline revision and the OpenCV version. File paths,
names, images and vectors are not part of this digest. Recognizer bytes are checked
before and after loading; the per-region-loaded detector bytes are checked before
and after analysis. A changed detector is refused, not labelled with the old
space. A future pipeline change must revise the manifest domain. Equivalent paths
with identical graph bytes/version have the same space. Different builds with the
same library version are not claimed bit-identical or quality-equivalent.

Each response carries its space with its vectors. The worker and enrollment helper
require a lower-case 64-hex digest using the existing bounded transports and
strict duplicate-key/nonfinite JSON refusal. The worker keeps the space with each
individual response, not shared mutable endpoint state. Missing metadata is
refused even when no face was returned. The enrollment helper returns the vector
and space together; the CLI does not let an operator manually relabel a vector.
Unqualified injected test analyzers do not receive a fabricated default identity.

New enrollments use exact schema 2 (`schema`, `embedding_space`, `identities`).
Both enrollment and matching require an explicit space; a mismatch is a fixed
failure, with no nearest-name fallback, file repair, automatic model conversion or
retry. Empty/missing legacy state may accept its first explicit-space enrollment.
A schema-2 store remains bound after its last identity is deleted; explicit purge
is required to reset it for another space. Threshold 0.363, normalization, ranking,
limits, private descriptor access, lock, exact revision and fsync/readback remain.

A digest is an accidental-mismatch guard, **not server authentication, attestation,
a trusted model registry, anonymization or recognition-quality evidence**. Existing
private endpoint/transport restrictions remain. Model mounts must be immutable
and all cooperating writers/readers upgraded; this does not defend against a
hostile privileged owner swapping and restoring model files between checks.

## Deliberate migration and mixed-version boundary

Nonempty schema-1 templates have no provable model provenance. They are readable
for metadata/list/delete and explicitly purgeable, but **cannot match or accept
additional enrollment**. They are never silently tagged as the currently installed
encoder. Existing private bytes are unchanged on refusal.

Before any real rollout, inventory and coordinate the Face server, Key worker,
CLI and Admin readers. A new worker refuses an old server without metadata; an
old reader (including the currently installed Admin258) refuses schema 2. Do not
start new schema-2 writes while old tools are still in use. Source tests do not
establish that a mixed-version installation is usable. Preserve the original
private state under its approved backup/retention policy before any owner-approved
migration. No private backup is created by this source change.

For a legacy store or a genuinely changed encoder, there is no supported vector
conversion or in-place relabel command. The operator must explicitly remove old
local identities (all entries via separate reviewed deletions, or the existing
explicit `purge` tool), then re-enroll from newly supplied, consented material.
A bound store switching spaces must be explicitly purged. An old binary rollback
cannot consume a new schema-2 store; plan source and state recovery together.
Do not restore an unrelated snapshot or automatically retry an uncertain write.
This procedure does not erase old backups, recordings, native groups, enrollment
photos/server copies or an already-read in-flight match.

## Qualification and limits

The new synthetic group covers same-dimension/different-model refusal, missing and
malformed live/saved metadata, legacy no-relabel refusal, retained metadata and
revision-checked deletion/purge, explicit fresh re-enrollment after reload, last
identity/reset semantics, graph/version/path identity, mocked graph replacement,
real loopback server/enrollment transport, per-response worker identity, duplicate
JSON, and no callback or store write after a mismatched result. Existing writer
serialization, private file, HTTP, credential isolation, deletion and data-policy
checks remain in the expanded group. Existing fixtures now explicitly identify
their fictional encoder; that is not migration of any real record.

Local qualification initially found missing space arguments in indirect fixture
writers and old fictional replies, plus two new fake-JPEG fixtures without the
required marker. These fixtures were corrected; production guards were retained.
No real image, identity, template, model inference, camera permission, profile,
controller, NAS file/service, candidate activation, dependency or release changes.
The expanded local group passed **721 cases**, 17 existing decoder-dependent
skips and four existing Pillow warnings; the final dedicated model-space group
passed **50 cases**, including later CLI and mid-job mismatch additions. The
normalizer and 13 storage/metadata/deletion methods are AST-identical to the
parent. The first Linux browser job found two indirect fictional enrollments
in the owned browser runner without the newly required space; those fixtures now
supply their fictional space. The corrected sandboxed Chrome desktop/mobile
scenario passes, owned resources are collected, and both identity components and
both confirmation screens were inspected. No production guard or UI markup was
changed to accept the fixtures. Whole-tree Ruff and diff checks pass. An initial expanded command named
a nonexistent test file and ran no tests; its corrected invocation supplied the
721-case result.

Linux CI supplies the existing decoder-backed media checks; same-agent source
qualification is not independent human review or native acceptance. #20 remains
OPEN pending the broader explicit-identity/native/quality/lifecycle evidence.
