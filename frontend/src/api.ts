const PREFIX = import.meta.env.VITE_API_BASE ?? "";

const LS_AUTH = "mm_auth_token";

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(LS_AUTH);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null) {
  try {
    if (token) localStorage.setItem(LS_AUTH, token);
    else localStorage.removeItem(LS_AUTH);
  } catch {
    /* ignore */
  }
}

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

export interface AuthStatus {
  auth_required: boolean;
  bootstrap_needed: boolean;
  /** 1人目作成後に自分でアカウント登録できるか（API 未対応時は undefined で表示可） */
  self_register_allowed?: boolean;
}

export interface AuthMe {
  username: string;
  is_admin: boolean;
}

export interface AdminUserRow {
  username: string;
  is_admin: boolean;
  created_at: string | null;
}

export interface MeLLMInfo {
  openai_configured: boolean;
  openai_model: string;
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers ?? undefined);
  const t = getStoredToken();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401 && t) {
    setStoredToken(null);
    window.dispatchEvent(new Event("mm-auth-lost"));
  }
  return res;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export async function getAuthStatus(): Promise<AuthStatus> {
  const res = await fetch(`${PREFIX}/api/auth/status`);
  return handle(res);
}

export async function getMeLlm(): Promise<MeLLMInfo> {
  const res = await apiFetch(`${PREFIX}/api/me/llm`);
  return handle(res);
}

export async function patchMeLlm(body: { openai_api_key?: string; openai_model?: string }): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/me/llm`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle(res);
}

export async function loginRequest(username: string, password: string): Promise<{ access_token: string }> {
  const res = await fetch(`${PREFIX}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return handle(res);
}

export async function registerRequest(username: string, password: string): Promise<{ access_token: string }> {
  const res = await fetch(`${PREFIX}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return handle(res);
}

export async function bootstrapRequest(username: string, password: string): Promise<{ access_token: string }> {
  const res = await fetch(`${PREFIX}/api/auth/bootstrap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return handle(res);
}

export async function getAuthMe(): Promise<AuthMe> {
  const res = await apiFetch(`${PREFIX}/api/auth/me`);
  return handle(res);
}

export async function adminListUsers(): Promise<AdminUserRow[]> {
  const res = await apiFetch(`${PREFIX}/api/admin/users`);
  return handle(res);
}

export async function adminCreateUser(body: {
  username: string;
  password: string;
  is_admin: boolean;
}): Promise<AdminUserRow> {
  const res = await apiFetch(`${PREFIX}/api/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle(res);
}

export async function adminResetPassword(username: string, newPassword: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/admin/users/${encodeURIComponent(username)}/password`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_password: newPassword }),
  });
  return handle(res);
}

export async function adminSetRole(username: string, isAdmin: boolean): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/admin/users/${encodeURIComponent(username)}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_admin: isAdmin }),
  });
  return handle(res);
}

export async function adminDeleteUser(username: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" });
  return handle(res);
}

export async function getVersion(): Promise<{ version: string }> {
  const res = await fetch(`${PREFIX}/api/version`);
  return handle(res);
}

export async function getPresets(): Promise<Record<string, { label: string }>> {
  const res = await apiFetch(`${PREFIX}/api/presets`);
  return handle(res);
}

export async function createTask(fd: FormData): Promise<{ task_id: string; filename: string }> {
  const res = await apiFetch(`${PREFIX}/api/tasks`, { method: "POST", body: fd });
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
  const res = await apiFetch(`${PREFIX}/api/records?${q.toString()}`);
  return handle(res);
}

export async function getQueue(): Promise<RecordRow[]> {
  const res = await apiFetch(`${PREFIX}/api/queue`);
  return handle(res);
}

export async function getRecord(id: string): Promise<RecordRow> {
  const res = await apiFetch(`${PREFIX}/api/records/${encodeURIComponent(id)}`);
  return handle(res);
}

export async function patchSummary(id: string, summary: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/records/${encodeURIComponent(id)}/summary`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ summary }),
  });
  return handle(res);
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function downloadExportMinutes(recordId: string, filename: string): Promise<void> {
  const res = await apiFetch(`${PREFIX}/api/records/${encodeURIComponent(recordId)}/export/minutes`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, filename);
}

export async function downloadExportTranscript(recordId: string, filename: string): Promise<void> {
  const res = await apiFetch(`${PREFIX}/api/records/${encodeURIComponent(recordId)}/export/transcript`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, filename);
}
