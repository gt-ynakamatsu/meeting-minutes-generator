"""Ollama HTTP クライアント（API のタグ一覧・ワーカーの generate URL）。"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import List

logger = logging.getLogger(__name__)


def ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def ollama_generate_url() -> str:
    return f"{ollama_base_url()}/api/generate"


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
    out = sorted(set(names))
    if not out:
        logger.warning(
            "Ollama /api/tags は成功したが models が空、または name/model を解釈できませんでした (%s)",
            base,
        )
    return out
