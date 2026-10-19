"""Pipeline tests on a synthetic discharge fixture (stub backend)."""

import pathlib

import pytest

from app import safety
from app.discharge.pipeline import (
    build_care_plan,
    extract_structured,
    run_pipeline,
    translate_plan,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "synthetic_discharge.txt"


@pytest.fixture(scope="module")
def redacted_text():
    raw = FIXTURE.read_text()
    redacted, _ = safety.redact_phi(raw)
    return redacted


def test_extract_medications(redacted_text):
    data = extract_structured(redacted_text)
    names = [m["name"].lower() for m in data["medications"]]
    assert "amoxicillin" in names
    assert "lisinopril" in names
    amox = next(m for m in data["medications"] if m["name"].lower() == "amoxicillin")
    assert amox["dose"] == "500 mg"
    assert amox["frequency"] == "three times a day"
    assert amox["schedule"] == ["8:00 AM", "2:00 PM", "8:00 PM"]
    assert amox["duration"] == "10 days"


def test_extract_prn_medication(redacted_text):
    data = extract_structured(redacted_text)
    alb = next(m for m in data["medications"] if m["name"].lower() == "albuterol")
    assert "as needed" in alb["frequency"]
    assert alb["schedule"] == ["as needed"]


def test_extract_follow_ups_and_red_flags(redacted_text):
    data = extract_structured(redacted_text)
    assert any("smith" in fu["who"].lower() for fu in data["follow_ups"])
    assert any("7 days" in fu["when"] for fu in data["follow_ups"])
    flags = " ".join(data["red_flags"]).lower()
    assert "trouble breathing" in flags
    assert "chest pain" in flags


def test_extract_restrictions(redacted_text):
    data = extract_structured(redacted_text)
    diet = " ".join(data["restrictions"]["diet"]).lower()
    activity = " ".join(data["restrictions"]["activity"]).lower()
    assert "alcohol" in diet
    assert "lifting" in activity


def test_care_plan_sections(redacted_text):
    result = run_pipeline(redacted_text)
    plan = result["plan"]
    for section in ("YOUR MEDICINES", "WARNING SIGNS", "YOUR FOLLOW-UP VISITS",
                    "EATING, DRINKING, AND ACTIVITY"):
        assert section in plan, f"missing section: {section}"
    assert "8:00 AM" in plan  # scheduled times rendered


def test_care_plan_no_invented_sections():
    # Minimal input still yields a well-formed plan, not a crash.
    plan = build_care_plan({"medications": [], "follow_ups": [],
                            "red_flags": [], "restrictions": {},
                            "diagnoses": []})
    assert "YOUR MEDICINES" in plan
    assert "No medicines were listed" in plan


def test_translate_spanish(redacted_text):
    plan = build_care_plan(extract_structured(redacted_text))
    es = translate_plan(plan, "es")
    assert "SUS MEDICAMENTOS" in es
    assert "SEÑALES DE ALARMA" in es


def test_translate_rejects_unknown_language():
    with pytest.raises(ValueError):
        translate_plan("hello", "fr")
