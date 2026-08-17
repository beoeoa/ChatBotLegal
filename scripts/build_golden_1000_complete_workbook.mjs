import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "J:/ChatBotLegal";
const outDir = path.join(root, "outputs/golden-1000-complete");
const readJson = async (name) => JSON.parse(await fs.readFile(path.join(outDir, name), "utf8"));
const data = await readJson("golden-1000-complete.json");
const evidencePayload = await readJson("claim-evidence-map.json");
const validation = await readJson("validation-report.json");
const sourceInventory = await readJson("source-inventory.json");
const validityGuards = await readJson("validity-guards.json");
const officialCurrent = await readJson("official-current-sources.json");
const cases = data.cases;
const evidence = evidencePayload.claims;
const evidenceByKey = new Map(evidence.map(row => [`${row.case_id}|${row.claim_id}`, row]));
const inventoryByArticle = new Map(sourceInventory.sources.map(row => [row.db_article_id, row]));
const currentByLaw = new Map(officialCurrent.sources.map(row => [row.law_number, row]));

const colors = {
  navy: "#12324A", teal: "#0F766E", blue: "#DCEFF7", pale: "#F5F8FA",
  amber: "#FFF2CC", red: "#FDE8E7", green: "#DDF3E4", white: "#FFFFFF",
  border: "#CAD6DE", ink: "#172B36", muted: "#526975",
};
const t = value => value === null || value === undefined ? "" : String(value);
const cat = item => item.risk_tags.find(tag => tag.startsWith("scenario_category_"))?.replace("scenario_category_", "") || "";
const safeTableName = name => name.replace(/[^A-Za-z0-9_]/g, "");

function setup(sheet) { sheet.showGridLines = false; }
function title(sheet, text, subtitle, lastColumn) {
  sheet.mergeCells(`A1:${lastColumn}1`);
  sheet.getRange("A1").values = [[text]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: colors.navy, font: { bold: true, color: colors.white, size: 15 },
    horizontalAlignment: "center", verticalAlignment: "center",
  };
  sheet.getRange("A1").format.rowHeight = 32;
  sheet.mergeCells(`A2:${lastColumn}2`);
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: colors.blue, font: { color: colors.navy, italic: true, size: 10 },
    wrapText: true, verticalAlignment: "center",
  };
  sheet.getRange("A2").format.rowHeight = 42;
}
function header(range) {
  range.format = {
    fill: colors.teal, font: { bold: true, color: colors.white, size: 9 },
    wrapText: true, horizontalAlignment: "center", verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: colors.border },
  };
  range.format.rowHeight = 34;
}
function body(range) {
  range.format = {
    font: { color: colors.ink, size: 9 }, wrapText: true, verticalAlignment: "top",
    borders: { insideHorizontal: { style: "thin", color: "#E1E8EC" } },
  };
}
function widths(sheet, values) {
  for (const [column, width] of Object.entries(values)) sheet.getRange(`${column}:${column}`).format.columnWidth = width;
}
function addTable(sheet, range, name) {
  const table = sheet.tables.add(range, true, safeTableName(name));
  table.style = "TableStyleMedium2";
  table.showFilterButton = true;
}
function writeTable(sheet, headers, rows, startRow, lastColumn, tableName) {
  sheet.getRange(`A${startRow}:${lastColumn}${startRow}`).values = [headers];
  header(sheet.getRange(`A${startRow}:${lastColumn}${startRow}`));
  if (rows.length) {
    const endRow = startRow + rows.length;
    sheet.getRange(`A${startRow + 1}:${lastColumn}${endRow}`).values = rows;
    body(sheet.getRange(`A${startRow + 1}:${lastColumn}${endRow}`));
    addTable(sheet, `A${startRow}:${lastColumn}${endRow}`, tableName);
  }
}

const wb = Workbook.create();
const summary = wb.worksheets.add("Tổng quan");
const guide = wb.worksheets.add("Hướng dẫn duyệt");
const golden = wb.worksheets.add("Golden 1000");
const claims = wb.worksheets.add("Claims");
const sources = wb.worksheets.add("Nguồn chính thức");
const physical = wb.worksheets.add("Bằng chứng vật lý");
const forbidden = wb.worksheets.add("Nguồn cấm");
const validity = wb.worksheets.add("Hiệu lực");
const checks = wb.worksheets.add("Kiểm tra");
for (const sheet of [summary, guide, golden, claims, sources, physical, forbidden, validity, checks]) setup(sheet);

title(summary, "GOLDEN 1.000 ĐỘC LẬP — GÓI DUYỆT CHUYÊN GIA", "Đã kiểm tra máy với nguồn chính thức và PostgreSQL tại ngày 11/08/2026. Trạng thái hiện tại: sẵn sàng để người có chuyên môn duyệt, chưa tự động coi là ground truth đã phê duyệt.", "J");
summary.getRange("A4:B4").values = [["Chỉ số", "Giá trị"]];
summary.getRange("D4:E4").values = [["Lĩnh vực", "Số ca"]];
summary.getRange("G4:H4").values = [["Cổng chất lượng", "Kết quả"]];
header(summary.getRange("A4:B4")); header(summary.getRange("D4:E4")); header(summary.getRange("G4:H4"));
summary.getRange("A5:A12").values = [["Tổng số ca"], ["Số claim"], ["Số nguồn"], ["Ca dự phòng"], ["Development"], ["Validation"], ["Held-out"], ["Ngày chốt hiệu lực"]];
summary.getRange("B5").formulas = [["=COUNTA('Golden 1000'!A6:A1005)"]];
summary.getRange("B6").formulas = [["=COUNTA(Claims!A6:A2005)"]];
summary.getRange("B7").formulas = [["=COUNTA('Nguồn chính thức'!A6:A1205)"]];
summary.getRange("B8").formulas = [["=COUNTIF('Golden 1000'!H6:H1005,TRUE)"]];
summary.getRange("B9").formulas = [["=COUNTIF('Golden 1000'!D6:D1005,\"development\")"]];
summary.getRange("B10").formulas = [["=COUNTIF('Golden 1000'!D6:D1005,\"validation\")"]];
summary.getRange("B11").formulas = [["=COUNTIF('Golden 1000'!D6:D1005,\"held_out\")"]];
summary.getRange("B12").values = [[data.legal_as_of]];
const domainCounts = Object.entries(data.summary.domain_counts);
summary.getRange("D5:D9").values = domainCounts.map(([domain]) => [domain]);
summary.getRange("E5:E9").formulas = domainCounts.map(([domain]) => [`=COUNTIF('Golden 1000'!B6:B1005,\"${domain}\")`]);
const gateRows = Object.entries(validation.checks).map(([name, passed]) => [name, passed ? "ĐẠT" : "KHÔNG ĐẠT"]);
summary.getRange(`G5:H${4 + gateRows.length}`).values = gateRows;
body(summary.getRange("A5:B12")); body(summary.getRange("D5:E9")); body(summary.getRange(`G5:H${4 + gateRows.length}`));
summary.getRange(`H5:H${4 + gateRows.length}`).conditionalFormats.add("containsText", { text: "ĐẠT", format: { fill: colors.green, font: { bold: true, color: "#14532D" } } });
const domainChart = summary.charts.add("bar", summary.getRange("D4:E9"));
domainChart.title = "Phân bổ 5 lĩnh vực"; domainChart.hasLegend = false; domainChart.setPosition("D11", "J25");
widths(summary, { A: 28, B: 18, C: 3, D: 34, E: 12, F: 3, G: 38, H: 18, I: 3, J: 3 });
summary.freezePanes.freezeRows(4);

title(guide, "HƯỚNG DẪN DUYỆT", "Bạn chỉ cần duyệt ở cột “Quyết định duyệt” của sheet Golden 1000; các sheet còn lại dùng để đối chiếu căn cứ và bằng chứng.", "E");
const guideRows = [
  [1, "Đọc câu hỏi", "Câu hỏi có ý nghĩa thực tế, đủ dữ kiện và đúng lĩnh vực.", "Yêu cầu sửa nếu câu hỏi tối nghĩa hoặc lộ sẵn toàn bộ đáp án.", "Golden 1000"],
  [2, "Kiểm tra nguồn", "Số hiệu, Điều và URL đúng nguồn chính thức.", "Không duyệt nếu sai văn bản/Điều hoặc URL không chính thức.", "Nguồn chính thức"],
  [3, "Kiểm tra hiệu lực", "Nguồn trả lời hiện hành còn hiệu lực; nguồn cũ được chặn rõ.", "Không dùng văn bản hết hiệu lực để trả lời hiện hành.", "Hiệu lực / Nguồn cấm"],
  [4, "Đối chiếu claim", "Mỗi ý bắt buộc đúng nguyên văn và có ích cho câu hỏi.", "Yêu cầu sửa claim thừa, thiếu hoặc không trả lời câu hỏi.", "Claims"],
  [5, "Đối chiếu bằng chứng", "Quote và char span khớp nội dung điều trong PostgreSQL.", "Không duyệt nếu claim không nằm đúng vị trí đã ghi.", "Bằng chứng vật lý"],
  [6, "Ca hỏi toàn Điều", "Đủ 100% nội dung Điều và đúng thứ tự.", "Không chấp nhận một chunk đại diện cho toàn Điều.", "Claims / Kiểm tra"],
  [7, "Ca dự phòng", "Chỉ từ chối khi thật sự thiếu dữ kiện hoặc ngoài phạm vi.", "Không biến câu có thể trả lời thành câu từ chối.", "Golden 1000"],
  [8, "Chốt duyệt", "Chọn Duyệt / Yêu cầu sửa / Từ chối và ghi nhận xét.", "Không để trống nhận xét nếu yêu cầu sửa hoặc từ chối.", "Golden 1000"],
];
writeTable(guide, ["Bước", "Việc cần làm", "Đạt", "Không đạt", "Xem tại sheet"], guideRows, 4, "E", "ReviewGuide");
widths(guide, { A: 9, B: 28, C: 60, D: 60, E: 26 }); guide.freezePanes.freezeRows(4);

const goldenRows = cases.map(item => [
  item.case_id, item.domain, cat(item), item.evaluation_split, item.procedure_family,
  item.questions.citizen, item.expected_answer_mode, item.expected_refusal,
  item.expected_sources.length, item.required_claims.length, item.legal_as_of,
  item.review_status, "Chưa duyệt", "",
]);
title(golden, "GOLDEN 1.000 — DANH SÁCH DUYỆT", "Mỗi dòng là một ca độc lập về nguồn–Điều–claim. Dùng bộ lọc theo lĩnh vực, loại ca hoặc split; quyết định duyệt tại hai cột cuối.", "N");
writeTable(golden, ["Case ID", "Lĩnh vực", "Loại ca", "Split", "Nghiệp vụ", "Câu hỏi người dân", "Chế độ trả lời", "Từ chối", "Số nguồn", "Số claim", "As-of", "Trạng thái máy", "Quyết định duyệt", "Nhận xét người duyệt"], goldenRows, 5, "N", "Golden1000Complete");
golden.getRange("M6:M1005").dataValidation = { rule: { type: "list", values: ["Chưa duyệt", "Duyệt", "Yêu cầu sửa", "Từ chối"] } };
golden.getRange("M6:M1005").conditionalFormats.add("containsText", { text: "Duyệt", format: { fill: colors.green, font: { color: "#14532D", bold: true } } });
golden.getRange("M6:M1005").conditionalFormats.add("containsText", { text: "Từ chối", format: { fill: colors.red, font: { color: "#991B1B", bold: true } } });
widths(golden, { A: 14, B: 28, C: 24, D: 14, E: 42, F: 100, G: 20, H: 12, I: 10, J: 10, K: 14, L: 24, M: 20, N: 70 });
golden.freezePanes.freezeRows(5); golden.freezePanes.freezeColumns(6);

const claimRows = [];
for (const item of cases) for (const claim of item.required_claims) {
  const ev = evidenceByKey.get(`${item.case_id}|${claim.claim_id}`) || {};
  claimRows.push([item.case_id, item.domain, cat(item), claim.claim_id, claim.order, claim.critical, claim.facet, claim.text, ev.db_document_id, ev.db_article_id, ev.char_start, ev.char_end, ev.quote === claim.text ? "Khớp" : "Lỗi"]);
}
title(claims, "CLAIMS BẮT BUỘC", "Các ý dùng để chấm độ đầy đủ và chống câu trả lời chỉ sao chép một đoạn nhỏ. Mỗi claim có một span bằng chứng riêng.", "M");
writeTable(claims, ["Case ID", "Lĩnh vực", "Loại ca", "Claim ID", "Thứ tự", "Bắt buộc", "Facet", "Nội dung claim", "DB document", "DB article", "char_start", "char_end", "Khớp quote"], claimRows, 5, "M", "GoldenClaims");
widths(claims, { A: 14, B: 28, C: 22, D: 12, E: 10, F: 12, G: 20, H: 110, I: 14, J: 14, K: 12, L: 12, M: 14 }); claims.freezePanes.freezeRows(5); claims.freezePanes.freezeColumns(4);

const sourceRows = [];
for (const item of cases) item.expected_sources.forEach((source, index) => {
  const firstClaim = item.required_claims[index] || item.required_claims[0];
  const ev = firstClaim ? evidenceByKey.get(`${item.case_id}|${firstClaim.claim_id}`) : null;
  const inv = ev ? inventoryByArticle.get(ev.db_article_id) : null;
  const official = currentByLaw.get(source.law_number) || {};
  sourceRows.push([item.case_id, index + 1, item.domain, source.law_number, source.document_title, source.article, source.clause, source.point, source.reason, source.proof.official_url, source.proof.checked_at, official.status || inv?.status || "", official.effective_date || inv?.effective_date || "", official.expired_date || "", source.proof.quote.length]);
});
title(sources, "NGUỒN CHÍNH THỨC", "Nguồn dùng để trả lời. URL chỉ thuộc vbpl.vn hoặc vanban.chinhphu.vn; hiệu lực được chốt tại ngày 11/08/2026.", "O");
writeTable(sources, ["Case ID", "#", "Lĩnh vực", "Số hiệu", "Tên văn bản", "Điều", "Khoản", "Điểm", "Lý do dùng", "Official URL", "Ngày kiểm tra", "DB status", "Hiệu lực từ", "Hết hiệu lực", "Độ dài quote"], sourceRows, 5, "O", "GoldenSources");
widths(sources, { A: 14, B: 7, C: 28, D: 23, E: 58, F: 10, G: 10, H: 10, I: 50, J: 70, K: 15, L: 15, M: 15, N: 15, O: 14 }); sources.freezePanes.freezeRows(5); sources.freezePanes.freezeColumns(6);

const physicalRows = evidence.map(ev => {
  const inv = inventoryByArticle.get(ev.db_article_id) || {};
  return [ev.case_id, ev.claim_id, inv.law_number || "", inv.article || "", ev.db_document_id, ev.db_article_id, ev.char_start, ev.char_end, ev.quote, "Khớp DB"];
});
title(physical, "BẰNG CHỨNG VẬT LÝ", "Quote là chuỗi nguyên văn tại đúng char_start/char_end trong legal_articles.content. Page/bounding box chưa có vì corpus hiện lưu chứng cứ ở cấp điều.", "J");
writeTable(physical, ["Case ID", "Claim ID", "Số hiệu", "Điều", "DB document", "DB article", "char_start", "char_end", "Quote nguyên văn", "Kiểm tra"], physicalRows, 5, "J", "PhysicalEvidence");
widths(physical, { A: 14, B: 12, C: 23, D: 10, E: 14, F: 14, G: 12, H: 12, I: 120, J: 15 }); physical.freezePanes.freezeRows(5); physical.freezePanes.freezeColumns(4);

const forbiddenRows = [];
for (const item of cases) for (const row of item.forbidden_sources) forbiddenRows.push([item.case_id, item.domain, row.law_number, row.reason]);
title(forbidden, "NGUỒN CẤM / HẾT HIỆU LỰC", "Các nguồn này dùng để kiểm tra hệ thống có chặn văn bản hết hiệu lực hay không; tuyệt đối không dùng làm căn cứ trả lời hiện hành.", "D");
writeTable(forbidden, ["Case ID", "Lĩnh vực", "Số hiệu", "Lý do cấm"], forbiddenRows, 5, "D", "ForbiddenSources");
widths(forbidden, { A: 14, B: 30, C: 25, D: 100 }); forbidden.freezePanes.freezeRows(5);

const usedSources = sourceInventory.sources.filter(row => row.used);
const uniqueUsed = [...new Map(usedSources.map(row => [`${row.db_document_id}|${row.db_article_id}`, row])).values()];
const currentRows = uniqueUsed.map(row => {
  const official = currentByLaw.get(row.law_number) || {};
  return ["Nguồn hiện hành", row.domain, row.law_number, row.document_title, row.article, official.status || row.status, official.effective_date || row.effective_date || "", official.expired_date || "", official.official_url || row.official_url, official.checked_at || data.legal_as_of];
});
const guardRows = validityGuards.guards.map(row => ["Nguồn phải chặn", row.domain, row.law_number, row.reason, "", "Hết hiệu lực toàn bộ", "", row.expired_date, row.official_url, row.checked_at]);
title(validity, "ĐỒNG BỘ HIỆU LỰC", "Một bảng đối chiếu chung cho nguồn hiện hành và nguồn phải chặn. Khi duyệt, ưu tiên trạng thái trên cổng chính thức nếu DB còn metadata cũ.", "J");
writeTable(validity, ["Loại", "Lĩnh vực", "Số hiệu", "Tên/Lý do", "Điều", "Trạng thái", "Hiệu lực từ", "Hết hiệu lực", "Official URL", "Ngày kiểm tra"], [...currentRows, ...guardRows], 5, "J", "ValiditySources");
validity.getRange(`A6:A${5 + currentRows.length + guardRows.length}`).conditionalFormats.add("containsText", { text: "Nguồn phải chặn", format: { fill: colors.red, font: { color: "#991B1B", bold: true } } });
widths(validity, { A: 20, B: 28, C: 24, D: 80, E: 10, F: 22, G: 15, H: 15, I: 70, J: 15 }); validity.freezePanes.freezeRows(5);

const checkRows = Object.entries(validation.checks).map(([name, passed]) => [name, passed, passed ? "ĐẠT" : "KHÔNG ĐẠT", ""]);
const metricRows = Object.entries(validation.counts).map(([name, value]) => [`count.${name}`, value, "Thông tin", ""]);
checkRows.push(["exact_article.minimum_coverage", validation.exact_article_coverage.minimum, validation.exact_article_coverage.minimum === 1 ? "ĐẠT" : "KHÔNG ĐẠT", "Phải bằng 100%"]);
title(checks, "KẾT QUẢ KIỂM TRA ĐỘC LẬP", "Các cổng này được tính từ JSON và đối chiếu read-only với PostgreSQL. Tất cả phải ĐẠT trước khi chuyển sang duyệt pháp lý.", "D");
writeTable(checks, ["Cổng/chỉ số", "Giá trị", "Kết quả", "Ghi chú"], [...checkRows, ...metricRows], 5, "D", "ValidationChecks");
checks.getRange(`C6:C${5 + checkRows.length}`).conditionalFormats.add("containsText", { text: "ĐẠT", format: { fill: colors.green, font: { color: "#14532D", bold: true } } });
widths(checks, { A: 52, B: 20, C: 18, D: 70 }); checks.freezePanes.freezeRows(5);

await fs.mkdir(path.join(outDir, "render"), { recursive: true });
const outputPath = path.join(outDir, "golden-1000-complete.xlsx");
const file = await SpreadsheetFile.exportXlsx(wb);
await file.save(outputPath);

const renderTargets = [
  ["Tổng quan", "A1:J25", "01-tong-quan.png"],
  ["Hướng dẫn duyệt", "A1:E12", "02-huong-dan.png"],
  ["Golden 1000", "A1:N16", "03-golden.png"],
  ["Claims", "A1:M16", "04-claims.png"],
  ["Nguồn chính thức", "A1:O15", "05-nguon.png"],
  ["Bằng chứng vật lý", "A1:J14", "06-bang-chung.png"],
  ["Nguồn cấm", "A1:D15", "07-nguon-cam.png"],
  ["Hiệu lực", "A1:J15", "08-hieu-luc.png"],
  ["Kiểm tra", "A1:D24", "09-kiem-tra.png"],
];
for (const [sheetName, range, filename] of renderTargets) {
  const image = await wb.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(outDir, "render", filename), new Uint8Array(await image.arrayBuffer()));
}
const inspect = await wb.inspect({ kind: "workbook,sheet,table", include: "values,formulas", maxChars: 22000, tableMaxRows: 5, tableMaxCols: 16, tableMaxCellChars: 140 });
await fs.writeFile(path.join(outDir, "golden-1000-complete.xlsx.inspect.ndjson"), inspect.ndjson, "utf8");
const formulaErrors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "formula errors" });
await fs.writeFile(path.join(outDir, "formula-errors.ndjson"), formulaErrors.ndjson, "utf8");
console.log(JSON.stringify({ outputPath, sheets: renderTargets.length, renders: renderTargets.length, formulaErrors: formulaErrors.ndjson }));
