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
| **Direct REST API** (`api.quickbase.com/v1`) | Fastest. Modern. JSON. Bulk endpoints. | Need to handle auth + retries yourself. | **Default for all production code.** |
| **CData JDBC/ODBC proxy** | SQL syntax. Federation across systems. | Adds 100-500ms per call. Adds another rate-limit layer. Has its own session/PAT to manage. | Ad-hoc SQL exploration. Joins across QB and other DBs. |
| **JSON-RPC API** (`pim.quickbase.com/db/<id>?a=...`) | Available for legacy operations not yet in REST. | Slow. XML responses on some endpoints. Per-record only. | Operations the v1 REST API doesn't support. |

**Rule:** if the v1 REST API supports your operation, use it. CData and JSON-RPC are fallbacks.

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

---

## 11. QB Codepage / View encoding rule (PERMANENT — never override)

QB codepages (views, pages deployed via `API_AddReplaceDBPage`) are stored and served in **Latin-1 / Windows-1252**. Any multi-byte UTF-8 character (code point > U+00FF) will be mangled into a diamond question mark or corrupted silently.

**Never use literal Unicode characters in QB views or codepages.** This includes:
- Em dash `—` (U+2014) — use ` - ` instead
- En dash `–` (U+2013) — use `-` instead
- Checkmark `✓` (U+2713) — use HTML entity `&#x2713;` in HTML, or JS escape `✓` in `.textContent` assignments
- Right arrow `->` symbols (U+2192) — use `->` instead
- Any character > U+00FF

**Safe alternatives:**
- HTML entities like `&#x2713;` `&#x26A0;` are safe — they are ASCII bytes in the source file; the browser decodes them.
- JavaScript Unicode escapes like `'￿'` or `'✓'` are safe — they are 6 ASCII characters in the source; the JS engine decodes them at runtime.
- Stick to ASCII + HTML entities for all rendered strings.

This rule was set by the user on 2026-05-16 after seeing diamond characters in a deployed FD-status panel. Apply it to every file that gets pasted or deployed to QB pages, including viewer.js, viewer.html, and any generated HTML stored in QB fields.
