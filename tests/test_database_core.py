import builtins
import os
import sqlite3
from datetime import datetime, timedelta
import types

import pytest

import database as db


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MM_AUTH_SECRET", "secret")
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", str(data_dir / "registry.db"))
    monkeypatch.setattr(db, "LEGACY_MINUTES_PATH", str(data_dir / "minutes.db"))
    return data_dir


def test_email_and_retention_and_slug(monkeypatch):
    assert db.registry_login_normalize(" A@B.COM ") == "a@b.com"
    with pytest.raises(ValueError):
        db.validate_registry_login_email("")
    with pytest.raises(ValueError):
        db.validate_registry_login_email("a@b")
    db.validate_registry_login_email("a.b@example.com")

    monkeypatch.delenv("MM_MINUTES_RETENTION_DAYS", raising=False)
    assert db.minutes_retention_days() == db.DEFAULT_MINUTES_RETENTION_DAYS
    monkeypatch.setenv("MM_MINUTES_RETENTION_DAYS", "bad")
    assert db.minutes_retention_days() == db.DEFAULT_MINUTES_RETENTION_DAYS
    monkeypatch.setenv("MM_MINUTES_RETENTION_DAYS", "183")
    assert db.minutes_retention_days() == db.DEFAULT_MINUTES_RETENTION_DAYS
    monkeypatch.setenv("MM_MINUTES_RETENTION_DAYS", "5")
    assert db.minutes_retention_days() == 5

    s = db._owner_slug("user@example.com")
    assert len(s) > 10
    assert "_" in s


def test_registry_user_lifecycle(isolated_db):
    db.init_db()
    assert db.count_users() == 0

    db.bootstrap_registry_admin("Admin@Example.com", "password123")
    assert db.count_users() == 1
    assert db.user_is_admin("admin@example.com") is True
    assert db.list_admin_emails() == ["admin@example.com"]

    with pytest.raises(ValueError):
        db.bootstrap_registry_admin("x@example.com", "password123")

    db.create_registry_user("user@example.com", "password123", is_admin=False)
    users = db.list_registry_users()
    assert len(users) == 2
    assert db.resolve_registry_username_for_mutation("USER@example.com") == "user@example.com"

    db.update_user_openai("user@example.com", api_key="k", model="")
    key, model = db.get_user_openai_settings("user@example.com")
    assert key == "k"
    assert model == "gpt-4o-mini"

    assert db.set_registry_user_password("user@example.com", "password999") is True
    assert db.set_registry_user_password("", "password999") is False
    with pytest.raises(ValueError):
        db.set_registry_user_password("user@example.com", "short")

    with pytest.raises(ValueError):
        db.delete_registry_user("admin@example.com")  # 最後の管理者

    with pytest.raises(ValueError):
        db.set_registry_user_admin("", True)
    with pytest.raises(KeyError):
        db.set_registry_user_admin("missing@example.com", True)
    db.set_registry_user_admin("user@example.com", True)
    assert db.user_is_admin("user@example.com") is True

    db.set_registry_user_admin("user@example.com", False)
    db.delete_registry_user("user@example.com")
    with pytest.raises(KeyError):
        db.delete_registry_user("missing@example.com")


def test_minutes_records_flow_and_queue(isolated_db):
    owner = "owner@example.com"
    db.save_initial_task("t1", owner, "a.mp3", owner=owner, topic="A", tags="tag1", category="cat1")
    row = db.get_record("t1", owner=owner)
    assert row is not None
    assert row["status"] == "pending"

    db.update_record("t1", owner=owner, status="processing 10%")
    db.update_record("t1", owner=owner, status="completed", transcript="txt", summary="sum")
    row2 = db.get_record("t1", owner=owner)
    assert row2["transcript"] == "txt"
    assert row2["summary"] == "sum"

    assert db.count_recent_records(owner=owner, days=30, search="A") >= 1
    recs = db.get_recent_records(owner=owner, days=30, category="cat1", status_filter="completed", limit=10, offset=0)
    assert len(recs) >= 1

    db.save_initial_task("t2", owner, "b.mp4", owner=owner)
    q = db.get_active_queue_records(owner=owner, days=30, limit=20)
    assert any(r["id"] == "t2" for r in q)

    gq = db.get_active_queue_records_global(viewer="", days=30, limit=20)
    assert any(r.get("id") == "t2" for r in gq)

    db.discard_task("t2", owner=owner)
    with pytest.raises(ValueError):
        db.discard_task("t1", owner=owner)  # completed は破棄不可
    with pytest.raises(KeyError):
        db.discard_task("missing", owner=owner)


def test_context_usage_and_admin_usage_helpers(isolated_db):
    assert db.parse_context_json(None) == {}
    assert db.parse_context_json({"context_json": ""}) == {}
    assert db.parse_context_json({"context_json": "[]"} ) == {}
    assert db.parse_context_json({"context_json": '{"k":1}'}) == {"k": 1}

    assert db.usage_media_kind_from_filename("a.mp4") == "video"
    assert db.usage_media_kind_from_filename("a.wav") == "audio"
    assert db.usage_media_kind_from_filename("a.srt") == "srt"
    assert db.usage_media_kind_from_filename("a.txt") == "txt"
    assert db.usage_media_kind_from_filename("a.bin") == "other"

    db.record_usage_job_submission(
        task_id="u1",
        user_email="U@EXAMPLE.COM",
        transcript_only=False,
        llm_provider="openai",
        model_name="",
        whisper_preset="accurate",
        original_filename="f.mp3",
        input_bytes=10,
        notification_type="email",
        has_supplementary_teams=True,
        has_supplementary_notes=False,
    )
    db.record_usage_guard_event("rate_limited", "u@example.com")
    db.record_usage_guard_event("upload_too_large", "u@example.com")
    db.update_usage_job_metrics(
        "u1",
        input_bytes=10,
        media_duration_sec=5.0,
        audio_extract_wall_sec=1.1,
        whisper_wall_sec=2.2,
        transcript_chars=100,
        extract_llm_sec=0.5,
        merge_llm_sec=0.4,
        llm_chunks=2,
        completion_wall_sec=12.3,
    )

    summary = db.admin_usage_summary(7)
    assert summary["total_submissions"] >= 1
    items, total = db.admin_usage_events(7, limit=10, offset=0)
    assert total >= 1
    assert len(items) >= 1
    assert "task_id" in items[0]
    assert "notification_type" in items[0]
    assert "has_supplementary_teams" in items[0]
    assert "has_supplementary_notes" in items[0]
    assert "completion_wall_sec" in items[0]
    settings_summary = db.admin_usage_settings_summary(7)
    assert settings_summary["total_submissions"] >= 1
    assert len(settings_summary["notification_breakdown"]) >= 1
    assert settings_summary["supplementary_teams_used"]["count"] >= 1
    assert settings_summary["total_guard_events"] >= 2

    nid = db.usage_admin_note_add("admin@example.com", " note ")
    assert nid is not None
    got = db.usage_admin_note_get(nid)
    assert got is not None and got["body"] == "note"
    assert len(db.usage_admin_notes_list()) >= 1
    assert db.usage_admin_note_delete(nid) is True
    assert db.usage_admin_note_delete(999999) is False


def test_purge_expired_minutes_and_files(isolated_db):
    db.init_minutes_db("")
    path = db.minutes_db_path("")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO records (id, email, filename, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "old1",
                "a@example.com",
                "x.mp3",
                "completed",
                (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.execute(
            "INSERT INTO records (id, email, filename, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "old2",
                "a@example.com",
                "x.mp3",
                "pending",
                (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
    os.makedirs("downloads", exist_ok=True)
    with open(os.path.join("downloads", "old1_tmp.bin"), "w", encoding="utf-8") as f:
        f.write("x")

    removed = db.purge_expired_minutes_db_path(path)
    assert removed == 1
    assert db.purge_expired_minutes("") >= 0
    assert db.purge_all_minutes_archives() >= 0


def test_database_edge_guards_and_error_paths(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(db, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(db, "REGISTRY_DB_PATH", str(data_dir / "registry.db"))
    monkeypatch.setattr(db, "LEGACY_MINUTES_PATH", str(data_dir / "minutes.db"))

    # auth 無効ガード
    monkeypatch.delenv("MM_AUTH_SECRET", raising=False)
    db.init_registry_db()  # no-op
    assert db.count_users() == 0
    assert db.admin_usage_summary(7)["total_submissions"] == 0
    assert db.admin_usage_events(7) == ([], 0)
    assert db.usage_admin_notes_list() == []
    assert db.usage_admin_note_get(1) is None
    assert db.usage_admin_note_add("u@example.com", "x") is None
    assert db.usage_admin_note_delete(1) is False
    assert db.suggestion_box_admin_get(1) is None

    # email validation の未達分岐
    for bad in [
        "a" * 255 + "@example.com",
        "aexample.com",
        "a@",
        "a..b@example.com",
        ".ab@example.com",
    ]:
        with pytest.raises(ValueError):
            db.validate_registry_login_email(bad)

    # parse_context_json guard
    assert db.parse_context_json({"x": 1}) == {}
    assert db.parse_context_json({"context_json": object()}) == {}
    assert db.usage_media_kind_from_filename("README") == "other"
    assert db._usage_pct(1, 0) == 0.0


def test_database_migration_and_filter_paths(isolated_db, monkeypatch, tmp_path):
    # users テーブルに is_admin が無いケース
    db_path = db.REGISTRY_DB_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL)")
        db._migrate_registry_is_admin(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert "is_admin" in cols

    # bootstrap admin from env
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM users")
        monkeypatch.setenv("MM_BOOTSTRAP_ADMIN_USER", "boot@example.com")
        monkeypatch.setenv("MM_BOOTSTRAP_ADMIN_PASSWORD", "password123")
        db._try_bootstrap_admin_registry(conn)
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert n == 1

    # maybe migrate legacy users (legacy に users 無しでも落ちない)
    with sqlite3.connect(db.LEGACY_MINUTES_PATH) as legacy:
        legacy.execute("CREATE TABLE IF NOT EXISTS dummy_tbl (x INTEGER)")
    db._maybe_migrate_legacy_users_to_registry()

    # records filter branches
    owner = "o@example.com"
    db.save_initial_task("f1", owner, "a.mp3", owner=owner, category="社内")
    db.update_record("f1", owner=owner, status="Error: failed")
    db.save_initial_task("f2", owner, "b.mp3", owner=owner, category="社外")
    db.discard_task("f2", owner=owner)
    db.save_initial_task("f3", owner, "c.mp3", owner=owner, category="社内")
    db.update_record("f3", owner=owner, status="processing:transcribing")

    assert db.count_recent_records(owner=owner, days=30, status_filter="error") >= 1
    assert db.count_recent_records(owner=owner, days=30, status_filter="cancelled") >= 1
    assert db.count_recent_records(owner=owner, days=30, status_filter="processing") >= 1

    # queue rows missing file
    assert db._queue_rows_from_minutes_path(str(tmp_path / "missing.db"), datetime.now()) == []


def test_database_usage_record_update_guard_paths(isolated_db, monkeypatch):
    # record_usage_job_submission: invalid task / provider normalize / auth off
    db.record_usage_job_submission("", "u@example.com", False, "x", "", input_bytes=-1)
    monkeypatch.delenv("MM_AUTH_SECRET", raising=False)
    db.record_usage_job_submission("u2", "u@example.com", False, "x", "")
    monkeypatch.setenv("MM_AUTH_SECRET", "secret")

    # update_usage_job_metrics guard
    db.update_usage_job_metrics("")  # no task id
    db.update_usage_job_metrics("u1")  # no fields
    db.update_usage_job_metrics("u1", input_bytes=1)

    # get_user_openai_settings fallback branches
    monkeypatch.setattr(db, "get_user_by_username", lambda _u: None)
    k, m = db.get_user_openai_settings("x")
    assert k is None and m == "gpt-4o-mini"

    class _BadRow(dict):
        def __getitem__(self, key):
            raise TypeError("bad row")

    monkeypatch.setattr(db, "get_user_by_username", lambda _u: _BadRow())
    k2, m2 = db.get_user_openai_settings("x")
    assert k2 is None and m2 == "gpt-4o-mini"

    # update_user_openai early returns
    db.update_user_openai("")
    monkeypatch.setattr(db, "init_registry_db", lambda: None)
    monkeypatch.setattr(db.os.path, "exists", lambda _p: False)
    db.update_user_openai("u@example.com", api_key="k")

    # user_is_admin fallback
    monkeypatch.setattr(db, "get_user_by_username", lambda _u: None)
    assert db.user_is_admin("x") is False
    monkeypatch.setattr(db, "get_user_by_username", lambda _u: {"is_admin": "x"})
    assert db.user_is_admin("x") is False


def test_database_more_guard_and_exception_paths(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    orig_get_user_by_username = db.get_user_by_username
    orig_sqlite_connect = db.sqlite3.connect
    # ensure_at_least_one_admin: 0件 / 既にadminあり / admin昇格
    with sqlite3.connect(db.REGISTRY_DB_PATH) as conn:
        conn.execute("DELETE FROM users")
        db._ensure_at_least_one_admin(conn)
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES ('u1@example.com', 'h', 0), ('u2@example.com', 'h', 0)"
        )
        db._ensure_at_least_one_admin(conn)
        c = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
        assert c == 1
        db._ensure_at_least_one_admin(conn)

    # bootstrap invalid email / n>0 / bcrypt import error
    with sqlite3.connect(db.REGISTRY_DB_PATH) as conn:
        monkeypatch.setenv("MM_BOOTSTRAP_ADMIN_USER", "bad")
        monkeypatch.setenv("MM_BOOTSTRAP_ADMIN_PASSWORD", "password123")
        db._try_bootstrap_admin_registry(conn)
        monkeypatch.setenv("MM_BOOTSTRAP_ADMIN_USER", "u1@example.com")
        db._try_bootstrap_admin_registry(conn)  # n>0

    # maybe migrate legacy users with users table (DETACH 経路)
    with sqlite3.connect(db.LEGACY_MINUTES_PATH) as legacy:
        legacy.execute("CREATE TABLE IF NOT EXISTS users (username TEXT, password_hash TEXT, created_at TEXT)")
        legacy.execute("DELETE FROM users")
        legacy.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('legacy@example.com', 'h', '2026-01-01')")
    with sqlite3.connect(db.REGISTRY_DB_PATH) as conn:
        conn.execute("DELETE FROM users")
    db._maybe_migrate_legacy_users_to_registry()

    # get_user_by_username fallback query(key!=raw)
    with sqlite3.connect(db.REGISTRY_DB_PATH) as conn:
        conn.execute("DELETE FROM users")
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES ('UPPER@example.com', 'h', 0)"
        )
    assert db.get_user_by_username("UPPER@example.com") is not None
    assert db.resolve_registry_username_for_mutation("missing@example.com") is None

    # get_user_openai_settings KeyError / IndexError 分岐
    class _KRow(dict):
        def __getitem__(self, k):
            raise KeyError(k)

    class _IRow(dict):
        def __getitem__(self, k):
            raise IndexError(k)

    monkeypatch.setattr(db, "get_user_by_username", lambda _u: _KRow())
    k, m = db.get_user_openai_settings("x")
    assert k is None and m == "gpt-4o-mini"
    monkeypatch.setattr(db, "get_user_by_username", lambda _u: _IRow())
    k, m = db.get_user_openai_settings("x")
    assert k is None and m == "gpt-4o-mini"
    monkeypatch.setattr(db, "get_user_by_username", orig_get_user_by_username)

    # update_user_openai sets empty
    monkeypatch.setattr(db, "init_registry_db", lambda: None)
    monkeypatch.setattr(db.os.path, "exists", lambda _p: True)
    db.update_user_openai("u@example.com", api_key=None, model=None)

    # count_admins path missing
    monkeypatch.setattr(db.os.path, "exists", lambda _p: False)
    assert db.count_admins() == 0

    # short password branches
    with pytest.raises(ValueError):
        db.bootstrap_registry_admin("a@example.com", "short")
    with pytest.raises(ValueError):
        db.create_registry_user("b@example.com", "short", is_admin=False)

    # last admin role demotion
    monkeypatch.setattr(db.os.path, "exists", lambda _p: True)
    db.init_db()
    with sqlite3.connect(db.REGISTRY_DB_PATH) as conn:
        conn.execute("DELETE FROM users")
        conn.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('admin@example.com', 'h', 1)")
    with pytest.raises(ValueError):
        db.set_registry_user_admin("admin@example.com", False)

    # delete empty user
    with pytest.raises(ValueError):
        db.delete_registry_user("")

    # remove_task_upload_files OSError swallow
    dld = tmp_path / "downloads"
    dld.mkdir(parents=True, exist_ok=True)
    p = dld / "task_x.bin"
    p.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(db.os, "remove", lambda *_a, **_k: (_ for _ in ()).throw(OSError("x")))
    db.remove_task_upload_files("task")

    # cleanup_user_prompts_dir exception swallow
    monkeypatch.setattr(db.os.path, "isdir", lambda _p: True)
    monkeypatch.setattr(db.shutil, "rmtree", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    db.cleanup_user_prompts_dir("tid")

    # purge early return / sqlite error return
    monkeypatch.setenv("MM_MINUTES_RETENTION_DAYS", "0")
    assert db.purge_expired_minutes_db_path("x") == 0
    monkeypatch.setenv("MM_MINUTES_RETENTION_DAYS", "30")
    bad_path = tmp_path / "bad.db"
    bad_path.write_text("not sqlite", encoding="utf-8")
    assert db.purge_expired_minutes_db_path(str(bad_path)) == 0

    # purge_all_minutes_archives with user_data scan
    ud = tmp_path / "data" / "user_data" / "a"
    ud.mkdir(parents=True, exist_ok=True)
    (ud / "minutes.db").write_bytes(b"")
    assert db.purge_all_minutes_archives() >= 0

    # discard error path / invalid state path
    owner = "z@example.com"
    db.save_initial_task("de1", owner, "a.mp3", owner=owner)
    db.update_record("de1", owner=owner, status="Error: boom")
    db.discard_task("de1", owner=owner)
    db.save_initial_task("de2", owner, "a.mp3", owner=owner)
    db.update_record("de2", owner=owner, status="queued")
    with pytest.raises(ValueError):
        db.discard_task("de2", owner=owner)

    # global queue branches: empty username continue / duplicate skip / legacy add / is_mine判定
    class _R(dict):
        def keys(self):
            return super().keys()

    monkeypatch.setattr(
        db,
        "list_registry_users",
        lambda: [{"username": ""}, {"username": "owner@example.com"}],
    )
    p_owner = db.minutes_db_path("owner@example.com")
    p_unknown = os.path.join(db.DATA_DIR, "user_data", "unknown", "minutes.db")
    monkeypatch.setattr(db, "_queue_rows_from_minutes_path", lambda p, _s: [_R({"id": "1", "created_at": "x", "email": "owner@example.com"})] if p in (p_owner, p_unknown, db.LEGACY_MINUTES_PATH) else [])
    monkeypatch.setattr(db.glob, "glob", lambda _pat: [p_owner, p_unknown])
    monkeypatch.setattr(db.os.path, "isfile", lambda p: p in (p_owner, p_unknown, db.LEGACY_MINUTES_PATH))
    out = db.get_active_queue_records_global(viewer="owner@example.com", days=7, limit=10)
    assert len(out) >= 2
    assert any(x.get("job_owner") is None for x in out)
    assert any(isinstance(x.get("is_mine"), bool) for x in out)

    # get_record path missing
    monkeypatch.setattr(db.os.path, "exists", lambda _p: False)
    assert db.get_record("none", owner="x") is None

    # record_usage_job_submission guard/exception
    monkeypatch.setenv("MM_AUTH_SECRET", "secret")
    monkeypatch.setattr(db.os.path, "exists", lambda _p: False)
    db.record_usage_job_submission("t", "u@example.com", False, "bad", "", input_bytes=1)
    monkeypatch.setattr(db.os.path, "exists", lambda _p: True)

    class _ConnOpErr:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_a, **_k):
            raise sqlite3.OperationalError("x")

    monkeypatch.setattr(db.sqlite3, "connect", lambda *_a, **_k: _ConnOpErr())
    db.record_usage_job_submission("t2", "u@example.com", False, "bad", "")

    # update_usage_job_metrics guard/exception
    monkeypatch.delenv("MM_AUTH_SECRET", raising=False)
    db.update_usage_job_metrics("t", input_bytes=1)
    monkeypatch.setenv("MM_AUTH_SECRET", "secret")
    monkeypatch.setattr(db.os.path, "exists", lambda _p: False)
    db.update_usage_job_metrics("t", input_bytes=1)
    monkeypatch.setattr(db.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(db.sqlite3, "connect", lambda *_a, **_k: _ConnOpErr())
    db.update_usage_job_metrics("t", input_bytes=1)

    # guard event invalid type / exception
    db.record_usage_guard_event("unknown", "u@example.com")
    monkeypatch.setattr(db.sqlite3, "connect", lambda *_a, **_k: _ConnOpErr())
    db.record_usage_guard_event("rate_limited", "u@example.com")

    # admin_usage_summary: transcript_only rows continue / ollama count path / metrics except
    class _ConnSummary:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, *_a, **_k):
            if "SELECT transcript_only" in sql:
                return types.SimpleNamespace(fetchall=lambda: [(1, "ollama", "m", "accurate", "audio"), (0, "ollama", "m2", "accurate", "video")])
            raise sqlite3.OperationalError("x")

    monkeypatch.setattr(db.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(db.sqlite3, "connect", lambda *_a, **_k: _ConnSummary())
    s = db.admin_usage_summary(7)
    assert s["provider_ollama"]["count"] >= 1

    # usage_admin_note_get / add guard
    monkeypatch.setattr(db.sqlite3, "connect", orig_sqlite_connect)
    monkeypatch.setattr(db.os.path, "exists", lambda _p: True)
    monkeypatch.setenv("MM_AUTH_SECRET", "secret")
    with sqlite3.connect(db.REGISTRY_DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS usage_admin_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, author_email TEXT, body TEXT)"
        )
    assert db.usage_admin_note_get(999999) is None
    assert db.usage_admin_note_add("u@example.com", "   ") is None


def test_database_remaining_small_branches(isolated_db, monkeypatch):
    # _try_bootstrap_admin_registry: bcrypt import error path
    db.init_db()
    with sqlite3.connect(db.REGISTRY_DB_PATH) as conn:
        conn.execute("DELETE FROM users")
        monkeypatch.setenv("MM_BOOTSTRAP_ADMIN_USER", "boot2@example.com")
        monkeypatch.setenv("MM_BOOTSTRAP_ADMIN_PASSWORD", "password123")
        orig_import = builtins.__import__

        def _imp(name, *args, **kwargs):
            if name == "bcrypt":
                raise ImportError("x")
            return orig_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _imp)
        db._try_bootstrap_admin_registry(conn)
        monkeypatch.setattr(builtins, "__import__", orig_import)

    # get_user_by_username path missing / get_user_openai_settings exception paths
    monkeypatch.setattr(db.os.path, "exists", lambda _p: False)
    assert db.get_user_by_username("a@example.com") is None
    monkeypatch.setattr(db.os.path, "exists", lambda _p: True)

    class _Bad1(dict):
        def __bool__(self):
            return True
        def __getitem__(self, _k):
            raise KeyError("x")

    class _Bad2(dict):
        def __bool__(self):
            return True
        def __getitem__(self, k):
            if k == "openai_api_key":
                return "x"
            raise KeyError("x")

    monkeypatch.setattr(db, "get_user_by_username", lambda _u: _Bad1())
    assert db.get_user_openai_settings("x") == (None, "gpt-4o-mini")
    monkeypatch.setattr(db, "get_user_by_username", lambda _u: _Bad2())
    assert db.get_user_openai_settings("x")[1] == "gpt-4o-mini"
