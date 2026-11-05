"""Explicit pinned native session for source export and migration readback.

Reuses the metadata reader's private credential and TLS primitives. No browser
cookie import, Integration-key substitution, redirects, retries, controls or
image upload. Only bootstrap, exact events, original crops and fixed-threshold
basic text searches are exposed. A session is never retained after this scope.
"""
from __future__ import annotations

import asyncio
import ipaddress
import time

import aiohttp

from . import camera_inventory as inventory
from .index_sources import MAX_IMAGE, _ID
from .native_camera_status import _credential


class NativeIndexError(RuntimeError):
    """Fixed error categories; never credentials, private queries or payloads."""


def _host(host):
    try:
        address = ipaddress.ip_address(host)
        if (not isinstance(address, ipaddress.IPv4Address)
                or not any(address in subnet for subnet in inventory._LOCAL_IPV4)):
            raise ValueError
        return str(address)
    except (ValueError, TypeError):
        raise NativeIndexError('Native index access requires a private IPv4 controller') from None


async def _body(response, limit):
    if response.content_length is not None and response.content_length > limit:
        raise NativeIndexError('The native index response exceeds its bounds')
    data = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise NativeIndexError('The native index response exceeds its bounds')
    return bytes(data)


class NativeIndexSession:
    def __init__(self, host, *, username_file, password_file, trust_file, cert_file,
                 mfa_method=None, token_provider=None):
        if mfa_method not in {None, "totp", "push", "email", "sms"}:
            raise NativeIndexError("Unsupported native MFA method")
        self.mfa_method, self.token_provider = mfa_method, token_provider
        self.host = _host(host)
        self.username_file, self.password_file = username_file, password_file
        self.trust_file, self.cert_file = trust_file, cert_file
        self._session = None
        self._attempted = False
        self.logout_acknowledged = False
        self._used = False
        port = inventory._INTEGRATION_PORT
        self._origin = f'https://{self.host}' + (f':{port}' if port != 443 else '')

    async def __aenter__(self):
        if self._used:
            raise NativeIndexError('A native index session is single-use')
        self._used = True
        try:
            context, pin = inventory._trusted_web(self.host, self.trust_file, self.cert_file)
            username = _credential(self.username_file, 256)
            password = _credential(self.password_file, 4096)
            self._session = aiohttp.ClientSession(
                connector=inventory.PinnedWebConnector(context, pin),
                cookie_jar=aiohttp.CookieJar(unsafe=True), trust_env=False,
                timeout=aiohttp.ClientTimeout(total=12, connect=4, sock_read=8),
                headers={'Origin': self._origin, 'Accept': 'application/json'})
            payload = {'username': username, 'password': password, 'rememberMe': False}
            status, raw = await self._login_post('/api/auth/login', payload)
            if status == 499:
                from .index_mfa import authenticate
                status = await authenticate(self, raw, payload)
            if status != 200 or not self._session.cookie_jar:
                if 400 <= status < 500 and not self._session.cookie_jar:
                    self._attempted = False
                raise NativeIndexError('Native index authentication was refused')
            async with asyncio.timeout(15):
                bootstrap = await self._json('/proxy/protect/api/bootstrap', 2*1024*1024)
                if (not isinstance(bootstrap, dict) or not isinstance(bootstrap.get('nvr'), dict)
                        or not inventory.is_supported_protect_version(bootstrap['nvr'].get('version'))):
                    raise NativeIndexError('The native index controller version is unsupported')
                self.protect_version = bootstrap['nvr']['version']
            return self
        except BaseException as exc:
            await self.__aexit__(type(exc), exc, exc.__traceback__)
            if isinstance(exc, (NativeIndexError, asyncio.CancelledError)):
                raise
            raise NativeIndexError('The native index session is unavailable') from None

    async def _login_post(self, path, payload):
        # All authentication requests share the approved pinned connector and RAM-only jar.
        self._attempted = True
        async with asyncio.timeout(15):
            async with self._session.post(self._origin+path, allow_redirects=False,
                                          json=payload) as response:
                raw = await _body(response, 262144)
                csrf = response.headers.get('X-CSRF-Token')
                if csrf:
                    if len(csrf) > 4096 or any(ord(c) < 32 for c in csrf):
                        raise NativeIndexError('The native index session is unavailable')
                    self._session.headers['X-CSRF-Token'] = csrf
                if 400 <= response.status < 500 and not self._session.cookie_jar:
                    self._attempted = False
                return response.status, raw

    async def __aexit__(self, *args):
        session, self._session = self._session, None
        if session is None:
            return
        try:
            if self._attempted:
                deadline = time.monotonic() + 3
                async with asyncio.timeout(3):
                    async with session.post(self._origin+'/api/auth/logout', allow_redirects=False,
                            timeout=aiohttp.ClientTimeout(total=3)) as response:
                        await _body(response, 4096)
                        if response.status not in {200, 204} or time.monotonic() >= deadline:
                            raise NativeIndexError('Native index session cleanup is unconfirmed')
                        self.logout_acknowledged = True
        except (aiohttp.ClientError, OSError, TimeoutError, NativeIndexError):
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError from None
            raise NativeIndexError('Native index session cleanup is unconfirmed') from None
        finally:
            session.cookie_jar.clear()
            await session.close()

    async def _get(self, path, limit, content_type, *, missing=False, params=None):
        if self._session is None:
            raise NativeIndexError('The native index session is closed')
        try:
            async with self._session.get(self._origin+path, params=params,
                                         allow_redirects=False) as response:
                if response.status in {401, 403}:
                    raise NativeIndexError('Native index authentication was refused')
                if response.status == 404 and missing:
                    await _body(response, 65536)
                    return None
                if response.status != 200 or response.content_type != content_type:
                    raise NativeIndexError('The native index response is unavailable or invalid')
                return await _body(response, limit)
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise NativeIndexError('The native index transport is unavailable') from None

    async def _json(self, path, limit, *, missing=False, params=None):
        raw = await self._get(path, limit, 'application/json', missing=missing, params=params)
        if raw is None:
            return None
        try:
            return inventory._json(raw)
        except (ValueError, inventory.InventoryError):
            raise NativeIndexError('The native index JSON is invalid') from None

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise NativeIndexError('The native index object reference is invalid')
        return value

    async def event(self, event_id):
        return await self._json('/proxy/protect/api/events/'+self._id(event_id),
                                2*1024*1024, missing=True)

    async def crop(self, crop_id):
        # No enhanced or transformed-image route can be constructed here.
        return await self._get('/proxy/protect/api/thumbnails/'+self._id(crop_id),
                               MAX_IMAGE, 'image/jpeg', missing=True)

    async def source_object(self, row, event):
        from .index_source_lookup import lookup_source_object
        return await lookup_source_object(self, row, event)

    async def source_detections(self, term, *, start, end, camera_id, offset, limit):
        from .index_source_lookup import MAX_PAGES, PAGE_SIZE, TERMS
        if (term not in TERMS or any(type(x) is not int for x in (start, end, offset, limit))
                or not 0 <= start <= end <= 2**53-1 or end-start > 86520000
                or limit != PAGE_SIZE or offset not in range(0, MAX_PAGES*PAGE_SIZE, PAGE_SIZE)):
            raise NativeIndexError('The source lookup admission is invalid')
        return await self._json('/proxy/protect/api/detection-nls', 8*1024*1024,
            params={'searchText': term, 'minSimilarity': '0', 'start': str(start),
                    'end': str(end), 'cameras': self._id(camera_id), 'offset': str(offset),
                    'limit': str(limit), 'ignoreTimeFilterFromText': 'true'})

    async def search(self, text, *, min_similarity, camera_id=None, start_ms=None, end_ms=None):
        if (not isinstance(text, str) or not text.strip() or len(text) > 1024
                or any(ord(c) < 32 for c in text)
                or type(min_similarity) is not int or not 1 <= min_similarity <= 100):
            raise NativeIndexError('The fixed-threshold native search admission is invalid')
        if ((camera_id is not None and (not isinstance(camera_id, str) or not _ID.fullmatch(camera_id)))
                or (start_ms is None) != (end_ms is None)
                or any(x is not None and (type(x) is not int or not 0 <= x <= 2**53-1)
                       for x in (start_ms, end_ms))
                or (start_ms is not None and start_ms > end_ms)):
            raise NativeIndexError('The fixed native search scope is invalid')
        params = {'searchText': text, 'minSimilarity': str(min_similarity)}
        if camera_id is not None:
            params['cameras'] = camera_id
        if start_ms is not None:
            params.update(start=str(start_ms), end=str(end_ms), ignoreTimeFilterFromText='true')
        response = await self._json('/proxy/protect/api/detection-nls', 2*1024*1024, params=params)
        if not isinstance(response, dict):
            raise NativeIndexError('The native search response is invalid')
        # Protect 7.3.74 echoes explicit time bounds but omits explicit cameras
        # from searchCondition. Record our actual pinned request, not a forged
        # server acknowledgment; readback still checks every returned camera.
        if camera_id is not None:
            response['_aikey_request_scope'] = {'camera_id': camera_id,
                'start_ms': start_ms, 'end_ms': end_ms}
        else:
            response.pop('_aikey_request_scope', None)
        return response


    async def search_sessions(self, text):
        if (not isinstance(text, str) or not text.strip() or len(text) > 1024
                or any(ord(c) < 32 for c in text)):
            raise NativeIndexError('The native session-search admission is invalid')
        return await self._json('/proxy/protect/api/detection-sessions/search', 2*1024*1024,
                               params={'query': text})
