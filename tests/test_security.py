"""Deployment security checks that should fail before a release is built."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_container_is_unprivileged_and_read_only():
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "compose.yaml").read_text()

    assert "USER 10001:10001" in dockerfile
    assert "read_only: true" in compose
    assert "cap_drop: [ALL]" in compose
    assert 'security_opt: ["no-new-privileges:true"]' in compose
    assert "/tmp:rw,nosuid,noexec" in compose


def test_public_health_uses_allowlisted_status_objects():
    runtime = (ROOT / "src/aikey/runtime.py").read_text()
    assert '"device": self.device.status' in runtime
    assert '"worker": self.worker.status()' in runtime
    assert '"search": self.search.status' in runtime
    assert '"config": self.config' not in runtime
    assert "hydrate_secrets" not in runtime
