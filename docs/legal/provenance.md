# Code and research provenance

This is an evidence request, not a retrospective permission statement. A URL,
hash, code author field or claim of independent implementation does not prove
ownership, lawful access or absence of copied expression. No organizational
clean-room process has been established.

## Current evidence

The repository records AI Key 2.2.8 ARM64 binary inspection and Protect 7.2.105
JavaScript inspection in [adoption evidence](../evidence/adoption-evidence.md).
The [research index](../evidence/README.md) also names AI Port controller and
firmware research. The records distinguish interface findings from raw analysis
artifacts. This PR adds no vendor package, binary, key, source extract or media.

The current tree and Git metadata are available for audit. Commit author labels
are metadata, not proof of a legal rightsholder. Historical AI-assisted commits
also need the owner's review; do not rewrite attribution or add retroactive
sign-offs to manufacture clearance. A review against vendor code and every
historical dependency has not been completed here.

## Research evidence to retain privately

Create one record for each examined artifact, including AI Port firmware, rather
than treating one authorization as covering all vendor software.

| Field | Required record |
| --- | --- |
| Artifact | Product, software/package version, acquisition date, official location and hash |
| Access basis | Authorized user, owned/controlled product or other entitlement, applicable agreement and relevant permission |
| Interoperability question | Specific discovery, management, adoption or event behavior missing from available public documentation |
| Alternatives | Public APIs/docs considered and why they did not supply the necessary information |
| Necessity and extent | Parts inspected, method used and why the minimum analysis was needed |
| Output | Interface facts retained, where they appear in code/docs/tests, and what material was excluded |
| Code origin | Independently authored implementation, third-party portions, notices and comparison/review results |
| Disclosure | Reviewer, intended recipients/public scope, basis for distributing the necessary findings |

The owner confirms that the analyzed packages came from public downloads and
that the project was motivated by ownership of UniFi equipment. Public-download
acquisition is recorded; product-specific use rights, applicable terms and
interoperability necessity are still `needs_evidence`. General equipment
ownership does not establish rights to every AI Key or AI Port package.
The owner also confirms development outside assigned employment duties without
employer code. This records a development statement, not a legal clearance of
all private agreements or third-party contributions.

The remaining artifact-specific fields are `needs_evidence`. Do not invent purchases, permissions,
authorship separation or a legal conclusion. Publish only a reference and review
status after the owner supplies the proof. Retain detailed extracted functions,
disassemblies and private records outside public Git and build contexts.

## Component origin mapping

Use the following as a starting inventory, then cover every release component.

| Implementation | Technical reference | Rights/origin review |
| --- | --- | --- |
| AI Key control, adoption and trust | adoption-evidence.md; device-contract.md | `needs_evidence` |
| AI Key callbacks and search | controller-evidence.md; search-evidence.md | `needs_evidence` |
| AI Port discovery, control and management | ai-port-controller-contract.md; ai-port-firmware-contract.md | `needs_evidence` |
| Providers, workers, detector adapters, relays and UI | source modules, upstream public APIs and synthetic tests | `needs_evidence` |
| Synthetic fixtures | tests and independently written loopback lab | Origin/content review `needs_evidence`; synthetic execution alone proves no ownership |

Keep protocol names, schemas and short necessary interface descriptions. Review
public documentation for unnecessary vendor implementation detail, translated
functions and copied text. The existing references and offsets are traceability
records, not permission to reproduce their source. The current scan does not
substitute for this content comparison.
