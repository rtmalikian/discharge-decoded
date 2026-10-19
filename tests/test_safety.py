"""Tests for the PHI/PII redaction gate."""

import logging

from app import safety


def test_redacts_common_phi_kinds():
    text = ("Patient: Jane Doe\nDOB: 03/14/1958\nMRN: 987654\n"
            "Call (555) 123-4567 or jane.doe@example.com\n"
            "123 Main St, Palmdale CA\nSSN 123-45-6789")
    redacted, report = safety.redact_phi(text)
    for kind in ("NAME", "DOB", "MRN", "PHONE", "EMAIL", "ADDRESS", "SSN"):
        assert report.get(kind, 0) >= 1, f"{kind} not redacted"
        assert f"[{kind}]" in redacted
    # Values must be gone.
    for value in ("Jane Doe", "03/14/1958", "987654", "(555) 123-4567",
                  "jane.doe@example.com", "123 Main St", "123-45-6789"):
        assert value not in redacted


def test_report_counts_kinds_only():
    redacted, report = safety.redact_phi("a@b.com and c@d.com, call 555-123-4567")
    assert report == {"EMAIL": 2, "PHONE": 1}
    # report carries counts, never the values
    assert "a@b.com" not in str(report)


def test_clean_text_passes_through():
    text = "Take one tablet every morning."
    redacted, report = safety.redact_phi(text)
    assert redacted == text
    assert report == {}


def test_log_record_carries_kinds_not_values(caplog):
    with caplog.at_level(logging.INFO, logger="discharge-decoded.safety"):
        safety.redact_phi("Patient: Jane Doe, phone 555-123-4567")
    records = [r for r in caplog.records if r.msg == "phi_redacted"]
    assert records, "expected a phi_redacted log record"
    logged = str(records[0].__dict__)
    assert "Jane Doe" not in logged
    assert "555-123-4567" not in logged
    assert "NAME" in logged and "PHONE" in logged


def test_looks_like_phi_audit_helper():
    assert "EMAIL" in safety.looks_like_phi("write to jane.doe@example.com")
    assert safety.looks_like_phi("Take one tablet every morning.") == []
