from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.routes import records
from backend.schemas import SummaryPatch


def test_queue_row_for_api_defaults_and_overrides():
    row = {"status": "completed", "transcript": "abc", "summary": "s", "x": 1}
    d = records._queue_row_for_api(row)
    assert d["transcript_ready"] is True
    assert "transcript" not in d
    assert "summary" not in d
    assert d["job_owner"] is None
    assert d["is_mine"] is True

    d2 = records._queue_row_for_api(
        {"status": "processing:transcribing", "transcript": "abc"},
        job_owner="u@example.com",
        is_mine=False,
    )
    assert d2["transcript_ready"] is False
    assert d2["job_owner"] == "u@example.com"
    assert d2["is_mine"] is False


def test_queue_row_for_api_non_dict_branch(monkeypatch):
    fake_row = object()
    monkeypatch.setattr(records, "sqlite_row_to_dict", lambda _r: {"status": "", "transcript": ""})
    d = records._queue_row_for_api(fake_row)
    assert d["transcript_ready"] is False


def test_list_records(monkeypatch):
    calls = {}

    def _count(owner, **kw):
        calls["count"] = (owner, kw)
        return 2

    def _rows(owner, **kw):
        calls["rows"] = (owner, kw)
        return [{"id": "t1"}, {"id": "t2"}]

    monkeypatch.setattr(records.db, "count_recent_records", _count)
    monkeypatch.setattr(records.db, "get_recent_records", _rows)
    monkeypatch.setattr(records, "sqlite_row_to_dict", lambda r: dict(r))

    res = records.list_records("owner@example.com", days=30, search="x", category="c", status_filter="processing", limit=10, offset=5)
    assert res.total == 2
    assert len(res.items) == 2
    assert calls["count"][0] == "owner@example.com"
    assert calls["rows"][1]["offset"] == 5


def test_queue_records_auth_on(monkeypatch):
    monkeypatch.setattr(records, "auth_enabled", lambda: True)
    monkeypatch.setattr(
        records.db,
        "get_active_queue_records_global",
        lambda **kw: [{"status": "completed", "transcript": "ok", "job_owner": "u", "is_mine": False}],
    )
    rows = records.queue_records("viewer@example.com")
    assert len(rows) == 1
    assert rows[0]["transcript_ready"] is True


def test_queue_records_auth_off(monkeypatch):
    monkeypatch.setattr(records, "auth_enabled", lambda: False)
    monkeypatch.setattr(records.db, "get_active_queue_records", lambda owner, **kw: [{"status": "", "transcript": ""}])
    rows = records.queue_records("viewer@example.com")
    assert len(rows) == 1
    assert rows[0]["is_mine"] is True


def test_get_record_found_and_not_found(monkeypatch):
    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: {"id": "t1"})
    monkeypatch.setattr(records, "sqlite_row_to_dict", lambda r: dict(r))
    assert records.get_record("t1", "u@example.com")["id"] == "t1"

    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: None)
    with pytest.raises(HTTPException) as e:
        records.get_record("t404", "u@example.com")
    assert e.value.status_code == 404


def test_discard_record_paths(monkeypatch):
    calls = {"removed": 0, "cleaned": 0, "revoked": 0}

    monkeypatch.setattr(records.db, "discard_task", lambda *_a, **_k: None)
    monkeypatch.setattr(records.db, "remove_task_upload_files", lambda _t: calls.__setitem__("removed", calls["removed"] + 1))
    monkeypatch.setattr(records.db, "cleanup_user_prompts_dir", lambda _t: calls.__setitem__("cleaned", calls["cleaned"] + 1))
    monkeypatch.setattr(
        records.celery_app,
        "control",
        SimpleNamespace(revoke=lambda *a, **k: calls.__setitem__("revoked", calls["revoked"] + 1)),
    )
    assert records.discard_record("tid", "owner") == {"ok": True}
    assert calls == {"removed": 1, "cleaned": 1, "revoked": 1}

    monkeypatch.setattr(records.db, "discard_task", lambda *_a, **_k: (_ for _ in ()).throw(KeyError("x")))
    with pytest.raises(HTTPException) as e:
        records.discard_record("tid", "owner")
    assert e.value.status_code == 404

    monkeypatch.setattr(records.db, "discard_task", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as e:
        records.discard_record("tid", "owner")
    assert e.value.status_code == 400

    monkeypatch.setattr(records.db, "discard_task", lambda *_a, **_k: None)
    monkeypatch.setattr(records.celery_app, "control", SimpleNamespace(revoke=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ng"))))
    assert records.discard_record("tid", "owner") == {"ok": True}


def test_export_minutes(monkeypatch):
    monkeypatch.setattr(records.db, "minutes_db_path", lambda _o: "db-path")
    monkeypatch.setattr(records.db, "purge_expired_minutes_db_path", lambda _p: None)

    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: None)
    with pytest.raises(HTTPException) as e:
        records.export_minutes("tid", "owner")
    assert e.value.status_code == 404

    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: {"summary": "", "filename": "a.wav"})
    with pytest.raises(HTTPException) as e:
        records.export_minutes("tid", "owner")
    assert e.value.status_code == 404

    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: {"summary": "## minutes", "filename": "a.wav"})
    res = records.export_minutes("tid", "owner")
    assert res.media_type.startswith("text/markdown")
    assert b"## minutes" in res.body
    assert "attachment;" in res.headers["content-disposition"]


def test_export_transcript(monkeypatch):
    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: None)
    with pytest.raises(HTTPException) as e:
        records.export_transcript("tid", "owner")
    assert e.value.status_code == 404

    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: {"transcript": "hello", "filename": "x.mp3"})
    res = records.export_transcript("tid", "owner")
    assert res.media_type.startswith("text/plain")
    assert res.body == b"hello"
    assert ".txt" in res.headers["content-disposition"]


def test_export_transcript_md_branches(monkeypatch):
    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: None)
    with pytest.raises(HTTPException) as e:
        records.export_transcript_md("tid", "owner")
    assert e.value.status_code == 404

    monkeypatch.setattr(
        records.db,
        "get_record",
        lambda *_a, **_k: {"status": "processing:transcribing", "transcript": "", "filename": "a.wav", "summary": ""},
    )
    with pytest.raises(HTTPException) as e:
        records.export_transcript_md("tid", "owner")
    assert e.value.status_code == 404
    assert "まだ完了" in str(e.value.detail)

    monkeypatch.setattr(
        records.db,
        "get_record",
        lambda *_a, **_k: {"status": "completed", "transcript": "  ", "filename": "a.wav", "summary": ""},
    )
    with pytest.raises(HTTPException) as e:
        records.export_transcript_md("tid", "owner")
    assert e.value.status_code == 404

    monkeypatch.setattr(
        records.db,
        "get_record",
        lambda *_a, **_k: {
            "status": "cancelled",
            "transcript": "text body",
            "filename": "a.wav",
            "summary": "【処理エラー】x",
        },
    )
    res = records.export_transcript_md("tid", "owner")
    txt = res.body.decode("utf-8")
    assert "文字起こしまで完了" in txt
    assert "transcript_a.md" in res.headers["content-disposition"]

    monkeypatch.setattr(
        records.db,
        "get_record",
        lambda *_a, **_k: {
            "status": "completed",
            "transcript": "text body",
            "filename": "b.wav",
            "summary": "",
        },
    )
    res2 = records.export_transcript_md("tid", "owner")
    txt2 = res2.body.decode("utf-8")
    assert "Whisper 等による自動文字起こし" in txt2


def test_patch_summary(monkeypatch):
    calls = {"purge": 0, "update": 0}
    monkeypatch.setattr(records.db, "minutes_db_path", lambda _o: "db-path")
    monkeypatch.setattr(records.db, "purge_expired_minutes_db_path", lambda _p: calls.__setitem__("purge", calls["purge"] + 1))

    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: None)
    with pytest.raises(HTTPException) as e:
        records.patch_summary("tid", SummaryPatch(summary="s"), "owner")
    assert e.value.status_code == 404

    monkeypatch.setattr(records.db, "get_record", lambda *_a, **_k: {"id": "tid"})
    monkeypatch.setattr(records.db, "update_record", lambda *_a, **_k: calls.__setitem__("update", calls["update"] + 1))
    assert records.patch_summary("tid", SummaryPatch(summary="new"), "owner") == {"ok": True}
    assert calls["purge"] >= 2
    assert calls["update"] == 1
