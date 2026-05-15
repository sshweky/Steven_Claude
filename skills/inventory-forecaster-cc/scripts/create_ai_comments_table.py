#!/usr/bin/env python3
"""
One-time bootstrap: create the `AI Comments` table in the InventoryTrack QB
app and add the four custom fields the viewers + F58 forecaster need.

Run once, then update the dbid + fid constants in:
  - codepage/viewer.html    (CFG.AI_COMMENTS_TID, CFG.AI_COMMENT_FID)
  - scripts/viewer.py       (AI_COMMENTS_* constants)
  - scripts/inventory_forecaster.py  (F58 SQL + AI_COMMENTS_TID)

Usage:
    python scripts/create_ai_comments_table.py
"""
import json
import sys
import urllib.request
import urllib.error

REALM       = "pim.quickbase.com"
USER_TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
APP_ID      = "bpd24h9wy"           # InventoryTrack

API_BASE    = "https://api.quickbase.com/v1"
HEADERS     = {
    "QB-Realm-Hostname": REALM,
    "Authorization":     f"QB-USER-TOKEN {USER_TOKEN}",
    "Content-Type":      "application/json",
    "User-Agent":        "P+P Inventory Forecaster",
}


def _req(method, url, body=None):
    """POST/GET helper with verbose error output."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  [{e.code}] {e.reason}\n  -> {e.read().decode('utf-8', errors='replace')}",
              file=sys.stderr)
        raise


def create_table():
    print(f"-> Creating table 'AI Comments' in app {APP_ID} ...")
    body = {
        "name":             "AI Comments",
        "description":      ("Audit trail of planner-issued AI forecast adjustments "
                             "(separate from mgr-facing Projection Comments). "
                             "F58 in inventory_forecaster.py replays active rows "
                             "(Ignored=false) on each forecast run."),
        "singleRecordName": "AI Comment",
        "pluralRecordName": "AI Comments",
    }
    resp = _req("POST", f"{API_BASE}/tables?appId={APP_ID}", body)
    tid  = resp.get("id")
    if not tid:
        raise RuntimeError(f"Unexpected create-table response: {resp}")
    print(f"   [OK] Table created  dbid = {tid}\n")
    return tid


def add_field(tid, label, field_type, properties=None, required=False):
    """`required` is ignored — QB schema API rejects the property even
    inside `properties` for some field types.  Set required via QB UI
    after creation if you need it."""
    print(f"-> Adding field [{label}] type={field_type} ...")
    body = {
        "label":      label,
        "fieldType":  field_type,
        "addToForms": True,
    }
    if properties:
        body["properties"] = properties
    resp = _req("POST", f"{API_BASE}/fields?tableId={tid}", body)
    fid  = resp.get("id")
    print(f"   [OK] {label}: fid {fid}")
    return fid


def main():
    print(f"\nUsing realm:  {REALM}")
    print(f"Using app:    {APP_ID} (InventoryTrack)\n")

    # Idempotent: if EXISTING_TID is set, skip creation and just add fields.
    # Useful when the script half-finished (e.g. table created but field
    # add failed) — don't double-create the table.
    EXISTING_TID = "bv2jirwts"   # set after first successful create-table call
    tid = EXISTING_TID if EXISTING_TID else create_table()
    print(f"Using table:  {tid}\n")

    fids = {}
    fids["Acct#-MStyle"] = add_field(tid, "Acct#-MStyle",   "text",            required=True)
    fids["Note"]         = add_field(tid, "Note",           "text-multi-line", required=True)
    fids["Author"]       = add_field(tid, "Author",         "user")
    fids["Ignored"]      = add_field(tid, "Ignored",        "checkbox")

    print("\n" + "="*60)
    print("  AI Comments table created  [OK]")
    print("="*60)
    print(f"  AI_COMMENTS_TID = {tid!r}")
    print(f"  AI_COMMENT_FID = {{")
    print(f"    'RECORD_ID':    3,                    # QB built-in")
    print(f"    'DATE_CREATED': 1,                    # QB built-in")
    print(f"    'ACCT_MSTYLE':  {fids['Acct#-MStyle']},")
    print(f"    'NOTE':         {fids['Note']},")
    print(f"    'AUTHOR':       {fids['Author']},")
    print(f"    'IGNORED':      {fids['Ignored']},")
    print("  }")
    print("="*60 + "\n")
    print(f"  QB URL: https://{REALM}/db/{tid}\n")


if __name__ == "__main__":
    main()
