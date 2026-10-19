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
