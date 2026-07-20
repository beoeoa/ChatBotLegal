from pathlib import Path

from api.legal_section_grounding import is_section_grounding_enabled
from api.models import AskResponse


def test_section_grounding_is_false_by_default_and_legacy_response_stays_flat(monkeypatch):
    monkeypatch.delenv("LEGAL_SECTION_GROUNDING_ENABLED", raising=False)

    assert is_section_grounding_enabled() is False
    assert AskResponse(answer="Nội dung cũ", question="Câu hỏi").answer_sections is None


def test_example_configuration_keeps_section_grounding_disabled():
    env_example = Path(__file__).parents[1] / ".env.example"

    assert "LEGAL_SECTION_GROUNDING_ENABLED=false" in env_example.read_text(encoding="utf-8")


def test_flag_can_be_enabled_without_changing_legacy_contract(monkeypatch):
    monkeypatch.setenv("LEGAL_SECTION_GROUNDING_ENABLED", "true")

    assert is_section_grounding_enabled() is True
    assert AskResponse(answer="Nội dung cũ", question="Câu hỏi").answer_sections is None
