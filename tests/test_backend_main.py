import pytest
from types import SimpleNamespace

from backend import main


@pytest.mark.anyio
async def test_lifespan_auth_disabled(monkeypatch):
    calls = {"init": 0, "purge": 0, "info": 0, "warn": 0}

    monkeypatch.setattr(main.db, "init_db", lambda: calls.__setitem__("init", calls["init"] + 1))
    monkeypatch.setattr(main.db, "purge_all_minutes_archives", lambda: 2)
    monkeypatch.setattr(main, "auth_enabled", lambda: False)

    class _L:
        def info(self, *a, **k):
            calls["info"] += 1

        def warning(self, *a, **k):
            calls["warn"] += 1

    monkeypatch.setattr(main, "logging", SimpleNamespace(getLogger=lambda _n: _L()))

    async with main.lifespan(main.app):
        pass

    assert calls["init"] == 1
    assert calls["info"] == 1
    assert calls["warn"] == 1


@pytest.mark.anyio
async def test_lifespan_auth_enabled_no_purge(monkeypatch):
    calls = {"info": 0, "warn": 0}
    monkeypatch.setattr(main.db, "init_db", lambda: None)
    monkeypatch.setattr(main.db, "purge_all_minutes_archives", lambda: 0)
    monkeypatch.setattr(main, "auth_enabled", lambda: True)

    class _L:
        def info(self, *a, **k):
            calls["info"] += 1

        def warning(self, *a, **k):
            calls["warn"] += 1

    monkeypatch.setattr(main, "logging", SimpleNamespace(getLogger=lambda _n: _L()))

    async with main.lifespan(main.app):
        pass

    assert calls["info"] == 0
    assert calls["warn"] == 0


def test_main_app_wired():
    assert main.app.title == "Meeting Minutes API"
