"""
backfill_variant_asins.py
=========================
Copy ASIN (Cust SKU#, fid 821) from base-style Amazon projection records to
variant-style records that are missing an ASIN.

Variants are identified by the SWITCHOVER_SUFFIXES: EC, COS, AMZ, DS, DTC.
If a variant mstyle (e.g. FF7266COS) has no ASIN and its base (FF7266) has one,
the base ASIN is written back to the variant record.

Uses QB REST API directly -- no CData dependency, no throttle risk.

Usage:
    python backfill_variant_asins.py [--dry-run] [--batch-size N]
"""

import json
import sys
import time
import urllib.request
import urllib.error
import argparse

# ── QB connection ─────────────────────────────────────────────────────────────
QB_REALM   = "pim.quickbase.com"
QB_TOKEN   = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_TABLE   = "bpd237tvm"   # Projections

# Field IDs (Projections table)
FID_KEY    = 292   # Acct# - MStyle (Key) -- merge key for upserts
FID_MSTYLE = 196   # Mstyle
FID_STATUS = 10    # Status @ Cust
FID_CUST   = 874   # Customr Name (typo is in QB -- no trailing 'e')
FID_CUST_SKU = 821   # Cust SKU#  -- writable text field (fid 817 "ASIN" is a lookup, not writable)

# Match same suffixes as SWITCHOVER_SUFFIXES in the forecaster.
# Ordered longest-first so "DTC" matches before a hypothetical "C" etc.
SWITCHOVER_SUFFIXES = ("DTC", "COS", "AMZ", "DS", "EC")

# Valid Amazon ASIN: exactly 10 uppercase alphanumeric characters starting with B
import re as _re
_ASIN_RE = _re.compile(r'^B[A-Z0-9]{9}$')

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
}

QUERY_URL  = "https://api.quickbase.com/v1/records/query"
UPSERT_URL = "https://api.quickbase.com/v1/records"


# ── QB helpers ────────────────────────────────────────────────────────────────

def _qb_post(url, payload_dict, label=""):
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=HEADERS, method="POST")
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"  [WARN] {label} HTTP {e.code} attempt {attempt}: {body[:300]}", flush=True)
            if attempt < 3:
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  [WARN] {label} error attempt {attempt}: {e}", flush=True)
            if attempt < 3:
                time.sleep(2 ** attempt)
    return None


def fetch_all_amazon_active():
    """
    Fetch all active Amazon Projections rows: Key, Mstyle, ASIN.
    Filters: Status starts with 'A' or 'FD'  AND  Customer contains 'amazon'.
    Paginates automatically (QB caps at 1000/page).
    """
    where = "({'10'.SW.'A'}OR{'10'.SW.'FD'})AND{'874'.CT.'amazon'}"
    select = [FID_KEY, FID_MSTYLE, FID_CUST_SKU]

    all_rows = []
    skip = 0
    top  = 1000
    while True:
        result = _qb_post(QUERY_URL, {
            "from":    QB_TABLE,
            "select":  select,
            "where":   where,
            "options": {"skip": skip, "top": top},
        }, label="query")

        if result is None:
            print("  [ERROR] Query failed after retries.", flush=True)
            sys.exit(1)

        batch = result.get("data", [])
        all_rows.extend(batch)

        meta  = result.get("metadata", {})
        total = meta.get("totalRecords", len(all_rows))
        if len(batch) < top or len(all_rows) >= total:
            break
        skip += top
        time.sleep(0.2)

    return all_rows


def cell_val(rec, fid):
    """Extract a scalar value from a QB REST record row."""
    v = (rec.get(str(fid)) or {}).get("value")
    return (v or "").strip() if isinstance(v, str) else (str(v).strip() if v is not None else "")


# ── Suffix logic ──────────────────────────────────────────────────────────────

def strip_suffix(mstyle):
    """
    Return (base_mstyle, matched_suffix) if mstyle ends in a SWITCHOVER suffix.
    Returns (None, None) if no suffix matches or the mstyle would reduce to empty.
    """
    ms_upper = mstyle.upper()
    for sfx in SWITCHOVER_SUFFIXES:
        if ms_upper.endswith(sfx) and len(mstyle) > len(sfx):
            return mstyle[: -len(sfx)], sfx
    return None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill ASIN from base mstyle to variant Amazon projections."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to QB")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Records per QB upsert call (default 500)")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"\nBackfill Variant ASINs  [{mode}]", flush=True)
    print("=" * 50, flush=True)
    print("Fetching active Amazon projections from QB ...", flush=True)

    raw = fetch_all_amazon_active()
    print(f"  {len(raw)} records fetched", flush=True)

    # Parse all records
    parsed = [
        {
            "key":    cell_val(r, FID_KEY),
            "mstyle": cell_val(r, FID_MSTYLE),
            "asin":   cell_val(r, FID_CUST_SKU),
        }
        for r in raw
    ]

    # Build source index: UPPER(mstyle) -> first VALID ASIN found.
    # Only accept real ASINs (10-char B-prefixed alphanumeric).
    # Some records have status text like "Active: Replen" in the ASIN field by
    # mistake; exclude those so they are never propagated to variants.
    source    = {}   # upper-mstyle -> valid asin
    bad_asins = []   # (key, mstyle, bad_value) -- flagged for user attention

    for r in parsed:
        if not r["asin"]:
            continue
        if _ASIN_RE.match(r["asin"].upper()):
            ms_up = r["mstyle"].upper()
            if ms_up not in source:
                source[ms_up] = r["asin"].upper()
        else:
            bad_asins.append((r["key"], r["mstyle"], r["asin"]))

    # Find variant records that need ASIN inherited from their base style
    targets = []
    skipped_no_base = []

    for r in parsed:
        if r["asin"]:
            continue   # already has ASIN -- nothing to do

        base, sfx = strip_suffix(r["mstyle"])
        if base is None:
            continue   # not a variant suffix we recognise

        inherited = source.get(base.upper())
        if not inherited:
            skipped_no_base.append((r["key"], r["mstyle"], base, sfx))
            continue   # base has no ASIN either -- can't inherit

        targets.append({
            "key":    r["key"],
            "mstyle": r["mstyle"],
            "base":   base,
            "suffix": sfx,
            "asin":   inherited,
        })

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n  {len(parsed)} Amazon active records total", flush=True)
    print(f"  {len(source)} records have a valid ASIN  (source pool)", flush=True)
    print(f"  {len(bad_asins)} records have a non-ASIN value in the ASIN field "
          f"(will NOT be propagated)", flush=True)
    print(f"  {len(skipped_no_base)} variant records skipped "
          f"(base style also has no ASIN)", flush=True)
    print(f"  {len(targets)} variant records will receive inherited ASIN", flush=True)

    if bad_asins:
        print(f"\n  WARNING -- non-ASIN values in Cust SKU# field (fix manually):", flush=True)
        for key, ms, val in bad_asins:
            print(f"    {key:<35}  {ms:<22}  value='{val}'", flush=True)

    if skipped_no_base:
        print(f"\n  Skipped (base has no valid ASIN):", flush=True)
        for key, ms, base, sfx in skipped_no_base:
            print(f"    {key:<35}  {ms}  (base={base}, sfx={sfx})", flush=True)

    if not targets:
        print("\n  Nothing to update.", flush=True)
        return

    print(f"\n  Updates to apply:", flush=True)
    for t in targets:
        print(f"    {t['key']:<35}  {t['mstyle']:<22} <- base {t['base']:<20}  ASIN={t['asin']}",
              flush=True)

    if args.dry_run:
        print(f"\n  [DRY RUN] {len(targets)} records would be written to QB.", flush=True)
        return

    # ── Upsert ────────────────────────────────────────────────────────────────
    upsert_rows = [
        {
            str(FID_KEY):  {"value": t["key"]},
            str(FID_CUST_SKU): {"value": t["asin"]},
        }
        for t in targets
    ]

    bs = args.batch_size
    print(f"\nWriting to QB ({len(upsert_rows)} records, batch={bs}) ...", flush=True)

    ok_total = fail_total = 0
    t0 = time.time()

    for i in range(0, len(upsert_rows), bs):
        chunk = upsert_rows[i : i + bs]
        result = _qb_post(UPSERT_URL, {
            "to":           QB_TABLE,
            "mergeFieldId": FID_KEY,
            "data":         chunk,
        }, label="upsert")

        if result is None:
            fail_total += len(chunk)
            print(f"  batch {i}-{i+len(chunk)} FAILED", flush=True)
        else:
            meta = result.get("metadata", {})
            ok = (len(meta.get("createdRecordIds", []))
                  + len(meta.get("updatedRecordIds", []))
                  + len(meta.get("unchangedRecordIds", [])))
            fail = max(0, len(chunk) - ok)
            if meta.get("lineErrors"):
                print(f"  [WARN] lineErrors: {meta['lineErrors']}", flush=True)
            ok_total   += ok
            fail_total += fail

        done = i + len(chunk)
        print(f"  {done}/{len(upsert_rows)}  ok={ok_total}  fail={fail_total}", flush=True)

        if i + bs < len(upsert_rows):
            time.sleep(0.2)

    elapsed = time.time() - t0
    print(f"\n{'='*50}", flush=True)
    print(f"  Done in {elapsed:.1f}s  --  {ok_total} written / {fail_total} failed",
          flush=True)

    if fail_total:
        sys.exit(1)


if __name__ == "__main__":
    main()
