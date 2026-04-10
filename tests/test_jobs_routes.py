import json
from io import BytesIO

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from backend.routes import jobs


def _upload(name: str | None, data: bytes = b"abc") -> UploadFile:
    return UploadFile(file=BytesIO(data), filename=name)


def _meta(**overrides) -> str:
    payload = {
        "notification_type": "browser",
        "llm_provider": "ollama",
        "ollama_model": "qwen2.5:7b",
        "openai_model": "gpt-4o-mini",
        "context": {},
        "whisper_preset": "accurate",
        "transcript_only": False,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


async def _create(**kwargs):
    payload = {
        "prompt_extract": None,
        "prompt_merge": None,
        "supplementary_teams": None,
        "supplementary_notes": None,
    }
    payload.update(kwargs)
    return await jobs.create_task(**payload)


@pytest.mark.anyio
async def test_supplementary_upload_ok():
    assert jobs._supplementary_upload_ok("memo.txt") is True
    assert jobs._supplementary_upload_ok("memo.md") is True
    assert jobs._supplementary_upload_ok("memo.vtt") is True
    assert jobs._supplementary_upload_ok("memo.pdf") is False
    assert jobs._supplementary_upload_ok("") is False


@pytest.mark.anyio
async def test_create_task_metadata_validation_error():
    with pytest.raises(HTTPException) as e:
        await _create(_auth="", metadata="{bad-json", file=_upload("a.wav"))
    assert e.value.status_code == 422


@pytest.mark.anyio
async def test_create_task_webhook_requires_email():
    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="u@example.com",
            metadata=_meta(notification_type="webhook", email=""),
            file=_upload("a.wav"),
        )
    assert e.value.status_code == 400
    assert "メールアドレス" in str(e.value.detail)


@pytest.mark.anyio
async def test_create_task_email_notify_checks(monkeypatch):
    monkeypatch.setattr(jobs.feature_flags, "email_notify_feature_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="u@example.com",
            metadata=_meta(notification_type="email"),
            file=_upload("a.wav"),
        )
    assert e.value.status_code == 400

    monkeypatch.setattr(jobs.feature_flags, "email_notify_feature_enabled", lambda: True)
    monkeypatch.setattr(jobs, "auth_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="",
            metadata=_meta(notification_type="email", email=""),
            file=_upload("a.wav"),
        )
    assert e.value.status_code == 400

    monkeypatch.setattr(jobs, "auth_enabled", lambda: True)
    monkeypatch.setattr(jobs.smtp_notify, "smtp_configured", lambda: False)
    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="u@example.com",
            metadata=_meta(notification_type="email"),
            file=_upload("a.wav"),
        )
    assert e.value.status_code == 503


@pytest.mark.anyio
async def test_create_task_openai_checks(monkeypatch):
    monkeypatch.setattr(jobs.feature_flags, "openai_feature_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="u@example.com",
            metadata=_meta(llm_provider="openai"),
            file=_upload("a.wav"),
        )
    assert e.value.status_code == 400

    monkeypatch.setattr(jobs.feature_flags, "openai_feature_enabled", lambda: True)
    monkeypatch.setattr(jobs, "auth_enabled", lambda: True)
    monkeypatch.setattr(jobs.db, "get_user_openai_settings", lambda _u: ("", "gpt-4o-mini"))
    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="u@example.com",
            metadata=_meta(llm_provider="openai"),
            file=_upload("a.wav"),
        )
    assert e.value.status_code == 400

    monkeypatch.setattr(jobs, "auth_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="",
            metadata=_meta(llm_provider="openai", openai_api_key=""),
            file=_upload("a.wav"),
        )
    assert e.value.status_code == 400


@pytest.mark.anyio
async def test_create_task_upload_validation():
    with pytest.raises(HTTPException) as e:
        await _create(_auth="", metadata=_meta(), file=_upload(None))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="u@example.com",
            metadata=_meta(),
            file=_upload("a.wav"),
            supplementary_teams=_upload("teams.pdf", b"t"),
        )
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        await _create(
            _auth="u@example.com",
            metadata=_meta(),
            file=_upload("a.wav"),
            supplementary_notes=_upload("notes.docx", b"n"),
        )
    assert e.value.status_code == 400


@pytest.mark.anyio
async def test_create_task_success_ollama(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jobs.uuid, "uuid4", lambda: "tid-001")
    monkeypatch.setattr(jobs, "auth_enabled", lambda: True)
    monkeypatch.setattr(jobs.feature_flags, "openai_feature_enabled", lambda: True)
    monkeypatch.setattr(jobs.feature_flags, "email_notify_feature_enabled", lambda: True)
    monkeypatch.setattr(jobs.smtp_notify, "smtp_configured", lambda: True)

    calls: dict[str, object] = {}

    def _save_initial_task(task_id, email, filename, **kwargs):
        calls["save"] = (task_id, email, filename, kwargs)

    def _record_usage(*args, **kwargs):
        calls["usage"] = (args, kwargs)

    def _merge_paths(task_id, pe, pm, st, sn):
        calls["prompt_bytes"] = (task_id, pe, pm, st, sn)
        return {"extract": "/tmp/e.txt", "merge": "/tmp/m.txt"}

    def _send_task(name, args, task_id):
        calls["celery"] = (name, args, task_id)

    monkeypatch.setattr(jobs.db, "save_initial_task", _save_initial_task)
    monkeypatch.setattr(jobs.db, "record_usage_job_submission", _record_usage)
    monkeypatch.setattr(jobs, "merge_task_prompt_paths", _merge_paths)
    monkeypatch.setattr(jobs.celery_app, "send_task", _send_task)

    res = await _create(
        _auth="owner@example.com",
        metadata=_meta(notification_type="browser", llm_provider="ollama", transcript_only=True),
        file=_upload("sample.wav", b"voice"),
        prompt_extract=_upload("extract.txt", b"pe"),
        prompt_merge=_upload("merge.txt", b"pm"),
        supplementary_teams=_upload("teams.md", b"st"),
        supplementary_notes=_upload("notes.vtt", b"sn"),
    )

    assert res["task_id"] == "tid-001"
    assert res["filename"] == "sample.wav"
    assert (tmp_path / "downloads" / "tid-001_sample.wav").exists()

    save_task_id, save_email, save_filename, save_kwargs = calls["save"]
    assert save_task_id == "tid-001"
    assert save_email == ""
    assert save_filename == "sample.wav"
    assert save_kwargs["owner"] == "owner@example.com"
    assert save_kwargs["transcript_only"] is True

    usage_args, usage_kwargs = calls["usage"]
    assert usage_args[0] == "tid-001"
    assert usage_args[1] == "owner@example.com"
    assert usage_kwargs["input_bytes"] == len(b"voice")

    p_task_id, pe, pm, st, sn = calls["prompt_bytes"]
    assert p_task_id == "tid-001"
    assert pe == b"pe"
    assert pm == b"pm"
    assert st == b"st"
    assert sn == b"sn"

    c_name, c_args, c_task_id = calls["celery"]
    assert c_name == "tasks.process_video_task"
    assert c_task_id == "tid-001"
    assert c_args[0] == "tid-001"
    assert c_args[2] == "sample.wav"
    assert c_args[7] == "owner@example.com"
    assert c_args[5]["provider"] == "ollama"
    assert c_args[5]["transcript_only"] is True


@pytest.mark.anyio
async def test_create_task_success_openai_no_auth(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jobs.uuid, "uuid4", lambda: "tid-002")
    monkeypatch.setattr(jobs, "auth_enabled", lambda: False)
    monkeypatch.setattr(jobs.feature_flags, "openai_feature_enabled", lambda: True)

    calls: dict[str, object] = {}
    monkeypatch.setattr(jobs.db, "save_initial_task", lambda *a, **k: calls.setdefault("save", (a, k)))
    monkeypatch.setattr(jobs, "merge_task_prompt_paths", lambda *a, **k: {})
    monkeypatch.setattr(jobs.celery_app, "send_task", lambda *a, **k: calls.setdefault("celery", (a, k)))

    res = await _create(
        _auth="",
        metadata=_meta(
            llm_provider="openai",
            openai_api_key="sk-test",
            openai_model="gpt-4o-mini",
            notification_type="webhook",
            email="notify@example.com",
        ),
        file=_upload("openai.wav", b"data"),
    )

    assert res == {"task_id": "tid-002", "filename": "openai.wav"}
    c_kwargs = calls["celery"][1]
    assert c_kwargs["args"][1] == "notify@example.com"  # email_for_worker
    assert c_kwargs["args"][5]["provider"] == "openai"
    assert c_kwargs["args"][5]["api_key"] == "sk-test"


@pytest.mark.anyio
async def test_create_task_success_email_openai_with_saved_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jobs.uuid, "uuid4", lambda: "tid-003")
    monkeypatch.setattr(jobs, "auth_enabled", lambda: True)
    monkeypatch.setattr(jobs.feature_flags, "email_notify_feature_enabled", lambda: True)
    monkeypatch.setattr(jobs.feature_flags, "openai_feature_enabled", lambda: True)
    monkeypatch.setattr(jobs.smtp_notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(jobs.db, "get_user_openai_settings", lambda _u: ("sk-saved", "gpt-saved"))

    calls: dict[str, object] = {}

    monkeypatch.setattr(jobs.db, "save_initial_task", lambda *a, **k: calls.setdefault("save", (a, k)))
    monkeypatch.setattr(jobs.db, "record_usage_job_submission", lambda *a, **k: calls.setdefault("usage", (a, k)))
    monkeypatch.setattr(jobs, "merge_task_prompt_paths", lambda *a, **k: {})
    monkeypatch.setattr(jobs.celery_app, "send_task", lambda *a, **k: calls.setdefault("celery", (a, k)))

    res = await _create(
        _auth="owner@example.com",
        metadata=_meta(
            notification_type="email",
            llm_provider="openai",
            email="",  # auth ON + ownerありなので owner が宛先に使われる
        ),
        file=_upload("saved-key.wav", b"xyz"),
    )

    assert res == {"task_id": "tid-003", "filename": "saved-key.wav"}

    # email 通知分岐: record_email/email_for_worker ともに owner が採用される
    save_args = calls["save"][0]
    assert save_args[1] == "owner@example.com"

    celery_kwargs = calls["celery"][1]
    assert celery_kwargs["args"][1] == "owner@example.com"

    # openai 分岐: 保存済みキー/モデルが llm_config に反映される
    llm_cfg = celery_kwargs["args"][5]
    assert llm_cfg["provider"] == "openai"
    assert llm_cfg["api_key"] == "sk-saved"
    assert llm_cfg["openai_model"] == "gpt-saved"
