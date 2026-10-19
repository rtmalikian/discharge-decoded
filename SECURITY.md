# Security Policy — Discharge Decoded

## What this software is (and is not)

Discharge Decoded is an **educational tool, not a medical device**. It reformats
discharge paperwork into plain language. It does not diagnose, prescribe, or
replace clinical judgment. It has **no FDA clearance** and makes no clinical
claims. Every care plan it produces must be reviewed with a clinician —
especially medication doses and warning signs.

## Threat model

**Assets:** patient discharge documents, which routinely contain PHI (names,
dates of birth, MRNs, contact details, diagnoses, medications).

**Adversaries / risks considered:**

1. **PHI leaking into logs or stored artifacts.** Mitigated by the redaction
   gate (`app/safety.py`): names, phones, emails, SSNs, DOBs, MRNs, and street
   addresses are replaced with `[KIND]` placeholders *before* extraction,
   logging, or any model call. Structured logs carry kinds and counts only —
   never values. There is no database; uploads live only in request memory.
2. **PHI sent to a third-party model provider.** Mitigated two ways: (a) only
   redacted text ever reaches the gateway, and (b) the `local` backend keeps
   inference on your own hardware (Ollama / llama.cpp), so nothing leaves the
   machine at all. The `stub` backend sends nothing anywhere.
3. **Prompt injection via uploaded documents.** Discharge text is treated as
   *data*, fenced in `[SOURCE]...[/SOURCE]` blocks, never as instructions.
   The stub backend only recognizes `[TASK: ...]` markers in the system prompt,
   not in source text.
4. **Oversized / malicious uploads.** 10 MB cap per upload; PDFs are parsed
   with pypdf (no code execution); images go through Pillow + tesseract only.
5. **Dependency risk.** Pinned-minimum `requirements.txt`, slim Docker base,
   no native extensions beyond Pillow/tesseract.

## Honest limits

- **Redaction is pattern-based, not perfect.** Unusual name formats, handwritten
  notes, or PHI embedded in images that OCR misreads can slip through. Treat
  redaction as a safety net. When handling real patient documents, **self-host
  with `AI_BACKEND=local`** so raw documents never leave your infrastructure.
- **The `stub` backend is deterministic rules, not intelligence.** It extracts
  with regexes and renders from templates. It will miss anything phrased
  unusually. It exists for tests, CI, and demos.
- **Even with a real model backend, outputs can be wrong.** Models hallucinate;
  OCR misreads doses. The UI carries a standing warning to verify with a
  clinician, and the print stylesheet keeps that warning on the page.
- **No authentication or multi-tenancy.** This is a single-user self-hosted
  tool. Do not expose it to the internet without putting auth, TLS, and rate
  limiting in front of it (reverse proxy).
- **No audit trail of who viewed what.** There is no user system and no
  retained record of generated plans. If you deploy this in a clinical setting,
  you own access logging and HIPAA compliance review.

## Reporting a vulnerability

Open a GitHub issue titled `[security]` or email the maintainer via the address
on the GitHub profile. Please do not include real patient data in the report.
