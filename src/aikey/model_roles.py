"""Versioned saved role assignments, not discovery or capability/weight proof.

Only allowlisted non-secret selection fields enter an identity. Source URLs and
paths are hashed, never returned; credentials and camera scopes do not enter it.
This inventory neither instantiates a model/client nor reads any referenced file.
"""

from __future__ import annotations

import hashlib
import json

from . import clip, deep_mode
from .providers import DEFAULT_BASE_URLS

SCHEMA = "aikey-model-registry/1"


def _part(config, key):
    value = config.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Role configuration unavailable")
    return value


def _row(role, selection, *, backend, source, expected, output, configured,
         dimensions=None, source_dimensions=None, revision=None, policy="separate_feature_flow",
         assignment="explicit"):
    model = selection.get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError("Role model identity unavailable")
    if revision is not None and not isinstance(revision, str):
        raise ValueError("Role model revision unavailable")
    declared = {"schema": SCHEMA, "role": role, "backend": backend, "model": model,
                "expected": expected, "output": output, "source": source,
                "dimensions": dimensions, "source_dimensions": source_dimensions,
                "revision": revision,
                "parameters": {k: selection[k] for k in ("temperature", "max_output_tokens")
                               if k in selection}}
    fingerprint = hashlib.sha256(json.dumps(declared, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode()).hexdigest()
    return {"role": role, "configured": bool(configured), "backend": backend,
            "model": model if configured else None, "expected_model": expected, "output": output,
            "dimensions": dimensions, "source_dimensions": source_dimensions,
            "selection_id": fingerprint if configured else None, "declared_revision": revision,
            "change_policy": policy, "assignment": assignment,
            "capability_status": "not_checked_by_this_read", "weights_verified": False}


def key_roles(config: dict, revision: str) -> dict:
    vision = _part(config, "inference")
    embedding = _part(config, "embeddings")
    finding = _part(config, "find_anything")
    speech = _part(config, "speech_to_text")
    deep = _part(config, "deep_understanding")
    face = _part(config, "face_recognition")
    enhance = _part(config, "face_enhancement")
    provider = vision.get("provider", "openai-compatible")
    rows = [
        _row("caption", vision, backend=provider,
             source=vision.get("base_url") or DEFAULT_BASE_URLS.get(provider),
             expected="explicit vision model", output="description text",
             configured=bool(vision.get("model")), policy="role_model_preview"),
        _row("text_embedding", {"model": embedding.get("model", "multilingual-e5-small")},
             backend=embedding.get("backend", "disabled"),
             source=embedding.get("model_path") if embedding.get("backend") == "sentence-transformers"
             else embedding.get("endpoint") or embedding.get("base_url"),
             expected="multilingual-e5-small", output="unit text vector", dimensions=384,
             configured=embedding.get("backend", "disabled") != "disabled",
             revision=embedding.get("revision"), policy="index_migration"),
        _row("search_image_text", {"model": clip.MODEL}, backend="local-clip-onnx",
             source=finding.get("clip_server"), expected=clip.MODEL, output="paired image/text vector",
             dimensions=clip.DIMENSIONS, configured=bool(finding), policy="index_migration"),
        _row("reverification", {"model": clip.MODEL}, backend="local-clip-onnx",
             source=finding.get("clip_server"), expected=clip.MODEL, output="bounded local verdict",
             dimensions=clip.DIMENSIONS, configured=finding.get("reverification") is True),
        _row("speech", speech, backend=speech.get("provider"), source=speech.get("base_url"),
             expected="transcription API model", output="bounded timestamped speech segments",
             configured=bool(speech), policy="role_model_preview"),
        _row("reidentification", {}, backend="local-reid-http", source=deep.get("reid_server"),
             expected=deep_mode.REID_MODEL, output="person vector", dimensions=deep_mode.REID_DIMENSIONS,
             source_dimensions=deep_mode.REID_SOURCE_DIMENSIONS, configured=bool(deep)),
        _row("recognition", {}, backend="local-sface-http", source=face.get("server"),
             expected="SFace-compatible 128-value encoder", output="private face vector",
             dimensions=128, configured=bool(face)),
        _row("reranking", {}, backend="local-rerank-http", source=deep.get("rerank_server"),
             expected="server-managed cross encoder", output="one finite score per document",
             configured=bool(deep.get("rerank_server"))),
        _row("enhancement", {}, backend="local-enhancement-http", source=enhance.get("server"),
             expected="server-managed image enhancer", output="bounded JPEG derivative",
             configured=bool(enhance)),
    ]
    return {"schema": SCHEMA, "configuration_revision": revision, "roles": rows}


def port_roles(config: dict, revision: str) -> dict:
    detector = _part(config, "live_pool_detector")
    vision = _part(detector, "provider_config")
    face = _part(config, "live_face")
    explicit_ocr = "ocr_provider_config" in detector
    ocr = _part(detector, "ocr_provider_config") if explicit_ocr else vision
    api = detector.get("inference_backend") == "vision_api"
    model_digest = detector.get("model_sha256") or detector.get("checkpoint_sha256")
    face_digest = (hashlib.sha256(json.dumps({k: face[k] for k in (
        "detector_sha256", "embedder_sha256")}, sort_keys=True).encode()).hexdigest()
                   if face and all(k in face for k in ("detector_sha256", "embedder_sha256")) else None)
    rows = [
        _row("detection", vision if api else {},
             backend=vision.get("provider") if api else detector.get("inference_backend", "local-pytorch"),
             source=vision.get("base_url") if api else model_digest,
             expected="explicit vision model" if api else "RF-DETR-Nano",
             output="bounded object boxes and scores", configured=bool(detector),
             revision=None if api else model_digest,
             policy="role_model_preview" if api else "separate_feature_flow"),
        _row("reverification", {}, backend="local-score-http", source=detector.get("score_server"),
             expected="server-managed local detector", output="bounded object scores",
             configured=bool(detector.get("score_server"))),
        _row("ocr", ocr, backend=ocr.get("provider"), source=ocr.get("base_url"),
             expected="explicit vision OCR model", output="plate string or null",
             configured=api and bool(detector.get("plate_cameras")),
             policy="role_model_preview" if explicit_ocr else "separate_ocr_configuration",
             assignment="explicit" if explicit_ocr else "legacy_shared_detection"),
        _row("recognition", {}, backend="local-onnx/" + str(face.get("embedder_input", "arcface")),
             source=face_digest, expected="512-value face encoder", output="local face vector",
             dimensions=512, configured=bool(face), revision=face_digest),
    ]
    return {"schema": SCHEMA, "configuration_revision": revision, "roles": rows}
