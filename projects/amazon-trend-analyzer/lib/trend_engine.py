"""
trend_engine.py — Core trend math for Amazon ASIN sales.

Input: weekly-aggregated DataFrame with columns
    [asin, week_start, units, revenue, gv, cr, asp, oos_signal, bsr]
    Each ASIN should have one row per week, ideally 52 weeks of history.

Output: per-ASIN dict with bucket assignment, all indices (units + revenue,
inclusive + exclusive baselines for L13/L26/L52), and flags.

This module has no Quickbase / CData dependency — it is pure pandas math.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Iterable

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Trend window sizes (in weeks)
RECENT_W = 4         # the L4W window we always compare from
BASELINES = (13, 26, 52)

# Sign threshold: |index - 1.0| < THRESH means "flat" (0 signal)
SIGN_THRESHOLD = 0.05    # ±5%

# Volatility flag: CV (coefficient of variation) above this on L13W units → "volatile"
VOLATILE_CV = 0.30


# -----------------------------------------------------------------------------
# Bucket assignment from the 3-window sign pattern
# -----------------------------------------------------------------------------

# The pattern is (sign_vs_L13, sign_vs_L26, sign_vs_L52) where each is -1, 0, +1.
# Lookup yields a stable bucket key.  Patterns not in this table → "stable".

BUCKETS = {
    # All three positive → strongest growth story
    ( 1,  1,  1): ("strong_winner",   "🚀 Strong Winner"),

    # Recent up, longer-term up, lapping flat/soft year
    ( 1,  1,  0): ("accelerating",    "🔥 Accelerating"),
    ( 1,  1, -1): ("accelerating",    "🔥 Accelerating"),

    # Recent up, mid-term flat, long-term up
    ( 1,  0,  1): ("recovering",      "🔄 Recovering"),
    ( 1,  0,  0): ("recovering",      "🔄 Recovering"),
    ( 1, -1,  1): ("recovering",      "🔄 Recovering"),

    # Recent up but trend still down at every other horizon
    ( 1,  0, -1): ("surge_on_decline","⚠️ Surge on Decline"),
    ( 1, -1, -1): ("surge_on_decline","⚠️ Surge on Decline"),
    ( 1, -1,  0): ("surge_on_decline","⚠️ Surge on Decline"),

    # Recent flat / mild dip with healthy long-term — watch list
    ( 0,  1,  1): ("cooling_winner",  "🧊 Cooling Winner"),
    ( 0,  0,  1): ("cooling_winner",  "🧊 Cooling Winner"),
    ( 0,  1,  0): ("stable",          "😴 Stable"),
    ( 0,  0,  0): ("stable",          "😴 Stable"),
    ( 0, -1,  0): ("soft",            "💤 Soft"),
    ( 0,  0, -1): ("soft",            "💤 Soft"),

    # Recent down, mixed
    (-1,  1,  1): ("cooling_winner",  "🧊 Cooling Winner"),
    (-1,  0,  1): ("new_decline",     "📉 New Decline"),
    (-1, -1,  1): ("new_decline",     "📉 New Decline"),
    (-1,  1,  0): ("cooling_winner",  "🧊 Cooling Winner"),
    (-1,  1, -1): ("lapping_softness","🔄 Lapping Softness"),
    (-1, -1,  0): ("sustained_decline","💀 Sustained Decline"),

    # All three negative → sustained decline
    (-1, -1, -1): ("sustained_decline","💀 Sustained Decline"),
    (-1,  0,  0): ("soft",            "💤 Soft"),
    (-1,  0, -1): ("sustained_decline","💀 Sustained Decline"),
}


def _sign(idx: float, threshold: float = SIGN_THRESHOLD) -> int:
    """Convert a ratio (1.0 = no change) to -1 / 0 / +1 based on threshold."""
    if pd.isna(idx):
        return 0
    if idx > 1 + threshold:
        return 1
    if idx < 1 - threshold:
        return -1
    return 0


def bucket_from_pattern(s13: int, s26: int, s52: int) -> tuple[str, str]:
    """Return (bucket_key, bucket_label) for a 3-sign pattern."""
    return BUCKETS.get((s13, s26, s52), ("stable", "😴 Stable"))


# -----------------------------------------------------------------------------
# Index math (the L4 vs L13 / L26 / L52 calculations)
# -----------------------------------------------------------------------------

def compute_window_means(series: pd.Series, window: int) -> float:
    """Mean over the last `window` periods. Returns NaN if not enough data."""
    s = series.dropna()
    if len(s) < window:
        return np.nan
    return float(s.iloc[-window:].mean())


def compute_exclusive_baseline(series: pd.Series, recent: int, baseline: int) -> float:
    """Mean over the `baseline` periods *before* the most recent `recent` periods.

    e.g. exclusive L13 = weeks [-recent-baseline : -recent].
    Used to isolate the trend cleanly — does NOT include L4 in L13.
    """
    s = series.dropna()
    if len(s) < recent + baseline:
        return np.nan
    return float(s.iloc[-(recent + baseline):-recent].mean())


def safe_index(numerator: float, denominator: float) -> float:
    """Compute numerator/denominator with safe handling of zero / NaN."""
    if pd.isna(numerator) or pd.isna(denominator) or denominator == 0:
        return np.nan
    return numerator / denominator


def compute_all_indices(weekly: pd.DataFrame, metric: str) -> dict:
    """Compute all 6 indices (inclusive + exclusive × L13/L26/L52) for one ASIN
    on a single metric (units or revenue).

    Assumes weekly is sorted ascending by week_start.
    """
    s = weekly[metric].astype(float)
    l4 = compute_window_means(s, RECENT_W)
    out = {}
    for b in BASELINES:
        # Inclusive: baseline window includes the L4
        inc_mean = compute_window_means(s, b)
        # Exclusive: baseline window is the b weeks BEFORE the L4
        exc_mean = compute_exclusive_baseline(s, RECENT_W, b - RECENT_W) \
            if b > RECENT_W else np.nan
        out[f"l4_vs_l{b}_inc"] = safe_index(l4, inc_mean)
        out[f"l4_vs_l{b}_exc"] = safe_index(l4, exc_mean)
    out["l4_mean"] = l4
    return out


# -----------------------------------------------------------------------------
# Driver decomposition (Phase-2 hook — kept here for symmetry, full impl in
# driver_decomp.py)
# -----------------------------------------------------------------------------

def short_driver_summary(weekly: pd.DataFrame, baseline_weeks: int = 13) -> dict:
    """Quick L4-vs-baseline driver % changes for the 5 driver fields.

    For the full driver narrative, see driver_decomp.py.
    """
    metrics = {
        "gv":  "glance_views",
        "cr":  "conversion_rate",
        "asp": "avg_sales_price",
    }
    out = {}
    for col, label in metrics.items():
        if col not in weekly.columns:
            out[label] = None
            continue
        l4 = compute_window_means(weekly[col], RECENT_W)
        base = compute_exclusive_baseline(weekly[col], RECENT_W, baseline_weeks - RECENT_W)
        out[label] = safe_index(l4, base) - 1.0 if not pd.isna(safe_index(l4, base)) else None

    # OOS days in L4W
    if "oos_signal" in weekly.columns:
        last4 = weekly.dropna(subset=["oos_signal"]).iloc[-RECENT_W:]
        out["oos_days_l4"] = int(last4["oos_signal"].sum())
    else:
        out["oos_days_l4"] = None

    # BSR — improvement is a *decrease* in numeric rank
    if "bsr" in weekly.columns:
        l4 = compute_window_means(weekly["bsr"], RECENT_W)
        base = compute_exclusive_baseline(weekly["bsr"], RECENT_W, baseline_weeks - RECENT_W)
        out["bsr_shift"] = (l4 - base) if not pd.isna(l4) and not pd.isna(base) else None
    else:
        out["bsr_shift"] = None

    return out


# -----------------------------------------------------------------------------
# Volatility flag
# -----------------------------------------------------------------------------

def volatility_flag(weekly: pd.DataFrame, metric: str = "units") -> bool:
    """Coefficient of variation on the L13W of `metric` exceeds VOLATILE_CV."""
    s = weekly[metric].dropna().astype(float)
    if len(s) < 13:
        return False
    last13 = s.iloc[-13:]
    mean = last13.mean()
    if mean == 0:
        return False
    return (last13.std() / mean) > VOLATILE_CV


# -----------------------------------------------------------------------------
# Main entry point — process one ASIN end-to-end
# -----------------------------------------------------------------------------

def analyze_asin(weekly: pd.DataFrame, asin: str, baseline_mode: str = "exclusive") -> dict:
    """Run the full trend analysis for one ASIN's weekly time series.

    Args:
        weekly: weekly-aggregated DataFrame for THIS asin only, sorted ascending
                by week_start, with columns [units, revenue, gv, cr, asp,
                oos_signal, bsr].
        asin: the ASIN string.
        baseline_mode: 'exclusive' (default — used for bucket assignment) or
                       'inclusive' (smoother — shown alongside in the UI).

    Returns:
        Dict with bucket, label, indices, drivers, flags, totals.
    """
    if weekly.empty:
        return {"asin": asin, "bucket": "no_data", "bucket_label": "No data"}

    weekly = weekly.sort_values("week_start").reset_index(drop=True)

    units_ix   = compute_all_indices(weekly, "units")
    revenue_ix = compute_all_indices(weekly, "revenue")

    # Composite index = average of units and revenue for the *active* baseline mode
    suffix = "_exc" if baseline_mode == "exclusive" else "_inc"
    composite = {
        f"l4_vs_l{b}": np.nanmean([
            units_ix.get(f"l4_vs_l{b}{suffix}", np.nan),
            revenue_ix.get(f"l4_vs_l{b}{suffix}", np.nan),
        ])
        for b in BASELINES
    }
    s13 = _sign(composite["l4_vs_l13"])
    s26 = _sign(composite["l4_vs_l26"])
    s52 = _sign(composite["l4_vs_l52"])
    bucket_key, bucket_label = bucket_from_pattern(s13, s26, s52)

    # Mixed-signal detection: units and revenue go opposite directions in L4 vs L13
    u_sign = _sign(units_ix.get(f"l4_vs_l13{suffix}", np.nan))
    r_sign = _sign(revenue_ix.get(f"l4_vs_l13{suffix}", np.nan))
    mixed_signal = (u_sign != 0 and r_sign != 0 and u_sign != r_sign)

    volatile = volatility_flag(weekly, "units")
    drivers  = short_driver_summary(weekly, baseline_weeks=13)

    totals = {
        "units_l4":      float(weekly["units"].iloc[-4:].sum())   if "units" in weekly else 0,
        "revenue_l4":    float(weekly["revenue"].iloc[-4:].sum()) if "revenue" in weekly else 0,
        "units_l52":     float(weekly["units"].sum())             if "units" in weekly else 0,
        "revenue_l52":   float(weekly["revenue"].sum())           if "revenue" in weekly else 0,
        "weeks_of_data": int(len(weekly)),
    }

    return {
        "asin": asin,
        "bucket": bucket_key,
        "bucket_label": bucket_label,
        "pattern": (s13, s26, s52),
        "mixed_signal": bool(mixed_signal),
        "volatile": bool(volatile),
        "composite": composite,
        "units_indices":   units_ix,
        "revenue_indices": revenue_ix,
        "drivers_l13":     drivers,
        "totals":          totals,
    }


def analyze_all(weekly_long: pd.DataFrame, baseline_mode: str = "exclusive") -> list[dict]:
    """Run analyze_asin for every ASIN in a long-format weekly DataFrame.

    Args:
        weekly_long: long-format DataFrame, one row per (asin, week_start).
        baseline_mode: see analyze_asin.

    Returns:
        List of per-ASIN result dicts.
    """
    out = []
    for asin, group in weekly_long.groupby("asin", sort=False):
        out.append(analyze_asin(group, asin, baseline_mode=baseline_mode))
    return out


# -----------------------------------------------------------------------------
# Aggregations across ASINs (top movers, bucket counts, $ at risk)
# -----------------------------------------------------------------------------

def bucket_summary(results: Iterable[dict]) -> pd.DataFrame:
    """Count ASINs and sum L4W revenue per bucket."""
    rows = []
    for r in results:
        rows.append({
            "bucket":       r.get("bucket"),
            "bucket_label": r.get("bucket_label"),
            "revenue_l4":   r.get("totals", {}).get("revenue_l4", 0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return (df.groupby(["bucket", "bucket_label"], as_index=False)
              .agg(asin_count=("bucket", "size"),
                   revenue_l4_total=("revenue_l4", "sum"))
              .sort_values("revenue_l4_total", ascending=False))


def top_movers(results: Iterable[dict], n: int = 20, by: str = "units") -> dict[str, list[dict]]:
    """Return top N ASINs going up and down by L4-vs-L13 (exclusive) on a given metric."""
    key = f"{by}_indices"
    rows = []
    for r in results:
        ix = r.get(key, {}).get("l4_vs_l13_exc", np.nan)
        if pd.isna(ix):
            continue
        rows.append({
            "asin": r["asin"],
            "bucket": r.get("bucket"),
            "index": float(ix),
            "revenue_l4": r.get("totals", {}).get("revenue_l4", 0),
        })
    df = pd.DataFrame(rows).sort_values("index", ascending=False)
    return {
        "up":   df.head(n).to_dict(orient="records"),
        "down": df.tail(n).iloc[::-1].to_dict(orient="records"),
    }


if __name__ == "__main__":
    print("trend_engine.py — import as a module, see analyze_all() for entry point")
