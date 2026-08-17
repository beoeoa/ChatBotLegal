import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const outDir = "J:/ChatBotLegal/outputs/golden-1000-complete";
const workbookPath = path.join(outDir, "golden-1000-complete.xlsx");
const validation = JSON.parse(await fs.readFile(path.join(outDir, "validation-report.json"), "utf8"));
const expectedClaims = validation.counts.claims;
const expectedSources = validation.counts.sources;
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const expectedSheets = ["Tổng quan", "Hướng dẫn duyệt", "Golden 1000", "Claims", "Nguồn chính thức", "Bằng chứng vật lý", "Nguồn cấm", "Hiệu lực", "Kiểm tra"];
const actualSheets = wb.worksheets.items.map(sheet => sheet.name);
const golden = wb.worksheets.getItem("Golden 1000");
const claims = wb.worksheets.getItem("Claims");
const sources = wb.worksheets.getItem("Nguồn chính thức");
const caseIds = golden.getRange("A6:A1005").values.flat().filter(Boolean);
const claimIds = claims.getRange("A6:A2005").values.flat().filter(Boolean);
const sourceIds = sources.getRange("A6:A1205").values.flat().filter(Boolean);
const formulaErrors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "formula errors" });
const formulaErrorFree = formulaErrors.ndjson.includes("matched 0 entries");
const checks = {
  exact_sheet_set: JSON.stringify(actualSheets) === JSON.stringify(expectedSheets),
  exact_1000_case_rows: caseIds.length === 1000 && new Set(caseIds).size === 1000,
  exact_claim_rows: claimIds.length === expectedClaims,
  exact_source_rows: sourceIds.length === expectedSources,
  formula_errors_zero: formulaErrorFree,
  review_dropdown_present: Boolean(golden.getRange("M6:M1005").dataValidation),
  render_files_complete: (await fs.readdir(path.join(outDir, "render"))).filter(name => name.endsWith(".png")).length === 9,
};
const report = {
  schema_version: "golden-1000-workbook-verification-v1",
  workbook: workbookPath,
  all_passed: Object.values(checks).every(Boolean),
  checks,
  counts: { sheets: actualSheets.length, cases: caseIds.length, claims: claimIds.length, sources: sourceIds.length },
  sheets: actualSheets,
  formula_error_scan: formulaErrors.ndjson,
};
await fs.writeFile(path.join(outDir, "workbook-verification.json"), JSON.stringify(report, null, 2), "utf8");
console.log(JSON.stringify(report));
if (!report.all_passed) process.exitCode = 1;
