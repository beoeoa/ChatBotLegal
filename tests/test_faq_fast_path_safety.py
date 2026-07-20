from api.routers.faq import faq_fast_path_eligible


def _faq(**updates):
    value = {
        "review_status": "approved",
        "fast_path_enabled": True,
        "approved_by": "user:admin-1",
        "eligible_roles": ["citizen"],
        "domain": "ho_tich_chung_thuc",
        "ward_scope": "Le Chan",
        "reviewed_corpus_revision": "rev-2",
        "reviewed_legal_as_of": "2026-07-16",
        "verified_source_refs": [
            {
                "chunk_id": 10,
                "law_number": "10/2020/NĐ-CP",
                "source_url": "https://example.test/legal/10",
            }
        ],
    }
    value.update(updates)
    return value


def test_seed_or_incomplete_faq_never_uses_fast_path():
    common = dict(
        role="citizen",
        domain="ho_tich_chung_thuc",
        ward_scope="Le Chan",
        corpus_revision="rev-2",
        legal_as_of="2026-07-16",
    )
    assert not faq_fast_path_eligible(
        _faq(approved_by="system_seed"), **common
    )
    assert not faq_fast_path_eligible(
        _faq(verified_source_refs=[]), **common
    )


def test_fast_path_requires_exact_role_domain_revision_and_verified_refs():
    assert faq_fast_path_eligible(
        _faq(),
        role="citizen",
        domain="ho_tich_chung_thuc",
        ward_scope="Le Chan",
        corpus_revision="rev-2",
        legal_as_of="2026-07-16",
    )
    assert not faq_fast_path_eligible(
        _faq(),
        role="officer",
        domain="ho_tich_chung_thuc",
        ward_scope="Le Chan",
        corpus_revision="rev-2",
        legal_as_of="2026-07-16",
    )
