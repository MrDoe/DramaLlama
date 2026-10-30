from __future__ import annotations

import io
import struct
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
import pytest

from drama_studio.engines.breeze_de import BreezeDeEngine


def _pcm_bytes(n: int = 1000) -> bytes:
    samples = [int(0.1 * 32767)] * n
    return struct.pack(f"<{n}h", *samples)


def _fake_response(content: bytes, status: int = 200, headers: dict | None = None):
    r = httpx.Response(status, content=content, headers=headers or {})
    r.request = httpx.Request("POST", "http://x")
    return r


def test_load_and_design_reference(monkeypatch, tmp_path):
    def fake_get(url, *a, **k):
        return _fake_response(b"", 200, {"content-type": "application/json"}) if False else httpx.Response(
            200, json={"status": "ok", "sample_rate": 24000}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    posted = {}

    def fake_post(url, *a, **k):
        posted["data"] = k.get("data")
        posted["files"] = k.get("files")
        return httpx.Response(200, content=_pcm_bytes(), request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    engine = BreezeDeEngine()
    engine.base_url = "http://127.0.0.1:8080"
    engine.load()
    assert engine.loaded

    out = tmp_path / "ref.wav"
    req = None
    from drama_studio.models import ReferenceRequest

    ref_req = ReferenceRequest(
        character_id="anna",
        text="Guten Tag, wie geht es dir.",
        instruction="klar und ruhig",
        output_path=str(out),
        cfg_scale=4.0,
        seed=7,
    )
    result = engine.design_reference(ref_req)
    assert Path(result.file).exists()
    assert result.sample_rate == 24000
    assert result.duration_s > 0


def test_render_stem_requires_no_crash(monkeypatch, tmp_path):
    def fake_get(url, *a, **k):
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    def fake_post(url, *a, **k):
        return httpx.Response(200, content=_pcm_bytes(2000), request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    engine = BreezeDeEngine()
    engine.base_url = "http://127.0.0.1:8080"
    engine.load()

    proj = tmp_path / "proj"
    (proj / "audio" / "takes").mkdir(parents=True)
    ref = tmp_path / "ref.wav"
    sf.write(str(ref), np.zeros(24000, dtype=np.float32), 24000, subtype="PCM_24")

    from drama_studio.models import StemRenderRequest

    req = StemRenderRequest(
        project_id="p",
        stem_id="s1-anna",
        speaker="ANNA",
        text="Hallo Welt.",
        instruction="ruhig",
        output_dir=str(proj),
        candidates=2,
        seed=42,
        ref_audio_path=str(ref),
        ref_text="Hallo Welt.",
        cfg_scale=2.5,
    )
    result = engine.render_stem(req)
    assert result.engine == "breeze-de"
    assert len(result.takes) == 2
    assert result.selected_take_id is not None
    for t in result.takes:
        assert (proj / t.file).exists()
