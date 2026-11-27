"""Did a TTS engine actually say the line? Cheap checks for rendered pieces."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import numpy as np

from .audio import rms

#: Calibrated on the CosyVoice 3 German spike: garbled, dropped or leaked-
#: instruction renderings scored at most 0.91, faithful ones 0.93 and up.
PASS_SCORE = 0.92

_SILENCE_RMS = 0.003  # about -50 dBFS
_MIN_DURATION_S = 0.12

_TAGS = re.compile(r"\[[^\]]*\]|<[^>]*>")
_UMLAUTS = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


def fold_speech(text: str) -> str:
    """Letters and digits only, so spacing and punctuation never count."""
    folded = _TAGS.sub(" ", text).casefold()
    for source, target in _UMLAUTS:
        folded = folded.replace(source, target)
    return "".join(re.findall(r"[^\W_]+", folded))


def match_score(expected: str, heard: str) -> float:
    """0..1 similarity between the scripted line and a transcript of the audio."""
    wanted = fold_speech(expected)
    if not wanted:
        return 1.0
    got = fold_speech(heard)
    if not re.search(r"\d", wanted):
        return SequenceMatcher(None, wanted, got, autojunk=False).ratio()
    # Digits may be transcribed as words ("7:45" -> "sieben fünfundvierzig"), so only
    # ask whether everything around them was said.
    wanted = re.sub(r"\d+", "", wanted)
    if not wanted:
        return 1.0 if got else 0.0
    matcher = SequenceMatcher(None, wanted, got, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(wanted)


def is_silent(samples: np.ndarray, sample_rate: int) -> bool:
    if len(samples) < _MIN_DURATION_S * sample_rate:
        return True
    return rms(samples) < _SILENCE_RMS
