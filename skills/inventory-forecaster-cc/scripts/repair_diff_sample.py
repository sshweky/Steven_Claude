"""
Diagnostic — compute proposed corrections to Projections.Ord_LW_n / Shp_LW_n
for a small sample of records, by aggregating Order_History per week.

DRY-RUN ONLY — does not write back.  Outputs a side-by-side comparison so the
user can verify the week-bucket convention and aggregation are correct before
running the full repair on all records.

Convention:
  Today (any day of week) → most recent COMPLETED Sun..Sat week is "LW".
  Ord_LW     = orders dated in [last_sat - 6 days, last_sat]
  Ord_LW_1   = the week before that
  ...
  Ord_LW_51  = 51 weeks before that
"""
import sys, time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory_forecaster import cdata_query  # noqa: E402

# Sample: 10 acct 1864 records spanning patterns
SAMPLE = [
    "1864-FF10159",     # Croston's outlier #1 (no cancels, all-shipped)
    "1864-FF7115",      # Croston's outlier #2 (recurring 1152-unit POs)
    "1864-FF7112",      # Croston's outlier #3
    "1864-FF25895",     # heavy cancellations (validates Bucket A logic)
    "1864-BB11483",     # Seasonal Baseline (steady high-volume)
    "1864-FF27266",     # Seasonal Baseline (Burt's Bees Manuka)
    "1864-FF6305PCS2",  # Seasonal Baseline (Arm & Hammer Tribone)
    "1864-BB12010",     # Croston's smaller
    "1864-FF18956",     # Arm & Hammer smaller
    "1864-BB16180",     # Kingsford cutlery
]


def _last_completed_sat(today=None):
    """Return date of most recent completed Saturday."""
    today = today or date.today()
    # weekday(): Mon=0..Sun=6;  Saturday=5
    days_since_sat = (today.weekday() - 5) % 7
    if days_since_sat == 0:                       # today IS Saturday → that's "LW end"
        return today
    return today - timedelta(days=days_since_sat)


def _week_idx_qb(order_date, last_sat):
    """Map Order_Date to QB Ord_LW_n index.
       LW (n=0)  = week ending last_sat
       LW_1 (n=1)= week ending last_sat - 7d
       ...
    Returns int n in [0, 51] or None if outside window or in current incomplete week.
    """
    if isinstance(order_date, str):
        try:
            order_date = datetime.fromisoformat(order_date[:10]).date()
        except Exception:
            return None
    if order_date > last_sat:                     # current incomplete week → skip
        return None
    days_back = (last_sat - order_date).days
    n = days_back // 7
    if n < 0 or n > 51:
        return None
    return n


def fetch_orders_for_acct(acct, today):
    """Pull all Order_History for an account in the L52W window."""
    last_sat = _last_completed_sat(today)
    window_start = (last_sat - timedelta(days=51 * 7 + 6)).strftime("%Y-%m-%d")
    sql = (
        "SELECT [Acct_MStyle], [Order_Date], [Qty_Ord], [Qty_Shpd], [Qty_Cxld] "
        "FROM [Quickbase1].[InventoryTrack].[Order_History] "
        f"WHERE [Acct_] = {acct} AND [Order_Date] >= '{window_start}'"
    )
    return cdata_query(sql, f"OH acct {acct}")


def fetch_projections_for_keys(keys):
    """Pull current Ord_LW_n and Shp_LW_n for the sample keys."""
    ord_cols = ["Ord_LW"] + [f"Ord_LW_{i}" for i in range(1, 52)]
    shp_cols = ["Shp_LW"] + [f"Shp_LW_{i}" for i in range(1, 52)]
    cols = ["Acct_MStyle_Key_", "Last_Ord_Date", "Last_Shp_Date"] + ord_cols + shp_cols
    sel = ", ".join(f"[{c}]" for c in cols)

    out = {}
    for key in keys:
        sql = (
            f"SELECT {sel} FROM [Quickbase1].[InventoryTrack].[Projections] "
            f"WHERE [Acct_MStyle_Key_] = '{key}'"
        )
        rows = cdata_query(sql, f"Proj {key}")
        if rows:
            out[key] = rows[0]
    return out


def compute_corrections(orders_by_acct, today, sample_keys):
    """Bucket Order_History into 52-week vectors per acct-mstyle."""
    last_sat = _last_completed_sat(today)
    out = {}
    for r in orders_by_acct:
        key = r.get("Acct_MStyle")
        if key not in sample_keys:
            continue
        n = _week_idx_qb(r.get("Order_Date"), last_sat)
        if n is None:
            continue
        e = out.setdefault(key, {"ord": [0.0] * 52, "shp": [0.0] * 52,
                                  "cxl": [0.0] * 52, "lines": [0] * 52})
        e["ord"][n] += float(r.get("Qty_Ord")  or 0)
        e["shp"][n] += float(r.get("Qty_Shpd") or 0)
        e["cxl"][n] += float(r.get("Qty_Cxld") or 0)
        e["lines"][n] += 1
    return out


def print_record_diff(key, current, proposed, last_sat):
    """Print a side-by-side per-week comparison for one record."""
    cur_ord = [float(current.get("Ord_LW")  or 0)] + \
              [float(current.get(f"Ord_LW_{i}") or 0) for i in range(1, 52)]
    cur_shp = [float(current.get("Shp_LW")  or 0)] + \
              [float(current.get(f"Shp_LW_{i}") or 0) for i in range(1, 52)]
    p_ord = proposed["ord"]
    p_shp = proposed["shp"]

    sum_cur_o = sum(cur_ord); sum_pro_o = sum(p_ord)
    sum_cur_s = sum(cur_shp); sum_pro_s = sum(p_shp)

    print(f"\n{'='*92}")
    print(f"{key}   Last_Ord_Date={current.get('Last_Ord_Date'):<12} "
          f"Last_Shp_Date={current.get('Last_Shp_Date')}")
    print(f"  L52W ORD totals:  current={sum_cur_o:>10,.0f}   "
          f"proposed={sum_pro_o:>10,.0f}   delta={sum_pro_o - sum_cur_o:+,.0f}")
    print(f"  L52W SHP totals:  current={sum_cur_s:>10,.0f}   "
          f"proposed={sum_pro_s:>10,.0f}   delta={sum_pro_s - sum_cur_s:+,.0f}")

    # Show only weeks where there's a meaningful disagreement (≥10 unit gap)
    diffs = []
    for n in range(52):
        d_o = p_ord[n] - cur_ord[n]
        d_s = p_shp[n] - cur_shp[n]
        if abs(d_o) >= 10 or abs(d_s) >= 10:
            wk_end = last_sat - timedelta(days=n * 7)
            wk_start = wk_end - timedelta(days=6)
            diffs.append((n, wk_start, wk_end, cur_ord[n], p_ord[n],
                          cur_shp[n], p_shp[n]))

    if not diffs:
        print("  (no per-week disagreements ≥10 units)")
        return

    print(f"  Per-week diffs (≥10 unit gap):")
    print(f"    {'Field':<8} {'Range':<24} {'cur_ord':>9} {'new_ord':>9} "
          f"{'Δord':>8}  {'cur_shp':>9} {'new_shp':>9} {'Δshp':>8}")
    for n, ws, we, c_o, p_o, c_s, p_s in diffs:
        field = f"LW{'_'+str(n) if n else ''}"
        rng = f"{ws.strftime('%m/%d')} – {we.strftime('%m/%d/%y')}"
        print(f"    {field:<8} {rng:<24} {c_o:>9,.0f} {p_o:>9,.0f} "
              f"{p_o-c_o:+8,.0f}  {c_s:>9,.0f} {p_s:>9,.0f} {p_s-c_s:+8,.0f}")


def main():
    today = date.today()
    last_sat = _last_completed_sat(today)
    print(f"Today: {today}.  LW = week ending {last_sat} "
          f"({(last_sat - timedelta(days=6)).strftime('%m/%d')} – "
          f"{last_sat.strftime('%m/%d/%y')})\n")

    print(f"[1/3] Pulling current Projections for {len(SAMPLE)} keys ...")
    current = fetch_projections_for_keys(SAMPLE)
    print(f"      {len(current)} records loaded\n")

    print(f"[2/3] Pulling Order_History for acct 1864 ...")
    orders = fetch_orders_for_acct(1864, today)
    print(f"      {len(orders)} order rows pulled\n")

    print(f"[3/3] Computing corrections ...")
    proposed = compute_corrections(orders, today, set(SAMPLE))
    print(f"      {len(proposed)} keys with Order_History activity\n")

    for key in SAMPLE:
        if key in current and key in proposed:
            print_record_diff(key, current[key], proposed[key], last_sat)
        elif key in current:
            print(f"\n{key}: no Order_History found (might be Inactive)")
        else:
            print(f"\n{key}: not found in Projections")


if __name__ == "__main__":
    main()
