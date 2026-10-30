from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import soundfile as sf

from ..audio import sha256_text, write_mono
from ..models import (
    StemRenderRequest,
    StemRenderResult,
    Take,
)
from ..suno import SunoBridge, SunoError
from .base import (
    BaseEngine,
    CancelCheck,
    EngineInfo,
    EngineNotAvailable,
    ProgressCallback,
    UnsupportedOperation,
)


def _pick_vocal_stem(clips: list[dict]) -> dict | None:
    for clip in clips:
        title = str(clip.get("title") or "").lower()
        if "vocal" in title:
            return clip
    # Fall back to the first stem clip if Suno did not label them.
    return clips[0] if clips else None


def _convert_to_wav(src: Path, dst: Path, sample_rate: int = 24000) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-ar", str(sample_rate), "-ac", "1",
            str(dst),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class SunoEngine(BaseEngine):
    """Suno as a dialogue TTS backend.

    Generates a clip whose lyrics are the stem text with an "audio drama radio
    play" style, optionally extracts the vocal stem, downloads it as m4a and
    converts to WAV so the existing Whisper aligner and mixer can consume it.
    """

    info = EngineInfo(
        name="suno",
        languages=frozenset({"en", "de"}),
        sample_rate=24000,
        supports_reference_clone=False,
        voice_mode="preset",
        description="Suno: audio-drama radio-play style dialogue, optional voice personas.",
        language_agnostic=True,
    )

    def __init__(self) -> None:
        super().__init__()
        self.bridge = SunoBridge()

    def load(self) -> None:
        if self._loaded:
            return
        try:
            health = self.bridge.health()
        except SunoError as exc:
            raise EngineNotAvailable(str(exc)) from exc
        if not health.get("extensionConnected"):
            raise EngineNotAvailable(
                "BetterSuno extension is not connected. Open suno.com with the "
                "BetterSuno extension loaded, then retry."
            )
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def design_reference(self, request) -> None:  # type: ignore[override]
        raise UnsupportedOperation(
            "Suno uses preset voices/personas; upload a voice sample and create a "
            "Suno persona, then set suno_persona_id on the character."
        )

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        self.load()
        output_dir = Path(request.output_dir)
        take_dir = output_dir / "audio" / "takes"
        take_dir.mkdir(parents=True, exist_ok=True)

        if cancelled and cancelled():
            raise RuntimeError("cancelled")

        if progress:
            progress(0.05, "submitting dialogue to Suno")

        payload = {
            "kind": "song",
            "lyrics": request.text,
            "title": f"dialogue {request.stem_id}",
            "tags": "audio drama radio play",
            "style_weight": 20,
            "mv": request.mv or "chirp-hawk",
            "instrumental": False,
        }
        if request.persona_id:
            payload["persona_id"] = request.persona_id

        try:
            result = self.bridge.generate(payload)
        except SunoError as exc:
            raise RuntimeError(str(exc)) from exc

        clips = result.get("clips") or []
        if not clips:
            raise RuntimeError("Suno returned no clips")

        if progress:
            progress(0.15, f"Suno queued {len(clips)} clip(s)")

        takes: list[Take] = []
        for index, clip in enumerate(clips[: request.candidates]):
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            clip_id = clip.get("id")
            if not clip_id:
                continue
            if progress:
                progress(
                    0.2 + 0.6 * index / max(1, len(clips)),
                    f"waiting for Suno clip {clip_id}",
                )
            try:
                finished = self.bridge.wait_for_clip(clip_id, timeout_s=180)
            except SunoError as exc:
                if progress:
                    progress(0, f"clip {clip_id} failed: {exc}")
                continue

            preview_url = finished.get("audio_url") or clip.get("audio_url")
            media = finished.get("media_urls") or clip.get("media_urls") or []
            if not preview_url and media:
                preview_url = media[0] if isinstance(media, list) else None

            # Extract vocal stem (preview after extraction).
            vocal_clip_id = None
            try:
                stems_result = self.bridge.make_stems(clip_id)
                stem_clips = stems_result.get("clips") or []
                vocal = _pick_vocal_stem(stem_clips)
                if vocal and vocal.get("id"):
                    vocal_clip_id = vocal.get("id")
                    # Refresh preview url from the stem clip when available.
                    try:
                        stem_clip_data = self.bridge.clip(vocal_clip_id)
                        stem_clip = stem_clip_data.get("clip") or {}
                        if stem_clip.get("audio_url"):
                            preview_url = stem_clip.get("audio_url")
                    except SunoError:
                        pass
            except SunoError:
                vocal_clip_id = None

            download_id = vocal_clip_id or clip_id
            try:
                m4a_path = self.bridge.download(download_id, take_dir, "m4a")
            except SunoError:
                # Fall back to wav if m4a is unavailable.
                try:
                    m4a_path = self.bridge.download(download_id, take_dir, "wav")
                except SunoError as exc:
                    if progress:
                        progress(0, f"download failed: {exc}")
                    continue

            wav_path = take_dir / (m4a_path.stem + ".wav")
            try:
                if m4a_path.suffix.lower() != ".wav":
                    _convert_to_wav(m4a_path, wav_path)
                else:
                    wav_path = m4a_path
            except Exception as exc:
                if progress:
                    progress(0, f"conversion failed: {exc}")
                continue

            try:
                info = sf.info(str(wav_path))
                duration = float(info.frames) / float(info.samplerate)
                sample_rate = int(info.samplerate)
            except Exception:
                duration = float(finished.get("duration") or clip.get("duration") or 0)
                sample_rate = self.info.sample_rate

            takes.append(
                Take(
                    id=f"suno-{request.stem_id}-{index}",
                    file=str(wav_path.relative_to(output_dir)),
                    engine=self.info.name,
                    duration_s=duration,
                    sample_rate=sample_rate,
                    seed=request.seed + index,
                    clip_id=clip_id,
                    preview_url=preview_url,
                    selected=False,
                )
            )

        if not takes:
            raise RuntimeError("Suno clips finished but no audio could be downloaded")

        best = takes[0]
        best.selected = True
        if progress:
            progress(1.0, "Suno stem complete")
        return StemRenderResult(
            stem_id=request.stem_id,
            engine=self.info.name,
            sample_rate=takes[0].sample_rate,
            takes=takes,
            selected_take_id=best.id,
            text_sha256=sha256_text(request.text),
        )
