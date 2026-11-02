"""Explicit, loopback-only structured VLM smoke check; no service management.

Run only inside an already isolated candidate's network namespace. This tool
cannot establish isolation, a cold start, internal model identity or quality.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from io import BytesIO
import ipaddress
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import aiohttp
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from aikey.protocol import ContractError, _json_object  # noqa: E402
from aikey.provider_network import ProviderConnector  # noqa: E402
from aikey.providers import ProviderError, VisionProvider  # noqa: E402

ATTEMPTS = 3
TIMEOUT_SECONDS = 100
MAX_REPLY_BYTES = 64 * 1024
EXPECTED = {"first_color": "red", "second_color": "blue"}


class CheckError(ValueError):
    """Fixed diagnostics; never expose provider content or exception messages."""


def make_provider(base_url: str, model: str) -> VisionProvider:
    # No DNS, remote/public destination, URL credential, API key or saved profile.
    try:
        provider = VisionProvider({"provider": "openai-compatible", "model": model,
                                   "base_url": base_url, "max_output_tokens": 128})
        url = urlsplit(provider.base_url)
        address = ipaddress.ip_address(url.hostname)
        if not address.is_loopback or url.scheme != "http":
            raise ValueError
    except (ProviderError, ValueError, TypeError):
        raise CheckError("explicit_http_loopback_required") from None
    return provider


def make_request(provider: VisionProvider) -> dict:
    images = []
    for color in ((255, 0, 0), (0, 0, 255)):
        output = BytesIO()
        Image.new("RGB", (512, 512), color).save(output, format="PNG")
        images.append(output.getvalue())
    color_schema = {"type": "string", "enum": ["red", "blue", "green", "white", "black", "yellow"]}
    schema = {"type": "object", "properties": {key: color_schema for key in EXPECTED},
              "required": list(EXPECTED), "additionalProperties": False}
    # The same adapter as Worker._describe: system role, images before text,
    # session_description JSON schema. The expected order is not in the prompt.
    _, _, payload = provider.build_structured_request(images,
        "Describe only what is visible in the images.",
        "Identify the color of image 1 and image 2. Return first_color and second_color in JSON.",
        schema, {"temperature": 0}, max_tokens=128)
    return payload


def match_response(provider: VisionProvider, raw: bytes) -> bool:
    try:
        text = provider.parse_response(_json_object(raw))
        return _json_object(text.encode("utf-8")) == EXPECTED
    except (ContractError, ProviderError, UnicodeError):
        raise CheckError("response_invalid") from None


def _bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _sync_directory(root: Path):
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _save(root: Path, name: str, raw: bytes):
    # New private root only, no resuming/overwriting an uncertain model request.
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    _sync_directory(root)


async def run_check(base_url: str, model: str, output: Path, *, acknowledge_load=False) -> dict:
    if acknowledge_load is not True:
        raise CheckError("explicit_load_acknowledgement_required")
    provider = make_provider(base_url, model)
    request = _bytes(make_request(provider))
    output = Path(output)
    try:
        output.mkdir(mode=0o700)  # Existing/linked roots refuse before any contact.
    except FileExistsError:
        raise CheckError("new_output_directory_required") from None
    _sync_directory(output.parent)
    digest = hashlib.sha256(request).hexdigest()
    report = {"schema": "aikey-caption-backend-check/1", "request_sha256": digest,
              "planned_requests": ATTEMPTS, "started_requests": 0, "results": [],
              "passed": False, "cold_start_verified": False, "quality_verified": False,
              "model_identity_verified": False, "native_verified": False,
              "provider_charge_usd": None, "status": "incomplete"}
    _save(output, "request.json", request)
    _save(output, "started.json", _bytes(report))
    try:
        async with aiohttp.ClientSession(connector=ProviderConnector(force_close=True),
                trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                headers={"Accept-Encoding": "identity", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS, connect=3)) as session:
            for number in range(1, ATTEMPTS + 1):
                row = {"number": number, "status": "incomplete", "http_status": None,
                       "request_sha256": digest, "response_sha256": None,
                       "response_truncated": False}
                # Persist uncertain admission *before* entering the HTTP client.
                _save(output, f"admitted-{number}.json", _bytes(row))
                report["started_requests"] += 1
                raw = bytearray()
                started = time.monotonic()
                try:
                    async with asyncio.timeout(TIMEOUT_SECONDS):
                        async with session.post(provider.url, data=request,
                                                allow_redirects=False) as response:
                            row["http_status"] = response.status
                            async for chunk in response.content.iter_chunked(8192):
                                raw.extend(chunk[:MAX_REPLY_BYTES + 1 - len(raw)])
                                if len(raw) > MAX_REPLY_BYTES:
                                    row["response_truncated"] = True
                                    break
                            usable = (response.status == 200 and not row["response_truncated"]
                                and response.headers.get("Content-Encoding", "identity") == "identity"
                                and (response.content_type == "application/json"
                                     or response.content_type.endswith("+json")))
                    if time.monotonic() - started >= TIMEOUT_SECONDS:
                        raise TimeoutError
                    # Raw bytes are committed in finally before parsing/gating.
                    row["status"] = "received" if usable else "response_unusable"
                except TimeoutError:
                    row["status"] = "timeout"
                except aiohttp.ClientError:
                    row["status"] = "transport_error"
                finally:
                    _save(output, f"response-{number}.bin", bytes(raw))
                    row["response_sha256"] = hashlib.sha256(raw).hexdigest()
                    row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                    report["results"].append(row)
                if row["status"] == "received":
                    try:
                        row["status"] = "matched" if match_response(provider, bytes(raw)) else "answer_mismatch"
                    except CheckError:
                        row["status"] = "response_invalid"
                _save(output, f"result-{number}.json", _bytes(row))
                if row["status"] != "matched":
                    report["status"] = "failed"
                    break
            else:
                report["passed"] = True
                report["status"] = "matched"
    except asyncio.CancelledError:
        report["status"] = "cancelled"
        raise
    finally:
        _save(output, "report.json", _bytes(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Explicit HTTP numeric loopback candidate base URL")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True, type=Path, help="New private directory; never reused")
    parser.add_argument("--acknowledge-load", action="store_true",
                        help="Authorize at most three synthetic requests to the already isolated candidate")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_check(args.base_url, args.model, args.output,
                                      acknowledge_load=args.acknowledge_load))
    except (CheckError, OSError):
        parser.exit(2, "Check refused or receipt write failed; inspect the output before any new attempt.\n")
    print(json.dumps({key: report[key] for key in ("status", "passed", "started_requests")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
