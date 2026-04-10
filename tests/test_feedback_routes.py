from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.routes import feedback
from backend.schemas import ErrorReportRequest, SuggestionBoxCreateRequest


def _req(ua: str = "pytest-agent"):
    return SimpleNamespace(headers={"user-agent": ua})


def test_error_report_recipients_auth_on(monkeypatch):
    monkeypatch.setattr(feedback, "auth_enabled", lambda: True)
    monkeypatch.setattr(feedback.db, "list_admin_emails", lambda: ["a@example.com", "a@example.com", "b@example.com"])
    got = feedback._error_report_recipients()
    assert got == ["a@example.com", "b@example.com"]


def test_error_report_recipients_auth_off(monkeypatch):
    monkeypatch.setattr(feedback, "auth_enabled", lambda: False)
    monkeypatch.setenv("MM_ERROR_REPORT_TO", "x@example.com, x@example.com, y@example.com")
    got = feedback._error_report_recipients()
    assert got == ["x@example.com", "y@example.com"]


def test_error_report_recipients_auth_off_empty(monkeypatch):
    monkeypatch.setattr(feedback, "auth_enabled", lambda: False)
    monkeypatch.delenv("MM_ERROR_REPORT_TO", raising=False)
    got = feedback._error_report_recipients()
    assert got == []


def test_error_report_smtp_not_configured(monkeypatch):
    monkeypatch.setattr(feedback.smtp_notify, "smtp_configured", lambda: False)
    with pytest.raises(HTTPException) as e:
        feedback.post_error_report(ErrorReportRequest(message="m"), _req(), "")
    assert e.value.status_code == 503


def test_error_report_no_recipients(monkeypatch):
    monkeypatch.setattr(feedback.smtp_notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(feedback, "_error_report_recipients", lambda: [])
    with pytest.raises(HTTPException) as e:
        feedback.post_error_report(ErrorReportRequest(message="m"), _req(), "")
    assert e.value.status_code == 503


def test_error_report_success_and_subject_trim(monkeypatch):
    sent = {}
    monkeypatch.setattr(feedback.smtp_notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(feedback, "_error_report_recipients", lambda: ["admin@example.com"])

    def _send(recipients, subject, text):
        sent["recipients"] = recipients
        sent["subject"] = subject
        sent["text"] = text

    monkeypatch.setattr(feedback.smtp_notify, "send_plain_email_to_recipients", _send)
    long_user = "u" * 400
    body = ErrorReportRequest(message="msg", detail="detail", page_url="http://localhost/x", client_version="v1")
    res = feedback.post_error_report(body, _req("ua"), long_user)
    assert res["ok"] is True
    assert res["sent_to_count"] == 1
    assert sent["recipients"] == ["admin@example.com"]
    assert len(sent["subject"]) <= 200
    assert "技術情報" in sent["text"]


def test_suggestion_webhook_url_priority(monkeypatch):
    monkeypatch.setenv("MM_SUGGESTION_BOX_WEBHOOK_URL", "http://x")
    monkeypatch.setenv("WEBHOOK_URL", "http://y")
    assert feedback._suggestion_webhook_url() == "http://x"


def test_suggestion_webhook_url_fallback(monkeypatch):
    monkeypatch.delenv("MM_SUGGESTION_BOX_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("WEBHOOK_URL", "http://y")
    assert feedback._suggestion_webhook_url() == "http://y"


def test_post_suggestion_auth_off(monkeypatch):
    monkeypatch.setattr(feedback, "auth_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        feedback.post_suggestion_box(SuggestionBoxCreateRequest(body="b"), _req(), "u@example.com")
    assert e.value.status_code == 403


def test_post_suggestion_create_failed(monkeypatch):
    monkeypatch.setattr(feedback, "auth_enabled", lambda: True)
    monkeypatch.setattr(feedback.db, "suggestion_box_create", lambda *a, **k: None)
    with pytest.raises(HTTPException) as e:
        feedback.post_suggestion_box(SuggestionBoxCreateRequest(body="b"), _req(), "u@example.com")
    assert e.value.status_code == 400


def test_post_suggestion_success_without_webhook(monkeypatch):
    monkeypatch.setattr(feedback, "auth_enabled", lambda: True)
    monkeypatch.setattr(feedback.db, "suggestion_box_create", lambda *a, **k: 101)
    monkeypatch.setattr(feedback, "_suggestion_webhook_url", lambda: "")
    res = feedback.post_suggestion_box(SuggestionBoxCreateRequest(subject="s", body="b"), _req(), "u@example.com")
    assert res == {"ok": True, "id": 101, "webhook_notified": False}


def test_post_suggestion_success_with_webhook(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(feedback, "auth_enabled", lambda: True)
    monkeypatch.setattr(feedback.db, "suggestion_box_create", lambda *a, **k: 102)
    monkeypatch.setattr(feedback, "_suggestion_webhook_url", lambda: "http://hook")

    def _post(url, json, timeout):
        calls["n"] += 1
        assert url == "http://hook"
        assert "目安箱" in json["text"]
        assert timeout == 10
        return object()

    monkeypatch.setattr(feedback.requests, "post", _post)
    res = feedback.post_suggestion_box(
        SuggestionBoxCreateRequest(subject="", body="b", page_url="", client_version=""),
        _req(),
        "u@example.com",
    )
    assert calls["n"] == 1
    assert res == {"ok": True, "id": 102, "webhook_notified": True}


def test_post_suggestion_webhook_error(monkeypatch):
    monkeypatch.setattr(feedback, "auth_enabled", lambda: True)
    monkeypatch.setattr(feedback.db, "suggestion_box_create", lambda *a, **k: 103)
    monkeypatch.setattr(feedback, "_suggestion_webhook_url", lambda: "http://hook")

    def _post(*args, **kwargs):
        raise RuntimeError("ng")

    monkeypatch.setattr(feedback.requests, "post", _post)
    res = feedback.post_suggestion_box(SuggestionBoxCreateRequest(subject="s", body="b"), _req(), "u@example.com")
    assert res == {"ok": True, "id": 103, "webhook_notified": False}
