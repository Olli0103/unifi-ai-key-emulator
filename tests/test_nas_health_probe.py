"""Execute the actual in-container probe against bounded fictional HTTP bodies."""

import builtins
import hashlib
import io
import json
import ssl
from types import SimpleNamespace

import pytest

from aikey.aiport_nas_reconcile import _HEALTH_SCRIPT


LIMIT = 256 * 1024  # Same health-response contract as the admin runtime reader.
CERT = "-----BEGIN CERTIFICATE-----\nZmljdGlvbmFsLXRlc3Qtb25seQ==\n-----END CERTIFICATE-----\n"
PIN = hashlib.sha256(ssl.PEM_cert_to_DER_cert(CERT)).hexdigest()


def payload(size):
    value = {"service": "aiport-candidate", "adopted": True,
             "control_connected": True, "fictional_private_diagnostics": ""}
    value["fictional_private_diagnostics"] = "x" * (size - len(json.dumps(value).encode()))
    result = json.dumps(value).encode()
    assert len(result) == size
    return result


def execute(body, *, status=200, pin=PIN, tls_error=False):
    """Only transport/files are doubles; run the shipped probe verbatim."""
    calls = {"connections": [], "requests": [], "reads": [], "trust": []}

    class Certificate:
        def __init__(self, path):
            assert path == "/state/device.crt"

        def __str__(self):
            return "/state/device.crt"

        def read_text(self):
            return CERT

    class Response:
        def __init__(self):
            self.status = status
            self.stream = io.BytesIO(body)

        def read(self, count):
            calls["reads"].append(count)
            return self.stream.read(count)

    class Connection:
        def __init__(self, host, port, *, timeout, context):
            assert (host, port, timeout) == ("127.0.0.1", 443, 5)
            assert context is trust and context.check_hostname is False
            calls["connections"].append((host, port))

        def request(self, method, path):
            calls["requests"].append((method, path))

        def getresponse(self):
            return Response()

    trust = SimpleNamespace(check_hostname=True)

    def context(*, cafile):
        calls["trust"].append(cafile)
        assert cafile == "/state/device.crt"
        if tls_error:
            raise ssl.SSLError("fictional TLS refusal")
        return trust

    replacements = {
        "http.client": SimpleNamespace(client=SimpleNamespace(HTTPSConnection=Connection)),
        "pathlib": SimpleNamespace(Path=Certificate),
        "ssl": SimpleNamespace(PEM_cert_to_DER_cert=ssl.PEM_cert_to_DER_cert,
                               create_default_context=context),
        "sys": SimpleNamespace(argv=["fictional-health-probe", pin]),
    }
    actual_import = builtins.__import__

    def import_module(name, *args, **kwargs):
        return replacements[name] if name in replacements else actual_import(name, *args, **kwargs)

    namespace = {"__builtins__": {**vars(builtins), "__import__": import_module}}
    try:
        exec(compile(_HEALTH_SCRIPT, "<shipped-nas-health-probe>", "exec"), namespace)
    except BaseException as error:
        return error, calls
    return None, calls


@pytest.mark.parametrize("size", [256, 4097, 4098, 129326, 172629, LIMIT])
def test_complete_bounded_health_is_accepted_without_diagnostic_output(size, capsys):
    error, calls = execute(payload(size))
    assert error is None
    assert json.loads(capsys.readouterr().out) == {
        "service": "aiport-candidate", "adopted": True, "control_connected": True}
    assert calls["connections"] == [("127.0.0.1", 443)]
    assert calls["requests"] == [("GET", "/healthz")]
    assert calls["trust"] == ["/state/device.crt"]


@pytest.mark.parametrize("extra", [1, 4096])
def test_oversized_valid_json_prefix_is_refused_not_silently_accepted(extra, capsys):
    # An early complete JSON object plus whitespace must not hide unread bytes.
    base = payload(256)
    error, calls = execute(base + b" " * (LIMIT + extra - len(base)))
    assert isinstance(error, SystemExit) and error.code == 6
    assert capsys.readouterr().out == ""
    assert max(calls["reads"]) <= LIMIT + 1


@pytest.mark.parametrize("status", [302, 401, 500])
def test_http_failures_are_refused_before_body_read(status, capsys):
    error, calls = execute(payload(129326), status=status)
    assert isinstance(error, SystemExit) and error.code == 4
    assert not calls["reads"] and capsys.readouterr().out == ""


@pytest.mark.parametrize("body", [b"{", b'[]', b'{"service":"wrong"}',
                                  b'{"service":"aiport-candidate","adopted":1,"control_connected":true}'])
def test_invalid_json_or_required_flags_are_refused(body, capsys):
    error, _ = execute(body)
    assert error is not None and capsys.readouterr().out == ""


def test_wrong_pin_fails_before_transport_or_trust_setup(capsys):
    error, calls = execute(payload(256), pin="0" * 64)
    assert isinstance(error, SystemExit) and error.code == 3
    assert not calls["trust"] and not calls["connections"] and not calls["requests"]
    assert capsys.readouterr().out == ""


def test_tls_refusal_does_not_fall_back_to_unverified_http(capsys):
    error, calls = execute(payload(256), tls_error=True)
    assert isinstance(error, ssl.SSLError)
    assert not calls["connections"] and not calls["requests"]
    assert capsys.readouterr().out == ""
