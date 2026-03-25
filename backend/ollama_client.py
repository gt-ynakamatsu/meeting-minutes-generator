"""Ollama HTTP クライアント（API のタグ一覧・ワーカーの generate URL）。"""

import json
import os
import urllib.error
import urllib.request
from typing import List


def ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def ollama_generate_url() -> str:
    return f"{ollama_base_url()}/api/generate"


def fetch_ollama_model_names(timeout_sec: float = 6.0) -> List[str]:
    url = f"{ollama_base_url()}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return []
    except Exception:
        return []
    names: List[str] = []
    for m in data.get("models") or []:
        if not isinstance(m, dict):
            continue
        n = m.get("name")
        if isinstance(n, str) and n.strip():
            names.append(n.strip())
    return sorted(set(names))
