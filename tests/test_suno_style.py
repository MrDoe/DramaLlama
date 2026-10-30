from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.llm import LLMError
from drama_studio.models import Character, Line
from drama_studio.suno_lyrics import (
    SUNO_STYLE_BASE_TAGS,
    llm_suggest_suno_style,
    suggest_suno_style_deterministic,
)


def make_lines() -> list[Line]:
    return [
        Line(id="s1-01", scene=1, speaker="MARA", text="You're late.", direction="cold, measured"),
        Line(id="s1-02", scene=1, speaker="MARA", text="Pack.", direction="sharp"),
    ]


def test_deterministic_style_prefill():
    character = Character(id="MARA", name="Mara", description="Cold controller.")
    suggestion = suggest_suno_style_deterministic(make_lines(), character, "SCENE 1 — ROOM")
    assert suggestion["tags"].startswith(SUNO_STYLE_BASE_TAGS)
    assert "cold, measured" in suggestion["tags"]
    assert "Mara" in suggestion["title"]
    assert len(suggestion["tags"]) <= 160
    assert len(suggestion["title"]) <= 60


def test_deterministic_style_without_directions():
    suggestion = suggest_suno_style_deterministic(
        [Line(id="s1-01", scene=1, speaker="X", text="Hi.")], None, ""
    )
    assert suggestion["tags"] == SUNO_STYLE_BASE_TAGS
    assert suggestion["title"] == "X"


class FakeLLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.payload


def test_llm_style_accepted():
    client = FakeLLM({"title": "Mara — Cold Room", "tags": "tense, whispered interrogation"})
    suggestion = llm_suggest_suno_style(make_lines(), client=client)
    assert suggestion["title"] == "Mara — Cold Room"
    assert suggestion["tags"].startswith(SUNO_STYLE_BASE_TAGS)
    assert "tense" in suggestion["tags"]
    assert client.calls[0][1]["keep_alive"] == 0


def test_llm_style_rejects_empty_tags():
    from drama_studio.llm import LLMParseError

    with pytest.raises(LLMParseError):
        llm_suggest_suno_style(make_lines(), client=FakeLLM({"title": "x", "tags": "  "}))


def test_llm_style_client_error():
    with pytest.raises(LLMError):
        llm_suggest_suno_style(make_lines(), client=FakeLLM(error=RuntimeError("offline")))


SCRIPT = """# STYLE TEST

## SCENE 1

MARA (cold): You're late.
"""


def make_client(tmp_path, llm_client):
    app = create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects", llm_client=llm_client)
    return TestClient(app)


def _project_with_stem(client):
    project = client.post(
        "/api/projects",
        json={"title": "Style", "language": "en", "script_text": SCRIPT},
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = next(s for s in built["stems"] if s["speaker"] == "MARA")
    return project, stem


def test_api_suno_style_llm_prefills_blanks(tmp_path):
    client = make_client(
        tmp_path, FakeLLM({"title": "Mara — Cold", "tags": "tense late-night"})
    )
    project, stem = _project_with_stem(client)
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
        json={"method": "llm"},
    )
    assert response.status_code == 200
    updated = next(s for s in response.json()["stems"] if s["id"] == stem["id"])
    assert updated["suno_title"] == "Mara — Cold"
    assert updated["suno_tags"].startswith(SUNO_STYLE_BASE_TAGS)


def test_api_suno_style_keeps_user_edits_unless_overwrite(tmp_path):
    client = make_client(tmp_path, FakeLLM({"title": "LLM", "tags": "llm-tag"}))
    project, stem = _project_with_stem(client)
    client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"suno_title": "Mine"},
    )
    kept = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
        json={"method": "llm"},
    )
    updated = next(s for s in kept.json()["stems"] if s["id"] == stem["id"])
    assert updated["suno_title"] == "Mine"
    assert updated["suno_tags"].startswith(SUNO_STYLE_BASE_TAGS)
    forced = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
        json={"method": "llm", "overwrite": True},
    )
    updated = next(s for s in forced.json()["stems"] if s["id"] == stem["id"])
    assert updated["suno_title"] == "LLM"


def test_api_suno_style_deterministic_and_validation(tmp_path):
    client = make_client(tmp_path, FakeLLM(error=LLMError("offline")))
    project, stem = _project_with_stem(client)
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
        json={"method": "deterministic"},
    )
    assert response.status_code == 200
    updated = next(s for s in response.json()["stems"] if s["id"] == stem["id"])
    assert updated["suno_tags"].startswith(SUNO_STYLE_BASE_TAGS)
    assert "[cold]" in (updated["suno_lyrics"] or "")
    assert (
        client.post(
            f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
            json={"method": "karaoke"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/projects/{project['id']}/stems/nope/suno-style",
            json={"method": "llm"},
        ).status_code
        == 404
    )
    failing = make_client(tmp_path, FakeLLM(error=LLMError("offline")))
    project, stem = _project_with_stem(failing)
    assert (
        failing.post(
            f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
            json={"method": "llm"},
        ).status_code
        == 502
    )


def test_api_suno_style_only_tags(tmp_path):
    client = make_client(tmp_path, FakeLLM({"title": "LLM Title", "tags": "llm-tag"}))
    project, stem = _project_with_stem(client)
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
        json={"method": "llm", "overwrite": True, "only": ["tags"]},
    )
    assert response.status_code == 200
    updated = next(s for s in response.json()["stems"] if s["id"] == stem["id"])
    assert updated["suno_title"] is None
    assert updated["suno_tags"].startswith(SUNO_STYLE_BASE_TAGS)
    bad = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-style",
        json={"method": "llm", "only": ["karaoke"]},
    )
    assert bad.status_code == 400
