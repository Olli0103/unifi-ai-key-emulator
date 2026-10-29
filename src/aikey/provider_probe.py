"""One explicitly requested synthetic vision API probe; never camera data/activation."""

from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import secrets
import time

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from .protocol import _json_object
from .provider_network import ProviderAddressError, ProviderConnector

TOTAL_SECONDS = 20
MAX_REPLY_BYTES = 64 * 1024
MAX_PENDING = 64
GRANT_SECONDS = 120
COOLDOWN_SECONDS = 30


class ProbeError(ValueError):
    """Fixed probe failure without provider text, keys, endpoints or media."""


class ProbeGrants:
    """Ephemeral one-use form grants. Restart invalidates all outstanding forms."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.pending = {}
        self.busy = False
        self.next_at = 0.0

    @staticmethod
    def _context(profile, role, revision, session):
        return profile, role, revision, hashlib.sha256(session.encode()).hexdigest()

    def issue(self, profile, role, revision, session) -> str | None:
        now = self.clock()
        self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
        if len(self.pending) >= MAX_PENDING:
            return None
        token = secrets.token_urlsafe(24)
        self.pending[token] = (now + GRANT_SECONDS, self._context(profile, role, revision, session))
        return token

    def begin(self, token, profile, role, revision, session):
        now = self.clock()
        if self.busy or now < self.next_at:
            raise ProbeError("probe_busy")
        if not isinstance(token, str) or len(token) != 32:
            raise ProbeError("probe_grant_invalid")
        grant = self.pending.get(token)
        if (grant is None or grant[0] <= now
                or grant[1] != self._context(profile, role, revision, session)):
            raise ProbeError("probe_grant_invalid")
        del self.pending[token]  # Consume before credential read or any request.
        self.busy = True
        self.next_at = now + COOLDOWN_SECONDS

    def finish(self):
        self.busy = False


def _challenge() -> tuple[bytes, str]:
    answer = secrets.token_hex(4).upper()
    picture = Image.new("RGB", (320, 96), "white")
    draw = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=44)
    draw.text((20, 18), answer, fill="black", font=font)
    output = BytesIO()
    picture.save(output, format="PNG")
    return output.getvalue(), answer


async def probe_vision(provider) -> dict:
    """One small synthetic image, no retry/fallback and no returned model text.

    Matching this probe tests a single API path, not internal model identity,
    general modality, recognition quality, deployment or provider billing.
    """
    started = time.monotonic()
    deadline = started + TOTAL_SECONDS
    attempted = False
    try:
        async with asyncio.timeout(TOTAL_SECONDS):
            image, answer = _challenge()
            provider.max_output_tokens = 128  # A local adapter copy, never saved settings.
            url, headers, payload = provider.build_request([image],
                "Read the eight uppercase hexadecimal characters in the image. "
                "Return only those eight characters.")
            async with aiohttp.ClientSession(connector=ProviderConnector(), trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                    headers={"Accept-Encoding": "identity"},
                    timeout=aiohttp.ClientTimeout(total=TOTAL_SECONDS, connect=3)) as session:
                attempted = True
                async with session.post(url, headers=headers, json=payload,
                                        allow_redirects=False) as response:
                    if (response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity"
                            or not (response.content_type == "application/json"
                                    or response.content_type.endswith("+json"))):
                        raise ProbeError("probe_unavailable")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        body.extend(chunk)
                        if len(body) > MAX_REPLY_BYTES:
                            raise ProbeError("probe_unavailable")
                    reply = provider.parse_response(_json_object(bytes(body)))
            finished = time.monotonic()
            if not started <= finished < deadline:
                raise ProbeError("probe_unavailable")
            return {"synthetic_image_matched": reply == answer,
                    "elapsed_ms": round((finished - started) * 1000),
                    "model_identity_verified": False, "quality_verified": False}
    except ProbeError:
        raise
    except ProviderAddressError:
        raise ProbeError("probe_not_started") from None
    except Exception:
        # A request may already have been charged. Do not return provider
        # exceptions, automatically retry, or promise a no-charge outcome.
        raise ProbeError("probe_unavailable" if attempted else "probe_not_started") from None
