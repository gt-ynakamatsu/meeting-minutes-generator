"""環境変数による機能 ON/OFF（API・Celery ワーカー共通）。"""

import os


def openai_feature_enabled() -> bool:
    """
    OpenAI / ChatGPT 連携を使うか。
    MM_OPENAI_ENABLED を 0 / false / no / off のいずれかにすると無効。
    未設定時は True（既存デプロイの後方互換）。
    """
    raw = os.getenv("MM_OPENAI_ENABLED")
    if raw is None:
        return True
    v = raw.strip().lower()
    return v not in ("0", "false", "no", "off", "")
