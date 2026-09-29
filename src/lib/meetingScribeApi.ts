// T3AI DEFTER backend API istemcisi. Backend'in tam sözleşmesi için
// meeting_scribe/server/app.py dosyasına bakılabilir.

const API_BASE_URL = (import.meta.env["VITE_MEETING_SCRIBE_API_URL"] ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const body = await response.json();
      detail = body?.detail ?? detail;
    } catch {
      // JSON olmayan hata gövdesi — statusText ile devam edilir.
    }
    // FastAPI doğrulama hataları (422) detail'i dizi/obje olarak döner;
    // Error mesajı her zaman okunabilir bir metin olsun diye normalize edilir.
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => (typeof item === "string" ? item : item?.msg ?? JSON.stringify(item))).join(", ")
          : JSON.stringify(detail);
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type MeetingStatus =
  | "created"
  | "recording"
  | "recorded"
  | "refining"
  | "refined"
  | "summarizing"
  | "summary_pending"
  | "summary_failed"
  | "summarized"
  | "finished"
  | "abandoned_cleaned"
  | "refine_failed"
  | "recording_failed";

export interface SummaryContent {
  summary_md: string;
  discussed_topics: string[];
  decisions: string[];
  actions: Array<{ task: string; owner: string; deadline: string }>;
  open_questions: string[];
}

export interface MeetingDetail {
  id: number;
  status: MeetingStatus;
  title: string | null;
  date: string;
  duration_sec: number | null;
  updated_at: string;
  language: MeetingLanguage;
  participants: { id: number; label: string }[];
  segments: unknown[];
  summary: (SummaryContent & { meeting_id: number }) | null;
}

export type MeetingLanguage = "mixed" | "tr" | "en" | "de" | "fr";

export function createMeeting(title: string, language: MeetingLanguage = "mixed") {
  return apiFetch<{ meeting_id: number }>("/meetings", {
    method: "POST",
    body: JSON.stringify({ title: title || null, language }),
  });
}

export function startRecording(meetingId: number) {
  return apiFetch<{ status: MeetingStatus }>(`/meetings/${meetingId}/recording/start`, { method: "POST" });
}

export function stopRecording(meetingId: number) {
  return apiFetch<{ status: MeetingStatus; offline: string }>(`/meetings/${meetingId}/recording/stop`, {
    method: "POST",
  });
}

export function getMeeting(meetingId: number) {
  return apiFetch<MeetingDetail>(`/meetings/${meetingId}`);
}

export function getTranscript(meetingId: number) {
  return apiFetch<{ transcript: string }>(`/meetings/${meetingId}/transcript`);
}

export function postSummary(meetingId: number) {
  return apiFetch<SummaryContent>(`/meetings/${meetingId}/summary`, { method: "POST" });
}

export function finishMeeting(meetingId: number) {
  return apiFetch<{ status: MeetingStatus }>(`/meetings/${meetingId}/finish`, { method: "POST" });
}

export function requestWebSocketToken() {
  return apiFetch<{ token: string; expires_in_seconds: number }>("/auth/websocket-token", { method: "POST" });
}

export function getWebSocketUrl(meetingId: number, token: string): string {
  const base = API_BASE_URL || window.location.origin;
  const wsBase = base.replace(/^http/, "ws");
  return `${wsBase}/meetings/${meetingId}/live/ws?ws_token=${encodeURIComponent(token)}`;
}
