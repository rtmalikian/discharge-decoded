# Changelog

## Unreleased — 2026-10-30

- Added open.epic MCP documentation integration: `app/epic_mcp.py`
  client for Epic's documentation/spec-lookup MCP server
  (`https://mcp.open.epic.com`, tools `search_documents` and
  `get_document`), wired to `GET /epic-mcp/tools`,
  `POST /epic-mcp/docs`, and `GET /epic-mcp/documentreference-spec`
  for the roadmap EHR-ingestion path. The server returns
  documentation only — it is not a patient-data server — and an
  in-code no-PHI guard refuses PHI-like queries. Includes
  `EPIC_MCP.md` and `epic-mcp.mcp.json.example`.
