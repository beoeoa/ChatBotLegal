from datetime import date
from unittest.mock import AsyncMock

import fitz
import httpx
import pytest

from api.source_gap_jobs import (
    build_candidate_queue_payload,
    create_source_gap_job,
    enqueue_source_gap_job,
    load_source_gap_jobs,
    process_source_gap_job,
    sync_downloaded_form_candidate_to_review_queue,
    sync_downloaded_candidate_to_review_queue,
)


def _client(handler):
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def test_found_not_retrieved_never_creates_a_crawl_job():
    assert create_source_gap_job(
        case_id="opaque-case",
        gap_type="FOUND_NOT_RETRIEVED",
        source_pages=["https://vbpl.vn/source"],
        legal_as_of="2026-07-27",
    ) is None


def test_source_gap_job_rejects_non_official_discovery_seed():
    try:
        create_source_gap_job(
            case_id="opaque-case",
            gap_type="MISSING_FORM_SOURCE",
            source_pages=["https://example.com/form"],
            legal_as_of="2026-07-27",
        )
    except ValueError as exc:
        assert str(exc) == "source_host_not_allowed"
    else:
        raise AssertionError("unofficial source seed must be rejected")


def test_form_download_stays_pending_and_records_provenance(tmp_path):
    file_body = b"%PDF-1.7\n" + b"x" * 512

    def handler(request):
        if request.url.path == "/procedure":
            return httpx.Response(
                200,
                text='<a href="/files/form.pdf">Tai bieu mau</a>',
                request=request,
            )
        return httpx.Response(
            200,
            content=file_body,
            headers={"content-type": "application/pdf"},
            request=request,
        )

    job = create_source_gap_job(
        case_id="opaque-case",
        gap_type="MISSING_FORM_SOURCE",
        source_pages=["https://dichvucong.gov.vn/procedure"],
        legal_as_of="2026-07-27",
        procedure_id="birth-registration",
        expected_name="Giay khai sinh",
    )
    result = process_source_gap_job(
        job,
        client=_client(handler),
        candidate_dir=tmp_path,
        checked_on=date(2026, 7, 27),
    )

    assert result["status"] == "downloaded_candidate"
    assert result["candidate"]["approved"] is False
    assert result["candidate"]["review_status"] == "candidate_pending_review"
    assert result["candidate"]["sha256"]
    assert result["candidate"]["provenance"]["publisher"] == "dichvucong.gov.vn"
    assert (tmp_path / result["candidate"]["file_name"]).is_file()


def test_direct_official_pdf_seed_can_become_candidate(tmp_path):
    file_body = b"%PDF-1.7\n" + b"x" * 512

    def handler(request):
        return httpx.Response(
            200,
            content=file_body,
            headers={"content-type": "application/pdf"},
            request=request,
        )

    job = create_source_gap_job(
        case_id="opaque-direct",
        gap_type="MISSING_FORM_SOURCE",
        source_pages=["https://moc.gov.vn/Images/form.pdf"],
        legal_as_of="2026-07-27",
    )
    result = process_source_gap_job(
        job,
        client=_client(handler),
        candidate_dir=tmp_path,
        checked_on=date(2026, 7, 27),
    )

    assert result["status"] == "downloaded_candidate"
    assert result["candidate"]["download_url"].endswith("/Images/form.pdf")


def test_official_pdf_form_candidate_extracts_only_declared_pages(tmp_path):
    source = fitz.open()
    for label in ("cover", "procedure", "official form", "notes"):
        page = source.new_page()
        page.insert_text((72, 72), label)
    file_body = source.tobytes()
    source.close()

    def handler(request):
        return httpx.Response(
            200,
            content=file_body,
            headers={"content-type": "application/pdf"},
            request=request,
        )

    job = create_source_gap_job(
        case_id="foreign-marriage-form",
        gap_type="MISSING_FORM_SOURCE",
        source_pages=["https://cdn.haiphong.gov.vn/forms/package.pdf"],
        legal_as_of="2026-07-27",
        procedure_id="dang_ky_ket_hon_nuoc_ngoai",
        expected_name="Tờ khai đăng ký kết hôn",
        expected_code="2.000806",
        source_pages_zero_based=[2, 3],
    )
    result = process_source_gap_job(
        job,
        client=_client(handler),
        candidate_dir=tmp_path,
        checked_on=date(2026, 7, 27),
    )

    assert result["status"] == "downloaded_candidate"
    candidate = result["candidate"]
    assert candidate["approved"] is False
    assert candidate["provenance"]["source_pages_zero_based"] == [2, 3]
    assert candidate["provenance"]["source_sha256"] != candidate["sha256"]
    extracted = (tmp_path / candidate["file_name"]).read_bytes()
    reader = fitz.open(stream=extracted, filetype="pdf")
    try:
        assert reader.page_count == 2
        first_page = reader.load_page(0).get_text()
        assert "official form" in first_page
        assert "cover" not in first_page
    finally:
        reader.close()


def test_html_disguised_as_pdf_is_not_published(tmp_path):
    def handler(request):
        if request.url.path == "/procedure":
            return httpx.Response(
                200,
                text='<a href="/files/form.pdf">Tai bieu mau</a>',
                request=request,
            )
        return httpx.Response(
            200,
            content=b"<!doctype html>" + b"x" * 512,
            headers={"content-type": "application/pdf"},
            request=request,
        )

    job = create_source_gap_job(
        case_id="opaque-case",
        gap_type="MISSING_FORM_SOURCE",
        source_pages=["https://dichvucong.gov.vn/procedure"],
        legal_as_of="2026-07-27",
    )
    result = process_source_gap_job(
        job,
        client=_client(handler),
        candidate_dir=tmp_path,
        checked_on=date(2026, 7, 27),
    )

    assert result["status"] == "failed_retryable"
    assert result["reason_code"] == "HTML_DISGUISED_AS_FILE"
    assert list(tmp_path.iterdir()) == []


def test_seven_distinct_official_checks_without_file_become_verified_gap(tmp_path):
    def handler(request):
        return httpx.Response(200, text="<html>No public file</html>", request=request)

    job = create_source_gap_job(
        case_id="opaque-case",
        gap_type="MISSING_FORM_SOURCE",
        source_pages=["https://dichvucong.gov.vn/procedure"],
        legal_as_of="2026-07-27",
    )
    for day in range(1, 8):
        job = process_source_gap_job(
            job,
            client=_client(handler),
            candidate_dir=tmp_path,
            checked_on=date(2026, 7, day),
        )

    assert job["status"] == "verified_gap"
    assert job["reason_code"] == "OFFICIAL_SOURCES_CHECKED_NO_PUBLIC_FILE"
    assert len(job["checked_dates"]) == 7


def test_captcha_is_blocked_external_without_guessing_metadata(tmp_path):
    def handler(request):
        return httpx.Response(403, text="<html>CAPTCHA</html>", request=request)

    job = create_source_gap_job(
        case_id="opaque-case",
        gap_type="MISSING_LEGAL_SOURCE",
        source_pages=["https://vbpl.vn/source"],
        legal_as_of="2026-07-27",
    )
    result = process_source_gap_job(
        job,
        client=_client(handler),
        candidate_dir=tmp_path,
        checked_on=date(2026, 7, 27),
    )

    assert result["status"] == "blocked_external"
    assert result["reason_code"] == "CAPTCHA_OR_AUTH_REQUIRED"
    assert "candidate" not in result


def test_job_store_is_idempotent_and_contains_no_question_text(tmp_path):
    store = tmp_path / "source-gap-jobs.json"
    job = create_source_gap_job(
        case_id="opaque-case",
        gap_type="MISSING_FORM_SOURCE",
        source_pages=["https://dichvucong.gov.vn/procedure"],
        legal_as_of="2026-07-27",
    )

    enqueue_source_gap_job(job, store_path=store)
    enqueue_source_gap_job(job, store_path=store)

    stored = load_source_gap_jobs(store)
    assert len(stored) == 1
    assert "question" not in store.read_text(encoding="utf-8").casefold()


def test_job_store_updates_same_id_with_new_processing_state_without_duplicate(tmp_path):
    store = tmp_path / "source-gap-jobs.json"
    queued = create_source_gap_job(
        case_id="opaque-state",
        gap_type="MISSING_LEGAL_SOURCE",
        source_pages=["https://vbpl.vn/document"],
        legal_as_of="2026-07-27",
        expected_code="16/2022/NĐ-CP",
    )
    downloaded = {
        **queued,
        "status": "downloaded_candidate",
        "reason_code": "DOWNLOADED_PENDING_REVIEW",
        "candidate": {"sha256": "a" * 64},
    }

    enqueue_source_gap_job(queued, store_path=store)
    updated = enqueue_source_gap_job(downloaded, store_path=store)

    assert updated["status"] == "downloaded_candidate"
    stored = load_source_gap_jobs(store)
    assert len(stored) == 1
    assert stored[0]["status"] == "downloaded_candidate"


def _downloaded_legal_job():
    return {
        "job_id": "sgj-test-official-law",
        "gap_type": "MISSING_LEGAL_SOURCE",
        "status": "downloaded_candidate",
        "legal_as_of": "2026-07-27",
        "expected_code": "16/2022/NĐ-CP",
        "expected_name": "Nghị định quy định xử phạt vi phạm hành chính về xây dựng",
        "candidate": {
            "source_type": "legal_document_candidate",
            "local_path": "data/uploads/source_gap_candidates/law.pdf",
            "file_name": "law.pdf",
            "file_format": "pdf",
            "sha256": "a" * 64,
            "size_bytes": 512,
            "source_page": "https://vanban.chinhphu.vn/document",
            "download_url": "https://vanban.chinhphu.vn/law.pdf",
            "provenance": {
                "source_page": "https://vanban.chinhphu.vn/document",
                "download_url": "https://vanban.chinhphu.vn/law.pdf",
                "publisher": "vanban.chinhphu.vn",
                "sha256": "a" * 64,
            },
        },
        "official_metadata": {
            "law_number": "16/2022/NĐ-CP",
            "title": "Nghị định quy định xử phạt vi phạm hành chính về xây dựng",
            "issuing_agency": "Chính phủ",
            "document_type": "Nghị định",
            "issued_date": "2022-01-28",
            "effective_date": "2022-01-28",
            "scope": "central",
            "confirmed_official_source": True,
        },
    }


def test_queue_payload_rejects_corrupt_official_identity():
    job = _downloaded_legal_job()
    job["expected_code"] = "16/2022/N?-CP"

    with pytest.raises(ValueError, match="invalid_official_identity_unicode"):
        build_candidate_queue_payload(job)


def test_queue_payload_keeps_candidate_fail_closed_until_review():
    payload = build_candidate_queue_payload(_downloaded_legal_job())

    assert payload["external_id"] == "source-gap:sgj-test-official-law"
    assert payload["status"] == "changes_requested"
    assert payload["review_status"] == "changes_requested"
    assert payload["approved"] is False
    assert payload["legal_review_status"] == "pending"
    assert payload["uploaded_file"]["sha256"] == "a" * 64
    assert payload["raw_metadata"]["provenance"]["publisher"] == "vanban.chinhphu.vn"
    assert payload["legal_validity_flags"]["legal_review_required"] is True
    assert payload["review_recommendation"]["hard_gate"]["eligible"] is False


def test_downloaded_form_candidate_routes_to_form_review_queue_idempotently(tmp_path):
    source = tmp_path / "data/uploads/source_gap_candidates/form.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
    checksum = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    queue_path = tmp_path / "notebook_data/forms/official_forms_candidates_classified.json"
    review_dir = tmp_path / "data/uploads/forms/official_candidates"
    job = {
        "job_id": "sgj-form-route",
        "gap_type": "MISSING_FORM_SOURCE",
        "status": "downloaded_candidate",
        "legal_as_of": "2026-07-27",
        "procedure_id": "dang_ky_ket_hon_nuoc_ngoai",
        "expected_code": "2.000806",
        "expected_name": "Tờ khai đăng ký kết hôn",
        "official_metadata": {
            "domain": "ho_tich",
            "legal_basis": ["60/2014/QH13", "123/2015/NĐ-CP"],
            "confirmed_official_source": True,
        },
        "candidate": {
            "local_path": str(source.relative_to(tmp_path)).replace("\\", "/"),
            "file_name": source.name,
            "file_format": "pdf",
            "sha256": checksum,
            "size_bytes": source.stat().st_size,
            "source_page": "https://haiphong.gov.vn/procedure",
            "download_url": "https://cdn.haiphong.gov.vn/forms/package.pdf",
            "provenance": {
                "source_sha256": "b" * 64,
                "source_pages_zero_based": [132, 133],
                "publisher": "cdn.haiphong.gov.vn",
            },
        },
    }

    first = sync_downloaded_form_candidate_to_review_queue(
        job,
        project_root=tmp_path,
        queue_path=queue_path,
        review_dir=review_dir,
    )
    second = sync_downloaded_form_candidate_to_review_queue(
        job,
        project_root=tmp_path,
        queue_path=queue_path,
        review_dir=review_dir,
    )

    assert first["action"] == "created"
    assert second["action"] == "unchanged"
    payload = __import__("json").loads(queue_path.read_text(encoding="utf-8"))
    record = payload["records"][0]
    assert record["procedure_id"] == "dang_ky_ket_hon_nuoc_ngoai"
    assert record["review_status"] == "candidate_pending_review"
    assert record["is_approved"] is False
    assert record["runtime_eligible"] is False
    assert record["source_gap_job_id"] == "sgj-form-route"
    assert (tmp_path / record["file_path"]).is_file()


def test_downloaded_form_candidate_reports_missing_official_procedure_code(
    tmp_path,
):
    source = tmp_path / "data/uploads/source_gap_candidates/form.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)
    checksum = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    job = {
        "job_id": "sgj-form-missing-code",
        "gap_type": "MISSING_FORM_SOURCE",
        "status": "downloaded_candidate",
        "procedure_id": "dang_ky_ket_hon_nuoc_ngoai",
        "expected_code": "",
        "expected_name": "Marriage registration declaration",
        "official_metadata": {
            "confirmed_official_source": True,
        },
        "candidate": {
            "canonical_name": "Marriage registration declaration",
            "local_path": str(source.relative_to(tmp_path)).replace("\\", "/"),
            "file_name": source.name,
            "file_format": "pdf",
            "sha256": checksum,
            "source_page": "https://haiphong.gov.vn/procedure",
            "download_url": "https://cdn.haiphong.gov.vn/forms/package.pdf",
        },
    }

    with pytest.raises(ValueError, match="official_procedure_code_required"):
        sync_downloaded_form_candidate_to_review_queue(
            job,
            project_root=tmp_path,
            queue_path=tmp_path / "queue.json",
            review_dir=tmp_path / "review",
        )


@pytest.mark.asyncio
async def test_source_gap_bridge_updates_existing_candidate_idempotently():
    existing = {
        "id": "legal_crawl_candidate:existing",
        "external_id": "source-gap:sgj-test-official-law",
        "uploaded_file": {},
    }
    query = AsyncMock(
        side_effect=[
            [{"id": "legal_crawl_source:source_gap"}],
            [existing],
            [{"id": "legal_crawl_source:source_gap"}],
            [{
                **existing,
                "approved": False,
                "legal_review_status": "pending",
                "uploaded_file": {"sha256": "a" * 64},
                "raw_metadata": {"source_gap_job_id": "sgj-test-official-law"},
                "extraction_result": {
                    "status": "review_required",
                    "ocr_status": "pending",
                    "processed_pages": 0,
                    "total_pages": 0,
                    "reason_code": "EXTRACTION_PENDING_REVIEW",
                },
            }],
        ]
    )
    create = AsyncMock()
    update = AsyncMock(return_value=[existing])

    first = await sync_downloaded_candidate_to_review_queue(
        _downloaded_legal_job(),
        query=query,
        create=create,
        update=update,
    )
    second = await sync_downloaded_candidate_to_review_queue(
        _downloaded_legal_job(),
        query=query,
        create=create,
        update=update,
    )

    assert first["action"] == "updated"
    assert second["action"] == "unchanged"
    create.assert_not_awaited()
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_gap_bridge_rejects_checksum_drift():
    query = AsyncMock(
        side_effect=[
            [{"id": "legal_crawl_source:source_gap"}],
            [{
                "id": "legal_crawl_candidate:existing",
                "external_id": "source-gap:sgj-test-official-law",
                "uploaded_file": {"sha256": "b" * 64},
            }],
        ]
    )

    with pytest.raises(ValueError, match="candidate_checksum_drift"):
        await sync_downloaded_candidate_to_review_queue(
            _downloaded_legal_job(),
            query=query,
            create=AsyncMock(),
            update=AsyncMock(),
        )
