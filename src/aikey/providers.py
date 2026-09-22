"""Vision request/response adapters. They perform no HTTP requests or fallback."""

from __future__ import annotations

import base64
import ipaddress
import math
from typing import Any
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    """Provider configuration or its response cannot produce a description."""


DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434",
    "openai-compatible": "http://127.0.0.1:11434/v1",
}


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ProviderError(f"Invalid {name}")
    return value.strip()


def image_mime(image: bytes) -> str:
    if not isinstance(image, bytes):
        raise ProviderError("Image input must contain bytes")
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    raise ProviderError("Unsupported image bytes")


class VisionProvider:
    """An explicitly selected API and model, independent of controller sessions."""

    def __init__(self, config: dict, *, lab: bool = False, require_api_key: bool = True):
        self.provider = config.get("provider", "openai-compatible")
        if self.provider not in DEFAULT_BASE_URLS:
            raise ProviderError("Unsupported inference.provider")
        self.model = _text(config.get("model"), "inference.model")
        self.base_url = _text(config.get("base_url") or DEFAULT_BASE_URLS[self.provider],
                              "inference.base_url").rstrip("/")
        try:
            url = urlsplit(self.base_url)
            if (url.scheme not in {"http", "https"} or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.query or url.fragment or any(ch.isspace() for ch in self.base_url)
                    or any(ch in self.base_url for ch in "\\%")):
                raise ValueError
            port = url.port or (443 if url.scheme == "https" else 80)
        except (ValueError, TypeError) as exc:
            raise ProviderError("Invalid inference.base_url") from exc
        if self.provider == "openai":
            official = url.scheme == "https" and url.hostname == "api.openai.com" and port == 443
            local_fixture = lab and _loopback(url.hostname)
            if not (official or local_fixture) or url.path != "/v1":
                raise ProviderError("OpenAI requires https://api.openai.com/v1, except explicit loopback lab fixtures")
            self.url = self.base_url + "/responses"
        elif self.provider == "ollama":
            if url.path not in {"", "/api"}:
                raise ProviderError("Ollama base_url must be the server root or end with /api")
            self.url = self.base_url + ("/chat" if url.path == "/api" else "/api/chat")
        else:
            self.url = self.base_url + "/chat/completions"
        self.headers = {}
        key = config.get("api_key")
        if key is not None:
            key = _text(key, "inference.api_key")
            if any(ch.isspace() for ch in key):
                raise ProviderError("Invalid inference.api_key")
            self.headers["Authorization"] = "Bearer " + key
        if self.provider == "openai" and require_api_key and not self.headers:
            raise ProviderError("OpenAI requires inference.api_key_file with a nonempty API key")
        self.max_output_tokens = config.get("max_output_tokens", 1024 if self.provider == "openai" else 256)
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 32768:
            raise ProviderError("inference.max_output_tokens must be an integer between 1 and 32768")
        self.temperature = config.get("temperature", None if self.provider == "openai" else 0)
        if self.temperature is not None and (type(self.temperature) not in {int, float}
                or not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2):
            raise ProviderError("inference.temperature must be between 0 and 2")

    def build_request(self, images: list[bytes], prompt: str) -> tuple[str, dict, dict]:
        if not isinstance(images, list) or not images:
            raise ProviderError("At least one image is required")
        encoded = [(image_mime(image), base64.b64encode(image).decode("ascii")) for image in images]
        if not isinstance(prompt, str) or not prompt.strip():
            raise ProviderError("A nonempty vision prompt is required")
        if self.provider == "openai":
            content = [{"type": "input_text", "text": prompt}]
            content.extend({"type": "input_image", "image_url": f"data:{mime};base64,{data}"}
                           for mime, data in encoded)
            payload = {"model": self.model, "input": [{"role": "user", "content": content}],
                       "store": False, "stream": False, "max_output_tokens": self.max_output_tokens}
            if self.temperature is not None:
                payload["temperature"] = self.temperature
        elif self.provider == "ollama":
            options = {"num_predict": self.max_output_tokens}
            if self.temperature is not None:
                options["temperature"] = self.temperature
            payload = {"model": self.model, "messages": [{"role": "user", "content": prompt,
                        "images": [data for _, data in encoded]}], "stream": False, "options": options}
        else:
            content = [{"type": "text", "text": prompt}]
            content.extend({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
                           for mime, data in encoded)
            payload = {"model": self.model, "messages": [{"role": "user", "content": content}],
                       "max_tokens": self.max_output_tokens, "stream": False}
            if self.temperature is not None:
                payload["temperature"] = self.temperature
        return self.url, dict(self.headers), payload

    def parse_response(self, result: Any) -> str:
        try:
            if not isinstance(result, dict) or result.get("error") is not None:
                raise ValueError
            if self.provider == "openai":
                if result.get("status") != "completed" or result.get("incomplete_details") is not None:
                    raise ValueError
                output = result["output"]
                if not isinstance(output, list):
                    raise ValueError
                parts = []
                for item in output:
                    if item.get("type") == "reasoning":
                        continue
                    if (item.get("type") != "message" or item.get("role") != "assistant"
                            or item.get("status") != "completed"):
                        raise ValueError
                    for content in item["content"]:
                        if content.get("type") != "output_text":
                            raise ValueError  # Includes refusal; never publish it as a caption.
                        if item.get("phase") != "commentary":
                            parts.append(content["text"])
                if any(not isinstance(part, str) for part in parts):
                    raise ValueError
                description = "\n".join(parts)
            elif self.provider == "ollama":
                if result.get("done") is not True or result.get("done_reason", "stop") != "stop":
                    raise ValueError
                message = result["message"]
                if message.get("tool_calls") or message.get("refusal"):
                    raise ValueError
                description = message["content"]
            else:
                choices = result["choices"]
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError
                choice = choices[0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError
                message = choice["message"]
                if message.get("tool_calls") or message.get("refusal"):
                    raise ValueError
                description = message["content"]
            if not isinstance(description, str) or not description.strip():
                raise ValueError
            return description.strip()
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise ProviderError("Inference did not return a complete, nonempty text description") from exc


def validate_inference_config(config: dict, *, lab: bool = False,
                              require_api_key: bool = True) -> VisionProvider:
    """Validate adapter settings and endpoint permission without any network I/O.

    Config editors may validate an API key file reference before its contents
    exist. Runtime callers retain the default requirement for a hydrated key.
    """
    provider = VisionProvider(config, lab=lab, require_api_key=require_api_key)
    for flag in ("allow_remote", "allow_insecure_http"):
        if flag in config and type(config[flag]) is not bool:
            raise ProviderError(f"inference.{flag} must be a JSON boolean")
    parsed = urlsplit(provider.base_url)
    if not _loopback(parsed.hostname) and config.get("allow_remote") is not True:
        raise ProviderError("Remote inference requires explicit inference.allow_remote=true")
    if (parsed.scheme == "http" and not _loopback(parsed.hostname)
            and config.get("allow_insecure_http") is not True):
        raise ProviderError("Non-loopback inference requires HTTPS or explicit allow_insecure_http")
    return provider
