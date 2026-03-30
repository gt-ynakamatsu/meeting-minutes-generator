"""フロントからのエラー・不具合報告を管理者メールへ転送（SMTP）。"""

import os
from typing import List

from fastapi import APIRouter, HTTPException, Request

import database as db
from backend import smtp_notify
from backend.auth_settings import auth_enabled
from backend.deps import OptionalApiUser
from backend.schemas import ErrorReportRequest

router = APIRouter(tags=["feedback"])


def _error_report_recipients() -> List[str]:
    if auth_enabled():
        admins = db.list_admin_emails()
        return list(dict.fromkeys(a.strip() for a in admins if (a or "").strip()))
    raw = (os.getenv("MM_ERROR_REPORT_TO") or "").strip()
    if not raw:
        return []
    return list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))


@router.post("/api/feedback/error-report")
def post_error_report(body: ErrorReportRequest, request: Request, user: OptionalApiUser):
    """SMTP 必須。認証 ON では管理者ログイン（メール）全員へ。OFF 時は MM_ERROR_REPORT_TO。"""
    if not smtp_notify.smtp_configured():
        raise HTTPException(
            status_code=503,
            detail="エラー報告メールを送るにはサーバに SMTP（MM_SMTP_HOST, MM_SMTP_FROM 等）を設定してください。",
        )
    recipients = _error_report_recipients()
    if not recipients:
        raise HTTPException(
            status_code=503,
            detail="送信先がありません。管理者ユーザーを登録するか、認証オフのときは MM_ERROR_REPORT_TO にメールアドレスを設定してください。",
        )

    reporter = (user or "").strip() or "（未ログイン／匿名）"
    ua = (request.headers.get("user-agent") or "").strip()[:500]
    page = (body.page_url or "").strip()[:2000]
    ver = (body.client_version or "").strip()[:64]
    msg = (body.message or "").strip()
    detail = (body.detail or "").strip()

    text = (
        "Meeting Minutes Notebook — 利用者からのエラー・不具合報告\n"
        "========================================\n\n"
        f"報告者（ログイン ID）: {reporter}\n"
        f"クライアント版: {ver or '—'}\n"
        f"ページ URL: {page or '—'}\n"
        f"User-Agent: {ua or '—'}\n\n"
        "--- 内容 ---\n"
        f"{msg}\n"
    )
    if detail:
        text += "\n--- 技術情報（スタック等） ---\n" + detail[:12000] + "\n"

    subject = f"[議事録ノート] エラー報告 ({reporter})"
    if len(subject) > 200:
        subject = subject[:197] + "..."

    smtp_notify.send_plain_email_to_recipients(recipients, subject, text)
    return {"ok": True, "sent_to_count": len(recipients)}
