import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
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
          <ReactMarkdown>{text}</ReactMarkdown>
        ) : null}
      </div>
    );
  } catch {
    return <ReactMarkdown>{text}</ReactMarkdown>;
  }
}

function recordIsActiveJob(status: string): boolean {
  return status === "pending" || status.startsWith("processing");
}

function RecordCard({
  row,
  onSaved,
  onDiscard,
}: {
  row: RecordRow;
  onSaved: () => void | Promise<void>;
  onDiscard?: (id: string) => void | Promise<void>;
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
  const statusSummary = statusStr.startsWith("Error") ? "エラー" : jobStatusShortLabel(statusStr);
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
          <div className="tabs">
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
          {tab === "preview" ? (
            <>
              <MinutesBody text={summary} />
              {summary.trim() && summary !== "None" ? (
                <p style={{ marginTop: "0.65rem" }}>
                  <button
                    type="button"
                    className="btn-link"
                    onClick={() =>
                      void downloadExportMinutes(row.id, `minutes_${exportBasename(row)}.md`).catch((e) => alert(String(e)))
                    }
                  >
                    議事録をダウンロード（.md）
                  </button>
                </p>
              ) : null}
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
      ) : statusStr === "cancelled" ? (
        <div className="cancelled-box">
          <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>
            このジョブは破棄されました。アップロードされた原稿ファイルはサーバから削除されています。
          </p>
        </div>
      ) : statusStr.startsWith("Error") ? (
        <div className="error-box">
          <strong>エラー</strong>
          <div>{statusStr}</div>
          <details style={{ marginTop: "0.5rem" }}>
            <summary>トラブルシューティング</summary>
            <ul className="muted" style={{ fontSize: "0.85rem" }}>
              <li>GPU / CUDA: 動画・音声モードでは Whisper が GPU を使います。</li>
              <li>Ollama: モデルが pull 済みか、`OLLAMA_BASE_URL` を確認してください。</li>
              <li>OpenAI: API キー・上限（429）を確認してください。</li>
              <li>テキスト / SRT: UTF-8 推奨。SRT のタイムコード形式を確認してください。</li>
              <li>カスタムプロンプト: `{"{"}CHUNK_TEXT{"}"}` / `{"{"}EXTRACTED_JSON{"}"}` の有無。</li>
            </ul>
          </details>
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
          <label>ユーザー名</label>
          <input value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" />
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
          代わりに環境変数 <code>MM_BOOTSTRAP_ADMIN_USER</code> / <code>MM_BOOTSTRAP_ADMIN_PASSWORD</code> で初期ユーザーを作ることもできます。
        </p>
      </main>
    </div>
  );
}

function AdminUserPanel({ selfUsername }: { selfUsername: string }) {
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
                <th style={{ textAlign: "left", padding: "0.5rem 0" }}>ユーザー</th>
                <th style={{ textAlign: "left", padding: "0.5rem" }}>管理者権限</th>
                <th style={{ textAlign: "right", padding: "0.5rem 0" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.username} style={{ borderBottom: "1px solid var(--border, #eee)" }}>
                  <td style={{ padding: "0.5rem 0" }}>
                    <code>{r.username}</code>
                    {r.username === selfUsername ? <span className="muted"> （あなた）</span> : null}
                  </td>
                  <td style={{ padding: "0.5rem" }}>{r.is_admin ? "はい" : "—"}</td>
                  <td style={{ padding: "0.5rem 0", textAlign: "right", whiteSpace: "nowrap" }}>
                    {editingPw === r.username ? (
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
                                await adminResetPassword(r.username, pwNew);
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
                        <button type="button" className="btn-link" onClick={() => { setEditingPw(r.username); setPwNew(""); }}>
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
                                await adminSetRole(r.username, !r.is_admin);
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
                          disabled={r.username === selfUsername}
                          onClick={() => {
                            if (!window.confirm(`ユーザー「${r.username}」を削除しますか？`)) return;
                            void (async () => {
                              setErr(null);
                              setMsg(null);
                              try {
                                await adminDeleteUser(r.username);
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
        <label>ユーザー名</label>
        <input value={nu} onChange={(e) => setNu(e.target.value)} autoComplete="off" />
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
                await adminCreateUser({ username: nu.trim(), password: np, is_admin: na });
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
            ? "ログインまたは新規登録で入れます。右上のアイコンからメニューを開き、「ログインフォームへ」でユーザー名欄にフォーカスできます。"
            : "ユーザー名とパスワードを入力してログインしてください。アカウントは管理者が発行します。右上のアイコンからメニューを開き、「ログインフォームへ」でフォームにフォーカスできます。"}
        </p>
      </SettingsDrawer>
      <main className="main auth-form" style={{ maxWidth: 420, margin: "2rem auto" }}>
        <h1>{mode === "login" ? "ログイン" : "新規登録"}</h1>
        <p className="muted">
          {mode === "login"
            ? "アカウントをお持ちの方はログインしてください。"
            : "ユーザー名とパスワードを決めて登録します（一般ユーザー）。パスワードは 8 文字以上です。"}
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
          <label>ユーザー名</label>
          <input
            ref={userRef}
            value={u}
            onChange={(e) => setU(e.target.value)}
            autoComplete={mode === "login" ? "username" : "username"}
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
      .catch(() => setAuthStatus({ auth_required: false, bootstrap_needed: false, self_register_allowed: false }));
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

  return (
    <AppMain
      showLogout={showLogout}
      serverOpenaiMode={authStatus.auth_required}
      authNonce={authNonce}
      onLogout={() => {
        setStoredToken(null);
        setAuthNonce((n) => n + 1);
      }}
    />
  );
}

function AppMain({
  showLogout,
  serverOpenaiMode,
  authNonce,
  onLogout,
}: {
  showLogout: boolean;
  serverOpenaiMode: boolean;
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
  const [openaiKey, setOpenaiKey] = useState("");
  const [openaiModel, setOpenaiModel] = useState("gpt-4o-mini");
  const [openaiConfigured, setOpenaiConfigured] = useState(false);
  const [profileOpenaiModel, setProfileOpenaiModel] = useState("gpt-4o-mini");
  const [openaiKeyDraft, setOpenaiKeyDraft] = useState("");
  const [llmProfileMsg, setLlmProfileMsg] = useState<string | null>(null);
  const [llmProfileErr, setLlmProfileErr] = useState<string | null>(null);

  const [notification, setNotification] = useState<"browser" | "webhook" | "none">("browser");
  const [email, setEmail] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");

  const [file, setFile] = useState<File | null>(null);
  const [promptExtract, setPromptExtract] = useState<File | null>(null);
  const [promptMerge, setPromptMerge] = useState<File | null>(null);

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
    if (!serverOpenaiMode) return;
    getMeLlm()
      .then((m) => {
        setOpenaiConfigured(m.openai_configured);
        setProfileOpenaiModel(m.openai_model || "gpt-4o-mini");
      })
      .catch(() => {});
  }, [serverOpenaiMode, authNonce]);

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
    refreshRecords().catch((e) => setErr(String(e)));
    refreshQueue().catch(() => {});
  }, [refreshRecords, refreshQueue]);

  useEffect(() => {
    savePending(pendingIds);
  }, [pendingIds]);

  useEffect(() => {
    if (pendingIds.length === 0) return;
    const tick = async () => {
      try {
        const next: string[] = [];
        for (const id of pendingIds) {
          const r = await getRecord(id);
          const st = r.status != null ? String(r.status) : "";
          if (st === "completed") {
            if (Notification.permission === "granted") {
              new Notification("議事録ができました", { body: r.filename });
            }
            continue;
          }
          if (st.startsWith("Error")) continue;
          if (st === "cancelled") continue;
          next.push(id);
        }
        setPendingIds(next);
        await refreshRecords();
        await refreshQueue();
      } catch {
        /* ignore */
      }
    };
    const h = window.setInterval(tick, 10_000);
    return () => window.clearInterval(h);
  }, [pendingIds, refreshRecords, refreshQueue]);

  const presetEntries = useMemo(() => {
    return Object.entries(presets).sort(([a], [b]) => {
      if (a === "standard") return -1;
      if (b === "standard") return 1;
      return a.localeCompare(b);
    });
  }, [presets]);

  const canSubmit =
    !!file &&
    (notification !== "webhook" || email.trim().length > 0) &&
    (llmProvider !== "openai" ||
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
      llm_provider: llmProvider,
      ollama_model: ollamaModel.trim(),
      openai_api_key: serverOpenaiMode ? null : openaiKey.trim() || null,
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
        setPendingIds((p) => [...p, res.task_id]);
      }
      setMsg("受け付けました。処理が始まるまで少しお待ちください。");
      setFile(null);
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
          左で会議情報とファイルを指定して投入。右でキューとアーカイブを確認します（一覧は下段のみスクロール）。
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
          {serverOpenaiMode ? (
            <p className="muted" style={{ fontSize: "0.85rem", lineHeight: 1.55 }}>
              OpenAI を使う場合は右上の<strong>アカウントアイコン</strong>からメニューを開き、<strong>設定</strong>から
              API キーを登録してください。モデル: <code>{profileOpenaiModel}</code>
              {!openaiConfigured ? "（未登録のため OpenAI での投入はできません）" : null}
            </p>
          ) : null}
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
            <>
              <label>Ollama モデル名</label>
              <input value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)} />
            </>
          )}

          <h3>通知</h3>
          <select
            value={notification}
            onChange={(e) => setNotification(e.target.value as "browser" | "webhook" | "none")}
          >
            <option value="browser">ブラウザ</option>
            <option value="webhook">Webhook</option>
            <option value="none">なし</option>
          </select>
          {notification === "webhook" ? (
            <>
              <label>メール（必須）</label>
              <input value={email} onChange={(e) => setEmail(e.target.value)} />
              <label>Webhook URL（任意）</label>
              <input value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} />
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
          <div className="main-file-picker main-file-picker--tight">
            <label htmlFor="mm-main-file">解析するファイル</label>
            <div className="main-file-picker__file-row">
              <input
                id="mm-main-file"
                form="mm-task-form"
                type="file"
                accept=".mp4,.mp3,.m4a,.wav,.txt,.srt"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <button
                className="btn-primary btn-primary--queue-submit"
                type="submit"
                form="mm-task-form"
                disabled={!canSubmit}
              >
                解析をキューに追加
              </button>
            </div>
            {file ? (
              <p className="muted main-file-picker__hint">
                選択中: <strong>{file.name}</strong>
              </p>
            ) : (
              <p className="muted main-file-picker__hint">
                .mp4 / .mp3 / .m4a / .wav / .txt / .srt
              </p>
            )}
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
                <RecordCard key={r.id} row={r} onSaved={refreshRecords} onDiscard={handleDiscardTask} />
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
                    <code>{authMe.username}</code>
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
                  認証は無効です。OpenAI を使う場合は左のフォームから API キーを入力してください。
                </p>
              </section>
            )}

            {serverOpenaiMode ? (
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
            <AdminUserPanel selfUsername={authMe.username} />
          </section>
        ) : null}
      </SettingsDrawer>

      <footer className="footer footer--app">Meeting Minutes Generator · v{version || "…"}</footer>
    </div>
  );
}
