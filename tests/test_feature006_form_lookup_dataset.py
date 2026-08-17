from datetime import date

from api.legal_form_catalog import FormCatalog
from scripts.build_form_lookup_release_dataset import DEFAULT_MANIFEST, build_dataset
from scripts.evaluate_form_catalog import evaluate, verify_dataset_sources


def test_release_dataset_covers_all_418_procedures_once():
    dataset = build_dataset(DEFAULT_MANIFEST)

    assert dataset["procedure_count"] == 418
    assert len({case["procedure_id"] for case in dataset["cases"]}) == 418
    assert all(case["query"] for case in dataset["cases"])


def test_form_catalog_exact_lookup_has_no_wrong_or_pending_forms():
    report = evaluate(
        catalog=FormCatalog.load_default(),
        dataset=build_dataset(DEFAULT_MANIFEST),
        legal_as_of=date(2026, 7, 30),
    )

    assert report["technical_pass"] is True
    assert report["exact_form_recall"] == 1.0
    assert report["wrong_form_count"] == 0
    assert report["missing_form_count"] == 0
    assert report["pending_form_exposure_count"] == 0
    assert report["expired_form_exposure_count"] == 0
    assert report["role_leakage_count"] == 0


def test_form_release_dataset_is_bound_to_current_source_checksums():
    dataset = build_dataset(DEFAULT_MANIFEST)

    integrity = verify_dataset_sources(dataset)

    assert integrity["fresh"] is True
    assert integrity["stale_sources"] == []


def test_form_quality_gate_rejects_a_stale_release_dataset():
    dataset = build_dataset(DEFAULT_MANIFEST)
    dataset["catalog_sha256"] = "0" * 64
    integrity = verify_dataset_sources(dataset)

    report = evaluate(
        catalog=FormCatalog.load_default(),
        dataset=dataset,
        legal_as_of=date(2026, 7, 30),
        dataset_integrity=integrity,
    )

    assert integrity["fresh"] is False
    assert integrity["stale_sources"] == ["catalog"]
    assert report["technical_pass"] is False
