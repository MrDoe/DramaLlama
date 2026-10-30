from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import drama_studio.api as api_module
from drama_studio.api import create_app
from drama_studio.engines.suno import (
    CAPTCHA_MARKER,
    CaptchaRequiredError,
    SunoEngine,
    is_captcha_error,
)
from drama_studio.models import StemRenderRequest
from drama_studio.suno import SunoBridge, SunoError

TURNSTILE_MESSAGE = (
    "Suno requires a Cloudflare Turnstile check that could not be solved "
    'automatically (Suno requires a human Turnstile check — tick the '
    '"Verify you are human" checkbox in the Suno tab, then retry)'
)


class FakeBridge:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.captcha_requests = 0

    def request_captcha(self):
        self.captcha_requests += 1
        if self.error:
            raise self.error
        return {"ok": True}

    def health(self):
        return {"ok": True, "extensionConnected": True}

    def generate(self, payload):
        if self.error:
            raise self.error
        return {"clips": []}


# --------------------------------------------------------------- classifier


def test_classifier_matches_real_message():
    assert is_captcha_error(TURNSTILE_MESSAGE)
    assert is_captcha_error(f"{CAPTCHA_MARKER} whatever")
    assert is_captcha_error("Suno requires hCaptcha")
    assert not is_captcha_error("Suno bridge not reachable at http://127.0.0.1:9430")
    assert not is_captcha_error("Suno returned no clips")


def test_captcha_error_carries_marker():
    error = CaptchaRequiredError(TURNSTILE_MESSAGE)
    assert str(error).startswith(CAPTCHA_MARKER)
    assert error.detail == TURNSTILE_MESSAGE
    assert is_captcha_error(str(error))


def test_engine_raises_captcha_error(tmp_path, monkeypatch):
    engine = SunoEngine()
    engine.bridge = FakeBridge(error=SunoError(TURNSTILE_MESSAGE))  # type: ignore[assignment]
    engine._loaded = True
    request = StemRenderRequest(
        project_id="p",
        stem_id="s1",
        speaker="MARA",
        text="You're late.",
        instruction="",
        output_dir=str(tmp_path),
    )
    with pytest.raises(CaptchaRequiredError) as caught:
        engine.render_stem(request)
    assert is_captcha_error(str(caught.value))


def test_engine_keeps_other_failures_as_runtime_error(tmp_path):
    engine = SunoEngine()
    engine.bridge = FakeBridge(error=SunoError("Suno bridge HTTP 500"))  # type: ignore[assignment]
    engine._loaded = True
    request = StemRenderRequest(
        project_id="p",
        stem_id="s1",
        speaker="MARA",
        text="You're late.",
        instruction="",
        output_dir=str(tmp_path),
    )
    with pytest.raises(RuntimeError) as caught:
        engine.render_stem(request)
    assert not isinstance(caught.value, CaptchaRequiredError)


# ------------------------------------------------------------------- bridge


def test_bridge_request_captcha_ok(monkeypatch):
    seen: dict = {}

    class Response:
        status_code = 200
        content = b'{"ok": true}'

        def json(self):
            return {"ok": True}

    def fake_post(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("drama_studio.suno.httpx.post", fake_post)
    assert SunoBridge("http://bridge:9430").request_captcha() == {"ok": True}
    assert seen["url"] == "http://bridge:9430/captcha"
    assert seen["timeout"] == 150


def test_bridge_request_captcha_error(monkeypatch):
    class Response:
        status_code = 502
        content = b'{"ok": false, "error": "x"}'

        def json(self):
            return {"ok": False, "error": "widget timed out"}

    monkeypatch.setattr(
        "drama_studio.suno.httpx.post", lambda url, timeout=None: Response()
    )
    with pytest.raises(SunoError, match="widget timed out"):
        SunoBridge("http://bridge:9430").request_captcha()


# ---------------------------------------------------------------------- api


def make_api_client(tmp_path, monkeypatch, bridge):
    monkeypatch.setattr(api_module, "SunoBridge", lambda *a, **k: bridge)
    app = create_app(tmp_path / "jobs.sqlite3", tmp_path / "projects")
    return TestClient(app)


def test_api_suno_captcha_ok(tmp_path, monkeypatch):
    bridge = FakeBridge()
    client = make_api_client(tmp_path, monkeypatch, bridge)
    response = client.post("/api/suno/captcha")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert bridge.captcha_requests == 1


def test_api_suno_captcha_error_is_502(tmp_path, monkeypatch):
    client = make_api_client(
        tmp_path, monkeypatch, FakeBridge(error=SunoError("no Suno tab"))
    )
    response = client.post("/api/suno/captcha")
    assert response.status_code == 502
    assert "no Suno tab" in response.json()["detail"]


class PayloadBridge:
    def __init__(self):
        self.payload = None

    def generate(self, payload):
        self.payload = payload
        return {"clips": []}


def _request(tmp_path):
    return StemRenderRequest(
        project_id="p",
        stem_id="s1",
        speaker="MARA",
        text="You're late.",
        instruction="",
        output_dir=str(tmp_path),
    )


def test_style_weight_omitted_by_default(tmp_path):
    engine = SunoEngine()
    bridge = PayloadBridge()
    engine.bridge = bridge  # type: ignore[assignment]
    engine._loaded = True
    with pytest.raises(RuntimeError):
        engine.render_stem(_request(tmp_path))
    assert "style_weight" not in bridge.payload


def test_style_weight_sent_when_set(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_module.write_suno({"plan": "pro"})
    engine = SunoEngine()
    bridge = PayloadBridge()
    engine.bridge = bridge  # type: ignore[assignment]
    engine._loaded = True
    request = _request(tmp_path).model_copy(update={"suno_style_weight": 45})
    with pytest.raises(RuntimeError):
        engine.render_stem(request)
    assert bridge.payload["style_weight"] == 45


# ------------------------------------------------------------- suno plan


def test_suno_plan_defaults_to_free(tmp_path, monkeypatch):
    from drama_studio.settings import resolve_suno

    monkeypatch.setenv("DRAMA_LLAMA_DATA", str(tmp_path))
    import drama_studio.settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.delenv("DRAMA_LLAMA_SUNO_PLAN", raising=False)
    settings, sources = resolve_suno()
    assert settings.plan == "free"
    assert sources["plan"] == "default"


def test_suno_plan_roundtrip(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")
    settings_module.write_suno({"plan": "pro"})
    settings, sources = settings_module.resolve_suno()
    assert settings.plan == "pro"
    assert sources["plan"] == "file"
    with pytest.raises(ValueError):
        settings_module.write_suno({"plan": "platinum"})


def test_paid_features_only_on_pro(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")

    request = _request(tmp_path).model_copy(
        update={
            "suno_style_weight": 40,
            "suno_weirdness": 30,
            "suno_audio_weight": 25,
            "persona_id": "p1",
            "persona_model": "voice_persona",
        }
    )

    free_engine = SunoEngine()
    free_bridge = PayloadBridge()
    free_engine.bridge = free_bridge  # type: ignore[assignment]
    free_engine._loaded = True
    with pytest.raises(RuntimeError):
        free_engine.render_stem(request)
    for key in ("style_weight", "weirdness", "audio_weight", "persona_id", "persona_model"):
        assert key not in free_bridge.payload

    settings_module.write_suno({"plan": "pro"})
    pro_engine = SunoEngine()
    pro_bridge = PayloadBridge()
    pro_engine.bridge = pro_bridge  # type: ignore[assignment]
    pro_engine._loaded = True
    with pytest.raises(RuntimeError):
        pro_engine.render_stem(request)
    assert pro_bridge.payload["style_weight"] == 40
    assert pro_bridge.payload["weirdness"] == 30
    assert pro_bridge.payload["audio_weight"] == 25
    assert pro_bridge.payload["persona_id"] == "p1"


def test_api_config_exposes_plan(tmp_path, monkeypatch):
    import drama_studio.settings as settings_module

    monkeypatch.setattr(settings_module, "SETTINGS_PATH", tmp_path / "settings.json")
    client = make_api_client(tmp_path, monkeypatch, FakeBridge())
    body = client.get("/api/config").json()
    assert body["suno"]["plan"] == "free"
    updated = client.put("/api/config", json={"suno": {"plan": "pro"}}).json()
    assert updated["suno"]["plan"] == "pro"
    assert client.put("/api/config", json={"suno": {"plan": "x"}}).status_code == 400


def test_bridge_exposes_make_stems():
    # Regression: make_stems was accidentally nested inside _clip_ready,
    # so stem extraction vanished and renders crashed mid-flight.
    assert callable(getattr(SunoBridge, "make_stems", None))


def test_estimate_alignment_is_proportional():
    from drama_studio.handlers import _estimate_alignment
    from drama_studio.models import Line

    lines = [
        Line(id="s1-01", scene=1, speaker="A", text="one two three"),
        Line(id="s1-02", scene=1, speaker="A", text="four"),
    ]
    result = _estimate_alignment("s1-a", 100.0, lines)
    assert result.aligner == "estimate"
    assert result.lines[0].start_s == 0
    assert result.lines[0].end_s == 75.0
    assert result.lines[1].start_s == 75.0
    assert all(line.fallback for line in result.lines)
    assert all(line.confidence == 0.05 for line in result.lines)


def test_take_defaults_to_full_mix():
    from drama_studio.models import Take

    take = Take(
        id="t", file="x.wav", engine="suno", duration_s=10.0, sample_rate=24000, seed=1
    )
    assert take.vocal_isolated is False
