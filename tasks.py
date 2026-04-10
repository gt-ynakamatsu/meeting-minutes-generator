import gc
import logging
import os
import sys
import time
from typing import Optional

# Redis 起動待ち（ワーカーが tasks を import するときのみ実行）
time.sleep(10)

from celery_app import celery_app

from openai import OpenAI
import requests
import json
import re
from faster_whisper import WhisperModel
import torch
import database as db
import feature_flags
from backend.ollama_client import ollama_generate_url, try_ollama_unload_model
from backend.ollama_model_profiles import resolve_ollama_options
from backend.presets_io import load_presets_dict
from moviepy.editor import AudioFileClip, VideoFileClip
import uuid
import shutil

# Ollama Configuration（Compose の worker では OLLAMA_BASE_URL を注入。ホストで Celery する場合は 127.0.0.1）

logger = logging.getLogger(__name__)

_MSG_TRANSCRIPT_ONLY_DONE = "✅ **書き起こし完了**（議事録なしモード）\nファイル: {filename}\n[アーカイブで書き起こしを確認]"
_MSG_MINUTES_DONE = "✅ **議事録作成完了**\nファイル: {filename}\n[アーカイブを確認]"
_MSG_TASK_FAILED = "❌ **議事録処理に失敗しました（ジョブは破棄されました）**\nファイル: {filename}\n\n{detail}"


def _maybe_send_completion_email(to_addr: str, filename: str, task_id: str) -> None:
    """モジュール先頭で backend を import するとワーカー起動に失敗する環境があるため遅延 import。"""
    if not feature_flags.email_notify_feature_enabled():
        return
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
    final_webhook_url = _final_webhook_url(webhook_url)
    if notification_type == "webhook" and email and final_webhook_url and final_webhook_url != "YOUR_WEBHOOK_URL_HERE":
        try:
            text = _MSG_TASK_FAILED.format(filename=filename, detail=text_detail)
            requests.post(
                final_webhook_url,
                json={"text": text, "email": email, "filename": filename},
            )
        except Exception:
            pass
    elif notification_type == "email" and email and feature_flags.email_notify_feature_enabled():
        try:
            from backend.smtp_notify import send_task_failure_email
        except ImportError as e:
            logger.warning("失敗メールをスキップ（backend.smtp_notify を読み込めません）: %s", e)
            return
        send_task_failure_email(email, filename, task_id, text_detail)


def _final_webhook_url(webhook_url):
    return webhook_url if webhook_url else os.getenv("WEBHOOK_URL")


def _notify_task_completion(
    notification_type: str,
    email: str,
    filename: str,
    webhook_url,
    text: str,
    task_id: str,
) -> None:
    if (notification_type or "") in ("", "browser", "none"):
        return
    final_webhook_url = _final_webhook_url(webhook_url)
    if notification_type == "webhook" and email and final_webhook_url and final_webhook_url != "YOUR_WEBHOOK_URL_HERE":
        try:
            requests.post(
                final_webhook_url,
                json={"text": text, "email": email, "filename": filename},
            )
        except Exception:
            pass
        return
    if notification_type == "email" and email:
        _maybe_send_completion_email(email, filename, task_id)


def _completion_message(kind: str, filename: str) -> str:
    if kind == "transcript_only":
        return _MSG_TRANSCRIPT_ONLY_DONE.format(filename=filename)
    return _MSG_MINUTES_DONE.format(filename=filename)


OLLAMA_URL = ollama_generate_url()
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"


def _ollama_http_timeout(phase=None):
    """Ollama /api/generate 用 requests タイムアウト (connect, read) 秒。read はマージが特に長くなりがち。"""
    if phase == "merge":
        raw_m = (os.getenv("MM_OLLAMA_MERGE_TIMEOUT_SEC") or "").strip()
        if raw_m:
            try:
                read_sec = max(60, int(raw_m))
                return (30, read_sec)
            except ValueError:
                pass
    raw = (os.getenv("MM_OLLAMA_TIMEOUT_SEC") or "600").strip()
    try:
        read_sec = max(60, int(raw))
    except ValueError:
        read_sec = 600
    return (30, read_sec)


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


def _whisper_transcribe_options(preset: str) -> dict:
    """UI のプリセット → faster_whisper.WhisperModel.transcribe に渡す追加引数（balanced はライブラリ既定）。"""
    p = (preset or "").strip().lower() or "accurate"
    if p == "fast":
        return {
            "beam_size": 1,
            "best_of": 1,
            "patience": 1.0,
            "temperature": (0.0, 0.2, 0.4),
        }
    if p == "accurate":
        return {
            "beam_size": 10,
            "best_of": 5,
            "patience": 2.0,
            "temperature": (0.0, 0.2, 0.4, 0.6),
        }
    return {}


def _trim_process_memory() -> None:
    """Python ガベージ回収と（Linux glibc で）ヒープの OS 返却を試みる。MoviePy/ffmpeg・大きなリスト後に有効なことがある。"""
    if (os.getenv("MM_WORKER_TRIM_RAM") or "1").strip().lower() in ("0", "false", "no"):
        return
    gc.collect()
    gc.collect()
    if sys.platform != "linux":
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except OSError:
        pass
    except Exception:
        logger.debug("malloc_trim に失敗（無視）", exc_info=True)


def _release_whisper_gpu_resources(device=None):
    """Whisper（CT2）の GPU と、あわせてプロセス RAM 回収を促す。"""
    _trim_process_memory()
    dev = (device or os.getenv("WHISPER_DEVICE") or "cuda").strip().lower() or "cuda"
    if dev != "cuda":
        return
    try:
        if not torch.cuda.is_available():
            return
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
    except Exception:
        logger.debug("Whisper 後の GPU 解放で例外（無視）", exc_info=True)


def _flush_whisper_before_ollama(device=None):
    """Whisper（faster-whisper / CT2）利用後に呼ぶ。Ollama が同じ GPU に載る前に VRAM を返し切る。

    エラー経路でも問答無用で呼ぶ。device が None のときは環境変数 WHISPER_DEVICE を参照する。
    """
    dev_raw = device if device is not None else (os.getenv("WHISPER_DEVICE") or "cuda")
    dev = (dev_raw or "cuda").strip().lower() or "cuda"
    logger.info("Whisper 用 GPU メモリをフラッシュします（Ollama 前／エラー時）device=%s", dev)
    _release_whisper_gpu_resources(dev)
    gc.collect()
    gc.collect()
    if dev == "cuda" and torch.cuda.is_available():
        try:
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        except Exception:
            logger.debug("Whisper フラッシュの empty_cache で例外（無視）", exc_info=True)
    gc.collect()
    _trim_process_memory()


def load_builtin_presets():
    global _PRESETS_CACHE
    if _PRESETS_CACHE is not None:
        return _PRESETS_CACHE
    _PRESETS_CACHE = load_presets_dict()
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


def call_llm(prompt, config, temperature=0.0, json_mode=False, ollama_phase=None):
    cfg = config or {}
    provider = cfg.get("provider", "ollama")

    if provider == "openai":
        if not feature_flags.openai_feature_enabled():
            raise Exception(
                "OpenAI はこの環境で無効です（MM_OPENAI_ENABLED）。Ollama を選ぶか管理者に連絡してください。"
            )
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
            ollama_options = resolve_ollama_options(
                model_name,
                phase=ollama_phase,
                caller_temperature=temperature,
            )
            res = requests.post(
                OLLAMA_URL,
                json={
                    "model": model_name,
                    "prompt": prompt,
                    "format": "json" if json_mode else None,
                    "stream": False,
                    "options": ollama_options,
                },
                timeout=_ollama_http_timeout(ollama_phase),
            )

            if res.status_code == 200:
                return res.json().get("response", "")
            detail = ""
            try:
                err_j = res.json()
                if isinstance(err_j, dict) and err_j.get("error"):
                    detail = str(err_j["error"]).strip()
            except Exception:
                pass
            if not detail:
                raw = (res.text or "").strip()
                if len(raw) > 1200:
                    raw = raw[:1200] + "…"
                detail = raw
            suffix = f": {detail}" if detail else ""
            raise Exception(f"Ollama HTTP {res.status_code}{suffix}")
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


def _try_ollama_unload_for_config(llm_config, ollama_model_name: str) -> None:
    """エラー・破棄後に Ollama の VRAM を早く返す。OpenAI 利用時は何もしない。"""
    cfg = llm_config if isinstance(llm_config, dict) else {}
    if cfg.get("provider", "ollama") == "openai":
        return
    model = (cfg.get("ollama_model") or ollama_model_name or "").strip() or DEFAULT_OLLAMA_MODEL
    try_ollama_unload_model(model)


def _remove_files(*paths) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def _safe_update_usage_metrics(task_id: str, **kwargs) -> None:
    try:
        db.update_usage_job_metrics(task_id, **kwargs)
    except Exception:
        pass


def _cleanup_after_cancel(task_id, owner, file_path, audio_path=None, llm_config=None, ollama_model=None):
    _cleanup_user_prompts(task_id)
    _remove_files(file_path, audio_path)
    _flush_whisper_before_ollama(None)
    _try_ollama_unload_for_config(llm_config, ollama_model or "")


def _cleanup_if_cancelled(task_id, owner, file_path, audio_path=None, llm_config=None, ollama_model=None) -> bool:
    """キャンセル済みなら後片付けして True を返す。"""
    if not _record_cancelled(task_id, owner or ""):
        return False
    _cleanup_after_cancel(task_id, owner, file_path, audio_path, llm_config, ollama_model)
    return True


def _normalize_task_runtime_config(llm_config):
    """API から渡された llm_config を実行時設定へ正規化する。"""
    lc = dict(llm_config) if isinstance(llm_config, dict) else {}
    notification_type = lc.pop("notification_type", "browser")
    transcript_only = bool(lc.pop("transcript_only", False) or lc.pop("audio_extract_only", False))
    wp_raw = str(lc.pop("whisper_preset", "") or "accurate").strip().lower()
    whisper_preset = wp_raw if wp_raw in ("fast", "balanced", "accurate") else "accurate"
    normalized_llm = lc if lc else None
    return notification_type, transcript_only, whisper_preset, normalized_llm


def _load_prompt_templates(prompt_paths):
    extract_path = prompt_paths.get("extract") if prompt_paths else None
    merge_path = prompt_paths.get("merge") if prompt_paths else None
    prompt_extract = load_prompt(extract_path) if extract_path else load_prompt(DEFAULT_PROMPT_EXTRACT)
    prompt_merge = load_prompt(merge_path) if merge_path else load_prompt(DEFAULT_PROMPT_MERGE)
    return prompt_extract, prompt_merge


def _build_prompt_shells(record, prompt_extract: str, prompt_merge: str):
    preset_ex, preset_mg = preset_hints_for_record(record)
    extract_shell = _assemble_prompt_with_context(
        prompt_extract, record, preset_ex, "# 会議タイプに関する追加指示"
    )
    merge_shell = _assemble_prompt_with_context(
        prompt_merge, record, preset_mg, "# 統合・整形の追加指示"
    )
    return extract_shell, merge_shell, preset_ex


def _assemble_prompt_with_context(base_template, record, preset_hint, hint_heading):
    """会議コンテキストとプリセットヒントをベーステンプレの前に付与する。"""
    ctx = build_meeting_context_block(record)
    parts = []
    if ctx:
        parts.append(ctx)
    if preset_hint:
        parts.append(f"{hint_heading}\n{preset_hint}")
    if parts:
        return "\n\n".join(parts) + "\n\n---\n\n" + base_template
    return base_template


SUPPLEMENTARY_MAX_CHARS = int(os.environ.get("MM_SUPPLEMENTARY_MAX_CHARS", "120000"))


def _strip_webvtt_to_plain(text: str) -> str:
    """WebVTT から発言テキストだけを粗く取り出す（タイムコード・キュー番号を除去）。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        if up == "WEBVTT" or up.startswith("NOTE"):
            continue
        if "-->" in s:
            continue
        if re.match(r"^\d+$", s):
            continue
        out.append(line.rstrip())
    return "\n".join(out)


def _build_supplementary_reference_text(teams_path, notes_path) -> str:
    parts = []
    if teams_path and os.path.isfile(teams_path):
        with open(teams_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        head = raw.lstrip()[:32].upper()
        if str(teams_path).lower().endswith(".vtt") or head.startswith("WEBVTT"):
            raw = _strip_webvtt_to_plain(raw)
        raw = raw.strip()
        if raw:
            parts.append("## Teams 等のトランスクリプト（参考）\n" + raw)
    if notes_path and os.path.isfile(notes_path):
        with open(notes_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read().strip()
        if raw:
            parts.append("## 担当メモ・補足（参考）\n" + raw)
    combined = "\n\n".join(parts)
    if len(combined) > SUPPLEMENTARY_MAX_CHARS:
        combined = combined[:SUPPLEMENTARY_MAX_CHARS] + "\n\n…（以下省略: MM_SUPPLEMENTARY_MAX_CHARS 上限）"
    return combined


def _inject_supplementary_extract(shell: str, sup_fill: str, sup_body: str) -> str:
    if "{SUPPLEMENTARY_REFERENCE}" in shell:
        return shell.replace("{SUPPLEMENTARY_REFERENCE}", sup_fill)
    if not (sup_body or "").strip():
        return shell
    inject = (
        "# --- 参考資料（Teams・メモ。会話ログを主たる根拠とし、固有名の照合に利用） ---\n"
        f"{sup_fill}\n\n"
    )
    if "{CHUNK_TEXT}" in shell:
        return shell.replace("{CHUNK_TEXT}", inject + "{CHUNK_TEXT}", 1)
    return inject + shell


def _media_duration_sec_from_segments(segments) -> float:
    if not segments:
        return 0.0
    try:
        return max(float(s.get("end", 0.0) or 0.0) for s in segments if isinstance(s, dict))
    except (TypeError, ValueError):
        return 0.0


def _inject_supplementary_merge(shell: str, sup_fill: str, sup_body: str) -> str:
    if "{SUPPLEMENTARY_REFERENCE}" in shell:
        return shell.replace("{SUPPLEMENTARY_REFERENCE}", sup_fill)
    if not (sup_body or "").strip():
        return shell
    suffix = (
        "\n\n# 参考資料（Teams・メモ）\n"
        f"{sup_fill}\n"
    )
    if "{EXTRACTED_JSON}" in shell:
        return shell.replace("{EXTRACTED_JSON}", "{EXTRACTED_JSON}" + suffix, 1)
    return shell + suffix


def _finish_transcript_only_task(
    task_id,
    owner_username,
    email,
    filename,
    file_path,
    audio_path,
    webhook_url,
    notification_type,
    llm_config,
    ollama_model,
    usage_metrics: Optional[dict] = None,
):
    """書き起こしのみモード: transcript 保存済みのあとで完了し、投入ファイルを掃除する。"""
    um = dict(usage_metrics or {})
    if os.path.isfile(file_path):
        um.setdefault("input_bytes", os.path.getsize(file_path))
    _safe_update_usage_metrics(
        task_id,
        input_bytes=um.get("input_bytes"),
        media_duration_sec=um.get("media_duration_sec"),
        audio_extract_wall_sec=um.get("audio_extract_wall_sec"),
        whisper_wall_sec=um.get("whisper_wall_sec"),
        transcript_chars=um.get("transcript_chars"),
        extract_llm_sec=um.get("extract_llm_sec"),
        merge_llm_sec=um.get("merge_llm_sec"),
        llm_chunks=um.get("llm_chunks"),
    )
    summary_md = (
        "## 書き起こしのみ完了\n\n"
        f"- 元ファイル: {filename}\n"
        "- **議事録（LLM による抽出・統合）は実行していません。**\n"
        "- アーカイブの「書き起こし」タブ、または書き起こしのエクスポートでテキストを取得できます。\n"
    )
    db.update_record(task_id, owner_username or "", status="completed", summary=summary_md)
    _remove_files(audio_path, file_path)
    _cleanup_user_prompts(task_id)
    _flush_whisper_before_ollama(None)
    _try_ollama_unload_for_config(llm_config, ollama_model)
    msg = _completion_message("transcript_only", filename)
    _notify_task_completion(notification_type, email, filename, webhook_url, msg, task_id)


@celery_app.task
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
    # notification_type / transcript_only は API が llm_config に同梱（古いワーカー互換のため kwargs 不使用）
    notification_type, transcript_only, whisper_preset, llm_config = _normalize_task_runtime_config(
        llm_config
    )

    db.purge_expired_minutes(owner_username or "")
    record = db.get_record(task_id, owner_username or "")
    if not record:
        return

    ollama_model = (llm_config or {}).get("ollama_model") or DEFAULT_OLLAMA_MODEL

    if _record_cancelled(task_id, owner_username or ""):
        _remove_files(file_path)
        _cleanup_user_prompts(task_id)
        _flush_whisper_before_ollama(None)
        return

    audio_path = os.path.join("downloads", f"{uuid.uuid4()}.mp3")

    prompt_extract, prompt_merge = _load_prompt_templates(prompt_paths)
    extract_shell, merge_shell, preset_ex = _build_prompt_shells(record, prompt_extract, prompt_merge)

    ext = os.path.splitext(file_path)[1].lower()
    is_transcript = ext in (".txt", ".srt")
    is_audio_only = ext in (".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma", ".m4b")
    # Whisper 使用時は実際の device（cuda 等）を記録し、fail / 例外で同じデバイスをフラッシュする
    whisper_cuda_device = None
    # 管理者向け利用メトリクス（壁時計・文字数。本文は保存しない）
    media_duration_sec = 0.0
    audio_extract_sec = 0.0
    whisper_sec = 0.0
    extract_llm_sec = 0.0
    merge_llm_sec = 0.0
    raw_transcript = ""

    def fail(msg, exc_info=False):
        _flush_whisper_before_ollama(whisper_cuda_device)
        if _cleanup_if_cancelled(task_id, owner_username, file_path, audio_path, llm_config, ollama_model):
            return
        err_summary = f"【処理エラー】\n{msg}"
        db.update_record(task_id, owner_username or "", status="cancelled", summary=err_summary)
        _notify_task_failure(notification_type, email, filename, webhook_url, msg, task_id)
        _cleanup_user_prompts(task_id)
        _remove_files(audio_path, file_path)
        _try_ollama_unload_for_config(llm_config, ollama_model)

    try:
        if is_transcript:
            if _record_cancelled(task_id, owner_username or ""):
                _cleanup_after_cancel(task_id, owner_username, file_path, audio_path, llm_config, ollama_model)
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
            media_duration_sec = _media_duration_sec_from_segments(segments)
            db.update_record(task_id, owner_username or "", transcript=raw_transcript)
            if transcript_only:
                _finish_transcript_only_task(
                    task_id,
                    owner_username,
                    email,
                    filename,
                    file_path,
                    audio_path,
                    webhook_url,
                    notification_type,
                    llm_config,
                    ollama_model,
                    usage_metrics={
                        "media_duration_sec": media_duration_sec,
                        "audio_extract_wall_sec": 0.0,
                        "whisper_wall_sec": 0.0,
                        "transcript_chars": len(raw_transcript),
                        "extract_llm_sec": 0.0,
                        "merge_llm_sec": 0.0,
                        "llm_chunks": 0,
                    },
                )
                return
        else:
            if _record_cancelled(task_id, owner_username or ""):
                _cleanup_after_cancel(task_id, owner_username, file_path, audio_path, llm_config, ollama_model)
                return
            db.update_record(task_id, owner_username or "", status="processing:extracting_audio")
            ae0 = time.perf_counter()
            if is_audio_only:
                audio_clip = None
                try:
                    audio_clip = AudioFileClip(file_path)
                    media_duration_sec = float(getattr(audio_clip, "duration", None) or 0.0)
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
                    media_duration_sec = float(getattr(video, "duration", None) or 0.0)
                    video.audio.write_audiofile(audio_path, logger=None)
                finally:
                    if video is not None:
                        video.close()
            audio_extract_sec = time.perf_counter() - ae0

            _trim_process_memory()

            if _record_cancelled(task_id, owner_username or ""):
                _cleanup_after_cancel(task_id, owner_username, file_path, audio_path, llm_config, ollama_model)
                return
            db.update_record(task_id, owner_username or "", status="processing:transcribing")
            wm, wd, wct = _whisper_runtime()
            whisper_cuda_device = wd
            tw_kw = _whisper_transcribe_options(whisper_preset)
            logger.info(
                "Whisper: model=%s device=%s compute_type=%s preset=%s transcribe_kw=%s",
                wm,
                wd,
                wct,
                whisper_preset,
                tw_kw if tw_kw else "library_defaults",
            )
            model = None
            segments = None
            tw0 = time.perf_counter()
            try:
                model = WhisperModel(wm, device=wd, compute_type=wct)
                raw_segments, _ = model.transcribe(audio_path, **tw_kw)
                segments = normalize_to_segments(list(raw_segments))
            finally:
                whisper_sec = time.perf_counter() - tw0
                model = None
                _release_whisper_gpu_resources(wd)

            # CT2 の VRAM を返し切ってから Ollama が同じ GPU にロードする（連続利用時の OOM 対策）
            _flush_whisper_before_ollama(wd)

            chunks_for_ai, raw_transcript = build_chunks_from_segments(segments)
            md_seg = _media_duration_sec_from_segments(segments)
            if md_seg > 0:
                media_duration_sec = md_seg
            db.update_record(task_id, owner_username or "", transcript=raw_transcript)
            if not chunks_for_ai:
                fail("文字起こし結果が空でした")
                return
            if transcript_only:
                _finish_transcript_only_task(
                    task_id,
                    owner_username,
                    email,
                    filename,
                    file_path,
                    audio_path,
                    webhook_url,
                    notification_type,
                    llm_config,
                    ollama_model,
                    usage_metrics={
                        "media_duration_sec": media_duration_sec,
                        "audio_extract_wall_sec": audio_extract_sec,
                        "whisper_wall_sec": whisper_sec,
                        "transcript_chars": len(raw_transcript),
                        "extract_llm_sec": 0.0,
                        "merge_llm_sec": 0.0,
                        "llm_chunks": 0,
                    },
                )
                return

        if not (prompt_extract or "").strip():
            extract_shell = _assemble_prompt_with_context(
                "{CHUNK_TEXT}", record, preset_ex, "# 会議タイプに関する追加指示"
            )

        teams_path = (prompt_paths or {}).get("supplementary_teams")
        notes_path = (prompt_paths or {}).get("supplementary_notes")
        sup_body = _build_supplementary_reference_text(teams_path, notes_path)
        sup_fill = (
            sup_body
            if sup_body.strip()
            else "(参考資料はありません。会話ログのみを根拠にしてください。)"
        )
        extract_shell = _inject_supplementary_extract(extract_shell, sup_fill, sup_body)
        merge_shell = _inject_supplementary_merge(merge_shell, sup_fill, sup_body)

        extracted_results = []
        extraction_errors = []
        total_chunks = len(chunks_for_ai)

        for i, chunk_text in enumerate(chunks_for_ai):
            if _cleanup_if_cancelled(task_id, owner_username, file_path, audio_path, llm_config, ollama_model):
                return
            db.update_record(task_id, owner_username or "", status=f"processing:extracting ({i+1}/{total_chunks})")
            prompt = extract_shell.replace("{CHUNK_TEXT}", chunk_text)
            te0 = time.perf_counter()
            try:
                response_text = call_llm(
                    prompt,
                    llm_config,
                    temperature=0,
                    json_mode=True,
                    ollama_phase="extract",
                )
                data = extract_json_block(response_text)
                if data:
                    extracted_results.append(data)
            except Exception as e:
                print(f"Extraction failed for chunk {i}: {e}")
                extraction_errors.append(f"Chunk {i}: {str(e)}")
            finally:
                extract_llm_sec += time.perf_counter() - te0

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

        if _cleanup_if_cancelled(task_id, owner_username, file_path, audio_path, llm_config, ollama_model):
            return
        db.update_record(task_id, owner_username or "", status="processing:merging")

        combined_data = {"decisions": [], "issues": [], "items": [], "notes": []}
        for data in extracted_results:
            for key in combined_data.keys():
                combined_data[key].extend(data.get(key, []))

        json_str = json.dumps(combined_data, ensure_ascii=False, indent=2)

        if not (prompt_merge or "").strip():
            final_summary = json_str
            merge_llm_sec = 0.0
        else:
            prompt = merge_shell.replace("{EXTRACTED_JSON}", json_str)
            tm0 = time.perf_counter()
            try:
                final_summary = call_llm(
                    prompt,
                    llm_config,
                    temperature=0.2,
                    json_mode=False,
                    ollama_phase="merge",
                )
            except Exception as e:
                final_summary = f"Merge failed (Error: {e})\n\n{json_str}"
                _try_ollama_unload_for_config(llm_config, ollama_model)
            finally:
                merge_llm_sec = time.perf_counter() - tm0

        if final_summary.startswith("```markdown"):
            final_summary = final_summary.replace("```markdown", "", 1)
        if final_summary.startswith("```"):
            final_summary = final_summary.replace("```", "", 1)
        if final_summary.endswith("```"):
            final_summary = final_summary[:-3]

        timestamp_pattern = r"[\[\(]?\d{1,2}:\d{2}(:\d{2})?(-\d{1,2}:\d{2}(:\d{2})?)?[\]\)]?"
        final_summary = re.sub(timestamp_pattern, "", final_summary)
        final_summary = final_summary.strip()

        input_b = os.path.getsize(file_path) if os.path.isfile(file_path) else None
        _safe_update_usage_metrics(
            task_id,
            input_bytes=input_b,
            media_duration_sec=media_duration_sec,
            audio_extract_wall_sec=0.0 if is_transcript else audio_extract_sec,
            whisper_wall_sec=whisper_sec,
            transcript_chars=len(raw_transcript or ""),
            extract_llm_sec=extract_llm_sec,
            merge_llm_sec=merge_llm_sec,
            llm_chunks=total_chunks,
        )

        db.update_record(task_id, owner_username or "", status="completed", summary=final_summary)

        msg = _completion_message("minutes", filename)
        _notify_task_completion(notification_type, email, filename, webhook_url, msg, task_id)

        _remove_files(audio_path, file_path)
        _cleanup_user_prompts(task_id)

        # 次ジョブ開始時の GPU / Ollama VRAM 不足を防ぐ（同一ワーカーで連続処理する場合）
        _flush_whisper_before_ollama(whisper_cuda_device)
        _try_ollama_unload_for_config(llm_config, ollama_model)

    except Exception as e:
        import traceback

        _flush_whisper_before_ollama(whisper_cuda_device)
        traceback.print_exc()
        if _cleanup_if_cancelled(task_id, owner_username, file_path, audio_path, llm_config, ollama_model):
            return
        err_text = str(e)
        err_summary = f"【処理エラー】\n{err_text}"
        db.update_record(task_id, owner_username or "", status="cancelled", summary=err_summary)
        _notify_task_failure(notification_type, email, filename, webhook_url, err_text, task_id)
        _cleanup_user_prompts(task_id)
        _remove_files(audio_path, file_path)
        _try_ollama_unload_for_config(llm_config, ollama_model)
