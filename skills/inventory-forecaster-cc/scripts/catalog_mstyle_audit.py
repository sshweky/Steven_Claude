"""
catalog_mstyle_audit.py
=======================
Cross-reference Amazon Catalog (bqp8vz625) mstyle against the active Projections
mstyle for each ASIN.  Show only records where they don't match -- these are the
catalog entries that need to be corrected.

Usage:
    python catalog_mstyle_audit.py
"""

import json, sys, time, urllib.request, urllib.error
from collections import defaultdict

QB_REALM = "pim.quickbase.com"
QB_TOKEN = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
HEADERS  = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
}
QUERY_URL = "https://api.quickbase.com/v1/records/query"


def qb_query_all(table, where, select, label=""):
    rows, skip = [], 0
    while True:
        payload = json.dumps({
            "from":    table,
            "select":  select,
            "where":   where,
            "options": {"skip": skip, "top": 1000},
        }).encode()
        req = urllib.request.Request(QUERY_URL, data=payload, headers=HEADERS, method="POST")
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    result = json.loads(r.read())
                break
            except Exception as e:
                print(f"  [WARN] {label} attempt {attempt}: {e}", flush=True)
                if attempt == 3:
                    raise
                time.sleep(3 * attempt)
        batch = result.get("data", [])
        rows.extend(batch)
        total = result.get("metadata", {}).get("totalRecords", len(rows))
        print(f"  {label}: {len(rows)}/{total} ...", flush=True)
        if len(batch) < 1000 or len(rows) >= total:
            break
        skip += 1000
        time.sleep(0.3)
    return rows


def cell(row, fid):
    v = (row.get(str(fid)) or {}).get("value")
    return str(v).strip() if v is not None else ""


# ── 1. Amazon Catalog: all records (filter in Python by status) ───────────────
# Pull everything -- the catalog uses "Future Delete" (not "FD") so a SW filter
# on "FD" misses those entries.  We keep Active* and Future Delete* entries.
print("\nFetching Amazon Catalog (all records) ...", flush=True)
cat_rows  = qb_query_all("bqp8vz625", "{3.GT.0}", [7, 33, 34, 51], "catalog")

def is_active_or_fd(status):
    s = status.upper()
    return s.startswith("A") or s.startswith("FUTURE") or s == "FD"

catalog = {}   # asin -> {mstyle, style_num, status}
for row in cat_rows:
    asin   = cell(row, 7)
    mstyle = cell(row, 34)
    model  = cell(row, 33)   # Style #
    status = cell(row, 51)
    if asin and mstyle and is_active_or_fd(status):
        catalog[asin] = {"mstyle": mstyle, "model": model, "status": status}

print(f"  {len(catalog)} active/Future-Delete catalog entries with ASIN + Mstyle", flush=True)


# ── 2. Projections: active + FD, Amazon, acct 1864 ───────────────────────────
print("\nFetching Projections (acct 1864, A + FD) ...", flush=True)
prj_where = "({'10'.SW.'A'}OR{'10'.SW.'FD'})AND{'874'.CT.'amazon'}AND{'292'.SW.'1864-'}"
prj_rows  = qb_query_all("bpd237tvm", prj_where, [292, 196, 821, 10], "projections")

active_by_asin = defaultdict(list)   # ASIN -> active ('A') projection records
all_by_asin    = defaultdict(list)   # ASIN -> all (A + FD) projection records

for row in prj_rows:
    asin   = cell(row, 821)   # Cust SKU#
    mstyle = cell(row, 196)
    key    = cell(row, 292)
    status = cell(row, 10)
    if not asin or asin == "0":
        continue
    rec = {"key": key, "mstyle": mstyle, "status": status}
    all_by_asin[asin].append(rec)
    if status.upper().startswith("A"):
        active_by_asin[asin].append(rec)

print(f"  {len(prj_rows)} projection records", flush=True)


# ── 3. Find mismatches ────────────────────────────────────────────────────────
mismatches = []

for asin, cat in sorted(catalog.items()):
    cat_ms      = cat["mstyle"]
    active_recs = active_by_asin.get(asin, [])
    all_recs    = all_by_asin.get(asin, [])

    if not all_recs:
        continue   # no projection for this ASIN at all -- skip

    active_ms_set = {r["mstyle"] for r in active_recs}
    all_ms_set    = {r["mstyle"] for r in all_recs}

    if cat_ms in active_ms_set:
        continue   # catalog mstyle matches an active projection -- OK

    # Catalog mstyle is NOT in any active projection
    if cat_ms in all_ms_set:
        reason = "Catalog mstyle is FD -- active projection switched to variant"
    else:
        reason = "Catalog mstyle has no projection record"

    mismatches.append({
        "asin":     asin,
        "cat_ms":   cat_ms,
        "cat_model": cat["model"],
        "cat_stat": cat["status"],
        "active":   sorted(active_recs, key=lambda r: r["mstyle"]),
        "reason":   reason,
    })


# ── 4. Report ─────────────────────────────────────────────────────────────────
print(f"\n{'='*90}", flush=True)
print(f"  Amazon Catalog Mstyle Audit  --  {len(mismatches)} mismatches", flush=True)
print(f"{'='*90}", flush=True)
print(f"\n  {'ASIN':<14}  {'Catalog Mstyle':<22}  {'Cat Status':<14}  "
      f"{'Active Projection Mstyle(s) -- needs to match this'}", flush=True)
print("  " + "-"*100, flush=True)

for m in mismatches:
    if m["active"]:
        prj_str = "  |  ".join(f"{r['mstyle']} ({r['status']})" for r in m["active"])
    else:
        prj_str = "(no active projection -- all FD)"
    print(f"  {m['asin']:<14}  {m['cat_ms']:<22}  {m['cat_stat']:<14}  {prj_str}",
          flush=True)

if not mismatches:
    print("  (none -- all catalog mstyles match their active projections)", flush=True)

print(flush=True)


if __name__ == "__main__":
    pass
