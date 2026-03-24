import json
import logging
import os
import sqlite3
import uuid
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import bcrypt
from pydantic import ValidationError

import database as db
import feature_flags
from backend import smtp_notify
from celery_app import celery_app
from backend.auth_jwt import create_access_token
from backend.auth_settings import auth_enabled, self_register_enabled
from backend.deps import AdminUser, ApiUser
from backend.schemas import (
    AdminCreateUserRequest,
    AdminPasswordResetRequest,
    AdminRolePatch,
    AdminUserRow,
    AuthMeResponse,
    AuthStatusResponse,
    BootstrapRequest,
    LoginRequest,
    MeLLMPatch,
    MeLLMResponse,
    OllamaModelsResponse,
    SummaryPatch,
    TaskSubmitMetadata,
    TokenResponse,
)
from backend.storage import save_uploaded_prompts


def _verify_password(raw: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), stored_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


def _ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def _fetch_ollama_model_names(timeout_sec: float = 6.0) -> List[str]:
    base = _ollama_base_url()
    url = f"{base}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return []
    except Exception:
        return []
    names: List[str] = []
    for m in data.get("models") or []:
        if not isinstance(m, dict):
            continue
        n = m.get("name")
        if isinstance(n, str) and n.strip():
            names.append(n.strip())
    return sorted(set(names))


def _content_disposition_attachment(filename: str) -> str:
    """ブラウザが UTF-8 ファイル名を解釈できるよう filename* を付与。"""
    ascii_fallback = "".join(c if 32 <= ord(c) < 127 and c not in '\\"' else "_" for c in filename).strip("_") or "download"
    if len(ascii_fallback) > 180:
        ascii_fallback = ascii_fallback[:180]
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    n = db.purge_all_minutes_archives()
    if n:
        logging.getLogger("uvicorn.error").info(
            "議事録の保持期限により %s 件のレコードを削除しました（MM_MINUTES_RETENTION_DAYS）。",
            n,
        )
    if not auth_enabled():
        logging.getLogger("uvicorn.error").warning(
            "MM_AUTH_SECRET が未設定のため認証が無効です。全利用者が同一の議事録 DB（data/minutes.db）を共有します。"
            "ユーザー別アーカイブには MM_AUTH_SECRET を設定してください（Docker Compose 既定ではフォールバック値で認証 ON）。"
        )
    yield


app = FastAPI(title="Meeting Minutes API", lifespan=lifespan)

_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8085",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/ollama/models", response_model=OllamaModelsResponse)
def ollama_models(_auth: ApiUser):
    """ブラウザが Ollama に直アクセスできないため、API 経由で /api/tags を返す。"""
    return OllamaModelsResponse(models=_fetch_ollama_model_names())


@app.get("/api/version")
def api_version():
    try:
        from version import __version__

        return {"version": __version__}
    except Exception:
        return {"version": "unknown"}


@app.get("/api/auth/status", response_model=AuthStatusResponse)
def auth_status():
    email_ok = smtp_notify.smtp_configured()
    oa = feature_flags.openai_feature_enabled()
    if not auth_enabled():
        return AuthStatusResponse(
            auth_required=False,
            bootstrap_needed=False,
            self_register_allowed=False,
            email_notify_available=email_ok,
            openai_enabled=oa,
        )
    n = db.count_users()
    return AuthStatusResponse(
        auth_required=True,
        bootstrap_needed=n == 0,
        self_register_allowed=self_register_enabled() and n > 0,
        email_notify_available=email_ok,
        openai_enabled=oa,
    )


@app.post("/api/auth/login", response_model=TokenResponse)
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
    # コピペや古いクライアント由来の CR を除去（パスワード先頭末尾の通常空白はそのまま）
    password = (body.password or "").replace("\r", "")
    row = db.get_user_by_username(email)
    if not row or not _verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")
    token = create_access_token(row["username"])
    return TokenResponse(access_token=token)


@app.post("/api/auth/bootstrap", response_model=TokenResponse)
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
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail="このメールアドレスは既に使われています") from e
    row = db.get_user_by_username(email)
    if not row:
        raise HTTPException(status_code=500, detail="登録に失敗しました")
    return TokenResponse(access_token=create_access_token(row["username"]))


@app.post("/api/auth/register", response_model=TokenResponse)
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
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail="このメールアドレスは既に使われています") from e
    row = db.get_user_by_username(email)
    if not row:
        raise HTTPException(status_code=500, detail="登録に失敗しました")
    return TokenResponse(access_token=create_access_token(row["username"]))


@app.get("/api/auth/me", response_model=AuthMeResponse)
def auth_me(_auth: ApiUser):
    if not auth_enabled():
        return AuthMeResponse(email="", is_admin=False)
    u = (_auth or "").strip()
    if not u:
        raise HTTPException(status_code=401, detail="認証が必要です")
    row = db.get_user_by_username(u)
    email_out = str(row["username"]) if row else u
    return AuthMeResponse(email=email_out, is_admin=db.user_is_admin(u))


@app.get("/api/admin/users", response_model=list[AdminUserRow])
def admin_list_users(_admin: AdminUser):
    rows = db.list_registry_users()
    return [
        AdminUserRow(
            email=r["username"],
            is_admin=bool(r["is_admin"]),
            created_at=str(r["created_at"]) if r.get("created_at") is not None else None,
        )
        for r in rows
    ]


@app.post("/api/admin/users", response_model=AdminUserRow)
def admin_create_user(body: AdminCreateUserRequest, _admin: AdminUser):
    u = db.registry_login_normalize(body.email or "")
    pw = (body.password or "").replace("\r", "")
    try:
        db.create_registry_user(u, pw, is_admin=body.is_admin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail="このメールアドレスは既に使われています") from e
    row = db.get_user_by_username(u)
    if not row:
        raise HTTPException(status_code=500, detail="作成に失敗しました")
    return AdminUserRow(
        email=str(row["username"]),
        is_admin=db.user_is_admin(str(row["username"])),
        created_at=str(row["created_at"]) if row["created_at"] is not None else None,
    )


@app.patch("/api/admin/users/{login_email}/password")
def admin_reset_password(login_email: str, body: AdminPasswordResetRequest, _admin: AdminUser):
    u = db.resolve_registry_username_for_mutation(login_email)
    if not u:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    try:
        ok = db.set_registry_user_password(u, (body.new_password or "").replace("\r", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    return {"ok": True}


@app.patch("/api/admin/users/{login_email}/role")
def admin_set_role(login_email: str, body: AdminRolePatch, _admin: AdminUser):
    u = db.resolve_registry_username_for_mutation(login_email)
    if not u:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    try:
        db.set_registry_user_admin(u, body.is_admin)
    except KeyError:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.delete("/api/admin/users/{login_email}")
def admin_delete_user(login_email: str, _admin: AdminUser):
    target = db.resolve_registry_username_for_mutation(login_email)
    if not target:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    admin_key = db.resolve_registry_username_for_mutation(_admin)
    if admin_key and admin_key == target:
        raise HTTPException(status_code=400, detail="自分自身は削除できません")
    try:
        db.delete_registry_user(target)
    except KeyError:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.get("/api/me/llm", response_model=MeLLMResponse)
def me_llm_get(_auth: ApiUser):
    oa = feature_flags.openai_feature_enabled()
    if not auth_enabled():
        return MeLLMResponse(
            openai_configured=False,
            openai_model="gpt-4o-mini",
            openai_feature_enabled=oa,
        )
    if not (_auth or "").strip():
        raise HTTPException(status_code=401, detail="認証が必要です")
    key, model = db.get_user_openai_settings(_auth)
    return MeLLMResponse(
        openai_configured=bool(key) if oa else False,
        openai_model=model,
        openai_feature_enabled=oa,
    )


@app.patch("/api/me/llm")
def me_llm_patch(body: MeLLMPatch, _auth: ApiUser):
    if not feature_flags.openai_feature_enabled():
        raise HTTPException(
            status_code=400,
            detail="OpenAI 連携は無効です（MM_OPENAI_ENABLED）。有効にするには環境変数を設定して API を再起動してください。",
        )
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="認証が無効なためサーバに保存できません")
    if not (_auth or "").strip():
        raise HTTPException(status_code=401, detail="認証が必要です")
    api_key_arg = None
    if "openai_api_key" in body.model_fields_set:
        api_key_arg = (body.openai_api_key or "").strip()
    model_arg = None
    if "openai_model" in body.model_fields_set:
        model_arg = (body.openai_model or "").strip() or "gpt-4o-mini"
    if api_key_arg is None and model_arg is None:
        return {"ok": True}
    db.update_user_openai(_auth, api_key=api_key_arg, model=model_arg)
    return {"ok": True}


@app.get("/api/presets")
def get_presets(_auth: ApiUser):
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "presets_builtin.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (OSError, json.JSONDecodeError):
        return {"standard": {"label": "標準", "extract_hint": "", "merge_hint": ""}}


@app.post("/api/tasks")
async def create_task(
    _auth: ApiUser,
    metadata: str = Form(...),
    file: UploadFile = File(...),
    prompt_extract: Optional[UploadFile] = File(None),
    prompt_merge: Optional[UploadFile] = File(None),
):
    try:
        meta = TaskSubmitMetadata.model_validate_json(metadata)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    if meta.notification_type == "webhook" and not (meta.email or "").strip():
        raise HTTPException(status_code=400, detail="Webhook 通知のときはメールアドレスが必須です")

    owner = (_auth or "").strip()
    record_email = (meta.email or "").strip()
    email_for_worker: Optional[str] = None

    if meta.notification_type == "email":
        if auth_enabled() and owner:
            dest = (meta.email or "").strip() or owner
        else:
            dest = (meta.email or "").strip()
        if not dest:
            raise HTTPException(
                status_code=400,
                detail="メール通知の宛先をフォームに入力するか、ログインしてください（ログイン時はログイン ID のメールに送ります）。",
            )
        if not smtp_notify.smtp_configured():
            raise HTTPException(
                status_code=503,
                detail="メール通知を使うにはサーバに SMTP を設定してください（MM_SMTP_HOST, MM_SMTP_FROM 等）。ワーカーにも同じ環境変数を渡してください。",
            )
        record_email = dest
        email_for_worker = dest
    elif meta.notification_type == "webhook":
        email_for_worker = (meta.email or "").strip()
    if meta.llm_provider == "openai" and not feature_flags.openai_feature_enabled():
        raise HTTPException(
            status_code=400,
            detail="OpenAI は現在無効です（MM_OPENAI_ENABLED=0 等）。ローカル（Ollama）を選ぶか、管理者に連絡してください。",
        )
    if meta.llm_provider == "openai":
        if auth_enabled() and owner:
            okey, omodel = db.get_user_openai_settings(_auth)
            if not okey:
                raise HTTPException(
                    status_code=400,
                    detail="OpenAI を使うには、先に「OpenAI 設定」で API キーを保存してください。",
                )
            openai_key = okey
            openai_model = omodel
        else:
            if not (meta.openai_api_key or "").strip():
                raise HTTPException(status_code=400, detail="OpenAI を選んだときは API キーが必須です")
            openai_key = meta.openai_api_key
            openai_model = meta.openai_model

    if not file.filename:
        raise HTTPException(status_code=400, detail="ファイル名がありません")

    task_id = str(uuid.uuid4())
    os.makedirs("downloads", exist_ok=True)
    safe_name = os.path.basename(file.filename)
    path = os.path.join("downloads", f"{task_id}_{safe_name}")

    body = await file.read()
    with open(path, "wb") as f:
        f.write(body)

    ctx_json = json.dumps(meta.context.model_dump(), ensure_ascii=False)

    db.save_initial_task(
        task_id,
        record_email,
        safe_name,
        owner=owner,
        topic=meta.topic.strip(),
        tags=meta.tags.strip(),
        category=meta.category,
        meeting_date=meta.meeting_date.strip(),
        preset_id=meta.preset_id.strip() or "standard",
        context_json=ctx_json,
    )

    if meta.llm_provider == "openai":
        llm_config = {
            "provider": "openai",
            "api_key": openai_key,
            "ollama_model": meta.ollama_model,
            "openai_model": openai_model,
            "notification_type": meta.notification_type,
        }
    else:
        llm_config = {
            "provider": "ollama",
            "api_key": None,
            "ollama_model": meta.ollama_model,
            "openai_model": meta.openai_model,
            "notification_type": meta.notification_type,
        }

    pe_bytes = await prompt_extract.read() if prompt_extract and prompt_extract.filename else None
    pm_bytes = await prompt_merge.read() if prompt_merge and prompt_merge.filename else None
    prompt_paths = save_uploaded_prompts(task_id, pe_bytes, pm_bytes)

    celery_app.send_task(
        "tasks.process_video_task",
        args=[
            task_id,
            email_for_worker,
            safe_name,
            path,
            meta.webhook_url,
            llm_config,
            prompt_paths,
            owner,
        ],
        task_id=task_id,
    )

    return {"task_id": task_id, "filename": safe_name}


@app.get("/api/records")
def list_records(
    _auth: ApiUser,
    days: int = 7,
    search: str = "",
    category: str = "",
    status_filter: str = "",
):
    rows = db.get_recent_records(
        _auth or "",
        days=days,
        search=search,
        category=category,
        status_filter=status_filter,
    )
    return [_row_to_dict(r) for r in rows]


@app.get("/api/queue")
def queue_records(_auth: ApiUser):
    rows = db.get_active_queue_records(_auth or "")
    return [_row_to_dict(r) for r in rows]


@app.get("/api/records/{task_id}")
def get_record(task_id: str, _auth: ApiUser):
    row = db.get_record(task_id, _auth or "")
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return _row_to_dict(row)


@app.post("/api/records/{task_id}/discard")
def discard_record(task_id: str, _auth: ApiUser):
    """待機・実行中のジョブを破棄する（DB を cancelled、Celery を revoke、投入ファイルを削除）。"""
    owner = (_auth or "").strip()
    try:
        db.discard_task(task_id, owner)
    except KeyError:
        raise HTTPException(status_code=404, detail="not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    db.remove_task_upload_files(task_id)
    db.cleanup_user_prompts_dir(task_id)
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception:
        logging.getLogger(__name__).debug("celery revoke failed for %s", task_id, exc_info=True)
    return {"ok": True}


@app.get("/api/records/{task_id}/export/minutes")
def export_minutes(task_id: str, _auth: ApiUser):
    db.purge_expired_minutes_db_path(db.minutes_db_path(_auth or ""))
    row = db.get_record(task_id, _auth or "")
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    summary = row["summary"]
    if summary is None or str(summary).strip() in ("", "None"):
        raise HTTPException(status_code=404, detail="議事録テキストがありません")
    body = str(summary).encode("utf-8")
    base = os.path.basename(row["filename"] or "minutes")
    fn = f"minutes_{base}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition_attachment(fn)},
    )


@app.get("/api/records/{task_id}/export/transcript")
def export_transcript(task_id: str, _auth: ApiUser):
    row = db.get_record(task_id, _auth or "")
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    text = row["transcript"] or ""
    body = str(text).encode("utf-8")
    base = os.path.basename(row["filename"] or "transcript")
    fn = f"{base}.txt"
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition_attachment(fn)},
    )


@app.patch("/api/records/{task_id}/summary")
def patch_summary(task_id: str, body: SummaryPatch, _auth: ApiUser):
    db.purge_expired_minutes_db_path(db.minutes_db_path(_auth or ""))
    row = db.get_record(task_id, _auth or "")
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    db.update_record(task_id, _auth or "", summary=body.summary)
    return {"ok": True}
