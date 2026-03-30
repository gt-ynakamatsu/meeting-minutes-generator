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


def _queue_row_for_api(row) -> dict:
    """キュー一覧はポーリングで頻繁に取るため、巨大な transcript / summary を載せず ready フラグのみ。"""
    d = sqlite_row_to_dict(row)
    t = d.get("transcript")
    status = str(d.get("status") or "")
    has_text = bool(t is not None and str(t).strip() != "")
    # Whisper 実行中は transcript が一瞬だけ入っても無効（完了＝次ステータスへ進んだ後）
    d["transcript_ready"] = has_text and status != "processing:transcribing"
    d.pop("transcript", None)
    d.pop("summary", None)
    return d


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
    return [_queue_row_for_api(r) for r in rows]


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


@router.get("/api/records/{task_id}/export/transcript_md")
def export_transcript_md(task_id: str, _auth: ApiUser):
    """Whisper 後など transcript が埋まっていれば先に取得可能（議事録整形前の生テキスト）。"""
    row = db.get_record(task_id, _auth or "")
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    text = row["transcript"] or ""
    st = str(row["status"] or "")
    if st == "processing:transcribing":
        raise HTTPException(
            status_code=404,
            detail="Whisper による文字起こしがまだ完了していません。しばらくしてから再度お試しください。",
        )
    if not str(text).strip():
        raise HTTPException(status_code=404, detail="文字起こしはまだありません（処理のこの段階では取得できません）")
    base = os.path.basename(row["filename"] or "transcript")
    safe_base = base.rsplit(".", 1)[0] if "." in base else base
    sum_raw = str(row["summary"] or "")
    err_cancelled = st == "cancelled" and "【処理エラー】" in sum_raw
    if err_cancelled:
        note = (
            "- 議事録の AI 推論（抽出・統合）でエラーが出たジョブですが、**文字起こしまで完了していた内容**を保存しています。\n"
        )
    else:
        note = (
            "- Whisper 等による自動文字起こしです。議事録の体裁整形・要約より先に保存した内容です。\n"
        )
    header = (
        "# 書き起こし（自動）\n\n"
        f"- 元ファイル: {base}\n"
        + note
        + "\n---\n\n"
    )
    body = (header + str(text)).encode("utf-8")
    fn = f"transcript_{safe_base}.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
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
