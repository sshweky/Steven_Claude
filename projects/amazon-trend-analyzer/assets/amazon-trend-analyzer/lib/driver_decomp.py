"""
driver_decomp.py — Decompose an ASIN's trend into 5 drivers.

Given an ASIN's weekly time series, identify which factor(s) are
actually moving the trend, ranked by magnitude, with a one-line
narrative the dashboard can show in the drill-down panel.

The 5 drivers, in priority order when tied:
    1. Availability (OOS) — biggest signal because it caps everything else
    2. Traffic (Glance Views)
    3. Conversion Rate
    4. Price (Average Sales Price)
    5. Rank (Subcategory BSR)
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import List, Dict, Any

from . import trend_engine as te

# Magnitude thresholds for narrative — under these, the driver is treated as "flat"
MIN_PCT = 0.03      # 3% change is the floor we mention
OOS_MENTION_DAYS = 1  # even 1 OOS day is worth mentioning


def _pct(v: float) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    sign = "+" if v >= 0 else "−"
    return f"{sign}{abs(v) * 100:.1f}%"


def _direction_word(v: float, positive_is_good: bool = True) -> str:
    """e.g. ('rose', 'fell'). For BSR, positive_is_good=False (rank going up = bad)."""
    if v is None or pd.isna(v):
        return "flat"
    if positive_is_good:
        return "rose" if v > 0 else "fell"
    return "improved" if v < 0 else "worsened"


def decompose(weekly: pd.DataFrame, baseline_weeks: int = 13) -> Dict[str, Any]:
    """Return ranked driver list + narrative for one ASIN's weekly data.

    Args:
        weekly: weekly-aggregated DataFrame for ONE asin only, sorted ascending.
        baseline_weeks: which baseline to compute drivers against (default L13W).

    Returns:
        {
          'ranked': [{driver, label, value, direction, magnitude, contribution}, ...],
          'narrative': "Trend driven mainly by ...",
          'baseline_weeks': baseline_weeks,
        }
    """
    if weekly.empty:
        return {"ranked": [], "narrative": "No data.", "baseline_weeks": baseline_weeks}

    drivers: List[Dict[str, Any]] = []

    # 1. Availability — OOS days in the recent window
    oos_days = None
    if "oos_signal" in weekly.columns:
        last4 = weekly.dropna(subset=["oos_signal"]).iloc[-te.RECENT_W:]
        oos_days = int(last4["oos_signal"].sum()) if len(last4) else 0
    if oos_days is not None and oos_days >= OOS_MENTION_DAYS:
        drivers.append({
            "driver": "availability",
            "label": "Availability",
            "value": oos_days,
            "value_fmt": f"{oos_days} OOS days in L4W",
            "magnitude": min(1.0, oos_days / 14),   # cap at 1.0
            "direction": "negative",
        })

    # 2-4. Traffic, Conversion, Price — % change L4 vs baseline (exclusive)
    pct_drivers = [
        ("traffic",    "Glance views",      "gv",  True),
        ("conversion", "Conversion rate",   "cr",  True),
        ("price",      "Avg sales price",   "asp", True),
    ]
    for key, label, col, positive_is_good in pct_drivers:
        if col not in weekly.columns:
            continue
        l4 = te.compute_window_means(weekly[col], te.RECENT_W)
        base = te.compute_exclusive_baseline(
            weekly[col], te.RECENT_W, baseline_weeks - te.RECENT_W
        )
        change = te.safe_index(l4, base)
        if pd.isna(change):
            continue
        pct = change - 1.0
        if abs(pct) < MIN_PCT:
            continue
        drivers.append({
            "driver": key,
            "label": label,
            "value": pct,
            "value_fmt": _pct(pct),
            "magnitude": abs(pct),
            "direction": "positive" if (pct > 0) == positive_is_good else "negative",
        })

    # 5. Rank — absolute shift; improvement = decrease
    if "bsr" in weekly.columns:
        l4 = te.compute_window_means(weekly["bsr"], te.RECENT_W)
        base = te.compute_exclusive_baseline(
            weekly["bsr"], te.RECENT_W, baseline_weeks - te.RECENT_W
        )
        if not (pd.isna(l4) or pd.isna(base)):
            shift = l4 - base
            # normalize to a magnitude — 10% rank shift is significant
            rel_shift = abs(shift) / max(base, 1)
            if rel_shift >= 0.05:
                drivers.append({
                    "driver": "rank",
                    "label": "Subcategory rank",
                    "value": shift,
                    "value_fmt": f"{int(shift):+,} positions",
                    "magnitude": min(1.0, rel_shift),
                    "direction": "negative" if shift > 0 else "positive",
                })

    # Rank drivers by magnitude (OOS effectively floats to top because of its high cap)
    drivers.sort(key=lambda d: d["magnitude"], reverse=True)

    narrative = _build_narrative(drivers)
    return {"ranked": drivers, "narrative": narrative, "baseline_weeks": baseline_weeks}


def _build_narrative(drivers: List[Dict[str, Any]]) -> str:
    """Turn ranked drivers into one English sentence."""
    if not drivers:
        return "No driver changes large enough to flag — trend is steady."

    primary = drivers[0]
    if primary["driver"] == "availability":
        head = f"L4W trend constrained by {primary['value_fmt']}"
    elif primary["driver"] == "traffic":
        verb = "lifted" if primary["direction"] == "positive" else "pulled down"
        head = f"Trend {verb} primarily by glance views ({primary['value_fmt']})"
    elif primary["driver"] == "conversion":
        verb = "lifted" if primary["direction"] == "positive" else "pulled down"
        head = f"Conversion rate {primary['value_fmt']} {verb} the trend"
    elif primary["driver"] == "price":
        head = f"ASP shift of {primary['value_fmt']} is the primary driver"
    elif primary["driver"] == "rank":
        head = f"Sub-category rank moved {primary['value_fmt']}"
    else:
        head = f"{primary['label']} change of {primary['value_fmt']} is the main driver"

    # Append the second driver if it's at least 50% as strong as the first
    if len(drivers) > 1 and drivers[1]["magnitude"] >= 0.5 * primary["magnitude"]:
        second = drivers[1]
        head += f"; {second['label'].lower()} also moved ({second['value_fmt']})"

    return head + "."


if __name__ == "__main__":
    print("driver_decomp.py — import as a module, see decompose() for entry point")
