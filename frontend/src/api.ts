const PREFIX = import.meta.env.VITE_API_BASE ?? "";

export interface MeetingContext {
  purpose: string;
  participants: string;
  glossary: string;
  tone: string;
  action_rules: string;
}

export interface TaskSubmitMetadata {
  email: string;
  webhook_url: string | null;
  notification_type: "browser" | "webhook" | "none";
  llm_provider: "ollama" | "openai";
  ollama_model: string;
  openai_api_key: string | null;
  openai_model: string;
  topic: string;
  meeting_date: string;
  category: string;
  tags: string;
  preset_id: string;
  context: MeetingContext;
}

export interface RecordRow {
  id: string;
  email: string;
  filename: string;
  status: string;
  transcript: string | null;
  summary: string | null;
  created_at: string;
  topic: string | null;
  tags: string | null;
  category: string | null;
  meeting_date: string | null;
  preset_id: string | null;
  context_json: string | null;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export async function getVersion(): Promise<{ version: string }> {
  const res = await fetch(`${PREFIX}/api/version`);
  return handle(res);
}

export async function getPresets(): Promise<Record<string, { label: string }>> {
  const res = await fetch(`${PREFIX}/api/presets`);
  return handle(res);
}

export async function createTask(fd: FormData): Promise<{ task_id: string; filename: string }> {
  const res = await fetch(`${PREFIX}/api/tasks`, { method: "POST", body: fd });
  return handle(res);
}

export async function listRecords(params: {
  days?: number;
  search?: string;
  category?: string;
  status_filter?: string;
}): Promise<RecordRow[]> {
  const q = new URLSearchParams();
  if (params.days != null) q.set("days", String(params.days));
  if (params.search) q.set("search", params.search);
  if (params.category) q.set("category", params.category);
  if (params.status_filter) q.set("status_filter", params.status_filter);
  const res = await fetch(`${PREFIX}/api/records?${q.toString()}`);
  return handle(res);
}

export async function getQueue(): Promise<RecordRow[]> {
  const res = await fetch(`${PREFIX}/api/queue`);
  return handle(res);
}

export async function getRecord(id: string): Promise<RecordRow> {
  const res = await fetch(`${PREFIX}/api/records/${id}`);
  return handle(res);
}

export async function patchSummary(id: string, summary: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${PREFIX}/api/records/${id}/summary`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ summary }),
  });
  return handle(res);
}

/** 長大テキストでも data URL 制限にかからないダウンロード用（GET で本文を返す） */
export function exportMinutesUrl(id: string): string {
  return `${PREFIX}/api/records/${encodeURIComponent(id)}/export/minutes`;
}

export function exportTranscriptUrl(id: string): string {
  return `${PREFIX}/api/records/${encodeURIComponent(id)}/export/transcript`;
}
