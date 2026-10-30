> ## ⚠️ PROOF OF CONCEPT — NOT CLINICALLY TESTED
>
> **This tool has not been tested clinically and is a proof-of-concept for
> research and software demonstration only. It is not a medical device, it
> does not diagnose, treat, or rule out any condition, and its outputs may
> be wrong — including false positives and false negatives.**
>
> **Do not use it to make decisions about your health.**
> For any medical issue or concern, seek a licensed healthcare professional.
> **In an emergency, go to your nearest emergency room or dial 911 immediately.**

▶ **Try the live demo:** https://rtmalikian.github.io/discharge-decoded/ — static preview with demo data, no backend connected.

---

# Discharge Decoded

**The measurable upgrade to hospital discharge.** Paper packets leave most patients with comprehension gaps, and the fix that works — teach-back — takes 1:1 nurse time, so it rarely happens well. Discharge Decoded replaces the packet with a **verified** plain-language care plan, a **readability guarantee**, and **automated teach-back** — and proves it all with an eval harness that runs on every build.

## The problem, measured

- **~78% of ED patients** show comprehension deficits in at least one area of their discharge instructions (Engel et al.).
- Patients who clearly understand their after-hospital instructions are **~30% less likely to be readmitted or visit the ED** (AHRQ-funded research, Project RED).
- Teach-back ("explain it back to me") cut heart-failure 30-day readmissions **nearly in half** — but it requires a nurse's undivided time per patient, so in practice it is rushed or skipped.

## What changes

1. **Verified extraction.** Every medication, follow-up, and warning sign pulled from the discharge papers is checked against the source text. A medication whose name can't be confirmed is *dropped* — never presented as fact. Anything partially confirmed is explicitly flagged in the plan.
2. **Readability guarantee.** Every plan is scored with Flesch-Kincaid and sent back through the AI gateway for rewriting until it reads at **grade 6 or below** (max 3 passes, then it's flagged honestly). The score ships in the API response and the UI. "Plain language" as a measurement, not a vibe.
3. **Automated teach-back.** After the plan, the patient explains the critical items back in their own words — meds, warning signs, follow-ups. The gateway checks coverage against the extracted item list and coaches on anything missed, looping until everything is confirmed or the patient opts out. Completion is tracked as a first-class metric.

## Metrics the eval harness tracks

Synthetic discharge fixtures with ground truth live in `tests/fixtures/`. Every `pytest -q` run measures:

| Metric | Threshold | Meaning |
|---|---|---|
| Medication extraction precision | ≥ 0.80 | extracted meds (name+dose) that match ground truth |
| Medication extraction recall | ≥ 0.80 | ground-truth meds the pipeline found |
| Red-flag recall | ≥ 0.70 | ground-truth warning signs recovered |
| Flesch-Kincaid grade | ≤ 6.0 | readability guarantee holds on the final plan |

Falling below a threshold fails the build. Thresholds are tuned on the deterministic stub backend; re-tune if you change backends.

## How it works

```
 ┌─────────────┐      ┌──────────────────┐      ┌─────────────────────────┐
 │  Discharge   │      │  Embedded AI     │      │  Care plan              │
 │  papers      │─────▶│  gateway         │─────▶│  • verified med schedule│
 │  photo / PDF │ OCR  │  /v1/chat/compl. │      │  • warning signs        │
 │  / pasted    │      │  /v1/embeddings  │      │  • follow-up checklist  │
 │  text        │      │  /v1/models      │      │  • diet/activity limits │
 └─────────────┘      └────────┬─────────┘      │  • readability ≤ grade 6│
        │ PHI redaction gate   │                │  • ES translation       │
        ▼ (before anything      │ backends (env AI_BACKEND):             │
        is stored or logged)   │   stub     → built-in, deterministic    │
                               │   local    → Ollama / llama.cpp (PHI stays home)
                               │   upstream → any OpenAI-compatible API │
                               └─────────────────────────┬───────────────┘
                                                         ▼
                                              ┌─────────────────────┐
                                              │ Automated teach-back│
                                              │ explain back → check│
                                              │ coverage → coach →  │
                                              │ completion metric   │
                                              └─────────────────────┘
```

The key architectural idea: **the product ships its own OpenAI-compatible inference gateway** (`/v1/models`, `/v1/chat/completions`, `/v1/embeddings`), and every AI feature calls through it — nothing talks to an outside API directly. Swap the backend with one env var: run the deterministic stub for tests and demos, a local model for PHI-safe self-hosting, or any OpenAI-compatible provider. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

A PHI redaction gate strips names, phones, emails, SSNs, DOBs, MRNs, and addresses *before* extraction, logging, or any model call. Logs carry kinds only (`PHONE`, `DOB`), never values.

## Quickstart

```bash
git clone https://github.com/rtmalikian/discharge-decoded
cd discharge-decoded
cp .env.example .env
docker compose up --build
# open http://localhost:8000
```

Or run locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Paste discharge text (or upload a photo/PDF), press **Make my care plan**, then **Start teach-back** to check understanding. Spanish translation is one click; the reading-level score is shown with the plan.

### Use a local model (PHI never leaves your machine)

```bash
docker compose --profile local-model up
docker compose exec local-model ollama pull llama3.1:8b
# set AI_BACKEND=local in .env, restart
```

### Use any OpenAI-compatible provider

```bash
# in .env
AI_BACKEND=upstream
UPSTREAM_BASE_URL=https://api.openai.com
UPSTREAM_API_KEY=sk-...
```

## API

| Endpoint | Description |
|---|---|
| `GET /` | Demo UI |
| `GET /healthz` | Status: backend + OCR availability |
| `GET /v1/models` | OpenAI-shaped model list |
| `POST /v1/chat/completions` | OpenAI-shaped chat completions |
| `POST /v1/embeddings` | OpenAI-shaped embeddings |
| `POST /api/upload` | Upload image/PDF → extracted + redacted text |
| `POST /api/care-plan` | `{text}` → `{plan, structured, redaction_report, verification_report, readability}` |
| `POST /api/translate` | `{plan, target: "es"}` → Spanish plan |
| `POST /api/teach-back/start` | `{structured}` → critical items to explain back |
| `POST /api/teach-back/answer` | `{item, answer}` → coverage check + coaching |
| `POST /api/teach-back/complete` | `{total_items, covered_items}` → completion metric |

The `/v1/*` routes are the same code path the product itself uses — point any OpenAI client library at this server and it works.

## Honest limits

- The default `stub` backend is deterministic and rule-based: great for demos, tests, and the eval harness — not a substitute for a real model. Point `AI_BACKEND` at `local` or `upstream` for production-quality language.
- OCR needs the `tesseract` binary (present in the Docker image). Without it, image upload is disabled and the paste-text path is used.
- Verification is only as good as the OCR text it checks against; a misread source can both hide and invent. The readability score is Flesch-Kincaid — a useful proxy, not a comprehension guarantee.
- This is an **educational tool, not a medical device**. It has no FDA clearance and is not a substitute for clinical judgment. Always review the plan — especially doses and warning signs — with your doctor, nurse, or pharmacist.
- Redaction is pattern-based; treat it as a safety net, not a guarantee. Self-host with a local model when handling real PHI.

## Epic documentation lookup (open.epic MCP)

For the roadmap EHR-ingestion work, Discharge Decoded includes a documentation/spec-lookup integration with Epic's open.epic MCP server (`https://mcp.open.epic.com`): `GET /epic-mcp/tools`, `POST /epic-mcp/docs`, and `GET /epic-mcp/documentreference-spec` fetch the Epic DocumentReference API spec for discharge-packet ingestion. This server returns documentation only — it is not a patient-data server, and a no-PHI guard in `app/epic_mcp.py` refuses any query that looks like patient data. See [EPIC_MCP.md](EPIC_MCP.md).

## Roadmap

- [ ] FHIR R4 export of the care plan
- [ ] Armenian + more language packs
- [ ] SMS/email medication reminders
- [ ] EHR discharge-packet ingestion via FHIR DocumentReference
- [ ] Teach-back analytics dashboard (completion + coverage by item type)

## Tests

```bash
pytest -q   # unit + eval harness: gateway contracts, pipeline, redaction,
            # readability gate, verified extraction, teach-back, metric thresholds
```

## Author

Built by **Raphael Malikian** ([@rtmalikian](https://github.com/rtmalikian)) — healthcare founder/operator who bootstrapped and ran a direct primary care clinic as CEO, now building clinical AI in the open. Week 1 of a weekly series shipping open-source clinical AI products with embedded, self-hostable AI infrastructure — each one named against a measurable failure in the current workflow, each one proving the fix with an eval harness.

## License

MIT — see [LICENSE](LICENSE).
