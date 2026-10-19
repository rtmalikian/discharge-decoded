"""Eval harness -- runs on every CI run (``pytest -q``).

Synthetic discharge fixtures ship with ground truth. We measure:
  - medication extraction precision / recall (name + dose must match)
  - red-flag recall (content-word overlap >= 0.5 counts as a hit)
  - Flesch-Kincaid grade of the final care plan (readability guarantee)

Thresholds below are tuned on the deterministic stub backend. If the
configured backend changes, re-tune them. Falling below a threshold fails
the build -- that is the point.
"""

import json
import pathlib
import re

import pytest

from app import safety
from app.discharge.pipeline import run_pipeline

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# Metric thresholds: the build fails below these.
MED_PRECISION_MIN = 0.80
MED_RECALL_MIN = 0.80
FLAG_RECALL_MIN = 0.70
FK_GRADE_MAX = 6.0

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "with",
        "on", "you", "your", "if", "is", "are", "over", "than", "more"}


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _norm_dose(dose: str) -> str:
    return " ".join(re.sub(r"\D", " ", (dose or "")).split())


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 3 and w not in _STOP}


def _load_case(stem: str):
    text = (FIXTURES / f"{stem}.txt").read_text()
    truth = json.loads((FIXTURES / f"{stem}.truth.json").read_text())
    return text, truth


def _evaluate(stem: str) -> dict:
    text, truth = _load_case(stem)
    redacted, _ = safety.redact_phi(text)
    result = run_pipeline(redacted)

    pred_meds = {(_norm_name(m.get("name")), _norm_dose(m.get("dose")))
                 for m in result["structured"]["medications"]}
    true_meds = {(_norm_name(m["name"]), _norm_dose(m["dose"]))
                 for m in truth["medications"]}
    tp = len(pred_meds & true_meds)
    precision = tp / len(pred_meds) if pred_meds else 0.0
    recall = tp / len(true_meds) if true_meds else 1.0

    pred_flags = result["structured"]["red_flags"]
    hits = 0
    for tf in truth["red_flags"]:
        tw = _content_words(tf)
        if any(len(tw & _content_words(pf)) / len(tw) >= 0.5 for pf in pred_flags):
            hits += 1
    flag_recall = hits / len(truth["red_flags"]) if truth["red_flags"] else 1.0

    return {
        "stem": stem,
        "med_precision": round(precision, 2),
        "med_recall": round(recall, 2),
        "flag_recall": round(flag_recall, 2),
        "fk_grade": result["readability"]["grade"],
        "meets_gate": result["readability"]["meets_gate"],
    }


@pytest.mark.parametrize("stem", ["eval_cellulitis", "eval_migraine"])
def test_eval_case(stem):
    m = _evaluate(stem)
    print(f"\neval/{stem}: P={m['med_precision']} R={m['med_recall']} "
          f"flagR={m['flag_recall']} FK={m['fk_grade']}")
    assert m["med_precision"] >= MED_PRECISION_MIN, \
        f"med precision {m['med_precision']} < {MED_PRECISION_MIN}"
    assert m["med_recall"] >= MED_RECALL_MIN, \
        f"med recall {m['med_recall']} < {MED_RECALL_MIN}"
    assert m["flag_recall"] >= FLAG_RECALL_MIN, \
        f"flag recall {m['flag_recall']} < {FLAG_RECALL_MIN}"
    assert m["fk_grade"] <= FK_GRADE_MAX, \
        f"readability grade {m['fk_grade']} > {FK_GRADE_MAX}"
    assert m["meets_gate"], "readability gate not met"
