"""Deterministic legal identity comparisons; never infer metadata from a title.

An identity match is a review conflict, not proof of equal legal content or of
successful ingestion. Legacy content_hash can be a listing hash: do not use it.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

IDENTITY_VERSION = "legal-identity-v1"
_DASHES = str.maketrans({char: "-" for char in "‐‑‒–—−"})


def _fold(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).translate(_DASHES)
    return "".join(c for c in value if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "D").casefold()


def normalize_law_number(value: Any) -> str:
    number = re.sub(r"\s+", "", _fold(value)).upper()
    # Only a complete printed identifier is accepted, never a date or a match
    # extracted from a title/body containing references to another instrument.
    if not re.fullmatch(r"\d{1,6}/(?=[A-Z0-9/-]*[A-Z])[A-Z0-9]+(?:[/-][A-Z0-9]+)*", number):
        return ""
    first, rest = number.split("/", 1)
    return f"{int(first)}/{rest}"


def number_search_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_law_number(value)).lstrip("0")


def normalize_agency(value: Any) -> str:
    agency = re.sub(r"[^a-z0-9]+", " ", _fold(value)).strip()
    for short, full in (("ubnd", "uy ban nhan dan"), ("hdnd", "hoi dong nhan dan"), ("tp", "thanh pho")):
        agency = re.sub(rf"\b{short}\b", full, agency)
    # The administrative place name remains part of the key.
    agency = re.sub(r"\b(uy ban nhan dan|hoi dong nhan dan) thanh pho\b", r"\1", agency)
    return " ".join(agency.split())


def canonical_source_url(value: Any) -> str:
    try:
        parts = urlsplit(str(value or "").strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.lower()
        port = parts.port
        netloc = host if not port or (parts.scheme.lower(), port) in {("https", 443), ("http", 80)} else f"{host}:{port}"
        query = sorted((key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                       if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"})
        # Keep ItemID, all non-tracking parameters, path case, and protocol.
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(query), ""))
    except (TypeError, ValueError):
        return ""


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def content_fingerprint(content: Any) -> str:
    # Preserve case, numbers, punctuation and negation; only normalize encoding
    # and layout whitespace. A hash proves equality, not legal correctness.
    body = " ".join(unicodedata.normalize("NFC", str(content or "")).split())
    return hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""


def legal_identity(record: dict[str, Any]) -> dict[str, str]:
    raw = record.get("raw_metadata") or {}
    if not isinstance(raw, dict):
        raw = {}
    identity = {
        "version": IDENTITY_VERSION,
        "number": normalize_law_number(record.get("law_number") or raw.get("law_number")),
        "agency": normalize_agency(record.get("issuing_agency") or raw.get("issuing_agency")),
        "document_type": " ".join(_fold(record.get("document_type") or raw.get("document_type")).split()),
        "issued_date": _date(record.get("issued_date") or raw.get("issued_date")),
        "source_url": canonical_source_url(record.get("source_url") or raw.get("source_url") or record.get("detail_url")),
        "content_sha256": content_fingerprint(record.get("content")) if not raw.get("metadata_only") else "",
    }
    return identity


def compare_legal_documents(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Return an explanation, not a boolean based on a printed number alone."""
    a, b = legal_identity(left), legal_identity(right)
    shared = [key for key in ("number", "agency", "issued_date", "source_url", "content_sha256") if a[key] and a[key] == b[key]]
    conflicts = [key for key in ("number", "agency", "issued_date", "document_type") if a[key] and b[key] and a[key] != b[key]]
    same_url = "source_url" in shared
    same_content = "content_sha256" in shared
    reasons: list[str] = []
    kind = "unrelated"
    if "number" in conflicts or "agency" in conflicts:
        kind = "metadata_conflict" if same_url or same_content else "unrelated"
    elif "number" in shared and "issued_date" in conflicts and not re.search(r"/(?:19|20)\d{2}/", a["number"]) and a["issued_date"][:4] != b["issued_date"][:4]:
        kind = "metadata_conflict" if same_url or same_content else "unrelated"
    elif conflicts and ("number" in shared or same_url or same_content):
        kind = "metadata_conflict"
    elif same_content:
        kind = "duplicate_content"
    elif "number" in shared or same_url:
        kind = "content_changed" if a["content_sha256"] and b["content_sha256"] else "identity_match"
    if kind != "unrelated":
        reasons.extend(f"same_{key}" for key in shared)
        reasons.extend(f"conflicting_{key}" for key in conflicts)
        if not a["content_sha256"] or not b["content_sha256"]:
            reasons.append("full_content_not_compared")
        if not all(a[key] and b[key] for key in ("number", "agency", "issued_date")):
            reasons.append("identity_metadata_incomplete")
    return {"kind": kind, "matched_fields": shared, "conflicting_fields": conflicts, "reason_codes": reasons}


def candidate_identity_revision(record: dict[str, Any]) -> str:
    snapshot = {"identity": legal_identity(record), "status": record.get("status"), "updated": str(record.get("updated") or "")}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def candidate_external_id(record: dict[str, Any], *, origin: str) -> str:
    """Versioned idempotency key; a changed body cannot collide with its predecessor."""
    identity = legal_identity(record)
    # Listing-only candidates have no body. Keep their listing evidence stable,
    # but different fetched full-text versions receive different keys.
    version = identity["content_sha256"] or content_fingerprint((record.get("raw_metadata") or {}).get("listing_context") or record.get("description") or record.get("title"))
    key = {**identity, "content_sha256": version, "origin": origin}
    return hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:40]
