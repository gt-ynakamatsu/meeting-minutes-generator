"""Ollama HTTP クライアント（API のタグ一覧・ワーカーの generate URL）。"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import List, Tuple

logger = logging.getLogger(__name__)

# 議事録用ドロップダウンから除外（埋め込み・RAG 専用など）。名前に部分一致（小文字）。
_DEFAULT_OLLAMA_UI_EXCLUDE_SUBSTR: Tuple[str, ...] = ("nomic-embed-text",)


def _ollama_model_excluded_from_ui(name: str) -> bool:
    lo = (name or "").strip().lower()
    if not lo:
        return True
    for s in _DEFAULT_OLLAMA_UI_EXCLUDE_SUBSTR:
        if s in lo:
            return True
    extra = os.getenv("MM_OLLAMA_UI_EXCLUDE_CONTAINS") or ""
    for part in extra.split(","):
        p = part.strip().lower()
        if p and p in lo:
            return True
    return False


def ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def ollama_generate_url() -> str:
    return f"{ollama_base_url()}/api/generate"


def try_ollama_unload_model(model_name: str, timeout_sec: float = 60.0) -> None:
    """Ollama にモデルを即時アンロードさせる（VRAM 解放）。失敗しても例外は出さない。"""
    if (os.getenv("OLLAMA_UNLOAD_ON_TASK_END") or "1").strip().lower() in ("0", "false", "no"):
        return
    name = (model_name or "").strip()
    if not name:
        return
    url = ollama_generate_url()
    body = json.dumps(
        {"model": name, "prompt": "", "keep_alive": 0, "stream": False},
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        logger.warning(
            "Ollama アンロード要求が HTTP エラー (%s): %s %s",
            name,
            e.code,
            ollama_base_url(),
        )
    except urllib.error.URLError as e:
        logger.warning(
            "Ollama アンロード要求に接続できません (%s, %s): %s",
            name,
            ollama_base_url(),
            e.reason if hasattr(e, "reason") else e,
        )
    except (TimeoutError, OSError) as e:
        logger.warning("Ollama アンロード要求がタイムアウトまたは I/O エラー (%s): %s", name, e)
    except Exception as e:
        logger.warning("Ollama アンロード要求で予期しないエラー (%s): %s", name, e)


def fetch_ollama_model_names(timeout_sec: float = 6.0) -> List[str]:
    base = ollama_base_url()
    url = f"{base}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        logger.warning("Ollama /api/tags が HTTP エラー: %s %s", e.code, base)
        return []
    except urllib.error.URLError as e:
        logger.warning(
            "Ollama /api/tags に接続できません（OLLAMA_BASE_URL=%s が ollama-server と同じ Docker ネットワーク上か確認）: %s",
            base,
            e.reason if hasattr(e, "reason") else e,
        )
        return []
    except (TimeoutError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Ollama /api/tags の応答が不正またはタイムアウト (%s): %s", base, e)
        return []
    except Exception as e:
        logger.warning("Ollama /api/tags 取得で予期しないエラー (%s): %s", base, e)
        return []
    names: List[str] = []
    for m in data.get("models") or []:
        if not isinstance(m, dict):
            continue
        raw = m.get("name") or m.get("model")
        if isinstance(raw, str) and raw.strip():
            names.append(raw.strip())
    out = sorted({n for n in names if not _ollama_model_excluded_from_ui(n)})
    if not names:
        logger.warning(
            "Ollama /api/tags は成功したが models が空、または name/model を解釈できませんでした (%s)",
            base,
        )
    elif not out and names:
        logger.warning(
            "Ollama /api/tags のモデルがすべて UI 除外ルールに該当しました (%s)。"
            " MM_OLLAMA_UI_EXCLUDE_CONTAINS を確認してください。",
            base,
        )
    return out
