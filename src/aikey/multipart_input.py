"""Bound recognized local model-upload parts without changing their wire bytes."""

from aiohttp import BodyPartReader


class MultipartInputError(ValueError):
    """The recognized part exceeds its limit or uses an unsupported encoding."""


async def read_part(part: BodyPartReader, limit: int) -> bytes:
    # Request.client_max_size does not account for streamed multipart reads.
    # Keep the existing wire bytes; never truncate, decode or re-encode an image.
    if (part.headers.get("Content-Transfer-Encoding", "binary").lower()
            not in {"binary", "8bit"}
            or part.headers.get("Content-Encoding", "identity").lower() != "identity"):
        raise MultipartInputError("Encoded parts unsupported")
    value = bytearray()
    while chunk := await part.read_chunk(8192):
        if len(value) + len(chunk) > limit:
            raise MultipartInputError("Part too large")
        value.extend(chunk)
    return bytes(value)
