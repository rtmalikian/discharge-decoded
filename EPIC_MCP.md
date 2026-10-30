# open.epic MCP integration — Discharge Decoded

Discharge Decoded integrates with Epic's **open.epic MCP server** as a
**documentation / specification-lookup integration only**. It helps the
developers building this project's roadmap EHR-ingestion path retrieve
Epic's own API specifications and integration guidance — in particular
the Epic **DocumentReference** API spec for discharge-packet ingestion.

Source: https://fhir.epic.com/documentation?docid=mcp

## What the server is — and is not

- **Server:** `open.epic MCP` (live-verified 2026-10-30, version 4.0.9)
- **URL:** `https://mcp.open.epic.com`
- **Transport:** Streamable HTTP (MCP over JSON-RPC 2.0, protocol
  version `2025-03-26`)
- **Content categories:** API specifications, interface summaries,
  additional documentation (data-sharing playbooks and tutorials),
  and FAQs — including Epic on FHIR content.
- **It is NOT a patient-data / FHIR-resource server.** It cannot query
  Patient, Observation, or Encounter resources, it has no SMART/OAuth
  flow, and it takes no credentials. Patient data in this project
  flows ONLY through the existing FHIR R4 client patterns and SMART
  on FHIR — never through this MCP server. Nothing in this integration
  changes how discharge documents themselves are processed.

## Live-verified tools

Tools are discovered at runtime via MCP `tools/list`. Live-verified
2026-10-30, the server exposes exactly two tools:

- `search_documents` — keyword/phrase search across Epic
  documentation; arguments `{query: string}` (max 500 chars). Returns
  documents with id, url, title, and snippet. Prefer R4 APIs. Use
  `get_document` with a returned id for full text.
- `get_document` — full contents of one document; arguments
  `{document_id: string}` (format `api:x`, `tutorial:x`, `faq:x`,
  `interface:x:x`), with optional `offset` for pagination in
  20,000-character pages.

## The no-PHI rule (enforced in code)

Epic states, in substance, that the open.epic MCP server **is not
designated to handle protected health information (PHI) or other
confidential information**, and that the user is responsible for
anything transmitted to it. Use is subject to the open.epic Terms
of Use.

`app/epic_mcp.py` enforces that rule in code, not just in this
document: before any `tools/call`, every argument string is screened
by the no-PHI guard and the call is **refused** (and the refusal
logged, rule names only) if it contains anything that looks like PHI
or patient data — FHIR resource JSON (`"resourceType"`),
`Patient/<id>` references, MRN/SSN-like patterns, or date-of-birth
phrasing. Only documentation-style queries are allowed: API names,
resource *type* names (e.g. `DocumentReference`), and spec topics.

## How Discharge Decoded uses it

Module: `app/epic_mcp.py` (`EpicMcpClient`, dependency-light on
`httpx`; the `mcp` SDK is not a dependency). It handles the
`initialize` handshake, captures and echoes the `Mcp-Session-Id`
header when the server returns one, parses both `application/json`
and `text/event-stream` (SSE-framed) responses, and degrades
gracefully — public lookup methods return `{"available": False, ...}`
instead of raising when the server is disabled or unreachable, so
the rest of the app works fully without it. The only exception raised
is `PhiGuardError` for a refused query.

Wiring in `app/main.py` (the roadmap EHR-ingestion path):

| Endpoint | Description |
|---|---|
| `GET /epic-mcp/tools` | List the MCP server's tools (`search_documents`, `get_document`) |
| `POST /epic-mcp/docs` | `{query}` → search Epic documentation (no-PHI guard; PHI-like queries get HTTP 400) |
| `GET /epic-mcp/documentreference-spec` | Fetch the Epic DocumentReference API spec for discharge-packet ingestion |

## Environment configuration

- `EPIC_MCP_URL` — default `https://mcp.open.epic.com`
- `EPIC_MCP_ENABLED` — default true; set to `0` / `false` / `no` to
  disable the documentation lookup. Timeout is 15 seconds.

## Setup for AI clients

`epic-mcp.mcp.json.example` at the repo root contains exactly:

```json
{
  "mcpServers": {
    "open-epic-mcp": {
      "url": "https://mcp.open.epic.com",
      "transport": "streamable-http"
    }
  }
}
```

Which clients read which filename: **Claude Code** reads `.mcp.json`
at the project root; **VS Code** reads `.vscode/mcp.json` (same
`mcpServers` shape, under its servers configuration); **Cursor**
reads `.cursor/mcp.json` in the project (or `~/.cursor/mcp.json`
globally). Copy the example to the filename your client reads and
adjust there — the checked-in `.example` file is an example to copy,
not a forced config, and no client is required to use this app.
