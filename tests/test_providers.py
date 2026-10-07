"""Provider contract tests use fixed synthetic bytes and loopback HTTP only."""

import base64
import copy

from aiohttp import web
import pytest

from aikey.providers import ProviderError, VisionProvider
from aikey.worker import JobProcessor, WorkerError


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWioAAAAASUVORK5CYII="
)
TEXT = "Synthetic fixture caption. No real camera image was analyzed."
OPENAI_RESPONSE = {
    "status": "completed", "error": None, "incomplete_details": None,
    "output": [{"type": "reasoning", "summary": []},
               {"type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": TEXT}]}],
}


def provider_config(provider):
    config = {"provider": provider, "model": "explicit-fixture-vision-model"}
    if provider in {"openai", "anthropic"}:
        config["api_key"] = "synthetic-fixture-secret"
    return config


def test_openai_responses_wire_shape_with_multiple_images():
    provider = VisionProvider({**provider_config("openai"), "max_output_tokens": 1536})
    url, headers, body = provider.build_request([PNG, b"\xff\xd8\xffsynthetic"], "Describe the images.")
    assert url == "https://api.openai.com/v1/responses"
    assert headers == {"Authorization": "Bearer synthetic-fixture-secret"}
    assert body == {
        "model": "explicit-fixture-vision-model", "store": False, "stream": False,
        "max_output_tokens": 1536,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Describe the images."},
            {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(PNG).decode()},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,/9j/c3ludGhldGlj"},
        ]}],
    }
    assert provider.parse_response(OPENAI_RESPONSE) == TEXT


def test_anthropic_messages_wire_shape_and_complete_text_only():
    provider = VisionProvider({**provider_config("anthropic"), "max_output_tokens": 384})
    url, headers, body = provider.build_request([PNG], "Describe.")
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers == {"x-api-key": "synthetic-fixture-secret",
                       "anthropic-version": "2023-06-01"}
    assert body == {"model": "explicit-fixture-vision-model", "max_tokens": 384,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64",
                         "media_type": "image/png", "data": base64.b64encode(PNG).decode()}},
                        {"type": "text", "text": "Describe."}]}]}
    assert provider.parse_response({"type": "message", "role": "assistant",
                                    "stop_reason": "end_turn",
                                    "content": [{"type": "thinking", "thinking": "internal"},
                                                {"type": "text", "text": TEXT}]}) == TEXT


def test_ollama_native_wire_shape_and_configured_output_budget():
    provider = VisionProvider({**provider_config("ollama"), "base_url": "http://localhost:11434/api",
                               "temperature": 0.2, "max_output_tokens": 400})
    url, headers, body = provider.build_request([PNG], "Describe.")
    assert url == "http://localhost:11434/api/chat"
    assert headers == {}
    assert body == {"model": "explicit-fixture-vision-model", "stream": False,
                    "messages": [{"role": "user", "content": "Describe.",
                                  "images": [base64.b64encode(PNG).decode()]}],
                    "options": {"num_predict": 400, "temperature": 0.2}}
    assert provider.parse_response({"done": True, "done_reason": "stop", "message": {"content": TEXT}}) == TEXT


def test_default_compatible_provider_preserves_chat_completions_contract():
    provider = VisionProvider({"model": "configured-local-model"})
    url, headers, body = provider.build_request([PNG], "Describe.")
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
    assert headers == {}
    assert body["messages"][0]["content"][1] == {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(PNG).decode()}}
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0
    assert body["stream"] is False


@pytest.mark.parametrize("overrides,match", [
    ({"provider": "unknown"}, "provider"),
    ({"model": ""}, "inference.model"),
    ({"provider": "openai"}, "api_key_file"),
    ({"provider": "anthropic"}, "api_key_file"),
    ({"provider": "anthropic", "api_key": "fixture", "base_url": "https://wrong.example/v1"}, "Anthropic requires"),
    ({"provider": "anthropic", "api_key": "fixture", "temperature": 0}, "temperature"),
    ({"provider": "openai", "api_key": "fixture", "base_url": "https://wrong.example/v1"}, "OpenAI requires"),
    ({"provider": "openai", "api_key": "fixture", "base_url": "http://127.0.0.1:8888/v1"}, "OpenAI requires"),
    ({"provider": "ollama", "base_url": "http://localhost:11434/v1"}, "server root"),
    ({"base_url": "http://user:secret@localhost/v1"}, "base_url"),
    ({"base_url": "https://example.test/v1?api-key=secret"}, "base_url"),
    ({"api_key": "secret\r\nX-Injection: true"}, "api_key"),
    ({"max_output_tokens": 0}, "max_output_tokens"),
    ({"max_output_tokens": True}, "max_output_tokens"),
    ({"temperature": float("nan")}, "temperature"),
])
def test_configuration_errors_do_not_select_a_fallback(overrides, match):
    with pytest.raises(ProviderError, match=match):
        VisionProvider({"model": "fixture-model", **overrides})


@pytest.mark.parametrize("update", [
    {"status": "incomplete"}, {"status": "in_progress"}, {"status": "failed"},
    {"error": {"message": "provider error"}},
    {"incomplete_details": {"reason": "max_output_tokens"}},
    {"output": []}, {"output": [{"type": "function_call"}]},
    {"output": [{"type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "refusal", "refusal": "No."}]}]},
    {"output": [{"type": "message", "role": "assistant", "status": "incomplete",
                 "content": [{"type": "output_text", "text": "Partial."}]}]},
])
def test_openai_incomplete_error_and_refusal_are_not_captions(update):
    provider = VisionProvider(provider_config("openai"))
    with pytest.raises(ProviderError, match="complete, nonempty"):
        provider.parse_response({**OPENAI_RESPONSE, **update})


def test_openai_commentary_is_not_published_as_scene_description():
    result = copy.deepcopy(OPENAI_RESPONSE)
    result["output"].insert(1, {"type": "message", "role": "assistant", "status": "completed",
                               "phase": "commentary", "content": [{"type": "output_text",
                               "text": "I will inspect this image."}]})
    assert VisionProvider(provider_config("openai")).parse_response(result) == TEXT


@pytest.mark.parametrize("result", [
    {"type": "message", "role": "assistant", "stop_reason": "max_tokens",
     "content": [{"type": "text", "text": "Partial"}]},
    {"type": "message", "role": "assistant", "stop_reason": "refusal",
     "content": [{"type": "text", "text": "No"}]},
    {"type": "message", "role": "assistant", "stop_reason": "end_turn",
     "content": [{"type": "tool_use", "name": "something"}]},
])
def test_anthropic_partial_refusal_and_tool_blocks_are_rejected(result):
    with pytest.raises(ProviderError, match="complete, nonempty"):
        VisionProvider(provider_config("anthropic")).parse_response(result)


@pytest.mark.parametrize("provider,result", [
    ("ollama", {"done": False, "message": {"content": "Partial"}}),
    ("ollama", {"done": True, "done_reason": "length", "message": {"content": "Partial"}}),
    ("ollama", {"done": True, "message": {"content": "", "thinking": "Internal reasoning"}}),
    ("ollama", {"done": True, "error": "failed", "message": {"content": "Something"}}),
    ("openai-compatible", {"choices": [{"finish_reason": "length", "message": {"content": "Partial"}}]}),
    ("openai-compatible", {"choices": [{"finish_reason": "stop", "message": {"refusal": "No", "content": "No"}}]}),
    ("openai-compatible", {"choices": [{"finish_reason": "stop", "message": {"content": []}}]}),
])
def test_native_and_compatible_provider_failures_are_not_captions(provider, result):
    with pytest.raises(ProviderError):
        VisionProvider(provider_config(provider)).parse_response(result)


@pytest.fixture
async def services():
    seen = {"model": [], "media": [], "callback": [], "status": 200, "response": None, "redirected": False}

    async def media(request):
        seen["media"].append(dict(request.headers))
        return web.Response(body=PNG, content_type="image/png")

    async def model(request):
        seen["model"].append((request.path, dict(request.headers), await request.json()))
        if seen["status"] == 302:
            raise web.HTTPFound("/unexpected-redirect")
        return web.json_response(seen["response"], status=seen["status"])

    async def callback(request):
        seen["callback"].append((dict(request.headers), await request.json()))
        return web.json_response({"accepted": True})

    async def unexpected(request):
        seen["redirected"] = True
        return web.Response()

    app = web.Application()
    app.router.add_get("/internal/aiprocessors/image/fixture", media)
    for path in ("/v1/responses", "/v1/messages", "/api/chat", "/v1/chat/completions"):
        app.router.add_post(path, model)
    app.router.add_post("/internal/aiprocessors/descriptions/fixture", callback)
    app.router.add_route("*", "/unexpected-redirect", unexpected)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    seen["origin"] = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield seen
    finally:
        await runner.cleanup()


def worker(services, tmp_path, provider):
    return JobProcessor({
        "runtime": {"mode": "lab"}, "controller_origins": [services["origin"]],
        "device": {"mac": "02:00:00:00:00:01"},
        "inference": {**provider_config(provider), "api_key": "synthetic-fixture-secret",
                      "base_url": services["origin"] + ("" if provider == "ollama" else "/v1")},
    }, tmp_path)


def command():
    return {"targetUri": ":7968/describe", "resUrl": "/internal/aiprocessors/descriptions/fixture",
            "payload": {"camera": "camera-fixture", "event": "event-fixture",
                        "images": [{"reqUrl": "/internal/aiprocessors/image/fixture"}]}}


@pytest.mark.parametrize("provider,path,response", [
    ("openai", "/v1/responses", OPENAI_RESPONSE),
    ("anthropic", "/v1/messages", {"type": "message", "role": "assistant",
                                   "stop_reason": "end_turn",
                                   "content": [{"type": "text", "text": TEXT}]}),
    ("ollama", "/api/chat", {"done": True, "done_reason": "stop", "message": {"content": TEXT}}),
    ("openai-compatible", "/v1/chat/completions", {"choices": [{"finish_reason": "stop", "message": {"content": TEXT}}]}),
])
async def test_worker_dispatches_exact_provider_and_keeps_credentials_separate(services, tmp_path, provider, path, response):
    services["response"] = response
    processor = worker(services, tmp_path, provider)
    try:
        result = await processor.handle(command())
        assert result["result"]["description"] == TEXT
        assert result["callback"] == "http_accepted"
        model_path, model_headers, payload = services["model"][0]
        assert model_path == path
        if provider == "anthropic":
            assert model_headers["x-api-key"] == "synthetic-fixture-secret"
            assert model_headers["anthropic-version"] == "2023-06-01"
            assert "Authorization" not in model_headers
        else:
            assert model_headers["Authorization"] == "Bearer synthetic-fixture-secret"
        assert "x-ident" not in model_headers
        assert all("Authorization" not in headers for headers in services["media"])
        callback_headers, callback_body = services["callback"][0]
        assert "Authorization" not in callback_headers
        assert callback_headers["x-ident"] == "020000000001"
        assert callback_body["camera"] == "camera-fixture"
        assert callback_body["event"] == "event-fixture"
        assert payload["model"] == "explicit-fixture-vision-model"
    finally:
        await processor.stop()


@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama", "openai-compatible"])
@pytest.mark.parametrize("status", [302, 429])
async def test_provider_http_failure_has_no_fallback_or_callback(services, tmp_path, provider, status):
    services["status"] = status
    services["response"] = {"error": "synthetic failure"}
    processor = worker(services, tmp_path, provider)
    try:
        with pytest.raises(WorkerError, match=f"Inference returned HTTP {status}"):
            await processor.handle(command())
        assert len(services["model"]) == 1
        assert services["callback"] == []
        assert services["redirected"] is False
    finally:
        await processor.stop()
