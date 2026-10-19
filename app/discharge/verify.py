"""Verified extraction: never present an unverified claim as fact.

The extractor outputs structured JSON. This module checks every extracted
claim against the source OCR text:

- medication whose name is not found in the source -> DROPPED (never shown),
  recorded in the report with a reason;
- medication whose dose digits are not found near the name -> KEPT but
  explicitly flagged ("dose not confirmed -- check your discharge papers");
- follow-up whose contact is not found in the source -> flagged;
- red flag whose content words mostly miss the source -> flagged.

Same verified-generation discipline as a clinical safety harness: the care
plan only asserts what the source supports.
"""

from __future__ import annotations

import re

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on",
    "you", "your", "if", "is", "are", "be", "have", "has", "over", "than",
    "more", "lasting", "call", "go",
}


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 3 and w not in _STOPWORDS]


def _window(src: str, needle: str, before: int = 60, after: int = 120) -> str:
    i = src.find(needle)
    if i < 0:
        return ""
    return src[max(0, i - before): i + len(needle) + after]


def verify_extraction(structured: dict, source_text: str) -> tuple[dict, dict]:
    """Return ``(verified_structured, report)``.

    ``report`` = ``{"checked": n, "dropped": [...], "flagged": [...]}`` where
    each dropped/flagged entry is ``{"type", "value", "reason"}``.
    """
    src = (source_text or "").lower()
    dropped: list[dict] = []
    flagged: list[dict] = []
    checked = 0

    meds = []
    for med in structured.get("medications", []):
        name = (med.get("name") or "").strip()
        dose = (med.get("dose") or "").strip()
        checked += 1
        if not name or name.lower() not in src:
            dropped.append({"type": "medication",
                            "value": f"{name} {dose}".strip(),
                            "reason": "medication name not found in source text"})
            continue
        dose_digits = re.sub(r"\D", " ", dose).split()
        window = _window(src, name.lower())
        dose_ok = (not dose_digits
                   or any(d in window for d in dose_digits))
        m = dict(med)
        if dose_ok:
            m["verification"] = "confirmed in source text"
        else:
            m["verification"] = ("dose not confirmed in source text -- "
                                 "check your discharge papers")
            flagged.append({"type": "medication_dose",
                            "value": f"{name} {dose}".strip(),
                            "reason": "dose digits not found near medication name"})
        meds.append(m)

    follow_ups = []
    for fu in structured.get("follow_ups", []):
        who = (fu.get("who") or "").strip()
        checked += 1
        tokens = [t for t in re.findall(r"[a-z]+", who.lower()) if len(t) > 2]
        if who and tokens and not any(t in src for t in tokens):
            flagged.append({"type": "follow_up", "value": who,
                            "reason": "contact not found in source text"})
            fu = {**fu, "verification": "not confirmed in source text"}
        else:
            fu = {**fu, "verification": "confirmed in source text"}
        follow_ups.append(fu)

    red_flags = []
    for flag in structured.get("red_flags", []):
        checked += 1
        words = _content_words(flag)
        hits = sum(1 for w in words if w in src)
        if words and hits / len(words) < 0.5:
            flagged.append({"type": "red_flag", "value": flag,
                            "reason": "wording not supported by source text"})
            continue  # unverified warning signs are dropped, not softened
        red_flags.append(flag)

    verified = dict(structured)
    verified["medications"] = meds
    verified["follow_ups"] = follow_ups
    verified["red_flags"] = red_flags
    report = {"checked": checked, "dropped": dropped, "flagged": flagged}
    return verified, report
