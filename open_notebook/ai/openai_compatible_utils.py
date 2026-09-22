"""Small URL helpers shared by OpenAI-compatible provider integrations.

LM Studio, vLLM and most local servers expose the OpenAI protocol below a
``/v1`` path.  The settings form historically accepted the host root and the
different call sites then disagreed about whether to add that path, which made
model discovery and generation fail in different ways.  Keep the value saved
by the user intact, but normalize it at the runtime boundary.
"""

from urllib.parse import urlsplit, urlunsplit


def normalize_openai_compatible_base_url(value: str | None) -> str:
    """Return a protocol base URL suitable for OpenAI-style endpoints.

    A host-only URL is upgraded to ``/v1``.  Explicit paths are preserved so
    providers such as OpenRouter (``/api/v1``) and custom gateways continue to
    work unchanged.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""

    trimmed = raw.rstrip("/")
    try:
        parsed = urlsplit(trimmed)
    except ValueError:
        return trimmed

    if parsed.scheme and parsed.netloc and parsed.path in ("", "/"):
        return urlunsplit(
            (parsed.scheme, parsed.netloc, "/v1", parsed.query, parsed.fragment)
        ).rstrip("/")
    return trimmed


def openai_compatible_models_url(value: str | None) -> str:
    """Build the models endpoint for an OpenAI-compatible base URL."""

    base_url = normalize_openai_compatible_base_url(value)
    if not base_url:
        return ""
    if base_url.endswith("/models"):
        return base_url
    return f"{base_url}/models"
