from __future__ import annotations

import numpy as np
import pytest

from drama_studio.speech_check import PASS_SCORE, fold_speech, is_silent, match_score


def test_fold_speech_ignores_case_punctuation_umlauts_and_tags():
    assert fold_speech("Er grüßt. [laughter] <sigh> Und!") == "ergruesstund"
    assert fold_speech("Straße zu Hause") == "strassezuhause"


@pytest.mark.parametrize(
    ("expected", "heard"),
    [
        ("Er kommt. Und er kommt spät.", "er kommt und er kommt spät"),
        ("Wann kommt der Bus?", "Wann kommt der bus"),
        ("Er ist nicht zu Hause.", "Er ist nicht zuhause."),
        ("Also gut. Noch einmal von vorn.", "Also gut, nochmal von vorn."),
    ],
)
def test_faithful_renderings_pass(expected, heard):
    assert match_score(expected, heard) >= PASS_SCORE


@pytest.mark.parametrize(
    ("expected", "heard"),
    [
        # The shapes of real failures: hallucination, garble, spoken
        # instruction, slurred words, a dropped word, silence.
        ("Der Bote hat uns heute früh geschrieben.", "Untertitelung. BR 2018"),
        ("Ich habe ein Ticket für den Nachtzug!", "Die Karbin Ticker fein in Nachtzug!"),
        ("Er kommt. Und er kommt spät.", "Also flüsternd, er kommt spät."),
        ("Er kommt. Und er kommt spät.", "Erkumt, unerkumpspät."),
        ("Jonas? Das … das ist viel zu spät.", "Das ist viel zu spät."),
        ("Bis morgen in der Werkstatt!", ""),
    ],
)
def test_garbled_dropped_or_leaked_renderings_fail(expected, heard):
    assert match_score(expected, heard) < PASS_SCORE


def test_spoken_numbers_do_not_fail_a_line_written_with_digits():
    assert (
        match_score("Der Zug fährt um 7:45 Uhr ab.", "Der Zug fährt um sieben, fünfundvierzig Uhr ab.")
        >= PASS_SCORE
    )
    assert match_score("Der Zug fährt um 7:45 Uhr ab.", "Guten Morgen zusammen.") < PASS_SCORE


def test_text_without_any_speakable_character_always_passes():
    assert match_score("…", "") == 1.0


def test_is_silent_detects_empty_and_near_silent_audio():
    rate = 24000
    tone = 0.2 * np.sin(np.linspace(0, 2000, rate, dtype=np.float32))
    assert not is_silent(tone, rate)
    assert is_silent(np.zeros(rate, dtype=np.float32), rate)
    assert is_silent(tone * 1e-3, rate)
    assert is_silent(tone[: rate // 20], rate)
