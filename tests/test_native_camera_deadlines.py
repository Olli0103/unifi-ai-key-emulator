"""Late CPU settlement cannot turn an expired metadata exchange into success."""

import pytest

from aikey import native_camera_status
from aikey.native_camera_status import NativeStatusError
from test_native_camera_status import controller


async def test_parse_settling_after_the_exchange_deadline_is_not_a_success(tmp_path, monkeypatch):
    async with controller(tmp_path, monkeypatch) as (reader, seen, _):
        clock = [100.0]
        reader._clock = lambda: clock[0]
        original = native_camera_status.parse_bootstrap

        def late_parse(value, *, observed_at):
            result = original(value, observed_at=observed_at)
            clock[0] += 20
            return result

        monkeypatch.setattr(native_camera_status, "parse_bootstrap", late_parse)
        with pytest.raises(NativeStatusError, match="native_status_unavailable"):
            await reader.read()
        assert reader._cache is None and seen[-1] == ("POST", "/api/auth/logout")


async def test_logout_settling_after_its_deadline_is_an_uncertain_cleanup(tmp_path, monkeypatch):
    clock = [100.0]

    def late_logout():
        clock[0] += 4

    async with controller(tmp_path, monkeypatch, logout_hook=late_logout) as (reader, seen, _):
        reader._clock = lambda: clock[0]
        with pytest.raises(NativeStatusError, match="native_session_cleanup_unconfirmed"):
            await reader.read()
        assert reader._cache is None and reader._cleanup_uncertain is True
        assert seen[-1] == ("POST", "/api/auth/logout")
