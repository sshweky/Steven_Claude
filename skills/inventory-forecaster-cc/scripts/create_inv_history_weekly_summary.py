"""
create_inv_history_weekly_summary.py

Builds "Inventory History - Weekly" table in InventoryTrack using QB summary
fields so values are ALWAYS live — no refresh script ever needed.

Architecture
============
  Parent: Inventory History - Weekly  (one row per Mstyle, key field = Mstyle)
  Child:  Inventory History           (many rows per Mstyle, one per day)
  Join:   Mstyle (text) in both tables

  52 formula-checkbox fields in Inventory History:
      "Is LW"    → true when [Date] = last Sunday
      "Is LW-1"  → true when [Date] = 2 Sundays ago
      ...
      "Is LW-51" → true when [Date] = 52 Sundays ago

  52 summary fields in Weekly:
      "ATS LW"    → MAX of [ATS Qty OH#] WHERE [Is LW]    = true
      "ATS LW-1"  → MAX of [ATS Qty OH#] WHERE [Is LW-1]  = true
      ...

Usage
=====
  python scripts/create_inv_history_weekly_summary.py
  python scripts/create_inv_history_weekly_summary.py --dry-run
  python scripts/create_inv_history_weekly_summary.py --skip-to-step 4  (resume after partial run)
"""

import sys, os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import json, time, argparse
import urllib.request, urllib.error

# ── Constants ─────────────────────────────────────────────────────────────────

QB_REALM  = "pim.quickbase.com"
QB_TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_BASE   = "https://api.quickbase.com/v1"
APP_ID    = "bpd24h9wy"    # InventoryTrack
SRC_TABLE = "br6dcnv35"    # Inventory History

SRC_FID_MSTYLE = 6         # Mstyle in Inventory History
SRC_FID_DATE   = 11        # Date  in Inventory History
SRC_FID_ATS    = 10        # ATS Qty OH# in Inventory History

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
    "User-Agent":        "petspeople-inv-history-weekly/2.0",
}

MAX_RETRIES = 4
BATCH_SIZE  = 500
PAGE_SIZE   = 2000

# QB DayOfWeek: 1=Sun, 2=Mon, ..., 7=Sat
# Last Sunday formula (always <= yesterday):
#   Today() - If(DayOfWeek(Today()) = 1, 7, DayOfWeek(Today()) - 1)
def _is_lw_formula(n: int) -> str:
    """
    QB checkbox formula: true when [Date] = last Sunday minus n weeks.
    Uses AddDays() because QB doesn't support Date - Number directly.
    DayOfWeek: 1=Sun, 7=Sat.  Last Sunday = AddDays(Today(), -days_since_sun)
    where days_since_sun = If(DayOfWeek(Today())=1, 7, DayOfWeek(Today())-1).
    """
    extra = f" - {n * 7}" if n > 0 else ""
    return (
        f"[Date] = AddDays(Today(), "
        f"-(If(DayOfWeek(Today()) = 1, 7, DayOfWeek(Today()) - 1)){extra})"
    )


# ── QB API helpers ────────────────────────────────────────────────────────────

def _raw(method, path, body=None, timeout=90):
    url = QB_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode(errors="replace")
            if e.code in (429, 502, 504) and attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"    [retry {attempt}] HTTP {e.code}, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {body_txt}") from e
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"    [retry {attempt}] {e}, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise


def qb_get(path):
    return _raw("GET", path)

def qb_post(path, body):
    return _raw("POST", path, body)

def qb_patch(path, body):
    return _raw("PATCH", path, body)

def qb_delete(path):
    return _raw("DELETE", path)


def qb_query_all(table_id, select_fids, where=""):
    """Paginate through all matching rows; return list of {label: value} dicts."""
    all_rows = []
    skip = 0
    while True:
        body = {"from": table_id, "select": select_fids,
                "options": {"top": PAGE_SIZE, "skip": skip}}
        if where:
            body["where"] = where
        resp = qb_post("/records/query", body)
        fid_label = {f["id"]: f["label"] for f in resp.get("fields", [])}
        rows = [
            {fid_label.get(int(k), str(k)): (v["value"] if isinstance(v, dict) else v)
             for k, v in row.items()}
            for row in resp.get("data", [])
        ]
        all_rows.extend(rows)
        total = resp.get("metadata", {}).get("totalRecords", len(all_rows))
        if skip + PAGE_SIZE >= total:
            break
        skip += PAGE_SIZE
        time.sleep(0.15)
    return all_rows


def upsert(table_id, payload_rows, merge_fid=None):
    """Bulk insert/upsert in BATCH_SIZE chunks. Returns (created, updated) totals."""
    created = updated = 0
    total = len(payload_rows)
    n_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, total, BATCH_SIZE):
        batch = payload_rows[i:i + BATCH_SIZE]
        body = {"to": table_id, "data": batch, "fieldsToReturn": []}
        if merge_fid:
            body["mergeFieldId"] = merge_fid
        resp = qb_post("/records", body)
        meta = resp.get("metadata", {})
        created += len(meta.get("createdRecordIds", []))
        updated += len(meta.get("updatedRecordIds", []))
        bn = i // BATCH_SIZE + 1
        print(f"    Batch {bn}/{n_batches}: {len(batch)} records")
        time.sleep(0.11)
    return created, updated


# ── Step functions ────────────────────────────────────────────────────────────

def step1_create_table(dry_run) -> str:
    """Create 'Inventory History - Weekly'. Returns new table DBID."""
    print("\n[Step 1] Create 'Inventory History - Weekly' table")
    if dry_run:
        print("  [dry-run] skipped"); return "DRY_RUN_TABLE"
    resp = qb_post(f"/tables?appId={APP_ID}", {
        "name":             "Inventory History - Weekly",
        "description":      "Live ATS Qty OH# via summary fields. One row per Mstyle, "
                            "52 weekly columns pulling from Inventory History.",
        "singleRecordName": "Weekly Snapshot",
        "pluralRecordName": "Weekly Snapshots",
    })
    tid = resp["id"]
    print(f"  Created: {tid}")
    return tid


def step2_create_mstyle_field(dest_tid, dry_run) -> int:
    """Create Mstyle (text, unique) in Weekly. Returns field ID."""
    print("\n[Step 2] Create Mstyle field in Weekly table")
    if dry_run:
        print("  [dry-run] skipped"); return 7
    resp = qb_post(f"/fields?tableId={dest_tid}", {
        "label":     "Mstyle",
        "fieldType": "text",
    })
    fid = resp["id"]
    print(f"  Created: fid={fid}")

    # Try to set Mstyle as the table's key field so the relationship can join on it
    print("  Attempting to set Mstyle as table key field...")
    for attempt_body in [
        {"keyFieldId": fid},
        {"properties": {"keyFieldId": fid}},
    ]:
        try:
            qb_patch(f"/tables/{dest_tid}", attempt_body)
            print(f"  Key field set OK via {attempt_body}")
            break
        except Exception as e:
            print(f"  [warn] {attempt_body} failed: {e}")
    return fid


def step3_populate_mstyles(dest_tid, dest_mstyle_fid, dry_run):
    """
    Pull unique Mstyles from Inventory History (last Sunday only — much faster
    than scanning all daily rows) and upsert one row per Mstyle into Weekly.
    """
    import datetime
    print("\n[Step 3] Populate Weekly with unique Mstyles from Inventory History")

    # Get last Sunday's date — same logic as _is_lw_formula but in Python
    today = datetime.date.today()
    dow = today.weekday()   # 0=Mon … 6=Sun
    last_sun = today - datetime.timedelta(days=7 if dow == 6 else dow + 1)
    date_str = last_sun.strftime("%m-%d-%Y")   # QB date query format
    where = f"{{'{SRC_FID_DATE}'.EX.'{date_str}'}}"

    print(f"  Pulling Mstyles for last Sunday ({last_sun})...")
    rows = qb_query_all(SRC_TABLE, [SRC_FID_MSTYLE], where=where)
    mstyles = sorted({r.get("Mstyle") or r.get(str(SRC_FID_MSTYLE))
                      for r in rows} - {None, ""})
    print(f"  {len(mstyles)} unique Mstyles found")

    if dry_run:
        print("  [dry-run] skipped upsert"); return

    payload = [{str(dest_mstyle_fid): {"value": ms}} for ms in mstyles]
    # No mergeFieldId — table is empty on first run; Mstyle uniqueness enforced by design
    c, u = upsert(dest_tid, payload)
    print(f"  Done: {c} created, {u} updated")


def step4_create_relationship(dest_tid, dry_run) -> int:
    """
    Create relationship: Inventory History (child) -> Weekly (parent).
    Uses Inventory History's existing Mstyle field (fid 6) as the foreign key.
    Returns the relationship ID.
    """
    print("\n[Step 4] Create relationship: Inventory History -> Weekly")
    if dry_run:
        print("  [dry-run] skipped"); return 0

    # Try using existing Mstyle field (fid 6) as foreign key (text-key relationship)
    try:
        resp = qb_post(f"/tables/{SRC_TABLE}/relationships", {
            "parentTableId":  dest_tid,
            "lookupFieldIds": [],
            "summaryFields":  [],
            "foreignKeyField": {"id": SRC_FID_MSTYLE},
        })
        rel_id = resp.get("id") or resp.get("relationshipId")
        print(f"  Relationship created (id={rel_id}) using existing Mstyle field")
        return rel_id
    except Exception as e:
        print(f"  [warn] Text-key relationship failed: {e}")

    # Fallback: create a new reference field (RID-based), populate it later
    print("  Falling back to RID-based reference field...")
    resp = qb_post(f"/tables/{SRC_TABLE}/relationships", {
        "parentTableId":  dest_tid,
        "lookupFieldIds": [],
        "summaryFields":  [],
        "foreignKeyField": {"label": "Related Weekly Record"},
    })
    rel_id  = resp.get("id") or resp.get("relationshipId")
    ref_fid = None
    # The created reference field is in the response
    for f in resp.get("fields", []):
        if f.get("type") == "dblink" or "Related" in f.get("label", ""):
            ref_fid = f["id"]
            break
    print(f"  Relationship created (id={rel_id}), reference field fid={ref_fid}")
    print("  Populating reference field in Inventory History...")
    _populate_reference_field(dest_tid, ref_fid)
    return rel_id


def _populate_reference_field(dest_tid, ref_fid):
    """
    When RID-based fallback is used: look up each Weekly record's RID by Mstyle,
    then bulk-update Inventory History records with the correct reference RID.
    """
    # Get Weekly RIDs keyed by Mstyle
    print("    Fetching Weekly record RIDs...")
    weekly_rows = qb_query_all(dest_tid, [3, 7])  # RID=3, Mstyle=7 (likely)
    # Discover the actual Mstyle fid in Weekly
    fields = qb_get(f"/fields?tableId={dest_tid}")
    fmap = {f["label"]: f["id"] for f in fields}
    mstyle_fid_dest = fmap.get("Mstyle", 7)
    weekly_rows = qb_query_all(dest_tid, [3, mstyle_fid_dest])
    rid_by_mstyle = {}
    for r in weekly_rows:
        ms  = r.get("Mstyle") or r.get(str(mstyle_fid_dest))
        rid = r.get("Record ID#") or r.get("3")
        if ms and rid:
            rid_by_mstyle[ms] = rid

    print(f"    {len(rid_by_mstyle)} Weekly RIDs loaded")

    # Pull all Inventory History RIDs + Mstyle
    print("    Fetching Inventory History record RIDs...")
    ih_rows = qb_query_all(SRC_TABLE, [3, SRC_FID_MSTYLE])
    payload = []
    for r in ih_rows:
        ms    = r.get("Mstyle") or r.get(str(SRC_FID_MSTYLE))
        ih_rid = r.get("Record ID#") or r.get("3")
        w_rid  = rid_by_mstyle.get(ms)
        if ih_rid and w_rid:
            payload.append({
                "3":          {"value": ih_rid},
                str(ref_fid): {"value": w_rid},
            })
    print(f"    Updating {len(payload)} Inventory History records...")
    c, u = upsert(SRC_TABLE, payload, merge_fid=3)
    print(f"    Done: {c} created, {u} updated")


def step5_create_is_lw_checkboxes(dry_run) -> list[int]:
    """
    Create 52 formula-checkbox fields in Inventory History:
    'Is LW' (n=0) through 'Is LW-51' (n=51).
    Returns list of fids [fid_LW, fid_LW1, ..., fid_LW51].
    """
    print("\n[Step 5] Create 52 formula-checkbox fields in Inventory History")
    if dry_run:
        print("  [dry-run] skipped"); return list(range(100, 152))

    fids = []
    for n in range(52):
        label   = "Is LW" if n == 0 else f"Is LW-{n}"
        formula = _is_lw_formula(n)
        resp = qb_post(f"/fields?tableId={SRC_TABLE}", {
            "label":      label,
            "fieldType":  "checkbox",
            "properties": {"formula": formula},
        })
        fid = resp["id"]
        fids.append(fid)
        print(f"  [{fid}] {label}")
        time.sleep(0.12)

    print(f"  Done. Is LW={fids[0]}, Is LW-51={fids[-1]}")
    return fids


def step6_create_summary_fields(dest_tid, is_lw_fids, dry_run):
    """
    Create 52 summary fields in Weekly:
    'ATS LW' through 'ATS LW-51', each = MAX(ATS Qty OH#) WHERE [Is LW-N] = true.
    """
    print("\n[Step 6] Create 52 summary fields in Weekly table")
    if dry_run:
        print("  [dry-run] skipped"); return

    created_fids = []
    for n in range(52):
        label    = "ATS LW" if n == 0 else f"ATS LW-{n}"
        is_lw_fid = is_lw_fids[n]
        criteria  = f"{{'{is_lw_fid}'.EX.'true'}}"

        resp = qb_post(f"/fields?tableId={dest_tid}", {
            "label":     label,
            "fieldType": "summary",
            "properties": {
                "summaryFunction":   "maximum",
                "summaryTargetTableId": SRC_TABLE,
                "summaryField":      {"id": SRC_FID_ATS},
                "criteria":          criteria,
            },
        })
        fid = resp["id"]
        created_fids.append(fid)
        print(f"  [{fid}] {label}  (where fid {is_lw_fid} = true)")
        time.sleep(0.12)

    print(f"  Done. ATS LW={created_fids[0]}, ATS LW-51={created_fids[-1]}")


# ── State file (for resume) ───────────────────────────────────────────────────

STATE_FILE = "inv_history_weekly_state.json"

def save_state(data: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  State saved to {STATE_FILE}")

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run",      action="store_true",
                    help="Print what would happen, touch nothing in QB")
    ap.add_argument("--skip-to-step", type=int, default=1, metavar="N",
                    help="Resume from step N using saved state (1-6)")
    args = ap.parse_args()

    state = load_state() if args.skip_to_step > 1 else {}

    print("=" * 60)
    print("Inventory History - Weekly  (summary-field build)")
    print("=" * 60)

    # ── Step 1 ─────────────────────────────────────────────────────────────
    if args.skip_to_step <= 1:
        dest_tid = step1_create_table(args.dry_run)
        state["dest_tid"] = dest_tid
        save_state(state)
    else:
        dest_tid = state["dest_tid"]
        print(f"\n[Step 1] Skipped — using dest_tid={dest_tid}")

    # ── Step 2 ─────────────────────────────────────────────────────────────
    if args.skip_to_step <= 2:
        dest_mstyle_fid = step2_create_mstyle_field(dest_tid, args.dry_run)
        state["dest_mstyle_fid"] = dest_mstyle_fid
        save_state(state)
    else:
        dest_mstyle_fid = state["dest_mstyle_fid"]
        print(f"\n[Step 2] Skipped — using dest_mstyle_fid={dest_mstyle_fid}")

    # ── Step 3 ─────────────────────────────────────────────────────────────
    if args.skip_to_step <= 3:
        step3_populate_mstyles(dest_tid, dest_mstyle_fid, args.dry_run)
    else:
        print(f"\n[Step 3] Skipped")

    # ── Step 4 ─────────────────────────────────────────────────────────────
    if args.skip_to_step <= 4:
        rel_id = step4_create_relationship(dest_tid, args.dry_run)
        state["rel_id"] = rel_id
        save_state(state)
    else:
        rel_id = state.get("rel_id", "unknown")
        print(f"\n[Step 4] Skipped — using rel_id={rel_id}")

    # ── Step 5 ─────────────────────────────────────────────────────────────
    if args.skip_to_step <= 5:
        is_lw_fids = step5_create_is_lw_checkboxes(args.dry_run)
        state["is_lw_fids"] = is_lw_fids
        save_state(state)
    else:
        is_lw_fids = state["is_lw_fids"]
        print(f"\n[Step 5] Skipped — using is_lw_fids[0]={is_lw_fids[0]}")

    # ── Step 6 ─────────────────────────────────────────────────────────────
    if args.skip_to_step <= 6:
        step6_create_summary_fields(dest_tid, is_lw_fids, args.dry_run)
    else:
        print(f"\n[Step 6] Skipped")

    print("\n" + "=" * 60)
    if args.dry_run:
        print("Dry-run complete. No changes made.")
    else:
        print("Build complete!")
        print(f"  Table: https://pim.quickbase.com/db/{dest_tid}")
        print("  Summary fields are live — no refresh needed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
