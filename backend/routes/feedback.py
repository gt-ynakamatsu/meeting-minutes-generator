"""フロントからのエラー・不具合報告を管理者メールへ転送（SMTP）。"""

import os
from typing import List

import requests
from fastapi import APIRouter, HTTPException, Request

import database as db
from backend import smtp_notify
from backend.auth_settings import auth_enabled
from backend.deps import ApiUser, OptionalApiUser
from backend.schemas import ErrorReportRequest, SuggestionBoxCreateRequest

router = APIRouter(tags=["feedback"])


def _error_report_recipients() -> List[str]:
    if auth_enabled():
        admins = db.list_admin_emails()
        return list(dict.fromkeys(a.strip() for a in admins if (a or "").strip()))
    raw = (os.getenv("MM_ERROR_REPORT_TO") or "").strip()
    if not raw:
        return []
    return list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))


def _suggestion_webhook_url() -> str:
    return (os.getenv("MM_SUGGESTION_BOX_WEBHOOK_URL") or os.getenv("WEBHOOK_URL") or "").strip()


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


@router.post("/api/feedback/suggestion-box")
def post_suggestion_box(body: SuggestionBoxCreateRequest, request: Request, user: ApiUser):
    """目安箱を保存し、設定があれば Webhook にも通知する。"""
    if not auth_enabled():
        raise HTTPException(status_code=403, detail="目安箱は認証有効時のみ利用できます")
    # ApiUser により、認証 ON のときは有効なログイン ID が入る。
    uid = user.strip()
    nid = db.suggestion_box_create(
        uid,
        body.subject,
        body.body,
        page_url=body.page_url,
        client_version=body.client_version,
    )
    if nid is None:
        raise HTTPException(status_code=400, detail="目安箱を保存できませんでした")

    sent = False
    wh = _suggestion_webhook_url()
    if wh and wh != "YOUR_WEBHOOK_URL_HERE":
        title = (body.subject or "").strip() or "（件名なし）"
        page = (body.page_url or "").strip() or "—"
        ver = (body.client_version or "").strip() or "—"
        ua = (request.headers.get("user-agent") or "").strip()[:200]
        msg = (
            "📮 Meeting Minutes Notebook 目安箱\n"
            f"- ID: #{nid}\n"
            f"- 投稿者: {uid}\n"
            f"- 件名: {title}\n"
            f"- バージョン: {ver}\n"
            f"- ページ: {page}\n"
            f"- UA: {ua or '—'}\n"
            f"- 内容:\n{(body.body or '').strip()[:1200]}"
        )
        try:
            requests.post(wh, json={"text": msg}, timeout=10)
            sent = True
        except Exception:
            sent = False
    return {"ok": True, "id": nid, "webhook_notified": sent}
