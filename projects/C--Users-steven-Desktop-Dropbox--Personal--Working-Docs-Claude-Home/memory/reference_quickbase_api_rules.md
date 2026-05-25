---
name: Quickbase API Rules
description: Universal rule book for accessing Quickbase from any script, chat, or skill — minimize API calls, avoid throttling, fail safely
type: reference
originSessionId: 5fd14678-40d0-43a0-be74-b35f8597e7d0
---
# Quickbase API Rules

A practical rule book for any code that talks to Quickbase. Applies whether you're going through CData, the REST API directly, the JSON RPC, or a generated SDK. The goal is **the smallest number of API calls that does the job**, with **resilience to throttling and outages**.

Quickbase enforces hidden per-realm and per-table rate limits. The symptom of crossing them is *not* a clean 429 — it's degrading latency, then 0-byte responses on `pim.quickbase.com`, then SocketTimeout. The realm slows for everyone (planners in the UI, other integrations) until the rate window rolls. **Treat QB as a precious shared resource, not a database.**

---

## Quick rules (memorize these)

1. **One bulk call beats N small calls.** Always.
2. **Pull only the fields you'll use.** Each extra column slows QB row reconstruction.
3. **Filter at QB, not in Python.** A WHERE clause is cheaper than 10× the rows.
4. **Cache schema and field maps for the lifetime of a run.**
5. **Reads use `POST /records/query`, writes use `POST /records` with `mergeFieldId`.**
6. **Always smoke-test with `TOP 1` / `top: 1` before any bulk job.**
7. **Persist completed keys to disk. Make every job resumable.**
8. **Heavy jobs run off-hours (02:00–05:00 local).**
9. **Sustained per-table write rate ≤ 10 req/s.** Burst ≤ 25 req/s. If unsure, throttle to 5 req/s.
10. **Direct REST API beats CData beats JSON-RPC.** Use the highest-leverage path you can.

---

## 1. Read patterns

### 1.1 Use `POST /records/query` with explicit `select`
Always pass a tight field list. Don't `select=*` or omit it (QB returns everything).

```json
POST https://api.quickbase.com/v1/records/query
{
  "from":   "<dbid>",
  "select": [3, 6, 12, 47],     // only the field IDs you need
  "where":  "{6.EX.'value'}",   // QB query language; filter server-side
  "options": { "top": 500 }     // pagination — pair with skip
}
```

- `select`: list of field IDs. Smaller list = faster QB response = less data over the wire = less you parse.
- `where`: QB's WHERE clause syntax. **Always filter server-side.** Pulling 10,000 rows to filter 500 in Python is the #1 cause of slow scripts.
- `top` + `skip` for pagination. QB caps at ~10,000 rows per call regardless of `top`.

### 1.2 Cache the field map
Field labels → IDs is a single `GET /fields?tableId=<dbid>` call. Cache the dict for the whole script run.

```python
_FIELD_MAP_CACHE = {}

def get_field_map(table_id):
    if table_id not in _FIELD_MAP_CACHE:
        fields = qb_request("GET", f"/fields?tableId={table_id}")
        _FIELD_MAP_CACHE[table_id] = {f["label"]: f["id"] for f in fields}
    return _FIELD_MAP_CACHE[table_id]
```

Never look up field IDs by name on every call. **One fetch per run, period.**

### 1.3 Filter early, project narrowly
Two queries that return the same data — one is 50× faster than the other:

```sql
-- ❌ BAD: pull everything, filter in Python
SELECT * FROM Projections                              -- ~200 fields × 4500 rows

-- ✓ GOOD: project + filter at QB
SELECT [Acct_MStyle_Key_], [AI_PRJ_W1] FROM Projections
WHERE [Status_Cust] LIKE 'A%' AND [Mstyle] LIKE 'FF%'  -- ~2 fields × 800 rows
```

### 1.4 Use saved reports when available
If the planners maintain a saved report that already filters/projects what you want, hit that report ID instead of building your own query. QB caches report results internally and reuses them across calls.

---

## 2. Write patterns

### 2.1 Bulk upsert via `POST /records` (the only writeback that matters at scale)

```json
POST https://api.quickbase.com/v1/records
{
  "to":            "<dbid>",
  "data":          [ { "<fid>": {"value": <val>}, ... }, ...],
  "mergeFieldId":  <fid_of_unique_key>,
  "fieldsToReturn": []           // empty array = don't echo updated rows
}
```

- **Up to 25,000 records per call.** Use **500–1,000** for safety; bigger batches risk timeouts and lose granularity on partial failures.
- `mergeFieldId` makes the operation an upsert keyed on a unique non-RID field (e.g. `Acct_MStyle_Key_`). Without it, you'd need each record's RID.
- `fieldsToReturn: []` skips echoing updated rows — saves bandwidth and QB time.

**Math:** 4,500 records via 500-batch bulk = **9 API calls**. Same workload via per-record EditRecord = **4,500 API calls**. That's a 500× difference in rate-limit pressure.

### 2.2 Never use API_EditRecord in a loop
`API_EditRecord` (legacy XML RPC) is one record per call. If your script has a `for record in records: edit(record)`, you're causing the throttling problem. Refactor to bulk.

### 2.3 Pre-build payloads using field IDs, not labels
QB resolves labels at request time. If you submit `{"AI_PRJ_W1": {"value": 42}}`, QB does a label lookup per row. Submit `{"123": {"value": 42}}` and it skips that step.

### 2.4 Idempotent writes only
Always design writes so re-running them produces the same final state. `mergeFieldId` upsert is idempotent. Append/insert is not. If you must insert, dedupe in the payload before the call.

---

## 3. Throttling, retry, and resilience

### 3.1 Empirical rate limits (no official ceiling — measured)
| Operation | Sustained | Burst |
|---|---|---|
| Reads on small tables (<10k rows) | ~30/s | ~50/s |
| Reads on large tables (>1M rows) | ~5/s | ~15/s |
| Writes (any table) | ~10/s | ~25/s |
| Concurrent connections per realm | ~50 | — |

If you don't know the size class, **throttle to 5 req/s and only push higher when you've measured the table's behavior under load.**

### 3.2 Implement throttle with a thread-safe pacer
```python
import threading, time
_lock, _last = threading.Lock(), [0.0]
def pace(min_ms):
    with _lock:
        wait = min_ms/1000 - (time.time() - _last[0])
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()
```

Call `pace(150)` before each request when running concurrent workers — this caps overall throughput regardless of thread count.

### 3.3 Retry with exponential backoff and re-prime
QB throttle responses include 429, 502, 504, **and silent 0-byte timeouts**. Treat all four the same:

```python
for attempt in range(1, MAX_RETRIES + 1):
    try:
        return qb_call(...)
    except (TimeoutError, IncompleteRead, urllib.error.HTTPError) as e:
        if attempt == MAX_RETRIES:
            raise
        # Re-prime any cached session/cookies on read errors
        if "IncompleteRead" in str(e) or "0 bytes" in str(e):
            invalidate_session()
        time.sleep(2 ** attempt)   # 2, 4, 8, 16, 32 seconds
```

Set `MAX_RETRIES = 5`. Total backoff budget: **62 seconds**. If 5 retries × 62s still fails, the realm is down — abort cleanly, don't loop forever.

### 3.4 Concurrency caps
Default to **2 parallel workers** on writes when scope is wide (e.g. all active records). Default to **6** on narrow scope. Never go above **10** without measuring.

### 3.5 Abort on empty when data was expected
If a fetch returns 0 rows and you expected records, **`sys.exit()` with a clear `[ABORT]` message** instead of "successfully writing 0 records." Silent empty results are how throttle failures become production bugs.

---

## 4. Schema discovery

### 4.1 Discover once, never on the hot path
- `GET /tables?appId=<appId>` — list of tables in app
- `GET /fields?tableId=<dbid>` — full field definitions for one table
- `GET /reports?tableId=<dbid>` — saved reports

Run these at startup, cache the response in a dict, never call again until next process start.

### 4.2 Don't list all apps to find one table
If you know the app ID, hit it directly. The "discover everything" pattern (`/apps` → loop tables → loop fields) burns through your read quota at startup before doing real work.

---

## 5. Connection layer choices

| Layer | Pros | Cons | Use when |
|---|---|---|---|
| **Direct REST API** (`api.quickbase.com/v1`) | Fastest. Modern. JSON. Bulk endpoints. Server-side filtering. | Need to handle auth + retries yourself. | **Default for all production code.** |
| **CData JDBC/ODBC proxy** | SQL syntax. Federation across systems. | Adds 100-500ms per call. Adds another rate-limit layer. **Does NOT push WHERE clauses to QB -- fetches all rows and filters client-side.** | Session prime (`getInstructions`). Ad-hoc SQL exploration on small tables only. |
| **JSON-RPC API** (`pim.quickbase.com/db/<id>?a=...`) | Available for legacy operations not yet in REST. | Slow. XML responses on some endpoints. Per-record only. | Operations the v1 REST API doesn't support. |

**Rule:** if the v1 REST API supports your operation, use it. CData and JSON-RPC are fallbacks.

### 5.2 CData full-table-scan behavior (critical -- read before using CData for any large-table query)

**CData does NOT push WHERE clauses to QuickBase.** When you run a CData SQL query like:

```sql
SELECT [col1],[col2] FROM Projections WHERE [Mstyle] = 'FF15592'
SELECT [Mstyle],[Master_Pack] FROM Styles  WHERE [Mstyle] IN ('FF15592', ...)
```

CData fetches **every row** from the target table from QB server-side, then filters in the CData layer. From QB's perspective this looks identical to `SELECT * FROM <table>` -- full table scan every time, regardless of scope. **This is true for every CData read, not just the canonical example tables.**

**Consequences:**
- A single-record dry-run (`--acct 23011 --mstyle FF15592`) hits QB as hard as a full `--all` run
- Under realm load (80 users sharing the realm), repeated large CData queries cause `IncompleteRead(0 bytes)` disconnects for everyone
- Wide tables compound the problem: QB must reconstruct every field on every row before returning, even when CData asks for only 2-3 columns
- A loop of N "narrow" CData queries against the same large table = N back-to-back full-table scans (the Phase 2 master-pack loop was 28 such scans before migration)

### 5.2.1 Unified CData-vs-REST policy (applies to ALL sources)

**This rule applies regardless of where the call originates:** production scripts, skill scripts, scheduled jobs, ad-hoc Claude chat, background subagents, planner-typed queries, browser-based exploration. The realm doesn't distinguish between sources -- a 30K-row full-scan from a chat session hits QB exactly as hard as one from a cron job.

**Important reframe:** the parameters that matter are **table characteristics**, not query characteristics. CData ignores your SELECT and WHERE, so the size of your query is irrelevant -- only the size of the underlying table determines realm impact. `SELECT [col1] FROM Styles WHERE [Mstyle]='FF15592'` looks tiny but hits the realm exactly as hard as `SELECT * FROM Styles`.

**Use CData/MCP only when ALL of these hold:**

| Parameter | Threshold |
|---|---|
| Target table row count | ≤ 100 rows AND not growing |
| Target table column count | ≤ 30 columns |
| Call pattern | Single one-shot (no loops, no batches, no retries by design) |
| Frequency | Ad-hoc or rare; not on a polling/schedule loop |
| Purpose | Either: (a) metadata (`getInstructions`, `getTables`, `getColumns`, `getProcedures`) -- always CData-OK regardless of size, or (b) genuine lookup against a stable small reference table |

**Use REST API when ANY of these hold:**

| Trigger | Why |
|---|---|
| Target table > 100 rows | Full-scan tax becomes real even for "1 row" queries |
| Target table > 30 columns | Wide-row reconstruction tax on QB side |
| Call inside a loop, batch, retry, or per-record pattern | One CData loop = N full-table scans |
| Table is growing (transactions, logs, time-series, projections) | Today's small table is tomorrow's throttle |
| Recurring/scheduled job | Recurring cost compounds |
| Critical path of a long-running pipeline | A throttle here blocks the whole run |
| You can't confidently answer "how many rows in this table?" | Default to REST when uncertain |

**Worked examples (pim.quickbase.com realm):**

| Table | dbid | Rows | Cols | Call type | Verdict |
|---|---|---|---|---|---|
| Divisions | `brjdizght` | 2 | few | one-shot lookup | CData OK |
| Master Brands | `breus3wdk` | 148 | few | one-shot lookup | CData OK (borderline; watch growth) |
| Amazon Bidding Profiles | `bp83954uq` | 25 | few | one-shot | CData OK |
| Dates | `bqn6k7suj` | 2,192 | few | one-shot lookup | REST required (rows > 100) |
| Projections | `bpd237tvm` | ~5,500 | 250 | any | REST required (already migrated) |
| Styles | `bphzqfkev` | ~30K | 423 | any | REST required (already migrated) |
| AI Comments | `bv2jirwts` | <100 | few | one-shot read | CData OK; watch growth |
| AI Comments inside a 5,000-record loop | same | -- | -- | per-record | REST required (loop trigger) |
| Any Amazon AdTrack perf table | many | millions | many | any | REST required, always |

**Rule of thumb:** when uncertain about table size, default to REST. The cost of an unnecessary REST call is ~50ms. The cost of an unnecessary CData full-scan against a large table is a realm outage for 80 users.

### 5.2.2 Tables this has applied to (and the fix)

| Table (dbid) | Width | Rows | Old CData path | New path |
|---|---|---|---|---|
| Projections (`bpd237tvm`) | ~250 cols | ~5,500 | Phase 1 single query | REST `POST /v1/records/query` (2026-05-25) |
| Styles (`bphzqfkev`) | 423 cols | ~30K | Phase 2 loop of 28 batches | REST `POST /v1/records/query` batched by 500 mstyles (2026-05-25) |

When a new heavy CData read is discovered, add a row to this table after migrating it.

See §5.2.1 below for the unified threshold-based policy that supersedes prior guidance. Short version:
- CData OK only when target table is ≤ 100 rows AND ≤ 30 cols AND the call is a single one-shot (no loop, no batch, no retry, no schedule).
- Metadata calls (`getInstructions`, `getTables`, `getColumns`, `getProcedures`) are always CData-fine regardless of underlying table size.
- All other reads go through QB REST API (`POST /v1/records/query`).

### 5.3 Field label normalization (CData SQL column names vs QB REST field labels)

CData normalizes QB field labels to SQL-safe column names by replacing any run of non-alphanumeric characters with a single underscore:

```python
import re
cdata_col_name = re.sub(r'[^a-zA-Z0-9]+', '_', qb_field_label)
# Examples:
# "Status @ Cust"       -> "Status_Cust"
# "Acct#-MStyle (Key)"  -> "Acct_MStyle_Key_"
# "Ord/LW 51"           -> "Ord_LW_51"
# "Shpd Wk L13W cust."  -> "Shpd_Wk_L13W_cust_"
```

Use this when building a label->FID map from `GET /v1/fields` to match against CData column names used elsewhere in your code.

### 5.1 Standard auth headers (REST)
```
QB-Realm-Hostname: <realm>.quickbase.com
Authorization:     QB-USER-TOKEN <user_token>
Content-Type:      application/json
User-Agent:        <project>/<version>
```

User tokens are scoped to one user — give bots their own QB account so their quota is isolated from human users.

---

## 6. Pre-flight and observability

### 6.1 Smoke-test before every bulk job
Run a single `top: 1` query against the target table at script start. If that fails or hangs, abort before launching the heavy work.

```python
def smoke_test(table_id):
    try:
        rows = qb_query(table_id, select=[3], top=1, timeout=30)
        return len(rows) > 0
    except Exception as e:
        sys.exit(f"[ABORT] QB smoke test failed: {e}")
```

### 6.2 Persist progress to disk
Every N records (typically 50-100), write the list of completed keys to a JSON file. On crash/throttle/timeout, the next run reads that file and resumes from where it left off — without re-doing work and re-hitting the rate limit.

```python
completed = set(json.load(open("completed.json")) if Path("completed.json").exists() else [])
to_do    = [r for r in records if r["key"] not in completed]
```

### 6.3 Log every API call's latency
Tag each request with start/end timestamps. When latency creeps from 200ms → 1s → 5s, you're approaching the throttle line. Pause your job before QB cuts you off.

### 6.4 Check QB Realm Admin → API Usage
QB exposes per-realm API usage metrics. Set a personal alert when your scripts hit >70% of the realm cap. The cap recovers slowly — better to back off proactively than wait out a throttle.

---

## 7. Operational hygiene

1. **Run heavy jobs off-hours.** Realm rate limits are shared with humans in the QB UI and any other integrations. Schedule for 02:00–05:00 local.
2. **Dedicated bot users.** Each script gets its own QB user account with a user token. Isolates quota and makes audit trails clean.
3. **Tag your User-Agent.** `User-Agent: petspeople-inventory-forecaster/1.2` — makes your traffic identifiable in QB logs when troubleshooting.
4. **Don't poll.** If you need fresh data, use webhooks or scheduled batches, not "every 30 seconds I'll check if anything changed."
5. **Document your tables and field IDs.** Keep a `reference_quickbase.md` with realm + token + table IDs + field IDs of fields you write to. Saves a `/fields` call every script run.

---

## 8. Anti-patterns (don't do this)

- ❌ `for r in records: edit(r)` — per-record loop. **The single biggest cause of throttling.**
- ❌ `select=*` — pulls every field, slow at QB and on the wire.
- ❌ Filter rows in Python after pulling everything.
- ❌ Look up field IDs by label on every request.
- ❌ Run heavy jobs during business hours.
- ❌ Retry forever without an exponential backoff or max budget.
- ❌ Treat 0-byte timeouts as "no data" — they're throttle signals.
- ❌ Share one QB user/token across multiple scripts (they fight for the same quota).
- ❌ Skip the smoke test "because it worked yesterday."
- ❌ **Use CData for any large-table read with narrow scope filters.** CData fetches the full table regardless of WHERE clause. For tables >500 rows with narrow scope, use QB REST API so filtering happens server-side. Projections (~5,500 rows × 250 cols) and Styles (~30K rows × 423 cols) are the canonical examples -- a 1-record CData query hits QB just as hard as a full-table scan.
- ❌ **Use CData for the Projections Phase 1 fetch or the Styles Phase 2 fetch.** Both are migrated to `POST /v1/records/query` with explicit FID list and server-side `where`. Never revert.
- ❌ **Loop a "narrow" CData read against the same large table.** A loop of N batches = N full-table scans of the underlying table. If your code does `for batch in batches: cdata_query(...)`, refactor to one REST call with WHERE before shipping.

---

## 9. Failure modes and what they mean

| Symptom | Likely cause | Action |
|---|---|---|
| Latency staircases (1s → 5s → 30s → ∞) | Approaching throttle line | Pause + backoff |
| `IncompleteRead(0 bytes)` | QB cut connection mid-response (throttled) | Retry with backoff + re-prime |
| `Timeout` on `/db/<id>?a=API_DoQuery` | Per-table rate limit on that dbid | Wait 5-15 min, switch to bulk |
| 429 Too Many Requests | Hard throttle from QB | Honor `Retry-After` header |
| Empty 200 OK | Often a silent throttle | Treat as failure, don't proceed |
| Auth 200 but query 504 | Endpoint-specific cap, not auth | Stay logged in, slow down queries |

---

## 10. The 30-second checklist before any new QB script

Before writing a single API call, answer these:

- [ ] Have I cached the field-id map at startup?
- [ ] Am I projecting only the columns I need?
- [ ] Am I filtering server-side with `where`?
- [ ] Are my writes batched via `POST /records` with `mergeFieldId`?
- [ ] Do I have a smoke test before the heavy work?
- [ ] Is my retry policy exponential-backoff with a max budget (≤62s)?
- [ ] Will I persist completed keys so the job is resumable?
- [ ] Is my concurrency capped (≤2 wide, ≤6 narrow)?
- [ ] Am I throttling to ≤10 req/s on writes?
- [ ] Will this run during off-hours, or do I need to scale way back?

If any answer is "no", reconsider before sending the first request.
