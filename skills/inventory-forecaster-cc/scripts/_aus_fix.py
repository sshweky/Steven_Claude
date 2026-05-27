"""Fix Amazon Australia Acct_MStyle_Key_ values: rename 27 records from
`1864-XAU` -> `1884-XAU`, then delete the duplicate `1864-FF33135AU`
record (the `1884-FF33135AU` record already exists and is the keeper).

Strategy:
  1. Pull all 28 candidate records WITH Record ID# (FID 3) so we can
     update via that immutable key (changing the text key field 292
     directly via mergeFieldId=292 would try to insert a new row instead
     of renaming an existing one).
  2. Snapshot to JSON for rollback.
  3. Bulk POST 27 update records using mergeFieldId=3 (Record ID) so QB
     matches by ID and writes the new value to field 292.
  4. DELETE the duplicate `1864-FF33135AU` row via POST /records/delete
     with a WHERE clause on its Record ID.

Run from scripts/ directory:  python _aus_fix.py [--dry-run | --execute]
"""
import urllib.request
import urllib.error
import json
import sys
from datetime import datetime

HEADERS = {
    "QB-Realm-Hostname": "pim.quickbase.com",
    "Authorization": "QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s",
    "Content-Type": "application/json",
}
PROJ_TID = "bpd237tvm"
KEY_FID  = 292            # Acct# - MStyle (Key)
RID_FID  = 3              # Record ID#
DUP_OLD_KEY = "1864-FF33135AU"   # row to DELETE (duplicate of 1884-FF33135AU)


def qb_query(body):
    req = urllib.request.Request(
        "https://api.quickbase.com/v1/records/query",
        data=json.dumps(body).encode(),
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


def qb_delete(payload):
    req = urllib.request.Request(
        "https://api.quickbase.com/v1/records",
        data=json.dumps(payload).encode(),
        headers=HEADERS, method="DELETE",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    dry_run = "--execute" not in sys.argv

    # 1) Pull all 28 candidate records with Record ID
    result = qb_query({
        "from":    PROJ_TID,
        "select":  [3, 292, 196, 363, 11, 1187, 10, 374],
        "where":   "({1187.EX.'1864'}OR{1187.EX.'01864'}OR{292.SW.'1864-'})"
                   "AND({196.CT.'AU'}OR{363.CT.'AUSTRALIA'})",
        "options": {"top": 500},
    })
    recs = result.get("data", [])
    print(f"Pulled {len(recs)} candidate records")

    # 2) Snapshot to JSON file
    snap = {
        "ts": datetime.now().isoformat(),
        "count": len(recs),
        "records": recs,
    }
    snap_path = f"_aus_fix_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(snap_path, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"Snapshot saved: {snap_path}")

    # Separate the duplicate from the renames
    rename_payload = []
    dup_rid = None
    for r in recs:
        cur_key = (r.get("292", {}) or {}).get("value", "")
        rid     = (r.get("3",   {}) or {}).get("value")
        if not cur_key.startswith("1864-"):
            continue
        if cur_key == DUP_OLD_KEY:
            dup_rid = rid
            print(f"DUPLICATE row identified for deletion: Record ID {rid}, key={cur_key}")
            continue
        new_key = "1884-" + cur_key.split("-", 1)[1]
        rename_payload.append({
            str(RID_FID): {"value": rid},
            str(KEY_FID): {"value": new_key},
        })

    print(f"\n{len(rename_payload)} records queued for KEY rename:")
    for p in rename_payload[:5]:
        print(f"  RID={p[str(RID_FID)]['value']:>6}  ->  {p[str(KEY_FID)]['value']}")
    print(f"  ... and {max(0, len(rename_payload)-5)} more")

    if dry_run:
        print("\n[DRY RUN] Pass --execute to actually apply changes.")
        return

    # 3) Bulk update (POST /records with mergeFieldId=3)
    print("\nApplying renames...")
    upd_result = qb_post_records({
        "to":           PROJ_TID,
        "data":         rename_payload,
        "mergeFieldId": RID_FID,
        "fieldsToReturn": [KEY_FID],
    })
    md = upd_result.get("metadata", {})
    print(f"  Updates: {md.get('updatedRecordIds', [])}")
    print(f"  Created (should be empty): {md.get('createdRecordIds', [])}")
    print(f"  Unchanged: {md.get('unchangedRecordIds', [])}")
    if upd_result.get("data"):
        sample = upd_result["data"][:3]
        print(f"  Sample written records: {sample}")

    # 4) Delete the duplicate
    if dup_rid is None:
        print(f"\n[WARN] Duplicate row {DUP_OLD_KEY} not found in the pull -- nothing to delete")
        return
    print(f"\nDeleting duplicate {DUP_OLD_KEY} (Record ID {dup_rid})...")
    del_result = qb_delete({
        "from":  PROJ_TID,
        "where": f"{{{RID_FID}.EX.'{dup_rid}'}}",
    })
    print(f"  Delete result: {del_result}")


if __name__ == "__main__":
    main()
