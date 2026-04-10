import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import ollama_client
from backend import ollama_model_profiles as omp


class _Resp:
    def __init__(self, body: str):
        self._b = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._b


def test_ollama_client_base_and_filter(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://x:11434/")
    assert ollama_client.ollama_base_url() == "http://x:11434"
    assert ollama_client.ollama_generate_url().endswith("/api/generate")

    monkeypatch.delenv("MM_OLLAMA_UI_EXCLUDE_CONTAINS", raising=False)
    assert ollama_client._ollama_model_excluded_from_ui("") is True
    assert ollama_client._ollama_model_excluded_from_ui("nomic-embed-text:latest") is True
    monkeypatch.setenv("MM_OLLAMA_UI_EXCLUDE_CONTAINS", "foo,bar")
    assert ollama_client._ollama_model_excluded_from_ui("my-foo-model") is True


def test_ollama_unload_and_fetch(monkeypatch):
    monkeypatch.setenv("OLLAMA_UNLOAD_ON_TASK_END", "0")
    ollama_client.try_ollama_unload_model("model")  # no-op branch

    monkeypatch.setenv("OLLAMA_UNLOAD_ON_TASK_END", "1")
    ollama_client.try_ollama_unload_model("")  # empty name branch

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", lambda *a, **k: _Resp("{}"))
    ollama_client.try_ollama_unload_model("m1")

    monkeypatch.setattr(
        ollama_client.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("u", 500, "x", None, None)),
    )
    ollama_client.try_ollama_unload_model("m2")

    monkeypatch.setattr(
        ollama_client.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("ng")),
    )
    ollama_client.try_ollama_unload_model("m3")

    monkeypatch.setattr(
        ollama_client.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("to")),
    )
    ollama_client.try_ollama_unload_model("m4")

    monkeypatch.setattr(
        ollama_client.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )
    ollama_client.try_ollama_unload_model("m5")

    # /api/tags success + exclude
    body = json.dumps({"models": [{"name": "qwen2.5:7b"}, {"model": "nomic-embed-text:latest"}]})
    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    names = ollama_client.fetch_ollama_model_names()
    assert names == ["qwen2.5:7b"]

    # empty models warning path
    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", lambda *a, **k: _Resp(json.dumps({"models": []})))
    assert ollama_client.fetch_ollama_model_names() == []

    monkeypatch.setattr(
        ollama_client.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("u", 500, "x", None, None)),
    )
    assert ollama_client.fetch_ollama_model_names() == []

    monkeypatch.setattr(
        ollama_client.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("ng")),
    )
    assert ollama_client.fetch_ollama_model_names() == []

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", lambda *a, **k: _Resp("{bad-json"))
    assert ollama_client.fetch_ollama_model_names() == []

    # models 内に dict 以外 + 全件除外
    body2 = json.dumps({"models": [123, {"name": "nomic-embed-text:latest"}]})
    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", lambda *a, **k: _Resp(body2))
    assert ollama_client.fetch_ollama_model_names() == []

    monkeypatch.setattr(
        ollama_client.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )
    assert ollama_client.fetch_ollama_model_names() == []


def test_ollama_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("MM_OLLAMA_PROFILES", "0")
    assert omp._profiles_enabled() is False
    assert omp._load_combined_rows() == []

    monkeypatch.setenv("MM_OLLAMA_PROFILES", "1")
    monkeypatch.delenv("MM_OLLAMA_PROFILES_PATH", raising=False)
    rows = omp._load_combined_rows()
    assert rows and isinstance(rows, list)

    # bad path fallback
    monkeypatch.setenv("MM_OLLAMA_PROFILES_PATH", str(tmp_path / "none.json"))
    rows2 = omp._load_combined_rows()
    assert rows2

    # valid file + cache hit
    p = tmp_path / "profiles.json"
    p.write_text(
        json.dumps(
            {
                "defaults": {"num_ctx": 8192},
                "profiles": [
                    {"match": "qwen", "top_p": 0.9},
                    "bad",
                    {"match": ""},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MM_OLLAMA_PROFILES_PATH", str(p))
    omp._PROFILE_STATE.update({"path": None, "mtime": None, "rows": None})
    rows3 = omp._load_combined_rows()
    assert any(str(r.get("match", "")).startswith("qwen") for r in rows3)
    rows4 = omp._load_combined_rows()
    assert rows4 is rows3

    with pytest.raises(ValueError):
        bad = tmp_path / "bad1.json"
        bad.write_text("[]", encoding="utf-8")
        omp._parse_profiles_file(str(bad))

    with pytest.raises(ValueError):
        bad2 = tmp_path / "bad2.json"
        bad2.write_text(json.dumps({"defaults": [1], "profiles": []}), encoding="utf-8")
        omp._parse_profiles_file(str(bad2))

    with pytest.raises(ValueError):
        bad3 = tmp_path / "bad3.json"
        bad3.write_text(json.dumps({"defaults": {}, "profiles": 1}), encoding="utf-8")
        omp._parse_profiles_file(str(bad3))

    assert omp._row_from_defaults({"a": 1}, {"match": "x", "b": 2, "c": None}) == {"a": 1, "b": 2}

    prof = omp._first_matching_profile("qwen2.5:7b", [{"match": "llama"}, {"match": "qwen"}])
    assert prof and prof["match"] == "qwen"
    assert omp._first_matching_profile("qwen2.5:7b", [{"match": ""}, {"match": "qwen"}])["match"] == "qwen"
    assert omp._first_matching_profile("", [{"match": "qwen"}]) is None

    orig_load = omp._load_combined_rows
    monkeypatch.setattr(
        omp,
        "_load_combined_rows",
        lambda: [{"match": "qwen", "num_ctx": 4096, "extract_temperature": 0.1, "merge_temperature": 0.2}],
    )
    o1 = omp.resolve_ollama_options("qwen2.5:7b", phase="extract", caller_temperature=0.5)
    assert o1["num_ctx"] == 4096 and o1["temperature"] == 0.1
    o2 = omp.resolve_ollama_options("qwen2.5:7b", phase="merge", caller_temperature=0.5)
    assert o2["temperature"] == 0.2

    monkeypatch.setattr(
        omp,
        "_load_combined_rows",
        lambda: [{"match": "qwen", "temperature": 0.3}],
    )
    o2b = omp.resolve_ollama_options("qwen2.5:7b", caller_temperature=0.5)
    assert o2b["temperature"] == 0.3

    o3 = omp.resolve_ollama_options("unknown", caller_temperature=0.7)
    assert o3["temperature"] == 0.7

    # _load_combined_rows の JSON 失敗パス
    monkeypatch.setattr(omp, "_load_combined_rows", orig_load)
    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("MM_OLLAMA_PROFILES", "1")
    monkeypatch.setenv("MM_OLLAMA_PROFILES_PATH", str(bad_json))
    omp._PROFILE_STATE.update({"path": None, "mtime": None, "rows": None})
    rows_bad = omp._load_combined_rows()
    assert rows_bad
