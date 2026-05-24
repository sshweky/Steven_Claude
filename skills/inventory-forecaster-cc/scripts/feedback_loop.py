"""
feedback_loop.py  --  Closed-loop learning framework: log every planner
                      override of AI, then periodically analyze to surface
                      systematic blindspots.

Two functions, both importable from viewer.py / push_validation_qb.py:

  log_override(record_key, ai_fcst, planner_fcst, source, ...)
       Appends one JSONL event to feedback/overrides.jsonl. Called from:
        - viewer.py when a planner clicks "Use AI" / "Use Suggested"
        - push_validation_qb.py when a manual projection diverges >10% from AI

  learn_from_overrides(min_events=20, lookback_days=30)
       Reads the JSONL, aggregates by (rule pattern, customer, brand, model),
       identifies systematic divergences (planners overrode AI in the same
       direction >75% of the time on >= 20 events). Writes a markdown report
       with concrete suggestions.

The data accumulates passively. The analysis runs on-demand.

Usage:
    # From inside viewer.py:
    from feedback_loop import log_override
    log_override("1864-FF8654", ai_total=12000, manual_total=8000,
                 source="planner_use_manual", customer="WAL MART STORES",
                 model="Seasonal Baseline", rule_fires=["F62", "F66"])

    # Analyze:
    python scripts/feedback_loop.py            # default 30d lookback
    python scripts/feedback_loop.py --lookback-days 90
"""

import argparse
import json
import sys
import time
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
FEEDBACK_DIR = SKILL_ROOT / "feedback"
FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
OVERRIDES_JSONL = FEEDBACK_DIR / "overrides.jsonl"


# ─────────────────────────────────────────────────────────────────────────────
# Log API -- callable from any script
# ─────────────────────────────────────────────────────────────────────────────

def log_override(record_key: str,
                 ai_total: float,
                 manual_total: float,
                 *,
                 source: str,
                 customer: str = "",
                 brand: str = "",
                 model: str = "",
                 rule_fires: list[str] | None = None,
                 ai_weeks: list[float] | None = None,
                 manual_weeks: list[float] | None = None,
                 note: str = ""):
    """Append one override-event to feedback/overrides.jsonl.

    Args:
        record_key:   Acct#-MStyle key (e.g. "1864-FF8654")
        ai_total:     sum of AI projection over 26 weeks
        manual_total: sum of manual projection over 26 weeks
        source:       short string describing what triggered the log, e.g.
                      "planner_use_manual" | "manual_diverges>10pct" |
                      "f58_comment_override" | "use_ai_button"
        customer:     customer name
        brand:        master brand
        model:        AI model used (Seasonal Baseline / Croston's / etc.)
        rule_fires:   list of rule codes that fired on this record
        ai_weeks:     26-element AI forecast (optional, for shape analysis)
        manual_weeks: 26-element manual projection (optional)
        note:         free-text annotation
    """
    if manual_total > 0:
        direction = "UP" if manual_total > ai_total else "DOWN" if manual_total < ai_total else "FLAT"
        delta_pct = round((manual_total - ai_total) / manual_total * 100, 2)
    else:
        direction = "UP" if ai_total < 0 else "FLAT"
        delta_pct = 0.0

    event = {
        "ts":           datetime.now().isoformat(timespec="seconds"),
        "key":          record_key,
        "source":       source,
        "ai_total":     round(ai_total, 0),
        "manual_total": round(manual_total, 0),
        "direction":    direction,
        "delta_pct":    delta_pct,
        "customer":     customer,
        "brand":        brand,
        "model":        model,
        "rule_fires":   rule_fires or [],
        "note":         note,
    }
    if ai_weeks is not None:
        event["ai_weeks"] = [round(v, 0) for v in ai_weeks]
    if manual_weeks is not None:
        event["manual_weeks"] = [round(v, 0) for v in manual_weeks]

    with open(OVERRIDES_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Read API -- analysis
# ─────────────────────────────────────────────────────────────────────────────

def read_recent_events(lookback_days: int = 30) -> list[dict]:
    """Return events from the last N days, newest first."""
    if not OVERRIDES_JSONL.exists():
        return []
    cutoff = datetime.now() - timedelta(days=lookback_days)
    events = []
    with open(OVERRIDES_JSONL, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"])
                if ts >= cutoff:
                    events.append(e)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return sorted(events, key=lambda e: e["ts"], reverse=True)


def aggregate_by_dim(events: list[dict], dim: str,
                     min_events: int = 20) -> list[dict]:
    """Group events by a dimension (customer/brand/model/rule_fire).
    Return groups with >= min_events that show a strong directional bias.
    """
    buckets = defaultdict(list)
    for e in events:
        if dim == "rule_fire":
            for code in e.get("rule_fires", []):
                buckets[code].append(e)
        else:
            v = e.get(dim, "")
            if v:
                buckets[v].append(e)

    findings = []
    for key, group in buckets.items():
        if len(group) < min_events:
            continue
        ups = sum(1 for e in group if e["direction"] == "UP")
        downs = sum(1 for e in group if e["direction"] == "DOWN")
        n = len(group)
        bias = max(ups, downs) / n
        if bias < 0.75:
            continue
        direction = "UP" if ups > downs else "DOWN"
        avg_delta = sum(e["delta_pct"] for e in group) / n
        findings.append({
            "dim":         dim,
            "key":         key,
            "n":           n,
            "direction":   direction,
            "bias_pct":    round(bias * 100, 1),
            "avg_delta":   round(avg_delta, 2),
            "ups":         ups,
            "downs":       downs,
        })
    return sorted(findings, key=lambda f: -f["n"])


def learn_from_overrides(lookback_days: int = 30,
                          min_events: int = 20) -> dict:
    """Analyze recent overrides, surface systematic patterns. Returns a dict
    suitable for markdown rendering."""
    events = read_recent_events(lookback_days)
    if not events:
        return {"events": 0, "findings": {}}

    findings = {
        "by_customer":  aggregate_by_dim(events, "customer", min_events),
        "by_brand":     aggregate_by_dim(events, "brand",    min_events),
        "by_model":     aggregate_by_dim(events, "model",    min_events),
        "by_rule_fire": aggregate_by_dim(events, "rule_fire", min_events),
    }
    return {
        "events":         len(events),
        "lookback_days":  lookback_days,
        "min_events":     min_events,
        "findings":       findings,
    }


def render_markdown(report: dict) -> str:
    """Render the report dict as a markdown document with action items."""
    md = ["# Planner Feedback Loop -- Override Analysis", ""]
    md.append(f"**Lookback:** {report.get('lookback_days', '?')} days")
    md.append(f"**Total events:** {report['events']}")
    md.append(f"**Minimum group size:** {report.get('min_events', '?')}")
    md.append(f"**Required directional bias:** 75%")
    md.append("")

    if report["events"] == 0:
        md.append("_No override events logged yet. The feedback loop needs at"
                  " least one forecast cycle with planner activity to surface findings._")
        return "\n".join(md)

    for dim_key, label in [
        ("by_customer", "Customers with systematic AI override"),
        ("by_brand",    "Brands with systematic AI override"),
        ("by_model",    "AI models with systematic override (model class bias)"),
        ("by_rule_fire", "Rules that consistently produce overridden forecasts"),
    ]:
        rows = report["findings"].get(dim_key, [])
        md.append(f"## {label}")
        if not rows:
            md.append("\n_No findings above threshold._\n")
            continue
        md.append("\n| Dimension | Events | Direction | Bias | Avg Δ |")
        md.append("|---|---:|---|---:|---:|")
        for f in rows:
            md.append(f"| `{f['key']}` | {f['n']} | {f['direction']} | "
                      f"{f['bias_pct']:.0f}% | {f['avg_delta']:+.1f}% |")
        md.append("")
        # Action items
        md.append("**Suggested actions:**")
        for f in rows[:5]:
            if dim_key == "by_customer":
                hint = f"  - Add `{f['key']}` to CUSTOMER_BIAS_CORRECTIONS with multiplier ~{1 + f['avg_delta']/100:.2f}."
            elif dim_key == "by_rule_fire":
                hint = f"  - Rule `{f['key']}` fires when planners disagree -- review its threshold or skip-conditions."
            else:
                hint = f"  - Investigate `{f['key']}` ({f['n']} events, {f['direction']} bias)."
            md.append(hint)
        md.append("")

    return "\n".join(md)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lookback-days", type=int, default=30)
    p.add_argument("--min-events", type=int, default=20)
    p.add_argument("--out", default=str(SKILL_ROOT / "feedback_report.md"))
    p.add_argument("--seed-test-data", action="store_true",
                   help="Append synthetic events to overrides.jsonl for smoke testing.")
    args = p.parse_args()

    if args.seed_test_data:
        # Smoke test: populate with 30 fake events biased UP for a customer.
        for i in range(30):
            log_override(f"1864-FAKE{i:03d}", ai_total=1000, manual_total=1500,
                         source="seed_test", customer="WAL MART STORES",
                         brand="Glad for Pets", model="Croston's",
                         rule_fires=["F18", "F66"])
        print(f"Seeded 30 synthetic events to {OVERRIDES_JSONL}")

    report = learn_from_overrides(args.lookback_days, args.min_events)
    md = render_markdown(report)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Analyzed {report['events']} events over {args.lookback_days} days.")
    print(f"Report -> {args.out}")


if __name__ == "__main__":
    main()
