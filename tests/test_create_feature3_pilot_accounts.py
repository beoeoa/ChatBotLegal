from scripts.create_feature3_pilot_accounts import _env_quote


def test_pilot_credential_writer_escapes_line_breaks_without_printing_secrets():
    assert _env_quote("abc\ndef") == "abcdef"
    assert _env_quote(r"a\\b") == r"a\\\\b"


def test_pilot_account_script_uses_distinct_role_identifiers():
    source = open("scripts/create_feature3_pilot_accounts.py", encoding="utf-8").read()
    assert "pilot_citizen_" in source
    assert "pilot_officer_" in source
    assert "PILOT_ISOLATION_ACCOUNT_A_TOKEN" in source
    assert "print(\"Created two isolated pilot test accounts" in source
