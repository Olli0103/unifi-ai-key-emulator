"""Revision-bound E5 document/query adapter for an explicitly local HTTP target."""
import ipaddress
from urllib.parse import urlsplit

import aiohttp

from .index_sources import _SHA
from .provider_network import ProviderConnector
from .search import EmbeddingService


def text_endpoint(value):
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or '')
        if (parsed.scheme != 'http' or not (address.is_private or address.is_loopback)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/', '/v1/embeddings')
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError
        return value.rstrip('/') if parsed.path == '/v1/embeddings' else value.rstrip('/')+'/v1/embeddings'
    except (ValueError, TypeError):
        raise ValueError('The session target must be an explicit local E5 endpoint') from None


class TextEmbedder:
    def __init__(self, endpoint, revision, *, encoding_identity=None):
        self.endpoint = text_endpoint(endpoint)
        if not isinstance(revision, str) or not _SHA.fullmatch(revision):
            raise ValueError('The target E5 checkpoint must have an exact SHA-256 revision')
        self.expected_revision = revision
        self.service = EmbeddingService({'backend': 'http', 'endpoint': self.endpoint,
            'allow_remote': True, 'allow_insecure_http': True, 'revision': revision}, expected_encoding=encoding_identity)
        self.encoding_identity = self.service.encoding_identity
        self._probe = None

    async def revision(self):
        if self._probe is None:
            self._probe = aiohttp.ClientSession(connector=ProviderConnector(), trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=8))
        parsed = urlsplit(self.endpoint)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        async with self._probe.get(origin+'/healthz', allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError('The E5 target identity is unavailable')
            raw = bytearray()
            async for block in response.content.iter_chunked(16384):
                raw.extend(block)
                if len(raw) > 65536:
                    raise ValueError('The E5 target health reply exceeds its bound')
        from .camera_inventory import _json
        value = _json(bytes(raw))
        if (not isinstance(value, dict) or value.get('embedding_revision') != self.expected_revision
                or value.get('embedding_encoding') != self.encoding_identity
                or not isinstance(value.get('models'), dict)
                or value['models'].get('embeddings') != 'multilingual-e5-small'):
            raise ValueError('The E5 target checkpoint or encoding contract differs')
        return value['embedding_revision']

    async def embed(self, text):
        return (await self.service.encode_documents([text.decode('utf-8')]))[0]

    async def embed_text(self, text):
        return (await self.service.encode_queries([text]))[0]

    async def close(self):
        await self.service.close()
        if self._probe is not None:
            await self._probe.close()
