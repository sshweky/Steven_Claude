#!/usr/bin/env python3
"""
backtest_ci.py  --  CI runner: forecast a fixed scope, compare to baseline,
                    fail if aggregate metrics drift >X%.

This is the safety net before merging rule changes. Runs --acct 1864
(steady reference scope), computes aggregate metrics (total units, MAPE
vs manual, alert rate, model split), compares to a committed baseline JSON.
Exit code 1 if drift exceeds tolerance.

Usage:
    python scripts/backtest_ci.py                    # full run, compare to baseline
    python scripts/backtest_ci.py --update-baseline  # update baseline (after intentional change)
    python scripts/backtest_ci.py --tolerance 0.05   # allow 5% aggregate drift (default 2%)
    python scripts/backtest_ci.py --scope acct=1864  # custom scope

Default baseline path: scripts/baselines/backtest_ci_acct1864.json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime


HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
BASELINE_DIR = HERE / "baselines"
BASELINE_DIR.mkdir(parents=True, exist_ok=True)


def aggregate_metrics(results_json_path: Path) -> dict:
    """Read a forecast_results.json and return a dict of aggregate metrics."""
    with open(results_json_path, encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", data if isinstance(data, list) else [])

    n           = len(records)
    n_alert     = sum(1 for r in records if r.get("alert"))
    ai_total    = sum(sum(r.get("forecast", r.get("fcst", []))) for r in records)
    manual_total = sum(sum(r.get("manual", [])) for r in records)

    # Per-record absolute % delta (clamped at 200% to avoid div-by-zero blowups)
    deltas = []
    for r in records:
        m = sum(r.get("manual", []))
        a = sum(r.get("forecast", r.get("fcst", [])))
        if m > 0:
            d = abs(a - m) / m
            deltas.append(min(d, 2.0))
        elif a > 0:
            deltas.append(2.0)
    median_delta = sorted(deltas)[len(deltas)//2] if deltas else 0.0
    avg_delta    = sum(deltas) / len(deltas) if deltas else 0.0

    # Model split
    model_split = {}
    for r in records:
        m = r.get("model", "unknown")
        model_split[m] = model_split.get(m, 0) + 1

    # Top fired rules
    rule_counts = {}
    for r in records:
        for code in r.get("rule_fires", []):
            rule_counts[code] = rule_counts.get(code, 0) + 1
    top_rules = dict(sorted(rule_counts.items(),
                             key=lambda x: -x[1])[:25])

    return {
        "n_records":        n,
        "n_alerts":         n_alert,
        "alert_rate":       n_alert / n if n > 0 else 0.0,
        "ai_total":         int(ai_total),
        "manual_total":     int(manual_total),
        "ai_vs_manual_pct": (ai_total - manual_total) / manual_total if manual_total > 0 else 0.0,
        "avg_abs_delta":    round(avg_delta, 4),
        "median_abs_delta": round(median_delta, 4),
        "model_split":      model_split,
        "top_25_rules":     top_rules,
    }


def compare(current: dict, baseline: dict, tol: float) -> tuple[bool, list[str]]:
    """Return (passed, list_of_drift_messages)."""
    msgs = []

    def chk(key: str, current_v, baseline_v, name: str):
        if baseline_v == 0:
            if abs(current_v) > tol:
                msgs.append(f"  {name}: was {baseline_v}, now {current_v} (drift)")
        else:
            rel = abs(current_v - baseline_v) / abs(baseline_v)
            if rel > tol:
                pct = rel * 100
                msgs.append(f"  {name}: was {baseline_v}, now {current_v} "
                            f"(drift {pct:.1f}% > tolerance {tol*100:.0f}%)")

    chk("n_records",     current["n_records"],     baseline["n_records"],     "Record count")
    chk("n_alerts",      current["n_alerts"],      baseline["n_alerts"],      "Alert count")
    chk("ai_total",      current["ai_total"],      baseline["ai_total"],      "AI total units")
    chk("avg_abs_delta", current["avg_abs_delta"], baseline["avg_abs_delta"], "Avg |delta|")

    # Model split drift -- any model whose count changed by > 10% of total
    cur_split = current.get("model_split", {})
    base_split = baseline.get("model_split", {})
    total_n = max(current["n_records"], baseline["n_records"], 1)
    for model in set(list(cur_split.keys()) + list(base_split.keys())):
        cur = cur_split.get(model, 0)
        base = base_split.get(model, 0)
        if abs(cur - base) / total_n > 0.10:
            msgs.append(f"  Model split [{model}]: was {base}, now {cur}")

    return len(msgs) == 0, msgs


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--update-baseline", action="store_true",
                   help="Overwrite the baseline JSON with current run's metrics.")
    p.add_argument("--tolerance", type=float, default=0.02,
                   help="Drift tolerance as decimal (default 0.02 = 2%%).")
    p.add_argument("--scope", default="acct=1864",
                   help="Scope filter passed to the forecaster (default: acct=1864).")
    p.add_argument("--baseline", default=None,
                   help="Custom baseline JSON path.")
    p.add_argument("--results", default=None,
                   help="Use an existing forecast_results.json instead of running the forecaster.")
    p.add_argument("--quick", action="store_true",
                   help="Skip the forecaster run and only use existing forecast_results.json.")
    args = p.parse_args()

    # Determine baseline path
    scope_safe = args.scope.replace("=", "").replace(",", "_").replace(" ", "_")
    baseline_path = Path(args.baseline) if args.baseline else \
                    BASELINE_DIR / f"backtest_ci_{scope_safe}.json"

    # Either use a pre-existing results file or run the forecaster
    if args.results or args.quick:
        results_path = Path(args.results) if args.results else SKILL_ROOT / "forecast_results.json"
        if not results_path.exists():
            sys.exit(f"ERROR: no results at {results_path}")
        print(f"Using existing results: {results_path}")
    else:
        # Run the forecaster with --dry-run (no QB writeback)
        scope_flag = []
        for piece in args.scope.split(","):
            k, _, v = piece.partition("=")
            scope_flag.append(f"--{k.strip()}")
            scope_flag.append(v.strip())
        out_path = SKILL_ROOT / f"forecast_results_ci_{scope_safe}.json"
        cmd = [sys.executable, str(HERE / "run_forecast.py"),
               *scope_flag, "--dry-run", "--out", out_path.name,
               "--no-outer-retry"]
        print(f"Running CI forecast: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            print(result.stdout[-2000:])
            print(result.stderr[-2000:], file=sys.stderr)
            sys.exit(f"ERROR: CI forecast run failed (exit {result.returncode})")
        results_path = SKILL_ROOT / out_path.name

    print(f"\nComputing aggregate metrics ...")
    current = aggregate_metrics(results_path)
    current["_ci_run_at"] = datetime.now().isoformat(timespec="seconds")
    current["_scope"]     = args.scope

    print(f"  n_records:        {current['n_records']}")
    print(f"  n_alerts:         {current['n_alerts']}  "
          f"({current['alert_rate']*100:.1f}%)")
    print(f"  AI total:         {current['ai_total']:,}")
    print(f"  Manual total:     {current['manual_total']:,}")
    print(f"  AI vs Manual:     {current['ai_vs_manual_pct']*100:+.1f}%")
    print(f"  Avg |delta|:      {current['avg_abs_delta']*100:.1f}%")
    print(f"  Median |delta|:   {current['median_abs_delta']*100:.1f}%")
    print(f"  Model split:      {current['model_split']}")

    if args.update_baseline:
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        print(f"\n[BASELINE UPDATED] -> {baseline_path}")
        sys.exit(0)

    if not baseline_path.exists():
        print(f"\nNo baseline at {baseline_path}.")
        print(f"To create one, run: python scripts/backtest_ci.py --update-baseline --scope {args.scope}")
        sys.exit(2)

    with open(baseline_path, encoding="utf-8") as f:
        baseline = json.load(f)

    print(f"\nComparing to baseline ({baseline.get('_ci_run_at', 'unknown date')}):")
    passed, drifts = compare(current, baseline, args.tolerance)

    if passed:
        print(f"  [OK] all metrics within {args.tolerance*100:.0f}% tolerance.")
        sys.exit(0)
    else:
        print(f"  [DRIFT] {len(drifts)} metric(s) exceeded tolerance:")
        for m in drifts:
            print(m)
        print(f"\nIf this drift is INTENTIONAL (e.g. you tuned a rule), run:")
        print(f"  python scripts/backtest_ci.py --update-baseline --scope {args.scope}")
        sys.exit(1)


if __name__ == "__main__":
    main()
