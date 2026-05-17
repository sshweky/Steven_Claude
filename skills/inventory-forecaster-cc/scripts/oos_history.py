"""
oos_history.py — VP-Q2 OOS-aware demand reconstruction.

Pulls Order_History per acct-mstyle, buckets each line by Order_Date into
52 weekly slots (oldest → newest, matching ORD_COLS layout), classifies the
cancellation reason, and produces a "clean demand" series:

  clean_ord_n  = SUM(Qty_Ord) - SUM(Qty_Cxld where reason ∈ Bucket B)
  oos_cxld_n   = SUM(Qty_Cxld where reason ∈ Bucket A or C)
  oos_severity = oos_cxld_n / clean_ord_n  (0 if clean_ord_n == 0)

Bucket A — OOS-driven cancels (smooth out, keep demand intent in clean_ord)
Bucket B — Demand-invalidating cancels (subtract from Qty_Ord — wasn't real demand)
Bucket C — Ambiguous (default to Bucket A treatment)

Usage as a library (called from inventory_forecaster.py):
    from oos_history import fetch_clean_demand
    oos_data = fetch_clean_demand(keys, today=date.today())

Usage standalone (for diagnostics):
    python scripts/oos_history.py --acct 1864 --diagnostic
"""
import argparse, sys, json, time
from datetime import date, datetime, timedelta
from pathlib import Path

# Import CData plumbing + ORD_COLS layout from the main forecaster
sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory_forecaster import (   # noqa: E402
    cdata_query, ORD_COLS,
    qb_run_report, QB_OPEN_POS_TABLE, QB_OPEN_POS_REPORT, QB_OPEN_POS_CACHE_HOURS,
)

# ─── VP-ATS: ATS Inventory History fetch ──────────────────────────────────────

ATS_HIST_TID     = "bv2sxg2ji"   # Inventory History - Weekly (parent table)
ATS_HIST_FID_KEY = 6              # Mstyle (primary key, text)
# FIDs ordered OLDEST → NEWEST (LW-25=fid90 … LW=fid64); fid 65 was deleted.
# ats_l26[k] aligns with the 52-week hist array at hist[26+k]:
#   k=0 → fid90 = ATS 25 weeks ago → hist[26]
#   k=25 → fid64 = ATS last week   → hist[51]
ATS_HIST_FIDS = [90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79, 78, 77, 76,
                 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 64]   # 26 values


def fetch_ats_history(mstyle_set=None, verbose=True):
    """Fetch Available-to-Sell (ATS) L26W history from InventoryTrack via QB REST.

    Table: bv2sxg2ji (Inventory History - Weekly), one record per Mstyle.
    ATS summary fields: fid 64 (ATS last week / newest) through fid 90
    (ATS 25 weeks ago / oldest), with fid 65 deleted.

    Returns {mstyle: [26 floats, oldest→newest]} where:
        result[mstyle][0]  = ATS 25 weeks ago  (aligns with hist[26])
        result[mstyle][25] = ATS last week     (aligns with hist[51])

    mstyle_set: optional iterable to filter the return set.  None → all mstyles.
    """
    from inventory_forecaster import _qb_request   # lazy import — avoids circular

    if verbose:
        scope = f"({len(list(mstyle_set))} mstyles)" if mstyle_set is not None else "(all)"
        print(f"      [vp-ats] fetching ATS L26W history from {ATS_HIST_TID} {scope} ...",
              flush=True)

    select_fids = [ATS_HIST_FID_KEY] + ATS_HIST_FIDS   # 27 fids total
    body = {
        "from":    ATS_HIST_TID,
        "select":  select_fids,
        "options": {"top": 50000, "skip": 0},
    }
    try:
        resp = _qb_request("POST", "/records/query", body=body, timeout=120)
    except Exception as _e:
        if verbose:
            print(f"      [vp-ats] WARN: QB REST fetch failed: {_e}", flush=True)
        return {}

    data_rows     = resp.get("data", [])
    mstyle_filter = set(mstyle_set) if mstyle_set is not None else None
    out           = {}
    n_skipped     = 0

    for r in data_rows:
        key_cell = r.get(str(ATS_HIST_FID_KEY)) or {}
        mstyle   = str((key_cell.get("value") if isinstance(key_cell, dict)
                        else key_cell) or "").strip()
        if not mstyle:
            continue
        if mstyle_filter is not None and mstyle not in mstyle_filter:
            n_skipped += 1
            continue

        ats = []
        for fid in ATS_HIST_FIDS:
            cell = r.get(str(fid)) or {}
            val  = cell.get("value") if isinstance(cell, dict) else cell
            try:
                ats.append(float(val or 0))
            except (TypeError, ValueError):
                ats.append(0.0)
        out[mstyle] = ats

    if verbose:
        print(f"      [vp-ats] {len(out)} mstyles with ATS history loaded"
              f"{f'  ({n_skipped} filtered out)' if n_skipped else ''}",
              flush=True)
    return out

# ─── Reason-code classification ───────────────────────────────────────────────
# Compared case-insensitively. Sub-reason "Low Margin" overrides parent → Bucket B.

_BUCKET_B_PARENTS = {
    "future delete status",
    "parent style is fd",
    "reserve future delete styles for b&m",
    "other - saving future delete items for b&m",
    "cancel due to availability in new upc",
}

_BUCKET_B_PARENT_PREFIXES = (
    "customer order error",   # all customer-mistake sub-reasons
)

_BUCKET_B_SUBREASONS = {
    "low margin",             # strategic decline, regardless of parent
}

_BUCKET_A_PARENTS = {
    "inventory error",
    "inventory error (demand/supply)",
    "supplier delay",
    "transportation delay",
    "warehouse error",
    "bad quality, inv moved to dmg",
    "quality issue, goods moved to dmg",
}


def classify_cancel(parent_code, sub_reason):
    """Return 'A' (OOS-driven), 'B' (demand-invalidating), or 'C' (ambiguous).

    Sub-reason 'Low Margin' wins regardless of parent — strategic decline.
    """
    p = (parent_code or "").strip().lower()
    s = (sub_reason  or "").strip().lower()

    if s in _BUCKET_B_SUBREASONS:
        return "B"
    if p in _BUCKET_B_PARENTS:
        return "B"
    for pref in _BUCKET_B_PARENT_PREFIXES:
        if p.startswith(pref):
            return "B"
    if p in _BUCKET_A_PARENTS:
        return "A"
    return "C"  # null, "any", "other", uncoded


# ─── Order_History fetch & per-week aggregation ───────────────────────────────

OH_COLS = [
    "Acct_MStyle", "Order_Date", "Qty_Ord", "Qty_Cxld", "Qty_Shpd",
    "Fill_Rate_Parent_Reason_Code", "Fill_Rate_Sub_Reason",
]


def _week_idx(order_date, ref_date):
    """Map Order_Date to ORD_COLS index (0=oldest, 51=newest).

    Week 51 (newest) = ref_date's week. Week 50 = one week earlier. ...
    Week 0 = 51 weeks before ref_date's week.
    Returns None if outside the 52-week window.
    """
    if order_date is None:
        return None
    if isinstance(order_date, str):
        try:
            order_date = datetime.fromisoformat(order_date[:10]).date()
        except Exception:
            return None
    weeks_ago = (ref_date - order_date).days // 7
    if weeks_ago < 0 or weeks_ago > 51:
        return None
    return 51 - weeks_ago


def _acct_from_key(key):
    """Extract the leading numeric account from a key like '1864-FF25895'."""
    if not key:
        return None
    pre = key.split("-")[0]
    return int(pre) if pre.isdigit() else None


def fetch_clean_demand(keys, today=None, verbose=True):
    """Pull Order_History for `keys` (list of Acct_MStyle_Key_) and return:

        { key: {
            "raw_ord":      [52 floats, oldest→newest],   # SUM(Qty_Ord)
            "clean_ord":    [52 floats],                    # raw_ord - bucket_B_cxld
            "oos_cxld":     [52 floats],                    # bucket_A + bucket_C cxld
            "raw_cxld":     [52 floats],                    # SUM(Qty_Cxld) all
            "oos_severity": [52 floats in [0,1+]],          # oos_cxld / clean_ord
            "n_orders":     [52 ints],
        } }

    The CData driver chokes on `Acct_MStyle IN (...)` and `LIKE` predicates on
    Order_History, but `Acct_ = <numeric>` works.  So we group keys by their
    leading numeric account, issue one query per account, and bucket the
    returned rows by Acct_MStyle in Python.  Keys whose account isn't queryable
    (non-numeric prefix) are skipped silently.

    If a per-account query fails (response-size wall), we fall back to
    quarterly date chunks for that account.

    Keys not present in Order_History are not returned.
    """
    today = today or date.today()
    window_start = (today - timedelta(weeks=52, days=7)).strftime("%Y-%m-%d")

    # Group keys by their numeric account
    accts = {}
    for k in keys:
        a = _acct_from_key(k)
        if a is not None:
            accts.setdefault(a, set()).add(k)

    if verbose:
        print(f"      [oos] fetching Order_History since {window_start} "
              f"across {len(accts)} accounts covering {len(keys)} keys ...")

    sel_sql = ", ".join(f"[{c}]" for c in OH_COLS)
    out = {}
    n_rows_total = 0

    def _ingest(rows, key_filter):
        """Add rows to `out`, only keeping those whose Acct_MStyle is in scope."""
        added = 0
        for r in rows:
            key  = r.get("Acct_MStyle")
            if not key or key not in key_filter:
                continue
            widx = _week_idx(r.get("Order_Date"), today)
            if widx is None:
                continue

            qord = float(r.get("Qty_Ord")  or 0)
            qcxl = float(r.get("Qty_Cxld") or 0)

            entry = out.setdefault(key, {
                "raw_ord":      [0.0] * 52,
                "clean_ord":    [0.0] * 52,
                "oos_cxld":     [0.0] * 52,
                "raw_cxld":     [0.0] * 52,
                "oos_severity": [0.0] * 52,
                "n_orders":     [0]   * 52,
                "_bucket_b":    [0.0] * 52,
            })

            bucket = classify_cancel(r.get("Fill_Rate_Parent_Reason_Code"),
                                     r.get("Fill_Rate_Sub_Reason"))

            entry["raw_ord"][widx]   += qord
            entry["raw_cxld"][widx]  += qcxl
            entry["n_orders"][widx]  += 1
            if qcxl > 0:
                if bucket == "B":
                    entry["_bucket_b"][widx] += qcxl
                else:
                    entry["oos_cxld"][widx]  += qcxl
            added += 1
        return added

    for acct, key_set in sorted(accts.items()):
        sql = (
            f"SELECT {sel_sql} "
            f"FROM [Quickbase1].[InventoryTrack].[Order_History] "
            f"WHERE [Acct_] = {acct} "
            f"AND [Order_Date] >= '{window_start}'"
        )
        rows = cdata_query(sql, f"order_history acct {acct}")
        if rows:
            added = _ingest(rows, key_set)
            n_rows_total += added
            if verbose:
                print(f"      [oos]   acct {acct}: {len(rows)} rows pulled, "
                      f"{added} kept (in-scope keys)")
            continue

        # Fallback: chunk by date — 13-week sub-queries
        if verbose:
            print(f"      [oos]   acct {acct}: full pull failed, "
                  f"chunking by 13w ...")
        chunk_starts = [
            (today - timedelta(weeks=w, days=7)).strftime("%Y-%m-%d")
            for w in (52, 39, 26, 13)
        ]
        chunk_ends   = [
            (today - timedelta(weeks=w)).strftime("%Y-%m-%d")
            for w in (39, 26, 13, 0)
        ]
        for s, e in zip(chunk_starts, chunk_ends):
            sql_c = (
                f"SELECT {sel_sql} "
                f"FROM [Quickbase1].[InventoryTrack].[Order_History] "
                f"WHERE [Acct_] = {acct} "
                f"AND [Order_Date] >= '{s}' AND [Order_Date] < '{e}'"
            )
            rows_c = cdata_query(sql_c, f"acct {acct} {s}..{e}")
            if rows_c:
                added = _ingest(rows_c, key_set)
                n_rows_total += added
                if verbose:
                    print(f"      [oos]     {s}..{e}: {len(rows_c)} rows, "
                          f"{added} kept")

    # Finalize: clean_ord = raw_ord - bucket_B; oos_severity = oos_cxld / clean_ord
    for key, e in out.items():
        for w in range(52):
            cln = e["raw_ord"][w] - e["_bucket_b"][w]
            cln = max(0.0, cln)
            e["clean_ord"][w] = cln
            e["oos_severity"][w] = (e["oos_cxld"][w] / cln) if cln > 0 else 0.0
        del e["_bucket_b"]

    if verbose:
        print(f"      [oos] {n_rows_total} order lines aggregated across "
              f"{len(out)} keys")
    return out


# ─── VP-Q4: forward-window confirmed PO pulls ─────────────────────────────────

def _forward_week_idx(order_date, last_sat):
    """Map Order_Date in the forward window to AI_PRJ week index (0..25).

    AI_PRJ_W1 = first week starting AFTER last_sat (i.e. days last_sat+1..+7).
    AI_PRJ_W2 = days last_sat+8..+14.  Etc.

    Returns int in [0, 25] or None if outside forward window or in the past.
    """
    if order_date is None:
        return None
    if isinstance(order_date, str):
        try:
            order_date = datetime.fromisoformat(order_date[:10]).date()
        except Exception:
            return None
    days_forward = (order_date - last_sat).days
    if days_forward <= 0:
        return None  # not in forward window
    n = (days_forward - 1) // 7
    if n < 0 or n > 25:
        return None
    return n


def _open_pos_cache_path():
    """Disk-cache file for the bulk open-PO report.  24h TTL by default."""
    cache_dir = Path(__file__).resolve().parent.parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "open_pos_report.json"


def _bucket_open_pos_into_weeks(rows, in_scope_keys, today, verbose=True, w1_date=None):
    """Take raw report rows ({label: value}) and bucket into forward 26-week
    qty-open totals keyed by Acct_MStyle (constructed from `Acct #` + `Mstyle`).
    Returns: {acct_mstyle: [w1, w2, ..., w26]}.

    w1_date: the Sunday that starts forecast week 1 (ORIG_PRJ_COLS[0] decoded).
    When provided, cancel dates are bucketed relative to w1_date so that the
    resulting open_po_wk[i] aligns exactly with forecast[i].  Without it,
    bucketing is relative to today, which causes a 1-week shift whenever the
    run date != W1_DATE (e.g. running on Monday with a Sunday W1)."""
    in_scope_set = set(in_scope_keys) if in_scope_keys else None
    out = {}
    n_kept_in_scope = 0
    n_skipped_no_date = 0
    n_skipped_outside_window = 0
    n_skipped_zero_qty = 0
    n_skipped_oos = 0
    for r in rows:
        acct = r.get("Acct #")
        mstyle = r.get("Mstyle") or r.get("Style #")
        if acct is None or not mstyle:
            continue
        # Acct # is numeric — coerce, then format as int-string to build the key.
        try:
            acct_int = int(float(acct))
        except (TypeError, ValueError):
            continue
        key = f"{acct_int}-{mstyle}"
        if in_scope_set is not None and key not in in_scope_set:
            n_skipped_oos += 1
            continue
        qopen = r.get("Qty Open")
        try:
            qopen = float(qopen or 0)
        except (TypeError, ValueError):
            qopen = 0
        if qopen <= 0:
            n_skipped_zero_qty += 1
            continue
        bucket_str = r.get("Cancel Date") or r.get("Order Date")
        if not bucket_str:
            n_skipped_no_date += 1
            continue
        try:
            bucket_date = (datetime.fromisoformat(bucket_str[:10]).date()
                           if isinstance(bucket_str, str) else bucket_str)
        except Exception:
            n_skipped_no_date += 1
            continue
        # Bucket relative to W1_DATE when available so open_po_wk[i] aligns
        # with forecast[i].  Falling back to today-relative bucketing causes
        # a shift equal to (today - W1_DATE) that incorrectly zeroes adjacent
        # weeks (VP-Q4 bug, 2026-05-17).
        if w1_date is not None:
            days_from_w1 = (bucket_date - w1_date).days
            if days_from_w1 < 0 or days_from_w1 >= 26 * 7:
                n_skipped_outside_window += 1
                continue
            n = days_from_w1 // 7
        else:
            days_forward = (bucket_date - today).days
            if days_forward <= 0 or days_forward > 26 * 7:
                n_skipped_outside_window += 1
                continue
            n = (days_forward - 1) // 7
        if not (0 <= n <= 25):
            continue
        if key not in out:
            out[key] = [0.0] * 26
        out[key][n] += qopen
        n_kept_in_scope += 1
    if verbose:
        print(f"      [vp-q4] bucketed {n_kept_in_scope:,} in-scope rows "
              f"across {len(out):,} keys "
              f"(skipped: {n_skipped_oos:,} out-of-scope · "
              f"{n_skipped_zero_qty:,} zero-qty · "
              f"{n_skipped_no_date:,} no-date · "
              f"{n_skipped_outside_window:,} outside-26w)")
    return out


def fetch_open_pos_forward(keys, today=None, verbose=True, w1_date=None):
    """VP-Q4: Pull confirmed open customer POs for the forward 26-week window.

    Returns: { acct_mstyle_key: [w1_open_qty, w2_open_qty, ..., w26_open_qty] }
    where each entry is the SUM of Qty_Open for open POs whose expected
    fulfillment lands in that forward week.

    "Open" = Qty_Open > 0 — the order is still on the books (not fully
    shipped, not fully cancelled).

    Bucket-date selection (in priority order):
      1. Cancel_Date    — for open POs this is the customer's ship-by deadline
                          (the date they will cancel if we haven't shipped).
                          For acct 1864 (Amazon) this typically lands 5-7 days
                          after Order_Date.  This is the date we expect to
                          ship — and therefore when downstream replen will
                          consume the open PO as committed demand.
      2. Order_Date     — fallback for POs without Cancel_Date (rare).
                          NOTE: Next_Rcpt_Date is INCOMING inventory from
                          our supplier, NOT outgoing ship-to-customer; do
                          not use it for forward-week bucketing.

    POs whose chosen bucket date falls outside the forward 26w window
    (e.g. ancient Order_Date with Qty_Open still > 0 due to stale data)
    are skipped — they don't represent actionable forward demand.

    Reuses the same per-account `Acct_ = N` query pattern as fetch_clean_demand.
    """
    today = today or date.today()
    # Forward window starts the day AFTER today (W1 = next 7 days).
    # Use today itself as the anchor (not last Saturday) since open POs ship
    # imminently — Amazon typically ships within days of Order_Date.
    window_end = today + timedelta(days=26 * 7)

    # ── BULK PATH (Option A+C from QB rules: 1 saved-report call + 24h disk
    # cache).  Replaces the 119-account loop below.  Falls through to legacy
    # on any error.  ────────────────────────────────────────────────────────
    try:
        cache_path = _open_pos_cache_path()
        cache_ttl_secs = QB_OPEN_POS_CACHE_HOURS * 3600
        rows = None
        if cache_path.exists():
            cache_age = time.time() - cache_path.stat().st_mtime
            if cache_age < cache_ttl_secs:
                if verbose:
                    age_min = cache_age / 60
                    print(f"      [vp-q4] using disk cache ({cache_path.name}, "
                          f"age {age_min:.0f}m / TTL {QB_OPEN_POS_CACHE_HOURS}h)")
                with open(cache_path) as f:
                    rows = json.load(f)
        if rows is None:
            if verbose:
                print(f"      [vp-q4] BULK fetch: running QB report "
                      f"#{QB_OPEN_POS_REPORT} on table {QB_OPEN_POS_TABLE} "
                      f"(replaces per-account loop) ...")
            t0 = time.time()
            rows = qb_run_report(QB_OPEN_POS_REPORT, QB_OPEN_POS_TABLE)
            if verbose:
                print(f"      [vp-q4] {len(rows):,} rows in {time.time()-t0:.1f}s "
                      f"(1 API call vs ~{len(set(k.split('-')[0] for k in keys if '-' in k))} "
                      f"per-account calls)")
            with open(cache_path, "w") as f:
                json.dump(rows, f)
            if verbose:
                print(f"      [vp-q4] cached → {cache_path.name} "
                      f"(reused for {QB_OPEN_POS_CACHE_HOURS}h)")
        out = _bucket_open_pos_into_weeks(rows, keys, today, verbose=verbose, w1_date=w1_date)
        return out
    except Exception as _e:
        if verbose:
            print(f"      [vp-q4] BULK path failed: {type(_e).__name__}: {_e}")
            print(f"      [vp-q4] falling back to legacy per-account loop ...")
        # Fall through to legacy code below.


    accts = {}
    for k in keys:
        a = _acct_from_key(k)
        if a is not None:
            accts.setdefault(a, set()).add(k)

    if verbose:
        print(f"      [vp-q4] fetching open POs (Qty_Open>0) shipping "
              f"{today.isoformat()} .. {window_end.isoformat()} "
              f"across {len(accts)} accounts ...")

    sel_cols = ["Acct_MStyle", "Order_Date", "Cancel_Date",
                "Qty_Ord", "Qty_Open", "Qty_Shpd", "Qty_Cxld"]
    sel_sql = ", ".join(f"[{c}]" for c in sel_cols)

    out = {}
    n_rows_total = 0
    n_skipped_old = 0
    for acct, key_set in sorted(accts.items()):
        # Pull ALL open POs for the account; we filter date in Python so we
        # can use Cancel_Date if present, Order_Date if not.
        # Cancel_Date = customer ship-by deadline (date we expect to ship to
        #   customer); Next_Rcpt_Date is INCOMING from supplier — do NOT use
        #   it for forward-week bucketing.
        sql = (
            f"SELECT {sel_sql} "
            f"FROM [Quickbase1].[InventoryTrack].[Order_History] "
            f"WHERE [Acct_] = {acct} "
            f"AND [Qty_Open] > 0"
        )
        rows = cdata_query(sql, f"open_pos acct {acct}")
        if not rows:
            continue

        for r in rows:
            key = r.get("Acct_MStyle")
            if not key or key not in key_set:
                continue
            qopen = float(r.get("Qty_Open") or 0)
            if qopen <= 0:
                continue

            # Pick bucket date: Cancel_Date (customer ship-by) if present,
            # else Order_Date as fallback.
            bucket_date_str = r.get("Cancel_Date") or r.get("Order_Date")
            if not bucket_date_str:
                continue
            try:
                bucket_date = (datetime.fromisoformat(bucket_date_str[:10]).date()
                               if isinstance(bucket_date_str, str) else bucket_date_str)
            except Exception:
                continue

            # Bucket relative to W1_DATE (forecast grid anchor) when available,
            # otherwise fall back to today-relative bucketing.
            if w1_date is not None:
                days_from_w1 = (bucket_date - w1_date).days
                if days_from_w1 < 0 or days_from_w1 >= 26 * 7:
                    n_skipped_old += 1
                    continue
                n = days_from_w1 // 7
            else:
                days_forward = (bucket_date - today).days
                if days_forward <= 0 or days_forward > 26 * 7:
                    n_skipped_old += 1
                    continue
                n = (days_forward - 1) // 7
            if n < 0 or n > 25:
                continue

            entry = out.setdefault(key, [0.0] * 26)
            entry[n] += qopen
            n_rows_total += 1

    if verbose:
        print(f"      [vp-q4] {n_rows_total} open-PO lines aggregated "
              f"across {len(out)} keys "
              f"({n_skipped_old} skipped: outside forward 26w window)")
    return out


# ─── Per-week classification (used by the forecaster's compound-catchup logic)─

OOS_HARD_THRESHOLD    = 0.50  # >=50% of week's demand cancelled = hard OOS
OOS_PARTIAL_THRESHOLD = 0.15  # 15-50% = partial OOS


def classify_week(oos_severity):
    """Return 'hard', 'partial', or 'clean' for one week's severity score."""
    if oos_severity >= OOS_HARD_THRESHOLD:
        return "hard"
    if oos_severity >= OOS_PARTIAL_THRESHOLD:
        return "partial"
    return "clean"


def neutralize_compounding(clean_ord, oos_severity, l13_threshold=1.5):
    """Detect hard-OOS week followed by an abnormal catch-up spike, and
    average them.  Returns a new clean_ord array; oos_severity is unchanged.

    Rule: if week N is hard-OOS AND week N+1 > 1.5x L13 nz-avg from clean
    weeks in the prior 13, merge the pair (sum and split evenly).
    """
    out = list(clean_ord)
    n = len(out)
    for i in range(n - 1):
        if classify_week(oos_severity[i]) != "hard":
            continue
        # Compute clean L13 nz-avg from the prior 13 weeks (excluding this week)
        prior_lo = max(0, i - 13)
        prior = [out[j] for j in range(prior_lo, i)
                 if out[j] > 0 and classify_week(oos_severity[j]) == "clean"]
        if not prior:
            continue
        l13_nz_avg = sum(prior) / len(prior)
        if out[i + 1] > l13_threshold * l13_nz_avg:
            merged = (out[i] + out[i + 1]) / 2.0
            out[i]     = merged
            out[i + 1] = merged
    return out


# ─── Standalone diagnostic mode ───────────────────────────────────────────────

def _diagnostic(keys, label="diagnostic"):
    """Quick diagnostic: how often does each bucket fire across `keys`?"""
    today = date.today()
    data = fetch_clean_demand(keys, today=today)

    n_records       = len(data)
    n_with_any_oos  = 0
    n_with_hard_oos = 0
    n_with_partial  = 0
    n_with_bucket_b = 0
    week_class_counts = {"hard": 0, "partial": 0, "clean": 0}
    total_raw_ord   = 0.0
    total_clean_ord = 0.0
    total_oos_cxld  = 0.0
    total_b_cxld    = 0.0

    for key, e in data.items():
        any_oos = any(s >= OOS_PARTIAL_THRESHOLD for s in e["oos_severity"])
        any_hard = any(s >= OOS_HARD_THRESHOLD for s in e["oos_severity"])
        any_bucket_b = any(e["raw_ord"][w] > e["clean_ord"][w] for w in range(52))

        if any_oos:     n_with_any_oos  += 1
        if any_hard:    n_with_hard_oos += 1
        if any_oos and not any_hard: n_with_partial += 1
        if any_bucket_b: n_with_bucket_b += 1

        for w in range(52):
            if e["raw_ord"][w] > 0 or e["raw_cxld"][w] > 0:
                week_class_counts[classify_week(e["oos_severity"][w])] += 1

        total_raw_ord   += sum(e["raw_ord"])
        total_clean_ord += sum(e["clean_ord"])
        total_oos_cxld  += sum(e["oos_cxld"])
        total_b_cxld    += sum(e["raw_cxld"]) - sum(e["oos_cxld"])

    print()
    print(f"=== VP-Q2 OOS diagnostic: {label} ===")
    print(f"Records with any Order_History in L52W:  {n_records:>6}")
    print(f"  with >=1 hard-OOS week    (>=50%):     {n_with_hard_oos:>6}")
    print(f"  with >=1 partial-OOS week (15-50%):    {n_with_partial:>6}")
    print(f"  with any Bucket-B (demand-invalid):    {n_with_bucket_b:>6}")
    print()
    print(f"Active-week classification (across all records):")
    print(f"  clean   weeks: {week_class_counts['clean']:>6}")
    print(f"  partial weeks: {week_class_counts['partial']:>6}")
    print(f"  hard    weeks: {week_class_counts['hard']:>6}")
    print()
    print(f"Volume in L52W:")
    print(f"  raw_ord units total:    {total_raw_ord:>14,.0f}")
    print(f"  clean_ord units total:  {total_clean_ord:>14,.0f}  "
          f"({(total_raw_ord - total_clean_ord)/max(1,total_raw_ord)*100:.1f}% removed as Bucket B)")
    print(f"  Bucket-A/C cancels:     {total_oos_cxld:>14,.0f}  "
          f"({total_oos_cxld/max(1,total_clean_ord)*100:.1f}% of clean demand)")
    print(f"  Bucket-B cancels:       {total_b_cxld:>14,.0f}")
    print()


def _main():
    p = argparse.ArgumentParser()
    p.add_argument("--acct", help="account number, e.g. 1864")
    p.add_argument("--customer", help="customer name substring")
    p.add_argument("--diagnostic", action="store_true",
                   help="run frequency analysis instead of dumping JSON")
    p.add_argument("--out", default="oos_history.json")
    args = p.parse_args()

    where = ["[Status_Cust] LIKE 'A%'"]
    label = "all-active"
    if args.acct:
        where.append(f"[Acct_MStyle_Key_] LIKE '{args.acct}-%'")
        label = f"acct{args.acct}"
    if args.customer:
        where.append(f"[Cust_Name] LIKE '%{args.customer}%'")
        label = f"cust_{args.customer}"

    sql = ("SELECT [Acct_MStyle_Key_] FROM [Quickbase1].[InventoryTrack].[Projections] "
           "WHERE " + " AND ".join(where))
    print(f"[1/2] Fetching scope keys ({label}) ...")
    rows = cdata_query(sql, "scope keys")
    keys = sorted({r.get("Acct_MStyle_Key_") for r in rows if r.get("Acct_MStyle_Key_")})
    print(f"      {len(keys)} keys in scope")

    if args.diagnostic:
        _diagnostic(keys, label=label)
        return

    print(f"[2/2] Fetching Order_History ...")
    data = fetch_clean_demand(keys)
    Path(args.out).write_text(json.dumps(data, indent=2))
    print(f"      wrote {args.out} ({len(data)} keys)")


if __name__ == "__main__":
    _main()
