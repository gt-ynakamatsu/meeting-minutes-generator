import importlib
import sys
import types


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _SessionState(dict):
    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value


class _Uploaded:
    def __init__(self, name: str, body: bytes):
        self.name = name
        self._body = body

    def getbuffer(self):
        return self._body


def _install_fake_modules(
    monkeypatch,
    tmp_path,
    *,
    submit: bool,
    auth_on: bool,
    openai_on: bool = False,
    provider_choice: str = "ローカル（Ollama）",
    notify_choice: str = "ブラウザ",
    webhook_email: str = "u@example.com",
    queue_rows=None,
    recent_rows=None,
    pending_status: str = "processing:extracting",
    preset_pending: bool = True,
):
    calls = {"saved": 0, "usage": 0, "delay": 0}

    st_mod = types.ModuleType("streamlit")
    st_mod.session_state = _SessionState()
    st_mod.sidebar = _Ctx()
    if preset_pending:
        st_mod.session_state.pending_tasks = ["tid_done", "tid_err"] if pending_status == "mix" else []

    upload = _Uploaded("sample.txt", b"hello\n\nworld") if submit else None

    def _columns(spec):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx() for _ in range(n)]

    st_mod.set_page_config = lambda **_k: None
    st_mod.columns = _columns
    st_mod.image = lambda *_a, **_k: None
    st_mod.markdown = lambda *_a, **_k: None
    st_mod.divider = lambda: None
    st_mod.caption = lambda *_a, **_k: None
    st_mod.info = lambda *_a, **_k: None
    st_mod.success = lambda *_a, **_k: None
    st_mod.balloons = lambda: None
    st_mod.subheader = lambda *_a, **_k: None
    st_mod.write = lambda *_a, **_k: None
    st_mod.progress = lambda *_a, **_k: None
    st_mod.toast = lambda *_a, **_k: None
    st_mod.error = lambda *_a, **_k: None
    st_mod.download_button = lambda *_a, **_k: None
    st_mod.rerun = lambda: None
    st_mod.expander = lambda *_a, **_k: _Ctx()
    st_mod.tabs = lambda labels: [_Ctx() for _ in labels]
    st_mod.form = lambda *_a, **_k: _Ctx()
    st_mod.form_submit_button = lambda *_a, **_k: False
    st_mod.text_area = lambda *_a, **_k: ""
    st_mod.checkbox = lambda *_a, **_k: submit is False
    st_mod.button = lambda *_a, **_k: submit
    st_mod.file_uploader = lambda *a, **k: upload if ("動画" in (a[0] if a else "") and "key" not in k) else None

    def _text_input(label, *_, key=None, **__):
        if "Webhook URL" in label:
            return "http://example.test/webhook"
        if "メールアドレス" in label:
            return webhook_email
        if "OpenAI API キー" in label:
            return ""
        if "Ollama モデル名" in label:
            return "qwen2.5:7b"
        return ""

    st_mod.text_input = _text_input
    st_mod.selectbox = lambda _label, options, index=0, **_k: options[index]

    def _radio(label, options, index=0, **_k):
        if "AI の接続先" in label:
            return provider_choice
        if "完了時の通知" in label:
            return notify_choice
        return options[index]

    st_mod.radio = _radio

    monkeypatch.setitem(sys.modules, "streamlit", st_mod)

    auto_mod = types.ModuleType("streamlit_autorefresh")
    auto_mod.st_autorefresh = lambda **_k: None
    monkeypatch.setitem(sys.modules, "streamlit_autorefresh", auto_mod)

    db_mod = types.ModuleType("database")
    db_mod.init_db = lambda: None
    db_mod.save_initial_task = lambda *_a, **_k: calls.update({"saved": calls["saved"] + 1})
    db_mod.record_usage_job_submission = lambda *_a, **_k: calls.update({"usage": calls["usage"] + 1})
    def _get_record(tid, *_a, **_k):
        if pending_status == "mix":
            st = "completed" if "done" in str(tid) else "Error: x"
        else:
            st = pending_status
        return {
            "status": st,
            "id": "rid1",
            "filename": "sample.txt",
            "topic": "",
            "category": "",
            "tags": "",
            "preset_id": "",
            "meeting_date": "",
            "transcript": "raw text",
            "summary": "summary text",
            "created_at": "2026-01-01 00:00:00",
        }

    db_mod.get_record = _get_record
    db_mod.get_active_queue_records = lambda *_a, **_k: queue_rows or []
    db_mod.get_active_queue_records_global = lambda *_a, **_k: queue_rows or []
    db_mod.get_recent_records = lambda *_a, **_k: recent_rows or []
    db_mod.parse_context_json = lambda *_a, **_k: {}
    db_mod.update_record = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "database", db_mod)

    ff_mod = types.ModuleType("feature_flags")
    ff_mod.openai_feature_enabled = lambda: openai_on
    monkeypatch.setitem(sys.modules, "feature_flags", ff_mod)

    backend_pkg = types.ModuleType("backend")
    auth_mod = types.ModuleType("backend.auth_settings")
    auth_mod.auth_enabled = lambda: auth_on
    presets_mod = types.ModuleType("backend.presets_io")
    presets_mod.preset_options_for_ui = lambda: [("standard", "標準")]
    monkeypatch.setitem(sys.modules, "backend", backend_pkg)
    monkeypatch.setitem(sys.modules, "backend.auth_settings", auth_mod)
    monkeypatch.setitem(sys.modules, "backend.presets_io", presets_mod)

    tasks_mod = types.ModuleType("tasks")

    class _P:
        @staticmethod
        def delay(*_a, **_k):
            calls["delay"] += 1

    tasks_mod.process_video_task = _P()
    monkeypatch.setitem(sys.modules, "tasks", tasks_mod)

    const_mod = types.ModuleType("streamlit_app.constants")
    logo = tmp_path / "logo.svg"
    logo.write_text("<svg/>", encoding="utf-8")
    const_mod.LOGO_SVG = str(logo)
    render_mod = types.ModuleType("streamlit_app.render")
    render_mod.render_error_hints = lambda *_a, **_k: None
    render_mod.render_minutes = lambda *_a, **_k: None
    render_mod.save_uploaded_prompts = lambda *_a, **_k: {}
    style_mod = types.ModuleType("streamlit_app.styles")
    style_mod.inject_ui_styles = lambda: None
    status_mod = types.ModuleType("streamlit_app.task_status")
    status_mod.progress_for_task_status = lambda *_a, **_k: (50, "processing")
    monkeypatch.setitem(sys.modules, "streamlit_app.constants", const_mod)
    monkeypatch.setitem(sys.modules, "streamlit_app.render", render_mod)
    monkeypatch.setitem(sys.modules, "streamlit_app.styles", style_mod)
    monkeypatch.setitem(sys.modules, "streamlit_app.task_status", status_mod)

    ver_mod = types.ModuleType("version")
    ver_mod.__version__ = "test"
    monkeypatch.setitem(sys.modules, "version", ver_mod)

    return calls


def test_app_import_smoke_no_submit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _install_fake_modules(monkeypatch, tmp_path, submit=False, auth_on=False, preset_pending=False)
    sys.modules.pop("app", None)
    importlib.import_module("app")
    assert calls["saved"] == 0
    assert calls["usage"] == 0
    assert calls["delay"] == 0


def test_app_submit_flow_calls_queue_and_usage(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _install_fake_modules(monkeypatch, tmp_path, submit=True, auth_on=True)
    sys.modules.pop("app", None)
    importlib.import_module("app")
    assert calls["saved"] == 1
    assert calls["usage"] == 1
    assert calls["delay"] == 1


def test_app_openai_webhook_and_archive_branches(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    queue_rows = [
        {
            "topic": "q",
            "filename": "f.mp4",
            "status": "processing",
            "created_at": "2026-01-01 00:00:00",
            "job_owner": "user@example.com",
            "email": "user@example.com",
        }
    ]
    recent_rows = [
        {
            "id": "rid1",
            "created_at": "2026-01-01 00:00:00",
            "topic": "topic",
            "filename": "f.mp4",
            "status": "completed",
            "summary": "sum",
            "transcript": "tr",
            "category": "社内",
            "tags": "a",
            "preset_id": "standard",
            "meeting_date": "2026-01-01",
        },
        {
            "id": "rid2",
            "created_at": "2026-01-01 00:00:00",
            "topic": "topic",
            "filename": "e.mp4",
            "status": "Error: x",
            "summary": "",
            "transcript": "",
            "category": "",
            "tags": "",
            "preset_id": "",
            "meeting_date": "",
        },
    ]
    _install_fake_modules(
        monkeypatch,
        tmp_path,
        submit=False,
        auth_on=True,
        openai_on=True,
        provider_choice="OpenAI API",
        notify_choice="Webhook",
        webhook_email="",
        queue_rows=queue_rows,
        recent_rows=recent_rows,
        pending_status="mix",
    )
    sys.modules.pop("app", None)
    importlib.import_module("app")


def test_app_non_auth_queue_and_context_edit_branches(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    queue_rows = [
        {
            "topic": "q",
            "filename": "f.mp4",
            "status": "processing",
            "created_at": "2026-01-01 00:00:00",
        }
    ]
    recent_rows = [
        {
            "id": "rid1",
            "created_at": "2026-01-01 00:00:00",
            "topic": "topic",
            "filename": "f.mp4",
            "status": "completed",
            "summary": "sum",
            "transcript": "tr",
            "category": "社内",
            "tags": "a",
            "preset_id": "standard",
            "meeting_date": "2026-01-01",
        },
        {
            "id": "rid2",
            "created_at": "2026-01-01 00:00:00",
            "topic": "topic",
            "filename": "p.mp4",
            "status": "processing",
            "summary": "",
            "transcript": "",
            "category": "",
            "tags": "",
            "preset_id": "",
            "meeting_date": "",
        },
    ]
    _install_fake_modules(
        monkeypatch,
        tmp_path,
        submit=True,
        auth_on=False,
        openai_on=True,
        provider_choice="OpenAI API",
        notify_choice="ブラウザ",
        queue_rows=queue_rows,
        recent_rows=recent_rows,
        pending_status="mix",
    )
    # context表示分岐 + 保存分岐
    import database
    database.parse_context_json = lambda *_a, **_k: {
        "purpose": "p",
        "participants": "u",
        "glossary": "g",
        "tone": "t",
        "action_rules": "a",
    }
    import streamlit
    streamlit.form_submit_button = lambda *_a, **_k: True
    sys.modules.pop("app", None)
    importlib.import_module("app")
