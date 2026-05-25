# Global Claude Instructions

## Quickbase / CData Rules (enforced every session)

Full rulebook: `C:\Users\steven\Desktop\Dropbox (Personal)\Working Docs\reference_quickbase_api_rules.md`

### Before any CData/QB call
- First call of every session MUST be `getInstructions("QuickBase")` — no exceptions
- Pre-declare call budget. If >10 CData calls projected, stop and propose a narrower scope before proceeding
- Smoke-test with TOP 1 before any bulk query

### Throttle protocol (shared realm — 80 users)
- Throttle signals: MCP connection lost, 0-byte, IncompleteRead, 429, 502, 504, "technical difficulties"
- TWO failures on any query → STOP all CData calls (including metadata) for 15 minutes minimum. Tell the user. Wait for explicit "resume" before any further QB call
- Backoff: 2s, 4s, 8s — max 3 retries with real elapsed delay between each
- Never say "Please wait - Retrying Connection" without elapsed seconds — that phrase is SP-API only

### Query discipline
- Never SELECT * — always project only the columns needed
- Always filter server-side (WHERE clause), never pull rows to filter in Python
- Cache field maps and schema for the lifetime of a run — never fetch on the hot path
- Wide column queries (100+ fields) are expensive; split or cache when possible
- Writes: bulk upsert via `POST /records` with `mergeFieldId`, 500-1000 records/batch
- Sustained write rate <= 10 req/s; default to 5 req/s if unsure

### Required filters (always apply, never omit)
- `Sales_Budgets`: always include `[YYYY_numeric_]={year}` AND `[Active_BV_2]=1`
- `Projections`: always include `[Status_Cust] LIKE 'A%'` or `LIKE 'FD%'`

### Anti-patterns (never do these)
- Per-record loops (`for r in records: edit(r)`) — use bulk upsert
- Fetching all rows then filtering in code
- Running heavy jobs during business hours — schedule for 02:00-05:00 local
- Retrying forever without exponential backoff and a max budget
