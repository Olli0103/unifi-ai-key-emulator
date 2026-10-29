"""Real local slow-drip responses must not renew the provider timeout per byte."""

import asyncio
import io
import ssl

import pytest

from aikey import provider_http
from aikey.tls import ensure_identity_certificate, server_context


@pytest.mark.parametrize("method", ["read", "read1", "readinto", "readline", "peek"])
def test_buffered_response_bytes_cannot_be_returned_after_the_deadline(monkeypatch, method):
    now = [0.0]
    monkeypatch.setattr(provider_http.time, "monotonic", lambda: now[0])
    body = b"private-synthetic-result\n"
    raw = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
                     + b"\r\n\r\n" + body)

    class Socket:
        closed = False

        def settimeout(self, value):
            assert 0 < value <= 1

        def makefile(self, *args, **kwargs):
            return raw

        def close(self):
            self.closed = True

    sock = Socket()
    response = provider_http._DeadlineResponse(provider_http._DeadlineSocket(sock, 1.0))
    response.begin()                         # the remaining body is already buffered
    now[0] = 1.1
    args = (bytearray(64),) if method == "readinto" else ()
    with pytest.raises(TimeoutError, match="provider_request_unavailable") as error:
        getattr(response, method)(*args)
    assert "private-synthetic-result" not in str(error.value)
    assert response.isclosed() and raw.closed and sock.closed


def test_late_send_is_not_retried_and_request_closes_the_socket(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(provider_http.time, "monotonic", lambda: now[0])
    sends = []

    class Socket:
        closed = False

        def settimeout(self, timeout):
            assert 0 < timeout <= 1

        def sendall(self, data):
            sends.append(len(data))
            now[0] = 1.1

        def close(self):
            self.closed = True

    sock = Socket()
    connection = provider_http.ProviderHTTPConnection("127.0.0.1", timeout=1)
    connection.sock = provider_http._DeadlineSocket(sock, 1.0)
    with pytest.raises(TimeoutError, match="provider_request_unavailable"):
        connection.request("POST", "/", body=b"synthetic")
    assert len(sends) == 1 and sock.closed and connection.sock is None


@pytest.mark.parametrize("secure", [False, True])
@pytest.mark.parametrize("phase", ["headers", "body"])
async def test_slow_header_or_body_bytes_do_not_extend_the_total_request_budget(tmp_path, secure, phase):
    active = set()

    async def serve(reader, writer):
        active.add(asyncio.current_task())
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            header = b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\nConnection: close\r\n\r\n"
            if phase == "headers":
                for byte in header:
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(.01)
                writer.write(b"x" * 20)
                await writer.drain()
            else:
                writer.write(header)
                await writer.drain()
                for _ in range(20):
                    writer.write(b"x")
                    await writer.drain()
                    await asyncio.sleep(.025)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            active.discard(asyncio.current_task())

    if secure:
        ensure_identity_certificate(tmp_path, "020000000001")
    server = await asyncio.start_server(serve, "127.0.0.1", 0,
                                       ssl=server_context(tmp_path) if secure else None)
    port = server.sockets[0].getsockname()[1]

    def request():
        if secure:
            connection = provider_http.ProviderHTTPSConnection("127.0.0.1", port, timeout=.12,
                context=ssl.create_default_context(cafile=str(tmp_path / "device.crt")))
        else:
            connection = provider_http.ProviderHTTPConnection("127.0.0.1", port, timeout=.12)
        try:
            connection.request("GET", "/")
            with connection.getresponse() as response:
                return response.read()
        finally:
            connection.close()

    try:
        with pytest.raises(TimeoutError):
            await asyncio.to_thread(request)
    finally:
        server.close()
        await server.wait_closed()
        for task in tuple(active):
            task.cancel()
        await asyncio.gather(*tuple(active), return_exceptions=True)
