# Installed console-command documentation coverage — 2 November 2026

The existing [documentation checks](../../tests/test_docs_commands.py) had a
three-command allowlist even though the package declares twenty installed
console entries. Port planning/provisioning/rollout/reconciliation, Apple
supervision/upgrade and other declared commands could be omitted from discovery.
Two added regressions failed on that parent checker: a generated list of all
actual installed entries was incomplete, and an invalid Port-plan example was
not discovered at all.

Discovery now reads the package's actual `project.scripts` table, retaining both
module and callable; existing module-invocation examples remain supported. The
real argparse entry point validates each extracted example and a sentinel stops
it immediately after parsing. No command body, deployment, model request,
credential read or service mutation is executed by the checker.

The first expanded run preserved six refusals: three exact numeric-placeholder
examples, a bare required-input-group command reference and two abbreviated
inline mode descriptions. These are **not six runtime bugs**. Three explicit
option/value placeholder pairs receive fictional numeric values for argparse
only; unknown values, flags and placeholder/option combinations remain unchanged
and refused. The two ellipsis descriptions now name modes separately from the
bare command. A required mutually-exclusive group may be omitted only for a
bare inline command reference. A further negative fixture exposed an older
exception that accepted an unknown inline flag when argparse reported missing
required arguments first. That exception is now restricted to flag-free
command/subcommand references; every flag-bearing snippet must parse fully.
Three historical Apple mode summaries are explicitly labeled abbreviated prose,
not rewritten into invented complete command receipts.
The negative Port example supplies its required input and must fail specifically
on the unknown flag before reading that fictional path.

Scope is installed project console entries plus existing module examples and
relative file targets. This is not execution of shell/Docker/tool scripts,
validation of every shell expansion or runtime value, a filesystem/network/model
acceptance test, external URL/anchor audit, independent installation or human
accessibility review. Arbitrary Markdown syntax is not a complete shell language.
The broader [#25](https://github.com/Olli0103/unifi-protect-local-ai/issues/25)
acceptance remains OPEN; this change does not authorize following deployment
examples on an existing adopted installation.

The separately completed [four-Port update](ports243-native-20261102.md) is
recorded alongside this test-only change. All application source, dependencies
and installed runtime bytes remain those already qualified in #243. This note
requires no new runtime build, full Mac-suite claim or production action.
