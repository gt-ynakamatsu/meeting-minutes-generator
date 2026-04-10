import database as db


def _configure_registry_paths(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MM_AUTH_SECRET", "x" * 40)
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", str(data_dir / "registry.db"))
    monkeypatch.setattr(db, "LEGACY_MINUTES_PATH", str(data_dir / "minutes.db"))
    db.init_registry_db()


def test_suggestion_box_create_and_list(monkeypatch, tmp_path):
    _configure_registry_paths(monkeypatch, tmp_path)

    ticket_id = db.suggestion_box_create(
        "USER@EXAMPLE.COM",
        "要望",
        "目安箱のテスト投稿",
        page_url="http://localhost:5173/page",
        client_version="dev",
    )
    assert isinstance(ticket_id, int)

    items, total = db.suggestion_box_admin_list()
    assert total == 1
    assert len(items) == 1
    assert items[0]["id"] == ticket_id
    assert items[0]["author_email"] == "user@example.com"
    assert items[0]["status"] == "new"


def test_suggestion_box_update_and_filter(monkeypatch, tmp_path):
    _configure_registry_paths(monkeypatch, tmp_path)
    ticket_id = db.suggestion_box_create("a@example.com", "s", "b")
    assert ticket_id is not None

    ok = db.suggestion_box_admin_update(ticket_id, status="in_progress", admin_note="確認中")
    assert ok is True

    row = db.suggestion_box_admin_get(ticket_id)
    assert row is not None
    assert row["status"] == "in_progress"
    assert row["admin_note"] == "確認中"

    items, total = db.suggestion_box_admin_list(status="in_progress")
    assert total == 1
    assert items[0]["id"] == ticket_id


def test_suggestion_box_requires_auth(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.delenv("MM_AUTH_SECRET", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", str(data_dir / "registry.db"))
    monkeypatch.setattr(db, "LEGACY_MINUTES_PATH", str(data_dir / "minutes.db"))

    assert db.suggestion_box_create("a@example.com", "s", "b") is None
    items, total = db.suggestion_box_admin_list()
    assert items == []
    assert total == 0
    assert db.suggestion_box_admin_update(1, status="done") is False


def test_suggestion_box_status_normalize_and_edge_cases(monkeypatch, tmp_path):
    _configure_registry_paths(monkeypatch, tmp_path)

    # status normalize
    assert db._normalize_suggestion_status(" new ") == "new"
    assert db._normalize_suggestion_status("IN_PROGRESS") == "in_progress"
    assert db._normalize_suggestion_status("x") == "new"

    # empty body -> create rejected
    assert db.suggestion_box_create("u@example.com", "s", "   ") is None

    # create then invalid status update should normalize to new
    ticket_id = db.suggestion_box_create("u@example.com", "s", "b")
    assert ticket_id is not None
    assert db.suggestion_box_admin_update(ticket_id, status="INVALID_STATUS") is True
    row = db.suggestion_box_admin_get(ticket_id)
    assert row is not None
    assert row["status"] == "new"

    # no fields to update -> False
    assert db.suggestion_box_admin_update(ticket_id) is False

    # not found
    assert db.suggestion_box_admin_get(999999) is None

    # admin list with unknown status (falls back to all)
    items, total = db.suggestion_box_admin_list(status="unknown", limit=1, offset=0)
    assert total >= 1
    assert len(items) == 1
