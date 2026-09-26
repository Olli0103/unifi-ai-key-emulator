"""Query transport for Protect's search WebSocket and its embedding backends.

Two profiles answer ``NL_PARSE``: ``e5-session-v1`` (deep-mode session
queries, 384-value E5) and ``clip-basic-v1`` (basic Find Anything, 768-value
CLIP ViT-L/14 from the local ``aikey.clip_server``). One service runs one
profile, and its identity is pinned in ``search-profile.json``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
from pathlib import Path
import re
import ssl
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

import aiohttp

from . import clip
from .protocol import ContractError, decode_message, encode_message
from .device import VerifiedConnector
from .embedding_profile import EmbeddingProfileError, ensure_embedding_profile


LOG = logging.getLogger(__name__)
MODEL = "multilingual-e5-small"
DIMENSIONS = 384
PROFILE = "e5-session-v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class EmbeddingError(RuntimeError):
    """A real compatible embedding could not be produced."""


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_embedding(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS:
        raise EmbeddingError("E5 requires exactly 384 embedding values")
    try:
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise EmbeddingError("Embedding contains non-finite or nonnumeric values")
        norm = math.hypot(*values)
    except OverflowError as exc:
        raise EmbeddingError("Embedding numeric values exceed supported range") from exc
    if not math.isfinite(norm) or norm <= 0:
        raise EmbeddingError("Embedding has zero or invalid norm")
    return [float(value / norm) for value in values]


class EmbeddingService:
    """One encoder configuration shared by document and query inference.

    HTTP expects an OpenAI-compatible /v1/embeddings endpoint. The local backend
    loads only an existing checkpoint and never downloads models implicitly.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = dict(config)
        self.backend = self.config.get("backend", "disabled")
        self.model = self.config.get("model", MODEL)
        if self.model not in (MODEL, f"intfloat/{MODEL}"):
            raise EmbeddingError("Only multilingual-e5-small is supported by this profile")
        self._session: aiohttp.ClientSession | None = None
        self._model: Any = None
        self._lock = asyncio.Lock()

    @property
    def identity(self) -> dict[str, Any]:
        """Non-secret profile identity used to prevent accidental index mixing."""
        return {
            "profile": PROFILE, "model": self.model, "dimensions": DIMENSIONS,
            "prefixes": {"query": "query: ", "document": "passage: "},
            "backend": self.backend,
            "source": self.config.get("model_path") if self.backend == "sentence-transformers"
            else self._endpoint() if self.backend == "http" else None,
            "revision": self.config.get("revision"),
        }

    async def encode_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._encode(texts, "passage: ")

    async def encode_queries(self, texts: list[str]) -> list[list[float]]:
        return await self._encode(texts, "query: ")

    async def embed(self, text: str, kind: str = "query") -> list[float]:
        if kind not in ("query", "document", "passage"):
            raise EmbeddingError("Unknown embedding kind")
        method = self.encode_queries if kind == "query" else self.encode_documents
        return (await method([text]))[0]

    async def _encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        if not isinstance(texts, list) or not 1 <= len(texts) <= 32:
            raise EmbeddingError("Provide between 1 and 32 texts")
        if any(not isinstance(text, str) or not text.strip() or len(text) > 8192 for text in texts):
            raise EmbeddingError("Texts must be nonempty strings of at most 8192 characters")
        prepared = [prefix + text.strip() for text in texts]
        async with self._lock:
            try:
                if self.backend == "http":
                    vectors = await self._http_encode(prepared)
                elif self.backend == "sentence-transformers":
                    vectors = await asyncio.to_thread(self._local_encode, prepared)
                else:
                    raise EmbeddingError("No embedding backend is configured")
            except EmbeddingError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, TypeError, RuntimeError) as exc:
                raise EmbeddingError(f"Embedding backend failed ({type(exc).__name__})") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError("Embedding backend returned the wrong number of vectors")
        return [normalize_embedding(vector) for vector in vectors]

    def _endpoint(self) -> str:
        endpoint = self.config.get("endpoint")
        if not endpoint:
            base = str(self.config.get("base_url", "")).rstrip("/")
            endpoint = base + ("/embeddings" if base.endswith("/v1") else "/v1/embeddings")
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment or parsed.query
                or any(char.isspace() for char in endpoint)
                or any(char in endpoint for char in "\\%")):
            raise EmbeddingError("Set a valid local embedding API URL without credentials")
        if not _loopback(parsed.hostname) and self.config.get("allow_remote") is not True:
            raise EmbeddingError("Remote embeddings require explicit embeddings.allow_remote=true")
        if (parsed.scheme == "http" and not _loopback(parsed.hostname)
                and self.config.get("allow_insecure_http") is not True):
            raise EmbeddingError(
                "Non-loopback embeddings require HTTPS or explicit allow_insecure_http"
            )
        return endpoint

    async def _http_encode(self, texts: list[str]) -> list[list[float]]:
        if self._session is None or self._session.closed:
            timeout = float(self.config.get("timeout_seconds", 8))
            if not math.isfinite(timeout) or timeout <= 0:
                raise EmbeddingError("Embedding timeout must be positive")
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout), trust_env=False
            )
        headers = {}
        token_file = self.config.get("bearer_token_file")
        if token_file:
            token = Path(token_file).read_text().strip()
            if not token or "\n" in token or "\r" in token:
                raise EmbeddingError("Embedding token file is empty or invalid")
            headers["Authorization"] = "Bearer " + token
        payload = {"model": self.model, "input": texts, "encoding_format": "float"}
        async with self._session.post(self._endpoint(), json=payload, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                raise EmbeddingError(f"Embedding API returned HTTP {response.status}")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise EmbeddingError("Embedding API response exceeds local size limit")
            try:
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError("Expected an embedding result object")
                if result.get("model", self.model) not in (MODEL, f"intfloat/{MODEL}"):
                    raise EmbeddingError("Embedding API reported an incompatible model")
                rows = result["data"]
                if not isinstance(rows, list) or len(rows) != len(texts):
                    raise ValueError("Unexpected result count")
                indexes = [row["index"] for row in rows]
                if any(type(index) is not int for index in indexes) or sorted(indexes) != list(range(len(texts))):
                    raise ValueError("Invalid embedding indexes")
                return [row["embedding"] for row in sorted(rows, key=lambda row: row["index"])]
            except (ValueError, TypeError, KeyError) as exc:
                raise EmbeddingError("Embedding API returned an invalid response") from exc

    def _local_encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            model_path = self.config.get("model_path")
            if not model_path or not Path(model_path).is_dir():
                raise EmbeddingError("sentence-transformers requires an existing local model_path")
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError("Install the optional sentence-transformers dependency in the project environment") from exc
            self._model = SentenceTransformer(
                str(model_path), device=self.config.get("device", "cpu"),
                local_files_only=True, trust_remote_code=False,
            )
            self._model.max_seq_length = 512
        vectors = self._model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False, prompt="",
        )
        return vectors.tolist()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


class SearchService:
    """Connect an adopted emulator to Protect's UCP4 query WebSocket."""

    def __init__(self, config: dict[str, Any], state_dir: Path, ssl_context: ssl.SSLContext | None = None):
        self.config = config
        self.state_dir = Path(state_dir)
        self.ssl_context = ssl_context
        self.profile = config.get("search", {}).get("profile", PROFILE)
        self.embeddings = EmbeddingService(config.get("embeddings", {}))
        self.clip: clip.ClipClient | None = None
        if self.profile == clip.PROFILE and config.get("search", {}).get("enabled") is True:
            try:
                self.clip = clip.ClipClient(config.get("find_anything"))
            except clip.ClipError as exc:
                raise EmbeddingError(str(exc)) from exc
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._stopping = asyncio.Event()
        self.status: dict[str, Any] = {"connected": False, "profile": self.profile, "last_error": None,
                                       "queries": 0, "query_failures": 0, "ignored_frames": 0}

    def _url(self) -> str:
        controller = self.config.get("controller", {})
        host = controller.get("host", "")
        if not isinstance(host, str) or not host or any(char in host for char in "/?#@"):
            raise ValueError("controller.host must be a hostname or IP address")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = int(controller.get("search_port", 7443))
        if not 1 <= port <= 65535:
            raise ValueError("Invalid controller search port")
        return f"wss://{host}:{port}/wss/nl-search/v1"

    def _fingerprint(self) -> str | None:
        controller = self.config.get("controller", {})
        return controller.get("search_expected_fingerprint") or controller.get("expected_fingerprint")

    def _mac(self) -> str:
        mac = re.sub(r"[:-]", "", str(self.config.get("device", {}).get("mac", ""))).lower()
        if not re.fullmatch(r"[0-9a-f]{12}", mac):
            raise ValueError("device.mac must contain 12 hexadecimal digits")
        return mac

    def _identity(self) -> dict[str, Any]:
        if self.clip is not None:
            return {"profile": clip.PROFILE, "model": clip.MODEL, "dimensions": clip.DIMENSIONS,
                    "backend": "local-clip-onnx", "source": self.clip.base}
        return self.embeddings.identity

    def _check_profile(self) -> None:
        try:
            ensure_embedding_profile(self.state_dir, self._identity())
        except EmbeddingProfileError as exc:
            raise EmbeddingError(str(exc)) from exc

    def validate_configuration(self) -> None:
        """Validate the enabled query service before any controller connection."""
        if self.config.get("search", {}).get("enabled", False) is not True:
            return
        if self.profile not in (PROFILE, clip.PROFILE):
            raise EmbeddingError("Search profile must be e5-session-v1 or clip-basic-v1")
        if self.profile == PROFILE and self.embeddings.backend not in ("http", "sentence-transformers"):
            raise EmbeddingError("Search requires a real embedding backend")
        self._url()
        self._mac()
        if self.ssl_context is not None:
            if self.ssl_context.verify_mode != ssl.CERT_REQUIRED:
                raise EmbeddingError("Search controller TLS must verify certificates")
            if not self.ssl_context.check_hostname and not self._fingerprint():
                raise EmbeddingError("Search controller TLS requires hostname verification or an explicit pin")
        self._check_profile()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        if self.config.get("search", {}).get("enabled", False) is not True:
            self.status["last_error"] = "disabled"
            return
        self.validate_configuration()
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="aikey-search")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None
        await self.embeddings.close()
        if self.clip is not None:
            await self.clip.close()
        self.status["connected"] = False

    async def _run(self) -> None:
        delay = float(self.config.get("search", {}).get("reconnect_seconds", 5))
        delay = min(max(delay, 1), 60)
        connector = VerifiedConnector(
            ssl_context=self.ssl_context or ssl.create_default_context(),
            expected_fingerprint=self._fingerprint(),
        )
        self._session = aiohttp.ClientSession(
            connector=connector, timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
        )
        try:
            while not self._stopping.is_set():
                try:
                    async with self._session.ws_connect(
                        self._url(), headers={"x-ident": self._mac()}, protocols=("ucp4",),
                        heartbeat=30, max_msg_size=MAX_RESPONSE_BYTES,
                    ) as websocket:
                        self.status.update(connected=True, last_error=None)
                        await websocket.send_bytes(encode_message(
                            {"id": uuid.uuid4().hex, "type": "request", "timestamp": int(time.time() * 1000), "action": "echo"},
                            {"event": "echo"},
                        ))
                        async for incoming in websocket:
                            if incoming.type == aiohttp.WSMsgType.BINARY:
                                try:
                                    reply = await self.handle_message(incoming.data)
                                except ContractError:
                                    # One undecodable frame must not drop the query channel.
                                    self.status["ignored_frames"] += 1
                                    continue
                                if reply is not None:
                                    await websocket.send_bytes(reply)
                            elif incoming.type == aiohttp.WSMsgType.ERROR:
                                raise ConnectionError("Search WebSocket failed")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.status["last_error"] = type(exc).__name__
                    LOG.warning("Search connection failed: %s", type(exc).__name__)
                finally:
                    self.status["connected"] = False
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._session.close()

    async def handle_message(self, wire: bytes) -> bytes | None:
        message = decode_message(wire)
        header, body = message.header, message.body
        if header.get("type") != "request":
            return None
        request_id = header.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise ContractError("Query request has no id")
        reply = {"id": request_id, "type": "response", "timestamp": int(time.time() * 1000), "error": None, "errorCode": 0}
        try:
            if header.get("action") == "echo":
                result = body
            elif self.clip is not None:
                # Basic Find Anything: Protect defaults model to clip-ViT-L-14.
                if header.get("action") != "NL_PARSE" or body.get("model", clip.MODEL) != clip.MODEL:
                    raise EmbeddingError("Only clip-ViT-L-14 NL_PARSE is supported by this profile")
                try:
                    vector = await self.clip.embed_text(body.get("querySentence"))
                except clip.ClipError as exc:
                    raise EmbeddingError(str(exc)) from exc
                result = {"keyTags": [], "objectTypes": [], "txtEmbed": vector, "model": clip.MODEL,
                          "dim": clip.DIMENSIONS, "exact_match": False}
                self.status["queries"] += 1
            else:
                if header.get("action") != "NL_PARSE" or body.get("model") != MODEL:
                    raise EmbeddingError("Only explicit multilingual-e5-small NL_PARSE is supported")
                vector = (await self.embeddings.encode_queries([body.get("querySentence")]))[0]
                result = {"keyTags": [], "objectTypes": [], "txtEmbed": vector, "model": MODEL,
                          "dim": DIMENSIONS, "exact_match": False}
                self.status["queries"] += 1
        except EmbeddingError as exc:
            self.status["query_failures"] += 1
            reply.update(error=str(exc), errorCode=1)
            result = {}
        return encode_message(reply, result)
