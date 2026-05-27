#!/usr/bin/env python3
"""
backfill_norm_ord_wk.py -- One-time backfill of Normalized Ord/Wk L4w,
L13w, and L26w for all active Projections records.

Computes raw average weekly order rate over the most recent 4 / 13 / 26
weeks of order history (same denominator convention as the forecaster).
The regular forecaster run will later update these with fully-normalized
values (post-F35/F41/F43/F47/ATS); for ~90% of records the raw and
normalized values are identical.

Call budget: ~1 (field map) + ~6 (paginated query) + ~11 (bulk writes) = ~18 calls
"""

import json, os, re, sys, time, urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from config import QB_REALM, QB_USER_TOKEN, QB_PROJ_TABLE, QB_REST_MAX_RETRIES, QB_BULK_BATCH

# FIDs confirmed 2026-05-27
FID_NORM_L4W  = 1626
FID_NORM_L13W = 1627
FID_NORM_L26W = 1628

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_USER_TOKEN}",
    "Content-Type":      "application/json",
}


def _qb_post(url, body):
    for attempt in range(1, QB_REST_MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt == QB_REST_MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)


def _qb_get(url):
    for attempt in range(1, QB_REST_MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers=HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt == QB_REST_MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)


def get_field_map():
    """Return label->fid dict (both space and underscore variants)."""
    fields = _qb_get(f"https://api.quickbase.com/v1/fields?tableId={QB_PROJ_TABLE}")
    l2f = {}
    for f in fields:
        label = f.get("label", "")
        fid   = f["id"]
        l2f[label]                      = fid
        l2f[re.sub(r'\W+', '_', label)] = fid
    return l2f


def fetch_active_rows(select_fids, fid_status, fid_key, f2l):
    """Paginated fetch of all active (Status A% or FD%) records."""
    url  = "https://api.quickbase.com/v1/records/query"
    rows = []
    skip = 0
    where = f"({{{fid_status}.SW.'A'}}OR{{{fid_status}.SW.'FD'}})"
    while True:
        resp = _qb_post(url, {
            "from":    QB_PROJ_TABLE,
            "select":  select_fids,
            "where":   where,
            "options": {"skip": skip, "top": 1000},
        })
        records = resp.get("data") or []
        for record in records:
            row = {}
            for fid_str, cell in record.items():
                label = f2l.get(int(fid_str), fid_str)
                row[label] = (cell or {}).get("value")
            rows.append(row)
        print(f"  ... skip={skip}, got {len(records)} rows (total: {len(rows)})")
        if len(records) < 1000:
            break
        skip += 1000
    return rows


def main():
    print("\n=== Normalized Ord/Wk Backfill ===")
    print(f"Target: QB Projections table {QB_PROJ_TABLE}\n")

    # ── Step 1: Field map ──────────────────────────────────────────────────────
    print("[1] Fetching field map...")
    l2f = get_field_map()
    f2l = {v: k for k, v in l2f.items()}

    fid_key    = l2f.get("Acct_MStyle_Key_") or l2f.get("Acct# - MStyle (Key)") or 292
    fid_status = l2f.get("Status_Cust")      or l2f.get("Status @ Cust")          or 10

    # Last 26 Ord_LW columns — enough for all 3 windows
    # ORD_COLS ordering: oldest first — Ord_LW_51..Ord_LW_1, Ord_LW (newest at end)
    # We only need the 26 newest: Ord_LW_25 down to Ord_LW
    ord_labels = [f"Ord_LW_{i}" for i in range(25, 0, -1)] + ["Ord_LW"]  # oldest->newest, 26 entries
    ord_fids   = [l2f.get(lbl) for lbl in ord_labels]
    missing    = [lbl for lbl, fid in zip(ord_labels, ord_fids) if not fid]
    if missing:
        print(f"  [WARN] Missing FIDs for: {missing[:5]}{'...' if len(missing)>5 else ''}")
    ord_fids = [f for f in ord_fids if f]  # drop any None

    select_fids = list(dict.fromkeys([fid_key, fid_status] + ord_fids))
    print(f"  key={fid_key}  status={fid_status}  ord_cols={len(ord_fids)}")
    print(f"  norm FIDs: L4w={FID_NORM_L4W}  L13w={FID_NORM_L13W}  L26w={FID_NORM_L26W}")

    # ── Step 2: Fetch all active records ──────────────────────────────────────
    print(f"\n[2] Fetching active Projections rows...")
    rows = fetch_active_rows(select_fids, fid_status, fid_key, f2l)
    print(f"  Total: {len(rows)} rows")

    # ── Step 3: Compute norm values ────────────────────────────────────────────
    print(f"\n[3] Computing Normalized Ord/Wk for {len(rows)} records...")
    payload = []
    zero_count = 0
    for row in rows:
        key = row.get("Acct_MStyle_Key_") or row.get("Acct# - MStyle (Key)") or ""
        if not key:
            continue
        # Build 26-entry history (oldest->newest): Ord_LW_25..Ord_LW
        hist26 = [float(row.get(lbl) or 0) for lbl in ord_labels]
        l4w  = round(sum(hist26[-4:])  / 4,  1)
        l13w = round(sum(hist26[-13:]) / 13, 1)
        l26w = round(sum(hist26[-26:]) / 26, 1)
        if l26w == 0:
            zero_count += 1
        payload.append({
            fid_key:      key,
            FID_NORM_L4W:  l4w,
            FID_NORM_L13W: l13w,
            FID_NORM_L26W: l26w,
        })

    print(f"  {len(payload)} records to write ({zero_count} with L26w=0)")

    # ── Step 4: Bulk upsert ────────────────────────────────────────────────────
    print(f"\n[4] Writing to QB ({len(payload)} records)...")
    batch_size = min(QB_BULK_BATCH, 500)
    n_ok = n_fail = 0
    for i in range(0, len(payload), batch_size):
        batch = payload[i:i + batch_size]
        # Convert to QB format: {str(fid): {"value": v}}
        data = [{str(k): {"value": v} for k, v in rec.items()} for rec in batch]
        try:
            result = _qb_post("https://api.quickbase.com/v1/records", {
                "to":             QB_PROJ_TABLE,
                "data":           data,
                "mergeFieldId":   fid_key,
                "fieldsToReturn": [],
            })
            meta      = result.get("metadata") or {}
            created   = len(meta.get("createdRecordIds")   or [])
            updated   = len(meta.get("updatedRecordIds")   or [])
            unchanged = len(meta.get("unchangedRecordIds") or [])
            errors    = meta.get("lineErrors") or {}
            n_ok     += created + updated + unchanged
            n_fail   += len(errors)
            print(f"  Batch {i // batch_size + 1}: {updated} updated, {unchanged} unchanged"
                  + (f", {len(errors)} errors" if errors else ""))
            if errors:
                for line, err in list(errors.items())[:3]:
                    print(f"    lineError[{line}]: {err}")
        except Exception as e:
            n_fail += len(batch)
            print(f"  Batch {i // batch_size + 1}: FAILED -- {e}")

    print(f"\nDone. {n_ok} records updated, {n_fail} failed.")


if __name__ == "__main__":
    main()
