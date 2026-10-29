"""Provider outage diagnostics for the AI Port detector (#6). Synthetic replies only."""

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest
from PIL import Image

from aikey.aiport_api_detection import (
    HTTP_429_CATEGORIES, ApiDetectionError, ApiObjectDetector, _http_429_category, _post,
)
from aikey.aiport_inference import FairInference

CAMERA = "2A1122334455"
SECRET = "-".join(("synthetic", "provider", "credential"))
MESSAGE = f"private provider message for {SECRET} camera {CAMERA} https://private.invalid/x"
STATUS_KEYS = {"api_last_provider_success_at", "api_provider_outage_since",
               "api_consecutive_provider_failures", "api_http_429_categories",
               "api_current_429_category"}


def _frame(color):
    data = BytesIO()
    Image.new("RGB", (64, 64), color).save(data, format="JPEG")
    return data.getvalue()


STILL, FRAME = _frame("black"), _frame("white")


def _config():
    return {"provider": "ollama", "model": "synthetic-vision",
            "base_url": "http://127.0.0.1:11434", "max_output_tokens": 128}


def _ok():
    return {"done": True, "message": {"content": '{"detections":[]}'}}


def _body(code=None, kind=None, *, anthropic=False):
    error = {"message": MESSAGE}
    if code is not None:
        error["code"] = code
    if kind is not None:
        error["type"] = kind
    return json.dumps({"type": "error", "error": error} if anthropic else {"error": error}).encode()


def _opener(monkeypatch, status, body):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise HTTPError("https://private.invalid/v1", status, MESSAGE,
                            {"x-request-id": SECRET}, BytesIO(body))

    monkeypatch.setattr("aikey.aiport_api_detection.build_opener", lambda *_args: Opener())


@pytest.mark.parametrize("body,category", [
    (_body("insufficient_quota", "insufficient_quota"), "quota"),       # OpenAI: credit or budget
    (_body("billing_hard_limit_reached"), "quota"),
    (_body("rate_limit_exceeded", "requests"), "rate"),                 # OpenAI: RPM
    (_body("rate_limit_exceeded", "tokens"), "rate"),                   # OpenAI: TPM
    (_body(kind="rate_limit_error", anthropic=True), "rate"),           # Anthropic
    (_body("insufficient_quota", "requests"), "unknown"),               # codes disagree
    (_body("something_new", "other"), "unknown"),
    (_body(), "unknown"),                                                # message only
    (json.dumps({"error": {"code": {"nested": "insufficient_quota"}}}).encode(), "unknown"),
    (json.dumps({"error": "insufficient_quota"}).encode(), "unknown"),  # not an object
    (json.dumps(["insufficient_quota"]).encode(), "unknown"),
    (b"insufficient_quota rate_limit_exceeded", "unknown"),             # plain text is never matched
    (b"", "unknown"),
    (b"\xff\xfe", "unknown"),
])
def test_only_unambiguous_machine_readable_codes_classify_a_429(body, category):
    assert _http_429_category(body) == category


def test_a_429_carries_its_category_and_nothing_from_the_reply(monkeypatch):
    _opener(monkeypatch, 429, _body("insufficient_quota", "insufficient_quota"))
    with pytest.raises(ApiDetectionError) as failure:
        _post("http://127.0.0.1:11434/api/chat", {}, {})
    error = failure.value
    assert error.args == ("api_detection_http_429",) and error.category == "quota"
    assert error.__cause__ is None and error.__suppress_context__ is True
    assert vars(error) == {"category": "quota"}
    for private in (SECRET, CAMERA, "private"):
        assert private not in repr(error) and private not in str(vars(error))


def test_other_http_errors_get_no_category(monkeypatch):
    _opener(monkeypatch, 503, _body("insufficient_quota"))
    with pytest.raises(ApiDetectionError, match="api_detection_http_5xx") as failure:
        _post("http://127.0.0.1:11434/api/chat", {}, {})
    assert not hasattr(failure.value, "category") and failure.value.__cause__ is None


def _detector(tmp_path, monkeypatch, outcomes, clock):
    now = [100.0]
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic", lambda: now[0])
    calls = []

    def transport(*_args):
        calls.append(1)
        outcome = outcomes.pop(0)
        if outcome == "ok":
            return _ok()
        error = ApiDetectionError(outcome[0])
        if outcome[1] is not None:
            error.category = outcome[1]
        raise error

    detector = ApiObjectDetector(_config(), tmp_path, threshold=0.8, transport=transport,
                                 clock=lambda: clock[0])
    last = [STILL]

    def motion():
        """Wait out any back-off, rearm the motion gate, then change the scene."""
        now[0] += 301
        for _ in range(3):
            now[0] += 0.1
            detector.detect_for_camera(CAMERA, last[0])
        last[0] = FRAME if last[0] is STILL else STILL
        now[0] += 0.1
        return detector.detect_for_camera(CAMERA, last[0])

    return detector, motion, calls, now


def test_a_success_ends_the_outage_but_keeps_the_cumulative_counts(tmp_path, monkeypatch):
    clock = [1_790_000_000.0]
    outcomes = [("api_detection_http_429", "quota"), ("api_detection_http_429", "rate"), "ok"]
    detector, motion, calls, _ = _detector(tmp_path, monkeypatch, outcomes, clock)
    assert detector.provider_status() == {
        "api_last_provider_success_at": None, "api_provider_outage_since": None,
        "api_consecutive_provider_failures": 0,
        "api_http_429_categories": {"quota": 0, "rate": 0, "unknown": 0},
        "api_current_429_category": None}
    with pytest.raises(ApiDetectionError):
        detector.detect_for_camera(CAMERA, STILL)          # startup probe
    clock[0] += 400
    with pytest.raises(ApiDetectionError):
        motion()
    status = detector.provider_status()
    assert status["api_provider_outage_since"] == 1_790_000_000   # the first failure, not the latest
    assert status["api_consecutive_provider_failures"] == 2
    assert status["api_current_429_category"] == "rate"
    assert status["api_http_429_categories"] == {"quota": 1, "rate": 1, "unknown": 0}
    clock[0] += 400
    assert motion() == () and len(calls) == 3
    status = detector.provider_status()
    assert status["api_last_provider_success_at"] == 1_790_000_800
    assert status["api_provider_outage_since"] is None
    assert status["api_consecutive_provider_failures"] == 0
    assert status["api_current_429_category"] is None
    assert status["api_http_429_categories"] == {"quota": 1, "rate": 1, "unknown": 0}
    assert detector.provider_failures == 2 and detector.budget is None   # still no cap


def test_back_off_timing_is_unchanged_by_the_new_state(tmp_path, monkeypatch):
    clock = [1_790_000_000.0]
    outcomes = [("api_detection_http_429", "quota"), "ok"]
    detector, _motion, calls, now = _detector(tmp_path, monkeypatch, outcomes, clock)
    with pytest.raises(ApiDetectionError):
        detector.detect_for_camera(CAMERA, STILL)
    for _ in range(3):
        now[0] += 0.1
        detector.detect_for_camera(CAMERA, STILL)
    now[0] += 0.1
    assert detector.detect_for_camera(CAMERA, FRAME) == ()    # inside the first 5 s pause
    assert len(calls) == 1 and detector.backoff_skips == 1


@pytest.mark.parametrize("outcome", [
    ("api_detection_http_5xx", None),
    ("api_detection_request_failed", None),
    ("api_detection_http_429", None),               # a 429 raised without a category
    ("api_detection_http_429", "private text"),     # a category outside the allowlist
])
def test_unknown_errors_start_an_outage_without_inventing_a_category(tmp_path, monkeypatch, outcome):
    clock = [1_790_000_000.0]
    detector, _motion, _calls, _ = _detector(tmp_path, monkeypatch, [outcome], clock)
    with pytest.raises(ApiDetectionError):
        detector.detect_for_camera(CAMERA, STILL)
    status = detector.provider_status()
    assert status["api_provider_outage_since"] == 1_790_000_000
    assert status["api_last_provider_success_at"] is None
    if outcome[0] == "api_detection_http_429":
        assert status["api_current_429_category"] == "unknown"
        assert status["api_http_429_categories"] == {"quota": 0, "rate": 0, "unknown": 1}
    else:
        assert status["api_current_429_category"] is None
        assert sum(status["api_http_429_categories"].values()) == 0
    assert set(status["api_http_429_categories"]) == set(HTTP_429_CATEGORIES)


def test_a_restart_starts_clean_and_nothing_is_written(tmp_path, monkeypatch):
    clock = [1_790_000_000.0]
    detector, _motion, _calls, _ = _detector(
        tmp_path, monkeypatch, [("api_detection_http_429", "quota")], clock)
    with pytest.raises(ApiDetectionError):
        detector.detect_for_camera(CAMERA, STILL)
    assert detector.provider_status()["api_provider_outage_since"] is not None
    restarted = ApiObjectDetector(_config(), tmp_path, threshold=0.8, transport=lambda *_: _ok(),
                                  clock=lambda: clock[0])
    assert restarted.provider_status()["api_provider_outage_since"] is None
    assert restarted.provider_status()["api_last_provider_success_at"] is None
    assert sum(restarted.provider_status()["api_http_429_categories"].values()) == 0
    assert list(tmp_path.iterdir()) == []                  # outage state is never persisted


async def test_pool_health_shows_times_and_codes_only(tmp_path, monkeypatch):
    _opener(monkeypatch, 429, _body("insufficient_quota", "insufficient_quota"))
    detector = ApiObjectDetector(_config(), tmp_path, threshold=0.8,       # real _post, fake opener
                                 clock=lambda: 1_790_000_000.5)

    async def on_result(*_args):
        pass

    pool = FairInference([CAMERA], load_detector=lambda: detector, on_result=on_result,
                         max_frames_per_camera=None)
    await pool.observe(CAMERA, STILL, generation=1)
    await pool.join()
    health = pool.snapshot()
    assert STATUS_KEYS <= set(health)
    assert health["api_provider_outage_since"] == 1_790_000_000
    assert health["api_current_429_category"] == "quota"
    assert health["api_http_429_categories"] == {"quota": 1, "rate": 0, "unknown": 0}
    assert health["api_request_cap"] is None and health["last_api_error_code"] == "api_detection_http_429"
    text = json.dumps([health, pool.camera_snapshot()])
    for private in (SECRET, CAMERA, "private", "message", "x-request-id"):
        assert private not in text
    await pool.close()
