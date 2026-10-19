"""Discharge Decoded -- FastAPI application.

Routes:
  /v1/*    Embedded OpenAI-compatible inference gateway (the product's AI
           layer -- every AI feature calls through here).
  /api/*   Product API: upload discharge documents, generate care plans,
           translate them.
  /        Static single-page demo UI.
  /healthz Health check.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import gateway, safety
from app.discharge import ocr
from app.discharge.pipeline import run_pipeline, translate_plan

logger = logging.getLogger("discharge-decoded")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app = FastAPI(
    title="Discharge Decoded",
    description="Turn hospital discharge papers into a plain-language care plan. "
                "Ships its own OpenAI-compatible AI gateway.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Gateway routes -- OpenAI-compatible shapes, served by this app
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[dict] = Field(default_factory=list)
    temperature: float = 0.2
    max_tokens: int = 2000
    response_format: dict | None = None


class EmbeddingsRequest(BaseModel):
    model: str | None = None
    input: str | list[str] = ""


@app.get("/v1/models")
def v1_models():
    return gateway.list_models()


@app.post("/v1/chat/completions")
def v1_chat_completions(req: ChatRequest):
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")
    return gateway.dispatch_chat(
        req.messages, model=req.model, temperature=req.temperature,
        max_tokens=req.max_tokens, response_format=req.response_format)


@app.post("/v1/embeddings")
def v1_embeddings(req: EmbeddingsRequest):
    return gateway.dispatch_embeddings(req.input, model=req.model)


# ---------------------------------------------------------------------------
# Product API
# ---------------------------------------------------------------------------

class CarePlanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class TranslateRequest(BaseModel):
    plan: str = Field(min_length=1, max_length=200_000)
    target: str = "es"


@app.get("/healthz")
def healthz():
    return {"status": "ok", "backend": gateway.get_backend(),
            "ocr": "tesseract" if ocr.tesseract_available() else "paste-text-only"}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 10 MB)")
    kind = ocr.sniff_kind(file.filename or "", file.content_type or "")
    try:
        if kind == "pdf":
            text, method = ocr.extract_text_from_pdf(data)
        else:
            text, method = ocr.extract_text_from_image(data)
    except ocr.OCRUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    redacted, report = safety.redact_phi(text)
    logger.info("upload processed",
                extra={"method": method, "phi_kinds": sorted(report.keys())})
    return {
        "text": redacted,
        "method": method,
        "redaction_report": report,
        "chars": len(redacted),
    }


@app.post("/api/care-plan")
def api_care_plan(req: CarePlanRequest):
    redacted, report = safety.redact_phi(req.text)
    try:
        result = run_pipeline(redacted)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    logger.info("care plan generated",
                extra={"phi_kinds": sorted(report.keys()),
                       "meds": len(result["structured"].get("medications", []))})
    return {
        "plan": result["plan"],
        "structured": result["structured"],
        "redaction_report": report,
    }


@app.post("/api/translate")
def api_translate(req: TranslateRequest):
    if req.target != "es":
        raise HTTPException(status_code=400,
                            detail="only 'es' (Spanish) is supported in v0.1.0")
    try:
        translated = translate_plan(req.plan, req.target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"translated_plan": translated, "target": req.target}


# ---------------------------------------------------------------------------
# Static demo UI
# ---------------------------------------------------------------------------

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
