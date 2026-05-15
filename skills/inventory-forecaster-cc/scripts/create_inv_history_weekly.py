"""
create_inv_history_weekly.py

Creates "Inventory History - Weekly" table in InventoryTrack (bpd24h9wy),
then populates it with ATS Qty OH# snapshots for the last 52 Sundays
pulled from the "Inventory History" table (br6dcnv35).

Fields created:
  Mstyle  (text, unique — used as merge key)
  ATS LW, ATS LW-1, ATS LW-2, ..., ATS LW-51  (numeric, 0 decimal places)

Usage:
  python scripts/create_inv_history_weekly.py
  python scripts/create_inv_history_weekly.py --dry-run
  python scripts/create_inv_history_weekly.py --skip-create --dest-table bXXXXXX
  python scripts/create_inv_history_weekly.py --date-fid 7 --ats-fid 12 --mstyle-fid 6
"""

import json, time, datetime, argparse
import urllib.request, urllib.error

QB_REALM   = "pim.quickbase.com"
QB_TOKEN   = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_BASE    = "https://api.quickbase.com/v1"
APP_ID     = "bpd24h9wy"          # InventoryTrack
SRC_TABLE  = "br6dcnv35"          # Inventory History

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
    "User-Agent":        "petspeople-inv-history-weekly/1.0",
}

MAX_RETRIES = 4
BATCH_SIZE  = 500
PAGE_SIZE   = 2000   # rows per QB read page

import sys, os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

# ── QB helpers ────────────────────────────────────────────────────────────────

def _qb_raw(method, path, body=None, timeout=90):
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
                print(f"  [RETRY {attempt}] HTTP {e.code}, waiting {wait}s…")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {body_txt}") from e
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"  [RETRY {attempt}] {e}, waiting {wait}s…")
                time.sleep(wait)
                continue
            raise


def qb_get(path):
    return _qb_raw("GET", path)


def qb_post(path, body):
    return _qb_raw("POST", path, body)


def qb_query_page(table_id, select_fids, where, skip=0):
    """One page of records. Returns (rows_as_dicts, metadata)."""
    resp = qb_post("/records/query", {
        "from":    table_id,
        "select":  select_fids,
        "where":   where,
        "options": {"top": PAGE_SIZE, "skip": skip},
    })
    fid_label = {f["id"]: f["label"] for f in resp.get("fields", [])}
    rows = [
        {fid_label.get(int(k), str(k)): (v["value"] if isinstance(v, dict) else v)
         for k, v in row.items()}
        for row in resp.get("data", [])
    ]
    return rows, resp.get("metadata", {})


def qb_query_all(table_id, select_fids, where):
    """Auto-paginate through all rows matching `where`."""
    all_rows = []
    skip = 0
    while True:
        rows, meta = qb_query_page(table_id, select_fids, where, skip=skip)
        all_rows.extend(rows)
        total = meta.get("totalRecords", len(all_rows))
        if skip + PAGE_SIZE >= total:
            break
        skip += PAGE_SIZE
        time.sleep(0.15)
    return all_rows


# ── Date helpers ──────────────────────────────────────────────────────────────

def last_52_sundays(today: datetime.date) -> list[datetime.date]:
    """
    Returns list of 52 dates, each a Sunday.
    Index 0 = most recent completed Sunday (LW).
    Index 51 = oldest Sunday (LW-51).
    """
    dow = today.weekday()          # 0=Mon … 6=Sun
    if dow == 6:                   # today is Sunday — don't include it as "last"
        last_sun = today - datetime.timedelta(weeks=1)
    else:
        last_sun = today - datetime.timedelta(days=dow + 1)
    return [last_sun - datetime.timedelta(weeks=i) for i in range(52)]


def qb_date(d: datetime.date) -> str:
    """QB query date format: MM-DD-YYYY"""
    return d.strftime("%m-%d-%Y")


# ── Schema discovery ──────────────────────────────────────────────────────────

def discover_src_fields(date_fid_arg, ats_fid_arg, mstyle_fid_arg):
    """
    Fetch Inventory History fields. Auto-detect date/ATS/Mstyle fids,
    or use the ones the caller provided. Returns (date_fid, ats_fid, mstyle_fid).
    """
    print(f"\n[Schema] Fetching fields from Inventory History ({SRC_TABLE})…")
    fields = qb_get(f"/fields?tableId={SRC_TABLE}")
    print(f"  {len(fields)} fields found:")
    for f in sorted(fields, key=lambda x: x["id"]):
        print(f"    [{f['id']:3d}] {f['label']:<40}  type={f['fieldType']}")

    if date_fid_arg and ats_fid_arg and mstyle_fid_arg:
        return date_fid_arg, ats_fid_arg, mstyle_fid_arg

    # Auto-detect
    date_fid = date_fid_arg
    ats_fid  = ats_fid_arg
    mstyle_fid = mstyle_fid_arg

    for f in fields:
        lbl = f["label"]
        lbl_lo = lbl.lower()
        fid = f["id"]
        # Prefer exact "Mstyle" match; avoid compound labels like "Date_Mstyle"
        if mstyle_fid is None and lbl_lo == "mstyle":
            mstyle_fid = fid
        if date_fid is None and f["fieldType"] in ("date", "timestamp") and (
                "date" in lbl_lo or "day" in lbl_lo or "snapshot" in lbl_lo or "week" in lbl_lo):
            date_fid = fid
        if ats_fid is None and "ats" in lbl_lo and ("oh" in lbl_lo or "qty" in lbl_lo):
            ats_fid = fid

    missing = []
    if date_fid   is None: missing.append("date field  → pass --date-fid <id>")
    if ats_fid    is None: missing.append("ATS Qty OH# → pass --ats-fid <id>")
    if mstyle_fid is None: missing.append("Mstyle      → pass --mstyle-fid <id>")

    if missing:
        print("\n[ABORT] Could not auto-detect:")
        for m in missing: print(f"  • {m}")
        sys.exit(1)

    print(f"\n  Using: date_fid={date_fid}, ats_fid={ats_fid}, mstyle_fid={mstyle_fid}")
    return date_fid, ats_fid, mstyle_fid


# ── Table & field creation ────────────────────────────────────────────────────

def create_table(dry_run: bool) -> str:
    print(f"\n[Create Table] Creating 'Inventory History - Weekly' in app {APP_ID}…")
    if dry_run:
        print("  [dry-run] skipped")
        return "DRY_RUN"
    resp = qb_post(f"/tables?appId={APP_ID}", {
        "name":               "Inventory History - Weekly",
        "description":        "ATS Qty OH# Sunday snapshots for the last 52 weeks, keyed by Mstyle.",
        "singleRecordName":   "Weekly Snapshot",
        "pluralRecordName":   "Weekly Snapshots",
    })
    table_id = resp["id"]
    print(f"  ✓ Table created: {table_id}")
    return table_id


def create_fields(table_id: str, dry_run: bool) -> tuple[int, list[int]]:
    """
    Creates Mstyle field + 52 ATS LW fields.
    Returns (mstyle_fid, [ats_fids…]) where ats_fids[0]=LW, ats_fids[51]=LW-51.
    """
    print(f"\n[Fields] Creating 53 fields in {table_id}…")

    if dry_run:
        print("  [dry-run] skipped all field creation")
        return 7, list(range(8, 60))

    # -- Mstyle (text, unique)
    resp = qb_post(f"/fields?tableId={table_id}", {
        "label":      "Mstyle",
        "fieldType":  "text",
        "properties": {"unique": True, "required": True},
    })
    mstyle_fid = resp["id"]
    print(f"  [{mstyle_fid}] Mstyle")
    time.sleep(0.12)

    # -- 52 ATS LW fields
    labels = ["ATS LW"] + [f"ATS LW-{i}" for i in range(1, 52)]
    ats_fids = []
    for lbl in labels:
        resp = qb_post(f"/fields?tableId={table_id}", {
            "label":      lbl,
            "fieldType":  "numeric",
            "properties": {"decimalPlaces": 0},
        })
        fid = resp["id"]
        ats_fids.append(fid)
        print(f"  [{fid}] {lbl}")
        time.sleep(0.12)

    print(f"  ✓ All fields created. Mstyle={mstyle_fid}, ATS LW={ats_fids[0]}, ATS LW-51={ats_fids[-1]}")
    return mstyle_fid, ats_fids


def load_dest_fields(table_id: str) -> tuple[int, list[int]]:
    """Load field IDs from an existing dest table (--skip-create path)."""
    print(f"\n[Fields] Loading fields from existing table {table_id}…")
    fields = qb_get(f"/fields?tableId={table_id}")
    fmap = {f["label"]: f["id"] for f in fields}

    mstyle_fid = fmap.get("Mstyle")
    if mstyle_fid is None:
        sys.exit("[ABORT] 'Mstyle' field not found in dest table")

    labels = ["ATS LW"] + [f"ATS LW-{i}" for i in range(1, 52)]
    ats_fids = []
    for lbl in labels:
        fid = fmap.get(lbl)
        if fid is None:
            sys.exit(f"[ABORT] Field '{lbl}' not found in dest table")
        ats_fids.append(fid)

    print(f"  ✓ Mstyle={mstyle_fid}, ATS LW={ats_fids[0]}, ATS LW-51={ats_fids[-1]}")
    return mstyle_fid, ats_fids


# ── Data pull ─────────────────────────────────────────────────────────────────

def pull_ats_data(date_fid, ats_fid, mstyle_fid_src, sundays) -> dict:
    """
    For each of the 52 Sundays, query Inventory History for all mstyle ATS values.
    Returns dict: { mstyle: [ats_lw, ats_lw1, …, ats_lw51] }
    Missing = None (written as 0 in QB).
    """
    print(f"\n[Pull] Pulling ATS Qty OH# for 52 Sundays from {SRC_TABLE}…")
    data: dict[str, list] = {}

    for i, sunday in enumerate(sundays):
        label    = "ATS LW" if i == 0 else f"ATS LW-{i}"
        date_str = qb_date(sunday)
        where    = f"{{'{date_fid}'.EX.'{date_str}'}}"

        print(f"  [{i:2d}] {label}  {sunday.isoformat()}  ", end="", flush=True)

        rows = qb_query_all(SRC_TABLE, [mstyle_fid_src, ats_fid], where)
        count = len(rows)

        for row in rows:
            # Labels come back from the field map — find the right keys
            mstyle = None
            ats_val = None
            for k, v in row.items():
                kl = k.lower()
                if "mstyle" in kl:
                    mstyle = v
                if "ats" in kl and ("oh" in kl or "qty" in kl):
                    ats_val = v
            if mstyle:
                if mstyle not in data:
                    data[mstyle] = [None] * 52
                try:
                    data[mstyle][i] = int(round(float(ats_val))) if ats_val is not None else None
                except (ValueError, TypeError):
                    data[mstyle][i] = None

        print(f"{count} rows  ({len(data)} mstyles so far)")
        time.sleep(0.15)   # ~6 req/s sustained

    print(f"\n  Total unique mstyles: {len(data)}")
    return data


# ── Write ─────────────────────────────────────────────────────────────────────

def upsert_data(dest_table_id, mstyle_fid_dest, ats_fids_dest, data, dry_run):
    mstyles = sorted(data.keys())
    total   = len(mstyles)
    n_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\n[Write] Upserting {total} mstyles in {n_batches} batches → {dest_table_id}…")

    if dry_run:
        print("  [dry-run] Sample (first 3):")
        for ms in mstyles[:3]:
            v = data[ms]
            print(f"    {ms}: LW={v[0]}, LW-1={v[1]}, LW-51={v[51]}")
        return

    written = 0
    for b_start in range(0, total, BATCH_SIZE):
        batch   = mstyles[b_start:b_start + BATCH_SIZE]
        payload = []
        for ms in batch:
            vals = data[ms]
            row  = {str(mstyle_fid_dest): {"value": ms}}
            for j, fid in enumerate(ats_fids_dest):
                v = vals[j]
                row[str(fid)] = {"value": v if v is not None else 0}
            payload.append(row)

        resp    = qb_post("/records", {
            "to":           dest_table_id,
            "data":         payload,
            "mergeFieldId": mstyle_fid_dest,
            "fieldsToReturn": [],
        })
        meta    = resp.get("metadata", {})
        created = len(meta.get("createdRecordIds", []))
        updated = len(meta.get("updatedRecordIds", []))
        written += len(batch)
        bn = b_start // BATCH_SIZE + 1
        print(f"  Batch {bn}/{n_batches}: {created} created, {updated} updated  ({written}/{total})")
        time.sleep(0.11)   # ≤10 req/s

    print(f"\n  ✓ Done. {written} mstyles written.")
    print(f"  View: https://pim.quickbase.com/db/{dest_table_id}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run",     action="store_true",
                    help="Discover schema + pull data but skip all writes")
    ap.add_argument("--skip-create", action="store_true",
                    help="Skip table/field creation; populate an existing dest table")
    ap.add_argument("--dest-table",  default=None,
                    help="Existing dest table DBID (required with --skip-create)")
    ap.add_argument("--date-fid",    type=int, default=None,
                    help="Field ID of the date field in Inventory History (override auto-detect)")
    ap.add_argument("--ats-fid",     type=int, default=None,
                    help="Field ID of ATS Qty OH# in Inventory History (override auto-detect)")
    ap.add_argument("--mstyle-fid",  type=int, default=None,
                    help="Field ID of Mstyle in Inventory History (override auto-detect)")
    args = ap.parse_args()

    if args.skip_create and not args.dest_table:
        ap.error("--skip-create requires --dest-table <dbid>")

    today   = datetime.date.today()
    sundays = last_52_sundays(today)
    print(f"[Init] Today={today}  LW Sunday={sundays[0]}  LW-51={sundays[51]}")

    # ── Step 1: Schema discovery ──────────────────────────────────────────
    date_fid, ats_fid, mstyle_fid_src = discover_src_fields(
        args.date_fid, args.ats_fid, args.mstyle_fid)

    # ── Smoke test ────────────────────────────────────────────────────────
    print(f"\n[Smoke] Checking Inventory History row count for LW ({sundays[0]})…")
    test_rows, test_meta = qb_query_page(
        SRC_TABLE, [mstyle_fid_src, ats_fid],
        f"{{'{date_fid}'.EX.'{qb_date(sundays[0])}'}}")
    total_expected = test_meta.get("totalRecords", len(test_rows))
    if total_expected == 0:
        print(f"  [WARN] No rows found for {sundays[0]}. "
              f"Try a different date or check the date field (--date-fid).")
        print("  Continuing anyway — data may populate on other Sundays.")
    else:
        print(f"  ✓ {total_expected} rows on {sundays[0]}  (sample mstyle: "
              f"{list(test_rows[0].values())[0] if test_rows else 'n/a'})")

    # ── Step 2/3: Create or load dest table ───────────────────────────────
    if args.skip_create:
        dest_table_id = args.dest_table
        mstyle_fid_dest, ats_fids_dest = load_dest_fields(dest_table_id)
    else:
        dest_table_id = create_table(args.dry_run)
        mstyle_fid_dest, ats_fids_dest = create_fields(dest_table_id, args.dry_run)

    # ── Step 4: Pull data ─────────────────────────────────────────────────
    data = pull_ats_data(date_fid, ats_fid, mstyle_fid_src, sundays)

    # ── Step 5: Write ─────────────────────────────────────────────────────
    upsert_data(dest_table_id, mstyle_fid_dest, ats_fids_dest, data, args.dry_run)

    if not args.dry_run:
        print(f"\n✓ All done!")
        print(f"  Table 'Inventory History - Weekly': https://pim.quickbase.com/db/{dest_table_id}")
    else:
        print(f"\n[dry-run] Complete. No data written.")


if __name__ == "__main__":
    main()
