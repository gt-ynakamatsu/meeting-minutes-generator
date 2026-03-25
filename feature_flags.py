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


def email_notify_feature_enabled() -> bool:
    """
    メール完了通知（UI・POST /api/tasks・ワーカー送信）を有効にするか。
    MM_EMAIL_NOTIFY_ENABLED を 1 / true / yes / on のいずれかにすると有効。
    未設定時は False（当面オフ。復活時は環境変数で再有効化）。
    """
    raw = os.getenv("MM_EMAIL_NOTIFY_ENABLED")
    if raw is None:
        return False
    v = raw.strip().lower()
    return v in ("1", "true", "yes", "on")
