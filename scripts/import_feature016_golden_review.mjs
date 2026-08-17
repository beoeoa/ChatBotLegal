import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {FileBlob, SpreadsheetFile, Workbook} from '@oai/artifact-tool';
import {
  normalizeReviewedRow,
  REVIEW_IMPORT_POLICY_VERSION,
} from './golden_review_import_contract.mjs';

function arg(name, fallback = '') {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function asText(value) {
  return String(value ?? '').normalize('NFC').trim();
}

function parseJsonArray(value, rowNumber, columnName, errors) {
  try {
    const parsed = JSON.parse(asText(value));
    if (!Array.isArray(parsed)) throw new Error('must be a JSON array');
    return parsed;
  } catch (error) {
    errors.push({
      row: rowNumber,
      code: 'invalid_json_cell',
      detail: `${columnName}: ${error.message}`,
    });
    return [];
  }
}

function parseBoolean(value) {
  const normalized = asText(value).toLocaleLowerCase('vi');
  return ['có', 'co', 'yes', 'true', '1'].includes(normalized);
}

function parseDate(value) {
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  if (typeof value === 'number' && Number.isFinite(value)) {
    const epoch = Date.UTC(1899, 11, 30);
    return new Date(epoch + value * 86400000).toISOString().slice(0, 10);
  }
  return asText(value);
}

function sourceKey(source) {
  return [source.law_number, source.article, source.clause, source.point]
    .map((value) => asText(value).toLocaleLowerCase('vi'))
    .join('|');
}

function addCaseError(result, code) {
  if (!result.errors.includes(code)) result.errors.push(code);
  result.case.review_status = 'proposed';
  if (!result.case.risk_tags.includes(code)) result.case.risk_tags.push(code);
  if (!result.case.risk_tags.includes('review_import_requires_correction')) {
    result.case.risk_tags.push('review_import_requires_correction');
  }
}

function countCodes(rows, field) {
  const counts = {};
  for (const row of rows) {
    for (const code of row[field] ?? []) counts[code] = (counts[code] ?? 0) + 1;
  }
  return Object.fromEntries(Object.entries(counts).sort((a, b) => a[0].localeCompare(b[0])));
}

const input = arg('--input');
const baselinePath = arg('--baseline', 'notebook_data/feature016-golden-v2.json');
const outputDataset = arg(
  '--output-dataset',
  'reports/feature016/phase-b/user-reviewed-golden-v2-candidate.json',
);
const outputAudit = arg(
  '--output-audit',
  'reports/feature016/phase-b/user-reviewed-golden-v2-audit.json',
);
const outputWorkbook = arg(
  '--output-workbook',
  'outputs/feature016-golden-review-audit/golden-v2-review-audit.xlsx',
);
const previewOutput = arg(
  '--preview',
  'outputs/feature016-golden-review-audit/golden-v2-review-audit-preview.png',
);

if (!input) throw new Error('--input is required');

const baseline = JSON.parse(await fs.readFile(baselinePath, 'utf8'));
const baselineById = new Map((baseline.cases ?? []).map((item) => [item.case_id, item]));
const inputBlob = await FileBlob.load(input);
const sourceWorkbook = await SpreadsheetFile.importXlsx(inputBlob);
const sourceSheet = sourceWorkbook.worksheets.getItemAt(0);
const values = sourceSheet.getUsedRange(true).values;
const headers = (values[0] ?? []).map(asText);
const expectedHeaders = [
  'STT', 'Case ID', 'Lĩnh vực', 'Câu hỏi người dân', 'Ngày hiệu lực', 'Nhóm thủ tục',
  'Nguồn bắt buộc (JSON)', 'Nguồn bị cấm (JSON)', 'Các ý bắt buộc có thứ tự (JSON)',
  'Chế độ mong đợi', 'Từ chối mong đợi', 'Trạng thái duyệt', 'Người duyệt', 'Ngày duyệt', 'Ghi chú duyệt',
];
if (JSON.stringify(headers.slice(0, expectedHeaders.length)) !== JSON.stringify(expectedHeaders)) {
  throw new Error('review workbook headers do not match the Feature 016 contract');
}

const rowResults = [];
const workbookErrors = [];
const seenIds = new Set();
for (let index = 1; index < values.length; index += 1) {
  const cells = values[index] ?? [];
  if (!cells.some((value) => asText(value))) continue;
  const rowNumber = index + 1;
  const cellErrors = [];
  const row = {
    caseId: asText(cells[1]),
    domain: asText(cells[2]),
    question: asText(cells[3]),
    legalAsOf: parseDate(cells[4]),
    procedureFamily: asText(cells[5]),
    expectedSources: parseJsonArray(cells[6], rowNumber, 'expected_sources', cellErrors),
    forbiddenSources: parseJsonArray(cells[7], rowNumber, 'forbidden_sources', cellErrors),
    requiredClaims: parseJsonArray(cells[8], rowNumber, 'required_claims', cellErrors),
    answerMode: asText(cells[9]),
    expectedRefusal: parseBoolean(cells[10]),
    reviewStatus: asText(cells[11]),
    reviewer: asText(cells[12]),
    reviewedAt: parseDate(cells[13]),
    reviewNotes: asText(cells[14]),
  };
  const result = normalizeReviewedRow(row);
  result.row = rowNumber;
  result.reviewer = row.reviewer;
  result.reviewed_at = row.reviewedAt;
  result.review_notes = row.reviewNotes;
  for (const error of cellErrors) addCaseError(result, error.code);

  const expected = baselineById.get(row.caseId);
  if (!expected) {
    addCaseError(result, 'case_id_not_in_baseline');
  } else {
    if (asText(expected.questions?.citizen) !== row.question) addCaseError(result, 'question_mismatch');
    if (asText(expected.domain) !== row.domain) addCaseError(result, 'domain_mismatch');
    if (asText(expected.legal_as_of) !== row.legalAsOf) addCaseError(result, 'legal_as_of_mismatch');
  }
  if (seenIds.has(row.caseId)) addCaseError(result, 'duplicate_case_id');
  seenIds.add(row.caseId);

  const expectedKeys = result.case.expected_sources.map(sourceKey);
  const forbiddenKeys = new Set(result.case.forbidden_sources.map(sourceKey));
  if (expectedKeys.some((key) => forbiddenKeys.has(key))) addCaseError(result, 'source_both_expected_and_forbidden');
  rowResults.push(result);
}

for (const caseId of baselineById.keys()) {
  if (!seenIds.has(caseId)) workbookErrors.push({code: 'baseline_case_missing', case_id: caseId});
}
if (rowResults.length !== baselineById.size) {
  workbookErrors.push({
    code: 'case_count_mismatch',
    expected: baselineById.size,
    actual: rowResults.length,
  });
}

const safeApproved = rowResults.filter((item) => item.case.review_status === 'approved').length;
const legallyReviewed = rowResults.filter((item) => item.case.review_status === 'legally_reviewed').length;
const proposed = rowResults.filter((item) => item.case.review_status === 'proposed').length;
const audit = {
  schema_version: '1.0',
  policy_version: REVIEW_IMPORT_POLICY_VERSION,
  input: {path: input, sheet: sourceSheet.name, rows: rowResults.length},
  baseline: {path: baselinePath, cases: baselineById.size},
  summary: {
    safe_approved: safeApproved,
    legally_reviewed: legallyReviewed,
    proposed,
    cases_with_errors: rowResults.filter((item) => item.errors.length).length,
    cases_with_warnings: rowResults.filter((item) => item.warnings.length).length,
    workbook_errors: workbookErrors.length,
    gate_b_legal_acceptance_ready:
      workbookErrors.length === 0 && safeApproved === baselineById.size,
  },
  error_counts: countCodes(rowResults, 'errors'),
  warning_counts: countCodes(rowResults, 'warnings'),
  workbook_errors: workbookErrors,
  cases: rowResults.map((item) => ({
    row: item.row,
    case_id: item.case.case_id,
    final_review_status: item.case.review_status,
    errors: item.errors,
    warnings: item.warnings,
    replacements: item.replacements,
    reviewer: item.reviewer,
    reviewed_at: item.reviewed_at,
    review_notes: item.review_notes,
  })),
};

const candidateDataset = {
  schema_version: '2.0',
  dataset_kind: audit.summary.gate_b_legal_acceptance_ready
    ? 'user_reviewed_legal_ground_truth'
    : 'normalized_review_candidate_not_legal_ground_truth',
  review_notice: audit.summary.gate_b_legal_acceptance_ready
    ? 'Imported from a user-reviewed workbook after all fail-closed checks passed.'
    : 'This candidate is not legal ground truth. Cases with any import error were demoted to proposed.',
  review_import: {
    policy_version: REVIEW_IMPORT_POLICY_VERSION,
    source_workbook: path.basename(input),
    source_sheet: sourceSheet.name,
  },
  cases: rowResults.map((item) => item.case),
};

await fs.mkdir(path.dirname(outputAudit), {recursive: true});
await fs.mkdir(path.dirname(outputDataset), {recursive: true});
await fs.writeFile(outputAudit, `${JSON.stringify(audit, null, 2)}\n`, 'utf8');
await fs.writeFile(outputDataset, `${JSON.stringify(candidateDataset, null, 2)}\n`, 'utf8');

const workbook = Workbook.create();
const summary = workbook.worksheets.add('Tổng quan');
const issues = workbook.worksheets.add('Cần sửa');
const normalized = workbook.worksheets.add('Đã chuẩn hóa');
const sources = workbook.worksheets.add('Nguồn thay thế');

summary.getRange('A1:F1').merge();
summary.getRange('A1').values = [['KIỂM TOÁN FILE DUYỆT GOLDEN V2 — FEATURE 016']];
summary.getRange('A2:F2').merge();
summary.getRange('A2').values = [[
  audit.summary.gate_b_legal_acceptance_ready
    ? 'Đạt điều kiện nhập Golden chính thức.'
    : 'CHƯA ĐẠT GATE B: không nhập tự động; các ca lỗi đã được hạ về proposed.',
]];
summary.getRange('A4:B10').values = [
  ['Chỉ số', 'Giá trị'],
  ['Tổng ca', rowResults.length],
  ['Approved an toàn', safeApproved],
  ['Legally reviewed', legallyReviewed],
  ['Proposed sau kiểm tra', proposed],
  ['Ca có lỗi', audit.summary.cases_with_errors],
  ['Ca có cảnh báo', audit.summary.cases_with_warnings],
];
summary.getRange('D4:F4').merge();
summary.getRange('D4').values = [['Nguyên tắc']];
summary.getRange('D5:F10').merge();
summary.getRange('D5').values = [[
  'Nguồn hết hiệu lực không được dùng làm expected source.\n' +
  'Ca có claims không thể đồng thời yêu cầu từ chối toàn bộ.\n' +
  'Ca có claims không thể để source_view_only.\n' +
  'Không tự động thay Điều/khoản khi văn bản bị thay thế.\n' +
  'Mọi lỗi đều hạ trạng thái về proposed để người duyệt quyết định lại.',
]];

const issueRows = rowResults
  .filter((item) => item.errors.length || item.warnings.length)
  .map((item) => [
    item.row,
    item.case.case_id,
    item.case.domain,
    item.case.questions.citizen,
    item.errors.join(', '),
    item.warnings.join(', '),
    item.replacements.map((entry) => `${entry.label} → ${entry.replacement}`).join('\n'),
    item.case.review_status,
    item.reviewer,
    item.review_notes,
  ]);
issues.getRange(`A1:J${issueRows.length + 1}`).values = [[
  'Dòng', 'Case ID', 'Lĩnh vực', 'Câu hỏi', 'Lỗi chặn', 'Cảnh báo',
  'Nguồn cần thay', 'Trạng thái an toàn', 'Người duyệt', 'Ghi chú',
], ...issueRows];
issues.tables.add(`A1:J${issueRows.length + 1}`, true, 'GoldenReviewIssues');

const normalizedRows = rowResults.map((item, index) => [
  index + 1,
  item.case.case_id,
  item.case.domain,
  item.case.questions.citizen,
  item.case.procedure_family ?? '',
  JSON.stringify(item.case.expected_sources),
  JSON.stringify(item.case.forbidden_sources),
  JSON.stringify(item.case.required_claims),
  item.case.expected_answer_mode,
  item.case.expected_refusal ? 'Có' : 'Không',
  item.case.review_status,
  item.errors.join(', '),
]);
normalized.getRange(`A1:L${normalizedRows.length + 1}`).values = [[
  'STT', 'Case ID', 'Lĩnh vực', 'Câu hỏi', 'Nhóm thủ tục', 'Nguồn bắt buộc chuẩn hóa',
  'Nguồn bị cấm chuẩn hóa', 'Các ý bắt buộc chuẩn hóa', 'Chế độ', 'Từ chối',
  'Trạng thái an toàn', 'Lỗi chặn',
], ...normalizedRows];
normalized.tables.add(`A1:L${normalizedRows.length + 1}`, true, 'NormalizedGoldenCases');

sources.getRange('A1:E9').values = [
  ['Nguồn cũ', 'Nguồn thay thế', 'Có hiệu lực từ', 'Nguồn chính thức', 'Ghi chú'],
  ['58/2014/QH13', '41/2024/QH15', '2025-07-01', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=175027', 'Luật BHXH cũ không dùng cho câu trả lời hiện hành'],
  ['38/2013/QH13', '74/2025/QH15', '2026-01-01', 'https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=179273', 'Luật Việc làm mới'],
  ['53/2014/QH13', '46/2024/QH15', '2025-07-01', 'https://vbpl.vn/TW/Pages/vanban.aspx', 'Phải đối chiếu lại đúng Điều của Luật Công chứng mới'],
  ['146/2018/NĐ-CP', '188/2025/NĐ-CP', '2025-08-15', 'https://vbpl.vn/TW/Pages/vbpq-luocdo.aspx?ItemID=179711', 'Nghị định 146 bị thay thế'],
  ['75/2023/NĐ-CP', '188/2025/NĐ-CP', '2025-08-15', 'https://vbpl.vn/TW/Pages/vbpq-luocdo.aspx?ItemID=179711', 'Văn bản sửa NĐ 146 cũng bị thay thế'],
  ['02/2025/NĐ-CP', '188/2025/NĐ-CP', '2025-08-15', 'https://vbpl.vn/TW/Pages/vbpq-luocdo.aspx?ItemID=179711', 'Văn bản sửa NĐ 146 cũng bị thay thế'],
  ['81/2021/NĐ-CP', '238/2025/NĐ-CP', '2025-09-03', 'https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=183688', 'Nghị định học phí cũ bị thay thế'],
  ['97/2023/NĐ-CP', '238/2025/NĐ-CP', '2025-09-03', 'https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=183688', 'Văn bản sửa NĐ 81 bị thay thế'],
];

const headerFormat = {
  fill: '#1F4E78',
  font: {bold: true, color: '#FFFFFF'},
  horizontalAlignment: 'center',
  verticalAlignment: 'center',
  wrapText: true,
};
summary.getRange('A1:F1').format = {fill: '#17365D', font: {bold: true, color: '#FFFFFF', size: 16}, horizontalAlignment: 'center'};
summary.getRange('A2:F2').format = {fill: audit.summary.gate_b_legal_acceptance_ready ? '#E2F0D9' : '#FCE4D6', font: {bold: true, color: audit.summary.gate_b_legal_acceptance_ready ? '#375623' : '#9C0006'}, horizontalAlignment: 'center', wrapText: true};
summary.getRange('A4:B4').format = headerFormat;
summary.getRange('D4:F4').format = headerFormat;
summary.getRange('D5:F10').format = {wrapText: true, verticalAlignment: 'top'};
summary.getRange('A:A').format.columnWidth = 30;
summary.getRange('B:B').format.columnWidth = 18;
summary.getRange('C:C').format.columnWidth = 4;
summary.getRange('D:F').format.columnWidth = 24;
summary.showGridLines = false;

for (const sheet of [issues, normalized, sources]) {
  const used = sheet.getUsedRange(true);
  used.format.wrapText = true;
  used.getRow(0).format = headerFormat;
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
}
issues.getRange('A:A').format.columnWidth = 8;
issues.getRange('B:B').format.columnWidth = 14;
issues.getRange('C:C').format.columnWidth = 23;
issues.getRange('D:D').format.columnWidth = 55;
issues.getRange('E:G').format.columnWidth = 34;
issues.getRange('H:J').format.columnWidth = 20;
issues.getRange(`E2:E${issueRows.length + 1}`).conditionalFormats.addCustom('=LEN(E2)>0', {fill: '#FFC7CE', font: {color: '#9C0006'}});
normalized.getRange('A:A').format.columnWidth = 7;
normalized.getRange('B:B').format.columnWidth = 14;
normalized.getRange('C:C').format.columnWidth = 23;
normalized.getRange('D:D').format.columnWidth = 52;
normalized.getRange('E:E').format.columnWidth = 24;
normalized.getRange('F:H').format.columnWidth = 45;
normalized.getRange('I:L').format.columnWidth = 20;
sources.getRange('A:C').format.columnWidth = 22;
sources.getRange('D:D').format.columnWidth = 58;
sources.getRange('E:E').format.columnWidth = 46;

await fs.mkdir(path.dirname(outputWorkbook), {recursive: true});
const workbookOutput = await SpreadsheetFile.exportXlsx(workbook);
await workbookOutput.save(outputWorkbook);
if (previewOutput) {
  await fs.mkdir(path.dirname(previewOutput), {recursive: true});
  const preview = await workbook.render({sheetName: 'Tổng quan', range: 'A1:F10', scale: 1.5, format: 'png'});
  await fs.writeFile(previewOutput, new Uint8Array(await preview.arrayBuffer()));
}

const inspection = await workbook.inspect({
  kind: 'table',
  sheetId: 'Tổng quan',
  range: 'A1:F10',
  include: 'values,formulas',
  tableMaxRows: 12,
  tableMaxCols: 8,
  maxChars: 6000,
});
const formulaErrors = await workbook.inspect({
  kind: 'match',
  searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A',
  options: {useRegex: true, maxResults: 300},
  summary: 'final formula error scan',
});
process.stdout.write(JSON.stringify({
  summary: audit.summary,
  outputDataset,
  outputAudit,
  outputWorkbook,
  inspection: inspection.ndjson,
  formulaErrors: formulaErrors.ndjson,
}));
