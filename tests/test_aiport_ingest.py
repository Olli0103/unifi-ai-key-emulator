"""AI Port stream admission and process lifecycle with synthetic frames."""

import json
from pathlib import Path
import shlex
import sys
import time

import pytest

from aikey.aiport_candidate import CandidateService
from aikey.aiport_ingest import AiPortIngress, IngressError


CAMERA_MAC = "2A1122334455"
SOURCE_IP = "192.168.10.1"
FRAME = b"\xff\xd8synthetic-frame\xff\xd9"


def fake_decoder(tmp_path: Path, *, emit_frame: bool = True,
                 stderr_text: str | None = None) -> tuple[str, Path]:
    executable = tmp_path / "synthetic-decoder"
    script = tmp_path / "synthetic-decoder.py"
    args_file = tmp_path / "decoder-args.json"
    output = "import sys, time, json\nfrom pathlib import Path\n"
    output += f"Path({str(args_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    if emit_frame:
        output += f"sys.stdout.buffer.write({FRAME!r})\nsys.stdout.buffer.flush()\n"
    if stderr_text is not None:
        output += f"sys.stderr.write({stderr_text!r})\nsys.stderr.flush()\n"
    else:
        output += "time.sleep(30)\n"
    script.write_text(output)
    executable.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
                          + shlex.quote(str(script)) + ' "$@"\n')
    executable.chmod(0o700)
    return str(executable), args_file


def start_payload(**changes) -> dict:
    payload = {"streaming": True, "ip": SOURCE_IP, "port": "7447",
               "uri": "SyntheticAlias123", "deviceID": CAMERA_MAC,
               "width": 1920, "height": 1080, "fps": 30}
    payload.update(changes)
    return payload


@pytest.mark.asyncio
async def test_ingress_starts_only_after_frame_and_stops_process(tmp_path):
    decoder, args_file = fake_decoder(tmp_path)
    ingress = AiPortIngress(camera_mac=CAMERA_MAC, source_ip=SOURCE_IP,
                           ffmpeg_path=decoder, start_timeout=2)
    try:
        assert await ingress.control(start_payload()) == {"status": "started", "usedPoints": 2}
        assert ingress.latest_frame() == FRAME
        assert ingress.frame_count == 1
        assert ingress.list_streams() == [{"deviceID": CAMERA_MAC, "points": 2}]
        args = json.loads(args_file.read_text())
        assert args[args.index("-i") + 1] == "rtsp://192.168.10.1:7447/SyntheticAlias123"
        assert args[args.index("-timeout") + 1] == "5000000"
        assert "-rw_timeout" not in args
        assert await ingress.control({"streaming": False, "deviceID": CAMERA_MAC}) == {
            "status": "stopped", "usedPoints": 0}
        assert ingress.list_streams() == []
        assert ingress.latest_frame() is None
        assert ingress.total_frames_decoded == 1
    finally:
        await ingress.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,code", [
    ({"ip": "192.168.10.2"}, "stream_source_not_authorized"),
    ({"port": "7441"}, "stream_source_not_authorized"),
    ({"deviceID": "0123456789AB"}, "camera_not_authorized"),
    ({"uri": "../camera"}, "invalid_stream_alias"),
    ({"uri": "name?token=secret"}, "invalid_stream_alias"),
    ({"fps": float("nan")}, "invalid_stream_dimensions"),
])
async def test_ingress_rejects_untrusted_control_before_process(tmp_path, changes, code):
    decoder, args_file = fake_decoder(tmp_path)
    ingress = AiPortIngress(camera_mac=CAMERA_MAC, source_ip=SOURCE_IP,
                           ffmpeg_path=decoder)
    with pytest.raises(IngressError, match=code):
        await ingress.control(start_payload(**changes))
    assert not args_file.exists()
    await ingress.close()


@pytest.mark.asyncio
async def test_ingress_never_reports_started_without_a_frame(tmp_path):
    decoder, _ = fake_decoder(tmp_path, emit_frame=False)
    ingress = AiPortIngress(camera_mac=CAMERA_MAC, source_ip=SOURCE_IP,
                           ffmpeg_path=decoder, start_timeout=0.1)
    with pytest.raises(IngressError, match="stream_start_timeout"):
        await ingress.control(start_payload())
    assert ingress.list_streams() == []
    await ingress.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stderr_text,code,markers,terms", [
    ("[rtsp] method DESCRIBE failed: 401 Unauthorized", "rtsp_access_denied", (),
     ("describe", "failed", "rtsp", "unauthorized")),
    ("[rtsp] method DESCRIBE failed: 404 Not Found", "rtsp_stream_not_found", (),
     ("describe", "failed", "found", "not", "rtsp")),
    ("Connection refused", "rtsp_connect_failed", (), ("connection", "refused")),
    ("Option rw_timeout not found", "decoder_option_missing", (),
     ("found", "not", "option", "timeout")),
    ("[rtsp] method DESCRIBE failed: 454 Session Not Found", "rtsp_status_454", (),
     ("describe", "failed", "found", "not", "rtsp", "session")),
    ("Failed to open private-alias-123", "stream_ended", (), ("failed", "open")),
    ("Error opening input file rtsp://192.168.10.1/private-alias-123",
     "stream_ended", ("input_open_failed",), ("error", "file", "input", "opening", "rtsp")),
])
async def test_decoder_failure_is_classified_without_exposing_private_output(
        tmp_path, stderr_text, code, markers, terms):
    decoder, _ = fake_decoder(tmp_path, emit_frame=False, stderr_text=stderr_text)
    ingress = AiPortIngress(camera_mac=CAMERA_MAC, source_ip=SOURCE_IP,
                           ffmpeg_path=decoder, start_timeout=2)
    with pytest.raises(IngressError) as failure:
        await ingress.control(start_payload())
    assert failure.value.code == code
    assert "private-alias-123" not in str(failure.value)
    assert ingress.last_decoder_exit_code == 0
    assert ingress.last_decoder_stderr_seen
    assert ingress.last_decoder_error_markers == markers
    assert "private-alias-123" not in repr(ingress.last_decoder_error_markers)
    assert ingress.last_decoder_error_terms == terms
    assert "private" not in ingress.last_decoder_error_terms
    assert "alias" not in ingress.last_decoder_error_terms
    assert ingress.list_streams() == []
    await ingress.close()


@pytest.mark.asyncio
async def test_success_clears_previous_decoder_error(tmp_path):
    decoder, _ = fake_decoder(tmp_path, emit_frame=False,
                              stderr_text="Option rw_timeout not found")
    ingress = AiPortIngress(camera_mac=CAMERA_MAC, source_ip=SOURCE_IP,
                           ffmpeg_path=decoder, start_timeout=2)
    with pytest.raises(IngressError, match="decoder_option_missing"):
        await ingress.control(start_payload())
    assert ingress.last_decoder_error_terms == ("found", "not", "option", "timeout")
    fake_decoder(tmp_path)
    try:
        assert await ingress.control(start_payload()) == {"status": "started", "usedPoints": 2}
        assert ingress.last_decoder_exit_code is None
        assert ingress.last_decoder_stderr_seen is False
        assert ingress.last_decoder_error_markers == ()
        assert ingress.last_decoder_error_terms == ()
    finally:
        await ingress.close()


@pytest.mark.asyncio
async def test_candidate_stream_response_excludes_private_alias(tmp_path):
    decoder, _ = fake_decoder(tmp_path)
    config = {"controller_ip": SOURCE_IP, "device_ip": "192.168.10.20",
              "mac": "2A9988776655", "firmware_version": "5.1.12",
              "diagnostic_hello_until": int(time.time()) + 60,
              "diagnostic_stream": {"camera_mac": CAMERA_MAC, "source_ip": SOURCE_IP,
                                    "ffmpeg_path": decoder}}
    service = CandidateService(config, tmp_path)
    service.param_agreements = 1

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    try:
        await service._send_diagnostic_hello(sink)
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ubnt_avclient_hello", "inResponseTo": 1}).encode())
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ubnt_avclient_paramAgreement", "messageId": 39}).encode())
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "UiStreamControl", "messageId": 40,
            "payload": start_payload()}).encode())
        assert sink.messages[-2]["statusCode"] == 0
        assert sink.messages[-2]["payload"] == {"status": "started", "usedPoints": 2}
        assert sink.messages[-1]["functionName"] == "EventAIPortStatus"
        assert sink.messages[-1]["payload"] == {
            "deviceID": CAMERA_MAC, "isStreaming": True,
            "isSmartDetectReady": False, "isAudioEventReady": False}
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "GetStreamList", "messageId": 41, "payload": {}}).encode())
        assert sink.messages[-1]["payload"] == {"list": [{"deviceID": CAMERA_MAC,
                                                           "points": 2}]}
        health = await service._health(None)
        assert "SyntheticAlias123" not in health.text
        assert service.stream_controls_started == 1
        assert service.stream_lists_answered == 1
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "UiStreamControl", "messageId": 42,
            "payload": start_payload(ip="192.168.10.99")}).encode())
        assert sink.messages[-1]["payload"] == {
            "description": "stream_source_not_authorized"}
        health = await service._health(None)
        assert "192.168.10.99" not in health.text
        assert service.last_stream_error == "stream_source_not_authorized"
    finally:
        await service.ingress.close()


@pytest.mark.asyncio
async def test_new_websocket_requires_fresh_parameter_agreement(tmp_path):
    config = {"controller_ip": SOURCE_IP, "device_ip": "192.168.10.20",
              "mac": "2A9988776655", "firmware_version": "5.1.12",
              "diagnostic_hello_until": int(time.time()) + 60}
    service = CandidateService(config, tmp_path)

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    await service._send_diagnostic_hello(sink)
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ubnt_avclient_hello", "inResponseTo": 1}).encode())
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "ubnt_avclient_paramAgreement", "messageId": 5}).encode())
    assert service.param_agreements == 1
    await service._send_diagnostic_hello(sink)
    before = len(sink.messages)
    await service._handle_diagnostic_frame(sink, json.dumps({
        "functionName": "UiStreamControl", "messageId": 6,
        "payload": {"streaming": False, "deviceID": CAMERA_MAC}}).encode())
    assert len(sink.messages) == before
