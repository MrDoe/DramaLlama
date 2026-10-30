from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drama_studio.api import create_app
from drama_studio.llm import LLMError
from drama_studio.models import Character, Line, ParseReport, Project, Scene, Script
from drama_studio.stems import build_stems
from drama_studio.suno_lyrics import (
    check_lyrics_verbatim,
    format_suno_lyrics,
    llm_format_suno_lyrics,
    resolve_suno_lyrics,
)


def make_project() -> Project:
    script = Script(
        title="Test",
        language="en",
        characters={
            "MARA": Character(id="MARA", name="Mara", description="Cold, controlled."),
        },
        scenes=[
            Scene(
                id=1,
                heading="SCENE 1 — ROOM",
                lines=[
                    Line(id="s1-01", scene=1, speaker="MARA", text="You're late.", direction="cold"),
                    Line(id="s1-03", scene=1, speaker="MARA", text="Pack.", direction="sharp"),
                ],
            ),
        ],
    )
    return Project(
        id="test-abc",
        title="Test",
        language="en",
        raw_script="",
        script=script,
        report=ParseReport(dialogue_lines=2, coverage=1.0),
    )


def make_lines() -> list[Line]:
    return [
        Line(id="s1-01", scene=1, speaker="MARA", text="You're late.", direction="cold, measured"),
        Line(id="s1-02", scene=1, speaker="MARA", text="Pack your bags.", direction=""),
        Line(id="s1-03", scene=1, speaker="MARA", text="Våådda? Host du g'redt?", direction="müde, fast weinend"),
    ]


def test_deterministic_tags_and_verbatim():
    lyrics = format_suno_lyrics(make_lines())
    assert "[cold, measured]\nYou're late." in lyrics
    # Directionless lines stay plain.
    assert "Pack your bags." in lyrics
    assert lyrics.count("Pack your bags.") == 1
    # Dialect is preserved byte-for-byte.
    assert "Våådda? Host du g'redt?" in lyrics
    assert "[müde, fast weinend]" in lyrics
    assert check_lyrics_verbatim(lyrics, make_lines()) == []


def test_deterministic_sanitizes_brackets_and_adds_baseline():
    character = Character(id="MARA", name="Mara", description="Cold [lead].")
    lines = [Line(id="s1-01", scene=1, speaker="MARA", text="Hi.", direction="[SFX: door] cold")]
    lyrics = format_suno_lyrics(lines, character, "SCENE 1")
    assert "[voice: Cold lead]" in lyrics
    assert "[SFX: door cold]" in lyrics
    assert lyrics.count("[") == lyrics.count("]")


def test_verbatim_check_flags_paraphrase():
    missing = check_lyrics_verbatim("[cold]\nYou are late.", make_lines())
    assert len(missing) == 3  # none of the three lines match


class StreamClient:
    """Fake LLM client speaking the chat_stream free-text protocol."""

    def __init__(self, chunks=None, error=None):
        self.chunks = chunks or []
        self.error = error
        self.calls = []

    def chat_stream(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        yield from self.chunks


GOOD_SHEET = (
    "[voice: cold controller]\n\n"
    "[cold, measured]\nYou're late.\n\n"
    "Pack your bags.\n\n"
    "[müde, fast weinend]\nVåådda? Host du g'redt?"
)


def test_llm_sheet_accepted_when_verbatim():
    client = StreamClient(chunks=[GOOD_SHEET])
    lyrics = llm_format_suno_lyrics(make_lines(), client=client)
    assert "[cold, measured]" in lyrics
    assert "Våådda? Host du g'redt?" in lyrics
    assert client.calls[0][1]["keep_alive"] == 0


def test_llm_sheet_rejected_on_paraphrase():
    from drama_studio.llm import LLMParseError

    client = StreamClient(chunks=["[cold]\nYou are late."])
    with pytest.raises(LLMParseError):
        llm_format_suno_lyrics(make_lines(), client=client)


def test_llm_client_error_propagates_as_llm_error():
    client = StreamClient(error=RuntimeError("offline"))
    with pytest.raises(LLMError):
        llm_format_suno_lyrics(make_lines(), client=client)


def test_resolve_falls_back_to_deterministic():
    lyrics, method = resolve_suno_lyrics(make_lines(), client=None)
    assert method == "deterministic"
    assert "You're late." in lyrics
    lyrics, method = resolve_suno_lyrics(
        make_lines(), client=StreamClient(error=RuntimeError("offline"))
    )
    assert method == "deterministic"
    assert "You're late." in lyrics


def test_build_stems_stores_lyric_sheet():
    project = make_project()
    stems = build_stems(project)
    mara = next(stem for stem in stems if stem.id == "s1-mara")
    assert mara.suno_lyrics is not None
    assert "[cold]\nYou're late." in mara.suno_lyrics
    assert "Pack." in mara.suno_lyrics


def test_suno_engine_prefers_lyric_sheet(tmp_path):
    from drama_studio.engines.suno import SunoEngine
    from drama_studio.models import StemRenderRequest

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
        text="You're late. ... Pack.",
        instruction="...",
        output_dir=str(tmp_path),
        suno_lyrics="[cold]\nYou're late.\n\n[sharp]\nPack.",
    )
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(request)
    assert captured["lyrics"] == "[cold]\nYou're late.\n\n[sharp]\nPack."

    legacy = request.model_copy(update={"suno_lyrics": None})
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(legacy)
    assert captured["lyrics"] == "You're late. ... Pack."


def test_suno_engine_reports_per_clip_download_errors(tmp_path):
    from drama_studio.engines.suno import SunoEngine
    from drama_studio.models import StemRenderRequest
    from drama_studio.suno import SunoError

    class FakeBridge:
        def generate(self, payload):
            return {"clips": [{"id": "clip-1"}, {"id": "clip-2"}]}

        def wait_for_clip(self, clip_id, timeout_s=180):
            return {"id": clip_id, "status": "complete"}

        def download(self, clip_id, output_dir, fmt="wav"):
            raise SunoError(f"Access denied: song {clip_id} is not in your library")

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
        candidates=2,
    )
    with pytest.raises(RuntimeError, match="download failed for clip clip-1"):
        engine.render_stem(request)


SCRIPT = """# LYRICS TEST

## SCENE 1

MARA (cold): You're late.
"""


def make_api_client(tmp_path, llm_client):
    app = create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects", llm_client=llm_client)
    return TestClient(app)


def _project_with_stems(client) -> tuple[dict, dict]:
    project = client.post(
        "/api/projects",
        json={"title": "Lyrics", "language": "en", "script_text": SCRIPT},
    ).json()
    built = client.post(f"/api/projects/{project['id']}/stems").json()
    stem = next(stem for stem in built["stems"] if stem["speaker"] == "MARA")
    assert "[cold]" in (stem["suno_lyrics"] or "")
    return project, stem


def test_api_suno_lyrics_deterministic(tmp_path):
    client = make_api_client(tmp_path, StreamClient(error=RuntimeError("offline")))
    project, stem = _project_with_stems(client)
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-lyrics",
        json={"method": "deterministic"},
    )
    assert response.status_code == 200
    updated = next(
        item
        for item in response.json()["stems"]
        if item["id"] == stem["id"]
    )
    assert "[cold]" in (updated["suno_lyrics"] or "")
    assert "You're late." in (updated["suno_lyrics"] or "")


def test_api_suno_lyrics_llm(tmp_path):
    client = make_api_client(tmp_path, StreamClient(chunks=["[ice cold]\nYou're late."]))
    project, stem = _project_with_stems(client)
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-lyrics",
        json={"method": "llm"},
    )
    assert response.status_code == 200
    updated = next(
        item
        for item in response.json()["stems"]
        if item["id"] == stem["id"]
    )
    assert updated["suno_lyrics"] == "[ice cold]\nYou're late."


def test_api_suno_lyrics_llm_paraphrase_is_502(tmp_path):
    client = make_api_client(tmp_path, StreamClient(chunks=["[cold]\nYou are late."]))
    project, stem = _project_with_stems(client)
    response = client.post(
        f"/api/projects/{project['id']}/stems/{stem['id']}/suno-lyrics",
        json={"method": "llm"},
    )
    assert response.status_code == 502


def test_api_suno_lyrics_validation(tmp_path):
    client = make_api_client(tmp_path, StreamClient(chunks=["x"]))
    project, stem = _project_with_stems(client)
    assert (
        client.post(
            f"/api/projects/{project['id']}/stems/{stem['id']}/suno-lyrics",
            json={"method": "karaoke"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/projects/{project['id']}/stems/nope/suno-lyrics",
            json={"method": "llm"},
        ).status_code
        == 404
    )


def test_api_stem_suno_title_tags_roundtrip(tmp_path):
    client = make_api_client(tmp_path, StreamClient(error=RuntimeError("offline")))
    project, stem = _project_with_stems(client)
    response = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"suno_title": "Rain Song", "suno_tags": "dark folk, slow"},
    )
    assert response.status_code == 200
    updated = next(
        item
        for item in response.json()["stems"]
        if item["id"] == stem["id"]
    )
    assert updated["suno_title"] == "Rain Song"
    assert updated["suno_tags"] == "dark folk, slow"
    cleared = client.patch(
        f"/api/projects/{project['id']}/stems/{stem['id']}",
        json={"suno_title": "", "suno_tags": "  "},
    )
    cleared_stem = next(
        item
        for item in cleared.json()["stems"]
        if item["id"] == stem["id"]
    )
    assert cleared_stem["suno_title"] is None
    assert cleared_stem["suno_tags"] is None


def test_suno_engine_uses_stem_title_tags(tmp_path):
    from drama_studio.engines.suno import SunoEngine
    from drama_studio.models import StemRenderRequest

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
        suno_title="Rain Song",
        suno_tags="dark folk, slow",
    )
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(request)
    assert captured["title"] == "Rain Song"
    assert captured["tags"] == "dark folk, slow"

    legacy = request.model_copy(update={"suno_title": None, "suno_tags": None})
    with pytest.raises(RuntimeError, match="no clips"):
        engine.render_stem(legacy)
    assert captured["title"] == "dialogue s1-mara"
    assert captured["tags"] == "audio drama radio play"
