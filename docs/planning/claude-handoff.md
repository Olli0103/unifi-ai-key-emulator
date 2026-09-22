# Contributor and Claude handoff

Use the linked [roadmap](../../PLAN.md) and [issue index](issues.md). The public source and synthetic lab are the default workspace. Give each contributor one bounded issue and check its dependencies before dispatch.

## First work packets

- Versioned compatibility manifest and sanitized fixture structure. Do not invent native contracts that have no evidence.
- Security threat model, administration contract and adversarial test plan. Agree on shared configuration/auth interfaces before building the UI.
- License/provenance inventory and open-source release preparation. License and naming decisions belong to the maintainer.

After these contracts are reviewed, provider configuration and camera inventory can proceed independently. Scheduling consumes the registry contract. The control site consumes the admin API contract. Search and legacy implementation wait for the corresponding native feasibility proof.

## Copyable task prompt

```text
Work on ISSUE_URL in Olli0103/unifi-ai-key-emulator from PUBLIC_COMMIT.
Read PLAN.md and the issue, including dependencies and acceptance checks.
If a dependency or native contract is missing, report needs_evidence with
an exact proposed interface or experiment. Do not invent compatibility.

Use a separate branch/worktree. Keep edits within the issue's agreed
modules. Ask the integration owner before changing shared contracts.
Use public source and synthetic fixtures only. Do not read ignored state,
secret files, browser sessions or camera media. Do not deploy, modify a
controller, enable capabilities or call paid inference services.

Implement the agreed scope and meaningful positive/negative tests.
Preserve the current adoption and persisted-caption behavior. Do not
replace a native workflow with an external-only result and call it parity.
Model/index compatibility must be checked, not inferred from vector width.

Return the commit/PR, changed behavior, checks run with results, assumptions,
remaining native acceptance steps and any needs_evidence blockers.
Do not mark native acceptance complete based on mocks or callback HTTP 200.
```

## Review and merge

The integration owner reviews implementation, security boundaries, tests and public-output redaction. Run relevant local checks, then separately run the issue's native acceptance on authorized equipment. Close the issue only when its acceptance evidence exists, or explicitly split out a remaining requirement without claiming full parity.

A planning review by Claude is not implementation or acceptance. Repository access does not grant access to private footage or deployment credentials. Release and deployment remain with the maintainer/integration owner.
