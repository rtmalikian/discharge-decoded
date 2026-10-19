"""Unit tests: readability gate, verified extraction, teach-back loop."""

from app.discharge import teachback
from app.discharge.pipeline import extract_structured, run_pipeline
from app.discharge.readability import (
    ensure_readability,
    flesch_kincaid_grade,
)
from app.discharge.verify import verify_extraction
from app import safety


def test_fk_grade_simple_text():
    assert flesch_kincaid_grade("Take one pill each morning. Drink water.") < 6.0


def test_fk_grade_hard_text():
    hard = ("The administration of the prescribed medication dosage is required "
            "prior to the consumption of nourishment in order to facilitate "
            "optimal therapeutic outcomes for the patient.")
    assert flesch_kincaid_grade(hard) > 6.0


def test_ensure_readability_rewrites_or_flags():
    hard = ("The administration of the prescribed medication dosage is required "
            "prior to the consumption of nourishment in order to facilitate "
            "optimal therapeutic outcomes for the patient. "
            "The physician will review your medication utilization.")
    out = ensure_readability(hard, target_grade=6.0, max_passes=3)
    assert out["passes"] <= 3
    assert out["grade"] <= out["grade_history"][0]  # never worse
    assert len(out["grade_history"]) == out["passes"] + 1
    if not out["meets_gate"]:
        assert out["passes"] == 3  # exhausted budget, then flagged


def test_ensure_readability_keeps_simple_text():
    out = ensure_readability("Take one pill each morning.", max_passes=3)
    assert out["passes"] == 0
    assert out["meets_gate"]


def test_verify_drops_unverified_medication():
    structured = {"medications": [
        {"name": "Amoxicillin", "dose": "500 mg", "frequency": "x",
         "schedule": [], "duration": "", "instructions": ""},
        {"name": "Imaginaril", "dose": "999 mg", "frequency": "x",
         "schedule": [], "duration": "", "instructions": ""},
    ], "follow_ups": [], "red_flags": [],
        "restrictions": {}, "diagnoses": []}
    source = "Take Amoxicillin 500 mg three times daily."
    verified, report = verify_extraction(structured, source)
    names = [m["name"] for m in verified["medications"]]
    assert "Amoxicillin" in names
    assert "Imaginaril" not in names  # never presented as fact
    assert any(d["value"].startswith("Imaginaril") for d in report["dropped"])


def test_verify_flags_unconfirmed_dose():
    structured = {"medications": [
        {"name": "Lisinopril", "dose": "10 mg", "frequency": "x",
         "schedule": [], "duration": "", "instructions": ""},
    ], "follow_ups": [], "red_flags": [],
        "restrictions": {}, "diagnoses": []}
    verified, report = verify_extraction(structured, "Take Lisinopril daily.")
    assert verified["medications"][0]["verification"].startswith("dose not confirmed")
    assert report["flagged"]


def test_verify_confirms_clean_extraction():
    structured = extract_structured("Take Amoxicillin 500 mg three times daily for 10 days.")
    verified, report = verify_extraction(structured, "Take Amoxicillin 500 mg three times daily for 10 days.")
    assert report["dropped"] == []
    assert all(m["verification"] == "confirmed in source text"
               for m in verified["medications"])


def test_critical_items_cover_meds_flags_followups():
    structured = {
        "medications": [{"name": "Amoxicillin", "dose": "500 mg",
                         "frequency": "three times a day"}],
        "red_flags": ["Trouble breathing"],
        "follow_ups": [{"who": "Dr. Smith", "when": "in 7 days"}],
    }
    items = teachback.critical_items(structured)
    kinds = [i["kind"] for i in items]
    assert kinds == ["medication", "red_flag", "follow_up"]
    assert all(i["key_points"] for i in items)


def test_teachback_check_covered():
    item = {"id": "med-0", "kind": "medication", "label": "Amoxicillin 500 mg",
            "key_points": ["amoxicillin", "500", "three times a day"]}
    res = teachback.check_answer(item, "I take 500 mg of amoxicillin three times a day.")
    assert res["covered"] is True
    assert res["missed_points"] == []


def test_teachback_check_coaches_misses():
    item = {"id": "med-0", "kind": "medication", "label": "Amoxicillin 500 mg",
            "key_points": ["amoxicillin", "500", "three times a day"]}
    res = teachback.check_answer(item, "I take the white pill.")
    assert res["covered"] is False
    assert res["missed_points"]
    assert "missing" in res["coach"]


def test_teachback_summarize():
    s = teachback.summarize(4, 4)
    assert s["teachback_completed"] is True and s["coverage"] == 1.0
    s = teachback.summarize(4, 2)
    assert s["teachback_completed"] is False and s["coverage"] == 0.5


def test_teachback_api_roundtrip(client):
    structured = {
        "medications": [{"name": "Amoxicillin", "dose": "500 mg",
                         "frequency": "three times a day"}],
        "red_flags": ["Trouble breathing"],
        "follow_ups": [],
    }
    start = client.post("/api/teach-back/start", json={"structured": structured}).json()
    assert start["total"] == 2
    item = start["items"][0]
    ans = client.post("/api/teach-back/answer",
                      json={"item": item,
                            "answer": "I take 500 mg amoxicillin three times a day"}).json()
    assert ans["covered"] is True
    done = client.post("/api/teach-back/complete",
                       json={"total_items": 2, "covered_items": 2}).json()
    assert done["teachback_completed"] is True


def test_care_plan_response_carries_gates(client):
    r = client.post("/api/care-plan", json={"text": "Take Amoxicillin 500 mg three times daily."})
    assert r.status_code == 200
    data = r.json()
    assert data["readability"]["meets_gate"] is True
    assert data["readability"]["grade"] <= 6.0
    assert "verification_report" in data
