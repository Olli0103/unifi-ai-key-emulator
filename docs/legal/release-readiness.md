# Community release readiness

The aim is a useful, independently implemented local AI processor compatible
with UniFi Protect. This branch responds to the owner's legal first assessment
of commit `4c71b732723ce2ccaec0a540736675789db9173d` on 30 September 2026.
It reduces technical risks, proposes the owner's selected Apache-2.0 license
for project code and records missing evidence. It does not declare the project
legally cleared or grant rights on behalf of unknown third-party rightsholders.

The audit baseline is the reviewed main commit. Opus's separate feature PR
#123 is outside this audit snapshot. After incorporating it or changing any
release input, refresh the dependency/content/origin review and fingerprint.
A prior approval must not silently extend to new code or model components.

The [audit scope record](audit-scope.json) reports a filename and binary-header
scan of 104 reachable base commits and 551 blobs. It found no matching
prohibited filenames or listed binary headers. This is not a secret scan,
protected-expression comparison or rights clearance.

## What this change establishes

- `diagnostic_resume_until` is rejected and tokenless adoption-state creation
  is removed. Existing saved controller-bound adoption and management-token
  adoption retain their synthetic tests. This branch changes no deployed state.
- Source and package checks reject firmware-like binaries, model checkpoints,
  private keys, state, research extracts and camera media. The existing full-history
  Gitleaks workflow remains a separate secret check.
- An evidence record distinguishes pending legal and ownership work from
  engineering results. A green automated audit cannot close human review gates.
- The public title is Local AI processor, with UniFi Protect as a compatibility
  description. No manufacturer affiliation or clean-room certification is claimed.

## Required evidence before release

| Gate | Evidence needed | Current state |
| --- | --- | --- |
| Code origin | File/component origin map, review for copied or translated protected expression, notices for third-party code | `needs_evidence` |
| Firmware authorization | Authorized user's access basis for each firmware/controller artifact, applicable terms and acquisition date | `needs_evidence` |
| Interoperability scope | Missing public information, necessity, minimum analyzed parts and permitted disclosure for each finding | `needs_evidence` |
| Contributor rights | All contributors' rights and consent to the chosen license, including historical contributions | `needs_evidence` |
| Employer rights | Owner confirms development outside assigned duties without employer code; agreement-specific questions remain for the legal review | Owner statement recorded |
| Project license | Owner-selected Apache-2.0 text and package metadata included in this draft; contributor/origin review remains separate | Proposal prepared |
| AI Port adoption | Separate test identity, generated keys, regular administrator token, pinned reconnect and rejection cases on an unmodified controller | `needs_evidence` |
| Dependencies | Exact resolved distributions, licenses, source and notice obligations for core and extras | `needs_evidence` |
| Images | Exact digest, base/OS packages, FFmpeg build flags, applicable license and corresponding source/notice delivery | `needs_evidence` |
| Models | Each checkpoint's version, hash, origin, weights license and conditions separate from its runtime code | `needs_evidence` |
| History | Full commit-history material/origin review, beyond a current-tree scan and Gitleaks | `needs_evidence` |
| Legal review | Targeted review of firmware rights, interoperability, EULA, adoption and trademark presentation | `needs_evidence` |

Only evidence-backed entries may move to `verified` in
[release-evidence.json](release-evidence.json), with a reviewer and a content-free reference to the retained record. Keep purchase records, employment
agreements, permissions and other private proof outside the repository. An
agent must not certify facts it cannot know or sign an owner's attestation.

## Checks

```sh
python tools/community_release_check.py --audit-only
python -m build
python tools/community_release_check.py --audit-only --dist dist
python tools/community_release_check.py --dist dist
```

The first two audits are automated content/structure checks. CI runs these and
regression tests; passing them does not approve publication. The final command
is intentionally blocked until the required evidence and an active `LICENSE`
are recorded. This draft now includes the proposed Apache license, but other
required evidence remains missing. Its source fingerprint must match the approved source inputs. A change
to tracked inputs invalidates the fingerprint. This records review of a
specific tree; it cannot prove the truth or legal sufficiency of attestations.

This repository has no publishing workflow. Any future package, image or release
publisher must require the final check and a maintainer approval before upload.
These checks do not restrict GitHub's existing source visibility or prevent
someone distributing a local copy. Inspect the history separately; deleting a
file at HEAD does not remove it from earlier commits. Do not rewrite history
or discard evidence as part of this PR.

## Owner review packet

Complete [provenance](provenance.md), [third-party distribution](third-party-distribution.md)
and [licensing](licensing.md). Have the qualified reviewer assess the release
snapshot and retained private evidence. In particular, ask for analysis of
UrhG §§ 69a, 69d, 69e, 69f and 69g, the applicable EULA and terms, rights under
§ 69b, and trademark presentation under MarkenG § 23. This checklist is an
engineering response to the owner's assessment, not a new legal opinion.

Primary references checked on 30 September 2026:

- [German Copyright Act](https://www.gesetze-im-internet.de/urhg/), especially
  [§ 69e](https://www.gesetze-im-internet.de/urhg/__69e.html) and
  [§ 69b](https://www.gesetze-im-internet.de/urhg/__69b.html).
- [Ubiquiti EULA](https://www.ui.com/eula/), especially II(b). Determine the
  terms actually accepted at acquisition/use; the currently published text
  does not prove historical incorporation or enforceability.
- [FFmpeg legal guidance](https://ffmpeg.org/legal.html). Determine obligations
  from the exact build rather than assigning every FFmpeg binary the same license.
- [Apache-2.0 text](https://www.apache.org/licenses/LICENSE-2.0.txt).
