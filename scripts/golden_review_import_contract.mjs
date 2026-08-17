const NAMED_DOCUMENTS = [
  ['bộ luật dân sự 2015', '91/2015/QH13'],
  ['luật hộ tịch 2014', '60/2014/QH13'],
  ['luật bảo hiểm xã hội 2014', '58/2014/QH13'],
  ['luật bhxh 2024', '41/2024/QH15'],
  ['luật bảo hiểm y tế 2008', '25/2008/QH12'],
  ['luật căn cước 2023', '26/2023/QH15'],
  ['luật công chứng 2014', '53/2014/QH13'],
  ['luật cư trú 2020', '68/2020/QH14'],
  ['luật đất đai 2024', '31/2024/QH15'],
  ['luật đất đai 2013', '45/2013/QH13'],
  ['luật khiếu nại 2011', '02/2011/QH13'],
  ['luật người cao tuổi 2009', '39/2009/QH12'],
  ['luật người khuyết tật 2010', '51/2010/QH12'],
  ['luật nhà ở 2023', '27/2023/QH15'],
  ['luật tố cáo 2018', '25/2018/QH14'],
  ['luật tố cáo 2011', '03/2011/QH13'],
  ['luật tố tụng hành chính 2015', '93/2015/QH13'],
  ['luật tố tụng hành chính 2010', '64/2010/QH12'],
  ['luật trẻ em 2016', '102/2016/QH13'],
  ['luật việc làm 2013', '38/2013/QH13'],
  ['luật xây dựng 2014', '50/2014/QH13'],
  ['luật xây dựng 2003', '16/2003/QH11'],
  ['luật xử lý vi phạm hành chính 2012', '15/2012/QH13'],
  ['luật cư trú 2006', '81/2006/QH11'],
  ['luật căn cước công dân 2014', '59/2014/QH13'],
  ['luật khiếu nại, tố cáo 1998', '09/1998/QH10'],
  ['luật bảo hiểm xã hội 2006', '71/2006/QH11'],
  ['pháp lệnh xử lý vi phạm hành chính 2002', '44/2002/PL-UBTVQH10'],
];

// These transitions are used only as fail-closed review guards.  They do not
// rewrite a source automatically: a reviewer must choose the replacement and
// its exact article.  Dates and relationships were checked against VBPL.
const REPLACED_DOCUMENTS = {
  '58/2014/QH13': {replacement: '41/2024/QH15', replacedFrom: '2025-07-01'},
  '38/2013/QH13': {replacement: '74/2025/QH15', replacedFrom: '2026-01-01'},
  '53/2014/QH13': {replacement: '46/2024/QH15', replacedFrom: '2025-07-01'},
  '146/2018/NĐ-CP': {replacement: '188/2025/NĐ-CP', replacedFrom: '2025-08-15'},
  '75/2023/NĐ-CP': {replacement: '188/2025/NĐ-CP', replacedFrom: '2025-08-15'},
  '02/2025/NĐ-CP': {replacement: '188/2025/NĐ-CP', replacedFrom: '2025-08-15'},
  '81/2021/NĐ-CP': {replacement: '238/2025/NĐ-CP', replacedFrom: '2025-09-03'},
  '97/2023/NĐ-CP': {replacement: '238/2025/NĐ-CP', replacedFrom: '2025-09-03'},
};

function normalizedText(value) {
  return String(value ?? '')
    .normalize('NFC')
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('vi');
}

function extractLawNumber(label) {
  const explicit = label.match(/\b\d{1,4}\/\d{4}\/[A-ZĐ][A-Z0-9Đ-]*\b/iu);
  if (explicit) return explicit[0].toUpperCase();
  const decision = label.match(/\b\d{1,4}\/QĐ-[A-ZĐ][A-Z0-9Đ-]*\b/iu);
  if (decision) return decision[0].toUpperCase();
  const normalized = normalizedText(label);
  const named = NAMED_DOCUMENTS.find(([prefix]) => normalized.startsWith(prefix));
  return named?.[1] ?? null;
}

function extractArticle(label) {
  const match = label.match(/Điều\s+([0-9]+(?:\s*,\s*[0-9]+)*)/iu);
  return match ? match[1].replace(/\s+/g, '') : null;
}

function isOnOrAfter(value, threshold) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(value ?? '')) && value >= threshold;
}

export function parseSourceLabel(label, options = {}) {
  const raw = String(label ?? '').normalize('NFC').trim();
  const lawNumber = extractLawNumber(raw);
  const errors = [];
  if (!lawNumber) {
    return {source: null, errors: ['source_identity_unresolved'], replacement: null};
  }
  const transition = REPLACED_DOCUMENTS[lawNumber];
  if (
    !options.forbidden
    && transition
    && isOnOrAfter(options.legalAsOf, transition.replacedFrom)
  ) {
    errors.push('expected_source_replaced');
  }
  return {
    source: {
      law_number: lawNumber,
      article: extractArticle(raw),
      clause: null,
      point: null,
      reason: raw,
      proof: null,
    },
    errors,
    replacement: transition?.replacement ?? null,
  };
}

function normalizeClaim(value, index) {
  const raw = String(value ?? '').normalize('NFC').trim();
  const text = raw.replace(/^\s*\d+[.)]\s*/, '').trim();
  return {
    claim_id: `claim-${String(index + 1).padStart(2, '0')}`,
    facet: 'reviewed_requirement',
    text,
    order: index + 1,
    critical: true,
  };
}

function addUnique(target, code) {
  if (!target.includes(code)) target.push(code);
}

export function normalizeReviewedRow(row) {
  const errors = [];
  const warnings = [];
  const expectedSources = [];
  const forbiddenSources = [];
  const replacements = [];

  for (const label of row.expectedSources ?? []) {
    const parsed = parseSourceLabel(label, {
      forbidden: false,
      legalAsOf: row.legalAsOf,
    });
    for (const code of parsed.errors) addUnique(errors, code);
    if (parsed.replacement) replacements.push({label, replacement: parsed.replacement});
    if (parsed.source) expectedSources.push(parsed.source);
  }
  for (const label of row.forbiddenSources ?? []) {
    const parsed = parseSourceLabel(label, {
      forbidden: true,
      legalAsOf: row.legalAsOf,
    });
    for (const code of parsed.errors) addUnique(errors, code);
    if (parsed.source) forbiddenSources.push(parsed.source);
  }

  const claims = (row.requiredClaims ?? [])
    .map(normalizeClaim)
    .filter((claim) => claim.text.length > 0);
  if (!expectedSources.length && !row.expectedRefusal) {
    addUnique(errors, 'reviewed_case_missing_expected_source');
  }
  if (row.reviewStatus === 'approved' && claims.length && row.expectedRefusal) {
    addUnique(errors, 'approved_claim_case_cannot_expect_refusal');
  }
  if (
    row.reviewStatus === 'approved'
    && claims.length
    && row.answerMode === 'source_view_only'
  ) {
    addUnique(errors, 'approved_claim_case_requires_answer_mode');
  }
  if (!String(row.reviewer ?? '').trim()) {
    warnings.push('reviewer_missing_in_workbook');
  }

  const importedStatus = ['proposed', 'legally_reviewed', 'approved'].includes(row.reviewStatus)
    ? row.reviewStatus
    : 'proposed';
  const safeStatus = errors.length ? 'proposed' : importedStatus;
  const riskTags = ['reviewed_workbook_import'];
  if (errors.length) riskTags.push('review_import_requires_correction', ...errors);

  return {
    case: {
      case_id: String(row.caseId ?? '').trim(),
      schema_version: '2.0',
      domain: String(row.domain ?? '').trim(),
      procedure_family: String(row.procedureFamily ?? '').trim() || null,
      legal_as_of: String(row.legalAsOf ?? '').trim(),
      questions: {citizen: String(row.question ?? '').trim(), officer: null},
      expected_sources: expectedSources,
      forbidden_sources: forbiddenSources,
      required_claims: claims,
      expected_answer_mode: row.answerMode,
      expected_refusal: Boolean(row.expectedRefusal),
      risk_tags: [...new Set(riskTags)],
      review_status: safeStatus,
    },
    errors,
    warnings,
    replacements,
  };
}

export const REVIEW_IMPORT_POLICY_VERSION = 'feature016-review-import-v1';
