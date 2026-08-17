import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

function arg(name, fallback = '') {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const input = arg('--input', 'notebook_data/feature016-golden-v2.json');
const output = arg('--output', 'reports/feature016/phase-b/golden-v2-legal-review.xlsx');
const previewOutput = arg('--preview', '');
const payload = JSON.parse(await fs.readFile(input, 'utf8'));
const cases = Array.isArray(payload.cases) ? payload.cases : [];

const wb = Workbook.create();
const summary = wb.worksheets.add('Hướng dẫn');
const review = wb.worksheets.add('Duyệt 100 câu');

summary.getRange('A1:F1').merge();
summary.getRange('A1').values = [['PHIẾU DUYỆT GOLDEN SET PHÁP LUẬT — FEATURE 016']];
summary.getRange('A2:F2').merge();
summary.getRange('A2').values = [['Chỉ người duyệt pháp lý mới đổi trạng thái. Không dùng câu trả lời cũ hoặc cờ “có nguồn” làm đáp án chuẩn.']];
summary.getRange('A4:B4').values = [['Trạng thái', 'Số lượng']];
summary.getRange('A5:A7').values = [['proposed'], ['legally_reviewed'], ['approved']];
summary.getRange('B5:B7').formulas = [
  ['=COUNTIF(\'Duyệt 100 câu\'!$L$2:$L$1001,A5)'],
  ['=COUNTIF(\'Duyệt 100 câu\'!$L$2:$L$1001,A6)'],
  ['=COUNTIF(\'Duyệt 100 câu\'!$L$2:$L$1001,A7)'],
];
summary.getRange('A9:F9').merge();
summary.getRange('A9').values = [['Quy tắc duyệt bắt buộc']];
summary.getRange('A10:F15').merge(true);
summary.getRange('A10').values = [[
  '1) Đối chiếu đúng văn bản, Điều/khoản/điểm và ngày hiệu lực.\n' +
  '2) Ghi nguồn bị cấm nếu là văn bản hết hiệu lực, sai thủ tục hoặc sai phạm vi.\n' +
  '3) Liệt kê các ý bắt buộc theo đúng thứ tự; mỗi ý phải có căn cứ.\n' +
  '4) Chỉ dùng approved khi đã kiểm tra đầy đủ. Nếu chưa đủ nguồn, giữ proposed hoặc chọn source_view_only + expected_refusal.\n' +
  '5) Không nhập thông tin cá nhân, mật khẩu, API key hoặc dữ liệu hồ sơ thật.'
]];

const headers = [
  'STT', 'Case ID', 'Lĩnh vực', 'Câu hỏi người dân', 'Ngày hiệu lực', 'Nhóm thủ tục',
  'Nguồn bắt buộc (JSON)', 'Nguồn bị cấm (JSON)', 'Các ý bắt buộc có thứ tự (JSON)',
  'Chế độ mong đợi', 'Từ chối mong đợi', 'Trạng thái duyệt', 'Người duyệt', 'Ngày duyệt', 'Ghi chú duyệt',
];
const rows = cases.map((item, index) => [
  index + 1,
  item.case_id ?? '',
  item.domain ?? '',
  item.questions?.citizen ?? '',
  item.legal_as_of ?? '',
  item.procedure_family ?? '',
  JSON.stringify(item.expected_sources ?? []),
  JSON.stringify(item.forbidden_sources ?? []),
  JSON.stringify(item.required_claims ?? []),
  item.expected_answer_mode ?? '',
  item.expected_refusal === true ? 'Có' : 'Không',
  item.review_status ?? 'proposed',
  '',
  '',
  '',
]);
review.getRange(`A1:O${rows.length + 1}`).values = [headers, ...rows];
review.tables.add(`A1:O${rows.length + 1}`, true, 'GoldenLegalReview');

const header = {fill: '#1F4E78', font: {bold: true, color: '#FFFFFF'}, horizontalAlignment: 'center', wrapText: true};
summary.getRange('A1:F1').format = {fill: '#17365D', font: {bold: true, color: '#FFFFFF', size: 16}, horizontalAlignment: 'center'};
summary.getRange('A2:F2').format = {fill: '#FFF2CC', font: {italic: true, color: '#7F6000'}, horizontalAlignment: 'center', wrapText: true};
summary.getRange('A4:B4').format = header;
summary.getRange('A9:F9').format = header;
summary.getRange('A10:F15').format = {wrapText: true, verticalAlignment: 'top'};
summary.getRange('A:A').format.columnWidth = 30;
summary.getRange('B:F').format.columnWidth = 20;
summary.freezePanes.freezeRows(4);
summary.showGridLines = false;

review.getRange('A1:O1').format = header;
review.getRange(`A1:O${rows.length + 1}`).format.wrapText = true;
review.getRange('A:A').format.columnWidth = 7;
review.getRange('B:B').format.columnWidth = 14;
review.getRange('C:C').format.columnWidth = 25;
review.getRange('D:D').format.columnWidth = 58;
review.getRange('E:F').format.columnWidth = 18;
review.getRange('G:I').format.columnWidth = 45;
review.getRange('J:L').format.columnWidth = 20;
review.getRange('M:O').format.columnWidth = 24;
review.getRange(`L2:L${rows.length + 1}`).dataValidation = {
  rule: {type: 'list', formula1: '"proposed,legally_reviewed,approved"'},
};
review.getRange(`L2:L${rows.length + 1}`).conditionalFormats.addCustom('=L2="proposed"', {fill: '#FFF2CC', font: {color: '#7F6000'}});
review.getRange(`L2:L${rows.length + 1}`).conditionalFormats.addCustom('=L2="legally_reviewed"', {fill: '#D9EAF7', font: {color: '#17365D'}});
review.getRange(`L2:L${rows.length + 1}`).conditionalFormats.addCustom('=L2="approved"', {fill: '#E2F0D9', font: {color: '#375623'}});
review.freezePanes.freezeRows(1);
review.showGridLines = false;

await fs.mkdir(path.dirname(output), {recursive: true});
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(output);
if (previewOutput) {
  const preview = await wb.render({sheetName: 'Hướng dẫn', autoCrop: 'all', scale: 1, format: 'png'});
  await fs.writeFile(previewOutput, new Uint8Array(await preview.arrayBuffer()));
}
process.stdout.write(JSON.stringify({cases: cases.length, output}));
