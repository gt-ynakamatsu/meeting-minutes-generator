import sqlite3

import pytest
from fastapi import HTTPException

from backend.routes import admin
from backend.schemas import (
    AdminCreateUserRequest,
    AdminPasswordResetRequest,
    AdminRolePatch,
    UsageAdminNoteCreate,
)


def test_admin_user_row_model_helper():
    row = {"username": "u@example.com", "is_admin": 1, "created_at": None}
    m = admin._admin_user_row_model(row)
    assert m.email == "u@example.com"
    assert m.is_admin is True
    assert m.created_at is None


def test_resolved_user_or_404(monkeypatch):
    monkeypatch.setattr(admin.db, "resolve_registry_username_for_mutation", lambda _e: "")
    with pytest.raises(HTTPException) as e:
        admin._resolved_user_or_404("x@example.com")
    assert e.value.status_code == 404

    monkeypatch.setattr(admin.db, "resolve_registry_username_for_mutation", lambda _e: "x@example.com")
    assert admin._resolved_user_or_404("x@example.com") == "x@example.com"


def test_admin_list_users(monkeypatch):
    monkeypatch.setattr(
        admin.db,
        "list_registry_users",
        lambda: [{"username": "a@example.com", "is_admin": 0, "created_at": "2026-01-01 00:00:00"}],
    )
    rows = admin.admin_list_users("admin@example.com")
    assert len(rows) == 1
    assert rows[0].email == "a@example.com"
    assert rows[0].is_admin is False


def test_admin_create_user_branches(monkeypatch):
    body = AdminCreateUserRequest(email="U@EXAMPLE.COM", password="password123", is_admin=True)
    monkeypatch.setattr(admin.db, "registry_login_normalize", lambda s: s.strip().lower())

    monkeypatch.setattr(admin.db, "create_registry_user", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as e:
        admin.admin_create_user(body, "admin@example.com")
    assert e.value.status_code == 400

    monkeypatch.setattr(
        admin.db,
        "create_registry_user",
        lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.IntegrityError("dup")),
    )
    with pytest.raises(HTTPException) as e:
        admin.admin_create_user(body, "admin@example.com")
    assert e.value.status_code == 409

    monkeypatch.setattr(admin.db, "create_registry_user", lambda *_a, **_k: None)
    monkeypatch.setattr(admin.db, "get_user_by_username", lambda _u: None)
    with pytest.raises(HTTPException) as e:
        admin.admin_create_user(body, "admin@example.com")
    assert e.value.status_code == 500

    monkeypatch.setattr(
        admin.db,
        "get_user_by_username",
        lambda _u: {"username": "u@example.com", "is_admin": 1, "created_at": "2026-01-01 00:00:00"},
    )
    row = admin.admin_create_user(body, "admin@example.com")
    assert row.email == "u@example.com"
    assert row.is_admin is True


def test_admin_reset_password_branches(monkeypatch):
    monkeypatch.setattr(admin, "_resolved_user_or_404", lambda _e: "u@example.com")
    body = AdminPasswordResetRequest(new_password="password123")

    monkeypatch.setattr(admin.db, "set_registry_user_password", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as e:
        admin.admin_reset_password("u@example.com", body, "admin@example.com")
    assert e.value.status_code == 400

    monkeypatch.setattr(admin.db, "set_registry_user_password", lambda *_a, **_k: False)
    with pytest.raises(HTTPException) as e:
        admin.admin_reset_password("u@example.com", body, "admin@example.com")
    assert e.value.status_code == 404

    monkeypatch.setattr(admin.db, "set_registry_user_password", lambda *_a, **_k: True)
    assert admin.admin_reset_password("u@example.com", body, "admin@example.com") == {"ok": True}


def test_admin_set_role_branches(monkeypatch):
    monkeypatch.setattr(admin, "_resolved_user_or_404", lambda _e: "u@example.com")
    body = AdminRolePatch(is_admin=True)

    monkeypatch.setattr(admin.db, "set_registry_user_admin", lambda *_a, **_k: (_ for _ in ()).throw(KeyError("x")))
    with pytest.raises(HTTPException) as e:
        admin.admin_set_role("u@example.com", body, "admin@example.com")
    assert e.value.status_code == 404

    monkeypatch.setattr(admin.db, "set_registry_user_admin", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as e:
        admin.admin_set_role("u@example.com", body, "admin@example.com")
    assert e.value.status_code == 400

    monkeypatch.setattr(admin.db, "set_registry_user_admin", lambda *_a, **_k: None)
    assert admin.admin_set_role("u@example.com", body, "admin@example.com") == {"ok": True}


def test_admin_delete_user_branches(monkeypatch):
    monkeypatch.setattr(admin, "_resolved_user_or_404", lambda _e: "target@example.com")
    monkeypatch.setattr(admin.db, "resolve_registry_username_for_mutation", lambda _e: "admin@example.com")

    # self delete
    monkeypatch.setattr(admin, "_resolved_user_or_404", lambda _e: "admin@example.com")
    with pytest.raises(HTTPException) as e:
        admin.admin_delete_user("admin@example.com", "admin@example.com")
    assert e.value.status_code == 400

    # key error
    monkeypatch.setattr(admin, "_resolved_user_or_404", lambda _e: "target@example.com")
    monkeypatch.setattr(admin.db, "delete_registry_user", lambda *_a, **_k: (_ for _ in ()).throw(KeyError("x")))
    with pytest.raises(HTTPException) as e:
        admin.admin_delete_user("target@example.com", "admin@example.com")
    assert e.value.status_code == 404

    # value error
    monkeypatch.setattr(admin.db, "delete_registry_user", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as e:
        admin.admin_delete_user("target@example.com", "admin@example.com")
    assert e.value.status_code == 400

    monkeypatch.setattr(admin.db, "delete_registry_user", lambda *_a, **_k: None)
    assert admin.admin_delete_user("target@example.com", "admin@example.com") == {"ok": True}


def test_admin_usage_endpoints(monkeypatch):
    monkeypatch.setattr(
        admin.db,
        "admin_usage_summary",
        lambda _days: {
            "period_days": 7,
            "total_submissions": 10,
            "pipeline_minutes_llm": {"count": 6, "pct": 60.0},
            "pipeline_transcript_only": {"count": 4, "pct": 40.0},
            "provider_ollama": {"count": 7, "pct": 70.0},
            "provider_openai": {"count": 3, "pct": 30.0},
            "ollama_models_for_llm_jobs": [],
            "openai_models_for_llm_jobs": [],
            "whisper_presets_for_media": [],
            "media_kind_breakdown": [],
            "metrics_rollup": {},
        },
    )
    s = admin.admin_usage_summary("admin@example.com", days=7)
    assert s.total_submissions == 10

    monkeypatch.setattr(
        admin.db,
        "admin_usage_events",
        lambda *_a, **_k: (
            [
                {
                    "id": 1,
                    "created_at": "2026-01-01 00:00:00",
                    "task_id": "t1",
                    "user_email": "u@example.com",
                    "transcript_only": False,
                    "llm_provider": "ollama",
                    "model_name": "qwen2.5:7b",
                    "whisper_preset": "accurate",
                    "media_kind": "audio",
                }
            ],
            1,
        ),
    )
    ev = admin.admin_usage_events("admin@example.com", days=7, limit=10, offset=0)
    assert ev.total == 1

    monkeypatch.setattr(
        admin.db,
        "admin_usage_settings_summary",
        lambda _days: {
            "period_days": 7,
            "total_submissions": 10,
            "notification_breakdown": [{"value": "browser", "count": 8, "pct": 80.0}],
            "supplementary_teams_used": {"count": 3, "pct": 30.0},
            "supplementary_notes_used": {"count": 4, "pct": 40.0},
            "supplementary_any_used": {"count": 5, "pct": 50.0},
            "guard_events": [{"event_type": "rate_limited", "count": 2}],
            "total_guard_events": 2,
        },
    )
    ss = admin.admin_usage_settings_summary("admin@example.com", days=7)
    assert ss.total_submissions == 10
    assert ss.total_guard_events == 2

    monkeypatch.setattr(admin.db, "usage_admin_notes_list", lambda: [{"id": 1, "author_email": "a", "body": "b", "created_at": "x"}])
    notes = admin.admin_usage_notes_list("admin@example.com")
    assert len(notes) == 1


def test_admin_usage_notes_add_delete(monkeypatch):
    body = UsageAdminNoteCreate(body="note")
    monkeypatch.setattr(admin.db, "usage_admin_note_add", lambda *_a, **_k: None)
    with pytest.raises(HTTPException) as e:
        admin.admin_usage_notes_add(body, "admin@example.com")
    assert e.value.status_code == 400

    monkeypatch.setattr(admin.db, "usage_admin_note_add", lambda *_a, **_k: 1)
    monkeypatch.setattr(admin.db, "usage_admin_note_get", lambda _i: None)
    with pytest.raises(HTTPException) as e:
        admin.admin_usage_notes_add(body, "admin@example.com")
    assert e.value.status_code == 500

    monkeypatch.setattr(
        admin.db,
        "usage_admin_note_get",
        lambda _i: {"id": 1, "author_email": "admin@example.com", "body": "note", "created_at": "2026-01-01 00:00:00"},
    )
    row = admin.admin_usage_notes_add(body, "admin@example.com")
    assert row.id == 1

    monkeypatch.setattr(admin.db, "usage_admin_note_delete", lambda _i: False)
    with pytest.raises(HTTPException) as e:
        admin.admin_usage_notes_delete(1, "admin@example.com")
    assert e.value.status_code == 404

    monkeypatch.setattr(admin.db, "usage_admin_note_delete", lambda _i: True)
    assert admin.admin_usage_notes_delete(1, "admin@example.com") == {"ok": True}
