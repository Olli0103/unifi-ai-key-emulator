"""The #12 Action 2 deploy list matches the AI Key runtime it claims to cover (#1, #12)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "planning" / "continuous-caption-rollout.md"
PACKAGE = ROOT / "src" / "aikey"


def runtime_closure() -> set[str]:
    """Modules the Key process can import from its entry points, lazy imports included."""
    seen, todo = set(), ["cli", "runtime"]
    while todo:
        name = todo.pop()
        if name in seen or not (PACKAGE / f"{name}.py").is_file():
            continue
        seen.add(name)
        source = (PACKAGE / f"{name}.py").read_text()
        todo += re.findall(r"from \.(\w+) import", source) + re.findall(r"from \. import (\w+)", source)
    return seen


def planned_delta() -> list[str]:
    text = PLAN.read_text()
    section = text[text.index("**Deploy delta**"):text.index("**Behavior changes with the current config**")]
    heading, listing = section.split("\n\n")[0].split("\n", 1)      # the list follows its heading line
    return re.findall(r"`(\w+)`", listing)


def test_every_planned_module_runs_in_the_key_process():
    delta = planned_delta()
    assert len(delta) == len(set(delta)) == 10
    assert set(delta) <= runtime_closure()


def test_modules_new_since_the_live_lineage_are_shipped_with_their_importers():
    closure, delta = runtime_closure(), set(planned_delta())
    # worker imports worker_archive; device imports state_schema lazily. Shipping
    # worker or device without them would fail at import or at state load.
    assert {"worker_archive", "state_schema"} <= closure
    assert {"worker", "worker_archive", "device", "state_schema"} <= delta


def test_host_tools_are_not_claimed_as_image_modules():
    delta = set(planned_delta())
    assert not {"caption_preflight", "uncertain_resolution", "apple_upgrade"} & delta
    assert not {"caption_preflight", "uncertain_resolution"} & runtime_closure()


def test_the_plan_states_the_live_behavior_changes():
    text = PLAN.read_text()
    assert "Behavior change with the current config:** none" not in text
    for marker in ("Restores Find Anything indexing", "job_identity_conflict", "Abort and roll back",
                   "needs_evidence (not changed by this deploy)"):
        assert marker in text
