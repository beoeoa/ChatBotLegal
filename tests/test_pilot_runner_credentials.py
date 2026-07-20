from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNERS = (
    "scripts/run_pilot_ask_matrix.py",
    "scripts/collect_334_live_answers.py",
    "scripts/check_334_asset_health.py",
    "scripts/evaluate_role_answers.py",
    "scripts/run_uat_api_smoke.py",
    "scripts/smoke_step20_pilot.py",
)


def test_pilot_runners_have_no_hardcoded_pilot_credential_or_bearer_value():
    for relative in RUNNERS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert '"gfi"' not in source, relative
        assert "'gfi'" not in source, relative
        assert "Bearer gfi" not in source, relative

