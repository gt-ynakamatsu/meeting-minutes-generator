"""ログイン・初回セットアップ・自己登録・セッション情報。"""

import os
import sqlite3

from fastapi import APIRouter, HTTPException

import database as db
import feature_flags
from backend import smtp_notify
from backend.auth_jwt import create_access_token
from backend.auth_settings import auth_enabled, self_register_enabled
from backend.deps import ApiUser
from backend.passwords import verify_password
from backend.schemas import AuthMeResponse, AuthStatusResponse, BootstrapRequest, LoginRequest, TokenResponse

router = APIRouter(tags=["auth"])


def _error_report_available() -> bool:
    if not smtp_notify.smtp_configured():
        return False
    if auth_enabled():
        return len(db.list_admin_emails()) > 0
    return bool((os.getenv("MM_ERROR_REPORT_TO") or "").strip())


@router.get("/api/auth/status", response_model=AuthStatusResponse)
def auth_status():
    email_feat = feature_flags.email_notify_feature_enabled()
    email_ok = email_feat and smtp_notify.smtp_configured()
    oa = feature_flags.openai_feature_enabled()
    er = _error_report_available()
    if not auth_enabled():
        return AuthStatusResponse(
            auth_required=False,
            bootstrap_needed=False,
            self_register_allowed=False,
            email_notify_feature_enabled=email_feat,
            email_notify_available=email_ok,
            openai_enabled=oa,
            error_report_available=er,
        )
    n = db.count_users()
    return AuthStatusResponse(
        auth_required=True,
        bootstrap_needed=n == 0,
        self_register_allowed=self_register_enabled() and n > 0,
        email_notify_feature_enabled=email_feat,
        email_notify_available=email_ok,
        openai_enabled=oa,
        error_report_available=er,
    )


@router.post("/api/auth/login", response_model=TokenResponse)
def auth_login(body: LoginRequest):
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="認証は無効です（MM_AUTH_SECRET が未設定）")
    if db.count_users() == 0:
        raise HTTPException(
            status_code=503,
            detail="ユーザーが未登録です。ブラウザで初回セットアップを行うか、MM_BOOTSTRAP_ADMIN_USER / MM_BOOTSTRAP_ADMIN_PASSWORD を設定して API を再起動してください。",
        )
    email = db.registry_login_normalize(body.email or "")
    try:
        db.validate_registry_login_email(email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    password = (body.password or "").replace("\r", "")
    row = db.get_user_by_username(email)
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")
    token = create_access_token(row["username"])
    return TokenResponse(access_token=token)


@router.post("/api/auth/bootstrap", response_model=TokenResponse)
def auth_bootstrap(body: BootstrapRequest):
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="認証は無効です（MM_AUTH_SECRET が未設定）")
    if db.count_users() > 0:
        raise HTTPException(status_code=403, detail="初期設定は既に完了しています")
    email = db.registry_login_normalize(body.email or "")
    password = (body.password or "").replace("\r", "")
    try:
        db.bootstrap_registry_admin(email, password)
    except ValueError as e:
        msg = str(e)
        code = 403 if "既に完了" in msg else 400
        raise HTTPException(status_code=code, detail=msg) from e
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="このメールアドレスは既に使われています") from None
    row = db.get_user_by_username(email)
    if not row:
        raise HTTPException(status_code=500, detail="登録に失敗しました")
    return TokenResponse(access_token=create_access_token(row["username"]))


@router.post("/api/auth/register", response_model=TokenResponse)
def auth_register(body: LoginRequest):
    """1人目以降の自己登録（一般ユーザー）。ユーザー0件のときは初回セットアップを利用すること。"""
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="認証は無効です（MM_AUTH_SECRET が未設定）")
    if not self_register_enabled():
        raise HTTPException(status_code=403, detail="自己登録は無効です（管理者にアカウント作成を依頼してください）")
    if db.count_users() == 0:
        raise HTTPException(
            status_code=400,
            detail="最初の管理者は「初回セットアップ」から作成してください。",
        )
    email = db.registry_login_normalize(body.email or "")
    password = (body.password or "").replace("\r", "")
    try:
        db.create_registry_user(email, password, is_admin=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="このメールアドレスは既に使われています") from None
    row = db.get_user_by_username(email)
    if not row:
        raise HTTPException(status_code=500, detail="登録に失敗しました")
    return TokenResponse(access_token=create_access_token(row["username"]))


@router.get("/api/auth/me", response_model=AuthMeResponse)
def auth_me(_auth: ApiUser):
    if not auth_enabled():
        return AuthMeResponse(email="", is_admin=False)
    u = (_auth or "").strip()
    if not u:
        raise HTTPException(status_code=401, detail="認証が必要です")
    row = db.get_user_by_username(u)
    email_out = str(row["username"]) if row else u
    return AuthMeResponse(email=email_out, is_admin=db.user_is_admin(u))
