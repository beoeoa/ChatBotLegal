from api.routers.search import _ask_error_response
from open_notebook.exceptions import (
    ConfigurationError,
    LegalRetrievalUnavailableError,
    NetworkError,
    RateLimitError,
)


def test_retrieval_error_has_actionable_recovery_steps():
    status, detail = _ask_error_response(
        LegalRetrievalUnavailableError(
            "Không kết nối được dịch vụ tra cứu pháp luật."
        )
    )

    assert status == 503
    assert detail["code"] == "LEGAL_RETRIEVAL_UNAVAILABLE"
    assert any("start_legal_search.ps1" in step for step in detail["how_to_fix"])
    assert detail["retryable"] is True


def test_provider_errors_have_distinct_codes():
    cases = [
        (RateLimitError("quota"), 429, "AI_PROVIDER_QUOTA_OR_RATE_LIMIT"),
        (ConfigurationError("bad model"), 400, "AI_MODEL_CONFIGURATION_ERROR"),
        (NetworkError("offline"), 503, "AI_PROVIDER_UNAVAILABLE"),
    ]

    for error, expected_status, expected_code in cases:
        status, detail = _ask_error_response(error)
        assert status == expected_status
        assert detail["code"] == expected_code
        assert detail["how_to_fix"]
