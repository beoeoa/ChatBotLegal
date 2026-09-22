"""Application-scoped HTTP connection pool for configured legal services.

No response cache, retry, TLS bypass, or user identity defaults. The API owns
startup/shutdown; standalone command/tests retain their short-lived client.
"""
import httpx

_client: httpx.AsyncClient | None = None


def get_legal_upstream_client() -> httpx.AsyncClient | None:
    return _client if _client is not None and not _client.is_closed else None


async def start_legal_upstream_client() -> None:
    global _client
    if get_legal_upstream_client() is None:
        _client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )


async def close_legal_upstream_client() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()
