import itertools
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "pilot" / "ask_quality_manifest.json"
EXPERT_PATH = ROOT / "notebook_data" / "legal-golden-expert-review.json"

DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc",
)
ROLES = ("citizen", "officer")
SCENARIO_CLASSES = ("routine", "complex", "boundary")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def test_manifest_is_exact_5_by_2_by_3_cartesian_product():
    manifest = _read(MANIFEST_PATH)
    cases = manifest["cases"]

    assert len(cases) == 30
    assert len({item["review_id"] for item in cases}) == 30
    actual = {
        (item["domain"], item["role"], item["scenario_class"])
        for item in cases
    }
    assert actual == set(itertools.product(DOMAINS, ROLES, SCENARIO_CLASSES))
    assert tuple(manifest["domains"]) == DOMAINS
    assert tuple(manifest["roles"]) == ROLES
    assert tuple(manifest["scenario_classes"]) == SCENARIO_CLASSES


def test_manifest_references_only_existing_review_records_without_copying_answers():
    manifest = _read(MANIFEST_PATH)
    expert = _read(EXPERT_PATH)
    expert_by_id = {
        str(item["review_id"]): item for item in expert.get("records") or []
    }

    for item in manifest["cases"]:
        assert set(item) == {"review_id", "domain", "role", "scenario_class"}
        review = expert_by_id[item["review_id"]]
        assert review["domain"] == item["domain"]
        assert review["role"] == item["role"]

