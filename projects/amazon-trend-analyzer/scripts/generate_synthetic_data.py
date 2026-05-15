"""
generate_synthetic_data.py — Build a realistic synthetic weekly DataFrame
with deliberate trend patterns, so we can test trend_engine + driver_decomp
end-to-end before plugging in live Quickbase data.

Output: CSV at scripts/synthetic_weekly.csv with columns
    asin, week_start, units, revenue, gv, cr, asp, oos_signal, bsr
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import date, timedelta
from pathlib import Path

rng = np.random.default_rng(42)

# Brand pool — fake but plausible mix for P+P
BRANDS = [
    ("Glad for Pets",      "Glad"),
    ("Arm & Hammer",       "Arm & Hammer"),
    ("Burt's Bees",        "Burt's Bees"),
    ("BioSilk",            "BioSilk"),
    ("CHI",                "CHI"),
    ("Vibrant Life",       "Vibrant Life"),
    ("Fresh Step",         "Fresh Step"),
    ("Kingsford",          "Kingsford"),
]

# Trend pattern recipes — each one shapes the 52-week weekly pattern
PATTERNS = [
    ("strong_winner",     6,  "all_up"),
    ("accelerating",      6,  "recent_surge"),
    ("sustained_decline", 6,  "all_down"),
    ("cooling_winner",    5,  "recent_weakness"),
    ("surge_on_decline",  5,  "blip_on_decline"),
    ("recovering",        4,  "valley_then_up"),
    ("mixed_signal",      5,  "units_up_rev_down"),
    ("volatile",          5,  "high_cv"),
    ("stable",            8,  "flat"),
]


def make_pattern(kind: str, base_units: float, n: int = 52) -> np.ndarray:
    """Return n weekly units values shaped by the requested pattern.

    NOTE: This is *synthetic* data designed to validate the trend engine —
    we deliberately keep seasonal noise small relative to trend signals so
    patterns are clearly distinguishable. Real data will be messier.
    """
    weeks = np.arange(n)
    seasonal = 0.08 * base_units * np.sin(2 * np.pi * weeks / 52)
    noise = rng.normal(0, 0.04 * base_units, n)

    if kind == "all_up":
        # Gentle ramp; recent weeks clearly above all baselines
        trend = np.linspace(-0.10, 0.40, n) * base_units
    elif kind == "recent_surge":
        trend = np.zeros(n)
        trend[-8:] = np.linspace(0.10, 0.45, 8) * base_units
    elif kind == "all_down":
        trend = np.linspace(0.30, -0.40, n) * base_units
    elif kind == "recent_weakness":
        # Healthy long-term trend, but a sharp turn down in the last ~5 weeks
        trend = np.linspace(-0.10, 0.30, n) * base_units
        trend[-5:] = np.linspace(0.25, -0.10, 5) * base_units
    elif kind == "blip_on_decline":
        trend = np.linspace(0.20, -0.30, n) * base_units
        trend[-3:] += 0.35 * base_units
    elif kind == "valley_then_up":
        trend = np.concatenate([
            np.linspace(0, -0.30, 30) * base_units,
            np.linspace(-0.30, 0.20, 22) * base_units,
        ])
    elif kind == "units_up_rev_down":
        # Units climb in recent weeks; ASP drop layered separately makes revenue dip
        trend = np.linspace(-0.05, 0.25, n) * base_units
    elif kind == "high_cv":
        trend = rng.normal(0, 0.30 * base_units, n)
    else:  # flat
        trend = np.zeros(n)

    series = base_units + seasonal + noise + trend
    return np.clip(series, 0, None).round()


def make_asp_pattern(kind: str, base_asp: float, n: int = 52) -> np.ndarray:
    """Pattern for Average Sales Price."""
    if kind == "units_up_rev_down":
        # ASP holds steady most of the period, then drops sharply in the last 6 weeks
        # → units L4 vs L13 = up, but revenue L4 vs L13 = down due to ASP plunge
        asp = np.full(n, base_asp) + rng.normal(0, 0.10, n)
        asp[-6:] = np.linspace(base_asp * 0.95, base_asp * 0.62, 6)
        return asp
    elif kind == "high_cv":
        return base_asp + rng.normal(0, 0.30, n)
    else:
        return base_asp + rng.normal(0, 0.15, n)


def make_oos_signal(kind: str, n: int = 52) -> np.ndarray:
    """Most ASINs have 0 OOS. Some patterns layer in OOS days near the end."""
    s = np.zeros(n, dtype=int)
    if kind in ("recent_weakness", "blip_on_decline"):
        if rng.random() < 0.5:
            s[-6:-2] = rng.integers(0, 3, 4)    # a few OOS days mid-recent
    if kind == "valley_then_up":
        s[15:25] = rng.integers(0, 4, 10)        # OOS spike during the valley
    return s


def make_gv_pattern(units: np.ndarray, base_cr: float, kind: str) -> np.ndarray:
    """GV is roughly units / cr — but conversion drifts a bit, which is interesting."""
    cr = make_cr_pattern(base_cr, len(units), kind)
    gv = units / np.clip(cr, 0.005, None)
    return gv.round()


def make_cr_pattern(base_cr: float, n: int, kind: str) -> np.ndarray:
    """CR drifts within a narrow band, plus a recent shift for some patterns."""
    cr = base_cr + rng.normal(0, 0.005, n)
    if kind == "recent_surge":
        cr[-4:] += 0.012        # conv lift
    elif kind == "recent_weakness":
        cr[-4:] -= 0.010
    return np.clip(cr, 0.005, 0.20)


def main():
    Path("scripts").mkdir(exist_ok=True)
    today = date.today()
    # Anchor weeks Monday-aligned, 52 weeks back
    last_monday = today - timedelta(days=today.weekday())
    weeks = [last_monday - timedelta(weeks=(51 - i)) for i in range(52)]

    rows = []
    catalog_rows = []
    asin_counter = 100000

    for pattern_key, count, shape in PATTERNS:
        for _ in range(count):
            asin = f"B0SYN{asin_counter:05d}"
            asin_counter += 1
            brand, master_brand = BRANDS[rng.integers(0, len(BRANDS))]
            base_units = float(rng.integers(80, 600))
            base_asp = float(rng.uniform(7.5, 28.0))
            base_cr = float(rng.uniform(0.04, 0.10))
            base_bsr = int(rng.integers(2000, 80000))

            units_arr = make_pattern(shape, base_units)
            asp_arr   = make_asp_pattern(shape, base_asp)
            cr_arr    = make_cr_pattern(base_cr, 52, shape)
            gv_arr    = make_gv_pattern(units_arr, base_cr, shape)
            oos_arr   = make_oos_signal(shape)
            bsr_drift = rng.normal(0, base_bsr * 0.02, 52).cumsum()
            if shape in ("all_up", "recent_surge"):
                bsr_drift -= np.linspace(0, base_bsr * 0.25, 52)
            elif shape in ("all_down", "recent_weakness"):
                bsr_drift += np.linspace(0, base_bsr * 0.25, 52)
            bsr_arr = np.clip(base_bsr + bsr_drift, 100, None).round().astype(int)

            for i, wk in enumerate(weeks):
                rows.append({
                    "asin":        asin,
                    "week_start":  wk.isoformat(),
                    "units":       float(units_arr[i]),
                    "revenue":     round(float(units_arr[i]) * float(asp_arr[i]), 2),
                    "gv":          float(gv_arr[i]),
                    "cr":          round(float(cr_arr[i]), 4),
                    "asp":         round(float(asp_arr[i]), 2),
                    "oos_signal":  int(oos_arr[i]),
                    "bsr":         int(bsr_arr[i]),
                })

            catalog_rows.append({
                "asin":         asin,
                "brand":        brand,
                "master_brand": master_brand,
                "description":  f"{brand} synthetic test product {asin_counter}",
                "pack_size":    int(rng.choice([1, 2, 4, 6, 12, 24])),
                "list_price":   round(base_asp * 1.15, 2),
                "expected_pattern": pattern_key,
            })

    weekly = pd.DataFrame(rows)
    catalog = pd.DataFrame(catalog_rows)
    weekly.to_csv("scripts/synthetic_weekly.csv", index=False)
    catalog.to_csv("scripts/synthetic_catalog.csv", index=False)
    print(f"Wrote {len(weekly):,} weekly rows for {len(catalog)} ASINs")
    print(f"  scripts/synthetic_weekly.csv")
    print(f"  scripts/synthetic_catalog.csv")


if __name__ == "__main__":
    main()
