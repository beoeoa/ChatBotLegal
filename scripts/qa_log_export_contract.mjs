const DOM_MARKERS = [
  'region "ask response"',
  'textbox "enter your question',
  'button "save to notebooks"',
  'region "notifications',
];

function text(value) {
  return String(value ?? '').normalize('NFC').trim();
}

export function normalizeRun(raw, index = 0) {
  const answer = text(raw.answer ?? raw.answer_text);
  const lowered = answer.toLocaleLowerCase('vi');
  if (DOM_MARKERS.some(marker => lowered.includes(marker))) {
    throw new Error(`Record ${index + 1} contains DOM/accessibility tree instead of answer text`);
  }
  const evaluation = raw.evaluation && typeof raw.evaluation === 'object' ? raw.evaluation : {};
  const evaluated = typeof evaluation.passed === 'boolean';
  const citations = Array.isArray(raw.citations) ? raw.citations : [];
  const reasonCodes = Array.isArray(evaluation.reason_codes) ? evaluation.reason_codes : [];
  const validity = raw.validity_result && typeof raw.validity_result === 'object' ? raw.validity_result : {};
  const states = Array.isArray(validity.states) ? validity.states : [];
  const latency = raw.latency_stages && typeof raw.latency_stages === 'object' ? raw.latency_stages : {};
  return {
    ordinal: index + 1,
    runId: text(raw.run_id),
    caseId: text(raw.case_id),
    role: text(raw.role),
    domain: text(raw.domain),
    legalAsOf: text(raw.legal_as_of),
    question: text(raw.question),
    answer,
    answerMode: text(raw.answer_mode),
    groundingStatus: text(raw.grounding_status),
    evaluationLabel: evaluated ? (evaluation.passed ? 'Đạt' : 'Không đạt') : 'Chưa chấm',
    evaluationPassed: evaluated ? evaluation.passed : null,
    reasonCodes: reasonCodes.map(text).filter(Boolean).join(', '),
    citationCount: citations.length,
    validityStates: states.map(text).filter(Boolean).join(', '),
    fallbackReason: text(raw.fallback_reason),
    errorCode: text(raw.error?.code),
    endToEndMs: Number(latency.end_to_end_ms ?? latency.total_ms ?? 0) || 0,
    traceId: text(raw.trace_id),
  };
}

export function summarizeRuns(rows) {
  return {
    total: rows.length,
    passed: rows.filter(row => row.evaluationPassed === true).length,
    failed: rows.filter(row => row.evaluationPassed === false).length,
    unscored: rows.filter(row => row.evaluationPassed === null).length,
    errors: rows.filter(row => row.errorCode).length,
    sourceOnly: rows.filter(row => row.answerMode === 'source_view_only').length,
  };
}
