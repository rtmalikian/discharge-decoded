# Architecture — Discharge Decoded

## The core idea: the product owns its AI layer

Most health apps call an outside AI API directly from feature code. Discharge
Decoded inverts that: **the application serves its own OpenAI-compatible
inference gateway**, and every AI feature is a client of that gateway.

```
                    ┌─────────────────────────────────────────┐
                    │            FastAPI application            │
                    │                                         │
  ┌──────────┐      │  ┌───────────────────────────────────┐  │
  │ Demo UI  │      │  │ Product API (/api/*)             │  │
  │ + any    │─────▶│  │  upload → redact → extract →    │  │
  │ OpenAI   │      │  │  care plan → translate          │  │
  │ client   │      │  └───────────────┬───────────────────┘  │
  └──────────┘      │                  │ dispatch_*()         │
                    │  ┌───────────────▼───────────────────┐  │
                    │  │ Gateway (/v1/*)                   │  │
                    │  │  GET  /v1/models                  │  │
                    │  │  POST /v1/chat/completions        │  │
                    │  │  POST /v1/embeddings              │  │
                    │  └───────────────┬───────────────────┘  │
                    │                  │ backend router       │
                    │      ┌───────────┼────────────┐         │
                    │      ▼           ▼            ▼         │
                    │   stub        local       upstream      │
                    │   (built-in   (Ollama /    (any OpenAI- │
                    │   rules)      llama.cpp)   compatible)  │
                    └─────────────────────────────────────────┘
```

Why this layering matters:

1. **One code path.** `app/main.py` route handlers and `app/discharge/pipeline.py`
   both call `app.gateway.dispatch_chat()` / `dispatch_embeddings()`. The
   product cannot bypass its own AI layer — auditing, redaction, and backend
   choice apply uniformly.
2. **Backend portability.** `AI_BACKEND=stub|local|upstream` (env var). Demos
   and CI run with zero model; a clinic self-hosts with `local` so PHI never
   leaves the building; anyone can point `upstream` at their provider.
3. **Interoperability.** Because the gateway speaks OpenAI request/response
   shapes, any OpenAI client library (or a future second product) can use this
   server as a drop-in model endpoint.
4. **Testability.** The stub backend is fully deterministic, so the whole
   pipeline — extraction, scheduling, translation — is covered by unit tests
   with no network and no model weights.

## Request flow: upload → care plan

1. `POST /api/upload` (image or PDF) → `app/discharge/ocr.py` extracts text
   (tesseract for images, pypdf for PDFs; `OCRUnavailable` → 422 with a
   paste-text hint).
2. `app/safety.py::redact_phi()` replaces PHI with `[KIND]` placeholders and
   returns a kinds-only report. **This happens before anything else.**
3. `POST /api/care-plan` → `extract_structured()` sends the redacted text to
   the gateway with the `[TASK: EXTRACT_STRUCTURED]` prompt; the response is
   parsed as JSON (medications with dose/frequency/schedule, follow-ups,
   red flags, restrictions).
4. `build_care_plan()` sends the JSON back through the gateway with
   `[TASK: CARE_PLAN]`; the response is the plain-language plan. Medication
   frequencies are normalized to clock times ("three times a day" →
   8:00 AM / 2:00 PM / 8:00 PM; "at bedtime" → 10:00 PM; PRN → "as needed").
5. `POST /api/translate` → `[TASK: TRANSLATE_ES]` renders the Spanish version.
6. The UI renders sections, shows the redaction notice, and offers a
   print-friendly stylesheet.

## The stub backend (deterministic, zero-model)

`app/gateway.py` keys stub responses off `[TASK: ...]` markers embedded in the
system prompt by `app/discharge/prompts.py`:

- `EXTRACT_STRUCTURED` → regex extraction (medication `Name <dose> mg|mcg`
  patterns, frequency keywords → plain language + clock times, follow-up /
  red-flag / restriction sentence classifiers).
- `CARE_PLAN` → plain-language templates over the extracted JSON. Written for
  ~6th-grade reading level: short sentences, no Latin abbreviations.
- `TRANSLATE_ES` → glossary translation over the template vocabulary, plus a
  machine-translation disclaimer. With a real model backend, the same prompt
  yields a full translation.

Real backends (`local`, `upstream`) forward the identical prompts over HTTP to
an OpenAI-compatible server and return the provider's content inside our
envelope, so switching backends never changes the product code.

## Safety pipeline

`app/safety.py` is a standalone module with no gateway dependency:

- Patterns: `EMAIL`, `PHONE`, `SSN`, `DOB` (labeled), `MRN`, `ADDRESS`,
  `NAME` (after "Patient:"/"Name:" labels).
- `redact_phi()` → `(redacted_text, {kind: count})`. Placeholders preserve
  document structure for the extractor.
- Logging uses `logger.info("phi_redacted", extra={"phi_kinds": [...]})` —
  kinds and counts only, enforced by test.
- `looks_like_phi()` is an audit helper to assert post-pipeline text is clean.

## Module map

| Path | Responsibility |
|---|---|
| `app/main.py` | Routes: `/v1/*`, `/api/*`, static UI, `/healthz` |
| `app/gateway.py` | OpenAI-shaped dispatch + backend router + stub engine |
| `app/safety.py` | PHI redaction gate, kinds-only logging |
| `app/discharge/ocr.py` | Image/PDF text extraction with graceful fallback |
| `app/discharge/prompts.py` | Task-marked prompt builders |
| `app/discharge/pipeline.py` | extract → care plan → translate orchestration |
| `static/index.html` | Single-page demo UI (upload/paste/translate/print) |
| `tests/` | Gateway contracts, pipeline fixtures, redaction tests |

## Deployment

- `Dockerfile`: python:3.12-slim + tesseract-ocr, uvicorn on :8000.
- `docker-compose.yml`: `api` service; optional `local-model` profile running
  Ollama for PHI-safe self-hosting.
- No database, no retained uploads, no auth — single-user self-hosted tool.
  Front with a reverse proxy (TLS + auth + rate limits) before any network
  exposure.
