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
