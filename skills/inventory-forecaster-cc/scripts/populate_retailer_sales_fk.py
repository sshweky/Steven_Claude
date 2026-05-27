#!/usr/bin/env python3
"""
Populate Retailer Sales FID 30 (FK for relationship 30 to Projections) from
FID 28 (formula field that already computes the correct Acct#-MStyle key).

Relationship 30: Projections (parent, FID 292 key) <- Retailer Sales (child, FID 30 FK)
FID 28 formula: ToText([Acct #]) & "-" & [Mstyle]  (e.g. "1864-FF12655")

This is a one-time backfill. Once complete, summary fields LY POS and CY POS
will auto-compute on Projections rows.

NOTE: New Retailer Sales records imported in future will also need FID 30 set.
Re-run this script after each POS data refresh.
"""
import time
import json
import urllib.request
import urllib.error

QB_REALM    = "pim.quickbase.com"
QB_TOKEN    = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
RS_TABLE    = "bv2izcn5b"   # Retailer Sales
FID_RECID   = 3
FID_FORMULA = 28            # Acct# - MStyle (key for Projections) [formula]
FID_FK      = 30            # Acct# - MStyle (key for Projections)2 [plain text, FK]

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization": f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "petspeople-populate-fk/1.0",
}
PAGE_SIZE  = 1000
WRITE_BATCH= 1000
WRITE_DELAY= 0.2   # 5 req/s


def qb_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def qb_post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def read_all_formula_values():
    """Read all Retailer Sales records: Record ID + FID 28 formula value."""
    records = []
    skip = 0
    total = None
    while True:
        payload = {
            "from": RS_TABLE,
            "select": [FID_RECID, FID_FORMULA],
            "where": "{3.GT.0}",
            "options": {"top": PAGE_SIZE, "skip": skip},
        }
        resp = qb_post("https://api.quickbase.com/v1/records/query", payload)
        batch = resp.get("data", [])
        if total is None:
            total = resp.get("metadata", {}).get("totalRecords", "?")
            print(f"Total records to process: {total}")
        records.extend(batch)
        skip += len(batch)
        print(f"  Read {skip} / {total}...", end="\r")
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(0.05)
    print(f"\nRead complete: {len(records)} records.")
    return records


def write_fk_values(records):
    """Write FID 30 = FID 28 value for all records."""
    total   = len(records)
    written = 0
    errors  = 0

    for i in range(0, total, WRITE_BATCH):
        batch = records[i : i + WRITE_BATCH]
        data  = [
            {
                str(FID_RECID): {"value": int(r[str(FID_RECID)]["value"])},
                str(FID_FK):    {"value": r[str(FID_FORMULA)]["value"]},
            }
            for r in batch
            if r.get(str(FID_FORMULA), {}).get("value")   # skip blanks
        ]
        if not data:
            written += len(batch)
            continue

        payload = {
            "to": RS_TABLE,
            "data": data,
            "mergeFieldId": FID_RECID,
            "fieldsToReturn": [],
        }
        try:
            resp = qb_post("https://api.quickbase.com/v1/records", payload)
            processed = resp.get("metadata", {}).get("lineErrors", {})
            errors += len(processed)
        except Exception as e:
            print(f"\nWrite error at batch {i}: {e}")
            errors += len(data)

        written += len(batch)
        pct = written / total * 100
        print(f"  Wrote {written} / {total} ({pct:.0f}%)  errors={errors}", end="\r")
        time.sleep(WRITE_DELAY)

    print(f"\nDone. Written={written}, Errors={errors}")


if __name__ == "__main__":
    print("=== Populate Retailer Sales FK (FID 30) from formula (FID 28) ===\n")
    rows = read_all_formula_values()
    write_fk_values(rows)
    print("\nFID 30 populated. Summary fields on relationship 30 will now compute.")
