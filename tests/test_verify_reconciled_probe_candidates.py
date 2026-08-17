import hashlib
import json
from pathlib import Path

from pypdf import PdfWriter

from scripts.verify_reconciled_probe_candidates import (
    _is_official_url,
    verify_candidates,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    artifact = tmp_path / "artifacts" / "form.pdf"
    artifact.parent.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with artifact.open("wb") as handle:
        writer.write(handle)
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    queue = tmp_path / "queue.json"
    _write_json(
        queue,
        {
            "legal_as_of": "2026-07-30",
            "candidate_only": True,
            "automated_approval": False,
            "human_attestation_created": False,
            "feature_flag_enabled": False,
            "summary": {"runtime_eligible_count": 0},
            "records": [
                {
                    "requirement_identity_id": "identity-1",
                    "research_action": "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION",
                    "approved": False,
                    "runtime_eligible": False,
                    "technical_evidence": {
                        "sha256": sha,
                        "source_page_url": "https://vbpl.vn/van-ban/test",
                        "source_download_url": (
                            "https://datafiles.chinhphu.vn/test.pdf"
                        ),
                        "effectivity_source_url": "https://vbpl.vn/test",
                        "approved": False,
                        "runtime_eligible": False,
                        "human_attestation_required": True,
                    },
                }
            ],
        },
    )
    probe = tmp_path / "probe"
    _write_json(
        probe / "code-resolution.json",
        {
            "pending_records": [
                {
                    "sha256": sha,
                    "local_path": str(
                        artifact.relative_to(tmp_path)
                    ).replace("\\", "/"),
                    "size_bytes": artifact.stat().st_size,
                }
            ]
        },
    )
    return queue, probe, artifact


def test_probe_verification_checks_checksum_and_openability(tmp_path):
    queue, probe, _ = _fixture(tmp_path)

    report = verify_candidates(
        queue_path=queue,
        probe_dirs=[probe],
        root=tmp_path,
    )

    assert report["technical_pass"] is True
    assert report["candidate_count"] == report["passed_count"] == 1
    assert report["file_format_counts"] == {"pdf": 1}


def test_probe_verification_fails_closed_on_artifact_drift(tmp_path):
    queue, probe, artifact = _fixture(tmp_path)
    artifact.write_bytes(artifact.read_bytes() + b"drift")

    report = verify_candidates(
        queue_path=queue,
        probe_dirs=[probe],
        root=tmp_path,
    )

    assert report["technical_pass"] is False
    assert report["failed_count"] == 1
    assert "ARTIFACT_CHECKSUM_MISMATCH" in report["checks"][0]["reason_codes"]


def test_current_official_document_metadata_can_ground_effectivity(tmp_path):
    queue, probe, _ = _fixture(tmp_path)
    payload = json.loads(queue.read_text(encoding="utf-8"))
    evidence = payload["records"][0]["technical_evidence"]
    evidence["effectivity_source_url"] = None
    evidence["effectivity_reason_code"] = "CURRENT_AS_OF_DATE"
    evidence["effective_from"] = "2026-01-01"
    _write_json(queue, payload)

    report = verify_candidates(
        queue_path=queue,
        probe_dirs=[probe],
        root=tmp_path,
    )

    assert report["technical_pass"] is True


def test_gazette_cdn_subdomain_is_an_official_download_host():
    assert _is_official_url(
        "https://g7.cdnchinhphu.vn/api/download/stream?file_name=form.pdf"
    )
