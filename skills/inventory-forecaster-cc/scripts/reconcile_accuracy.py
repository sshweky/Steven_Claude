"""
reconcile_accuracy.py -- Reconcile snapshot projections against actuals.

Reads PRJ_Snapshot rows, determines which week slots are now "mature" (the
actual week has passed), looks up actuals from Actuals_Weekly, writes
W01_Actual ... W26_Actual back to PRJ_Snapshot, then prints an accuracy
summary with WAPE / bias / hit-rate by lead-time bucket and snapshot month.

A week N in a snapshot row is "mature" when:
    W1_Date + (N-1)*7 days < today

(meaning that full week has completed and actuals should be available.)

Pre-requisites:
  - create_accuracy_tables.py must have been run
  - snapshot_weekly.py must have populated PRJ_Snapshot and Actuals_Weekly

Usage:
    python reconcile_accuracy.py [--dry-run] [--since YYYY-MM-DD]

    --dry-run            Print changes without writing to QB
    --since YYYY-MM-DD   Only reconcile snapshot rows with Snapshot_Date >= this date
"""

import argparse
import json
import sys
import time
import os
from collections import defaultdict
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Bootstrap imports
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

QB_BASE = "https://api.quickbase.com/v1"

# Lead-time buckets for accuracy reporting
BUCKET_DEFS = [
    ("W1-W4",   range(1,  5)),
    ("W5-W13",  range(5, 14)),
    ("W14-W26", range(14, 27)),
]

# ---------------------------------------------------------------------------
# QB REST helper (identical retry pattern to snapshot_weekly.py)
# ---------------------------------------------------------------------------

def _headers():
    return {
        "QB-Realm-Hostname": config.QB_REALM,
        "Authorization":     f"QB-USER-TOKEN {config.QB_USER_TOKEN}",
        "Content-Type":      "application/json",
    }


def _qb_request(method, path, payload=None):
    """QB REST call with exponential backoff."""
    import urllib.request
    import urllib.error

    url  = QB_BASE + path
    data = json.dumps(payload).encode() if payload else None

    for attempt in range(1, config.QB_REST_MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
            with urllib.request.urlopen(req, timeout=90) as resp:
                elapsed_ms = (time.monotonic() - start) * 1000
                body = resp.read()
                if not body:
                    raise RuntimeError("Empty 200 response (throttle signal)")
                if elapsed_ms > config.QB_LATENCY_WARN_MS:
                    print(f"  [WARN] QB latency {elapsed_ms:.0f}ms -- realm may be under pressure")
                if elapsed_ms > config.QB_LATENCY_ABORT_MS:
                    # Warn but do not abort -- reconcile is a weekly batch, not interactive.
                    print(f"  [WARN] QB latency {elapsed_ms:.0f}ms exceeds abort threshold -- adding 5s cooldown")
                    time.sleep(5)
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code in (429, 502, 503, 504):
                if attempt >= config.QB_REST_MAX_RETRIES:
                    raise RuntimeError(
                        f"QB throttle {exc.code} after {attempt} attempts"
                    ) from exc
                delay = 2 ** attempt
                print(f"  [WARN] QB {exc.code} attempt {attempt}, backoff {delay}s ...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"QB HTTP {exc.code}: {body_text}") from exc
        except RuntimeError:
            raise
        except Exception as exc:
            if attempt >= config.QB_REST_MAX_RETRIES:
                raise
            delay = 2 ** attempt
            print(f"  [WARN] QB error attempt {attempt} ({exc}), backoff {delay}s ...")
            time.sleep(delay)

    raise RuntimeError("QB request failed after all retries")


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def _fetch_all(table_id, select_fids, where_clause=None):
    """Fetch all rows from a QB table via paginated POST /records/query.

    Returns list of raw record dicts {fid: {value: ...}, ...}.
    """
    all_records = []
    skip = 0
    page_size = 10000

    payload_base = {
        "from":   table_id,
        "select": select_fids,
        "options": {
            "skip": skip,
            "top":  page_size,
            "compareWithAppLocalTime": False,
        },
    }
    if where_clause:
        payload_base["where"] = where_clause

    while True:
        payload = dict(payload_base)
        payload["options"] = {"skip": skip, "top": page_size,
                              "compareWithAppLocalTime": False}
        if where_clause:
            payload["where"] = where_clause

        time.sleep(config.QB_INTER_CALL_DELAY_S)
        resp = _qb_request("POST", "/records/query", payload)
        data = resp.get("data", [])

        if not data:
            break

        all_records.extend(data)
        count = len(all_records)
        if count % 500 == 0 or len(data) < page_size:
            print(f"    Fetched {count} rows from {table_id} ...")

        if len(data) < page_size:
            break
        skip += page_size

    return all_records


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def _val(cell):
    """Extract scalar from a QB {value: ...} cell or raw scalar."""
    if isinstance(cell, dict):
        return cell.get("value")
    return cell


def _safe_float(cell, default=0.0):
    v = _val(cell)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_str(cell, default=""):
    v = _val(cell)
    if v is None:
        return default
    return str(v)


def _parse_date(cell):
    """Parse a QB date cell (YYYY-MM-DD string or date obj) to date, or None."""
    v = _val(cell)
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Pre-flight config checks
# ---------------------------------------------------------------------------

def _check_config():
    snap_tid  = getattr(config, "QB_PRJ_SNAPSHOT_TID",   "")
    act_tid   = getattr(config, "QB_ACTUALS_WEEKLY_TID",  "")
    snap_fids = getattr(config, "PRJ_SNAP_FIDS",  {})
    act_fids  = getattr(config, "ACT_WEEKLY_FIDS", {})

    if not snap_tid or "PLACEHOLDER" in snap_tid:
        raise SystemExit("QB_PRJ_SNAPSHOT_TID not configured. Run create_accuracy_tables.py first.")
    if not act_tid or "PLACEHOLDER" in act_tid:
        raise SystemExit("QB_ACTUALS_WEEKLY_TID not configured. Run create_accuracy_tables.py first.")

    required_snap = ["Snapshot_Key", "Key", "Snapshot_Date", "W1_Date",
                     "W01", "W01_Actual"]
    for k in required_snap:
        if not snap_fids.get(k):
            raise SystemExit(
                f"PRJ_SNAP_FIDS['{k}'] is 0 -- fill in FIDs from create_accuracy_tables.py."
            )

    required_act = ["Week_Key", "Key", "Week_Date", "Ord_Units"]
    for k in required_act:
        if not act_fids.get(k):
            raise SystemExit(
                f"ACT_WEEKLY_FIDS['{k}'] is 0 -- fill in FIDs from create_accuracy_tables.py."
            )

    return snap_tid, act_tid, snap_fids, act_fids


# ---------------------------------------------------------------------------
# Step 1: Read PRJ_Snapshot
# ---------------------------------------------------------------------------

def fetch_snapshot_rows(snap_tid, snap_fids, since_date=None):
    """Return list of snapshot row dicts with resolved field values."""
    # Build select list: record ID (3), plus all our fields
    select_fids = [3]  # Record ID#
    snap_field_names = [
        "Snapshot_Key", "Key", "Snapshot_Date", "W1_Date",
    ]
    # W01-W26 actuals only (to check which are already filled)
    for w in range(1, 27):
        snap_field_names.append(f"W{w:02d}_Actual")

    for name in snap_field_names:
        fid = snap_fids.get(name)
        if fid and fid not in select_fids:
            select_fids.append(fid)

    # Also add W01-W26 projected values (needed for accuracy calc)
    for w in range(1, 27):
        fid = snap_fids.get(f"W{w:02d}")
        if fid and fid not in select_fids:
            select_fids.append(fid)

    where_clause = None
    if since_date:
        sd_fid = snap_fids.get("Snapshot_Date")
        if sd_fid:
            where_clause = f"{{{sd_fid}.OAF.'{since_date.isoformat()}'}}"

    print(f"  Fetching PRJ_Snapshot rows (since {since_date}) ...")
    raw = _fetch_all(snap_tid, select_fids, where_clause)
    print(f"  {len(raw)} PRJ_Snapshot rows fetched")

    # Normalize into dicts with meaningful keys
    rows = []
    for rec in raw:
        row = {
            "record_id":     _safe_float(rec.get("3") or rec.get(3), default=0),
            "snapshot_key":  _safe_str(rec.get(str(snap_fids.get("Snapshot_Key")))
                                        or rec.get(snap_fids.get("Snapshot_Key"))),
            "key":           _safe_str(rec.get(str(snap_fids.get("Key")))
                                        or rec.get(snap_fids.get("Key"))),
            "snapshot_date": _parse_date(rec.get(str(snap_fids.get("Snapshot_Date")))
                                          or rec.get(snap_fids.get("Snapshot_Date"))),
            "w1_date":       _parse_date(rec.get(str(snap_fids.get("W1_Date")))
                                          or rec.get(snap_fids.get("W1_Date"))),
        }
        for w in range(1, 27):
            prj_fid = snap_fids.get(f"W{w:02d}")
            act_fid = snap_fids.get(f"W{w:02d}_Actual")
            row[f"w{w:02d}"]        = _safe_float(
                rec.get(str(prj_fid)) or rec.get(prj_fid)
            )
            row[f"w{w:02d}_actual"] = _safe_float(
                rec.get(str(act_fid)) or rec.get(act_fid),
                default=-1.0,   # -1 flags "not yet filled" vs 0 actual
            )
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Step 2: Read Actuals_Weekly into in-memory dict
# ---------------------------------------------------------------------------

def fetch_actuals_dict(act_tid, act_fids):
    """Return dict {week_key_str -> ord_units_float}.

    week_key_str format: "KeyValue|YYYY-MM-DD"
    """
    wk_fid  = act_fids.get("Week_Key")
    ou_fid  = act_fids.get("Ord_Units")

    select_fids = [fid for fid in [wk_fid, ou_fid] if fid]

    print("  Fetching Actuals_Weekly ...")
    raw = _fetch_all(act_tid, select_fids)
    print(f"  {len(raw)} Actuals_Weekly rows fetched")

    actuals = {}
    for rec in raw:
        wk  = _safe_str(rec.get(str(wk_fid)) or rec.get(wk_fid))
        val = _safe_float(rec.get(str(ou_fid)) or rec.get(ou_fid))
        if wk:
            actuals[wk] = val

    return actuals


# ---------------------------------------------------------------------------
# Step 3: Determine mature weeks and collect updates
# ---------------------------------------------------------------------------

def collect_updates(snap_rows, actuals_dict, today, snap_fids):
    """Return list of update dicts ready for QB batch write.

    Each update dict: {record_id_fid: value, actual_fid: value, ...}
    Only rows with at least one new actual are included.
    """
    updates = []
    stats = {
        "rows_checked":    len(snap_rows),
        "rows_updated":    0,
        "actuals_matched": 0,
        "actuals_missing": 0,
    }

    for row in snap_rows:
        w1_date = row.get("w1_date")
        key_val = row.get("key")
        rec_id  = int(row.get("record_id", 0))

        if not w1_date or not key_val or not rec_id:
            continue

        new_actuals = {}  # week_num -> actual_value

        for w in range(1, 27):
            # Is this week mature?
            week_start = w1_date + timedelta(days=(w - 1) * 7)
            if week_start >= today:
                continue  # not yet passed

            # Already filled?
            current_actual = row.get(f"w{w:02d}_actual", -1.0)
            if current_actual >= 0:
                continue  # already reconciled

            # Look up actual
            week_date_str = week_start.isoformat()
            lookup_key    = f"{key_val}|{week_date_str}"
            if lookup_key in actuals_dict:
                new_actuals[w] = actuals_dict[lookup_key]
                stats["actuals_matched"] += 1
            else:
                stats["actuals_missing"] += 1

        if not new_actuals:
            continue

        # Build QB update row
        update_row = {3: {"value": rec_id}}  # Record ID# as merge key
        for w, val in new_actuals.items():
            act_fid = snap_fids.get(f"W{w:02d}_Actual")
            if act_fid:
                update_row[act_fid] = {"value": val}

        updates.append(update_row)
        # Store resolved actuals back into row for accuracy calc below
        for w, val in new_actuals.items():
            row[f"w{w:02d}_actual"] = val

        stats["rows_updated"] += 1

    return updates, stats


# ---------------------------------------------------------------------------
# Step 4: Batch write back to PRJ_Snapshot
# ---------------------------------------------------------------------------

def write_updates(snap_tid, updates, dry_run=False):
    """Batch-upsert updated rows using Record ID# (FID 3) as merge key."""
    batch_size = config.QB_BULK_BATCH
    total = 0
    errors = []

    for start in range(0, len(updates), batch_size):
        chunk = updates[start : start + batch_size]
        if dry_run:
            print(f"  [DRY-RUN] Would write {len(chunk)} actuals back to PRJ_Snapshot "
                  f"(batch {start//batch_size + 1})")
            total += len(chunk)
            continue

        payload = {
            "to":           snap_tid,
            "data":         chunk,
            "mergeFieldId": 3,   # Record ID#
            "fieldsToReturn": [],
        }
        time.sleep(config.QB_INTER_CALL_DELAY_S)
        try:
            resp = _qb_request("POST", "/records", payload)
            processed = resp.get("metadata", {}).get(
                "totalNumberOfRecordsProcessed", len(chunk)
            )
            total += processed
            if total % 500 == 0:
                print(f"  Wrote {total} rows so far ...")
        except Exception as exc:
            errors.append(f"batch {start}-{start + len(chunk)}: {exc}")
            print(f"  [ERROR] {errors[-1]}")

    return total, errors


# ---------------------------------------------------------------------------
# Step 5: Accuracy summary
# ---------------------------------------------------------------------------

def _wape(proj_list, actual_list):
    """Weighted Absolute Percentage Error = sum|A-P| / sum|A|."""
    sum_err = sum(abs(a - p) for p, a in zip(proj_list, actual_list))
    sum_act = sum(abs(a) for a in actual_list)
    return (sum_err / sum_act) if sum_act > 0 else None


def _bias(proj_list, actual_list):
    """Mean signed % error = mean((P-A)/A).  Positive = over-projection."""
    errors = []
    for p, a in zip(proj_list, actual_list):
        if a != 0:
            errors.append((p - a) / a)
    return (sum(errors) / len(errors)) if errors else None


def _hit_rate(proj_list, actual_list, threshold=0.15):
    """Fraction of pairs where |P-A|/A <= threshold."""
    hits = 0
    valid = 0
    for p, a in zip(proj_list, actual_list):
        if a != 0:
            valid += 1
            if abs(p - a) / abs(a) <= threshold:
                hits += 1
    return (hits / valid) if valid > 0 else None


def _bucket_pairs(rows, w_range):
    """Collect all (projected, actual) pairs for weeks in w_range."""
    proj_vals = []
    act_vals  = []
    for row in rows:
        w1 = row.get("w1_date")
        if w1 is None:
            continue
        for w in w_range:
            p = row.get(f"w{w:02d}", 0.0)
            a = row.get(f"w{w:02d}_actual", -1.0)
            if a < 0:   # not yet reconciled
                continue
            proj_vals.append(p)
            act_vals.append(a)
    return proj_vals, act_vals


def print_accuracy_summary(snap_rows, today):
    """Compute and print WAPE / bias / hit-rate grouped by bucket and month."""
    # Only include rows that have at least one reconciled actual
    eligible = [
        r for r in snap_rows
        if any(r.get(f"w{w:02d}_actual", -1.0) >= 0 for w in range(1, 27))
    ]

    if not eligible:
        print("\n  No reconciled actuals found -- accuracy summary unavailable.")
        return

    print()
    print("=" * 72)
    print("ACCURACY SUMMARY")
    print("=" * 72)
    print(f"  Snapshot rows with at least one actual: {len(eligible)}")
    print()

    # Overall by bucket
    print(f"  {'Bucket':<12}  {'WAPE':>7}  {'Bias':>7}  {'Hit15%':>7}  {'N pairs':>8}")
    print(f"  {'-'*12}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*8}")
    for bucket_name, w_range in BUCKET_DEFS:
        pv, av = _bucket_pairs(eligible, w_range)
        wape_v   = _wape(pv, av)
        bias_v   = _bias(pv, av)
        hit15_v  = _hit_rate(pv, av, 0.15)
        n        = len(pv)
        wape_s   = f"{wape_v*100:6.1f}%" if wape_v  is not None else "  N/A  "
        bias_s   = f"{bias_v*100:+6.1f}%" if bias_v  is not None else "  N/A  "
        hit15_s  = f"{hit15_v*100:6.1f}%" if hit15_v is not None else "  N/A  "
        print(f"  {bucket_name:<12}  {wape_s:>7}  {bias_s:>7}  {hit15_s:>7}  {n:>8,}")

    # By snapshot month
    monthly = defaultdict(list)
    for row in eligible:
        sd = row.get("snapshot_date")
        if sd:
            month_key = sd.strftime("%Y-%m")
            monthly[month_key].append(row)

    if len(monthly) > 1:
        print()
        print(f"  By Snapshot Month:")
        print(f"  {'Month':<10}  {'Bucket':<12}  {'WAPE':>7}  {'Bias':>7}  {'Hit15%':>7}  {'N pairs':>8}")
        print(f"  {'-'*10}  {'-'*12}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*8}")
        for month_key in sorted(monthly.keys()):
            month_rows = monthly[month_key]
            for bucket_name, w_range in BUCKET_DEFS:
                pv, av = _bucket_pairs(month_rows, w_range)
                if not pv:
                    continue
                wape_v  = _wape(pv, av)
                bias_v  = _bias(pv, av)
                hit15_v = _hit_rate(pv, av, 0.15)
                n       = len(pv)
                wape_s  = f"{wape_v*100:6.1f}%"  if wape_v  is not None else "  N/A  "
                bias_s  = f"{bias_v*100:+6.1f}%" if bias_v  is not None else "  N/A  "
                hit15_s = f"{hit15_v*100:6.1f}%" if hit15_v is not None else "  N/A  "
                print(f"  {month_key:<10}  {bucket_name:<12}  {wape_s:>7}  {bias_s:>7}  {hit15_s:>7}  {n:>8,}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(dry_run=False, since_date=None):
    today = date.today()
    print("=" * 72)
    print("reconcile_accuracy.py -- Projection Accuracy Reconciliation")
    print(f"Run date: {today.isoformat()}")
    if since_date:
        print(f"Lookback since: {since_date.isoformat()}")
    print("=" * 72)

    # Config checks
    snap_tid, act_tid, snap_fids, act_fids = _check_config()
    print(f"\nPRJ_Snapshot table:    {snap_tid}")
    print(f"Actuals_Weekly table:  {act_tid}")

    # ── Step 1: Read PRJ_Snapshot ─────────────────────────────────────────
    print("\nStep 1: Reading PRJ_Snapshot ...")
    snap_rows = fetch_snapshot_rows(snap_tid, snap_fids, since_date=since_date)

    # ── Step 2: Read Actuals_Weekly ───────────────────────────────────────
    print("\nStep 2: Reading Actuals_Weekly ...")
    actuals_dict = fetch_actuals_dict(act_tid, act_fids)
    print(f"  Actuals dict size: {len(actuals_dict):,} entries")

    # ── Step 3: Collect updates ───────────────────────────────────────────
    print("\nStep 3: Determining mature weeks and matching actuals ...")
    updates, collect_stats = collect_updates(snap_rows, actuals_dict, today, snap_fids)

    print(f"  Rows checked:         {collect_stats['rows_checked']:,}")
    print(f"  Rows with new data:   {collect_stats['rows_updated']:,}")
    print(f"  Actuals matched:      {collect_stats['actuals_matched']:,}")
    print(f"  Week slots missing:   {collect_stats['actuals_missing']:,}")

    # ── Step 4: Write back ────────────────────────────────────────────────
    if updates:
        print(f"\nStep 4: Writing {len(updates)} updated rows to PRJ_Snapshot ...")
        written, errors = write_updates(snap_tid, updates, dry_run=dry_run)
        print(f"  Written: {written}")
        if errors:
            print(f"  Errors ({len(errors)}):")
            for e in errors:
                print(f"    {e}")
    else:
        print("\nStep 4: No new actuals to write -- PRJ_Snapshot is up to date.")
        written = 0
        errors  = []

    # ── Step 5: Accuracy summary (on updated in-memory rows) ──────────────
    print_accuracy_summary(snap_rows, today)

    # ── Final summary ─────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("RUN COMPLETE")
    print("=" * 72)
    print(f"  Snapshot rows read:   {len(snap_rows):,}")
    print(f"  Actuals loaded:       {len(actuals_dict):,}")
    print(f"  Rows reconciled:      {collect_stats['rows_updated']:,}")
    print(f"  QB rows written:      {written:,}")
    if errors:
        print(f"  Errors:               {len(errors)}")
    if dry_run:
        print()
        print("  [DRY-RUN] No records were actually written to Quickbase.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Reconcile PRJ_Snapshot projections against Actuals_Weekly."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print changes without writing to QB.",
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", default=None,
        help="Only process snapshot rows with Snapshot_Date >= this date.",
    )
    args = parser.parse_args()

    since = None
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            raise SystemExit(f"Invalid --since date: {args.since!r}  (expected YYYY-MM-DD)")

    run(dry_run=args.dry_run, since_date=since)
