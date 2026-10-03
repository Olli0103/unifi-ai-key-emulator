"""Protect's retroactive run state rules (#21, N10)."""

import json

import pytest

from aikey.retroactive_plan import PlanError, main, plan


def test_a_completed_run_cannot_be_continued_or_restarted():
    result = plan(["completed"], flag_enabled=True)
    assert result["state"] == "completed" and result["allowed"] == []
    assert "re-adoption" in result["refused"]["start"] and "terminal" in result["refused"]["cancel"]
    assert result["continue_older_events"] is False


def test_a_cancelled_run_is_terminal_too():
    result = plan(["cancelled"], flag_enabled=True)
    assert result["allowed"] == [] and result["continue_older_events"] is False


def test_only_a_fresh_processor_record_may_start():
    result = plan(["not_started"], flag_enabled=True)
    assert result["allowed"] == ["start"] and result["continue_older_events"] is True


def test_running_and_paused_runs_continue_through_pause_resume_and_cancel():
    running = plan(["running"], flag_enabled=True)
    assert running["allowed"] == ["pause", "cancel"] and running["continue_older_events"] is True
    paused = plan(["paused"], flag_enabled=True)
    assert paused["allowed"] == ["resume", "cancel"] and paused["continue_older_events"] is True


def test_the_flag_off_stops_a_running_run_without_pausing_it():
    result = plan(["running"], flag_enabled=False)
    assert result["state"] == "stopped_by_flag" and result["continue_older_events"] is True
    assert "resume" in result["refused"]


def test_mixed_processor_states_refuse_start_and_resume():
    result = plan(["completed", "not_started"], flag_enabled=True)
    assert result["state"] == "mixed" and "start" in result["refused"]
    assert result["continue_older_events"] is False


@pytest.mark.parametrize("states", [[], ["done"], "completed", [None]])
def test_unknown_states_are_refused(states):
    with pytest.raises(PlanError):
        plan(states, flag_enabled=True)


def test_the_cli_reports_the_plan(capsys):
    assert main(["completed", "--flag", "on"]) == 0
    assert json.loads(capsys.readouterr().out)["continue_older_events"] is False
