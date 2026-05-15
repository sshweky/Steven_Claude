"""
pull_data.py — Pull weekly-aggregated Amazon sales + catalog from Quickbase
via CData.  Follows the Quickbase API Rules:
  - One bulk read per table (no per-ASIN looping)
  - Project only needed columns; never SELECT *
  - Server-side filter (active ASINs, last 52 weeks)
  - Smoke test (TOP 1) before bulk
  - Cache results to local CSV so analysis is fast & QB is touched once

This module is the *integration glue*.  All math is downstream in
trend_engine.py / driver_decomp.py.

USAGE (chat-driven via CData Connect AI MCP):
  The skill router (SKILL.md) drives the CData calls directly through
  the MCP tools.  This module provides the canonical SQL strings and the
  CSV column normalization step.
"""
from __future__ import annotations

import pandas as pd
from pathlib import Path
from datetime import date, timedelta


# -----------------------------------------------------------------------------
# SQL queries — copy these into ClaudeAI:ClaudeAI_run_query through the MCP
# -----------------------------------------------------------------------------

def smoke_test_sql() -> str:
    """1-row smoke test. Run this BEFORE the bulk pulls. Required by QB rules §6.1."""
    return """SELECT TOP 1 [Start_Date], [ASIN], [Ordered_Units]
                 FROM [ProductTrack].[Amazon_Daily_Sales]"""


def date_range_sql() -> str:
    """Confirm we have ≥52W of history and reasonable row volume."""
    return """SELECT MIN([Start_Date]) AS min_date,
                     MAX([Start_Date]) AS max_date,
                     COUNT(*)          AS row_count,
                     COUNT(DISTINCT [ASIN]) AS asin_count
                 FROM [ProductTrack].[Amazon_Daily_Sales]
                 WHERE [Start_Date] >= DATE_SUB(CURRENT_DATE, INTERVAL 60 WEEK)"""


def daily_metrics_sql(start_date: str, end_date: str | None = None,
                      status_value: str = "ACTIVE") -> str:
    """Pull DAILY ASIN metrics from Amazon_AdTrack.Daily_Metrics.

    Why Daily_Metrics instead of Amazon_Daily_Sales:
      - Bestseller_Rank IS populated here (vs 100% NULL on Amazon_Daily_Sales)
      - Lost_Sales_Units_Due_to_OOS_ + Lost_Sales_Units_Due_to_LBB_ give
        concrete lost-demand counts (a real driver signal, not a flag)
      - Catalog attributes (Master_Brand, Master_Pack, Product_Category,
        ASIN_Description_) are pre-joined here — no separate catalog pull
      - Buybox_Price is here, enabling buy-box loss as a driver
      - Has_A_Content and Has_Coupon_ flags add drill-down context

    Column name notes (gotchas relative to Amazon_Daily_Sales):
      - Date column is `Date` (not `Start_Date`)
      - Conversion rate is `CVR_` (not `Conversion_Rate`)
      - Description is `ASIN_Description_` (with underscore suffix)

    Recommended chunking: 13 weeks per call. Four chunks cover 52 weeks.
    Volume estimate: ~50K rows per 13-week chunk for ~900 ACTIVE ASINs/day.
    """
    where = [
        f"[Date] >= '{start_date}'",
        f"[ASIN_Status] = '{status_value}'",
        "[Ordered_Units] > 0",
    ]
    if end_date:
        where.append(f"[Date] <= '{end_date}'")
    where_sql = " AND ".join(where)
    return f"""
        SELECT [ASIN],
               [Date],
               [Master_Brand]                      AS brand,
               [ASIN_Description_]                 AS description,
               [Master_Pack]                       AS pack_size,
               [Product_Category]                  AS category,
               [Ordered_Units]                     AS units,
               [Ordered_Revenue]                   AS revenue,
               [Glance_Views]                      AS gv,
               [CVR_]                              AS cr,
               [Average_Sales_Price]               AS asp,
               [Rep_OOS]                           AS oos_signal,
               [Lost_Sales_Units_Due_to_OOS_]      AS lost_oos,
               [Lost_Sales_Units_Due_to_LBB_]      AS lost_lbb,
               [Bestseller_Rank]                   AS bsr,
               [Buybox_Price]                      AS bb_price
          FROM [Amazon_AdTrack].[Daily_Metrics]
         WHERE {where_sql}
    """.strip()


# Backwards-compatibility alias for the old name
daily_sales_sql = daily_metrics_sql


def date_chunks(weeks_back: int = 52, chunk_weeks: int = 13) -> list[tuple[str, str]]:
    """Generate date-range chunks for the bulk pull.

    Returns a list of (start_date, end_date) tuples covering the last
    `weeks_back` weeks in `chunk_weeks`-week increments.

    Example: weeks_back=52, chunk_weeks=13 → 4 chunks of 13 weeks each.
    """
    today = date.today()
    end = today
    chunks = []
    for _ in range((weeks_back + chunk_weeks - 1) // chunk_weeks):
        start = end - timedelta(weeks=chunk_weeks) + timedelta(days=1)
        chunks.append((start.isoformat(), end.isoformat()))
        end = start - timedelta(days=1)
    return list(reversed(chunks))


def aggregate_daily_to_weekly(daily: pd.DataFrame,
                              week_anchor: str = "W-SUN") -> pd.DataFrame:
    """Aggregate daily rows to Monday-anchored weekly buckets.

    Args:
        daily: DataFrame with at minimum [asin, date, units, revenue, gv,
               cr, asp, oos_signal, bsr]. Optional new columns from
               Daily_Metrics: lost_oos, lost_lbb, bb_price, brand,
               description, pack_size, category.
        week_anchor: pandas frequency. W-SUN means weeks end Sunday → start
                     Monday, which matches P+P's convention.

    Returns:
        Weekly-aggregated DataFrame: one row per (asin, week_start).
        Catalog attributes (brand, description, etc.) carry through via "first".
    """
    daily = daily.copy()
    daily.columns = [c.lower() for c in daily.columns]

    # Accept either "date" (Daily_Metrics) or "start_date" (legacy Amazon_Daily_Sales)
    date_col = "date" if "date" in daily.columns else "start_date"
    if date_col not in daily.columns:
        raise ValueError("daily DataFrame must have a 'date' or 'start_date' column")
    daily[date_col] = pd.to_datetime(daily[date_col], errors="coerce")
    daily = daily.dropna(subset=[date_col, "asin"])

    # Bucket every date to the Monday of that week
    daily["week_start"] = (
        daily[date_col] - pd.to_timedelta(daily[date_col].dt.weekday, unit="D")
    ).dt.date.astype(str)

    # Build the aggregation map dynamically based on what columns are present.
    # Sum: volumes/counts. Mean: rates/prices. First: per-ASIN attributes that
    # don't change within a week (brand, category, description, pack size).
    agg_map = {}
    for col, how in [
        ("units",      "sum"),
        ("revenue",    "sum"),
        ("gv",         "sum"),
        ("oos_signal", "sum"),
        ("lost_oos",   "sum"),
        ("lost_lbb",   "sum"),
        ("cr",         "mean"),
        ("asp",        "mean"),
        ("bb_price",   "mean"),
        ("bsr",        "mean"),
        ("brand",        "first"),
        ("description",  "first"),
        ("pack_size",    "first"),
        ("category",     "first"),
    ]:
        if col in daily.columns:
            agg_map[col] = (col, how)

    agg = (daily.groupby(["asin", "week_start"], as_index=False)
                .agg(**agg_map))
    return agg.sort_values(["asin", "week_start"]).reset_index(drop=True)


def catalog_attributes_sql(asin_list: list[str] | None = None) -> str:
    """Pull catalog attributes for the ASINs in the sales pull.

    Schema confirmed: Amazon_Catalog lives in [Amazon_AdTrack], NOT [ProductTrack].
    Table has 700+ columns; we project only the ~11 we need for the dashboard.

    Args:
        asin_list: optional ASIN allow-list. Quickbase IN clauses cap at ~1000.
                   If you have more, chunk and call multiple times.
    """
    columns = [
        "[ASIN]",
        "[ASIN_Status]",
        "[Brand]",
        "[Master_Brand]",
        "[Listing_Title]",                  # the readable product name
        "[Master_Pack]",                    # pack size
        "[Amazon_List_Price]",
        "[Product_Category]",
        "[Product_Subcategory]",
        "[ASIN_Age]",                       # days since launch
        "[Date_1st_Shpd_to_Amazon_ASIN_]",  # launch date
    ]
    col_clause = ", ".join(columns)

    where = "WHERE [ASIN_Status] = 'ACTIVE'"
    if asin_list:
        quoted = ", ".join(f"'{a}'" for a in asin_list[:1000])
        where += f" AND [ASIN] IN ({quoted})"
    return f"SELECT {col_clause} FROM [Amazon_AdTrack].[Amazon_Catalog] {where}".strip()


# -----------------------------------------------------------------------------
# CSV normalization (the post-pull cleanup)
# -----------------------------------------------------------------------------

EXPECTED_WEEKLY_COLUMNS = [
    "asin", "week_start", "units", "revenue", "gv", "cr", "asp", "oos_signal", "bsr"
]


def normalize_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce types + ensure expected columns are present and named correctly.

    CData returns column names matching the SQL aliases.  Lowercase them
    for downstream consistency.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    # Some QB datetime cols come back as strings — keep as ISO date strings
    # for the JSON payload, but coerce to date for the sort
    if "week_start" in df.columns:
        df["week_start"] = pd.to_datetime(df["week_start"], errors="coerce").dt.date.astype(str)
    for c in ["units", "revenue", "gv", "cr", "asp", "bsr"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "oos_signal" in df.columns:
        df["oos_signal"] = pd.to_numeric(df["oos_signal"], errors="coerce").fillna(0).astype(int)
    # Drop rows missing the keys
    df = df.dropna(subset=["asin", "week_start"])
    return df.sort_values(["asin", "week_start"]).reset_index(drop=True)


def normalize_catalog(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.lower().replace("_", "").replace(" ", "") for c in df.columns]
    # We accept whatever attributes came back; the dashboard only requires asin + brand
    # Map back to the names the html_builder expects
    rename_map = {
        "description":  "description",
        "description_": "description",
        "masterbrand":  "master_brand",
        "packsize":     "pack_size",
        "listprice":    "list_price",
    }
    df = df.rename(columns=rename_map)
    if "asin" not in df.columns:
        raise ValueError("catalog pull missing ASIN column")
    return df.drop_duplicates(subset=["asin"]).reset_index(drop=True)


# -----------------------------------------------------------------------------
# Local cache so we don't re-pull on every dashboard rebuild
# -----------------------------------------------------------------------------

DEFAULT_CACHE = Path.home() / ".cache" / "amazon-trend-analyzer"


def cache_paths(cache_dir: Path | None = None) -> dict[str, Path]:
    base = (cache_dir or DEFAULT_CACHE)
    base.mkdir(parents=True, exist_ok=True)
    return {
        "weekly":  base / "weekly.csv",
        "catalog": base / "catalog.csv",
        "stamp":   base / "pulled_at.txt",
    }


def load_cached(cache_dir: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    p = cache_paths(cache_dir)
    if not (p["weekly"].exists() and p["catalog"].exists()):
        return None
    return pd.read_csv(p["weekly"]), pd.read_csv(p["catalog"])


def save_to_cache(weekly: pd.DataFrame, catalog: pd.DataFrame,
                  cache_dir: Path | None = None) -> None:
    p = cache_paths(cache_dir)
    weekly.to_csv(p["weekly"], index=False)
    catalog.to_csv(p["catalog"], index=False)
    p["stamp"].write_text(pd.Timestamp.now().isoformat())
