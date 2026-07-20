"""Small deterministic checks used before legal evidence reaches the LLM."""

import re
import unicodedata
from collections.abc import Iterable


_STOPWORDS = {
    "cho", "cua", "cần", "can", "toi", "tôi", "muon", "muốn", "hoi", "hỏi",
    "nhu", "như", "the", "thế", "nao", "nào", "voi", "với", "tai", "tại",
    "o", "ở", "den", "đến", "nộp", "nop", "lam", "làm", "co", "có", "duoc", "được",
    "phai", "phải", "mot", "một", "nhung", "những", "trong", "theo", "ve", "về",
    "xin", "hay", "hãy", "giup", "giúp", "huong", "hướng", "dan", "dẫn", "thuc", "thực",
    "tuc", "tục", "giay", "giấy", "to", "tờ", "thong", "thông", "tin", "tin",
}


def ascii_fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", ascii_fold(value))
        if len(token) >= 4 and token not in _STOPWORDS
    }


def retrieval_has_query_overlap(question: str, results: Iterable[dict], *, limit: int = 5) -> bool:
    """Return false when the selected-domain hits are clearly unrelated.

    This is intentionally conservative: it only triggers a broader retry when
    none of the first hits shares at least two meaningful query terms.
    """
    query_terms = _tokens(question)
    if not query_terms:
        return True

    for item in list(results)[:limit]:
        text = " ".join(
            str(item.get(field) or "")
            for field in (
                "law_number", "document_title", "article_title",
                "chunk_heading", "field_name", "content",
            )
        )
        overlap = query_terms.intersection(_tokens(text))
        if len(overlap) >= min(2, len(query_terms)):
            return True
    return False


def expanded_retrieval_reason(question: str, results: Iterable[dict]) -> str | None:
    """Return an explicit reason when the fast legal scope is not enough.

    A non-empty result list is not evidence that it can answer a compound land,
    inheritance, planning, or specialised-sanction question. These questions
    need the active central corpus as a controlled second retrieval tier.
    """
    rows = [item for item in results if isinstance(item, dict)]
    if not rows:
        return "empty_core_results"
    normalized = ascii_fold(question)

    # 1. Civil registration (birth, marriage, death...) needs expanded tier if core has no civil docs
    civil_terms = ("khai sinh", "ket hon", "kết hôn", "khai tu", "khai tử", "ho tich", "hộ tịch", "doc than", "độc thân", "hon nhan", "hôn nhân")
    if any(term in normalized for term in civil_terms):
        has_civil_doc = False
        for item in rows:
            doc_domain = str(item.get("domain_slug") or item.get("domain") or "").strip()
            doc_title = str(item.get("document_title") or item.get("title") or "").lower()
            if doc_domain == "ho_tich_chung_thuc" or any(t in doc_title for t in ("hộ tịch", "khai sinh", "kết hôn", "khai tử", "hôn nhân")):
                has_civil_doc = True
                break
        if not has_civil_doc:
            return "civil_registration_needs_expanded_tier"

    # 2. Complex land or inheritance checks
    land_terms = ("dat dai", "quyen su dung dat", "so do", "giay tay")
    compound_terms = (
        "thua ke", "nguoi ban da mat", "nguoi chuyen nhuong da mat",
        "quy hoach", "tranh chap", "toa an", "thua ke khong hop tac",
    )
    if any(term in normalized for term in land_terms) and any(
        term in normalized for term in compound_terms
    ):
        return "complex_land_or_inheritance_question"
    if not retrieval_has_query_overlap(question, rows):
        return "low_query_overlap"
    article_keys = {
        str(item.get("article_id") or item.get("article_number") or "").strip()
        for item in rows
    }
    if len(article_keys - {""}) < 3:
        return "fewer_than_three_distinct_articles"

    return None
