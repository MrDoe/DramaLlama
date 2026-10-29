from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .config import LLM_KEEP_ALIVE, LLM_MODEL, OLLAMA_URL
from .models import (
    Character,
    Language,
    Line,
    ParseReport,
    ParseResult,
    ParseWarning,
    Scene,
    Script,
)
from .parser import clean_dialogue, normalize_speaker


class LLMError(RuntimeError):
    pass


class LLMParseError(LLMError):
    pass


class LLMLine(BaseModel):
    speaker: str
    text: str
    direction: str = ""
    confidence: float = 1.0


class LLMScene(BaseModel):
    heading: str = ""
    setting: str = ""
    stage_directions: list[str] = Field(default_factory=list)
    lines: list[LLMLine] = Field(default_factory=list)


class LLMCharacter(BaseModel):
    id: str = ""
    name: str
    description: str = ""


class LLMScript(BaseModel):
    title: str = ""
    language: Language = "en"
    characters: list[LLMCharacter] = Field(default_factory=list)
    scenes: list[LLMScene] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You are a precise screenplay parser for an audio-drama production tool.
Return only JSON matching the supplied schema.

Rules:
- Copy every spoken line verbatim. Never paraphrase, merge, split or invent dialogue.
- Keep inline vocal tags such as <sigh> or <laugh> in the spoken text.
- Put performance directions (parentheticals) in `direction`, not in `text`.
- Put [SFX: ...], [AMBIENCE: ...] and [PAUSE ...] lines in `stage_directions`.
- Attribute speakers from labels and context. Use "UNKNOWN" when genuinely unclear.
- Write short character descriptions inferred only from the script.
- Preserve scene order and line order.
"""


def _json_schema() -> dict[str, Any]:
    return LLMScript.model_json_schema()


def _normalize(text: str) -> str:
    text = re.sub(r"\*\*|__|\*|_", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.lower().split())


class OllamaClient:
    def __init__(
        self,
        base_url: str = OLLAMA_URL,
        model: str = LLM_MODEL,
        timeout_s: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        keep_alive: str | int = LLM_KEEP_ALIVE,
        model: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
            "keep_alive": keep_alive,
        }
        if schema is not None:
            payload["format"] = schema

        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        content = (body.get("message") or {}).get("content", "")
        if not content:
            raise LLMError("Ollama returned an empty message")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama returned invalid JSON: {exc}") from exc

    def unload(self, model: str | None = None) -> None:
        try:
            httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": model or self.model, "keep_alive": 0},
                timeout=30,
            )
        except Exception:
            pass

    def available(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=3)
            response.raise_for_status()
            return True
        except Exception:
            return False


def _convert(payload: dict[str, Any], raw: str, language: str) -> ParseResult:
    try:
        parsed = LLMScript.model_validate(payload)
    except Exception as exc:
        raise LLMParseError(f"LLM JSON did not match the script schema: {exc}") from exc

    characters: dict[str, Character] = {}
    for item in parsed.characters:
        key = normalize_speaker(item.id or item.name)
        if not key:
            continue
        characters[key] = Character(
            id=key,
            name=item.name.strip() or key.title(),
            description=item.description.strip(),
            engine="chatterbox" if language == "de" else "breeze",
        )

    scenes: list[Scene] = []
    total_lines = 0
    total_stage = 0
    matched = 0
    raw_norm = _normalize(raw)
    warnings: list[ParseWarning] = []

    for scene_index, llm_scene in enumerate(parsed.scenes, 1):
        scene = Scene(
            id=scene_index,
            heading=llm_scene.heading.strip() or f"Scene {scene_index}",
            setting=llm_scene.setting.strip(),
            stage_directions=[item.strip() for item in llm_scene.stage_directions if item.strip()],
        )
        total_stage += len(scene.stage_directions)
        for line_index, llm_line in enumerate(llm_scene.lines, 1):
            speaker = normalize_speaker(llm_line.speaker) or "UNKNOWN"
            if speaker not in characters:
                characters[speaker] = Character(
                    id=speaker,
                    name=speaker.title(),
                    engine="chatterbox" if language == "de" else "breeze",
                )
            text = clean_dialogue(llm_line.text)
            if not text:
                continue
            line = Line(
                id=f"s{scene_index}-{line_index:02d}",
                scene=scene_index,
                speaker=speaker,
                text=text,
                direction=llm_line.direction.strip(),
            )
            scene.lines.append(line)
            total_lines += 1
            if _normalize(text) and _normalize(text) in raw_norm:
                matched += 1
            else:
                warnings.append(
                    ParseWarning(
                        source_line=0,
                        message="LLM line is not a verbatim substring of the source",
                        text=text[:120],
                    )
                )
            if llm_line.confidence < 0.6:
                warnings.append(
                    ParseWarning(
                        source_line=0,
                        message=f"low speaker confidence ({llm_line.confidence:.2f})",
                        text=f"{speaker}: {text[:80]}",
                    )
                )
        scenes.append(scene)

    if total_lines == 0:
        raise LLMParseError("LLM returned no dialogue lines")

    coverage = matched / total_lines
    if coverage < 0.85:
        raise LLMParseError(
            f"LLM dialogue coverage is only {coverage:.0%}; refusing to replace the source text"
        )

    source_lines = len([line for line in raw.splitlines() if line.strip()])
    words = sum(len(line.text.split()) for scene in scenes for line in scene.lines)
    report = ParseReport(
        method="llm",
        source_lines=source_lines,
        scene_headings=len(scenes),
        dialogue_lines=total_lines,
        stage_directions=total_stage,
        coverage=round(coverage, 3),
        runtime_estimate_s=round(words / 2.6, 1),
        warnings=warnings,
    )
    script = Script(
        title=parsed.title.strip() or "Untitled drama",
        language=language,  # type: ignore[arg-type]
        characters=characters,
        scenes=scenes,
    )
    return ParseResult(script=script, report=report)


def llm_parse_script(
    raw: str,
    language: str = "en",
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
) -> ParseResult:
    client = client or OllamaClient(model=model or LLM_MODEL)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Language: {'German' if language == 'de' else 'English'}\n"
                "Parse this script into the JSON schema:\n\n"
                f"{raw}"
            ),
        },
    ]
    payload = client.chat_json(
        messages,
        schema=_json_schema(),
        temperature=0.2,
        keep_alive=0,
        model=model,
    )
    return _convert(payload, raw, language)
