/** サブパス配信時は /meetingminutesnotebook のように末尾スラッシュなし */
const PREFIX = String(import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

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
  notification_type: "browser" | "webhook" | "email" | "none";
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
  /** true のとき書き起こしまで（Whisper または .txt/.srt）。議事録用 LLM は使わない */
  transcript_only?: boolean;
  /** 動画・音声の Whisper 文字起こしの探索の強さ（既定 accurate） */
  whisper_preset?: "fast" | "balanced" | "accurate";
}

export interface RecordRow {
  id: string;
  email: string;
  filename: string;
  status: string;
  transcript: string | null;
  summary: string | null;
  created_at: string;
  /** ワーカーが pending から初めて処理を進めた時刻（DB マイグレーション前は無し） */
  processing_started_at?: string | null;
  /** 完了またはエラー終了時刻 */
  processing_finished_at?: string | null;
  topic: string | null;
  tags: string | null;
  category: string | null;
  meeting_date: string | null;
  preset_id: string | null;
  context_json: string | null;
  /** 1 のとき書き起こしのみジョブ（議事録 LLM なし） */
  transcript_only?: number | boolean;
  /** /api/queue のみ。transcript があり、かつ Whisper 実行中（processing:transcribing）でない */
  transcript_ready?: boolean;
  /** 認証あり: ジョブ所有者のログイン ID（メール）。レガシー共有 DB は null */
  job_owner?: string | null;
  /** 認証あり: 現在のユーザーが投入したジョブか（他人のジョブは破棄・DL 不可） */
  is_mine?: boolean;
}

export interface AuthStatus {
  auth_required: boolean;
  bootstrap_needed: boolean;
  /** 1人目作成後に自分でアカウント登録できるか（API 未対応時は undefined で表示可） */
  self_register_allowed?: boolean;
  /** MM_EMAIL_NOTIFY_ENABLED がオンのとき true（メール通知を UI に出す） */
  email_notify_feature_enabled?: boolean;
  /** 上記がオンかつ SMTP 設定済みのとき true（メールを送れる） */
  email_notify_available?: boolean;
  /** MM_OPENAI_ENABLED がオフのとき false（未対応 API では undefined = 従来どおり表示） */
  openai_enabled?: boolean;
  /** SMTP 済みかつ管理者宛先あり（または MM_ERROR_REPORT_TO）のとき true */
  error_report_available?: boolean;
  /** MM_MINUTES_RETENTION_DAYS（既定 90≒3か月）。0 以下で自動削除なし */
  minutes_retention_days?: number;
}

export interface AuthMe {
  email: string;
  is_admin: boolean;
}

export interface AdminUserRow {
  email: string;
  is_admin: boolean;
  created_at: string | null;
}

export interface MeLLMInfo {
  openai_configured: boolean;
  openai_model: string;
  /** false のとき OpenAI 設定 API は利用不可 */
  openai_feature_enabled?: boolean;
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

/** FastAPI の { "detail": ... } を人が読める文字列に。 */
function parseFastApiDetail(body: string): string | null {
  try {
    const j = JSON.parse(body) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      const parts = d.map((x) => {
        if (x && typeof x === "object" && "msg" in x) return String((x as { msg: string }).msg);
        return JSON.stringify(x);
      });
      return parts.join("\n");
    }
    if (d != null && typeof d === "object") return JSON.stringify(d);
  } catch {
    /* ignore */
  }
  return null;
}

function looksLikeHtml(body: string): boolean {
  const s = body.slice(0, 200).trimStart().toLowerCase();
  return s.startsWith("<!doctype") || s.startsWith("<html");
}

/** 502 時の nginx 本文などをそのまま画面に出さない */
function formatHttpError(res: Response, body: string): string {
  const ct = (res.headers.get("content-type") || "").toLowerCase();
  if (ct.includes("application/json")) {
    const parsed = parseFastApiDetail(body);
    if (parsed) return parsed;
  } else {
    const parsed = parseFastApiDetail(body);
    if (parsed) return parsed;
  }

  if (looksLikeHtml(body)) {
    if (res.status === 502) {
      return "API サーバに接続できませんでした（502 Bad Gateway）。Docker では api コンテナが起動しているか、ログを確認してください。";
    }
    if (res.status === 503) {
      return "サービスが一時的に利用できません（503）。";
    }
    if (res.status === 504) {
      return "ゲートウェイがタイムアウトしました（504）。アップロードが大きい場合は時間をおいて再試行してください。";
    }
    return `サーバから HTML エラーが返りました（HTTP ${res.status}）。管理者に連絡してください。`;
  }

  const t = body.trim();
  if (t.length > 400) return `${t.slice(0, 400)}…`;
  return t || res.statusText || `HTTP ${res.status}`;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(formatHttpError(res, text));
  }
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown, method: "POST" | "PATCH" = "POST"): Promise<T> {
  const res = await apiFetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle(res);
}

function parseXhrHeaders(raw: string): Headers {
  const h = new Headers();
  for (const line of raw.split(/\r?\n/)) {
    const idx = line.indexOf(":");
    if (idx <= 0) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1).trim();
    if (k) h.append(k, v);
  }
  return h;
}

async function postFormDataWithUploadProgress(
  path: string,
  fd: FormData,
  onUploadProgress?: (loaded: number, total: number | null, percent: number) => void,
): Promise<Response> {
  return new Promise<Response>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const t = getStoredToken();
    xhr.open("POST", path, true);
    if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
    if (onUploadProgress) {
      xhr.upload.onprogress = (ev) => {
        const loaded = Number(ev.loaded) || 0;
        const total = ev.lengthComputable && Number.isFinite(ev.total) ? Number(ev.total) : null;
        const percent = total && total > 0 ? Math.min(100, Math.max(0, Math.floor((loaded / total) * 100))) : 0;
        onUploadProgress(loaded, total, percent);
      };
    }
    xhr.onerror = () => reject(new Error("アップロード中にネットワークエラーが発生しました。"));
    xhr.onabort = () => reject(new Error("アップロードが中断されました。"));
    xhr.onload = () => {
      if (xhr.status === 401 && t) {
        setStoredToken(null);
        window.dispatchEvent(new Event("mm-auth-lost"));
      }
      if (!(xhr.status >= 100 && xhr.status <= 599)) {
        reject(new Error("アップロードの応答を取得できませんでした。"));
        return;
      }
      const headers = parseXhrHeaders(xhr.getAllResponseHeaders() || "");
      resolve(
        new Response(xhr.responseText ?? "", {
          status: xhr.status,
          statusText: xhr.statusText || "",
          headers,
        }),
      );
    };
    xhr.send(fd);
  });
}

export async function getAuthStatus(): Promise<AuthStatus> {
  const res = await fetch(`${PREFIX}/api/auth/status`);
  return handle(res);
}

export async function submitErrorReport(body: {
  message: string;
  detail?: string;
  page_url?: string;
  client_version?: string;
}): Promise<{ ok: boolean; sent_to_count?: number }> {
  return postJson(`${PREFIX}/api/feedback/error-report`, body, "POST");
}

export async function submitSuggestionBox(body: {
  subject?: string;
  body: string;
  page_url?: string;
  client_version?: string;
}): Promise<{ ok: boolean; id: number; webhook_notified?: boolean }> {
  return postJson(`${PREFIX}/api/feedback/suggestion-box`, body, "POST");
}

/** Ollama のローカルタグ一覧（API が OLLAMA_BASE_URL の /api/tags を中継） */
export async function getOllamaModels(): Promise<string[]> {
  const res = await apiFetch(`${PREFIX}/api/ollama/models`);
  const data = await handle<{ models: string[] }>(res);
  return Array.isArray(data.models) ? data.models : [];
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

export async function loginRequest(email: string, password: string): Promise<{ access_token: string }> {
  const res = await fetch(`${PREFIX}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handle(res);
}

export async function registerRequest(email: string, password: string): Promise<{ access_token: string }> {
  const res = await fetch(`${PREFIX}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handle(res);
}

export async function bootstrapRequest(email: string, password: string): Promise<{ access_token: string }> {
  const res = await fetch(`${PREFIX}/api/auth/bootstrap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
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
  email: string;
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

export async function adminResetPassword(loginEmail: string, newPassword: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/admin/users/${encodeURIComponent(loginEmail)}/password`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_password: newPassword }),
  });
  return handle(res);
}

export async function adminSetRole(loginEmail: string, isAdmin: boolean): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/admin/users/${encodeURIComponent(loginEmail)}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_admin: isAdmin }),
  });
  return handle(res);
}

export async function adminDeleteUser(loginEmail: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/admin/users/${encodeURIComponent(loginEmail)}`, { method: "DELETE" });
  return handle(res);
}

export type UsageCountPct = { count: number; pct: number };

export type UsageMetricsRollup = {
  jobs_with_metrics: number;
  sum_input_bytes: number;
  avg_input_bytes: number;
  sum_media_duration_sec: number;
  avg_media_duration_sec: number;
  sum_audio_extract_sec: number;
  avg_audio_extract_sec: number;
  sum_whisper_sec: number;
  avg_whisper_sec: number;
  sum_transcript_chars: number;
  avg_transcript_chars: number;
  sum_extract_llm_sec: number;
  sum_merge_llm_sec: number;
  sum_llm_sec: number;
  sum_llm_chunks: number;
};

export type AdminUsageSummary = {
  period_days: number;
  total_submissions: number;
  pipeline_minutes_llm: UsageCountPct;
  pipeline_transcript_only: UsageCountPct;
  provider_ollama: UsageCountPct;
  provider_openai: UsageCountPct;
  ollama_models_for_llm_jobs: { model: string; count: number; pct: number }[];
  openai_models_for_llm_jobs: { model: string; count: number; pct: number }[];
  whisper_presets_for_media: { preset: string; count: number; pct: number }[];
  media_kind_breakdown: { kind: string; count: number; pct: number }[];
  metrics_rollup: UsageMetricsRollup;
};

export type AdminUsageSettingsSummary = {
  period_days: number;
  total_submissions: number;
  notification_breakdown: { value: string; count: number; pct: number }[];
  supplementary_teams_used: UsageCountPct;
  supplementary_notes_used: UsageCountPct;
  supplementary_any_used: UsageCountPct;
  guard_events: { event_type: string; count: number }[];
  total_guard_events: number;
};

export type UsageEventRow = {
  id: number;
  created_at: string;
  task_id: string;
  user_email: string;
  transcript_only: boolean;
  llm_provider: string;
  model_name: string;
  whisper_preset: string;
  media_kind: string;
  notification_type?: string | null;
  has_supplementary_teams?: boolean | null;
  has_supplementary_notes?: boolean | null;
  input_bytes?: number | null;
  media_duration_sec?: number | null;
  audio_extract_wall_sec?: number | null;
  whisper_wall_sec?: number | null;
  transcript_chars?: number | null;
  extract_llm_sec?: number | null;
  merge_llm_sec?: number | null;
  llm_chunks?: number | null;
  completion_wall_sec?: number | null;
};

export type UsageAdminNoteRow = {
  id: number;
  created_at: string;
  author_email: string;
  body: string;
};

export type SuggestionBoxRow = {
  id: number;
  created_at: string;
  updated_at: string;
  author_email: string;
  subject: string;
  body: string;
  page_url: string;
  client_version: string;
  status: "new" | "in_progress" | "done";
  admin_note: string;
};

export async function adminUsageSummary(days: number): Promise<AdminUsageSummary> {
  const q = new URLSearchParams({ days: String(days) });
  const res = await apiFetch(`${PREFIX}/api/admin/usage/summary?${q.toString()}`);
  return handle(res);
}

export async function adminUsageSettingsSummary(days: number): Promise<AdminUsageSettingsSummary> {
  const q = new URLSearchParams({ days: String(days) });
  const res = await apiFetch(`${PREFIX}/api/admin/usage/settings-summary?${q.toString()}`);
  return handle(res);
}

export async function adminUsageEvents(params: {
  days: number;
  limit?: number;
  offset?: number;
}): Promise<{ items: UsageEventRow[]; total: number }> {
  const q = new URLSearchParams({ days: String(params.days) });
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const res = await apiFetch(`${PREFIX}/api/admin/usage/events?${q.toString()}`);
  return handle(res);
}

export async function downloadAdminUsageLogMd(days: number, filename: string): Promise<void> {
  const q = new URLSearchParams({ days: String(days) });
  const res = await apiFetch(`${PREFIX}/api/admin/usage/export/md?${q.toString()}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, filename);
}

export async function adminUsageNotesList(): Promise<UsageAdminNoteRow[]> {
  const res = await apiFetch(`${PREFIX}/api/admin/usage/notes`);
  return handle(res);
}

export async function adminUsageNoteAdd(body: string): Promise<UsageAdminNoteRow> {
  const res = await apiFetch(`${PREFIX}/api/admin/usage/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  return handle(res);
}

export async function adminUsageNoteDelete(noteId: number): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/admin/usage/notes/${noteId}`, { method: "DELETE" });
  return handle(res);
}

export async function adminSuggestionBoxList(params?: {
  status?: "" | "new" | "in_progress" | "done";
  limit?: number;
  offset?: number;
}): Promise<{ items: SuggestionBoxRow[]; total: number }> {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(`${PREFIX}/api/admin/suggestion-box${qs ? `?${qs}` : ""}`);
  return handle(res);
}

export async function adminSuggestionBoxPatch(
  ticketId: number,
  body: { status?: "new" | "in_progress" | "done"; admin_note?: string },
): Promise<SuggestionBoxRow> {
  return postJson(`${PREFIX}/api/admin/suggestion-box/${ticketId}`, body, "PATCH");
}

export async function getVersion(): Promise<{ version: string }> {
  const res = await fetch(`${PREFIX}/api/version`);
  return handle(res);
}

export async function getPresets(): Promise<Record<string, { label: string }>> {
  const res = await apiFetch(`${PREFIX}/api/presets`);
  return handle(res);
}

export async function createTask(
  fd: FormData,
  options?: { onUploadProgress?: (loaded: number, total: number | null, percent: number) => void },
): Promise<{ task_id: string; filename: string }> {
  const res = await postFormDataWithUploadProgress(`${PREFIX}/api/tasks`, fd, options?.onUploadProgress);
  return handle(res);
}

export async function listRecords(params: {
  days?: number;
  search?: string;
  category?: string;
  status_filter?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: RecordRow[]; total: number }> {
  const q = new URLSearchParams();
  if (params.days != null) q.set("days", String(params.days));
  if (params.search) q.set("search", params.search);
  if (params.category) q.set("category", params.category);
  if (params.status_filter) q.set("status_filter", params.status_filter);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
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

/** 404 のとき null（ポーリング用。それ以外の HTTP エラーは従来どおり throw） */
export async function getRecordOrNull(id: string): Promise<RecordRow | null> {
  const res = await apiFetch(`${PREFIX}/api/records/${encodeURIComponent(id)}`);
  if (res.status === 404) return null;
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

export async function discardRecord(id: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${PREFIX}/api/records/${encodeURIComponent(id)}/discard`, {
    method: "POST",
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

/** Whisper 後など transcript があるとき先に .md で取得（キュー中でも可） */
export async function downloadExportTranscriptMd(recordId: string, filename: string): Promise<void> {
  const res = await apiFetch(`${PREFIX}/api/records/${encodeURIComponent(recordId)}/export/transcript_md`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, filename);
}

