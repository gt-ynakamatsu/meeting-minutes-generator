import json
import os
import uuid

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import database as db
import feature_flags
from backend.auth_settings import auth_enabled
from backend.presets_io import preset_options_for_ui
from tasks import process_video_task
from version import __version__

from streamlit_app.constants import LOGO_SVG
from streamlit_app.render import render_error_hints, render_minutes, save_uploaded_prompts
from streamlit_app.styles import inject_ui_styles
from streamlit_app.task_status import progress_for_task_status

st.set_page_config(
    page_title="Meeting Minutes Notebook",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()
inject_ui_styles()

if "pending_tasks" not in st.session_state:
    st.session_state.pending_tasks = []

_OLLAMA_HELP = "例: qwen2.5:7b / llama3.2 など。コンテナ内で pull 済みの名前を指定してください。"

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
        <h1 style="margin:0.35rem 0 0.5rem 0;">Meeting Minutes Notebook</h1>
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

preset_options = preset_options_for_ui()
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
    openai_api_key = None
    openai_model = "gpt-4o-mini"
    ollama_default = "qwen2.5:7b"

    if feature_flags.openai_feature_enabled():
        llm_provider = st.radio(
            "AI の接続先",
            ["ローカル（Ollama）", "OpenAI API"],
            index=0,
            help="社内の Ollama か、クラウドの OpenAI を選べます。",
        )
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
        st.caption("OpenAI / ChatGPT 連携はオフです（`MM_OPENAI_ENABLED`）。Ollama のみ使用します。")
        llm_provider = "ローカル（Ollama）"

    use_ollama = (not feature_flags.openai_feature_enabled()) or llm_provider == "ローカル（Ollama）"
    ollama_model = (
        st.text_input("Ollama モデル名", value=ollama_default, help=_OLLAMA_HELP)
        if use_ollama
        else ollama_default
    )

    _whisper_labels = {
        "高速（所要時間は短め）": "fast",
        "標準（バランス）": "balanced",
        "高精度（精度優先・所要時間は長め）": "accurate",
    }
    _whisper_choice = st.selectbox(
        "音声認識の品質（Whisper）",
        list(_whisper_labels.keys()),
        index=2,
        help=(
            "動画・音声を Whisper で文字起こしするときの探索の強さです。"
            "高精度にすると誤変換が減りやすい一方、GPU／CPU の負荷と待ち時間は大きくなります。"
            " .txt / .srt を直接渡す場合は使われません。"
        ),
        key="whisper_preset_ui",
    )
    whisper_preset = _whisper_labels[_whisper_choice]

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
    st.caption("議事録の補正・固有名の照合に使う参考テキスト（任意）")
    sup_teams = st.file_uploader(
        "参考: Teams 等のトランスクリプト",
        type=["txt", "md", "vtt"],
        key="sup_teams",
        help="タイムコード付き .vtt は簡易的に本文だけ取り出します。",
    )
    sup_notes = st.file_uploader(
        "参考: 担当メモ・.md",
        type=["txt", "md", "vtt"],
        key="sup_notes",
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
    transcript_only = st.checkbox(
        "書き起こしのみ（.txt/.srt は読み取り、動画・音声は Whisper のみ。議事録は作らない）",
        value=False,
        help="LLM（Ollama / OpenAI）は使いません。OpenAI キーは不要です。",
    )
    uploaded_file = st.file_uploader(
        "動画 / 音声 / 文字起こし",
        type=["mp4", "mp3", "m4a", "wav", "txt", "srt"],
        help="テキスト・SRT の場合は Whisper をスキップし、その内容から議事録を作成します。",
    )

    can_submit = uploaded_file is not None
    if notification_type == "Webhook" and not email:
        can_submit = False
    if llm_provider == "OpenAI API" and not openai_api_key and not transcript_only:
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
                transcript_only=transcript_only,
            )

            if auth_enabled():
                prov = "openai" if llm_provider == "OpenAI API" else "ollama"
                model_name = (openai_model if llm_provider == "OpenAI API" else ollama_model) or ""
                db.record_usage_job_submission(
                    task_id,
                    (email or "").strip(),
                    transcript_only,
                    prov,
                    model_name.strip(),
                    whisper_preset=whisper_preset,
                    original_filename=uploaded_file.name,
                    input_bytes=os.path.getsize(path),
                )

            ntype = {"ブラウザ": "browser", "Webhook": "webhook", "なし": "none"}.get(
                notification_type, "browser"
            )
            llm_config = {
                "provider": "openai" if llm_provider == "OpenAI API" else "ollama",
                "api_key": openai_api_key,
                "ollama_model": ollama_model,
                "openai_model": openai_model,
                "notification_type": ntype,
                "transcript_only": transcript_only,
                "whisper_preset": whisper_preset,
            }
            prompt_paths = save_uploaded_prompts(task_id, fmt_extract, fmt_merge, sup_teams, sup_notes)
            process_video_task.delay(
                task_id,
                email,
                uploaded_file.name,
                path,
                webhook_url,
                llm_config,
                prompt_paths,
                "",
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
                progress, status_text = progress_for_task_status(status)
                with st.sidebar:
                    st.caption("進行状況")
                    st.write(f"**{record['filename']}**")
                    st.progress(progress / 100)
                    st.caption(status_text)

    st.session_state.pending_tasks = remaining_tasks

st.subheader("処理キュー（待機・実行中）")
if auth_enabled():
    st.caption("認証あり: 全登録ユーザーの待機・実行中を表示します（共有ワーカーの順番の目安）。")
    queue = db.get_active_queue_records_global(viewer="", days=7, limit=80)
else:
    queue = db.get_active_queue_records()
if not queue:
    st.caption("現在、待機・実行中のジョブはありません。")
else:
    for q in queue:
        meta = q["topic"] or "（議題なし）"
        if auth_enabled() and isinstance(q, dict):
            owner_disp = q.get("job_owner") or q.get("email") or "レガシー/共有"
            st.write(f"- **{meta}** · `{q['filename']}` · `{q['status']}` · {q['created_at']} · *投入者: {owner_disp}*")
        else:
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
                st.caption(
                    "表示用の整形済みテキストをそのまま編集して保存できます（JSON 議事録の場合は JSON のまま編集になります）。"
                )
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
