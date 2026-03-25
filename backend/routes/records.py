"""議事録レコードの参照・更新・エクスポート・破棄。"""

import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

import database as db
from backend.deps import ApiUser
from backend.http_utils import content_disposition_attachment, sqlite_row_to_dict
from backend.schemas import SummaryPatch
from celery_app import celery_app

router = APIRouter(tags=["records"])
logger = logging.getLogger(__name__)


@router.get("/api/records")
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
    return [sqlite_row_to_dict(r) for r in rows]


@router.get("/api/queue")
def queue_records(_auth: ApiUser):
    rows = db.get_active_queue_records(_auth or "")
    return [sqlite_row_to_dict(r) for r in rows]


@router.get("/api/records/{task_id}")
def get_record(task_id: str, _auth: ApiUser):
    row = db.get_record(task_id, _auth or "")
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return sqlite_row_to_dict(row)


@router.post("/api/records/{task_id}/discard")
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
        logger.debug("celery revoke failed for %s", task_id, exc_info=True)
    return {"ok": True}


@router.get("/api/records/{task_id}/export/minutes")
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
        headers={"Content-Disposition": content_disposition_attachment(fn)},
    )


@router.get("/api/records/{task_id}/export/transcript")
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
        headers={"Content-Disposition": content_disposition_attachment(fn)},
    )


@router.patch("/api/records/{task_id}/summary")
def patch_summary(task_id: str, body: SummaryPatch, _auth: ApiUser):
    db.purge_expired_minutes_db_path(db.minutes_db_path(_auth or ""))
    row = db.get_record(task_id, _auth or "")
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    db.update_record(task_id, _auth or "", summary=body.summary)
    return {"ok": True}
