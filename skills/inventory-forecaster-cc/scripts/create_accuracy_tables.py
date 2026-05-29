"""
create_accuracy_tables.py -- Bootstrap script for projection accuracy tracking.

Creates two new QB tables in the InventoryTrack app (bpd24h9wy):
  - PRJ_Snapshot:    one row per (key x snapshot_date), holds W01-W26 projections
                     plus W01_Actual-W26_Actual (filled by reconcile_accuracy.py)
  - Actuals_Weekly:  one row per (key x week_date), holds actual Ord_Units

Idempotent: if QB_PRJ_SNAPSHOT_TID / QB_ACTUALS_WEEKLY_TID are already set in
config (non-empty, non-PLACEHOLDER), skips creation and just prints existing IDs.

Run once to bootstrap.  After running, copy the printed config lines into
config.py (QB tables section).

Usage:
    python create_accuracy_tables.py [--dry-run]
"""

import argparse
import json
import sys
import time
import os

# ---------------------------------------------------------------------------
# Bootstrap path so we can import config.py from this directory
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# ---------------------------------------------------------------------------
# QB REST helpers
# ---------------------------------------------------------------------------
QB_BASE = "https://api.quickbase.com/v1"
QB_APP  = "bpd24h9wy"   # InventoryTrack

def _headers():
    return {
        "QB-Realm-Hostname": config.QB_REALM,
        "Authorization":     f"QB-USER-TOKEN {config.QB_USER_TOKEN}",
        "Content-Type":      "application/json",
    }


def _qb_request(method, path, payload=None, dry_run=False):
    """Single QB REST call with retry/backoff.  Returns parsed JSON or raises."""
    import urllib.request
    import urllib.error

    url = QB_BASE + path
    data = json.dumps(payload).encode() if payload else None

    for attempt in range(1, config.QB_REST_MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
            with urllib.request.urlopen(req, timeout=60) as resp:
                elapsed_ms = (time.monotonic() - start) * 1000
                body = resp.read()
                if not body:
                    raise RuntimeError("Empty 200 response (throttle signal)")
                if elapsed_ms > config.QB_LATENCY_WARN_MS:
                    print(f"  [WARN] QB latency {elapsed_ms:.0f}ms on {method} {path}")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code in (429, 502, 503, 504):
                if attempt >= config.QB_REST_MAX_RETRIES:
                    raise RuntimeError(f"QB throttle {exc.code} after {attempt} attempts: {body_text}") from exc
                delay = 2 ** attempt
                print(f"  [WARN] QB {exc.code} on attempt {attempt}, backoff {delay}s ...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"QB HTTP {exc.code}: {body_text}") from exc
        except Exception as exc:
            if attempt >= config.QB_REST_MAX_RETRIES:
                raise
            delay = 2 ** attempt
            print(f"  [WARN] QB error on attempt {attempt} ({exc}), backoff {delay}s ...")
            time.sleep(delay)

    raise RuntimeError("QB request failed after all retries")


# ---------------------------------------------------------------------------
# Existing-table detection
# ---------------------------------------------------------------------------

def _list_app_tables():
    """Return list of existing tables in QB_APP as [{id, name}, ...]."""
    result = _qb_request("GET", f"/tables?appId={QB_APP}")
    # result is a list of table dicts
    if isinstance(result, list):
        return result
    return result.get("tables", [])


def _table_exists_by_name(tables, name):
    """Return table dict if a table with this name already exists, else None."""
    for t in tables:
        if t.get("name", "").lower() == name.lower():
            return t
    return None


# ---------------------------------------------------------------------------
# Field creation helpers
# ---------------------------------------------------------------------------

FIELD_TYPE_MAP = {
    "text":    "text",
    "date":    "date",
    "numeric": "numeric",
}

def _create_field(table_id, label, field_type, dry_run=False):
    """Create a single field in table_id.  Returns created field dict."""
    payload = {"label": label, "fieldType": field_type}
    if dry_run:
        print(f"    [DRY-RUN] POST /fields?tableId={table_id}  label={label!r} type={field_type}")
        return {"id": 0, "label": label}
    time.sleep(0.3)  # gentle pacing when creating many fields
    result = _qb_request("POST", f"/fields?tableId={table_id}", payload)
    return result


def _create_fields_batch(table_id, field_specs, dry_run=False):
    """Create a list of (label, type) tuples.  Returns {label: fid} dict."""
    fid_map = {}
    for spec in field_specs:
        label, ftype = spec[0], spec[1]
        f = _create_field(table_id, label, ftype, dry_run=dry_run)
        fid = f.get("id", 0)
        fid_map[label] = fid
        print(f"    Created field {label!r} -> FID {fid}")
    return fid_map


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def _create_table(name, description, dry_run=False):
    """Create a new table in QB_APP.  Returns table ID string."""
    payload = {
        "name":        name,
        "description": description,
        "singleRecordName": name.rstrip("s"),  # cosmetic singular
        "pluralRecordName": name,
    }
    if dry_run:
        print(f"  [DRY-RUN] POST /tables?appId={QB_APP}  name={name!r}")
        return "DRY_RUN_TID"
    result = _qb_request("POST", f"/tables?appId={QB_APP}", payload)
    tid = result.get("id") or result.get("tableId")
    if not tid:
        raise RuntimeError(f"No table ID in response: {result}")
    return tid


# ---------------------------------------------------------------------------
# PRJ_Snapshot field spec
# ---------------------------------------------------------------------------

def _prj_snapshot_field_specs():
    """Return ordered list of (label, type) for PRJ_Snapshot."""
    specs = [
        ("Snapshot_Key",  "text"),    # composite key: Key|Snapshot_Date
        ("Key",           "text"),
        ("Snapshot_Date", "date"),
        ("W1_Date",       "date"),
    ]
    for w in range(1, 27):
        specs.append((f"W{w:02d}", "numeric"))
    for w in range(1, 27):
        specs.append((f"W{w:02d}_Actual", "numeric"))
    return specs


# ---------------------------------------------------------------------------
# Actuals_Weekly field spec
# ---------------------------------------------------------------------------

def _actuals_weekly_field_specs():
    """Return ordered list of (label, type) for Actuals_Weekly."""
    return [
        ("Week_Key",   "text"),    # composite key: Key|Week_Date
        ("Key",        "text"),
        ("Week_Date",  "date"),
        ("Ord_Units",  "numeric"),
    ]


# ---------------------------------------------------------------------------
# Config-line printer
# ---------------------------------------------------------------------------

def _print_config_lines(snap_tid, snap_fids, act_tid, act_fids):
    sep = "-" * 72
    print()
    print(sep)
    print("ADD THESE LINES TO config.py (QB tables section, after QB_INV_FLOW_TABLE):")
    print(sep)
    print()
    print("# Projection Accuracy tracking tables (created 2026-05-29)")
    print(f'QB_PRJ_SNAPSHOT_TID    = os.environ.get("QB_PRJ_SNAPSHOT_TID",    "{snap_tid}")')
    print(f'QB_ACTUALS_WEEKLY_TID  = os.environ.get("QB_ACTUALS_WEEKLY_TID",  "{act_tid}")')
    print()
    print("# PRJ_Snapshot field IDs (filled after create_accuracy_tables.py)")

    # Build the dict representation
    lines = ["PRJ_SNAP_FIDS = {"]
    for label, fid in snap_fids.items():
        lines.append(f'    "{label}": {fid},')
    lines.append("}")
    print("\n".join(lines))
    print()
    print("# Actuals_Weekly field IDs")
    lines2 = ["ACT_WEEKLY_FIDS = {"]
    for label, fid in act_fids.items():
        lines2.append(f'    "{label}": {fid},')
    lines2.append("}")
    print("\n".join(lines2))
    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(dry_run=False):
    print("=" * 72)
    print("create_accuracy_tables.py -- QB Projection Accuracy Bootstrap")
    print("=" * 72)

    # Check if config already has real TIDs (not placeholder, not zero)
    snap_tid_cfg = getattr(config, "QB_PRJ_SNAPSHOT_TID", "")
    act_tid_cfg  = getattr(config, "QB_ACTUALS_WEEKLY_TID", "")

    already_snap = snap_tid_cfg and "PLACEHOLDER" not in snap_tid_cfg
    already_act  = act_tid_cfg  and "PLACEHOLDER" not in act_tid_cfg

    if already_snap and already_act:
        print()
        print("Tables already exist in config.py:")
        print(f"  QB_PRJ_SNAPSHOT_TID   = {snap_tid_cfg}")
        print(f"  QB_ACTUALS_WEEKLY_TID = {act_tid_cfg}")
        print()
        print("To re-create, remove those values from config.py first.")
        return

    # Fetch existing app tables to avoid duplicates
    print("\nFetching existing app tables ...")
    time.sleep(config.QB_INTER_CALL_DELAY_S)
    app_tables = _list_app_tables()
    print(f"  Found {len(app_tables)} existing tables in app {QB_APP}")

    # ── PRJ_Snapshot ──────────────────────────────────────────────────────

    existing_snap = _table_exists_by_name(app_tables, "PRJ_Snapshot")
    if existing_snap and not already_snap:
        snap_tid = existing_snap["id"]
        print(f"\nPRJ_Snapshot table already exists: {snap_tid}  (skipping creation)")
    else:
        print("\nCreating PRJ_Snapshot table ...")
        if not dry_run:
            time.sleep(config.QB_INTER_CALL_DELAY_S)
        snap_tid = _create_table(
            "PRJ_Snapshot",
            "Weekly snapshot of W1-W26 manual projections with actuals filled in by reconcile_accuracy.py",
            dry_run=dry_run,
        )
        print(f"  PRJ_Snapshot created: {snap_tid}")

    print("  Creating PRJ_Snapshot fields ...")
    if not dry_run:
        time.sleep(config.QB_INTER_CALL_DELAY_S)
    snap_specs = _prj_snapshot_field_specs()
    snap_fids  = _create_fields_batch(snap_tid, snap_specs, dry_run=dry_run)
    print(f"  PRJ_Snapshot: {len(snap_fids)} fields created")

    # ── Actuals_Weekly ────────────────────────────────────────────────────

    existing_act = _table_exists_by_name(app_tables, "Actuals_Weekly")
    if existing_act and not already_act:
        act_tid = existing_act["id"]
        print(f"\nActuals_Weekly table already exists: {act_tid}  (skipping creation)")
    else:
        print("\nCreating Actuals_Weekly table ...")
        if not dry_run:
            time.sleep(config.QB_INTER_CALL_DELAY_S)
        act_tid = _create_table(
            "Actuals_Weekly",
            "One row per (key x week_date): actual Ord_Units from Projections.Ord_LW",
            dry_run=dry_run,
        )
        print(f"  Actuals_Weekly created: {act_tid}")

    print("  Creating Actuals_Weekly fields ...")
    if not dry_run:
        time.sleep(config.QB_INTER_CALL_DELAY_S)
    act_specs = _actuals_weekly_field_specs()
    act_fids  = _create_fields_batch(act_tid, act_specs, dry_run=dry_run)
    print(f"  Actuals_Weekly: {len(act_fids)} fields created")

    # ── Summary ──────────────────────────────────────────────────────────

    print()
    print("Table IDs:")
    print(f"  PRJ_Snapshot    = {snap_tid}")
    print(f"  Actuals_Weekly  = {act_tid}")

    _print_config_lines(snap_tid, snap_fids, act_tid, act_fids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bootstrap QB projection accuracy tracking tables."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be created without making any QB calls.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
