"""Offline AI Key interface experiment. No transport, authentication, or inference.

This is an independent implementation of a bounded JSON/UCP4 subset observed
in firmware. Passing its checks does not establish controller acceptance.
"""

from dataclasses import dataclass
import json
import math
import re
import struct


MAX_RECORD_BYTES = 1024 * 1024  # Local guard, not an observed protocol maximum.
RECOGNIZE_ANYTHING_PATH = "/internal/aiprocessors/recognize-anything"
_RECORD = struct.Struct(">BBBBI")


class ContractError(ValueError):
    """The message is outside the explicitly supported offline subset."""


@dataclass(frozen=True)
class Message:
    header: dict
    body: dict


def _json_bytes(value):
    if not isinstance(value, dict):
        raise ContractError("JSON record must be an object")
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ContractError("Record is not finite UTF-8 JSON") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ContractError("Non-finite JSON number")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ContractError("Non-finite JSON number")
    return parsed


def _json_object(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError("Invalid JSON record") from exc
    if not isinstance(value, dict):
        raise ContractError("JSON record must be an object")
    return value


def encode_message(header, body, *, max_record_bytes=MAX_RECORD_BYTES):
    """Serialize two uncompressed JSON records; this performs no I/O."""
    parts = []
    for record_type, value in ((1, header), (2, body)):
        raw = _json_bytes(value)
        if len(raw) > max_record_bytes or len(raw) > 0xFFFFFFFF:
            raise ContractError("Record exceeds local byte limit")
        parts.extend((_RECORD.pack(record_type, 1, 0, 0, len(raw)), raw))
    return b"".join(parts)


def decode_message(wire, *, max_record_bytes=MAX_RECORD_BYTES):
    """Decode exactly one message, rejecting unsupported flags and trailing data."""
    if not isinstance(wire, bytes):
        raise ContractError("Input must be bytes")
    offset, records = 0, []
    for expected_type in (1, 2):
        if len(wire) - offset < _RECORD.size:
            raise ContractError("Truncated record header")
        record_type, fmt, compression, reserved, size = _RECORD.unpack_from(wire, offset)
        offset += _RECORD.size
        if (record_type, fmt, compression, reserved) != (expected_type, 1, 0, 0):
            raise ContractError("Unsupported record type, format, or flags")
        if size > max_record_bytes:
            raise ContractError("Record exceeds local byte limit")
        if len(wire) - offset < size:
            raise ContractError("Truncated record payload")
        raw = wire[offset:offset + size]
        offset += size
        # The observed SLM reader permits an empty body, but not an empty header.
        records.append({} if expected_type == 2 and not raw else _json_object(raw))
    if offset != len(wire):
        raise ContractError("Trailing bytes after message")
    return Message(*records)


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ContractError(f"{name} must be a nonempty string")


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ContractError(f"{name} must be a nonnegative integer")


def _strings(value, name):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ContractError(f"{name} must be a list of strings")


def _embedding(value, name):
    if not isinstance(value, list) or any(
        not (type(x) is int or type(x) is float and math.isfinite(x)) for x in value
    ):
        raise ContractError(f"{name} must be a list of finite numbers")
    # This helper checks shape only; different controller paths use different widths.


def validate_e5_query_embedding(result, *, require_model=True):
    """Check the Protect 7.2.105 session-search E5 profile, without inference.

    Source module 50407 requires 384 values and rejects a supplied wrong model.
    Its receiver permits an absent model. Our default additionally requires an
    explicit model to avoid asserting compatibility from a dimension alone.
    Even a correct label and length do not prove a compatible embedding space.
    """
    if not isinstance(result, dict):
        raise ContractError("Expected query result object")
    _embedding(result.get("txtEmbed"), "txtEmbed")
    if len(result["txtEmbed"]) != 384:
        raise ContractError("E5 session query requires 384 values")
    model = result.get("model")
    if model is None and not require_model:
        return
    if not isinstance(model, str) or model.split("@", 1)[0] != "multilingual-e5-small":
        raise ContractError("E5 query requires explicit multilingual-e5-small model")


def validate_request(message):
    header, body = message.header, message.body
    if header.get("type") != "request":
        raise ContractError("Expected request envelope")
    _text(header.get("id"), "id")
    _integer(header.get("timestamp"), "timestamp")
    action = header.get("action")
    if action == "NL_PARSE":
        _text(body.get("querySentence"), "querySentence")
        if "tagHierarchy" in body and type(body["tagHierarchy"]) is not bool:
            raise ContractError("tagHierarchy must be a boolean")
    elif action == "IMAGE_SEARCH":
        _text(body.get("imgUri"), "imgUri")
    else:
        raise ContractError("Unsupported request action")
    return message


def validate_result(action, result):
    """Validate shape only. This does not assess model output or semantic accuracy."""
    _json_bytes(result)
    if action == "IMAGE_SEARCH":
        if set(result) != {"imgEmbed"}:
            raise ContractError("IMAGE_SEARCH result requires only imgEmbed")
        _embedding(result["imgEmbed"], "imgEmbed")
    elif action == "NL_PARSE":
        required = {"keyTags", "objectTypes"}
        allowed = required | {"txtEmbed", "startTime", "endTime", "timeTag", "exact_match"}
        if not required <= set(result) or not set(result) <= allowed:
            raise ContractError("Unsupported NL_PARSE result fields")
        _strings(result["objectTypes"], "objectTypes")
        if not isinstance(result["keyTags"], list):
            raise ContractError("keyTags must be a list")
        for tag in result["keyTags"]:
            if not isinstance(tag, dict) or set(tag) != {"matchedWord", "tags"}:
                raise ContractError("Unsupported keyTags entry")
            _text(tag["matchedWord"], "matchedWord")
            _strings(tag["tags"], "tags")
        if "txtEmbed" in result:
            _embedding(result["txtEmbed"], "txtEmbed")
        for name in ("startTime", "endTime"):
            if name in result:
                _integer(result[name], name)
        if "timeTag" in result and not isinstance(result["timeTag"], str):
            raise ContractError("timeTag must be a string")
        if "exact_match" in result and type(result["exact_match"]) is not bool:
            raise ContractError("exact_match must be a boolean")
    else:
        raise ContractError("Unsupported result action")


class RequestLedger:
    """Offline correlation only. Caller supplies every result and timestamp."""

    def __init__(self):
        self._pending = {}

    def accept(self, wire):
        message = validate_request(decode_message(wire))
        request_id = message.header["id"]
        if request_id in self._pending:
            raise ContractError("Duplicate pending request id")
        self._pending[request_id] = message.header["action"]
        return message

    def respond(self, request_id, result, *, timestamp):
        if request_id not in self._pending:
            raise ContractError("Unknown or already completed request id")
        validate_result(self._pending[request_id], result)
        _integer(timestamp, "timestamp")
        # Firmware success envelopes use error=null and errorCode=0.
        wire = encode_message({"id": request_id, "type": "response", "timestamp": timestamp,
                               "error": None, "errorCode": 0}, result)
        del self._pending[request_id]
        return wire


def description_result(event_id, description):
    """Key 2.2.8 emitted shape; misses cameraId required by Protect 7.2.105."""
    _text(event_id, "eventId")
    _text(description, "description")
    return {"eventId": event_id, "status": "success", "description": description}


def receiver_description_result(camera_id, event_id, description):
    """Protect 7.2.105 legacy description schema, with explicit camera identity."""
    _text(camera_id, "cameraId")
    return {"cameraId": camera_id, **description_result(event_id, description)}


def description_multipart(result, *, profile, boundary, filename="description.json"):
    """Return (content_type, bytes) with one JSON file part named ram. No sender."""
    fields = {"eventId", "status", "description"}
    if profile == "protect-7.2.105":
        fields.add("cameraId")
    elif profile != "key-2.2.8":
        raise ContractError("Select an observed description contract profile")
    if not isinstance(result, dict) or set(result) != fields:
        raise ContractError("Unsupported description result fields")
    if "cameraId" in fields:
        _text(result["cameraId"], "cameraId")
    _text(result["eventId"], "eventId")
    _text(result["description"], "description")
    if result["status"] != "success":
        raise ContractError("Only observed description success shape is supported")
    if not isinstance(boundary, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", boundary):
        raise ContractError("Invalid local multipart boundary")
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", filename):
        raise ContractError("Invalid local multipart filename")
    raw = _json_bytes(result)
    marker = boundary.encode("ascii")
    if marker in raw:
        raise ContractError("Boundary occurs in JSON payload")
    body = (b"--" + marker + b'\r\nContent-Disposition: form-data; name="ram"; filename="'
            + filename.encode("ascii") + b'"\r\nContent-Type: application/json\r\n\r\n'
            + raw + b"\r\n--" + marker + b"--\r\n")
    return f"multipart/form-data; boundary={boundary}", body


def task_description_json(task_id, result):
    """Separate Protect 7.2.105 task callback: return path and JSON, never send.

    taskId belongs in the route. The task-ledger acceptance rules are outside
    this shape experiment. This is not the legacy multipart 'ram' contract.
    """
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise ContractError("Invalid local task id")
    _json_bytes(result)
    strings = {"camera", "event", "pass", "description", "model", "version"}
    allowed = strings | {"labels", "descEmbedding", "failed"}
    if "description" not in result or not set(result) <= allowed:
        raise ContractError("Unsupported task description result fields")
    for name in strings & set(result):
        if not isinstance(result[name], str):
            raise ContractError(f"{name} must be a string")
    if "labels" in result:
        _strings(result["labels"], "labels")
    if "descEmbedding" in result and result["descEmbedding"] is not None:
        _embedding(result["descEmbedding"], "descEmbedding")
    if "failed" in result:
        if not isinstance(result["failed"], list):
            raise ContractError("failed must be a list")
        for failure in result["failed"]:
            if not isinstance(failure, dict) or not set(failure) <= {"objectId", "reason"}:
                raise ContractError("Unsupported failed entry")
            if any(not isinstance(value, str) for value in failure.values()):
                raise ContractError("Failure values must be strings")
    return f"/internal/aiprocessors/descriptions/{task_id}", _json_bytes(result)
