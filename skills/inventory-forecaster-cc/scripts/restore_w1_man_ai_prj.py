"""restore_w1_man_ai_prj.py

One-time restore: today's writeback (2026-05-24, Sunday) incorrectly zeroed
both MAN PRJ W1 and AI PRJ W1 for 1,439 records that had Opn_W1 > 0.

Root cause: VP-Q4 Sunday gate only protected Amazon FF/BB, not all customers.
All records with an open W1 PO had their W1 zeroed even though the week had
just started and no orders had been received.

Restore value: Opn_W1 (confirmed open PO qty for W1).  The original planner
values and pre-VP-Q4 AI values were not saved before the writeback.  Setting
both to the open PO quantity is the best available proxy -- it represents the
confirmed demand expected for W1.

Run once: python scripts/restore_w1_man_ai_prj.py
"""

import json
import re
import os
import sys
import time
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_PATH = SCRIPT_DIR / "forecast_results.json"

QB_PROJ_TABLE = "bpd237tvm"

sys.path.insert(0, str(SCRIPT_DIR))
from config import QB_USER_TOKEN, QB_REALM, QB_BULK_BATCH

def _qb_headers():
    return {
        "Authorization": f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "QB-Realm-Hostname": QB_REALM,
        "Content-Type": "application/json",
    }

def qb_bulk_update(table_id, payload, merge_fid):
    url = "https://api.quickbase.com/v1/records"
    hdrs = _qb_headers()
    n_ok = n_fail = 0
    errors = []
    for start in range(0, len(payload), QB_BULK_BATCH):
        batch = payload[start:start + QB_BULK_BATCH]
        body = {
            "to": table_id,
            "data": [{str(k): {"value": v} for k, v in row.items()} for row in batch],
            "mergeFieldId": merge_fid,
        }
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=hdrs, json=body, timeout=60)
                if resp.status_code == 200:
                    n_ok += len(batch)
                    break
                else:
                    if attempt == 2:
                        n_fail += len(batch)
                        errors.append({"batch_start": start, "status": resp.status_code,
                                       "error": resp.text[:300]})
                    else:
                        time.sleep(2 ** attempt)
            except Exception as e:
                if attempt == 2:
                    n_fail += len(batch)
                    errors.append({"batch_start": start, "error": str(e)})
                else:
                    time.sleep(2 ** attempt)
        pct = min(100, int((start + len(batch)) / len(payload) * 100))
        print(f"   {start + len(batch)}/{len(payload)}  ({pct}%)  ok={n_ok} fail={n_fail}")
    return n_ok, n_fail, errors

def discover_fids():
    """Return (ai_w1_fid, man_w1_fid, merge_fid) from QB field list."""
    url = f"https://api.quickbase.com/v1/fields?tableId={QB_PROJ_TABLE}"
    resp = requests.get(url, headers=_qb_headers(), timeout=30)
    resp.raise_for_status()
    fields = resp.json()

    ai_w1_fid = man_w1_fid = merge_fid = None
    for f in fields:
        label = f.get("label", "")
        fid   = f["id"]
        if label in ("AI PRJ W1", "AI_PRJ_W1"):
            ai_w1_fid = fid
            print(f"   AI PRJ W1 field: '{label}' -> FID {fid}")
        if re.match(r"^\d{2} \d{2} W1$", label):
            man_w1_fid = fid
            print(f"   MAN PRJ W1 field: '{label}' -> FID {fid}")
        if label in ("Acct# - MStyle (Key)", "Acct - MStyle Key", "Acct_MStyle_Key_"):
            merge_fid = fid

    if not ai_w1_fid:
        raise RuntimeError("Could not find AI PRJ W1 field.")
    if not man_w1_fid:
        raise RuntimeError("Could not find MAN PRJ W1 field (pattern DD MM W1).")
    if not merge_fid:
        merge_fid = 292   # known FID for bpd237tvm
        print(f"   Merge field not found by label -- using known FID {merge_fid}")
    return ai_w1_fid, man_w1_fid, merge_fid

def main():
    print(f"Loading {RESULTS_PATH} ...")
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = data.get("records", data) if isinstance(data, dict) else data
    print(f"  {len(results)} total records")

    # Find records where Opn_W1 > 0 and NOT F_PO_CUTOFF zeroed
    # (F_PO_CUTOFF records have no open W1 PO -- they were zeroed because NO
    # PO arrived by cutoff.  Those are correct and should not be restored.)
    affected = []
    for rec in results:
        opn_w = rec.get("opn_w") or []
        w1_po = opn_w[0] if len(opn_w) > 0 else 0
        if w1_po > 0 and not rec.get("zero_man_w1_cutoff"):
            affected.append({"key": rec["key"], "w1_po": int(w1_po)})

    print(f"  {len(affected)} records with Opn_W1 > 0 and no F_PO_CUTOFF flag "
          f"(VP-Q4 incorrectly zeroed W1 on Sunday)")

    if not affected:
        print("Nothing to restore.")
        return

    print("\n  Sample (first 10):")
    for r in affected[:10]:
        print(f"    {r['key']:40s}  Opn_W1={r['w1_po']:,}")

    print(f"\n  Will set AI PRJ W1 = Opn_W1 AND MAN PRJ W1 = Opn_W1 for {len(affected)} records")
    print("  (Opn_W1 = confirmed open PO qty -- best available proxy for original values)\n")

    print("Discovering field IDs from QB ...")
    ai_w1_fid, man_w1_fid, merge_fid = discover_fids()

    payload = []
    for r in affected:
        payload.append({
            merge_fid:  r["key"],
            ai_w1_fid:  r["w1_po"],
            man_w1_fid: r["w1_po"],
        })

    print(f"\nPushing {len(payload)} records to QB table {QB_PROJ_TABLE} ...")
    t0 = time.time()
    n_ok, n_fail, errors = qb_bulk_update(QB_PROJ_TABLE, payload, merge_fid)
    elapsed = time.time() - t0

    print(f"\nDone in {elapsed:.1f}s  --  {n_ok} restored / {n_fail} failed")
    if errors:
        print(f"Errors: {json.dumps(errors, indent=2)}")
    else:
        print("All W1 records restored (AI PRJ W1 + MAN PRJ W1 = Opn_W1).")

if __name__ == "__main__":
    main()
