"""Real trusted TLS transport plus hostile origin/Host/proxy/credential inputs."""

import json
import ssl

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey.admin_listener import configure_listener
from aikey.admin_security import AdminSecurity, SESSION_COOKIE_NAME
from aikey.config import initialize
from aikey.control_site import ControlSite, _COOKIE
from aikey.tls import ensure_identity_certificate
from test_control_site_secrets import PASSWORD, assert_private_page


def identity(tmp_path):
    directory = tmp_path / "admin-tls"
    directory.mkdir(mode=0o700)
    return ensure_identity_certificate(directory, "AABBCCDDEEFF")


@pytest.mark.parametrize("bind,origin,tls", [
    ("0.0.0.0", "https://admin.example:8765", True),
    ("::", "https://admin.example:8765", True),
    ("8.8.8.8", "https://admin.example:8765", True),
    ("224.0.0.1", "https://admin.example:8765", True),
    ("169.254.1.1", "https://admin.example:8765", True),
    ("::ffff:127.0.0.1", "https://admin.example:8765", True),
    ("localhost", "https://admin.example:8765", True),
    ("192.168.1.2", "http://192.168.1.2:8765", False),
    ("127.0.0.1", "https://admin.example:8765", False),
    ("127.0.0.1", "https://admin.example:8443", True),
    ("127.0.0.1", "http://127.0.0.1:8765", True),
])
def test_unsafe_or_inconsistent_listeners_fail_before_serving(tmp_path, bind, origin, tls):
    certificate, key = identity(tmp_path)
    with pytest.raises(ValueError):
        configure_listener(8765, bind, origin, certificate if tls else None, key if tls else None)


@pytest.mark.parametrize("bind", ["127.0.0.1", "::1", "10.1.2.3", "172.18.0.3",
                                  "192.168.1.2", "100.64.1.2", "fd00::1"])
def test_one_explicit_private_address_requires_and_loads_tls(tmp_path, bind):
    certificate, key = identity(tmp_path)
    listener = configure_listener(8765, bind, "https://admin.example:8765", certificate, key)
    assert listener.ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert listener.host == bind
    assert listener.loopback_only == (bind in {"127.0.0.1", "::1"})


@pytest.mark.parametrize("change", ["missing_key", "public_key", "public_certificate", "symlink_key",
                                   "symlink_certificate", "bad_pair", "directory", "fifo"])
def test_tls_material_refusals_never_replace_files(tmp_path, change):
    certificate, key = identity(tmp_path)
    before = certificate.read_bytes(), key.read_bytes()
    selected_cert, selected_key = certificate, key
    if change == "missing_key":
        selected_key = None
    elif change == "public_key":
        key.chmod(0o644)
    elif change == "public_certificate":
        certificate.chmod(0o644)
    elif change.startswith("symlink"):
        link = tmp_path / "linked-input"
        link.symlink_to(key if change == "symlink_key" else certificate)
        if change == "symlink_key":
            selected_key = link
        else:
            selected_cert = link
    elif change == "bad_pair":
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        _, selected_key = ensure_identity_certificate(other, "AABBCCDDEEFE")
    elif change == "directory":
        selected_key = key.parent
    elif change == "fifo":
        import os
        selected_key = tmp_path / "fifo"
        os.mkfifo(selected_key, mode=0o600)
    with pytest.raises((ValueError, OSError)):
        configure_listener(8765, "127.0.0.1", "https://localhost:8765", selected_cert, selected_key)
    assert (certificate.read_bytes(), key.read_bytes()) == before


def test_encrypted_key_fails_without_an_interactive_prompt(tmp_path):
    from cryptography.hazmat.primitives import serialization
    certificate, key = identity(tmp_path)
    plaintext = serialization.load_pem_private_key(key.read_bytes(), password=None)
    key.write_bytes(plaintext.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b"synthetic-passphrase")))
    before = key.read_bytes()
    with pytest.raises(ValueError, match="Encrypted administrator TLS keys"):
        configure_listener(8765, "127.0.0.1", "https://localhost:8765", certificate, key)
    assert key.read_bytes() == before


@pytest.mark.parametrize("origin", ["", "http://admin.example:8765", "https://user@admin.example:8765",
                                   "https://admin.example:8765/path", "https://admin.example:8765?query=1"])
def test_constructor_refuses_unsafe_origins_without_configuration_writes(tmp_path, origin):
    certificate, key = identity(tmp_path)
    path = tmp_path / "not-created" / "config.json"
    with pytest.raises(ValueError):
        ControlSite(path, signing_key=b"q" * 32, password_record="synthetic-unused-record", port=8765,
                    origin=origin, tls_certificate=certificate, tls_private_key=key)
    assert not path.parent.exists()


@pytest.mark.asyncio
async def test_real_https_login_secure_cookie_csrf_rollback_and_logout(tmp_path):
    certificate, key = identity(tmp_path)
    path = tmp_path / "key" / "config.json"
    initialize(path, path.parent, controller_host="127.0.0.1")
    original = path.read_bytes()
    site = ControlSite(path, signing_key=b"q" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD), port=8765,
                       origin="https://127.0.0.1:8765", tls_certificate=certificate, tls_private_key=key)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server(ssl=site.listener.ssl_context)
    site.origin = f"https://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"q" * 32, site.origin)
    trust = ssl.create_default_context(cafile=str(certificate))
    client = TestClient(server, connector=aiohttp.TCPConnector(ssl=trust),
                        cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        response = await client.get("/", allow_redirects=False)
        assert response.status == 303
        response = await client.post("/login", data={"password": PASSWORD},
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        cookie = response.cookies[SESSION_COOKIE_NAME]
        assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
        assert cookie["path"] == "/" and not cookie["domain"] and _COOKIE not in response.cookies
        cookie_value = client.session.cookie_jar.filter_cookies(URL(site.origin))[SESSION_COOKIE_NAME].value
        csrf = site.security.csrf_token(cookie_value)
        response = await client.get("/")
        assert response.status == 200
        assert_private_page(response, await response.text())
        fields = {"csrf": csrf, "profile": "aikey", "role": "caption",
                  "revision": site.aikey.snapshot().revision, "model": "synthetic-tls-caption"}
        for headers, data in [
            ({"Origin": "https://other.example"}, fields),
            ({"Origin": site.origin, "Host": "other.example"}, fields),
            ({"Origin": site.origin}, {**fields, "csrf": "forged"}),
        ]:
            refused = await client.post("/role-model", headers=headers, data=data, allow_redirects=False)
            assert refused.status == 403 and path.read_bytes() == original
        response = await client.post("/role-model", data=fields, headers={"Origin": site.origin},
                                     allow_redirects=False)
        assert response.status == 303
        assert json.loads(path.read_text())["inference"]["model"] == "synthetic-tls-caption"
        response = await client.post("/provider/rollback", data={"csrf": csrf, "profile": "aikey",
            "revision": site.aikey.snapshot().revision}, headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303 and path.read_bytes() == original
        response = await client.post("/logout", data={"csrf": csrf}, headers={"Origin": site.origin},
                                     allow_redirects=False)
        assert response.status == 303
        assert response.cookies[SESSION_COOKIE_NAME]["secure"]
        assert response.cookies[SESSION_COOKIE_NAME]["max-age"] == "0"
        response = await client.get("/", allow_redirects=False)
        assert response.status == 303
        async with aiohttp.ClientSession() as untrusted:
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                await untrusted.get(site.origin + "/login")
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_forwarded_headers_cannot_turn_plain_http_into_https(tmp_path):
    certificate, key = identity(tmp_path)
    path = tmp_path / "key" / "config.json"
    initialize(path, path.parent, controller_host="127.0.0.1")
    site = ControlSite(path, signing_key=b"q" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD), port=8765,
                       origin="https://127.0.0.1:8765", tls_certificate=certificate, tls_private_key=key)
    # Deliberately miswire a plaintext fixture instead of the production TLS listener.
    async with TestClient(TestServer(site.app())) as client:
        response = await client.post("/login", data={"password": PASSWORD}, allow_redirects=False,
            headers={"Host": "127.0.0.1:8765", "Origin": site.origin,
                     "X-Forwarded-Proto": "https", "X-Forwarded-Host": "127.0.0.1:8765",
                     "Forwarded": 'proto=https;host="127.0.0.1:8765"'})
        assert response.status == 403 and not response.cookies
