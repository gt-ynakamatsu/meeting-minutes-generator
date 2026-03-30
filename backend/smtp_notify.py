"""タスク完了などの通知メール（環境変数 MM_SMTP_*）。API は設定有無の検証、ワーカーが送信。"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    h = (os.getenv("MM_SMTP_HOST") or "").strip()
    f = (os.getenv("MM_SMTP_FROM") or "").strip()
    return bool(h and f)


def send_task_completion_email(to_addr: str, filename: str, task_id: str) -> None:
    """完了通知を送信。失敗時はログのみ（タスク結果は変えない）。"""
    if not smtp_configured():
        return
    to_addr = (to_addr or "").strip()
    if not to_addr:
        return

    host = (os.getenv("MM_SMTP_HOST") or "").strip()
    try:
        port = int((os.getenv("MM_SMTP_PORT") or "587").strip())
    except ValueError:
        port = 587
    user = (os.getenv("MM_SMTP_USER") or "").strip()
    password = (os.getenv("MM_SMTP_PASSWORD") or "").strip()
    from_addr = (os.getenv("MM_SMTP_FROM") or "").strip()
    starttls = (os.getenv("MM_SMTP_STARTTLS") or "1").strip().lower() not in ("0", "false", "no", "off")

    subject = f"議事録が完了しました: {filename}"
    body = (
        f"ファイル名: {filename}\n"
        f"タスク ID: {task_id}\n\n"
        "アプリの議事録一覧から内容を確認できます。\n"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as smtp:
                if starttls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except Exception:
        logger.exception("SMTP 送信に失敗しました to=%s task_id=%s", to_addr, task_id)


def send_task_failure_email(to_addr: str, filename: str, task_id: str, error_detail: str) -> None:
    """処理失敗通知。失敗時はログのみ。"""
    if not smtp_configured():
        return
    to_addr = (to_addr or "").strip()
    if not to_addr:
        return

    host = (os.getenv("MM_SMTP_HOST") or "").strip()
    try:
        port = int((os.getenv("MM_SMTP_PORT") or "587").strip())
    except ValueError:
        port = 587
    user = (os.getenv("MM_SMTP_USER") or "").strip()
    password = (os.getenv("MM_SMTP_PASSWORD") or "").strip()
    from_addr = (os.getenv("MM_SMTP_FROM") or "").strip()
    starttls = (os.getenv("MM_SMTP_STARTTLS") or "1").strip().lower() not in ("0", "false", "no", "off")

    detail = (error_detail or "").strip()[:4000]
    subject = f"議事録の処理に失敗しました: {filename}"
    body = (
        f"ファイル名: {filename}\n"
        f"タスク ID: {task_id}\n\n"
        "ジョブは破棄され、アップロード済みの原稿ファイルはサーバから削除されています。\n\n"
        "---\n"
        f"{detail}\n"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as smtp:
                if starttls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except Exception:
        logger.exception("失敗通知メールの送信に失敗しました to=%s task_id=%s", to_addr, task_id)


def send_plain_email_to_recipients(to_addrs: list[str], subject: str, body: str) -> None:
    """複数宛先へ同じ本文の通知（エラー報告など）。失敗時はログのみ。"""
    if not smtp_configured():
        return
    addrs = [a.strip() for a in to_addrs if (a or "").strip()]
    if not addrs:
        return

    host = (os.getenv("MM_SMTP_HOST") or "").strip()
    try:
        port = int((os.getenv("MM_SMTP_PORT") or "587").strip())
    except ValueError:
        port = 587
    user = (os.getenv("MM_SMTP_USER") or "").strip()
    password = (os.getenv("MM_SMTP_PASSWORD") or "").strip()
    from_addr = (os.getenv("MM_SMTP_FROM") or "").strip()
    starttls = (os.getenv("MM_SMTP_STARTTLS") or "1").strip().lower() not in ("0", "false", "no", "off")

    subj = (subject or "").strip()[:900] or "Meeting Minutes — 通知"
    text = (body or "").strip()
    if len(text) > 500_000:
        text = text[:500_000] + "\n\n… (truncated)"

    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = from_addr
    msg["To"] = ", ".join(addrs)
    msg.set_content(text)

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as smtp:
                if starttls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except Exception:
        logger.exception("SMTP 一斉送信に失敗しました to=%s", addrs)
