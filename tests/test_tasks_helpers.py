import importlib
import builtins
import json
import sys
import types
from types import SimpleNamespace

import pytest


@pytest.fixture
def tasks_mod(monkeypatch):
    import time as _time

    # tasks.py import 時の待機を無効化
    monkeypatch.setattr(_time, "sleep", lambda _n: None)

    # 外部依存を軽量スタブ化
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = object
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = object
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = SimpleNamespace(is_available=lambda: False, synchronize=lambda: None, empty_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    fake_moviepy = types.ModuleType("moviepy")
    fake_moviepy_editor = types.ModuleType("moviepy.editor")
    fake_moviepy_editor.AudioFileClip = object
    fake_moviepy_editor.VideoFileClip = object
    monkeypatch.setitem(sys.modules, "moviepy", fake_moviepy)
    monkeypatch.setitem(sys.modules, "moviepy.editor", fake_moviepy_editor)

    fake_celery_mod = types.ModuleType("celery_app")

    class _FakeCelery:
        def task(self, fn=None):
            if fn is None:
                return lambda f: f
            return fn

        def send_task(self, *args, **kwargs):
            return None

    fake_celery_mod.celery_app = _FakeCelery()
    monkeypatch.setitem(sys.modules, "celery_app", fake_celery_mod)

    sys.modules.pop("tasks", None)
    return importlib.import_module("tasks")


def test_timeout_and_whisper_options(tasks_mod, monkeypatch):
    monkeypatch.delenv("MM_OLLAMA_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("MM_OLLAMA_MERGE_TIMEOUT_SEC", raising=False)
    assert tasks_mod._ollama_http_timeout() == (30, 600)

    monkeypatch.setenv("MM_OLLAMA_TIMEOUT_SEC", "30")
    assert tasks_mod._ollama_http_timeout() == (30, 60)
    monkeypatch.setenv("MM_OLLAMA_TIMEOUT_SEC", "abc")
    assert tasks_mod._ollama_http_timeout() == (30, 600)

    monkeypatch.setenv("MM_OLLAMA_MERGE_TIMEOUT_SEC", "120")
    assert tasks_mod._ollama_http_timeout("merge") == (30, 120)
    monkeypatch.setenv("MM_OLLAMA_MERGE_TIMEOUT_SEC", "abc")
    assert tasks_mod._ollama_http_timeout("merge") == (30, 600)

    assert tasks_mod._whisper_transcribe_options("fast")["beam_size"] == 1
    assert tasks_mod._whisper_transcribe_options("accurate")["beam_size"] == 10
    assert tasks_mod._whisper_transcribe_options("balanced") == {}


def test_row_context_and_presets(tasks_mod, monkeypatch):
    rec = {"topic": "議題", "meeting_date": "2026-04-10", "category": "定例", "tags": "a,b", "preset_id": "x"}
    monkeypatch.setattr(
        tasks_mod.db,
        "parse_context_json",
        lambda _r: {
            "purpose": "目的",
            "participants": "A,B",
            "glossary": "用語",
            "tone": "丁寧",
            "action_rules": "期限必須",
        },
    )
    block = tasks_mod.build_meeting_context_block(rec)
    assert "会議コンテキスト" in block
    assert "議題: 議題" in block
    assert "会議の目的: 目的" in block

    monkeypatch.setattr(tasks_mod, "load_builtin_presets", lambda: {"standard": {"extract_hint": "e", "merge_hint": "m"}})
    eh, mh = tasks_mod.preset_hints_for_record({"preset_id": "missing"})
    assert (eh, mh) == ("e", "m")
    assert tasks_mod._row_str(None, "x", "d") == "d"
    assert tasks_mod._row_str({}, "x", "d") == "d"
    class _Idx:
        def __getitem__(self, _k):
            raise IndexError("x")
    assert tasks_mod._row_str(_Idx(), "x", "d") == "d"
    assert tasks_mod.build_meeting_context_block({}) == ""


def test_normalize_segments_and_chunks(tasks_mod):
    segs = tasks_mod.normalize_to_segments(
        [
            SimpleNamespace(start=0.0, end=1.2, text=" hello "),
            {"start": 1.3, "end": 2.2, "text": " world "},
        ]
    )
    assert len(segs) == 2
    assert segs[0]["text"] == "hello"

    srt = "1\n00:00:00,000 --> 00:00:02,000\nA\n\n2\n00:00:02,000 --> 00:00:04,000\nB"
    srt_segs = tasks_mod.normalize_to_segments(srt)
    assert len(srt_segs) == 2

    plain = tasks_mod.normalize_to_segments("foo\n\nbar")
    assert [x["text"] for x in plain] == ["foo", "bar"]

    chunks, raw = tasks_mod.build_chunks_from_segments(srt_segs, chunk_sec=1, char_chunk=1000)
    assert len(chunks) >= 1
    assert "[00:00:00-00:00:02]" in raw

    text_only = [{"start": 0.0, "end": 0.0, "text": "x" * 15}]
    chunks2, raw2 = tasks_mod.build_chunks_from_segments(text_only, chunk_sec=10, char_chunk=10)
    assert len(chunks2) == 2
    assert raw2 == "x" * 15
    assert tasks_mod.build_chunks_from_segments([]) == ([], "")


def test_json_and_supplementary_helpers(tasks_mod, tmp_path, monkeypatch):
    assert tasks_mod.extract_json_block('{"a":1}') == {"a": 1}
    assert tasks_mod.extract_json_block("prefix\n{\"a\":2}\nsuffix") == {"a": 2}
    assert tasks_mod.extract_json_block("{bad") is None
    assert tasks_mod.extract_json_block("xx {bad} yy") is None
    assert tasks_mod.extract_json_block("not-json") is None
    assert tasks_mod.format_timestamp(3661) == "01:01:01"

    vtt = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nhello\nNOTE x\n"
    assert tasks_mod._strip_webvtt_to_plain(vtt) == "hello"

    teams = tmp_path / "teams.vtt"
    notes = tmp_path / "notes.txt"
    teams.write_text(vtt, encoding="utf-8")
    notes.write_text("memo", encoding="utf-8")

    monkeypatch.setattr(tasks_mod, "SUPPLEMENTARY_MAX_CHARS", 20)
    sup = tasks_mod._build_supplementary_reference_text(str(teams), str(notes))
    assert "Teams 等のトランスクリプト" in sup
    assert "以下省略" in sup

    ex = tasks_mod._inject_supplementary_extract("AA {CHUNK_TEXT} BB", "SUP", "body")
    assert "SUP" in ex and "{CHUNK_TEXT}" in ex
    ex2 = tasks_mod._inject_supplementary_extract("{SUPPLEMENTARY_REFERENCE}", "SUP", "body")
    assert ex2 == "SUP"

    mg = tasks_mod._inject_supplementary_merge("{EXTRACTED_JSON}", "SUP", "body")
    assert "参考資料" in mg
    mg2 = tasks_mod._inject_supplementary_merge("{SUPPLEMENTARY_REFERENCE}", "SUP", "body")
    assert mg2 == "SUP"
    mg3 = tasks_mod._inject_supplementary_merge("plain", "SUP", "body")
    assert "SUP" in mg3

    assert tasks_mod._media_duration_sec_from_segments([{"end": 12.3}, {"end": 4}]) == 12.3
    assert tasks_mod._media_duration_sec_from_segments(["x"]) == 0.0


def test_extract_schema_retry_helper_and_validation(tasks_mod, monkeypatch):
    monkeypatch.delenv("MM_EXTRACT_SCHEMA_RETRY", raising=False)
    assert tasks_mod._extract_retry_max() == 1
    monkeypatch.setenv("MM_EXTRACT_SCHEMA_RETRY", "3")
    assert tasks_mod._extract_retry_max() == 3
    monkeypatch.setenv("MM_EXTRACT_SCHEMA_RETRY", "999")
    assert tasks_mod._extract_retry_max() == 3
    monkeypatch.setenv("MM_EXTRACT_SCHEMA_RETRY", "abc")
    assert tasks_mod._extract_retry_max() == 1

    assert tasks_mod._shorten_text("abc", 10) == "abc"
    short = tasks_mod._shorten_text("abcdef", 3)
    assert short.startswith("abc")
    assert short != "abcdef"

    ok, errs = tasks_mod._validate_extraction_payload({"decisions": [], "issues": [], "items": [], "notes": []})
    assert ok is True
    assert errs == []

    ok2, errs2 = tasks_mod._validate_extraction_payload("bad")
    assert ok2 is False
    assert "トップレベルはJSONオブジェクト" in errs2[0]

    ok3, errs3 = tasks_mod._validate_extraction_payload(
        {
            "decisions": [{"text": "", "evidence": []}],
            "issues": [{"text": "i", "evidence": [""]}],
            "items": [{"who": 1, "what": "", "due": 2, "evidence": []}],
            "notes": [{"text": "", "evidence": []}],
        }
    )
    assert ok3 is False
    assert any("decisions[0].text" in e for e in errs3)
    assert any("items[0].who" in e for e in errs3)
    assert any("notes[0].text" in e or "notes[0].evidence" in e for e in errs3)


def test_small_side_effect_helpers(tasks_mod, tmp_path, monkeypatch):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("a", encoding="utf-8")
    f2.write_text("b", encoding="utf-8")
    tasks_mod._remove_files(str(f1), str(f2), None, str(tmp_path / "none.txt"))
    assert not f1.exists() and not f2.exists()

    called = {}
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda tid, **kw: called.update({"tid": tid, "kw": kw}))
    tasks_mod._safe_update_usage_metrics("t1", x=1)
    assert called["tid"] == "t1"
    assert called["kw"]["x"] == 1
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    tasks_mod._safe_update_usage_metrics("t2", y=2)  # 例外を飲み込む
    monkeypatch.setattr(tasks_mod.os.path, "isdir", lambda _p: True)
    monkeypatch.setattr(tasks_mod.shutil, "rmtree", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    tasks_mod._cleanup_user_prompts("t3")

    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: None)
    assert tasks_mod._record_cancelled("t4", "") is False

    called = {"m": None}
    monkeypatch.setattr(tasks_mod, "try_ollama_unload_model", lambda m: called.update({"m": m}))
    tasks_mod._try_ollama_unload_for_config(None, "")
    assert called["m"] == tasks_mod.DEFAULT_OLLAMA_MODEL
    tasks_mod._try_ollama_unload_for_config({"provider": "openai"}, "x")

    monkeypatch.setattr(tasks_mod.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(tasks_mod.os, "remove", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    tasks_mod._remove_files("a")

    monkeypatch.setattr(tasks_mod, "_cleanup_user_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_remove_files", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    tasks_mod._cleanup_after_cancel("tid", "", "f", None, {}, "")

    assert tasks_mod._assemble_prompt_with_context("base", {}, "", "h") == "base"
    assert "h\nhint" in tasks_mod._assemble_prompt_with_context("base", {"topic": "t"}, "hint", "h")
    assert "会議コンテキスト" in tasks_mod._assemble_prompt_with_context("base", {"topic": "t"}, "", "h")

    p = tmp_path / "p.txt"
    p.write_text("abc", encoding="utf-8")
    assert tasks_mod.load_prompt(str(p)) == "abc"
    monkeypatch.setattr(tasks_mod.os.path, "exists", lambda _p: False)
    assert tasks_mod.load_prompt(str(tmp_path / "none.txt")) == ""

    assert tasks_mod._final_webhook_url("http://x") == "http://x"
    assert "書き起こし完了" in tasks_mod._completion_message("transcript_only", "a.wav")
    assert "議事録作成完了" in tasks_mod._completion_message("minutes", "a.wav")


def test_call_llm_branches(tasks_mod, monkeypatch):
    monkeypatch.setattr(tasks_mod.feature_flags, "openai_feature_enabled", lambda: False)
    with pytest.raises(Exception):
        tasks_mod.call_llm("p", {"provider": "openai", "api_key": "k"}, json_mode=False)

    monkeypatch.setattr(tasks_mod.feature_flags, "openai_feature_enabled", lambda: True)

    class _OpenAIClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_k: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
                )
            )

    monkeypatch.setattr(tasks_mod, "OpenAI", _OpenAIClient)
    assert tasks_mod.call_llm("p", {"provider": "openai", "api_key": "k"}, json_mode=False) == "ok"

    class _OpenAIFail:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_k: (_ for _ in ()).throw(RuntimeError("ng"))))

    monkeypatch.setattr(tasks_mod, "OpenAI", _OpenAIFail)
    with pytest.raises(Exception):
        tasks_mod.call_llm("p", {"provider": "openai", "api_key": "k"}, json_mode=False)

    monkeypatch.setattr(tasks_mod, "resolve_ollama_options", lambda *_a, **_k: {"temperature": 0.1})

    class _ResOK:
        status_code = 200

        @staticmethod
        def json():
            return {"response": "r1"}

    monkeypatch.setattr(tasks_mod.requests, "post", lambda *a, **k: _ResOK())
    assert tasks_mod.call_llm("p", {"provider": "ollama", "ollama_model": "m"}) == "r1"

    class _ResErr:
        status_code = 500
        text = "boom"

        @staticmethod
        def json():
            return {"error": "bad"}

    monkeypatch.setattr(tasks_mod.requests, "post", lambda *a, **k: _ResErr())
    with pytest.raises(Exception):
        tasks_mod.call_llm("p", {"provider": "ollama", "ollama_model": "m"})

    class _ResErrRaw:
        status_code = 500
        text = "x" * 1500

        @staticmethod
        def json():
            raise RuntimeError("bad-json")

    monkeypatch.setattr(tasks_mod.requests, "post", lambda *a, **k: _ResErrRaw())
    with pytest.raises(Exception):
        tasks_mod.call_llm("p", {"provider": "ollama", "ollama_model": "m"})


def test_process_video_task_transcript_only_contract(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.txt"
    src.write_text("line1\n\nline2", encoding="utf-8")

    state = {
        "status": "pending",
        "topic": "",
        "meeting_date": "",
        "category": "",
        "tags": "",
        "preset_id": "",
        "context_json": "",
        "summary": "",
        "transcript": "",
    }
    updates = []
    metrics = {}
    completion = {}

    def _get_record(_task_id, _owner=""):
        return state

    def _update_record(_task_id, _owner="", status=None, transcript=None, summary=None):
        if status is not None:
            state["status"] = status
        if transcript is not None:
            state["transcript"] = transcript
        if summary is not None:
            state["summary"] = summary
        updates.append({"status": status, "transcript": transcript, "summary": summary})

    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", _get_record)
    monkeypatch.setattr(tasks_mod.db, "update_record", _update_record)
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda _tid, **kw: metrics.update(kw))
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks_mod,
        "_notify_task_completion",
        lambda *a, **k: completion.update({"args": a, "kwargs": k}),
    )

    tasks_mod.process_video_task(
        "tid1",
        "u@example.com",
        "input.txt",
        str(src),
        llm_config={"notification_type": "browser", "transcript_only": True},
        owner_username="owner@example.com",
    )

    assert state["status"] == "completed"
    assert "書き起こしのみ完了" in state["summary"]
    assert "line1" in state["transcript"]
    assert metrics["transcript_chars"] > 0
    assert src.exists() is False
    assert completion.get("args")
    assert any(u["status"] == "processing:reading_transcript" for u in updates)


def test_process_video_task_minutes_success_contract(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.txt"
    src.write_text("line1\n\nline2", encoding="utf-8")
    p_ex = tmp_path / "extract.txt"
    p_mg = tmp_path / "merge.txt"
    p_ex.write_text("EX:{CHUNK_TEXT}", encoding="utf-8")
    p_mg.write_text("MG:{EXTRACTED_JSON}", encoding="utf-8")

    state = {
        "status": "pending",
        "topic": "",
        "meeting_date": "",
        "category": "",
        "tags": "",
        "preset_id": "",
        "context_json": "",
        "summary": "",
        "transcript": "",
    }
    metrics = {}
    notify = {"ok": 0}

    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(
        tasks_mod.db,
        "update_record",
        lambda _tid, _owner="", status=None, transcript=None, summary=None: state.update(
            {
                **({"status": status} if status is not None else {}),
                **({"transcript": transcript} if transcript is not None else {}),
                **({"summary": summary} if summary is not None else {}),
            }
        ),
    )
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda _tid, **kw: metrics.update(kw))
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_notify_task_completion", lambda *_a, **_k: notify.update({"ok": notify["ok"] + 1}))

    def _fake_call_llm(prompt, _cfg, temperature=0.0, json_mode=False, ollama_phase=None):
        if json_mode:
            assert "EX:" in prompt
            return json.dumps({"decisions": ["d1"], "issues": [], "items": [], "notes": []}, ensure_ascii=False)
        assert ollama_phase == "merge"
        return "```markdown\n[00:01] final summary\n```"

    monkeypatch.setattr(tasks_mod, "call_llm", _fake_call_llm)

    tasks_mod.process_video_task(
        "tid2",
        "u@example.com",
        "input.txt",
        str(src),
        llm_config={"notification_type": "browser", "transcript_only": False, "provider": "ollama"},
        prompt_paths={"extract": str(p_ex), "merge": str(p_mg)},
        owner_username="owner@example.com",
    )

    assert state["status"] == "completed"
    assert "final summary" in state["summary"]
    assert "[00:01]" not in state["summary"]  # タイムスタンプ除去契約
    assert metrics["llm_chunks"] >= 1
    assert notify["ok"] == 1
    assert src.exists() is False


def test_process_video_task_extraction_failure_cancels(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.txt"
    src.write_text("line1\n\nline2", encoding="utf-8")
    p_ex = tmp_path / "extract.txt"
    p_mg = tmp_path / "merge.txt"
    p_ex.write_text("EX:{CHUNK_TEXT}", encoding="utf-8")
    p_mg.write_text("MG:{EXTRACTED_JSON}", encoding="utf-8")

    state = {
        "status": "pending",
        "topic": "",
        "meeting_date": "",
        "category": "",
        "tags": "",
        "preset_id": "",
        "context_json": "",
        "summary": "",
        "transcript": "",
    }
    failed = {"n": 0}

    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(
        tasks_mod.db,
        "update_record",
        lambda _tid, _owner="", status=None, transcript=None, summary=None: state.update(
            {
                **({"status": status} if status is not None else {}),
                **({"transcript": transcript} if transcript is not None else {}),
                **({"summary": summary} if summary is not None else {}),
            }
        ),
    )
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_notify_task_failure", lambda *_a, **_k: failed.update({"n": failed["n"] + 1}))
    monkeypatch.setattr(tasks_mod, "call_llm", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("llm down")))

    tasks_mod.process_video_task(
        "tid3",
        "u@example.com",
        "input.txt",
        str(src),
        llm_config={"notification_type": "webhook", "transcript_only": False, "provider": "ollama"},
        prompt_paths={"extract": str(p_ex), "merge": str(p_mg)},
        owner_username="owner@example.com",
    )

    assert state["status"] == "cancelled"
    assert "議事録生成エラー" in state["summary"]
    assert failed["n"] == 1
    assert src.exists() is False


def test_notification_and_email_helpers(tasks_mod, monkeypatch):
    # _maybe_send_completion_email: feature flag off
    monkeypatch.setattr(tasks_mod.feature_flags, "email_notify_feature_enabled", lambda: False)
    tasks_mod._maybe_send_completion_email("u@example.com", "a.wav", "t1")

    # _maybe_send_completion_email: ImportError
    monkeypatch.setattr(tasks_mod.feature_flags, "email_notify_feature_enabled", lambda: True)
    sys.modules.pop("backend.smtp_notify", None)
    orig_import = builtins.__import__

    def _imp(name, *args, **kwargs):
        if name == "backend.smtp_notify":
            raise ImportError("x")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _imp)
    tasks_mod._maybe_send_completion_email("u@example.com", "a.wav", "t1")
    monkeypatch.setattr(builtins, "__import__", orig_import)

    # _maybe_send_completion_email: success
    sent = {"ok": 0}
    fake_smtp = types.ModuleType("backend.smtp_notify")
    fake_smtp.send_task_completion_email = lambda *_a, **_k: sent.update({"ok": sent["ok"] + 1})
    fake_smtp.send_task_failure_email = lambda *_a, **_k: sent.update({"ok": sent["ok"] + 1})
    monkeypatch.setitem(sys.modules, "backend.smtp_notify", fake_smtp)
    tasks_mod._maybe_send_completion_email("u@example.com", "a.wav", "t1")
    assert sent["ok"] == 1

    # failure notify: browser/none no-op
    tasks_mod._notify_task_failure("browser", "u@example.com", "a.wav", "", "detail", "tid")
    tasks_mod._notify_task_failure("none", "u@example.com", "a.wav", "", "detail", "tid")

    # failure notify: webhook
    posted = {"n": 0}
    monkeypatch.setattr(tasks_mod.requests, "post", lambda *_a, **_k: posted.update({"n": posted["n"] + 1}))
    tasks_mod._notify_task_failure("webhook", "u@example.com", "a.wav", "http://wh", "detail", "tid")
    assert posted["n"] == 1

    # failure notify: webhook exception swallow
    monkeypatch.setattr(
        tasks_mod.requests,
        "post",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    tasks_mod._notify_task_failure("webhook", "u@example.com", "a.wav", "http://wh", "detail", "tid")

    # failure notify: email path
    monkeypatch.setattr(tasks_mod.feature_flags, "email_notify_feature_enabled", lambda: True)
    tasks_mod._notify_task_failure("email", "u@example.com", "a.wav", "", "detail", "tid")
    assert sent["ok"] >= 2

    # completion notify: browser/none no-op
    tasks_mod._notify_task_completion("browser", "u@example.com", "a.wav", "", "done", "tid")
    tasks_mod._notify_task_completion("none", "u@example.com", "a.wav", "", "done", "tid")

    # completion notify: webhook success + exception swallow
    posted2 = {"n": 0}
    monkeypatch.setattr(tasks_mod.requests, "post", lambda *_a, **_k: posted2.update({"n": posted2["n"] + 1}))
    tasks_mod._notify_task_completion("webhook", "u@example.com", "a.wav", "http://wh", "done", "tid")
    assert posted2["n"] == 1
    monkeypatch.setattr(
        tasks_mod.requests,
        "post",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    tasks_mod._notify_task_completion("webhook", "u@example.com", "a.wav", "http://wh", "done", "tid")

    # completion notify: email path
    called = {"n": 0}
    monkeypatch.setattr(tasks_mod, "_maybe_send_completion_email", lambda *_a, **_k: called.update({"n": called["n"] + 1}))
    tasks_mod._notify_task_completion("email", "u@example.com", "a.wav", "", "done", "tid")
    assert called["n"] == 1


def test_process_video_task_audio_branch_transcript_only(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.mp3"
    src.write_bytes(b"audio-bytes")

    state = {
        "status": "pending",
        "topic": "",
        "meeting_date": "",
        "category": "",
        "tags": "",
        "preset_id": "",
        "context_json": "",
        "summary": "",
        "transcript": "",
    }
    metrics = {}

    class _AudioClip:
        def __init__(self, _p):
            self.duration = 7.5

        def write_audiofile(self, path, logger=None):
            with open(path, "wb") as f:
                f.write(b"wav")

        def close(self):
            return None

    class _WM:
        def __init__(self, *_a, **_k):
            pass

        def transcribe(self, *_a, **_k):
            return [SimpleNamespace(start=0.0, end=1.0, text="hello")], {}

    monkeypatch.setattr(tasks_mod, "AudioFileClip", _AudioClip)
    monkeypatch.setattr(tasks_mod, "WhisperModel", _WM)
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda _tid, **kw: metrics.update(kw))
    monkeypatch.setattr(
        tasks_mod.db,
        "update_record",
        lambda _tid, _owner="", status=None, transcript=None, summary=None: state.update(
            {
                **({"status": status} if status is not None else {}),
                **({"transcript": transcript} if transcript is not None else {}),
                **({"summary": summary} if summary is not None else {}),
            }
        ),
    )
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)

    tasks_mod.process_video_task(
        "ta1",
        "u@example.com",
        "input.mp3",
        str(src),
        llm_config={"transcript_only": True, "notification_type": "browser"},
        owner_username="owner@example.com",
    )
    assert state["status"] == "completed"
    assert "書き起こしのみ完了" in state["summary"]
    assert "hello" in state["transcript"]
    assert metrics["media_duration_sec"] >= 1.0
    assert src.exists() is False


def test_process_video_task_video_without_audio_fails(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.mp4"
    src.write_bytes(b"video-bytes")

    state = {
        "status": "pending",
        "topic": "",
        "meeting_date": "",
        "category": "",
        "tags": "",
        "preset_id": "",
        "context_json": "",
        "summary": "",
        "transcript": "",
    }
    failed = {"n": 0}

    class _Video:
        def __init__(self, _p):
            self.audio = None
            self.duration = 9.0

        def close(self):
            return None

    monkeypatch.setattr(tasks_mod, "VideoFileClip", _Video)
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(
        tasks_mod.db,
        "update_record",
        lambda _tid, _owner="", status=None, transcript=None, summary=None: state.update(
            {
                **({"status": status} if status is not None else {}),
                **({"transcript": transcript} if transcript is not None else {}),
                **({"summary": summary} if summary is not None else {}),
            }
        ),
    )
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_notify_task_failure", lambda *_a, **_k: failed.update({"n": failed["n"] + 1}))

    tasks_mod.process_video_task(
        "tv1",
        "u@example.com",
        "input.mp4",
        str(src),
        llm_config={"transcript_only": False, "notification_type": "webhook"},
        owner_username="owner@example.com",
    )
    assert state["status"] == "cancelled"
    assert "動画に音声トラックがありません" in state["summary"]
    assert failed["n"] == 1
    assert src.exists() is False


def test_process_video_task_video_audio_and_cancel_points(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.mp4"
    src.write_bytes(b"video")
    p_ex = tmp_path / "extract.txt"
    p_mg = tmp_path / "merge.txt"
    p_ex.write_text("", encoding="utf-8")  # 904-907 経路
    p_mg.write_text("MG:{EXTRACTED_JSON}", encoding="utf-8")

    state = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": "", "summary": "", "transcript": ""}
    cleanup = {"n": 0}

    class _Audio:
        def write_audiofile(self, path, logger=None):
            with open(path, "wb") as f:
                f.write(b"wav")

    class _Video:
        def __init__(self, _p):
            self.audio = _Audio()
            self.duration = 3.2

        def close(self):
            return None

    class _WM:
        def __init__(self, *_a, **_k):
            pass

        def transcribe(self, *_a, **_k):
            return [SimpleNamespace(start=0.0, end=1.0, text="t")], {}

    calls = {"n": 0}

    def _get_record(*_a, **_k):
        calls["n"] += 1
        # extract loop前（962前）でキャンセルにする
        if calls["n"] >= 4:
            state["status"] = "cancelled"
        return state

    monkeypatch.setattr(tasks_mod, "VideoFileClip", _Video)
    monkeypatch.setattr(tasks_mod, "WhisperModel", _WM)
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", _get_record)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_cleanup_after_cancel", lambda *_a, **_k: cleanup.update({"n": cleanup["n"] + 1}))
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_release_whisper_gpu_resources", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks_mod,
        "call_llm",
        lambda *_a, **k: json.dumps({"decisions": ["d"], "issues": [], "items": [], "notes": []}, ensure_ascii=False)
        if k.get("json_mode")
        else "final",
    )

    tasks_mod.process_video_task(
        "tv2",
        "u@example.com",
        "input.mp4",
        str(src),
        llm_config={"notification_type": "browser"},
        prompt_paths={"extract": str(p_ex), "merge": str(p_mg)},
        owner_username="owner@example.com",
    )
    assert cleanup["n"] >= 1


def test_process_video_task_merge_fallback_and_empty_merge_prompt(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.txt"
    src.write_text("line1\n\nline2", encoding="utf-8")
    p_ex = tmp_path / "extract.txt"
    p_ex.write_text("EX:{CHUNK_TEXT}", encoding="utf-8")
    p_mg = tmp_path / "merge.txt"
    p_mg.write_text("MG:{EXTRACTED_JSON}", encoding="utf-8")

    state = {
        "status": "pending",
        "topic": "",
        "meeting_date": "",
        "category": "",
        "tags": "",
        "preset_id": "",
        "context_json": "",
        "summary": "",
        "transcript": "",
    }

    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks_mod.db,
        "update_record",
        lambda _tid, _owner="", status=None, transcript=None, summary=None: state.update(
            {
                **({"status": status} if status is not None else {}),
                **({"transcript": transcript} if transcript is not None else {}),
                **({"summary": summary} if summary is not None else {}),
            }
        ),
    )

    def _llm(prompt, _cfg, temperature=0.0, json_mode=False, ollama_phase=None):
        if json_mode:
            return json.dumps({"decisions": ["d1"], "issues": [], "items": [], "notes": []}, ensure_ascii=False)
        raise RuntimeError("merge failed")

    monkeypatch.setattr(tasks_mod, "call_llm", _llm)
    tasks_mod.process_video_task(
        "tm1",
        "u@example.com",
        "input.txt",
        str(src),
        llm_config={"notification_type": "browser"},
        prompt_paths={"extract": str(p_ex), "merge": str(p_mg)},
        owner_username="owner@example.com",
    )
    assert state["status"] == "completed"
    assert state["summary"].startswith("Merge failed")

    # merge プロンプト空分岐（json_str をそのまま採用）
    src2 = tmp_path / "input2.txt"
    src2.write_text("lineA\n\nlineB", encoding="utf-8")
    p_mg_empty = tmp_path / "merge_empty.txt"
    p_mg_empty.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        tasks_mod,
        "call_llm",
        lambda *_a, **k: json.dumps({"decisions": ["d2"], "issues": [], "items": [], "notes": []}, ensure_ascii=False),
    )
    tasks_mod.process_video_task(
        "tm2",
        "u@example.com",
        "input2.txt",
        str(src2),
        llm_config={"notification_type": "browser"},
        prompt_paths={"extract": str(p_ex), "merge": str(p_mg_empty)},
        owner_username="owner@example.com",
    )
    assert state["status"] == "completed"
    assert '"decisions"' in state["summary"]


def test_process_video_task_extract_retry_and_partial_continue(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.txt"
    src.write_text("line1\n\nline2", encoding="utf-8")
    p_ex = tmp_path / "extract.txt"
    p_mg = tmp_path / "merge.txt"
    p_ex.write_text("EX:{CHUNK_TEXT}", encoding="utf-8")
    p_mg.write_text("MG:{EXTRACTED_JSON}", encoding="utf-8")

    state = {
        "status": "pending",
        "topic": "",
        "meeting_date": "",
        "category": "",
        "tags": "",
        "preset_id": "",
        "context_json": "",
        "summary": "",
        "transcript": "",
    }
    seen_extract_prompts = []

    monkeypatch.setenv("MM_EXTRACT_SCHEMA_RETRY", "1")
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tasks_mod.db,
        "update_record",
        lambda _tid, _owner="", status=None, transcript=None, summary=None: state.update(
            {
                **({"status": status} if status is not None else {}),
                **({"transcript": transcript} if transcript is not None else {}),
                **({"summary": summary} if summary is not None else {}),
            }
        ),
    )
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: (["c1", "c2"], "raw"))

    calls = {"extract": 0}

    def _llm(prompt, _cfg, temperature=0.0, json_mode=False, ollama_phase=None):
        if json_mode:
            seen_extract_prompts.append(prompt)
            calls["extract"] += 1
            # chunk1: 1回目/2回目ともスキーマ不正 -> スキップされる
            if calls["extract"] in (1, 2):
                return json.dumps({"decisions": [{"text": "d"}], "issues": [], "items": [], "notes": []}, ensure_ascii=False)
            # chunk2: 1回目は不正、2回目で回復
            if calls["extract"] == 3:
                return "not-json"
            return json.dumps(
                {
                    "decisions": [{"text": "d2", "evidence": ["00:00:01-00:00:02"]}],
                    "issues": [],
                    "items": [],
                    "notes": [],
                },
                ensure_ascii=False,
            )
        assert ollama_phase == "merge"
        return "ok summary"

    monkeypatch.setattr(tasks_mod, "call_llm", _llm)

    tasks_mod.process_video_task(
        "tr1",
        "u@example.com",
        "input.txt",
        str(src),
        llm_config={"notification_type": "browser"},
        prompt_paths={"extract": str(p_ex), "merge": str(p_mg)},
        owner_username="owner@example.com",
    )

    assert state["status"] == "completed"
    assert "ok summary" in state["summary"]
    assert any("再出力指示（必須）" in p for p in seen_extract_prompts[1:])


def test_process_video_task_early_return_and_outer_exception(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.txt"
    src.write_text("line1", encoding="utf-8")

    # record が無い場合は即 return
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: None)
    tasks_mod.process_video_task("te0", "u@example.com", "input.txt", str(src), llm_config={}, owner_username="")

    src2 = tmp_path / "input2.txt"
    src2.write_text("line1\n\nline2", encoding="utf-8")
    state = {"status": "pending", "summary": "", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    failed = {"n": 0}

    def _update_record(_tid, _owner="", status=None, transcript=None, summary=None):
        if status == "completed":
            raise RuntimeError("db down")
        if status is not None:
            state["status"] = status
        if summary is not None:
            state["summary"] = summary

    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod.db, "update_record", _update_record)
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_notify_task_failure", lambda *_a, **_k: failed.update({"n": failed["n"] + 1}))
    monkeypatch.setattr(
        tasks_mod,
        "call_llm",
        lambda *_a, **k: (
            json.dumps({"decisions": ["d"], "issues": [], "items": [], "notes": []}, ensure_ascii=False)
            if k.get("json_mode")
            else "final"
        ),
    )

    p_ex = tmp_path / "extract2.txt"
    p_mg = tmp_path / "merge2.txt"
    p_ex.write_text("EX:{CHUNK_TEXT}", encoding="utf-8")
    p_mg.write_text("MG:{EXTRACTED_JSON}", encoding="utf-8")

    tasks_mod.process_video_task(
        "te1",
        "u@example.com",
        "input2.txt",
        str(src2),
        llm_config={"notification_type": "webhook"},
        prompt_paths={"extract": str(p_ex), "merge": str(p_mg)},
        owner_username="owner@example.com",
    )
    assert state["status"] == "cancelled"
    assert "db down" in state["summary"]
    assert failed["n"] == 1


def test_memory_and_gpu_release_error_branches(tasks_mod, monkeypatch):
    # _trim_process_memory: disabled
    monkeypatch.setenv("MM_WORKER_TRIM_RAM", "0")
    tasks_mod._trim_process_memory()

    # _trim_process_memory: linux + OSError / generic exception
    monkeypatch.setenv("MM_WORKER_TRIM_RAM", "1")
    monkeypatch.setattr(tasks_mod.sys, "platform", "linux")

    class _CDLLRaiseOSError:
        def __call__(self, _name):
            raise OSError("no libc")

    fake_ctypes1 = types.SimpleNamespace(CDLL=_CDLLRaiseOSError())
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes1)
    tasks_mod._trim_process_memory()

    class _CDLLRaiseEx:
        def __call__(self, _name):
            raise RuntimeError("x")

    fake_ctypes2 = types.SimpleNamespace(CDLL=_CDLLRaiseEx())
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes2)
    tasks_mod._trim_process_memory()

    # _release_whisper_gpu_resources: not cuda
    tasks_mod._release_whisper_gpu_resources("cpu")

    # cuda but not available
    monkeypatch.setattr(tasks_mod.torch.cuda, "is_available", lambda: False)
    tasks_mod._release_whisper_gpu_resources("cuda")

    # cuda available but synchronize/empty_cache error
    monkeypatch.setattr(tasks_mod.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(tasks_mod.torch.cuda, "synchronize", lambda: (_ for _ in ()).throw(RuntimeError("sync")))
    tasks_mod._release_whisper_gpu_resources("cuda")

    # flush path empty_cache exception
    monkeypatch.setattr(tasks_mod.torch.cuda, "synchronize", lambda: None)
    monkeypatch.setattr(tasks_mod.torch.cuda, "empty_cache", lambda: (_ for _ in ()).throw(RuntimeError("cache")))
    tasks_mod._flush_whisper_before_ollama("cuda")


def test_failure_notification_email_import_error(tasks_mod, monkeypatch):
    monkeypatch.setattr(tasks_mod.feature_flags, "email_notify_feature_enabled", lambda: True)
    orig_import = builtins.__import__
    def _imp(name, *args, **kwargs):
        if name == "backend.smtp_notify":
            raise ImportError("x")
        return orig_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", _imp)
    tasks_mod._notify_task_failure("email", "u@example.com", "a.wav", "", "detail", "tid")
    monkeypatch.setattr(builtins, "__import__", orig_import)


def test_process_video_task_additional_fail_and_cancel_points(tasks_mod, tmp_path, monkeypatch):
    # chunks empty -> fail(878-879) + fail cleanup except(758/761-762)
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    state = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": "", "summary": ""}

    class _AC:
        def __init__(self, _p):
            self.duration = 1.0
        def write_audiofile(self, path, logger=None):
            with open(path, "wb") as f:
                f.write(b"w")
        def close(self):
            return None

    class _WM:
        def __init__(self, *_a, **_k):
            pass
        def transcribe(self, *_a, **_k):
            return [SimpleNamespace(start=0.0, end=1.0, text="x")], {}

    monkeypatch.setattr(tasks_mod, "AudioFileClip", _AC)
    monkeypatch.setattr(tasks_mod, "WhisperModel", _WM)
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **k: state.update({"status": k.get("status", state.get("status")), "summary": k.get("summary", state.get("summary"))}))
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: ([], "raw"))
    monkeypatch.setattr(tasks_mod.os, "remove", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(tasks_mod, "_notify_task_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)

    tasks_mod.process_video_task("t_more1", "u@example.com", "a.mp3", str(src), llm_config={"notification_type": "webhook"}, owner_username="o")
    assert state["status"] == "cancelled"

    # fail() inside extraction invalid-json branch
    src2 = tmp_path / "b.txt"
    src2.write_text("a\n\nb", encoding="utf-8")
    st2 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": "", "summary": ""}
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: st2)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **k: st2.update({"status": k.get("status", st2.get("status")), "summary": k.get("summary", st2.get("summary"))}))
    monkeypatch.setattr(tasks_mod, "call_llm", lambda *_a, **_k: "not-json")
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: (["c1"], "raw"))
    tasks_mod.process_video_task("t_more2", "u@example.com", "b.txt", str(src2), llm_config={"notification_type": "webhook"}, owner_username="o")
    assert "Chunk 1: JSONとして解釈できませんでした。" in st2["summary"]

    # cancel at loop start (926-927), before merging (963-964), outer except cancelled (1036-1037)
    src3 = tmp_path / "c.txt"
    src3.write_text("a\n\nb", encoding="utf-8")
    seq = {"n": 0}
    st3 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    cleanup = {"n": 0}
    def _get(*_a, **_k):
        seq["n"] += 1
        if seq["n"] >= 5:
            st3["status"] = "cancelled"
        return st3
    monkeypatch.setattr(tasks_mod.db, "get_record", _get)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_cleanup_after_cancel", lambda *_a, **_k: cleanup.update({"n": cleanup["n"] + 1}))
    monkeypatch.setattr(tasks_mod, "call_llm", lambda *_a, **k: json.dumps({"decisions": ["d"], "issues": [], "items": [], "notes": []}, ensure_ascii=False))
    tasks_mod.process_video_task("t_more3", "u@example.com", "c.txt", str(src3), llm_config={"notification_type": "browser"}, owner_username="o")
    assert cleanup["n"] >= 1

    # outer except + cancelled at catch branch
    src4 = tmp_path / "d.txt"
    src4.write_text("a", encoding="utf-8")
    st4 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: st4)
    monkeypatch.setattr(tasks_mod, "load_prompt", lambda *_a, **_k: "{CHUNK_TEXT}")
    orig_open = builtins.open
    def _open_fail(path, *args, **kwargs):
        if str(path).endswith("d.txt"):
            raise RuntimeError("boom")
        return orig_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", _open_fail)
    rc = {"n": 0}
    def _rc(*_a, **_k):
        rc["n"] += 1
        return rc["n"] >= 2  # 初回チェックは通し、except 側で cancelled にする
    monkeypatch.setattr(tasks_mod, "_record_cancelled", _rc)
    tasks_mod.process_video_task("t_more4", "u@example.com", "d.txt", str(src4), llm_config={"notification_type": "browser"}, owner_username="o")
    monkeypatch.setattr(builtins, "open", orig_open)
    assert cleanup["n"] >= 2


def test_markdown_fence_cleanup_branch(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "m.txt"
    src.write_text("a\n\nb", encoding="utf-8")
    p_ex = tmp_path / "ex.txt"
    p_mg = tmp_path / "mg.txt"
    p_ex.write_text("EX:{CHUNK_TEXT}", encoding="utf-8")
    p_mg.write_text("MG:{EXTRACTED_JSON}", encoding="utf-8")
    st = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": "", "summary": ""}
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: st)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod.db, "update_usage_job_metrics", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **k: st.update({"status": k.get("status", st.get("status")), "summary": k.get("summary", st.get("summary"))}))
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)

    def _llm(_p, _c, json_mode=False, **_k):
        if json_mode:
            return json.dumps({"decisions": ["d"], "issues": [], "items": [], "notes": []}, ensure_ascii=False)
        return "```\nabc\n```"

    monkeypatch.setattr(tasks_mod, "call_llm", _llm)
    tasks_mod.process_video_task("tmd", "u@example.com", "m.txt", str(src), llm_config={"notification_type": "browser"}, prompt_paths={"extract": str(p_ex), "merge": str(p_mg)}, owner_username="o")
    assert st["status"] == "completed"
    assert "```" not in st["summary"]


def test_tasks_remaining_branch_points(tasks_mod, tmp_path, monkeypatch):
    # line 189: malloc_trim 実行
    monkeypatch.setenv("MM_WORKER_TRIM_RAM", "1")
    monkeypatch.setattr(tasks_mod.sys, "platform", "linux")
    called = {"n": 0}
    fake_ctypes = types.SimpleNamespace(
        CDLL=lambda _name: types.SimpleNamespace(malloc_trim=lambda _x: called.update({"n": called["n"] + 1}))
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    tasks_mod._trim_process_memory()
    assert called["n"] == 1

    # lines 618/623: supplementary inject fallback + media duration valid
    out = tasks_mod._inject_supplementary_extract("plain-shell", "SUP", "body")
    assert out.startswith("# --- 参考資料")
    assert tasks_mod._media_duration_sec_from_segments([]) == 0.0
    assert tasks_mod._media_duration_sec_from_segments([{"start": 0.0, "end": 2.5}]) == 2.5

    # lines 750-751: fail() 内 cancelled cleanup
    src = tmp_path / "x.txt"
    src.write_text("a\n\nb", encoding="utf-8")
    state = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    cseq = {"n": 0}
    def _get_record_cancel(*_a, **_k):
        cseq["n"] += 1
        if cseq["n"] >= 3:
            state["status"] = "cancelled"
        return state
    cleanup = {"n": 0}
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", _get_record_cancel)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod, "_cleanup_after_cancel", lambda *_a, **_k: cleanup.update({"n": cleanup["n"] + 1}))
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [])
    tasks_mod.process_video_task("tt1", "u@example.com", "x.txt", str(src), llm_config={"notification_type": "browser"}, owner_username="o")
    assert cleanup["n"] >= 1

    # lines 775-776 / 779-780: text path fail branches
    src2 = tmp_path / "y.txt"
    src2.write_text("a", encoding="utf-8")
    st2 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": "", "summary": ""}
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: st2)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **k: st2.update({"status": k.get("status", st2.get("status")), "summary": k.get("summary", st2.get("summary"))}))
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [])
    tasks_mod.process_video_task("tt2", "u@example.com", "y.txt", str(src2), llm_config={"notification_type": "webhook"}, owner_username="o")
    assert "読み取れるセグメントがありません" in st2["summary"]
    st2["status"] = "pending"
    src2.write_text("a", encoding="utf-8")
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [{"start": 0.0, "end": 0.0, "text": "t"}])
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: ([], "raw"))
    tasks_mod.process_video_task("tt3", "u@example.com", "y.txt", str(src2), llm_config={"notification_type": "webhook"}, owner_username="o")
    assert "チャンクを生成できませんでした" in st2["summary"]

    # line 808/809: non-transcript 分岐の early cancel
    src3 = tmp_path / "z.mp3"
    src3.write_bytes(b"x")
    st3 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    seq3 = {"n": 0}
    def _get3(*_a, **_k):
        seq3["n"] += 1
        if seq3["n"] >= 3:
            st3["status"] = "cancelled"
        return st3
    c3 = {"n": 0}
    monkeypatch.setattr(tasks_mod.db, "get_record", _get3)
    monkeypatch.setattr(tasks_mod, "_cleanup_after_cancel", lambda *_a, **_k: c3.update({"n": c3["n"] + 1}))
    tasks_mod.process_video_task("tt4", "u@example.com", "z.mp3", str(src3), llm_config={"notification_type": "browser"}, owner_username="o")
    assert c3["n"] >= 1

    # line 905: empty extract prompt fallback
    src4 = tmp_path / "w.txt"
    src4.write_text("a\n\nb", encoding="utf-8")
    p_ex = tmp_path / "ex_empty.txt"
    p_mg = tmp_path / "mg.txt"
    p_ex.write_text("", encoding="utf-8")
    p_mg.write_text("", encoding="utf-8")
    st4 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": "", "summary": ""}
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: st4)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **k: st4.update({"status": k.get("status", st4.get("status")), "summary": k.get("summary", st4.get("summary"))}))
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [{"start": 0.0, "end": 0.0, "text": "t"}])
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: (["c"], "raw"))
    monkeypatch.setattr(tasks_mod, "call_llm", lambda *_a, **k: json.dumps({"decisions": ["d"], "issues": [], "items": [], "notes": []}, ensure_ascii=False) if k.get("json_mode") else "")
    monkeypatch.setattr(tasks_mod, "_safe_update_usage_metrics", lambda *_a, **_k: None)
    tasks_mod.process_video_task("tt5", "u@example.com", "w.txt", str(src4), llm_config={"notification_type": "browser"}, prompt_paths={"extract": str(p_ex), "merge": str(p_mg)}, owner_username="o")

    # line 926/927: extraction loop先頭 cancel
    src5 = tmp_path / "v.txt"
    src5.write_text("a\n\nb", encoding="utf-8")
    st5 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    c5 = {"n": 0, "c": 0}
    def _get5(*_a, **_k):
        c5["n"] += 1
        if c5["n"] >= 4:
            st5["status"] = "cancelled"
        return st5
    monkeypatch.setattr(tasks_mod.db, "get_record", _get5)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_cleanup_after_cancel", lambda *_a, **_k: c5.update({"c": c5["c"] + 1}))
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [{"start": 0.0, "end": 0.0, "text": "t"}])
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: (["c1", "c2"], "raw"))
    tasks_mod.process_video_task("tt6", "u@example.com", "v.txt", str(src5), llm_config={"notification_type": "browser"}, owner_username="o")
    assert c5["c"] >= 1

    # line 1036/1037: outer exceptでcancelled cleanup
    src6 = tmp_path / "u.txt"
    src6.write_text("a\n\nb", encoding="utf-8")
    st6 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    c6 = {"n": 0, "c": 0}
    def _rc(*_a, **_k):
        c6["n"] += 1
        return c6["n"] >= 2
    monkeypatch.setattr(tasks_mod, "_record_cancelled", _rc)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: st6)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(tasks_mod, "_cleanup_after_cancel", lambda *_a, **_k: c6.update({"c": c6["c"] + 1}))
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [{"start": 0.0, "end": 0.0, "text": "t"}])
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: (["c"], "raw"))
    tasks_mod.process_video_task("tt7", "u@example.com", "u.txt", str(src6), llm_config={"notification_type": "browser"}, owner_username="o")
    assert c6["c"] >= 1


def test_cancel_cleanup_branches(tasks_mod, tmp_path, monkeypatch):
    src = tmp_path / "input.txt"
    src.write_text("hello", encoding="utf-8")

    state = {"status": "cancelled", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    cleanup = {"n": 0}

    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state)
    monkeypatch.setattr(tasks_mod, "_cleanup_after_cancel", lambda *_a, **_k: cleanup.update({"n": cleanup["n"] + 1}))

    tasks_mod.process_video_task(
        "tc1",
        "u@example.com",
        "input.txt",
        str(src),
        llm_config={"notification_type": "browser"},
        owner_username="owner@example.com",
    )
    assert cleanup["n"] == 0  # 先頭キャンセルは _remove_files + _cleanup_user_prompts 経路

    # 処理途中キャンセル（text path 内）
    src2 = tmp_path / "input2.txt"
    src2.write_text("line1\n\nline2", encoding="utf-8")
    state2 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}

    def _get_record(*_a, **_k):
        # 1,2回目は pending（初期取得 + 先頭キャンセルチェック）
        # 3回目以降で cancelled（is_transcript ブロック内チェックで _cleanup_after_cancel を通す）
        if _get_record.calls < 2:
            _get_record.calls += 1
            return state2
        state2["status"] = "cancelled"
        return state2

    _get_record.calls = 0
    monkeypatch.setattr(tasks_mod.db, "get_record", _get_record)
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod, "load_prompt", lambda *_a, **_k: "{CHUNK_TEXT}")
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [{"start": 0.0, "end": 0.0, "text": "t"}])
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: (["chunk"], "raw"))
    tasks_mod.process_video_task(
        "tc2",
        "u@example.com",
        "input2.txt",
        str(src2),
        llm_config={"notification_type": "browser"},
        owner_username="owner@example.com",
    )
    assert cleanup["n"] >= 1


def test_process_video_task_cancelled_return_points(tasks_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_mod.db, "purge_expired_minutes", lambda *_a, **_k: 0)
    monkeypatch.setattr(tasks_mod.db, "parse_context_json", lambda _r: {})
    monkeypatch.setattr(tasks_mod, "_flush_whisper_before_ollama", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_try_ollama_unload_for_config", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_cleanup_user_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks_mod, "_remove_files", lambda *_a, **_k: None)

    # fail() 内の _cleanup_if_cancelled=True 経路（tasks.py:775）
    src1 = tmp_path / "c1.txt"
    src1.write_text("x", encoding="utf-8")
    state1 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state1)
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [])
    monkeypatch.setattr(tasks_mod, "_cleanup_if_cancelled", lambda *_a, **_k: True)
    tasks_mod.process_video_task(
        "tc3",
        "u@example.com",
        "c1.txt",
        str(src1),
        llm_config={"notification_type": "browser"},
        owner_username="owner@example.com",
    )

    # outer except 側の _cleanup_if_cancelled=True 経路（tasks.py:1047）
    src2 = tmp_path / "c2.txt"
    src2.write_text("x\ny", encoding="utf-8")
    state2 = {"status": "pending", "topic": "", "meeting_date": "", "category": "", "tags": "", "preset_id": "", "context_json": ""}
    monkeypatch.setattr(tasks_mod.db, "get_record", lambda *_a, **_k: state2)
    monkeypatch.setattr(tasks_mod, "normalize_to_segments", lambda *_a, **_k: [{"start": 0.0, "end": 0.0, "text": "t"}])
    monkeypatch.setattr(tasks_mod, "build_chunks_from_segments", lambda *_a, **_k: (["c"], "raw"))
    monkeypatch.setattr(tasks_mod.db, "update_record", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(tasks_mod, "_cleanup_if_cancelled", lambda *_a, **_k: True)
    tasks_mod.process_video_task(
        "tc4",
        "u@example.com",
        "c2.txt",
        str(src2),
        llm_config={"notification_type": "browser"},
        owner_username="owner@example.com",
    )
