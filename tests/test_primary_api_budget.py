"""A saved primary-only budget and type-only errors, without retries or scope changes."""
import json
import socket
import ssl
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest
from multidict import MultiDict

import aikey.aiport_api_detection as api
from aikey.aiport_candidate import CandidateError, parse_config
from aikey.aiport_config_store import AiPortConfigurationError
from test_aiport_api_detection import _ollama_config
from test_aiport_config_store import fixture
from test_primary_detection_schema import local_settings
from test_control_site_secrets import signed_in


@pytest.mark.parametrize("value", [None, True, False, 1, 61, 15.0, "30", [], {}])
def test_invalid_budget_refuses_before_key_reads_or_writes(tmp_path, value, monkeypatch):
    monkeypatch.setattr(api, "_read_private_key", lambda *_: pytest.fail("key read"))
    with pytest.raises(api.ApiDetectionError, match="invalid_primary_detection_timeout"):
        api.ApiObjectDetector({**_ollama_config(), "api_key_file": "/missing"}, tmp_path,
                              threshold=.5, request_timeout_s=value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("budget", [2, 15, 30, 60])
def test_primary_only_transport_budget_and_diagnostics(tmp_path, monkeypatch, budget):
    detector = api.ApiObjectDetector(_ollama_config(), tmp_path, threshold=.5,
                                    request_timeout_s=budget)
    seen = []
    def post(*args, timeout=15):
        seen.append((args, timeout))
        return {}
    monkeypatch.setattr(api, "_post", post)
    detector._primary_request("url", {}, {"same": "payload"})
    # OCR and crop still use the legacy callable, not the primary wrapper.
    assert detector.transport is api._DEFAULT_DETECTION_TRANSPORT
    assert seen == [(("url", {}, {"same": "payload"}), budget)]
    assert detector.diagnostic_counts("fictional")["primary_request_timeout_s"] == budget


def test_default_budget_and_custom_transport_are_unchanged(tmp_path):
    seen = []
    def custom(*args):
        seen.append(args)
        return {}
    detector = api.ApiObjectDetector(_ollama_config(), tmp_path, threshold=.5, transport=custom)
    assert detector.request_timeout_s == 15
    assert detector.transport is custom
    assert detector._primary_request("url", {}, {}) == {}
    assert seen == [("url", {}, {})]
    # Existing callers may replace the injected transport after construction.
    detector.transport = lambda *_: {"replacement": True}
    assert detector._primary_request("url", {}, {}) == {"replacement": True}


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("exception,subtype", [
    (TimeoutError("private timeout"), "timeout"),
    (ConnectionRefusedError("private address"), "connection"),
    (ssl.SSLError("private TLS"), "tls"),
    (OSError("private IO"), "io"),
    (ValueError("private text"), "unknown"),
])
def test_transport_subtype_preserves_legacy_code_without_raw_chains(monkeypatch, exception, subtype, wrapped):
    raised = URLError(exception) if wrapped else exception
    class Opener:
        def open(self, *_args, **_kwargs):
            raise raised
    monkeypatch.setattr(api, "build_opener", lambda *_: Opener())
    with pytest.raises(api.ApiDetectionError) as caught:
        api._post("http://127.0.0.1:9", {}, {})
    error = caught.value
    assert error.args == ("api_detection_request_failed",)
    assert error.subtype == subtype
    assert error.__cause__ is None and error.__context__ is None
    assert vars(error) == {"subtype": subtype}


@pytest.mark.parametrize("raised,code,category", [
    (URLError(socket.gaierror("private DNS")), "api_detection_dns_unavailable", None),
    (HTTPError("private-url", 429, "private", {}, BytesIO(b'{"error":{"code":"insufficient_quota"}}')),
     "api_detection_http_429", "quota"),
    (HTTPError("private-url", 401, "private", {}, BytesIO(b"private")), "api_detection_http_4xx", None),
    (HTTPError("private-url", 503, "private", {}, BytesIO(b"private")), "api_detection_http_5xx", None),
])
def test_dns_http_and_quota_policy_codes_preserved_without_private_context(monkeypatch, raised, code, category):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise raised
    monkeypatch.setattr(api, "build_opener", lambda *_: Opener())
    with pytest.raises(api.ApiDetectionError) as caught:
        api._post("http://127.0.0.1:9", {}, {})
    assert caught.value.args == (code,)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert getattr(caught.value, "category", None) == category


@pytest.mark.parametrize("value", [None, True, 1, 61, 15.0, "30"])
def test_invalid_saved_budget_refused_before_admitted_write(tmp_path, value):
    store, path, _ = fixture(tmp_path)
    result = store.apply(store.snapshot().revision, local_settings(tmp_path))
    before = path.read_bytes()
    raw = json.loads(before)
    raw["live_pool_detector"]["request_timeout_s"] = value
    with pytest.raises(CandidateError):
        parse_config(json.dumps(raw).encode(), state_dir=tmp_path)
    with pytest.raises(AiPortConfigurationError):
        store.apply(result.revision, local_settings(tmp_path, request_timeout_s=value))
    assert path.read_bytes() == before


async def test_optional_budget_preserved_exact_undo_and_real_runtime_factory(tmp_path, monkeypatch):
    import aikey.aiport_candidate as candidate
    store, path, _ = fixture(tmp_path)
    assert store.snapshot().request_timeout_s == 15
    result = store.apply(store.snapshot().revision, local_settings(tmp_path, request_timeout_s=30))
    saved = path.read_bytes()
    assert result.request_timeout_s == 30
    fields = local_settings(tmp_path)
    fields["model"] = "fictional-replacement"
    result = store.apply(result.revision, fields)
    assert result.request_timeout_s == 30
    loaded = candidate.load_config(path)
    service = candidate.CandidateService(loaded, tmp_path)
    seen = []
    monkeypatch.setattr(candidate, "ApiObjectDetector", lambda *a, **kw: seen.append(kw))
    try:
        service._inference._load_detector()
        assert seen[0]["request_timeout_s"] == 30
    finally:
        await service._inference.close()
        service._model_work_fence.close()
    store.rollback_provider(result.revision)
    assert path.read_bytes() == saved


def form(site, cookie):
    return MultiDict({"csrf": site.security.csrf_token(cookie), "profile": "aiport",
        "revision": site.aiports["aiport"].snapshot().revision,
        "provider": "ollama", "model": "fictional-local", "base_url": "http://127.0.0.1:9",
        "api_key": "", "max_output_tokens": "256", "threshold": ".8", "smart_types": "person",
        "max_requests_per_hour": "", "request_timeout_present": "yes", "request_timeout_s": "30"})


async def test_form_explicit_budget_legacy_preservation_and_reset(tmp_path):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    try:
        for mode, expected in [("set", 30), ("legacy", 30), ("reset", 15)]:
            data = form(site, cookie)
            if mode == "legacy":
                del data["request_timeout_present"]
                del data["request_timeout_s"]
            if mode == "reset":
                data["request_timeout_s"] = "15"
            response = await client.post("/provider", data=data, headers={"Origin": site.origin}, allow_redirects=False)
            assert response.status == 303
            assert site.aiports["aiport"].snapshot().request_timeout_s == expected
        page = await (await client.get("/")).text()
        assert "Primary detection request timeout (seconds)" in page
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("fault", ["marker_missing", "duplicate_marker", "duplicate_value", "empty", "fraction", "bool", "below", "above", "whitespace"])
async def test_malformed_form_never_writes(tmp_path, fault):
    site, server, client, cookie, _ = await signed_in(tmp_path)
    try:
        path = site.aiports["aiport"].path
        before = path.read_bytes()
        data = form(site, cookie)
        if fault == "marker_missing":
            del data["request_timeout_present"]
        elif fault == "duplicate_marker":
            data.add("request_timeout_present", "yes")
        elif fault == "duplicate_value":
            data.add("request_timeout_s", "15")
        else:
            data["request_timeout_s"] = {"empty":"", "fraction":"15.5", "bool":"true", "below":"1", "above":"61", "whitespace":" 30"}[fault]
        response = await client.post("/provider", data=data, headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 200 and "Settings not saved" in await response.text()
        assert path.read_bytes() == before
    finally:
        await client.close()
        await server.close()


def test_actual_detection_path_uses_builtin_budget_once(tmp_path, monkeypatch):
    from test_aiport_api_detection import FIRST, STILL, _response
    seen = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, limit):
            return json.dumps(_response('{"detections":[]}')).encode()
    class Opener:
        def open(self, request, *, timeout):
            seen.append((request.full_url, timeout))
            return Response()
    monkeypatch.setattr(api, "build_opener", lambda *_: Opener())
    detector = api.ApiObjectDetector(_ollama_config(), tmp_path, threshold=.5, request_timeout_s=30)
    assert detector.detect_for_camera(FIRST, STILL) == ()
    assert len(seen) == 1 and seen[0][1] == 30
    assert detector.diagnostic_counts(FIRST)["empty_responses"] == 1
