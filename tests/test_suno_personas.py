from __future__ import annotations

import pytest

from drama_studio.api import create_app
from drama_studio.engines.suno import SunoEngine
from drama_studio.models import Character, StemRenderRequest
from drama_studio.suno import SunoBridge, SunoError


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


PERSONAS_PAYLOAD = {
    "ok": True,
    "own": [
        {
            "id": "voice-1",
            "name": "Heinrich",
            "description": "",
            "persona_model": "voice_persona",
            "image_url": None,
            "root_clip_id": "clip-1",
        }
    ],
    "followed": [],
}


def test_list_personas(monkeypatch):
    seen = {}

    def fake_get(url, timeout=None):
        seen["url"] = url
        return FakeResponse(PERSONAS_PAYLOAD)

    monkeypatch.setattr("drama_studio.suno.httpx.get", fake_get)
    data = SunoBridge().list_personas()
    assert seen["url"].endswith("/personas")
    assert data["own"][0]["name"] == "Heinrich"
    assert data["own"][0]["persona_model"] == "voice_persona"


def test_list_personas_unreachable(monkeypatch):
    def fake_get(url, timeout=None):
        raise RuntimeError("no route")

    monkeypatch.setattr("drama_studio.suno.httpx.get", fake_get)
    with pytest.raises(SunoError):
        SunoBridge().list_personas()


def test_get_persona(monkeypatch):
    seen = {}

    def fake_get(url, timeout=None):
        seen["url"] = url
        return FakeResponse({"ok": True, "persona": {"id": "voice-1"}})

    monkeypatch.setattr("drama_studio.suno.httpx.get", fake_get)
    data = SunoBridge().get_persona("voice-1")
    assert seen["url"].endswith("/personas/voice-1")
    assert data["persona"]["id"] == "voice-1"


def test_character_accepts_persona_model():
    character = Character(
        id="MARA", name="Mara", suno_persona_id="voice-1",
        suno_persona_model="voice_persona",
    )
    assert character.suno_persona_id == "voice-1"
    assert character.suno_persona_model == "voice_persona"


def test_suno_engine_forwards_persona(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_module.write_suno({"plan": "pro"})

    captured: dict = {}

    class FakeBridge:
        def generate(self, payload):
            captured.update(payload)
            return {"clips": []}

    engine = SunoEngine()
    engine.bridge = FakeBridge()  # type: ignore[assignment]
    engine._loaded = True
    request = StemRenderRequest(
        project_id="p",
        stem_id="s1-mara",
        speaker="MARA",
        text="You're late.",
        instruction="...",
        output_dir=str(tmp_path),
        persona_id="voice-1",
        persona_model="voice_persona",
    )
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(request)
    assert captured["persona_id"] == "voice-1"
    assert captured["persona_model"] == "voice_persona"


def test_suno_engine_omits_persona_when_unset(tmp_path):
    captured: dict = {}

    class FakeBridge:
        def generate(self, payload):
            captured.update(payload)
            return {"clips": []}

    engine = SunoEngine()
    engine.bridge = FakeBridge()  # type: ignore[assignment]
    engine._loaded = True
    request = StemRenderRequest(
        project_id="p",
        stem_id="s1-mara",
        speaker="MARA",
        text="You're late.",
        instruction="...",
        output_dir=str(tmp_path),
    )
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(request)
    assert "persona_id" not in captured
    assert "persona_model" not in captured


SCRIPT_PERSONA = """# PERSONA TEST

## SCENE 1

MARA (cold): You're late.
"""


class FakeBridge:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def list_personas(self):
        if self.error:
            raise self.error
        return self.payload

    def get_persona(self, persona_id):
        if self.error:
            raise self.error
        return {"ok": True, "persona": {"id": persona_id, "name": "Heinrich"}}


def make_api_client(tmp_path, monkeypatch, bridge):
    from fastapi.testclient import TestClient

    import drama_studio.api as api_module

    monkeypatch.setattr(api_module, "SunoBridge", lambda *args, **kwargs: bridge)
    app = create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects")
    return TestClient(app)


def _persona_project(client):
    project = client.post(
        "/api/projects",
        json={"title": "Persona", "language": "en", "script_text": SCRIPT_PERSONA},
    ).json()
    return project


def test_api_suno_personas_lists(tmp_path, monkeypatch):
    from drama_studio.suno import SunoError

    client = make_api_client(
        tmp_path, monkeypatch, FakeBridge(payload={"ok": True, "own": [{"id": "v1", "name": "Heinrich"}], "followed": []})
    )
    response = client.get("/api/suno/personas")
    assert response.status_code == 200
    assert response.json()["own"][0]["name"] == "Heinrich"


def test_api_suno_personas_bridge_error_is_502(tmp_path, monkeypatch):
    from drama_studio.suno import SunoError

    client = make_api_client(tmp_path, monkeypatch, FakeBridge(error=SunoError("down")))
    assert client.get("/api/suno/personas").status_code == 502


def test_api_suno_persona_detail(tmp_path, monkeypatch):
    client = make_api_client(tmp_path, monkeypatch, FakeBridge())
    response = client.get("/api/suno/personas/v1")
    assert response.status_code == 200
    assert response.json()["persona"]["id"] == "v1"


def test_api_character_persona_select_and_clear(tmp_path, monkeypatch):
    client = make_api_client(tmp_path, monkeypatch, FakeBridge())
    project = _persona_project(client)
    updated = client.patch(
        f"/api/projects/{project['id']}/characters/MARA",
        json={"suno_persona_id": "v1", "suno_persona_model": "voice_persona"},
    )
    assert updated.status_code == 200
    character = updated.json()["script"]["characters"]["MARA"]
    assert character["suno_persona_id"] == "v1"
    assert character["suno_persona_model"] == "voice_persona"
    cleared = client.patch(
        f"/api/projects/{project['id']}/characters/MARA",
        json={"suno_persona_id": "", "suno_persona_model": ""},
    )
    assert cleared.status_code == 200
    character = cleared.json()["script"]["characters"]["MARA"]
    assert character["suno_persona_id"] is None
    assert character["suno_persona_model"] is None


def test_api_character_persona_model_validated(tmp_path, monkeypatch):
    client = make_api_client(tmp_path, monkeypatch, FakeBridge())
    project = _persona_project(client)
    response = client.patch(
        f"/api/projects/{project['id']}/characters/MARA",
        json={"suno_persona_model": "bogus"},
    )
    assert response.status_code == 400
