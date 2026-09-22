"""Exclude explicitly labelled QA fixtures from citizen-facing FAQ reads."""
import re
from collections.abc import Mapping
from typing import Any


def is_qa_faq(item: Mapping[str, Any]) -> bool:
    if item.get("is_test") is True or item.get("is_fixture") is True:
        return True
    # Match labels only at the beginning; ordinary questions containing
    # 'test' (medical tests, examinations, etc.) must remain visible.
    return any(
        re.match(r"^\s*(?:\[QA\]|QA[_\s-]|TEST\s+E2E(?:[_\s:-]|$))", str(item.get(field) or ""), re.I)
        for field in ("question", "title")
    )
