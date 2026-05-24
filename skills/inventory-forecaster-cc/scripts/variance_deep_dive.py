#!/usr/bin/env python3
"""
variance_deep_dive.py  --  Deep analysis of records where AI and manual diverge.

Reads forecast_results.json and looks for actionable patterns by inspecting
records where |AI - manual| / manual > 5%.

For each such record, examines:
  - Manual week-shape (front-loaded, back-loaded, flat, bi-modal, killed)
  - AI week-shape (smooth, lumpy, biweekly, peak)
  - L26 order history (level, trend, cv)
  - POS signal (Amazon items)
  - Customer & brand
  - Item status / lifecycle stage
  - Which rules fired

Then surfaces patterns where planners systematically beat AI:
  - Customers/brands where planners go consistently UP or DOWN
  - Manual shapes that the AI doesn't produce (sudden W1 spike, mid-horizon pause)
  - Rules that correlate with disagreement
  - Cases where the manual shape implies forward-looking info AI lacks

Outputs:
  - variance_deep_dive.md (the prioritized findings)
  - variance_findings.csv (raw record-level data for spreadsheet drilldown)
  - variance_top100.json (richest records for further inspection)
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict, Counter
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent


def load_records(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("records", data if isinstance(data, list) else [])


def get_fcst(r): return r.get("forecast", r.get("fcst", [])) or []
def get_manual(r): return r.get("manual", []) or []


def shape_classify(weeks: list[float]) -> str:
    """Classify a 26-week shape into a coarse category."""
    if not weeks or sum(weeks) == 0:
        return "ZERO"
    nz = [v for v in weeks if v > 0]
    nz_rate = len(nz) / len(weeks)
    total = sum(weeks)
    front13 = sum(weeks[:13]) / total
    back13  = sum(weeks[13:]) / total
    cv = statistics.stdev(nz) / statistics.mean(nz) if len(nz) > 1 else 0

    if nz_rate < 0.30:
        return "SPARSE"
    if front13 > 0.65:
        return "FRONT_LOADED"
    if back13 > 0.65:
        return "BACK_LOADED"
    if cv < 0.2:
        return "FLAT"
    # Check for bi-modal (one big week + flat rest)
    sorted_w = sorted(nz, reverse=True)
    if len(sorted_w) >= 2 and sorted_w[0] > 3 * (statistics.mean(sorted_w[1:]) if len(sorted_w) > 1 else 0):
        return "SPIKE"
    return "VARIABLE"


def delta_pct(ai_total: float, man_total: float) -> float | None:
    if man_total <= 0:
        return None
    return (ai_total - man_total) / man_total * 100


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results", default=str(SKILL_ROOT / "forecast_results.json"))
    p.add_argument("--threshold", type=float, default=5.0,
                   help="Minimum |delta %%| to include (default 5).")
    p.add_argument("--min-manual", type=float, default=500,
                   help="Minimum manual total to filter noise from tiny records (default 500).")
    p.add_argument("--out-md",  default=str(SKILL_ROOT / "variance_deep_dive.md"))
    p.add_argument("--out-csv", default=str(SKILL_ROOT / "variance_findings.csv"))
    p.add_argument("--out-json", default=str(SKILL_ROOT / "variance_top100.json"))
    args = p.parse_args()

    records = load_records(Path(args.results))
    print(f"Loaded {len(records)} records.")

    # Filter to records where there's a meaningful variance
    flagged = []
    for r in records:
        f = get_fcst(r)
        m = get_manual(r)
        if not f or not m:
            continue
        ai_total = sum(f)
        man_total = sum(m)
        # Skip tiny records
        if man_total < args.min_manual:
            continue
        d = delta_pct(ai_total, man_total)
        if d is None or abs(d) < args.threshold:
            continue
        # Skip records the AI deliberately zeroed (Inactive routing)
        if ai_total == 0 and r.get("model", "").startswith("Inactive"):
            continue
        flagged.append({
            "key":          r.get("key"),
            "cust":         r.get("cust"),
            "mstyle":       r.get("mstyle"),
            "model":        r.get("model"),
            "ai_total":     int(ai_total),
            "man_total":    int(man_total),
            "delta_pct":    round(d, 1),
            "abs_delta":    int(ai_total - man_total),
            "ai_shape":     shape_classify(f),
            "man_shape":    shape_classify(m),
            "rule_fires":   r.get("rule_fires", []),
            "status_cust":  r.get("status_cust", ""),
            "item_status":  r.get("item_status", ""),
            "alert":        r.get("alert"),
            "history_l26":  r.get("history_l26_ord", []),
            "ai":           f,
            "manual":       m,
        })

    flagged.sort(key=lambda x: -abs(x["abs_delta"]))
    print(f"Flagged: {len(flagged)} records with |delta| > {args.threshold}% AND manual >= {args.min_manual}")

    # ── Pattern aggregations ──────────────────────────────────────────────
    by_direction = Counter("UP" if f["delta_pct"] > 0 else "DOWN" for f in flagged)
    by_customer  = defaultdict(lambda: {"n": 0, "up": 0, "down": 0, "abs_gap": 0})
    by_brand     = defaultdict(lambda: {"n": 0, "up": 0, "down": 0, "abs_gap": 0})
    by_model     = defaultdict(lambda: {"n": 0, "up": 0, "down": 0, "abs_gap": 0})
    by_man_shape = defaultdict(lambda: {"n": 0, "up": 0, "down": 0, "abs_gap": 0})
    by_ai_shape  = defaultdict(lambda: {"n": 0, "up": 0, "down": 0, "abs_gap": 0})
    by_shape_pair = defaultdict(lambda: {"n": 0, "abs_gap": 0})  # (man_shape, ai_shape)
    by_status    = defaultdict(lambda: {"n": 0, "abs_gap": 0})
    rule_when_diverging = Counter()

    for f in flagged:
        d = "UP" if f["delta_pct"] > 0 else "DOWN"
        for d_dict, key in [(by_customer, f["cust"]), (by_brand, ""),  # brand not in records
                              (by_model, f["model"]),
                              (by_man_shape, f["man_shape"]),
                              (by_ai_shape, f["ai_shape"])]:
            if not key: continue
            b = d_dict[key]
            b["n"] += 1
            b["up"] += (d == "UP")
            b["down"] += (d == "DOWN")
            b["abs_gap"] += abs(f["abs_delta"])
        by_shape_pair[(f["man_shape"], f["ai_shape"])]["n"] += 1
        by_shape_pair[(f["man_shape"], f["ai_shape"])]["abs_gap"] += abs(f["abs_delta"])
        by_status[f["status_cust"] or "(unknown)"]["n"] += 1
        by_status[f["status_cust"] or "(unknown)"]["abs_gap"] += abs(f["abs_delta"])
        for code in f["rule_fires"]:
            rule_when_diverging[code] += 1

    # Sort the biggest customers/models/shapes by abs_gap
    top_cust   = sorted(by_customer.items(), key=lambda x: -x[1]["abs_gap"])[:15]
    top_model  = sorted(by_model.items(), key=lambda x: -x[1]["abs_gap"])[:8]
    top_shape  = sorted(by_shape_pair.items(), key=lambda x: -x[1]["abs_gap"])[:15]
    top_status = sorted(by_status.items(), key=lambda x: -x[1]["abs_gap"])[:10]
    top_rules  = rule_when_diverging.most_common(25)

    # ── Markdown report ──────────────────────────────────────────────────
    md = [f"# Variance Deep-Dive: AI vs Manual (>{args.threshold}% delta)",
          "",
          f"**Source:** `{args.results}`",
          f"**Total records:** {len(records)}",
          f"**Records flagged:** {len(flagged)} "
            f"({len(flagged)/len(records)*100:.1f}% of total)",
          f"**Direction split:** UP={by_direction['UP']} DOWN={by_direction['DOWN']}",
          ""]

    # Aggregate impact
    total_abs_gap = sum(abs(f["abs_delta"]) for f in flagged)
    md.extend([
        f"**Total |unit gap|:** {total_abs_gap:,}",
        f"**Median |delta %|:** {statistics.median(abs(f['delta_pct']) for f in flagged):.1f}%",
        "",
        "---",
        "",
        "## 1. By customer (top 15 by absolute unit gap)",
        "",
        "| Customer | Records | UP | DOWN | |unit gap| | Bias |",
        "|---|---:|---:|---:|---:|---|"
    ])
    for cust, b in top_cust:
        bias = ""
        if b["up"] >= 3 * b["down"]: bias = "Manual UP-bias"
        elif b["down"] >= 3 * b["up"]: bias = "Manual DOWN-bias"
        md.append(f"| `{cust[:40]}` | {b['n']} | {b['up']} | {b['down']} "
                  f"| {b['abs_gap']:,} | {bias} |")

    md.extend(["",
        "## 2. By manual / AI shape combo (top 15 by |unit gap|)",
        "",
        "Reveals the *shape mismatches* where AI and planner disagree on WHEN demand falls.",
        "",
        "| Manual shape | -> | AI shape | Records | |unit gap| |",
        "|---|---|---|---:|---:|"
    ])
    for (ms, ais), b in top_shape:
        md.append(f"| `{ms}` | -> | `{ais}` | {b['n']} | {b['abs_gap']:,} |")

    md.extend(["",
        "## 3. By status_cust (lifecycle stage)",
        "",
        "| Status_Cust | Records | |unit gap| |",
        "|---|---:|---:|"])
    for st, b in top_status:
        md.append(f"| `{st[:40]}` | {b['n']} | {b['abs_gap']:,} |")

    md.extend(["",
        "## 4. By AI model class",
        "",
        "| Model | Records | UP | DOWN | |unit gap| |",
        "|---|---:|---:|---:|---:|"])
    for m, b in top_model:
        md.append(f"| `{m[:35]}` | {b['n']} | {b['up']} | {b['down']} | {b['abs_gap']:,} |")

    md.extend(["",
        "## 5. Rules that most often fire on disagreeing records",
        "",
        "| Rule | Times fired on disagreements |",
        "|---|---:|"])
    for code, n in top_rules:
        md.append(f"| `{code}` | {n} |")

    md.extend(["",
        "## 6. Top 50 records by absolute unit gap",
        "",
        "| Key | Customer | Model | Manual | AI | Δ | M shape | AI shape |",
        "|---|---|---|---:|---:|---:|---|---|"])
    for f in flagged[:50]:
        md.append(f"| `{f['key']}` | {f['cust'][:25]} | {f['model'][:15]} "
                  f"| {f['man_total']:,} | {f['ai_total']:,} | "
                  f"{f['delta_pct']:+.1f}% | {f['man_shape']} | {f['ai_shape']} |")

    with open(args.out_md, "w", encoding="utf-8") as f_md:
        f_md.write("\n".join(md))
    print(f"Wrote markdown -> {args.out_md}")

    # CSV
    with open(args.out_csv, "w", encoding="utf-8", newline="") as f_csv:
        f_csv.write("key,cust,model,ai_total,man_total,delta_pct,abs_gap,man_shape,ai_shape,status_cust,rule_fires\n")
        for f in flagged:
            rules = "|".join(f["rule_fires"])
            f_csv.write(f"{f['key']},\"{f['cust']}\",{f['model']},"
                        f"{f['ai_total']},{f['man_total']},{f['delta_pct']},"
                        f"{f['abs_delta']},{f['man_shape']},{f['ai_shape']},"
                        f"{f['status_cust']},{rules}\n")
    print(f"Wrote CSV -> {args.out_csv}")

    # JSON for top 100 (with full week arrays + history)
    with open(args.out_json, "w", encoding="utf-8") as f_json:
        json.dump(flagged[:100], f_json, indent=2)
    print(f"Wrote top-100 JSON -> {args.out_json}")


if __name__ == "__main__":
    main()
