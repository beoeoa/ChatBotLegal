import asyncio
import operator
import os
import re
import time
import unicodedata
from typing import Annotated, List, Optional

import httpx
from ai_prompter import Prompter
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from loguru import logger
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.exceptions import (
    LegalRetrievalUnavailableError,
    OpenNotebookError,
)
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content
from api.legal_retrieval_policy import expanded_retrieval_reason
from api.legal_answer_quality import build_evidence_coverage
from api.legal_grounding import validate_legal_references
from api.legal_search_client import get_legal_search_client
from api.legal_section_grounding import (
    LegalIssue,
    classify_issue_domain,
    is_section_grounding_enabled,
    select_eligible_evidence,
)
from api.observability import telemetry
from api.legal_question_policy import (
    answer_contract,
    missing_contract_headings,
    normalize_answer_markdown,
    render_answer_contract,
)


def _llm_timeout_seconds() -> float:
    try:
        return max(
            30.0,
            float(os.getenv("LEGAL_LLM_TIMEOUT_SECONDS", "180")),
        )
    except ValueError:
        return 180.0


class SubGraphState(TypedDict):
    question: str
    role: str
    term: str
    instructions: str
    results: str
    answer: str
    ids: list
    domain: Optional[str]
    question_type: Optional[str]
    required_sections: list[str]
    legal_as_of: Optional[str]
    retrieval_question: Optional[str]
    request_id: Optional[str]
    issue_id: Optional[str]
    issue_domain: Optional[str]



class Search(BaseModel):
    term: str
    instructions: str = Field(
        description="Tell the answering LLM what information to extract"
    )


class Strategy(BaseModel):
    reasoning: str
    searches: List[Search] = Field(
        default_factory=list,
        description="You can add up to five searches to this strategy",
    )


class ThreadState(TypedDict):
    question: str
    role: str
    strategy: Strategy
    answers: Annotated[list, operator.add]
    completion_flags: Annotated[list, operator.add]
    evidence: Annotated[list, operator.add]
    final_answer: str
    domain: Optional[str]
    question_type: Optional[str]
    required_sections: list[str]
    legal_as_of: Optional[str]
    # Feature 005 keeps prior conversation text available for generation while
    # retrieval receives only the current request question.
    retrieval_question: Optional[str]
    request_id: Optional[str]
    issue_id: Optional[str]
    issue_domain: Optional[str]



def _fallback_strategy(question: str) -> Strategy:
    return Strategy(
        reasoning=(
            "Mo hinh khong tao duoc chien luoc co cau truc; "
            "tra cuu truc tiep theo cau hoi goc."
        ),
        searches=[
            Search(
                term=question,
                instructions=(
                    "Tim can cu phap ly con hieu luc lien quan truc tiep; "
                    "uu tien van ban Hai Phong nhung tuan thu thu bac phap ly "
                    "cua van ban Trung uong."
                ),
            )
        ],
    )


async def call_model_with_messages(
    state: ThreadState, config: RunnableConfig
) -> dict:
    # Legal retrieval already embeds the complete question with VNLegal-LAL.
    # Avoid spending a separate LLM call to paraphrase the same query.
    return {"strategy": _fallback_strategy(state["question"])}


async def trigger_queries(state: ThreadState, config: RunnableConfig):
    return [
        Send(
            "provide_answer",
            {
                "question": state["question"],
                "role": state["role"],
                "instructions": search.instructions,
                "term": search.term,
                "domain": state.get("domain"),
                "question_type": state.get("question_type"),
                "required_sections": state.get("required_sections") or [],
                "legal_as_of": state.get("legal_as_of"),
                "retrieval_question": state.get("retrieval_question"),
                "request_id": state.get("request_id"),
                "issue_id": state.get("issue_id"),
                "issue_domain": state.get("issue_domain"),
            },
        )
        for search in state["strategy"].searches
    ]


def _filter_by_applicability_tags(question: str, results: list[dict]) -> list[dict]:
    """Apply only explicit retrieval metadata; never encode a law-specific rule.

    Sources without applicability metadata are kept for the grounding layer.
    This prevents a hard-coded document number from silently overriding the
    corpus while still honoring reviewed domestic/foreign scope tags.
    """
    folded_question = _normalize_grounding_text(question)
    requested_scope = (
        "foreign"
        if any(
            phrase in folded_question
            for phrase in (
                "nuoc ngoai",
                "ngoai nuoc",
                "nguoi nuoc ngoai",
                "quoc tich nuoc ngoai",
            )
        )
        else "domestic"
    )
    filtered: list[dict] = []
    for result in results:
        raw_tags = result.get("applicability_tags") or result.get("scope_tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = {_normalize_grounding_text(tag) for tag in raw_tags if tag}
        if tags and {"domestic", "foreign"}.intersection(tags) and requested_scope not in tags:
            continue
        filtered.append(result)
    return filtered


async def provide_answer(
    state: SubGraphState, config: RunnableConfig
) -> dict:
    try:
        search_started = time.perf_counter()
        results = await _legal_search(
            state.get("retrieval_question") or state["term"],
            state.get("domain"),
            state.get("legal_as_of"),
            request_id=state.get("request_id"),
            issue_id=state.get("issue_id"),
            issue_domain=state.get("issue_domain"),
        )
        telemetry.record_ask_stage(
            "retrieval",
            duration_ms=(time.perf_counter() - search_started) * 1000,
        )
        results = _filter_by_applicability_tags(state["question"], results)
        # Feature 005 is opt-in until its quality gate is signed off. When it
        # is enabled, candidates must match the exact current request and
        # issue before any prompt/validator can see them. Conversation context
        # remains in ``question`` for generation only.
        if (
            is_section_grounding_enabled()
            and state.get("request_id")
            and state.get("issue_id")
        ):
            issue = LegalIssue(
                request_id=str(state["request_id"]),
                issue_id=str(state["issue_id"]),
                text=str(state.get("retrieval_question") or state["term"]),
                domain=classify_issue_domain(
                    str(state.get("retrieval_question") or state["term"])
                ),
            )
            results = select_eligible_evidence(
                results,
                issue,
                legal_as_of=state.get("legal_as_of"),
            )
        logger.info(
            "Legal retrieval completed in {:.2f}s with {} results",
            time.perf_counter() - search_started,
            len(results),
        )
        payload = dict(state)
        
        # Match only byte-verified official Hai Phong form source packages.
        form_context = ""
        try:
            from api.routers.ward_procedures import build_official_form_context

            form_context = build_official_form_context(
                str(state["question"]),
                domain=state.get("domain"),
            )
        except Exception as e:
            logger.warning(f"Failed to load matching forms for context: {str(e)}")

        payload["results"] = (
            _format_evidence_for_prompt(results) + ("\n\n" + form_context if form_context else "")
            if results
            else (
                "Không tìm thấy đoạn nguồn pháp luật phù hợp trong dữ liệu hiện có. Không được tự tạo căn cứ pháp luật." 
                + ("\n\n" + form_context if form_context else "")
            )
        )
        payload["ids"] = [result["id"] for result in results]
        payload["evidence_coverage"] = build_evidence_coverage(
            results,
            required_sections=state.get("required_sections") or [],
        )
        payload["answer_contract"] = render_answer_contract(
            answer_contract(
                state.get("role") or "citizen",
                state.get("question_type"),
                state.get("required_sections") or [],
            )
        )
        system_prompt = Prompter(prompt_template="ask/query_process").render(
            data=payload
        )
        # Citizen answers use one compact generation pass. Grounding and
        # citation validation still run after generation; officer/admin keep
        # the larger response budget for procedural analysis.
        answer_max_tokens = 4096 if state.get("role") == "citizen" else 16000
        model = await provision_langchain_model(
            system_prompt,
            config.get("configurable", {}).get("answer_model"),
            "tools",
            max_tokens=answer_max_tokens,
        )
        llm_started = time.perf_counter()
        try:
            ai_message = await asyncio.wait_for(
                model.ainvoke(system_prompt),
                timeout=_llm_timeout_seconds(),
            )
        finally:
            logger.info(
                "Legal answer model finished after {:.2f}s; prompt_chars={}",
                time.perf_counter() - llm_started,
                len(system_prompt),
            )
        raw_answer = clean_thinking_content(
            extract_text_content(ai_message.content)
        )
        telemetry.record_ask_stage(
            "generation",
            duration_ms=(time.perf_counter() - llm_started) * 1000,
        )
        answer_complete = _has_completion_marker(raw_answer)
        answer = _normalize_answer_format(
            _remove_completion_marker(raw_answer)
        )
        logger.info(
            "Legal answer produced {} characters; complete_marker={}; "
            "finish_reason={!r}; ending={!r}",
            len(answer),
            answer_complete,
            ai_message.response_metadata.get("finish_reason"),
            answer[-100:],
        )
        return {
            "answers": [answer],
            "completion_flags": [answer_complete],
            "evidence": results,
        }
    except OpenNotebookError:
        raise
    except Exception as exc:
        error_class, user_message = classify_error(exc)
        raise error_class(user_message) from exc


async def _legal_search(
    term: str,
    domain: str | None = None,
    legal_as_of: str | None = None,
    *,
    request_id: str | None = None,
    issue_id: str | None = None,
    issue_domain: str | None = None,
) -> list[dict]:
    legal_client = get_legal_search_client()
    service_url = legal_client.base_url
    # The streaming route may pass recent conversation context in ``term``.
    # Retrieval has a strict 2,000-character request limit; keep the newest
    # part, which contains the current question, while the full context still
    # remains available to the answering prompt.
    retrieval_term = str(term or "").strip()
    if len(retrieval_term) > 1900:
        retrieval_term = retrieval_term[-1900:]
    payload = {
        "query": retrieval_term,
        "limit": 6,
        "candidate_count": 180,
        "retrieval_tier": "core",
        "include_trace": True,
    }
    if legal_as_of:
        payload["as_of"] = legal_as_of
    if domain:
        payload["domain"] = domain
    if request_id:
        payload["request_id"] = request_id
    if issue_id:
        payload["issue_id"] = issue_id
    if issue_domain:
        payload["issue_domain"] = issue_domain
    async def _post_search(request_payload: dict) -> dict:
        return await legal_client.search(
            request_payload,
            compatibility_fields=("retrieval_tier",),
        )

    try:
        response_data = await _post_search(payload)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise LegalRetrievalUnavailableError(
            f"Không kết nối được dịch vụ tra cứu pháp luật tại {service_url}."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise LegalRetrievalUnavailableError(
            "Dịch vụ tra cứu pháp luật phản hồi lỗi "
            f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc

    items = response_data.get("results") or []
    fallback_reason = expanded_retrieval_reason(retrieval_term, items)
    if fallback_reason:
        # Keep the fast Hai Phong/commune index as default. Query the full
        # active corpus only when it cannot supply enough distinct evidence.
        retry_payload = dict(payload)
        retry_payload["retrieval_tier"] = "expanded"
        retry_payload["candidate_count"] = 240
        response_data = await _post_search(retry_payload)
        trace = response_data.get("trace")
        if isinstance(trace, dict):
            trace["retrieval_fallback"] = {
                "from_tier": "core",
                "to_tier": "expanded",
                "reason": fallback_reason,
            }
        items = response_data.get("results") or []
    results = []
    relationship_documents = set()
    for item in items:
        document_key = (
            item.get("law_number")
            or item.get("document_title")
            or item.get("document_id")
        )
        relationships = []
        if document_key not in relationship_documents:
            relationship_documents.add(document_key)
            relationships = _compact_relationships(
                item.get("relationships") or []
            )
        result = {
            "id": f"legal:{item['chunk_id']}",
            "chunk_id": item.get("chunk_id"),
            "document_id": item.get("document_id"),
            "article_id": item.get("article_id"),
            "law_number": item.get("law_number"),
            "document_title": item.get("document_title"),
            "document_type": item.get("document_type"),
            "issuing_agency": item.get("issuing_agency"),
            "scope": item.get("scope"),
            "domain_slug": item.get("domain_slug"),
            "domain_name": item.get("domain_name"),
            "document_status": item.get("document_status"),
            "effective_status": item.get("effective_status"),
            "issued_date": item.get("issued_date"),
            "effective_date": item.get("effective_date"),
            "expired_date": item.get("expired_date"),
            "article_number": item.get("article_number"),
            "article_title": item.get("article_title"),
            "chunk_heading": item.get("chunk_heading"),
            "content": item.get("content"),
            "source_url": item.get("source_url"),
            "official_level": item.get("official_level"),
            "applicability_info": item.get("applicability_info"),
            "request_id": item.get("request_id") or request_id,
            "issue_id": item.get("issue_id") or issue_id,
            "issue_domain": item.get("issue_domain") or issue_domain or domain,
            "relationships": relationships,
        }
        # Keep the raw trace out of the prompt but preserve it on one evidence
        # item so the API can persist the selected tier and latency snapshot.
        if not results and isinstance(response_data.get("trace"), dict):
            result["_retrieval_trace"] = response_data["trace"]
        results.append(result)
    return results


def _format_evidence_for_prompt(results: list[dict]) -> str:
    sections = []
    for index, result in enumerate(results, start=1):
        metadata = [
            ("Văn bản", result.get("law_number") or result.get("document_title")),
            ("Tên văn bản", result.get("document_title")),
            ("Loại văn bản", result.get("document_type")),
            ("Cơ quan ban hành", result.get("issuing_agency")),
            ("Phạm vi", result.get("scope")),
            ("Trạng thái", result.get("document_status")),
            ("Ngày ban hành", result.get("issued_date")),
            ("Ngày hiệu lực", result.get("effective_date")),
            ("Ngày hết hiệu lực", result.get("expired_date")),
            ("Điều", result.get("article_number")),
            ("Tên điều", result.get("article_title")),
            ("Đề mục", result.get("chunk_heading")),
        ]
        lines = [f"## Nguồn {index}"]
        lines.extend(
            f"- {label}: {value}"
            for label, value in metadata
            if value not in (None, "", [])
        )
        relationships = result.get("relationships") or []
        if relationships:
            lines.append("- Quan hệ văn bản:")
            for relationship in relationships:
                related = (
                    relationship.get("law_number")
                    or relationship.get("title")
                    or "không rõ"
                )
                relation_type = relationship.get("type") or "liên quan"
                status = relationship.get("status") or "chưa rõ trạng thái"
                lines.append(
                    f"  - {relation_type}: {related}; trạng thái: {status}"
                )
        lines.extend(
            [
                "- Nguyên văn đoạn nguồn:",
                str(result.get("content") or "").strip(),
            ]
        )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _compact_relationships(relationships: list[dict]) -> list[dict]:
    compact = []
    seen = set()
    for relationship in relationships:
        law_number = relationship.get("related_law_number")
        title = relationship.get("related_title")
        if not law_number and not title:
            continue
        key = (
            relationship.get("direction"),
            relationship.get("relationship_type"),
            law_number,
            title,
        )
        if key in seen:
            continue
        seen.add(key)
        compact.append(
            {
                "direction": relationship.get("direction"),
                "type": relationship.get("relationship_type"),
                "law_number": law_number,
                "title": title,
                "status": relationship.get("related_status"),
                "effective_date": relationship.get(
                    "related_effective_date"
                ),
                "expired_date": relationship.get(
                    "related_expired_date"
                ),
            }
        )
        if len(compact) == 8:
            break
    return compact


def _enrich_citations(text: str, evidence: list[dict]) -> str:
    """Replace a legacy internal citation with public retrieval metadata."""
    sources = {}
    for source in evidence:
        source_id = source.get("id")
        if source_id:
            chunk_id = str(source_id).split(":", 1)[-1]
            sources[chunk_id] = source
            
    def clean_article_title(title: str) -> str:
        if not title:
            return ""
        # Lấy phần sau dấu > cuối cùng (breadcrumb)
        parts = [p.strip() for p in title.split(">")]
        last_part = parts[-1] if parts else title
        # Loại bỏ tiền tố "Điều X. " hoặc "Điều X: " hoặc "Điều X "
        cleaned = re.sub(r"^Điều\s+\d+[a-zA-Z]?[\.\s:-]*", "", last_part, flags=re.IGNORECASE)
        return cleaned.strip()

    def replace_match(match: re.Match) -> str:
        chunk_id = match.group(1)
        source = sources.get(chunk_id)
        if not source:
            return match.group(0)
            
        law_number = source.get("law_number")
        law_name = source.get("law_name") or source.get("document_title", "")
        article_number = source.get("article_number")
        article_title = source.get("article_title", "")
        clause_number = source.get("clause_number")
        point_number = source.get("point_number")
        
        parts = []
        if law_number:
            parts.append(law_number)
        elif law_name:
            parts.append(law_name)
            
        if article_number:
            parts.append(f"Điều {article_number}")
        if clause_number:
            parts.append(f"Khoản {clause_number}")
        if point_number:
            parts.append(f"Điểm {point_number}")
            
        citation_text = " - ".join(parts) if parts else "Văn bản pháp luật"
        cleaned_title = clean_article_title(article_title)
        if cleaned_title:
            citation_text += f" ({cleaned_title})"
            
        return f"[{citation_text}]"

    # Match [legal:12345] or legal:12345
    pattern = re.compile(r"\[?\s*legal:(\d+)\s*\]?", re.IGNORECASE)
    return pattern.sub(replace_match, text)


async def write_final_answer(
    state: ThreadState, config: RunnableConfig
) -> dict:
    try:
        draft = "\n\n".join(
            answer.strip()
            for answer in state.get("answers", [])
            if answer and answer.strip()
        )
        evidence = state.get("evidence", [])
        if not draft:
            return {"final_answer": _enrich_citations(_no_answer_guidance(), evidence)}
        contract = answer_contract(
            state.get("role") or "citizen",
            state.get("question_type"),
            state.get("required_sections") or [],
        )
        draft = normalize_answer_markdown(draft)
        flags = state.get("completion_flags", [])
        completion_marked = all(flags) if flags else True
        validation_started = time.perf_counter()
        repair_reason = _repair_reason(
            draft,
            evidence,
            completion_marked=completion_marked,
        )
        telemetry.record_ask_stage(
            "validation",
            duration_ms=(time.perf_counter() - validation_started) * 1000,
        )
        if repair_reason is None:
            telemetry.record_ask_outcome("repair", "skipped")
            missing = missing_contract_headings(draft, contract)
            if missing:
                logger.info(
                    "Answer has {} presentation-heading omissions; "
                    "returning without an editorial model call",
                    len(missing),
                )
            logger.info(
                "Returning grounded legal draft with {} characters",
                len(draft),
            )
            return {"final_answer": _enrich_citations(draft, evidence)}

        logger.warning(
            "Repairing legal answer for reason={}",
            repair_reason,
        )

        # Unsupported references are localized defects.  If the draft still
        # contains current-request grounded fragments, remove only the unsafe
        # fragments deterministically instead of risking a slow global rewrite
        # that may discard the useful answer on timeout.
        if repair_reason in {"invalid_citation", "unsupported_legal_reference"}:
            safe_draft = _safe_draft_after_failed_repair(draft, evidence)
            if safe_draft:
                telemetry.record_ask_outcome("repair", "skipped")
                logger.info(
                    "Returning deterministically salvaged grounded draft with {} characters",
                    len(safe_draft),
                )
                return {"final_answer": _enrich_citations(safe_draft, evidence)}

        telemetry.record_ask_outcome("repair", "attempted")
        repair_started = time.perf_counter()
        if repair_reason == "truncated_answer":
            completed = await _complete_legal_answer(
                state, config, draft, evidence
            )
            telemetry.record_ask_stage(
                "repair",
                duration_ms=(time.perf_counter() - repair_started) * 1000,
                outcome="success" if completed else "failed",
            )
            telemetry.record_ask_outcome(
                "repair", "success" if completed else "failed"
            )
            if completed:
                return {"final_answer": _enrich_citations(completed, evidence)}
            return {"final_answer": _no_answer_guidance()}

        form_context = ""
        try:
            from api.routers.ward_procedures import build_official_form_context

            form_context = build_official_form_context(
                str(state["question"]),
                domain=state.get("domain"),
            )
        except Exception as exc:
            logger.warning("Failed to load forms for answer repair: {}", exc)

        repair_payload = {
            "question": state["question"],
            "role": state["role"],
            "question_type": state.get("question_type"),
            "draft": draft,
            "evidence": _format_evidence_for_prompt(evidence),
            "ids": [source.get("id") for source in evidence],
            "form_context": form_context or "Không có biểu mẫu chính thức phù hợp đã được duyệt.",
            "answer_contract": render_answer_contract(contract),
        }
        repair_prompt = Prompter(
            prompt_template="ask/grounding_review"
        ).render(data=repair_payload)
        try:
            # Repair has a smaller output budget for citizen-facing answers.
            # Keep the role decision local to this function: this branch is
            # reached only for legal-risk repairs and must never fail because
            # of an undeclared rendering variable.
            is_citizen = str(state.get("role") or "citizen").casefold() == "citizen"
            model = await provision_langchain_model(
                repair_prompt,
                config.get("configurable", {}).get("answer_model"),
                "tools",
                max_tokens=4096 if is_citizen else 16000,
            )
            repair_timeout = min(
                _llm_timeout_seconds(),
                max(20.0, float(os.getenv("LEGAL_REPAIR_TIMEOUT_SECONDS", "45"))),
            )
            repaired_message = await asyncio.wait_for(
                model.ainvoke(repair_prompt),
                timeout=repair_timeout,
            )
        except Exception as exc:
            logger.warning(
                "Legal answer repair failed; returning evidence-gap guidance: {}",
                exc.__class__.__name__,
            )
            telemetry.record_ask_stage(
                "repair",
                duration_ms=(time.perf_counter() - repair_started) * 1000,
                outcome="failed",
            )
            telemetry.record_ask_outcome("repair", "failed")
            safe_draft = _safe_draft_after_failed_repair(draft, evidence)
            return {
                "final_answer": _enrich_citations(safe_draft, evidence)
                if safe_draft
                else _no_answer_guidance()
            }
        raw_repaired = clean_thinking_content(
            extract_text_content(repaired_message.content)
        )
        repaired = _normalize_answer_format(
            _remove_completion_marker(raw_repaired)
        )
        repaired = normalize_answer_markdown(repaired)
        logger.info(
            "Legal answer repair produced {} characters; ending={!r}",
            len(repaired),
            repaired[-80:],
        )
        repaired_validation = validate_legal_references(repaired, evidence)
        telemetry.record_ask_stage(
            "repair",
            duration_ms=(time.perf_counter() - repair_started) * 1000,
            outcome=(
                "success"
                if repaired_validation.status != "ungrounded"
                else "failed"
            ),
        )
        telemetry.record_ask_outcome(
            "repair",
            "success" if repaired_validation.status != "ungrounded" else "failed",
        )
        if repaired_validation.status == "ungrounded" or _answer_is_truncated(
            repaired, completion_marked=_has_completion_marker(raw_repaired)
        ):
            logger.warning(
                "Single repair pass did not produce a verified complete answer"
            )
            safe_draft = _safe_draft_after_failed_repair(draft, evidence)
            return {
                "final_answer": _enrich_citations(safe_draft, evidence)
                if safe_draft
                else _no_answer_guidance()
            }
        return {"final_answer": _enrich_citations(repaired, evidence)}
    except OpenNotebookError:
        raise
    except Exception as exc:
        error_class, user_message = classify_error(exc)
        raise error_class(user_message) from exc


_CITATION_RE = re.compile(r"legal:(\d+)", re.IGNORECASE)
_LAW_NUMBER_RE = re.compile(
    r"\b\d{1,4}/\d{4}/[A-ZÀ-ỸĐ0-9.-]+"
    r"(?:-[A-ZÀ-ỸĐ0-9.-]+)*\b",
    re.IGNORECASE,
)
_ARTICLE_RE = re.compile(r"\bĐiều\s+(\d+[a-z]?)\b", re.IGNORECASE)
_MEASURE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*"
    r"(?:phút|giờ|ngày|tháng|năm|đồng|triệu|tỷ|%|trường hợp)\b",
    re.IGNORECASE,
)
_DOCUMENT_NAME_RE = re.compile(
    r"\b(?:Luật|Bộ luật|Nghị định|Thông tư(?: liên tịch)?|"
    r"Nghị quyết|Quyết định|Chỉ thị)\s+"
    r".+?(?=\s*\[legal:|[.;,\n]|$)",
    re.IGNORECASE,
)
_ENGLISH_LEAK_RE = re.compile(
    r"\b(?:according to|processing time|which is also|within|"
    r"individual answers?|final answer|per\s*\[?legal)\b",
    re.IGNORECASE,
)


async def _complete_legal_answer(
    state: ThreadState,
    config: RunnableConfig,
    draft: str,
    evidence: list[dict],
) -> str | None:
    completion_payload = {
        "question": state["question"],
        "role": state["role"],
        "draft": draft,
        "evidence": _format_evidence_for_prompt(evidence),
        "ids": [source.get("id") for source in evidence],
        "answer_contract": render_answer_contract(
            answer_contract(
                state.get("role") or "citizen",
                state.get("question_type"),
                state.get("required_sections") or [],
            )
        ),
    }
    completion_prompt = Prompter(
        prompt_template="ask/answer_completion"
    ).render(data=completion_payload)
    model = await provision_langchain_model(
        completion_prompt,
        config.get("configurable", {}).get("answer_model"),
        "tools",
        max_tokens=4096 if state.get("role") == "citizen" else 16000,
    )
    try:
        message = await asyncio.wait_for(
            model.ainvoke(completion_prompt),
            timeout=_llm_timeout_seconds(),
        )
    except Exception:
        logger.exception("Legal answer completion call failed")
        return None

    raw_completed = clean_thinking_content(
        extract_text_content(message.content)
    )
    has_completion_marker = _has_completion_marker(raw_completed)
    completed = _normalize_answer_format(
        _remove_completion_marker(raw_completed)
    )
    completed = _remove_invalid_citations(completed, evidence)
    logger.info(
        "Legal answer completion produced {} characters; ending={!r}",
        len(completed),
        completed[-80:],
    )
    completed = normalize_answer_markdown(completed)
    validation = validate_legal_references(completed, evidence)
    if (
        validation.status == "ungrounded"
        or _answer_is_truncated(
            completed,
            completion_marked=has_completion_marker,
        )
    ):
        return None
    return completed


def _normalize_answer_format(text: str) -> str:
    normalized = re.sub(
        r"`?\[?\s*legal\s*:\s*(\d+)\s*\]?`?",
        r"[legal:\1]",
        text,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bper\s*(?=\[legal:)",
        "theo ",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\]\s*\[", "] [", normalized)
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    return normalized.strip()


_COMPLETION_MARKER = "[[HOAN_TAT]]"


def _has_completion_marker(text: str) -> bool:
    return _COMPLETION_MARKER in text


def _remove_completion_marker(text: str) -> str:
    return text.replace(_COMPLETION_MARKER, "").strip()


def _needs_quality_repair(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 40 or _ENGLISH_LEAK_RE.search(stripped):
        return True
    if stripped.count("**") % 2 or stripped.count("`") % 2:
        return True
    if re.search(r"(?:[*:_\-]|\b(?:theo|trong vòng|within))\s*$", stripped, re.I):
        return True
    if stripped[-1] not in ".!?)]":
        return True
    return False


def _needs_role_repair(
    text: str,
    role: str,
    question_type: str | None = None,
    required_sections: list[str] | None = None,
) -> bool:
    """Compatibility checker backed by the shared presentation contract."""
    contract = answer_contract(role, question_type, required_sections or [])
    return bool(missing_contract_headings(text, contract))


def _answer_is_truncated(text: str, *, completion_marked: bool) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    suspicious_end = bool(
        re.search(
            r"(?:[*:_\-,]|\b(?:theo|trong vòng|within|gồm|bao gồm|là))\s*$",
            stripped,
            re.IGNORECASE,
        )
    )
    if suspicious_end:
        return True
    if completion_marked:
        return False
    return stripped[-1] not in ".!?)]"


def _repair_reason(
    text: str,
    evidence: list[dict],
    *,
    completion_marked: bool,
) -> str | None:
    """Return the only reasons that may spend one additional model call."""
    validation = validate_legal_references(text, evidence)
    if validation.invalid_internal_ids:
        return "invalid_citation"
    if validation.unsupported_references:
        return "unsupported_legal_reference"
    if _answer_is_truncated(text, completion_marked=completion_marked):
        return "truncated_answer"
    return None


def _safe_draft_after_failed_repair(
    text: str,
    evidence: list[dict],
) -> str | None:
    """Keep grounded fragments while removing fragments with unsafe references.

    A single unsupported citation or law number must not turn an otherwise
    useful answer into a global refusal.  This deterministic fallback removes
    only the sentence/line that fails the same legal-reference validator used
    by the normal answer path.  It succeeds only when at least one remaining
    fragment is explicitly bound to evidence from the current request.
    """
    normalized = normalize_answer_markdown(_normalize_answer_format(text))
    kept_lines: list[str] = []
    pending_headings: list[str] = []
    has_grounded_fragment = False
    removed_unsafe_fragment = False

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            if kept_lines and kept_lines[-1] != "":
                kept_lines.append("")
            continue

        if re.match(r"^#{1,6}\s+", line):
            heading_validation = validate_legal_references(line, evidence)
            if (
                heading_validation.invalid_internal_ids
                or heading_validation.unsupported_references
            ):
                removed_unsafe_fragment = True
                pending_headings.clear()
            else:
                pending_headings.append(line)
                if (
                    heading_validation.has_explicit_reference
                    and heading_validation.matched_source_ids
                ):
                    has_grounded_fragment = True
            continue

        safe_fragments: list[str] = []
        for fragment in re.split(r"(?<=[.!?])\s+", line):
            fragment = fragment.strip()
            if not fragment:
                continue
            fragment_validation = validate_legal_references(fragment, evidence)
            if (
                fragment_validation.invalid_internal_ids
                or fragment_validation.unsupported_references
            ):
                removed_unsafe_fragment = True
                continue
            safe_fragments.append(fragment)
            if (
                fragment_validation.has_explicit_reference
                and fragment_validation.matched_source_ids
            ):
                has_grounded_fragment = True

        if safe_fragments:
            if pending_headings:
                kept_lines.extend(pending_headings)
                pending_headings.clear()
            kept_lines.append(" ".join(safe_fragments))
        else:
            # Do not leave a heading whose only content was rejected.
            pending_headings.clear()

    cleaned = "\n".join(kept_lines).strip()
    if not cleaned or not has_grounded_fragment:
        return None

    cleaned_validation = validate_legal_references(cleaned, evidence)
    if cleaned_validation.status == "ungrounded":
        return None
    if removed_unsafe_fragment:
        cleaned += (
            "\n\n> Một số nội dung đã được lược bỏ vì chưa đối chiếu được "
            "với nguồn của lượt hỏi này."
        )
    return normalize_answer_markdown(cleaned)


def _normalize_grounding_text(value: object) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").casefold())
    normalized = "".join(
        char
        for char in normalized
        if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")
    normalized = normalized.replace("\u0111", "d")
    return re.sub(
        r"\s+", " ", re.sub(r"[^\w%]+", " ", normalized)
    ).strip()


def _source_text(source: dict) -> str:
    return " ".join(
        str(value or "")
        for value in (
            source.get("law_number"),
            source.get("document_title"),
            source.get("document_type"),
            source.get("issuing_agency"),
            source.get("article_number"),
            source.get("article_title"),
            source.get("chunk_heading"),
            source.get("content"),
            source.get("relationships") or [],
        )
    )


def _has_unsupported_references(
    text: str, evidence: list[dict]
) -> bool:
    validation = validate_legal_references(text, evidence)
    return validation.status != "fully_grounded"


def _has_invalid_citation_ids(text: str, evidence: list[dict]) -> bool:
    return bool(validate_legal_references(text, evidence).invalid_internal_ids)


def _remove_invalid_citations(text: str, evidence: list[dict]) -> str:
    allowed_ids = {
        str(source["id"]).split(":", 1)[-1]
        for source in evidence
        if source.get("id")
    }

    def replace_citation(match: re.Match) -> str:
        return (
            match.group(0)
            if match.group(1) in allowed_ids
            else ""
        )

    cleaned = re.sub(
        r"\[?\s*legal:(\d+)\s*\]?",
        replace_citation,
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _no_answer_guidance() -> str:
    return (
        "Tôi chưa nhận được đủ nội dung để trả lời câu hỏi này. "
        "Vui lòng mô tả ngắn gọn vấn đề, địa bàn và kết quả bạn cần biết."
    )


agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_node("provide_answer", provide_answer)
agent_state.add_node("write_final_answer", write_final_answer)
agent_state.add_edge(START, "agent")
agent_state.add_conditional_edges(
    "agent", trigger_queries, ["provide_answer"]
)
agent_state.add_edge("provide_answer", "write_final_answer")
agent_state.add_edge("write_final_answer", END)

graph = agent_state.compile()
