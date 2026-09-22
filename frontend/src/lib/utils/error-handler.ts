/**
 * Utility to map backend English error messages to i18n keys.
 */
export const ERROR_MAP: Record<string, string> = {
  "Notebook not found": "apiErrors.notebookNotFound",
  "Source not found": "apiErrors.sourceNotFound",
  "File upload failed": "apiErrors.fileUploadFailed",
  "URL is required for link type": "apiErrors.urlRequired",
  "Content is required for text type": "apiErrors.contentRequired",
  "Invalid source type": "apiErrors.invalidSourceType",
  "Processing failed": "apiErrors.processingFailed",
  "Failed to queue processing": "apiErrors.failedToQueue",
  "sort_by must be 'created' or 'updated'": "apiErrors.invalidSortBy",
  "sort_order must be 'asc' or 'desc'": "apiErrors.invalidSortOrder",
  "Access to file denied": "apiErrors.accessDenied",
  "File not found on server": "apiErrors.fileNotFoundOnServer",
  "Missing authorization": "apiErrors.unauthorized",
  "Invalid password": "apiErrors.invalidPassword",
  "Invalid authorization header format": "apiErrors.unauthorized",
  "Missing authorization header": "apiErrors.unauthorized",
  "Vector search requires an embedding model": "apiErrors.embeddingModelRequired",
  "Ask feature requires an embedding model": "apiErrors.embeddingModelRequired",
  "Strategy model": "apiErrors.strategyModelNotFound",
  "Answer model": "apiErrors.answerModelNotFound",
  "Final answer model": "apiErrors.finalAnswerModelNotFound",
  "No answer generated": "apiErrors.noAnswerGenerated",
};

const DEFAULT_ERROR = "Không thể hoàn tất thao tác. Vui lòng thử lại.";

/** Machine codes are for branching and logs; these messages are safe to render. */
const VIETNAMESE_ERROR_MAP: Record<string, string> = {
  form_case_version_conflict: 'Hồ sơ vừa được cập nhật. Hãy làm mới rồi thử lại.',
  form_published_replacement_required: 'Mẫu đã công khai cần tạo bản thay thế hoặc gỡ khỏi công khai, không sửa trực tiếp.',
  form_replacement_already_pending: 'Mẫu này đã có bản thay thế đang xử lý. Hãy mở hồ sơ đó trong danh sách.',
  form_replacement_stale: 'Mẫu gốc đã thay đổi. Hãy tạo bản thay thế từ mẫu mới nhất.',
  form_release_base_stale: 'Danh mục công khai vừa thay đổi. Hãy xác nhận và tạo bản phát hành mới.',
  form_attestation_stale: 'Hồ sơ đã thay đổi sau khi xác nhận. Cần kiểm tra và xác nhận lại.',
  form_binding_audience_mismatch: 'Đối tượng sử dụng mẫu không khớp với liên kết thủ tục. Hãy kiểm tra các thủ tục dùng chung mẫu.',
  legal_source_content_unavailable: 'Chưa lấy được toàn văn từ trang nguồn. Hãy tải tệp gốc hoặc dán nội dung; liên kết vẫn được giữ để đối chiếu.',
  faq_active_release_changed: 'Danh mục công khai vừa thay đổi. Hãy tải lại và tạo bản phát hành mới.',
  faq_withdrawal_reason_and_release_required: 'Hãy nhập lý do ít nhất 10 ký tự và tải lại danh mục đang công khai.',
  faq_withdrawal_not_active: 'Câu hỏi này không còn trong danh mục công khai. Hãy tải lại danh sách.',
  faq_withdrawal_items_invalid: 'Không thể vừa rút vừa phát hành cùng một câu hỏi. Hãy kiểm tra lại lựa chọn.',
  faq_withdrawal_recovery_unavailable: 'Chưa lưu được hồ sơ rút nội dung an toàn. Chưa thay đổi danh mục công khai; vui lòng thử lại.',
  unauthorized: "Phiên đăng nhập không còn hợp lệ. Vui lòng đăng nhập lại.",
  missing_authorization: "Phiên đăng nhập không còn hợp lệ. Vui lòng đăng nhập lại.",
  invalid_authorization: "Phiên đăng nhập không còn hợp lệ. Vui lòng đăng nhập lại.",
  invalid_password: "Mật khẩu không đúng. Vui lòng kiểm tra và thử lại.",
  invalid_credentials: "Tên đăng nhập hoặc mật khẩu không đúng.",
  must_change_password: "Bạn cần đổi mật khẩu khởi tạo trước khi tiếp tục.",
  account_disabled: "Tài khoản này đang bị khóa. Vui lòng liên hệ quản trị viên.",
  access_denied: "Bạn không có quyền thực hiện thao tác này.",
  forbidden: "Bạn không có quyền thực hiện thao tác này.",

  document_vectors_not_ready_for_restore:
    "Chưa thể khôi phục vì dữ liệu tra cứu của văn bản chưa được lập đầy đủ. Hãy lập lại chỉ mục rồi thử lại.",
  document_vectors_not_ready_for_history:
    "Chưa thể chuyển sang tra cứu lịch sử vì dữ liệu tra cứu lịch sử chưa đầy đủ. Hãy lập lại chỉ mục rồi thử lại.",
  document_serving_state_changed:
    "Văn bản vừa được cập nhật ở nơi khác. Hệ thống đã tải lại dữ liệu; vui lòng kiểm tra rồi xác nhận lại.",
  document_state_applied_audit_pending:
    "Trạng thái văn bản đã được cập nhật, nhưng lịch sử thao tác đang được ghi bổ sung. Vui lòng tải lại sau ít phút.",
  document_metadata_changed:
    "Thông tin văn bản vừa được chỉnh sửa ở nơi khác. Hệ thống đã tải lại dữ liệu; vui lòng kiểm tra rồi lưu lại.",
  immutable_release_document_requires_replacement:
    "Văn bản thuộc bộ dữ liệu đã phát hành nên không thể sửa trực tiếp. Hãy dùng chức năng Thay thế văn bản.",
  document_metadata_updated_validity_pending:
    "Metadata đã lưu nhưng trạng thái hiệu lực chưa đồng bộ. Hãy tải lại và xác nhận hiệu lực lần nữa.",
  expired_date_before_effective_date:
    "Ngày hết hiệu lực không được trước ngày có hiệu lực.",
  expired_document_cannot_be_restored_to_search:
    "Văn bản đã hết hiệu lực hoặc đã được thay thế nên không thể dùng cho tra cứu hiện hành. Bạn có thể đưa văn bản vào tra cứu lịch sử.",
  document_not_effective_for_current_search:
    "Văn bản chưa đến ngày có hiệu lực nên chưa thể dùng cho tra cứu hiện hành.",
  unactivated_document_cannot_enter_search:
    "Văn bản chưa hoàn tất kích hoạt nên chưa thể đưa vào tìm kiếm.",
  immutable_release_document_cannot_be_hard_deleted:
    "Văn bản đang thuộc bộ dữ liệu đã phát hành nên không thể xóa vĩnh viễn trực tiếp. Hãy loại văn bản khỏi tìm kiếm và phát hành lại bộ dữ liệu.",
  live_vector_verification_required:
    "Hệ thống chưa xác nhận đã dọn hết dữ liệu tra cứu của văn bản nên tạm dừng xóa để tránh dữ liệu còn sót.",
  document_hard_delete_confirmation_mismatch:
    "Số hiệu xác nhận không khớp. Vui lòng nhập đúng số hiệu đang hiển thị.",
  document_not_found: "Không tìm thấy văn bản này hoặc văn bản đã được xóa.",
  document_already_exists: "Văn bản này đã có trong kho. Vui lòng mở bản đang có để đối chiếu.",
  duplicate_document: "Văn bản này có thể đã tồn tại. Vui lòng đối chiếu bản trùng trước khi duyệt.",
  replacement_document_not_ready: "Văn bản thay thế chưa đủ dữ liệu để kích hoạt.",
  replacement_conflict: "Quan hệ thay thế vừa thay đổi. Vui lòng tải lại và kiểm tra trước khi tiếp tục.",
  replacement_activation_failed:
    "Chưa hoàn tất thay thế. Bản cũ vẫn được bảo vệ; hãy kiểm tra trạng thái hai văn bản rồi thử lại.",
  replacement_assignment_sync_failed:
    "Bản thay thế đã được tạo nhưng chưa đồng bộ phòng ban. Bản mới đã được tạm ngừng để đối soát an toàn.",
  replacement_confirmation_incomplete:
    "Chưa xác nhận đủ kết quả thay thế. Hãy tải lại trang và kiểm tra trạng thái bản cũ, bản mới.",
  replacement_idempotency_conflict:
    "Nội dung yêu cầu thay thế không khớp với lượt đang xử lý. Hãy tải lại rồi gửi một yêu cầu mới.",
  replacement_in_progress:
    "Yêu cầu thay thế này đang được xử lý. Vui lòng chờ hoàn tất, không gửi lặp lại.",
  replacement_previous_attempt_failed:
    "Lượt thay thế trước chưa hoàn tất. Hãy kiểm tra bản mới đã tạo trước khi thử lại.",
  replacement_import_not_active:
    "Bản thay thế chưa được kích hoạt đầy đủ nên bản cũ chưa bị thay đổi.",
  replacement_retrieval_precheck_failed:
    "Bản thay thế chưa xuất hiện đầy đủ trong kho tra cứu nên bản cũ vẫn được giữ nguyên.",
  replacement_post_switch_retrieval_smoke_failed:
    "Kiểm tra tra cứu sau thay thế chưa đạt. Hệ thống đã bảo vệ hoặc phục hồi bản cũ; hãy kiểm tra trạng thái trước khi thử lại.",
  replacement_document_mismatch:
    "Bản thay thế không khớp văn bản đang mở. Hãy tải lại trang và thực hiện lại từ đúng văn bản.",
  replacement_document_must_differ:
    "Văn bản thay thế phải là một bản ghi khác với văn bản cũ.",
  replacement_document_not_current:
    "Bản thay thế chưa sẵn sàng cho tra cứu hiện hành nên chưa thể chuyển bản cũ sang lịch sử.",
  source_not_verified: "Nguồn văn bản chưa được xác minh nên chưa thể thực hiện thao tác này.",
  schema_not_approved: "Chức năng này chưa được cấu hình để sử dụng.",
  vector_store_not_ready: "Kho tra cứu hiện chưa sẵn sàng. Vui lòng thử lại sau.",

  proposal_candidate_not_found: "Không tìm thấy đề xuất này hoặc đề xuất đã được xử lý.",
  candidate_not_found: "Không tìm thấy bản đề xuất này hoặc bản đề xuất đã được xử lý.",
  candidate_revision_conflict: "Đề xuất vừa được cập nhật. Vui lòng tải lại trước khi tiếp tục.",
  candidate_already_imported: "Văn bản trong đề xuất này đã được nhập kho.",
  crawl_already_running: "Nguồn này đang được quét. Hệ thống không tạo thêm một lượt quét trùng.",
  source_fetch_failed: "Không đọc được nội dung từ nguồn đã cung cấp. Vui lòng kiểm tra đường dẫn rồi thử lại.",
  extraction_failed: "Không đọc được nội dung tệp. Vui lòng kiểm tra định dạng hoặc chọn tệp khác.",
  import_failed: "Không thể nhập văn bản vào kho. Dữ liệu cũ vẫn được giữ nguyên.",
  processing_failed: "Không thể xử lý dữ liệu. Vui lòng kiểm tra nguồn rồi thử lại.",
  file_too_large: "Tệp vượt quá dung lượng cho phép. Vui lòng chọn tệp nhỏ hơn.",
  unsupported_file_type: "Định dạng tệp này chưa được hỗ trợ.",

  conversation_has_no_user_question: "Cuộc trò chuyện này chưa có câu hỏi để chuyển hỗ trợ.",
  model_not_found: "Không tìm thấy mô hình đã chọn. Vui lòng chọn mô hình khác.",
  model_unavailable: "Mô hình đã chọn hiện chưa sẵn sàng. Vui lòng thử lại hoặc chọn mô hình khác.",
  provider_unavailable: "Dịch vụ mô hình hiện chưa sẵn sàng. Vui lòng thử lại sau.",
  provider_timeout: "Mô hình phản hồi quá thời gian cho phép. Vui lòng thử lại.",
  firebase_email_exists_local: "Email này đã có tài khoản nội bộ. Hãy nhập đúng mật khẩu tài khoản đó để liên kết Google.",
  firebase_link_email_mismatch: "Email Google phải trùng email của tài khoản nội bộ.",
  firebase_link_citizen_only: "Chỉ tài khoản công dân mới được liên kết Google tại màn hình đăng nhập này.",
  firebase_uid_already_linked: "Tài khoản Google này đã được liên kết với một tài khoản khác.",
  local_account_already_linked: "Tài khoản nội bộ đã liên kết với một tài khoản Google khác.",
  api_key_invalid: "Khóa kết nối không hợp lệ. Vui lòng kiểm tra lại trong phần cài đặt.",
  rate_limit_exceeded: "Dịch vụ đang nhận quá nhiều yêu cầu. Vui lòng chờ một lúc rồi thử lại.",
  no_answer_generated: "Hệ thống chưa tạo được câu trả lời. Vui lòng thử lại.",
};

const STATUS_MESSAGES: Record<number, string> = {
  400: "Thông tin gửi lên chưa hợp lệ. Vui lòng kiểm tra và thử lại.",
  401: "Phiên đăng nhập không còn hợp lệ. Vui lòng đăng nhập lại.",
  403: "Bạn không có quyền thực hiện thao tác này.",
  404: "Không tìm thấy dữ liệu yêu cầu hoặc dữ liệu đã được xóa.",
  408: "Yêu cầu mất quá nhiều thời gian. Vui lòng thử lại.",
  409: "Dữ liệu vừa được thay đổi ở nơi khác. Vui lòng tải lại rồi thực hiện lại.",
  413: "Tệp vượt quá dung lượng cho phép. Vui lòng chọn tệp nhỏ hơn.",
  422: "Một số thông tin chưa hợp lệ. Vui lòng kiểm tra các trường đã nhập.",
  429: "Hệ thống đang nhận quá nhiều yêu cầu. Vui lòng chờ một lúc rồi thử lại.",
  500: "Hệ thống gặp sự cố khi xử lý yêu cầu. Dữ liệu của bạn chưa bị thay đổi.",
  502: "Dịch vụ đang tạm thời gián đoạn. Vui lòng thử lại sau.",
  503: "Dịch vụ đang tạm thời gián đoạn. Vui lòng thử lại sau.",
  504: "Dịch vụ phản hồi quá thời gian cho phép. Vui lòng thử lại.",
};

type ErrorDetails = {
  code?: string;
  message?: string;
  status?: number;
};

function tryParseJson(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed || !["{", "["].includes(trimmed[0])) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function firstString(...values: unknown[]): string | undefined {
  return values.find(
    (value): value is string => typeof value === "string" && Boolean(value.trim()),
  )?.trim();
}

function normalizeCode(value?: string): string | undefined {
  if (!value) return undefined;
  return value.trim().toLowerCase().replace(/[\s.-]+/g, "_");
}

function readDetails(value: unknown, seen = new Set<unknown>()): ErrorDetails {
  if (value == null || seen.has(value)) return {};
  if (typeof value === "string") {
    const parsed = tryParseJson(value);
    if (parsed !== value) return readDetails(parsed, seen);
    return { message: value.trim(), code: normalizeCode(value) };
  }
  if (typeof value !== "object") return {};
  seen.add(value);

  if (Array.isArray(value)) {
    const messages = value
      .map((item) => readDetails(item, seen).message)
      .filter((item): item is string => Boolean(item));
    return { message: messages.length ? messages.join("; ") : undefined };
  }

  const record = value as Record<string, unknown>;
  const response = record.response && typeof record.response === "object"
    ? record.response as Record<string, unknown>
    : undefined;
  const nested = readDetails(
    response?.data ?? record.detail ?? record.error ?? record.errors,
    seen,
  );
  const ownCode = firstString(record.code, record.error_code, record.reason_code);
  const ownMessage = firstString(record.message, record.error_description);
  const statusCandidate = response?.status ?? record.status ?? record.statusCode;

  return {
    code: normalizeCode(ownCode) || nested.code,
    message: nested.message || ownMessage,
    status: typeof statusCandidate === "number" ? statusCandidate : nested.status,
  };
}

function mappedMessage(details: ErrorDetails): string | undefined {
  const code = normalizeCode(details.code);
  const messageCode = normalizeCode(details.message);
  if (code && VIETNAMESE_ERROR_MAP[code]) return VIETNAMESE_ERROR_MAP[code];
  if (messageCode && VIETNAMESE_ERROR_MAP[messageCode]) return VIETNAMESE_ERROR_MAP[messageCode];

  const lower = (details.message || "").toLowerCase();
  if (lower.includes("failed to fetch") || lower.includes("network error") || lower.includes("err_network")) {
    return "Không thể kết nối tới hệ thống. Vui lòng kiểm tra kết nối mạng rồi thử lại.";
  }
  if (lower.includes("timeout") || lower.includes("timed out") || code === "econnaborted" || code === "etimedout") {
    return "Yêu cầu mất quá nhiều thời gian. Vui lòng thử lại.";
  }
  if (lower.includes("file upload failed")) return "Không thể tải tệp lên. Vui lòng kiểm tra tệp rồi thử lại.";
  if (lower.includes("not found")) return STATUS_MESSAGES[404];
  if (lower.includes("permission denied") || lower.includes("access denied")) return STATUS_MESSAGES[403];
  return undefined;
}

function looksLikeVietnameseUserCopy(value: string): boolean {
  if (!value || value.length > 600) return false;
  if (/[{}[\]<>]|\b(?:traceback|exception|stack|http\s*\d{3}|sql|axios|typeerror|referenceerror|crawl4ai|anti-bot|minimal_text|selector|playwright)\b/i.test(value)) return false;
  if (/(?:^|[|;,])\s*[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}/i.test(value)) return false;
  if (/^[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}$/i.test(value.trim())) return false;
  return /[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/i.test(value)
    || /\b(không|vui lòng|đã|chưa|cần|hệ thống|tài khoản|mật khẩu|văn bản|yêu cầu|dữ liệu|thao tác|nguồn|tệp|lỗi)\b/i.test(value);
}

/** Returns the backend machine code for branching or audit; never render it. */
export function getApiErrorCode(error: unknown): string | undefined {
  return readDetails(error).code;
}

/**
 * Formats any API/runtime error for direct display in the Vietnamese UI.
 * Unknown English or technical content is intentionally replaced by fallback.
 */
export function formatApiError(error: unknown, fallback = DEFAULT_ERROR): string {
  const details = readDetails(error);
  const mapped = mappedMessage(details);
  if (mapped) return mapped;
  if (details.message && looksLikeVietnameseUserCopy(details.message)) return details.message;
  if (details.status && STATUS_MESSAGES[details.status]) return STATUS_MESSAGES[details.status];
  return fallback;
}

/** Returns an i18n key for legacy translated surfaces. */
export function getApiErrorKey(errorOrMessage: unknown, fallbackKey?: string): string {
  const raw = readDetails(errorOrMessage).message;
  if (raw && ERROR_MAP[raw]) return ERROR_MAP[raw];
  if (raw) {
    for (const [message, key] of Object.entries(ERROR_MAP)) {
      if (raw.startsWith(message)) return key;
    }
  }
  return fallbackKey || "apiErrors.genericError";
}

/** Uses existing i18n keys where available, otherwise safe Vietnamese copy. */
export function getApiErrorMessage(
  errorOrMessage: unknown,
  t: (key: string) => string,
  fallbackKey?: string,
): string {
  const raw = readDetails(errorOrMessage).message;
  if (raw && ERROR_MAP[raw]) return t(ERROR_MAP[raw]);
  if (raw) {
    for (const [message, key] of Object.entries(ERROR_MAP)) {
      if (raw.startsWith(message)) return t(key);
    }
  }

  const fallback = fallbackKey ? t(fallbackKey) : t("apiErrors.genericError");
  return formatApiError(errorOrMessage, fallback || DEFAULT_ERROR);
}
