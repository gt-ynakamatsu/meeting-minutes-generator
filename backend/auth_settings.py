import os


def auth_secret() -> str:
    return (os.getenv("MM_AUTH_SECRET") or "").strip()


def auth_enabled() -> bool:
    return bool(auth_secret())


def token_ttl_hours() -> int:
    raw = (os.getenv("MM_AUTH_TOKEN_HOURS") or "168").strip()
    try:
        h = int(raw)
    except ValueError:
        return 168
    return max(1, min(h, 24 * 30))


def self_register_enabled() -> bool:
    """1人目作成後に /api/auth/register を許可するか（既定 ON）。MM_AUTH_SELF_REGISTER=0 で管理者招待のみに。"""
    raw = (os.getenv("MM_AUTH_SELF_REGISTER") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")
