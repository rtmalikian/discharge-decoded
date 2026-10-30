"""Mock-only tests for the open.epic MCP documentation client.

No live network: every test runs against an httpx MockTransport fake
MCP server, a refusing transport, or the disabled configuration.
"""

import asyncio
import json

import httpx
import pytest

from app.epic_mcp import EpicMcpClient, PhiGuardError, screen_for_phi

SESSION_ID = "fake-session-123"

TOOLS = [
    {
        "name": "search_documents",
        "description": "Keyword/phrase search across Epic documentation.",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "get_document",
        "description": "Full contents of one document by id.",
        "inputSchema": {"type": "object",
                        "properties": {"document_id": {"type": "string"}}},
    },
]

SEARCH_RESULT = [{
    "id": "api:9999",
    "title": "DocumentReference.Search (R4)",
    "url": "https://example.invalid/doc",
    "snippet": "Search DocumentReference resources.",
}]


class FakeMcpServer:
    """Stateful fake of the open.epic MCP server (Streamable HTTP)."""

    def __init__(self, sse_for_call=False):
        self.sse_for_call = sse_for_call
        self.requests = []          # parsed JSON-RPC payloads, in order
        self.session_headers = []   # Mcp-Session-Id seen per request

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.session_headers.append(request.headers.get("mcp-session-id"))
        raw = request.content
        payload = json.loads(raw) if raw else {}
        if payload:
            self.requests.append(payload)
        method = payload.get("method")

        if method == "initialize":
            body = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "open.epic MCP", "version": "4.0.9"},
                "capabilities": {},
            }}
            return httpx.Response(
                200, json=body, headers={"Mcp-Session-Id": SESSION_ID})
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            body = {"jsonrpc": "2.0", "id": payload.get("id"),
                    "result": {"tools": TOOLS}}
            return httpx.Response(200, json=body)
        if method == "tools/call":
            name = payload["params"]["name"]
            if name == "search_documents":
                result = {
                    "isError": False,
                    "content": [{"type": "text",
                                 "text": json.dumps(SEARCH_RESULT)}],
                    "structuredContent": {"result": SEARCH_RESULT},
                }
            else:
                result = {
                    "isError": False,
                    "content": [{"type": "text",
                                 "text": "Full DocumentReference spec text."}],
                }
            body = {"jsonrpc": "2.0", "id": payload.get("id"),
                    "result": result}
            if self.sse_for_call:
                return httpx.Response(
                    200,
                    content=f"event: message\ndata: {json.dumps(body)}\n\n",
                    headers={"Content-Type": "text/event-stream"})
            return httpx.Response(200, json=body)
        return httpx.Response(400, json={"error": "unknown method"})

    def methods(self):
        return [p.get("method") for p in self.requests]


def make_client(fake, **kwargs):
    return EpicMcpClient(transport=httpx.MockTransport(fake), **kwargs)


def test_initialize_handshake_session_echo_and_tools_list():
    fake = FakeMcpServer()
    out = asyncio.run(make_client(fake).list_tools())

    assert out["available"] is True
    assert [t["name"] for t in out["tools"]] == [
        "search_documents", "get_document"]
    assert out["server_info"] == {"name": "open.epic MCP",
                                  "version": "4.0.9"}
    # Handshake happened first, with this repo's client identity.
    assert fake.methods()[0] == "initialize"
    init_params = fake.requests[0]["params"]
    assert init_params["protocolVersion"] == "2025-03-26"
    assert init_params["clientInfo"] == {"name": "discharge-decoded",
                                         "version": "0.1.0"}
    assert "notifications/initialized" in fake.methods()
    # Session id returned by initialize is echoed on later requests.
    assert fake.session_headers[0] is None
    assert SESSION_ID in fake.session_headers[1:]


def test_tools_call_json_response_search_docs():
    fake = FakeMcpServer()
    out = asyncio.run(make_client(fake).search_docs(
        "DocumentReference FHIR R4"))
    assert out["available"] is True
    assert out["results"] == SEARCH_RESULT
    assert "tools/call" in fake.methods()


def test_tools_call_sse_response():
    fake = FakeMcpServer(sse_for_call=True)
    client = make_client(fake)
    out = asyncio.run(client.call_tool(
        "get_document", {"document_id": "api:9999"}))
    assert out["available"] is True
    assert out["is_error"] is False
    assert out["content_texts"] == ["Full DocumentReference spec text."]


def test_get_document_parses_text_content():
    fake = FakeMcpServer()
    out = asyncio.run(make_client(fake).get_document("api:9999"))
    assert out["available"] is True
    assert out["document_id"] == "api:9999"
    assert out["content_texts"] == ["Full DocumentReference spec text."]


def test_no_phi_guard_refuses_patient_reference():
    fake = FakeMcpServer()
    client = make_client(fake)
    with pytest.raises(PhiGuardError):
        asyncio.run(client.search_docs("discharge for Patient/abc-123"))
    # Nothing was transmitted.
    assert fake.requests == []


def test_no_phi_guard_refuses_resource_json():
    fake = FakeMcpServer()
    client = make_client(fake)
    payload = json.dumps({"resourceType": "Patient", "id": "abc-123"})
    assert "fhir-resource-json" in screen_for_phi(payload)
    with pytest.raises(PhiGuardError):
        asyncio.run(client.call_tool(
            "search_documents", {"query": payload}))
    assert fake.requests == []


def test_no_phi_guard_refuses_via_endpoint(client):
    r = client.post("/epic-mcp/docs",
                    json={"query": "Patient/abc-123 discharge summary"})
    assert r.status_code == 400
    assert "no-PHI guard" in r.json()["detail"]


def test_graceful_degradation_when_unreachable():
    def refusing(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = EpicMcpClient(transport=httpx.MockTransport(refusing))
    tools = asyncio.run(client.list_tools())
    assert tools["available"] is False and tools["tools"] == []
    docs = asyncio.run(client.search_docs("DocumentReference FHIR R4"))
    assert docs["available"] is False and docs["results"] == []


def test_disabled_client_degrades_without_network():
    fake = FakeMcpServer()
    client = make_client(fake, enabled=False)
    out = asyncio.run(client.list_tools())
    assert out["available"] is False and out["tools"] == []
    assert fake.requests == []


def test_epic_mcp_tools_endpoint_when_disabled(client, monkeypatch):
    monkeypatch.setenv("EPIC_MCP_ENABLED", "0")
    r = client.get("/epic-mcp/tools")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False and data["tools"] == []
