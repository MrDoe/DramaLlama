from __future__ import annotations

from drama_studio.suno import _clip_ready


def test_clip_ready_accepts_complete_status():
    assert _clip_ready({"status": "complete", "audio_url": "/api/forbidden"}) is True


def test_clip_ready_accepts_progressive_audio():
    assert _clip_ready({"status": "streaming", "audio_url": "https://cdn/x.m4a"}) is True


def test_clip_ready_rejects_encrypted_placeholder_without_media():
    assert _clip_ready({"status": "streaming", "audio_url": "/api/forbidden"}) is False
    assert _clip_ready({"status": "error", "audio_url": "https://cdn/x.m4a"}) is False


def test_clip_ready_accepts_media_urls():
    assert _clip_ready({"status": "queued", "media_urls": [{"url": "https://cdn/x.m4a"}]}) is True
    assert _clip_ready({"status": "streaming", "media_urls": [{"url": "https://cdn/x", "delivery": "progressive"}]}) is True


def test_clip_ready_waits_for_progressive_media():
    streaming_webm = {
        "url": "https://audiopipe.suno.ai/?item_id=abc&format=webm&encoded=true",
        "content_type": "webm-opus",
        "delivery": "streaming",
        "encoding": "1.0.0",
    }
    # Streaming relay asset only: keep polling, the downloadable mix is not
    # there yet (downloading now fails with "download not available").
    assert _clip_ready({"status": "streaming", "media_urls": [streaming_webm]}) is False
    assert _clip_ready({"status": "queued", "media_urls": [{"url": "https://cdn/x"}]}) is False
    progressive_m4a = {
        "url": "https://cdn.suno.ai/1/clip/abc.m4a",
        "content_type": "m4a-opus",
        "delivery": "progressive",
        "encoding": "1.0.0",
    }
    assert _clip_ready({"status": "streaming", "media_urls": [progressive_m4a]}) is True


def test_library_requests_stay_under_the_suno_maximum(monkeypatch):
    """Asking the Suno API for more than 100 clips is an error, not a short page.

    The bridge forwarded the picker's 500, upstream answered "Limit exceeds
    maximum of 100", and the panel saw a 502 and never rendered a single clip —
    so a request for "lots" has to arrive as a legal request for 100.
    """
    from drama_studio.suno import SunoBridge

    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "clips": []}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        return _Response()

    monkeypatch.setattr("httpx.get", fake_get)
    bridge = SunoBridge(base_url="http://bridge.test")
    bridge.list_library(limit=500)
    assert seen["params"]["limit"] == 100
    bridge.list_library(limit=7)
    assert seen["params"]["limit"] == 7
    bridge.list_library()
    assert seen["params"]["limit"] == 100
