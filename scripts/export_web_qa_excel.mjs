import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const input = 'J:/ChatBotLegal/reports/web-citizen-100/web-qa-log.json';
const outDir = 'J:/ChatBotLegal/reports/web-citizen-100';
const data = JSON.parse(await fs.readFile(input, 'utf8'));
const records = (data.records || []).sort((a,b) => a.index-b.index);
const wb = Workbook.create();
const summary = wb.worksheets.add('Tổng hợp');
const qa = wb.worksheets.add('Câu hỏi - trả lời');
summary.getRange('A1:D1').merge();
summary.getRange('A1').values = [['Báo cáo kiểm thử hỏi đáp trực tiếp trên web - vai trò người dân']];
summary.getRange('A3:B10').values = [
  ['Chỉ tiêu','Giá trị'],
  ['Tổng câu hỏi đã chạy', records.length],
  ['Hoàn thành', records.filter(r=>r.status==='completed').length],
  ['Có nguồn', records.filter(r=>r.has_sources===true).length],
  ['Chế độ dự phòng', records.filter(r=>r.fallback===true).length],
  ['Có dấu hiệu thiếu nguồn/nội dung', records.filter(r=>/Chưa đủ nguồn|Trả lời chưa đầy đủ|Cần bổ sung nguồn/.test(r.answer_text||'')).length],
  ['Lỗi/timeout', records.filter(r=>r.status!=='completed').length],
  ['Ghi chú', 'Kiểm thử trực tiếp trên UI; không suy đoán nội dung khi giao diện không trả lời đầy đủ.'],
];
summary.getRange('A12:B12').values = [['Lĩnh vực','Số câu']];
const domains = [...new Set(records.map(r=>r.domain))];
summary.getRange(`A13:B${12+domains.length}`).values = domains.map(d=>[d, records.filter(r=>r.domain===d).length]);
summary.getRange('A1:D1').format = {fill:'#1F4E78', font:{bold:true,color:'#FFFFFF',size:14}, horizontalAlignment:'center'};
summary.getRange('A3:B3').format = {fill:'#D9EAF7', font:{bold:true,color:'#000000'}};
summary.getRange('A12:B12').format = {fill:'#D9EAF7', font:{bold:true,color:'#000000'}};
summary.getRange('A1:D20').format.wrapText = true;
summary.getRange('A:A').format.columnWidth = 34;
summary.getRange('B:B').format.columnWidth = 28;
summary.freezePanes.freezeRows(3);

const headers = ['STT','Lĩnh vực','Câu hỏi','Câu trả lời trên giao diện','Trạng thái','Thời gian (giây)','Có nguồn','Dự phòng','Lỗi','Ghi chú'];
qa.getRange(`A1:J${records.length+1}`).values = [headers, ...records.map(r=>[
  r.index+1, r.domain||'', r.question||'', r.answer_text||'[Không thu được toàn văn từ phiên web trước; chỉ lưu trạng thái UI.]',
  r.status||'', r.elapsed_s??'', r.has_sources?'Có':'Không', r.fallback?'Có':'Không', r.error?'Có':'Không',
  r.answer_text ? '' : 'Cần chạy lại câu này để thu toàn văn trả lời.'
])];
qa.getRange('A1:J1').format = {fill:'#1F4E78', font:{bold:true,color:'#FFFFFF'}, wrapText:true};
qa.getRange(`A1:J${records.length+1}`).format.wrapText = true;
qa.getRange('A:A').format.columnWidth = 8; qa.getRange('B:B').format.columnWidth = 24;
qa.getRange('C:C').format.columnWidth = 55; qa.getRange('D:D').format.columnWidth = 80;
qa.getRange('E:J').format.columnWidth = 18;
qa.freezePanes.freezeRows(1);
qa.tables.add(`A1:J${records.length+1}`, true, 'WebQaLog');

await fs.mkdir(outDir, {recursive:true});
const preview = await wb.render({sheetName:'Tổng hợp', autoCrop:'all', scale:1, format:'png'});
await fs.writeFile(`${outDir}/web-qa-report-preview.png`, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(`${outDir}/web-qa-log.xlsx`);
console.log(JSON.stringify({records:records.length, output:`${outDir}/web-qa-log.xlsx`}));
