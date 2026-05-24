"""restore_w2_man_prj.py

One-time restore: today's writeback incorrectly zeroed MAN PRJ W2 for records
that had Opn_W2 > 0.  This script reads forecast_results.json, identifies the
affected records (opn_w[1] > 0), and writes MAN PRJ W2 = opn_w[1] back to QB.

Best-available restore value: opn_w[1] (the confirmed open PO qty for W2).
The original planner values were not saved before the writeback.  This sets
W2 back to the open PO quantity, which is the most logically correct value.
Planners can adjust from the codepage if their original value differed.

Run once: python scripts/restore_w2_man_prj.py
"""

import json
import os
import sys
import time
import requests
from base64 import b64encode
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_PATH = SCRIPT_DIR / "forecast_results.json"

QB_PROJ_TABLE = "bpd237tvm"
QB_REALM      = "pim.quickbase.com"
QB_USER_TOKEN = os.environ.get("QB_USER_TOKEN", "")  # set in env or paste below

sys.path.insert(0, str(SCRIPT_DIR))
from config import QB_USER_TOKEN, QB_REALM, QB_BULK_BATCH

# ---------------------------------------------------------------------------
# Auth helpers (reuse pattern from inventory_forecaster.py)
# ---------------------------------------------------------------------------

def _qb_headers():
    return {
        "Authorization": f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "QB-Realm-Hostname": QB_REALM,
        "Content-Type": "application/json",
    }

def qb_bulk_update(table_id, payload, merge_fid):
    url = f"https://api.quickbase.com/v1/records"
    hdrs = _qb_headers()
    n_ok = 0
    n_fail = 0
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

# ---------------------------------------------------------------------------
# Discover the MAN PRJ W2 field ID from QB
# ---------------------------------------------------------------------------

def discover_man_w2_fid():
    """Fetch the fields list from the Projections table and find the MAN PRJ W2 FID."""
    import re
    url = f"https://api.quickbase.com/v1/fields?tableId={QB_PROJ_TABLE}"
    resp = requests.get(url, headers=_qb_headers(), timeout=30)
    resp.raise_for_status()
    fields = resp.json()
    # MAN PRJ cols match MM_DD_W<n> where n=2
    man_w2_fid = None
    for f in fields:
        label = f.get("label", "")
        if re.match(r"^\d{2}_\d{2}_W2$", label):
            man_w2_fid = f["id"]
            print(f"   Found MAN PRJ W2 field: '{label}' -> FID {man_w2_fid}")
            break
    if not man_w2_fid:
        raise RuntimeError("Could not find MAN PRJ W2 field (pattern MM_DD_W2) in Projections table.")
    return man_w2_fid

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not QB_USER_TOKEN:
        print("ERROR: set QB_USER_TOKEN environment variable to your QB user token.")
        print("  e.g.  set QB_USER_TOKEN=b6ur8b_your_token_here")
        sys.exit(1)

    print(f"Loading {RESULTS_PATH} ...")
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)

    print(f"  {len(results)} total records")

    # Find records where Opn_W2 > 0 (those had MAN PRJ W2 zeroed)
    affected = []
    for rec in results:
        opn_w = rec.get("opn_w") or []
        w2_po = opn_w[1] if len(opn_w) > 1 else 0
        if w2_po > 0:
            affected.append({"key": rec["key"], "w2_po": int(w2_po)})

    print(f"  {len(affected)} records with Opn_W2 > 0 (MAN PRJ W2 was incorrectly zeroed)")

    if not affected:
        print("Nothing to restore.")
        return

    # Show a sample
    print("\n  Sample (first 10):")
    for r in affected[:10]:
        print(f"    {r['key']:40s}  Opn_W2={r['w2_po']:,}  -> MAN_PRJ_W2 will be set to {r['w2_po']:,}")

    print(f"\n  Restoring MAN PRJ W2 = Opn_W2 for {len(affected)} records ...")
    print("  Note: original planner values were not saved before the writeback.")
    print("  Using Opn_W2 (confirmed open PO qty) as the restore value.\n")

    # Discover the W2 FID
    print("Discovering MAN PRJ W2 field ID from QB ...")
    man_w2_fid = discover_man_w2_fid()

    # Discover the merge field (Acct_MStyle_Key_ = FID 292 on bpd237tvm)
    merge_fid = 292

    # Build payload
    payload = []
    for r in affected:
        payload.append({
            merge_fid:   r["key"],
            man_w2_fid:  r["w2_po"],
        })

    print(f"\nPushing {len(payload)} records to QB table {QB_PROJ_TABLE} (mergeFieldId={merge_fid}) ...")
    t0 = time.time()
    n_ok, n_fail, errors = qb_bulk_update(QB_PROJ_TABLE, payload, merge_fid)
    elapsed = time.time() - t0

    print(f"\nDone in {elapsed:.1f}s  --  {n_ok} restored / {n_fail} failed")
    if errors:
        print(f"Errors: {json.dumps(errors, indent=2)}")
    else:
        print("All records restored successfully.")

if __name__ == "__main__":
    main()
