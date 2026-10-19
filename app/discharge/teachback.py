"""Automated teach-back loop.

Teach-back ("explain it back to me") is the evidence-based gold standard for
discharge comprehension -- it roughly halved heart-failure readmissions in
trials -- but it needs 1:1 nurse time and rarely happens well. This module
automates it:

1. ``critical_items(structured)`` turns the verified extraction into a short
   list of checkable items: every medication (name/dose/frequency), every
   red flag, every follow-up.
2. The patient explains each item back in their own words.
3. ``check_answer(item, answer)`` goes through the gateway, which checks the
   answer's coverage against the item's key points and coaches on anything
   missed.
4. The loop continues until every item is covered or the patient opts out.
5. ``summarize`` reports completion -- a first-class metric, logged
   server-side and returned to the caller.
"""

from __future__ import annotations

import re

from app import gateway
from app.discharge import prompts


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1]


def critical_items(structured: dict) -> list[dict]:
    """Verified extraction -> teach-back items with key points to cover."""
    items: list[dict] = []
    for i, med in enumerate(structured.get("medications", [])):
        name = med.get("name", "")
        dose = med.get("dose", "")
        freq = med.get("frequency", "")
        dose_digits = " ".join(re.sub(r"\D", " ", dose).split())
        points = [p for p in [name.lower(), dose_digits, freq.lower()] if p]
        items.append({
            "id": f"med-{i}",
            "kind": "medication",
            "label": f"{name} {dose}".strip(),
            "prompt": f"In your own words, how do you take {name}?",
            "key_points": points,
        })
    for i, flag in enumerate(structured.get("red_flags", [])):
        words = [w for w in re.findall(r"[a-z]+", flag.lower())
                 if len(w) > 3][:4]
        items.append({
            "id": f"flag-{i}",
            "kind": "red_flag",
            "label": flag,
            "prompt": ("What warning signs mean you should call your doctor "
                       "or go to the emergency room?"),
            "key_points": words,
        })
    for i, fu in enumerate(structured.get("follow_ups", [])):
        who, when = fu.get("who", ""), fu.get("when", "")
        points = [p for p in [who.lower(), when.lower()] if p]
        items.append({
            "id": f"fu-{i}",
            "kind": "follow_up",
            "label": f"{who} {when}".strip(),
            "prompt": "Who do you follow up with, and when?",
            "key_points": points,
        })
    return items


def check_answer(item: dict, answer: str) -> dict:
    """Gateway-checked coverage of one teach-back answer.

    Returns ``{"covered", "coverage", "hit_points", "missed_points", "coach"}``.
    """
    resp = gateway.dispatch_chat(
        prompts.build_teachback_check_messages(item, answer))
    import json as _json
    try:
        return _json.loads(resp["choices"][0]["message"]["content"])
    except Exception:
        # Never fail the loop on a malformed gateway reply; treat as
        # uncovered so the patient is coached, not passed.
        return {"covered": False, "coverage": 0.0, "hit_points": [],
                "missed_points": item.get("key_points", []),
                "coach": "I couldn't quite follow that -- try once more in your own words."}


def summarize(total_items: int, covered_items: int) -> dict:
    completed = total_items > 0 and covered_items >= total_items
    return {
        "teachback_completed": completed,
        "items_total": total_items,
        "items_covered": covered_items,
        "coverage": round(covered_items / total_items, 2) if total_items else 0.0,
    }
