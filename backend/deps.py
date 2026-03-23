from typing import Annotated, Optional

import database as db
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth_jwt import decode_access_token
from backend.auth_settings import auth_enabled

_bearer = HTTPBearer(auto_error=False)


def require_api_user(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> str:
    if not auth_enabled():
        return ""
    if creds is None or not (creds.credentials or "").strip():
        raise HTTPException(
            status_code=401,
            detail="認証が必要です",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials.strip())
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise ValueError("bad sub")
        return sub.strip()
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="トークンが無効または期限切れです",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def require_admin(user: Annotated[str, Depends(require_api_user)]) -> str:
    if not auth_enabled():
        raise HTTPException(status_code=403, detail="認証が無効なため管理 API は使えません")
    if not db.user_is_admin(user):
        raise HTTPException(status_code=403, detail="管理者権限が必要です")
    return user


ApiUser = Annotated[str, Depends(require_api_user)]
AdminUser = Annotated[str, Depends(require_admin)]
