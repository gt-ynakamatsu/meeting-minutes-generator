import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import ValidationError

import database as db
from celery_app import celery_app
from backend.schemas import SummaryPatch, TaskSubmitMetadata
from backend.storage import save_uploaded_prompts


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}


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


@app.get("/api/version")
def api_version():
    try:
        from version import __version__

        return {"version": __version__}
    except Exception:
        return {"version": "unknown"}


@app.get("/api/presets")
def get_presets():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "presets_builtin.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (OSError, json.JSONDecodeError):
        return {"standard": {"label": "標準", "extract_hint": "", "merge_hint": ""}}


@app.post("/api/tasks")
async def create_task(
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
    if meta.llm_provider == "openai" and not (meta.openai_api_key or "").strip():
        raise HTTPException(status_code=400, detail="OpenAI を選んだときは API キーが必須です")

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
        meta.email or "",
        safe_name,
        topic=meta.topic.strip(),
        tags=meta.tags.strip(),
        category=meta.category,
        meeting_date=meta.meeting_date.strip(),
        preset_id=meta.preset_id.strip() or "standard",
        context_json=ctx_json,
    )

    llm_config = {
        "provider": "openai" if meta.llm_provider == "openai" else "ollama",
        "api_key": meta.openai_api_key,
        "ollama_model": meta.ollama_model,
        "openai_model": meta.openai_model,
    }

    pe_bytes = await prompt_extract.read() if prompt_extract and prompt_extract.filename else None
    pm_bytes = await prompt_merge.read() if prompt_merge and prompt_merge.filename else None
    prompt_paths = save_uploaded_prompts(task_id, pe_bytes, pm_bytes)

    email_for_worker = meta.email if meta.notification_type == "webhook" else None

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
        ],
    )

    return {"task_id": task_id, "filename": safe_name}


@app.get("/api/records")
def list_records(
    days: int = 7,
    search: str = "",
    category: str = "",
    status_filter: str = "",
):
    rows = db.get_recent_records(
        days=days,
        search=search,
        category=category,
        status_filter=status_filter,
    )
    return [_row_to_dict(r) for r in rows]


@app.get("/api/queue")
def queue_records():
    rows = db.get_active_queue_records()
    return [_row_to_dict(r) for r in rows]


@app.get("/api/records/{task_id}")
def get_record(task_id: str):
    row = db.get_record(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return _row_to_dict(row)


@app.get("/api/records/{task_id}/export/minutes")
def export_minutes(task_id: str):
    row = db.get_record(task_id)
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
def export_transcript(task_id: str):
    row = db.get_record(task_id)
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
def patch_summary(task_id: str, body: SummaryPatch):
    row = db.get_record(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    db.update_record(task_id, summary=body.summary)
    return {"ok": True}
