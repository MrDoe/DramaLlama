"""Suno dialogue lyrics: bracket-tagged lyric sheets per speaker stem.

Suno does not take plain prose plus a separate instruction field. It reads
the lyrics themselves, where ``[square bracket]`` lines describe delivery
(style, emotion, intensity) and the remaining lines are sung/spoken verbatim.

This module owns both passes:

1. **Deterministic** (:func:`format_suno_lyrics`) — instant, always available.
   Each parsed :class:`Line` becomes one block: an optional ``[tag]`` line
   built from the line's ``direction`` / ``parenthetical`` / ``emotion``
   metadata, followed by the dialogue text **verbatim**. Lines without any
   direction stay plain. Blocks are joined with a blank line so Suno treats
   each beat as its own phrase.
2. **LLM** (:func:`llm_format_suno_lyrics`) — the "another LLM parsing output".
   The local model rewrites the same stem with richer Suno-style ``[...]``
   direction tags. Dialogue must stay byte-for-byte verbatim (dialect included:
   Bavarian spellings are never "corrected"); only bracket tags may be added.
   Output is validated by :func:`check_lyrics_verbatim` and rejected on any
   paraphrase, exactly like the script-parse validator.

:func:`resolve_suno_lyrics` tries the LLM pass first and falls back to the
deterministic sheet on any failure, so rendering never blocks on the model.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .models import Character, Line

if TYPE_CHECKING:  # duck-typed; only needs .chat_stream(...)
    from .llm import OllamaClient


SUNO_LYRICS_SYSTEM_PROMPT = """\
You format dialogue stems as Suno lyric sheets for an audio drama.
Return ONLY the lyric sheet as plain text — no JSON, no explanations.

Rules:
- Copy every numbered dialogue line VERBATIM, in order. Never paraphrase,
  merge, split, reorder, translate or "correct" spelling or dialect.
- Before a line, you may add ONE bracket tag line such as
  [cold, measured] or [shouting, furious] describing voice, style and emotion.
  Use the supplied direction hints; enrich them in a few words when useful.
- You may add ONE opening tag (e.g. [low room tone, tense]) for the scene mood.
- Never add dialogue, narration, speaker names or stage directions of your own.
- Never put spoken words inside [...] brackets.
"""


def _clean_tag_part(value: str) -> str:
    """Strip bracket characters so tags cannot nest or swallow dialogue."""
    return re.sub(r"[\[\]]", "", value).strip(" ,;:.").strip()


def line_tag(line: Line) -> str:
    """Build the ``[...]`` tag content for one line ("" when directionless)."""
    parts: list[str] = []
    for raw in (line.direction or "", line.parenthetical or "", line.emotion or ""):
        cleaned = _clean_tag_part(raw)
        if cleaned:
            parts.append(cleaned)
    if line.intensity and line.intensity >= 4 and parts:
        parts.append(f"intensity {line.intensity}/5")
    tag = ", ".join(parts)
    # Suno tags stay short; keep the full direction but cap runaway text.
    return tag[:160]


def format_suno_lyrics(
    lines: list[Line],
    character: Character | None = None,
    scene_heading: str = "",
) -> str:
    """Deterministic stem -> Suno lyric sheet (dialogue verbatim)."""
    blocks: list[str] = []
    if character and character.description.strip():
        mood = _clean_tag_part(character.description)
        if mood:
            blocks.append(f"[voice: {mood[:120]}]")
    elif scene_heading.strip():
        blocks.append(f"[{_clean_tag_part(scene_heading)[:120]}]")
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        tag = line_tag(line)
        blocks.append(f"[{tag}]\n{text}" if tag else text)
    return "\n\n".join(blocks)


def _normalize(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", " ", text)  # ignore bracket tags
    text = re.sub(r"\*\*|__|\*|_", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.lower().split())


def check_lyrics_verbatim(lyrics: str, lines: list[Line]) -> list[str]:
    """Return the dialogue lines missing from *lyrics* (empty = verbatim OK)."""
    haystack = _normalize(lyrics)
    missing: list[str] = []
    for line in lines:
        needle = _normalize(line.text)
        if needle and needle not in haystack:
            missing.append(line.text[:120])
    return missing


def llm_format_suno_lyrics(
    lines: list[Line],
    *,
    character: Character | None = None,
    scene_heading: str = "",
    client: Any,
    model: str | None = None,
) -> str:
    """Ask the local LLM for a richer Suno lyric sheet. Raises on paraphrase."""
    from .llm import LLMError, LLMParseError

    speakable = [line for line in lines if line.text.strip()]
    if not speakable:
        raise LLMParseError("stem has no dialogue lines to format")
    numbered = []
    for index, line in enumerate(speakable, 1):
        hints = "; ".join(
            part
            for part in (
                line.direction.strip(),
                (line.parenthetical or "").strip(),
                line.emotion.strip() if line.emotion else "",
            )
            if part
        )
        numbered.append(
            f"{index}. text: {line.text.strip()}\n   direction: {hints or '—'}"
        )
    baseline = (
        character.description.strip() if character and character.description else "—"
    )
    user_content = (
        f"Scene: {scene_heading.strip() or '—'}\n"
        f"Speaker: {character.name if character else speakable[0].speaker}\n"
        f"Voice baseline: {baseline}\n\n"
        "Dialogue lines (keep every one verbatim):\n" + "\n".join(numbered)
    )
    try:
        chunks = client.chat_stream(
            [
                {"role": "system", "content": SUNO_LYRICS_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            keep_alive=0,
            model=model,
        )
        lyrics = "".join(chunks).strip()
    except Exception as exc:
        raise LLMError(f"Suno lyrics LLM request failed: {exc}") from exc
    if not lyrics:
        raise LLMParseError("Suno lyrics LLM returned an empty sheet")
    missing = check_lyrics_verbatim(lyrics, speakable)
    if missing:
        raise LLMParseError(
            "Suno lyrics LLM dropped or rewrote "
            f"{len(missing)} line(s), e.g. {missing[0]!r}; refusing sheet"
        )
    return lyrics


def resolve_suno_lyrics(
    lines: list[Line],
    *,
    character: Character | None = None,
    scene_heading: str = "",
    client: Any | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Best-effort lyric sheet: ``(lyrics, "llm" | "deterministic")``.

    The LLM pass is attempted only when a client is supplied; any failure
    (offline model, paraphrase rejection, timeout) falls back to the
    deterministic sheet so Suno rendering never blocks.
    """
    deterministic = format_suno_lyrics(lines, character, scene_heading)
    if client is None:
        return deterministic, "deterministic"
    try:
        return (
            llm_format_suno_lyrics(
                lines,
                character=character,
                scene_heading=scene_heading,
                client=client,
                model=model,
            ),
            "llm",
        )
    except Exception:
        return deterministic, "deterministic"


SUNO_STYLE_SYSTEM_PROMPT = """\
You write Suno style tags and song titles for audio-drama dialogue stems.
Return ONLY JSON: {"title": "...", "tags": "..."} — no explanations.

Rules:
- tags: comma-separated Suno style tags, max 120 characters. Always start
  with "audio drama radio play", then 2-4 tags for mood, delivery and
  atmosphere inferred from the directions (e.g. "tense whisper",
  "cold interrogation"). Write tags in the dialogue language unless it is
  Bavarian dialect — then use English tags.
- title: short evocative title (speaker + scene mood), max 60 characters.
- Never copy dialogue lines into the tags or the title.
"""

SUNO_STYLE_BASE_TAGS = "audio drama radio play"


def suggest_suno_style_deterministic(
    lines: list[Line],
    character: Character | None = None,
    scene_heading: str = "",
) -> dict[str, str]:
    """Instant style prefill from direction metadata (no LLM needed)."""
    moods: list[str] = []
    for line in lines:
        for raw in (line.direction or "", line.parenthetical or "", line.emotion or ""):
            cleaned = _clean_tag_part(raw)
            if cleaned and cleaned.lower() not in {m.lower() for m in moods}:
                moods.append(cleaned)
    tags = SUNO_STYLE_BASE_TAGS
    if moods:
        extra = "; ".join(moods[:3])
        tags = f"{tags}, {extra}"[:160]
    speaker = character.name if character else (lines[0].speaker if lines else "")
    heading = _clean_tag_part(scene_heading)
    title = f"{speaker} — {heading}"[:60] if heading else speaker[:60]
    return {"title": title, "tags": tags}


def llm_suggest_suno_style(
    lines: list[Line],
    *,
    character: Character | None = None,
    scene_heading: str = "",
    client: Any,
    model: str | None = None,
) -> dict[str, str]:
    """Ask the local LLM for Suno title/tags. Raises on invalid output."""
    from .llm import LLMError, LLMParseError

    speakable = [line for line in lines if line.text.strip()]
    if not speakable:
        raise LLMParseError("stem has no dialogue lines to describe")
    numbered = []
    for index, line in enumerate(speakable, 1):
        hints = "; ".join(
            part
            for part in (
                line.direction.strip(),
                (line.parenthetical or "").strip(),
                line.emotion.strip() if line.emotion else "",
            )
            if part
        )
        numbered.append(
            f"{index}. \"{line.text.strip()}\" direction: {hints or '—'}"
        )
    baseline = (
        character.description.strip() if character and character.description else "—"
    )
    user_content = (
        f"Scene: {scene_heading.strip() or '—'}\n"
        f"Speaker: {character.name if character else speakable[0].speaker}\n"
        f"Voice baseline: {baseline}\n\n"
        "Dialogue (for mood only, do not copy):\n" + "\n".join(numbered)
    )
    try:
        payload = client.chat_json(
            [
                {"role": "system", "content": SUNO_STYLE_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.5,
            keep_alive=0,
            model=model,
        )
    except Exception as exc:
        raise LLMError(f"Suno style LLM request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMParseError("Suno style LLM did not return a JSON object")
    title = str(payload.get("title") or "").strip()[:60]
    tags = str(payload.get("tags") or "").strip()[:160]
    if not tags:
        raise LLMParseError("Suno style LLM returned empty tags")
    if SUNO_STYLE_BASE_TAGS not in tags.lower():
        tags = f"{SUNO_STYLE_BASE_TAGS}, {tags}"[:160]
    return {"title": title, "tags": tags}
