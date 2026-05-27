"""Phase 2 of the Amazon Australia key fix.

The first attempt (_aus_fix.py) couldn't update FID 292 because it's the
table's key field -- QB silently no-ops REST updates to the designated key
field.  This script:

  1. Switches the table's key field temporarily from 292 -> 3 (Record ID).
  2. Bulk-updates 27 records to set FID 292 = '1884-XAU'.
  3. Restores the key field back to FID 292.
  4. Verifies the renames landed.
"""
import urllib.request
import json
import sys
import time

HEADERS = {
    "QB-Realm-Hostname": "pim.quickbase.com",
    "Authorization": "QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s",
    "Content-Type": "application/json",
}
PROJ_TID = "bpd237tvm"
APP_ID   = "bpd24h9wy"
KEY_FID  = 292
RID_FID  = 3
DUP_OLD_KEY = "1864-FF33135AU"


def qb_query(body):
    req = urllib.request.Request(
        "https://api.quickbase.com/v1/records/query",
        data=json.dumps(body).encode(),
        headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def qb_get_table():
    req = urllib.request.Request(
        f"https://api.quickbase.com/v1/tables/{PROJ_TID}?appId={APP_ID}",
        headers=HEADERS, method="GET",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def qb_set_key_field(new_key_fid):
    req = urllib.request.Request(
        f"https://api.quickbase.com/v1/tables/{PROJ_TID}?appId={APP_ID}",
        data=json.dumps({"keyFieldId": new_key_fid}).encode(),
        headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def qb_post_records(payload):
    req = urllib.request.Request(
        "https://api.quickbase.com/v1/records",
        data=json.dumps(payload).encode(),
        headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    dry_run = "--execute" not in sys.argv

    # 1) Pull the 27 records still needing rename
    result = qb_query({
        "from":    PROJ_TID,
        "select":  [3, 292, 196],
        "where":   "{292.SW.'1864-'}AND{196.CT.'AU'}AND{363.CT.'AMAZON AUSTRALIA'}",
        "options": {"top": 100},
    })
    recs = result.get("data", [])
    print(f"Records still needing rename: {len(recs)}")
    if not recs:
        print("Nothing to do.")
        return

    rename_payload = []
    for r in recs:
        cur_key = (r.get("292", {}) or {}).get("value", "")
        rid     = (r.get("3",   {}) or {}).get("value")
        if not cur_key.startswith("1864-"):
            continue
        new_key = "1884-" + cur_key.split("-", 1)[1]
        rename_payload.append({
            str(RID_FID): {"value": rid},
            str(KEY_FID): {"value": new_key},
        })
    print(f"Renames queued: {len(rename_payload)}")

    if dry_run:
        for p in rename_payload[:3]:
            print(f"  RID={p[str(RID_FID)]['value']:>6}  ->  {p[str(KEY_FID)]['value']}")
        print(f"  ... and {max(0, len(rename_payload)-3)} more")
        print("\n[DRY RUN] Pass --execute to apply.")
        return

    # 2) Check current key field
    table_info = qb_get_table()
    orig_key_fid = table_info.get("keyFieldId")
    print(f"Current keyFieldId: {orig_key_fid}")
    if orig_key_fid != KEY_FID:
        print(f"[WARN] Expected keyFieldId={KEY_FID}, found {orig_key_fid} -- aborting")
        return

    # 3) Switch key field to Record ID
    print(f"Switching keyFieldId: {KEY_FID} -> {RID_FID} ...")
    try:
        sw_result = qb_set_key_field(RID_FID)
        print(f"  -> keyFieldId now {sw_result.get('keyFieldId')}")
    except Exception as e:
        print(f"  [FAIL] {e}")
        return

    time.sleep(2)   # let QB settle

    # 4) Bulk update via Record ID merge
    print("Applying renames via mergeFieldId=3 (Record ID)...")
    try:
        upd_result = qb_post_records({
            "to":            PROJ_TID,
            "data":          rename_payload,
            "mergeFieldId":  RID_FID,
            "fieldsToReturn": [KEY_FID],
        })
        md = upd_result.get("metadata", {})
        print(f"  Updated: {len(md.get('updatedRecordIds', []))}")
        print(f"  Created: {len(md.get('createdRecordIds', []))}  (should be 0)")
        print(f"  Unchanged: {len(md.get('unchangedRecordIds', []))}")
        if upd_result.get("data"):
            sample = upd_result["data"][:3]
            print(f"  Sample written: {sample}")
    except Exception as e:
        print(f"  [FAIL] {e}")
        # Still try to restore key field
        try:
            qb_set_key_field(KEY_FID)
            print(f"  Key field restored to {KEY_FID}")
        except Exception as e2:
            print(f"  [DANGER] Could not restore key field: {e2}")
        return

    # 5) Restore key field
    print(f"Restoring keyFieldId: {RID_FID} -> {KEY_FID} ...")
    try:
        sw_result2 = qb_set_key_field(KEY_FID)
        print(f"  -> keyFieldId now {sw_result2.get('keyFieldId')}")
    except Exception as e:
        print(f"  [DANGER] Could not restore key field: {e}")
        print(f"  Manual intervention required: in QB, set Projections key field back to FID {KEY_FID}")
        return

    # 6) Verify
    verify = qb_query({
        "from":    PROJ_TID,
        "select":  [3, 292],
        "where":   "{292.SW.'1864-'}AND{363.CT.'AMAZON AUSTRALIA'}",
        "options": {"top": 100},
    })
    remaining = verify.get("data", [])
    if remaining:
        print(f"\n[WARN] {len(remaining)} records still have 1864- prefix:")
        for r in remaining[:5]:
            print(f"  RID={r.get('3',{}).get('value')}  key={r.get('292',{}).get('value')}")
    else:
        print("\nAll Amazon Australia keys successfully renamed to 1884- prefix.")


if __name__ == "__main__":
    main()
