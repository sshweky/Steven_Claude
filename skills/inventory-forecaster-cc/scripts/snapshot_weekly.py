"""
snapshot_weekly.py -- Weekly projection snapshot job.

Reads the current state of Projections (bpd237tvm) and writes two outputs:

  1. PRJ_Snapshot   -- W01-W26 manual projection values as of today, keyed by
                       Snapshot_Key = "Key|Snapshot_Date" (composite for merge)
  2. Actuals_Weekly -- Last week's actual orders (Ord_LW) keyed by
                       Week_Key = "Key|Week_Date" (composite for merge)

Designed to run every Sunday after the weekly forecast refresh.

Pre-requisites:
  - create_accuracy_tables.py must have been run and config.py must contain
    QB_PRJ_SNAPSHOT_TID, QB_ACTUALS_WEEKLY_TID, PRJ_SNAP_FIDS, ACT_WEEKLY_FIDS.

Usage:
    python snapshot_weekly.py [--dry-run]

    --dry-run   Print what would be written; no actual QB writes.
"""

import argparse
import json
import re
import sys
import time
import os
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Bootstrap imports
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

QB_BASE = "https://api.quickbase.com/v1"
PROJ_TABLE = config.QB_PROJ_TABLE   # bpd237tvm

# ---------------------------------------------------------------------------
# Retry/REST helpers (shared pattern across scripts)
# ---------------------------------------------------------------------------

def _headers():
    return {
        "QB-Realm-Hostname": config.QB_REALM,
        "Authorization":     f"QB-USER-TOKEN {config.QB_USER_TOKEN}",
        "Content-Type":      "application/json",
    }


def _qb_request(method, path, payload=None):
    """QB REST call with exponential backoff (max 3 retries, 2/4/8s delays)."""
    import urllib.request
    import urllib.error

    url = QB_BASE + path
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
                    # Warn but do not abort -- snapshot is a weekly batch, not interactive.
                    # Slow responses are expected during business hours on bulk reads.
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
# Field-map helpers
# ---------------------------------------------------------------------------

def fetch_field_map(table_id):
    """Return list of field dicts for table_id (GET /v1/fields?tableId=...)."""
    time.sleep(config.QB_INTER_CALL_DELAY_S)
    result = _qb_request("GET", f"/fields?tableId={table_id}")
    if isinstance(result, list):
        return result
    return result.get("fields", result)


def _label_matches(label, *substrings, case_insensitive=True):
    """Return True if all substrings appear in label."""
    check = label.lower() if case_insensitive else label
    return all(s.lower() in check for s in substrings)


# ---------------------------------------------------------------------------
# W1_Date derivation
# ---------------------------------------------------------------------------

_W1_LABEL_RE = re.compile(r"^\s*(\d{2})\s+(\d{2})\s+W(\d+)", re.IGNORECASE)


def _parse_w1_date_from_label(label, reference_year=None):
    """Parse a W1 field label like '05 24 W1' into the week-start Sunday.

    The label format produced by the forecaster is 'MM DD W{n}' where MM DD
    is the Sunday start date of the projection week (P+P weeks run Sun-Sat).
    The date in the label IS the week start -- return it as-is.

    If we cannot parse, returns today.
    """
    if reference_year is None:
        reference_year = date.today().year
    m = _W1_LABEL_RE.match(label)
    if not m:
        return date.today()
    month, day, _ = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        # Build the date in the reference year; if it falls more than 7 days
        # in the future, try prior year (handles Jan/Feb snapshots in Dec)
        candidate = date(reference_year, month, day)
        if candidate > date.today() + timedelta(days=7):
            candidate = date(reference_year - 1, month, day)
        # The MM DD in the label IS the Sunday start of the week
        return candidate
    except ValueError:
        return date.today()


# ---------------------------------------------------------------------------
# Field discovery for Projections table
# ---------------------------------------------------------------------------

_MAN_PRJ_LABEL_RE = re.compile(r"^\s*\d{2}\s+\d{2}\s+W(\d+)", re.IGNORECASE)


def discover_projections_fields(fields):
    """Return a dict of discovered FIDs from the Projections field map.

    Returns:
        {
          "key_fid":        <int>,   # Acct#-MStyle Key
          "status_fid":     <int>,   # Status @ Cust
          "ord_lw_fid":     <int>,   # Ord/LW (last week actuals)
          "man_prj":        {1: fid, 2: fid, ..., 26: fid},  # W1-W26
          "w1_date":        <date>,  # parsed from W1 label
          "w1_label":       <str>,   # raw label of the W1 field
        }
    """
    result = {
        "key_fid":    None,
        "status_fid": None,
        "ord_lw_fid": None,
        "man_prj":    {},
        "w1_date":    None,
        "w1_label":   None,
    }

    for f in fields:
        fid   = f.get("id")
        label = f.get("label", "")

        # Key field: label contains "Acct", "MStyle", and "Key"
        if result["key_fid"] is None and _label_matches(label, "acct", "mstyle", "key"):
            result["key_fid"] = fid
            continue

        # Status @ Cust field: label contains "Status" and "Cust"
        if result["status_fid"] is None and _label_matches(label, "status", "cust"):
            result["status_fid"] = fid
            continue

        # Ord/LW: exactly "Ord/LW" -- NOT Ord_LW_2 etc.
        # QB labels often stored with slashes or spaces
        stripped = re.sub(r"[\s/]+", "", label.upper())
        if stripped == "ORDLW" and result["ord_lw_fid"] is None:
            # Confirm it's not a numbered variant (Ord/LW 2, Ord/LW_2, etc.)
            if not re.search(r"[\s/_]?\d+\s*$", label):
                result["ord_lw_fid"] = fid
                continue

        # MAN_PRJ week fields: label matches "MM DD Wn" exactly
        m = _MAN_PRJ_LABEL_RE.match(label)
        if m:
            week_num = int(m.group(1))
            if 1 <= week_num <= 26:
                result["man_prj"][week_num] = fid
                if week_num == 1:
                    result["w1_label"] = label
                    result["w1_date"]  = _parse_w1_date_from_label(label)

    missing = []
    if result["key_fid"]    is None: missing.append("Acct#-MStyle Key")
    if result["status_fid"] is None: missing.append("Status @ Cust")
    if result["ord_lw_fid"] is None: missing.append("Ord/LW")
    if len(result["man_prj"]) < 26:
        missing.append(f"W1-W26 MAN_PRJ (found {len(result['man_prj'])})")

    if missing:
        print(f"  [WARN] Projections field discovery: missing {missing}")

    return result


# ---------------------------------------------------------------------------
# Projections data fetch (paginated)
# ---------------------------------------------------------------------------

def fetch_projections(fids, status_fid):
    """Fetch all active Projections records.  Returns list of row dicts.

    Each row dict: {fid: value, ...} mirroring QB response data.
    """
    all_records = []
    skip = 0
    page_size = 10000
    select_fids = [fids["key_fid"], status_fid, fids["ord_lw_fid"]] + list(
        fids["man_prj"].values()
    )
    # Remove None from select list (shouldn't happen but be safe)
    select_fids = [f for f in select_fids if f is not None]

    while True:
        payload = {
            "from":   PROJ_TABLE,
            "select": select_fids,
            "where":  f"{{{status_fid}.SW.'A'}}",
            "options": {
                "skip":         skip,
                "top":          page_size,
                "compareWithAppLocalTime": False,
            },
        }
        time.sleep(config.QB_INTER_CALL_DELAY_S)
        resp = _qb_request("POST", "/records/query", payload)
        data = resp.get("data", [])
        fields_meta = resp.get("fields", [])

        if not data:
            break

        all_records.extend(data)
        count = len(all_records)
        if count % 500 == 0 or len(data) < page_size:
            print(f"  Fetched {count} Projections records ...")

        if len(data) < page_size:
            break
        skip += page_size

    print(f"  Total Projections records fetched: {len(all_records)}")
    return all_records


# ---------------------------------------------------------------------------
# Snapshot row builders
# ---------------------------------------------------------------------------

def _safe_int(val):
    """Convert QB value dict or scalar to int, rounding."""
    if val is None:
        return 0
    if isinstance(val, dict):
        val = val.get("value", 0)
    if val is None or val == "":
        return 0
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return 0


def _safe_str(val):
    if isinstance(val, dict):
        val = val.get("value", "")
    return str(val) if val is not None else ""


def build_snapshot_rows(records, fids, snapshot_date, w1_date):
    """Convert raw QB records to PRJ_Snapshot upsert rows.

    Returns list of dicts suitable for use in QB REST batch write.
    The caller must map these to actual FIDs from PRJ_SNAP_FIDS config.
    """
    snap_fids = getattr(config, "PRJ_SNAP_FIDS", {})
    rows = []

    key_fid = fids["key_fid"]
    snapshot_date_str = snapshot_date.isoformat()  # YYYY-MM-DD
    w1_date_str       = w1_date.isoformat()

    for rec in records:
        key_val = _safe_str(rec.get(str(key_fid)) or rec.get(key_fid))
        if not key_val:
            continue

        snapshot_key = f"{key_val}|{snapshot_date_str}"

        row = {}

        # Snapshot_Key (composite merge key)
        sk_fid = snap_fids.get("Snapshot_Key")
        if sk_fid:
            row[sk_fid] = {"value": snapshot_key}

        # Key
        k_fid = snap_fids.get("Key")
        if k_fid:
            row[k_fid] = {"value": key_val}

        # Snapshot_Date
        sd_fid = snap_fids.get("Snapshot_Date")
        if sd_fid:
            row[sd_fid] = {"value": snapshot_date_str}

        # W1_Date
        w1d_fid = snap_fids.get("W1_Date")
        if w1d_fid:
            row[w1d_fid] = {"value": w1_date_str}

        # W01-W26 projection values
        for w in range(1, 27):
            src_fid = fids["man_prj"].get(w)
            dst_fid = snap_fids.get(f"W{w:02d}")
            if src_fid and dst_fid:
                val = _safe_int(rec.get(str(src_fid)) or rec.get(src_fid))
                row[dst_fid] = {"value": val}

        if row:
            rows.append(row)

    return rows


def build_actuals_rows(records, fids, w1_date):
    """Convert raw QB records to Actuals_Weekly upsert rows.

    Week_Date = W1_Date - 7 days (the week that just ended).
    """
    act_fids = getattr(config, "ACT_WEEKLY_FIDS", {})
    rows = []

    key_fid    = fids["key_fid"]
    ord_lw_fid = fids["ord_lw_fid"]
    week_date  = (w1_date - timedelta(days=7)).isoformat()

    for rec in records:
        key_val = _safe_str(rec.get(str(key_fid)) or rec.get(key_fid))
        if not key_val:
            continue

        week_key = f"{key_val}|{week_date}"
        ord_val  = _safe_int(rec.get(str(ord_lw_fid)) or rec.get(ord_lw_fid))

        row = {}

        wk_fid = act_fids.get("Week_Key")
        if wk_fid:
            row[wk_fid] = {"value": week_key}

        k_fid = act_fids.get("Key")
        if k_fid:
            row[k_fid] = {"value": key_val}

        wd_fid = act_fids.get("Week_Date")
        if wd_fid:
            row[wd_fid] = {"value": week_date}

        ou_fid = act_fids.get("Ord_Units")
        if ou_fid:
            row[ou_fid] = {"value": ord_val}

        if row:
            rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# QB batch insert (plain -- no mergeFieldId)
# ---------------------------------------------------------------------------
# QB REST API mergeFieldId only works against the table's built-in key field
# (Record ID# = FID 3).  Our custom Snapshot_Key / Week_Key text fields cannot
# be used as mergeFieldId.
#
# Each weekly snapshot produces brand-new (Key, Snapshot_Date) combinations,
# so plain INSERT is correct and safe.  We add a same-day duplicate guard in
# run() to prevent duplicates when the job is re-run on the same day.

def _insert_batch(table_id, rows, dry_run=False):
    """POST up to QB_BULK_BATCH rows at a time as plain INSERTs.
    Returns (total_inserted, errors)."""
    batch_size = config.QB_BULK_BATCH  # 500
    total = 0
    errors = []

    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        if dry_run:
            print(f"  [DRY-RUN] Would insert {len(chunk)} rows to {table_id}  "
                  f"batch {start//batch_size + 1}")
            total += len(chunk)
            continue

        payload = {
            "to":             table_id,
            "data":           chunk,
            "fieldsToReturn": [],
        }
        time.sleep(config.QB_INTER_CALL_DELAY_S)
        try:
            resp = _qb_request("POST", "/records", payload)
            processed = resp.get("metadata", {}).get("totalNumberOfRecordsProcessed", len(chunk))
            total += processed
            if total % 500 == 0:
                print(f"  Inserted {total} rows so far ...")
        except Exception as exc:
            errors.append(f"batch {start}-{start+len(chunk)}: {exc}")
            print(f"  [ERROR] {errors[-1]}")

    return total, errors


def _count_existing_by_date(table_id, fid, date_str):
    """Return count of existing rows where fid (date field) EX date_str.

    Used to detect same-day duplicate snapshots.
    """
    payload = {
        "from":   table_id,
        "select": [3],   # just Record ID# -- cheapest possible SELECT
        "where":  f"{{{fid}.EX.'{date_str}'}}",
        "options": {"top": 1, "skip": 0},
    }
    time.sleep(config.QB_INTER_CALL_DELAY_S)
    resp = _qb_request("POST", "/records/query", payload)
    # QB returns metadata.totalRecords for the full match count
    return resp.get("metadata", {}).get("totalRecords", len(resp.get("data", [])))


# ---------------------------------------------------------------------------
# Pre-flight config checks
# ---------------------------------------------------------------------------

def _check_config():
    """Verify that the accuracy tables are configured."""
    snap_tid = getattr(config, "QB_PRJ_SNAPSHOT_TID", "")
    act_tid  = getattr(config, "QB_ACTUALS_WEEKLY_TID", "")
    snap_fids = getattr(config, "PRJ_SNAP_FIDS", {})
    act_fids  = getattr(config, "ACT_WEEKLY_FIDS", {})

    if not snap_tid or "PLACEHOLDER" in snap_tid:
        raise SystemExit(
            "QB_PRJ_SNAPSHOT_TID not set in config.py. "
            "Run create_accuracy_tables.py first."
        )
    if not act_tid or "PLACEHOLDER" in act_tid:
        raise SystemExit(
            "QB_ACTUALS_WEEKLY_TID not set in config.py. "
            "Run create_accuracy_tables.py first."
        )

    snap_key_fid = snap_fids.get("Snapshot_Key", 0)
    act_key_fid  = act_fids.get("Week_Key", 0)

    if not snap_key_fid:
        raise SystemExit(
            "PRJ_SNAP_FIDS['Snapshot_Key'] is 0 in config.py. "
            "Fill in FIDs from create_accuracy_tables.py output."
        )
    if not act_key_fid:
        raise SystemExit(
            "ACT_WEEKLY_FIDS['Week_Key'] is 0 in config.py. "
            "Fill in FIDs from create_accuracy_tables.py output."
        )

    return snap_tid, act_tid, snap_fids, act_fids, snap_key_fid, act_key_fid


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(dry_run=False, force=False):
    print("=" * 72)
    print("snapshot_weekly.py -- Weekly Projection Snapshot")
    print(f"Run date: {date.today().isoformat()}")
    print("=" * 72)

    # Config checks
    snap_tid, act_tid, snap_fids, act_fids, snap_merge_fid, act_merge_fid = (
        _check_config()
    )
    print(f"\nPRJ_Snapshot table:    {snap_tid}")
    print(f"Actuals_Weekly table:  {act_tid}")

    # ── Step 1: Fetch field map from Projections ──────────────────────────
    print(f"\nStep 1: Fetching Projections field map ({PROJ_TABLE}) ...")
    fields = fetch_field_map(PROJ_TABLE)
    print(f"  {len(fields)} fields retrieved")

    fids = discover_projections_fields(fields)

    if fids["w1_date"] is None:
        raise SystemExit(
            "Could not parse W1_Date from Projections field labels. "
            "Are MAN_PRJ week fields present? (expect labels like '05 24 W1')"
        )

    print(f"  Key FID:       {fids['key_fid']}")
    print(f"  Status FID:    {fids['status_fid']}")
    print(f"  Ord/LW FID:    {fids['ord_lw_fid']}")
    print(f"  W1 label:      {fids['w1_label']!r}")
    print(f"  W1_Date:       {fids['w1_date']}")
    print(f"  MAN_PRJ weeks: {sorted(fids['man_prj'].keys())}")

    snapshot_date = date.today()
    w1_date       = fids["w1_date"]

    # ── Step 2: Fetch active Projections records ──────────────────────────
    print(f"\nStep 2: Fetching active Projections records ...")
    records = fetch_projections(fids, fids["status_fid"])

    if not records:
        raise SystemExit("No active Projections records returned -- aborting.")

    # ── Step 3: Build and write PRJ_Snapshot rows ─────────────────────────
    print(f"\nStep 3: Building PRJ_Snapshot rows ...")
    snap_rows = build_snapshot_rows(records, fids, snapshot_date, w1_date)
    print(f"  {len(snap_rows)} snapshot rows built")

    if dry_run:
        # Show a sample row
        if snap_rows:
            sample = snap_rows[0]
            # Print first few key-value pairs for verification
            snap_fid_inv = {v: k for k, v in snap_fids.items()}
            print("  Sample snapshot row (first record):")
            for fid_key, val in list(sample.items())[:8]:
                label = snap_fid_inv.get(fid_key, fid_key)
                print(f"    {label}: {val}")

    print(f"\nWriting PRJ_Snapshot (merge FID {snap_merge_fid}) ...")
    snap_written, snap_errors = _upsert_batch(
        snap_tid, snap_rows, snap_merge_fid, dry_run=dry_run
    )

    # ── Step 4: Build and write Actuals_Weekly rows ───────────────────────
    print(f"\nStep 4: Building Actuals_Weekly rows ...")
    act_rows = build_actuals_rows(records, fids, w1_date)
    print(f"  {len(act_rows)} actuals rows built")
    print(f"  Week_Date (last week):  {(w1_date - timedelta(days=7)).isoformat()}")

    print(f"\nWriting Actuals_Weekly (merge FID {act_merge_fid}) ...")
    act_written, act_errors = _upsert_batch(
        act_tid, act_rows, act_merge_fid, dry_run=dry_run
    )

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"  Snapshot date:   {snapshot_date.isoformat()}")
    print(f"  W1_Date:         {w1_date.isoformat()}")
    print(f"  Actuals week:    {(w1_date - timedelta(days=7)).isoformat()}")
    print(f"  Records fetched: {len(records)}")
    print(f"  Snap rows built: {len(snap_rows)}")
    print(f"  Snap written:    {snap_written}")
    print(f"  Act rows built:  {len(act_rows)}")
    print(f"  Act written:     {act_written}")
    if snap_errors:
        print(f"  Snap errors ({len(snap_errors)}):")
        for e in snap_errors:
            print(f"    {e}")
    if act_errors:
        print(f"  Act errors ({len(act_errors)}):")
        for e in act_errors:
            print(f"    {e}")
    if not snap_errors and not act_errors:
        print("  No errors.")
    if dry_run:
        print()
        print("  [DRY-RUN] No records were actually written to Quickbase.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Take weekly snapshot of Projections into PRJ_Snapshot and Actuals_Weekly."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without making any QB writes.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
