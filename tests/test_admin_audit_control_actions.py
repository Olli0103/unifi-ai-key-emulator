"""Real form boundaries with auditing enabled; fixed events must not cause HTTP 500."""

import json

import pytest

from aikey.admin_audit import AuditLog
from aikey.config_store import ConfigurationStoreError
from test_control_site_secrets import signed_in
from test_caption_camera_enrollment import setup as caption_setup, preview as caption_preview, consent
from test_worker_limits_control import configured, preview as limits_preview


async def fixture(tmp_path, flow):
    if flow == "caption_enrollment":
        site, server, client, cookie, path, _ = await caption_setup(tmp_path)
    else:
        site, server, client, cookie, path = await signed_in(tmp_path)
        configured(path)
    return site, server, client, cookie, path


async def review(site, client, cookie, flow):
    if flow == "caption_enrollment":
        grant, _ = await caption_preview(site, client, cookie)
        return consent(grant)
    grant = await limits_preview(site, client, cookie)
    return {**grant, "confirm": "yes", "acknowledge_load": "yes"}


@pytest.mark.parametrize("flow", ["caption_enrollment", "worker_limits"])
@pytest.mark.parametrize("operation", ["preview", "rejected", "saved", "uncertain"])
async def test_form_actions_remain_usable_with_private_audit(tmp_path, monkeypatch, flow, operation):
    site, server, client, cookie, path = await fixture(tmp_path, flow)
    route = "/caption-camera-enrollment" if flow == "caption_enrollment" else "/worker-limits"
    action = flow
    try:
        before = path.read_bytes()
        if operation in {"preview", "rejected"}:
            site.audit = AuditLog(tmp_path / "private-audit.jsonl")
        if operation == "rejected":
            response = await client.post(route + "/preview", data={
                "csrf": site.security.csrf_token(cookie)}, headers={"Origin": site.origin})
            assert response.status == 409
            outcome = "rejected"
        else:
            payload = await review(site, client, cookie, flow)
            if operation == "preview":
                outcome = "previewed"
            else:
                site.audit = AuditLog(tmp_path / "private-audit.jsonl")
                if operation == "uncertain":
                    name = "enroll_caption_cameras" if flow == "caption_enrollment" else "apply_worker_limits"
                    original = getattr(site.aikey, name)
                    def lost_ack(*args):
                        original(*args)
                        raise ConfigurationStoreError("fictional lost acknowledgement")
                    monkeypatch.setattr(site.aikey, name, lost_ack)
                response = await client.post(route + "/apply", data=payload,
                    headers={"Origin": site.origin}, allow_redirects=False)
                assert response.status == (503 if operation == "uncertain" else 200)
                outcome = operation
                assert path.read_bytes() != before
        if operation in {"preview", "rejected"}:
            assert path.read_bytes() == before
        entries = site.audit.recent()
        assert [(r["action"], r["result"], r["profile"]) for r in entries] == [(action, outcome, "aikey")]
        assert site.audit.failures == 0
        audit = site.audit.path.read_text()
        assert site.audit.path.stat().st_mode & 0o777 == 0o600
        assert set(json.loads(audit)) == {"action", "at", "profile", "result"}
        assert all(v not in audit for v in (str(tmp_path), "Fictional", "camera_ids", "provider-key", "127.0.0.1"))
    finally:
        await client.close()
        await server.close()
