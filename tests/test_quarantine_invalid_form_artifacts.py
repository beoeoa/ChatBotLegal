from scripts.quarantine_invalid_form_artifacts import quarantine_payload


def test_quarantine_preserves_human_legal_decision_and_blocks_runtime():
    digest = "a" * 64
    payload = {
        "forms": [
            {
                "form_id": "form-1",
                "sha256": digest,
                "review_status": "approved",
                "legal_review_status": "approved",
                "approved": True,
                "runtime_eligible": True,
                "catalog_status": "available_official_source",
            }
        ]
    }

    result, changed = quarantine_payload(
        payload,
        collection_key="forms",
        artifact_hashes={digest},
    )

    form = result["forms"][0]
    assert form["review_status"] == "approved"
    assert form["legal_review_status"] == "approved"
    assert form["approved"] is True
    assert form["runtime_eligible"] is False
    assert form["is_quarantined"] is True
    assert form["catalog_status"] == "quarantined"
    assert changed[0]["legal_decision_preserved"] is True


def test_quarantine_ignores_unrelated_artifact():
    payload = {"records": [{"id": "candidate", "sha256": "b" * 64}]}

    result, changed = quarantine_payload(
        payload,
        collection_key="records",
        artifact_hashes={"a" * 64},
    )

    assert result == payload
    assert changed == []
