import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';
import { normalizeRun, summarizeRuns } from './qa_log_export_contract.mjs';

function argument(name, fallback = '') {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const input = argument('--input', 'J:/ChatBotLegal/reports/web-citizen-1000-roles/qa-log.json');
const output = argument('--output', path.join(path.dirname(input), 'qa-log.xlsx'));
const previewOutput = argument('--preview', '');
const payload = JSON.parse(await fs.readFile(input, 'utf8'));
const sourceRecords = Array.isArray(payload.records)
  ? payload.records
  : Array.isArray(payload.runs)
    ? payload.runs
    : [];
const rows = sourceRecords.map(normalizeRun);
const metrics = summarizeRuns(rows);

const wb = Workbook.create();
const summary = wb.worksheets.add('Tổng hợp');
const detail = wb.worksheets.add('Hỏi đáp chi tiết');

summary.getRange('A1:F1').merge();
summary.getRange('A1').values = [['BÁO CÁO KIỂM THỬ ĐỘ TIN CẬY HỎI ĐÁP PHÁP LUẬT']];
summary.getRange('A2:F2').merge();
summary.getRange('A2').values = [['Kết quả chỉ được tính “Đạt” từ bộ chấm theo rubric; cờ “có nguồn” không phải tiêu chí đúng/sai.']];
summary.getRange('A4:B4').values = [['Chỉ tiêu', 'Giá trị']];
summary.getRange('A5:A10').values = [
  ['Tổng lượt'], ['Đạt toàn bộ rubric'], ['Không đạt'], ['Chưa có ground truth để chấm'], ['Có lỗi'], ['Chế độ chỉ xem nguồn'],
];
summary.getRange('B5:B10').values = [[metrics.total], [metrics.passed], [metrics.failed], [metrics.unscored], [metrics.errors], [metrics.sourceOnly]];

const roleCounts = new Map();
const domainCounts = new Map();
for (const row of rows) {
  const role = roleCounts.get(row.role) ?? {total: 0, passed: 0, failed: 0, unscored: 0};
  role.total += 1;
  if (row.evaluationPassed === true) role.passed += 1;
  else if (row.evaluationPassed === false) role.failed += 1;
  else role.unscored += 1;
  roleCounts.set(row.role, role);
  const domain = domainCounts.get(row.domain) ?? {total: 0, passed: 0, failed: 0, unscored: 0};
  domain.total += 1;
  if (row.evaluationPassed === true) domain.passed += 1;
  else if (row.evaluationPassed === false) domain.failed += 1;
  else domain.unscored += 1;
  domainCounts.set(row.domain, domain);
}

summary.getRange('D4:H4').values = [['Role', 'Tổng', 'Đạt', 'Không đạt', 'Chưa chấm']];
const roleRows = [...roleCounts.entries()].sort(([a], [b]) => a.localeCompare(b, 'vi')).map(([key, value]) => [key, value.total, value.passed, value.failed, value.unscored]);
if (roleRows.length) summary.getRange(`D5:H${4 + roleRows.length}`).values = roleRows;
const domainHeader = 13;
summary.getRange(`A${domainHeader}:E${domainHeader}`).values = [['Lĩnh vực', 'Tổng', 'Đạt', 'Không đạt', 'Chưa chấm']];
const domainRows = [...domainCounts.entries()].sort(([a], [b]) => a.localeCompare(b, 'vi')).map(([key, value]) => [key, value.total, value.passed, value.failed, value.unscored]);
if (domainRows.length) summary.getRange(`A${domainHeader + 1}:E${domainHeader + domainRows.length}`).values = domainRows;

const headers = [
  'STT', 'Run ID', 'Mã câu', 'Role', 'Lĩnh vực', 'Hiệu lực tại ngày', 'Câu hỏi', 'Câu trả lời',
  'Chế độ trả lời', 'Grounding', 'Kết quả chấm', 'Mã lỗi rubric', 'Số trích dẫn', 'Trạng thái hiệu lực',
  'Lý do dự phòng', 'Lỗi hệ thống', 'Tổng thời gian (ms)', 'Trace ID',
];
const detailRows = rows.map(row => [
  row.ordinal, row.runId, row.caseId, row.role, row.domain, row.legalAsOf, row.question, row.answer,
  row.answerMode, row.groundingStatus, row.evaluationLabel, row.reasonCodes, row.citationCount,
  row.validityStates, row.fallbackReason, row.errorCode, row.endToEndMs, row.traceId,
]);
detail.getRange(`A1:R${detailRows.length + 1}`).values = [headers, ...detailRows];
if (detailRows.length) detail.tables.add(`A1:R${detailRows.length + 1}`, true, 'StructuredQaRuns');

const headerStyle = {fill: '#1F4E78', font: {bold: true, color: '#FFFFFF'}, wrapText: true, horizontalAlignment: 'center'};
summary.getRange('A1:H1').format = {fill: '#17365D', font: {bold: true, color: '#FFFFFF', size: 16}, horizontalAlignment: 'center'};
summary.getRange('A2:H2').format = {fill: '#FFF2CC', font: {italic: true, color: '#7F6000'}, horizontalAlignment: 'center', wrapText: true};
summary.getRange('A4:B4').format = headerStyle;
summary.getRange('D4:H4').format = headerStyle;
summary.getRange(`A${domainHeader}:E${domainHeader}`).format = headerStyle;
summary.getRange('A:A').format.columnWidth = 34;
summary.getRange('B:C').format.columnWidth = 18;
summary.getRange('D:D').format.columnWidth = 24;
summary.getRange('E:H').format.columnWidth = 16;
summary.freezePanes.freezeRows(4);
summary.showGridLines = false;

detail.getRange('A1:R1').format = headerStyle;
detail.getRange(`A1:R${detailRows.length + 1}`).format.wrapText = true;
detail.getRange('A:A').format.columnWidth = 8;
detail.getRange('B:C').format.columnWidth = 18;
detail.getRange('D:D').format.columnWidth = 18;
detail.getRange('E:E').format.columnWidth = 27;
detail.getRange('F:F').format.columnWidth = 16;
detail.getRange('G:G').format.columnWidth = 52;
detail.getRange('H:H').format.columnWidth = 88;
detail.getRange('I:R').format.columnWidth = 18;
detail.freezePanes.freezeRows(1);
detail.showGridLines = false;

await fs.mkdir(path.dirname(output), {recursive: true});
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(output);
if (previewOutput) {
  const preview = await wb.render({sheetName: 'Tổng hợp', autoCrop: 'all', scale: 1, format: 'png'});
  await fs.writeFile(previewOutput, new Uint8Array(await preview.arrayBuffer()));
}
process.stdout.write(JSON.stringify({records: rows.length, output, metrics}));
