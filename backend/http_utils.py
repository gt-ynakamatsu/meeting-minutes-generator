"""HTTP レスポンス用の小さなユーティリティ。"""

from typing import Any, Dict
from urllib.parse import quote


def sqlite_row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def content_disposition_attachment(filename: str) -> str:
    """ブラウザが UTF-8 ファイル名を解釈できるよう filename* を付与。"""
    ascii_fallback = "".join(c if 32 <= ord(c) < 127 and c not in '\\"' else "_" for c in filename).strip("_") or "download"
    if len(ascii_fallback) > 180:
        ascii_fallback = ascii_fallback[:180]
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'
