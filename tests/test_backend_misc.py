import builtins
import json
import sys
import types
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import bcrypt

import feature_flags
from backend import auth_settings, deps, http_utils, passwords, presets_io
from backend.routes import meta, presets, profile
from backend.schemas import MeLLMPatch


class _FakeRow:
    def __init__(self, data):
        self._d = dict(data)

    def keys(self):
        return list(self._d.keys())

    def __getitem__(self, key):
        return self._d[key]


def test_http_utils_helpers():
    assert http_utils.sqlite_row_to_dict(None) == {}
    row = _FakeRow({"id": 1, "name": "x"})
    assert http_utils.sqlite_row_to_dict(row) == {"id": 1, "name": "x"}

    cd = http_utils.content_disposition_attachment('議事録 "a".md')
    assert "attachment;" in cd
    assert "filename*=" in cd

    long_name = "a" * 400 + ".txt"
    cd2 = http_utils.content_disposition_attachment(long_name)
    assert 'filename="' in cd2


def test_passwords_verify():
    hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode("ascii")
    assert passwords.verify_password("secret", hashed) is True
    assert passwords.verify_password("wrong", hashed) is False
    assert passwords.verify_password("x", "not-a-hash") is False


def test_auth_settings(monkeypatch):
    monkeypatch.delenv("MM_AUTH_SECRET", raising=False)
    assert auth_settings.auth_enabled() is False

    monkeypatch.setenv("MM_AUTH_SECRET", "  abc  ")
    assert auth_settings.auth_secret() == "abc"
    assert auth_settings.auth_enabled() is True

    monkeypatch.setenv("MM_AUTH_TOKEN_HOURS", "10")
    assert auth_settings.token_ttl_hours() == 10
    monkeypatch.setenv("MM_AUTH_TOKEN_HOURS", "0")
    assert auth_settings.token_ttl_hours() == 1
    monkeypatch.setenv("MM_AUTH_TOKEN_HOURS", "9999")
    assert auth_settings.token_ttl_hours() == 24 * 30
    monkeypatch.setenv("MM_AUTH_TOKEN_HOURS", "bad")
    assert auth_settings.token_ttl_hours() == 168

    monkeypatch.setenv("MM_AUTH_SELF_REGISTER", "0")
    assert auth_settings.self_register_enabled() is False
    monkeypatch.setenv("MM_AUTH_SELF_REGISTER", "yes")
    assert auth_settings.self_register_enabled() is True


def test_feature_flags(monkeypatch):
    monkeypatch.delenv("MM_OPENAI_ENABLED", raising=False)
    assert feature_flags.openai_feature_enabled() is True
    monkeypatch.setenv("MM_OPENAI_ENABLED", "off")
    assert feature_flags.openai_feature_enabled() is False

    monkeypatch.delenv("MM_EMAIL_NOTIFY_ENABLED", raising=False)
    assert feature_flags.email_notify_feature_enabled() is False
    monkeypatch.setenv("MM_EMAIL_NOTIFY_ENABLED", "true")
    assert feature_flags.email_notify_feature_enabled() is True


def test_deps_require_api_user_and_optional(monkeypatch):
    monkeypatch.setattr(deps, "auth_enabled", lambda: False)
    assert deps.require_api_user(None) == ""
    assert deps.optional_api_user(None) == ""

    monkeypatch.setattr(deps, "auth_enabled", lambda: True)
    with pytest_raises_http(401):
        deps.require_api_user(None)
    assert deps.optional_api_user(None) == ""

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="t")
    monkeypatch.setattr(deps, "decode_access_token", lambda _t: {"sub": " user@example.com "})
    assert deps.require_api_user(creds) == "user@example.com"
    assert deps.optional_api_user(creds) == "user@example.com"

    monkeypatch.setattr(deps, "decode_access_token", lambda _t: {"sub": ""})
    with pytest_raises_http(401):
        deps.require_api_user(creds)
    assert deps.optional_api_user(creds) == ""

    monkeypatch.setattr(deps, "decode_access_token", lambda _t: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest_raises_http(401):
        deps.require_api_user(creds)
    assert deps.optional_api_user(creds) == ""


def test_deps_require_admin(monkeypatch):
    monkeypatch.setattr(deps, "auth_enabled", lambda: False)
    with pytest_raises_http(403):
        deps.require_admin("u@example.com")

    monkeypatch.setattr(deps, "auth_enabled", lambda: True)
    monkeypatch.setattr(deps.db, "user_is_admin", lambda _u: False)
    with pytest_raises_http(403):
        deps.require_admin("u@example.com")

    monkeypatch.setattr(deps.db, "user_is_admin", lambda _u: True)
    assert deps.require_admin("u@example.com") == "u@example.com"


def test_presets_io(monkeypatch, tmp_path):
    assert presets_io.presets_builtin_path().endswith("presets_builtin.json")
    p = tmp_path / "presets.json"
    p.write_text(
        json.dumps(
            {
                "x": {"label": "X"},
                "standard": {"label": "標準"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(presets_io, "presets_builtin_path", lambda: str(p))
    d = presets_io.load_presets_dict()
    assert "standard" in d
    opts = presets_io.preset_options_for_ui()
    assert opts[0][0] == "standard"

    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(presets_io, "presets_builtin_path", lambda: str(bad))
    d2 = presets_io.load_presets_dict()
    assert "standard" in d2


def test_routes_meta_and_presets(monkeypatch):
    assert meta.health() == {"status": "ok"}
    monkeypatch.setattr(meta, "fetch_ollama_model_names", lambda: ["a", "b"])
    assert meta.ollama_models().models == ["a", "b"]

    vmod = types.ModuleType("version")
    vmod.__version__ = "9.9.9"
    monkeypatch.setitem(sys.modules, "version", vmod)
    assert meta.api_version() == {"version": "9.9.9"}

    real_import = builtins.__import__

    def _imp(name, *args, **kwargs):
        if name == "version":
            raise ImportError("x")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _imp)
    assert meta.api_version() == {"version": "unknown"}

    monkeypatch.setattr(presets, "load_presets_dict", lambda: {"standard": {"label": "標準"}})
    assert "standard" in presets.get_presets("u@example.com")


def test_routes_profile(monkeypatch):
    monkeypatch.setattr(profile.feature_flags, "openai_feature_enabled", lambda: True)
    monkeypatch.setattr(profile, "auth_enabled", lambda: False)
    r = profile.me_llm_get("")
    assert r.openai_configured is False

    monkeypatch.setattr(profile, "auth_enabled", lambda: True)
    with pytest_raises_http(401):
        profile.me_llm_get("")

    monkeypatch.setattr(profile.db, "get_user_openai_settings", lambda _u: ("k", "m"))
    r2 = profile.me_llm_get("u@example.com")
    assert r2.openai_configured is True
    assert r2.openai_model == "m"

    monkeypatch.setattr(profile.feature_flags, "openai_feature_enabled", lambda: False)
    with pytest_raises_http(400):
        profile.me_llm_patch(MeLLMPatch(openai_api_key="k"), "u@example.com")

    monkeypatch.setattr(profile.feature_flags, "openai_feature_enabled", lambda: True)
    monkeypatch.setattr(profile, "auth_enabled", lambda: False)
    with pytest_raises_http(400):
        profile.me_llm_patch(MeLLMPatch(openai_api_key="k"), "u@example.com")

    monkeypatch.setattr(profile, "auth_enabled", lambda: True)
    with pytest_raises_http(401):
        profile.me_llm_patch(MeLLMPatch(openai_api_key="k"), "")

    called = {"n": 0}
    monkeypatch.setattr(profile.db, "update_user_openai", lambda *_a, **_k: called.__setitem__("n", called["n"] + 1))
    assert profile.me_llm_patch(MeLLMPatch(), "u@example.com") == {"ok": True}
    assert called["n"] == 0

    assert profile.me_llm_patch(MeLLMPatch(openai_api_key=" k ", openai_model=""), "u@example.com") == {"ok": True}
    assert called["n"] == 1


class pytest_raises_http:
    def __init__(self, code: int):
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc_type is None:
            raise AssertionError("HTTPException was not raised")
        if not isinstance(exc, HTTPException):
            return False
        assert exc.status_code == self.code
        return True
