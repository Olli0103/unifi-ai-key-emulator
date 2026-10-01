"""Deep understanding (Protect 7.3.70): dedup sessions, their descriptions and search.

Protect groups detections into sessions on the console and asks a deep-mode
AI Key for three things, all answered here with local models only:

- ``RequestAI :7445/generate-embeddings``: a re-identification vector per
  person crop (``reidEmbed``), posted to ``/internal/aiprocessors/embeddings/
  {taskId}``. Protect joins a person to a session at cosine >= 0.75 by default
  and stores the vector in ``smartDetectSessionObjects.reidEmbedding``, a
  ``vector(512)`` column on the Key's search host (read back 30 Sep).
- ``RequestAI :7968/describe`` (``promptProfile: session-v1``): a JSON
  ``{"description", "labels"}`` for a session's representative crops (``open``
  pass) or its object boxes in a video export (``close`` pass), posted with a
  384-value E5 ``descEmbedding`` to ``/internal/aiprocessors/descriptions/
  {taskId}``. Protect ships the prompts, sampling and the JSON schema itself
  (``changeDescribePrompts``); the Key stores them and echoes their hash.
- ``NL_PARSE`` with ``model: multilingual-e5-small``: the session search
  query vector (search.py).

The mode itself is Protect's choice: ``changeAiInferAgentSettings {modelMode}``
switches it and getInfo echoes it as ``featureFlags.aiMode``.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

EMBED_TARGET = ":7445/generate-embeddings"
DESCRIBE_TARGET = ":7968/describe"
EMBED_CALLBACK = re.compile(r"^/internal/aiprocessors/embeddings/([A-Za-z0-9_-]{1,128})$")
PROMPTS_FILE = "describe-prompts.json"
REID_DIMENSIONS = 512             # smartDetectSessionObjects.reidEmbedding is vector(512)
REID_MODEL = "person-reidentification-retail-0288"
MAX_PROMPTS_BYTES = 64000         # Protect refuses to ship a larger config
DESCRIBE_TYPES = ("person", "face", "vehicle", "animal")
MAX_EMBED_IMAGES = 32
MAX_DESCRIBE_IMAGES = 8
MAX_LABELS = 64
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PROMPT_FIELDS = {"systemPrompt", "userPrompt", "temperature", "topK", "topP", "minP",
                  "repeatPenalty", "presencePenalty", "bboxMargin", "describeSchema"}


class DeepModeError(ValueError):
    """A deep-mode request, reply or configuration outside the accepted shape."""


def validate_config(value: Any) -> dict | None:
    """``deep_understanding: {"reid_server", "rerank_server"?}`` (local HTTP) or None.

    ``rerank_server`` serves ``/v1/rerank`` for Protect's hybrid session search
    (rerank_relay.py); without it the relay stays off.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or not {"reid_server"} <= set(value) <= {"reid_server", "rerank_server"}:
        raise DeepModeError("deep_understanding needs reid_server and optionally rerank_server")
    for key in value:
        server = value[key]
        parsed = urlsplit(server) if isinstance(server, str) else None
        try:
            address = ipaddress.ip_address(parsed.hostname or "") if parsed else None
        except ValueError:
            address = None
        if (parsed is None or parsed.scheme != "http" or address is None
                or not (address.is_loopback or address.is_private) or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            # Person crops and session texts never leave this host or its container network.
            raise DeepModeError(f"deep_understanding.{key} must be a local HTTP server")
    return {key: server.rstrip("/") for key, server in value.items()}


def combo_key(types: list[str]) -> str:
    """Protect's describeComboKey: unique lowercase types, sorted, joined by '+'."""
    return "+".join(sorted({t.strip().lower() for t in types if isinstance(t, str) and t.strip()}))


def validate_prompts(body: Any) -> tuple[dict, str]:
    """Check a ``changeDescribePrompts`` body; return (prompts, configHash)."""
    if (not isinstance(body, dict) or set(body) != {"describePrompts", "configHash"}
            or not isinstance(body["configHash"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", body["configHash"])):
        raise DeepModeError("Unsupported describe prompts")
    prompts = body["describePrompts"]
    if (not isinstance(prompts, dict) or not 1 <= len(prompts) <= 16
            or len(json.dumps(body, separators=(",", ":"))) > MAX_PROMPTS_BYTES):
        raise DeepModeError("Unsupported describe prompts")
    for key, entry in prompts.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z]+(\+[a-z]+)*", key)
                or not isinstance(entry, dict) or not {"systemPrompt", "userPrompt",
                                                       "describeSchema"} <= set(entry)
                or set(entry) - _PROMPT_FIELDS):
            raise DeepModeError("Unsupported describe prompts")
        for name in ("systemPrompt", "userPrompt", "describeSchema"):
            _decode(entry[name])
        json.loads(_decode(entry["describeSchema"]))
        for name in _PROMPT_FIELDS - {"systemPrompt", "userPrompt", "describeSchema"}:
            if name in entry and (type(entry[name]) not in (int, float) or entry[name] != entry[name]):
                raise DeepModeError("Unsupported describe prompts")
    return prompts, body["configHash"]


def _decode(value: Any) -> str:
    if not isinstance(value, str):
        raise DeepModeError("Prompt fields are base64 text")
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise DeepModeError("Prompt fields are base64 text") from exc


def save_prompts(state_dir: Path, prompts: dict, config_hash: str) -> None:
    from .config import atomic_private
    atomic_private(Path(state_dir) / PROMPTS_FILE,
                   json.dumps({"describePrompts": prompts, "configHash": config_hash},
                              separators=(",", ":")))


def load_prompts(state_dir: Path) -> dict | None:
    """The stored prompts, or None before Protect synced any."""
    path = Path(state_dir) / PROMPTS_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(raw) > MAX_PROMPTS_BYTES + 1024 or path.is_symlink():
        raise DeepModeError("Stored describe prompts are unusable")
    try:
        prompts, _ = validate_prompts(json.loads(raw))
    except ValueError as exc:
        raise DeepModeError("Stored describe prompts are unusable") from exc
    return prompts


def select_prompt(prompts: dict, types: list[str]) -> dict:
    """The prompt for the exact type combination, else the first single type."""
    key = combo_key(types)
    entry = prompts.get(key)
    if entry is None:
        present = set(key.split("+")) if key else set()
        entry = next((prompts[t] for t in DESCRIBE_TYPES if t in present and t in prompts), None)
    if entry is None:
        raise DeepModeError("No describe prompt for these object types")
    return {"system": _decode(entry["systemPrompt"]), "user": _decode(entry["userPrompt"]),
            "schema": json.loads(_decode(entry["describeSchema"])),
            "sampling": {name: entry[name] for name in ("temperature", "topK", "topP", "minP",
                                                        "repeatPenalty", "presencePenalty")
                         if name in entry},
            "margin": float(entry.get("bboxMargin", 0.1))}


def parse_description(text: str) -> tuple[str, list[str]]:
    """The model's JSON answer as (description, labels); labels are key:value."""
    try:
        value = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError as exc:
        raise DeepModeError("The model did not answer with JSON") from exc
    description = value.get("description") if isinstance(value, dict) else None
    labels = value.get("labels", []) if isinstance(value, dict) else None
    if (not isinstance(description, str) or not description.strip() or len(description) > 4000
            or not isinstance(labels, list)):
        raise DeepModeError("The model's JSON lacks a description")
    kept, seen = [], set()
    for label in labels:
        # Qwen3-VL writes "key: value" (live, 30 Sep); spaces around the colon go.
        key, _, value = label.partition(":") if isinstance(label, str) else ("", "", "")
        key, value = key.strip(), " ".join(value.split())
        normalized = f"{key}:{value}"
        if (re.fullmatch(r"[A-Za-z]+", key) and value and ":" not in value and len(normalized) <= 64
                and normalized not in seen):
            seen.add(normalized)
            kept.append(normalized)
    return description.strip(), kept[:MAX_LABELS]


def padded_reid(vector: list[float]) -> list[float]:
    """An L2-normalized re-ID vector zero-padded to the session column's 512 values.

    Zero padding leaves every cosine similarity unchanged.
    """
    if not isinstance(vector, list) or not 1 <= len(vector) <= REID_DIMENSIONS:
        raise DeepModeError("Re-ID vector has the wrong size")
    norm = sum(v * v for v in vector) ** 0.5
    if not norm or norm != norm:
        raise DeepModeError("Re-ID vector is empty")
    return [round(v / norm, 6) for v in vector] + [0.0] * (REID_DIMENSIONS - len(vector))


def validate_embed_request(command: dict) -> tuple[dict, str]:
    """A ``generate-embeddings`` RequestAI; returns (payload, callback path)."""
    body = command.get("payload")
    if (set(command) - {"targetUri", "timeoutMs", "resUrl", "payload"}
            or not isinstance(body, dict) or set(body) != {"camera", "event", "images"}
            or not isinstance(body["camera"], str) or not _ID.fullmatch(body["camera"])
            or not isinstance(body["event"], str) or not _ID.fullmatch(body["event"])
            or not isinstance(body["images"], list)
            or not 1 <= len(body["images"]) <= MAX_EMBED_IMAGES):
        raise DeepModeError("Unsupported generate-embeddings payload")
    for image in body["images"]:
        if (not isinstance(image, dict)
                or not {"reqUrl", "thumbnailId", "objectId"} <= set(image)
                or set(image) - {"reqUrl", "thumbnailId", "objectId", "objectType", "trackerId"}
                or not isinstance(image["thumbnailId"], str) or not _ID.fullmatch(image["thumbnailId"])
                or not isinstance(image["objectId"], str) or not _ID.fullmatch(image["objectId"])
                or image["reqUrl"] != f"/internal/aiprocessors/image/{image['thumbnailId']}"
                or image.get("objectType") not in (None, *DESCRIBE_TYPES)):
            raise DeepModeError("Unsupported generate-embeddings image")
    path = urlsplit(command.get("resUrl") or "").path
    if not EMBED_CALLBACK.fullmatch(path):
        raise DeepModeError("Embeddings callbacks use the task route")
    return body, path


def validate_describe_request(body: Any) -> None:
    """A 7.3.70 session ``describe`` payload: images (open) or video windows (close)."""
    if (not isinstance(body, dict)
            or not {"camera", "event", "pass", "promptProfile"} <= set(body)
            or set(body) - {"camera", "event", "pass", "promptProfile", "images", "videos"}
            or not isinstance(body["camera"], str) or not _ID.fullmatch(body["camera"])
            or not isinstance(body["event"], str) or not _ID.fullmatch(body["event"])
            or body["pass"] not in ("open", "close") or body["promptProfile"] != "session-v1"):
        raise DeepModeError("Unsupported describe payload")
    images, videos = body.get("images", []), body.get("videos", [])
    if not isinstance(images, list) or not isinstance(videos, list) or bool(images) == bool(videos):
        raise DeepModeError("Exactly one nonempty images or videos list is required")
    for image in images:
        if (not isinstance(image, dict) or not isinstance(image.get("thumbnailId"), str)
                or not _ID.fullmatch(image["thumbnailId"])
                or image.get("reqUrl") != f"/internal/aiprocessors/image/{image['thumbnailId']}"
                or image.get("objectType") not in DESCRIBE_TYPES):
            raise DeepModeError("Unsupported describe image")
    objects = 0
    for video in videos:
        if (not isinstance(video, dict) or set(video) != {"reqUrl", "objects"}
                or not isinstance(video["reqUrl"], str) or not isinstance(video["objects"], list)
                or not video["objects"]):
            raise DeepModeError("Unsupported describe video")
        for item in video["objects"]:
            coord = item.get("coord") if isinstance(item, dict) else None
            if (not isinstance(item, dict) or item.get("objectType") not in DESCRIBE_TYPES
                    or type(item.get("ts")) is not int or not isinstance(coord, list)
                    or len(coord) != 4 or any(type(v) not in (int, float) for v in coord)):
                raise DeepModeError("Unsupported describe object")
            objects += 1
    if len(images) + objects > MAX_DESCRIBE_IMAGES:
        raise DeepModeError("Too many describe inputs")

