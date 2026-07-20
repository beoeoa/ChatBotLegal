import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

def test_legal_search_citations_internal_url(monkeypatch):
    """Test that search citations map to canonical internal_url and fallback url."""
    from api.routers.search import _build_citations_from_retrieval
    
    mock_retrieval = [
        {
            "chunk_id": "123",
            "doc_id": "456",
            "law_number": "01/2024/N?-CP",
            "document_title": "Ngh? ??nh quy ??nh ABC",
            "article_number": "5",
            "clause_number": "2",
            "point_number": "a",
            "issuing_agency": "Ch?nh ph?",
            "scope": "Trung ??ng",
            "source_url": "https://vbpl.vn/broken-link",
        }
    ]
    
    citations = _build_citations_from_retrieval(mock_retrieval)
    assert len(citations) == 1
    assert citations[0]["doc_id"] == "456"
    assert citations[0]["internal_url"] == "/legal-documents/456?article=5&clause=2&point=a"
    assert citations[0]["clause_number"] == "2"
    assert citations[0]["point_number"] == "a"
    assert citations[0]["source_metadata"]["issuing_agency"] == "Ch?nh ph?"
    assert "https://vbpl.vn/van-ban/tim-kiem?q=" in citations[0]["fallback_search_url"]
    assert "01%2F2024%2FN" in citations[0]["fallback_search_url"]

def test_document_pdf_fallback_label(monkeypatch):
    """Test that generated PDF has the system extract label."""
    import scripts.legal_search_server
    
    class MockRetriever:
        def document_detail(self, doc_id, article=None, include_content=False):
            return {
                "document_title": "Test Document",
                "law_number": "123/QD",
                "source_url": "http://example.com/source",
                "source_file_available": False,
                "article_index": [
                    {"article_number": "1", "article_title": "Test Title", "has_content": True}
                ],
                "articles": [
                    {
                        "article_number": "1",
                        "article_title": "Test Title",
                        "chunks": [{"content": "Test content line 1."}]
                    }
                ]
            }
        
    retriever = MockRetriever()
    # patch the document_pdf function onto our mock, maintaining self
    original_pdf = scripts.legal_search_server.LegalRetriever.document_pdf
    
    def bound_pdf(*args, **kwargs):
        return original_pdf(retriever, *args, **kwargs)
        
    try:
        pdf_bytes, filename = bound_pdf("doc-id", article="1")
        assert filename == "123-QD.pdf"
        
        # Test PDF content to ensure the label was injected
        import fitz
        pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = pdf[0].get_text("text")
        pdf.close()
        
        # Arial TTF is registered before writing; text extraction preserves the
        # Vietnamese label and inserted content (PyMuPDF uses NBSP for spaces).
        normalized = text.replace("\u00a0", " ")
        assert "\u0042\u1ea2N TR\u00cdCH XU\u1ea4T T\u1eea KHO H\u1ec6 TH\u1ed0NG" in normalized
        assert "Kh\u00f4ng ph\u1ea3i b\u1ea3n C\u00f4ng b\u00e1o" in normalized
        assert "Test content line 1" in normalized
        assert "http://example.com/source" in normalized
        
    except ImportError:
        pytest.skip("PyMuPDF not installed, skipping PDF generation test")

def test_document_detail_cache(monkeypatch):
    """Test that document metadata is cached."""
    import scripts.legal_search_server
    
    # clear cache
    scripts.legal_search_server._document_cache.clear()
    
    original_doc_detail = scripts.legal_search_server.LegalRetriever.document_detail
    
    call_count = 0
    def mock_doc_detail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return {"doc_id": "test", "content": "mock"}
        
    scripts.legal_search_server._set_cached_document("test_cache_id", {"doc_id": "test_cache_id", "content": "mock_cache", "article_index": []})
    
    cached = scripts.legal_search_server._get_cached_document("test_cache_id")
    assert cached["doc_id"] == "test_cache_id"
    assert "article_index" in cached
    scripts.legal_search_server._invalidate_document_cache("test_cache_id")
    assert scripts.legal_search_server._get_cached_document("test_cache_id") is None
