"""Turn free-text stage directions into delivery styles CosyVoice 3 can act on.

CosyVoice reads German directions aloud and barely reacts to free-form English,
so a *local* language model picks one phrase from a small, measured table (or
"none", which means plain voice cloning). Script text never leaves the machine
for this: a hosted LLM configured in Settings is not used.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..llm import LLMClient, LLMError, OllamaClient, make_llm_client_for
from ..settings import resolve_llm

NO_STYLE = "none"
BATCH = 40


@dataclass(frozen=True)
class Style:
    meaning: str  # what the classifier is told
    instruction: str  # what CosyVoice is told for English
    #: For German: the same wish in Chinese, ending in "speak German". The
    #: English-project phrases give German lines an English accent. Chosen by ear
    #: (2026-11-27) over English and German wordings; the effect is milder.
    german: str


#: Chosen by measurement with the RL model and the fp32 speech tokenizer
#: (scripts/bench_cosyvoice.py, 48 German lines per phrase, 2026-11-27): the
#: comment gives lines said correctly and the median change against plain
#: cloning (which scored 98 %). English and Chinese phrases are mixed on
#: purpose; each is the one that worked. Keys are stored in projects, so phrases
#: may change without invalidating them.
STYLES: dict[str, Style] = {
    "soft": Style(  # 100 %, 1.34x slower, slightly lower pitch; level unchanged
        "quiet, soft, whispering, subdued",
        "Please say a sentence in a very soft voice.",
        "请用非常轻柔的声音说德语。",
    ),
    "loud": Style(  # 100 %, +6.7 dB, +3.1 semitones
        "loud, shouting, calling out",
        "请用尽可能大声地说一句话。",
        "请尽可能大声地说德语。",
    ),
    "slow": Style(  # 96 %, 1.85x slower
        "slow, deliberate, word by word",
        "请用尽可能慢地语速说一句话。",
        "请用尽可能慢的语速说德语。",
    ),
    "fast": Style(  # 94 %, 0.79x the duration
        "fast, rushed, hurried",
        "Please speak very fast.",
        "请用尽可能快的语速说德语。",
    ),
    "angry": Style(  # 98 %, +5.0 dB, +1.3 semitones
        "angry, furious, aggressive",
        "Speak very angrily and loudly.",
        "请非常生气地说德语。",
    ),
    "sad": Style(  # 94 %, 1.2x slower
        "sad, tearful, grieving",
        "请非常伤心地说一句话。",
        "请非常伤心地说德语。",
    ),
    "happy": Style(  # 98 %, +3.0 dB, +1.0 semitones
        "happy, cheerful, delighted",
        "Please speak very happily.",
        "请非常开心地说德语。",
    ),
}

_SYSTEM_PROMPT = """\
You label stage directions of an audio drama. Directions may be German or
English. For every numbered direction pick exactly one style, or "none".

Styles:
{styles}

Pick "none" when no style clearly fits, when the direction is about movement or
action rather than how the voice sounds, or when you are unsure. "none" is
always safe; a wrong style is not.
Answer with JSON: {{"assignments": [{{"id": 1, "style": "none"}}, ...]}}
"""


def instruction_for(style: str, language: str = "en") -> str | None:
    entry = STYLES.get(style)
    if entry is None:
        return None
    return entry.german if language == "de" else entry.instruction


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "style": {"type": "string", "enum": [*STYLES, NO_STYLE]},
                    },
                    "required": ["id", "style"],
                },
            }
        },
        "required": ["assignments"],
    }


def classify_directions(directions: list[str], client: LLMClient) -> dict[str, str]:
    """``{direction: style key}`` for every input. Raises LLMError on failure."""
    system = _SYSTEM_PROMPT.format(
        styles="\n".join(f"- {key}: {style.meaning}" for key, style in STYLES.items())
    )
    result = dict.fromkeys(directions, NO_STYLE)
    for start in range(0, len(directions), BATCH):
        batch = directions[start : start + BATCH]
        numbered = "\n".join(f"{index}. {text}" for index, text in enumerate(batch, 1))
        reply = client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": numbered},
            ],
            schema=_schema(),
            temperature=0.0,
            keep_alive=0,  # free the GPU before synthesis starts
        )
        assignments = reply.get("assignments") if isinstance(reply, dict) else None
        if not isinstance(assignments, list):
            raise LLMError("direction classification returned no assignments")
        for item in assignments:
            if not isinstance(item, dict):
                continue
            number, style = item.get("id"), item.get("style")
            if isinstance(number, int) and 1 <= number <= len(batch) and style in STYLES:
                result[batch[number - 1]] = style
    return result


def local_llm_client() -> LLMClient | None:
    """The configured LLM if it runs here, otherwise a local Ollama, else None."""
    settings, _ = resolve_llm()
    configured = make_llm_client_for(settings)
    if configured.provider == "local" and configured.available():
        return configured
    ollama = OllamaClient(base_url=config.OLLAMA_URL, model=config.LLM_MODEL, api_key="")
    if ollama.provider != "local":
        return None
    models, _ = ollama.probe_models()
    if not models:
        return None
    if ollama.model not in models:
        ollama.model = models[0]
    return ollama
