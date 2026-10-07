"""The AI Port snapshot path must stay bound to one image and one controller."""

from io import BytesIO

from PIL import Image
import pytest

from aikey.aiport_snapshots import (
    SnapshotError, make_smart_snapshot, validated_upload_url,
)
from aikey.aiport_tracking import TrackChange


def test_smart_snapshot_is_cropped_jpeg_with_matching_track():
    image = Image.new("RGB", (640, 360), "blue")
    frame = BytesIO()
    image.save(frame, format="JPEG")
    track = TrackChange("enter", 42, "person", "person", 0.91,
                        (0.25, 0.2, 0.5, 0.8))
    result = make_smart_snapshot(frame.getvalue(), track, 1_790_000_000_000)
    assert result.filename == result.metadata["smartDetectSnapshot"]
    assert result.metadata["smartDetectSnapshotType"] == "person"
    assert result.metadata["trackerID"] == 42
    event = {}
    result.add_to_event(event)
    assert event["smartDetectSnapshotFullFoV"] == result.full_fov_filename
    assert event["smartDetectSnapshotFullFoVWidth"] == 640
    assert event["smartDetectSnapshotFullFoVHeight"] == 360
    with Image.open(BytesIO(result.full_fov_jpeg)) as full_fov:
        assert full_fov.size == (640, 360)
    with Image.open(BytesIO(result.jpeg)) as crop:
        assert crop.format == "JPEG"
        assert crop.size == (result.metadata["smartDetectSnapshotWidth"],
                             result.metadata["smartDetectSnapshotHeight"])
        assert crop.width == crop.height


def test_upload_request_accepts_only_exact_pinned_controller_path():
    filename = "smartdetectsnap_zone_421790000000000.jpg"
    uri = ("https://192.168.10.1:6666/internal/camera-upload/"
           "01234567-89ab-4def-8123-0123456789ab")
    payload = {"what": "smartDetectZoneSnapshot", "filename": filename,
               "quality": "medium", "timeoutMs": 60_000, "uri": uri}
    assert validated_upload_url(payload, controller_ip="192.168.10.1",
                                filename=filename) == uri
    for bad in (uri.replace("192.168.10.1", "192.168.10.2"),
                uri.replace(":6666", ":443"),
                uri.replace("https:", "http:"),
                uri + "?next=evil", uri.replace("/internal/", "/other/"),
                uri.rsplit("/", 1)[0] + "/" + "a" * 32):
        with pytest.raises(SnapshotError):
            validated_upload_url({**payload, "uri": bad},
                                 controller_ip="192.168.10.1", filename=filename)
    with pytest.raises(SnapshotError):
        validated_upload_url({**payload, "filename": "other.jpg"},
                             controller_ip="192.168.10.1", filename=filename)


def test_a_track_inside_the_reverification_window_is_flagged_for_the_ai_key():
    frame = BytesIO()
    Image.new("RGB", (320, 240), (40, 90, 140)).save(frame, format="JPEG")
    track = TrackChange("enter", 7, "person", "person", 0.6, (0.2, 0.2, 0.5, 0.8))
    assert make_smart_snapshot(frame.getvalue(), track, 1_790_000_000_000).metadata[
        "reVerifyEligible"] is False
    assert make_smart_snapshot(frame.getvalue(), track, 1_790_000_000_000,
                               reverify_eligible=True).metadata["reVerifyEligible"] is True


def test_a_snapshot_reports_its_crop_and_monotonic_clock_like_a_native_camera():
    # Every flagged native snapshot carried framingRect and clockBestMonotonic
    # (28 Sep support file); AI Port snapshots had neither (7 Oct).
    frame = BytesIO()
    Image.new("RGB", (640, 360), (40, 90, 140)).save(frame, format="JPEG")
    track = TrackChange("leave", 7, "person", "person", 0.6, (0.345, 0.125, 0.701, 0.94))
    snapshot = make_smart_snapshot(frame.getvalue(), track, 1_790_000_000_000,
                                   reverify_eligible=True, monotonic_ms=123_456)
    metadata = snapshot.metadata
    assert metadata["clockBestMonotonic"] == 123_456
    x, y, w, h = metadata["framingRect"]
    assert all(type(v) is int for v in metadata["framingRect"])
    # The crop is square in pixels and holds the object's box.
    assert abs(w * 640 / 1000 - h * 360 / 1000) <= 2
    assert x <= 345 and x + w >= 701 and y <= 125 and y + h >= 940 and y + h <= 1000
    default = make_smart_snapshot(frame.getvalue(), track, 1_790_000_000_000).metadata
    assert type(default["clockBestMonotonic"]) is int and default["clockBestMonotonic"] > 0
