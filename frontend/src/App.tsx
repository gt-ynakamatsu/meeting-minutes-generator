import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  createTask,
  exportMinutesUrl,
  exportTranscriptUrl,
  getPresets,
  getQueue,
  getRecord,
  getVersion,
  listRecords,
  patchSummary,
  type RecordRow,
  type TaskSubmitMetadata,
} from "./api";

const LS_PENDING = "mm_pending_tasks";

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

function RecordCard({ row, onSaved }: { row: RecordRow; onSaved: () => void }) {
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

  const label = `${row.created_at} · ${(row.topic || "").trim() || "（議題なし）"} · ${row.filename} · ${row.status}`;

  return (
    <details className="card" open={false}>
      <summary>{label}</summary>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        分類: {row.category || "—"} ／ タグ: {row.tags || "—"} ／ プリセット: {row.preset_id || "—"} ／ 日付:{" "}
        {row.meeting_date || "—"}
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

      {row.status === "completed" ? (
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
                  <a href={exportMinutesUrl(row.id)} download>
                    議事録をダウンロード（.md）
                  </a>
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
                <a href={exportTranscriptUrl(row.id)} download>
                  テキストをダウンロード
                </a>
              </p>
            </div>
          ) : null}
        </>
      ) : row.status.startsWith("Error") ? (
        <div className="error-box">
          <strong>エラー</strong>
          <div>{row.status}</div>
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
        <p className="muted">ステータス: {row.status}</p>
      )}
    </details>
  );
}

export default function App() {
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
      filterStatus === "（すべて）" ? "" : filterStatus === "完了" ? "completed" : filterStatus === "エラー" ? "error" : "processing";
    const rows = await listRecords({ days: 7, search, category: cat, status_filter: st });
    setRecords(rows);
  }, [search, filterCat, filterStatus]);

  const refreshQueue = useCallback(async () => {
    setQueue(await getQueue());
  }, []);

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
          if (r.status === "completed") {
            if (Notification.permission === "granted") {
              new Notification("議事録ができました", { body: r.filename });
            }
            continue;
          }
          if (r.status.startsWith("Error")) continue;
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
    (llmProvider !== "openai" || openaiKey.trim().length > 0);

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
      openai_api_key: openaiKey.trim() || null,
      openai_model: openaiModel,
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
    <div className="layout">
      <header className="hero">
        <span className="pill">社内利用</span>
        <span className="pill">フロント / API 分離</span>
        <h1>AI 議事録アーカイブ</h1>
        <p className="muted">
          動画・音声、または文字起こし済みテキスト・SRT から議事録を作成します。左のフォームからキューに投入し、下のアーカイブで結果を確認できます。
        </p>
      </header>

      <aside className="sidebar">
        <form onSubmit={onSubmit}>
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
          <label>AI の接続先</label>
          <select
            value={llmProvider}
            onChange={(e) => setLlmProvider(e.target.value as "ollama" | "openai")}
          >
            <option value="ollama">ローカル（Ollama）</option>
            <option value="openai">OpenAI API</option>
          </select>
          {llmProvider === "openai" ? (
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

          <h3>ファイル</h3>
          <input
            type="file"
            accept=".mp4,.mp3,.m4a,.wav,.txt,.srt"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <details>
            <summary>カスタムプロンプト（任意）</summary>
            <label>抽出 .txt</label>
            <input type="file" accept=".txt" onChange={(e) => setPromptExtract(e.target.files?.[0] ?? null)} />
            <label>統合 .txt</label>
            <input type="file" accept=".txt" onChange={(e) => setPromptMerge(e.target.files?.[0] ?? null)} />
          </details>

          {msg ? <p className="muted">{msg}</p> : null}
          {err ? <p className="error-box">{err}</p> : null}

          <button className="btn-primary" type="submit" disabled={!canSubmit}>
            解析をキューに追加
          </button>
        </form>
      </aside>

      <main className="main">
        <h2>処理キュー</h2>
        <div className="queue">
          {queue.length === 0 ? (
            <p className="muted">待機・実行中のジョブはありません。</p>
          ) : (
            <ul>
              {queue.map((q) => (
                <li key={q.id}>
                  <strong>{q.topic || "（議題なし）"}</strong> · {q.filename} · <code>{q.status}</code>
                </li>
              ))}
            </ul>
          )}
        </div>

        <h2>議事録アーカイブ</h2>
        <div className="filters">
          <input placeholder="キーワード検索" value={search} onChange={(e) => setSearch(e.target.value)} />
          <select value={filterCat} onChange={(e) => setFilterCat(e.target.value)}>
            {["（すべて）", "未分類", "社内", "顧客・社外", "その他"].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            {["（すべて）", "完了", "エラー", "処理中"].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        {records.length === 0 ? (
          <p className="muted">該当する記録がありません。</p>
        ) : (
          records.map((r) => <RecordCard key={r.id} row={r} onSaved={refreshRecords} />)
        )}
      </main>

      <footer className="footer">Meeting Minutes Generator · v{version || "…"}</footer>
    </div>
  );
}
