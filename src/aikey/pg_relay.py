"""Source-filtering TCP relay for the AI Key's PostgreSQL search host on macOS.

Protect connects to PostgreSQL at the AI Key's own address, port 5432 (#2).
Apple ``container`` port publishing rewrites every peer to the bridge
address, so PostgreSQL's HBA cannot tell the console from any other LAN host.
This relay listens on the AI Key address, admits only explicitly allowed
source networks (the console /32, and the host-only container bridge for
the Key's own credential rotation), and splices bytes to PostgreSQL
published only on the host bridge. TLS and SCRAM stay end to end between
the peer and PostgreSQL; the relay never reads, logs or stores traffic.

It has no dependencies beyond the standard library, so it runs under the
system's signed ``/usr/bin/python3`` (3.9+), which the macOS application
firewall admits as signed software. It writes only counters to a status
file. A NAS deployment does not need it: macvlan keeps real peer addresses.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import signal
import tempfile
import time

_COUNTER_LIMIT = 10 ** 9
_CHUNK = 65536


class Relay:
    def __init__(self, listen, target, allowed, *, max_connections=32, status_path=None,
                 connect_timeout=5.0):
        self.listen = listen
        self.target = target
        self.allowed = [ipaddress.ip_network(value, strict=True) for value in allowed]
        if not self.allowed or any(network.num_addresses > 256 for network in self.allowed):
            raise ValueError("Allow at least one network, each no wider than /24")
        self.max_connections = max_connections
        self.status_path = status_path
        self.connect_timeout = connect_timeout
        self.active = 0
        self.counters = {"accepted": 0, "refused_source": 0, "refused_capacity": 0,
                         "upstream_failures": 0, "closed": 0}
        self.started_at = int(time.time())

    def _count(self, key):
        self.counters[key] = min(self.counters[key] + 1, _COUNTER_LIMIT)

    def admits(self, host):
        try:
            address = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return False
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        return any(address in network for network in self.allowed)

    def status(self):
        return {"schema": 1, "listen": "%s:%d" % self.listen, "target": "%s:%d" % self.target,
                "allowed": [str(network) for network in self.allowed], "active": self.active,
                "max_connections": self.max_connections, "started_at": self.started_at,
                "updated_at": int(time.time()), **self.counters}

    def write_status(self):
        if not self.status_path:
            return
        directory = os.path.dirname(os.path.abspath(self.status_path))
        descriptor, temporary = tempfile.mkstemp(prefix=".relay-status-", dir=directory)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(self.status(), output, indent=1)
                output.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.status_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def _pipe(self, reader, writer):
        try:
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                if writer.can_write_eof():
                    writer.write_eof()
            except (OSError, RuntimeError):
                pass

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("", 0)
        if not self.admits(str(peer[0])):
            self._count("refused_source")
            writer.close()
            return
        if self.active >= self.max_connections:
            self._count("refused_capacity")
            writer.close()
            return
        self.active += 1
        self._count("accepted")
        upstream_writer = None
        try:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(*self.target), self.connect_timeout)
            except (OSError, asyncio.TimeoutError):
                self._count("upstream_failures")
                return
            await asyncio.gather(self._pipe(reader, upstream_writer),
                                 self._pipe(upstream_reader, writer))
        finally:
            self.active -= 1
            self._count("closed")
            for stream in (writer, upstream_writer):
                if stream is not None:
                    stream.close()

    async def serve(self, stop):
        server = await asyncio.start_server(self.handle, *self.listen, reuse_address=True)
        try:
            while not stop.is_set():
                self.write_status()
                try:
                    await asyncio.wait_for(stop.wait(), 10)
                except asyncio.TimeoutError:
                    pass
        finally:
            server.close()
            await server.wait_closed()
            self.write_status()


def _endpoint(value):
    host, _, port = value.rpartition(":")
    ipaddress.ip_address(host)
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("port")
    return host, port


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aikey-pg-relay")
    parser.add_argument("--listen", required=True, help="AI Key address and port, e.g. 192.168.0.98:5432")
    parser.add_argument("--target", required=True, help="host-only PostgreSQL, e.g. 192.168.64.1:55432")
    parser.add_argument("--allow", action="append", required=True,
                        help="allowed source network; repeat (console /32, container bridge /24)")
    parser.add_argument("--max-connections", type=int, default=32)
    parser.add_argument("--status", help="private JSON file for counters")
    args = parser.parse_args(argv)
    relay = Relay(_endpoint(args.listen), _endpoint(args.target), args.allow,
                  max_connections=args.max_connections, status_path=args.status)

    async def run():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        await relay.serve(stop)
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
