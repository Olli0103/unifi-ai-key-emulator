"""Native-resolution subprocesses are bounded, isolated and always collected."""

import concurrent.futures
import json
import subprocess
import threading
import time

import pytest

from aikey import provider_dns as dns, provider_http
from aikey import aiport_api_detection as api
from aikey.provider_network import ProviderAddressError
from test_aiport_api_detection import FIRST, STILL
from test_provider_http import Socket


def processes(monkeypatch):
    original, made = subprocess.Popen, []
    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        made.append((process, args, kwargs))
        return process
    monkeypatch.setattr(dns.subprocess, "Popen", start)
    return made


def test_real_localhost_resolution_has_no_credential_environment_or_extra_descriptors(monkeypatch):
    made = processes(monkeypatch)
    monkeypatch.setenv("SYNTHETIC_PROVIDER_TOKEN", "synthetic-env-canary")
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic-proxy-canary.invalid:1")
    rows = dns.resolve_addresses("localhost", 8765, 5)
    assert rows and all(row[4][0] in {"127.0.0.1", "::1"} and row[4][1] == 8765 for row in rows)
    process, args, kwargs = made[0]
    assert process.poll() == 0 and process.stdin.closed and process.stdout.closed
    assert args[0][1:4] == ["-I", "-S", "-c"] and "localhost" not in args[0]
    assert set(kwargs["env"]) == {"PATH", "LANG", "LC_ALL"} and kwargs["close_fds"]


def test_stalled_native_resolution_is_killed_and_collected_at_the_deadline(monkeypatch):
    made = processes(monkeypatch)
    monkeypatch.setattr(dns, "_HELPER", "import time;time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(dns.ProviderDNSTimeout, match="^provider_dns_deadline$"):
        dns.resolve_addresses("localhost", 443, .15)
    assert time.monotonic() - started < 1.5
    assert len(made) == 1 and made[0][0].poll() is not None
    assert made[0][0].stdin.closed and made[0][0].stdout.closed


@pytest.mark.parametrize("value", [[], {"rows": []}, {"rows": [[True, 1, 6, "", ["127.0.0.1", 443]]]},
    {"rows": [[2, 1, 6, "", ["127.0.0.1", True]]]},
    {"rows": [[2, 1, 6, "", ["127.0.0.1", 444]]]},
    {"rows": [[10, 1, 6, "", ["::1", 443, False, 0]]]},
    {"rows": [[2, 1, 6, "", ["127.0.0.1", 443]]], "unexpected": "synthetic-canary"},
    {"policy": True}, {"rows": [[2, 1, 6, "", ["127.0.0.1", 443]]] * 65},
])
def test_malformed_or_excess_resolver_output_is_a_fixed_policy_refusal(monkeypatch, value):
    monkeypatch.setattr(dns, "_HELPER", "print(" + repr(json.dumps(value)) + ")")
    with pytest.raises(ProviderAddressError, match="^provider_address_policy_refused$"):
        dns.resolve_addresses("localhost", 443, 5)


@pytest.mark.parametrize("helper", ["print('not-json-synthetic-canary')", "raise RuntimeError('synthetic-canary')",
                                     "print('{\"unavailable\":true}')"])
def test_failure_subtypes_never_include_resolver_payload(monkeypatch, helper):
    monkeypatch.setattr(dns, "_HELPER", helper)
    with pytest.raises((ProviderAddressError, dns.ProviderLookupError, dns.ProviderResolverError)) as error:
        dns.resolve_addresses("localhost", 443, 5)
    assert "canary" not in str(error.value).lower()


def test_cancellation_collects_the_child_and_returns_its_admission_slot(monkeypatch):
    original, made = subprocess.Popen, []
    def interrupt(*args, **kwargs):
        process = original(*args, **kwargs)
        made.append(process)
        def communicate(*a, **kw):
            raise KeyboardInterrupt()
        process.communicate = communicate
        return process
    monkeypatch.setattr(dns, "_HELPER", "import time;time.sleep(30)")
    monkeypatch.setattr(dns.subprocess, "Popen", interrupt)
    with pytest.raises(KeyboardInterrupt):
        dns.resolve_addresses("localhost", 443, 5)
    assert len(made) == 1 and made[0].poll() is not None
    assert made[0].stdin.closed and made[0].stdout.closed
    assert dns._SLOTS.acquire(blocking=False)
    dns._SLOTS.release()


def test_at_most_four_resolver_children_and_busy_admission_is_bounded(monkeypatch):
    made, condition = [], threading.Condition()
    original = subprocess.Popen
    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        with condition:
            made.append(process)
            condition.notify_all()
        return process
    monkeypatch.setattr(dns.subprocess, "Popen", start)
    monkeypatch.setattr(dns, "_HELPER", "import time;time.sleep(30)")
    def lookup():
        with pytest.raises(dns.ProviderDNSTimeout):
            dns.resolve_addresses("localhost", 443, 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(lookup) for _ in range(4)]
        with condition:
            assert condition.wait_for(lambda: len(made) == 4, timeout=.8)
        started = time.monotonic()
        with pytest.raises(dns.ProviderDNSTimeout):
            dns.resolve_addresses("localhost", 443, .05)
        assert len(made) == 4 and time.monotonic() - started < .8
        for future in futures:
            future.result(timeout=3)
    assert all(process.poll() is not None for process in made)


def test_dns_deadline_never_opens_http(monkeypatch):
    sockets = []
    monkeypatch.setattr(dns, "_HELPER", "import time;time.sleep(30)")
    monkeypatch.setattr(provider_http.socket, "socket", lambda *a: sockets.append(a))
    with pytest.raises(dns.ProviderDNSTimeout):
        provider_http._connect(("localhost", 443), .1)
    assert sockets == []


def test_literal_ip_needs_no_resolver_process(monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Literal IP resolution must not start a child")
    monkeypatch.setattr(dns.subprocess, "Popen", forbidden)
    connection = Socket()
    monkeypatch.setattr(provider_http.socket, "socket", lambda *a: connection)
    assert provider_http._connect(("127.0.0.1", 443), 1) is connection
    assert connection.peer == ("127.0.0.1", 443)


def test_detection_preflight_deadline_is_distinct_and_preserves_paid_allowance(tmp_path, monkeypatch):
    key = tmp_path / "synthetic-key"
    key.write_text("synthetic-only")
    key.chmod(0o600)
    detector = api.ApiObjectDetector({"provider": "openai", "model": "gpt-6-luna",
        "api_key_file": str(key), "allow_remote": True}, tmp_path, threshold=.8,
        max_requests_per_hour=2)
    calls = []
    detector.transport = lambda *a: calls.append("http")
    def timeout(*args):
        calls.append("dns")
        assert args[-1] == 5
        raise dns.ProviderDNSTimeout()
    monkeypatch.setattr(api, "resolve_addresses", timeout)
    for _ in range(2):
        with pytest.raises(api.ApiDetectionError, match="^api_detection_dns_timeout$"):
            detector.detect_for_camera(FIRST, STILL)
    assert calls == ["dns"] and detector.budget.remaining(FIRST) == 2
    assert detector._fallback_reason(api.ApiDetectionError("api_detection_dns_timeout")) == "dns_timeout"


def test_provider_post_preserves_dns_deadline_subtype(monkeypatch):
    def timeout(*args):
        raise dns.ProviderDNSTimeout()
    monkeypatch.setattr(provider_http, "resolve_addresses", timeout)
    with pytest.raises(api.ApiDetectionError, match="^api_detection_dns_timeout$"):
        api._post("https://localhost:8443/v1/probe", {}, {})


def test_helper_start_failure_is_not_misreported_as_dns_outage_or_fallback(monkeypatch):
    def unavailable(*a, **kw):
        raise OSError("SYNTHETIC-EXEC-CANARY")
    monkeypatch.setattr(dns.subprocess, "Popen", unavailable)
    with pytest.raises(dns.ProviderResolverError, match="^provider_resolver_unavailable$"):
        dns.resolve_addresses("localhost", 443, 1)
    with pytest.raises(api.ApiDetectionError, match="^api_detection_resolver_unavailable$"):
        api._post("https://localhost:8443/v1/probe", {}, {})
    assert api.ApiObjectDetector._fallback_reason(api.ApiDetectionError("api_detection_resolver_unavailable")) is None
