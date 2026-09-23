"""Short-lived TLS byte relay for one controller-to-candidate management trial.

The relay never terminates TLS or reads HTTP. It binds only one explicit host
address and accepts only the specified controller address. Root is needed to
bind host port 443 on macOS; the process drops to the supplied UID/GID after
the socket is open and exits after the fixed deadline.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import os
import signal
from typing import Awaitable, Callable


_MAX_BYTES = 1024 * 1024
_IDLE_SECONDS = 30


class RelayError(ValueError):
    """The relay cannot safely bind or forward the requested endpoint."""


def _ipv4(value: str, *, allow_loopback: bool = False) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise RelayError("An explicit IPv4 address is required") from exc
    if not address.is_private or address.is_multicast or address.is_unspecified:
        raise RelayError("A private unicast IPv4 address is required")
    if address.is_loopback and not allow_loopback:
        raise RelayError("A LAN IPv4 address is required")
    return str(address)


class BoundedRelay:
    def __init__(self, *, listen_ip: str, listen_port: int,
                 upstream_port: int, allowed_source_ip: str,
                 allow_loopback: bool = False,
                 upstream_check: Callable[[], Awaitable[None]] | None = None):
        self.listen_ip = _ipv4(listen_ip, allow_loopback=allow_loopback)
        self.allowed_source_ip = _ipv4(allowed_source_ip, allow_loopback=allow_loopback)
        if not (0 <= listen_port <= 65535 and 1 <= upstream_port <= 65535):
            raise RelayError("Invalid relay port")
        if listen_port == upstream_port:
            raise RelayError("Relay and upstream ports must differ")
        self.listen_port = listen_port
        self.upstream_port = upstream_port
        self.upstream_check = upstream_check
        self.server: asyncio.AbstractServer | None = None
        self.accepted = 0
        self.rejected = 0
        self.active: set[asyncio.Task] = set()

    async def start(self):
        if self.server is not None:
            return
        self.server = await asyncio.start_server(self._accept, self.listen_ip,
                                                  self.listen_port, backlog=8)
        self.listen_port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for task in list(self.active):
            task.cancel()
        if self.active:
            await asyncio.gather(*self.active, return_exceptions=True)

    async def _accept(self, downstream_reader: asyncio.StreamReader,
                      downstream_writer: asyncio.StreamWriter):
        peer = downstream_writer.get_extra_info("peername")
        if not isinstance(peer, tuple) or peer[0] != self.allowed_source_ip:
            self.rejected += 1
            downstream_writer.close()
            await downstream_writer.wait_closed()
            return
        self.accepted += 1
        task = asyncio.current_task()
        self.active.add(task)
        upstream_writer = None
        try:
            if self.upstream_check is not None:
                await self.upstream_check()
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(self.listen_ip, self.upstream_port), 5)
            directions = [asyncio.create_task(self._pipe(downstream_reader, upstream_writer)),
                          asyncio.create_task(self._pipe(upstream_reader, downstream_writer))]
            done, pending = await asyncio.wait(directions, return_when=asyncio.FIRST_COMPLETED)
            for direction in pending:
                direction.cancel()
            await asyncio.gather(*directions, return_exceptions=True)
        except (OSError, TimeoutError, ValueError):
            pass
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                with contextlib.suppress(OSError):
                    await upstream_writer.wait_closed()
            downstream_writer.close()
            with contextlib.suppress(OSError):
                await downstream_writer.wait_closed()
            self.active.discard(task)

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        transferred = 0
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=_IDLE_SECONDS)
            if not chunk:
                return
            transferred += len(chunk)
            if transferred > _MAX_BYTES:
                return
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), timeout=_IDLE_SECONDS)


async def _run(args):
    relay = BoundedRelay(listen_ip=args.listen_ip, listen_port=443,
                         upstream_port=8443, allowed_source_ip=args.controller_ip)
    await relay.start()
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(args.drop_gid)
        os.setuid(args.drop_uid)
    elif os.geteuid() != args.drop_uid or os.getegid() != args.drop_gid:
        await relay.stop()
        raise RelayError("Relay must run as root for initial bind or as the requested non-root identity")
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, done.set)
    print("AI Port management relay active for at most 600 seconds", flush=True)
    try:
        await asyncio.wait_for(done.wait(), timeout=600)
    except TimeoutError:
        pass
    finally:
        await relay.stop()
        print(f"AI Port management relay stopped; accepted={relay.accepted} rejected={relay.rejected}",
              flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded, source-restricted AI Port TLS pass-through")
    parser.add_argument("--listen-ip", required=True)
    parser.add_argument("--controller-ip", required=True)
    parser.add_argument("--drop-uid", required=True, type=int)
    parser.add_argument("--drop-gid", required=True, type=int)
    args = parser.parse_args(argv)
    if args.drop_uid <= 0 or args.drop_gid < 0:
        parser.error("The relay must drop to a non-root UID")
    try:
        asyncio.run(_run(args))
    except (OSError, RelayError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
