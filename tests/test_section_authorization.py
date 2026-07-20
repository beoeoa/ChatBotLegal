"""Feature 005 regression tests for cross-account Ask isolation."""

import pytest

from api.routers.search import _resolve_ask_session_owner_key


def test_direct_conversation_or_evidence_identifier_cannot_replace_authenticated_owner():
    authenticated_owner = _resolve_ask_session_owner_key(
        user_id="citizen-current", role="citizen"
    )
    attempted_foreign_id = "citizen-other:conversation-123:evidence-456"

    assert authenticated_owner == "citizen-current"
    assert attempted_foreign_id not in authenticated_owner


@pytest.mark.parametrize("role", ["citizen", "officer"])
def test_missing_authenticated_identity_has_no_legacy_role_owner_fallback(role):
    assert _resolve_ask_session_owner_key(user_id=None, role=role) is None
