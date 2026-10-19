"""Discharge pipeline stages.

Each stage calls the embedded gateway through ``app.gateway.dispatch_chat``
-- the exact same code path the ``/v1/chat/completions`` route handler uses.
Nothing in the product bypasses the gateway layer.

Pipeline: extract -> verify (drop/flag unverified claims) -> care plan ->
readability gate (rewrite until <= grade 6).
"""

from __future__ import annotations

import json
import re

from app import gateway
from app.discharge import prompts
from app.discharge.readability import ensure_readability
from app.discharge.verify import verify_extraction


def _strip_code_fences(content: str) -> str:
    content = content.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, re.S)
    return m.group(1) if m else content


def extract_structured(redacted_text: str) -> dict:
    """Discharge text -> structured facts (meds, follow-ups, red flags, ...)."""
    resp = gateway.dispatch_chat(
        prompts.build_extract_messages(redacted_text),
        response_format={"type": "json_object"},
    )
    content = _strip_code_fences(resp["choices"][0]["message"]["content"])
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"gateway returned non-JSON extraction: {exc}")
    # Normalize shape so downstream code never KeyErrors.
    data.setdefault("medications", [])
    data.setdefault("follow_ups", [])
    data.setdefault("red_flags", [])
    data.setdefault("restrictions", {}).setdefault("diet", [])
    data["restrictions"].setdefault("activity", [])
    data["restrictions"].setdefault("other", [])
    data.setdefault("diagnoses", [])
    return data


def build_care_plan(structured: dict) -> str:
    """Structured facts -> plain-language care plan text."""
    resp = gateway.dispatch_chat(
        prompts.build_care_plan_messages(json.dumps(structured, indent=2)))
    return resp["choices"][0]["message"]["content"].strip()


def translate_plan(plan_text: str, target: str = "es") -> str:
    """Care plan -> translated care plan (currently Spanish)."""
    resp = gateway.dispatch_chat(prompts.build_translate_messages(plan_text, target))
    return resp["choices"][0]["message"]["content"].strip()


def run_pipeline(redacted_text: str) -> dict:
    """Full pipeline: text -> extract -> verify -> plan -> readability gate."""
    structured = extract_structured(redacted_text)
    verified, verification_report = verify_extraction(structured, redacted_text)
    plan = build_care_plan(verified)
    readability = ensure_readability(plan)
    return {
        "structured": verified,
        "verification_report": verification_report,
        "plan": readability["text"],
        "readability": {
            "grade": readability["grade"],
            "passes": readability["passes"],
            "meets_gate": readability["meets_gate"],
            "grade_history": readability["grade_history"],
            "target_grade": 6.0,
        },
    }
