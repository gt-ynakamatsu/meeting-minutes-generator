import time
from typing import Any, Dict

import jwt

from backend.auth_settings import auth_secret, token_ttl_hours

_ALGO = "HS256"


def create_access_token(username: str) -> str:
    secret = auth_secret()
    now = int(time.time())
    exp = now + token_ttl_hours() * 3600
    payload: Dict[str, Any] = {"sub": username, "iat": now, "exp": exp}
    return jwt.encode(payload, secret, algorithm=_ALGO)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, auth_secret(), algorithms=[_ALGO])
