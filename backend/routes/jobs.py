"""解析タスクの受付（Celery 投入）。"""

import json
import os
import uuid
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

_SUPPLEMENTARY_EXT = frozenset({".txt", ".md", ".vtt"})


def _supplementary_upload_ok(name: Optional[str]) -> bool:
    if not (name or "").strip():
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in _SUPPLEMENTARY_EXT


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

    if supplementary_teams and supplementary_teams.filename and not _supplementary_upload_ok(supplementary_teams.filename):
        raise HTTPException(
            status_code=400,
            detail="参考資料（Teams 等）は .txt / .md / .vtt のみ対応です",
        )
    if supplementary_notes and supplementary_notes.filename and not _supplementary_upload_ok(supplementary_notes.filename):
        raise HTTPException(
            status_code=400,
            detail="参考資料（メモ）は .txt / .md / .vtt のみ対応です",
        )

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
        transcript_only=bool(meta.transcript_only),
    )

    if auth_enabled() and owner:
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
            input_bytes=len(body),
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
