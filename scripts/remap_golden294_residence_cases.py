"""Remap the 90 approved residence Golden cases to 116/2026/TT-BCA.

This is deliberately a case-level migration, not a law-number replacement.
The successor instrument reorganised provisions, consolidated forms, removed
district-level responsibilities and changed online residence procedures.  The
mapping below therefore points each reviewed case to the exact current article
and provision text stored in PostgreSQL.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping, Sequence
import unicodedata

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_legal_retrieval import _database_url


APPROVAL_PHRASE = "Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca"
CURRENT_LAW = "116/2026/TT-BCA"
CURRENT_DOCUMENT_ID = 127597
CURRENT_TITLE = "Quy định chi tiết một số điều và biện pháp thi hành Luật Cư trú"
OFFICIAL_URL = (
    "https://vbpl.vn/van-ban/chi-tiet/"
    "thong-tu-so-116-2026-tt-bca-quy-dinh-chi-tiet-mot-so-dieu-va-"
    "bien-phap-thi-hanh-luat-cu-tru--a8024a50-83f3-11f1-9f42-03eb144ddbd7"
)
LEGAL_AS_OF = "2026-08-11"
OLD_LAWS = {"55/2021/TT-BCA", "66/2023/TT-BCA"}


# value: (current article number, zero-based paragraph indexes).  ``None``
# means the complete current article and is used by full-article tests.
CASE_RULES: dict[str, tuple[str, tuple[int, ...] | None]] = {
    # Former Article 3 / receiving and resolving residence files.
    "golden-0403": ("3", (0,)),
    "golden-0407": ("3", (5,)),
    "golden-0411": ("3", (5,)),
    "golden-0415": ("3", (2,)),
    "golden-0419": ("3", (5,)),
    "golden-0423": ("3", (7,)),
    "golden-0427": ("3", (8,)),
    "golden-0523": ("3", None),
    # Former Article 4 / feedback about residence.
    "golden-0431": ("4", (0,)),
    "golden-0435": ("4", (1,)),
    "golden-0439": ("4", (3,)),
    "golden-0443": ("4", (4,)),
    "golden-0447": ("4", (5,)),
    "golden-0451": ("4", (7,)),
    "golden-0455": ("4", (7,)),
    "golden-0459": ("4", (8,)),
    "golden-0527": ("4", None),
    # Temporary restriction on changing residence.
    "golden-0463": ("5", (0,)),
    "golden-0467": ("5", (1,)),
    "golden-0471": ("5", (2,)),
    "golden-0531": ("5", None),
    # Household relationship and residence-data handling.
    "golden-0475": ("6", None),
    "golden-0479": ("6", (1,)),
    "golden-0483": ("6", (1,)),
    "golden-0487": ("6", (2,)),
    "golden-0491": ("6", (0,)),
    "golden-0495": ("6", (0,)),
    "golden-0535": ("6", None),
    # Permanent residence at religious/social-assistance facilities.
    "golden-0499": ("7", (0,)),
    "golden-0503": ("7", (1,)),
    "golden-0507": ("7", (2,)),
    "golden-0539": ("7", None),
    "golden-0543": ("8", None),
    "golden-0546": ("9", None),
    # Adjustment of residence information.
    "golden-0510": ("10", (0,)),
    "golden-0513": ("10", (1,)),
    "golden-0516": ("10", (2,)),
    "golden-0519": ("10", (3,)),
    "golden-0549": ("10", None),
    "golden-0551": ("10", (4,)),
    # Staying notification: former Article 15 -> current Article 14.
    "golden-0553": ("14", (0, 1, 2, 3)),
    "golden-0554": ("14", (1,)),
    "golden-0556": ("14", (1,)),
    "golden-0557": ("14", (2,)),
    "golden-0559": ("14", (4,)),
    "golden-0560": ("14", (5, 6, 7)),
    "golden-0562": ("14", (8,)),
    "golden-0563": ("14", (8,)),
    # Temporary absence: former Article 16 -> current Article 15.
    "golden-0565": ("15", (3, 4)),
    "golden-0566": ("15", (4,)),
    "golden-0568": ("15", (4,)),
    "golden-0569": ("15", (4,)),
    "golden-0571": ("15", (7,)),
    # Residence confirmation: former Article 17 -> current Article 16.
    "golden-0574": ("16", (0,)),
    "golden-0577": ("16", (4,)),
    "golden-0580": ("16", (4,)),
    "golden-0583": ("16", (4,)),
    "golden-0586": ("16", (5,)),
    "golden-0589": ("16", (5,)),
    # 66/2023 amendment cases parsed under its embedded Article 17.
    "golden-0404": ("16", (0,)),
    "golden-0408": ("16", (4,)),
    "golden-0412": ("16", (4,)),
    "golden-0416": ("16", (4,)),
    "golden-0420": ("16", (5,)),
    "golden-0424": ("16", (5,)),
    "golden-0428": ("16", (5,)),
    "golden-0432": ("23", (1,)),
    "golden-0436": ("24", (6,)),
    "golden-0440": ("25", (1, 2)),
    "golden-0444": ("14", (0, 1, 2, 3)),
    "golden-0448": ("9", (0,)),
    "golden-0540": ("16", None),
    # 66/2023 form amendments -> current consolidated form rules.
    "golden-0524": ("3", None),
    "golden-0452": ("20", (1,)),
    "golden-0456": ("20", (2,)),
    "golden-0460": ("21", (9,)),
    "golden-0464": ("21", (10,)),
    "golden-0528": ("21", None),
    "golden-0468": ("20", (1,)),
    "golden-0472": ("20", (2,)),
    "golden-0476": ("20", (3,)),
    "golden-0480": ("20", (4,)),
    "golden-0484": ("20", (5,)),
    "golden-0488": ("20", (5,)),
    "golden-0492": ("20", (6,)),
    "golden-0496": ("20", (7,)),
    "golden-0500": ("20", (8,)),
    "golden-0504": ("20", (9,)),
    "golden-0532": ("20", None),
    # Responsibility for implementation.
    "golden-0536": ("28", None),
}


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_article(article_number: str, content: str) -> str:
    value = content.strip()
    markers = ["\n\nChương "]
    if article_number == "28":
        markers.extend(["| Nơi nhận:", "Mẫu CT01 ban hành kèm theo"])
    indexes = [value.find(marker) for marker in markers if value.find(marker) >= 0]
    if indexes:
        value = value[: min(indexes)].rstrip()
    return value


def load_current_articles() -> dict[str, dict[str, str]]:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT a.article_number, a.title, a.content
                FROM legal_articles a
                WHERE a.document_id = :document_id
                  AND a.id BETWEEN 930623 AND 930650
                ORDER BY a.id
                """
            ),
            {"document_id": CURRENT_DOCUMENT_ID},
        ).mappings().all()
    engine.dispose()
    articles = {
        str(row["article_number"]): {
            "title": str(row["title"]),
            "content": _clean_article(
                str(row["article_number"]), str(row["content"])
            ),
        }
        for row in rows
    }
    required = {article for article, _ in CASE_RULES.values()}
    if not required <= set(articles):
        raise RuntimeError(
            f"GOLDEN294_RESIDENCE_CURRENT_ARTICLES_MISSING:{sorted(required-set(articles))}"
        )
    return articles


def _selected_text(content: str, selectors: tuple[int, ...] | None) -> str:
    if selectors is None:
        return content
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    if not selectors or max(selectors) >= len(paragraphs):
        raise ValueError("GOLDEN294_RESIDENCE_PARAGRAPH_SELECTOR_INVALID")
    if tuple(range(min(selectors), max(selectors) + 1)) != selectors:
        raise ValueError("GOLDEN294_RESIDENCE_PARAGRAPH_SELECTOR_NOT_CONTIGUOUS")
    return "\n\n".join(paragraphs[index] for index in selectors)


def _source_claim_indexes(case: Mapping[str, Any], source: Mapping[str, Any]) -> list[int]:
    claims = list(case.get("required_claims") or [])
    sources = list(case.get("expected_sources") or [])
    quote = _normalise(str((source.get("proof") or {}).get("quote") or ""))
    matches = [
        index
        for index, claim in enumerate(claims)
        if _normalise(str(claim.get("text") or "")) in quote
    ]
    if len(sources) == 1:
        return list(range(len(claims)))
    if len(matches) != 1:
        raise ValueError(
            f"GOLDEN294_RESIDENCE_MULTI_ISSUE_CLAIM_AMBIGUOUS:{case.get('case_id')}"
        )
    return matches


def _short(value: str, limit: int = 190) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _question_for_case(
    case: Mapping[str, Any],
    full_article: bool,
    *,
    original_question: str = "",
    old_source: Mapping[str, Any] | None = None,
    article_number: str = "",
    evidence: str = "",
) -> str:
    sources = list(case.get("expected_sources") or [])
    claims = list(case.get("required_claims") or [])
    if len(sources) == 1:
        if old_source is None or not original_question.strip():
            raise ValueError("GOLDEN294_RESIDENCE_ORIGINAL_QUESTION_REQUIRED")
        old_article = re.escape(str(old_source.get("article") or ""))
        # Preserve the human-approved scenario wording so cases that test the
        # same successor provision remain distinct.  Remove obsolete exact
        # identifiers before adding one unambiguous current Article+law pair.
        remapped = original_question
        for obsolete_law in OLD_LAWS:
            remapped = remapped.replace(
                obsolete_law,
                "quy định cư trú áp dụng trước ngày 01/07/2026",
            )
        if old_article:
            remapped = re.sub(
                rf"\bĐiều\s+{old_article}\b",
                "quy định tương ứng",
                remapped,
                flags=re.IGNORECASE,
            )
        remapped = " ".join(remapped.split())
        instruction = (
            f"Căn cứ hiện hành bắt buộc dùng là Điều {article_number} "
            f"{CURRENT_LAW}"
        )
        if full_article:
            instruction += (
                "; hãy nạp đủ điều, giữ đúng thứ tự khoản/điểm và báo rõ nếu "
                "thiếu bất kỳ phần nào."
            )
        else:
            instruction += (
                f", đối chiếu nội dung “{_short(evidence)}” và giải thích ngắn "
                "gọn, không suy diễn thêm."
            )
        return f"{remapped} {instruction}"

    issues: list[str] = []
    for position, source in enumerate(sources, start=1):
        quote = _normalise(str((source.get("proof") or {}).get("quote") or ""))
        matching = [
            claim
            for claim in claims
            if _normalise(str(claim.get("text") or "")) in quote
        ]
        if not matching:
            raise ValueError(
                f"GOLDEN294_RESIDENCE_QUESTION_SOURCE_CLAIM_MISSING:{case.get('case_id')}"
            )
        issues.append(
            f"({position}) Điều {source.get('article')} {source.get('law_number')}: "
            f"{_short(str(matching[0].get('text') or ''), 150)}"
        )
    return (
        "Tôi đồng thời cần xử lý các vấn đề sau theo đúng thứ tự: "
        + "; ".join(issues)
        + ". Xin tách riêng từng vấn đề, nêu đúng quy định hiện hành và nguồn tương ứng."
    )


def target_case_ids(dataset: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for case in dataset.get("cases") or []:
        if any(
            str(source.get("law_number") or "") in OLD_LAWS
            for source in case.get("expected_sources") or []
        ):
            result.add(str(case.get("case_id") or ""))
    return result


def transform_dataset(
    dataset: Mapping[str, Any], articles: Mapping[str, Mapping[str, str]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    targets = target_case_ids(dataset)
    if targets != set(CASE_RULES):
        raise ValueError(
            "GOLDEN294_RESIDENCE_CASE_SET_MISMATCH:"
            f"missing={sorted(set(CASE_RULES)-targets)}:extra={sorted(targets-set(CASE_RULES))}"
        )
    transformed = deepcopy(dict(dataset))
    changes: list[dict[str, Any]] = []
    for case in transformed.get("cases") or []:
        case_id = str(case.get("case_id") or "")
        if case_id not in CASE_RULES:
            continue
        article_number, selectors = CASE_RULES[case_id]
        article = articles[article_number]
        article_content = str(article["content"])
        evidence = _selected_text(article_content, selectors)
        char_start = article_content.find(evidence)
        if char_start < 0:
            raise ValueError(f"GOLDEN294_RESIDENCE_EVIDENCE_NOT_EXACT:{case_id}")
        old_sources = [
            source
            for source in case.get("expected_sources") or []
            if str(source.get("law_number") or "") in OLD_LAWS
        ]
        if len(old_sources) != 1:
            raise ValueError(f"GOLDEN294_RESIDENCE_OLD_SOURCE_COUNT:{case_id}")
        old_source = old_sources[0]
        claim_indexes = _source_claim_indexes(case, old_source)
        original_citizen_question = str(
            (case.get("questions") or {}).get("citizen") or ""
        )

        # Golden v2 deliberately keeps a small source contract.  Physical
        # proof is an exact quote plus deterministic character offsets; URL and
        # checked-at provenance live in the remap report, not as undeclared
        # fields inside each Golden source.
        source = {
            key: deepcopy(old_source.get(key))
            for key in ("law_number", "article", "clause", "point", "reason")
        }
        source.update(
            {
                "law_number": CURRENT_LAW,
                "article": article_number,
                "clause": None,
                "point": None,
                "reason": (
                    f"Điều {article_number} {CURRENT_LAW} - nguồn hiện hành đã được "
                    "đối chiếu tại ngày chốt."
                ),
                "proof": {
                    "quote": evidence,
                    "page_number": None,
                    "char_start": char_start,
                    "char_end": char_start + len(evidence),
                },
            }
        )
        case["expected_sources"] = [
            source if candidate is old_source else candidate
            for candidate in case.get("expected_sources") or []
        ]

        claims = list(case.get("required_claims") or [])
        template = deepcopy(claims[min(claim_indexes)])
        template.update({"text": evidence, "critical": True})
        first_index = min(claim_indexes)
        claims = [
            claim
            for index, claim in enumerate(claims)
            if index not in set(claim_indexes)
        ]
        claims.insert(first_index, template)
        for order, claim in enumerate(claims, start=1):
            claim["order"] = order
        case["required_claims"] = claims
        case["procedure_family"] = str(article["title"])
        case["legal_as_of"] = LEGAL_AS_OF
        tags = list(case.get("risk_tags") or [])
        if "residence_successor_remap_116_2026" not in tags:
            tags.append("residence_successor_remap_116_2026")
        case["risk_tags"] = tags
        case["review_status"] = "approved"
        if "unresolved_reason" in case:
            case["unresolved_reason"] = None
        questions = dict(case.get("questions") or {})
        questions["citizen"] = _question_for_case(
            case,
            selectors is None,
            original_question=original_citizen_question,
            old_source=old_source,
            article_number=article_number,
            evidence=evidence,
        )
        case["questions"] = questions
        changes.append(
            {
                "case_id": case_id,
                "old_law": old_source.get("law_number"),
                "old_article": old_source.get("article"),
                "new_law": CURRENT_LAW,
                "new_article": article_number,
                "full_article": selectors is None,
                "proof_characters": len(evidence),
            }
        )

    transformed["legal_as_of"] = LEGAL_AS_OF
    if "generated_at" in transformed:
        transformed["generated_at"] = datetime.now(timezone.utc).isoformat()
    return transformed, changes


def validate_transformed(
    dataset: Mapping[str, Any], articles: Mapping[str, Mapping[str, str]]
) -> dict[str, Any]:
    cases = list(dataset.get("cases") or [])
    if len(cases) != 1000 or len({case.get("case_id") for case in cases}) != 1000:
        raise ValueError("GOLDEN294_RESIDENCE_DATASET_CARDINALITY")
    seen_questions: dict[str, str] = {}
    for case in cases:
        for role, question in (case.get("questions") or {}).items():
            if not question:
                continue
            normalized = _normalise(str(question))
            previous = seen_questions.get(normalized)
            if previous:
                raise ValueError(
                    "GOLDEN294_RESIDENCE_DUPLICATE_QUESTION:"
                    f"{previous}:{case.get('case_id')}:{role}"
                )
            seen_questions[normalized] = f"{case.get('case_id')}:{role}"
    targets = [case for case in cases if str(case.get("case_id")) in CASE_RULES]
    if len(targets) != 90:
        raise ValueError("GOLDEN294_RESIDENCE_TARGET_CARDINALITY")
    law_counts: dict[str, int] = {}
    for case in targets:
        serialised = json.dumps(case, ensure_ascii=False)
        if any(old_law in serialised for old_law in OLD_LAWS):
            raise ValueError(f"GOLDEN294_RESIDENCE_STALE_SOURCE:{case.get('case_id')}")
        current_sources = [
            source
            for source in case.get("expected_sources") or []
            if source.get("law_number") == CURRENT_LAW
        ]
        if len(current_sources) != 1:
            raise ValueError(f"GOLDEN294_RESIDENCE_CURRENT_SOURCE_COUNT:{case.get('case_id')}")
        source = current_sources[0]
        article_number = str(source.get("article") or "")
        proof = source.get("proof") or {}
        quote = str(proof.get("quote") or "")
        content = str(articles[article_number]["content"])
        start = int(proof.get("char_start"))
        end = int(proof.get("char_end"))
        if content[start:end] != quote or not quote.strip():
            raise ValueError(f"GOLDEN294_RESIDENCE_PROOF_OFFSET:{case.get('case_id')}")
        if not any(
            _normalise(str(claim.get("text") or "")) in _normalise(quote)
            for claim in case.get("required_claims") or []
        ):
            raise ValueError(f"GOLDEN294_RESIDENCE_CLAIM_NOT_GROUNDED:{case.get('case_id')}")
        law_counts[article_number] = law_counts.get(article_number, 0) + 1
    return {
        "case_count": len(cases),
        "remapped_case_count": len(targets),
        "remaining_old_source_count": 0,
        "current_article_distribution": dict(sorted(law_counts.items(), key=lambda x: int(x[0]))),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target-input", type=Path)
    parser.add_argument("--target-canonical", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args()
    if args.apply and args.approval != APPROVAL_PHRASE:
        raise SystemExit("GOLDEN294_RESIDENCE_APPROVAL_REQUIRED")

    articles = load_current_articles()
    source_document = json.loads(args.input.read_text(encoding="utf-8"))
    canonical_document = json.loads(args.canonical.read_text(encoding="utf-8"))
    if target_case_ids(source_document) != target_case_ids(canonical_document):
        raise SystemExit("GOLDEN294_RESIDENCE_CANONICAL_TARGET_SET_MISMATCH")

    transformed, changes = transform_dataset(source_document, articles)
    transformed_canonical, canonical_changes = transform_dataset(
        canonical_document, articles
    )
    validation = validate_transformed(transformed, articles)
    canonical_validation = validate_transformed(transformed_canonical, articles)
    _write_json(args.output.resolve(), transformed)

    report: dict[str, Any] = {
        "schema_version": "golden294-residence-remap-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "preview",
        "approval": args.approval if args.apply else None,
        "input": str(args.input.resolve()),
        "canonical": str(args.canonical.resolve()),
        "proposal": str(args.output.resolve()),
        "before_sha256": {
            "input": _sha256(args.input.resolve()),
            "canonical": _sha256(args.canonical.resolve()),
        },
        "validation": validation,
        "canonical_validation": canonical_validation,
        "changes": changes,
        "canonical_change_count": len(canonical_changes),
    }

    if args.apply:
        target_input = (args.target_input or args.input).resolve()
        target_canonical = (args.target_canonical or args.canonical).resolve()
        if not target_input.is_file() or not target_canonical.is_file():
            raise SystemExit("GOLDEN294_RESIDENCE_APPLY_TARGET_MISSING")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = ROOT / "backups" / "golden294-live" / f"residence-remap-{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        input_backup = backup_dir / target_input.name
        canonical_backup = backup_dir / target_canonical.name
        shutil.copy2(target_input, input_backup)
        shutil.copy2(target_canonical, canonical_backup)
        _write_json(target_input, transformed)
        _write_json(target_canonical, transformed_canonical)
        report.update(
            {
                "status": "applied",
                "backup_dir": str(backup_dir),
                "apply_targets": {
                    "input": str(target_input),
                    "canonical": str(target_canonical),
                },
                "after_sha256": {
                    "input": _sha256(target_input),
                    "canonical": _sha256(target_canonical),
                },
            }
        )
    _write_json(args.report.resolve(), report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "remapped_case_count": validation["remapped_case_count"],
                "remaining_old_source_count": validation["remaining_old_source_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
