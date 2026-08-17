from api.form_governance_models import canonical_sha256
from api.form_governance_release_validator import (
    DVC_ATTACHMENT_ENDPOINT,
    HttpFormSourceVerifier,
    validate_release_manifest,
)


def _manifest():
    return {
        "schema_version": "form-release-v1",
        "release_id": "release-test",
        "version": 1,
        "legal_as_of": "2026-08-11",
        "source_snapshot_sha256": "a" * 64,
        "previous_release_id": None,
        "procedures": [{
            "procedure_id": "p1",
            "name": "Thủ tục thử",
            "domain": "cu_tru_an_ninh",
            "official_source_url": "https://dichvucong.gov.vn/p1",
            "coverage_status": "released",
        }],
        "assets": [{
            "form_id": "f1",
            "canonical_name": "Mẫu thử",
            "asset_kind": "file",
            "source_url": "https://vbpl.vn/f1.pdf",
            "source_checksum": "b" * 64,
            "audiences": ["citizen"],
            "coverage_status": "released",
        }],
        "bindings": [{
            "binding_id": "b1",
            "procedure_id": "p1",
            "form_id": "f1",
            "requirement": "required",
            "audience": "citizen",
            "coverage_status": "released",
        }],
        "aliases": [{"procedure_id": "p1", "alias": "làm thủ tục thử", "alias_kind": "natural"}],
        "gaps": [],
        "exclusions": [],
        "coverage": {
            "procedure_total": 1,
            "procedure_decided": 1,
            "identity_total": 1,
            "identity_decided": 1,
            "binding_total": 1,
            "binding_decided": 1,
            "complete": True,
        },
        "build": {"pipeline_version": "test"},
    }


def test_release_gate_detects_manifest_tampering_and_remote_checksum_failure():
    manifest = _manifest()
    expected = canonical_sha256(manifest)
    manifest["assets"][0]["canonical_name"] = "Đã bị sửa"
    report = validate_release_manifest(
        manifest,
        expected_manifest_sha256=expected,
        source_verifier=lambda _asset: "FORM_CHECKSUM_MISMATCH",
    )
    assert report["passed"] is False
    assert "FORM_RELEASE_MANIFEST_TAMPERED" in report["errors"]
    assert "FORM_CHECKSUM_MISMATCH" in report["errors"]
    assert report["source_checks"]["mode"] == "remote"


def test_release_gate_rejects_coverage_not_matching_manifest():
    manifest = _manifest()
    manifest["coverage"]["identity_decided"] = 0
    report = validate_release_manifest(
        manifest,
        expected_manifest_sha256=canonical_sha256(manifest),
    )
    assert report["passed"] is False
    assert "FORM_COVERAGE_MANIFEST_MISMATCH" in report["errors"]


def test_release_gate_rejects_two_form_ids_for_the_same_normalized_asset():
    manifest = _manifest()
    duplicate = {**manifest["assets"][0], "form_id": "f2"}
    manifest["assets"].append(duplicate)
    manifest["bindings"].append({
        **manifest["bindings"][0],
        "binding_id": "b2",
        "form_id": "f2",
    })
    manifest["coverage"].update(
        identity_total=2,
        identity_decided=2,
        binding_total=2,
        binding_decided=2,
    )
    report = validate_release_manifest(
        manifest,
        expected_manifest_sha256=canonical_sha256(manifest),
    )
    assert report["passed"] is False
    assert "FORM_ASSET_IDENTITY_DUPLICATE" in report["errors"]


def test_release_gate_counts_owner_deferred_without_calling_it_verified_gap():
    manifest = _manifest()
    manifest["procedures"][0]["coverage_status"] = "owner_deferred"
    manifest["assets"] = []
    manifest["bindings"] = []
    common = {
        "reason_code": "USER_EXCLUDED_SUPPLEMENT_FROM_CURRENT_RELEASE",
        "decision_fingerprint": "c" * 64,
        "decided_by": "admin",
        "decided_at": "2026-08-12T00:00:00+07:00",
        "public_eligible": False,
        "router_eligible": False,
    }
    manifest["exclusions"] = [
        {**common, "target_type": "procedure", "target_id": "p1"},
        {**common, "target_type": "identity", "target_id": "identity-1"},
        {**common, "target_type": "binding", "target_id": "binding-identity-1-p1"},
    ]
    report = validate_release_manifest(
        manifest,
        expected_manifest_sha256=canonical_sha256(manifest),
    )
    assert report["passed"] is True
    assert report["errors"] == []


def test_http_verifier_fetches_exact_dvc_attachment_bytes(tmp_path) -> None:
    import hashlib
    import httpx

    content = b"official attachment bytes"
    attachment_id = "00000000-0000-0000-0000-000000000001"
    referer = "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET" and str(request.url) == referer:
            return httpx.Response(200, content=b"procedure page", request=request)
        if request.method == "POST" and str(request.url) == DVC_ATTACHMENT_ENDPOINT:
            assert attachment_id.encode() in request.content
            return httpx.Response(200, content=content, request=request)
        return httpx.Response(404, request=request)

    runtime_path = "feature017/release/f1.docx"
    runtime_file = tmp_path / runtime_path
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(content)
    verifier = HttpFormSourceVerifier(
        transport=httpx.MockTransport(handler), runtime_root=tmp_path
    )
    asset = {
        "form_id": "f1",
        "asset_kind": "file",
        "source_url": referer,
        "source_checksum": hashlib.sha256(content).hexdigest(),
        "runtime_path": runtime_path,
        "download_url": "/api/procedures/forms-catalog/assets/f1/download",
        "provenance": {
            "canonical_artifact": {
                "attachment_id": attachment_id,
                "official_endpoint": DVC_ATTACHMENT_ENDPOINT,
                "referer_url": referer,
            }
        },
    }

    assert verifier(asset) is None
    assert calls == [("GET", referer), ("POST", DVC_ATTACHMENT_ENDPOINT)]


def test_http_verifier_rejects_invalid_dvc_attachment_provenance(tmp_path) -> None:
    runtime_path = "feature017/release/f1.docx"
    runtime_file = tmp_path / runtime_path
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(b"runtime")
    verifier = HttpFormSourceVerifier(runtime_root=tmp_path)
    asset = {
        "form_id": "f1",
        "asset_kind": "file",
        "source_url": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1",
        "source_checksum": __import__("hashlib").sha256(b"runtime").hexdigest(),
        "runtime_path": runtime_path,
        "download_url": "/api/procedures/forms-catalog/assets/f1/download",
        "provenance": {
            "canonical_artifact": {
                "attachment_id": "not-a-uuid",
                "official_endpoint": DVC_ATTACHMENT_ENDPOINT,
            }
        },
    }

    assert verifier(asset) == "FORM_DVC_ATTACHMENT_ID_INVALID"
