import os
from typing import Any, Dict, Optional


def save_uploaded_prompts(task_id: str, extract_bytes: Optional[bytes], merge_bytes: Optional[bytes]) -> Optional[Dict[str, str]]:
    paths: Dict[str, str] = {}
    base = os.path.join("data", "user_prompts", task_id)
    if extract_bytes is not None:
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, "prompt_extract.txt")
        text = extract_bytes.decode("utf-8", errors="replace")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths["extract"] = p
    if merge_bytes is not None:
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, "prompt_merge.txt")
        text = merge_bytes.decode("utf-8", errors="replace")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths["merge"] = p
    return paths if paths else None


def save_supplementary_inputs(
    task_id: str,
    teams_bytes: Optional[bytes],
    notes_bytes: Optional[bytes],
) -> Optional[Dict[str, str]]:
    """Teams 等のトランスクリプト・担当メモを UTF-8 として保存（ワーカーが参照）。"""
    paths: Dict[str, str] = {}
    base = os.path.join("data", "user_prompts", task_id)
    if teams_bytes is not None:
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, "supplementary_teams.txt")
        text = teams_bytes.decode("utf-8", errors="replace")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths["supplementary_teams"] = p
    if notes_bytes is not None:
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, "supplementary_notes.txt")
        text = notes_bytes.decode("utf-8", errors="replace")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths["supplementary_notes"] = p
    return paths if paths else None


def merge_task_prompt_paths(
    task_id: str,
    extract_bytes: Optional[bytes],
    merge_bytes: Optional[bytes],
    teams_bytes: Optional[bytes],
    notes_bytes: Optional[bytes],
) -> Optional[Dict[str, str]]:
    """カスタムプロンプトと参考資料パスを1本の dict にまとめる（空なら None）。"""
    merged: Dict[str, str] = {}
    up = save_uploaded_prompts(task_id, extract_bytes, merge_bytes)
    if up:
        merged.update(up)
    sp = save_supplementary_inputs(task_id, teams_bytes, notes_bytes)
    if sp:
        merged.update(sp)
    return merged if merged else None
