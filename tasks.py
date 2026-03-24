import os
import time
import logging

# Redis 起動待ち（ワーカーが tasks を import するときのみ実行）
time.sleep(10)

from celery_app import celery_app

app = celery_app

from openai import OpenAI
import requests
import json
import re
from faster_whisper import WhisperModel
import torch
import database as db
from moviepy.editor import AudioFileClip, VideoFileClip
import uuid
import shutil

# Ollama Configuration（Compose の worker では OLLAMA_BASE_URL を注入。ホストで Celery する場合は 127.0.0.1）

logger = logging.getLogger(__name__)


def _maybe_send_completion_email(to_addr: str, filename: str, task_id: str) -> None:
    """モジュール先頭で backend を import するとワーカー起動に失敗する環境があるため遅延 import。"""
    try:
        from backend.smtp_notify import send_task_completion_email
    except ImportError as e:
        logger.warning("メール通知をスキップ（backend.smtp_notify を読み込めません）: %s", e)
        return
    send_task_completion_email(to_addr, filename, task_id)


def _notify_task_failure(
    notification_type: str,
    email: str,
    filename: str,
    webhook_url,
    detail: str,
    task_id: str,
) -> None:
    """処理失敗時の webhook / メール（browser はフロントのポーリングで通知）。"""
    if (notification_type or "") in ("", "browser", "none"):
        return
    text_detail = (detail or "").strip()[:1200]
    final_webhook_url = webhook_url if webhook_url else os.getenv("WEBHOOK_URL")
    if notification_type == "webhook" and email and final_webhook_url and final_webhook_url != "YOUR_WEBHOOK_URL_HERE":
        try:
            text = f"❌ **議事録処理に失敗しました（ジョブは破棄されました）**\nファイル: {filename}\n\n{text_detail}"
            requests.post(
                final_webhook_url,
                json={"text": text, "email": email, "filename": filename},
            )
        except Exception:
            pass
    elif notification_type == "email" and email:
        try:
            from backend.smtp_notify import send_task_failure_email
        except ImportError as e:
            logger.warning("失敗メールをスキップ（backend.smtp_notify を読み込めません）: %s", e)
            return
        send_task_failure_email(email, filename, task_id, text_detail)


def _ollama_generate_url():
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    return f"{base}/api/generate"


OLLAMA_URL = _ollama_generate_url()
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Pipeline Config
CHUNK_SEC = 75
CHAR_CHUNK = 6000

_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROMPT_EXTRACT = os.path.join(_APP_ROOT, "prompts", "prompt_extract.txt")
DEFAULT_PROMPT_MERGE = os.path.join(_APP_ROOT, "prompts", "prompt_merge.txt")

_PRESETS_CACHE = None


def _whisper_runtime():
    """faster-whisper の設定。VRAM 不足時は WHISPER_MODEL=small や WHISPER_COMPUTE_TYPE=int8_float16 等を試す。"""
    model = (os.getenv("WHISPER_MODEL") or "medium").strip() or "medium"
    device = (os.getenv("WHISPER_DEVICE") or "cuda").strip() or "cuda"
    compute_type = (os.getenv("WHISPER_COMPUTE_TYPE") or "float16").strip() or "float16"
    return model, device, compute_type


def load_builtin_presets():
    global _PRESETS_CACHE
    if _PRESETS_CACHE is not None:
        return _PRESETS_CACHE
    path = os.path.join(os.path.dirname(__file__), "presets_builtin.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _PRESETS_CACHE = json.load(f)
    except (OSError, json.JSONDecodeError):
        _PRESETS_CACHE = {
            "standard": {"label": "標準", "extract_hint": "", "merge_hint": ""}
        }
    return _PRESETS_CACHE


def _row_str(record, key, default=""):
    if not record:
        return default
    try:
        v = record[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else str(v)


def build_meeting_context_block(record):
    """抽出・統合の両方に前置する会議コンテキスト（ユーザー入力は事実として参照）。"""
    if not record:
        return ""
    ctx = db.parse_context_json(record)
    lines = [
        "# 会議コンテキスト（ユーザー入力）",
        "以下はユーザーが入力した背景情報です。発言ログにない内容は推測・補完しないでください。",
    ]
    topic = _row_str(record, "topic").strip()
    if topic:
        lines.append(f"- 議題: {topic}")
    md = _row_str(record, "meeting_date").strip()
    if md:
        lines.append(f"- 開催日・目安: {md}")
    cat = _row_str(record, "category").strip()
    if cat:
        lines.append(f"- 分類: {cat}")
    tags = _row_str(record, "tags").strip()
    if tags:
        lines.append(f"- タグ: {tags}")

    purpose = (ctx.get("purpose") or "").strip()
    if purpose:
        lines.append(f"- 会議の目的: {purpose}")
    participants = (ctx.get("participants") or "").strip()
    if participants:
        lines.append(f"- 参加者・役割: {participants}")
    glossary = (ctx.get("glossary") or "").strip()
    if glossary:
        lines.append(f"- 用語・固有名詞の表記: {glossary}")
    tone = (ctx.get("tone") or "").strip()
    if tone:
        lines.append(f"- 文体・トーン: {tone}")
    action_rules = (ctx.get("action_rules") or "").strip()
    if action_rules:
        lines.append(f"- アクション記載ルール: {action_rules}")

    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def preset_hints_for_record(record):
    presets = load_builtin_presets()
    pid = _row_str(record, "preset_id").strip() or "standard"
    p = presets.get(pid) or presets.get("standard") or {}
    return (
        (p.get("extract_hint") or "").strip(),
        (p.get("merge_hint") or "").strip(),
    )


def normalize_to_segments(input_data):
    """
    入力を標準的なセグメント辞書のリストに正規化する
    Output format: [{'start': float, 'end': float, 'text': str}, ...]
    """

    def parse_srt_time(t_str):
        h, m, s_ms = t_str.split(":")
        s, ms = s_ms.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    segments = []

    if isinstance(input_data, list):
        for item in input_data:
            if hasattr(item, "start") and hasattr(item, "end") and hasattr(item, "text"):
                segments.append(
                    {
                        "start": float(item.start),
                        "end": float(item.end),
                        "text": item.text.strip(),
                    }
                )
            elif isinstance(item, dict) and "text" in item:
                segments.append(
                    {
                        "start": float(item.get("start", 0.0)),
                        "end": float(item.get("end", 0.0)),
                        "text": item.get("text", "").strip(),
                    }
                )

    elif isinstance(input_data, str):
        srt_pattern = re.compile(
            r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:(?!\n\n).)*)",
            re.DOTALL,
        )
        matches = srt_pattern.findall(input_data.replace("\r\n", "\n"))

        if matches:
            for _, start_str, end_str, text_content in matches:
                segments.append(
                    {
                        "start": parse_srt_time(start_str),
                        "end": parse_srt_time(end_str),
                        "text": text_content.strip().replace("\n", " "),
                    }
                )
        else:
            parts = input_data.split("\n\n")
            for p in parts:
                if p.strip():
                    segments.append({"start": 0.0, "end": 0.0, "text": p.strip()})

    return segments


def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_chunks_from_segments(segments, chunk_sec=CHUNK_SEC, char_chunk=CHAR_CHUNK):
    """タイムスタンプが有効なら時間チャンク、無い（プレーンテキスト等）は文字数チャンク。"""
    if not segments:
        return [], ""

    use_time = any((seg["end"] - seg["start"]) > 0.5 for seg in segments)
    chunks_for_ai = []

    if use_time:
        full_transcript_text = []
        current_chunk_lines = []
        chunk_start_time = None
        for seg in segments:
            start = seg["start"]
            end = seg["end"]
            text = seg["text"]
            if chunk_start_time is None:
                chunk_start_time = start
            ts_str = f"[{format_timestamp(start)}-{format_timestamp(end)}]"
            line = f"{ts_str} {text}"
            current_chunk_lines.append(line)
            full_transcript_text.append(line)
            if (end - chunk_start_time) >= chunk_sec:
                chunks_for_ai.append("\n".join(current_chunk_lines))
                current_chunk_lines = []
                chunk_start_time = None
        if current_chunk_lines:
            chunks_for_ai.append("\n".join(current_chunk_lines))
        raw_transcript = "\n".join(full_transcript_text)
    else:
        big = "\n".join(seg["text"] for seg in segments)
        raw_transcript = big
        if len(big) <= char_chunk:
            chunks_for_ai.append(big)
        else:
            for i in range(0, len(big), char_chunk):
                part = big[i : i + char_chunk]
                chunks_for_ai.append(f"[セクション {i // char_chunk + 1}]\n{part}")

    return chunks_for_ai, raw_transcript


def load_prompt(filename):
    if not os.path.exists(filename):
        return ""
    with open(filename, "r", encoding="utf-8") as f:
        return f.read()


def extract_json_block(text):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = text[start : end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    return None


def call_llm(prompt, config, temperature=0.0, json_mode=False):
    cfg = config or {}
    provider = cfg.get("provider", "ollama")

    if provider == "openai":
        try:
            client = OpenAI(api_key=cfg.get("api_key"))
            response_format = {"type": "json_object"} if json_mode else {"type": "text"}

            model_id = cfg.get("openai_model") or DEFAULT_OPENAI_MODEL
            completion = client.chat.completions.create(
                model=model_id,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant found at the start of the prompt.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                response_format=response_format,
            )
            return completion.choices[0].message.content
        except Exception as e:
            raise Exception(f"OpenAI API Error: {str(e)}")

    else:
        try:
            model_name = cfg.get("ollama_model") or DEFAULT_OLLAMA_MODEL
            res = requests.post(
                OLLAMA_URL,
                json={
                    "model": model_name,
                    "prompt": prompt,
                    "format": "json" if json_mode else None,
                    "stream": False,
                    "options": {"temperature": temperature, "num_ctx": 8192},
                },
                timeout=600,
            )

            if res.status_code == 200:
                return res.json().get("response", "")
            else:
                raise Exception(f"Ollama HTTP {res.status_code}")
        except Exception as e:
            raise Exception(f"Ollama Error: {str(e)}")


def _cleanup_user_prompts(task_id):
    d = os.path.join("data", "user_prompts", task_id)
    try:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def _record_cancelled(task_id: str, owner: str) -> bool:
    row = db.get_record(task_id, owner or "")
    if not row:
        return False
    return (row["status"] or "").strip() == "cancelled"


def _cleanup_after_cancel(task_id, owner, file_path, audio_path=None):
    _cleanup_user_prompts(task_id)
    for p in (file_path, audio_path):
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _assemble_extract_prompt(base_template, record, preset_extract_hint):
    ctx = build_meeting_context_block(record)
    parts = []
    if ctx:
        parts.append(ctx)
    if preset_extract_hint:
        parts.append("# 会議タイプに関する追加指示\n" + preset_extract_hint)
    if parts:
        return "\n\n".join(parts) + "\n\n---\n\n" + base_template
    return base_template


def _assemble_merge_prompt(base_template, record, preset_merge_hint):
    ctx = build_meeting_context_block(record)
    parts = []
    if ctx:
        parts.append(ctx)
    if preset_merge_hint:
        parts.append("# 統合・整形の追加指示\n" + preset_merge_hint)
    if parts:
        return "\n\n".join(parts) + "\n\n---\n\n" + base_template
    return base_template


@app.task
def process_video_task(
    task_id,
    email,
    filename,
    file_path,
    webhook_url=None,
    llm_config=None,
    prompt_paths=None,
    owner_username="",
):
    # notification_type は API が llm_config に同梱（Celery kwargs だと古いワーカーが TypeError になるため）
    _lc = dict(llm_config) if isinstance(llm_config, dict) else {}
    notification_type = _lc.pop("notification_type", "browser")
    llm_config = _lc if _lc else None

    db.purge_expired_minutes(owner_username or "")
    record = db.get_record(task_id, owner_username or "")
    if not record:
        return

    if _record_cancelled(task_id, owner_username or ""):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        _cleanup_user_prompts(task_id)
        return

    ollama_model = (llm_config or {}).get("ollama_model") or DEFAULT_OLLAMA_MODEL
    audio_path = os.path.join("downloads", f"{uuid.uuid4()}.mp3")

    extract_path = prompt_paths.get("extract") if prompt_paths else None
    merge_path = prompt_paths.get("merge") if prompt_paths else None
    prompt_extract = load_prompt(extract_path) if extract_path else load_prompt(DEFAULT_PROMPT_EXTRACT)
    prompt_merge = load_prompt(merge_path) if merge_path else load_prompt(DEFAULT_PROMPT_MERGE)

    preset_ex, preset_mg = preset_hints_for_record(record)
    extract_shell = _assemble_extract_prompt(prompt_extract, record, preset_ex)
    merge_shell = _assemble_merge_prompt(prompt_merge, record, preset_mg)

    ext = os.path.splitext(file_path)[1].lower()
    is_transcript = ext in (".txt", ".srt")
    is_audio_only = ext in (".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma", ".m4b")

    def fail(msg, exc_info=False):
        if _record_cancelled(task_id, owner_username or ""):
            _cleanup_after_cancel(task_id, owner_username, file_path, audio_path)
            return
        err_summary = f"【処理エラー】\n{msg}"
        db.update_record(task_id, owner_username or "", status="cancelled", summary=err_summary)
        _notify_task_failure(notification_type, email, filename, webhook_url, msg, task_id)
        _cleanup_user_prompts(task_id)
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    try:
        if is_transcript:
            if _record_cancelled(task_id, owner_username or ""):
                _cleanup_after_cancel(task_id, owner_username, file_path, audio_path)
                return
            db.update_record(task_id, owner_username or "", status="processing:reading_transcript")
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            segments = normalize_to_segments(content)
            if not segments:
                fail("テキストが空か、読み取れるセグメントがありません")
                return
            chunks_for_ai, raw_transcript = build_chunks_from_segments(segments)
            if not chunks_for_ai:
                fail("チャンクを生成できませんでした")
                return
            db.update_record(task_id, owner_username or "", transcript=raw_transcript)
        else:
            if _record_cancelled(task_id, owner_username or ""):
                _cleanup_after_cancel(task_id, owner_username, file_path, audio_path)
                return
            db.update_record(task_id, owner_username or "", status="processing:extracting_audio")
            if is_audio_only:
                audio_clip = None
                try:
                    audio_clip = AudioFileClip(file_path)
                    audio_clip.write_audiofile(audio_path, logger=None)
                finally:
                    if audio_clip is not None:
                        audio_clip.close()
            else:
                video = None
                try:
                    video = VideoFileClip(file_path)
                    if video.audio is None:
                        fail("動画に音声トラックがありません")
                        return
                    video.audio.write_audiofile(audio_path, logger=None)
                finally:
                    if video is not None:
                        video.close()

            if _record_cancelled(task_id, owner_username or ""):
                _cleanup_after_cancel(task_id, owner_username, file_path, audio_path)
                return
            db.update_record(task_id, owner_username or "", status="processing:transcribing")
            wm, wd, wct = _whisper_runtime()
            logger.info("Whisper: model=%s device=%s compute_type=%s", wm, wd, wct)
            model = WhisperModel(wm, device=wd, compute_type=wct)
            raw_segments, _ = model.transcribe(audio_path)
            segments = normalize_to_segments(list(raw_segments))
            del model
            if wd == "cuda":
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

            chunks_for_ai, raw_transcript = build_chunks_from_segments(segments)
            db.update_record(task_id, owner_username or "", transcript=raw_transcript)
            if not chunks_for_ai:
                fail("文字起こし結果が空でした")
                return

        if not (prompt_extract or "").strip():
            extract_shell = _assemble_extract_prompt("{CHUNK_TEXT}", record, preset_ex)

        extracted_results = []
        extraction_errors = []
        total_chunks = len(chunks_for_ai)

        for i, chunk_text in enumerate(chunks_for_ai):
            if _record_cancelled(task_id, owner_username or ""):
                _cleanup_after_cancel(task_id, owner_username, file_path, audio_path)
                return
            db.update_record(task_id, owner_username or "", status=f"processing:extracting ({i+1}/{total_chunks})")
            prompt = extract_shell.replace("{CHUNK_TEXT}", chunk_text)
            try:
                response_text = call_llm(prompt, llm_config, temperature=0, json_mode=True)
                data = extract_json_block(response_text)
                if data:
                    extracted_results.append(data)
            except Exception as e:
                print(f"Extraction failed for chunk {i}: {e}")
                extraction_errors.append(f"Chunk {i}: {str(e)}")

        if not extracted_results:
            detail = (
                f"## ⚠️ 議事録生成エラー\n\nAIによる抽出に失敗しました。\n\n**考えられる原因:**\n"
                f"1. Ollamaモデル (`{ollama_model}`) が未ダウンロード、または OpenAI キー／モデル指定が不正\n"
                "2. LLM サーバが応答していない、またはタイムアウト\n"
                "3. カスタム抽出プロンプトに `{CHUNK_TEXT}` が含まれていない\n\n**デバッグ情報:**\n"
            )
            if extraction_errors:
                detail += "\n".join(extraction_errors[:5])
            else:
                detail += "No errors captured, but no JSON data was extracted."
            fail(detail)
            return

        if _record_cancelled(task_id, owner_username or ""):
            _cleanup_after_cancel(task_id, owner_username, file_path, audio_path)
            return
        db.update_record(task_id, owner_username or "", status="processing:merging")

        combined_data = {"decisions": [], "issues": [], "items": [], "notes": []}
        for data in extracted_results:
            for key in combined_data.keys():
                combined_data[key].extend(data.get(key, []))

        json_str = json.dumps(combined_data, ensure_ascii=False, indent=2)

        if not (prompt_merge or "").strip():
            final_summary = json_str
        else:
            prompt = merge_shell.replace("{EXTRACTED_JSON}", json_str)
            try:
                final_summary = call_llm(prompt, llm_config, temperature=0.2, json_mode=False)
            except Exception as e:
                final_summary = f"Merge failed (Error: {e})\n\n{json_str}"

        if final_summary.startswith("```markdown"):
            final_summary = final_summary.replace("```markdown", "", 1)
        if final_summary.startswith("```"):
            final_summary = final_summary.replace("```", "", 1)
        if final_summary.endswith("```"):
            final_summary = final_summary[:-3]

        timestamp_pattern = r"[\[\(]?\d{1,2}:\d{2}(:\d{2})?(-\d{1,2}:\d{2}(:\d{2})?)?[\]\)]?"
        final_summary = re.sub(timestamp_pattern, "", final_summary)
        final_summary = final_summary.strip()

        db.update_record(task_id, owner_username or "", status="completed", summary=final_summary)

        final_webhook_url = webhook_url if webhook_url else os.getenv("WEBHOOK_URL")
        if notification_type == "webhook" and email and final_webhook_url and final_webhook_url != "YOUR_WEBHOOK_URL_HERE":
            msg = f"✅ **議事録作成完了**\nファイル: {filename}\n[アーカイブを確認]"
            try:
                requests.post(
                    final_webhook_url,
                    json={"text": msg, "email": email, "filename": filename},
                )
            except Exception:
                pass
        elif notification_type == "email" and email:
            _maybe_send_completion_email(email, filename, task_id)

        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        _cleanup_user_prompts(task_id)

    except Exception as e:
        import traceback

        traceback.print_exc()
        if _record_cancelled(task_id, owner_username or ""):
            _cleanup_after_cancel(task_id, owner_username, file_path, audio_path)
            return
        err_text = str(e)
        err_summary = f"【処理エラー】\n{err_text}"
        db.update_record(task_id, owner_username or "", status="cancelled", summary=err_summary)
        _notify_task_failure(notification_type, email, filename, webhook_url, err_text, task_id)
        _cleanup_user_prompts(task_id)
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
