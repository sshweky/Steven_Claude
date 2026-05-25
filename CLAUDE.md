# Global Claude Instructions

## Quickbase / CData Rules (enforced every session)

Treat QB as a precious shared resource, not a database. Realm rate limits are shared across 80 users — degraded performance or outages affect everyone.

---

### Session start (non-negotiable)
- First CData call every session MUST be `getInstructions("QuickBase")` — no exceptions
- Pre-declare call budget before starting any QB work. If >10 CData calls projected, stop, propose a narrower scope, and wait for approval
- Smoke-test with TOP 1 before any bulk query — if it fails, abort before launching heavy work

---

### Throttle protocol (shared realm — 80 users, failure = company outage)

Throttle signals: `MCP server connection lost`, 0-byte response, `IncompleteRead`, 429, 502, 504, `QuickBase is experiencing technical difficulties`

- TWO failures on any query → STOP all CData calls (including metadata) for 15 minutes minimum. Tell the user explicitly. Wait for explicit "resume" before any further QB call
- Backoff = real wall-clock delay: 2s, 4s, 8s — max 3 retries. Never fake-wait
- "Please wait - Retrying Connection" is SP-API only — never use that phrase for CData

---

### Required filters (always apply, never omit)

- `Sales_Budgets`: always `[YYYY_numeric_]={year}` AND `[Active_BV_2]=1`. BV `0326`, OG `O-26`
- `Projections`: always `[Status_Cust] LIKE 'A%'` or `[Status_Cust] LIKE 'FD%'`. Order history: `Ord_LW` through `Ord_LW_51`

---

### Read patterns

- **Never SELECT *** — always project only the columns you will use. Each extra column slows QB row reconstruction
- **Always filter server-side** — a WHERE clause is cheaper than pulling 10x the rows and filtering in Python
- **Cache the field map** — `GET /fields?tableId=<dbid>` once at startup, reuse for the entire run. Never look up field IDs on the hot path
- **Use saved reports when available** — QB caches report results internally; prefer saved report IDs over custom queries
- **Paginate with top + skip** — QB caps at ~10,000 rows per call regardless of `top`
- Wide column queries (100+ fields) are expensive — split into narrower fetches or cache results

```sql
-- Bad: pulls ~200 fields x 4500 rows
SELECT * FROM Projections

-- Good: project + filter at QB
SELECT [Acct_MStyle_Key_], [AI_PRJ_W1] FROM Projections
WHERE [Status_Cust] LIKE 'A%' AND [Mstyle] LIKE 'FF%'
```

---

### Write patterns

- **Bulk upsert via `POST /records` with `mergeFieldId`** — 500-1000 records per batch
- `fieldsToReturn: []` — skip echoing updated rows to save bandwidth
- **Never `API_EditRecord` in a loop** — per-record calls are the #1 cause of throttling. 4,500 records = 9 bulk calls vs 4,500 per-record calls (500x difference)
- **Sustained write rate <= 10 req/s** — default to 5 req/s if unsure. Burst <= 25 req/s
- **Idempotent writes only** — design so re-running produces the same final state

```json
POST https://api.quickbase.com/v1/records
{
  "to": "<dbid>",
  "data": [{ "<fid>": {"value": <val>} }],
  "mergeFieldId": <fid_of_unique_key>,
  "fieldsToReturn": []
}
```

---

### Retry and resilience

Treat 429, 502, 504, `IncompleteRead(0 bytes)`, and empty 200 OK the same — all are throttle signals.

```python
for attempt in range(1, 4):
    try:
        return qb_call(...)
    except (TimeoutError, IncompleteRead, HTTPError):
        if attempt == 3:
            raise
        if "IncompleteRead" in str(e) or "0 bytes" in str(e):
            invalidate_session()
        time.sleep(2 ** attempt)  # 2s, 4s, 8s
```

- Max 3 retries, total backoff budget ~14 seconds
- If retries fail: abort cleanly, tell the user, do not loop forever
- Abort on empty when data was expected — 0 rows when rows were expected is a failure, not success

---

### Schema discovery

- `GET /tables?appId=<appId>` — list tables
- `GET /fields?tableId=<dbid>` — full field definitions
- Run at startup only, cache for the whole run. Never call on the hot path
- Don't list all apps to find one table — hit the known app ID directly

---

### Connection layer preference

Direct REST API (`api.quickbase.com/v1`) > CData > JSON-RPC

- **Direct REST**: fastest, modern JSON, bulk endpoints, server-side WHERE filtering — default for all production code
- **CData**: adds 100-500ms per call plus its own rate-limit layer. **CRITICAL: CData does NOT push WHERE clauses to QB -- it fetches the entire table and filters client-side.** A 1-record CData query on Projections (4,500 rows x 250 cols) hits QB identically to a full table scan. Use CData only for: session prime (`getInstructions`), metadata queries, and small tables (<100 rows). Never use CData for the Projections table or any large-table narrow-scope query.
- **JSON-RPC** (`pim.quickbase.com`): legacy, slow, per-record — only when REST doesn't support the operation

**CData full-scan anti-pattern (never do this for large tables):**
```sql
-- Looks like a narrow query -- actually fetches 4,500 rows x 250 cols from QB
SELECT [col1] FROM Projections WHERE [Mstyle] = 'FF15592'
```
**Use QB REST API instead** (`POST /v1/records/query` with `where` and explicit `select` FIDs) -- QB filters server-side, returns exactly 1 row.

**Field label normalization** -- CData converts QB labels to SQL column names by replacing all non-alphanumeric characters with a single underscore:
```python
cdata_name = re.sub(r'[^a-zA-Z0-9]+', '_', qb_label)
# "Status @ Cust" -> "Status_Cust", "Ord/LW 51" -> "Ord_LW_51"
```

---

### Concurrency

- Default 2 parallel workers for wide scope (all active records)
- Default 6 for narrow scope
- Never exceed 10 without measuring behavior under load

---

### Observability

- Log each API call's latency — when it staircases (200ms -> 1s -> 5s), back off before QB cuts you off
- Persist completed keys to disk every 50-100 records so jobs are resumable after throttle/crash
- Check QB Realm Admin > API Usage — alert personally when hitting >70% of realm cap

---

### Operational hygiene

- Heavy jobs run off-hours: 02:00-05:00 local
- Dedicated bot users — each script gets its own QB account/token to isolate quota
- Tag User-Agent: `petspeople-<script-name>/1.x` for identifiable audit trails
- Don't poll — use webhooks or scheduled batches, not "check every 30 seconds"

---

### Anti-patterns (never do these)

- `for r in records: edit(r)` — per-record loop, the single biggest cause of throttling
- `SELECT *` — pulls every field, slow at QB and on the wire
- Filtering rows in Python after pulling everything
- Looking up field IDs by label on every request
- Running heavy jobs during business hours
- Retrying forever without exponential backoff or a max budget
- Treating 0-byte timeouts as "no data" — they are throttle signals
- Sharing one QB user/token across multiple scripts
- Skipping the smoke test

---

### Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| Latency staircases 200ms -> 5s -> 30s | Approaching throttle | Pause + backoff |
| `IncompleteRead(0 bytes)` | QB cut connection (throttled) | Retry with backoff + re-prime session |
| Timeout on `/db/<id>?a=API_DoQuery` | Per-table rate limit | Wait 5-15 min, switch to bulk |
| 429 Too Many Requests | Hard throttle | Honor `Retry-After` header |
| Empty 200 OK | Silent throttle | Treat as failure, do not proceed |
| QB "technical difficulties" error | QB-side stress or throttle | Count as failure, apply backoff |

---

### Pre-flight checklist (before any new QB script)

- [ ] Field-id map cached at startup?
- [ ] Projecting only needed columns?
- [ ] Filtering server-side with WHERE?
- [ ] Writes batched via `POST /records` with `mergeFieldId`?
- [ ] Smoke test before heavy work?
- [ ] Retry policy: exponential backoff, max 3 retries?
- [ ] Completed keys persisted so job is resumable?
- [ ] Concurrency capped (<=2 wide, <=6 narrow)?
- [ ] Write rate <= 10 req/s?
- [ ] Running off-hours, or scaled way back for business hours?

---

## Owned Brands

Never recommend SKU reduction, distribution cuts, or rationalization for A&H, Burt's Bees, BioSilk, CHI, Vibrant Life, Glad for Pets, Kingsford, GladWare. Applies to our own brands only — competitors are fair game.
