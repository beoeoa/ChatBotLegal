import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = process.argv[2];
const reportPath = process.argv[3];
if (!workbookPath || !reportPath) throw new Error("workbook and report paths are required");
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const golden = wb.worksheets.getItem("Golden 1000");
const approval = wb.worksheets.getItem("Biên bản duyệt");
const ids = golden.getRange("A6:A1005").values.flat().filter(Boolean);
const statuses = golden.getRange("L6:L1005").values.flat();
const decisions = golden.getRange("M6:M1005").values.flat();
const errors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "approved workbook formula errors",
});
const checks = {
  exact_1000_unique_cases: ids.length === 1000 && new Set(ids).size === 1000,
  every_machine_status_approved: statuses.length === 1000 && statuses.every(value => value === "approved"),
  every_review_decision_approved: decisions.length === 1000 && decisions.every(value => value === "Duyệt"),
  approval_receipt_sheet_present: approval.getRange("B5").values[0][0] === "approved",
  exact_10_sheets: wb.worksheets.items.length === 10,
  formula_errors_zero: errors.ndjson.includes("matched 0 entries"),
  render_files_complete: (await fs.readdir(path.join(path.dirname(workbookPath), "render-approved"))).filter(name => name.endsWith(".png")).length === 10,
};
const report = {
  schema_version: "golden-1000-approved-workbook-verification-v1",
  workbook: workbookPath,
  all_passed: Object.values(checks).every(Boolean),
  checks,
  counts: { cases: ids.length, approved_statuses: statuses.filter(value => value === "approved").length, approved_decisions: decisions.filter(value => value === "Duyệt").length, sheets: wb.worksheets.items.length },
};
await fs.writeFile(reportPath, JSON.stringify(report, null, 2), "utf8");
console.log(JSON.stringify(report));
if (!report.all_passed) process.exitCode = 1;
