#!/usr/bin/env python3
"""
One-time bootstrap: create the `Inventory Flow Comments` table in the
InventoryTrack QB app.  Mirrors the `Projection Comments` flag/comment
pattern but at MSTYLE grain (not Acct-MStyle) — used by the upcoming
Inventory Management screen.

Run once, then update the dbid + fid constants in:
  - scripts/inv_mgmt_viewer.py
  - (eventually) codepage/inv_mgmt_viewer.html

Usage:
    python scripts/create_inv_flow_comments_table.py
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
    print(f"-> Creating table 'Inventory Flow Comments' in app {APP_ID} ...")
    body = {
        "name":             "Inventory Flow Comments",
        "description":      ("Planner-↔-inv-mgr flag/comment thread keyed by Mstyle "
                             "(warehouse inventory grain).  Mirrors Projection "
                             "Comments but for Inventory Management.  Used by the "
                             "Inventory Management screen — read by the comment "
                             "history pane and the L30d expandable list."),
        "singleRecordName": "Inventory Comment",
        "pluralRecordName": "Inventory Comments",
    }
    resp = _req("POST", f"{API_BASE}/tables?appId={APP_ID}", body)
    tid  = resp.get("id")
    if not tid:
        raise RuntimeError(f"Unexpected create-table response: {resp}")
    print(f"   [OK] Table created  dbid = {tid}\n")
    return tid


def add_field(tid, label, field_type, properties=None):
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

    # If you need to re-run, set EXISTING_TID after the first successful create
    EXISTING_TID = ""   # leave blank on first run; paste dbid here to skip create
    tid = EXISTING_TID if EXISTING_TID else create_table()
    print(f"Using table:  {tid}\n")

    fids = {}
    # FK to Inventory Flow (Mstyle grain — Inv Flow is per-mstyle, not per-acct-mstyle)
    fids["Mstyle"] = add_field(tid, "Mstyle", "text")
    # Comment text — multi-line so planners can write multiple sentences
    fids["Note"] = add_field(tid, "Note", "text-multi-line")
    # Flag — using plain text (not multi-choice) so any value the UI sends works.
    # The UI dropdown shows: Needs Action / Investigating / In Progress / Resolved / Dismissed
    fids["Flag"] = add_field(tid, "Flag", "text")
    # Author — User field, auto-populated from QB session on insert
    fids["Author"] = add_field(tid, "Author", "user")

    print("\n" + "="*60)
    print("  Inventory Flow Comments table created  [OK]")
    print("="*60)
    print(f"  INV_FLOW_COMMENTS_TID = {tid!r}")
    print(f"  INV_FLOW_COMMENT_FID = {{")
    print(f"    'RECORD_ID':    3,                    # QB built-in")
    print(f"    'DATE_CREATED': 1,                    # QB built-in")
    print(f"    'MSTYLE':       {fids['Mstyle']},")
    print(f"    'NOTE':         {fids['Note']},")
    print(f"    'FLAG':         {fids['Flag']},")
    print(f"    'AUTHOR':       {fids['Author']},")
    print("  }")
    print("="*60 + "\n")
    print(f"  QB URL: https://{REALM}/db/{tid}\n")


if __name__ == "__main__":
    main()
