"""リポジトリ同梱の presets_builtin.json の読み込み（FastAPI / Streamlit 共通）。"""

import json
import os
from typing import Any, Dict, List, Tuple

_FALLBACK: Dict[str, Any] = {"standard": {"label": "標準", "extract_hint": "", "merge_hint": ""}}


def presets_builtin_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "presets_builtin.json")


def load_presets_dict() -> Dict[str, Any]:
    try:
        with open(presets_builtin_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(_FALLBACK)


def preset_options_for_ui() -> List[Tuple[str, str]]:
    """Streamlit の selectbox 用: (preset_id, label) を standard 優先でソート。"""
    data = load_presets_dict()
    items = list(data.items())
    items.sort(key=lambda x: (0 if x[0] == "standard" else 1, x[0]))
    return [(k, str(v.get("label", k))) for k, v in items]
