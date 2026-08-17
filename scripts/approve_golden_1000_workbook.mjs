import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const input = process.argv[2];
const output = process.argv[3];
const receiptPath = process.argv[4];
if (!input || !output || !receiptPath) {
  throw new Error("usage: node approve_golden_1000_workbook.mjs INPUT OUTPUT RECEIPT");
}

const receipt = JSON.parse(await fs.readFile(receiptPath, "utf8"));
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const golden = wb.worksheets.getItem("Golden 1000");
const statusBefore = golden.getRange("L6:L1005").values.flat();
const decisionsBefore = golden.getRange("M6:M1005").values.flat();
if (statusBefore.length !== 1000 || statusBefore.some(value => value !== "pending_human_review")) {
  throw new Error("approval refused: workbook machine status is not 1,000 pending_human_review rows");
}
if (decisionsBefore.some(value => value !== "Chưa duyệt")) {
  throw new Error("approval refused: workbook contains a non-pending review decision");
}

golden.getRange("A1:N1").values = [["GOLDEN 1.000 — ĐÃ PHÊ DUYỆT TOÀN BỘ"]];
golden.getRange("L6:L1005").values = Array.from({ length: 1000 }, () => ["approved"]);
golden.getRange("M6:M1005").values = Array.from({ length: 1000 }, () => ["Duyệt"]);
golden.getRange("N6:N1005").values = Array.from({ length: 1000 }, () => [
  "Người dùng xác nhận đã xem toàn bộ và đồng ý duyệt tất cả trong task Codex ngày 11/08/2026.",
]);
golden.getRange("L6:M1005").format.fill = "#DCFCE7";
golden.getRange("L6:M1005").format.font = { color: "#166534", bold: true };

const summary = wb.worksheets.getItem("Tổng quan");
summary.getRange("A1:J1").values = [["GOLDEN 1.000 ĐỘC LẬP — ĐÃ PHÊ DUYỆT"]];
summary.getRange("A2:J2").values = [[
  "Đã được người dùng xác nhận duyệt toàn bộ ngày 11/08/2026; mọi thay đổi sau phát hành sẽ làm thay đổi hash trong biên bản.",
]];

const approval = wb.worksheets.add("Biên bản duyệt");
approval.showGridLines = false;
approval.getRange("A1:F1").merge();
approval.getRange("A1").values = [["BIÊN BẢN PHÊ DUYỆT GOLDEN 1.000"]];
approval.getRange("A2:F2").merge();
approval.getRange("A2").values = [[
  "Ghi nhận quyết định duyệt toàn bộ; file được bảo vệ bằng hash trong manifest và approval receipt.",
]];
approval.getRange("A4:B15").values = [
  ["Thuộc tính", "Giá trị"],
  ["Trạng thái", "approved"],
  ["Quyết định", "Duyệt toàn bộ 1.000 ca"],
  ["Người duyệt", "workspace_user"],
  ["Kênh xác nhận", "Codex task"],
  ["Ngày hiệu lực dữ liệu", receipt.legal_as_of],
  ["Thời điểm ghi nhận", new Date(receipt.approved_at)],
  ["Số ca", receipt.case_count],
  ["Số claim", receipt.claim_count],
  ["Số nguồn", receipt.source_count],
  ["Hash biên bản", receipt.entry_hash],
  ["Thay đổi corpus/vector production", "Không"],
];
approval.getRange("D4:F4").merge();
approval.getRange("D4").values = [["Kiểm soát phát hành"]];
approval.getRange("D5:F10").merge();
approval.getRange("D5").values = [[
  "Bản trước duyệt được giữ nguyên. Bản này ghi 1.000 quyết định Duyệt. " +
  "Mọi ca phải được tái kiểm tra khi văn bản nguồn thay đổi hiệu lực; không tự động thay Điều/khoản hoặc nguồn pháp luật.",
]];
approval.getRange("A1:F1").format = {
  fill: "#153B53",
  font: { color: "#FFFFFF", bold: true, size: 18 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
approval.getRange("A2:F2").format = {
  fill: "#D9EEF7",
  font: { italic: true, color: "#36566B" },
  wrapText: true,
  verticalAlignment: "center",
};
approval.getRange("A4:B4").format = {
  fill: "#148277", font: { color: "#FFFFFF", bold: true },
};
approval.getRange("D4:F4").format = {
  fill: "#148277", font: { color: "#FFFFFF", bold: true }, horizontalAlignment: "center",
};
approval.getRange("A5:A15").format = { fill: "#EAF5F4", font: { bold: true } };
approval.getRange("B5:B15").format.wrapText = true;
approval.getRange("B10").format.numberFormat = "yyyy-mm-dd hh:mm:ss";
approval.getRange("D5:F10").format = {
  fill: "#ECFDF5", font: { color: "#166534" }, wrapText: true, verticalAlignment: "top",
};
approval.getRange("A1:F15").format.borders = { preset: "outside", style: "thin", color: "#B9CDD8" };
approval.getRange("A1:F2").format.rowHeight = 34;
approval.getRange("A:A").format.columnWidth = 28;
approval.getRange("B:B").format.columnWidth = 72;
approval.getRange("C:C").format.columnWidth = 3;
approval.getRange("D:F").format.columnWidth = 24;
approval.freezePanes.freezeRows(4);

const inspection = await wb.inspect({
  kind: "table",
  range: "'Golden 1000'!L1:N15",
  include: "values,formulas",
  tableMaxRows: 15,
  tableMaxCols: 3,
  maxChars: 6000,
});
const errors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "approved workbook formula errors",
});

await fs.mkdir(path.dirname(output), { recursive: true });
const renderDir = path.join(path.dirname(output), "render-approved");
await fs.mkdir(renderDir, { recursive: true });
const renderNames = [
  ["Tổng quan", "A1:J25", "01-tong-quan"], ["Hướng dẫn duyệt", "A1:E12", "02-huong-dan"],
  ["Golden 1000", "A1:N16", "03-golden"], ["Claims", "A1:M16", "04-claims"],
  ["Nguồn chính thức", "A1:O15", "05-nguon"], ["Bằng chứng vật lý", "A1:J14", "06-bang-chung"],
  ["Nguồn cấm", "A1:D15", "07-nguon-cam"], ["Hiệu lực", "A1:J15", "08-hieu-luc"],
  ["Kiểm tra", "A1:D24", "09-kiem-tra"], ["Biên bản duyệt", "A1:F15", "10-bien-ban"],
];
for (const [sheetName, range, fileName] of renderNames) {
  const preview = await wb.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(renderDir, `${fileName}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const exported = await SpreadsheetFile.exportXlsx(wb);
await exported.save(output);
await fs.writeFile(`${output}.inspect.ndjson`, inspection.ndjson, "utf8");
await fs.writeFile(path.join(path.dirname(output), "approved-formula-errors.ndjson"), errors.ndjson, "utf8");
console.log(JSON.stringify({ output, sheets: wb.worksheets.items.length, renders: renderNames.length, formulaErrors: errors.ndjson }));
