"""Embedded OpenAI-compatible inference gateway.

This module is the architectural core of Discharge Decoded: instead of the
product calling an outside AI API directly, every AI feature goes through this
gateway, which speaks the OpenAI API shapes (``/v1/models``,
``/v1/chat/completions``, ``/v1/embeddings``) and routes to a pluggable
backend selected by the ``AI_BACKEND`` environment variable:

- ``stub`` (default): deterministic, template/rule-based responses. Zero model
  needed. Used by tests, CI, and the live demo when no model is configured.
- ``local``: forwards to an OpenAI-compatible server on your own hardware
  (Ollama, llama.cpp server, vLLM, ...) via ``LOCAL_MODEL_URL``. PHI never
  leaves the machine -- the self-hosting story.
- ``upstream``: forwards to any OpenAI-compatible provider via
  ``UPSTREAM_BASE_URL`` + ``UPSTREAM_API_KEY``.

The FastAPI route handlers in ``app/main.py`` and the discharge pipeline in
``app/discharge/`` both call the ``dispatch_*`` functions below, so the
product always travels the same code path -- nothing bypasses the gateway.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid

import httpx
from fastapi import HTTPException

STUB_MODEL_ID = "discharge-decoded-stub-1"
_REQUEST_TIMEOUT = 120.0


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def get_backend() -> str:
    """Return the configured backend name: stub | local | upstream."""
    return (os.environ.get("AI_BACKEND", "stub") or "stub").strip().lower()


def _local_base() -> str:
    return (os.environ.get("LOCAL_MODEL_URL", "http://localhost:11434") or "").rstrip("/")


def _upstream_base() -> str:
    return (os.environ.get("UPSTREAM_BASE_URL", "https://api.openai.com") or "").rstrip("/")


def _upstream_headers() -> dict:
    key = os.environ.get("UPSTREAM_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


# ---------------------------------------------------------------------------
# Public dispatch API -- the single code path for all AI calls
# ---------------------------------------------------------------------------

def list_models() -> dict:
    backend = get_backend()
    if backend == "stub":
        return {
            "object": "list",
            "data": [
                {
                    "id": STUB_MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "discharge-decoded",
                }
            ],
        }
    if backend == "local":
        try:
            r = httpx.get(f"{_local_base()}/v1/models", timeout=10.0)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {
                "object": "list",
                "data": [{"id": "local-model", "object": "model",
                          "created": int(time.time()), "owned_by": "local"}],
            }
    if backend == "upstream":
        try:
            r = httpx.get(f"{_upstream_base()}/v1/models",
                          headers=_upstream_headers(), timeout=15.0)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            raise HTTPException(status_code=502,
                                detail=f"upstream /v1/models failed: {exc}")
    raise HTTPException(status_code=500, detail=f"unknown AI_BACKEND: {backend}")


def dispatch_chat(messages: list[dict], model: str | None = None,
                  temperature: float = 0.2, max_tokens: int = 2000,
                  response_format: dict | None = None) -> dict:
    """OpenAI-shaped chat completion through the configured backend."""
    backend = get_backend()
    if backend == "stub":
        content = _stub_chat(messages)
        model_id = model or STUB_MODEL_ID
    elif backend in ("local", "upstream"):
        content, model_id = _forward_chat(
            messages, model, temperature, max_tokens, response_format, backend)
    else:
        raise HTTPException(status_code=500, detail=f"unknown AI_BACKEND: {backend}")

    prompt_tokens = max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4)
    completion_tokens = max(1, len(content) // 4)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def dispatch_embeddings(input: str | list[str], model: str | None = None) -> dict:
    """OpenAI-shaped embeddings through the configured backend."""
    backend = get_backend()
    texts = [input] if isinstance(input, str) else list(input)
    if backend == "stub":
        vectors = [_stub_embedding(t) for t in texts]
        model_id = model or STUB_MODEL_ID
    elif backend in ("local", "upstream"):
        vectors, model_id = _forward_embeddings(texts, model, backend)
    else:
        raise HTTPException(status_code=500, detail=f"unknown AI_BACKEND: {backend}")

    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": vec, "index": i}
            for i, vec in enumerate(vectors)
        ],
        "model": model_id,
        "usage": {
            "prompt_tokens": max(1, sum(len(t) for t in texts) // 4),
            "total_tokens": max(1, sum(len(t) for t in texts) // 4),
        },
    }


# ---------------------------------------------------------------------------
# Forwarding backends
# ---------------------------------------------------------------------------

def _forward_chat(messages, model, temperature, max_tokens, response_format, backend):
    if backend == "local":
        url = f"{_local_base()}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        model_id = model or os.environ.get("LOCAL_MODEL", "local-model")
    else:
        url = f"{_upstream_base()}/v1/chat/completions"
        headers = _upstream_headers()
        model_id = model or os.environ.get("UPSTREAM_MODEL", "gpt-4o-mini")
    payload = {"model": model_id, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if response_format:
        payload["response_format"] = response_format
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"{backend} chat completions failed: {exc}")
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=502,
                            detail=f"{backend} returned an unexpected chat shape")
    return content, data.get("model", model_id)


def _forward_embeddings(texts, model, backend):
    if backend == "local":
        url = f"{_local_base()}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        model_id = model or os.environ.get("LOCAL_MODEL", "local-model")
    else:
        url = f"{_upstream_base()}/v1/embeddings"
        headers = _upstream_headers()
        model_id = model or os.environ.get("UPSTREAM_MODEL", "text-embedding-3-small")
    try:
        r = httpx.post(url, json={"model": model_id, "input": texts},
                       headers=headers, timeout=_REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"{backend} embeddings failed: {exc}")
    try:
        vectors = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
    except (KeyError, TypeError):
        raise HTTPException(status_code=502,
                            detail=f"{backend} returned an unexpected embeddings shape")
    return vectors, data.get("model", model_id)


# ---------------------------------------------------------------------------
# Stub backend -- deterministic, zero-model, used by tests/CI/demo
# ---------------------------------------------------------------------------

def _stub_embedding(text: str, dims: int = 16) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round(b / 255.0, 6) for b in digest[:dims]]


def _find_task(messages: list[dict]) -> tuple[str | None, str]:
    """Pull the [TASK: ...] marker and the source text out of the messages."""
    blob = "\n".join(str(m.get("content", "")) for m in messages)
    m = re.search(r"\[TASK:\s*([A-Z_]+)\]", blob)
    task = m.group(1) if m else None
    # Source text is fenced between markers by the prompt builders.
    src = re.search(r"\[SOURCE\]\s*(.*?)\s*\[/SOURCE\]", blob, re.S)
    return task, (src.group(1) if src else blob)


def _stub_chat(messages: list[dict]) -> str:
    task, source = _find_task(messages)
    if task == "EXTRACT_STRUCTURED":
        return _stub_extract(source)
    if task == "CARE_PLAN":
        return _stub_care_plan(source)
    if task == "TRANSLATE_ES":
        return _stub_translate_es(source)
    # Graceful default: short deterministic completion.
    snippet = re.sub(r"\s+", " ", source).strip()[:160]
    return f"Stub response for: {snippet}" if snippet else "Stub response."


# --- structured extraction (deterministic rules) ----------------------------

_MED_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z\-]+)"
    r"(?:\s+(?i:inhaler|tablets?|capsules?|syrup|drops|spray|patch|cream|"
    r"ointment|solution|suspension|injection|lozenge))?"
    r"\s+(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|g|mL|units))\b"
)
_FREQ_PATTERNS = [
    (r"three times (daily|a day)|\bTID\b", "three times a day", ["8:00 AM", "2:00 PM", "8:00 PM"]),
    (r"twice (daily|a day)|\bBID\b", "two times a day", ["8:00 AM", "8:00 PM"]),
    (r"four times (daily|a day)|\bQID\b", "four times a day", ["8:00 AM", "12:00 PM", "4:00 PM", "8:00 PM"]),
    (r"every (\d+)\s*hours?", None, None),  # handled dynamically
    (r"\bQD\b|once (daily|a day)|every day|every morning", "one time a day", ["8:00 AM"]),
    (r"at bedtime|\bQHS\b|every night", "at bedtime", ["10:00 PM"]),
    (r"with meals", "with each meal", ["8:00 AM", "12:30 PM", "6:30 PM"]),
]
_DURATION_RE = re.compile(r"for\s+(\d+\s*(?:days?|weeks?|months?))", re.I)
_INSTR_RE = re.compile(r",?\s*(with food|with meals|on an empty stomach|with a full glass of water)\.?", re.I)


def _plain_frequency(line: str) -> tuple[str, list[str]]:
    low = line.lower()
    prn = bool(re.search(r"\bas needed\b|\bprn\b", low))
    for pattern, plain, times in _FREQ_PATTERNS:
        m = re.search(pattern, low, re.I)
        if not m:
            continue
        if plain is None:  # "every N hours"
            n = int(m.group(1))
            slots = [f"{h % 12 or 12}:00 {'AM' if h < 12 else 'PM'}"
                     for h in range(8, 8 + 24, n)][: 24 // n]
            text = f"every {n} hours"
            return (f"{text} as needed" if prn else text,
                    ["as needed"] if prn else slots)
        return (f"{plain} as needed" if prn else plain,
                ["as needed"] if prn else list(times))
    return ("as needed" if prn else "as directed", ["as needed"] if prn else [])


def _stub_extract(source: str) -> str:
    import json as _json
    medications, follow_ups, red_flags, diet, activity, other, diagnoses = [], [], [], [], [], [], []
    for raw_line in source.splitlines():
        line = raw_line.strip().lstrip("-•*0123456789. ").strip()
        if not line:
            continue
        low = line.lower()
        # Skip section headers ("MEDICATIONS:", "FOLLOW-UP:", ...).
        if re.match(r"^(medications|follow[\s-]?up|diet|activity|restrictions|"
                    r"diagnos[ie]s?|return precautions)\s*:?\s*$", low):
            continue

        med = _MED_RE.search(line)
        if med and not re.search(r"discharge|diagnosis|patient", low):
            freq_plain, times = _plain_frequency(line)
            dur = _DURATION_RE.search(line)
            instr = _INSTR_RE.search(line)
            medications.append({
                "name": med.group("name").strip(),
                "dose": med.group("dose").strip(),
                "frequency": freq_plain,
                "schedule": times,
                "duration": dur.group(1) if dur else "",
                "instructions": instr.group(1) if instr else "",
            })
            continue

        if re.search(r"follow[\s-]?up|appointment|see (dr|doctor)|x-?ray|lab|blood work|recheck", low):
            who_m = re.search(
                r"(?:with|at|see)\s+([A-Z][\w.\-]*(?:\s+[A-Z][\w.\-]*)*)", line)
            who = who_m.group(1).strip().rstrip(".") if who_m else line[:60].rstrip(".")
            if not who or re.match(r"^follow[\s-]?up$", who, re.I):
                continue
            when_m = re.search(r"in\s+(\d+\s*(?:days?|weeks?|months?))", low)
            follow_ups.append({
                "who": who,
                "when": f"in {when_m.group(1)}" if when_m else "",
            })
            continue

        if re.search(r"call your doctor|go to the (emergency|er)|return if|seek care|warning|call 911", low):
            cond = re.split(r"\bif you have\b|\bif\b", line, maxsplit=1)
            chunk = cond[1] if len(cond) > 1 else line
            parts = re.split(r",|\bor\b|\band\b", chunk)
            for p in parts:
                p = p.strip(" .")
                if len(p) > 3 and not re.search(r"call your doctor|emergency", p, re.I):
                    red_flags.append(p[0].upper() + p[1:])
            continue

        if re.search(r"\bavoid\b|do not|don't|no heavy|rest for|limit", low):
            clean = re.sub(r"^(diet|activity)\s*:\s*", "", line, flags=re.I)
            (diet if re.search(r"alcohol|drink|eat|food|diet|fluid", low) else activity).append(clean)
            continue

        if re.search(r"diagnos(is|ed)|admitted for", low):
            d = re.sub(r"(?i)^.*diagnos(is|ed with|is)\s*:?\s*", "", line).strip(" .")
            if d:
                diagnoses.append(d)

    return _stub_json({
        "medications": medications,
        "follow_ups": follow_ups,
        "red_flags": red_flags,
        "restrictions": {"diet": diet, "activity": activity, "other": other},
        "diagnoses": diagnoses,
    })


def _stub_json(obj) -> str:
    import json as _json
    return _json.dumps(obj, indent=2)


# --- care-plan rendering (plain-language templates) -------------------------

def _stub_care_plan(source: str) -> str:
    """Render the extracted JSON into a plain-language care plan."""
    import json as _json
    try:
        data = _json.loads(source)
    except Exception:
        data = {}
    lines: list[str] = []
    lines.append("YOUR CARE PLAN (plain language)")
    lines.append("")

    meds = data.get("medications", [])
    lines.append("YOUR MEDICINES")
    if meds:
        for med in meds:
            name, dose = med.get("name", ""), med.get("dose", "")
            freq, sched = med.get("frequency", ""), med.get("schedule", [])
            times = ", ".join(s for s in sched if s != "as needed")
            line = f"Take {dose} of {name}, {freq}."
            if times:
                line += f" Set times: {times}."
            if med.get("duration"):
                line += f" Keep taking it for {med['duration']}."
            if med.get("instructions"):
                line += f" {med['instructions'].capitalize()}."
            lines.append(f"- {line}")
    else:
        lines.append("- No medicines were listed in your discharge papers.")
    lines.append("")

    lines.append("WARNING SIGNS -- GET CARE RIGHT AWAY IF YOU NOTICE:")
    flags = data.get("red_flags", [])
    if flags:
        for f in flags:
            lines.append(f"- {f}")
        lines.append("If you have any of these, call your doctor or go to the emergency room.")
    else:
        lines.append("- No warning signs were listed. If you feel much worse, call your doctor.")
    lines.append("")

    lines.append("YOUR FOLLOW-UP VISITS")
    fus = data.get("follow_ups", [])
    if fus:
        for fu in fus:
            when = f" ({fu['when']})" if fu.get("when") else ""
            lines.append(f"- [ ] {fu.get('who', '')}{when}")
    else:
        lines.append("- No follow-up visits were listed. Ask your doctor if you need one.")
    lines.append("")

    lines.append("EATING, DRINKING, AND ACTIVITY")
    restr = data.get("restrictions", {})
    any_r = False
    for label, items in (("Food and drink", restr.get("diet", [])),
                         ("Activity", restr.get("activity", [])),
                         ("Other", restr.get("other", []))):
        for item in items:
            lines.append(f"- {label}: {item}")
            any_r = True
    if not any_r:
        lines.append("- No limits were listed. Follow your doctor's advice.")
    lines.append("")

    lines.append("QUESTIONS TO ASK AT YOUR FOLLOW-UP VISIT")
    lines.append("- Are my medicines still right for me?")
    lines.append("- What test results should I ask about?")
    lines.append("- What should I do if I miss a dose?")
    return "\n".join(lines)


# --- Spanish translation (stub glossary over template vocabulary) ------------

_ES_GLOSSARY: list[tuple[str, str]] = [
    ("YOUR CARE PLAN (plain language)", "SU PLAN DE CUIDADO (en lenguaje sencillo)"),
    ("YOUR MEDICINES", "SUS MEDICAMENTOS"),
    ("WARNING SIGNS -- GET CARE RIGHT AWAY IF YOU NOTICE:",
     "SEÑALES DE ALARMA -- BUSQUE ATENCIÓN DE INMEDIATO SI NOTA:"),
    ("If you have any of these, call your doctor or go to the emergency room.",
     "Si tiene alguno de estos síntomas, llame a su médico o vaya a la sala de emergencias."),
    ("YOUR FOLLOW-UP VISITS", "SUS CITAS DE SEGUIMIENTO"),
    ("EATING, DRINKING, AND ACTIVITY", "COMIDA, BEBIDA Y ACTIVIDAD"),
    ("QUESTIONS TO ASK AT YOUR FOLLOW-UP VISIT", "PREGUNTAS PARA SU CITA DE SEGUIMIENTO"),
    ("No medicines were listed in your discharge papers.",
     "No se listaron medicamentos en sus documentos de alta."),
    ("No warning signs were listed. If you feel much worse, call your doctor.",
     "No se listaron señales de alarma. Si se siente mucho peor, llame a su médico."),
    ("No follow-up visits were listed. Ask your doctor if you need one.",
     "No se listaron citas de seguimiento. Pregunte a su médico si necesita una."),
    ("No limits were listed. Follow your doctor's advice.",
     "No se listaron límites. Siga los consejos de su médico."),
    ("Take", "Tome"),
    ("Set times:", "Horarios:"),
    ("Keep taking it for", "Síguelo tomando por"),
    ("of", "de"),
    ("Food and drink", "Comida y bebida"),
    ("Activity", "Actividad"),
    ("Other", "Otro"),
    ("Are my medicines still right for me?", "¿Mis medicamentos siguen siendo adecuados para mí?"),
    ("What test results should I ask about?", "¿Sobre qué resultados de pruebas debo preguntar?"),
    ("What should I do if I miss a dose?", "¿Qué debo hacer si olvido una dosis?"),
]


def _stub_translate_es(source: str) -> str:
    out = source
    for en, es in _ES_GLOSSARY:
        out = out.replace(en, es)
    header = ("NOTA: traducción automática de demostración. "
              "Con un modelo real configurado, esta traducción sería completa. "
              "Revise su plan con su médico.\n\n")
    return header + out
