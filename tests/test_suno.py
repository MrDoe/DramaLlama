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
