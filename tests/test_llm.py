from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.llm import LLMError, LLMParseError, llm_parse_script

RAW = """# TEST DRAMA

## SCENE 1 — ROOM

*[SFX: rain]*

**MARA** *(cold)*
You're late.

**ELIAS** *(tired)*
Traffic.
"""

MESSY_RAW = """Test Drama
Scene 1 Room
[MARA] "You're late."
ELIAS: "Traffic."
"""

CANONICAL = """# TEST DRAMA

## SCENE 1 — ROOM

[SFX: rain]

MARA (cold): You're late.
ELIAS (tired): Traffic.
"""


class FakeClient:
    def __init__(self, text=CANONICAL, error=None):
        self.text = text
        self.error = error
        self.calls = []

    def chat_text(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.text


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(
        settings_module, "SETTINGS_PATH", tmp_path / "settings.json"
    )
    for var in (
        "DRAMA_LLAMA_LLM_API",
        "DRAMA_STUDIO_LLM_API",
        "DRAMA_LLAMA_LLM_URL",
        "DRAMA_STUDIO_LLM_URL",
        "OLLAMA_URL",
        "DRAMA_LLAMA_LLM",
        "DRAMA_STUDIO_LLM",
        "DRAMA_LLAMA_LLM_API_KEY",
        "DRAMA_STUDIO_LLM_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_llm_parse_converts_and_validates():
    client = FakeClient()
    result = llm_parse_script(RAW, "en", client=client)
    assert result.report.method == "llm"
    assert result.report.coverage == 1.0
    assert len(result.script.scenes) == 1
    assert [line.speaker for line in result.script.scenes[0].lines] == ["MARA", "ELIAS"]
    assert result.script.scenes[0].lines[0].direction == "cold"
    assert client.calls[0][1]["keep_alive"] == 0


def test_llm_parse_normalizes_messy_formats():
    result = llm_parse_script(MESSY_RAW, "en", client=FakeClient())
    assert result.report.method == "llm"
    assert result.report.coverage == 1.0
    assert [line.speaker for line in result.script.scenes[0].lines] == ["MARA", "ELIAS"]
    assert result.script.scenes[0].lines[0].text == "You're late."


def test_llm_parse_rejects_paraphrase():
    text = """# TEST DRAMA

## SCENE 1 — ROOM

MARA (cold): You are late.
ELIAS (tired): Traffic.
"""
    with pytest.raises(LLMParseError):
        llm_parse_script(RAW, "en", client=FakeClient(text))


def test_llm_parse_strips_code_fences():
    text = "```text\n" + CANONICAL + "\n```"
    result = llm_parse_script(RAW, "en", client=FakeClient(text))
    assert result.report.method == "llm"
    assert len(result.script.scenes[0].lines) == 2


def make_client(tmp_path, llm_client):
    app = create_app(
        tmp_path / "jobs.sqlite3", tmp_path / "projects", llm_client=llm_client
    )
    return app, TestClient(app)


def test_api_llm_parse(tmp_path):
    _, client = make_client(tmp_path, FakeClient())
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": RAW}
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/parse", json={"method": "llm"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["method"] == "llm"
    assert len(body["script"]["scenes"][0]["lines"]) == 2


def test_api_llm_failure_falls_back(tmp_path):
    _, client = make_client(tmp_path, FakeClient(error=LLMError("no model")))
    project = client.post(
        "/api/projects", json={"title": "Test", "language": "en", "script_text": RAW}
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/parse", json={"method": "llm"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["method"] == "deterministic"
    assert any("LLM parse failed" in warning["message"] for warning in body["report"]["warnings"])


def test_api_config_reports_llm(tmp_path):
    _, client = make_client(tmp_path, FakeClient())
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert body["llm"]["provider"] == "local"
    assert body["llm"]["api"] == "ollama"
    assert body["llm"]["apiKeyConfigured"] is False
    assert "api_key" not in body["llm"]
    assert "apiKey" not in body["llm"]


def test_ollama_client_provider_and_auth_header(monkeypatch):
    from drama_studio.llm import OllamaClient

    assert OllamaClient("http://127.0.0.1:11434").provider == "local"
    assert OllamaClient("http://localhost:11434").provider == "local"
    assert OllamaClient("https://cloud.example.com").provider == "cloud"

    seen: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "{}"}}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("drama_studio.llm.httpx.post", fake_post)
    OllamaClient("https://cloud.example.com", api_key="secret").chat_json([])
    assert seen["headers"] == {"Authorization": "Bearer secret"}
    OllamaClient("https://cloud.example.com").chat_json([])
    assert seen["headers"] == {}


class FakeOpenAIResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class FakeOpenAIStream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return iter(self._lines)


def test_openai_client_chat_json_shape(monkeypatch):
    from drama_studio.llm import OpenAIChatClient

    seen: dict[str, object] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["payload"] = json
        seen["headers"] = headers
        return FakeOpenAIResponse(
            {"choices": [{"message": {"content": '{"title": "X"}'}}]}
        )

    monkeypatch.setattr("drama_studio.llm.httpx.post", fake_post)
    client = OpenAIChatClient("https://opencode.ai/inference/go/openai/v1", api_key="k")
    result = client.chat_json([{"role": "user", "content": "hi"}], schema={"type": "object"})
    assert result == {"title": "X"}
    assert seen["url"] == (
        "https://opencode.ai/inference/go/openai/v1/chat/completions"
    )
    assert seen["payload"]["model"] == client.model
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert "format" not in seen["payload"]
    assert "options" not in seen["payload"]
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["headers"]["User-Agent"].startswith("drama-llama/")
    assert seen["headers"]["x-opencode-session"]
    assert client.provider == "cloud"


def test_openai_client_chat_json_model_override(monkeypatch):
    from drama_studio.llm import OpenAIChatClient

    seen: dict[str, object] = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["payload"] = json
        return FakeOpenAIResponse({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr("drama_studio.llm.httpx.post", fake_post)
    OpenAIChatClient("https://cloud.example.com", model="base").chat_json(
        [], model="override"
    )
    assert seen["payload"]["model"] == "override"
    assert "response_format" not in seen["payload"]


def test_openai_client_chat_stream_parses_sse(monkeypatch):
    from drama_studio.llm import OpenAIChatClient

    lines = [
        'data: {"choices": [{"delta": {"content": "{\\"a\\""}}]}',
        "",
        'data: {"choices": [{"delta": {"content": ": 1}"}}]}',
        "data: [DONE]",
    ]

    def fake_stream(method, url, json=None, headers=None, timeout=None):
        assert url.endswith("/chat/completions")
        assert json["stream"] is True
        assert headers["User-Agent"].startswith("drama-llama/")
        return FakeOpenAIStream(lines)

    monkeypatch.setattr("drama_studio.llm.httpx.stream", fake_stream)
    chunks = list(OpenAIChatClient("https://cloud.example.com").chat_stream([]))
    assert chunks == ['{"a"', ": 1}"]
    assert "".join(chunks) == '{"a": 1}'


def test_openai_client_unload_is_noop():
    from drama_studio.llm import OpenAIChatClient

    assert OpenAIChatClient("https://cloud.example.com").unload() is None


class FakeCatalogResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def test_openai_probe_falls_back_to_go_catalog(monkeypatch):
    from drama_studio.llm import OpenAIChatClient

    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url == "https://opencode.ai/inference/go/openai/v1/models":
            return FakeCatalogResponse(404, {})
        if url == "https://opencode.ai/inference/go/v1/models":
            return FakeCatalogResponse(
                200,
                {"data": [{"id": "kimi-k3"}, {"id": "text-embedding-3"}]},
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("drama_studio.llm.httpx.get", fake_get)
    client = OpenAIChatClient("https://opencode.ai/inference/go/openai/v1")
    usable, excluded = client.probe_models()
    assert usable == ["kimi-k3"]
    assert excluded == ["text-embedding-3"]
    assert calls[0].endswith("/openai/v1/models")
    assert client.available() is True


def test_openai_check_reports_auth_failure(monkeypatch):
    from drama_studio.llm import OpenAIChatClient

    def fake_get(url, headers=None, timeout=None):
        return FakeCatalogResponse(401, {})

    monkeypatch.setattr("drama_studio.llm.httpx.get", fake_get)
    result = OpenAIChatClient("https://cloud.example.com").check()
    assert result["ok"] is False
    assert result["models"] == []
    assert "401" in result["error"]


def test_openai_check_reports_unreachable(monkeypatch):
    from drama_studio.llm import OpenAIChatClient

    def fake_get(url, headers=None, timeout=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("drama_studio.llm.httpx.get", fake_get)
    result = OpenAIChatClient("https://cloud.example.com").check()
    assert result["ok"] is False
    assert "no response" in result["error"]


def test_ollama_probe_hides_embedding_models(monkeypatch):
    from drama_studio.llm import OllamaClient

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/api/tags")
        return FakeCatalogResponse(
            200,
            {
                "models": [
                    {"name": "chat-a"},
                    {"name": "embed-b"},
                    {"name": "mystery-c"},
                ]
            },
        )

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith("/api/show")
        capabilities = {
            "chat-a": ["completion"],
            "embed-b": ["embedding"],
            "mystery-c": [],
        }[json["model"]]
        return FakeCatalogResponse(200, {"capabilities": capabilities} if capabilities else {})

    monkeypatch.setattr("drama_studio.llm.httpx.get", fake_get)
    monkeypatch.setattr("drama_studio.llm.httpx.post", fake_post)
    usable, excluded = OllamaClient("http://127.0.0.1:11434").probe_models()
    assert usable == ["chat-a", "mystery-c"]
    assert excluded == ["embed-b"]


def test_make_llm_client_selects_protocol(monkeypatch):
    from drama_studio.llm import OllamaClient, OpenAIChatClient, make_llm_client

    assert isinstance(make_llm_client(), OllamaClient)
    monkeypatch.setenv("DRAMA_LLAMA_LLM_API", "openai")
    client = make_llm_client()
    assert isinstance(client, OpenAIChatClient)
