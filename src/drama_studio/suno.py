from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import httpx

from .config import SUNO_BRIDGE_URL


class SunoError(RuntimeError):
    pass


class SunoBridge:
    """HTTP client for the local BetterSuno sidecar in `suno-bridge/`."""

    def __init__(self, base_url: str = SUNO_BRIDGE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=3)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise SunoError(
                f"Suno bridge not reachable at {self.base_url}; "
                "start it with `node suno-bridge/server.mjs`"
            ) from exc

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}/generate", json=payload, timeout=180
            )
        except Exception as exc:
            raise SunoError(f"Suno bridge request failed: {exc}") from exc
        data = response.json() if response.content else {}
        if response.status_code >= 400 or not data.get("ok"):
            raise SunoError(data.get("error") or f"Suno bridge HTTP {response.status_code}")
        return data

    def clip(self, clip_id: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self.base_url}/clip/{clip_id}", timeout=30
            )
        except Exception as exc:
            raise SunoError(f"Could not fetch Suno clip: {exc}") from exc
        data = response.json() if response.content else {}
        if response.status_code >= 400 or not data.get("ok"):
            raise SunoError(data.get("error") or f"Suno bridge HTTP {response.status_code}")
        return data

    def list_personas(self) -> dict[str, Any]:
        """Own (all pages) + followed + loved Suno personas via the bridge."""
        try:
            response = httpx.get(f"{self.base_url}/personas", timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise SunoError(f"Could not list Suno personas: {exc}") from exc
        if not data.get("ok"):
            raise SunoError(data.get("error") or "Suno bridge returned no personas")
        return data

    def get_persona(self, persona_id: str) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/personas/{persona_id}", timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise SunoError(f"Could not fetch Suno persona: {exc}") from exc
        if not data.get("ok"):
            raise SunoError(data.get("error") or "Suno bridge returned no persona")
        return data

    def request_captcha(self) -> dict[str, Any]:
        """Ask the BetterSuno extension to mint a Turnstile token now.

        Forces the extension to reuse Suno's own widget or render its visible
        checkbox, instead of waiting for a generation to fail. Returns
        ``{"ok": True}`` — the token itself never leaves the bridge.
        """
        try:
            response = httpx.post(f"{self.base_url}/captcha", timeout=150)
        except Exception as exc:
            raise SunoError(f"Suno captcha request failed: {exc}") from exc
        data = response.json() if response.content else {}
        if response.status_code >= 400 or not data.get("ok"):
            raise SunoError(
                data.get("error") or f"Suno bridge HTTP {response.status_code}"
            )
        return {"ok": True}

    def download(
        self, clip_id: str, output_dir: str | Path, fmt: str = "wav"
    ) -> Path:
        try:
            response = httpx.post(
                f"{self.base_url}/download",
                json={"clip_id": clip_id, "output_dir": str(output_dir), "format": fmt},
                timeout=240,
            )
        except Exception as exc:
            raise SunoError(f"Suno download failed: {exc}") from exc
        data = response.json() if response.content else {}
        if response.status_code >= 400 or not data.get("ok"):
            raise SunoError(data.get("error") or f"Suno bridge HTTP {response.status_code}")
        text = data.get("text", "")
        match = re.search(r"Downloaded to (.+?) \(", text)
        if match:
            path = Path(match.group(1).strip())
            if path.exists():
                return path
        # Fallback: newest audio file in the requested directory.
        candidates = sorted(
            Path(output_dir).glob(f"*{clip_id[-4:]}.*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
        raise SunoError(f"downloaded file not found (bridge said: {text[:200]})")

    def wait_for_clip(
        self, clip_id: str, timeout_s: float = 180, interval_s: float = 3
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            data = self.clip(clip_id)
            last = data.get("clip") or {}
            if isinstance(last, dict) and _clip_ready(last):
                return last
            time.sleep(interval_s)
        raise SunoError(
            f"Suno clip {clip_id} did not finish within {timeout_s:.0f}s "
            f"(last status: {last.get('status') if isinstance(last, dict) else 'unknown'})"
        )

    def make_stems(self, clip_id: str) -> dict[str, Any]:
        """Extract vocals/instrumental stems for a finished clip."""
        try:
            response = httpx.post(
                f"{self.base_url}/stems",
                json={"clip_id": clip_id},
                timeout=120,
            )
        except Exception as exc:
            raise SunoError(f"Suno stem extraction failed: {exc}") from exc
        data = response.json() if response.content else {}
        if response.status_code >= 400 or not data.get("ok"):
            raise SunoError(
                data.get("error") or f"Suno bridge HTTP {response.status_code}"
            )
        return data


def _clip_ready(clip: dict[str, Any]) -> bool:
    status = str(clip.get("status") or "").lower()
    if status in {"error", "failed"}:
        return False
    audio = str(clip.get("audio_url") or "")
    media = clip.get("media_urls") or []
    return status in {"complete", "completed"} or (
        bool(audio) and "/forbidden" not in audio
    ) or bool(media)
