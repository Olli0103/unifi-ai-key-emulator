"""Local CLIP ViT-L/14 embeddings for basic Find Anything (#2, #21).

Protect 7.3.x answers a basic text search by sending ``NL_PARSE`` with model
``clip-ViT-L-14`` and ranking ``ramDetections.embedding`` (768 values) by
cosine distance on the search host. The same encoder must produce both the
text query vector and every indexed object vector, so one local server
(``aikey.clip_server``) serves both. No text, frame or crop leaves this host
or its container network, and nothing here logs or stores them.

Configuration (``find_anything``)::

    {"clip_server": "http://192.168.64.1:8180",
     "index_camera_ids": ["<protect camera id>", ...],
     "max_objects": 8,
     "retroactive": false}

``retroactive`` (opt-in) advertises ``supportRetroactiveProcessing``, so
Protect may backfill past events of these cameras as ``multipleImages``
tasks; those crops are embedded locally only.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp


MODEL = "clip-ViT-L-14"
DIMENSIONS = 768
PROFILE = "clip-basic-v1"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CAMERA_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class ClipError(RuntimeError):
    """A compatible local CLIP embedding could not be produced."""


def normalize(values: Any) -> list[float]:
    """Check one 768-value vector and return it L2-normalized."""
    if not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS:
        raise ClipError("CLIP ViT-L/14 embeddings have exactly 768 values")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ClipError("Embedding contains non-finite or nonnumeric values")
    norm = math.sqrt(math.fsum(float(value) * float(value) for value in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ClipError("Embedding has zero or invalid norm")
    return [float(value) / norm for value in values]


def validate_find_anything_config(value: Any) -> dict:
    """Return a normalized copy of ``find_anything`` or raise ClipError."""
    if (not isinstance(value, dict)
            or set(value) - {"clip_server", "index_camera_ids", "max_objects", "retroactive",
                             "reverification", "tag_server"}
            or "clip_server" not in value):
        raise ClipError("find_anything needs clip_server and optional index_camera_ids, max_objects, retroactive")
    for flag in ("retroactive", "reverification"):
        if type(value.get(flag, False)) is not bool:
            raise ClipError(f"find_anything.{flag} must be a JSON boolean")
    server = value["clip_server"]
    if not _local_server(server):
        # Query text and frames stay on this host or its container network.
        raise ClipError("find_anything.clip_server must be a local HTTP server")
    tag_server = value.get("tag_server")
    if tag_server is not None and not _local_server(tag_server):
        # RAM++ open-vocabulary tags (vision_server): frames stay local too.
        raise ClipError("find_anything.tag_server must be a local HTTP server")
    cameras = value.get("index_camera_ids", [])
    if (not isinstance(cameras, list) or len(cameras) > 32
            or any(not isinstance(c, str) or not _CAMERA_ID.match(c) for c in cameras)
            or len(set(cameras)) != len(cameras)):
        raise ClipError("find_anything.index_camera_ids must list at most 32 camera IDs")
    limit = value.get("max_objects", 8)
    if type(limit) is not int or not 1 <= limit <= 16:
        raise ClipError("find_anything.max_objects must be 1..16")
    return {"clip_server": server.rstrip("/"), "index_camera_ids": list(cameras), "max_objects": limit,
            "retroactive": value.get("retroactive", False),
            "reverification": value.get("reverification", False),
            **({"tag_server": tag_server.rstrip("/")} if tag_server is not None else {})}


def _local_server(server: Any) -> bool:
    parsed = urlsplit(server) if isinstance(server, str) else None
    try:
        address = ipaddress.ip_address(parsed.hostname or "") if parsed else None
    except ValueError:
        address = None
    return not (parsed is None or parsed.scheme != "http" or address is None
                or not (address.is_loopback or address.is_private)
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.username or parsed.password)


class ClipClient:
    """Talks to the local CLIP server; one shared session, bounded replies."""

    def __init__(self, config: dict, *, timeout_s: float = 30, expected_revision: str | None = None):
        self.config = validate_find_anything_config(config)
        self.base = self.config["clip_server"]
        self.timeout_s = timeout_s
        # When the index pins encoder weights, replies from other weights are
        # refused, so an index never mixes vectors from two encoders (#18).
        self.expected_revision = expected_revision
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout_s), trust_env=False)
        return self._session

    async def _vectors(self, response: aiohttp.ClientResponse, count: int) -> list[list[float]]:
        if response.status != 200:
            raise ClipError(f"CLIP server returned HTTP {response.status}")
        data = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            data.extend(chunk)
            if len(data) > _MAX_RESPONSE_BYTES:
                raise ClipError("CLIP server reply exceeds its size limit")
        try:
            reply = json.loads(data)
            if reply.get("model") != MODEL or reply.get("dim") != DIMENSIONS:
                raise ClipError("CLIP server reported an incompatible model")
            if self.expected_revision is not None and reply.get("revision") != self.expected_revision:
                raise ClipError("CLIP server weights differ from the pinned index encoder")
            vectors = reply["embeddings"]
            if not isinstance(vectors, list) or len(vectors) != count:
                raise ValueError("wrong count")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ClipError("CLIP server returned an invalid reply") from exc
        return [normalize(vector) for vector in vectors]

    async def revision(self) -> str | None:
        """The server's encoder weights revision, from its health route."""
        try:
            async with self._client().get(self.base + "/healthz", allow_redirects=False) as response:
                if response.status != 200:
                    raise ClipError(f"CLIP server returned HTTP {response.status}")
                reply = json.loads((await response.content.read(65536)) or b"{}")
        except ClipError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
            raise ClipError(f"CLIP server unavailable ({type(exc).__name__})") from exc
        value = reply.get("revision") if isinstance(reply, dict) else None
        return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None

    async def embed_text(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip() or len(text) > 1024:
            raise ClipError("Query text must be 1 to 1024 characters")
        try:
            async with self._client().post(self.base + "/v1/text", json={"texts": [text.strip()]},
                                           allow_redirects=False) as response:
                return (await self._vectors(response, 1))[0]
        except ClipError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise ClipError(f"CLIP server unavailable ({type(exc).__name__})") from exc

    async def embed_regions(self, jpeg: bytes, regions: list[list[float]]) -> list[list[float]]:
        """One vector per normalized xyxy region of one JPEG frame."""
        if not 1 <= len(regions) <= 16:
            raise ClipError("Embed 1 to 16 regions per frame")
        form = aiohttp.FormData()
        form.add_field("image", jpeg, filename="frame.jpg", content_type="image/jpeg")
        form.add_field("regions", json.dumps(regions))
        try:
            async with self._client().post(self.base + "/v1/image", data=form,
                                           allow_redirects=False) as response:
                return await self._vectors(response, len(regions))
        except ClipError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise ClipError(f"CLIP server unavailable ({type(exc).__name__})") from exc

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
