from pathlib import Path

import pytest


def test_internal_pdf_preserves_vietnamese_unicode_and_highlights_article():
    import fitz
    import scripts.legal_search_server as service

    expected = "Ph\u01b0\u1eddng L\u00ea Ch\u00e2n, H\u1ea3i Ph\u00f2ng, quy\u1ec1n s\u1eed d\u1ee5ng \u0111\u1ea5t"

    class StubRetriever:
        def document_detail(self, doc_id, article=None, include_content=False):
            return {
                "document_title": "Quy \u0111\u1ecbnh v\u1ec1 quy\u1ec1n s\u1eed d\u1ee5ng \u0111\u1ea5t", 
                "law_number": "10/2024/N\u0110-CP",
                "source_url": "https://internal.example/document/10-2024",
                "articles": [{
                    "article_number": "6",
                    "article_title": "Quy\u1ec1n v\u00e0 ngh\u0129a v\u1ee5", 
                    "chunks": [{"content": expected}],
                }],
            }

    payload, _filename = service.LegalRetriever.document_pdf(StubRetriever(), "42", article="6")
    pdf = fitz.open(stream=payload, filetype="pdf")
    text = "\n".join(page.get_text("text") for page in pdf).replace("\u00a0", " ")
    pdf.close()

    assert expected in text
    assert "B\u1ea2N TR\u00cdCH XU\u1ea4T T\u1eea KHO H\u1ec6 TH\u1ed0NG" in text
    assert "\u0110i\u1ec1u 6" in text


def test_internal_pdf_refuses_probable_mojibake():
    import scripts.legal_search_server as service

    class StubRetriever:
        def document_detail(self, doc_id, article=None, include_content=False):
            return {
                "document_title": "V\u0103n b\u1ea3n h\u1ee3p l\u1ec7", 
                "law_number": "1/2024/Q\u0110-UBND",
                "source_url": "",
                "articles": [{
                    "article_number": "1",
                    "article_title": "N\u1ed9i dung", 
                    "chunks": [{"content": "Ph\u00c3\u00b2ng L\u00c3\u00aa Ch\u00c3\u00a2n"}],
                }],
            }

    with pytest.raises(ValueError, match="mojibake"):
        service.LegalRetriever.document_pdf(StubRetriever(), "43", article="1")


def test_mojibake_guard_allows_valid_vietnamese_a_circumflex_and_tilde():
    import scripts.legal_search_server as service

    valid = "ỦY BAN NHÂN DÂN CẤP XÃ"
    assert service._contains_probable_mojibake(valid) is False


def test_cached_pdf_artifact_avoids_duplicate_export(tmp_path, monkeypatch):
    import scripts.legal_search_server as service

    monkeypatch.setattr(service, "LEGAL_PDF_ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(service, "LEGAL_PDF_CACHE_MAX_AGE_SECONDS", 3600)
    calls = {"count": 0}

    def fake_export(doc_id, article=None):
        calls["count"] += 1
        return b"%PDF-fake", "test.pdf"

    monkeypatch.setattr(service.retriever, "document_pdf", fake_export)
    first, first_hit = service._cached_pdf_artifact("99", "6")
    second, second_hit = service._cached_pdf_artifact("99", "6")

    assert first == second
    assert first.read_bytes() == b"%PDF-fake"
    assert first_hit is False
    assert second_hit is True
    assert calls["count"] == 1


def test_original_pdf_headers_include_streaming_cache_metadata(tmp_path):
    source = Path("api/routers/legal_search.py").read_text(encoding="utf-8")
    assert '"X-Legal-Pdf-Origin": "original-file"' in source
    assert '"Cache-Control": "private, max-age=86400"' in source
    assert 'FileResponse(' in source


def test_document_detail_uses_metadata_first_and_article_content_on_demand():
    source = Path("scripts/legal_search_server.py").read_text(encoding="utf-8")
    assert 'load_content = bool(article or include_content)' in source
    assert 'LEFT JOIN legal_article_chunks c ON c.article_id = a.id" if load_content else ""' in source
    assert 'X-Legal-View-Ms' in source
