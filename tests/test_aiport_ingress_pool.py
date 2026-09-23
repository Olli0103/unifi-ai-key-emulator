"""Synthetic multi-camera stream budget, without RTSP or private devices."""

import pytest

from aikey import aiport_ingest
from aikey.aiport_ingest import AiPortIngressPool, IngressError


def policy(number):
    return {"camera_mac": f"2A11223344{number:02X}",
            "source_ip": "192.168.10.1", "ffmpeg_path": "/usr/bin/ffmpeg"}


def command(number, *, width=3840, height=2160, streaming=True):
    camera_mac = policy(number)["camera_mac"]
    if not streaming:
        return {"streaming": False, "deviceID": camera_mac}
    return {"streaming": True, "deviceID": camera_mac,
            "ip": "192.168.10.1", "port": 7447,
            "uri": f"synthetic-{number}", "width": width,
            "height": height, "fps": 15}


class FakeIngress:
    def __init__(self, *, camera_mac, source_ip, ffmpeg_path, frame_observer=None):
        self.camera_mac = camera_mac
        self.source_ip = source_ip
        self.reserved_points = 0
        self.report_healthy = True
        self.observer = frame_observer

    async def control(self, payload):
        if not payload["streaming"]:
            self.reserved_points = 0
            return {"status": "stopped", "usedPoints": 0}
        spec = aiport_ingest._stream_spec(
            payload, camera_mac=self.camera_mac, source_ip=self.source_ip)
        self.reserved_points = spec.points
        return {"status": "started", "usedPoints": spec.points}

    def list_streams(self):
        return ([{"deviceID": self.camera_mac, "points": self.reserved_points}]
                if self.reserved_points and self.report_healthy else [])

    async def close(self):
        self.reserved_points = 0


@pytest.mark.asyncio
async def test_pool_routes_only_allowlisted_cameras_and_reserves_stalled_capacity(monkeypatch):
    monkeypatch.setattr(aiport_ingest, "AiPortIngress", FakeIngress)
    pool = AiPortIngressPool([policy(1), policy(2), policy(3)])
    assert (await pool.control(command(1)))["usedPoints"] == 5
    assert (await pool.control(command(2)))["usedPoints"] == 5
    assert pool.reserved_points == 10
    assert len(pool.list_streams()) == 2
    with pytest.raises(IngressError, match="stream_capacity_exceeded"):
        await pool.control(command(3))
    pool._ingresses[policy(1)["camera_mac"]].report_healthy = False
    assert len(pool.list_streams()) == 1
    with pytest.raises(IngressError, match="stream_capacity_exceeded"):
        await pool.control(command(3))
    await pool.control(command(1, streaming=False))
    assert (await pool.control(command(3)))["usedPoints"] == 5
    assert pool.reserved_points == 10
    await pool.close()
    assert pool.reserved_points == 0


@pytest.mark.asyncio
async def test_pool_accepts_five_hd_streams_but_rejects_wrong_camera_or_source(monkeypatch):
    monkeypatch.setattr(aiport_ingest, "AiPortIngress", FakeIngress)
    pool = AiPortIngressPool([policy(number) for number in range(1, 6)])
    for number in range(1, 6):
        assert (await pool.control(command(number, width=1920, height=1080)))[
            "usedPoints"] == 2
    assert pool.reserved_points == 10
    with pytest.raises(IngressError, match="camera_not_authorized"):
        await pool.control(command(6, width=1920, height=1080))
    wrong_source = command(1, width=1920, height=1080)
    wrong_source["ip"] = "192.168.10.2"
    with pytest.raises(IngressError, match="stream_source_not_authorized"):
        await pool.control(wrong_source)
    assert pool.reserved_points == 10


def test_pool_rejects_oversized_or_duplicate_camera_policy(monkeypatch):
    monkeypatch.setattr(aiport_ingest, "AiPortIngress", FakeIngress)
    with pytest.raises(IngressError, match="invalid_camera_pool"):
        AiPortIngressPool([policy(number) for number in range(1, 7)])
    with pytest.raises(IngressError, match="duplicate_camera"):
        AiPortIngressPool([policy(1), policy(1)])
