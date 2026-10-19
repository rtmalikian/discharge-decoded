"""Readability guarantee: Flesch-Kincaid grade level as a measured gate.

Every generated care plan is scored. If it reads above ``target_grade``
(default 6.0), it is sent back through the gateway for simplification, up to
``max_passes`` times. The score, pass count, and whether the gate was met are
surfaced in the API response and the UI -- "plain language" as a measurement,
not a vibe.
"""

from __future__ import annotations

import re

from app import gateway
from app.discharge import prompts

_VOWELS = "aeiouy"


def count_syllables(word: str) -> int:
    word = word.lower().strip(".,!?;:'\"()[]")
    if not word:
        return 0
    count, prev_vowel = 0, False
    for ch in word:
        is_vowel = ch in _VOWELS
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _sentences(text: str) -> list[str]:
    # Split on terminal punctuation AND line breaks: care plans are
    # bullet lists where each line is its own thought.
    parts = re.split(r"[.!?]+|\n+", text or "")
    return [p.strip(" -•\t") for p in parts if p.strip(" -•\t")]


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text or "")


def flesch_kincaid_grade(text: str) -> float:
    """Return the Flesch-Kincaid grade level of ``text`` (lower = simpler)."""
    sents, words = _sentences(text), _words(text)
    if not sents or not words:
        return 0.0
    syllables = sum(count_syllables(w) for w in words)
    return round(
        0.39 * (len(words) / len(sents))
        + 11.8 * (syllables / len(words))
        - 15.59,
        2,
    )


def ensure_readability(plan_text: str, target_grade: float = 6.0,
                       max_passes: int = 3) -> dict:
    """Rewrite through the gateway until the plan meets the grade gate.

    Returns ``{"text", "grade", "passes", "meets_gate", "grade_history"}``.
    """
    current = plan_text
    grade = flesch_kincaid_grade(current)
    history = [grade]
    passes = 0
    while grade > target_grade and passes < max_passes:
        resp = gateway.dispatch_chat(
            prompts.build_rewrite_messages(current, target_grade))
        current = resp["choices"][0]["message"]["content"].strip() or current
        passes += 1
        grade = flesch_kincaid_grade(current)
        history.append(grade)
    return {
        "text": current,
        "grade": grade,
        "passes": passes,
        "meets_gate": grade <= target_grade,
        "grade_history": history,
    }
