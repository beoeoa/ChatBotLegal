from pathlib import Path

from scripts.capture_golden294_live_baseline import TARGET_LAWS, _sha256


def test_target_scope_includes_phase9_and_reviewed_local_exact_documents():
    assert TARGET_LAWS == (
        "31/2024/QH15",
        "73/2025/QH15",
        "55/2021/TT-BCA",
        "66/2023/TT-BCA",
        "88/2025/QH15",
        "116/2026/TT-BCA",
        "62/2020/QH14",
        "43/2025/NQ-HDND",
    )


def test_sha256_is_stable_and_missing_is_explicit(tmp_path: Path):
    source = tmp_path / "evidence.txt"
    source.write_bytes(b"official-evidence")
    assert _sha256(source) == "8be3bf5864870bd3a60917f898e13f40d00bbf4af01ac9530357e23e35af322d"
    assert _sha256(tmp_path / "missing.txt") is None
