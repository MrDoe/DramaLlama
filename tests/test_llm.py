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

PAYLOAD = {
    "title": "TEST DRAMA",
    "language": "en",
    "characters": [
        {"id": "MARA", "name": "Mara", "description": "Controlled."},
        {"id": "ELIAS", "name": "Elias", "description": "Tired."},
    ],
    "scenes": [
        {
            "heading": "SCENE 1 — ROOM",
            "setting": "",
            "stage_directions": ["SFX: rain"],
            "lines": [
                {"speaker": "MARA", "text": "You're late.", "direction": "cold", "confidence": 0.9},
                {"speaker": "ELIAS", "text": "Traffic.", "direction": "tired", "confidence": 0.9},
            ],
        }
    ],
}


class FakeClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.payload


def test_llm_parse_converts_and_validates():
    client = FakeClient(PAYLOAD)
    result = llm_parse_script(RAW, "en", client=client)
    assert result.report.method == "llm"
    assert result.report.coverage == 1.0
    assert len(result.script.scenes) == 1
    assert [line.speaker for line in result.script.scenes[0].lines] == ["MARA", "ELIAS"]
    assert result.script.scenes[0].lines[0].direction == "cold"
    assert client.calls[0][1]["keep_alive"] == 0


def test_llm_parse_rejects_paraphrase():
    payload = {
        **PAYLOAD,
        "scenes": [
            {
                **PAYLOAD["scenes"][0],
                "lines": [{"speaker": "MARA", "text": "You are late.", "confidence": 1.0}],
            }
        ],
    }
    with pytest.raises(LLMParseError):
        llm_parse_script(RAW, "en", client=FakeClient(payload))


def make_client(tmp_path, llm_client):
    app = create_app(
        tmp_path / "jobs.sqlite3", tmp_path / "projects", llm_client=llm_client
    )
    return app, TestClient(app)


def test_api_llm_parse(tmp_path):
    _, client = make_client(tmp_path, FakeClient(PAYLOAD))
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
