#!/usr/bin/env python3
"""
Gap Analysis — Manual Projections vs AI Forecasts

Inspects validation_results.json (or forecast_results.json) and identifies
systematic patterns where AI projections diverge from manual projections.
Categorizes root causes and produces a structured markdown report that drives
future model improvements.

Run AFTER a --validate or forecast run:

    python scripts/gap_analysis.py --results validation_results.json
    python scripts/gap_analysis.py --results validation_results.json --top 200
    python scripts/gap_analysis.py --results validation_results.json --out gap_analysis_report.md

The report identifies records in these buckets:
  1. Inactive-with-activity       — classified inactive but L26/L52 shows orders
  2. Seasonal-ramp under-forecast  — category items where L52 peak >> L13 trough
  3. Over-forecast (declining)     — L4W trending down but AI projects L13 avg
  4. Sparse-intermittent low       — baseline too conservative vs L26/L52 non-zero avg
  5. Prime Day pre-buy gap         — Amazon items that manual anticipates bigger
  6. Category-uncategorized        — description hints at seasonality not matched

For each bucket, the report suggests concrete model changes to the forecaster.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

# Configure stdout to utf-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# Keywords used by the forecaster's CATEGORY_PROFILES — any description containing
# one of these should already be getting category seasonality applied.
CATEGORY_KEYWORDS = [
    "charcoal", "chimney", "fire starter", "firestarter", "lighter fluid",
    "grill brush", "mosquito", "insect repel", "bug repel",
    "sunscreen", "sun care", "sunblock",
    "holiday", "christmas", "ice melt", "de-icer",
]

# Additional keywords that SHOULD trigger category seasonality but currently do NOT
# (discovered from gap analysis — these are candidates to add to CATEGORY_PROFILES)
UNMATCHED_SEASONAL_KEYWORDS = {
    "kingsford":           "outdoor_grill",   # All Kingsford = outdoor grill season
    "fabuloso":            "cleaning",        # Household cleaning (steady)
    "fraganzia":           "cleaning",        # Household cleaning (steady)
    "deodorizing ball":    "spring_cleaning", # Spring-summer home-fragrance lift
    "air freshener":       "spring_cleaning",
    "scent booster":       "spring_cleaning",
    "snack bowl":          "party_summer",    # Outdoor party season
    "paper bowl":          "party_summer",
    "paper plate":         "party_summer",
    "grill cleaner":       "outdoor_grill",
    "wooden fire":         "outdoor_grill",
}


def load_results(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def classify_gap(rec):
    """Return (bucket_name, diagnosis_string) for why AI diverges from manual."""
    m = rec.get("projection_total", 0)
    a = rec.get("ai_total", 0)
    if m <= 0:
        return None, None
    gap_pct = (a - m) / m * 100
    hist_ord = rec.get("history_l26_ord", [])
    hist_full = rec.get("history_l52_ord", hist_ord)  # fall back to L26
    l13 = hist_ord[:13]
    l26_tail = hist_ord[13:26] if len(hist_ord) >= 26 else []
    l13_nz = [v for v in l13 if v > 0]
    l26_tail_nz = [v for v in l26_tail if v > 0]
    l26_nz = l13_nz + l26_tail_nz
    model = rec.get("ai_model", "")
    pattern = rec.get("pattern", "")
    desc = rec.get("desc", "").lower()

    l13_all_avg = sum(l13) / 13 if l13 else 0
    l13_nz_avg = sum(l13_nz) / len(l13_nz) if l13_nz else 0
    l26_nz_avg = sum(l26_nz) / len(l26_nz) if l26_nz else 0
    l4_avg = sum(l13[:4]) / 4 if len(l13) >= 4 else 0
    peak = max(hist_full) if hist_full else 0
    peak_ratio = peak / l13_nz_avg if l13_nz_avg > 0 else 0

    # 1. Inactive-with-activity
    if model == "Inactive" and (len(l26_nz) >= 4 or len(l13_nz) >= 3) and m > 5000:
        return "inactive_with_activity", (
            f"Classified inactive but {len(l26_nz)} non-zero L26W weeks "
            f"(avg {l26_nz_avg:.0f}). Manual projects {m:,}."
        )

    # 2. Seasonal-ramp under-forecast
    if gap_pct < -20 and peak_ratio > 3.0 and m > 10000:
        # peak is >3x current baseline — strong seasonal item
        kw_match = next((k for k, v in UNMATCHED_SEASONAL_KEYWORDS.items() if k in desc), None)
        cat_match = next((k for k in CATEGORY_KEYWORDS if k in desc), None)
        if kw_match and not cat_match:
            return "seasonal_unmatched", (
                f"L52 peak {peak:,} / L13 avg {l13_nz_avg:.0f} = {peak_ratio:.1f}x. "
                f"Description keyword '{kw_match}' → '{UNMATCHED_SEASONAL_KEYWORDS[kw_match]}' "
                f"not in CATEGORY_PROFILES."
            )
        return "seasonal_ramp_underforecast", (
            f"Peak {peak:,} is {peak_ratio:.1f}x L13 avg {l13_nz_avg:.0f}. "
            f"Forecast anchored to trough, not peak."
        )

    # 3. Over-forecast on declining items
    if gap_pct > 15 and l13_nz_avg > 0 and l4_avg < l13_nz_avg * 0.7:
        return "declining_overforecast", (
            f"L4W avg {l4_avg:.0f} is {l4_avg/l13_nz_avg*100:.0f}% of L13 avg {l13_nz_avg:.0f}. "
            f"Item declining but AI held baseline."
        )

    # 4. Over-forecast from isolated spike
    if gap_pct > 15 and l13_nz:
        max_nz = max(l13_nz)
        sorted_nz = sorted(l13_nz)
        median_nz = sorted_nz[len(sorted_nz) // 2]
        if median_nz > 0 and max_nz > median_nz * 3:
            return "spike_overforecast", (
                f"L13 non-zero max {max_nz:,} is {max_nz/median_nz:.1f}x median {median_nz:.0f}. "
                f"Outlier cap didn't fully neutralize."
            )

    # 5. Sparse/intermittent with high manual
    if pattern in ("sparse_intermittent", "intermittent") and gap_pct < -30 and m > 10000:
        return "sparse_baseline_too_low", (
            f"Pattern '{pattern}' but manual projects {m:,}. "
            f"L13nz_avg {l13_nz_avg:.0f} vs L26nz_avg {l26_nz_avg:.0f}. "
            f"Consider MAX(L13, L26, L52) baseline."
        )

    # 6. Amazon Prime Day pre-buy gap (Amazon only, gap on W5-W9 ramp)
    cust = rec.get("cust", "").upper()
    if "AMAZON" in cust and gap_pct < -15 and m > 10000:
        # Check if manual front-loads W5-W9 heavily
        weeks = rec.get("weeks", [])
        ai_fc = rec.get("ai_forecast", [])
        if len(weeks) >= 9 and len(ai_fc) >= 9:
            m_w5_9 = sum(w.get("projection", 0) for w in weeks[4:9])
            a_w5_9 = sum(ai_fc[4:9])
            m_avg_w5_9 = m_w5_9 / 5
            m_avg_all = m / 26
            if m_avg_w5_9 > m_avg_all * 1.15 and m_w5_9 > a_w5_9 * 1.3:
                return "prime_day_prebuy_gap", (
                    f"Manual W5-W9 avg {m_avg_w5_9:.0f} vs overall {m_avg_all:.0f} "
                    f"(+{(m_avg_w5_9/m_avg_all-1)*100:.0f}%). AI W5-W9 = {sum(ai_fc[4:9]):,}."
                )

    # 7. Generic under-forecast (catch-all)
    if gap_pct < -20:
        return "under_forecast_other", (
            f"Gap {gap_pct:+.1f}%. Baseline={rec.get('baseline_src', '?')}. "
            f"Pattern={pattern}."
        )

    if gap_pct > 20:
        return "over_forecast_other", (
            f"Gap {gap_pct:+.1f}%. Baseline={rec.get('baseline_src', '?')}. "
            f"Pattern={pattern}."
        )

    return "aligned", f"Gap {gap_pct:+.1f}% — within tolerance."


# Human-readable bucket names and suggested fixes
BUCKET_META = {
    "inactive_with_activity": {
        "title": "Inactive-with-Activity (misclassification)",
        "fix": (
            "In `classify()`: do NOT return 'inactive' if L26W non-zero weeks >= 4 "
            "OR L52W non-zero weeks >= 8. For those cases, route to Heuristic with "
            "baseline = MAX(L26W non-zero avg, L52W non-zero avg). Items with true "
            "zero-activity across L52 remain inactive (genuinely dead SKUs)."
        ),
    },
    "seasonal_unmatched": {
        "title": "Seasonal category not in CATEGORY_PROFILES",
        "fix": (
            "Add missing category profiles to CATEGORY_PROFILES in inventory_forecaster.py. "
            "Also match on Brand (e.g., 'Kingsford' → outdoor_grill) and Product_Category / "
            "Product_Subcategory fields, not description alone. Queue the listed keywords."
        ),
    },
    "seasonal_ramp_underforecast": {
        "title": "Seasonal-ramp under-forecast (peak >> trough)",
        "fix": (
            "Add peak-anchored baseline: when category profile matches AND L52 peak > 3x "
            "L13 non-zero avg, compute `peak_baseline = avg(L52 weeks falling in category "
            "peak months)` and anchor the seasonal curve to that. Then each week = "
            "peak_baseline * (category_multiplier[w] / max(category_multiplier))."
        ),
    },
    "declining_overforecast": {
        "title": "Declining item over-forecast",
        "fix": (
            "Detect end-of-life / declining items: if L4W avg < L13W non-zero avg × 0.7, "
            "blend the 26-week forecast toward L4W avg using weight = 0.5 on L4W and 0.5 "
            "on the model forecast. Further down-weight weeks W14-W26."
        ),
    },
    "spike_overforecast": {
        "title": "Isolated spike over-forecast (outlier cap)",
        "fix": (
            "Tighten Fix 3 outlier cap: lower the threshold from 3.0x median to 2.5x "
            "median, OR add a secondary check — if max(L13_nz) > 2x L13_all_avg AND "
            "max occurs only once, cap at 2x L13_all_avg."
        ),
    },
    "sparse_baseline_too_low": {
        "title": "Sparse/intermittent baseline too conservative",
        "fix": (
            "For sparse_intermittent / intermittent with high manual projection, set "
            "baseline = MAX(L13W non-zero avg, L26W non-zero avg, L52W non-zero avg) "
            "instead of the current L13W-first-fallback chain."
        ),
    },
    "prime_day_prebuy_gap": {
        "title": "Amazon Prime Day pre-buy gap (W5-W9 under-forecast)",
        "fix": (
            "Extend PRIME_DAY_WEEKS lift schedule to a tapered ramp: "
            "W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). "
            "Currently W7-W9 only at flat 1.25."
        ),
    },
    "under_forecast_other": {
        "title": "Other under-forecast",
        "fix": "Manual inspection needed — review description and history pattern.",
    },
    "over_forecast_other": {
        "title": "Other over-forecast",
        "fix": "Manual inspection needed — review description and history pattern.",
    },
    "aligned": {
        "title": "Aligned with manual (within ±10%)",
        "fix": "No fix needed.",
    },
}


def run_analysis(results_path, top_n, out_path):
    data = load_results(results_path)
    recs = data.get("records", [])
    if not recs:
        print(f"ERROR: No records found in {results_path}")
        sys.exit(1)

    recs_sorted = sorted(recs, key=lambda r: r.get("projection_total", 0), reverse=True)
    top = recs_sorted[:top_n]

    # Overall stats
    tot_m = sum(r.get("projection_total", 0) for r in top)
    tot_a = sum(r.get("ai_total", 0) for r in top)
    tot_e = sum(r.get("expected_total", 0) for r in top)

    # Bucket records
    by_bucket = defaultdict(list)
    for r in top:
        b, diag = classify_gap(r)
        if b is None:
            continue
        by_bucket[b].append((r, diag))

    # Order buckets by total unit gap (largest first), aligned at the bottom
    bucket_gap = {}
    for b, items in by_bucket.items():
        gap_units = sum(abs(r.get("projection_total", 0) - r.get("ai_total", 0))
                        for r, _ in items)
        bucket_gap[b] = gap_units
    ordered_buckets = sorted(by_bucket.keys(),
                             key=lambda b: (-bucket_gap[b] if b != "aligned" else 0))

    # Description keyword frequency for unmatched-seasonal bucket
    unmatched_kw_counter = Counter()
    for r, _ in by_bucket.get("seasonal_unmatched", []):
        desc = r.get("desc", "").lower()
        for k in UNMATCHED_SEASONAL_KEYWORDS:
            if k in desc:
                unmatched_kw_counter[k] += 1

    # Write markdown report
    lines = []
    lines.append(f"# Manual vs AI Projection Gap Analysis")
    lines.append("")
    lines.append(f"- **Source**: `{os.path.basename(results_path)}`  ")
    lines.append(f"- **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    lines.append(f"- **Scope**: top **{len(top)}** records by manual projection volume  ")
    lines.append("")

    lines.append("## Overall")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Total manual projection | {tot_m:,} |")
    _ai_pct  = f"{(tot_a-tot_m)/tot_m*100:+.1f}%" if tot_m else "n/a"
    _exp_pct = f"{(tot_e-tot_m)/tot_m*100:+.1f}%" if tot_m else "n/a"
    lines.append(f"| Total AI projection | {tot_a:,} ({_ai_pct}) |")
    lines.append(f"| Total expected (baseline × profile) | {int(tot_e):,} ({_exp_pct}) |")
    lines.append(f"| Absolute unit gap | {abs(tot_a-tot_m):,} |")
    lines.append("")

    # Gap distribution
    dist = {"AI << M (<-30%)": 0, "AI < M (-30..-10)": 0, "close (±10%)": 0,
            "AI > M (+10..+30)": 0, "AI >> M (>+30)": 0}
    for r in top:
        m = r.get("projection_total", 0)
        a = r.get("ai_total", 0)
        if m <= 0:
            continue
        g = (a - m) / m * 100
        if g < -30: dist["AI << M (<-30%)"] += 1
        elif g < -10: dist["AI < M (-30..-10)"] += 1
        elif g < 10: dist["close (±10%)"] += 1
        elif g < 30: dist["AI > M (+10..+30)"] += 1
        else: dist["AI >> M (>+30)"] += 1

    lines.append("### Gap distribution")
    lines.append("")
    lines.append("| Bucket | Records |")
    lines.append("|---|---|")
    for k, v in dist.items():
        lines.append(f"| {k} | {v} |")
    lines.append("")

    # Buckets with proposed fixes
    lines.append("## Root-cause buckets")
    lines.append("")
    for b in ordered_buckets:
        items = by_bucket[b]
        meta = BUCKET_META.get(b, {"title": b, "fix": ""})
        gap_units = bucket_gap.get(b, 0)
        lines.append(f"### {meta['title']}")
        lines.append("")
        lines.append(f"**{len(items)} records · absolute unit gap: {gap_units:,}**")
        lines.append("")
        if meta.get("fix"):
            lines.append(f"**Proposed fix:** {meta['fix']}")
            lines.append("")

        # Show up to 15 worst offenders per bucket
        items_sorted = sorted(
            items,
            key=lambda t: abs(t[0].get("projection_total", 0) - t[0].get("ai_total", 0)),
            reverse=True,
        )[:15]
        if items_sorted:
            lines.append("| Key | Manual | AI | Gap% | Description | Diagnosis |")
            lines.append("|---|---:|---:|---:|---|---|")
            for r, diag in items_sorted:
                m = r.get("projection_total", 0)
                a = r.get("ai_total", 0)
                g = (a - m) / m * 100 if m else 0
                desc = r.get("desc", "")[:45].replace("|", "/")
                lines.append(
                    f"| {r['key']} | {m:,} | {a:,} | {g:+.1f}% | {desc} | {diag} |"
                )
            lines.append("")

    # Unmatched seasonal keyword summary
    if unmatched_kw_counter:
        lines.append("## Unmatched seasonal keywords (add to CATEGORY_PROFILES)")
        lines.append("")
        lines.append("| Keyword | Records | Suggested category |")
        lines.append("|---|---:|---|")
        for k, c in unmatched_kw_counter.most_common():
            lines.append(f"| `{k}` | {c} | {UNMATCHED_SEASONAL_KEYWORDS[k]} |")
        lines.append("")

    # Priority-ordered fix list
    lines.append("## Priority-ordered model fixes")
    lines.append("")
    priority_order = [
        "inactive_with_activity",
        "seasonal_ramp_underforecast",
        "seasonal_unmatched",
        "prime_day_prebuy_gap",
        "sparse_baseline_too_low",
        "declining_overforecast",
        "spike_overforecast",
    ]
    rank = 1
    for b in priority_order:
        if b not in by_bucket:
            continue
        meta = BUCKET_META[b]
        n = len(by_bucket[b])
        units = bucket_gap[b]
        lines.append(f"{rank}. **{meta['title']}** ({n} records, {units:,} unit gap)")
        lines.append(f"   - {meta['fix']}")
        lines.append("")
        rank += 1

    # Raw CSV export of top records for spreadsheet analysis
    csv_path = out_path.replace(".md", "_records.csv")
    try:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write("key,mstyle,cust,desc,pattern,model,manual,ai,gap_pct,l13_nz_avg,l52_peak,bucket\n")
            for r in top:
                m = r.get("projection_total", 0)
                a = r.get("ai_total", 0)
                g = (a - m) / m * 100 if m else 0
                hist_ord = r.get("history_l26_ord", [])
                l13_nz = [v for v in hist_ord[:13] if v > 0]
                l13_nz_avg = sum(l13_nz)/len(l13_nz) if l13_nz else 0
                peak = max(hist_ord) if hist_ord else 0
                b, _ = classify_gap(r)
                desc = r.get("desc", "").replace(",", " ").replace('"', "'")[:80]
                f.write(
                    f'{r["key"]},{r.get("mstyle","")},{r.get("cust","")[:20]},'
                    f'"{desc}",{r.get("pattern","")},{r.get("ai_model","")},'
                    f'{m},{a},{g:.1f},{l13_nz_avg:.0f},{peak},{b or ""}\n'
                )
    except Exception as e:
        print(f"WARN: could not write CSV: {e}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Console summary
    print("=" * 70)
    print(f"  Gap Analysis — top {len(top)} records by manual volume")
    print("=" * 70)
    print(f"  Manual total:   {tot_m:,}")
    print(f"  AI total:       {tot_a:,}  ({(tot_a-tot_m)/tot_m*100:+.1f}%)" if tot_m else f"  AI total:       {tot_a:,}  (n/a)")
    print(f"  Unit gap:       {abs(tot_a-tot_m):,}")
    print()
    print(f"  Root-cause buckets (by absolute unit gap):")
    for b in ordered_buckets:
        if b == "aligned":
            continue
        print(f"    {BUCKET_META[b]['title']:45s}  "
              f"n={len(by_bucket[b]):3d}  gap={bucket_gap[b]:>9,}")
    print()
    print(f"  Report: {out_path}")
    print(f"  CSV:    {csv_path}")


def main():
    p = argparse.ArgumentParser(description="Gap analysis — manual vs AI projections")
    p.add_argument("--results", default="validation_results.json",
                   help="Results JSON (validation_results.json or forecast_results.json)")
    p.add_argument("--top", type=int, default=100,
                   help="Number of top-volume records to analyze (default: 100)")
    p.add_argument("--out", default="gap_analysis_report.md",
                   help="Output markdown path (default: gap_analysis_report.md)")
    args = p.parse_args()
    run_analysis(args.results, args.top, args.out)


if __name__ == "__main__":
    main()
