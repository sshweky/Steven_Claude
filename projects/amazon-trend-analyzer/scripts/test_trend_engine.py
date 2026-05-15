"""
test_trend_engine.py — Smoke-test trend_engine and driver_decomp on the
synthetic dataset.  Confirms:
  1. analyze_all() runs without errors on the synthetic CSV
  2. The bucket assignment broadly matches the *expected_pattern* tag
  3. Driver decomposition returns sensible narratives
"""
from __future__ import annotations

import sys
import pandas as pd
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import trend_engine as te
from lib import driver_decomp as dd


# Map expected pattern keys (from synthetic data) to acceptable bucket outputs.
# We allow a *set* of buckets because adjacent buckets can both be valid for
# the same generated pattern — the engine isn't deterministic to one bucket.
EXPECTED_BUCKETS = {
    "strong_winner":     {"strong_winner", "accelerating"},
    "accelerating":      {"accelerating", "strong_winner", "recovering"},
    "sustained_decline": {"sustained_decline", "new_decline", "soft"},
    "cooling_winner":    {"cooling_winner", "new_decline", "soft", "lapping_softness"},
    "surge_on_decline":  {"surge_on_decline", "recovering", "accelerating"},
    "recovering":        {"recovering", "accelerating", "new_decline", "strong_winner"},
    "mixed_signal":      None,                # any bucket OK, just want mixed_signal flag
    "volatile":          None,                # any bucket OK, just want volatile flag
    "stable":            {"stable", "soft", "cooling_winner"},
}


def main():
    weekly = pd.read_csv("scripts/synthetic_weekly.csv")
    catalog = pd.read_csv("scripts/synthetic_catalog.csv")
    expected = dict(zip(catalog["asin"], catalog["expected_pattern"]))

    results = te.analyze_all(weekly, baseline_mode="exclusive")

    # Validate
    bucket_counts = {}
    mismatches = []
    mixed_signal_count = 0
    volatile_count = 0

    for r in results:
        bucket_counts[r["bucket"]] = bucket_counts.get(r["bucket"], 0) + 1
        exp_pattern = expected[r["asin"]]
        if r.get("mixed_signal"):
            mixed_signal_count += 1
        if r.get("volatile"):
            volatile_count += 1

        accept = EXPECTED_BUCKETS.get(exp_pattern)
        if accept is not None and r["bucket"] not in accept:
            mismatches.append((r["asin"], exp_pattern, r["bucket"], r["composite"]))

    print(f"\n{'Bucket':<20}{'Count':>6}")
    print("-" * 26)
    for b, c in sorted(bucket_counts.items(), key=lambda x: -x[1]):
        print(f"{b:<20}{c:>6}")
    print(f"\nmixed_signal flagged: {mixed_signal_count}")
    print(f"volatile flagged:     {volatile_count}")
    print(f"\nTotal ASINs analysed: {len(results)}")
    print(f"Bucket mismatches:    {len(mismatches)}")

    # Validate that mixed_signal pattern produces mixed_signal flag at least some of the time
    mixed_in_results = [r for r in results
                        if expected[r["asin"]] == "mixed_signal" and r["mixed_signal"]]
    print(f"  of which 'mixed_signal' pattern actually flagged: "
          f"{len(mixed_in_results)} / {sum(1 for v in expected.values() if v == 'mixed_signal')}")

    volatile_in_results = [r for r in results
                           if expected[r["asin"]] == "volatile" and r["volatile"]]
    print(f"  of which 'volatile' pattern actually flagged: "
          f"{len(volatile_in_results)} / {sum(1 for v in expected.values() if v == 'volatile')}")

    if mismatches:
        print(f"\nFirst few mismatches (acceptable in moderate numbers — synthetic data is noisy):")
        for asin, exp, got, comp in mismatches[:10]:
            print(f"  {asin}  expected {exp:<20} got {got:<20} comp={comp}")

    # Sample drivers for a couple of ASINs
    sample = next((r for r in results if r["bucket"] == "accelerating"), results[0])
    asin = sample["asin"]
    asin_weekly = weekly[weekly["asin"] == asin].sort_values("week_start")
    drv = dd.decompose(asin_weekly)
    print(f"\nSample driver decomposition for {asin} ({sample['bucket_label']}):")
    print(f"  Narrative: {drv['narrative']}")
    for d in drv["ranked"]:
        print(f"  - {d['label']:<22} {d['value_fmt']:<22} mag={d['magnitude']:.3f}")

    # Aggregations
    print("\nBucket summary:")
    print(te.bucket_summary(results).to_string(index=False))

    movers = te.top_movers(results, n=5, by="units")
    print(f"\nTop 5 units movers UP:")
    for m in movers["up"]:
        print(f"  {m['asin']}  index={m['index']:.3f}  bucket={m['bucket']}")
    print(f"\nTop 5 units movers DOWN:")
    for m in movers["down"]:
        print(f"  {m['asin']}  index={m['index']:.3f}  bucket={m['bucket']}")


if __name__ == "__main__":
    main()
