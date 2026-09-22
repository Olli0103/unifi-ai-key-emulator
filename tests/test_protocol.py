"""Synthetic fixtures only. Tests do not load, import, or run firmware."""

from email import policy
from email.parser import BytesParser
import json
import unittest

from aikey.protocol import (
    ContractError, RequestLedger, decode_message, description_multipart,
    description_result, encode_message, receiver_description_result,
    task_description_json, validate_e5_query_embedding, validate_result,
)


class FramingTests(unittest.TestCase):
    def test_fixed_wire_bytes(self):
        # Hand-authored 8-byte prefixes, independently counted payload lengths.
        fixture = bytes.fromhex("010100000000000a") + b'{"id":"a"}'
        fixture += bytes.fromhex("0201000000000009") + b'{"x":"y"}'
        self.assertEqual(encode_message({"id": "a"}, {"x": "y"}), fixture)
        decoded = decode_message(fixture)
        self.assertEqual(decoded.header, {"id": "a"})
        self.assertEqual(decoded.body, {"x": "y"})

    def test_nonascii_length_counts_bytes(self):
        wire = encode_message({}, {"text": "Tür"})
        self.assertEqual(wire[10:18], bytes.fromhex("020100000000000f"))
        self.assertEqual(wire[18:], b'{"text":"T\xc3\xbcr"}')

    def test_every_truncated_prefix_rejected(self):
        fixture = bytes.fromhex("01010000000000027b7d02010000000000027b7d")
        for length in range(len(fixture)):
            with self.subTest(length=length), self.assertRaises(ContractError):
                decode_message(fixture[:length])

    def test_all_record_flags_checked(self):
        fixture = bytearray.fromhex("01010000000000027b7d02010000000000027b7d")
        for offset in (0, 1, 2, 3, 10, 11, 12, 13):
            mutated = fixture.copy()
            mutated[offset] = 7
            with self.subTest(offset=offset), self.assertRaises(ContractError):
                decode_message(bytes(mutated))

    def test_oversized_declared_length_rejected_before_payload(self):
        with self.assertRaisesRegex(ContractError, "byte limit"):
            decode_message(bytes.fromhex("01010000ffffffff"))
        with self.assertRaisesRegex(ContractError, "byte limit"):
            encode_message({}, {"large": "123456"}, max_record_bytes=3)

    def test_trailing_data_rejected(self):
        fixture = bytes.fromhex("01010000000000027b7d02010000000000027b7d00")
        with self.assertRaisesRegex(ContractError, "Trailing"):
            decode_message(fixture)

    def test_empty_body_observed_fallback(self):
        fixture = bytes.fromhex("01010000000000027b7d0201000000000000")
        self.assertEqual(decode_message(fixture).body, {})

    def test_bad_json_invalid_utf8_and_nonobject_payloads(self):
        for raw in (b'{', b'[]', b'null', b'{"a":1,"a":2}', b'{"x":NaN}',
                    b'{"x":1e9999}', b'{"x":"\xff"}'):
            # Independent assembler exercises decoder without its encoder.
            wire = bytes.fromhex("01010000000000027b7d")
            wire += b'\x02\x01\x00\x00' + len(raw).to_bytes(4, 'big') + raw
            with self.subTest(raw=raw), self.assertRaises(ContractError):
                decode_message(wire)

    def test_encoder_rejects_nonfinite_json(self):
        with self.assertRaises(ContractError):
            encode_message({}, {"value": float("nan")})


def synthetic_request(request_id, action, body):
    # Test assembler is deliberately separate from the production encoder.
    header = {"id": request_id, "type": "request", "timestamp": 123, "action": action}
    h, b = json.dumps(header).encode(), json.dumps(body).encode()
    return b'\x01\x01\x00\x00' + len(h).to_bytes(4, 'big') + h + \
        b'\x02\x01\x00\x00' + len(b).to_bytes(4, 'big') + b


def independently_read(wire):
    h_size = int.from_bytes(wire[4:8], 'big')
    b_size = int.from_bytes(wire[12 + h_size:16 + h_size], 'big')
    header = json.loads(wire[8:8 + h_size])
    body = json.loads(wire[16 + h_size:16 + h_size + b_size])
    return header, body


class CorrelationTests(unittest.TestCase):
    def test_out_of_order_results_keep_ids_and_payload(self):
        ledger = RequestLedger()
        ledger.accept(synthetic_request("synthetic-nl", "NL_PARSE", {"querySentence": "red car"}))
        ledger.accept(synthetic_request("synthetic-image", "IMAGE_SEARCH", {"imgUri": "https://invalid.example/test"}))
        # Deliberately tiny artificial vectors test transport only, never inference.
        image = {"imgEmbed": [0.25, -0.75]}
        nl = {"keyTags": [{"matchedWord": "car", "tags": ["car"]}],
              "objectTypes": ["vehicle"], "txtEmbed": [1.0, 0.0],
              "startTime": 1000, "endTime": 2000, "timeTag": "fixture", "exact_match": False}
        for request_id, payload in (("synthetic-image", image), ("synthetic-nl", nl)):
            header, body = independently_read(ledger.respond(request_id, payload, timestamp=456))
            self.assertEqual(header, {"id": request_id, "type": "response", "timestamp": 456,
                                      "error": None, "errorCode": 0})
            self.assertEqual(body, payload)

    def test_unknown_duplicate_and_completed_ids(self):
        ledger = RequestLedger()
        wire = synthetic_request("id-1", "IMAGE_SEARCH", {"imgUri": "fixture"})
        ledger.accept(wire)
        with self.assertRaises(ContractError):
            ledger.accept(wire)
        with self.assertRaises(ContractError):
            ledger.respond("wrong", {"imgEmbed": []}, timestamp=1)
        ledger.respond("id-1", {"imgEmbed": []}, timestamp=1)
        with self.assertRaises(ContractError):
            ledger.respond("id-1", {"imgEmbed": []}, timestamp=1)

    def test_validation_failure_keeps_request_pending(self):
        ledger = RequestLedger()
        ledger.accept(synthetic_request("i", "IMAGE_SEARCH", {"imgUri": "fixture"}))
        with self.assertRaises(ContractError):
            ledger.respond("i", {"imgEmbed": [True]}, timestamp=1)
        self.assertEqual(independently_read(ledger.respond("i", {"imgEmbed": []}, timestamp=2))[0]["id"], "i")

    def test_invalid_and_unsupported_requests(self):
        for action, body in (("NL_PARSE", {}), ("NL_PARSE", {"querySentence": 12}),
                             ("NL_PARSE", {"querySentence": "x", "tagHierarchy": "yes"}),
                             ("IMAGE_SEARCH", {}), ("UNKNOWN", {})):
            with self.subTest(action=action, body=body), self.assertRaises(ContractError):
                RequestLedger().accept(synthetic_request("i", action, body))

    def test_result_schema_rejects_mixed_actions_and_nonfinite_embeddings(self):
        for action, result in (("IMAGE_SEARCH", {"txtEmbed": []}),
                               ("IMAGE_SEARCH", {"imgEmbed": [float("inf")]}),
                               ("NL_PARSE", {"keyTags": ["car"], "objectTypes": []}),
                               ("NL_PARSE", {"keyTags": [], "objectTypes": [], "exact_match": 1})):
            with self.subTest(action=action, result=result), self.assertRaises(ContractError):
                validate_result(action, result)

    def test_observed_empty_failure_shapes_remain_shape_only(self):
        validate_result("NL_PARSE", {"objectTypes": [], "keyTags": []})
        validate_result("IMAGE_SEARCH", {"imgEmbed": []})


class DescriptionTests(unittest.TestCase):
    def test_independent_mime_parser_sees_exact_fields_and_original_event_id(self):
        event_id = 'synthetic/event:"ä"'
        result = description_result(event_id, "Synthetic fixture only. No image was analyzed.")
        content_type, wire = description_multipart(result, profile="key-2.2.8", boundary="contract-boundary")
        parsed = BytesParser(policy=policy.default).parsebytes(
            b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode() + b"\r\n\r\n" + wire)
        parts = list(parsed.iter_parts())
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_param("name", header="content-disposition"), "ram")
        self.assertEqual(parts[0].get_filename(), "description.json")
        self.assertEqual(parts[0].get_content_type(), "application/json")
        received = json.loads(parts[0].get_payload(decode=True))
        self.assertEqual(set(received), {"eventId", "status", "description"})
        self.assertEqual(received["eventId"], event_id)
        self.assertEqual(received["status"], "success")
        self.assertEqual(received, result)

    def test_fixed_multipart_bytes(self):
        expected = (b'--unit\r\nContent-Disposition: form-data; name="ram"; filename="description.json"\r\n'
                    b'Content-Type: application/json\r\n\r\n'
                    b'{"eventId":"e","status":"success","description":"synthetic"}\r\n--unit--\r\n')
        self.assertEqual(description_multipart(description_result("e", "synthetic"),
                                              profile="key-2.2.8", boundary="unit")[1], expected)

    def test_no_boundary_or_filename_injection(self):
        result = description_result("e", "synthetic")
        for kwargs in ({"boundary": 'x\r\nInjected: yes'},
                       {"boundary": "good", "filename": 'bad"\r\n'},
                       {"boundary": "good", "filename": "../../result.json"},
                       {"boundary": "synthetic"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContractError):
                description_multipart(result, profile="key-2.2.8", **kwargs)

    def test_description_requires_explicit_text_and_event(self):
        for event_id, text in (("", "synthetic"), ("e", ""), (None, "synthetic")):
            with self.assertRaises(ContractError):
                description_result(event_id, text)

    def test_receiver_profile_rejects_device_shape_missing_camera(self):
        device_result = description_result("fixture-event", "Synthetic text.")
        with self.assertRaises(ContractError):
            description_multipart(device_result, profile="protect-7.2.105", boundary="test")
        result = receiver_description_result("fixture-camera", "fixture-event", "Synthetic text.")
        self.assertEqual(result, {"cameraId": "fixture-camera", "eventId": "fixture-event",
                                  "status": "success", "description": "Synthetic text."})
        content_type, wire = description_multipart(result, profile="protect-7.2.105", boundary="test")
        parsed = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + wire)
        self.assertEqual(json.loads(next(parsed.iter_parts()).get_payload(decode=True)), result)

    def test_profiles_cannot_silently_mix(self):
        result = receiver_description_result("c", "e", "Synthetic text.")
        for profile in ("key-2.2.8", "invented"):
            with self.assertRaises(ContractError):
                description_multipart(result, profile=profile, boundary="test")


class TaskDescriptionTests(unittest.TestCase):
    def test_task_route_and_json_keep_all_identities(self):
        synthetic = {"camera": "fixture-camera", "event": "fixture-event", "pass": "fixture-pass",
                     "description": "Synthetic fixture. No image analyzed.", "labels": ["fixture"],
                     "descEmbedding": [0.5, -0.5], "model": "synthetic", "version": "fixture",
                     "failed": [{"objectId": "fixture-object", "reason": "synthetic"}]}
        path, raw = task_description_json("fixture-task", synthetic)
        self.assertEqual(path, "/internal/aiprocessors/descriptions/fixture-task")
        self.assertEqual(json.loads(raw), synthetic)
        self.assertNotIn("taskId", json.loads(raw))

    def test_task_minimal_and_null_embedding_shapes(self):
        for payload in ({"description": ""}, {"description": "synthetic", "descEmbedding": None}):
            self.assertEqual(json.loads(task_description_json("t", payload)[1]), payload)

    def test_task_schema_rejects_legacy_shape_and_wrong_fields(self):
        for payload in (description_result("e", "synthetic"),
                        {"description": 3}, {"description": "x", "labels": "label"},
                        {"description": "x", "descEmbedding": [True]},
                        {"description": "x", "failed": [{"reason": False}]}):
            with self.subTest(payload=payload), self.assertRaises(ContractError):
                task_description_json("t", payload)

    def test_task_route_rejects_path_injection(self):
        for task_id in ("../event", "a/b", "a?query=x", ""):
            with self.assertRaises(ContractError):
                task_description_json(task_id, {"description": "synthetic"})


class EmbeddingProfileTests(unittest.TestCase):
    def test_e5_requires_384_values_and_correct_explicit_model(self):
        # A one-hot synthetic vector proves only dimensions, never semantics.
        vector = [1.0] + [0.0] * 383
        validate_e5_query_embedding({"txtEmbed": vector, "model": "multilingual-e5-small@fixture"})
        for result in ({"txtEmbed": vector, "model": "clip-ViT-L-14"},
                       {"txtEmbed": [0.0] * 768, "model": "multilingual-e5-small"},
                       {"txtEmbed": vector[:-1], "model": "multilingual-e5-small"}):
            with self.subTest(model=result["model"], width=len(result["txtEmbed"])), self.assertRaises(ContractError):
                validate_e5_query_embedding(result)

    def test_missing_model_distinguishes_local_guard_from_observed_receiver(self):
        result = {"txtEmbed": [1.0] + [0.0] * 383}
        with self.assertRaises(ContractError):
            validate_e5_query_embedding(result)
        validate_e5_query_embedding(result, require_model=False)


if __name__ == "__main__":
    unittest.main()
