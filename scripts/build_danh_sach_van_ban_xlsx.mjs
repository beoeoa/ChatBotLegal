import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const ROOT = "J:/ChatBotLegal";
const snapshotPath = `${ROOT}/reports/retrieval-release-v2/source-snapshot-12236.json`;
const auditPath = `${ROOT}/reports/retrieval-release-v2/source-content-audit-12236-v3.json`;
const outputDir = `${ROOT}/outputs/retrieval-v2`;
const outputPath = `${outputDir}/danh_sach_van_ban.xlsx`;

function cleanLawNumber(raw) {
  const text = String(raw ?? "")
    .normalize("NFC")
    .replace(/[–—−]/g, "-")
    .replace(/[／⁄]/g, "/")
    .trim();
  const standard = text.match(/\b\d{1,4}\s*[\/-]\s*\d{2,4}(?:\s*[\/-]\s*[\p{L}\p{N}]+(?:\s*[\/-]\s*[\p{L}\p{N}]+)*)?/gu) ?? [];
  const legacy = text.match(/\b\d{1,4}\s*[\/-]\s*[\p{L}\p{N}]+(?:\s*[\/-]\s*[\p{L}\p{N}]+)*/gu) ?? [];
  const candidates = [...new Set([...standard, ...legacy])]
    .map((value) => value.replace(/\s+/g, "").toUpperCase())
    .filter((value) => value.length > 2);
  return {
    clean: candidates[0] ?? "",
    candidates: candidates.join(" | "),
    reason: candidates.length === 0 ? "missing_or_unrecognized" : candidates.length === 1 ? "normalized" : "multiple_candidates_review",
  };
}

function esc(value) {
  return value == null ? "" : String(value);
}

const snapshot = JSON.parse(await fs.readFile(snapshotPath, "utf8"));
const audit = JSON.parse(await fs.readFile(auditPath, "utf8"));
const auditById = new Map((audit.records ?? []).map((row) => [Number(row.document_id), row]));
const rows = (snapshot.documents ?? []).map((document) => {
  const auditRow = auditById.get(Number(document.document_id)) ?? {};
  const cleaned = cleanLawNumber(document.law_number);
  const transport = auditRow.transport_observation ?? {};
  const sourceEvidence = auditRow.source_content_evidence ?? {};
  return [
    Number(document.document_id),
    esc(document.title),
    esc(document.law_number),
    cleaned.clean,
    cleaned.candidates,
    cleaned.reason,
    esc(document.issuing_agency),
    esc(document.status),
    esc(document.effective_date),
    esc(document.expired_date),
    esc(document.scope),
    esc(document.sector),
    esc(document.source_url),
    esc(transport.transport_status),
    esc(auditRow.verification_status),
    sourceEvidence.verified === true,
    esc(auditRow.serving_state_observed),
  ];
});

const headers = [
  "document_id",
  "Tên văn bản",
  "Số hiệu gốc",
  "Số hiệu sạch",
  "Ứng viên số hiệu",
  "Kết quả làm sạch",
  "Cơ quan ban hành",
  "Trạng thái DB",
  "Ngày hiệu lực",
  "Ngày hết hiệu lực",
  "Phạm vi",
  "Lĩnh vực",
  "URL hiện tại",
  "Transport URL",
  "Xác minh nội dung",
  "Identity verified",
  "Serving state quan sát",
];

const total = rows.length;
const uniqueIds = new Set(rows.map((row) => row[0])).size;
const emptyClean = rows.filter((row) => !row[3]).length;
const ambiguousClean = rows.filter((row) => row[5] === "multiple_candidates_review").length;
const verified = rows.filter((row) => row[15] === true).length;
const transportCounts = {};
const verificationCounts = {};
for (const row of rows) {
  transportCounts[row[13] || "missing"] = (transportCounts[row[13] || "missing"] ?? 0) + 1;
  verificationCounts[row[14] || "missing"] = (verificationCounts[row[14] || "missing"] ?? 0) + 1;
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Tổng quan");
const data = workbook.worksheets.add("Danh sách văn bản");
summary.showGridLines = false;
data.showGridLines = false;

summary.getRange("A1:F1").merge();
summary.getRange("A1").values = [["Danh sách 12.236 văn bản – chuẩn hóa số hiệu và audit URL"]];
summary.getRange("A1:F1").format = { fill: "#0F766E", font: { bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "center", verticalAlignment: "center" };
summary.getRange("A3:B10").values = [
  ["Chỉ tiêu", "Giá trị"],
  ["Tổng document", total],
  ["Document ID duy nhất", uniqueIds],
  ["Số hiệu sạch rỗng", emptyClean],
  ["Số hiệu có nhiều ứng viên", ambiguousClean],
  ["Identity đã xác minh nội dung", verified],
  ["SHA-256 source audit v3", esc(audit.report_sha256)],
  ["Cảnh báo", "HTTP 200 không đồng nghĩa nguồn hợp lệ; URL đề xuất phải verify lại."],
];
summary.getRange("A3:B3").format = { fill: "#D1FAE5", font: { bold: true, color: "#064E3B" } };
summary.getRange("A3:B10").format.borders = { preset: "all", style: "thin", color: "#D1D5DB" };
summary.getRange("A12:B12").values = [["Transport URL", "Số lượng"]];
summary.getRange(`A13:B${12 + Object.keys(transportCounts).length}`).values = Object.entries(transportCounts);
summary.getRange("A12:B12").format = { fill: "#DBEAFE", font: { bold: true, color: "#1E3A8A" } };
summary.getRange(`D12:E12`).values = [["Xác minh nội dung", "Số lượng"]];
summary.getRange(`D13:E${12 + Object.keys(verificationCounts).length}`).values = Object.entries(verificationCounts);
summary.getRange("D12:E12").format = { fill: "#DBEAFE", font: { bold: true, color: "#1E3A8A" } };
summary.getRange("A12:E30").format.borders = { preset: "all", style: "thin", color: "#D1D5DB" };
summary.getRange("A1:F1").format.rowHeight = 28;
summary.getRange("A:A").format.columnWidth = 28;
summary.getRange("B:B").format.columnWidth = 32;
summary.getRange("D:D").format.columnWidth = 28;
summary.getRange("E:E").format.columnWidth = 16;

data.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
data.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
data.getRange(`A1:Q${total + 1}`).format.font = { size: 9 };
data.getRange("A1:Q1").format = { fill: "#0F766E", font: { bold: true, color: "#FFFFFF", size: 9 }, wrapText: true, horizontalAlignment: "center", verticalAlignment: "center" };
data.getRange(`A1:Q${total + 1}`).format.borders = { preset: "all", style: "thin", color: "#E5E7EB" };
data.getRange(`A2:A${total + 1}`).format.numberFormat = "0";
data.getRange(`P2:P${total + 1}`).format.horizontalAlignment = "center";
data.getRange(`M2:M${total + 1}`).format.wrapText = true;
data.getRange(`B2:B${total + 1}`).format.wrapText = true;
data.getRange(`A1:Q${total + 1}`).format.autofitColumns();
for (const [column, width] of [["A", 12], ["B", 42], ["C", 22], ["D", 22], ["E", 32], ["F", 24], ["G", 28], ["H", 16], ["I", 14], ["J", 14], ["K", 20], ["L", 20], ["M", 55], ["N", 15], ["O", 18], ["P", 16], ["Q", 22]]) {
  data.getRange(`${column}:${column}`).format.columnWidth = width;
}
data.getRange("A1:Q1").format.rowHeight = 34;
data.freezePanes.freezeRows(1);
data.freezePanes.freezeColumns(2);
data.tables.add(`A1:Q${total + 1}`, true, "LegalDocumentsCleanTable");

const inspect = await workbook.inspect({ kind: "table", sheetId: "Tổng quan", range: "A1:F20", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 6, maxChars: 5000 });
console.log(inspect.ndjson);
const preview = await workbook.render({ sheetName: "Tổng quan", range: "A1:F20", scale: 1, format: "png" });
await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(`${outputDir}/danh_sach_van_ban_preview.png`, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(JSON.stringify({ outputPath, total, uniqueIds, emptyClean, ambiguousClean, verified, transportCounts, verificationCounts }));
