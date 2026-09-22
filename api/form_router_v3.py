"""Deterministic Feature 017 procedure and released-form resolver."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from typing import Any, Mapping

from api.administrative_query_signals import administrative_domain
from api.legal_form_catalog import fold_text
from api.legal_domains import canonicalize_legal_domain
from api.official_source_adapters import is_allowlisted_official_url


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPLICIT_FORM_CODE_RE = re.compile(
    r"\b(?:mau|form)\s+(?:so\s+)?([a-z]{1,8}\d{1,4}(?:[/-][a-z0-9-]+)?)\b",
    re.IGNORECASE,
)

_DOMAIN_MARKERS: dict[str, tuple[str, ...]] = {
    "ho_tich_chung_thuc": (
        "khai sinh", "khai tu", "ket hon", "ho tich", "chung thuc",
        "tinh trang hon nhan", "nuoi con nuoi", "con nuoi", "ly hon",
    ),
    "dat_dai_xay_dung": (
        "dat dai", "so do", "quyen su dung dat", "thua dat", "xay dung",
        "chuyen muc dich",
    ),
    "an_sinh_y_te_giao_duc": (
        "tro cap", "bao tro", "an sinh", "y te", "giao duc", "mam non",
        "tieu hoc", "truong hoc", "bao hiem",
    ),
    "cu_tru_an_ninh": (
        "cu tru", "thuong tru", "tam tru", "luu tru", "qua dem", "ngu lai",
        "can cuoc", "ho chieu", "xuat nhap canh", "thong hanh",
        "khai bao tam tru", "khai bao luu tru", "an ninh trat tu",
        "giay chung nhan du dieu kien ve an ninh trat tu", "co so kinh doanh",
    ),
    "khieu_nai_to_cao_xu_phat": (
        "khieu nai", "to cao", "xu phat", "quyet dinh phat", "bien ban vi pham",
    ),
    "kinh_te": (
        "ho kinh doanh", "dang ky kinh doanh", "giay chung nhan dang ky kinh doanh",
        "hop tac xa", "to hop tac",
    ),
    "hanh_chinh_cong": (
        "bo phan mot cua", "mot cua", "dich vu cong", "ma ho so",
        "giay hen", "ho so truc tuyen", "nop ho so online",
        "nop ho so bang van ban", "van ban den", "van ban di",
        "ho so luu tru", "sao luc ho so",
    ),
}


def _canonical_domain(value: Any) -> str:
    folded = fold_text(value).replace(" ", "_")
    return canonicalize_legal_domain(folded) or folded


def _strong_query_domains(question: str) -> set[str]:
    query = fold_text(question)
    domains = {
        domain
        for domain, markers in _DOMAIN_MARKERS.items()
        if any(marker in query for marker in markers)
    }
    shared = administrative_domain(question)
    if shared:
        domains.add(shared)
    return domains


def _primary_query_domain(question: str) -> str | None:
    """Return the dominant legal topic, not every incidental data source."""

    query = fold_text(question)
    # These named benefits are stronger than an incidental reference to the
    # population/residence database used to check eligibility.
    if "tro cap huu tri xa hoi" in query or "huu tri xa hoi" in query:
        return "an_sinh_y_te_giao_duc"
    shared = administrative_domain(question)
    if shared:
        return shared
    scored = [
        (sum(len(marker.split()) ** 2 for marker in markers if marker in query), domain)
        for domain, markers in _DOMAIN_MARKERS.items()
    ]
    score, domain = max(scored, default=(0, ""))
    return domain if score else None


def _contextual_hard_negative(question: str, procedure: Mapping[str, Any]) -> bool:
    """Reject known unsafe cross-actor candidates before lexical ranking."""

    query = fold_text(question)
    label = fold_text(
        f"{procedure.get('name') or ''} {procedure.get('canonical_name') or ''} "
        f"{procedure.get('authority') or ''}"
    )
    if (
        ("chu tich ubnd phuong" in query or "chu tich uy ban nhan dan phuong" in query)
        and "cong an" in label
    ):
        return True
    death_markers = ("qua doi", "tu vong", "chet", "mai tang", "mai táng")
    if (
        ("tro cap huu tri xa hoi" in query or "huu tri xa hoi" in query)
        and "mai tang" in label
        and not any(marker in query for marker in death_markers)
    ):
        return True
    # A request for a new construction permit must not be tied to the adjacent
    # adjustment procedure merely because both labels share the same long
    # phrase. Conversely, an explicit adjustment request must stay on that
    # procedure. This is an action hard-negative, not a guessed identity.
    if "giay phep xay dung" in query or "gpxd" in query:
        query_changes = any(
            marker in query
            for marker in (
                "dieu chinh",
                "sua doi giay phep",
                "thay doi giay phep",
                "sua chua",
                "cai tao",
            )
        )
        query_requests_new = any(
            marker in query
            for marker in ("xin giay phep", "cap moi", "xay moi", "giay phep moi")
        )
        label_changes = any(
            marker in label for marker in ("dieu chinh", "sua chua", "cai tao")
        )
        if query_changes != label_changes and (query_changes or query_requests_new):
            return True
    # A court request for recognition of an amicable divorce is a different
    # procedure from civil-status registration after a divorce ("ghi chú ly
    # hôn") and from a marital-status certificate. Shared wording must not
    # nominate either neighboring form.
    if "ly hon thuan tinh" in query or "thuan tinh ly hon" in query:
        if not (
            "ly hon thuan tinh" in label
            or "thuan tinh ly hon" in label
            or "cong nhan thuan tinh ly hon" in label
        ):
            return True
    # Mẫu 03 is reused by multiple procedures. The requested action is part
    # of the form identity: cấp đổi, cấp lại and cấp mới are not
    # interchangeable. The active ANTT catalog contains all three adjacent
    # procedures, so the two-way đổi/lại guard alone still left đổi/mới tied.
    query_cap_doi = bool(re.search(r"\bcap doi\b", query))
    query_cap_lai = bool(re.search(r"\bcap lai\b", query))
    query_cap_moi = bool(re.search(r"\bcap moi\b", query))
    label_cap_doi = bool(re.search(r"\bcap doi\b", label))
    label_cap_lai = bool(re.search(r"\bcap lai\b", label))
    label_cap_moi = bool(re.search(r"\bcap moi\b", label))
    if query_cap_doi and label_cap_lai and not label_cap_doi:
        return True
    if query_cap_doi and label_cap_moi and not label_cap_doi:
        return True
    if query_cap_lai and label_cap_doi and not label_cap_lai:
        return True
    if query_cap_lai and label_cap_moi and not label_cap_lai:
        return True
    if query_cap_moi and (label_cap_doi or label_cap_lai) and not label_cap_moi:
        return True
    return False


def _domain_conflicts(question: str, procedure: Mapping[str, Any]) -> bool:
    detected = _primary_query_domain(question)
    candidate = _canonical_domain(procedure.get("domain"))
    return bool(
        _contextual_hard_negative(question, procedure)
        or (detected and candidate and candidate != detected)
    )


def _explicit_form_code(question: str) -> str | None:
    match = EXPLICIT_FORM_CODE_RE.search(fold_text(question))
    return match.group(1).replace(" ", "").casefold() if match else None


def _procedures_for_form_code(
    code: str,
    manifest: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    def normalized_code(value: Any) -> str:
        folded = fold_text(value).replace(" ", "").casefold()
        return re.sub(r"^(?:mau|form)(?:so)?", "", folded)

    matching_form_ids = {
        str(asset.get("form_id") or "")
        for asset in manifest.get("assets") or []
        if str(asset.get("coverage_status") or "") == "released"
        and normalized_code(asset.get("form_code")) == normalized_code(code)
    }
    if not matching_form_ids:
        return []
    procedure_ids = {
        str(binding.get("procedure_id") or "")
        for binding in manifest.get("bindings") or []
        if str(binding.get("form_id") or "") in matching_form_ids
        and str(binding.get("coverage_status") or "") == "released"
    }
    return [
        procedure
        for procedure in manifest.get("procedures") or []
        if str(procedure.get("procedure_id") or "") in procedure_ids
    ]
GENERIC_FORM_RE = re.compile(r"(?iu)\b(?:mẫu|mau)\s*(?:số|so)?\s*[a-z]{0,5}\d+[a-z0-9/-]*\b")


def _effective(item: Mapping[str, Any], legal_as_of: date) -> bool:
    start = str(item.get("effective_from") or "")[:10]
    end = str(item.get("effective_to") or "")[:10]
    try:
        if start and legal_as_of < date.fromisoformat(start): return False
        if end and legal_as_of > date.fromisoformat(end): return False
    except ValueError:
        return False
    return True


def _audience_is_allowed(configured: Any, requested: str) -> bool:
    """Apply the public form audience contract consistently.

    Citizen-facing forms are also needed by officers who guide citizens, while
    officer-only material must never be exposed to a citizen.  Older releases
    predominantly bind assets to ``citizen`` only, so exact audience equality
    incorrectly made the officer chatbot lose every published form.
    """

    requested = str(requested or "citizen").strip().casefold()
    if isinstance(configured, str):
        values = {configured.strip().casefold()}
    else:
        values = {
            str(value or "").strip().casefold()
            for value in (configured or [])
            if str(value or "").strip()
        }
    if "both" in values:
        return True
    if requested == "officer":
        return bool(values & {"citizen", "officer"})
    return "citizen" in values


def _tokens(value: str) -> set[str]:
    return set(fold_text(value).split())


_GENERIC_PROCEDURE_PREFIXES = ("thu tuc hanh chinh ve ", "thu tuc ")


def _procedure_label_variants(value: Any) -> tuple[str, ...]:
    folded = fold_text(value)
    if not folded:
        return ()
    variants = [folded]
    for prefix in _GENERIC_PROCEDURE_PREFIXES:
        if folded.startswith(prefix):
            stripped = folded[len(prefix):].strip()
            if stripped:
                variants.append(stripped)
            break
    return tuple(dict.fromkeys(variants))


def _bm25_scores(query: str, documents: list[tuple[str, str]]) -> dict[str, float]:
    query_terms = list(_tokens(query))
    if not query_terms or not documents:
        return {}
    tokenized = [(identity, fold_text(text).split()) for identity, text in documents]
    average_length = sum(len(tokens) for _, tokens in tokenized) / max(1, len(tokenized))
    document_frequency = Counter()
    for _, tokens in tokenized:
        document_frequency.update(set(tokens))
    scores: dict[str, float] = {}
    for identity, tokens in tokenized:
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            frequency_docs = document_frequency[term]
            inverse_frequency = math.log(1 + (len(tokenized) - frequency_docs + 0.5) / (frequency_docs + 0.5))
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(1.0, average_length))
            score += inverse_frequency * frequency * 2.5 / denominator
        scores[identity] = max(scores.get(identity, 0.0), score)
    return scores


def _candidate_payload(procedure: Mapping[str, Any]) -> dict[str, str]:
    return {
        "procedure_id": str(procedure.get("procedure_id") or ""),
        "procedure_code": str(procedure.get("procedure_code") or ""),
        "name": str(procedure.get("name") or procedure.get("canonical_name") or ""),
    }


def _resolve_procedure(question: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    query = fold_text(question)
    procedures = list(manifest.get("procedures") or [])
    aliases = list(manifest.get("aliases") or [])
    explicit_form_code = _explicit_form_code(question)
    form_code_candidate_ids: set[str] = set()
    if explicit_form_code:
        form_code_procedures = _procedures_for_form_code(explicit_form_code, manifest)
        compatible = [
            item for item in form_code_procedures
            if not _domain_conflicts(question, item)
        ]
        if len(compatible) == 1:
            return {
                "status": "resolved",
                "procedure": compatible[0],
                "method": "exact_form_code",
                "confidence": 1.0,
                "confirmed": True,
                "form_code": explicit_form_code.upper(),
            }
        if form_code_procedures and not compatible:
            return {
                "status": "clarification_required",
                "candidates": [],
                "reason": "FORM_PROCEDURE_DOMAIN_MISMATCH",
            }
        if not compatible:
            return {"status": "unsupported", "reason": "FORM_CODE_NOT_IN_RELEASE"}
        # A shared code (for example Mẫu 01/04) is only a candidate filter.
        # Continue through exact procedure name/alias and lexical confirmation.
        form_code_candidate_ids = {
            str(item.get("procedure_id") or "") for item in compatible
        }
    exact: dict[str, dict[str, Any]] = {}
    exact_specificity: dict[str, tuple[int, int, int]] = {}
    blocked: set[str] = set()
    for procedure in procedures:
        for index, value in enumerate((procedure.get("procedure_id"), procedure.get("procedure_code"), procedure.get("name"), procedure.get("canonical_name"))):
            matches = [
                variant for variant in _procedure_label_variants(value)
                if query == variant or f" {variant} " in f" {query} "
            ]
            if matches:
                folded = max(matches, key=lambda item: (len(item.split()), len(item)))
                pid = str(procedure["procedure_id"])
                exact[pid] = procedure
                specificity = (
                    3 if query == folded else (2 if index < 2 else 1),
                    len(folded.split()),
                    len(folded),
                )
                exact_specificity[pid] = max(
                    exact_specificity.get(pid, (0, 0, 0)), specificity
                )
    for alias in aliases:
        variants = _procedure_label_variants(alias.get("alias"))
        pid = str(alias.get("procedure_id") or "")
        if alias.get("alias_kind") in {"exclude", "hard_negative"}:
            if any(query == item or f" {item} " in f" {query} " for item in variants):
                blocked.add(pid)
            continue
        matches = [
            item for item in variants
            if query == item or f" {item} " in f" {query} "
        ]
        if matches:
            folded = max(matches, key=lambda item: (len(item.split()), len(item)))
            found = next((x for x in procedures if str(x.get("procedure_id")) == pid), None)
            if found:
                exact[pid] = found
                specificity = (
                    3 if query == folded else 1,
                    len(folded.split()),
                    len(folded),
                )
                exact_specificity[pid] = max(
                    exact_specificity.get(pid, (0, 0, 0)), specificity
                )
    exact = {
        pid: item for pid, item in exact.items()
        if pid not in blocked and not _domain_conflicts(question, item)
        and (not form_code_candidate_ids or pid in form_code_candidate_ids)
    }
    FOREIGN_MARKERS = ("nuoc ngoai", "quoc te", "nguoi nuoc ngoai", "yeu to nuoc ngoai")
    has_foreign_intent = any(marker in query for marker in FOREIGN_MARKERS)
    if not has_foreign_intent:
        exact_domestic = {
            pid: item for pid, item in exact.items()
            if not any(marker in fold_text(item.get("name") or item.get("canonical_name")) for marker in FOREIGN_MARKERS)
        }
        if exact_domestic:
            exact = exact_domestic
    else:
        exact_foreign = {
            pid: item for pid, item in exact.items()
            if any(marker in fold_text(item.get("name") or item.get("canonical_name")) for marker in FOREIGN_MARKERS)
        }
        if exact_foreign:
            exact = exact_foreign
    exact_specificity = {
        pid: score for pid, score in exact_specificity.items() if pid in exact
    }
    if exact:
        best = max(exact_specificity.values())
        winners = sorted(pid for pid, score in exact_specificity.items() if score == best)
        if len(winners) == 1:
            return {
                "status": "resolved",
                "procedure": exact[winners[0]],
                "method": "exact",
                "confidence": 1.0,
                "confirmed": True,
            }
        return {"status": "clarification_required", "candidates": [_candidate_payload(exact[pid]) for pid in winners], "reason": "FORM_AMBIGUOUS_PROCEDURE"}
    if GENERIC_FORM_RE.search(question) and len(_tokens(question)) <= 8:
        return {
            "status": "clarification_required",
            "candidates": [],
            "reason": "FORM_CODE_REQUIRES_PROCEDURE",
        }
    q = _tokens(question); ranked = []
    # A short generic phrase shared by several procedure names must not be
    # disambiguated by a longer alias belonging to only one of them.
    shared = [
        str(item["procedure_id"])
        for item in procedures
        if q and q.issubset(_tokens(str(item.get("name") or item.get("canonical_name") or "")))
    ]
    if len(q) <= 3 and len(shared) > 1:
        by_id = {str(item["procedure_id"]): item for item in procedures}
        return {"status": "clarification_required", "candidates": [_candidate_payload(by_id[pid]) for pid in sorted(shared)], "reason": "FORM_AMBIGUOUS_PROCEDURE"}

    documents: list[tuple[str, str]] = []
    by_id = {str(item["procedure_id"]): item for item in procedures}
    for procedure in procedures:
        pid = str(procedure["procedure_id"])
        if pid in blocked or (
            form_code_candidate_ids and pid not in form_code_candidate_ids
        ):
            continue
        labels = [procedure.get("name"), procedure.get("canonical_name"), procedure.get("procedure_code")]
        labels += [
            item.get("alias") for item in aliases
            if str(item.get("procedure_id")) == pid
            and item.get("alias_kind") not in {"exclude", "hard_negative"}
        ]
        documents.append((pid, " ".join(str(label or "") for label in labels)))
    bm25 = _bm25_scores(question, documents)
    maximum = max(bm25.values(), default=0.0)
    for pid, raw_score in bm25.items():
        procedure = by_id[pid]
        labels = [
            procedure.get("name"),
            procedure.get("canonical_name"),
            procedure.get("procedure_code"),
            *[
                item.get("alias") for item in aliases
                if str(item.get("procedure_id")) == pid
                and item.get("alias_kind") not in {"exclude", "hard_negative"}
            ],
        ]
        coverage = max(
            (len(q & _tokens(str(label or ""))) / max(1, len(q)) for label in labels),
            default=0.0,
        )
        normalized_bm25 = raw_score / maximum if maximum else 0.0
        score = 0.7 * normalized_bm25 + 0.3 * coverage
        if coverage:
            ranked.append((score, pid, procedure))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    ranked = [item for item in ranked if not _domain_conflicts(question, item[2])]
    # Coverage is the binding signal here.  A fixed floating-point cutoff
    # made the answer depend on an uncalibrated score rather than on whether
    # the official procedure identity matched the request.
    if not ranked:
        if form_code_candidate_ids:
            return {
                "status": "clarification_required",
                "candidates": [
                    _candidate_payload(by_id[pid])
                    for pid in sorted(form_code_candidate_ids)
                    if pid in by_id
                ][:5],
                "reason": "FORM_CODE_REQUIRES_PROCEDURE",
            }
        return {"status": "unsupported", "reason": "FORM_PROCEDURE_NOT_CONFIRMED"}
    if len(ranked) > 1 and ranked[1][0] >= ranked[0][0] - 0.10:
        return {"status": "clarification_required", "candidates": [_candidate_payload(ranked[0][2]), _candidate_payload(ranked[1][2])], "reason": "FORM_AMBIGUOUS_PROCEDURE"}
    return {
        "status": "resolved",
        "procedure": ranked[0][2],
        "method": "bm25",
        "confidence": round(ranked[0][0], 4),
        "confirmed": True,
    }


def resolve_forms(
    *,
    question: str,
    manifest: Mapping[str, Any],
    audience: str = "citizen",
    legal_as_of: date | None = None,
    # Private fast path used by the public catalog.  The catalog already
    # iterates an authoritative procedure from the release manifest, so it
    # must not run the fuzzy identity resolver once per row.  Keeping these
    # keyword-only/private avoids changing the public resolver contract.
    _known_procedure: Mapping[str, Any] | None = None,
    _assets_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    _bindings_by_procedure: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    legal_as_of = legal_as_of or date.today()
    if manifest.get("schema_version") != "form-release-v1": return {"status": "source_gap", "identity_status": "unsupported", "form_status": "source_gap", "reason": "FORM_RELEASE_MANIFEST_INVALID", "recommended_forms": [], "rejected_forms": [], "forms_unavailable": True, "data_gap_status": "FORM_RELEASE_MANIFEST_INVALID", "data_gap_reasons": ["FORM_RELEASE_MANIFEST_INVALID"]}
    if _known_procedure is not None:
        identity = {
            "status": "resolved",
            "procedure": _known_procedure,
            # Match the public contract produced by the normal exact lookup;
            # this is an implementation shortcut, not a new resolution mode.
            "method": "exact",
            "confidence": 1.0,
            "confirmed": True,
        }
    else:
        identity = _resolve_procedure(question, manifest)
    if identity["status"] != "resolved":
        identity_status = "ambiguous" if identity["status"] == "clarification_required" else "unsupported"
        return {**identity, "identity_status": identity_status, "form_status": "not_requested", "recommended_forms": [], "rejected_forms": [], "forms_unavailable": True, "clarifying_questions": (["Bạn muốn thực hiện thủ tục nào?"] if identity["status"] == "clarification_required" else [])}
    if not identity.get("confirmed"):
        return {
            "status": "clarification_required",
            "identity_status": "ambiguous",
            "form_status": "not_requested",
            "reason": "FORM_PROCEDURE_NOT_CONFIRMED",
            "recommended_forms": [],
            "rejected_forms": [],
            "forms_unavailable": True,
            "clarifying_questions": ["Bạn muốn thực hiện thủ tục nào?"],
        }
    procedure = identity["procedure"]; pid = str(procedure["procedure_id"])
    identity_confirmation = {
        "confirmed": True,
        "procedure_id": pid,
        "method": identity.get("method"),
        "confidence": identity.get("confidence", 1.0),
    }
    if procedure.get("coverage_status") == "verified_gap": return {"status": "source_gap", "identity_status": "confirmed", "form_status": "source_gap", "procedure_id": pid, "procedure": procedure, "identity_confirmation": identity_confirmation, "reason": "FORM_VERIFIED_GAP", "recommended_forms": [], "rejected_forms": [], "forms_unavailable": True, "data_gap_status": "FORM_VERIFIED_GAP", "data_gap_reasons": ["FORM_VERIFIED_GAP"]}
    if procedure.get("coverage_status") == "owner_deferred": return {"status": "source_gap", "identity_status": "confirmed", "form_status": "source_gap", "procedure_id": pid, "procedure": procedure, "identity_confirmation": identity_confirmation, "reason": "FORM_OWNER_DEFERRED", "recommended_forms": [], "rejected_forms": [], "forms_unavailable": True, "data_gap_status": "FORM_OWNER_DEFERRED", "data_gap_reasons": ["FORM_OWNER_DEFERRED"]}
    if procedure.get("coverage_status") != "released" or not _effective(procedure, legal_as_of): return {"status": "source_gap", "identity_status": "confirmed", "form_status": "source_gap", "procedure_id": pid, "procedure": procedure, "identity_confirmation": identity_confirmation, "reason": "FORM_PROCEDURE_NOT_EFFECTIVE", "recommended_forms": [], "rejected_forms": [], "forms_unavailable": True, "data_gap_status": "FORM_PROCEDURE_NOT_EFFECTIVE", "data_gap_reasons": ["FORM_PROCEDURE_NOT_EFFECTIVE"]}
    assets = (
        _assets_by_id
        if _assets_by_id is not None
        else {str(x["form_id"]): x for x in manifest.get("assets") or []}
    )
    forms, rejected = [], []
    applicable_bindings = []
    bindings = (
        _bindings_by_procedure.get(pid, [])
        if _bindings_by_procedure is not None
        else manifest.get("bindings") or []
    )
    for binding in bindings:
        if _bindings_by_procedure is None and str(binding.get("procedure_id")) != pid:
            continue
        if binding.get("coverage_status") != "released" or not _audience_is_allowed(binding.get("audience"), audience) or not _effective(binding, legal_as_of):
            continue
        applicable_bindings.append(binding)
        form_id = str(binding.get("form_id") or "")
        asset = assets.get(form_id)
        if not asset or asset.get("coverage_status") != "released" or not _audience_is_allowed(asset.get("audiences"), audience) or not _effective(asset, legal_as_of):
            rejected.append({"form_id": form_id, "reason": "FORM_ASSET_INELIGIBLE"}); continue
        checksum = str(asset.get("source_checksum") or "")
        if not SHA256_RE.fullmatch(checksum) or not is_allowlisted_official_url(str(asset.get("source_url") or "")):
            rejected.append({"form_id": form_id, "reason": "FORM_CHECKSUM_MISMATCH"}); continue
        if asset.get("asset_kind") == "file" and asset.get("runtime_path"):
            expected_download_url = (
                f"/api/procedures/forms-catalog/assets/{form_id}/download"
            )
            if asset.get("download_url") != expected_download_url:
                rejected.append({"form_id": form_id, "reason": "FORM_RUNTIME_ASSET_INELIGIBLE"}); continue
            download_url = expected_download_url
        else:
            download_url = asset.get("download_url") or asset.get("source_url")
        # A current official snapshot proves that the asset is eligible at the
        # snapshot date; it does not prove that this was its legal start date.
        # Keep the two concepts separate to avoid telling users that a form
        # "takes effect" on the date it was merely verified.
        effective_from = binding.get("effective_from") or asset.get("effective_from") or procedure.get("effective_from")
        verified_as_of = manifest.get("legal_as_of") or legal_as_of.isoformat()
        effective_to = binding.get("effective_to") or asset.get("effective_to") or procedure.get("effective_to")
        legal_basis = binding.get("legal_basis") or asset.get("issuing_instrument")
        if isinstance(legal_basis, str):
            legal_basis = [legal_basis] if legal_basis.strip() else []
        forms.append({"name": asset.get("canonical_name"), "display_name": asset.get("canonical_name"), "file_type": "eform" if asset.get("asset_kind") == "eform" else str(asset.get("file_format") or asset.get("file_type") or "file"), "download_url": download_url, "official_level": "official", "review_status": "approved", "has_official_file": asset.get("asset_kind") == "file", "has_official_resource": True, "procedure_id": pid, "procedure_name": procedure.get("name") or procedure.get("canonical_name"), "procedure_source_url": procedure.get("official_source_url"), "form_id": form_id, "form_code": asset.get("form_code"), "audience": binding.get("audience"), "required_or_conditional": binding.get("requirement"), "condition": binding.get("condition"), "legal_basis": list(legal_basis or []), "source_url": asset.get("source_url"), "source_checksum": checksum, "asset_kind": asset.get("asset_kind"), "effective_from": effective_from, "effective_to": effective_to, "verified_as_of": verified_as_of, "form_binding_verified": True, "form_checksum_verified": True, "procedure_identity_confirmed": True, "procedure_identity_method": identity.get("method")})
    forms.sort(key=lambda x: (0 if x["required_or_conditional"] == "required" else 1, x.get("form_code") or "", x["form_id"]))
    if not applicable_bindings or rejected or len(forms) != len(applicable_bindings):
        return {"status": "source_gap", "identity_status": "confirmed", "form_status": "source_gap", "procedure_id": pid, "procedure": procedure, "identity_confirmation": identity_confirmation, "reason": "FORM_SOURCE_GAP", "recommended_forms": [], "rejected_forms": rejected, "rejected": rejected, "forms_unavailable": True, "data_gap_status": "FORM_SOURCE_GAP", "data_gap_reasons": ["FORM_SOURCE_GAP"]}
    return {"status": "resolved", "identity_status": "confirmed", "form_status": "resolved", "procedure_id": pid, "resolution_method": identity["method"], "identity_confirmation": identity_confirmation, "recommended_forms": forms, "forms_unavailable": False, "data_gap_status": None, "data_gap_reasons": [], "evidence_packet": {"release_id": manifest.get("release_id"), "legal_as_of": legal_as_of.isoformat(), "procedure": {"procedure_id": pid, "name": procedure.get("name") or procedure.get("canonical_name"), "domain": procedure.get("domain"), "authority": procedure.get("authority"), "official_source_url": procedure.get("official_source_url"), "coverage_status": procedure.get("coverage_status")}, "identity_confirmation": identity_confirmation, "forms": [{key: item.get(key) for key in ("form_id", "form_code", "name", "asset_kind", "download_url", "source_url", "source_checksum", "required_or_conditional", "condition", "effective_from", "effective_to", "verified_as_of", "procedure_identity_confirmed")} for item in forms], "form_ids": [x["form_id"] for x in forms], "provider_may_modify_form_identity": False}, "rejected_forms": rejected, "rejected": rejected}


def public_catalog_from_manifest(
    manifest: Mapping[str, Any],
    *,
    audience: str = "citizen",
    legal_as_of: date | None = None,
) -> dict[str, Any]:
    """Project the active release for the public procedure catalog.

    The projection deliberately calls the same deterministic resolver used by
    chat.  A withdrawn or replaced binding therefore disappears from both
    surfaces as soon as the active release pointer changes; no legacy FAQ or
    procedure-record form can silently re-introduce it.
    """

    legal_as_of = legal_as_of or date.today()
    if manifest.get("schema_version") != "form-release-v1":
        raise ValueError("FORM_RELEASE_MANIFEST_INVALID")

    # Index the release once.  Resolving a catalog row is identity-confirmed
    # already, so scanning every procedure/alias and every binding for each
    # row only adds quadratic work to the public page.
    assets_by_id: dict[str, Mapping[str, Any]] = {
        str(item.get("form_id")): item
        for item in manifest.get("assets") or []
        if isinstance(item, Mapping) and item.get("form_id")
    }
    bindings_by_procedure: dict[str, list[Mapping[str, Any]]] = {}
    for binding in manifest.get("bindings") or []:
        if not isinstance(binding, Mapping):
            continue
        bindings_by_procedure.setdefault(
            str(binding.get("procedure_id") or ""), []
        ).append(binding)

    items: list[dict[str, Any]] = []
    for raw in manifest.get("procedures") or []:
        if not isinstance(raw, Mapping):
            continue
        procedure_id = str(raw.get("procedure_id") or "").strip()
        if not procedure_id:
            continue
        resolved = resolve_forms(
            question=procedure_id,
            manifest=manifest,
            audience=audience,
            legal_as_of=legal_as_of,
            _known_procedure=raw,
            _assets_by_id=assets_by_id,
            _bindings_by_procedure=bindings_by_procedure,
        )
        # A release procedure identifier is itself an authoritative identity.
        # If a malformed manifest makes it ambiguous, expose no forms and make
        # the gap explicit instead of guessing a binding.
        forms = [
            dict(item)
            for item in resolved.get("recommended_forms") or []
            if isinstance(item, Mapping)
        ]
        items.append(
            {
                "procedure_id": procedure_id,
                "procedure_code": raw.get("procedure_code") or procedure_id,
                "name": raw.get("name") or raw.get("canonical_name") or procedure_id,
                "domain": raw.get("domain") or "hanh_chinh_cong",
                "authority": raw.get("authority") or raw.get("department"),
                "official_source_url": raw.get("official_source_url"),
                "coverage_status": raw.get("coverage_status"),
                "primary_organization_unit_id": raw.get(
                    "primary_organization_unit_id"
                ),
                "primary_organization_unit_name": raw.get(
                    "primary_organization_unit_name"
                ),
                "supporting_organization_unit_ids": list(
                    raw.get("supporting_organization_unit_ids") or []
                ),
                "form_status": resolved.get("form_status") or "source_gap",
                "forms_unavailable": bool(
                    resolved.get("forms_unavailable", not forms)
                ),
                "reason": resolved.get("reason"),
                "data_gap_reasons": list(
                    resolved.get("data_gap_reasons") or []
                ),
                "forms": forms,
            }
        )
    return {
        "schema_version": "public-form-catalog-v1",
        "release_id": manifest.get("release_id"),
        "version": manifest.get("version"),
        "legal_as_of": legal_as_of.isoformat(),
        "audience": audience,
        "items": items,
        "total": len(items),
    }


def resolve_from_configured_release(*, question: str, audience: str, legal_as_of: date) -> dict[str, Any] | None:
    """Resolve only when Feature 017 shadow/active mode is explicitly set.

    The default returns ``None`` so existing JSON behavior is untouched.  Both
    shadow and active read the canonical active release; callers decide whether
    to observe or serve the result.
    """
    import os
    mode = str(os.getenv("FORM_GOVERNANCE_ROUTER_MODE") or "disabled").casefold()
    if mode not in {"shadow", "active"}: return None
    from api.form_governance_service import get_form_governance_service
    repository = get_form_governance_service().repository
    if mode == "active":
        rollout_roles = {
            item.strip().casefold()
            for item in str(
                os.getenv("FORM_GOVERNANCE_ROLLOUT_ROLES") or "citizen"
            ).split(",")
            if item.strip()
        }
        if audience.casefold() not in rollout_roles:
            return None
    release = repository.active_release()
    if not release and mode == "shadow":
        shadow_release_id = str(
            os.getenv("FORM_GOVERNANCE_SHADOW_RELEASE_ID") or ""
        ).strip()
        if shadow_release_id:
            candidate = repository.get_release(shadow_release_id)
            if candidate and candidate.get("status") == "validated":
                release = candidate
    if not release: return {"status":"source_gap","reason":"FORM_RELEASE_NOT_FOUND","recommended_forms":[],"router_mode":mode}
    manifest = release.get("manifest") or release
    return {**resolve_forms(question=question, manifest=manifest, audience=audience, legal_as_of=legal_as_of), "router_mode":mode}
