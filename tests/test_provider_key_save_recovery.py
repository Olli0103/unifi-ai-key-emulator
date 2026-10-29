"""A failed save acknowledgement must not remove a committed provider key."""

import json
from pathlib import Path

import pytest

from aikey.config_store import ConfigurationStoreError, RevisionConflict
from aikey.aiport_config_store import AiPortConfigurationError, AiPortRevisionConflict
from test_control_site_secrets import signed_in, provider_form, SECRET


@pytest.mark.parametrize("profile", ["aikey", "aiport"])
@pytest.mark.parametrize("superseded", [False, True])
async def test_error_after_committed_provider_change_preserves_its_new_key(
        tmp_path, monkeypatch, profile, superseded):
    site, server, client, cookie, key_path = await signed_in(tmp_path)
    try:
        store = site.aikey if profile == "aikey" else site.aiports["aiport"]
        method = "apply_inference" if profile == "aikey" else "apply"
        real = getattr(store, method)
        error = ConfigurationStoreError if profile == "aikey" else AiPortConfigurationError

        def interrupted(*args):
            real(*args)
            if superseded:
                # Another successful change can archive the first new key before
                # its caller receives an error. Reading only live config is unsafe.
                other = {**args[1], "provider": "ollama", "model": "synthetic-second-model",
                         "base_url": "http://127.0.0.1:11434"}
                other.pop("api_key_file", None)
                real(store.snapshot().revision, other)
            raise error("synthetic failure after atomic config replacement")

        monkeypatch.setattr(store, method, interrupted)
        fields = provider_form(site, cookie, profile=profile, revision=store.snapshot().revision)
        if profile == "aiport":
            fields.update(threshold="0.8", smart_types="person", max_requests_per_hour="")
        response = await client.post("/provider", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        body = await response.text()
        assert response.status == 200 and SECRET not in body
        assert "Save outcome uncertain" in body and "Reload settings before retrying" in body
        raw = json.loads(store.path.read_text())
        provider = (raw["inference"] if profile == "aikey"
                    else raw["live_pool_detector"]["provider_config"])
        if superseded:
            assert "api_key_file" not in provider
            assert store.provider_rollback_status()["available"] is True
            store.rollback_provider(store.snapshot().revision)
            raw = json.loads(store.path.read_text())
            provider = (raw["inference"] if profile == "aikey"
                        else raw["live_pool_detector"]["provider_config"])
        referenced = store.path.parent / Path(provider["api_key_file"]).name
        assert referenced.is_file(), "a committed key was deleted after a failed acknowledgement"
        assert referenced.read_text().strip() == SECRET
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("profile", ["aikey", "aiport"])
async def test_proven_revision_conflict_after_preview_discards_only_the_unused_key(tmp_path, monkeypatch, profile):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    try:
        store = site.aikey if profile == "aikey" else site.aiports["aiport"]
        method = "apply_inference" if profile == "aikey" else "apply"
        conflict = RevisionConflict if profile == "aikey" else AiPortRevisionConflict
        original = store.path.read_bytes()

        def rejected(*args):
            raise conflict("synthetic concurrent save")

        monkeypatch.setattr(store, method, rejected)
        fields = provider_form(site, cookie, profile=profile, revision=store.snapshot().revision)
        if profile == "aiport":
            fields.update(threshold="0.8", smart_types="person", max_requests_per_hour="")
        response = await client.post("/provider", data=fields,
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert "Settings not saved" in await response.text()
        assert store.path.read_bytes() == original and not list(store.path.parent.glob("provider-key-*"))
    finally:
        await client.close()
        await server.close()
