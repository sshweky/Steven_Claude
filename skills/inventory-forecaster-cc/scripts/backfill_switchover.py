#!/usr/bin/env python3
"""
backfill_switchover.py -- One-time backfill of Switchover_To_MStyle,
Switchover_Active, and Switchover_Date for Amazon (acct 1864) projections
where an EC/COS/AMZ/DS/DTC/PX variant sibling exists in scope.

Does NOT touch AI_PRJ_W* or MAN PRJ columns.

Call budget: ~1 (field map GET) + 3-4 (paginated query) + 1-2 (writes) = ~6-8 calls
"""

import json, os, re, sys, time, urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from config import QB_REALM, QB_USER_TOKEN, QB_PROJ_TABLE, QB_REST_MAX_RETRIES, QB_BULK_BATCH

AMAZON_ACCT = "1864"

SWITCHOVER_SUFFIXES = ("EC", "COS", "AMZ", "DS", "DTC", "PX")  # longest-match wins

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
    """Return (label->fid, fid->label) dicts.  Both underscore and space variants keyed."""
    fields = _qb_get(f"https://api.quickbase.com/v1/fields?tableId={QB_PROJ_TABLE}")
    l2f, f2l = {}, {}
    for f in fields:
        label = f.get("label", "")
        fid   = f["id"]
        l2f[label]                        = fid  # "Status @ Cust"
        l2f[re.sub(r'\W+', '_', label)]   = fid  # "Status_Cust"
        f2l[fid]                          = label
    return l2f, f2l


def fetch_amazon_rows(select_fids, where, f2l):
    """Paginated fetch; returns list of dicts keyed by QB label."""
    url      = "https://api.quickbase.com/v1/records/query"
    all_rows = []
    skip     = 0
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
            all_rows.append(row)
        print(f"      ... skip={skip}, got {len(records)} rows (total so far: {len(all_rows)})")
        if len(records) < 1000:
            break
        skip += 1000
    return all_rows


def main():
    print("\n=== Switchover Backfill ===")
    print(f"Target: Amazon acct {AMAZON_ACCT}, QB Projections table {QB_PROJ_TABLE}\n")

    # ── Step 1: Field map ──────────────────────────────────────────────────────
    print("[1] Fetching field map...")
    l2f, f2l = get_field_map()

    fid_key    = l2f.get("Acct_MStyle_Key_")  or l2f.get("Acct#_-_MStyle_(Key)") or 292
    fid_mstyle = l2f.get("Mstyle")            or 196
    fid_status = l2f.get("Status_Cust")       or 10
    fid_sw_act = l2f.get("Switchover_Active") or 1602
    fid_sw_ms  = l2f.get("Switchover_To_MStyle") or 1603
    fid_sw_dt  = l2f.get("Switchover_Date")   or 1604

    print(f"    key={fid_key}  mstyle={fid_mstyle}  status={fid_status}")
    print(f"    sw_active={fid_sw_act}  sw_mstyle={fid_sw_ms}  sw_date={fid_sw_dt}")

    # Find current MAN PRJ W1-W26 FIDs and their calendar dates
    _today    = date.today()
    prj_fids  = {}   # week_num -> fid
    col_dates = {}   # week_num -> date
    for label, fid in l2f.items():
        m = re.match(r'^(\d{2})_(\d{2})_W(\d+)$', label)
        if m:
            mm, dd, wn = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= wn <= 26:
                try:
                    d = date(_today.year, mm, dd)
                    if (d - _today).days < -180:
                        d = date(_today.year + 1, mm, dd)
                    prj_fids[wn]  = fid
                    col_dates[wn] = d
                except ValueError:
                    pass

    print(f"    MAN PRJ weeks found: {len(prj_fids)} (W1-W{max(prj_fids) if prj_fids else '?'})")

    # ── Step 2: Fetch all active Amazon projections ────────────────────────────
    select_fids = list({fid_key, fid_mstyle, fid_status, fid_sw_act, fid_sw_ms, fid_sw_dt}
                       | set(prj_fids.values()))

    where = (f"({{{fid_status}.SW.'A'}}OR{{{fid_status}.SW.'FD'}})"
             f"AND{{{fid_key}.SW.'{AMAZON_ACCT}-'}}")

    print(f"\n[2] Fetching active Amazon projections...")
    rows = fetch_amazon_rows(select_fids, where, f2l)
    print(f"    Total: {len(rows)} rows\n")

    # ── Step 3: Build acct-mstyle index ───────────────────────────────────────
    by_acct_ms = {}
    for r in rows:
        key = (r.get("Acct_MStyle_Key_") or r.get("Acct# - MStyle (Key)") or "")
        ms  = (r.get("Mstyle") or "")
        if "-" not in key or not ms:
            continue
        acct = key.split("-", 1)[0]
        by_acct_ms[(acct, ms.strip())] = r

    # ── Step 4: Detect ec_parents and compute switchover fields ───────────────
    print("[3] Detecting EC/COS/AMZ variant siblings and computing switchover fields...")
    updates = []   # list of {key, sw_to_mstyle, sw_date}

    for r in rows:
        key = (r.get("Acct_MStyle_Key_") or r.get("Acct# - MStyle (Key)") or "")
        ms  = (r.get("Mstyle") or "").strip()
        if "-" not in key or not ms:
            continue
        acct = key.split("-", 1)[0]

        # Skip if planner already set Switchover_To_MStyle
        # Note: row keys use QB's original label (with spaces), not underscore form
        existing_sw_ms = (r.get("Switchover To MStyle") or r.get("Switchover_To_MStyle") or "").strip()
        if existing_sw_ms:
            # Check if we still need to compute the date
            existing_sw_dt = (r.get("Switchover_Date") or "")[:10]
            v_row = by_acct_ms.get((acct, existing_sw_ms))
            if v_row and not existing_sw_dt:
                # Has mstyle but no date -- compute it
                sw_date = None
                for wn in sorted(prj_fids.keys()):
                    label = f2l.get(prj_fids[wn], "")
                    val   = float(v_row.get(label) or 0)
                    if val > 0:
                        sw_date = col_dates.get(wn)
                        break
                if sw_date:
                    updates.append({
                        "key":           key,
                        "sw_to_mstyle":  existing_sw_ms,
                        "sw_date":       sw_date.isoformat(),
                        "set_active":    False,  # already planner-set, don't override
                        "reason":        "date-only (planner mstyle set, date missing)",
                    })
            continue

        # Auto-detect: find first variant suffix sibling in scope
        variant_found = None
        for sfx in sorted(SWITCHOVER_SUFFIXES, key=len, reverse=True):
            if (acct, f"{ms}{sfx}") in by_acct_ms:
                variant_found = f"{ms}{sfx}"
                break
        if not variant_found:
            continue

        # Find Switchover_Date from variant's first non-zero MAN PRJ week
        v_row   = by_acct_ms[(acct, variant_found)]
        sw_date = None
        for wn in sorted(prj_fids.keys()):
            label = f2l.get(prj_fids[wn], str(prj_fids[wn]))
            val   = float(v_row.get(label) or 0)
            if val > 0:
                sw_date = col_dates.get(wn)
                break

        updates.append({
            "key":          key,
            "sw_to_mstyle": variant_found,
            "sw_date":      sw_date.isoformat() if sw_date else None,
            "set_active":   True,
            "reason":       f"auto-detected {variant_found}",
        })

    print(f"    Found {len(updates)} records to update\n")

    if not updates:
        print("Nothing to backfill. Exiting.")
        return

    for u in updates[:20]:
        date_str = u["sw_date"] or "(no variant PRJ set yet)"
        active_str = " + Active=True" if u["set_active"] else ""
        print(f"    {u['key']}: -> {u['sw_to_mstyle']} | date={date_str}{active_str}  ({u['reason']})")
    if len(updates) > 20:
        print(f"    ... and {len(updates) - 20} more")

    # ── Step 5: Write back ─────────────────────────────────────────────────────
    print(f"\n[4] Writing switchover fields to QB ({len(updates)} records)...")

    # Build payload using FIDs directly
    payload_data = []
    for u in updates:
        rec = {str(fid_key): {"value": u["key"]}}
        rec[str(fid_sw_ms)] = {"value": u["sw_to_mstyle"]}
        if u["set_active"]:
            rec[str(fid_sw_act)] = {"value": True}
        if u["sw_date"]:
            rec[str(fid_sw_dt)] = {"value": u["sw_date"]}
        payload_data.append(rec)

    batch_size = min(QB_BULK_BATCH, 500)
    n_ok = n_fail = 0
    for i in range(0, len(payload_data), batch_size):
        batch = payload_data[i:i + batch_size]
        try:
            result = _qb_post("https://api.quickbase.com/v1/records", {
                "to":             QB_PROJ_TABLE,
                "data":           batch,
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
            print(f"    Batch {i // batch_size + 1}: {created} created, {updated} updated, "
                  f"{unchanged} already set" + (f", {len(errors)} errors" if errors else ""))
            if errors:
                for line, err in list(errors.items())[:5]:
                    print(f"      lineError[{line}]: {err}")
        except Exception as e:
            n_fail += len(batch)
            print(f"    Batch {i // batch_size + 1}: FAILED -- {e}")

    print(f"\nDone. {n_ok} records updated, {n_fail} failed.")


if __name__ == "__main__":
    main()
