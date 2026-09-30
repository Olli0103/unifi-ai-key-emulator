# Proposed project license and contributor rights

The owner selected Apache-2.0 during release preparation on 30 September 2026.
This draft PR includes its unmodified text as root `LICENSE` and declares it
in package metadata. It proposes licensing the project-authored material under
those terms. Main remains unchanged until a maintainer merges the PR.

License selection is not a certification of rights to every historical or
third-party contribution. The origin, contributor-rights and legal-review
release gates remain open. Dependency/model/vendor material is not relicensed
by the project license.

## Before merging the license proposal

1. Resolve historical contributor rights and review the owner's retained
   development statement. The owner has confirmed personal development outside
   assigned employment duties without employer code. That statement is not an
   employer-issued clearance or an assessment of private agreements.
2. Review the unmodified [Apache-2.0 text](https://www.apache.org/licenses/LICENSE-2.0.txt)
   included as root `LICENSE` for material the rightsholders may license. Add accurate
   notices and preserve third-party licenses. Do not label vendor artifacts,
   dependency packages or model weights as project-owned Apache code.
3. Verify the declared `Apache-2.0` metadata and packaged license file in both
   wheel and sdist. This PR adds both with a supported build backend.
4. Enable the contribution policy under that license and obtain actual
   contributor sign-offs/permissions for historical work. Do not generate
   attestations in another person's name.
5. Pass the exact-source release gate and maintainer release approval.

Apache's contributor patent grant does not establish freedom from third-party
patents, grant rights to vendor software, or settle an employer's ownership.
Dependency/model license terms remain separate.

The public project name is **Local AI processor**. Use "compatible with UniFi
Protect" to identify the target system. UniFi and Ubiquiti are trademarks of
their respective owner; this is an independent, unofficial project. Do not use
manufacturer logos or suggest endorsement. Keeping an existing repository URL
is not a trademark clearance finding. Review naming and presentation before a
public launch; this PR does not rename the repository or native device identity.
