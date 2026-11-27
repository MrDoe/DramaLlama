"""New German projects start on CosyVoice when it is installed; nothing else moves."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drama_studio import api as api_module
from drama_studio.api import create_app

SCRIPT = "## SZENE 1\n\nANNA: Guten Morgen.\n"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects"))


def _create(client, language: str, **extra) -> dict:
    response = client.post(
        "/api/projects",
        json={"title": "T", "language": language, "script_text": SCRIPT, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    ("installed", "language", "requested", "stored"),
    [
        (True, "de", None, "cosyvoice"),
        (True, "en", None, None),
        (True, "de", "chatterbox", "chatterbox"),
        (False, "de", None, None),
    ],
)
def test_default_engine_of_a_new_project(
    client, monkeypatch, installed, language, requested, stored
):
    monkeypatch.setattr(api_module, "_cosyvoice_installed", lambda: installed)

    project = _create(client, language, default_engine=requested)

    assert project["default_engine"] == stored


def test_existing_auto_projects_are_left_alone(client, monkeypatch):
    monkeypatch.setattr(api_module, "_cosyvoice_installed", lambda: False)
    project = _create(client, "de")

    monkeypatch.setattr(api_module, "_cosyvoice_installed", lambda: True)

    assert client.get(f"/api/projects/{project['id']}").json()["default_engine"] is None
