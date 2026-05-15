---
name: CData MCP Authentication
description: How to authenticate to the CData MCP server for the inventory forecaster — Basic auth with email:PAT, not Bearer token
type: reference
originSessionId: 5fd14678-40d0-43a0-be74-b35f8597e7d0
---
CData MCP endpoint: `https://mcp.cloud.cdata.com/mcp`

**Auth format:** `Authorization: Basic base64(email:PAT)`
- Email: `steven@skaffles.com`
- PAT: **NEVER expires** — do not suggest refreshing it.

**Session prerequisite (added 2026-04-29):** CData now requires `getInstructions(driverName="Quickbase1")` to be called once per session before any `queryData` call. Without it, `queryData` returns `IncompleteRead(0 bytes read)`. The forecaster's `cdata_query()` lazily primes via `_prime_cdata()` on first use. If you write a new script hitting `mcp.cloud.cdata.com` directly, do the same.

**Why not Bearer:** The Anthropic API's `authorization_token` field sends `Authorization: Bearer <token>`, which CData rejects ("Unsupported Authorization Type"). CData requires Basic auth for PATs.

**Response format:** CData returns columnar JSON — `{"results": [{"schema": [...], "rows": [[...], ...]}]}` — not a list of dicts. The script's `_parse_cdata_result()` converts this to list-of-dicts.

**No Anthropic SDK needed:** The forecaster script (`inventory-forecaster-cc`) connects directly to CData via `urllib` — no `ANTHROPIC_API_KEY` required.
