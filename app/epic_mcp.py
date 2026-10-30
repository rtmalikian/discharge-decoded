"""open.epic MCP server client — Epic *documentation* lookup only.

Epic publishes a remote Model Context Protocol (MCP) server at
https://mcp.open.epic.com (Streamable HTTP transport). Verified live
2026-10-30: serverInfo name "open.epic MCP", version "4.0.9", protocol
version "2025-03-26". Source documentation:
https://fhir.epic.com/documentation?docid=mcp

WHAT THIS SERVER IS: a documentation/specification lookup service. Its
tools (discovered at runtime via ``tools/list``; live-verified
2026-10-30) are:

- ``search_documents`` — keyword/phrase search across Epic's
  interoperability documentation (API specifications, interface
  summaries, data-sharing playbooks/tutorials, FAQs, Epic on FHIR
  content). Returns document ids, titles, urls and snippets.
- ``get_document`` — full contents of one document by id
  (``api:x``, ``tutorial:x``, ``faq:x``, ``interface:x:x``).

WHAT IT IS NOT: it is NOT a patient-data or FHIR-resource server. It
cannot query Patient/Observation/Encounter resources, it has no
SMART/OAuth flow and takes no credentials. Patient data in this project
flows ONLY through the FHIR R4 client + SMART on FHIR — never through
this MCP server.

NO-PHI GUARD (enforced here, in code): Epic states the server "is not
designated to handle protected health information (PHI) or other
confidential information" and that the user is responsible for anything
transmitted to it. Accordingly, every ``tools/call`` argument is
screened before anything is sent; a query that looks like PHI /
patient data (FHIR resource JSON, ``Patient/<id>`` references,
MRN/SSN-like patterns, date-of-birth phrasing) is REFUSED and the
refusal is logged. Only documentation-style queries (API names,
resource *type* names, spec topics) are allowed through.

Graceful degradation: the integration is disabled by
``EPIC_MCP_ENABLED=0`` and every public method returns an
``available: False`` result instead of raising when the server is
unreachable, so the rest of the application works fully without it.
The only exception raised is :class:`PhiGuardError`, which signals a
refused query, not a network problem.

Config (environment):
- ``EPIC_MCP_URL`` — default ``https://mcp.open.epic.com``
- ``EPIC_MCP_ENABLED`` — default true; ``0``/``false``/``no`` disables

Dependency-light on purpose: plain httpx (already a project
dependency); the ``mcp`` SDK is NOT required.
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

log = logging.getLogger(__name__)

DEFAULT_URL = "https://mcp.open.epic.com"
PROTOCOL_VERSION = "2025-03-26"
CLIENT_NAME = "discharge-decoded"
CLIENT_VERSION = "0.1.0"
DEFAULT_TIMEOUT = 15.0


class EpicMcpError(Exception):
    """Transport/protocol failure talking to the open.epic MCP server."""


class PhiGuardError(ValueError):
    """A query was refused by the no-PHI guard; nothing was transmitted."""


# ------------------------------- no-PHI guard -------------------------------
# Rule names are safe to log; the matched text itself never is.
_PHI_RULES: list[tuple[str, re.Pattern]] = [
    ("fhir-resource-json", re.compile(r'"\s*resourceType\s*"', re.I)),
    ("patient-reference", re.compile(r"\bpatient\s*/\s*[A-Za-z0-9][A-Za-z0-9\-._]*", re.I)),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("ssn-or-mrn-number", re.compile(r"\b\d{9}\b")),
    ("mrn-label", re.compile(r"\b(mrn|medical record number)\b\s*[:#]?\s*\d", re.I)),
    ("dob-label", re.compile(r"\b(dob|date of birth|born on)\b", re.I)),
]


def screen_for_phi(text: str) -> list[str]:
    """Return the names of any no-PHI rules ``text`` trips (empty = clean)."""
    if not text:
        return []
    return [name for name, pat in _PHI_RULES if pat.search(text)]


def assert_no_phi(text: str, field: str = "query") -> None:
    """Raise PhiGuardError if ``text`` looks like PHI / patient data."""
    hits = screen_for_phi(text)
    if hits:
        # Log rule names only — never the screened text.
        log.warning("epic-mcp no-PHI guard refused %s (rules: %s)",
                    field, ", ".join(hits))
        raise PhiGuardError(
            f"{field} refused by the open.epic MCP no-PHI guard "
            f"(matched: {', '.join(hits)}). The open.epic MCP server is a "
            "documentation service that is not designated to handle PHI; "
            "send documentation-style queries only (API names, resource "
            "type names, spec topics)."
        )


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


# ------------------------------- wire helpers -------------------------------

def _parse_rpc_body(resp: httpx.Response) -> dict:
    """Parse a JSON-RPC response in either JSON or SSE-framed form."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        last = None
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        last = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
        if last is None:
            raise EpicMcpError("SSE response contained no JSON-RPC message")
        return last
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise EpicMcpError(f"non-JSON response from MCP server: {exc}") from exc


# --------------------------------- client -----------------------------------

class EpicMcpClient:
    """Minimal JSON-RPC 2.0 / Streamable HTTP client for open.epic MCP."""

    def __init__(self, base_url: str | None = None, enabled: bool | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 transport: httpx.AsyncBaseTransport | None = None,
                 client_name: str = CLIENT_NAME,
                 client_version: str = CLIENT_VERSION):
        self.base_url = (base_url or os.environ.get("EPIC_MCP_URL")
                         or DEFAULT_URL).rstrip("/")
        if enabled is None:
            raw = os.environ.get("EPIC_MCP_ENABLED", "1").strip().lower()
            enabled = raw not in ("0", "false", "no", "off")
        self.enabled = enabled
        self.timeout = timeout
        self.transport = transport
        self.client_name = client_name
        self.client_version = client_version
        self._session_id: str | None = None
        self._initialized = False
        self._rpc_id = 0

    # -- low level -----------------------------------------------------------
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    async def _post(self, client: httpx.AsyncClient, payload: dict,
                    expect_response: bool = True) -> dict | None:
        try:
            resp = await client.post(self.base_url, headers=self._headers(),
                                     json=payload)
        except httpx.HTTPError as exc:
            raise EpicMcpError(f"MCP server unreachable: {exc}") from exc
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if resp.status_code >= 400:
            raise EpicMcpError(
                f"MCP server returned HTTP {resp.status_code}")
        if not expect_response:
            return None
        body = _parse_rpc_body(resp)
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            raise EpicMcpError(
                f"MCP error {err.get('code')}: {err.get('message')}")
        return body

    async def _ensure_initialized(self, client: httpx.AsyncClient) -> None:
        if self._initialized:
            return
        self._rpc_id += 1
        body = await self._post(client, {
            "jsonrpc": "2.0", "id": self._rpc_id, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name,
                               "version": self.client_version},
            }})
        result = (body or {}).get("result") or {}
        self.server_info = result.get("serverInfo", {})
        await self._post(client, {"jsonrpc": "2.0",
                                  "method": "notifications/initialized"},
                         expect_response=False)
        self._initialized = True

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(transport=self.transport,
                                     timeout=self.timeout,
                                     follow_redirects=True) as client:
            await self._ensure_initialized(client)
            self._rpc_id += 1
            body = await self._post(client, {
                "jsonrpc": "2.0", "id": self._rpc_id, "method": method,
                "params": params or {}})
            return (body or {}).get("result") or {}

    # -- public API ------------------------------------------------------------
    def _unavailable(self, reason: str) -> dict:
        return {"available": False, "reason": reason, "server": self.base_url}

    async def list_tools(self) -> dict:
        """Return {"available", "tools": [{name, description, input_schema}]}.

        Never raises for network problems — returns available=False.
        """
        if not self.enabled:
            return {**self._unavailable("disabled via EPIC_MCP_ENABLED"),
                    "tools": []}
        try:
            result = await self._rpc("tools/list")
        except EpicMcpError as exc:
            log.warning("epic-mcp tools/list unavailable: %s", exc)
            return {**self._unavailable(str(exc)), "tools": []}
        tools = [{"name": t.get("name", ""),
                  "description": t.get("description", ""),
                  "input_schema": t.get("inputSchema", {})}
                 for t in result.get("tools", [])]
        return {"available": True, "server": self.base_url,
                "server_info": getattr(self, "server_info", {}),
                "tools": tools}

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool after screening every argument string for PHI.

        Raises PhiGuardError on a refused query. Network failures raise
        EpicMcpError; callers that need degradation should use
        search_docs()/get_document(), which convert failures to
        available=False results.
        """
        arguments = arguments or {}
        for value in _iter_strings(arguments):
            assert_no_phi(value, field=f"argument for tool '{name}'")
        result = await self._rpc("tools/call", {"name": name,
                                                "arguments": arguments})
        texts: list[str] = []
        for block in result.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return {"available": True, "tool": name,
                "is_error": bool(result.get("isError", False)),
                "content_texts": texts,
                "structured": result.get("structuredContent")}

    async def search_docs(self, query: str) -> dict:
        """Search Epic's documentation (the server's search tool).

        The query is screened by the no-PHI guard (PhiGuardError
        propagates). Network problems degrade to available=False.
        """
        assert_no_phi(query)
        if not self.enabled:
            return {**self._unavailable("disabled via EPIC_MCP_ENABLED"),
                    "query": query, "results": []}
        try:
            listed = await self.list_tools()
            if not listed["available"]:
                return {**listed, "query": query, "results": []}
            tool = next((t["name"] for t in listed["tools"]
                         if "search" in t["name"]), "search_documents")
            out = await self.call_tool(tool, {"query": query})
        except EpicMcpError as exc:
            log.warning("epic-mcp search unavailable: %s", exc)
            return {**self._unavailable(str(exc)), "query": query,
                    "results": []}
        results: list = []
        if out.get("structured") and isinstance(out["structured"].get("result"), list):
            results = out["structured"]["result"]
        else:
            for text in out["content_texts"]:
                try:
                    parsed = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(parsed, list):
                    results = parsed
                    break
        return {"available": True, "server": self.base_url, "query": query,
                "results": results, "content_texts": out["content_texts"]}

    async def get_document(self, document_id: str) -> dict:
        """Fetch one documentation document by id (guard + degradation)."""
        assert_no_phi(document_id, field="document_id")
        if not self.enabled:
            return {**self._unavailable("disabled via EPIC_MCP_ENABLED"),
                    "document_id": document_id, "content_texts": []}
        try:
            listed = await self.list_tools()
            if not listed["available"]:
                return {**listed, "document_id": document_id,
                        "content_texts": []}
            tool = next((t["name"] for t in listed["tools"]
                         if "document" in t["name"] and "search" not in t["name"]),
                        "get_document")
            out = await self.call_tool(tool, {"document_id": document_id})
        except EpicMcpError as exc:
            log.warning("epic-mcp get_document unavailable: %s", exc)
            return {**self._unavailable(str(exc)),
                    "document_id": document_id, "content_texts": []}
        return {"available": True, "server": self.base_url,
                "document_id": document_id,
                "content_texts": out["content_texts"],
                "structured": out.get("structured")}
