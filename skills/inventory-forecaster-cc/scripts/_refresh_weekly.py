"""
_refresh_weekly.py

Populates Inventory History - Weekly (bv2sxg2ji) with ATS Qty OH# values
for the last 52 Sundays.

Steps:
  1. Create 52 numeric fields in bv2sxg2ji (ATS LW, ATS LW-1, ..., ATS LW-51)
     if they don't already exist.
  2. Pull mstyle -> record_id mapping from bv2sxg2ji.
  3. For each of the 52 Sundays, query Inventory History for ATS values.
  4. Upsert values into bv2sxg2ji in batches of 500.
"""

import sys, os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import json, time
import urllib.request, urllib.error
from datetime import date, timedelta

QB_REALM  = "pim.quickbase.com"
QB_TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_BASE   = "https://api.quickbase.com/v1"

SRC_TABLE  = "br6dcnv35"   # Inventory History
DEST_TABLE = "bv2sxg2ji"   # Inventory History - Weekly

SRC_FID_DATE    = 11
SRC_FID_MSTYLE  = 6
SRC_FID_ATS     = 10
DEST_FID_RID    = 3
DEST_FID_MSTYLE = 6

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
    "User-Agent":        "petspeople-inv-history-weekly/4.0",
}

MAX_RETRIES = 4
BATCH_SIZE  = 500
PAGE_SIZE   = 10000


def _raw(method, path, body=None, timeout=90):
    url = QB_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            if e.code in (429, 502, 504) and attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"  [retry {attempt}] HTTP {e.code}, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {txt}") from e
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise

def qb_get(path):         return _raw("GET", path)
def qb_post(path, body):  return _raw("POST", path, body)


def last_n_sundays(n=52):
    """Return list of the last n Sundays (most recent first)."""
    today = date.today()
    dow = today.weekday()          # Mon=0 ... Sun=6
    days_since_sunday = (dow + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    return [last_sunday - timedelta(weeks=i) for i in range(n)]


def query_date_records(target_date: date) -> tuple[list, str]:
    """
    Query Inventory History for a specific date (all pages).
    Returns (rows_list, used_date_str).
    If Sunday has no data, automatically falls back to Monday.
    """
    for delta, day_label in [(0, "Sun"), (1, "Mon")]:
        d = target_date + timedelta(days=delta)
        d_str = qb_date_str(d)
        q = (f"{{'{SRC_FID_DATE}'.OAF.'{d_str}'}}"
             f" AND {{'{SRC_FID_DATE}'.OBF.'{d_str}'}}")
        all_rows = []
        skip = 0
        while True:
            try:
                resp = qb_post("/records/query", {
                    "from":    SRC_TABLE,
                    "select":  [SRC_FID_MSTYLE, SRC_FID_ATS],
                    "where":   q,
                    "options": {"top": PAGE_SIZE, "skip": skip},
                })
            except Exception as e:
                return [], d_str
            rows = resp.get("data", [])
            all_rows.extend(rows)
            if len(rows) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
            time.sleep(0.15)
        if all_rows:
            return all_rows, f"{d_str}({day_label})"
        # Sunday had 0 records — fall through to try Monday
        time.sleep(0.12)
    return [], qb_date_str(target_date) + "(no data)"


def qb_date_str(d: date) -> str:
    return d.strftime("%m/%d/%Y")


# ── Step 1: Create 52 numeric fields ──────────────────────────────────────────
print("=" * 60)
print("Step 1: Create 52 numeric ATS fields in Weekly table")
print("=" * 60)

existing_fields = qb_get(f"/fields?tableId={DEST_TABLE}")
existing_labels = {f["label"].strip().lower(): f["id"] for f in existing_fields}

ats_fids = []  # index 0 = ATS LW, index 1 = ATS LW-1, etc.

for n in range(52):
    label = "ATS LW" if n == 0 else f"ATS LW-{n}"
    if label.lower() in existing_labels:
        fid = existing_labels[label.lower()]
        ats_fids.append(fid)
        print(f"  [exists]  [{fid:5d}] {label}")
    else:
        try:
            resp = qb_post(f"/fields?tableId={DEST_TABLE}", {
                "label":      label,
                "fieldType":  "numeric",
                "properties": {"decimalPlaces": 0, "blankIsZero": True},
            })
            fid = resp["id"]
            ats_fids.append(fid)
            print(f"  [created] [{fid:5d}] {label}")
        except Exception as e:
            print(f"  FAILED {label}: {e}")
            ats_fids.append(None)
        time.sleep(0.12)

good_fids = [f for f in ats_fids if f]
print(f"\n  {len(good_fids)}/52 fields ready. "
      f"ATS LW=fid {ats_fids[0]}, ATS LW-51=fid {ats_fids[51]}")

if len(good_fids) < 52:
    print("  WARNING: Some fields could not be created. Proceeding with available fields.")


# ── Step 2: Pull mstyle -> record_id map from Weekly table ────────────────────
print("\n" + "=" * 60)
print("Step 2: Pull mstyle -> record_id map from Weekly table")
print("=" * 60)

mstyle_to_rid = {}
skip = 0

while True:
    resp = qb_post("/records/query", {
        "from":    DEST_TABLE,
        "select":  [DEST_FID_RID, DEST_FID_MSTYLE],
        "options": {"top": PAGE_SIZE, "skip": skip},
    })
    rows = resp.get("data", [])
    for row in rows:
        rid    = row[str(DEST_FID_RID)]["value"]
        mstyle = row[str(DEST_FID_MSTYLE)]["value"]
        if mstyle:
            mstyle_to_rid[mstyle.strip().upper()] = rid
    print(f"  Page {skip // PAGE_SIZE + 1}: {len(rows)} rows  "
          f"(running total: {len(mstyle_to_rid)} mstyles)")
    if len(rows) < PAGE_SIZE:
        break
    skip += PAGE_SIZE
    time.sleep(0.2)

print(f"\n  Total mstyles in Weekly table: {len(mstyle_to_rid)}")


# ── Step 3: Query Inventory History for each Sunday (Monday fallback) ─────────
print("\n" + "=" * 60)
print("Step 3: Pull ATS values — Sunday preferred, Monday fallback")
print("=" * 60)

sundays = last_n_sundays(52)
print(f"  Date range: {qb_date_str(sundays[51])} to {qb_date_str(sundays[0])}")
print()

# Build data dict: mstyle -> [ats_lw, ats_lw1, ..., ats_lw51]  (None = no data)
ats_data = {ms: [None] * 52 for ms in mstyle_to_rid}

for n, sunday in enumerate(sundays):
    label = "ATS LW" if n == 0 else f"ATS LW-{n}"

    rows, used_date = query_date_records(sunday)

    for row in rows:
        ms_raw  = row[str(SRC_FID_MSTYLE)]["value"]
        ats_val = row[str(SRC_FID_ATS)]["value"]
        if not ms_raw:
            continue
        ms = ms_raw.strip().upper()
        if ms in ats_data:
            cur = ats_data[ms][n]
            if cur is None or (ats_val is not None and ats_val > cur):
                ats_data[ms][n] = ats_val

    print(f"  {label:12s} ({used_date}):  {len(rows):6,} records")
    time.sleep(0.12)

# Save checkpoint so Step 4 can be retried without re-pulling data
with open("_weekly_ats_checkpoint.json", "w") as _f:
    json.dump({
        "mstyle_to_rid": mstyle_to_rid,
        "ats_fids": ats_fids,
        "ats_data": {k: v for k, v in ats_data.items()},
    }, _f)
print("  Checkpoint saved to _weekly_ats_checkpoint.json")


# ── Step 4: Upsert into Weekly table ──────────────────────────────────────────
print("\n" + "=" * 60)
print("Step 4: Upsert ATS values into Weekly table")
print("=" * 60)

# Map n -> fid for fields that were successfully created/found
n_to_fid = {n: fid for n, fid in enumerate(ats_fids) if fid is not None}

records_to_write = []
mstyles_with_any_data = 0

for ms, values in ats_data.items():
    rid = mstyle_to_rid.get(ms)
    if not rid:
        continue

    rec = {str(DEST_FID_RID): {"value": rid}}
    has_data = False
    for n, val in enumerate(values):
        fid = n_to_fid.get(n)
        if fid is None:
            continue
        if val is not None:
            rec[str(fid)] = {"value": int(round(val))}
            has_data = True
        # If val is None, leave blank (don't write 0 — blankIsZero handles display)

    if has_data:
        records_to_write.append(rec)
        mstyles_with_any_data += 1

print(f"  {len(records_to_write):,} mstyle records to update "
      f"({len(mstyle_to_rid) - len(records_to_write):,} had no data in any week)")
print()

written = 0
errors  = 0

for i in range(0, len(records_to_write), BATCH_SIZE):
    batch = records_to_write[i : i + BATCH_SIZE]
    try:
        resp = qb_post("/records", {
            "to":             DEST_TABLE,
            "data":           batch,
            "mergeFieldId":   DEST_FID_RID,
            "fieldsToReturn": [],
        })
        processed = resp.get("totalNumberOfRecordsProcessed", len(batch))
        written  += processed
        pct       = written / len(records_to_write) * 100
        print(f"  Batch {i // BATCH_SIZE + 1:4d}: {processed:4d} written  "
              f"({written:,}/{len(records_to_write):,}  {pct:.0f}%)")
    except Exception as e:
        errors += 1
        print(f"  BATCH ERROR {i // BATCH_SIZE + 1}: {e}")
    time.sleep(0.22)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("COMPLETE")
print("=" * 60)
print(f"  ATS fields created:   {len(good_fids)}/52")
print(f"  Mstyles in table:     {len(mstyle_to_rid):,}")
print(f"  Records updated:      {written:,}")
print(f"  Batch errors:         {errors}")
print(f"  Table URL: https://pim.quickbase.com/db/{DEST_TABLE}")

# Save fid map
fid_map = {
    "dest_table": DEST_TABLE,
    "src_table":  SRC_TABLE,
    "ats_fids":   ats_fids,
    "sundays":    [qb_date_str(s) for s in sundays],
}
with open("inv_history_weekly_fids.json", "w") as f:
    json.dump(fid_map, f, indent=2)
print("  Field/date map saved to inv_history_weekly_fids.json")
print()
