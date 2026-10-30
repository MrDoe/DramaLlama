from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.settings import preview_llm, resolve_llm, write_llm

ENV_VARS = (
    "DRAMA_LLAMA_LLM_API",
    "DRAMA_STUDIO_LLM_API",
    "DRAMA_LLAMA_LLM_URL",
    "DRAMA_STUDIO_LLM_URL",
    "OLLAMA_URL",
    "DRAMA_LLAMA_LLM",
    "DRAMA_STUDIO_LLM",
    "DRAMA_LLAMA_LLM_API_KEY",
    "DRAMA_STUDIO_LLM_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(
        settings_module, "SETTINGS_PATH", tmp_path / "settings.json"
    )
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def settings_path(tmp_path):
    import drama_studio.settings as settings_module

    return settings_module.SETTINGS_PATH


def test_resolve_defaults():
    settings, sources = resolve_llm()
    assert settings.api == "ollama"
    assert settings.url == "http://127.0.0.1:11434"
    assert settings.model == "gemma4:12b-16k"
    assert settings.api_key == ""
    assert sources == {
        "api": "default",
        "url": "default",
        "model": "default",
        "api_key": "default",
    }


def test_env_layer(monkeypatch):
    monkeypatch.setenv("DRAMA_LLAMA_LLM", "env-model")
    monkeypatch.setenv("OLLAMA_URL", "http://env-host:11434")
    settings, sources = resolve_llm()
    assert settings.model == "env-model"
    assert sources["model"] == "env"
    assert settings.url == "http://env-host:11434"
    assert sources["url"] == "env"
    assert sources["api"] == "default"


def test_file_wins_over_env(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setenv("DRAMA_LLAMA_LLM", "env-model")
    monkeypatch.setenv("DRAMA_LLAMA_LLM_URL", "http://env-host:11434")
    settings_module.SETTINGS_PATH.write_text(
        json.dumps({"llm": {"model": "file-model"}})
    )
    settings, sources = resolve_llm()
    assert settings.model == "file-model"
    assert sources["model"] == "file"
    assert settings.url == "http://env-host:11434"
    assert sources["url"] == "env"


def test_invalid_file_values_ignored(tmp_path):
    import drama_studio.settings as settings_module

    settings_module.SETTINGS_PATH.write_text(
        json.dumps({"llm": {"api": "bogus", "model": "", "url": 42}})
    )
    settings, sources = resolve_llm()
    assert settings.api == "ollama"
    assert settings.model == "gemma4:12b-16k"
    assert settings.url == "http://127.0.0.1:11434"
    assert sources["api"] == "default"


def test_write_validation():
    with pytest.raises(ValueError, match="ollama.*openai"):
        write_llm({"api": "bogus"})
    with pytest.raises(ValueError, match="http"):
        write_llm({"url": "not-a-url"})
    with pytest.raises(ValueError, match="unknown"):
        write_llm({"nope": "x"})
    with pytest.raises(ValueError, match="non-empty"):
        write_llm({"model": "  "})


def test_write_and_clear_api_key():
    settings, sources = write_llm({"api_key": "secret"})
    assert settings.api_key == "secret"
    assert sources["api_key"] == "file"
    settings, _ = write_llm({"api_key": ""})
    assert settings.api_key == ""
    import drama_studio.settings as settings_module

    assert "api_key" not in json.loads(
        settings_module.SETTINGS_PATH.read_text()
    )["llm"]


def test_preview_does_not_persist():
    preview = preview_llm({"model": "preview-model"})
    assert preview.model == "preview-model"
    settings, sources = resolve_llm()
    assert settings.model == "gemma4:12b-16k"
    assert sources["model"] == "default"


class FakeClient:
    pass


def make_client(tmp_path):
    app = create_app(
        tmp_path / "jobs.sqlite3", tmp_path / "projects", llm_client=FakeClient()
    )
    return app, TestClient(app)


def test_config_put_roundtrip_rebuilds_client(tmp_path):
    app, client = make_client(tmp_path)
    response = client.put(
        "/api/config",
        json={"llm": {"model": "saved-model", "api_key": "secret"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["llm"]["model"] == "saved-model"
    assert body["llm"]["sources"]["model"] == "file"
    assert body["llm"]["apiKeyConfigured"] is True
    assert "secret" not in response.text
    assert app.state.llm.model == "saved-model"

    config = client.get("/api/config").json()
    assert config["llm"]["model"] == "saved-model"
    assert "secret" not in json.dumps(config)


def test_config_put_invalid_rejected(tmp_path):
    _, client = make_client(tmp_path)
    response = client.put("/api/config", json={"llm": {"api": "bogus"}})
    assert response.status_code == 400


def test_config_test_uses_patch_without_persisting(tmp_path, monkeypatch):
    from drama_studio.llm import OpenAIChatClient

    _, client = make_client(tmp_path)
    monkeypatch.setattr(
        OpenAIChatClient,
        "check",
        lambda self: {
            "ok": True,
            "models": ["kimi-k3"],
            "excluded": ["text-embedding-x"],
            "error": None,
        },
    )
    response = client.post(
        "/api/config/test",
        json={
            "llm": {
                "api": "openai",
                "url": "https://cloud.example.com",
                "model": "kimi-k3",
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "api": "openai",
        "provider": "cloud",
        "model": "kimi-k3",
        "models": ["kimi-k3"],
        "excluded": ["text-embedding-x"],
        "error": None,
    }
    saved = client.get("/api/config").json()
    assert saved["llm"]["api"] == "ollama"


def test_config_test_reports_unreachable(tmp_path, monkeypatch):
    from drama_studio.llm import OllamaClient

    _, client = make_client(tmp_path)
    monkeypatch.setattr(OllamaClient, "available", lambda self: False)
    response = client.post("/api/config/test", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["models"] == []
    assert body["excluded"] == []
    assert "http://127.0.0.1:11434" in body["error"]
