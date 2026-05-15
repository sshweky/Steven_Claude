"""
Build empirically-derived monthly seasonality profiles by Product_Category and
(Category, Subcategory) from invoice ship history.

Source : Quickbase1.ProductTrack.Invoices — one row per invoice line.
Window : 2024-01-01 → today (2024 + 2025 + 2026 YTD).
Method :
  1. Single CData GROUP BY pulls SUM(Qty_Shpd) per (year, month, category,
     subcategory).  Server-side aggregation collapses millions of rows to ~1k.
  2. Roll up to category-only and (category, subcategory) levels.
  3. Build 12-element monthly demand index (mean = 1.0) across ALL years
     pooled together — pooling smooths year-on-year noise.
  4. Quality gates per profile:
       - min ≥ 100,000 units total
       - ≥ 3 active months (>0)
       - peak/trough ≥ 1.30   (anything below this is functionally flat)
       - ≥ 2 of 3 years contributing
  5. Save to scripts/derived_category_profiles.json.

Follows QB API guidelines:
  - one bulk read with server-side GROUP BY (vs. pulling raw rows)
  - tight projection (4 columns) and server-side WHERE filter
  - smoke test with TOP 1 before the full pull
  - exponential backoff via cdata_query() built-in retries
"""
import sys, json, time, math
from collections import defaultdict
from datetime import date

sys.path.insert(0, "scripts")
from inventory_forecaster import cdata_query

INVOICE_TBL  = "[Quickbase1].[ProductTrack].[Invoices]"
START_YEAR   = 2024
END_YEAR     = date.today().year      # 2026 currently
OUT_PATH     = "scripts/derived_category_profiles.json"

# Quality gates
MIN_TOTAL_UNITS  = 100_000      # skip categories under this volume
MIN_ACTIVE_MONTHS = 3
MIN_PEAK_TROUGH   = 1.30        # below this is functionally flat
MIN_YEARS         = 2           # need at least 2 of 3 years contributing


def smoke_test():
    """Tiny TOP 1 to confirm the table responds before the bulk pull."""
    print("[smoke] testing Invoices table reachability ...", flush=True)
    rows = cdata_query(
        f"SELECT TOP 1 [YYYY], [Product_Category], [Qty_Shpd] FROM {INVOICE_TBL}",
        "smoke",
    )
    if not rows:
        sys.exit("[ABORT] smoke test failed — Invoices table not reachable.")
    print(f"[smoke] ok — sample row: {rows[0]}", flush=True)


def fetch_aggregated():
    """
    Year-by-year bulk GROUP BY.  Single combined 3-year query times out due to
    QB-side throttling on the Invoices table; year-at-a-time slices are well
    within QB's per-call budget AND give us per-year resumability.

    Server-side aggregation: pull only the sums and grouping keys, never raw
    invoice lines.  Tight projection (4 grouping cols + 1 sum).
    """
    all_rows = []
    cache_path = "cache/invoice_grouped_by_year.json"
    import os
    os.makedirs("cache", exist_ok=True)

    # Load existing cache (per-year) so retries don't re-pull completed years
    cache = {}
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
            print(f"[cache] resumed — {sorted(cache.keys())} already pulled", flush=True)
        except Exception:
            cache = {}

    for year in range(START_YEAR, END_YEAR + 1):
        ykey = str(year)
        if ykey in cache and cache[ykey]:
            print(f"[fetch] {year}: using cached ({len(cache[ykey]):,} rows)", flush=True)
            all_rows.extend(cache[ykey])
            continue
        sql = (
            "SELECT "
            "  [YYYY] AS yyyy, "
            "  MONTH([Shpd_Date]) AS mm, "
            "  [Product_Category] AS cat, "
            "  [Product_Subcategory] AS subcat, "
            "  SUM([Qty_Shpd]) AS qty "
            f"FROM {INVOICE_TBL} "
            f"WHERE [YYYY] = {year} AND [Qty_Shpd] > 0 "
            "GROUP BY [YYYY], MONTH([Shpd_Date]), [Product_Category], [Product_Subcategory]"
        )
        print(f"[fetch] {year}: running GROUP BY ...", flush=True)
        t0 = time.time()
        rows = cdata_query(sql, f"invoice_grouped_{year}")
        elapsed = time.time() - t0
        print(f"[fetch] {year}: {len(rows):,} rows in {elapsed:.1f}s", flush=True)
        if not rows:
            print(f"[warn] {year}: 0 rows — skipping (could be throttle)", flush=True)
            continue
        cache[ykey] = rows
        # checkpoint after every successful year
        with open(cache_path, "w") as f:
            json.dump(cache, f)
        all_rows.extend(rows)
        # small breather between years to avoid back-to-back pressure
        time.sleep(2)

    if not all_rows:
        sys.exit("[ABORT] all years returned 0 rows — likely a throttle / connection issue.")
    print(f"[fetch] total {len(all_rows):,} aggregation rows across {END_YEAR-START_YEAR+1} years", flush=True)
    return all_rows


def _norm_text(s):
    return (s or "").strip()


def build_profiles(rows):
    """
    Roll up to (a) category-only and (b) (category, subcategory) levels.
    Compute 12-element monthly index normalized to mean=1.0.
    Apply quality gates.

    Returns:
      {
        "by_category":     { "<cat>": { "profile": [12 floats], "stats": {...} } },
        "by_subcategory":  { "<cat>||<sub>": { "profile": [12], "stats": {...} } },
      }
    """
    # Aggregate by month
    cat_month  = defaultdict(lambda: [0.0] * 12)   # cat -> 12 monthly totals
    sub_month  = defaultdict(lambda: [0.0] * 12)   # (cat,sub) -> 12 monthly totals
    cat_years  = defaultdict(set)                   # cat -> set(years that contributed)
    sub_years  = defaultdict(set)
    cat_total  = defaultdict(float)
    sub_total  = defaultdict(float)

    for r in rows:
        try:
            yyyy = int(r["yyyy"])
            mm   = int(r["mm"])
            qty  = float(r["qty"] or 0)
        except (TypeError, ValueError):
            continue
        cat = _norm_text(r.get("cat"))
        sub = _norm_text(r.get("subcat"))
        if not cat or qty <= 0 or mm < 1 or mm > 12:
            continue
        cat_month[cat][mm-1] += qty
        cat_total[cat]       += qty
        cat_years[cat].add(yyyy)
        if sub:
            key = f"{cat}||{sub}"
            sub_month[key][mm-1] += qty
            sub_total[key]       += qty
            sub_years[key].add(yyyy)

    def _to_index(monthly_totals, total_units, years):
        """Returns (profile|None, stats_dict, reason_if_skipped)."""
        active_months = sum(1 for v in monthly_totals if v > 0)
        if total_units < MIN_TOTAL_UNITS:
            return None, None, f"total_units {total_units:,.0f} < {MIN_TOTAL_UNITS:,}"
        if active_months < MIN_ACTIVE_MONTHS:
            return None, None, f"active_months {active_months} < {MIN_ACTIVE_MONTHS}"
        if len(years) < MIN_YEARS:
            return None, None, f"years {sorted(years)} < {MIN_YEARS}"

        mean = total_units / 12.0
        if mean <= 0:
            return None, None, "mean=0"
        idx = [v / mean for v in monthly_totals]

        peak = max(idx)
        trough = min(i for i in idx if i > 0) if any(i > 0 for i in idx) else 0
        peak_trough = (peak / trough) if trough > 0 else float("inf")

        if peak_trough < MIN_PEAK_TROUGH:
            return None, None, f"peak/trough {peak_trough:.2f} < {MIN_PEAK_TROUGH:.2f} (flat)"

        # round for compactness
        idx_rounded = [round(v, 3) for v in idx]
        stats = {
            "total_units":  int(round(total_units)),
            "years":        sorted(years),
            "peak_month":   idx.index(peak) + 1,
            "peak_idx":     round(peak, 2),
            "trough_idx":   round(trough, 2),
            "peak_trough":  round(peak_trough, 2),
            "active_months": active_months,
        }
        return idx_rounded, stats, None

    by_category, by_subcategory = {}, {}
    skipped_cat, skipped_sub = [], []

    for cat, monthly in cat_month.items():
        prof, stats, why = _to_index(monthly, cat_total[cat], cat_years[cat])
        if prof:
            by_category[cat] = {"profile": prof, "stats": stats}
        else:
            skipped_cat.append((cat, cat_total[cat], why))

    for key, monthly in sub_month.items():
        prof, stats, why = _to_index(monthly, sub_total[key], sub_years[key])
        if prof:
            by_subcategory[key] = {"profile": prof, "stats": stats}
        else:
            skipped_sub.append((key, sub_total[key], why))

    return by_category, by_subcategory, skipped_cat, skipped_sub


def main():
    print(f"[start] {date.today()} — building category profiles "
          f"{START_YEAR}-{END_YEAR} from {INVOICE_TBL}", flush=True)
    smoke_test()
    rows = fetch_aggregated()

    by_cat, by_sub, skipped_cat, skipped_sub = build_profiles(rows)

    print(f"\n[result] kept {len(by_cat)} categories, {len(by_sub)} subcategories", flush=True)
    print(f"[result] skipped {len(skipped_cat)} categories, {len(skipped_sub)} subcategories", flush=True)

    # Sort kept categories by total units desc for the printed summary
    print("\nTop categories by volume (kept):")
    sorted_cats = sorted(by_cat.items(), key=lambda kv: -kv[1]["stats"]["total_units"])
    for cat, payload in sorted_cats[:20]:
        s = payload["stats"]
        peak_m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][s["peak_month"]-1]
        print(f"  {cat:35s}  {s['total_units']:>12,}  peak={peak_m} ({s['peak_idx']:.2f})  p/t={s['peak_trough']:.2f}")

    # Show top 10 skipped categories so we can see what was dropped and why
    print("\nTop skipped categories (by volume):")
    for cat, total, why in sorted(skipped_cat, key=lambda x: -x[1])[:10]:
        print(f"  {cat:35s}  {int(total):>12,}  ({why})")

    out = {
        "generated_at": date.today().isoformat(),
        "source_table": "Quickbase1.ProductTrack.Invoices",
        "window":       {"start_year": START_YEAR, "end_year": END_YEAR},
        "method":       "monthly Qty_Shpd index, mean=1.0, pooled across years",
        "gates": {
            "min_total_units":  MIN_TOTAL_UNITS,
            "min_active_months": MIN_ACTIVE_MONTHS,
            "min_peak_trough":   MIN_PEAK_TROUGH,
            "min_years":         MIN_YEARS,
        },
        "by_category":    by_cat,
        "by_subcategory": by_sub,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[saved] {OUT_PATH}  ({len(by_cat)} cats + {len(by_sub)} subcats)", flush=True)


if __name__ == "__main__":
    main()
