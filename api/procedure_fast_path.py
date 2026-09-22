"""Deterministic answers for the managed administrative-procedure catalogue.

The fast path is deliberately small and local.  It only selects a reviewed
procedure record and renders facts already present in that record; it never
generates legal values and never calls an embedding or language-model service.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from api.data_paths import notebook_data_dir
from api.legal_query_understanding import classify_legal_query
from api.legal_query_normalization import normalize_legal_query as build_legal_query_normalization


FAST_PATH_VERSION = "procedure-fast-v2"
_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^a-z0-9]+")

# These are intentionally reviewed, one-to-one expansions.  Ambiguous
# abbreviations are not expanded unless the question contains a useful
# procedure context as well.
REVIEWED_ABBREVIATIONS: dict[str, str] = {
    "gpxd": "giay phep xay dung",
    "gcn": "giay chung nhan",
    "cccd": "can cuoc",
    "bhyt": "bao hiem y te",
    "vneid": "dinh danh dien tu",
    "dvc": "dich vu cong",
    "ubnd": "uy ban nhan dan",
    "ct07": "xac nhan thong tin ve cu tru",
    "hk": "ho khau",
    "tt": "tam tru",
    "tthu": "thu tuc",
}

_STOPWORDS = {
    "anh", "chi", "anhchi", "cho", "toi", "minh", "muon", "xin", "hoi",
    "giup", "voi", "ve", "la", "thi", "co", "khong", "duoc", "can",
    "phai", "the", "nao", "nhu", "mot", "cua", "tai", "o", "trong",
    "cho", "nay", "hien", "nay", "tu", "den", "va", "hay", "hoac",
    "thuc", "hien", "thu", "tuc", "hanh", "chinh", "cap", "dang", "ky",
}

_FACET_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("documents", ("ho so", "giay to", "can mang", "chuan bi", "thanh phan", "tai lieu")),
    ("submission_place", ("o dau", "noi nop", "nop o", "den dau", "dia chi", "tiep nhan")),
    ("duration", ("bao lau", "thoi han", "may ngay", "mat bao lau", "khi nao", "ngay lam viec")),
    ("fee", ("le phi", "phi", "bao nhieu tien", "chi phi", "thu bao nhieu", "tien")),
    ("steps", ("lam sao", "trinh tu", "cac buoc", "bat dau", "thuc hien", "quy trinh")),
)


def _catalog_path() -> Path:
    configured = str(os.getenv("PROCEDURE_FAST_PATH_CATALOG") or "").strip()
    if configured:
        return Path(configured)
    return notebook_data_dir() / "forms" / "managed_procedures_runtime_v1.json"


def _strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.replace("đ", "d").replace("Đ", "D")


def normalize_procedure_query(value: Any) -> str:
    """Normalize Vietnamese input while keeping a token-safe local form."""

    text = _strip_accents(str(value or "")).casefold()
    text = text.replace("/", " ").replace("-", " ")
    # Expand only reviewed abbreviations.  This makes ``gpxd`` and ``cccd``
    # useful without turning arbitrary short words into legal assertions.
    for short, expanded in REVIEWED_ABBREVIATIONS.items():
        text = re.sub(rf"(?<![a-z0-9]){re.escape(short)}(?![a-z0-9])", expanded, text)
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def _query_variants(value: Any) -> tuple[str, ...]:
    """Return bounded shared-normalizer variants plus the local safe form.

    The main chatbot's normalizer owns reviewed social shorthand, typo fixes
    and contextual aliases.  The local procedure normalizer remains in the
    set as a fail-safe so a missing or malformed normalization artifact never
    makes the public route unavailable.
    """

    raw = str(value or "").strip()
    values: list[str] = [raw]
    try:
        packet = build_legal_query_normalization(raw)
        values.extend(
            str(item.query).strip()
            for item in packet.variants
            if str(item.query or "").strip()
        )
        values.append(str(packet.normalized_query or "").strip())
    except Exception:
        # The procedure matcher must stay usable during a partial local
        # bootstrap when the optional normalization catalogue is unavailable.
        pass
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        candidate = normalize_procedure_query(item)
        if candidate and candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return tuple(normalized)


def build_query_variants(value: Any) -> tuple[str, ...]:
    """Public bounded variant builder shared by procedure and intent matchers."""

    return _query_variants(value)


def _tokens(value: str, *, meaningful: bool = False) -> set[str]:
    words = set(_WORD_RE.findall(value))
    if meaningful:
        words = {word for word in words if word not in _STOPWORDS and len(word) > 1}
    return words


def _char_ngrams(value: str, size: int = 3) -> set[str]:
    compact = value.replace(" ", "")
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _record_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("ward_procedure:")


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


@lru_cache(maxsize=4)
def _load_catalog_cached(path_string: str, stamp: int) -> tuple[dict[str, Any], ...]:
    path = Path(path_string)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = payload.get("procedures") if isinstance(payload, Mapping) else payload
        if isinstance(rows, list):
            return tuple(dict(row) for row in rows if isinstance(row, Mapping))
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    # A local installation can be bootstrapping before the managed projection
    # exists.  The seed is still deterministic and does not add a second data
    # source once the runtime projection is available.
    try:
        from api.seed_haiphong_90_procedures import HAI_PHONG_90_PROCEDURES

        return tuple(dict(row) for row in HAI_PHONG_90_PROCEDURES)
    except Exception:
        return ()


def clear_fast_path_cache() -> None:
    _load_catalog_cached.cache_clear()


def load_procedure_catalog() -> tuple[dict[str, Any], ...]:
    path = _catalog_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = 0
    return _load_catalog_cached(str(path), stamp)


def fast_path_release_id(items: Iterable[Mapping[str, Any]] | None = None) -> str:
    rows = list(items if items is not None else load_procedure_catalog())
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{FAST_PATH_VERSION}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _allowed_audience(row: Mapping[str, Any], audience: str) -> bool:
    allowed = row.get("eligible_roles") or row.get("audience")
    if not allowed:
        return True
    if isinstance(allowed, str):
        allowed = [allowed]
    values = {str(item).casefold() for item in allowed}
    return bool({audience.casefold(), "citizen", "guest", "both", "all"} & values)


def is_fast_path_eligible(row: Mapping[str, Any], *, audience: str = "citizen") -> bool:
    """Fail closed for archived/unapproved records.

    ``managed_live`` is the approval state used by the Admin procedure
    projection.  A public source URL is included when present; absence of one
    is surfaced as a source gap instead of inventing a URL.
    """

    if not _record_id(row.get("procedure_id") or row.get("id")):
        return False
    if row.get("archived") is True or row.get("is_archived") is True:
        return False
    if row.get("approved") is False:
        return False
    review = str(row.get("review_status") or "approved").casefold()
    catalog = str(row.get("catalog_status") or "approved").casefold()
    source = str(row.get("source_status") or "managed_live").casefold()
    if review not in {"approved", "released", "published"}:
        return False
    if catalog not in {"approved", "active", "released", "published"}:
        return False
    if source in {"expired", "excluded", "archived", "withdrawn", "stale", "invalid"}:
        return False
    return _allowed_audience(row, audience)


def _aliases(row: Mapping[str, Any]) -> list[str]:
    name = str(row.get("name") or row.get("procedure_name") or "").strip()
    values = [name, *_safe_list(row.get("aliases"))]
    procedure_id = _record_id(row.get("procedure_id") or row.get("id"))
    if procedure_id:
        values.append(procedure_id.replace("_", " "))
    # Human questions often omit administrative wrappers while retaining the
    # distinctive words.  Keep generated aliases conservative.
    values.extend(
        [
            name.replace(" (Cấp xã)", "").replace(" (Thẩm quyền cấp xã)", ""),
            name.replace("Đăng ký ", "").replace("Cấp ", ""),
        ]
    )
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_procedure_query(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _score(question: str, alias: str) -> float:
    if not question or not alias:
        return 0.0
    if question == alias:
        return 1.0
    if alias in question:
        return 0.93
    q_tokens = _tokens(question, meaningful=True)
    a_tokens = _tokens(alias, meaningful=True)
    if not q_tokens or not a_tokens:
        return 0.0
    overlap = len(q_tokens & a_tokens)
    coverage = overlap / max(1, min(len(q_tokens), len(a_tokens)))
    jaccard = overlap / max(1, len(q_tokens | a_tokens))
    q_grams = _char_ngrams(question)
    a_grams = _char_ngrams(alias)
    ngram = len(q_grams & a_grams) / max(1, len(q_grams | a_grams))
    sequence = SequenceMatcher(None, question, alias).ratio()
    # Coverage rewards a natural question that includes a procedure's key
    # words; n-grams catch small typos without allowing a single word to win.
    score = 0.48 * coverage + 0.20 * jaccard + 0.20 * ngram + 0.12 * sequence
    if overlap < 2 and not (alias in question or question in alias):
        score *= 0.55
    return round(min(1.0, score), 6)


def score_normalized_query(question: str, alias: str) -> float:
    """Score two already-normalized strings for a bounded fast-path matcher."""

    return _score(question, alias)


def detect_procedure_facet(question: str, *, classification: Mapping[str, Any] | None = None) -> str:
    # Reuse the main chatbot's reviewed intent vocabulary while keeping the
    # quick route's answer renderer independent.  The keyword fallback below
    # remains useful for new procedure wording that has not been added to M4.
    intent = str((classification or {}).get("intent") or "").upper()
    intent_facets = {
        "REQUIRED_DOCUMENTS": "documents",
        "AUTHORITY": "submission_place",
        "DEADLINE": "duration",
        "FEE": "fee",
        "PROCESS": "steps",
    }
    if intent in intent_facets:
        return intent_facets[intent]
    normalized = normalize_procedure_query(question)
    for facet, terms in _FACET_TERMS:
        if any(term in normalized for term in terms):
            return facet
    return "overview"


def _source_url(row: Mapping[str, Any]) -> str | None:
    for key in ("source_url", "official_source_page", "official_source_url"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _procedure_detail(row: Mapping[str, Any], *, release_id: str) -> dict[str, Any]:
    procedure_id = _record_id(row.get("procedure_id") or row.get("id"))
    domain = str(row.get("domain") or row.get("domain_slug") or "").strip() or None
    detail = {
        "procedure_id": procedure_id,
        # Keep the additive fast-path projection readable by older procedure
        # cards that use the ward_procedure field names.
        "id": procedure_id,
        "name": str(row.get("name") or row.get("procedure_name") or "").strip(),
        "domain": domain,
        "domain_slug": domain,
        "department": row.get("department") or row.get("receiving_authority"),
        "submission_place": str(row.get("submission_place") or "").strip(),
        "documents_required": _safe_list(row.get("documents_required")),
        "steps": _safe_list(row.get("steps")),
        "duration": str(row.get("duration") or "").strip(),
        "fee": str(row.get("fee") or "").strip(),
        "guidance": str(row.get("guidance") or "").strip(),
        "legal_basis": _safe_list(row.get("legal_basis")),
        "forms": list(row.get("forms") or []) if isinstance(row.get("forms"), list) else [],
        "source_url": _source_url(row),
        "source_status": str(row.get("source_status") or "managed_live"),
        "review_status": str(row.get("review_status") or "approved"),
        "fast_path_release_id": release_id,
    }
    return detail


def _join_list(values: list[str]) -> str:
    if not values:
        return "thông tin thành phần hồ sơ đang được cập nhật trong bản thủ tục công bố"
    values = [_clean_clause(value) for value in values]
    if len(values) == 1:
        return values[0]
    return "; ".join(values[:-1]) + "; và " + values[-1]


def _clean_clause(value: Any) -> str:
    """Avoid doubled terminal punctuation while retaining the source wording."""

    return str(value or "").strip().rstrip(" .;。；")


def render_procedure_answer(row: Mapping[str, Any], *, facet: str, release_id: str) -> str:
    name = str(row.get("name") or row.get("procedure_name") or "thủ tục này").strip()
    documents = _safe_list(row.get("documents_required"))
    steps = _safe_list(row.get("steps"))
    place = str(row.get("submission_place") or "").strip()
    duration = str(row.get("duration") or "").strip()
    fee = str(row.get("fee") or "").strip()
    guidance = str(row.get("guidance") or "").strip()

    if facet == "documents":
        lead = f"Để thực hiện {name}, anh/chị cần chuẩn bị "
        return lead + _join_list(documents) + ". " + (f"Hồ sơ nộp tại {_clean_clause(place)}." if place else "")
    if facet == "submission_place":
        return f"Hồ sơ {name} được tiếp nhận tại {_clean_clause(place or 'nơi tiếp nhận ghi trong bản thủ tục công bố')}."
    if facet == "duration":
        return f"Thời hạn giải quyết {name} là {_clean_clause(duration or 'chưa có thông tin đã xác minh trong bản thủ tục hiện hành')}."
    if facet == "fee":
        return f"Lệ phí của {name}: {_clean_clause(fee or 'chưa có thông tin đã xác minh trong bản thủ tục hiện hành')}."
    if facet == "steps":
        if steps:
            numbered = " ".join(f"Bước {index + 1}: {_clean_clause(value)}." for index, value in enumerate(steps))
            return f"Với {name}, trình tự thực hiện như sau: {numbered}"
        return f"Trình tự của {name} chưa có thông tin đã xác minh trong bản thủ tục hiện hành."

    parts = [f"Anh/chị đang hỏi về {name}."]
    if guidance:
        parts.append(guidance.rstrip())
    if place:
        parts.append(f"Hồ sơ được tiếp nhận tại {_clean_clause(place)}.")
    if duration:
        parts.append(f"Thời hạn giải quyết: {_clean_clause(duration)}.")
    return " ".join(parts)


@dataclass(frozen=True)
class FastPathMatch:
    decision: str
    reason_code: str
    score: float = 0.0
    second_score: float = 0.0
    facet: str = "overview"
    procedure: dict[str, Any] | None = None
    answer: str | None = None
    release_id: str | None = None
    source_gap: tuple[str, ...] = ()

    @property
    def hit(self) -> bool:
        return self.decision == "hit" and self.procedure is not None and bool(self.answer)


def match_procedure_fast_path(
    question: str,
    *,
    audience: str = "citizen",
    domain: str | None = None,
    legal_as_of: date | None = None,
    catalog: Iterable[Mapping[str, Any]] | None = None,
) -> FastPathMatch:
    question_variants = _query_variants(question)
    normalized_question = question_variants[0] if question_variants else normalize_procedure_query(question)
    # This is the shared question-understanding front door.  It only describes
    # the request; the fast path still selects and renders managed procedure
    # facts locally and never delegates the answer to the main chatbot.
    classification = classify_legal_query(question)
    if len(normalized_question) < 4:
        return FastPathMatch("miss", "QUESTION_TOO_SHORT")
    rows = [dict(row) for row in (catalog if catalog is not None else load_procedure_catalog())]
    release_id = fast_path_release_id(rows)
    candidates: list[tuple[float, dict[str, Any]]] = []
    normalized_domain = normalize_procedure_query(domain) if domain else None
    for row in rows:
        if not is_fast_path_eligible(row, audience=audience):
            continue
        row_domain = normalize_procedure_query(row.get("domain") or row.get("domain_slug"))
        if normalized_domain and row_domain != normalized_domain:
            continue
        status = str(row.get("effectivity_status") or row.get("validity_status") or "").casefold()
        if status in {"expired", "withdrawn", "excluded", "invalid", "stale"}:
            continue
        best = max(
            (_score(variant, alias) for variant in question_variants for alias in _aliases(row)),
            default=0.0,
        )
        if best:
            candidates.append((best, row))
    candidates.sort(key=lambda item: (-item[0], _record_id(item[1].get("procedure_id") or item[1].get("id"))))
    if not candidates:
        return FastPathMatch("miss", "NO_CANDIDATE", release_id=release_id)
    best_score, best_row = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    # A single distinctive procedure phrase is enough only at a high score;
    # lower scores need a clear margin from the next procedure.
    if best_score < 0.50:
        return FastPathMatch("miss", "MATCH_BELOW_THRESHOLD", best_score, second_score, release_id=release_id)
    if second_score >= 0.46 and (best_score - second_score) < 0.08:
        return FastPathMatch("ambiguous", "MULTIPLE_PROCEDURES", best_score, second_score, release_id=release_id)
    facet = detect_procedure_facet(question, classification=classification)
    detail = _procedure_detail(best_row, release_id=release_id)
    source_gap: list[str] = []
    if not detail.get("source_url"):
        source_gap.append("Mục quản lý thủ tục chưa có URL nguồn chính thức để hiển thị.")
        require_source = str(os.getenv("PROCEDURE_FAST_PATH_REQUIRE_SOURCE_URL", "false")).strip().casefold()
        if require_source in {"1", "true", "yes", "on"}:
            return FastPathMatch(
                "stale",
                "SOURCE_URL_REQUIRED",
                best_score,
                second_score,
                facet,
                detail,
                release_id=release_id,
                source_gap=tuple(source_gap),
            )
    if legal_as_of is not None and detail.get("source_status") in {"managed_live", "approved"}:
        # The managed record has no historical validity interval.  Do not
        # claim it answers a historical-date question.
        if any(term in normalized_question for term in ("nam ", "ngay ", "truoc ", "hieu luc", "quy dinh cu")):
            return FastPathMatch("stale", "HISTORICAL_DATE_REQUIRES_SOURCE", best_score, second_score, facet, detail, release_id=release_id)
    answer = render_procedure_answer(best_row, facet=facet, release_id=release_id)
    return FastPathMatch(
        "hit", "MATCH_CONFIDENT", best_score, second_score, facet, detail, answer,
        release_id, tuple(source_gap),
    )


def match_to_response(match: FastPathMatch, *, question: str) -> dict[str, Any] | None:
    """Project a match into the additive AskResponse fields."""

    if not match.hit:
        return None
    detail = dict(match.procedure or {})
    source_url = detail.get("source_url")
    citations: list[dict[str, Any]] = []
    if source_url:
        citations.append({
            "document_title": detail.get("name") or "Thủ tục hành chính",
            "effective_status": detail.get("source_status") or "managed_live",
            "source_url": source_url,
            "verification_status": "managed_approved",
            "label": "Nguồn thủ tục đã phát hành",
        })
    return {
        "answer": match.answer,
        "question": question,
        "citations": citations,
        "procedure_detail": detail,
        "procedure_summary": detail.get("name"),
        "grounding_status": "fully_grounded" if citations else "partially_grounded",
        "answer_status": "verified" if citations else "partial",
        "outcome": "answered",
        "reason_code": match.reason_code,
        "retryable": False,
        "answer_mode": "procedure_fast_path",
        "llm_used": False,
        "fast_path_release_id": match.release_id,
        "procedure_id": detail.get("procedure_id"),
        "answer_facet": match.facet,
        "source_gap": list(match.source_gap),
        "generation_provenance": {
            "mode": "procedure_fast_path",
            "provider_label": "deterministic",
            "model_calls": 0,
        },
    }
