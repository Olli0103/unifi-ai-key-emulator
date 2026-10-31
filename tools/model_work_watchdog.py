"""Fence an existing, hash-pinned local watchdog; no new inference probe policy.

Only enrolled, image-pinned Key/live-pool consumers are covered. The private
operator configuration and shared inode must be provisioned separately. An
uncertain run leaves maintenance intent in place, requiring reconciliation.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from aikey.model_work_fence import ModelWorkFence, ModelWorkFenceError, SCHEMA

CONFIG_SCHEMA = "local-aikey-model-watchdog/1"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class WatchdogError(RuntimeError):
    """Fixed local error; no subprocess, URL, configuration or model payload."""


def _path(value):
    if (not isinstance(value, str) or not Path(value).is_absolute()
            or ".." in Path(value).parts or any(ord(c) < 32 for c in value)):
        raise WatchdogError("watchdog_configuration_refused")
    return Path(value)


def _private_snapshot(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        meta = os.fstat(source.fileno())
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                or meta.st_mode & 0o077 or not 0 < meta.st_size <= limit):
            raise WatchdogError("watchdog_private_file_refused")
        raw = source.read(limit + 1)
    if not raw or len(raw) > limit:
        raise WatchdogError("watchdog_private_file_refused")
    return raw


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def load(path):
    try:
        value = json.loads(_private_snapshot(path, 32768), object_pairs_hook=_unique)
        fields = {"schema", "state", "legacy_script", "legacy_sha256", "provider",
                  "participants", "ready_client"}
        if (not isinstance(value, dict) or set(value) != fields
                or value["schema"] != CONFIG_SCHEMA
                or not isinstance(value["legacy_sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", value["legacy_sha256"])):
            raise ValueError
        _path(value["state"])
        legacy = _private_snapshot(_path(value["legacy_script"]), 65536)
        if hashlib.sha256(legacy).hexdigest() != value["legacy_sha256"]:
            raise ValueError
        rows = value["participants"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
            raise ValueError
        if len({row["container"] for row in rows}) != len(rows):
            raise ValueError
        for row in rows:
            if (set(row) != {"container", "image", "kind", "state"}
                    or row["kind"] not in {"key", "port"}
                    or not _NAME.fullmatch(row["container"])
                    or not _IMAGE.fullmatch(row["image"])):
                raise ValueError
            _path(row["state"])
        for field in ("provider", "ready_client"):
            row = value[field]
            expected = {"container", "image", "url", "model"} if field == "provider" else {"container", "image"}
            if (set(row) != expected or not _NAME.fullmatch(row["container"])
                    or not _IMAGE.fullmatch(row["image"])):
                raise ValueError
        provider = value["provider"]
        url = urlsplit(provider["url"])
        ip = ipaddress.ip_address(url.hostname)
        if (url.scheme != "http" or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path != "/v1/models"
                or not ip.is_private or ip.is_unspecified or ip.is_multicast
                or not 1 <= (url.port or 80) <= 65535
                or not isinstance(provider["model"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", provider["model"])):
            raise ValueError
        return value, legacy
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise WatchdogError("watchdog_configuration_refused") from None


def _docker(*args, code=None, timeout=10):
    result = subprocess.run(["docker", *args], input=code, capture_output=True,
                            timeout=timeout, env={"PATH": "/usr/local/bin:/usr/bin:/bin",
                                                 "LANG": "C.UTF-8"})
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise WatchdogError("watchdog_container_read_refused")
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError, RecursionError):
        raise WatchdogError("watchdog_container_read_refused") from None


def _inspect(row, *, timeout=10):
    values = _docker("inspect", row["container"], timeout=timeout)
    if (not isinstance(values, list) or len(values) != 1
            or not isinstance(values[0], dict)
            or values[0].get("Image") != row["image"]):
        raise WatchdogError("watchdog_container_binding_changed")
    return values[0]


def verify(config, fence):
    provider, client = _inspect(config["provider"]), _inspect(config["ready_client"])
    if not client["State"]["Running"]:
        raise WatchdogError("watchdog_probe_client_unavailable")
    ip = urlsplit(config["provider"]["url"]).hostname
    if ip not in {v.get("IPAddress") for v in provider["NetworkSettings"]["Networks"].values()}:
        raise WatchdogError("watchdog_provider_address_changed")
    waiting = False
    for row in config["participants"]:
        current = _inspect(row)
        if not current["State"]["Running"]:
            raise WatchdogError("watchdog_consumer_unavailable")
        mounts = [m for m in current["Mounts"] if m["Destination"] == "/state"]
        if len(mounts) != 1 or mounts[0]["Source"] != row["state"]:
            raise WatchdogError("watchdog_consumer_state_changed")
        peer = ModelWorkFence(Path(row["state"]), enabled=True)
        try:
            if peer.status()["binding"] != fence.status()["binding"]:
                raise WatchdogError("watchdog_consumer_inode_changed")
        finally:
            peer.close()
        # No controller/model request or private payload; verified local TLS.
        code = b'''from pathlib import Path
import http.client,json,socket,ssl
from cryptography import x509
from cryptography.x509.oid import ExtensionOID
kind=KIND
port=8080 if kind=='key' else 443
host='127.0.0.1'
if kind=='port':
 cert=x509.load_pem_x509_certificate(Path('/state/device.crt').read_bytes())
 ips=[str(x) for x in cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(x509.IPAddress)]
 if host not in ips:host=json.loads(Path('/state/config.json').read_bytes())['device_ip']
c=http.client.HTTPSConnection(host,port,timeout=3,context=ssl.create_default_context(cafile='/state/device.crt'))
c._create_connection=lambda a,t,source_address=None:socket.create_connection(('127.0.0.1',port),t)
c.request('GET','/healthz');r=c.getresponse();assert r.status==200
raw=r.read(1048577);assert len(raw)<=1048576
value=json.loads(raw);c.close()
fence=(value.get('worker') or {}).get('model_work_fence') if kind=='key' else value.get('model_work_fence')
print(json.dumps(fence))
'''.replace(b"KIND", repr(row["kind"]).encode("ascii"))
        observed = _docker("exec", "-i", current["Id"], "python3", "-", code=code, timeout=5)
        if (not isinstance(observed, dict) or observed.get("schema") != SCHEMA
                or observed.get("enabled") is not True or observed.get("closed") is not False
                or observed.get("binding") != fence.status()["binding"]
                or observed.get("maintenance") != "writer_active"
                or type(observed.get("leases")) is not int or observed["leases"] != 0
                or type(observed.get("waiters")) is not int or observed["waiters"] < 0):
            raise WatchdogError("watchdog_consumer_fence_unverified")
        waiting |= observed["waiters"] > 0
        # A stop/recreate during verification invalidates the observation.
        after = _inspect(row)
        if (after["Id"], after["State"]["Pid"]) != (current["Id"], current["State"]["Pid"]):
            raise WatchdogError("watchdog_consumer_process_changed")
    return provider, waiting


def run_legacy(snapshot, timeout=90):
    # Execute an anonymous snapshot, not a path reopened after verification.
    # No source/credential value is put in argv or subprocess stdin.
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        source.write(snapshot)
        source.flush()
        source.seek(0)
        fd_path = ("/proc/self/fd/" if Path("/proc/self/fd").is_dir() else "/dev/fd/") + str(source.fileno())
        child = subprocess.Popen(["/bin/bash", fd_path], stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=err, pass_fds=(source.fileno(),),
                                 start_new_session=True,
                                 env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"})
        try:
            child.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=2)
            raise
        if child.returncode or out.tell() > 65536 or err.tell() > 65536:
            raise WatchdogError("watchdog_legacy_execution_failed")


def ready(config, before, timeout=90):
    deadline = time.monotonic() + timeout
    code = b'''import json,urllib.parse,urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def read(url):
 with client.open(url,timeout=3) as response:
  assert response.status==200
  raw=response.read(131073);assert len(raw)<=131072
  return raw
value=json.loads(read(URL))
read(URL.rsplit('/v1/models',1)[0]+'/v2/models/'+urllib.parse.quote(MODEL,safe='')+'/ready')
print(json.dumps({'ready':isinstance(value.get('data'),list) and any(isinstance(x,dict) and x.get('id')==MODEL for x in value['data'])}))
'''.replace(b"URL", repr(config["provider"]["url"]).encode("ascii")).replace(
            b"MODEL", repr(config["provider"]["model"]).encode("ascii"))
    while time.monotonic() < deadline:
        current = _inspect(config["provider"], timeout=min(10, max(0.001, deadline-time.monotonic())))
        if current["Id"] != before["Id"]:
            raise WatchdogError("watchdog_provider_process_replaced")
        if urlsplit(config["provider"]["url"]).hostname not in {
                row.get("IPAddress") for row in current["NetworkSettings"]["Networks"].values()}:
            raise WatchdogError("watchdog_provider_address_changed")
        if current["State"]["Running"]:
            try:
                client = _inspect(config["ready_client"], timeout=min(
                    5, max(0.001, deadline-time.monotonic())))
                result = _docker("exec", "-i", client["Id"], "python3", "-", code=code,
                                 timeout=min(8, max(0.001, deadline-time.monotonic())))
                if result == {"ready": True} and time.monotonic() < deadline:
                    return
            except WatchdogError:
                pass
        time.sleep(0.25)
    raise WatchdogError("watchdog_provider_readiness_unverified")


def execute(config, snapshot, fence, *, verify_consumers=verify, execute_legacy=run_legacy,
            verify_ready=ready):
    writer = fence.try_exclusive()
    if writer is None:
        return {"status": "busy", "legacy_invoked": False}
    try:
        provider, waiting = verify_consumers(config, fence)
        if waiting:
            return {"status": "consumer_waiting", "legacy_invoked": False}
        transaction = uuid.uuid4().hex
        writer.begin(transaction)
        execute_legacy(snapshot)
        verify_ready(config, provider)
        writer.finish(transaction)
        return {"status": "completed", "legacy_invoked": True,
                "participant_count": len(config["participants"])}
    finally:
        # Never clear intent here. Cancellation, failure or owner loss stays
        # blocked even after the kernel releases this writer's lock.
        writer.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    fence = None
    try:
        config, snapshot = load(args.config)
        fence = ModelWorkFence(Path(config["state"]), enabled=True)
        print(json.dumps(execute(config, snapshot, fence)))
        return 0
    except (WatchdogError, ModelWorkFenceError, OSError, ValueError, TypeError, KeyError,
            AttributeError, subprocess.SubprocessError):
        print(json.dumps({"status": "refused_or_unresolved", "automatic_retry_authorized": False}))
        return 1
    finally:
        if fence is not None:
            fence.close()


if __name__ == "__main__":
    raise SystemExit(main())
