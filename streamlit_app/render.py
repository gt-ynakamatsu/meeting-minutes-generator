import json

import streamlit as st

from backend.storage import save_uploaded_prompts as save_uploaded_prompts_bytes


def render_minutes(text: str) -> None:
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
    ex = extract_file.getvalue() if extract_file is not None else None
    mg = merge_file.getvalue() if merge_file is not None else None
    return save_uploaded_prompts_bytes(task_id, ex, mg)


def render_error_hints(status: str) -> None:
    if not status.startswith("Error"):
        return
    with st.expander("トラブルシューティング（よくある原因）", expanded=False):
        st.markdown(
            """
- **GPU / CUDA**: 動画・音声モードでは Whisper が GPU を使います。`CUDA ... out of memory` のときはワーカーに `WHISPER_MODEL=small` や `WHISPER_COMPUTE_TYPE=int8_float16`（`.env`）を試してください。`nvidia-smi` で空きを確認し、ワーカーログを参照してください。
- **Ollama**: モデルが未 pull のときは `docker exec ollama-server ollama pull <モデル名>` を実行してください。接続先は `OLLAMA_BASE_URL` です。
- **OpenAI**: API キー・モデル名・利用上限（429）を確認してください。
- **テキスト / SRT**: 文字コードは UTF-8 推奨。SRT はタイムコード行の形式が崩れていると読み取れないことがあります。
- **カスタムプロンプト**: 抽出用に `{CHUNK_TEXT}`、統合用に `{EXTRACTED_JSON}` が含まれているか確認してください。
            """
        )
        st.caption(f"生ステータス: {status}")
