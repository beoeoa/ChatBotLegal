from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTRACT = ROOT / "scripts" / "qa_log_export_contract.mjs"
EXPORTER = ROOT / "scripts" / "export_qa_log_excel.mjs"


def test_export_contract_uses_evaluation_not_has_sources(tmp_path: Path):
    runner = tmp_path / "check.mjs"
    contract_uri = CONTRACT.as_uri()
    runner.write_text(
        f"""
import {{ normalizeRun, summarizeRuns }} from {json.dumps(contract_uri)};
const rows = [
  normalizeRun({{run_id:'1',case_id:'a',role:'citizen',domain:'x',question:'q',answer:'wrong',has_sources:true,evaluation:{{passed:false,reason_codes:['failed_correct_source']}},citations:[]}}),
  normalizeRun({{run_id:'2',case_id:'b',role:'citizen',domain:'x',question:'q2',answer:'right',has_sources:false,evaluation:{{passed:true}},citations:[]}})
];
process.stdout.write(JSON.stringify({{rows, summary:summarizeRuns(rows)}}));
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(runner)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["rows"][0]["evaluationLabel"] == "Không đạt"
    assert result["rows"][1]["evaluationLabel"] == "Đạt"
    assert result["summary"]["passed"] == 1
    assert result["summary"]["failed"] == 1
    assert "has_sources" not in EXPORTER.read_text(encoding="utf-8")


def test_export_contract_rejects_dom_answer(tmp_path: Path):
    runner = tmp_path / "check.mjs"
    runner.write_text(
        f"""
import {{ normalizeRun }} from {json.dumps(CONTRACT.as_uri())};
try {{
  normalizeRun({{run_id:'1',case_id:'a',role:'citizen',domain:'x',question:'q',answer:'region "Ask Response": textbox "Enter your question"'}});
  process.exit(2);
}} catch (error) {{
  process.stdout.write(error.message);
}}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(runner)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "DOM/accessibility" in completed.stdout
