"""Which retroactive-processing actions Protect accepts for a stored run (#21, N10).

Protect keeps one retroactive run per AI processor record. The rules below are
Protect 7.3.60's (``startRetroactiveProcessing``, ``cancelRetroactiveProcessing``,
``pauseRetroactiveProcessing``, the resume handler and ``runRetroactiveProcessing``)
and match what 7.3.68 did live on 26 Sep 2026:

* ``start`` requires ``not_started`` on every processor. ``not_started`` is only
  the model default: no code path resets a run to it, so a ``completed`` or
  ``cancelled`` run is terminal for that processor record. Only a new record,
  that is re-adopting the AI Key under a new identity, starts over.
* ``cancel`` accepts only ``running`` or ``paused``; ``resume`` only ``paused``;
  ``pause`` only ``running``.
* A ``running`` run with ``supportRetroactiveProcessing`` off is stopped but not
  paused: it resumes when the Key reconnects with the flag on.

This module performs no I/O. It lets a tool or the admin site refuse an action
before it reaches Protect, and it explains why older events cannot be continued
once a run has completed.
"""

from __future__ import annotations

import argparse
import json

STATES = ("not_started", "running", "paused", "cancelled", "completed")
ACTIONS = ("start", "pause", "resume", "cancel")

_TERMINAL = ("A completed or cancelled run is terminal: start requires not_started on every "
             "processor and nothing resets a run to not_started except a new processor record "
             "(re-adoption, which changes the AI Key identity)")


class PlanError(ValueError):
    """The run states are missing or not ones Protect defines."""


def plan(states: list[str], *, flag_enabled: bool) -> dict:
    """Allowed and refused actions for the processors' stored run states."""
    if (not isinstance(states, list) or not states
            or any(state not in STATES for state in states) or type(flag_enabled) is not bool):
        raise PlanError("states must list Protect run states and flag_enabled must be a boolean")
    kinds = set(states)
    refused = {}
    if kinds != {"not_started"}:
        refused["start"] = (_TERMINAL if kinds & {"completed", "cancelled"}
                            else "start requires not_started on every processor")
    if not kinds <= {"running"}:
        refused["pause"] = "pause requires every processor to be running"
    if not kinds <= {"paused"}:
        refused["resume"] = "resume requires every processor to be paused"
    if not kinds <= {"running", "paused"}:
        refused["cancel"] = ("cancel accepts only running or paused runs" if not kinds & {"completed", "cancelled"}
                             else "cancel accepts only running or paused runs; " + _TERMINAL)
    if len(kinds) > 1:
        summary = "mixed"
    else:
        (summary,) = kinds
    if summary == "running" and not flag_enabled:
        summary = "stopped_by_flag"        # resumes on reconnect with the flag on
    return {"state": summary,
            "allowed": [action for action in ACTIONS if action not in refused],
            "refused": refused,
            "continue_older_events": "start" not in refused or summary in {"running", "paused",
                                                                           "stopped_by_flag"}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aikey-retroactive-plan")
    parser.add_argument("states", nargs="+", choices=STATES)
    parser.add_argument("--flag", choices=("on", "off"), required=True,
                        help="supportRetroactiveProcessing as the AI Key advertises it")
    args = parser.parse_args(argv)
    print(json.dumps(plan(args.states, flag_enabled=args.flag == "on"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
