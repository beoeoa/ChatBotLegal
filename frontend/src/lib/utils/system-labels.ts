/** User-facing labels for machine states returned by the backend. */
const STATUS_LABELS: Record<string, string> = {
  active: 'Đang hoạt động',
  inactive: 'Tạm ngừng',
  available: 'Sẵn sàng',
  unavailable: 'Chưa sẵn sàng',
  degraded: 'Hoạt động chưa đầy đủ',
  draft: 'Bản nháp',
  staging: 'Đang chuẩn bị',
  pending: 'Đang chờ xử lý',
  submitted: 'Đã gửi, đang chờ duyệt',
  resubmitted: 'Đã bổ sung, đang chờ duyệt',
  needs_supplement: 'Cần bổ sung thông tin',
  source_approved: 'Nguồn đã được duyệt',
  legal_enrichment: 'Đang hoàn thiện thông tin pháp lý',
  ready_for_attestation: 'Chờ xác nhận pháp lý',
  queued: 'Đang chờ xử lý',
  running: 'Đang xử lý',
  processing: 'Đang xử lý',
  completed: 'Đã hoàn tất',
  completed_with_warnings: 'Đã hoàn tất, cần kiểm tra',
  failed: 'Không hoàn tất',
  approved: 'Đã duyệt',
  rejected: 'Đã từ chối',
  changes_requested: 'Cần bổ sung',
  imported: 'Đã nhập kho',
  import_queued: 'Đang chờ nhập kho',
  import_failed: 'Nhập kho không thành công',
  duplicate_archived: 'Đã lưu bản trùng',
  replacement_review: 'Chờ đối chiếu thay thế',
  release_candidate: 'Đang kiểm tra trước khi phát hành',
  candidate: 'Bản thử đang chờ kiểm tra',
  candidate_pending_review: 'Đang chờ kiểm tra',
  ready_for_human_attestation: 'Chờ người duyệt xác nhận',
  confirmed: 'Đã xác nhận',
  dismissed: 'Đã đóng',
  released: 'Đã phát hành',
  validated: 'Đã đạt điều kiện kiểm tra',
  withdrawn: 'Đã rút',
  attested: 'Đã xác nhận nghiệp vụ',
  quarantined: 'Tạm cách ly khỏi tra cứu',
  current_retrievable: 'Đang dùng cho tra cứu hiện hành',
  historical_only: 'Chỉ dùng cho tra cứu lịch sử',
  future_effective: 'Chưa dùng; đang chờ ngày có hiệu lực',
  excluded: 'Đã loại khỏi tìm kiếm',
  blocked: 'Đã chặn',
  archived: 'Đã lưu trữ',
  historical: 'Đang lưu trữ để tra cứu lịch sử',
  missing: 'Thiếu dữ liệu tra cứu',
  orphan: 'Không còn văn bản nguồn',
  duplicate: 'Dữ liệu bị trùng',
  fingerprint_mismatch: 'Dữ liệu không đồng bộ',
  expired: 'Hết hiệu lực',
  not_yet_effective: 'Chưa có hiệu lực',
  unknown: 'Chưa xác minh',
  not_started: 'Chưa bắt đầu',
  verified_data_gap: 'Thiếu dữ liệu đã xác minh',
  completed_fail_closed: 'Đã dừng an toàn để chờ xử lý',
  attested_pending_release_gates: 'Đã xác nhận, đang chờ kiểm tra phát hành',
};

const DATA_QUALITY_LABELS: Record<string, string> = {
  missing_source: 'Thiếu nguồn chính thức',
  missing_metadata: 'Thiếu thông tin mô tả',
  zero_chunks: 'Chưa có nội dung tra cứu',
  unknown_status: 'Chưa xác minh hiệu lực',
  unclassified: 'Chưa phân loại lĩnh vực',
  missing_effective_date: 'Thiếu ngày có hiệu lực',
  missing_issued_date: 'Thiếu ngày ban hành',
  missing_law_number: 'Thiếu số, ký hiệu văn bản',
  missing_issuing_agency: 'Thiếu cơ quan ban hành',
  fingerprint_mismatch: 'Dữ liệu tra cứu không đồng nhất',
  missing: 'Thiếu dữ liệu tra cứu',
};

function normalized(value?: string | null): string {
  return String(value || '').trim().toLowerCase().replace(/[\s.-]+/g, '_');
}

export function systemStatusLabel(value?: string | null, fallback = 'Chưa xác định'): string {
  return STATUS_LABELS[normalized(value)] || fallback;
}

export function dataQualityLabel(value?: string | null): string {
  return DATA_QUALITY_LABELS[normalized(value)] || 'Cần kiểm tra dữ liệu';
}

export function dataQualitySummary(values?: Array<string | null> | null): string {
  const labels = [...new Set((values || []).map(dataQualityLabel))];
  return labels.length ? labels.join('; ') : 'Dữ liệu đầy đủ';
}

export function roleLabel(value?: string | null): string {
  const labels: Record<string, string> = {
    admin: 'Quản trị viên',
    officer: 'Cán bộ',
    citizen: 'Người dân',
    system: 'Hệ thống',
  };
  return labels[normalized(value)] || 'Chưa xác định vai trò';
}

export function activityActionLabel(value?: string | null): string {
  const action = normalized(value);
  const labels: Record<string, string> = {
    exclude: 'Loại văn bản khỏi tìm kiếm',
    restore: 'Khôi phục văn bản vào tìm kiếm',
    historical: 'Chuyển văn bản sang tra cứu lịch sử',
    replace: 'Thay thế văn bản',
    hard_delete: 'Xóa vĩnh viễn văn bản',
    create: 'Tạo mới',
    update: 'Cập nhật',
    delete: 'Xóa',
    approve: 'Duyệt',
    reject: 'Từ chối',
  };
  if (labels[action]) return labels[action];
  if (action.includes('hard_delete')) return labels.hard_delete;
  if (action.includes('exclude')) return labels.exclude;
  if (action.includes('restore')) return labels.restore;
  if (action.includes('histor')) return labels.historical;
  if (action.includes('replace')) return labels.replace;
  if (action.includes('approve')) return labels.approve;
  if (action.includes('reject')) return labels.reject;
  if (action.includes('delete')) return labels.delete;
  if (action.includes('update')) return labels.update;
  if (action.includes('create')) return labels.create;
  return 'Thao tác quản trị';
}
