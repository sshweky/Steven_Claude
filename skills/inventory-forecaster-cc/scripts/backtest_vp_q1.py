"""
VP-Q1 baseline-logic back-test.

Compares the AI_PRJ values currently live in Quickbase (produced by the OLD
all-zeros-filtered baseline) against what the NEW evidence-based logic
produces for the same scope.

Usage:
    python scripts/backtest_vp_q1.py --acct 1864
    python scripts/backtest_vp_q1.py --customer "WAL MART STORES"

Output (in skill root):
    backtest_vp_q1_<scope>.csv    line-per-record old vs new vs baseline_mode
    backtest_vp_q1_<scope>.md     summary report for VP review
"""
import os, sys, json, argparse, subprocess, re
from pathlib import Path
import requests

ROOT  = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

H = {
    "QB-Realm-Hostname": "pim.quickbase.com",
    "Authorization":     "QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s",
    "Content-Type":      "application/json",
}
QB_API = "https://api.quickbase.com/v1"
TBL    = "bpd237tvm"  # Projections

# Field IDs (validated 2026-04-28)
F_KEY        = 292
F_MSTYLE     = 196
F_CUST       = 363
F_DESC       = 205
F_STATUS     = 10
F_AI_PRJ_W   = list(range(1511, 1537))  # W1..W26


def fetch_current_qb_snapshot(where):
    """Pull current AI_PRJ_W1..W26 + identity for scope. Returns {key: row}."""
    select = [F_KEY, F_MSTYLE, F_CUST, F_DESC] + F_AI_PRJ_W
    out = {}
    skip = 0
    while True:
        r = requests.post(f"{QB_API}/records/query", headers=H, json={
            "from": TBL, "select": select, "where": where,
            "options": {"skip": skip, "top": 1000},
        })
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", [])
        for row in rows:
            key = (row.get(str(F_KEY)) or {}).get("value", "")
            if not key:
                continue
            ai = [float((row.get(str(f)) or {}).get("value") or 0) for f in F_AI_PRJ_W]
            out[key] = {
                "key":    key,
                "mstyle": (row.get(str(F_MSTYLE)) or {}).get("value", ""),
                "cust":   (row.get(str(F_CUST))   or {}).get("value", ""),
                "desc":   (row.get(str(F_DESC))   or {}).get("value", ""),
                "ai_old": ai,
                "ai_old_total": sum(ai),
            }
        if len(rows) < 1000:
            break
        skip += 1000
    return out


def run_new_forecaster(scope_args):
    """Invoke the forecaster (new logic) in dry-run mode for the same scope.
    Returns parsed forecast_results.json content."""
    out_path = ROOT / "backtest_new_forecast.json"
    if out_path.exists():
        out_path.unlink()
    cmd = [
        sys.executable, str(SCRIPTS / "inventory_forecaster.py"),
        *scope_args,
        "--dry-run",
        "--out", str(out_path),
    ]
    print("Running:", " ".join(cmd))
    rc = subprocess.run(cmd, cwd=str(ROOT))
    if rc.returncode != 0:
        sys.exit(f"forecaster exited {rc.returncode}")
    if not out_path.exists():
        sys.exit("forecaster did not produce backtest_new_forecast.json")
    return json.loads(out_path.read_text())


def build_diff_report(old_by_key, new_results, scope_label):
    """Join old vs new by key and produce CSV + summary markdown."""
    rows  = []
    skipped_no_match = 0
    for r in new_results.get("records", []):
        key = r.get("key")
        if not key:
            continue
        old = old_by_key.get(key)
        if not old:
            skipped_no_match += 1
            continue
        new_fcst = r.get("forecast") or []
        new_total = sum(float(v) for v in new_fcst) if new_fcst else 0
        old_total = old["ai_old_total"]
        delta_pct = ((new_total - old_total) / old_total * 100) if old_total > 0 else 0
        rows.append({
            "key":        key,
            "cust":       old["cust"][:25],
            "mstyle":     old["mstyle"],
            "desc":       (old["desc"] or "")[:40],
            "model":      r.get("model", ""),
            "baseline_mode": r.get("baseline_mode", ""),
            "old_wkly":   round(old_total / 26, 1),
            "new_wkly":   round(new_total / 26, 1),
            "old_26w":    round(old_total),
            "new_26w":    round(new_total),
            "delta_pct":  round(delta_pct, 1),
        })
    # Sort by absolute delta_pct descending
    rows.sort(key=lambda r: -abs(r["delta_pct"]))

    # CSV
    csv_path = ROOT / f"backtest_vp_q1_{scope_label}.csv"
    import csv
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                          ["key","cust","mstyle","desc","model","baseline_mode",
                           "old_wkly","new_wkly","old_26w","new_26w","delta_pct"])
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {csv_path.name} ({len(rows)} records)")

    # Summary markdown
    md = []
    md.append(f"# VP-Q1 baseline back-test &mdash; {scope_label}\n")
    md.append(f"Compares AI_PRJ values currently in Quickbase (old logic) vs the new ")
    md.append(f"evidence-based baseline-mode logic in `seasonal_baseline()`.\n\n")
    md.append(f"- **Records compared:** {len(rows)}")
    if skipped_no_match:
        md.append(f"  (+{skipped_no_match} new records had no QB AI_PRJ to compare)")
    md.append("")

    # Headline totals
    old_total = sum(r["old_26w"] for r in rows)
    new_total = sum(r["new_26w"] for r in rows)
    delta = new_total - old_total
    delta_pct = (delta / old_total * 100) if old_total > 0 else 0
    md.append(f"- **Aggregate 26-week demand:**")
    md.append(f"    - Old: {old_total:,} units")
    md.append(f"    - New: {new_total:,} units")
    md.append(f"    - Delta: {delta:+,} units ({delta_pct:+.1f}%)")
    md.append("")

    # Baseline mode breakdown
    by_mode = {}
    for r in rows:
        m = r["baseline_mode"] or "(no mode)"
        # Strip the parenthetical detail to bucket cleanly
        m_short = re.sub(r"\s*\(.*\)$", "", m).strip()
        by_mode.setdefault(m_short, []).append(r)
    md.append(f"## Baseline-mode breakdown\n")
    md.append(f"| Mode | Count | Old 26w | New 26w | Delta% |")
    md.append(f"|---|---:|---:|---:|---:|")
    for m_short, recs in sorted(by_mode.items(), key=lambda x: -len(x[1])):
        ot = sum(r["old_26w"] for r in recs)
        nt = sum(r["new_26w"] for r in recs)
        dp = ((nt - ot) / ot * 100) if ot > 0 else 0
        md.append(f"| {m_short} | {len(recs)} | {ot:,} | {nt:,} | {dp:+.1f}% |")
    md.append("")

    # Top 25 biggest changes
    md.append(f"## Top 25 records by absolute change\n")
    md.append(f"| Key | Customer | Mstyle | Desc | Old/wk | New/wk | Delta% | Mode |")
    md.append(f"|---|---|---|---|---:|---:|---:|---|")
    for r in rows[:25]:
        md.append(
            f"| {r['key']} | {r['cust']} | {r['mstyle']} | {r['desc'][:32]} | "
            f"{r['old_wkly']:.0f} | {r['new_wkly']:.0f} | "
            f"{r['delta_pct']:+.1f}% | {(r['baseline_mode'] or '')[:55]} |"
        )

    md_path = ROOT / f"backtest_vp_q1_{scope_label}.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"  wrote {md_path.name}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--acct")
    p.add_argument("--customer")
    p.add_argument("--mstyle")
    args = p.parse_args()

    scope_args = []
    parts = []
    if args.acct:
        scope_args += ["--acct", args.acct]
        parts.append(f"acct{args.acct}")
        # QB Status_Cust starts with 'A' AND key starts with the acct number
        where = (f"{{{F_STATUS}.SW.'A'}}AND{{{F_KEY}.SW.'{args.acct}-'}}")
    elif args.customer:
        scope_args += ["--customer", args.customer]
        parts.append("cust_" + re.sub(r"[^A-Za-z0-9]+", "_", args.customer)[:30])
        where = f"{{{F_STATUS}.SW.'A'}}AND{{{F_CUST}.CT.'{args.customer}'}}"
    elif args.mstyle:
        scope_args += ["--mstyle", args.mstyle]
        parts.append(f"mstyle_{args.mstyle}")
        where = f"{{{F_STATUS}.SW.'A'}}AND{{{F_MSTYLE}.EX.'{args.mstyle}'}}"
    else:
        sys.exit("Specify --acct, --customer, or --mstyle")

    scope_label = "_".join(parts)

    print(f"\n[1/3] Snapshotting current QB AI_PRJ for {scope_label}...")
    old_by_key = fetch_current_qb_snapshot(where)
    print(f"      pulled {len(old_by_key)} records")

    print(f"\n[2/3] Running forecaster with NEW logic (dry-run)...")
    new_results = run_new_forecaster(scope_args)
    print(f"      forecaster produced {len(new_results.get('records', []))} records")

    print(f"\n[3/3] Building diff report...")
    build_diff_report(old_by_key, new_results, scope_label)
    print("\nDONE")


if __name__ == "__main__":
    main()
