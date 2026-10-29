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
    assert _clip_ready({"status": "queued", "media_urls": [{"url": "https://cdn/x"}]}) is True
