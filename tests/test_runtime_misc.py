import json
from types import SimpleNamespace

import pytest
import jwt

import version
from backend import auth_jwt, storage
from streamlit_app import constants, render, styles, task_status


def test_auth_jwt_create_decode(monkeypatch):
    monkeypatch.setattr(auth_jwt, "auth_secret", lambda: "sec")
    monkeypatch.setattr(auth_jwt, "token_ttl_hours", lambda: 24 * 365 * 100)
    monkeypatch.setattr(auth_jwt.time, "time", lambda: 1000)

    token = auth_jwt.create_access_token("u@example.com")
    payload_raw = jwt.decode(token, "sec", algorithms=["HS256"], options={"verify_exp": False})
    assert payload_raw["sub"] == "u@example.com"
    payload = auth_jwt.decode_access_token(token)
    assert payload["sub"] == "u@example.com"
    assert payload["iat"] == 1000
    assert payload["exp"] > payload["iat"]


def test_storage_helpers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert storage.save_uploaded_prompts("t1", None, None) is None
    p = storage.save_uploaded_prompts("t1", b"a", b"b")
    assert p and "extract" in p and "merge" in p
    assert (tmp_path / p["extract"]).exists()
    assert (tmp_path / p["merge"]).exists()

    s = storage.save_supplementary_inputs("t2", b"x", b"y")
    assert s and "supplementary_teams" in s and "supplementary_notes" in s
    assert storage.save_supplementary_inputs("t2", None, None) is None

    m = storage.merge_task_prompt_paths("t3", b"e", None, b"t", None)
    assert m and "extract" in m and "supplementary_teams" in m
    assert storage.merge_task_prompt_paths("t4", None, None, None, None) is None


def test_streamlit_constants_and_styles(monkeypatch):
    assert constants.LOGO_SVG.endswith("assets\\svg\\logo.svg") or constants.LOGO_SVG.endswith("assets/svg/logo.svg")

    called = {}
    monkeypatch.setattr(styles, "st", SimpleNamespace(markdown=lambda *a, **k: called.setdefault("m", (a, k))))
    styles.inject_ui_styles()
    assert "style" in called["m"][0][0]
    assert called["m"][1]["unsafe_allow_html"] is True


@pytest.mark.parametrize(
    ("status", "pct"),
    [
        ("processing", 5),
        ("processing:reading_transcript", 18),
        ("processing:extracting_audio", 10),
        ("processing:transcribing", 40),
        ("processing:extracting_1", 55),
        ("processing:merging", 80),
        ("processing:summarizing", 80),
        ("processing:sending_notification", 95),
        ("other", 0),
    ],
)
def test_task_status_progress(status, pct):
    p, _ = task_status.progress_for_task_status(status)
    assert p == pct


def test_render_minutes_and_helpers(monkeypatch):
    calls = {"md": [], "info": 0, "caption": 0}

    class _Exp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_st = SimpleNamespace(
        markdown=lambda *a, **k: calls["md"].append((a, k)),
        info=lambda *_a, **_k: calls.__setitem__("info", calls["info"] + 1),
        expander=lambda *_a, **_k: _Exp(),
        caption=lambda *_a, **_k: calls.__setitem__("caption", calls["caption"] + 1),
    )
    monkeypatch.setattr(render, "st", fake_st)

    render.render_minutes(
        json.dumps(
            {
                "decisions": [{"text": "d1"}],
                "issues": [{"text": "i1"}],
                "items": [{"who": "A", "what": "w", "due": "tomorrow"}],
                "notes": [{"text": "n1"}],
            },
            ensure_ascii=False,
        )
    )
    assert len(calls["md"]) > 3

    render.render_minutes("[]")  # json だが dict ではない -> ValueError 分岐
    render.render_minutes("plain text")
    assert any("plain text" in str(args[0][0]) for args in calls["md"] if args[0])

    render.render_minutes("")
    assert calls["info"] == 1

    class _F:
        def __init__(self, b):
            self._b = b

        def getvalue(self):
            return self._b

    monkeypatch.setattr(render, "merge_task_prompt_paths", lambda *_a: {"extract": "x"})
    out = render.save_uploaded_prompts("tid", _F(b"e"), _F(b"m"), _F(b"t"), _F(b"n"))
    assert out == {"extract": "x"}

    render.render_error_hints("ok")
    c0 = calls["caption"]
    render.render_error_hints("Error: x")
    assert calls["caption"] == c0 + 1


def test_version_module():
    assert isinstance(version.__version__, str) and version.__version__
    assert isinstance(version.CHANGELOG_VERSION, str) and version.CHANGELOG_VERSION
