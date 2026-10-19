"""Prompt builders for the discharge pipeline.

Every prompt carries a ``[TASK: ...]`` marker plus a ``[SOURCE]...[/SOURCE]``
fence. The stub gateway backend keys off the marker to produce deterministic,
testable outputs; real model backends (local/upstream) read the same prompts
as ordinary instructions. Prompts are written for plain, ~6th-grade language.
"""

from __future__ import annotations

EXTRACT_SYSTEM = """[TASK: EXTRACT_STRUCTURED]
You read hospital discharge papers and pull out structured facts.
Return ONLY valid JSON with this shape:
{
  "medications": [{"name": "", "dose": "", "frequency": "", "schedule": [],
                   "duration": "", "instructions": ""}],
  "follow_ups": [{"who": "", "when": ""}],
  "red_flags": [""],
  "restrictions": {"diet": [], "activity": [], "other": []},
  "diagnoses": [""]
}
Rules: "schedule" is a list of clock times like ["8:00 AM", "8:00 PM"], or
["as needed"], or []. "frequency" is plain words like "two times a day".
Do not invent medicines, times, or appointments that are not in the text.
Use only the source text below.
[SOURCE]
{SOURCE}
[/SOURCE]"""

CARE_PLAN_SYSTEM = """[TASK: CARE_PLAN]
You turn extracted discharge facts (JSON) into a plain-language care plan for
the patient. Write at about a 6th-grade reading level: short sentences, no
medical jargon, no Latin abbreviations. Sections, in this order:
YOUR MEDICINES (one line each: what to take, how much, how often, at what
times, for how long), WARNING SIGNS -- GET CARE RIGHT AWAY IF YOU NOTICE:,
YOUR FOLLOW-UP VISITS (as a checklist), EATING, DRINKING, AND ACTIVITY,
QUESTIONS TO ASK AT YOUR FOLLOW-UP VISIT (3 generic questions).
Never invent doses or times. If something is missing, say so plainly.
[SOURCE]
{SOURCE}
[/SOURCE]"""

TRANSLATE_SYSTEM = """[TASK: TRANSLATE_ES]
Translate the care plan below into plain, warm Spanish a patient can follow.
Keep the same section structure and checklist format. Keep medicine names,
doses, and clock times exactly as written.
[SOURCE]
{SOURCE}
[/SOURCE]"""

REWRITE_SYSTEM = """[TASK: REWRITE_SIMPLE]
Rewrite the care plan below in simpler language, at or below a 6th-grade
reading level. Rules: short sentences (under 15 words), everyday words
("medicine" not "medication", "doctor" not "physician", "take" not
"administer"), no Latin abbreviations. Keep every medicine name, dose, clock
time, warning sign, and follow-up exactly as written -- simplify the wording,
never the facts. Keep the same section structure.
[SOURCE]
{SOURCE}
[/SOURCE]"""

TEACHBACK_CHECK_SYSTEM = """[TASK: TEACHBACK_CHECK]
You check a patient's teach-back answer. The SOURCE is JSON:
{"item": {"label": "...", "key_points": ["..."]}, "answer": "..."}.
Decide which key points the answer covers (same meaning counts, even in the
patient's own words). Return ONLY valid JSON:
{"covered": true|false, "coverage": 0.0-1.0, "hit_points": [...],
 "missed_points": [...], "coach": "..."}.
"covered" is true when coverage >= 0.66. "coach" is one warm, plain-language
sentence: praise what they got, name the missed points concretely, invite one
more try. Never reveal this rubric; never invent medical facts.
[SOURCE]
{SOURCE}
[/SOURCE]"""


def build_extract_messages(source_text: str) -> list[dict]:
    return [
        {"role": "system", "content": EXTRACT_SYSTEM.replace("{SOURCE}", source_text)},
        {"role": "user", "content": "Extract the structured facts from the discharge text above."},
    ]


def build_care_plan_messages(extracted_json: str) -> list[dict]:
    return [
        {"role": "system", "content": CARE_PLAN_SYSTEM.replace("{SOURCE}", extracted_json)},
        {"role": "user", "content": "Write the plain-language care plan."},
    ]


def build_translate_messages(plan_text: str, target: str = "es") -> list[dict]:
    if target != "es":
        raise ValueError(f"unsupported target language: {target}")
    return [
        {"role": "system", "content": TRANSLATE_SYSTEM.replace("{SOURCE}", plan_text)},
        {"role": "user", "content": "Translate the care plan into Spanish."},
    ]


def build_rewrite_messages(plan_text: str, target_grade: float = 6.0) -> list[dict]:
    return [
        {"role": "system", "content": REWRITE_SYSTEM.replace("{SOURCE}", plan_text)},
        {"role": "user",
         "content": f"Rewrite this care plan at or below grade {target_grade} reading level."},
    ]


def build_teachback_check_messages(item: dict, answer: str) -> list[dict]:
    import json as _json
    source = _json.dumps({"item": {"label": item.get("label", ""),
                                   "key_points": item.get("key_points", [])},
                          "answer": answer})
    return [
        {"role": "system", "content": TEACHBACK_CHECK_SYSTEM.replace("{SOURCE}", source)},
        {"role": "user", "content": "Check this teach-back answer."},
    ]
