import pytest
from fastapi import HTTPException

from backend.routes import admin
from backend.schemas import AdminSuggestionBoxPatch


def test_admin_suggestion_list(monkeypatch):
    monkeypatch.setattr(
        admin.db,
        "suggestion_box_admin_list",
        lambda **kwargs: (
            [
                {
                    "id": 1,
                    "created_at": "2026-01-01 00:00:00",
                    "updated_at": "2026-01-01 00:00:00",
                    "author_email": "u@example.com",
                    "subject": "s",
                    "body": "b",
                    "page_url": "",
                    "client_version": "v1",
                    "status": "new",
                    "admin_note": "",
                }
            ],
            1,
        ),
    )
    res = admin.admin_suggestion_box_list("admin@example.com", status="", limit=80, offset=0)
    assert res.total == 1
    assert len(res.items) == 1
    assert res.items[0].id == 1


def test_admin_suggestion_patch_not_found_on_update(monkeypatch):
    monkeypatch.setattr(admin.db, "suggestion_box_admin_update", lambda *args, **kwargs: False)
    with pytest.raises(HTTPException) as e:
        admin.admin_suggestion_box_patch(1, AdminSuggestionBoxPatch(status="done"), "admin@example.com")
    assert e.value.status_code == 404


def test_admin_suggestion_patch_not_found_on_get(monkeypatch):
    monkeypatch.setattr(admin.db, "suggestion_box_admin_update", lambda *args, **kwargs: True)
    monkeypatch.setattr(admin.db, "suggestion_box_admin_get", lambda ticket_id: None)
    with pytest.raises(HTTPException) as e:
        admin.admin_suggestion_box_patch(1, AdminSuggestionBoxPatch(status="done"), "admin@example.com")
    assert e.value.status_code == 404


def test_admin_suggestion_patch_success(monkeypatch):
    monkeypatch.setattr(admin.db, "suggestion_box_admin_update", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        admin.db,
        "suggestion_box_admin_get",
        lambda ticket_id: {
            "id": ticket_id,
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "author_email": "u@example.com",
            "subject": "s",
            "body": "b",
            "page_url": "",
            "client_version": "",
            "status": "done",
            "admin_note": "ok",
        },
    )
    row = admin.admin_suggestion_box_patch(2, AdminSuggestionBoxPatch(status="done", admin_note="ok"), "admin@example.com")
    assert row.id == 2
    assert row.status == "done"
    assert row.admin_note == "ok"
