#!/usr/bin/env python3
"""
rule_attribution.py  --  Per-rule effect report from a completed forecast run.

Reads forecast_results.json and computes for each rule code:
  - Fire count (how many records the rule fired on)
  - Median |delta| on records where it fired (vs records where it didn't)
  - Aggregate unit effect (sum of AI-manual gap on records where it fired)
  - Severity tier (CRITICAL / HIGH / MEDIUM / LOW based on impact)

Answers questions like:
  "Is F62 actually pulling weight, or could we remove it?"
  "Which rules are responsible for the biggest manual-vs-AI gaps?"
  "Are there rules that fire on many records but produce no measurable change?"

Usage:
    python scripts/rule_attribution.py
    python scripts/rule_attribution.py --results path/to/forecast_results.json
    python scripts/rule_attribution.py --top 30 --out rule_attribution.md
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("records", data if isinstance(data, list) else [])


def per_rule_stats(records: list[dict]) -> dict:
    """Compute per-rule metrics. Returns {rule_code: {...metrics...}}."""
    # Bucket records by which rules fired on each
    rule_to_records = defaultdict(list)
    for r in records:
        for code in r.get("rule_fires", []) or []:
            rule_to_records[code].append(r)

    stats = {}
    for code, recs in rule_to_records.items():
        n = len(recs)
        deltas = []
        unit_gap_sum = 0
        ai_sum = 0
        manual_sum = 0
        models = defaultdict(int)
        for r in recs:
            m = sum(r.get("manual", []))
            a = sum(r.get("fcst", []))
            ai_sum += a
            manual_sum += m
            unit_gap_sum += (a - m)
            if m > 0:
                deltas.append(min(abs(a - m) / m, 2.0))
            models[r.get("model", "?")] += 1

        deltas.sort()
        med = deltas[len(deltas)//2] if deltas else 0
        avg = sum(deltas) / len(deltas) if deltas else 0

        stats[code] = {
            "n":              n,
            "median_delta":   round(med, 4),
            "avg_delta":      round(avg, 4),
            "unit_gap":       int(unit_gap_sum),
            "abs_unit_gap":   int(abs(unit_gap_sum)),
            "ai_total":       int(ai_sum),
            "manual_total":   int(manual_sum),
            "models_touched": dict(models),
        }
    return stats


def tier(s: dict, total_records: int) -> str:
    """Classify a rule's impact tier."""
    fire_rate = s["n"] / max(total_records, 1)
    gap = s["abs_unit_gap"]
    if gap >= 100_000 and fire_rate >= 0.05:
        return "CRITICAL"
    if gap >= 25_000 or s["median_delta"] >= 0.30:
        return "HIGH"
    if gap >= 5_000 or s["median_delta"] >= 0.15:
        return "MEDIUM"
    return "LOW"


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results", default=str(SKILL_ROOT / "forecast_results.json"))
    p.add_argument("--top", type=int, default=40,
                   help="Show top N rules in the markdown report (default 40).")
    p.add_argument("--out", default=str(SKILL_ROOT / "rule_attribution.md"))
    p.add_argument("--csv", default=str(SKILL_ROOT / "rule_attribution.csv"))
    args = p.parse_args()

    path = Path(args.results)
    if not path.exists():
        sys.exit(f"ERROR: results file not found: {path}")

    records = load(path)
    print(f"Loaded {len(records)} records from {path}")

    stats = per_rule_stats(records)
    print(f"Found {len(stats)} distinct rules with fire sites.")

    # Sort by impact (abs_unit_gap desc, then n desc)
    ranked = sorted(stats.items(),
                    key=lambda x: (-x[1]["abs_unit_gap"], -x[1]["n"]))

    # Markdown report
    md = [f"# Rule Attribution Report", "",
          f"Source: `{path.name}`  ·  {len(records)} records  ·  {len(stats)} rules",
          "",
          "**Interpretation:**",
          "- `n` = records where this rule fired",
          "- `median |delta|` = median |AI - manual| / manual on those records",
          "- `unit gap` = sum of (AI - manual) on those records (signed)",
          "- Tier: CRITICAL >= 100K units AND fires on >=5% of records",
          "",
          "## Top rules by impact", "",
          "| Rank | Rule | Tier | n | Fire % | Median Δ | Unit gap | AI total | Top model |",
          "|------|------|------|---:|---:|---:|---:|---:|---|"]

    for i, (code, s) in enumerate(ranked[:args.top], 1):
        tier_str = tier(s, len(records))
        fire_pct = s["n"] / max(len(records), 1) * 100
        top_model = max(s["models_touched"].items(), key=lambda x: x[1])[0] \
                    if s["models_touched"] else "?"
        md.append(f"| {i} | `{code}` | {tier_str} | {s['n']} | "
                  f"{fire_pct:.1f}% | {s['median_delta']*100:.1f}% | "
                  f"{s['unit_gap']:+,} | {s['ai_total']:,} | {top_model} |")

    # Aggregate by phase prefix (HIS/CLS/BAS/...) -- mostly empty for now
    # since most rules are still legacy F#. Will be useful after B5 migration.
    md.extend(["", "## By rule family", "",
               "| Family | Rules | Total fires | Total |unit gap| |",
               "|---|---:|---:|---:|"])
    family_stats = defaultdict(lambda: {"rules": 0, "fires": 0, "gap": 0})
    for code, s in stats.items():
        fam = code.split("-")[0] if "-" in code else code.rstrip("abcdef")[:1] or "?"
        # collapse F58 -> F, R5 -> R, VP-Q4 -> VP, etc.
        if code.startswith("F_"):
            fam = "F_"
        elif code.startswith("VP-"):
            fam = "VP"
        else:
            fam = code[0]
        family_stats[fam]["rules"] += 1
        family_stats[fam]["fires"] += s["n"]
        family_stats[fam]["gap"] += s["abs_unit_gap"]
    for fam, fs in sorted(family_stats.items(), key=lambda x: -x[1]["gap"]):
        md.append(f"| {fam} | {fs['rules']} | {fs['fires']} | {fs['gap']:,} |")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\nWrote markdown report -> {args.out}")

    # CSV
    with open(args.csv, "w", encoding="utf-8", newline="") as f:
        f.write("rule_code,tier,n,fire_pct,median_delta,avg_delta,unit_gap,abs_unit_gap,ai_total,manual_total,top_model\n")
        for code, s in ranked:
            tier_str = tier(s, len(records))
            fire_pct = s["n"] / max(len(records), 1) * 100
            top_model = max(s["models_touched"].items(), key=lambda x: x[1])[0] \
                        if s["models_touched"] else "?"
            f.write(f"{code},{tier_str},{s['n']},{fire_pct:.2f},"
                    f"{s['median_delta']:.4f},{s['avg_delta']:.4f},"
                    f"{s['unit_gap']},{s['abs_unit_gap']},"
                    f"{s['ai_total']},{s['manual_total']},{top_model}\n")
    print(f"Wrote CSV -> {args.csv}")


if __name__ == "__main__":
    main()
