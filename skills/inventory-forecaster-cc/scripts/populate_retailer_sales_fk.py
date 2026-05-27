#!/usr/bin/env python3
"""
Populate Retailer Sales FID 30 (FK for relationship 30 to Projections) from
FID 28 (formula field that computes Acct#-MStyle key, e.g. "1864-FF12655").

Streams in pages of 500, writes each page immediately (no large in-memory buffer).
Checkpoints progress to disk -- safe to Ctrl-C and resume.

Usage:
    python populate_retailer_sales_fk.py           # fresh run or resume from checkpoint
    python populate_retailer_sales_fk.py --reset   # delete checkpoint and restart
"""
import time
import json
import sys
import os
import urllib.request
import urllib.error

QB_REALM    = "pim.quickbase.com"
QB_TOKEN    = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
RS_TABLE    = "bv2izcn5b"   # Retailer Sales
FID_RECID   = 3
FID_FORMULA = 28            # Acct# - MStyle (key for Projections) [formula]
FID_FK      = 30            # Acct# - MStyle (key for Projections)2 [FK for rel 30]

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
    "User-Agent":        "petspeople-populate-fk/1.1",
}
READ_PAGE   = 500    # smaller pages to avoid 504s
WRITE_BATCH = 500
READ_DELAY  = 0.25   # ~4 reads/s to avoid hammering QB
WRITE_DELAY = 0.25   # ~4 writes/s
CHECKPOINT  = os.path.join(os.path.dirname(__file__), "populate_fk_checkpoint.json")
MAX_RETRIES = 3


def save_checkpoint(last_id, written, errors):
    with open(CHECKPOINT, "w") as f:
        json.dump({"last_id": last_id, "written": written, "errors": errors}, f)


def load_checkpoint():
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT) as f:
            return json.load(f)
    return {"last_id": 0, "written": 0, "errors": 0}


def qb_post(url, body, attempt=1):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (429, 502, 504) and attempt <= MAX_RETRIES:
            wait = 2 ** attempt
            print(f"\n  HTTP {e.code} — backing off {wait}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            return qb_post(url, body, attempt + 1)
        raise


if __name__ == "__main__":
    if "--reset" in sys.argv and os.path.exists(CHECKPOINT):
        os.remove(CHECKPOINT)
        print("Checkpoint deleted. Starting fresh.")

    cp      = load_checkpoint()
    last_id = cp["last_id"]
    written = cp["written"]
    errors  = cp["errors"]

    print(f"=== Populate Retailer Sales FK (FID 30) from formula (FID 28) ===")
    if last_id > 0:
        print(f"Resuming from Record ID > {last_id}  (already written: {written})")
    print()

    total_read  = 0
    total_pages = 0

    while True:
        # --- READ one page of records ---
        payload = {
            "from":    RS_TABLE,
            "select":  [FID_RECID, FID_FORMULA],
            "where":   f"{{3.GT.{last_id}}}",
            "sortBy":  [{"fieldId": FID_RECID, "order": "ASC"}],
            "options": {"top": READ_PAGE},
        }

        try:
            resp = qb_post("https://api.quickbase.com/v1/records/query", payload)
        except Exception as e:
            print(f"\nFatal read error: {e}  — checkpoint saved at Record ID {last_id}")
            save_checkpoint(last_id, written, errors)
            sys.exit(1)

        batch = resp.get("data", [])
        if not batch:
            break   # done

        total_pages += 1
        total_read  += len(batch)

        # --- Compute FK values ---
        write_data = [
            {
                str(FID_RECID): {"value": int(r[str(FID_RECID)]["value"])},
                str(FID_FK):    {"value": r.get(str(FID_FORMULA), {}).get("value", "")},
            }
            for r in batch
            if r.get(str(FID_FORMULA), {}).get("value")   # skip blanks
        ]

        max_id_in_batch = max(int(r[str(FID_RECID)]["value"]) for r in batch)

        # --- WRITE ---
        if write_data:
            write_payload = {
                "to":          RS_TABLE,
                "data":        write_data,
                "mergeFieldId": FID_RECID,
                "fieldsToReturn": [],
            }
            try:
                wr = qb_post("https://api.quickbase.com/v1/records", write_payload)
                line_errors = len(wr.get("metadata", {}).get("lineErrors", {}))
                written += len(write_data) - line_errors
                errors  += line_errors
            except Exception as e:
                print(f"\nWrite error at id {last_id}: {e}")
                errors += len(write_data)

        last_id = max_id_in_batch
        save_checkpoint(last_id, written, errors)

        print(f"  Page {total_pages}: read through id {last_id}  "
              f"cumulative written={written}  errors={errors}", end="\r", flush=True)

        time.sleep(READ_DELAY)
        time.sleep(WRITE_DELAY)

    print(f"\nComplete. Pages={total_pages}  Total read={total_read}  "
          f"Written={written}  Errors={errors}")
    if os.path.exists(CHECKPOINT):
        os.remove(CHECKPOINT)
    print("Checkpoint cleared.")
