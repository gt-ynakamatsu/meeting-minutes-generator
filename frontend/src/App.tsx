import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import {
  adminCreateUser,
  adminDeleteUser,
  adminListUsers,
  adminResetPassword,
  adminSetRole,
  bootstrapRequest,
  createTask,
  discardRecord,
  downloadExportMinutes,
  downloadExportTranscript,
  getAuthMe,
  getAuthStatus,
  getPresets,
  getMeLlm,
  getOllamaModels,
  getQueue,
  getRecord,
  getStoredToken,
  getVersion,
  listRecords,
  loginRequest,
  registerRequest,
  patchMeLlm,
  patchSummary,
  setStoredToken,
  type AdminUserRow,
  type AuthMe,
  type AuthStatus,
  type RecordRow,
  type TaskSubmitMetadata,
} from "./api";
import { jobStatusShortLabel, parseJobStatus } from "./jobStatus";

const LS_PENDING = "mm_pending_tasks";
/** ワーカーがエラー破棄したレコードの summary 先頭（一覧の「エラー」フィルタと一致） */
const TASK_ERROR_SUMMARY_PREFIX = "【処理エラー】";

function taskErrorDetailFromSummary(raw: string): string {
  const t = (raw || "").trim();
  if (!t.startsWith(TASK_ERROR_SUMMARY_PREFIX)) return t;
  const rest = t.slice(TASK_ERROR_SUMMARY_PREFIX.length).replace(/^\s*\n?/, "").trim();
  return rest || t;
}

function clipForDesktopNotification(text: string, maxLen = 900): string {
  const t = (text || "").trim();
  if (t.length <= maxLen) return t;
  return `${t.slice(0, maxLen)}…`;
}

/** Whisper / PyTorch / CUDA の GPU メモリ・割り当て系とみなす（ログ全文に対して判定） */
function looksLikeGpuMemoryError(text: string): boolean {
  const s = (text || "").toLowerCase();
  if (!s.trim()) return false;
  if (s.includes("out of memory") || s.includes("out-of-memory")) return true;
  if (/\boom\b/.test(s)) return true;
  if (s.includes("cuda error") || s.includes("cudnn") || s.includes("cublas")) return true;
  if (s.includes("cuda failed")) return true;
  if (s.includes("torch.cuda.outofmemory")) return true;
  if (s.includes("allocation failed") || s.includes("failed to allocate")) return true;
  if (s.includes("gpu memory") || s.includes("vram")) return true;
  if (s.includes("cublas_status_alloc_failed")) return true;
  return false;
}

function ErrorUserGuidance({ errorText }: { errorText: string }) {
  const gpu = looksLikeGpuMemoryError(errorText);
  return (
    <p
      className="muted"
      style={{
        margin: "0.75rem 0 0",
        fontSize: "0.9rem",
        lineHeight: 1.55,
        padding: "0.55rem 0.65rem",
        borderRadius: 6,
        background: "rgba(0,0,0,0.04)",
      }}
    >
      {gpu ? (
        <>
          このエラーは <strong>GPU のメモリ不足</strong> などが原因の可能性があります。ワーカー側の環境変数（例:{" "}
          <code>WHISPER_MODEL</code> を <code>small</code> に、<code>WHISPER_COMPUTE_TYPE</code> を軽い設定に）を見直したうえで、
          <strong>同じファイルを再アップロードしてやり直してください</strong>。
        </>
      ) : (
        <>
          このエラーは種類が特定できないため、<strong>上記のログ内容を控えたうえで管理者にお問い合わせください</strong>。
        </>
      )}
    </p>
  );
}

function TroubleshootingHints({ showOpenAiLine = true }: { showOpenAiLine?: boolean }) {
  return (
    <details style={{ marginTop: "0.5rem" }}>
      <summary>トラブルシューティング</summary>
      <ul className="muted" style={{ fontSize: "0.85rem" }}>
        <li>
          GPU / CUDA: 動画・音声では Whisper が GPU を使います。「out of memory」のときはワーカーの環境変数で{" "}
          <code>WHISPER_MODEL=small</code> や <code>WHISPER_COMPUTE_TYPE=int8_float16</code> を試す（詳細は README /{" "}
          <code>.env.example</code>）。
        </li>
        <li>Ollama: モデルが pull 済みか、`OLLAMA_BASE_URL` を確認してください。</li>
        {showOpenAiLine ? <li>OpenAI: API キー・上限（429）を確認してください。</li> : null}
        <li>テキスト / SRT: UTF-8 推奨。SRT のタイムコード形式を確認してください。</li>
        <li>カスタムプロンプト: `{"{"}CHUNK_TEXT{"}"}` / `{"{"}EXTRACTED_JSON{"}"}` の有無。</li>
      </ul>
    </details>
  );
}

function formatDbDateTime(value: string | null | undefined): string {
  if (value == null || String(value).trim() === "") return "—";
  const raw = String(value).trim();
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return raw;
  try {
    return d.toLocaleString("ja-JP", { dateStyle: "short", timeStyle: "medium" });
  } catch {
    return raw;
  }
}

function clampPct(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function JobProgressPanel({ status, compact = false }: { status: string | null | undefined; compact?: boolean }) {
  const p = parseJobStatus(String(status ?? ""));
  const cls = compact ? "job-progress job-progress--compact" : "job-progress";
  const ov = clampPct(p.overallPercent);
  const ph = clampPct(p.phasePercent);

  if (p.kind === "error") {
    return (
      <div className={cls}>
        <div className="job-progress__title job-progress__title--error">{p.title}</div>
        {p.detail ? <div className="job-progress__detail">{p.detail}</div> : null}
      </div>
    );
  }

  if (p.kind === "cancelled") {
    return (
      <div className={cls}>
        <div className="job-progress__title job-progress__title--cancelled">{p.title}</div>
        {p.detail ? (
          <div className="job-progress__detail muted" style={{ fontSize: compact ? "0.78rem" : "0.82rem" }}>
            {p.detail}
          </div>
        ) : null}
      </div>
    );
  }

  if (p.kind === "completed") {
    return (
      <div className={cls}>
        <div className="job-progress__title">{p.title}</div>
        <div className="job-progress__meta">
          <span>全体</span>
          <span>100%</span>
        </div>
        <progress className="job-progress__bar job-progress__bar--overall" max={100} value={100} />
      </div>
    );
  }

  return (
    <div className={cls}>
      <div className="job-progress__title">{p.title}</div>
      {p.detail ? (
        <div className="job-progress__detail muted" style={{ fontSize: compact ? "0.78rem" : "0.82rem" }}>
          {p.detail}
        </div>
      ) : null}
      <div className="job-progress__meta">
        <span>全体の進捗（目安）</span>
        <span>{ov}%</span>
      </div>
      <progress className="job-progress__bar job-progress__bar--overall" max={100} value={ov} />
      <div className="job-progress__meta">
        <span>現在の工程</span>
        <span>{ph}%</span>
      </div>
      <progress className="job-progress__bar job-progress__bar--phase" max={100} value={ph} />
    </div>
  );
}

function UserCircleIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
      <path
        d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"
        fill="currentColor"
      />
    </svg>
  );
}

function SettingsDrawer({
  open,
  onClose,
  title = "設定",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="settings-drawer-root" role="dialog" aria-modal="true" aria-labelledby="settings-drawer-title">
      <button type="button" className="settings-drawer-backdrop" aria-label="閉じる" onClick={onClose} />
      <div className="settings-drawer-panel">
        <div className="settings-drawer-head">
          <h2 id="settings-drawer-title">{title}</h2>
          <button type="button" className="settings-drawer-close" onClick={onClose} aria-label="閉じる">
            ×
          </button>
        </div>
        <div className="settings-drawer-body">{children}</div>
      </div>
    </div>
  );
}

function AccountMenuDropdown({
  items,
}: {
  items: { key: string; label: string; onClick: () => void; danger?: boolean }[];
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [menuOpen]);

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        type="button"
        className="account-icon-btn"
        aria-label="アカウントメニュー"
        aria-expanded={menuOpen}
        aria-haspopup="true"
        onClick={() => setMenuOpen((v) => !v)}
      >
        <UserCircleIcon />
      </button>
      {menuOpen ? (
        <div className="account-menu-dropdown" role="menu">
          {items.map((it) => (
            <button
              key={it.key}
              type="button"
              role="menuitem"
              className={`account-menu-item${it.danger ? " account-menu-item--danger" : ""}`}
              onClick={() => {
                it.onClick();
                setMenuOpen(false);
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function exportBasename(row: RecordRow): string {
  const f = row.filename || "file";
  const base = f.replace(/^.*[/\\]/, "") || "file";
  return base.length > 180 ? base.slice(0, 180) : base;
}

function loadPending(): string[] {
  try {
    const raw = localStorage.getItem(LS_PENDING);
    if (!raw) return [];
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function savePending(ids: string[]) {
  localStorage.setItem(LS_PENDING, JSON.stringify(ids));
}

/** 社内議事録テンプレ（罫線・【】見出し）— Markdown の単一改行が潰れるため pre-wrap でそのまま出す */
function looksLikeMinutesTemplateMd(s: string): boolean {
  const t = s.slice(0, 1500);
  if (t.includes("社内会議議事録") || t.includes("顧客会議議事録")) return true;
  if (t.includes("━━━━━━━━")) return true;
  if (t.includes("【議事内容】")) return true;
  if (/【\s*議/.test(t)) return true;
  return false;
}

function MinutesMarkdownOrTemplate({ text }: { text: string }) {
  if (looksLikeMinutesTemplateMd(text)) {
    return <div className="minutes-template-text">{text}</div>;
  }
  return (
    <div className="minutes-markdown">
      <ReactMarkdown remarkPlugins={[remarkBreaks]}>{text}</ReactMarkdown>
    </div>
  );
}

function MinutesBody({ text }: { text: string }) {
  if (!text || text === "None") {
    return <p className="muted">詳細な議事録データが作成されていません。</p>;
  }
  try {
    const data = JSON.parse(text) as Record<string, unknown>;
    if (typeof data !== "object" || data === null) throw new Error("not object");
    const decisions = data.decisions as { text?: string }[] | undefined;
    const issues = data.issues as { text?: string }[] | undefined;
    const items = data.items as { who?: string; what?: string; due?: string }[] | undefined;
    const notes = data.notes as { text?: string }[] | undefined;
    return (
      <div className="minutes-json">
        {decisions?.length ? (
          <>
            <h4>決定事項</h4>
            <ul>
              {decisions.map((d, i) => (
                <li key={i}>{d.text}</li>
              ))}
            </ul>
          </>
        ) : null}
        {issues?.length ? (
          <>
            <h4>課題</h4>
            <ul>
              {issues.map((x, i) => (
                <li key={i}>{x.text}</li>
              ))}
            </ul>
          </>
        ) : null}
        {items?.length ? (
          <>
            <h4>アクション</h4>
            <ul>
              {items.map((x, i) => (
                <li key={i}>
                  [ ] <strong>{x.who ?? "担当未定"}</strong>: {x.what}
                  {x.due ? `（期限: ${x.due}）` : ""}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {notes?.length ? (
          <>
            <h4>重要メモ</h4>
            <ul>
              {notes.map((x, i) => (
                <li key={i}>{x.text}</li>
              ))}
            </ul>
          </>
        ) : null}
        {!decisions?.length && !issues?.length && !items?.length && !notes?.length ? (
          <MinutesMarkdownOrTemplate text={text} />
        ) : null}
      </div>
    );
  } catch {
    return <MinutesMarkdownOrTemplate text={text} />;
  }
}

function escapeForHtmlText(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const POPUP_PREVIEW_STYLES = `body{margin:0;background:#f6faf7;color:#1a1a1a;}
.popup-root{box-sizing:border-box;padding:1rem 1.35rem;max-width:52rem;margin:0 auto;font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif;}
.popup-title{font-size:1.05rem;margin:0 0 1rem;color:#1b4332;font-weight:700;line-height:1.4;word-break:break-word;}
.muted{color:#5c6f64;}
.minutes-json h4{margin:1rem 0 0.35rem;color:#1b4332;font-size:0.95rem;}
.minutes-json ul{margin:0.25rem 0 0.65rem;padding-left:1.35rem;}
.minutes-json li{margin:0.2rem 0;}
.popup-body h1,.popup-body h2,.popup-body h3{color:#1b4332;margin:1rem 0 0.45rem;line-height:1.35;}
.popup-body h1{font-size:1.2rem;}
.popup-body h2{font-size:1.05rem;}
.popup-body h3{font-size:1rem;}
.popup-body p{margin:0.5rem 0;}
.popup-body ul,.popup-body ol{padding-left:1.35rem;margin:0.5rem 0;}
.popup-body blockquote{margin:0.5rem 0;padding-left:0.85rem;border-left:3px solid #95d5b2;color:#374151;}
.popup-body code{background:#eef5f1;padding:0.12em 0.35em;border-radius:4px;font-size:0.9em;}
.popup-body pre{background:#eef5f1;padding:0.75rem 1rem;border-radius:8px;overflow:auto;font-size:0.88rem;}
.popup-body a{color:#2d6a4f;}
.popup-fallback{white-space:pre-wrap;word-break:break-word;font-size:0.9rem;padding:0.5rem 0;}
.minutes-template-text{white-space:pre-wrap;word-break:break-word;font-size:0.88rem;line-height:1.65;margin:0;}
.minutes-markdown p{margin:0.2rem 0;}
.minutes-markdown ul,.minutes-markdown ol{margin:0.35rem 0;padding-left:1.35rem;}`;

/** プレビューと同じ内容を別ウィンドウに表示 */
function openMinutesPreviewWindow(heading: string, bodyText: string) {
  const t = (bodyText || "").trim();
  if (!t || t === "None") {
    window.alert("表示する議事録がありません。");
    return;
  }
  const w = window.open("", "_blank", "width=1100,height=880");
  if (!w) {
    window.alert(
      "別ウィンドウを開けませんでした。ブラウザでこのサイト（アドレスバー左の鍵アイコン等）からポップアップを許可してください。",
    );
    return;
  }

  const safeTitle = escapeForHtmlText(heading.slice(0, 200) || "議事録プレビュー");
  const h1Text = (heading || "").trim() || "議事録プレビュー";

  let bodyInner: string;
  try {
    bodyInner = renderToStaticMarkup(
      <div className="popup-root">
        <h1 className="popup-title">{h1Text}</h1>
        <div className="popup-body">
          <MinutesBody text={bodyText} />
        </div>
      </div>,
    );
  } catch (e) {
    console.error("openMinutesPreviewWindow renderToStaticMarkup", e);
    bodyInner = `<div class="popup-root"><h1 class="popup-title">${escapeForHtmlText(h1Text)}</h1><pre class="popup-fallback">${escapeForHtmlText(bodyText)}</pre></div>`;
  }

  const full = `<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>${safeTitle}</title><style>${POPUP_PREVIEW_STYLES}</style></head><body>${bodyInner}</body></html>`;

  try {
    w.document.open();
    w.document.write(full);
    w.document.close();
    w.focus();
  } catch (e) {
    console.error("openMinutesPreviewWindow document.write", e);
    w.close();
    window.alert("別ウィンドウへの書き込みに失敗しました。ブラウザの設定を確認してください。");
  }
}

function recordIsActiveJob(status: string): boolean {
  return status === "pending" || status.startsWith("processing");
}

function RecordCard({
  row,
  onSaved,
  onDiscard,
  showOpenAiTroubleshooting = true,
}: {
  row: RecordRow;
  onSaved: () => void | Promise<void>;
  onDiscard?: (id: string) => void | Promise<void>;
  showOpenAiTroubleshooting?: boolean;
}) {
  const [tab, setTab] = useState<"preview" | "edit" | "raw">("preview");
  const [editText, setEditText] = useState(row.summary || "");
  const [saving, setSaving] = useState(false);
  const summary = row.summary || "";

  useEffect(() => {
    setEditText(summary);
  }, [summary, row.id]);

  const ctx = useMemo(() => {
    if (!row.context_json) return null;
    try {
      return JSON.parse(row.context_json) as Record<string, string>;
    } catch {
      return null;
    }
  }, [row.context_json]);

  const statusStr = row.status != null ? String(row.status) : "";
  const errorStyleCancelled =
    statusStr === "cancelled" && summary.trimStart().startsWith(TASK_ERROR_SUMMARY_PREFIX);
  const workerErrorLogText = errorStyleCancelled ? taskErrorDetailFromSummary(summary) : "";
  const statusSummary =
    statusStr.startsWith("Error") || errorStyleCancelled ? "エラー" : jobStatusShortLabel(statusStr);
  const label = `${formatDbDateTime(row.created_at)} · ${(row.topic || "").trim() || "（議題なし）"} · ${row.filename} · ${statusSummary}`;

  return (
    <details className="card" open={false}>
      <summary>{label}</summary>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        分類: {row.category || "—"} ／ タグ: {row.tags || "—"} ／ プリセット: {row.preset_id || "—"} ／ 日付:{" "}
        {row.meeting_date || "—"}
      </p>
      <p className="muted" style={{ fontSize: "0.8rem", margin: "0.35rem 0 0" }}>
        受付: {formatDbDateTime(row.created_at)} ／ 処理開始: {formatDbDateTime(row.processing_started_at)} ／
        処理終了: {formatDbDateTime(row.processing_finished_at)}
      </p>
      {ctx && Object.values(ctx).some(Boolean) ? (
        <details>
          <summary>入力したコンテキスト</summary>
          <ul className="muted" style={{ fontSize: "0.88rem" }}>
            {ctx.purpose ? <li>目的: {ctx.purpose}</li> : null}
            {ctx.participants ? <li>参加者: {ctx.participants}</li> : null}
            {ctx.glossary ? <li>用語: {ctx.glossary}</li> : null}
            {ctx.tone ? <li>トーン: {ctx.tone}</li> : null}
            {ctx.action_rules ? <li>アクションルール: {ctx.action_rules}</li> : null}
          </ul>
        </details>
      ) : null}

      {statusStr === "completed" ? (
        <>
          <div className="tabs tabs--record">
            <div className="tabs--record__modes">
              <button type="button" className={tab === "preview" ? "active" : ""} onClick={() => setTab("preview")}>
                プレビュー
              </button>
              <button type="button" className={tab === "edit" ? "active" : ""} onClick={() => setTab("edit")}>
                手直し・保存
              </button>
              <button type="button" className={tab === "raw" ? "active" : ""} onClick={() => setTab("raw")}>
                書き起こし
              </button>
            </div>
            <div className="tabs--record__tail-actions">
              <button
                type="button"
                className="tabs--record__tool"
                title="現在保存されているプレビュー内容を別ウィンドウで開きます"
                disabled={!summary.trim() || summary === "None"}
                onClick={() => {
                  const h = `${(row.topic || "").trim() || "（議題なし）"} · ${row.filename || "file"}`;
                  openMinutesPreviewWindow(h, summary);
                }}
              >
                別ウィンドウで開く
              </button>
              <button
                type="button"
                className="tabs--record__tool"
                title="議事録を Markdown ファイルでダウンロードします"
                disabled={!summary.trim() || summary === "None"}
                onClick={() =>
                  void downloadExportMinutes(row.id, `minutes_${exportBasename(row)}.md`).catch((e) => alert(String(e)))
                }
              >
                議事録をダウンロード（.md）
              </button>
            </div>
          </div>
          {tab === "preview" ? (
            <>
              <MinutesBody text={summary} />
            </>
          ) : null}
          {tab === "edit" ? (
            <div>
              <p className="muted" style={{ fontSize: "0.85rem" }}>
                テキストを編集して保存できます。
              </p>
              <textarea rows={14} value={editText} onChange={(e) => setEditText(e.target.value)} style={{ width: "100%" }} />
              <button
                type="button"
                className="btn-primary"
                style={{ maxWidth: 240 }}
                disabled={saving}
                onClick={async () => {
                  setSaving(true);
                  try {
                    await patchSummary(row.id, editText);
                    await onSaved();
                  } catch (e) {
                    alert(String(e));
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                上書き保存
              </button>
            </div>
          ) : null}
          {tab === "raw" ? (
            <div>
              <textarea readOnly rows={12} value={row.transcript || ""} style={{ width: "100%" }} />
              <p style={{ marginTop: "0.5rem" }}>
                <button
                  type="button"
                  className="btn-link"
                  onClick={() =>
                    void downloadExportTranscript(row.id, `${exportBasename(row)}.txt`).catch((e) => alert(String(e)))
                  }
                >
                  テキストをダウンロード
                </button>
              </p>
            </div>
          ) : null}
        </>
      ) : statusStr === "cancelled" && errorStyleCancelled ? (
        <div className="error-box">
          <strong>エラー（ジョブは破棄済み）</strong>
          <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.88rem" }}>
            アップロードされた原稿ファイルはサーバから削除されています。以下はワーカーが記録したエラー内容です。
          </p>
          <div
            className="task-error-detail"
            style={{
              marginTop: "0.65rem",
              fontSize: "0.9rem",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              lineHeight: 1.5,
            }}
          >
            {workerErrorLogText}
          </div>
          <ErrorUserGuidance errorText={workerErrorLogText} />
          <TroubleshootingHints showOpenAiLine={showOpenAiTroubleshooting} />
        </div>
      ) : statusStr === "cancelled" ? (
        <div className="cancelled-box">
          <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>
            このジョブは破棄されました。アップロードされた原稿ファイルはサーバから削除されています。
          </p>
          {summary.trim() && summary !== "None" ? (
            <pre className="muted" style={{ margin: "0.65rem 0 0", fontSize: "0.82rem", whiteSpace: "pre-wrap" }}>
              {summary}
            </pre>
          ) : null}
        </div>
      ) : statusStr.startsWith("Error") ? (
        <div className="error-box">
          <strong>エラー</strong>
          <div>{statusStr}</div>
          <ErrorUserGuidance errorText={statusStr} />
          <TroubleshootingHints showOpenAiLine={showOpenAiTroubleshooting} />
        </div>
      ) : (
        <div>
          {recordIsActiveJob(statusStr) && onDiscard ? (
            <p style={{ margin: "0 0 0.5rem" }}>
              <button
                type="button"
                className="btn-discard"
                onClick={() => {
                  void onDiscard(row.id);
                }}
              >
                処理を破棄
              </button>
            </p>
          ) : null}
          <JobProgressPanel status={statusStr} />
          <p className="muted" style={{ fontSize: "0.75rem", margin: "0.5rem 0 0" }}>
            生ステータス: <code>{statusStr || "—"}</code>
          </p>
        </div>
      )}
    </details>
  );
}

function BootstrapPanel({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [p2, setP2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  return (
    <div className="auth-shell">
      <header className="auth-top-bar">
        <span className="auth-top-bar-brand">AI 議事録</span>
        <AccountMenuDropdown
          items={[
            { key: "settings", label: "説明・設定", onClick: () => setSettingsOpen(true) },
            {
              key: "form",
              label: "セットアップフォームへ",
              onClick: () => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
            },
          ]}
        />
      </header>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)}>
        <p className="muted" style={{ marginTop: 0 }}>
          初回セットアップでは、下のフォームから最初の管理者アカウントを登録してください。登録後は通常どおりログインして利用できます。
        </p>
      </SettingsDrawer>
      <main className="main auth-form" style={{ maxWidth: 480, margin: "2rem auto" }}>
        <h1>初回セットアップ</h1>
        <p className="muted">
          最初の管理者アカウントを作成してください。この操作はユーザーが 0 人のときだけ可能です。パスワードは 8 文字以上です。
        </p>
        <form
          ref={formRef}
          id="bootstrap-account-form"
          onSubmit={(e) => {
            e.preventDefault();
            void (async () => {
              setErr(null);
              if (p !== p2) {
                setErr("パスワードが一致しません");
                return;
              }
              setBusy(true);
              try {
                const { access_token } = await bootstrapRequest(u.trim(), p);
                setStoredToken(access_token);
                onDone();
              } catch (ex) {
                setErr(String(ex));
              } finally {
                setBusy(false);
              }
            })();
          }}
        >
          <label>メールアドレス（ログイン ID）</label>
          <input
            type="email"
            value={u}
            onChange={(e) => setU(e.target.value)}
            autoComplete="email"
          />
          <label>パスワード（8 文字以上）</label>
          <input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="new-password" />
          <label>パスワード（確認）</label>
          <input type="password" value={p2} onChange={(e) => setP2(e.target.value)} autoComplete="new-password" />
          {err ? <p className="error-box">{err}</p> : null}
          <button className="btn-primary" type="submit" disabled={busy || !u.trim() || p.length < 8}>
            登録してログイン
          </button>
        </form>
        <p className="muted" style={{ marginTop: "1.25rem", fontSize: "0.85rem" }}>
          代わりに環境変数 <code>MM_BOOTSTRAP_ADMIN_USER</code>（メールアドレス） / <code>MM_BOOTSTRAP_ADMIN_PASSWORD</code> で初期ユーザーを作ることもできます。
        </p>
      </main>
    </div>
  );
}

function AdminUserPanel({ selfEmail }: { selfEmail: string }) {
  const [rows, setRows] = useState<AdminUserRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [nu, setNu] = useState("");
  const [np, setNp] = useState("");
  const [na, setNa] = useState(false);
  const [editingPw, setEditingPw] = useState<string | null>(null);
  const [pwNew, setPwNew] = useState("");

  const load = useCallback(() => {
    setErr(null);
    adminListUsers()
      .then(setRows)
      .catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <p className="muted" style={{ fontSize: "0.88rem", marginTop: 0 }}>
        ユーザーの登録・削除、パスワード再設定、管理者権限の付与・解除ができます。最後の管理者は削除・権限解除できません。
      </p>
      {msg ? <p className="muted">{msg}</p> : null}
      {err ? <p className="error-box">{err}</p> : null}

      <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border, #ddd)" }}>
                <th style={{ textAlign: "left", padding: "0.5rem 0" }}>メールアドレス</th>
                <th style={{ textAlign: "left", padding: "0.5rem" }}>管理者権限</th>
                <th style={{ textAlign: "right", padding: "0.5rem 0" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.email} style={{ borderBottom: "1px solid var(--border, #eee)" }}>
                  <td style={{ padding: "0.5rem 0" }}>
                    <code>{r.email}</code>
                    {r.email === selfEmail ? <span className="muted"> （あなた）</span> : null}
                  </td>
                  <td style={{ padding: "0.5rem" }}>{r.is_admin ? "はい" : "—"}</td>
                  <td style={{ padding: "0.5rem 0", textAlign: "right", whiteSpace: "nowrap" }}>
                    {editingPw === r.email ? (
                      <span style={{ display: "inline-flex", flexWrap: "wrap", gap: "0.35rem", alignItems: "center", justifyContent: "flex-end" }}>
                        <input
                          type="password"
                          placeholder="新パスワード"
                          value={pwNew}
                          onChange={(e) => setPwNew(e.target.value)}
                          style={{ maxWidth: 140 }}
                        />
                        <button
                          type="button"
                          className="btn-primary"
                          onClick={() => {
                            void (async () => {
                              setErr(null);
                              setMsg(null);
                              try {
                                await adminResetPassword(r.email, pwNew);
                                setEditingPw(null);
                                setPwNew("");
                                setMsg("パスワードを更新しました。");
                                load();
                              } catch (e) {
                                setErr(String(e));
                              }
                            })();
                          }}
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          className="btn-link"
                          onClick={() => {
                            setEditingPw(null);
                            setPwNew("");
                          }}
                        >
                          取消
                        </button>
                      </span>
                    ) : (
                      <>
                        <button type="button" className="btn-link" onClick={() => { setEditingPw(r.email); setPwNew(""); }}>
                          パスワード
                        </button>
                        <button
                          type="button"
                          className="btn-link"
                          onClick={() => {
                            void (async () => {
                              setErr(null);
                              setMsg(null);
                              try {
                                await adminSetRole(r.email, !r.is_admin);
                                setMsg("管理者権限を更新しました。");
                                load();
                              } catch (e) {
                                setErr(String(e));
                              }
                            })();
                          }}
                        >
                          {r.is_admin ? "管理者権限を解除" : "管理者権限を付与"}
                        </button>
                        <button
                          type="button"
                          className="btn-link"
                          style={{ color: "var(--danger, #b00)" }}
                          disabled={r.email === selfEmail}
                          onClick={() => {
                            if (!window.confirm(`このメールアドレス（${r.email}）のユーザーを削除しますか？`)) return;
                            void (async () => {
                              setErr(null);
                              setMsg(null);
                              try {
                                await adminDeleteUser(r.email);
                                setMsg("ユーザーを削除しました。");
                                load();
                              } catch (e) {
                                setErr(String(e));
                              }
                            })();
                          }}
                        >
                          削除
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

      <h3 style={{ marginTop: "1.5rem" }}>ユーザーを追加</h3>
      <div className="auth-form" style={{ maxWidth: "100%" }}>
        <label>メールアドレス（ログイン ID）</label>
        <input type="email" value={nu} onChange={(e) => setNu(e.target.value)} autoComplete="off" />
        <label>初期パスワード（8 文字以上）</label>
        <input type="password" value={np} onChange={(e) => setNp(e.target.value)} autoComplete="new-password" />
        <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
          <input type="checkbox" checked={na} onChange={(e) => setNa(e.target.checked)} />
          管理者権限を付与する
        </label>
        <button
          type="button"
          className="btn-primary"
          disabled={!nu.trim() || np.length < 8}
          onClick={() => {
            void (async () => {
              setErr(null);
              setMsg(null);
              try {
                await adminCreateUser({ email: nu.trim().toLowerCase(), password: np, is_admin: na });
                setNu("");
                setNp("");
                setNa(false);
                setMsg("ユーザーを追加しました。");
                load();
              } catch (e) {
                setErr(String(e));
              }
            })();
          }}
        >
          追加
        </button>
      </div>
    </>
  );
}

function LoginPanel({
  onSuccess,
  selfRegisterAllowed,
}: {
  onSuccess: () => void;
  selfRegisterAllowed: boolean;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [p2, setP2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const userRef = useRef<HTMLInputElement>(null);
  return (
    <div className="auth-shell">
      <header className="auth-top-bar">
        <span className="auth-top-bar-brand">AI 議事録</span>
        <AccountMenuDropdown
          items={[
            { key: "settings", label: "説明・設定", onClick: () => setSettingsOpen(true) },
            { key: "form", label: "ログインフォームへ", onClick: () => userRef.current?.focus() },
          ]}
        />
      </header>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)}>
        <p className="muted" style={{ marginTop: 0 }}>
          {selfRegisterAllowed
            ? "ログインまたは新規登録で入れます。右上のアイコンからメニューを開き、「ログインフォームへ」でメールアドレス欄にフォーカスできます。"
            : "メールアドレスとパスワードを入力してログインしてください。アカウントは管理者が発行します。右上のアイコンからメニューを開き、「ログインフォームへ」でフォームにフォーカスできます。"}
        </p>
      </SettingsDrawer>
      <main className="main auth-form" style={{ maxWidth: 420, margin: "2rem auto" }}>
        <h1>{mode === "login" ? "ログイン" : "新規登録"}</h1>
        <p className="muted">
          {mode === "login"
            ? "アカウントをお持ちの方はログインしてください。"
            : "メールアドレスとパスワードを決めて登録します（一般ユーザー）。パスワードは 8 文字以上です。"}
        </p>
        {selfRegisterAllowed ? (
          <div className="auth-mode-tabs" role="tablist" aria-label="ログインまたは登録">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "login"}
              className={mode === "login" ? "auth-mode-tab active" : "auth-mode-tab"}
              onClick={() => {
                setMode("login");
                setErr(null);
                setP2("");
              }}
            >
              ログイン
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "register"}
              className={mode === "register" ? "auth-mode-tab active" : "auth-mode-tab"}
              onClick={() => {
                setMode("register");
                setErr(null);
                setP2("");
              }}
            >
              新規登録
            </button>
          </div>
        ) : null}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void (async () => {
              setErr(null);
              if (mode === "register") {
                if (p !== p2) {
                  setErr("パスワードが一致しません");
                  return;
                }
              }
              setBusy(true);
              try {
                const { access_token } =
                  mode === "login"
                    ? await loginRequest(u.trim(), p)
                    : await registerRequest(u.trim(), p);
                setStoredToken(access_token);
                onSuccess();
              } catch (ex) {
                setErr(String(ex));
              } finally {
                setBusy(false);
              }
            })();
          }}
        >
          <label>メールアドレス</label>
          <input
            ref={userRef}
            type="email"
            value={u}
            onChange={(e) => setU(e.target.value)}
            autoComplete="email"
          />
          <label>パスワード{mode === "register" ? "（8 文字以上）" : ""}</label>
          <input
            type="password"
            value={p}
            onChange={(e) => setP(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
          {mode === "register" ? (
            <>
              <label>パスワード（確認）</label>
              <input
                type="password"
                value={p2}
                onChange={(e) => setP2(e.target.value)}
                autoComplete="new-password"
              />
            </>
          ) : null}
          {err ? <p className="error-box">{err}</p> : null}
          <button
            className="btn-primary"
            type="submit"
            disabled={busy || !u.trim() || (mode === "register" ? p.length < 8 : !p)}
          >
            {mode === "login" ? "ログイン" : "登録してログイン"}
          </button>
        </form>
      </main>
    </div>
  );
}

export default function App() {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [authNonce, setAuthNonce] = useState(0);

  useEffect(() => {
    getAuthStatus()
      .then(setAuthStatus)
      .catch(() =>
        setAuthStatus({
          auth_required: false,
          bootstrap_needed: false,
          self_register_allowed: false,
          openai_enabled: true,
        }),
      );
  }, []);

  useEffect(() => {
    const h = () => setAuthNonce((n) => n + 1);
    window.addEventListener("mm-auth-lost", h);
    return () => window.removeEventListener("mm-auth-lost", h);
  }, []);

  if (authStatus === null) {
    return (
      <div className="layout">
        <p className="muted" style={{ padding: "2rem" }}>
          読み込み中…
        </p>
      </div>
    );
  }

  if (authStatus.auth_required && authStatus.bootstrap_needed) {
    return (
      <BootstrapPanel
        onDone={async () => {
          const st = await getAuthStatus();
          setAuthStatus(st);
        }}
      />
    );
  }

  const hasToken = !!getStoredToken();
  if (authStatus.auth_required && !hasToken) {
    return (
      <LoginPanel
        onSuccess={() => setAuthNonce((n) => n + 1)}
        selfRegisterAllowed={authStatus.self_register_allowed !== false}
      />
    );
  }

  const showLogout = authStatus.auth_required;

  const openaiFeatureEnabled = authStatus.openai_enabled !== false;

  return (
    <AppMain
      showLogout={showLogout}
      serverOpenaiMode={authStatus.auth_required}
      openaiFeatureEnabled={openaiFeatureEnabled}
      emailNotifyAvailable={authStatus.email_notify_available === true}
      authNonce={authNonce}
      onLogout={() => {
        setStoredToken(null);
        setAuthNonce((n) => n + 1);
      }}
    />
  );
}

/** 解析キューに載せるファイル（input accept と一致） */
const TASK_FILE_EXTENSIONS = [".mp4", ".mp3", ".m4a", ".wav", ".txt", ".srt"];

function pickTaskMediaFile(list: FileList | null): File | null {
  if (!list?.length) return null;
  for (let i = 0; i < list.length; i++) {
    const f = list.item(i);
    if (!f) continue;
    const lower = f.name.toLowerCase();
    if (TASK_FILE_EXTENSIONS.some((ext) => lower.endsWith(ext))) return f;
  }
  return null;
}

/** Ollama /api/tags 由来の候補＋コンボボックス。未登録モデルはそのまま入力可能（datalist は見た目が select と揃わないため未使用） */
function OllamaModelField({
  value,
  onChange,
  candidates,
  loading,
}: {
  value: string;
  onChange: (v: string) => void;
  candidates: string[];
  loading: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(-1);
  const highlightRef = useRef(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = "mm-ollama-model-listbox";

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase();
    if (!q) return candidates;
    return candidates.filter((c) => c.toLowerCase().includes(q));
  }, [candidates, value]);

  const showList = open && filtered.length > 0;

  useEffect(() => {
    const max = filtered.length - 1;
    if (max < 0) {
      if (highlightRef.current !== -1) {
        highlightRef.current = -1;
        setActiveIdx(-1);
      }
      return;
    }
    if (highlightRef.current > max) {
      highlightRef.current = max;
      setActiveIdx(max);
    }
  }, [filtered.length, value]);

  const setHighlight = (idx: number) => {
    const max = filtered.length - 1;
    const next = max < 0 ? -1 : Math.max(0, Math.min(idx, max));
    highlightRef.current = next;
    setActiveIdx(next);
  };

  useEffect(() => {
    const onDocDown = (e: MouseEvent) => {
      if (wrapRef.current?.contains(e.target as Node)) return;
      setOpen(false);
      highlightRef.current = -1;
      setActiveIdx(-1);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, []);

  const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        setOpen(false);
        highlightRef.current = -1;
        setActiveIdx(-1);
      }
      return;
    }
    if (!candidates.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) setOpen(true);
      if (filtered.length === 0) return;
      const cur = highlightRef.current;
      const next = cur < 0 ? 0 : cur + 1;
      setHighlight(next);
      return;
    }
    if (e.key === "ArrowUp" && open && filtered.length > 0) {
      e.preventDefault();
      const cur = highlightRef.current;
      const next = cur < 0 ? 0 : cur - 1;
      setHighlight(next);
      return;
    }
    if (e.key === "Enter" && open) {
      const idx = highlightRef.current;
      if (idx >= 0 && filtered[idx]) {
        e.preventDefault();
        onChange(filtered[idx]);
        setOpen(false);
        highlightRef.current = -1;
        setActiveIdx(-1);
      }
    }
  };

  return (
    <>
      <label htmlFor="mm-ollama-model-input">Ollama モデル名</label>
      {loading ? (
        <p className="muted" style={{ fontSize: "0.8rem", margin: "0 0 0.25rem" }}>
          Ollama からモデル一覧を取得しています…
        </p>
      ) : null}
      <div className="mm-ollama-combobox" ref={wrapRef}>
        <input
          ref={inputRef}
          id="mm-ollama-model-input"
          role="combobox"
          aria-expanded={showList}
          aria-controls={showList ? listboxId : undefined}
          aria-autocomplete="list"
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
            highlightRef.current = -1;
            setActiveIdx(-1);
          }}
          onFocus={() => {
            if (candidates.length > 0) setOpen(true);
          }}
          onKeyDown={onInputKeyDown}
          placeholder={candidates.length > 0 ? "一覧から選ぶかモデル名を入力" : "モデル名（例: qwen2.5:7b）"}
          autoComplete="off"
          spellCheck={false}
        />
        {candidates.length > 0 ? (
          <button
            type="button"
            className="mm-ollama-combobox__toggle"
            tabIndex={-1}
            aria-label="モデル候補を開く"
            aria-expanded={open}
            aria-controls={listboxId}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setOpen((o) => !o);
              highlightRef.current = -1;
              setActiveIdx(-1);
              inputRef.current?.focus();
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M6 9l6 6 6-6"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        ) : null}
        {showList ? (
          <ul className="mm-ollama-combobox__list" id={listboxId} role="listbox">
            {filtered.map((m, idx) => (
              <li
                key={m}
                role="option"
                aria-selected={idx === activeIdx}
                className={`mm-ollama-combobox__option${idx === activeIdx ? " mm-ollama-combobox__option--active" : ""}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onChange(m);
                  setOpen(false);
                  highlightRef.current = -1;
                  setActiveIdx(-1);
                }}
                onMouseEnter={() => setHighlight(idx)}
              >
                {m}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {!loading && candidates.length === 0 ? (
        <p className="muted" style={{ fontSize: "0.78rem", margin: "0.25rem 0 0", lineHeight: 1.45 }}>
          一覧を取得できませんでした。Ollama が起動しているか、API コンテナの <code>OLLAMA_BASE_URL</code>（Docker では <code>llm-net</code> 経由）を確認してください。手入力は可能です。
        </p>
      ) : null}
    </>
  );
}

function UploadDropIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      width="40"
      height="40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path
        d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function AppMain({
  showLogout,
  serverOpenaiMode,
  openaiFeatureEnabled,
  emailNotifyAvailable,
  authNonce,
  onLogout,
}: {
  showLogout: boolean;
  serverOpenaiMode: boolean;
  /** MM_OPENAI_ENABLED がオフのとき false（OpenAI UI・API を使わない） */
  openaiFeatureEnabled: boolean;
  emailNotifyAvailable: boolean;
  authNonce: number;
  onLogout: () => void;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"general" | "admin">("general");
  const [authMe, setAuthMe] = useState<AuthMe | null>(null);
  const [version, setVersion] = useState("");
  const [presets, setPresets] = useState<Record<string, { label: string }>>({});
  const [presetId, setPresetId] = useState("standard");
  const [records, setRecords] = useState<RecordRow[]>([]);
  const [queue, setQueue] = useState<RecordRow[]>([]);
  const [pendingIds, setPendingIds] = useState<string[]>(loadPending);
  const errorNotifiedRef = useRef(new Set<string>());
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const [topic, setTopic] = useState("");
  const [meetingDate, setMeetingDate] = useState("");
  const [category, setCategory] = useState("未分類");
  const [tags, setTags] = useState("");
  const [purpose, setPurpose] = useState("");
  const [participants, setParticipants] = useState("");
  const [glossary, setGlossary] = useState("");
  const [tone, setTone] = useState("（指定なし）");
  const [actionRules, setActionRules] = useState("");

  const [llmProvider, setLlmProvider] = useState<"ollama" | "openai">("ollama");
  const [ollamaModel, setOllamaModel] = useState("qwen2.5:7b");
  const [ollamaTagList, setOllamaTagList] = useState<string[]>([]);
  const [ollamaTagsLoading, setOllamaTagsLoading] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [openaiModel, setOpenaiModel] = useState("gpt-4o-mini");
  const [openaiConfigured, setOpenaiConfigured] = useState(false);
  const [profileOpenaiModel, setProfileOpenaiModel] = useState("gpt-4o-mini");
  const [openaiKeyDraft, setOpenaiKeyDraft] = useState("");
  const [llmProfileMsg, setLlmProfileMsg] = useState<string | null>(null);
  const [llmProfileErr, setLlmProfileErr] = useState<string | null>(null);

  const [notification, setNotification] = useState<"browser" | "webhook" | "email" | "none">("browser");
  const [email, setEmail] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");

  const [file, setFile] = useState<File | null>(null);
  const [promptExtract, setPromptExtract] = useState<File | null>(null);
  const [promptMerge, setPromptMerge] = useState<File | null>(null);
  const [fileDropActive, setFileDropActive] = useState(false);
  const fileDragDepth = useRef(0);
  const mainFileInputRef = useRef<HTMLInputElement>(null);
  const pendingIdsRef = useRef<string[]>(pendingIds);
  /** 破棄・pending 追加のたびに増やし、古い tick の setPendingIds を無効化する */
  const pendingPollRevisionRef = useRef(0);

  const onTaskFileDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  const onTaskFileDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const types = Array.from(e.dataTransfer.types ?? []);
    if (!types.includes("Files")) return;
    fileDragDepth.current += 1;
    setFileDropActive(true);
  }, []);

  const onTaskFileDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    fileDragDepth.current -= 1;
    if (fileDragDepth.current <= 0) {
      fileDragDepth.current = 0;
      setFileDropActive(false);
    }
  }, []);

  const onTaskFileDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    fileDragDepth.current = 0;
    setFileDropActive(false);
    const chosen = pickTaskMediaFile(e.dataTransfer.files);
    if (chosen) {
      setFile(chosen);
      setErr(null);
    } else if (e.dataTransfer.files.length > 0) {
      setErr("対応していない形式です。.mp4 / .mp3 / .m4a / .wav / .txt / .srt をドロップしてください。");
    }
  }, []);

  const [search, setSearch] = useState("");
  const [filterCat, setFilterCat] = useState("（すべて）");
  const [filterStatus, setFilterStatus] = useState("（すべて）");

  const refreshRecords = useCallback(async () => {
    const cat = filterCat === "（すべて）" ? "" : filterCat;
    const st =
      filterStatus === "（すべて）"
        ? ""
        : filterStatus === "完了"
          ? "completed"
          : filterStatus === "エラー"
            ? "error"
            : filterStatus === "破棄"
              ? "cancelled"
              : "processing";
    const rows = await listRecords({ days: 7, search, category: cat, status_filter: st });
    setRecords(rows);
  }, [search, filterCat, filterStatus]);

  const refreshQueue = useCallback(async () => {
    setQueue(await getQueue());
  }, []);

  useEffect(() => {
    if (!serverOpenaiMode) {
      setAuthMe(null);
      return;
    }
    getAuthMe()
      .then(setAuthMe)
      .catch(() => setAuthMe(null));
  }, [serverOpenaiMode, authNonce]);

  useEffect(() => {
    if (!openaiFeatureEnabled && llmProvider === "openai") {
      setLlmProvider("ollama");
    }
  }, [openaiFeatureEnabled, llmProvider]);

  useEffect(() => {
    if (!serverOpenaiMode || !openaiFeatureEnabled) return;
    getMeLlm()
      .then((m) => {
        if (m.openai_feature_enabled === false) return;
        setOpenaiConfigured(m.openai_configured);
        setProfileOpenaiModel(m.openai_model || "gpt-4o-mini");
      })
      .catch(() => {});
  }, [serverOpenaiMode, openaiFeatureEnabled, authNonce]);

  useEffect(() => {
    getVersion().then((v) => setVersion(v.version)).catch(() => setVersion("?"));
    getPresets()
      .then((p) => {
        setPresets(p);
        if (!p[presetId]) {
          const first = Object.keys(p).sort((a, b) => (a === "standard" ? -1 : b === "standard" ? 1 : a.localeCompare(b)))[0];
          if (first) setPresetId(first);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    setOllamaTagsLoading(true);
    getOllamaModels()
      .then((names) => {
        if (!cancelled) setOllamaTagList(names);
      })
      .catch(() => {
        if (!cancelled) setOllamaTagList([]);
      })
      .finally(() => {
        if (!cancelled) setOllamaTagsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authNonce]);

  const ollamaCandidates = useMemo(() => {
    const uniq = new Set<string>(ollamaTagList);
    const cur = ollamaModel.trim();
    if (cur) uniq.add(cur);
    return Array.from(uniq).sort((a, b) => a.localeCompare(b));
  }, [ollamaTagList, ollamaModel]);

  useEffect(() => {
    refreshRecords().catch((e) => setErr(String(e)));
    refreshQueue().catch(() => {});
  }, [refreshRecords, refreshQueue]);

  useEffect(() => {
    savePending(pendingIds);
  }, [pendingIds]);

  useEffect(() => {
    pendingIdsRef.current = pendingIds;
  }, [pendingIds]);

  const hasPendingBrowserPoll = pendingIds.length > 0;

  useEffect(() => {
    if (!hasPendingBrowserPoll) return;
    const tick = async () => {
      try {
        const idsSnapshot = [...pendingIdsRef.current];
        if (idsSnapshot.length === 0) return;
        const revAtStart = pendingPollRevisionRef.current;
        const stillActive = new Set<string>();
        for (const id of idsSnapshot) {
          const r = await getRecord(id);
          const st = r.status != null ? String(r.status) : "";
          const sum = r.summary != null ? String(r.summary) : "";
          if (st === "completed") {
            errorNotifiedRef.current.delete(id);
            if (Notification.permission === "granted") {
              new Notification("議事録ができました", { body: r.filename || "" });
            }
            continue;
          }
          if (st.startsWith("Error")) {
            if (!errorNotifiedRef.current.has(id)) {
              errorNotifiedRef.current.add(id);
              const errBody = st.replace(/^Error:\s*/i, "").trim();
              if (Notification.permission === "granted") {
                new Notification("議事録処理でエラーが発生しました（ジョブを破棄します）", {
                  body: clipForDesktopNotification(errBody || r.filename || ""),
                });
              } else {
                setMsg(`議事録処理でエラーが発生しました（破棄処理中）: ${errBody || r.filename || ""}`);
              }
            }
            try {
              await discardRecord(id);
              errorNotifiedRef.current.delete(id);
            } catch {
              stillActive.add(id);
            }
            continue;
          }
          if (st === "cancelled") {
            if (sum.trimStart().startsWith(TASK_ERROR_SUMMARY_PREFIX)) {
              if (!errorNotifiedRef.current.has(id)) {
                errorNotifiedRef.current.add(id);
                const body = taskErrorDetailFromSummary(sum);
                if (Notification.permission === "granted") {
                  new Notification("議事録処理でエラーが発生しました（ジョブは破棄されました）", {
                    body: clipForDesktopNotification(body || r.filename || ""),
                  });
                } else {
                  setMsg(`議事録処理でエラー（破棄済み）: ${body || r.filename || ""}`);
                }
              }
            }
            errorNotifiedRef.current.delete(id);
            continue;
          }
          stillActive.add(id);
        }
        if (revAtStart !== pendingPollRevisionRef.current) return;
        setPendingIds((prev) => {
          if (revAtStart !== pendingPollRevisionRef.current) return prev;
          const out: string[] = [];
          for (const id of prev) {
            if (idsSnapshot.includes(id)) {
              if (stillActive.has(id)) out.push(id);
            } else {
              out.push(id);
            }
          }
          return out;
        });
        await refreshRecords();
        await refreshQueue();
      } catch {
        /* ignore */
      }
    };
    const h = window.setInterval(tick, 10_000);
    void tick();
    return () => window.clearInterval(h);
  }, [hasPendingBrowserPoll, refreshRecords, refreshQueue]);

  const presetEntries = useMemo(() => {
    return Object.entries(presets).sort(([a], [b]) => {
      if (a === "standard") return -1;
      if (b === "standard") return 1;
      return a.localeCompare(b);
    });
  }, [presets]);

  const emailRecipientOk =
    notification !== "email" ||
    (emailNotifyAvailable &&
      (!serverOpenaiMode
        ? email.trim().length > 0
        : email.trim().length > 0 || !!authMe?.email));

  const effectiveLlmProvider = openaiFeatureEnabled ? llmProvider : "ollama";

  const canSubmit =
    !!file &&
    (notification !== "webhook" || email.trim().length > 0) &&
    emailRecipientOk &&
    (effectiveLlmProvider !== "openai" ||
      (serverOpenaiMode ? openaiConfigured : openaiKey.trim().length > 0));

  const closeSettings = useCallback(() => {
    setSettingsOpen(false);
    setSettingsTab("general");
  }, []);

  const handleDiscardTask = useCallback(
    async (id: string) => {
      if (!window.confirm("このジョブの処理を破棄しますか？\n投入した原稿ファイルはサーバから削除されます。")) return;
      try {
        await discardRecord(id);
        pendingPollRevisionRef.current += 1;
        setPendingIds((p) => p.filter((x) => x !== id));
        await refreshQueue();
        await refreshRecords();
      } catch (e) {
        alert(String(e));
      }
    },
    [refreshQueue, refreshRecords],
  );

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    if (!file) return;
    const toneVal = tone.startsWith("（") ? "" : tone;
    const meta: TaskSubmitMetadata = {
      email: email.trim(),
      webhook_url: webhookUrl.trim() || null,
      notification_type: notification,
      llm_provider: effectiveLlmProvider,
      ollama_model: ollamaModel.trim(),
      openai_api_key:
        serverOpenaiMode || effectiveLlmProvider !== "openai" ? null : openaiKey.trim() || null,
      openai_model: serverOpenaiMode ? profileOpenaiModel : openaiModel,
      topic: topic.trim(),
      meeting_date: meetingDate.trim(),
      category,
      tags: tags.trim(),
      preset_id: presetId,
      context: {
        purpose: purpose.trim(),
        participants: participants.trim(),
        glossary: glossary.trim(),
        tone: toneVal,
        action_rules: actionRules.trim(),
      },
    };
    const fd = new FormData();
    fd.append("metadata", JSON.stringify(meta));
    fd.append("file", file);
    if (promptExtract) fd.append("prompt_extract", promptExtract);
    if (promptMerge) fd.append("prompt_merge", promptMerge);
    try {
      const res = await createTask(fd);
      if (notification === "browser") {
        pendingPollRevisionRef.current += 1;
        setPendingIds((p) => [...p, res.task_id]);
      }
      setMsg("受け付けました。処理が始まるまで少しお待ちください。");
      setFile(null);
      const fin = mainFileInputRef.current;
      if (fin) fin.value = "";
    } catch (ex) {
      setErr(String(ex));
    }
  };

  return (
    <div className="layout layout--app">
      <header className="hero hero--compact">
        <div className="hero-top">
          <div className="hero-top-actions">
            <AccountMenuDropdown
              items={[
                {
                  key: "settings",
                  label: "設定",
                  onClick: () => {
                    setSettingsTab("general");
                    setSettingsOpen(true);
                  },
                },
                ...(authMe?.is_admin && serverOpenaiMode
                  ? [
                      {
                        key: "admin",
                        label: "ユーザー・権限管理",
                        onClick: () => {
                          setSettingsTab("admin");
                          setSettingsOpen(true);
                        },
                      },
                    ]
                  : []),
                showLogout
                  ? {
                      key: "signout",
                      label: "サインアウト",
                      danger: true,
                      onClick: () => {
                        closeSettings();
                        onLogout();
                      },
                    }
                  : {
                      key: "signin",
                      label: "サインイン",
                      onClick: () => {
                        setSettingsTab("general");
                        setSettingsOpen(true);
                      },
                    },
              ]}
            />
          </div>
        </div>
        <h1>AI 議事録アーカイブ</h1>
        <p className="muted hero--tagline">
          左パネルで会議情報・LLM・通知などを入力。右の上段の枠をクリックするかドラッグ＆ドロップでファイルを選び「解析をキューに追加」。その下に処理キューとアーカイブ（アーカイブ一覧は画面下の広い領域に表示され、内部でスクロール。狭い画面は上から縦並び）。
        </p>
      </header>

      <div className="layout-columns">
      <aside className="sidebar sidebar--scroll">
        <form id="mm-task-form" onSubmit={onSubmit}>
          <h3>会議情報</h3>
          <label>議題（任意）</label>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="例: 四半期レビュー" />
          <label>開催日・目安（任意）</label>
          <input value={meetingDate} onChange={(e) => setMeetingDate(e.target.value)} placeholder="2025-03-20" />
          <label>分類</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {["未分類", "社内", "顧客・社外", "その他"].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <label>タグ（任意）</label>
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="カンマ区切り" />
          <label>会議タイププリセット</label>
          <select value={presetId} onChange={(e) => setPresetId(e.target.value)}>
            {presetEntries.map(([id, p]) => (
              <option key={id} value={id}>
                {p.label} ({id})
              </option>
            ))}
          </select>

          <details>
            <summary>精度向上用コンテキスト</summary>
            <label>会議の目的</label>
            <textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} />
            <label>参加者・役割</label>
            <textarea value={participants} onChange={(e) => setParticipants(e.target.value)} />
            <label>用語・固有名詞</label>
            <textarea value={glossary} onChange={(e) => setGlossary(e.target.value)} />
            <label>文体・トーン</label>
            <select value={tone} onChange={(e) => setTone(e.target.value)}>
              {["（指定なし）", "敬体（です・ます）", "常体（である調）", "口語を残しつつ読みやすく"].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <label>アクション記載ルール</label>
            <textarea value={actionRules} onChange={(e) => setActionRules(e.target.value)} />
          </details>

          <h3>解析設定</h3>
          {serverOpenaiMode && openaiFeatureEnabled ? (
            <p className="muted" style={{ fontSize: "0.85rem", lineHeight: 1.55 }}>
              OpenAI を使う場合は右上の<strong>アカウントアイコン</strong>からメニューを開き、<strong>設定</strong>から
              API キーを登録してください。モデル: <code>{profileOpenaiModel}</code>
              {!openaiConfigured ? "（未登録のため OpenAI での投入はできません）" : null}
            </p>
          ) : null}
          {openaiFeatureEnabled ? (
            <>
              <label>AI の接続先</label>
              <select
                value={llmProvider}
                onChange={(e) => setLlmProvider(e.target.value as "ollama" | "openai")}
              >
                <option value="ollama">ローカル（Ollama）</option>
                <option value="openai">OpenAI API</option>
              </select>
              {llmProvider === "openai" ? (
                serverOpenaiMode ? (
                  <p className="muted" style={{ fontSize: "0.88rem" }}>
                    上の「OpenAI アカウント」でキーを登録してください。使用モデル: <code>{profileOpenaiModel}</code>
                    {!openaiConfigured ? "（未登録のため投入できません）" : null}
                  </p>
                ) : (
                  <>
                    <label>OpenAI API キー</label>
                    <input type="password" value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)} />
                    <label>OpenAI モデル</label>
                    <select value={openaiModel} onChange={(e) => setOpenaiModel(e.target.value)}>
                      {["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o4-mini", "o3-mini"].map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </>
                )
              ) : (
                <OllamaModelField
                  value={ollamaModel}
                  onChange={setOllamaModel}
                  candidates={ollamaCandidates}
                  loading={ollamaTagsLoading}
                />
              )}
            </>
          ) : (
            <>
              <p className="muted" style={{ fontSize: "0.85rem", lineHeight: 1.55, margin: "0 0 0.65rem" }}>
                OpenAI / ChatGPT は<strong>オフ</strong>です（API キー入力・登録はありません）。解析は{" "}
                <strong>Ollama のモデル名</strong>で行います。再有効化: サーバに <code>MM_OPENAI_ENABLED=1</code> を設定して再起動。
              </p>
              <label>AI の接続先</label>
              <select value="ollama" disabled aria-label="現在はローカル（Ollama）のみ利用可能です">
                <option value="ollama">ローカル（Ollama）</option>
              </select>
              <OllamaModelField
                value={ollamaModel}
                onChange={setOllamaModel}
                candidates={ollamaCandidates}
                loading={ollamaTagsLoading}
              />
            </>
          )}

          <h3>通知</h3>
          <select
            value={notification}
            onChange={(e) =>
              setNotification(e.target.value as "browser" | "webhook" | "email" | "none")
            }
          >
            <option value="browser">ブラウザ</option>
            <option value="webhook">Webhook</option>
            <option value="email" disabled={!emailNotifyAvailable}>
              メール（SMTP 設定時）
            </option>
            <option value="none">なし</option>
          </select>
          {!emailNotifyAvailable ? (
            <p className="muted" style={{ fontSize: "0.85rem", margin: "0.25rem 0 0" }}>
              メール通知を使うには、サーバに <code>MM_SMTP_HOST</code> と <code>MM_SMTP_FROM</code> などを設定し、API・ワーカーを再起動してください。
            </p>
          ) : null}
          {notification === "webhook" ? (
            <>
              <label>メール（必須）</label>
              <input value={email} onChange={(e) => setEmail(e.target.value)} />
              <label>Webhook URL（任意）</label>
              <input value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} />
            </>
          ) : null}
          {notification === "email" ? (
            <>
              {serverOpenaiMode && authMe?.email ? (
                <p className="muted" style={{ fontSize: "0.88rem", margin: "0.35rem 0" }}>
                  既定の通知先: <code>{authMe.email}</code>
                  （別アドレスへ送る場合のみ下に入力）
                </p>
              ) : null}
              <label>
                {serverOpenaiMode && authMe?.email ? "別の通知先メール（任意）" : "通知先メール（必須）"}
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={serverOpenaiMode && authMe?.email ? authMe.email : "name@example.com"}
                autoComplete="email"
              />
            </>
          ) : null}

          <details>
            <summary>カスタムプロンプト（任意）</summary>
            <label>抽出 .txt</label>
            <input type="file" accept=".txt" onChange={(e) => setPromptExtract(e.target.files?.[0] ?? null)} />
            <label>統合 .txt</label>
            <input type="file" accept=".txt" onChange={(e) => setPromptMerge(e.target.files?.[0] ?? null)} />
          </details>

          {msg ? <p className="muted">{msg}</p> : null}
          {err ? <p className="error-box">{err}</p> : null}
        </form>
      </aside>

      <main className="main main--stack">
        <div className="main-pane-top">
          <div
            className={`main-file-picker main-file-picker--upload${fileDropActive ? " main-file-picker--drop-target" : ""}`}
            onDragEnter={onTaskFileDragEnter}
            onDragLeave={onTaskFileDragLeave}
            onDragOver={onTaskFileDragOver}
            onDrop={onTaskFileDrop}
          >
            <div className="main-file-picker__heading">解析するファイル</div>
            <label htmlFor="mm-main-file" className="main-file-drop">
              <input
                ref={mainFileInputRef}
                id="mm-main-file"
                form="mm-task-form"
                type="file"
                className="main-file-input-sr"
                accept=".mp4,.mp3,.m4a,.wav,.txt,.srt"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <UploadDropIcon className="main-file-drop__icon" />
              <span className="main-file-drop__title">クリックしてファイルを選択</span>
              <span className="main-file-drop__sub">またはこの枠内にドラッグ＆ドロップ</span>
              <span className="main-file-drop__formats">対応形式: .mp4 / .mp3 / .m4a / .wav / .txt / .srt</span>
              {file ? (
                <span className="main-file-drop__selected">
                  選択中: <strong>{file.name}</strong>
                </span>
              ) : null}
            </label>
            <div className="main-file-picker__actions">
              <button
                className="btn-primary btn-primary--queue-submit"
                type="submit"
                form="mm-task-form"
                disabled={!canSubmit}
              >
                解析をキューに追加
              </button>
            </div>
          </div>

          <section className="main-queue" aria-label="処理キュー">
            <h2 className="main-subhead">処理キュー</h2>
            <div className="queue">
              {queue.length === 0 ? (
                <p className="muted" style={{ fontSize: "0.85rem", margin: 0 }}>
                  待機・実行中のジョブはありません。
                </p>
              ) : (
                <ul className="queue-list">
                  {queue.map((q) => (
                    <li key={q.id} className="queue-card">
                      <div className="queue-card__head">
                        <strong>{q.topic || "（議題なし）"}</strong>
                        <span className="muted queue-card__file">{q.filename}</span>
                      </div>
                      <div className="queue-card__times muted">
                        受付 {formatDbDateTime(q.created_at)}
                        {q.processing_started_at ? ` · 処理開始 ${formatDbDateTime(q.processing_started_at)}` : ""}
                        {q.processing_finished_at ? ` · 処理終了 ${formatDbDateTime(q.processing_finished_at)}` : ""}
                      </div>
                      <p style={{ margin: "0 0 0.35rem" }}>
                        <button type="button" className="btn-discard btn-discard--compact" onClick={() => handleDiscardTask(q.id)}>
                          処理を破棄
                        </button>
                      </p>
                      <JobProgressPanel status={q.status != null ? String(q.status) : ""} compact />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>

        <section className="main-pane-archive" aria-label="議事録アーカイブ">
          <h2 className="main-subhead">議事録アーカイブ</h2>
          <div className="filters filters--tight">
            <input placeholder="キーワード検索" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select value={filterCat} onChange={(e) => setFilterCat(e.target.value)}>
              {["（すべて）", "未分類", "社内", "顧客・社外", "その他"].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            {["（すべて）", "完了", "エラー", "破棄", "処理中"].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          </div>

          <div className="main-archive-scroll">
            {records.length === 0 ? (
              <p className="muted">該当する記録がありません。</p>
            ) : (
              records.map((r) => (
                <RecordCard
                  key={r.id}
                  row={r}
                  onSaved={refreshRecords}
                  onDiscard={handleDiscardTask}
                  showOpenAiTroubleshooting={openaiFeatureEnabled}
                />
              ))
            )}
          </div>
        </section>
      </main>
      </div>

      <SettingsDrawer
        open={settingsOpen}
        onClose={closeSettings}
        title={
          serverOpenaiMode && authMe?.is_admin && settingsTab === "admin" ? "ユーザー・権限管理" : "設定"
        }
      >
        {serverOpenaiMode && authMe?.is_admin ? (
          <div className="settings-drawer-tabs" role="tablist" aria-label="設定の種別">
            <button
              type="button"
              role="tab"
              aria-selected={settingsTab === "general"}
              className={`settings-drawer-tab${settingsTab === "general" ? " settings-drawer-tab--active" : ""}`}
              onClick={() => setSettingsTab("general")}
            >
              一般
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={settingsTab === "admin"}
              className={`settings-drawer-tab${settingsTab === "admin" ? " settings-drawer-tab--active" : ""}`}
              onClick={() => setSettingsTab("admin")}
            >
              ユーザー・権限
            </button>
          </div>
        ) : null}

        {settingsTab === "general" || !serverOpenaiMode || !authMe?.is_admin ? (
          <>
            {serverOpenaiMode ? (
              <section className="settings-section">
                <h3>アカウント</h3>
                {authMe ? (
                  <p style={{ margin: 0, fontSize: "0.92rem" }}>
                    <code>{authMe.email}</code>
                    {authMe.is_admin ? <span className="muted"> · 管理者</span> : null}
                  </p>
                ) : (
                  <p className="muted">アカウント情報を読み込み中です…</p>
                )}
                {showLogout ? (
                  <p className="muted" style={{ fontSize: "0.82rem", margin: "0.65rem 0 0" }}>
                    ログアウトは右上メニューの「サインアウト」から行えます。
                  </p>
                ) : null}
              </section>
            ) : (
              <section className="settings-section">
                <h3>アカウント</h3>
                <p className="muted" style={{ margin: 0 }}>
                  {openaiFeatureEnabled
                    ? "認証は無効です。OpenAI を使う場合は左のフォームから API キーを入力してください。"
                    : "認証は無効です。解析はローカル（Ollama）のみです。"}
                </p>
              </section>
            )}

            {serverOpenaiMode && openaiFeatureEnabled ? (
              <section className="settings-section">
                <h3>OpenAI</h3>
            <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
              API キーはサーバ（registry DB）に保存され、あなたの議事録ジョブでのみ使われます。
            </p>
            <label>API キー（新規または入れ替え）</label>
            <input
              type="password"
              value={openaiKeyDraft}
              onChange={(e) => setOpenaiKeyDraft(e.target.value)}
              placeholder={openaiConfigured ? "登録済み（変更するときだけ入力）" : "sk-..."}
              autoComplete="off"
            />
            <label>モデル</label>
            <select value={profileOpenaiModel} onChange={(e) => setProfileOpenaiModel(e.target.value)}>
              {["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o4-mini", "o3-mini"].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                void (async () => {
                  setLlmProfileErr(null);
                  setLlmProfileMsg(null);
                  try {
                    const hadKeyInput = openaiKeyDraft.trim().length > 0;
                    const patch: { openai_api_key?: string; openai_model: string } = {
                      openai_model: profileOpenaiModel,
                    };
                    if (hadKeyInput) patch.openai_api_key = openaiKeyDraft.trim();
                    await patchMeLlm(patch);
                    setOpenaiKeyDraft("");
                    const m = await getMeLlm();
                    setOpenaiConfigured(m.openai_configured);
                    setProfileOpenaiModel(m.openai_model);
                    setLlmProfileMsg(
                      hadKeyInput
                        ? "API キーを保存しました。"
                        : m.openai_configured
                          ? "モデル設定を更新しました。"
                          : "モデルを保存しました（API キーは未登録のままです）。",
                    );
                  } catch (ex) {
                    setLlmProfileErr(String(ex));
                  }
                })();
              }}
            >
              OpenAI 設定を保存
            </button>
            {openaiConfigured ? (
              <p style={{ marginTop: "0.5rem" }}>
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => {
                    void (async () => {
                      setLlmProfileErr(null);
                      setLlmProfileMsg(null);
                      try {
                        await patchMeLlm({ openai_api_key: "" });
                        const m = await getMeLlm();
                        setOpenaiConfigured(m.openai_configured);
                        setLlmProfileMsg("API キーを削除しました。");
                      } catch (ex) {
                        setLlmProfileErr(String(ex));
                      }
                    })();
                  }}
                >
                  保存済み API キーを削除
                </button>
              </p>
            ) : null}
            {llmProfileMsg ? <p className="muted" style={{ fontSize: "0.85rem" }}>{llmProfileMsg}</p> : null}
            {llmProfileErr ? <p className="error-box">{llmProfileErr}</p> : null}
              </section>
            ) : null}
          </>
        ) : null}

        {settingsTab === "admin" && serverOpenaiMode && authMe?.is_admin ? (
          <section className="settings-section">
            <AdminUserPanel selfEmail={authMe.email} />
          </section>
        ) : null}
      </SettingsDrawer>

      <footer className="footer footer--app">Meeting Minutes Generator · v{version || "…"}</footer>
    </div>
  );
}
