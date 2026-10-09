"""Bounded document reads from HTTPS or S3; credentials stay in the worker environment."""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

import httpx

from vitruvio.kernel import EvidenceRefusedError, SourceError, UsageError

DEFAULT_MAX_BYTES = 50 * 1024 * 1024


def max_document_bytes(configured: int | None) -> int:
    deployment = int(os.environ.get("VITRUVIO_HTTP_MAX_DOCUMENT_BYTES", DEFAULT_MAX_BYTES))
    return min(configured, deployment) if configured else deployment


async def read_document(uri: str, max_bytes: int) -> bytes:
    parsed = urlsplit(uri)
    if parsed.scheme == "https":
        try:
            async with (
                httpx.AsyncClient(timeout=60, follow_redirects=False) as client,
                client.stream("GET", uri) as response,
            ):
                response.raise_for_status()
                if int(response.headers.get("content-length", "0")) > max_bytes:
                    raise EvidenceRefusedError(f"document exceeds the {max_bytes} byte limit")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise EvidenceRefusedError(f"document exceeds the {max_bytes} byte limit")
                return bytes(body)
        except httpx.HTTPError as error:
            raise SourceError("HTTPS document download failed") from error
    if parsed.scheme == "s3" and parsed.netloc and parsed.path.lstrip("/"):
        return await asyncio.to_thread(_read_s3, parsed.netloc, parsed.path.lstrip("/"), max_bytes)
    raise UsageError("document uri must be an HTTPS URL or s3://bucket/key")


def _read_s3(bucket: str, key: str, max_bytes: int) -> bytes:
    import boto3

    client = boto3.client("s3", endpoint_url=os.environ.get("VITRUVIO_HTTP_S3_ENDPOINT"))
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as error:
        raise SourceError("S3 document download failed") from error
    if response.get("ContentLength", 0) > max_bytes:
        response["Body"].close()
        raise EvidenceRefusedError(f"document exceeds the {max_bytes} byte limit")
    body = bytearray()
    stream = response["Body"]
    try:
        for chunk in stream.iter_chunks(chunk_size=1024 * 1024):
            body.extend(chunk)
            if len(body) > max_bytes:
                raise EvidenceRefusedError(f"document exceeds the {max_bytes} byte limit")
    finally:
        stream.close()
    return bytes(body)
