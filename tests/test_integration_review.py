"""Regressions found while reviewing the assembled services, with no network I/O."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from aikey.embedding_profile import EmbeddingProfileError, ensure_embedding_profile
from aikey.search import EmbeddingError, SearchService
from aikey.worker import JobProcessor, WorkerError


def config():
    return {
        "runtime": {"mode": "lab"},
        "controller_origins": ["http://127.0.0.1:7444"],
        "controller": {"host": "127.0.0.1"},
        "device": {"mac": "02:00:00:00:00:01"},
        "inference": {"base_url": "http://127.0.0.1:11434/v1", "model": "configured-vision-model"},
        "embeddings": {"backend": "http", "base_url": "http://127.0.0.1:8081/v1",
                       "model": "intfloat/multilingual-e5-small", "revision": "configured-revision-1"},
        "worker": {"description_embeddings": True},
        "search": {"enabled": False},
    }


async def test_document_only_start_records_profile_before_any_query_service(tmp_path):
    options = config()
    worker = JobProcessor(options, tmp_path)
    try:
        await worker.start()
        path = tmp_path / "search-profile.json"
        document_profile = path.read_bytes()
        options["search"]["enabled"] = True
        service = SearchService(options, tmp_path)
        service.validate_configuration()
        assert service._session is None
        assert service._task is None
        assert path.read_bytes() == document_profile
        assert json.loads(document_profile)["prefixes"] == {"query": "query: ", "document": "passage: "}
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        await worker.stop()


@pytest.mark.parametrize("changed", [
    {"revision": "configured-revision-2"},
    {"base_url": "http://127.0.0.1:8082/v1"},
])
async def test_document_only_reconfiguration_is_rejected_before_sessions(tmp_path, monkeypatch, changed):
    options = config()
    worker = JobProcessor(options, tmp_path)
    await worker.start()
    await worker.stop()
    original = (tmp_path / "search-profile.json").read_bytes()
    options["embeddings"].update(changed)
    restarted = JobProcessor(options, tmp_path)
    create_session = Mock(side_effect=AssertionError("No session may start before validation"))
    monkeypatch.setattr("aikey.worker.aiohttp.ClientSession", create_session)
    try:
        with pytest.raises(WorkerError, match="profile changed"):
            await restarted.start()
        create_session.assert_not_called()
        assert restarted._tasks == []
        assert (tmp_path / "search-profile.json").read_bytes() == original
    finally:
        await restarted.stop()


async def test_query_preflight_catches_document_profile_change_without_network(tmp_path):
    options = config()
    worker = JobProcessor(options, tmp_path)
    await worker.start()
    await worker.stop()
    options["embeddings"]["revision"] = "changed-after-documents"
    options["search"]["enabled"] = True
    service = SearchService(options, tmp_path)
    with pytest.raises(EmbeddingError, match="profile changed"):
        service.validate_configuration()
    assert service._session is None
    assert service._task is None


@pytest.mark.parametrize("existing", [b"{bad json", b"x" * 16385, b"null"])
async def test_invalid_persistent_profile_is_not_repaired_or_ignored(tmp_path, existing):
    path = tmp_path / "search-profile.json"
    path.write_bytes(existing)
    worker = JobProcessor(config(), tmp_path)
    with pytest.raises(WorkerError, match="profile"):
        await worker.start()
    assert worker._session is None
    assert path.read_bytes() == existing
    await worker.stop()


def test_failed_profile_publication_does_not_leave_partial_state(tmp_path, monkeypatch):
    def fail_link(*args):
        raise OSError("Synthetic local filesystem failure")

    monkeypatch.setattr("aikey.embedding_profile.os.link", fail_link)
    with pytest.raises(EmbeddingProfileError, match="persist"):
        ensure_embedding_profile(tmp_path, {"model": "configured", "revision": "one"})
    assert list(tmp_path.iterdir()) == []


def test_concurrent_creator_cannot_overwrite_different_profile(tmp_path, monkeypatch):
    def other_writer_wins(temporary, destination):
        Path(destination).write_text('{"model":"another-profile"}\n')
        raise FileExistsError("Synthetic competing first startup")

    monkeypatch.setattr("aikey.embedding_profile.os.link", other_writer_wins)
    with pytest.raises(EmbeddingProfileError, match="profile changed"):
        ensure_embedding_profile(tmp_path, {"model": "our-profile"})
    assert json.loads((tmp_path / "search-profile.json").read_text()) == {"model": "another-profile"}
    assert [path.name for path in tmp_path.iterdir()] == ["search-profile.json"]
