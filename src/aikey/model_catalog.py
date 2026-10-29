"""Bounded provider catalog reads; never inference, discovery or model selection."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import stat

import aiohttp

from .providers import ProviderError, validate_inference_config
from .provider_network import ProviderConnector
from .speech import SpeechError, SpeechProvider

_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+/-]{0,199}\Z")
MAX_MODELS = 512
MAX_BYTES = 256 * 1024
MAX_PAGES = 8
TOTAL_SECONDS = 15


class CatalogError(ValueError):
    """A fixed, non-sensitive catalog failure; no provider bodies or paths."""


def _key(reference, host_state: Path, runtime_state: Path) -> str:
    try:
        path = Path(reference)
        if not path.is_absolute():
            raise ValueError
        if path.parent == runtime_state:
            path = host_state / path.name
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or not 0 < info.st_size <= 4096):
                raise ValueError
            value = source.read(4097).decode().strip()
        if (not value or len(value) > 4096 or not value.isascii()
                or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value)):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise CatalogError("catalog_key_unavailable") from exc


def catalog_provider(selection: dict, *, speech: bool, host_state: Path,
                     runtime_state: Path, lab: bool = False):
    """Validate the saved role only; never take a destination/key from a form."""
    if not isinstance(selection, dict):
        raise CatalogError("catalog_provider_invalid")
    options = dict(selection)
    if options.get("api_key_file"):
        options["api_key"] = _key(options["api_key_file"], host_state, runtime_state)
    try:
        if speech:
            return SpeechProvider(options, lab=lab)
        # A model is not needed to GET a catalog. This placeholder is never sent.
        options["model"] = options.get("model") or "catalog-only-no-inference"
        return validate_inference_config(options, lab=lab)
    except (ProviderError, SpeechError) as exc:
        raise CatalogError("catalog_provider_invalid") from exc


def _page(reply, provider: str) -> tuple[list[dict], str | None]:
    if not isinstance(reply, dict) or reply.get("error") is not None:
        raise CatalogError("catalog_response_invalid")
    allowed = ({"models", "error"} if provider == "ollama" else
               {"data", "first_id", "last_id", "has_more", "error"} if provider == "anthropic" else
               {"data", "object", "has_more", "error"})
    if not set(reply) <= allowed:
        raise CatalogError("catalog_pagination_invalid")
    if "object" in reply and reply["object"] != "list":
        raise CatalogError("catalog_response_invalid")
    items = reply.get("models" if provider == "ollama" else "data")
    if not isinstance(items, list) or len(items) > MAX_MODELS:
        raise CatalogError("catalog_response_invalid")
    models = []
    for item in items:
        if not isinstance(item, dict):
            raise CatalogError("catalog_response_invalid")
        model = item.get("model", item.get("name")) if provider == "ollama" else item.get("id")
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
            raise CatalogError("catalog_response_invalid")
        advertised = None
        if provider == "anthropic":
            capabilities = item.get("capabilities")
            if isinstance(capabilities, dict):
                image = capabilities.get("image_input")
                if isinstance(image, dict) and type(image.get("supported")) is bool:
                    advertised = image["supported"]
        if provider == "ollama" and any(item.get(k) is not None and not isinstance(item[k], str)
                                         for k in ("remote_host", "remote_model")):
            raise CatalogError("catalog_response_invalid")
        models.append({"id": model, "image_input_advertised": advertised,
                       "provider_reported_remote": (bool(item.get("remote_host") or item.get("remote_model"))
                                                    if provider == "ollama" else None),
                       "capability_tested": False})
    more = reply.get("has_more", False)
    if type(more) is not bool:
        raise CatalogError("catalog_response_invalid")
    cursor = None
    if more:
        # Only Anthropic's declared cursor protocol is implemented. Never follow
        # a response-supplied URL or present a partial unknown-protocol catalog.
        cursor = reply.get("last_id")
        if (provider != "anthropic" or not models or cursor != models[-1]["id"]):
            raise CatalogError("catalog_pagination_invalid")
    elif provider == "anthropic" and "has_more" not in reply:
        raise CatalogError("catalog_pagination_invalid")
    return models, cursor


async def fetch_catalog(provider) -> dict:
    """GET from the selected provider with isolated credentials, no cookies/retry."""
    if provider.provider == "ollama":
        url = provider.base_url + ("/tags" if provider.base_url.endswith("/api") else "/api/tags")
    else:
        url = provider.base_url + "/models"
    models, seen, cursors = [], set(), set()
    credentials = [value.removeprefix("Bearer ") if name.lower() == "authorization" else value
                   for name, value in provider.headers.items()
                   if name.lower() in {"authorization", "x-api-key"}]
    params = {"limit": "100"} if provider.provider == "anthropic" else None
    received = 0
    try:
        async with asyncio.timeout(TOTAL_SECONDS):
            async with aiohttp.ClientSession(
                    connector=ProviderConnector(),
                    timeout=aiohttp.ClientTimeout(total=8, connect=3), trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                    headers={"Accept-Encoding": "identity"}) as session:
                for _ in range(MAX_PAGES):
                    async with session.get(url, params=params, headers=provider.headers,
                                           allow_redirects=False) as response:
                        if response.status != 200:
                            raise CatalogError("catalog_http_failure")
                        body = bytearray()
                        async for chunk in response.content.iter_chunked(16384):
                            received += len(chunk)
                            if received > MAX_BYTES:
                                raise CatalogError("catalog_response_too_large")
                            body.extend(chunk)
                    try:
                        page, cursor = _page(json.loads(body), provider.provider)
                    except (ValueError, TypeError, RecursionError) as exc:
                        if isinstance(exc, CatalogError):
                            raise
                        raise CatalogError("catalog_response_invalid") from exc
                    for model in page:
                        if any(value and value in model["id"] for value in credentials):
                            raise CatalogError("catalog_response_invalid")
                        if model["id"] in seen or len(models) >= MAX_MODELS:
                            raise CatalogError("catalog_incomplete")
                        seen.add(model["id"])
                        models.append(model)
                    if cursor is None:
                        return {"models": models, "complete": True, "capability_tested": False}
                    if cursor in cursors:
                        raise CatalogError("catalog_pagination_invalid")
                    cursors.add(cursor)
                    params = {"limit": "100", "after_id": cursor}
        raise CatalogError("catalog_incomplete")
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise CatalogError("catalog_unavailable") from exc
