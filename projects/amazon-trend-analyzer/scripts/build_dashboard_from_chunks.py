#!/usr/bin/env python3
"""
build_dashboard_from_chunks.py — Take the CSVs pulled by qb_rest_pull.py,
run the trend engine, and produce the interactive HTML dashboard.

Run after qb_rest_pull.py finishes successfully.

USAGE:
  python build_dashboard_from_chunks.py [--chunks-dir ./qb_chunks]
                                         [--out dashboard.html]
                                         [--baseline exclusive|inclusive]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import pull_data, trend_engine as te, html_builder as hb


def load_chunks(chunks_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (weekly_df, catalog_df).

    weekly_df:  per-(asin, week_start) aggregated metrics, ready for trend_engine
    catalog_df: per-ASIN attributes for the dashboard panel
    """
    # Look for the stitched file first (cleanest), then page_*.csv from the
    # report-based pull, then the legacy chunk_*.csv naming.
    stitched = chunks_dir / "all_daily.csv"
    if stitched.exists() and stitched.stat().st_size > 0:
        print(f"Loading stitched file: {stitched.name}")
        daily = pd.read_csv(stitched)
    else:
        # Fall back to combining individual CSVs (page_*.csv from report pull,
        # or chunk_*.csv from date-range pull)
        csvs = sorted(list(chunks_dir.glob("page_*.csv"))
                    + list(chunks_dir.glob("chunk_*.csv")))
        if not csvs:
            sys.exit(f"[ABORT] No data files in {chunks_dir}\n"
                     f"        Looked for: all_daily.csv, page_*.csv, chunk_*.csv\n"
                     f"        Did qb_rest_pull.py complete successfully?")
        print(f"Loading {len(csvs)} CSV file(s)...")
        daily = pd.concat([pd.read_csv(p) for p in csvs], ignore_index=True)

    # Dedup defensively in case of overlapping chunks
    if "asin" in daily.columns and "date" in daily.columns:
        daily = daily.drop_duplicates(subset=["asin", "date"])
    elif "ASIN" in daily.columns and "Date" in daily.columns:
        daily = daily.drop_duplicates(subset=["ASIN", "Date"])
        # Normalize the column names to lowercase short forms
        daily = daily.rename(columns={"ASIN": "asin", "Date": "date"})

    # Late-binding column normalization for fields the QB report uses
    # different names for than the trend engine expects:
    late_renames = {
        "CVR%":              "cr",
        "CVR_":              "cr",
        "ASIN (Description)": "description",
        "Master Pack":       "pack_size",
        "Master_Pack":       "pack_size",
        "Has A+ Content":    "has_aplus",
        "Has Coupon?":       "has_coupon",
        "In Stock Today?":   "in_stock_today",
        "Buybox Winner":     "buybox_winner",
        "Sellable On Hand Units": "sellable_units",
        "Number of Ratings": "num_ratings",
        "Average Rating":    "avg_rating",
    }
    daily = daily.rename(columns={k: v for k, v in late_renames.items()
                                  if k in daily.columns})

    # CVR sometimes comes back as a percent (0–100) rather than a fraction.
    # The trend engine expects 0–1, so coerce if needed.
    if "cr" in daily.columns:
        cr_max = pd.to_numeric(daily["cr"], errors="coerce").max()
        if cr_max is not None and cr_max > 1.5:   # clearly a percent
            daily["cr"] = pd.to_numeric(daily["cr"], errors="coerce") / 100

    # Synthesize an oos_signal proxy if the raw OOS field isn't present.
    # `in_stock_today` is a boolean — 1 = in stock, 0 = OOS. We invert it
    # so oos_signal sums to "OOS days" the same way Rep_OOS does.
    if "oos_signal" not in daily.columns and "in_stock_today" in daily.columns:
        is_oos = daily["in_stock_today"].astype(str).str.lower().isin(
            ["false", "0", "no"])
        daily["oos_signal"] = is_oos.astype(int)

    print(f"  Total daily rows: {len(daily):,}")
    if "asin" in daily.columns:
        print(f"  Unique ASINs:     {daily['asin'].nunique():,}")
    if "date" in daily.columns:
        print(f"  Date range:       {daily['date'].min()} → {daily['date'].max()}")

    # Aggregate to weekly grain
    weekly = pull_data.aggregate_daily_to_weekly(daily)
    print(f"  Weekly rows:      {len(weekly):,}")

    # Build the catalog DataFrame from the most-recent row per ASIN
    daily["date"] = pd.to_datetime(daily["date"])
    latest_per_asin = daily.sort_values("date").drop_duplicates("asin", keep="last")

    cat_cols = ["asin"]
    for c in ["brand", "description", "pack_size", "category"]:
        if c in latest_per_asin.columns:
            cat_cols.append(c)
    catalog = latest_per_asin[cat_cols].copy()

    # html_builder also looks for master_brand; fall back to brand if absent
    catalog["master_brand"] = catalog["brand"] if "brand" in catalog.columns else "—"

    # list_price proxy: latest avg_sales_price if available
    if "asp" in latest_per_asin.columns:
        catalog["list_price"] = latest_per_asin["asp"].values
    return weekly, catalog


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks-dir", default="./qb_chunks",
                    help="Directory containing chunk_*.csv from qb_rest_pull.py")
    ap.add_argument("--out", default="./amazon_trend_dashboard.html",
                    help="Output HTML file path")
    ap.add_argument("--baseline", default="exclusive",
                    choices=["exclusive", "inclusive"],
                    help="Bucket assignment uses this baseline mode")
    args = ap.parse_args()

    chunks_dir = Path(args.chunks_dir)
    if not chunks_dir.exists():
        sys.exit(f"[ABORT] {chunks_dir} does not exist. "
                 f"Did you run qb_rest_pull.py first?")

    weekly, catalog = load_chunks(chunks_dir)

    if len(weekly) == 0:
        sys.exit("[ABORT] No weekly data after aggregation")

    weeks_per_asin = weekly.groupby("asin").size()
    print(f"\nPer-ASIN week counts:  min={weeks_per_asin.min()}  "
          f"median={int(weeks_per_asin.median())}  max={weeks_per_asin.max()}")

    if weeks_per_asin.max() < 5:
        print("\n⚠️  Fewer than 5 weeks of data per ASIN — trend windows will be"
              " unstable. Pull more history with WEEKS_BACK=17 or 52 in qb_rest_pull.py")

    print(f"\nRunning trend engine (baseline mode: {args.baseline})...")
    results = te.analyze_all(weekly, baseline_mode=args.baseline)

    summary = te.bucket_summary(results)
    print("\nBucket summary:")
    if not summary.empty:
        print(summary.to_string(index=False))

    print(f"\nRendering dashboard → {args.out}")
    out = hb.render(results, weekly, catalog,
                    out_path=args.out, baseline_mode=args.baseline)
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"  ✓ {out}  ({size_mb:.1f} MB)")
    print(f"\nOpen in a browser: file://{out.resolve()}")


if __name__ == "__main__":
    main()
