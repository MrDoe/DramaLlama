from __future__ import annotations

import pytest

from drama_studio.engines import cosyvoice_directions as directions
from drama_studio.llm import LLMError, OllamaClient, OpenAIChatClient
from drama_studio.settings import LlmSettings


class FakeClient:
    def __init__(self, reply=None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def chat_json(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.error:
            raise self.error
        return self.reply


def _first_style() -> str:
    return next(iter(directions.STYLES))


def test_classification_maps_each_direction_to_a_known_style_or_none():
    style = _first_style()
    client = FakeClient(
        {"assignments": [{"id": 1, "style": style}, {"id": 2, "style": "none"}]}
    )

    result = directions.classify_directions(["leise", "blickt zur Tür"], client)

    assert result == {"leise": style, "blickt zur Tür": "none"}
    call = client.calls[0]
    assert call["keep_alive"] == 0
    enum = call["schema"]["properties"]["assignments"]["items"]["properties"]["style"]["enum"]
    assert set(enum) == {*directions.STYLES, "none"}
    assert "blickt zur Tür" in call["messages"][-1]["content"]


def test_unknown_styles_and_missing_answers_become_none():
    client = FakeClient({"assignments": [{"id": 1, "style": "operatic"}, {"id": 9}]})

    assert directions.classify_directions(["a", "b"], client) == {"a": "none", "b": "none"}


@pytest.mark.parametrize("reply", [[], {"assignments": "soft"}, {}])
def test_a_malformed_reply_is_an_error_so_nothing_gets_cached(reply):
    with pytest.raises(LLMError):
        directions.classify_directions(["leise"], FakeClient(reply))


def test_many_directions_are_sent_in_batches():
    client = FakeClient({"assignments": []})
    names = [f"d{i}" for i in range(directions.BATCH * 2 + 1)]

    assert set(directions.classify_directions(names, client)) == set(names)
    assert len(client.calls) == 3


def test_instruction_lookup():
    style = _first_style()
    assert directions.instruction_for(style) == directions.STYLES[style].instruction
    assert directions.instruction_for("none") is None
    assert directions.instruction_for("gone-from-the-table") is None


def _settings(monkeypatch, **values):
    monkeypatch.setattr(
        directions, "resolve_llm", lambda: (LlmSettings(**values), {})
    )


def test_a_configured_local_model_is_used(monkeypatch):
    _settings(monkeypatch, api="openai", url="http://127.0.0.1:8080/v1", model="m")
    monkeypatch.setattr(OpenAIChatClient, "available", lambda self: True)

    client = directions.local_llm_client()

    assert isinstance(client, OpenAIChatClient) and client.model == "m"


def test_a_hosted_configuration_is_never_used_local_ollama_is(monkeypatch):
    _settings(
        monkeypatch, api="openai", url="https://example.com/v1", model="hosted", api_key="k"
    )
    monkeypatch.setattr(OllamaClient, "probe_models", lambda self: (["qwen2.5:3b"], []))

    client = directions.local_llm_client()

    assert isinstance(client, OllamaClient)
    assert client.provider == "local"
    assert client.model == "qwen2.5:3b"
    assert client.api_key == ""


def test_no_local_model_means_no_classification(monkeypatch):
    _settings(monkeypatch, api="openai", url="https://example.com/v1", model="hosted")

    # What probe_models reports for an unreachable server.
    monkeypatch.setattr(OllamaClient, "probe_models", lambda self: ([], []))

    assert directions.local_llm_client() is None
