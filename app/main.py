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
from app.epic_mcp import EpicMcpClient, PhiGuardError
from app.discharge.pipeline import run_pipeline, translate_plan
from app.discharge.teachback import check_answer, critical_items, summarize

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


class TeachBackStartRequest(BaseModel):
    structured: dict


class TeachBackAnswerRequest(BaseModel):
    item: dict
    answer: str = Field(min_length=1, max_length=5_000)


class TeachBackCompleteRequest(BaseModel):
    total_items: int = Field(ge=0)
    covered_items: int = Field(ge=0)


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
                       "meds": len(result["structured"].get("medications", [])),
                       "fk_grade": result["readability"]["grade"],
                       "readability_gate": result["readability"]["meets_gate"],
                       "verified_dropped": len(result["verification_report"]["dropped"]),
                       "verified_flagged": len(result["verification_report"]["flagged"])})
    return {
        "plan": result["plan"],
        "structured": result["structured"],
        "redaction_report": report,
        "verification_report": result["verification_report"],
        "readability": result["readability"],
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


@app.post("/api/teach-back/start")
def api_teachback_start(req: TeachBackStartRequest):
    items = critical_items(req.structured or {})
    # Key points stay server-side per answer call; the client only needs
    # prompts. (Sent here for the demo UI; a hardened deploy would keep them.)
    return {"items": items, "total": len(items)}


@app.post("/api/teach-back/answer")
def api_teachback_answer(req: TeachBackAnswerRequest):
    if not req.item.get("key_points"):
        raise HTTPException(status_code=400, detail="item has no key points")
    return check_answer(req.item, req.answer)


@app.post("/api/teach-back/complete")
def api_teachback_complete(req: TeachBackCompleteRequest):
    summary = summarize(req.total_items, req.covered_items)
    # First-class metric, kinds/counts only -- never patient wording.
    logger.info("teachback_complete", extra={
        "teachback_completed": summary["teachback_completed"],
        "items_total": summary["items_total"],
        "items_covered": summary["items_covered"],
        "coverage": summary["coverage"],
    })
    return summary


# ---------------------------------------------------------------------------
# open.epic MCP -- documentation/spec lookup only (see EPIC_MCP.md)
#
# The open.epic MCP server is NOT a patient-data server: it returns Epic
# API specifications and integration guidance only. Patient data never
# flows through it. This wiring serves the roadmap EHR-ingestion path:
# fetching the Epic DocumentReference API spec that a future FHIR
# discharge-packet ingestion feature would be built against.
# ---------------------------------------------------------------------------

class EpicMcpDocsRequest(BaseModel):
    query: str = Field(default="DocumentReference FHIR R4",
                       min_length=1, max_length=500)


@app.get("/epic-mcp/tools")
async def epic_mcp_tools():
    return await EpicMcpClient().list_tools()


@app.post("/epic-mcp/docs")
async def epic_mcp_docs(req: EpicMcpDocsRequest):
    try:
        return await EpicMcpClient().search_docs(req.query)
    except PhiGuardError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/epic-mcp/documentreference-spec")
async def epic_mcp_documentreference_spec():
    """Fetch the Epic DocumentReference spec for discharge-packet ingestion."""
    try:
        return await EpicMcpClient().search_docs(
            "DocumentReference FHIR R4 discharge summary")
    except PhiGuardError as exc:  # pragma: no cover - fixed query is clean
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Static demo UI
# ---------------------------------------------------------------------------

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
