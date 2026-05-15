"""
VP-Q2 OOS-smoothing back-test.

Runs the forecaster TWICE for the same scope:
  (a) Without --oos-smoothing (baseline = current production logic)
  (b) With    --oos-smoothing (VP-Q2 logic: clean demand from Order_History)

Then joins the two forecast outputs by key and produces:
    backtest_vp_q2_<scope>.csv    line-per-record with old vs new totals
    backtest_vp_q2_<scope>.md     summary report for VP review

Usage:
    python scripts/backtest_vp_q2.py --acct 1864
    python scripts/backtest_vp_q2.py --customer "WAL MART STORES"
"""
import os, sys, json, argparse, subprocess, re, csv
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run_forecast(scope_args, with_oos, out_path):
    """Invoke inventory_forecaster.py in dry-run mode and capture results."""
    if out_path.exists():
        out_path.unlink()
    cmd = [
        sys.executable, str(SCRIPTS / "inventory_forecaster.py"),
        *scope_args,
        "--dry-run",
        "--out", str(out_path),
    ]
    if with_oos:
        cmd.append("--oos-smoothing")
    label = "WITH oos-smoothing" if with_oos else "BASELINE (no smoothing)"
    print(f"\n[run] {label}")
    print("      " + " ".join(cmd))
    rc = subprocess.run(cmd, cwd=str(ROOT))
    if rc.returncode != 0:
        sys.exit(f"forecaster exited {rc.returncode} on {label}")
    if not out_path.exists():
        sys.exit(f"forecaster did not produce {out_path}")
    return json.loads(out_path.read_text())


def build_diff_report(base_results, vp2_results, scope_label):
    base_by_key = {r["key"]: r for r in base_results.get("records", []) if r.get("key")}
    vp2_by_key  = {r["key"]: r for r in vp2_results.get("records",  []) if r.get("key")}

    rows  = []
    for key, vp2 in vp2_by_key.items():
        base = base_by_key.get(key)
        if not base:
            continue
        old_total = float(base.get("new_total") or 0)
        new_total = float(vp2.get("new_total")  or 0)
        delta_pct = ((new_total - old_total) / old_total * 100) if old_total > 0 else 0
        rows.append({
            "key":         key,
            "cust":        (vp2.get("cust") or "")[:25],
            "mstyle":      vp2.get("mstyle", ""),
            "model_base":  base.get("model", ""),
            "model_vp2":   vp2.get("model", ""),
            "baseline_mode_base": base.get("baseline_mode", ""),
            "baseline_mode_vp2":  vp2.get("baseline_mode", ""),
            "old_wkly":    round(old_total / 26, 1),
            "new_wkly":    round(new_total / 26, 1),
            "old_26w":     int(old_total),
            "new_26w":     int(new_total),
            "delta_units": int(new_total - old_total),
            "delta_pct":   round(delta_pct, 1),
        })
    rows.sort(key=lambda r: -abs(r["delta_pct"]))

    csv_path = ROOT / f"backtest_vp_q2_{scope_label}.csv"
    fields = list(rows[0].keys()) if rows else [
        "key","cust","mstyle","model_base","model_vp2",
        "baseline_mode_base","baseline_mode_vp2",
        "old_wkly","new_wkly","old_26w","new_26w","delta_units","delta_pct"
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\n  wrote {csv_path.name} ({len(rows)} records)")

    md = []
    md.append(f"# VP-Q2 OOS-smoothing back-test &mdash; {scope_label}\n")
    md.append("Compares the production forecaster output (no smoothing) vs the new ")
    md.append("VP-Q2 OOS-aware demand reconstruction (clean demand from Order_History).\n\n")
    md.append(f"- **Records compared:** {len(rows)}\n")

    old_total = sum(r["old_26w"] for r in rows)
    new_total = sum(r["new_26w"] for r in rows)
    delta = new_total - old_total
    delta_pct = (delta / old_total * 100) if old_total > 0 else 0
    md.append(f"- **Aggregate 26-week demand:**")
    md.append(f"    - Baseline:    {old_total:,} units")
    md.append(f"    - VP-Q2:       {new_total:,} units")
    md.append(f"    - Delta:       {delta:+,} units ({delta_pct:+.1f}%)\n")

    n_changed = sum(1 for r in rows if r["delta_units"] != 0)
    n_lifted  = sum(1 for r in rows if r["delta_units"] >  0)
    n_lowered = sum(1 for r in rows if r["delta_units"] <  0)
    md.append(f"- **Records with any change:** {n_changed}  "
              f"(lifted: {n_lifted}, lowered: {n_lowered})\n")

    by_model = {}
    for r in rows:
        m = r["model_vp2"] or "(blank)"
        by_model.setdefault(m, []).append(r)
    md.append(f"\n## Model breakdown\n")
    md.append(f"| Model | Count | Base 26w | VP-Q2 26w | Delta% |")
    md.append(f"|---|---:|---:|---:|---:|")
    for m, recs in sorted(by_model.items(), key=lambda x: -len(x[1])):
        ot = sum(r["old_26w"] for r in recs)
        nt = sum(r["new_26w"] for r in recs)
        dp = ((nt - ot) / ot * 100) if ot > 0 else 0
        md.append(f"| {m} | {len(recs)} | {ot:,} | {nt:,} | {dp:+.1f}% |")

    md.append(f"\n## Top 25 records by absolute change\n")
    md.append(f"| Key | Customer | Mstyle | Model | Old/wk | New/wk | Delta% |")
    md.append(f"|---|---|---|---|---:|---:|---:|")
    for r in rows[:25]:
        md.append(
            f"| {r['key']} | {r['cust']} | {r['mstyle']} | "
            f"{r['model_vp2'][:18]} | "
            f"{r['old_wkly']:.0f} | {r['new_wkly']:.0f} | {r['delta_pct']:+.1f}% |"
        )

    md_path = ROOT / f"backtest_vp_q2_{scope_label}.md"
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
    elif args.customer:
        scope_args += ["--customer", args.customer]
        parts.append("cust_" + re.sub(r"[^A-Za-z0-9]+", "_", args.customer)[:30])
    elif args.mstyle:
        scope_args += ["--mstyle", args.mstyle]
        parts.append(f"mstyle_{args.mstyle}")
    else:
        sys.exit("Specify --acct, --customer, or --mstyle")

    scope_label = "_".join(parts)

    base_path = ROOT / "backtest_vp_q2_base.json"
    vp2_path  = ROOT / "backtest_vp_q2_vp2.json"

    base = run_forecast(scope_args, with_oos=False, out_path=base_path)
    vp2  = run_forecast(scope_args, with_oos=True,  out_path=vp2_path)

    print(f"\n[diff] building report ...")
    build_diff_report(base, vp2, scope_label)
    print("\nDONE")


if __name__ == "__main__":
    main()
