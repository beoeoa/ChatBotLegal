"use client";

import {
  ChangeEvent,
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertCircle,
  Check,
  FileUp,
  MessageSquare,
  Search,
  Send,
  Sparkles,
  Star,
  UserCheck,
  Wifi,
  WifiOff,
} from "lucide-react";
import { toast } from "sonner";

import { apiClient } from "@/lib/api/client";
import { searchApi } from "@/lib/api/search";
import { getApiUrl } from "@/lib/config";
import { useAuthStore } from "@/lib/stores/auth-store";
import { formatApiError } from "@/lib/utils/error-handler";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

const DOMAINS: Record<string, string> = {
  ho_tich_chung_thuc: "Hộ tịch - Chứng thực",
  dat_dai_xay_dung: "Đất đai - Xây dựng",
  an_sinh_y_te_giao_duc: "An sinh - Y tế - Giáo dục",
  cu_tru_an_ninh: "Cư trú - An ninh trật tự",
  khieu_nai_to_cao_xu_phat: "Khiếu nại - Tố cáo - Xử phạt",
  hanh_chinh_cong: "Hành chính công",
  trat_tu_do_thi: "Trật tự đô thị",
};
const STAFFED_DOMAIN_KEYS = [
  "ho_tich_chung_thuc",
  "dat_dai_xay_dung",
  "an_sinh_y_te_giao_duc",
  "hanh_chinh_cong",
  "trat_tu_do_thi",
] as const;

const STATUS_LABELS: Record<string, string> = {
  waiting: "Chờ tiếp nhận",
  queued: "Đang chờ phân công",
  assigned: "Đã phân công",
  active: "Đang xử lý",
  waiting_citizen: "Chờ người dân bổ sung",
  waiting_officer: "Chờ cán bộ phản hồi",
  resolved: "Đã xử lý",
  closed: "Đã đóng",
  cancelled: "Đã hủy",
  expired: "Hết hạn",
};

const ROLE_LABELS: Record<string, string> = {
  citizen: "Người dân",
  officer: "Cán bộ hỗ trợ",
  admin: "Quản trị viên",
  system: "Hệ thống",
};

type TicketStatus =
  | "waiting"
  | "queued"
  | "assigned"
  | "active"
  | "waiting_citizen"
  | "waiting_officer"
  | "resolved"
  | "closed"
  | "cancelled"
  | "expired";

interface Attachment {
  id: string;
  name: string;
  size: number;
  download_url: string;
}

interface Message {
  id: string;
  sender_id: string;
  sender_role: string;
  content: string;
  attachments?: Attachment[];
  created_at: string;
}

interface Ticket {
  id: string;
  version?: number;
  citizen_id?: string;
  question?: string;
  ai_summary?: string | null;
  notice?: string | null;
  domain: string;
  primary_organization_unit_id?: string | null;
  canonical_domain?: string;
  status: TicketStatus;
  priority: string;
  assigned_officer_id?: string | null;
  message_count: number;
  unread_count?: number;
  created_at: string;
  updated_at: string;
  messages?: Message[];
  rating?: number | null;
  needs_attention?: boolean;
}

function normalizeTicket(ticket: Partial<Ticket> & { id: string }): Ticket {
  return {
    citizen_id: "",
    question: "",
    ai_summary: null,
    notice: null,
    priority: "normal",
    message_count: 0,
    created_at: "",
    updated_at: "",
    status: "waiting",
    ...ticket,
    domain: ticket.domain || ticket.canonical_domain || "",
  };
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status;
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] || "Thành viên hỗ trợ";
}

function LegacySupportContent({ content }: { content: string }) {
  const parts = content.split(/(?:^|\n)\s*(user|assistant|system):\s*/i);
  if (parts.length < 3) {
    return <p className="whitespace-pre-wrap break-words">{content}</p>;
  }
  const blocks: Array<{ role: string; text: string }> = [];
  for (let index = 1; index < parts.length; index += 2) {
    const role = parts[index] || "system";
    const text = parts[index + 1] || "";
    if (text.trim()) blocks.push({ role, text: text.trim() });
  }
  return (
    <div className="space-y-2 rounded-md border border-dashed p-2 text-sm">
      <p className="text-xs font-medium text-muted-foreground">Nội dung phiên bản cũ</p>
      {blocks.map((block, index) => (
        <div key={`${block.role}-${index}`}>
          <p className="text-xs font-medium text-muted-foreground">{roleLabel(block.role)}</p>
          <p className="whitespace-pre-wrap break-words">{block.text}</p>
        </div>
      ))}
    </div>
  );
}

function apiErrorDetail(error: unknown, fallback: string): string {
  return formatApiError(error, fallback);
}

function authState() {
  return useAuthStore.getState();
}

async function supportWebSocketUrl(
  ticketId?: string,
  domain?: string,
  officerQueue = false,
) {
  const authTicket = await apiClient.post<{
    ticket: string;
    expires_in_seconds: number;
  }>("/support/ws-ticket", {
    ticket_id: ticketId || null,
    domain: domain || null,
    officer_queue: officerQueue,
  });
  let apiUrl = "";
  try {
    apiUrl = await getApiUrl();
  } catch {
    // Realtime must connect to FastAPI directly; Next.js rewrites do not
    // reliably proxy WebSocket upgrades in local development.
    apiUrl = `${window.location.protocol}//${window.location.hostname}:5055`;
  }
  const origin = (apiUrl || window.location.origin)
    .replace(/\/$/, "")
    .replace(/^http/, "ws");
  const params = new URLSearchParams({
    auth_ticket: authTicket.data.ticket,
    ...(ticketId ? { ticket_id: ticketId } : {}),
    ...(domain ? { domain } : {}),
    ...(officerQueue ? { queue: "officer" } : {}),
  });
  return `${origin}/api/support/ws?${params}`;
}

export default function LiveSupportPage() {
  const { role, userId, username } = useAuthStore();
  const searchParams = useSearchParams();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [queueTickets, setQueueTickets] = useState<Ticket[]>([]);
  const [supportTab, setSupportTab] = useState<"active" | "queue" | "completed">("active");
  const [ticketSearch, setTicketSearch] = useState("");
  const [mobileListOpen, setMobileListOpen] = useState(false);
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [overview, setOverview] = useState({
    active_count: 0,
    max_capacity: 3,
    presence_status: "offline",
    queue_count: 0,
    needs_attention_count: 0,
    oldest_wait_seconds: 0,
  });
  const [active, setActive] = useState<Ticket | null>(null);
  const [ticketId, setTicketId] = useState<string | null>(
    searchParams?.get("ticket") || null,
  );
  const domain = "ho_tich_chung_thuc";
  const [unitId, setUnitId] = useState("");
  const [routingUnits, setRoutingUnits] = useState<Array<{unit_id: string; unit_name: string}>>([]);
  const [routingRevision, setRoutingRevision] = useState<number>();
  const [departmentRoutingEnabled, setDepartmentRoutingEnabled] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void apiClient.get<{config_revision: number; department_routing_enabled: boolean; options: Array<{unit_id: string; unit_name: string}>}>("/support/routing-options").then(({ data }) => {
      if (cancelled) return;
      const units = Array.from(new Map(data.options.map(item => [item.unit_id, item])).values());
      setRoutingUnits(units); setRoutingRevision(data.config_revision);
      setDepartmentRoutingEnabled(data.department_routing_enabled === true);
      setUnitId(current => units.some(item => item.unit_id === current) ? current : units[0]?.unit_id || "");
    }).catch(() => { if (!cancelled) setRoutingUnits([]) });
    return () => { cancelled = true };
  }, [role]);
  const [question, setQuestion] = useState("");
  const [input, setInput] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>(
    [],
  );
  const [typing, setTyping] = useState(false);
  const [connected, setConnected] = useState(false);
  const [queueConnected, setQueueConnected] = useState(false);
  const [rating, setRating] = useState(0);
  const [transferDomain, setTransferDomain] = useState("");
  const [transferReason, setTransferReason] = useState("");
  const [transferOpen, setTransferOpen] = useState(false);
  const [resolveOpen, setResolveOpen] = useState(false);
  const [resolutionNote, setResolutionNote] = useState("");
  const [officerDomains, setOfficerDomains] = useState<string[]>([]);
  const socketRef = useRef<WebSocket | null>(null);
  const queueSocketRef = useRef<WebSocket | null>(null);
  const currentUser = userId || username || `legacy:${role || "citizen"}`;

  const [copilotOpen, setCopilotOpen] = useState(false);
  const [copilotQuery, setCopilotQuery] = useState("");
  const [copilotLoading, setCopilotLoading] = useState(false);
  const [copilotAnswer, setCopilotAnswer] = useState<string | null>(null);

  const runCopilotSearch = async (queryText?: string) => {
    const q = (queryText || copilotQuery || active?.question || "").trim();
    if (!q) return;
    setCopilotLoading(true);
    try {
      const res = await searchApi.askKnowledgeBaseSimple({
        question: q,
        role: role || "citizen",
        strategy_model: "",
        answer_model: "",
        final_answer_model: "",
        domain: active?.domain || domain,
      });
      setCopilotAnswer(res.answer);
    } catch {
      toast.error("Không thể tra cứu AI Copilot.");
    } finally {
      setCopilotLoading(false);
    }
  };

  const refreshList = useCallback(async () => {
    try {
      const response = role === "officer"
        ? await apiClient.get<Ticket[]>("/support/officer/worklist", { params: { limit: 20 } })
        : await apiClient.get<Ticket[]>("/support/tickets");
      setTickets(response.data.map((ticket) => normalizeTicket(ticket)));
    } catch {
      // Polling is best effort. The page remains usable while a service restarts.
    }
  }, [role]);

  const refreshOfficerData = useCallback(async () => {
    if (role !== "officer") return;
    try {
      const [queueResponse, overviewResponse] = await Promise.all([
        apiClient.get<Ticket[]>("/support/officer/queue"),
        apiClient.get<typeof overview>("/support/officer/overview"),
      ]);
      setQueueTickets(queueResponse.data.map((ticket) => normalizeTicket(ticket)));
      setOverview((value) => ({ ...value, ...overviewResponse.data }));
    } catch {
      // Legacy deployments may not expose the split endpoints yet.
    }
  }, [role]);

  const refreshTicket = useCallback(async (id: string) => {
    try {
      const response = await apiClient.get<Ticket>(`/support/tickets/${id}`);
      setActive(normalizeTicket(response.data));
    } catch {
      setActive(null);
    }
  }, []);

  useEffect(() => {
    void refreshList();
    void refreshOfficerData();

    // HTTP poll is fallback only: pause while the tab is hidden, and skip
    // when the primary WebSocket stream is healthy.
    let delayMs = 8000;
    let timer: number | undefined;
    let cancelled = false;

    const wsHealthy = () =>
      role === "officer" ? queueConnected : connected;

    const tick = () => {
      if (cancelled) return;
      timer = window.setTimeout(() => {
        void (async () => {
          if (cancelled) return;
          if (
            document.visibilityState === "visible" &&
            !wsHealthy()
          ) {
            await refreshList();
            await refreshOfficerData();
          }
          delayMs = 8000;
          tick();
        })();
      }, delayMs);
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible" && !wsHealthy()) {
        void refreshList();
        void refreshOfficerData();
      }
    };

    tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [connected, queueConnected, refreshList, refreshOfficerData, role]);

  useEffect(() => {
    if (role !== "officer") {
      setOfficerDomains([]);
      return;
    }
    let cancelled = false;
    void apiClient
      .get<string[]>("/support/my-domains")
      .then((response) => {
        if (!cancelled) setOfficerDomains(response.data);
      })
      .catch(() => {
        if (!cancelled) setOfficerDomains([]);
      });
    return () => {
      cancelled = true;
    };
  }, [role]);

  useEffect(() => {
    if (role !== "officer" || officerDomains.length === 0) return;
    let cancelled = false;
    const heartbeat = async () => {
      try {
        const response = await apiClient.post("/support/officer/presence", {
          domains: officerDomains,
          max_capacity: 3,
        });
        if (!cancelled) {
          setOverview((value) => ({ ...value, ...response.data }));
        }
      } catch {
        // Compatibility mode keeps polling and the legacy claim endpoint.
      }
    };
    void heartbeat();
    const heartbeatInterval = window.setInterval(
      () => void heartbeat(),
      20_000,
    );

    void supportWebSocketUrl(undefined, undefined, true)
      .then((url) => {
        if (cancelled) return;
        const socket = new WebSocket(url);
        queueSocketRef.current = socket;
        socket.onopen = () => setQueueConnected(true);
        socket.onclose = () => setQueueConnected(false);
        socket.onerror = () => setQueueConnected(false);
        socket.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (
              [
                "queue.created",
                "ticket.transferred",
                "ticket.reassigned",
              ].includes(data.type)
            ) {
              void refreshList();
              void refreshOfficerData();
              toast.info("Có thay đổi trong hàng chờ hỗ trợ của bạn.");
            }
          } catch {
            // Polling is the fallback for malformed or missed events.
          }
        };
      })
      .catch(() => {
        // Canonical single-stream mode is not active yet; polling remains safe.
        setQueueConnected(false);
      });
    return () => {
      cancelled = true;
      setQueueConnected(false);
      window.clearInterval(heartbeatInterval);
      queueSocketRef.current?.close();
      queueSocketRef.current = null;
    };
  }, [officerDomains, refreshList, refreshOfficerData, role]);

  useEffect(() => {
    if (role !== "officer" || ticketId || tickets.length === 0) return;
    const first = tickets.find((item) => item.needs_attention) || tickets[0];
    if (first) setTicketId(first.id);
  }, [role, ticketId, tickets]);

  useEffect(() => {
    if (ticketId) void refreshTicket(ticketId);
  }, [refreshTicket, ticketId]);

  useEffect(() => {
    if (!ticketId) return;

    let delayMs = 5000;
    let timer: number | undefined;
    let cancelled = false;

    const tick = () => {
      if (cancelled) return;
      timer = window.setTimeout(() => {
        void (async () => {
          if (cancelled) return;
          if (document.visibilityState === "visible" && !connected) {
            await refreshTicket(ticketId);
          }
          delayMs = 5000;
          tick();
        })();
      }, delayMs);
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible" && !connected) {
        void refreshTicket(ticketId);
      }
    };

    tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [connected, refreshTicket, ticketId]);

  // Drafts are isolated per ticket so switching between conversations never
  // loses text that has not been sent yet.
  useEffect(() => {
    if (!ticketId || typeof window === "undefined") return;
    try {
      const drafts = JSON.parse(sessionStorage.getItem("live-support-drafts") || "{}");
      setInput(typeof drafts[ticketId] === "string" ? drafts[ticketId] : "");
    } catch {
      setInput("");
    }
  }, [ticketId]);

  useEffect(() => {
    if (!ticketId || typeof window === "undefined") return;
    try {
      const drafts = JSON.parse(sessionStorage.getItem("live-support-drafts") || "{}");
      if (input.trim()) drafts[ticketId] = input;
      else delete drafts[ticketId];
      sessionStorage.setItem("live-support-drafts", JSON.stringify(drafts));
    } catch {
      // Draft persistence is best effort.
    }
  }, [input, ticketId]);

  useEffect(() => {
    if (!ticketId || !role || role === "admin") return;
    let socket: WebSocket | null = null;
    let cancelled = false;
    void supportWebSocketUrl(ticketId)
      .then((url) => {
        if (cancelled) return;
        socket = new WebSocket(url);
        socketRef.current = socket;
        socket.onopen = () => setConnected(true);
        socket.onclose = () => setConnected(false);
        socket.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === "typing" && data.sender_id !== currentUser)
              setTyping(Boolean(data.is_typing));
            if (
              data.type === "message.created" ||
              data.type?.startsWith("ticket.")
            ) {
              void refreshTicket(ticketId);
              void refreshList();
            }
          } catch {
            // Polling remains available if a WebSocket event cannot be read.
          }
        };
      })
      .catch(() => {
        // Queued tickets intentionally use polling until an officer is assigned.
        setConnected(false);
      });
    return () => {
      cancelled = true;
      setConnected(false);
      socket?.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [currentUser, refreshList, refreshTicket, role, ticketId]);

  const createTicket = async (event: FormEvent) => {
    event.preventDefault();
    if (question.trim().length < 5) {
      toast.error("Nội dung yêu cầu cần ít nhất 5 ký tự.");
      return;
    }
    try {
      const response = await apiClient.post<Ticket>("/support/tickets", {
        question,
        organization_unit_id: unitId,
        routing_revision: routingRevision,
      });
      setTicketId(response.data.id);
      setActive(response.data);
      setQuestion("");
      toast.success("Đã gửi yêu cầu đến hàng chờ cán bộ phụ trách.");
      void refreshList();
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, "Không thể tạo yêu cầu hỗ trợ."));
    }
  };

  const claim = async (ticketIdOverride?: string) => {
    const id = ticketIdOverride || ticketId;
    try {
      const claimed = await apiClient.post("/support/officer/claim-next");
      const allocation = claimed.data?.assignment;
      if (!allocation) {
        toast.info(
          "Hiện không có yêu cầu phù hợp hoặc bạn đã đủ số phiên đang xử lý.",
        );
        return;
      }
      await apiClient.post(
        `/support/officer/assignments/${allocation.id}/activate`,
        {
          lease_token: claimed.data.lease_token,
        },
      );
      setTicketId(allocation.ticket_id);
      await refreshTicket(allocation.ticket_id);
      void refreshList();
      void refreshOfficerData();
    } catch (error: unknown) {
      const status = (error as { response?: { status?: number } })?.response
        ?.status;
      if (status === 409 && id) {
        try {
          setTicketId(id);
          await apiClient.post(`/support/tickets/${id}/claim`, {});
          await refreshTicket(id);
          void refreshList();
          void refreshOfficerData();
          return;
        } catch (legacyError: unknown) {
          toast.error(
            apiErrorDetail(legacyError, "Không thể tiếp nhận phiên."),
          );
          return;
        }
      }
      toast.error(apiErrorDetail(error, "Không thể tiếp nhận phiên."));
    }
  };

  const closeTicket = async () => {
    if (!ticketId) return;
    if (role === "officer" && resolutionNote.trim().length < 10) {
      toast.error("Vui lòng nhập tóm tắt kết quả xử lý (ít nhất 10 ký tự).");
      return;
    }
    try {
      if (role === "officer") {
        try {
          await apiClient.post(`/support/tickets/${ticketId}/resolve`, {
            resolution_note: resolutionNote.trim(),
          });
        } catch (error: unknown) {
          const status = (error as { response?: { status?: number } })?.response
            ?.status;
          if (status !== 409) throw error;
          await apiClient.patch(`/support/tickets/${ticketId}/close`, {
            resolution_note: resolutionNote.trim(),
          });
        }
      } else {
        await apiClient.patch(`/support/tickets/${ticketId}/close`, {
          resolution_note: resolutionNote.trim(),
        });
      }
      await refreshTicket(ticketId);
      void refreshList();
      void refreshOfficerData();
      setResolveOpen(false);
      setResolutionNote("");
    } catch {
      toast.error("Không thể đóng phiên.");
    }
  };

  const transfer = async () => {
    if (!ticketId || !transferDomain) return;
    if (transferReason.trim().length < 10) {
      toast.error("Vui lòng nhập lý do chuyển (ít nhất 10 ký tự).");
      return;
    }
    try {
      await apiClient.post(`/support/tickets/${ticketId}/decline`, {
        transfer_domain: transferDomain,
        reason: transferReason.trim(),
        expected_version: active?.version,
      });
      setActive(null);
      setTicketId(null);
      void refreshList();
      void refreshOfficerData();
      setTransferOpen(false);
      setTransferReason("");
      toast.success("Đã chuyển phiên đến phòng ban chủ trì của lĩnh vực đã chọn.");
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, "Không thể chuyển phiên."));
    }
  };

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!ticketId || !input.trim()) return;
    try {
      await apiClient.post(`/support/tickets/${ticketId}/messages`, {
        content: input.trim(),
        attachment_ids: pendingAttachments.map((item) => item.id),
      });
      setInput("");
      try {
        const drafts = JSON.parse(sessionStorage.getItem("live-support-drafts") || "{}");
        delete drafts[ticketId];
        sessionStorage.setItem("live-support-drafts", JSON.stringify(drafts));
      } catch {
        // no-op
      }
      setPendingAttachments([]);
      socketRef.current?.send(
        JSON.stringify({ type: "typing", is_typing: false }),
      );
      await refreshTicket(ticketId);
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, "Không thể gửi tin nhắn."));
    }
  };

  const downloadAttachment = async (file: Attachment) => {
    try {
      const apiUrl = await getApiUrl();
      const token = String(authState().token || "");
      const response = await fetch(`${apiUrl}${file.download_url}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.detail || "Không thể tải tệp đính kèm.");
      }
      const href = URL.createObjectURL(await response.blob());
      const anchor = window.document.createElement("a");
      anchor.href = href;
      anchor.download = file.name || "tep-dinh-kem";
      anchor.click();
      URL.revokeObjectURL(href);
    } catch (error: unknown) {
      toast.error(formatApiError(error, "Không thể tải tệp đính kèm."));
    }
  };

  const uploadAttachment = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !ticketId) return;
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await apiClient.post<Attachment>(
        `/support/tickets/${ticketId}/attachments`,
        body,
      );
      setPendingAttachments((old) => [...old, response.data]);
      toast.success("Đã đính kèm tệp.");
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, "Không thể tải tệp."));
    } finally {
      event.target.value = "";
    }
  };

  const submitRating = async () => {
    if (!ticketId || !rating) return;
    try {
      await apiClient.post(`/support/tickets/${ticketId}/rating`, {
        rating,
        feedback: "",
      });
      await refreshTicket(ticketId);
      toast.success("Cảm ơn bạn đã đánh giá.");
    } catch (error: unknown) {
      toast.error(apiErrorDetail(error, "Chưa thể ghi nhận đánh giá."));
    }
  };

  const listForTab = role === "officer"
    ? (supportTab === "queue" ? queueTickets : supportTab === "completed" ? tickets.filter((item) => ["resolved", "closed"].includes(item.status)) : tickets)
    : tickets;
  const displayedTickets = listForTab.filter((item) => {
    const query = ticketSearch.trim().toLocaleLowerCase("vi-VN");
    const matchesQuery = !query || `${item.id} ${item.question || ""} ${item.domain} ${DOMAINS[item.domain] || ""}`.toLocaleLowerCase("vi-VN").includes(query);
    const matchesPriority = priorityFilter === "all" || item.priority === priorityFilter;
    const matchesUnread = !unreadOnly || Boolean(item.unread_count || item.needs_attention);
    return matchesQuery && matchesPriority && matchesUnread;
  });

  if (role === "admin") {
    return (
      <AppShell>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-12 md:p-6 md:pb-16">
          <main className="mx-auto max-w-xl space-y-4">
            <Card>
              <CardHeader>
                <h1 className="text-lg font-semibold leading-none tracking-tight">
                  Hỗ trợ trực tuyến dành cho người dân và cán bộ
                </h1>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-muted-foreground">
                <p>
                  Quản trị viên không tham gia nhắn tin trong các phiên hỗ trợ. Người dân
                  được kết nối trực tiếp với cán bộ phụ trách các lĩnh vực chuyên môn.
                </p>
              </CardContent>
            </Card>
          </main>
        </div>
      </AppShell>
    );
  }

  const requestForm = (
        <Card className="mx-auto w-full max-w-2xl shadow-none">
          <CardHeader>
            <h1 className="flex items-center gap-2 text-lg font-semibold leading-none tracking-tight">
              <MessageSquare className="h-5 w-5" />
              Hỗ trợ trực tuyến với cán bộ
            </h1>
            <p className="text-sm text-muted-foreground">
              Đây là kênh trao đổi với cán bộ phụ trách, không phải chatbot. Hãy
              chọn phòng ban và mô tả rõ thắc mắc.
            </p>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={createTicket}>
              <label className="block text-sm font-medium" htmlFor="support-department">Phòng ban tiếp nhận</label>
              <Select value={unitId} onValueChange={setUnitId}>
                <SelectTrigger id="support-department" className="h-auto min-h-11 w-full whitespace-normal text-left">
                  <SelectValue placeholder="Chọn phòng ban" />
                </SelectTrigger>
                <SelectContent>
                  {routingUnits.map((unit) => (
                    <SelectItem key={unit.unit_id} value={unit.unit_id}>
                      {unit.unit_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <label className="block text-sm font-medium" htmlFor="support-question">Nội dung cần hỗ trợ</label>
              <Textarea
                id="support-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                rows={5}
                className="min-h-32"
                placeholder="Mô tả nội dung cần cán bộ hỗ trợ..."
              />
              {routingRevision !== undefined && !departmentRoutingEnabled && <p role="status" className="rounded-lg border p-3 text-sm text-muted-foreground">Kênh hỗ trợ đang chờ quản trị viên hoàn tất phân công cán bộ theo phòng ban. Anh/chị vẫn có thể tiếp tục hỏi đáp và xem thủ tục.</p>}
              <Button type="submit" disabled={!unitId || !departmentRoutingEnabled}>
                <Send className="mr-2 h-4 w-4" />
                Gửi yêu cầu hỗ trợ
              </Button>
            </form>
          </CardContent>
        </Card>
  );

  return (
    <AppShell>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center justify-between gap-2 border-b bg-card p-3 lg:hidden">
          <span className="text-sm font-semibold">Hỗ trợ trực tuyến</span>
          <Button variant="outline" size="sm" aria-expanded={mobileListOpen} onClick={() => setMobileListOpen(value => !value)}>
            {mobileListOpen ? "Ẩn danh sách" : "Danh sách phiên"}
          </Button>
        </div>
        <div className="relative flex min-h-0 w-full flex-1 overflow-hidden bg-background">
      <aside aria-label="Danh sách phiên hỗ trợ" className={`${mobileListOpen ? "flex" : "hidden"} min-h-0 w-full shrink-0 flex-col overflow-y-auto border-r bg-card p-4 lg:flex lg:w-[280px]`}>

        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <h1 className="font-semibold">{role === "officer" ? "Bàn hỗ trợ trực tuyến" : "Phiên hỗ trợ"}</h1>
            {role === "officer" && (
              <p className="text-xs text-muted-foreground">
                Đang xử lý {overview.active_count}/{overview.max_capacity} · {overview.presence_status}
              </p>
            )}
          </div>
          {connected ? (
            <Wifi className="h-4 w-4 text-emerald-600" />
          ) : (
            <WifiOff className="h-4 w-4 text-amber-600" />
          )}
        </div>
        {role === "citizen" && <Button className="mb-4 w-full shrink-0" onClick={() => { setTicketId(null); setActive(null); setMobileListOpen(false); }}>Tạo yêu cầu hỗ trợ</Button>}
        {role === "citizen" && tickets.length === 0 && <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">Chưa có phiên hỗ trợ. Tạo yêu cầu để trao đổi với cán bộ phụ trách.</p>}
        {role === "officer" && (
          <>
            <div className="mb-3 grid grid-cols-3 gap-1 text-[11px]">
              {([
                ["active", `Đang xử lý (${tickets.length})`],
                ["queue", `Hàng chờ (${overview.queue_count})`],
                ["completed", "Đã hoàn tất"],
              ] as const).map(([value, label]) => (
                <button key={value} type="button" className={`rounded px-2 py-1 ${supportTab === value ? "bg-primary text-primary-foreground" : "bg-muted"}`} onClick={() => setSupportTab(value)}>{label}</button>
              ))}
            </div>
            <div className="mb-3 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded border p-2"><b>{overview.needs_attention_count}</b><span className="ml-1 text-muted-foreground">Chưa đọc</span></div>
              <div className="rounded border p-2"><b>{Math.floor(overview.oldest_wait_seconds / 60)}p</b><span className="ml-1 text-muted-foreground">Chờ lâu nhất</span></div>
            </div>
            {supportTab === "queue" && <Button className="mb-3 w-full" onClick={() => void claim()} disabled={overview.active_count >= overview.max_capacity}><UserCheck className="mr-2 h-4 w-4" />Nhận yêu cầu tiếp theo</Button>}
            <div className="mb-3 space-y-2">
              <Input value={ticketSearch} onChange={(event) => setTicketSearch(event.target.value)} placeholder={supportTab === "queue" ? "Tìm mã phiên hoặc lĩnh vực..." : "Tìm trong phiên hỗ trợ..."} className="h-8 text-xs" />
              {supportTab === "queue" && <p className="text-xs text-muted-foreground">Nội dung yêu cầu được hiển thị sau khi tiếp nhận phiên.</p>}
              <div className="flex gap-2">
                <Select value={priorityFilter} onValueChange={setPriorityFilter}>
                  <SelectTrigger className="h-8 flex-1 text-xs"><SelectValue placeholder="Ưu tiên" /></SelectTrigger>
                  <SelectContent><SelectItem value="all">Mọi ưu tiên</SelectItem><SelectItem value="urgent">Khẩn cấp</SelectItem><SelectItem value="high">Cao</SelectItem><SelectItem value="normal">Thường</SelectItem><SelectItem value="low">Thấp</SelectItem></SelectContent>
                </Select>
                <Button type="button" size="sm" variant={unreadOnly ? "default" : "outline"} className="h-8 text-xs" onClick={() => setUnreadOnly((value) => !value)}>Chưa đọc</Button>
              </div>
            </div>
          </>
        )}
        {displayedTickets.map((ticket) => (
          <div
            key={ticket.id}
            className={`mb-2 shrink-0 rounded-lg border p-3 ${ticketId === ticket.id ? "border-primary bg-primary/5" : ""}`}
          >
            <button
              onClick={() => { setTicketId(ticket.id); setMobileListOpen(false); }}
              aria-pressed={ticketId === ticket.id}
              className="min-h-11 w-full text-left hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring"
            >
              <div className="flex justify-between gap-2">
                <Badge>{statusLabel(ticket.status)}</Badge>
                {ticket.unread_count ? (
                  <Badge variant="destructive">{ticket.unread_count}</Badge>
                ) : null}
              </div>
              <p className="mt-2 line-clamp-2 text-sm">{ticket.question}</p>
              <p className="mt-1 break-all text-xs text-muted-foreground">Mã phiên: {ticket.id}</p>
              <small className="text-muted-foreground">
                {routingUnits.find(unit => unit.unit_id === ticket.primary_organization_unit_id)?.unit_name || DOMAINS[ticket.domain] || ticket.domain}
              </small>
            </button>
          </div>
        ))}
        {role === "officer" && displayedTickets.length === 0 && <div className="rounded border border-dashed p-4 text-center text-xs text-muted-foreground">{listForTab.length === 0 ? (supportTab === "queue" ? "Chưa có yêu cầu trong lĩnh vực của bạn." : "Chưa có phiên được phân công.") : "Không có phiên khớp bộ lọc."}<div className="mt-2">{supportTab === "queue" && listForTab.length === 0 && <Button size="sm" variant="outline" onClick={() => void claim()}>Nhận yêu cầu tiếp theo</Button>}</div></div>}
      </aside>
      <section aria-label="Nội dung phiên hỗ trợ" className={`${mobileListOpen ? "hidden" : "flex"} min-h-0 min-w-0 flex-1 flex-col lg:flex`}>
        {role === "citizen" && !ticketId ? <div className="min-h-0 flex-1 overflow-y-auto p-4 lg:p-8">{requestForm}</div> : !active ? (
          <div className="m-auto text-center text-muted-foreground">
            <AlertCircle className="mx-auto mb-2 h-9 w-9" />
            Chọn một phiên hỗ trợ để bắt đầu.
          </div>
        ) : (
          <>
            <header className="flex max-h-[35dvh] shrink-0 flex-wrap items-center justify-between gap-3 overflow-y-auto border-b bg-card p-4">
              <div>
                <b>{DOMAINS[active.domain] || active.domain}</b>
                <p className="text-sm text-muted-foreground">
                  {statusLabel(active.status)} ·{" "}
                  {connected
                    ? "Kết nối thời gian thực"
                    : "Đang dùng polling dự phòng"}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {role === "officer" && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setCopilotOpen(!copilotOpen);
                      if (!copilotAnswer && active?.question) {
                        setCopilotQuery(active.question);
                        void runCopilotSearch(active.question);
                      }
                    }}
                    className="gap-1.5"
                  >
                    <Sparkles className="h-4 w-4 text-amber-500" />
                    AI Copilot Tra cứu
                  </Button>
                )}
                {role === "officer" && active.status !== "closed" && (
                  <>
                    <Select
                      value={transferDomain}
                      onValueChange={setTransferDomain}
                    >
                      <SelectTrigger className="w-44">
                        <SelectValue placeholder="Chuyển lĩnh vực" />
                      </SelectTrigger>
                      <SelectContent>
                        {STAFFED_DOMAIN_KEYS.map((key) => (
                          <SelectItem key={key} value={key}>
                            {DOMAINS[key]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      variant="outline"
                      disabled={!transferDomain}
                      onClick={() => setTransferOpen(true)}
                    >
                      Chuyển phiên
                    </Button>
                  </>
                )}
                {role === "officer" &&
                  !["resolved", "closed", "cancelled", "expired"].includes(
                    active.status,
                  ) && (
                  <Button variant="outline" onClick={() => setResolveOpen(true)}>
                    <Check className="mr-2 h-4 w-4" />
                    Đánh dấu đã xử lý
                  </Button>
                )}
              </div>
            </header>
            {transferOpen && role === "officer" && (
              <div className="border-b bg-amber-50 p-4" role="dialog" aria-label="Xác nhận chuyển phiên">
                <p className="font-semibold">Chuyển phiên sang {DOMAINS[transferDomain] || transferDomain}</p>
                <Textarea className="mt-2" value={transferReason} onChange={(event) => setTransferReason(event.target.value)} placeholder="Lý do chuyển (bắt buộc, tối thiểu 10 ký tự)" rows={3} />
                <div className="mt-2 flex gap-2"><Button size="sm" onClick={() => void transfer()}>Xác nhận chuyển</Button><Button size="sm" variant="ghost" onClick={() => setTransferOpen(false)}>Hủy</Button></div>
              </div>
            )}
            {resolveOpen && role === "officer" && (
              <div className="border-b bg-emerald-50 p-4" role="dialog" aria-label="Hoàn tất phiên">
                <p className="font-semibold">Tóm tắt kết quả xử lý</p>
                <Textarea className="mt-2" value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} placeholder="Ghi rõ nội dung đã hướng dẫn hoặc kết quả xử lý (tối thiểu 10 ký tự)" rows={3} />
                <div className="mt-2 flex gap-2"><Button size="sm" onClick={() => void closeTicket()}>Hoàn tất phiên</Button><Button size="sm" variant="ghost" onClick={() => setResolveOpen(false)}>Hủy</Button></div>
              </div>
            )}
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              <section className="space-y-3 rounded-lg border bg-muted/20 p-4">
                <div>
                  <h2 className="font-semibold">Nội dung yêu cầu</h2>
                  <p className="mt-1 whitespace-pre-wrap break-words text-sm">
                    {active.messages?.find((message) => message.sender_role === "citizen")?.content || active.question || "Chưa có nội dung câu hỏi."}
                  </p>
                </div>
                {active.ai_summary && (
                  <div className="border-t pt-3">
                    <h2 className="font-semibold">Thông tin AI đã cung cấp</h2>
                    <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{active.ai_summary}</p>
                  </div>
                )}
                {active.notice && <p className="text-xs text-muted-foreground">{active.notice}</p>}
              </section>
              {active.messages?.map((message) => (
                <article
                  key={message.id}
                  className={`max-w-[78%] rounded-lg border p-3 ${message.sender_id === currentUser ? "ml-auto bg-primary text-primary-foreground" : "bg-card"}`}
                >
                  <div className="mb-1 text-xs opacity-75">
                    {roleLabel(message.sender_role)} ·{" "}
                    {new Date(message.created_at).toLocaleTimeString("vi-VN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </div>
                  <LegacySupportContent content={message.content} />
                  {message.attachments?.map((file) => (
                    <button
                      type="button"
                      className="mt-2 block text-left text-xs underline"
                      key={file.id}
                      onClick={() => void downloadAttachment(file)}
                    >
                      ↳ {file.name}
                    </button>
                  ))}
                </article>
              ))}
              {typing && (
                <p className="text-xs text-muted-foreground">
                  Cán bộ/Người dân đang nhập...
                </p>
              )}
            </div>
            {active.status === "closed" && role === "citizen" ? (
              <div className="border-t p-4">
                <p className="mb-2 text-sm">Đánh giá hỗ trợ của cán bộ:</p>
                <div className="flex gap-1">
                  {[1, 2, 3, 4, 5].map((value) => (
                    <button
                      type="button"
                      onClick={() => setRating(value)}
                      key={value}
                    >
                      <Star
                        className={`h-6 w-6 ${value <= rating ? "fill-amber-400 text-amber-400" : "text-muted-foreground"}`}
                      />
                    </button>
                  ))}
                  <Button size="sm" disabled={!rating} onClick={submitRating}>
                    Gửi
                  </Button>
                </div>
              </div>
            ) : active.status !== "closed" ? (
              <form onSubmit={sendMessage} className="shrink-0 border-t bg-card p-3">
                <div className="mb-2 flex flex-wrap gap-2">
                  {pendingAttachments.map((file) => (
                    <Badge key={file.id}>{file.name}</Badge>
                  ))}
                  <label className="cursor-pointer">
                    <FileUp className="h-5 w-5" />
                    <input
                      className="hidden"
                      type="file"
                      aria-label="Đính kèm tệp hỗ trợ"
                      onChange={uploadAttachment}
                    />
                  </label>
                </div>
                <div className="flex gap-2">
                  <Input
                    value={input}
                    onChange={(event) => {
                      setInput(event.target.value);
                      socketRef.current?.send(
                        JSON.stringify({ type: "typing", is_typing: true }),
                      );
                    }}
                    placeholder="Nhập tin nhắn..."
                  />
                  <Button type="submit" aria-label="Gửi tin nhắn">
                    <Send className="h-4 w-4" />
                  </Button>
                </div>
              </form>
            ) : null}
          </>
        )}
      </section>
      {role === "officer" && copilotOpen && (
        <aside aria-label="Trợ lý tra cứu cho cán bộ" className="absolute inset-y-0 right-0 z-20 flex w-80 max-w-full shrink-0 flex-col space-y-3 overflow-y-auto border-l bg-card p-4 shadow-xl 2xl:static 2xl:shadow-none">
          <div className="flex items-center justify-between border-b pb-2">
            <span className="font-semibold text-sm flex items-center gap-1.5">
              <Sparkles className="h-4 w-4 text-amber-500" />
              AI Copilot Tra cứu Pháp lý
            </span>
            <Button
              variant="ghost"
              size="sm"
              aria-label="Đóng trợ lý tra cứu"
              onClick={() => setCopilotOpen(false)}
            >
              ✕
            </Button>
          </div>

          <div className="space-y-2">
            <Textarea
              value={copilotQuery}
              onChange={(e) => setCopilotQuery(e.target.value)}
              placeholder="Nhập câu hỏi tra cứu luật..."
              rows={3}
              className="text-xs"
            />
            <Button
              size="sm"
              className="w-full gap-1.5"
              disabled={copilotLoading}
              onClick={() => void runCopilotSearch()}
            >
              <Search className="h-3.5 w-3.5" />
              {copilotLoading ? "Đang tra cứu..." : "Tra cứu căn cứ"}
            </Button>
          </div>

          {copilotAnswer && (
            <div className="flex-1 space-y-2 overflow-y-auto rounded border bg-muted/30 p-3 text-xs">
              <div className="font-medium text-primary">
                Gợi ý trả lời & Trích dẫn:
              </div>
              <p className="whitespace-pre-wrap text-muted-foreground">
                {copilotAnswer}
              </p>
              <Button
                size="sm"
                variant="outline"
                className="w-full mt-2 gap-1 text-xs"
                onClick={() => {
                  setInput((prev) =>
                    prev ? `${prev}\n\n${copilotAnswer}` : copilotAnswer,
                  );
                  toast.success("Đã chèn câu trả lời vào khung chat!");
                }}
              >
                <Check className="h-3.5 w-3.5 mr-1" />
                Chèn vào tin nhắn trả lời
              </Button>
            </div>
          )}
        </aside>
      )}
        </div>
      </div>
    </AppShell>
  );
}
