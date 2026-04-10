"""解析タスクの受付（Celery 投入）。"""

import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

import database as db
import feature_flags
from backend import smtp_notify
from backend.auth_settings import auth_enabled
from backend.deps import ApiUser
from backend.schemas import TaskSubmitMetadata
from backend.storage import merge_task_prompt_paths
from celery_app import celery_app

router = APIRouter(tags=["jobs"])

logger = logging.getLogger(__name__)

_SUPPLEMENTARY_TEAMS_EXT = frozenset({".vtt"})
_SUPPLEMENTARY_NOTES_EXT = frozenset({".txt", ".md"})
_SUBMIT_RATE_STATE: dict[str, deque[float]] = {}
_SUBMIT_RATE_LOCK = threading.Lock()

_GIB = 1024 ** 3
_UPLOAD_MAX_BYTES_DEFAULT = 5 * _GIB


@dataclass(frozen=True)
class UploadGuardSettings:
    max_bytes: int
    rate_limit_count: int
    rate_limit_window_sec: int
    min_free_gb: int
    warn_free_gb: int


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _supplementary_teams_upload_ok(name: Optional[str]) -> bool:
    if not (name or "").strip():
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in _SUPPLEMENTARY_TEAMS_EXT


def _supplementary_notes_upload_ok(name: Optional[str]) -> bool:
    if not (name or "").strip():
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in _SUPPLEMENTARY_NOTES_EXT


def _upload_max_bytes() -> int:
    return _env_int("MM_UPLOAD_MAX_BYTES", _UPLOAD_MAX_BYTES_DEFAULT, minimum=1)


def _submit_rate_limit_count() -> int:
    return _env_int("MM_TASK_SUBMIT_RATE_LIMIT_COUNT", 30, minimum=0)


def _submit_rate_limit_window_sec() -> int:
    return _env_int("MM_TASK_SUBMIT_RATE_LIMIT_WINDOW_SEC", 60, minimum=1)


def _upload_min_free_gb() -> int:
    return _env_int("MM_UPLOAD_MIN_FREE_GB", 5, minimum=0)


def _upload_warn_free_gb() -> int:
    return _env_int("MM_UPLOAD_WARN_FREE_GB", 20, minimum=0)


def _upload_guard_settings() -> UploadGuardSettings:
    return UploadGuardSettings(
        max_bytes=_upload_max_bytes(),
        rate_limit_count=_submit_rate_limit_count(),
        rate_limit_window_sec=_submit_rate_limit_window_sec(),
        min_free_gb=_upload_min_free_gb(),
        warn_free_gb=_upload_warn_free_gb(),
    )


def _check_submit_rate_limit(actor_key: str, settings: Optional[UploadGuardSettings] = None) -> None:
    cfg = settings or _upload_guard_settings()
    count = cfg.rate_limit_count
    if count <= 0:
        return
    window_sec = cfg.rate_limit_window_sec
    now = time.monotonic()
    with _SUBMIT_RATE_LOCK:
        q = _SUBMIT_RATE_STATE.get(actor_key)
        if q is None:
            q = deque()
            _SUBMIT_RATE_STATE[actor_key] = q
        while q and (now - q[0]) > window_sec:
            q.popleft()
        if len(q) >= count:
            db.record_usage_guard_event("rate_limited", actor_key)
            raise HTTPException(
                status_code=429,
                detail=f"アップロードが集中しています。{window_sec}秒あたり{count}件までに制限されています。少し待って再試行してください。",
            )
        q.append(now)


def _check_upload_capacity_guard(settings: Optional[UploadGuardSettings] = None, actor_key: str = "") -> None:
    cfg = settings or _upload_guard_settings()
    os.makedirs("downloads", exist_ok=True)
    usage = shutil.disk_usage("downloads")
    free = int(usage.free)
    warn_gb = cfg.warn_free_gb
    min_gb = cfg.min_free_gb
    warn_b = warn_gb * _GIB
    min_b = min_gb * _GIB

    if warn_b > 0 and free < warn_b:
        logger.warning(
            "アップロード保存先の空き容量が少なくなっています: free=%.2f GiB",
            free / _GIB,
        )
    if min_b > 0 and free < min_b:
        db.record_usage_guard_event("disk_low", actor_key)
        raise HTTPException(
            status_code=503,
            detail=(
                "サーバの空き容量が不足しているためアップロードを受け付けできません。"
                f"現在の空き: {free / _GIB:.2f} GiB（必要下限: {min_gb} GiB）"
            ),
        )


async def _save_upload_file_stream(
    upload: UploadFile,
    path: str,
    chunk_size: int = 1024 * 1024,
    max_bytes: Optional[int] = None,
    actor_key: str = "",
) -> int:
    """UploadFile をチャンク保存し、保存バイト数を返す。"""
    total = 0
    limit_bytes = max_bytes if max_bytes is not None else _upload_max_bytes()
    with open(path, "wb") as f:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > limit_bytes:
                db.record_usage_guard_event("upload_too_large", actor_key)
                raise HTTPException(
                    status_code=413,
                    detail=f"アップロード上限は {limit_bytes / _GIB:.0f} GiB です。ファイルを分割して再試行してください。",
                )
            f.write(chunk)
    return total


@router.post("/api/tasks")
async def create_task(
    _auth: ApiUser,
    metadata: str = Form(...),
    file: UploadFile = File(...),
    prompt_extract: Optional[UploadFile] = File(None),
    prompt_merge: Optional[UploadFile] = File(None),
    supplementary_teams: Optional[UploadFile] = File(None),
    supplementary_notes: Optional[UploadFile] = File(None),
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
    actor_key = owner or record_email or "anonymous"
    guard_settings = _upload_guard_settings()
    _check_submit_rate_limit(actor_key, settings=guard_settings)
    _check_upload_capacity_guard(settings=guard_settings, actor_key=actor_key)

    if meta.notification_type == "email":
        if not feature_flags.email_notify_feature_enabled():
            raise HTTPException(
                status_code=400,
                detail="メール通知は現在利用できません。ブラウザ・Webhook・なしから選ぶか、管理者に連絡してください。",
            )
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
    else:
        openai_key = None
        openai_model = meta.openai_model

    if not file.filename:
        raise HTTPException(status_code=400, detail="ファイル名がありません")

    if supplementary_teams and supplementary_teams.filename and not _supplementary_teams_upload_ok(supplementary_teams.filename):
        raise HTTPException(
            status_code=400,
            detail="参考資料（Teams 等）は .vtt のみ対応です",
        )
    if supplementary_notes and supplementary_notes.filename and not _supplementary_notes_upload_ok(supplementary_notes.filename):
        raise HTTPException(
            status_code=400,
            detail="参考資料（メモ）は .txt / .md のみ対応です",
        )

    task_id = str(uuid.uuid4())
    os.makedirs("downloads", exist_ok=True)
    safe_name = os.path.basename(file.filename)
    path = os.path.join("downloads", f"{task_id}_{safe_name}")

    try:
        input_bytes = await _save_upload_file_stream(
            file,
            path,
            max_bytes=guard_settings.max_bytes,
            actor_key=actor_key,
        )
    except Exception:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        raise

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
        transcript_only=bool(meta.transcript_only),
    )

    model_for_log = (
        (openai_model or meta.openai_model or "gpt-4o-mini").strip()
        if meta.llm_provider == "openai"
        else (meta.ollama_model or "").strip()
    )

    db.record_usage_job_submission(
        task_id,
        owner,
        bool(meta.transcript_only),
        meta.llm_provider,
        model_for_log,
        whisper_preset=(meta.whisper_preset or "accurate"),
        original_filename=safe_name,
        input_bytes=input_bytes,
        notification_type=meta.notification_type,
        has_supplementary_teams=bool(supplementary_teams and supplementary_teams.filename),
        has_supplementary_notes=bool(supplementary_notes and supplementary_notes.filename),
    )

    whisper_bundle = {"whisper_preset": meta.whisper_preset}

    if meta.llm_provider == "openai":
        llm_config = {
            "provider": "openai",
            "api_key": openai_key,
            "ollama_model": meta.ollama_model,
            "openai_model": openai_model,
            "notification_type": meta.notification_type,
            "transcript_only": bool(meta.transcript_only),
            **whisper_bundle,
        }
    else:
        llm_config = {
            "provider": "ollama",
            "api_key": None,
            "ollama_model": meta.ollama_model,
            "openai_model": meta.openai_model,
            "notification_type": meta.notification_type,
            "transcript_only": bool(meta.transcript_only),
            **whisper_bundle,
        }

    pe_bytes = await prompt_extract.read() if prompt_extract and prompt_extract.filename else None
    pm_bytes = await prompt_merge.read() if prompt_merge and prompt_merge.filename else None
    st_bytes = await supplementary_teams.read() if supplementary_teams and supplementary_teams.filename else None
    sn_bytes = await supplementary_notes.read() if supplementary_notes and supplementary_notes.filename else None
    prompt_paths = merge_task_prompt_paths(task_id, pe_bytes, pm_bytes, st_bytes, sn_bytes)

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
