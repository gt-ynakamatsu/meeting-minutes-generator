import json
import streamlit as st
import database as db
from tasks import process_video_task
import uuid
import os

from streamlit_autorefresh import st_autorefresh
from version import __version__

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_SVG = os.path.join(APP_DIR, "assets", "svg", "logo.svg")
PRESETS_PATH = os.path.join(APP_DIR, "presets_builtin.json")

st.set_page_config(
    page_title="AI議事録アーカイブ",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()


def load_preset_options():
    try:
        with open(PRESETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [("standard", "標準")]
    items = list(data.items())
    items.sort(key=lambda x: (0 if x[0] == "standard" else 1, x[0]))
    return [(k, v.get("label", k)) for k, v in items]


def inject_ui_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&family=Noto+Sans+JP:wght@400;500;600;700&display=swap');
        html, body, [class*="css"] {
            font-family: 'Noto Sans JP', 'DM Sans', system-ui, -apple-system, sans-serif;
        }
        div[data-testid="stAppViewContainer"] {
            background: linear-gradient(165deg, #fbf9f6 0%, #eef4f0 45%, #e8f0eb 100%);
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f7faf8 0%, #eef5f1 100%);
            border-right: 1px solid rgba(27, 67, 50, 0.08);
        }
        .mm-hero {
            background: #ffffff;
            border-radius: 20px;
            padding: 1.75rem 2rem;
            border: 1px solid rgba(27, 67, 50, 0.06);
            box-shadow: 0 12px 40px rgba(27, 67, 50, 0.06);
            margin-bottom: 1.25rem;
        }
        .mm-muted { color: #5c6f64; font-size: 0.95rem; line-height: 1.65; }
        .mm-pill {
            display: inline-block;
            background: #d8f3dc;
            color: #1b4332;
            font-size: 0.78rem;
            font-weight: 600;
            padding: 0.2rem 0.65rem;
            border-radius: 999px;
            margin-right: 0.35rem;
        }
        h1 { letter-spacing: -0.02em; color: #1b4332 !important; }
        h2, h3 { color: #2d6a4f !important; }
        div[data-testid="stExpander"] {
            background: #fff;
            border-radius: 14px !important;
            border: 1px solid rgba(27, 67, 50, 0.08) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_minutes(text):
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError

        if data.get("decisions"):
            st.markdown("##### 決定事項")
            for d in data["decisions"]:
                st.markdown(f"- {d.get('text', '')}", unsafe_allow_html=True)

        if data.get("issues"):
            st.markdown("##### 課題")
            for i in data["issues"]:
                st.markdown(f"- {i.get('text', '')}", unsafe_allow_html=True)

        if data.get("items"):
            st.markdown("##### アクション")
            for i in data["items"]:
                who = f"**{i.get('who', '担当未定')}**"
                due = f"（期限: {i.get('due')}）" if i.get("due") else ""
                st.markdown(f"- [ ] {who}: {i.get('what', '')}{due}", unsafe_allow_html=True)

        if data.get("notes"):
            st.markdown("##### 重要メモ")
            for n in data["notes"]:
                st.markdown(f"- {n.get('text', '')}", unsafe_allow_html=True)

    except (json.JSONDecodeError, ValueError, TypeError):
        if text and text != "None":
            st.markdown(text)
        else:
            st.info("詳細な議事録データが作成されていません。")


def save_uploaded_prompts(task_id, extract_file, merge_file):
    paths = {}
    base = os.path.join("data", "user_prompts", task_id)
    if extract_file is not None:
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, "prompt_extract.txt")
        text = extract_file.getvalue().decode("utf-8", errors="replace")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths["extract"] = p
    if merge_file is not None:
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, "prompt_merge.txt")
        text = merge_file.getvalue().decode("utf-8", errors="replace")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths["merge"] = p
    return paths if paths else None


def render_error_hints(status: str):
    if not status.startswith("Error"):
        return
    with st.expander("トラブルシューティング（よくある原因）", expanded=False):
        st.markdown(
            """
- **GPU / CUDA**: 動画・音声モードでは Whisper が GPU を使います。`nvidia-smi` で空きを確認し、ワーカーログを参照してください。
- **Ollama**: モデルが未 pull のときは `docker exec ollama-server ollama pull <モデル名>` を実行してください。接続先は `OLLAMA_BASE_URL` です。
- **OpenAI**: API キー・モデル名・利用上限（429）を確認してください。
- **テキスト / SRT**: 文字コードは UTF-8 推奨。SRT はタイムコード行の形式が崩れていると読み取れないことがあります。
- **カスタムプロンプト**: 抽出用に `{CHUNK_TEXT}`、統合用に `{EXTRACTED_JSON}` が含まれているか確認してください。
            """
        )
        st.caption(f"生ステータス: {status}")


inject_ui_styles()

if "pending_tasks" not in st.session_state:
    st.session_state.pending_tasks = []

hero_cols = st.columns([1, 5])
with hero_cols[0]:
    if os.path.isfile(LOGO_SVG):
        st.image(LOGO_SVG, width=72)
with hero_cols[1]:
    st.markdown(
        """
        <div class="mm-hero">
        <span class="mm-pill">社内利用</span>
        <span class="mm-pill">GPU 対応</span>
        <h1 style="margin:0.35rem 0 0.5rem 0;">AI 議事録アーカイブ</h1>
        <p class="mm-muted" style="margin:0;">
        会議の動画・音声、または文字起こし済みテキスト・SRT から議事録を作成します。
        左のパネルで会議の背景を入力すると、抽出・統合の精度が上がりやすくなります。
        </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    """
    <script>
    if ("Notification" in window) {
        Notification.requestPermission();
    }
    </script>
    """,
    unsafe_allow_html=True,
)

preset_options = load_preset_options()
preset_ids = [p[0] for p in preset_options]
preset_labels = [p[1] for p in preset_options]

with st.sidebar:
    st.markdown("### 会議情報")
    topic = st.text_input("議題（任意）", placeholder="例: 四半期レビュー", key="meta_topic")
    meeting_date = st.text_input(
        "開催日・目安（任意）",
        placeholder="例: 2025-03-20 / 今週金曜",
        key="meta_date",
    )
    category = st.selectbox(
        "分類",
        ["未分類", "社内", "顧客・社外", "その他"],
        index=0,
        key="meta_cat",
    )
    tags = st.text_input(
        "タグ（任意・カンマ区切り）",
        placeholder="例: プロダクト, キックオフ",
        key="meta_tags",
    )

    preset_idx = st.selectbox(
        "会議タイププリセット",
        range(len(preset_labels)),
        format_func=lambda i: preset_labels[i],
        help="抽出・統合プロンプトの前に、用途別のヒントを差し込みます。",
        key="meta_preset",
    )
    preset_id = preset_ids[preset_idx]

    with st.expander("精度向上用コンテキスト（推奨）", expanded=False):
        purpose = st.text_area(
            "会議の目的・決めたいこと",
            placeholder="例: 次スプリントのスコープとリスクを合意する",
            height=80,
            key="ctx_purpose",
        )
        participants = st.text_area(
            "参加者・役割",
            placeholder="例: 山田（PM）／佐藤（開発）／鈴木（顧客）",
            height=80,
            key="ctx_participants",
        )
        glossary = st.text_area(
            "用語・固有名詞の正しい表記",
            placeholder="例: 製品名 X は「エックス」と表記",
            height=80,
            key="ctx_glossary",
        )
        tone = st.selectbox(
            "文体・トーン",
            ["（指定なし）", "敬体（です・ます）", "常体（である調）", "口語を残しつつ読みやすく"],
            key="ctx_tone",
        )
        action_rules = st.text_area(
            "アクション記載ルール",
            placeholder="例: 期限が不明なときは「未定」と書く／担当不明は null",
            height=72,
            key="ctx_action",
        )

    st.divider()
    st.markdown("### 今回の解析設定")
    llm_provider = st.radio(
        "AI の接続先",
        ["ローカル（Ollama）", "OpenAI API"],
        index=0,
        help="社内の Ollama か、クラウドの OpenAI を選べます。",
    )

    openai_api_key = None
    openai_model = "gpt-4o-mini"
    ollama_model = "qwen2.5:7b"

    if llm_provider == "OpenAI API":
        openai_api_key = st.text_input(
            "OpenAI API キー",
            type="password",
            help="ブラウザに残りません。タスク実行時のみワーカーに渡ります。",
        )
        openai_model = st.selectbox(
            "OpenAI モデル",
            ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o4-mini", "o3-mini"],
            index=0,
        )
    else:
        ollama_model = st.text_input(
            "Ollama モデル名",
            value="qwen2.5:7b",
            help="例: qwen2.5:7b / llama3.2 など。コンテナ内で pull 済みの名前を指定してください。",
        )

    st.divider()
    st.markdown("### 議事録フォーマット（任意）")
    st.caption(
        "社内テンプレに差し替えたいときだけアップロード。"
        " 抽出用は `{CHUNK_TEXT}`、統合用は `{EXTRACTED_JSON}` が必要です。"
    )
    fmt_extract = st.file_uploader(
        "抽出プロンプト（.txt）",
        type=["txt"],
        key="fmt_extract",
    )
    fmt_merge = st.file_uploader(
        "統合・整形プロンプト（.txt）",
        type=["txt"],
        key="fmt_merge",
    )

    st.divider()
    st.markdown("### 通知")
    notification_type = st.radio(
        "完了時の通知",
        ["ブラウザ", "Webhook", "なし"],
        index=0,
        horizontal=False,
    )

    email = None
    webhook_url = None
    if notification_type == "Webhook":
        email = st.text_input("あなたのメールアドレス（必須）", placeholder="name@example.com")
        webhook_url = st.text_input(
            "Webhook URL（任意）",
            placeholder="https://hooks.slack.com/...",
            help="空欄のときは環境変数 WEBHOOK_URL が使われます。",
        )
    elif notification_type == "ブラウザ":
        st.info("ブラウザの通知を許可すると、このタブが開いている間に完了をお知らせします。")

    st.divider()
    st.markdown("### ファイル")
    uploaded_file = st.file_uploader(
        "動画 / 音声 / 文字起こし",
        type=["mp4", "mp3", "m4a", "wav", "txt", "srt"],
        help="テキスト・SRT の場合は Whisper をスキップし、その内容から議事録を作成します。",
    )

    can_submit = uploaded_file is not None
    if notification_type == "Webhook" and not email:
        can_submit = False
    if llm_provider == "OpenAI API" and not openai_api_key:
        can_submit = False

    if st.button("解析をキューに追加", type="primary", disabled=not can_submit, use_container_width=True):
        if uploaded_file:
            task_id = str(uuid.uuid4())
            path = os.path.join("downloads", uploaded_file.name)
            os.makedirs("downloads", exist_ok=True)
            with open(path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            tone_val = None if tone.startswith("（") else tone
            context_obj = {
                "purpose": purpose.strip(),
                "participants": participants.strip(),
                "glossary": glossary.strip(),
                "tone": tone_val or "",
                "action_rules": action_rules.strip(),
            }
            context_json = json.dumps(context_obj, ensure_ascii=False)

            db.save_initial_task(
                task_id,
                email if email else "",
                uploaded_file.name,
                topic=topic.strip(),
                tags=tags.strip(),
                category=category,
                meeting_date=meeting_date.strip(),
                preset_id=preset_id,
                context_json=context_json,
            )

            llm_config = {
                "provider": "openai" if llm_provider == "OpenAI API" else "ollama",
                "api_key": openai_api_key,
                "ollama_model": ollama_model,
                "openai_model": openai_model,
            }
            prompt_paths = save_uploaded_prompts(task_id, fmt_extract, fmt_merge)
            process_video_task.delay(
                task_id,
                email,
                uploaded_file.name,
                path,
                webhook_url,
                llm_config,
                prompt_paths,
            )

            if notification_type == "ブラウザ":
                st.session_state.pending_tasks.append(task_id)

            st.success("受け付けました。処理が始まるまで少しだけお待ちください。")
            st.balloons()

if st.session_state.pending_tasks:
    st_autorefresh(interval=10000, limit=None, key="mm_task_poll")

    remaining_tasks = []
    for tid in st.session_state.pending_tasks:
        record = db.get_record(tid)
        if record:
            status = record["status"]
            if status == "completed":
                st.toast(f"完了しました: {record['filename']}")
                st.markdown(
                    f"""
                    <script>
                    new Notification("議事録ができました", {{
                        body: "{record['filename']}",
                    }});
                    </script>
                    """,
                    unsafe_allow_html=True,
                )
            elif status.startswith("Error"):
                st.error(f"エラー: {record['filename']} — {status}")
            else:
                remaining_tasks.append(tid)

                progress = 0
                status_text = "待機中…"
                if status == "processing":
                    progress = 5
                    status_text = "処理を開始しています…"
                elif status == "processing:reading_transcript":
                    progress = 18
                    status_text = "文字起こし済みテキストを読み込み中…"
                elif status == "processing:extracting_audio":
                    progress = 10
                    status_text = "音声を取り出しています…"
                elif status == "processing:transcribing":
                    progress = 40
                    status_text = "文字起こし中（Whisper）…"
                elif status.startswith("processing:extracting"):
                    progress = 55
                    status_text = "内容を抽出しています…"
                elif status == "processing:merging":
                    progress = 80
                    status_text = "議事録にまとめています…"
                elif status == "processing:summarizing":
                    progress = 80
                    status_text = "要約・整形中…"
                elif status == "processing:sending_notification":
                    progress = 95
                    status_text = "通知を送っています…"

                with st.sidebar:
                    st.caption("進行状況")
                    st.write(f"**{record['filename']}**")
                    st.progress(progress / 100)
                    st.caption(status_text)

    st.session_state.pending_tasks = remaining_tasks

st.subheader("処理キュー（待機・実行中）")
queue = db.get_active_queue_records()
if not queue:
    st.caption("現在、待機・実行中のジョブはありません。")
else:
    for q in queue:
        meta = q["topic"] or "（議題なし）"
        st.write(f"- **{meta}** · `{q['filename']}` · `{q['status']}` · {q['created_at']}")

st.markdown("---")
st.subheader("議事録アーカイブ")

fc1, fc2, fc3 = st.columns([2, 1, 1])
with fc1:
    q_search = st.text_input("キーワード検索", placeholder="議題・ファイル名・タグなど", key="filter_q")
with fc2:
    q_cat = st.selectbox(
        "分類フィルタ",
        ["（すべて）", "未分類", "社内", "顧客・社外", "その他"],
        key="filter_cat",
    )
with fc3:
    q_status = st.selectbox(
        "ステータス",
        ["（すべて）", "完了", "エラー", "処理中"],
        key="filter_status",
    )

cat_param = "" if q_cat == "（すべて）" else q_cat
st_map = {"（すべて）": "", "完了": "completed", "エラー": "error", "処理中": "processing"}
status_param = st_map.get(q_status, "")

records = db.get_recent_records(
    days=7,
    search=q_search.strip(),
    category=cat_param,
    status_filter=status_param,
)

if not records:
    st.caption("該当する記録がありません。条件を変えるか、新規に解析を追加してください。")

for r in records:
    topic_label = (r["topic"] or "").strip() or "（議題なし）"
    label = f"{r['created_at']} · {topic_label} · {r['filename']} · {r['status']}"
    with st.expander(label):
        cmeta1, cmeta2 = st.columns(2)
        with cmeta1:
            st.caption(f"分類: {r['category'] or '—'} ／ タグ: {r['tags'] or '—'}")
        with cmeta2:
            st.caption(f"プリセット: {r['preset_id'] or '—'} ／ 日付: {r['meeting_date'] or '—'}")

        ctx = db.parse_context_json(r)
        if any(ctx.get(k) for k in ("purpose", "participants", "glossary", "tone", "action_rules")):
            with st.expander("入力したコンテキスト", expanded=False):
                if ctx.get("purpose"):
                    st.markdown(f"**目的** {ctx['purpose']}")
                if ctx.get("participants"):
                    st.markdown(f"**参加者** {ctx['participants']}")
                if ctx.get("glossary"):
                    st.markdown(f"**用語** {ctx['glossary']}")
                if ctx.get("tone"):
                    st.markdown(f"**トーン** {ctx['tone']}")
                if ctx.get("action_rules"):
                    st.markdown(f"**アクションルール** {ctx['action_rules']}")

        if r["status"] == "completed":
            summary_text = r["summary"] if r["summary"] and r["summary"] != "None" else ""

            tab_preview, tab_edit, tab_raw = st.tabs(["プレビュー", "手直し・保存", "書き起こし"])
            with tab_preview:
                st.markdown("##### 議事録（AI）")
                render_minutes(summary_text)
                st.download_button(
                    "議事録をダウンロード（.md）",
                    summary_text,
                    file_name=f"minutes_{r['filename']}.md",
                    key=f"dl_sum_{r['id']}",
                    disabled=not summary_text,
                )
            with tab_edit:
                st.caption("表示用の整形済みテキストをそのまま編集して保存できます（JSON 議事録の場合は JSON のまま編集になります）。")
                with st.form(f"edit_summary_{r['id']}"):
                    edited = st.text_area(
                        "議事録テキスト",
                        value=summary_text,
                        height=420,
                        label_visibility="collapsed",
                    )
                    if st.form_submit_button("上書き保存"):
                        db.update_record(r["id"], summary=edited)
                        st.success("保存しました。")
                        st.rerun()
            with tab_raw:
                st.markdown("##### 書き起こし全文")
                tr = r["transcript"] or ""
                st.text_area(
                    "transcript",
                    tr,
                    height=320,
                    key=f"tr_{r['id']}",
                    label_visibility="collapsed",
                )
                st.download_button(
                    "テキストをダウンロード",
                    tr,
                    file_name=f"{r['filename']}.txt",
                    key=f"dl_{r['id']}",
                )
        elif r["status"].startswith("Error"):
            st.error(f"ステータス: {r['status']}")
            render_error_hints(r["status"])
        else:
            st.info(f"ステータス: {r['status']}")

st.caption(f"Meeting Minutes Generator · v{__version__}")
