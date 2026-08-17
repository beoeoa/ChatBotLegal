from __future__ import annotations

from pathlib import Path

import pytest

from api.source_gap_jobs import (
    enqueue_answer_source_gap_notice,
    list_answer_source_gap_notices,
)


def test_answer_source_gap_is_admin_only_and_never_auto_imports(tmp_path: Path):
    store = tmp_path / "source-gaps.json"
    notice = enqueue_answer_source_gap_notice(
        question="Hồ sơ của ông Nguyễn Văn A còn thiếu biểu mẫu nào?",
        missing_facets=["documents", "form"],
        domain="ho_tich_chung_thuc",
        legal_as_of="2026-08-10",
        actor_role="system",
        store_path=store,
    )
    assert notice["status"] == "admin_review_required"
    assert notice["visibility"] == "admin"
    assert notice["approval_required"] is True
    assert notice["auto_import"] is False
    assert notice["auto_approve"] is False
    assert notice["auto_activate"] is False
    assert "Nguyễn Văn A" not in store.read_text(encoding="utf-8")
    assert len(notice["question_sha256"]) == 64

    assert list_answer_source_gap_notices(role="admin", store_path=store) == [notice]
    for role in ("citizen", "officer"):
        with pytest.raises(PermissionError, match="admin_role_required"):
            list_answer_source_gap_notices(role=role, store_path=store)


def test_answer_source_gap_enqueue_is_idempotent_and_rejects_user_role(tmp_path: Path):
    store = tmp_path / "source-gaps.json"
    kwargs = {
        "question": "Không tìm thấy thời hạn",
        "missing_facets": ["deadline"],
        "domain": "cu_tru_an_ninh",
        "legal_as_of": "2026-08-10",
        "actor_role": "system",
        "store_path": store,
    }
    first = enqueue_answer_source_gap_notice(**kwargs)
    second = enqueue_answer_source_gap_notice(**kwargs)
    assert first["job_id"] == second["job_id"]
    assert len(list_answer_source_gap_notices(role="admin", store_path=store)) == 1

    with pytest.raises(PermissionError, match="system_or_admin_role_required"):
        enqueue_answer_source_gap_notice(**{**kwargs, "actor_role": "citizen"})
