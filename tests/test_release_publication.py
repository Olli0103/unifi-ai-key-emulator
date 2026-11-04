"""Fictional GitHub metadata transport. Never an actual release or signature."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_publication", Path(__file__).resolve().parents[1] / "tools/release_publication.py"
)
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


def plan():
    return {
        "tag": "v0.1.0rc1",
        "version": "0.1.0rc1",
        "workflow_run_id": "123",
        "source_revision": "a" * 40,
        "source_snapshot_sha256": "b" * 64,
        "images": {
            "aikey": "ghcr.io/olli0103/fictional-key",
            "aiport": "ghcr.io/olli0103/fictional-port",
        },
    }


class Github:
    def __init__(self, packet):
        self.packet = packet
        self.items = []
        self.writes = []
        self.after_write = "stored"

    def read(self, argv, **kwargs):
        assert argv == [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "repos/" + publication.gate.REPOSITORY + "/releases?per_page=100",
        ]
        return json.dumps([self.items])

    def write(self, argv, root):
        assert argv[:3] == ["gh", "release", "create"]
        self.writes.append(argv)
        notes = Path(argv[argv.index("--notes-file") + 1]).read_text()
        assert "--verify-tag" in argv and "--target" in argv
        assert argv[argv.index("--target") + 1] == self.packet["source_revision"]
        if self.after_write != "not_stored":
            self.items = [
                {
                    "id": 99,
                    "tag_name": self.packet["tag"],
                    "draft": True,
                    "target_commitish": "main",
                    "body": notes,
                }
            ]
        if self.after_write == "ack_lost":
            raise publication.PublicationError("release_write_outcome_uncertain")


def test_real_reservation_code_verifies_provider_not_target_branch_spelling(tmp_path):
    packet = plan()
    transport = Github(packet)
    result = publication.reserve(tmp_path, packet, run=transport.read, write=transport.write)
    assert result["phase"] == "draft_reserved" and result["release_id"] == 99
    assert result["write_acknowledged"] is True and result["provider_readback_verified"] is True
    assert len(transport.writes) == 1
    bound = dict(packet, reservation_id=99)
    assert publication.original(tmp_path, bound, run=transport.read)["id"] == 99


def test_lost_create_ack_is_reconciled_without_a_second_write(tmp_path):
    packet = plan()
    transport = Github(packet)
    transport.after_write = "ack_lost"
    result = publication.reserve(tmp_path, packet, run=transport.read, write=transport.write)
    assert result["write_acknowledged"] is False and result["provider_readback_verified"] is True
    assert len(transport.writes) == 1 and result["published"] is False


def test_ack_without_persisted_effect_is_not_a_reserved_release(tmp_path):
    packet = plan()
    transport = Github(packet)
    transport.after_write = "not_stored"
    with pytest.raises(publication.PublicationError, match="unavailable"):
        publication.reserve(tmp_path, packet, run=transport.read, write=transport.write)
    assert len(transport.writes) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "existing",
        "missing_id",
        "wrong_id",
        "wrong_source",
        "wrong_run",
        "published",
        "duplicate_marker",
        "quoted_marker",
        "duplicate_rows",
    ],
)
def test_foreign_or_ambiguous_reservation_never_authorizes_upload(tmp_path, fault):
    packet = plan()
    transport = Github(packet)
    publication.reserve(tmp_path, packet, run=transport.read, write=transport.write)
    transport.writes.clear()
    bound = dict(packet, reservation_id=99)
    if fault == "existing":
        with pytest.raises(publication.PublicationError, match="already_exists"):
            publication.reserve(tmp_path, packet, run=transport.read, write=transport.write)
        assert transport.writes == []
        return
    if fault == "missing_id":
        del bound["reservation_id"]
    elif fault == "wrong_id":
        bound["reservation_id"] = 100
    elif fault == "wrong_source":
        bound["source_revision"] = "c" * 40
    elif fault == "wrong_run":
        bound["workflow_run_id"] = "124"
    elif fault == "published":
        transport.items[0]["draft"] = False
    elif fault == "duplicate_marker":
        transport.items[0]["body"] += "\n" + publication.marker(packet)
    elif fault == "quoted_marker":
        transport.items[0]["body"] = "FICTIONAL quote:\n" + transport.items[0]["body"]
    else:
        transport.items.append(copy.deepcopy(transport.items[0]))
    with pytest.raises(publication.PublicationError):
        publication.original(tmp_path, bound, run=transport.read)
    assert transport.writes == []


def test_failed_complete_listing_is_not_absence_or_permission_to_create(tmp_path):
    packet = plan()
    transport = Github(packet)

    def failed(*args, **kwargs):
        raise publication.gate.PreparationError("release_metadata_unavailable")

    with pytest.raises(ValueError):
        publication.reserve(tmp_path, packet, run=failed, write=transport.write)
    assert transport.writes == []
