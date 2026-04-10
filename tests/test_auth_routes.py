import sqlite3

import pytest
from fastapi import HTTPException

from backend.routes import auth
from backend.schemas import BootstrapRequest, LoginRequest


def test_error_report_available(monkeypatch):
    monkeypatch.setattr(auth.smtp_notify, "smtp_configured", lambda: False)
    assert auth._error_report_available() is False

    monkeypatch.setattr(auth.smtp_notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth.db, "list_admin_emails", lambda: [])
    assert auth._error_report_available() is False
    monkeypatch.setattr(auth.db, "list_admin_emails", lambda: ["a@example.com"])
    assert auth._error_report_available() is True

    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    monkeypatch.setenv("MM_ERROR_REPORT_TO", "")
    assert auth._error_report_available() is False
    monkeypatch.setenv("MM_ERROR_REPORT_TO", "x@example.com")
    assert auth._error_report_available() is True


def test_auth_status(monkeypatch):
    monkeypatch.setattr(auth.feature_flags, "email_notify_feature_enabled", lambda: True)
    monkeypatch.setattr(auth.smtp_notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(auth.feature_flags, "openai_feature_enabled", lambda: True)
    monkeypatch.setattr(auth, "_error_report_available", lambda: True)
    monkeypatch.setattr(auth.db, "minutes_retention_days", lambda: 30)

    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    r = auth.auth_status()
    assert r.auth_required is False
    assert r.self_register_allowed is False

    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth.db, "count_users", lambda: 0)
    monkeypatch.setattr(auth, "self_register_enabled", lambda: True)
    r2 = auth.auth_status()
    assert r2.auth_required is True
    assert r2.bootstrap_needed is True
    assert r2.self_register_allowed is False

    monkeypatch.setattr(auth.db, "count_users", lambda: 3)
    r3 = auth.auth_status()
    assert r3.bootstrap_needed is False
    assert r3.self_register_allowed is True


def test_auth_login(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        auth.auth_login(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 400

    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth.db, "count_users", lambda: 0)
    with pytest.raises(HTTPException) as e:
        auth.auth_login(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 503

    monkeypatch.setattr(auth.db, "count_users", lambda: 1)
    monkeypatch.setattr(auth.db, "registry_login_normalize", lambda s: s.strip().lower())
    monkeypatch.setattr(auth.db, "validate_registry_login_email", lambda _e: (_ for _ in ()).throw(ValueError("bad email")))
    with pytest.raises(HTTPException) as e:
        auth.auth_login(LoginRequest(email="bad", password="x"))
    assert e.value.status_code == 400

    monkeypatch.setattr(auth.db, "validate_registry_login_email", lambda _e: None)
    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: None)
    with pytest.raises(HTTPException) as e:
        auth.auth_login(LoginRequest(email="u@example.com", password="x"))
    assert e.value.status_code == 401

    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: {"username": "u@example.com", "password_hash": "h"})
    monkeypatch.setattr(auth, "verify_password", lambda *_a, **_k: False)
    with pytest.raises(HTTPException) as e:
        auth.auth_login(LoginRequest(email="u@example.com", password="x"))
    assert e.value.status_code == 401

    monkeypatch.setattr(auth, "verify_password", lambda *_a, **_k: True)
    monkeypatch.setattr(auth, "create_access_token", lambda _u: "tok")
    ok = auth.auth_login(LoginRequest(email="u@example.com", password="x"))
    assert ok.access_token == "tok"


def test_auth_bootstrap(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        auth.auth_bootstrap(BootstrapRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 400

    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth.db, "count_users", lambda: 1)
    with pytest.raises(HTTPException) as e:
        auth.auth_bootstrap(BootstrapRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 403

    monkeypatch.setattr(auth.db, "count_users", lambda: 0)
    monkeypatch.setattr(auth.db, "registry_login_normalize", lambda s: s.strip().lower())
    monkeypatch.setattr(auth.db, "bootstrap_registry_admin", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("既に完了")))
    with pytest.raises(HTTPException) as e:
        auth.auth_bootstrap(BootstrapRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 403

    monkeypatch.setattr(auth.db, "bootstrap_registry_admin", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as e:
        auth.auth_bootstrap(BootstrapRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 400

    monkeypatch.setattr(
        auth.db,
        "bootstrap_registry_admin",
        lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.IntegrityError("dup")),
    )
    with pytest.raises(HTTPException) as e:
        auth.auth_bootstrap(BootstrapRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 409

    monkeypatch.setattr(auth.db, "bootstrap_registry_admin", lambda *_a, **_k: None)
    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: None)
    with pytest.raises(HTTPException) as e:
        auth.auth_bootstrap(BootstrapRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 500

    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: {"username": "a@example.com"})
    monkeypatch.setattr(auth, "create_access_token", lambda _u: "tok2")
    ok = auth.auth_bootstrap(BootstrapRequest(email="a@example.com", password="x"))
    assert ok.access_token == "tok2"


def test_auth_register(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        auth.auth_register(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 400

    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "self_register_enabled", lambda: False)
    with pytest.raises(HTTPException) as e:
        auth.auth_register(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 403

    monkeypatch.setattr(auth, "self_register_enabled", lambda: True)
    monkeypatch.setattr(auth.db, "count_users", lambda: 0)
    with pytest.raises(HTTPException) as e:
        auth.auth_register(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 400

    monkeypatch.setattr(auth.db, "count_users", lambda: 1)
    monkeypatch.setattr(auth.db, "registry_login_normalize", lambda s: s.strip().lower())
    monkeypatch.setattr(auth.db, "create_registry_user", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as e:
        auth.auth_register(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 400

    monkeypatch.setattr(
        auth.db,
        "create_registry_user",
        lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.IntegrityError("dup")),
    )
    with pytest.raises(HTTPException) as e:
        auth.auth_register(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 409

    monkeypatch.setattr(auth.db, "create_registry_user", lambda *_a, **_k: None)
    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: None)
    with pytest.raises(HTTPException) as e:
        auth.auth_register(LoginRequest(email="a@example.com", password="x"))
    assert e.value.status_code == 500

    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: {"username": "a@example.com"})
    monkeypatch.setattr(auth, "create_access_token", lambda _u: "tok3")
    ok = auth.auth_register(LoginRequest(email="a@example.com", password="x"))
    assert ok.access_token == "tok3"


def test_auth_me(monkeypatch):
    monkeypatch.setattr(auth, "auth_enabled", lambda: False)
    r = auth.auth_me("")
    assert r.email == ""
    assert r.is_admin is False

    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    with pytest.raises(HTTPException) as e:
        auth.auth_me("")
    assert e.value.status_code == 401

    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: None)
    monkeypatch.setattr(auth.db, "user_is_admin", lambda _u: False)
    r2 = auth.auth_me("u@example.com")
    assert r2.email == "u@example.com"
    assert r2.is_admin is False

    monkeypatch.setattr(auth.db, "get_user_by_username", lambda _u: {"username": "x@example.com"})
    monkeypatch.setattr(auth.db, "user_is_admin", lambda _u: True)
    r3 = auth.auth_me("u@example.com")
    assert r3.email == "x@example.com"
    assert r3.is_admin is True
