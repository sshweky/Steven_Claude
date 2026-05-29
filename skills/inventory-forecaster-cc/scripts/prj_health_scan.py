#!/usr/bin/env python3
"""
prj_health_scan.py  --  Inventory flow health scanner
=======================================================
Reads forecast_results.json, fetches Inventory Flow data from QB REST,
simulates week-by-week inventory, and flags records that need attention.

Detected issue types (see ISSUE_RANK for severity order):
  DATA_GAP      W1 Beg Inv = 0 but W2 shows significant stock -- QB formula
                timing issue, not a real OOS.  "Did you know your W1 is blank?"
  OOS_TRUE      W1 Beg Inv = 0 AND the forward cascade stays near 0 (real stockout)
  REORDER_NOW   Simulated inventory hits 0 within lead-time window --
                if you don't order this week you WILL stock out
  REORDER_WATCH Simulated stockout in LT + 4 weeks -- order soon
  LOW_WOS       Current WOS < 50% of lead time (thin buffer)
  OVERSTOCK     Pipeline (OH + all scheduled receipts) > demand + safety stock
  MISMATCH      Manual demand > 120% of AI demand for 3+ weeks (planner may be over-
                projecting and masking an upcoming inventory concern)

Usage:
    python prj_health_scan.py                   # all active mstyles
    python prj_health_scan.py --top 50          # limit report to top 50
    python prj_health_scan.py --issues OOS_TRUE REORDER_NOW   # filter issue types
    python prj_health_scan.py --customer AMAZON # filter by customer substring

Output (written to ../analysis/):
    prj_health_scan.md   --  ranked markdown report
    prj_health_scan.csv  --  raw per-mstyle data for pivot tables
"""

import sys, os, json, csv, math, argparse
import urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import defaultdict

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import QB_REALM, QB_USER_TOKEN, QB_INV_FLOW_TABLE

QB_API = "https://api.quickbase.com/v1"   # always api.quickbase.com; QB_REALM goes in headers only
_HDRS  = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_USER_TOKEN}",
    "Content-Type":      "application/json",
}

# ── Inventory Flow FIDs (from viewer.html + _INV_FLOW_FALLBACK_FIDS) ──────────
_IF_MSTYLE_FID  = 20
_IF_OPT_WOS     = 137    # original Opt WOS
_IF_OPT_FINAL   = 1897   # Opt WOS + planner override (preferred)
_IF_NEXT_RCPT   = 235    # Next Avl Rcpt Dt (date)
_IF_LT_WKS      = 1525   # Lead Time in weeks (LT + Trans / 7)
_IF_MOQ         = 226    # Minimum Order Quantity

# Beg Inv Wk1..Wk26 (QB formula fields -- weekly snapshot of projected OH)
_IF_BEG_FIDS = [134, 8, 9, 10, 110, 111, 112, 113, 114, 115,
                116, 117, 118, 128, 129, 130, 131, 120, 121, 122,
                123, 124, 125, 126, 127, 119]

# Rcv Wk0..Wk26  (Wk0 = past-due, rolls into W1)
_IF_RCV_FIDS = [295, 28, 35, 36, 50, 51, 65, 66, 67, 68, 69, 70, 71,
                72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85]

ALL_FIDS = ([_IF_MSTYLE_FID, _IF_OPT_WOS, _IF_OPT_FINAL,
             _IF_NEXT_RCPT, _IF_LT_WKS, _IF_MOQ]
            + _IF_BEG_FIDS + _IF_RCV_FIDS)

ISSUE_RANK = {
    "OOS_TRUE":     1,
    "REORDER_NOW":  2,
    "DATA_GAP":     3,
    "REORDER_WATCH":4,
    "LOW_WOS":      5,
    "OVERSTOCK":    6,
    "MISMATCH":     7,
}

ISSUE_LABEL = {
    "OOS_TRUE":     "OOS (True)",
    "REORDER_NOW":  "Reorder NOW",
    "DATA_GAP":     "W1 Data Gap",
    "REORDER_WATCH":"Reorder Watch",
    "LOW_WOS":      "Low WOS",
    "OVERSTOCK":    "Overstock",
    "MISMATCH":     "Demand Mismatch",
}


# ── QB REST helpers ────────────────────────────────────────────────────────────
def _qb_post(path, body):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(QB_API + path, data=data,
                                  headers=_HDRS, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _qb_get(path):
    req = urllib.request.Request(QB_API + path, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_inv_flow(mstyles):
    """Batch-fetch Inventory Flow for all requested mstyles.
    Returns dict: mstyle -> {beg, rcv, opt_wos, lt_wks, moq, next_rcpt}
    """
    print(f"  [inv_flow] fetching {len(mstyles)} mstyles from QB REST ...")
    batch  = 100
    out    = {}
    total  = 0
    mlist  = sorted(mstyles)

    for i in range(0, len(mlist), batch):
        chunk = mlist[i:i+batch]
        where = " OR ".join(f"{{20.EX.'{ms}'}}" for ms in chunk)
        body  = {
            "from":   QB_INV_FLOW_TABLE,
            "select": ALL_FIDS,
            "where":  f"({where})",
            "options":{"top": batch + 5},
        }
        try:
            resp = _qb_post("/records/query", body)
        except Exception as e:
            print(f"  [inv_flow] WARNING: batch {i//batch+1} failed: {e}")
            continue

        fields = {f["id"]: f["label"] for f in resp.get("fields", [])}
        for row in resp.get("data", []):
            def cell(fid):
                v = row.get(str(fid), {}).get("value")
                if v is None or v == "":
                    return 0.0
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0

            def scell(fid):
                v = row.get(str(fid), {}).get("value")
                return str(v) if v else ""

            ms = scell(_IF_MSTYLE_FID)
            if not ms:
                continue

            beg = [cell(f) for f in _IF_BEG_FIDS]       # W1..W26
            rcv_raw = [cell(f) for f in _IF_RCV_FIDS]    # Wk0..Wk26
            # Merge Wk0 (past-due) into W1
            rcv = rcv_raw[1:]                              # W1..W26
            rcv[0] += rcv_raw[0]

            opt_wos_final = cell(_IF_OPT_FINAL)
            opt_wos_base  = cell(_IF_OPT_WOS)
            opt_wos = opt_wos_final if opt_wos_final > 0 else opt_wos_base

            lt_wks     = cell(_IF_LT_WKS)
            moq        = cell(_IF_MOQ)
            next_rcpt  = scell(_IF_NEXT_RCPT)

            # Prefer row with non-zero beg_inv if duplicate
            if ms in out and beg[0] == 0 and out[ms]["beg"][0] != 0:
                continue

            out[ms] = {
                "beg":       beg,
                "rcv":       rcv,
                "opt_wos":   opt_wos,
                "lt_wks":    lt_wks,
                "moq":       moq,
                "next_rcpt": next_rcpt,
            }
            total += 1

    print(f"  [inv_flow] {total} mstyles loaded")
    return out


# ── Projection data ────────────────────────────────────────────────────────────
def load_forecast_results(path):
    """Load forecast_results.json. Returns list of record dicts."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("records", [])
    return data


def roll_up_demand_by_mstyle(records, customer_filter=None):
    """Sum manual projections by mstyle across all accounts.
    Manual = planner's committed demand signal.
    Falls back to AI forecast if manual is all zeros.
    Returns dict: mstyle -> [w1..w26] weekly demand.
    Also tracks: mstyle -> list of (key, ai_26w, man_26w) for mismatch detection.
    """
    demand   = defaultdict(lambda: [0.0]*26)
    ai_prj   = defaultdict(lambda: [0.0]*26)
    accounts = defaultdict(list)  # mstyle -> list of (key, man_26w, ai_26w, model)

    for r in records:
        ms = r.get("mstyle", "")
        if not ms:
            continue
        cust = r.get("cust", "")
        if customer_filter and customer_filter.upper() not in cust.upper():
            continue

        model = r.get("model", "")
        if "Inactive" in model or "zeroed" in model.lower():
            continue  # skip inactive -- no real demand

        man = [float(v or 0) for v in r.get("manual", [0]*26)]
        ai  = [float(v or 0) for v in r.get("forecast", [0]*26)]

        # Use manual if non-zero, else AI
        use = man if sum(man) > 0 else ai

        for w in range(26):
            demand[ms][w] += use[w]
            ai_prj[ms][w] += ai[w]

        man_26w = sum(man)
        ai_26w  = sum(ai)
        accounts[ms].append({
            "key":     r.get("key", ""),
            "cust":    cust,
            "man_26w": man_26w,
            "ai_26w":  ai_26w,
            "model":   model,
        })

    return demand, ai_prj, accounts


# ── Health analysis per mstyle ─────────────────────────────────────────────────
def analyze_mstyle(ms, inv_data, demand_wks, ai_wks, acct_list):
    """Return a dict of flags + metrics for one mstyle."""
    beg     = inv_data["beg"]       # W1..W26
    rcv     = inv_data["rcv"]       # W1..W26
    opt_wos = inv_data["opt_wos"]   # target weeks of supply
    lt_wks  = inv_data["lt_wks"]    # lead time in weeks
    moq     = inv_data["moq"]
    next_rcpt = inv_data["next_rcpt"]

    lt_wks  = max(lt_wks, 1.0)
    moq_min = max(moq, 2500)  # fallback min significance threshold

    demand = demand_wks  # 26-element list
    ai     = ai_wks

    total_man_demand = sum(demand)
    total_ai_demand  = sum(ai)

    # Average demand rate (non-zero weeks only for intermittent items)
    nz_dem = [d for d in demand if d > 0]
    avg_demand = (sum(nz_dem) / len(nz_dem)) if nz_dem else 0.0
    avg_demand_all = sum(demand) / 26.0

    issues = []
    flags  = {}

    # ── DATA_GAP detection ─────────────────────────────────────────────────────
    # W1 Beg Inv = 0 but downstream shows meaningful inventory.
    # Classic symptom: QB formula timing lag where last week's receipt is
    # booked for "W2 arrival" even though stock physically exists.
    # Rule: W1=0 AND W2 has inventory NOT fully explained by a W2 receipt
    #       (i.e., the jump is suspiciously large vs. what demand could have caused).
    w1_beg = beg[0]
    w2_beg = beg[1] if len(beg) > 1 else 0
    w2_rcv = rcv[1] if len(rcv) > 1 else 0
    w1_dem = demand[0] if demand else 0

    # If W1=0 but: prior-week carry-forward estimate is large
    #   carry_est = W2_beg - W2_rcv + W1_demand
    #   (if positive and > threshold, W1 SHOULD have had inventory)
    carry_est = w2_beg - w2_rcv + w1_dem
    is_data_gap = (
        w1_beg == 0
        and (carry_est > moq_min or (w2_beg > moq_min and w2_beg > w2_rcv * 1.1))
    )

    if is_data_gap:
        issues.append("DATA_GAP")
        flags["data_gap_carry_est"] = round(carry_est)
        # For simulation, use W2 beg as effective starting inventory
        effective_w1 = carry_est if carry_est > 0 else w2_beg
    else:
        effective_w1 = w1_beg

    # ── True OOS (W1=0, not a data gap, demand exists) ─────────────────────────
    if w1_beg == 0 and not is_data_gap and avg_demand > 0:
        issues.append("OOS_TRUE")

    # ── Inventory simulation ───────────────────────────────────────────────────
    # Walk week by week: inv = starting_inv + receipt - demand
    # Use effective_w1 to correct data gaps.
    inv          = effective_w1
    inv_by_week  = []
    stockout_wk  = None
    wos_by_week  = []

    for w in range(26):
        inv += rcv[w]
        inv -= demand[w]
        inv_by_week.append(max(inv, 0))
        wos = (inv / avg_demand) if avg_demand > 0 else (999 if inv > 0 else 0)
        wos_by_week.append(round(wos, 1))
        if inv <= 0 and stockout_wk is None and avg_demand > 0:
            stockout_wk = w + 1  # 1-based week

    # ── REORDER_NOW / REORDER_WATCH ────────────────────────────────────────────
    # If you order today, earliest new stock arrives in lt_wks.
    # If stockout happens BEFORE that window closes, you are already late.
    if stockout_wk is not None:
        if stockout_wk <= lt_wks:
            issues.append("REORDER_NOW")
            flags["stockout_week"]  = stockout_wk
            flags["lt_wks"]         = round(lt_wks, 1)
        elif stockout_wk <= lt_wks + 4:
            issues.append("REORDER_WATCH")
            flags["stockout_week"]  = stockout_wk
            flags["lt_wks"]         = round(lt_wks, 1)

    # ── LOW_WOS (current WOS < 50% of lead time) ──────────────────────────────
    current_wos = wos_by_week[0] if wos_by_week else 0
    safe_wos    = opt_wos if opt_wos > 0 else lt_wks * 1.5
    if current_wos > 0 and current_wos < safe_wos * 0.5 and avg_demand > 0:
        issues.append("LOW_WOS")
        flags["current_wos"]  = current_wos
        flags["target_wos"]   = round(safe_wos, 1)

    # ── OVERSTOCK ──────────────────────────────────────────────────────────────
    # Pipeline = beg_inv + all future receipts within 26w
    # Safety   = opt_wos * avg_demand
    # Excess   = pipeline - demand_26w - safety
    pipeline   = effective_w1 + sum(rcv)
    safety     = (opt_wos if opt_wos > 0 else lt_wks * 2) * avg_demand_all
    excess     = pipeline - total_man_demand - safety
    if (excess > moq_min and avg_demand_all > 0
            and pipeline > 0
            and "OOS_TRUE" not in issues
            and "REORDER_NOW" not in issues):
        pipeline_wos = pipeline / avg_demand_all if avg_demand_all > 0 else 999
        if pipeline_wos > 33 or excess > moq_min:
            issues.append("OVERSTOCK")
            flags["pipeline"]     = round(pipeline)
            flags["demand_26w"]   = round(total_man_demand)
            flags["excess_units"] = round(excess)
            flags["pipeline_wos"] = round(pipeline_wos, 1)

    # ── MISMATCH (manual >> AI for multiple weeks) ────────────────────────────
    if total_ai_demand > 0 and total_man_demand > 0:
        ratio = total_man_demand / total_ai_demand
        over_weeks = sum(1 for w in range(13)
                         if ai[w] > 0 and demand[w] > ai[w] * 1.2)
        if ratio > 1.2 and over_weeks >= 3:
            issues.append("MISMATCH")
            flags["man_vs_ai_pct"]  = round((ratio - 1) * 100)
            flags["over_ai_weeks"]  = over_weeks

    if not issues:
        return None  # healthy record

    # ── Assemble result ────────────────────────────────────────────────────────
    top_issue  = min(issues, key=lambda x: ISSUE_RANK.get(x, 99))
    n_accounts = len(acct_list)
    top_accts  = sorted(acct_list, key=lambda a: a["man_26w"], reverse=True)[:3]

    return {
        "mstyle":         ms,
        "issues":         issues,
        "top_issue":      top_issue,
        "severity":       ISSUE_RANK.get(top_issue, 9),
        "n_accounts":     n_accounts,
        "top_accounts":   [a["key"] for a in top_accts],
        "beg_inv_w1":     round(w1_beg),
        "effective_w1":   round(effective_w1),
        "w2_beg":         round(w2_beg),
        "w2_rcv":         round(w2_rcv),
        "carry_est":      round(carry_est) if is_data_gap else None,
        "avg_demand_wk":  round(avg_demand, 1),
        "man_26w":        round(total_man_demand),
        "ai_26w":         round(total_ai_demand),
        "stockout_wk":    stockout_wk,
        "lt_wks":         round(lt_wks, 1),
        "opt_wos":        round(opt_wos, 1),
        "current_wos":    wos_by_week[0] if wos_by_week else 0,
        "next_rcpt":      next_rcpt,
        "moq":            round(moq),
        "pipeline":       round(effective_w1 + sum(rcv)),
        "flags":          flags,
    }


# ── Report generation ──────────────────────────────────────────────────────────
# ISSUE_SUMMARY: static one-liner for the summary table (no format variables)
ISSUE_SUMMARY = {
    "OOS_TRUE":     "Item is truly out of stock in W1 with no bridge receipt.",
    "REORDER_NOW":  "Simulated inventory hits zero within lead-time window. Order this week.",
    "DATA_GAP":     "W1 Beg Inv = 0 but carry-forward implies stock exists. Likely QB timing lag.",
    "REORDER_WATCH":"Projected stockout soon. Order within 2 weeks to stay covered.",
    "LOW_WOS":      "Current WOS below 50% of safety target. Thin buffer vs lead time.",
    "OVERSTOCK":    "Pipeline units exceed 26-week demand + safety stock. Consider cancelling POs.",
    "MISMATCH":     "Manual plan is 20%+ above AI for 3+ weeks. Planner may be over-projecting.",
}

# ISSUE_DESC: per-row action line with format variables filled from each record
ISSUE_DESC = {
    "OOS_TRUE":     "Item is truly out of stock in W1 with no bridge receipt. "
                    "Orders placed now cannot arrive for {lt_wks} weeks.",
    "REORDER_NOW":  "Simulated inventory hits zero in W{stockout_wk} -- within "
                    "your {lt_wks}-week lead time. Order this week.",
    "DATA_GAP":     "W1 Beg Inv shows 0 but carry-forward math implies ~{carry_est:,} "
                    "units. Likely QB formula timing lag, not true OOS. Verify OH.",
    "REORDER_WATCH":"Projected stockout at W{stockout_wk}. LT = {lt_wks} wks. "
                    "Order within 2 weeks to stay covered.",
    "LOW_WOS":      "Current WOS {current_wos:.1f} wks < 50% of target "
                    "{target_wos:.1f} wks. Thin buffer heading into lead time.",
    "OVERSTOCK":    "Pipeline {pipeline:,} units vs {demand_26w:,} demand. "
                    "Excess: {excess_units:,} units ({pipeline_wos:.0f} WOS). "
                    "Consider cancelling or pushing out POs.",
    "MISMATCH":     "Manual {man_vs_ai_pct}% above AI for {over_ai_weeks} of "
                    "next 13 weeks. Planner may be over-projecting; verify.",
}


def format_issue_line(rec):
    issue = rec["top_issue"]
    tmpl  = ISSUE_DESC.get(issue, issue)
    data  = dict(rec)
    data.update(rec.get("flags", {}))
    try:
        return tmpl.format(**data)
    except KeyError:
        return ISSUE_LABEL.get(issue, issue)


def write_report(results, out_path, args):
    total = len(results)
    by_issue = defaultdict(list)
    for r in results:
        by_issue[r["top_issue"]].append(r)

    lines = []
    lines.append(f"# Inventory Flow Health Scan")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
                 f"Records flagged: {total}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Issue | Count | Description |")
    lines.append("|---|---|---|")
    for issue in sorted(ISSUE_RANK, key=lambda x: ISSUE_RANK[x]):
        cnt = len(by_issue.get(issue, []))
        if cnt > 0:
            lines.append(f"| **{ISSUE_LABEL[issue]}** | {cnt} | "
                         f"{ISSUE_DESC[issue].split('.')[0]} |")
    lines.append("")

    # Detail by issue type
    for issue in sorted(ISSUE_RANK, key=lambda x: ISSUE_RANK[x]):
        recs = sorted(by_issue.get(issue, []),
                      key=lambda r: r["man_26w"], reverse=True)
        if not recs:
            continue
        lines.append(f"## {ISSUE_LABEL[issue]}  ({len(recs)} mstyles)")
        lines.append("")
        lines.append("| MStyle | Accounts | Demand 26w | Beg Inv W1 | LT | Stockout Wk | Action |")
        lines.append("|---|---|---|---|---|---|---|")

        for r in recs[:args.top]:
            acct_str  = ", ".join(r["top_accounts"][:2])
            stockout  = f"W{r['stockout_wk']}" if r.get("stockout_wk") else "--"
            action    = format_issue_line(r)[:80]
            lines.append(
                f"| {r['mstyle']} | {r['n_accounts']} | "
                f"{r['man_26w']:,} | {r['beg_inv_w1']:,} | "
                f"{r['lt_wks']:.0f}w | {stockout} | {action} |"
            )
        lines.append("")

    report = "\n".join(lines)
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"  Report -> {out_path}")
    return report


def write_csv(results, out_path):
    cols = ["mstyle", "top_issue", "issues", "n_accounts", "top_accounts",
            "beg_inv_w1", "effective_w1", "w2_beg", "w2_rcv", "carry_est",
            "avg_demand_wk", "man_26w", "ai_26w", "lt_wks", "opt_wos",
            "current_wos", "stockout_wk", "next_rcpt", "moq", "pipeline"]
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = dict(r)
            row["issues"]       = "; ".join(r.get("issues", []))
            row["top_accounts"] = ", ".join(r.get("top_accounts", []))
            w.writerow(row)
    print(f"  CSV    -> {out_path}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Inventory flow health scan")
    p.add_argument("--results",  default=str(HERE / "forecast_results.json"),
                   help="Path to forecast_results.json")
    p.add_argument("--top",      type=int, default=999,
                   help="Max records per section in report")
    p.add_argument("--issues",   nargs="+", default=None,
                   help="Filter to specific issue types")
    p.add_argument("--customer", default=None,
                   help="Filter by customer name substring")
    p.add_argument("--out-dir",  default=str(HERE.parent / "analysis"),
                   help="Output directory")
    args = p.parse_args()

    print("\n=== Inventory Flow Health Scan ===")
    print(f"  results: {args.results}")

    # 1. Load forecast results
    records = load_forecast_results(args.results)
    print(f"  {len(records)} records loaded from forecast_results.json")

    # 2. Roll up demand by mstyle
    demand_map, ai_map, acct_map = roll_up_demand_by_mstyle(
        records, customer_filter=args.customer)
    mstyles = sorted(demand_map.keys())
    print(f"  {len(mstyles)} active mstyles with demand")

    # 3. Fetch Inventory Flow
    inv_data = fetch_inv_flow(mstyles)
    missing  = [ms for ms in mstyles if ms not in inv_data]
    if missing:
        print(f"  WARNING: {len(missing)} mstyles have no Inventory Flow record")

    # 4. Analyze each mstyle
    results = []
    for ms in mstyles:
        if ms not in inv_data:
            continue
        result = analyze_mstyle(
            ms,
            inv_data[ms],
            demand_map[ms],
            ai_map[ms],
            acct_map.get(ms, []),
        )
        if result is None:
            continue
        if args.issues and result["top_issue"] not in args.issues:
            continue
        results.append(result)

    # Sort: severity first, then demand volume
    results.sort(key=lambda r: (r["severity"], -r["man_26w"]))
    print(f"  {len(results)} mstyles flagged")

    # 5. Write outputs
    out_dir = Path(args.out_dir)
    report  = write_report(results, out_dir / "prj_health_scan.md", args)
    write_csv(results, out_dir / "prj_health_scan.csv")

    # 6. Console summary
    print()
    print("  Top issues:")
    for r in results[:15]:
        tags = "+".join(r["issues"])
        print(f"    {r['mstyle']:<14} [{tags:<20}]  "
              f"man={r['man_26w']:>8,}  beg_w1={r['beg_inv_w1']:>8,}  "
              f"stockout=W{r['stockout_wk'] or '--'}")

    print()
    print(f"Done.  {len(results)} mstyles need attention.")


if __name__ == "__main__":
    main()
