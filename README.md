# Discharge Decoded

**Turn hospital discharge papers into a plain-language care plan.** Photograph (or paste) your discharge instructions — get back a medication schedule with times, warning signs to watch for, a follow-up checklist, and eating/activity guidance, written in plain language. Optional Spanish translation. Free, open-source, self-hostable.

## Why this exists

Hospital discharge is where care most often breaks down:

- Patients who clearly understand their after-hospital instructions are **~30% less likely to be readmitted or visit the ED** (AHRQ-funded research, Project RED).
- Yet **~78% of ED patients show comprehension deficits** in at least one area of their discharge instructions (Engel et al.).
- Teach-back programs that fixed this cut heart-failure 30-day readmissions **nearly in half** — but most hospitals still hand patients dense, jargon-filled packets.

Discharge Decoded is the patient-side version of that fix: an open tool that decodes the packet into a plan a person can actually follow.

## How it works

```
 ┌─────────────┐      ┌──────────────────┐      ┌─────────────────────────┐
 │  Discharge   │      │  Embedded AI     │      │  Care plan              │
 │  papers      │─────▶│  gateway         │─────▶│  • med schedule + times │
 │  photo / PDF │ OCR  │  /v1/chat/compl. │      │  • warning signs        │
 │  / pasted    │      │  /v1/embeddings  │      │  • follow-up checklist  │
 │  text        │      │  /v1/models      │      │  • diet/activity limits │
 └─────────────┘      └────────┬─────────┘      │  • ES translation       │
        │ PHI redaction gate   │                └─────────────────────────┘
        ▼ (before anything      │ backends (env AI_BACKEND):
        is stored or logged)   │   stub     → built-in, deterministic
                               │   local    → Ollama / llama.cpp (PHI stays home)
                               │   upstream → any OpenAI-compatible API
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

Paste discharge text (or upload a photo/PDF), press **Make my care plan**, print it. Spanish translation is one click.

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
| `POST /api/care-plan` | `{text}` → `{plan, structured, redaction_report}` |
| `POST /api/translate` | `{plan, target: "es"}` → Spanish plan |

The `/v1/*` routes are the same code path the product itself uses — point any OpenAI client library at this server and it works.

## Honest limits

- The default `stub` backend is deterministic and rule-based: great for demos and tests, not a substitute for a real model. Point `AI_BACKEND` at `local` or `upstream` for production-quality language.
- OCR needs the `tesseract` binary (present in the Docker image). Without it, image upload is disabled and the paste-text path is used.
- This is an **educational tool, not a medical device**. It has no FDA clearance and is not a substitute for clinical judgment. Always review the plan — especially doses and warning signs — with your doctor, nurse, or pharmacist.
- Redaction is pattern-based; treat it as a safety net, not a guarantee. Self-host with a local model when handling real PHI.

## Roadmap

- [ ] Teach-back quiz mode: "explain your meds back to me" (*good first issue*)
- [ ] Armenian + more language packs
- [ ] FHIR R4 export of the care plan
- [ ] SMS/email medication reminders
- [ ] EHR discharge-packet ingestion via FHIR DocumentReference

## Tests

```bash
pytest -q   # 30+ tests: gateway contracts, pipeline, redaction — all on the stub backend
```

## Author

Built by **Raphael Malikian** ([@rtmalikian](https://github.com/rtmalikian)) — healthcare founder/operator who bootstrapped and ran a direct primary care clinic as CEO, now building clinical AI in the open. Part of a weekly series shipping open-source clinical AI products with embedded, self-hostable AI infrastructure.

## License

MIT — see [LICENSE](LICENSE).
