"""
build_demo_dashboard.py — End-to-end smoke test: synthetic data → trend engine
→ HTML dashboard. Drops the result at scripts/demo_dashboard.html.
"""
from __future__ import annotations

import sys
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import trend_engine as te
from lib import html_builder as hb


def main():
    weekly = pd.read_csv("scripts/synthetic_weekly.csv")
    catalog = pd.read_csv("scripts/synthetic_catalog.csv")

    print(f"Loaded {len(weekly):,} weekly rows × {len(catalog)} ASINs")
    results = te.analyze_all(weekly, baseline_mode="exclusive")
    print(f"Analyzed {len(results)} ASINs")

    out = hb.render(results, weekly, catalog,
                    out_path="scripts/demo_dashboard.html",
                    baseline_mode="exclusive")
    size_kb = out.stat().st_size / 1024
    print(f"\nWrote {out}  ({size_kb:.1f} KB)")
    print("\nBucket summary:")
    print(te.bucket_summary(results).to_string(index=False))


if __name__ == "__main__":
    main()
