#!/usr/bin/env python3
"""
tune_thresholds.py  --  Grid search over env-var-overridable thresholds
                        in scripts/config.py to find the parameter set
                        that minimizes aggregate |AI - manual| on a fixed scope.

Workflow:
  1. Pick a scope (default --acct 1864, ~100 records, runs in 2-5 min).
  2. Choose which thresholds to vary (default: F59I/J WOS gates + POS anchor).
  3. For each combo in the grid, set env vars and run --dry-run forecast.
  4. Compute aggregate metric (avg |delta|, or AI vs manual unit gap).
  5. Report ranked combos with the improvement vs baseline.

This converts "should we tune F59i WOS gate from 6.0 to 7.0?" from a multi-day
debate into a 30-minute experiment with numbers.

CAUTION:
  - Each combo = one full forecaster run (2-5 min for --acct 1864).
  - 3 thresholds x 4 values each = 64 combos = 2-5 hours.
  - Default grid is INTENTIONALLY small (3 thresholds x 2 values = 8 combos).
  - Bigger grids: pass --combos or edit the CONFIG section below.

Usage:
    python scripts/tune_thresholds.py                          # default 8-combo run
    python scripts/tune_thresholds.py --scope acct=1864
    python scripts/tune_thresholds.py --metric ai_vs_manual    # minimize unit gap
    python scripts/tune_thresholds.py --quick                  # tiny 2-combo sanity run
"""

import argparse
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent


# ─────────────────────────────────────────────────────────────────────────────
# Grids -- which thresholds to vary + what values to try.
# Each entry: env-var name -> [list of values to test]
# Keep small unless you have time for a big run.
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_GRID = {
    "F59I_WOS_HEALTHY_GATE":  [5.0, 6.0, 7.0],       # baseline 6.0
    "F59J_WOS_RESTOCK_GATE":  [7.0, 8.0, 9.0],       # baseline 8.0
    "F59I_POS_ANCHOR_STRONG": [1.30, 1.40, 1.50],    # baseline 1.40
}

QUICK_GRID = {
    "F59I_WOS_HEALTHY_GATE":  [6.0, 7.0],
    "F59I_POS_ANCHOR_STRONG": [1.30, 1.40],
}


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────

def compute_metric(results_path: Path, metric: str) -> float:
    """Read results JSON, compute the requested metric (lower = better)."""
    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", data if isinstance(data, list) else [])
    if not records:
        return float("inf")

    if metric == "avg_abs_delta":
        # mean of clamped per-record |AI - manual| / manual
        ds = []
        for r in records:
            m = sum(r.get("manual", []))
            a = sum(r.get("fcst", []))
            if m > 0:
                ds.append(min(abs(a - m) / m, 2.0))
            elif a > 0:
                ds.append(2.0)
        return sum(ds) / len(ds) if ds else float("inf")

    elif metric == "ai_vs_manual":
        # Absolute aggregate unit gap (lower = closer to planner consensus)
        ai = sum(sum(r.get("fcst", [])) for r in records)
        man = sum(sum(r.get("manual", [])) for r in records)
        return abs(ai - man)

    elif metric == "median_abs_delta":
        ds = []
        for r in records:
            m = sum(r.get("manual", []))
            a = sum(r.get("fcst", []))
            if m > 0:
                ds.append(min(abs(a - m) / m, 2.0))
        ds.sort()
        return ds[len(ds)//2] if ds else float("inf")

    else:
        raise ValueError(f"Unknown metric: {metric}")


# ─────────────────────────────────────────────────────────────────────────────
# Grid search runner
# ─────────────────────────────────────────────────────────────────────────────

def run_one_combo(scope_flags: list[str], env_overrides: dict, out_suffix: str) -> Path:
    """Run forecaster --dry-run with the given env overrides. Return results path."""
    env = os.environ.copy()
    for k, v in env_overrides.items():
        env[k] = str(v)
    out_name = f"forecast_results_tune_{out_suffix}.json"
    cmd = [sys.executable, str(HERE / "run_forecast.py"),
           *scope_flags, "--dry-run", "--out", out_name, "--no-outer-retry"]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        print(f"  [combo {out_suffix}] FAILED -- stdout tail:")
        print(proc.stdout[-1500:])
        return None
    return SKILL_ROOT / out_name


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scope", default="acct=1864",
                   help="Scope filter (default: acct=1864).")
    p.add_argument("--metric", default="avg_abs_delta",
                   choices=["avg_abs_delta", "median_abs_delta", "ai_vs_manual"],
                   help="Objective metric to minimize (default: avg_abs_delta).")
    p.add_argument("--quick", action="store_true",
                   help="Use the tiny 2-combo QUICK_GRID for a sanity check.")
    p.add_argument("--out", default=str(SKILL_ROOT / "tune_thresholds_report.md"),
                   help="Markdown report path.")
    args = p.parse_args()

    grid = QUICK_GRID if args.quick else DEFAULT_GRID
    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]
    combos = list(itertools.product(*value_lists))
    print(f"Tuning {len(keys)} thresholds: {keys}")
    print(f"  {len(combos)} combos to test (metric: {args.metric}, scope: {args.scope})")

    # Build scope flags
    scope_flags = []
    for piece in args.scope.split(","):
        k, _, v = piece.partition("=")
        scope_flags.append(f"--{k.strip()}")
        scope_flags.append(v.strip())

    results = []
    for i, combo in enumerate(combos, 1):
        env_overrides = dict(zip(keys, combo))
        suffix = f"c{i:03d}"
        print(f"\n[{i}/{len(combos)}] {env_overrides}")
        t0 = time.time()
        path = run_one_combo(scope_flags, env_overrides, suffix)
        elapsed = time.time() - t0
        if not path:
            print(f"  -> FAIL ({elapsed:.0f}s)")
            results.append({"combo": env_overrides, "metric": None, "elapsed": elapsed})
            continue
        score = compute_metric(path, args.metric)
        print(f"  -> {args.metric}={score:.4f}  ({elapsed:.0f}s)")
        results.append({"combo": env_overrides, "metric": score, "elapsed": elapsed,
                        "results_path": str(path)})

    # Rank
    valid = [r for r in results if r["metric"] is not None]
    valid.sort(key=lambda r: r["metric"])

    if not valid:
        sys.exit("ERROR: no successful runs")

    best = valid[0]
    worst = valid[-1]
    pct_improvement = (worst["metric"] - best["metric"]) / worst["metric"] * 100 \
                      if worst["metric"] > 0 else 0.0

    # Markdown report
    md = ["# Threshold Tuning Report", "",
          f"**Scope:** {args.scope}",
          f"**Metric:** {args.metric} (lower = better)",
          f"**Combos tested:** {len(combos)} ({len(valid)} succeeded)",
          f"**Best vs worst spread:** {pct_improvement:.1f}%",
          "",
          "## Best combo", ""]
    md.append("```")
    for k, v in best["combo"].items():
        md.append(f"  {k}={v}")
    md.append(f"  -> {args.metric} = {best['metric']:.4f}")
    md.append("```")
    md.extend(["", "## All combos (ranked)", "",
               "| Rank | " + " | ".join(keys) + f" | {args.metric} | seconds |",
               "|---|" + "|".join(["---"] * len(keys)) + "|---|---|"])
    for i, r in enumerate(valid, 1):
        cells = [str(r["combo"][k]) for k in keys]
        md.append(f"| {i} | " + " | ".join(cells) +
                  f" | {r['metric']:.4f} | {r['elapsed']:.0f} |")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\nWrote report -> {args.out}")
    print(f"Best config saved at top of report. To adopt, update scripts/config.py defaults.")


if __name__ == "__main__":
    main()
