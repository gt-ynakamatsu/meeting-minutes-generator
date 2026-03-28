"""Ollama モデル名に応じた /api/generate の options 解決。

- 既定は num_ctx=4096（従来どおり）。
- prefix 一致（大文字小文字無視）で最初にマッチしたプロファイルを適用。
- MM_OLLAMA_PROFILES_PATH で JSON を指定すると、その profiles をビルトインより先に評価する
  （より具体的な match を JSON の先頭に書ける）。

無効化: MM_OLLAMA_PROFILES=0 / false / no
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

Phase = Optional[Literal["extract", "merge"]]

# Ollama /api/generate の options でよく使うキー（それ以外は未知でも送らない）
OLLAMA_OPTION_KEYS = frozenset(
    {
        "num_ctx",
        "num_batch",
        "num_gpu",
        "main_gpu",
        "low_vram",
        "num_thread",
        "num_keep",
        "num_predict",
        "top_k",
        "top_p",
        "tfs_z",
        "typical_p",
        "repeat_last_n",
        "repeat_penalty",
        "presence_penalty",
        "frequency_penalty",
        "mirostat",
        "mirostat_eta",
        "mirostat_tau",
        "penalize_newline",
        "stop",
    }
)

DEFAULT_OPTIONS: Dict[str, Any] = {"num_ctx": 4096}

# 控えめな出発点。運用では JSON で上書き・追加を推奨。
BUILTIN_PROFILES: List[Dict[str, Any]] = [
    {
        "match": "qwen",
        "top_p": 0.95,
        "repeat_penalty": 1.05,
    },
    {
        "match": "llama3",
        "top_p": 0.9,
        "repeat_penalty": 1.05,
    },
    {
        "match": "llama",
        "repeat_penalty": 1.05,
    },
    {
        "match": "mistral",
        "repeat_penalty": 1.08,
    },
    {
        "match": "gemma",
        "merge_temperature": 0.15,
        "repeat_penalty": 1.1,
    },
]

_PROFILE_STATE: Dict[str, Any] = {
    "path": None,
    "mtime": None,
    "rows": None,
}


def _profiles_enabled() -> bool:
    v = (os.getenv("MM_OLLAMA_PROFILES") or "1").strip().lower()
    return v not in ("0", "false", "no")


def _parse_profiles_file(path: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("root must be an object")
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("defaults must be an object")
    profiles = raw.get("profiles") or []
    if not isinstance(profiles, list):
        raise ValueError("profiles must be an array")
    return defaults, profiles


def _row_from_defaults(defaults: Dict[str, Any], prof: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(defaults)
    for k, v in prof.items():
        if k == "match":
            continue
        if v is None:
            continue
        out[k] = v
    return out


def _load_combined_rows() -> List[Dict[str, Any]]:
    if not _profiles_enabled():
        return []

    path = (os.getenv("MM_OLLAMA_PROFILES_PATH") or "").strip()
    if not path:
        return list(BUILTIN_PROFILES)

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        logger.warning("MM_OLLAMA_PROFILES_PATH を開けません（ビルトインのみ）: %s", path)
        return list(BUILTIN_PROFILES)

    if (
        _PROFILE_STATE["path"] == path
        and _PROFILE_STATE["mtime"] == mtime
        and _PROFILE_STATE["rows"] is not None
    ):
        return _PROFILE_STATE["rows"]

    try:
        defaults, user_profiles = _parse_profiles_file(path)
        merged_user: List[Dict[str, Any]] = []
        for i, p in enumerate(user_profiles):
            if not isinstance(p, dict):
                logger.warning("profiles[%d] をスキップ（object ではない）", i)
                continue
            if not str(p.get("match") or "").strip():
                logger.warning("profiles[%d] をスキップ（match が空）", i)
                continue
            merged_user.append(_row_from_defaults(defaults, p))
        rows = merged_user + list(BUILTIN_PROFILES)
        _PROFILE_STATE["path"] = path
        _PROFILE_STATE["mtime"] = mtime
        _PROFILE_STATE["rows"] = rows
        logger.info(
            "Ollama モデルプロファイル: %s（ユーザー %d 件 + ビルトイン）",
            path,
            len(merged_user),
        )
        return rows
    except Exception as e:
        logger.warning("Ollama プロファイル JSON の解釈に失敗 (%s): %s", path, e)
        rows = list(BUILTIN_PROFILES)
        _PROFILE_STATE["path"] = path
        _PROFILE_STATE["mtime"] = mtime
        _PROFILE_STATE["rows"] = rows
        return rows


def _first_matching_profile(model_name: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    name = (model_name or "").strip().lower()
    if not name:
        return None
    for prof in rows:
        m = str(prof.get("match") or "").strip().lower()
        if not m:
            continue
        if name.startswith(m):
            return prof
    return None


def resolve_ollama_options(
    model_name: str,
    *,
    phase: Phase = None,
    caller_temperature: float = 0.0,
) -> Dict[str, Any]:
    """tasks.call_llm 用。temperature を含む Ollama options dict を返す。"""
    opts = dict(DEFAULT_OPTIONS)
    rows = _load_combined_rows()
    prof = _first_matching_profile(model_name, rows)

    caller_t = float(caller_temperature)

    if prof:
        for key, val in prof.items():
            if key in ("match", "extract_temperature", "merge_temperature"):
                continue
            if key in OLLAMA_OPTION_KEYS and val is not None:
                opts[key] = val

        if phase == "extract" and prof.get("extract_temperature") is not None:
            caller_t = float(prof["extract_temperature"])
        elif phase == "merge" and prof.get("merge_temperature") is not None:
            caller_t = float(prof["merge_temperature"])
        elif prof.get("temperature") is not None:
            caller_t = float(prof["temperature"])

    opts["temperature"] = caller_t
    return opts
