"""Deterministic Vietnamese legal-query normalization before retrieval.

The normalizer deliberately keeps the user's wording and produces bounded
retrieval variants.  It does not classify a domain, infer a procedure, or add
legal facts.  A failure always falls back to the raw query so this module is
safe to place in front of the frozen v6r26 retrieval service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from typing import Any, Mapping, Sequence


NORMALIZATION_VERSION = "legal-query-normalization-v1"
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "legal-query-normalization-v1.json"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_MAX_VARIANTS = 4

_URL_OR_EMAIL_RE = re.compile(r"(?:https?://[^\s]+|[^\s@]+@[^\s@]+\.[^\s@]+)")
_LEGAL_CODE_RE = re.compile(
    r"\b(?:\d+(?:\.\d+){2,}|\d{1,4}/\d{2,4}/[A-Za-zÀ-ỹĐđ0-9-]+|"
    r"[A-ZĐ]{2,}\d+[A-Za-zĐđ0-9.-]*)\b"
)
_ARTICLE_RE = re.compile(
    r"\b(?:Điều|Khoản|Điểm|Dieu|Khoan|Diem)\s+[0-9]+[A-Za-zĐđ]?\b",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r"(['\"“‘«]).*?(['\"”’»])")
_WORD_RE = re.compile(r"(?<![\wÀ-ỹĐđ])([\wÀ-ỹĐđ]+)(?![\wÀ-ỹĐđ])", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").casefold())
    return _WHITESPACE_RE.sub(
        " ",
        "".join(
            char
            for char in decomposed
            if unicodedata.category(char) != "Mn"
        ).replace("đ", "d"),
    ).strip()


def _cue_present(folded_text: str, folded_cue: str) -> bool:
    """Match context cues by token boundary; avoid ``ba`` matching ``ban``."""

    if not folded_cue:
        return False
    return re.search(
        rf"(?<![\wÀ-ỹĐđ]){re.escape(folded_cue)}(?![\wÀ-ỹĐđ])",
        folded_text,
    ) is not None


def _clean_transport(value: str) -> str:
    value = unicodedata.normalize("NFC", str(value or "").strip())
    value = "".join(
        char
        for char in value
        if char in "\n\t" or unicodedata.category(char) != "Cc"
    )
    value = _WHITESPACE_RE.sub(" ", value)
    value = re.sub(r"([!?.,;:])\1{2,}", r"\1", value)
    return value.strip()


def _checksum(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProtectedSpanV1:
    start: int
    end: int
    kind: str
    checksum: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class QueryVariantV1:
    query_id: str
    type: str
    query: str
    confidence: float
    weight: float
    change_codes: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query_type": self.type,
            "query": self.query,
            "confidence": round(self.confidence, 4),
            "weight": round(self.weight, 4),
            "change_codes": list(self.change_codes),
        }


@dataclass(frozen=True)
class QueryNormalizationPacketV1:
    version: str
    raw_query: str
    normalized_query: str
    variants: tuple[QueryVariantV1, ...]
    protected_spans: tuple[ProtectedSpanV1, ...] = ()
    corrections: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    checksum: str = ""
    normalization_ms: float = 0.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "raw_query": self.raw_query,
            "normalized_query": self.normalized_query,
            "variants": [item.to_payload() for item in self.variants],
            "protected_spans": [item.to_payload() for item in self.protected_spans],
            "corrections": [dict(item) for item in self.corrections],
            "warnings": list(self.warnings),
            "checksum": self.checksum,
            "normalization_ms": self.normalization_ms,
        }

    def trace(self) -> dict[str, Any]:
        """Privacy-safe trace; query contents are intentionally omitted."""

        return {
            "normalization_version": self.version,
            "normalization_checksum": self.checksum,
            "protected_span_count": len(self.protected_spans),
            "correction_count": len(self.corrections),
            "variant_count": len(self.variants),
            "change_codes": sorted(
                {
                    code
                    for variant in self.variants
                    for code in variant.change_codes
                }
            ),
            "normalization_ms": self.normalization_ms,
            "fallback_to_raw": any(
                warning in {"NORMALIZER_ARTIFACT_INVALID", "NORMALIZER_EXCEPTION"}
                for warning in self.warnings
            ),
            "warnings": list(self.warnings),
        }


def is_normalization_enabled(role: str, environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    enabled = str(source.get("LEGAL_QUERY_NORMALIZATION_V1_ENABLED", "false")).strip().casefold()
    roles = {
        item.strip().casefold()
        for item in str(source.get("LEGAL_QUERY_NORMALIZATION_V1_ROLES", "")).split(",")
        if item.strip()
    }
    return enabled in _TRUE_VALUES and str(role or "citizen").strip().casefold() in roles


@lru_cache(maxsize=4)
def _load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    if not isinstance(catalog, Mapping) or catalog.get("version") != NORMALIZATION_VERSION:
        raise ValueError("normalization_catalog_version_mismatch")
    expected = str(catalog.get("checksum") or "").strip()
    body = {key: value for key, value in catalog.items() if key != "checksum"}
    if expected and expected != _checksum(body):
        raise ValueError("normalization_catalog_checksum_mismatch")
    return catalog


def _protected_spans(text: str) -> tuple[ProtectedSpanV1, ...]:
    matches: list[tuple[int, int, str]] = []
    for pattern, kind in (
        (_URL_OR_EMAIL_RE, "url_or_email"),
        (_LEGAL_CODE_RE, "legal_code"),
        (_ARTICLE_RE, "structural_reference"),
        (_QUOTED_RE, "quoted_text"),
    ):
        matches.extend((match.start(), match.end(), kind) for match in pattern.finditer(text))
    selected: list[ProtectedSpanV1] = []
    for start, end, kind in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < current.end and end > current.start for current in selected):
            continue
        selected.append(
            ProtectedSpanV1(
                start=start,
                end=end,
                kind=kind,
                checksum=_checksum(text[start:end]),
            )
        )
    return tuple(selected)


def _mask_protected(text: str, spans: Sequence[ProtectedSpanV1]) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}
    pieces: list[str] = []
    cursor = 0
    for index, span in enumerate(spans):
        token = f" __LEGAL_PROTECTED_{index}__ "
        pieces.append(text[cursor:span.start])
        pieces.append(token)
        replacements[token.strip()] = text[span.start:span.end]
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces), replacements


def _unmask(value: str, replacements: Mapping[str, str]) -> str:
    for token, original in replacements.items():
        value = value.replace(token, original)
    return _WHITESPACE_RE.sub(" ", value).strip()


def _overlaps(start: int, end: int, spans: Sequence[ProtectedSpanV1]) -> bool:
    return any(start < span.end and end > span.start for span in spans)


def _replace_word_aliases(
    masked: str,
    *,
    aliases: Mapping[str, Any],
    spans: Sequence[ProtectedSpanV1],
    original: str,
) -> tuple[str, list[dict[str, Any]], set[str]]:
    corrections: list[dict[str, Any]] = []
    codes: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        folded = _fold(token)
        entry = aliases.get(folded)
        if not isinstance(entry, Mapping) or _overlaps(match.start(1), match.end(1), spans):
            return token
        replacement = str(entry.get("replacement") or "").strip()
        if not replacement or folded == _fold(replacement):
            return token
        code = str(entry.get("code") or "abbr_unambiguous")
        confidence = float(entry.get("confidence") or 1.0)
        corrections.append(
            {
                "kind": "abbreviation",
                "original_checksum": _checksum(token),
                "replacement_checksum": _checksum(replacement),
                "code": code,
                "confidence": round(confidence, 4),
            }
        )
        codes.add(code)
        return replacement

    return _WORD_RE.sub(replace, masked), corrections, codes


def _apply_phrase_corrections(
    masked: str,
    phrases: Mapping[str, str],
) -> tuple[str, list[dict[str, Any]], set[str]]:
    corrections: list[dict[str, Any]] = []
    codes: set[str] = set()
    value = masked
    for source, replacement in sorted(phrases.items(), key=lambda item: -len(item[0])):
        pattern = re.compile(rf"(?<![\wÀ-ỹĐđ]){re.escape(source)}(?![\wÀ-ỹĐđ])", re.IGNORECASE)
        if not pattern.search(value):
            continue
        value = pattern.sub(replacement, value)
        corrections.append(
            {
                "kind": "phrase_typo",
                "original_checksum": _checksum(source),
                "replacement_checksum": _checksum(replacement),
                "code": "reviewed_phrase_correction",
                "confidence": 0.99,
            }
        )
        codes.add("reviewed_phrase_correction")
    return value, corrections, codes


def _contextual_alias_variants(
    masked: str,
    *,
    entries: Mapping[str, Any],
    base_codes: set[str],
) -> tuple[str, list[tuple[str, float, str]], list[dict[str, Any]], set[str]]:
    folded = _fold(masked)
    primary = masked
    alternatives: list[tuple[str, float, str]] = []
    corrections: list[dict[str, Any]] = []
    codes = set(base_codes)
    for alias, raw_options in entries.items():
        options = raw_options if isinstance(raw_options, list) else []
        if not options:
            continue
        pattern = re.compile(rf"(?<![\wÀ-ỹĐđ]){re.escape(alias)}(?![\wÀ-ỹĐđ])", re.IGNORECASE)
        # Match against the original text because ``Đ`` and accented aliases
        # are deliberately folded only for cue scoring, not for replacement.
        if not pattern.search(masked):
            continue
        scored: list[tuple[float, Mapping[str, Any]]] = []
        for option in options:
            if not isinstance(option, Mapping):
                continue
            required = [_fold(item) for item in option.get("required_any") or []]
            excluded = [_fold(item) for item in option.get("excluded_any") or []]
            if excluded and any(_cue_present(folded, item) for item in excluded):
                continue
            score = float(option.get("confidence") or 0.7)
            if required and any(_cue_present(folded, item) for item in required):
                score += 0.20
            elif required:
                score -= 0.20
            scored.append((score, option))
        if not scored:
            continue
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best = scored[0]
        best_text = str(best.get("replacement") or "").strip()
        if not best_text:
            continue
        margin = best_score - (scored[1][0] if len(scored) > 1 else 0.0)
        if best_score >= 0.90 and margin >= 0.12:
            primary = pattern.sub(best_text, primary)
            code = str(best.get("code") or "abbr_context")
            corrections.append(
                {
                    "kind": "abbreviation_context",
                    "original_checksum": _checksum(alias),
                    "replacement_checksum": _checksum(best_text),
                    "code": code,
                    "confidence": round(min(best_score, 1.0), 4),
                }
            )
            codes.add(code)
            continue
        for alt_score, option in scored[:2]:
            alt_text = str(option.get("replacement") or "").strip()
            if not alt_text or _fold(alt_text) == _fold(alias):
                continue
            alternatives.append((pattern.sub(alt_text, masked), min(max(alt_score, 0.70), 1.0), "abbr_ambiguous"))
    return primary, alternatives, corrections, codes


def _obvious_token_correction(
    masked: str,
    vocabulary: Sequence[str],
) -> tuple[str, list[dict[str, Any]], set[str]]:
    folded_vocab = {
        _fold(item): item
        for item in vocabulary
        if len(_fold(item)) >= 4
    }
    if not folded_vocab:
        return masked, [], set()
    keys = list(folded_vocab)
    corrections: list[dict[str, Any]] = []
    codes: set[str] = set()
    changed = masked
    for match in list(_WORD_RE.finditer(masked)):
        token = match.group(1)
        folded = _fold(token)
        if len(folded) < 4 or folded in folded_vocab or folded.startswith("__legal_protected_"):
            continue
        matches = difflib.get_close_matches(folded, keys, n=2, cutoff=0.88)
        if not matches:
            continue
        candidate = matches[0]
        ratio = difflib.SequenceMatcher(None, folded, candidate).ratio()
        margin = ratio - (
            difflib.SequenceMatcher(None, folded, matches[1]).ratio()
            if len(matches) > 1
            else 0.0
        )
        if ratio < 0.94 or margin < 0.04:
            continue
        replacement = folded_vocab[candidate]
        if _fold(replacement) == folded:
            continue
        changed = re.sub(
            rf"(?<![\wÀ-ỹĐđ]){re.escape(token)}(?![\wÀ-ỹĐđ])",
            replacement,
            changed,
            count=1,
        )
        corrections.append(
            {
                "kind": "spelling",
                "original_checksum": _checksum(token),
                "replacement_checksum": _checksum(replacement),
                "code": "vocabulary_edit_distance",
                "confidence": round(ratio, 4),
            }
        )
        codes.add("vocabulary_edit_distance")
    return changed, corrections, codes


def _raw_packet(question: str, warning: str) -> QueryNormalizationPacketV1:
    raw = str(question or "").strip()
    variant = QueryVariantV1(
        query_id="issue-1-raw",
        type="raw",
        query=raw,
        confidence=1.0,
        weight=1.0,
    )
    payload = {
        "version": NORMALIZATION_VERSION,
        "raw_query": raw,
        "normalized_query": raw,
        "variants": [variant.to_payload()],
        "warnings": [warning],
    }
    return QueryNormalizationPacketV1(
        version=NORMALIZATION_VERSION,
        raw_query=raw,
        normalized_query=raw,
        variants=(variant,),
        warnings=(warning,),
        checksum=_checksum(payload),
    )


def normalize_legal_query(
    question: str,
    *,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
) -> QueryNormalizationPacketV1:
    """Build a bounded, deterministic normalization packet.

    The function is intentionally total: malformed configuration or an
    unexpected token always returns a raw-only packet with a warning.
    """

    started = time.perf_counter()
    raw = str(question or "").strip()
    if not raw:
        return _raw_packet(raw, "EMPTY_QUERY")
    try:
        catalog = _load_catalog(Path(catalog_path))
        clean = _clean_transport(raw)
        spans = _protected_spans(clean)
        masked, placeholders = _mask_protected(clean, spans)
        aliases = catalog.get("unambiguous_aliases") or {}
        masked, corrections, codes = _replace_word_aliases(
            masked,
            aliases=aliases,
            spans=(),
            original=clean,
        )
        masked, phrase_corrections, phrase_codes = _apply_phrase_corrections(
            masked,
            catalog.get("phrase_corrections") or {},
        )
        corrections.extend(phrase_corrections)
        codes.update(phrase_codes)
        masked, spelling_corrections, spelling_codes = _obvious_token_correction(
            masked,
            catalog.get("vocabulary") or [],
        )
        corrections.extend(spelling_corrections)
        codes.update(spelling_codes)
        primary_masked, ambiguous, ambiguous_corrections, ambiguous_codes = _contextual_alias_variants(
            masked,
            entries=catalog.get("ambiguous_aliases") or {},
            base_codes=codes,
        )
        corrections.extend(ambiguous_corrections)
        codes.update(ambiguous_codes)
        normalized = _unmask(primary_masked, placeholders)
        variants: list[QueryVariantV1] = [
            QueryVariantV1(
                query_id="issue-1-raw",
                type="raw",
                query=raw,
                confidence=1.0,
                weight=1.0,
            )
        ]
        if normalized and _fold(normalized) != _fold(raw):
            confidence = min(
                [float(item.get("confidence") or 1.0) for item in corrections]
                or [1.0]
            )
            variants.append(
                QueryVariantV1(
                    query_id="issue-1-normalized",
                    type="normalized",
                    query=normalized,
                    confidence=confidence,
                    weight=max(0.70, confidence),
                    change_codes=tuple(sorted(codes)),
                )
            )
        for index, (alternative, confidence, code) in enumerate(ambiguous, start=1):
            restored = _unmask(alternative, placeholders)
            if not restored or _fold(restored) in {_fold(item.query) for item in variants}:
                continue
            variants.append(
                QueryVariantV1(
                    query_id=f"issue-1-ambiguous-{index}",
                    type="ambiguous_alias",
                    query=restored,
                    confidence=confidence,
                    weight=max(0.70, confidence),
                    change_codes=(code,),
                )
            )
            if len(variants) >= _MAX_VARIANTS:
                break
        variants = variants[:_MAX_VARIANTS]
        payload = {
            "version": NORMALIZATION_VERSION,
            "raw_query": raw,
            "normalized_query": normalized,
            "variants": [item.to_payload() for item in variants],
            "protected_spans": [item.to_payload() for item in spans],
            "corrections": corrections,
        }
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        return QueryNormalizationPacketV1(
            version=NORMALIZATION_VERSION,
            raw_query=raw,
            normalized_query=normalized,
            variants=tuple(variants),
            protected_spans=spans,
            corrections=tuple(corrections),
            checksum=_checksum(payload),
            normalization_ms=elapsed,
        )
    except Exception:
        packet = _raw_packet(raw, "NORMALIZER_ARTIFACT_INVALID")
        return packet.__class__(**{**packet.__dict__, "normalization_ms": round((time.perf_counter() - started) * 1000, 3)})


def build_retrieval_variants(
    question: str,
    *,
    issue_id: str = "issue-1",
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
) -> tuple[QueryNormalizationPacketV1, list[dict[str, Any]]]:
    packet = normalize_legal_query(question, catalog_path=catalog_path)
    variants = [
        {
            **item.to_payload(),
            "query_id": f"{issue_id}-{item.query_id.split('-', 2)[-1]}",
            "normalization_checksum": packet.checksum,
        }
        for item in packet.variants
    ]
    return packet, variants[:_MAX_VARIANTS]
