"""
push_validation_qb.py  —  Push validation_results.json → QB Projections table
                           via QB REST API (no CData).

Uses the upsert endpoint with mergeFieldId=292 (Acct#-MStyle Key),
batched at 500 records per call.  Skips Validation_Flag (1572) and
Validation_Comments (1573) — those are user-owned fields.

Usage:
    python push_validation_qb.py [--dry-run] [--batch-size N] [path/to/validation_results.json]
"""

import json
import sys
import time
import urllib.request
import urllib.error
import argparse
from pathlib import Path

# ── QB connection ─────────────────────────────────────────────────────────────
QB_REALM   = "pim.quickbase.com"
QB_TOKEN   = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_TABLE   = "bpd237tvm"   # InventoryTrack → Projections
MERGE_FID  = 292           # Acct#-MStyle (Key)

# Validation field IDs (user-owned 1572/1573 intentionally omitted)
FID_KEY       = 292
FID_PRIORITY  = 1574
FID_PATTERN   = 1575
FID_MAX_SEV   = 1576
FID_N_FLAGS   = 1577
FID_BIWEEKLY  = 1578
FID_NARRATIVE = 1579
FID_AI_MODEL  = 1580
FID_PROJ_WK   = 1581

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
}

UPSERT_URL = "https://api.quickbase.com/v1/records"


def rec_to_row(rec):
    return {
        str(FID_KEY):       {"value": rec["key"]},
        str(FID_PRIORITY):  {"value": rec.get("priority") or "LOW"},
        str(FID_PATTERN):   {"value": rec.get("pattern") or ""},
        str(FID_MAX_SEV):   {"value": rec.get("max_severity") or "OK"},
        str(FID_N_FLAGS):   {"value": int(rec.get("n_flags") or 0)},
        str(FID_BIWEEKLY):  {"value": bool(rec.get("biweekly"))},
        str(FID_NARRATIVE): {"value": rec.get("narrative") or ""},
        str(FID_AI_MODEL):  {"value": rec.get("ai_model") or ""},
        str(FID_PROJ_WK):   {"value": float(rec.get("proj_per_wk") or 0)},
    }


def upsert_batch(rows, dry_run=False):
    payload = json.dumps({
        "to":          QB_TABLE,
        "mergeFieldId": MERGE_FID,
        "data":        rows,
    }).encode("utf-8")

    if dry_run:
        return len(rows), 0

    req = urllib.request.Request(
        UPSERT_URL, data=payload, headers=HEADERS, method="POST"
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
                updated = result.get("metadata", {}).get("totalNumberOfRecordsProcessed", len(rows))
                return updated, 0
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"  [WARN] HTTP {e.code} on attempt {attempt}: {body[:200]}", flush=True)
            if attempt < 3:
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  [WARN] Error on attempt {attempt}: {e}", flush=True)
            if attempt < 3:
                time.sleep(2 ** attempt)
    return 0, len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", nargs="?",
                        default=str(Path(__file__).parent / "validation_results.json"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and batch but do not send to QB")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    print(f"\nLoading {args.json_path} ...", flush=True)
    with open(args.json_path, encoding="utf-8") as f:
        data = json.load(f)

    records = data["records"]
    total   = len(records)
    bs      = args.batch_size
    batches = (total + bs - 1) // bs

    print(f"  {total} records  |  {batches} batches of {bs}  |  "
          f"{'DRY RUN' if args.dry_run else 'LIVE'}", flush=True)
    print(f"\nPushing to QB table {QB_TABLE} (mergeFieldId={MERGE_FID}) ...", flush=True)

    ok_total = fail_total = 0
    t0 = time.time()

    for i in range(0, total, bs):
        batch_rows = [rec_to_row(r) for r in records[i:i + bs]]
        ok, fail   = upsert_batch(batch_rows, dry_run=args.dry_run)
        ok_total   += ok
        fail_total += fail
        done = i + len(batch_rows)
        pct  = done / total * 100
        print(f"  {done:>5}/{total}  ({pct:.0f}%)  batch ok={ok} fail={fail}", flush=True)
        # Brief pause between batches to stay friendly to QB
        if not args.dry_run and i + bs < total:
            time.sleep(0.3)

    elapsed = time.time() - t0
    print(f"\n{'='*50}", flush=True)
    print(f"  Done in {elapsed:.1f}s  —  "
          f"{ok_total} pushed  /  {fail_total} failed", flush=True)

    if fail_total:
        sys.exit(1)


if __name__ == "__main__":
    main()
