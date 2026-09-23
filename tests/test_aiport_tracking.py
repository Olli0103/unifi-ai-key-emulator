"""Short detections need stable, bounded tracks before native events are possible."""

import pytest

from aikey.aiport_detection import ObjectObservation
from aikey.aiport_tracking import TemporalTracker, TrackingError


def observation(kind="person", box=(0.1, 0.1, 0.4, 0.8), score=0.9):
    return ObjectObservation(kind, kind, score, box)


def test_transient_observation_never_enters_and_confirmed_track_leaves_once():
    tracker = TemporalTracker()
    assert tracker.update((observation(),), now=10.0) == ()
    first = tracker.update((observation(box=(0.11, 0.1, 0.41, 0.8)),), now=11.0)
    assert [(change.edge, change.track_id, change.kind) for change in first] == [
        ("enter", 1, "person")]
    moved = tracker.update((observation(box=(0.12, 0.1, 0.42, 0.8)),), now=12.0)
    assert [(change.edge, change.track_id) for change in moved] == [("moving", 1)]
    assert tracker.update((), now=14.0) == ()
    left = tracker.update((), now=15.1)
    assert [(change.edge, change.track_id) for change in left] == [("leave", 1)]
    assert tracker.update((), now=16.0) == ()


def test_isolated_false_positive_must_confirm_again_after_a_missed_frame():
    tracker = TemporalTracker()
    assert tracker.update((observation(),), now=1.0) == ()
    assert tracker.update((), now=2.0) == ()
    assert tracker.update((observation(),), now=3.0) == ()
    entered = tracker.update((observation(),), now=4.0)
    assert [(change.edge, change.track_id) for change in entered] == [("enter", 2)]


def test_tracks_survive_reordered_detections_without_merging_classes():
    tracker = TemporalTracker()
    person = observation()
    dog = observation("animal", (0.6, 0.2, 0.9, 0.7))
    assert tracker.update((person, dog), now=1.0) == ()
    entered = tracker.update((dog, person), now=2.0)
    assert {(change.track_id, change.kind) for change in entered} == {
        (1, "person"), (2, "animal")}
    moved = tracker.update((dog, person), now=3.0)
    assert {(change.track_id, change.kind) for change in moved} == {
        (1, "person"), (2, "animal")}


def test_full_capacity_keeps_confirmed_tracks_and_drops_new_candidates():
    tracker = TemporalTracker(max_tracks=1)
    near = observation(box=(0.1, 0.1, 0.3, 0.7))
    far = observation(box=(0.7, 0.1, 0.9, 0.7))
    tracker.update((near,), now=1.0)
    assert tracker.update((near,), now=2.0)[0].track_id == 1
    assert tracker.update((far,), now=3.0) == ()
    changes = tracker.update((near,), now=4.0)
    assert [(change.edge, change.track_id) for change in changes] == [("moving", 1)]


def test_spatial_jump_needs_new_confirmation_and_expires_old_track():
    tracker = TemporalTracker()
    near = observation(box=(0.1, 0.1, 0.3, 0.7))
    far = observation(box=(0.7, 0.1, 0.9, 0.7))
    tracker.update((near,), now=1.0)
    assert tracker.update((near,), now=2.0)[0].edge == "enter"
    assert tracker.update((far,), now=3.0) == ()
    entered = tracker.update((far,), now=4.0)
    assert [(change.edge, change.track_id) for change in entered] == [("enter", 2)]
    left = tracker.update((far,), now=5.1)
    assert ("leave", 1) in [(change.edge, change.track_id) for change in left]


def test_tracker_rejects_invalid_time_boxes_and_unbounded_input():
    tracker = TemporalTracker(max_tracks=2)
    tracker.update((), now=5.0)
    with pytest.raises(TrackingError, match="invalid_tracking_time"):
        tracker.update((), now=4.0)
    with pytest.raises(TrackingError, match="invalid_tracking_observation"):
        tracker.update((observation(box=(-0.1, 0, 0.5, 0.5)),), now=6.0)
    with pytest.raises(TrackingError, match="tracking_capacity_exceeded"):
        tracker.update((observation(),) * 101, now=6.0)
    assert tracker.update((observation(),), now=6.0) == ()
