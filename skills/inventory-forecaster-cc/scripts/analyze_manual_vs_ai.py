"""
analyze_manual_vs_ai.py
-----------------------
Fetches active projections from Quickbase, compares manual (planner) projections
against AI-generated projections, and produces a detailed markdown report plus
a row-level CSV for further analysis.

Designed to be run regularly (weekly or after each forecast run) to surface
patterns where planners are outperforming the algorithm — used to guide
algorithm improvements.

Usage:
    python scripts/analyze_manual_vs_ai.py [--limit N]

    --limit N   Fetch only the first N records (default: all active)

Outputs:
    analysis/manual_vs_ai_analysis.md   Full narrative report
    analysis/manual_vs_ai_stats.csv     Row-level stats (all enriched records)
    analysis/analysis_results.json      Machine-readable aggregates

QB connection:  direct REST API (no CData dependency)
"""

import sys, os, re, json, csv, argparse
from datetime import datetime
from collections import defaultdict

try:
    import requests
except ImportError:
    sys.exit("ERROR: pip install requests")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
QB_REALM  = "pim.quickbase.com"
QB_TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_BASE   = "https://api.quickbase.com/v1"
PROJ_TID  = "bpd237tvm"

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
}

# Known static FIDs on Projections table
F_RECORD_ID  = 3
F_STATUS     = 10     # Status @ Cust
F_MSTYLE     = 196
F_CUSTOMER   = 363
F_BRAND      = 197
F_INV_MGR    = 936
F_ITEM_STATUS = 374
F_L13W       = 1593   # Ord/Wk L13w # (numeric)
F_L26W       = 1591   # Ord/Wk L26w (numeric)

# AI PRJ W1-W26 (stable FIDs)
AI_FIDS = list(range(1511, 1537))   # 1511..1536

# Order history Wk1M-Wk26M (most-recent to oldest)
ORD_FIDS = [457] + list(range(464, 489))   # 457, 464..488  → 26 total

# MAN PRJ FIDs are date-stamped and roll every Monday — discovered dynamically
# from field labels matching "MM DD W1" .. "MM DD W26"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_DIR = os.path.join(SCRIPT_DIR, "..", "analysis")
os.makedirs(ANALYSIS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# QB helpers
# ---------------------------------------------------------------------------
def _qb_get(path, params=None):
    r = requests.get(f"{QB_BASE}{path}", headers=HEADERS, params=params or {}, timeout=60)
    r.raise_for_status()
    return r.json()

def _qb_post(path, body):
    r = requests.post(f"{QB_BASE}{path}", headers=HEADERS, json=body, timeout=120)
    r.raise_for_status()
    return r.json()

def fval(rec, fid):
    """Extract numeric value from a QB record cell (by string fid)."""
    v = rec.get(str(fid), {})
    if isinstance(v, dict):
        v = v.get("value")
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def sval(rec, fid):
    """Extract string value."""
    v = rec.get(str(fid), {})
    if isinstance(v, dict):
        val = v.get("value")
        if isinstance(val, dict):           # user field
            return val.get("name", "") or val.get("email", "") or ""
        return str(val) if val is not None else ""
    return str(v) if v else ""

def avg(lst):
    return sum(lst) / len(lst) if lst else None

# ---------------------------------------------------------------------------
# Step 1 — Discover rolling MAN PRJ FIDs from field labels
# ---------------------------------------------------------------------------
def discover_man_fids():
    print("Discovering MAN PRJ FIDs from field list...")
    fields = _qb_get("/fields", {"tableId": PROJ_TID})
    man_re = re.compile(r'^\d{2} \d{2} W(\d+)$')
    fid_map = {}
    for f in fields:
        m = man_re.match(f.get("label", ""))
        if m:
            wk = int(m.group(1))
            if 1 <= wk <= 26:
                # Multiple date stamps may exist; keep the highest FID (most recent)
                if wk not in fid_map or f["id"] > fid_map[wk]:
                    fid_map[wk] = f["id"]
    man_fids = [fid_map[w] for w in range(1, 27) if w in fid_map]
    print(f"  Found {len(man_fids)} MAN PRJ fields (W1-W{len(man_fids)})")
    if len(man_fids) != 26:
        print(f"  WARNING: expected 26, got {len(man_fids)} -- some weeks may be missing")
    return man_fids

# ---------------------------------------------------------------------------
# Step 2 — Fetch active projections
# ---------------------------------------------------------------------------
def fetch_projections(man_fids, limit=None):
    select_fids = (
        [F_RECORD_ID, F_STATUS, F_MSTYLE, F_CUSTOMER, F_BRAND,
         F_INV_MGR, F_ITEM_STATUS, F_L13W, F_L26W]
        + man_fids + AI_FIDS + ORD_FIDS
    )
    # Status_Cust LIKE 'A%'  →  QB starts-with operator
    where = f"{{{F_STATUS}.SW.'A'}}"

    print(f"Fetching active projections (Status @ Cust starts with 'A')...")
    all_rows = []
    skip = 0
    batch = 2000
    while True:
        body = {
            "from":    PROJ_TID,
            "select":  select_fids,
            "where":   where,
            "options": {"top": batch, "skip": skip},
        }
        resp = _qb_post("/records/query", body)
        rows = resp.get("data", [])
        all_rows.extend(rows)
        print(f"  … {len(all_rows)} records")
        if len(rows) < batch:
            break
        skip += batch
        if limit and len(all_rows) >= limit:
            all_rows = all_rows[:limit]
            break

    print(f"Total fetched: {len(all_rows)}")
    return all_rows

# ---------------------------------------------------------------------------
# Step 3 — Enrich records
# ---------------------------------------------------------------------------
def enrich(raw_rows, man_fids):
    man_labels = [f"MAN_W{i+1}" for i in range(26)]
    ai_labels  = [f"AI_W{i+1}"  for i in range(26)]
    ord_labels = [f"ORD_W{i+1}" for i in range(26)]

    enriched = []
    for rec in raw_rows:
        row = {
            "rid":         sval(rec, F_RECORD_ID),
            "status":      sval(rec, F_STATUS),
            "mstyle":      sval(rec, F_MSTYLE),
            "customer":    sval(rec, F_CUSTOMER),
            "brand":       sval(rec, F_BRAND),
            "inv_mgr":     sval(rec, F_INV_MGR),
            "item_status": sval(rec, F_ITEM_STATUS),
            "l13w_avg":    fval(rec, F_L13W),
            "l26w_avg":    fval(rec, F_L26W),
        }

        man_vals = [fval(rec, fid) for fid in man_fids]
        ai_vals  = [fval(rec, fid) for fid in AI_FIDS]
        ord_vals = [fval(rec, fid) for fid in ORD_FIDS]

        for i, lbl in enumerate(man_labels): row[lbl] = man_vals[i] if i < len(man_vals) else 0
        for i, lbl in enumerate(ai_labels):  row[lbl] = ai_vals[i]
        for i, lbl in enumerate(ord_labels): row[lbl] = ord_vals[i]

        man_total = sum(man_vals)
        ai_total  = sum(ai_vals)
        ord_total = sum(ord_vals)

        # Direction
        if ai_total == 0 and man_total == 0:
            direction = "BOTH_ZERO"
            delta_pct = 0.0
        elif ai_total == 0:
            direction = "UP"
            delta_pct = 999.0
        else:
            delta_pct = (man_total - ai_total) / ai_total * 100
            if abs(delta_pct) <= 5:
                direction = "FLAT"
            elif delta_pct > 5:
                direction = "UP"
            else:
                direction = "DOWN"

        man_zeros = sum(1 for v in man_vals if v == 0)
        ai_zeros  = sum(1 for v in ai_vals  if v == 0)
        killed    = man_zeros >= 20 and ai_total > 0

        # Front-load score: avg(MAN/AI ratio W1-W6) / avg(MAN/AI ratio W7-W26)
        wk_ratios = [(m / a) if a > 0 else None
                     for m, a in zip(man_vals, ai_vals)]
        front = [r for r in wk_ratios[:6]  if r is not None]
        back  = [r for r in wk_ratios[6:]  if r is not None]
        fl_score = (avg(front) / avg(back)) if (front and back and avg(back)) else None

        # Spike weeks: MAN > 2× AI_avg AND (MAN - AI) > AI_avg
        ai_avg_nz = ai_total / 26 if ai_total > 0 else 0
        spike_weeks = [
            wi + 1
            for wi, (m, a) in enumerate(zip(man_vals, ai_vals))
            if ai_avg_nz > 0 and m > 2 * ai_avg_nz and (m - a) > ai_avg_nz
        ]

        # L13W anchoring
        l13_basis = row["l13w_avg"] * 26
        man_vs_l13 = (man_total / l13_basis) if l13_basis > 0 and man_total > 0 else None
        ai_vs_l13  = (ai_total  / l13_basis) if l13_basis > 0 and ai_total  > 0 else None

        # L4W trend ratio
        l4w_avg = avg(ord_vals[:4]) or 0
        trend_ratio = (l4w_avg / row["l13w_avg"]) if row["l13w_avg"] > 0 else None

        # Volume tier
        l13 = row["l13w_avg"]
        vol_tier = "HIGH" if l13 >= 500 else "MED" if l13 >= 100 else "LOW" if l13 > 0 else "ZERO"

        row.update({
            "man_total":       man_total,
            "ai_total":        ai_total,
            "ord_total":       ord_total,
            "l13w_26basis":    l13_basis,
            "delta_pct":       delta_pct,
            "direction":       direction,
            "man_zeros":       man_zeros,
            "ai_zeros":        ai_zeros,
            "killed":          killed,
            "front_load_score": fl_score,
            "spike_weeks":     spike_weeks,
            "man_vs_l13":      man_vs_l13,
            "ai_vs_l13":       ai_vs_l13,
            "trend_ratio":     trend_ratio,
            "vol_tier":        vol_tier,
        })
        enriched.append(row)

    return enriched, man_labels, ai_labels, ord_labels

# ---------------------------------------------------------------------------
# Step 4 — Build report sections
# ---------------------------------------------------------------------------
def build_report(enriched, man_labels, ai_labels, n_fetched):
    lines = []
    def pr(s=""):
        lines.append(s)

    today = datetime.now().strftime("%Y-%m-%d")
    pr(f"# Manual vs AI Projection Analysis")
    pr(f"**Generated:** {today}  ")
    pr(f"**Source table:** Projections (bpd237tvm), InventoryTrack app  ")
    pr(f"**Filter:** Status @ Cust starts with 'A' (active records)  ")
    pr(f"**Sample:** {n_fetched} records")
    pr()
    pr("---")
    pr()

    # Subsets
    both_have  = [r for r in enriched if r["man_total"] > 0 and r["ai_total"] > 0]
    man_only   = [r for r in enriched if r["man_total"] > 0 and r["ai_total"] == 0]
    ai_only    = [r for r in enriched if r["man_total"] == 0 and r["ai_total"] > 0]
    both_zero  = [r for r in enriched if r["man_total"] == 0 and r["ai_total"] == 0]
    killed_lst = [r for r in enriched if r["killed"]]

    # ---- 1. Composition ----
    pr("## 1. Sample Composition")
    pr()
    pr("| Segment | Count | % |")
    pr("|---|---|---|")
    n = len(enriched)
    for label, grp in [("Both MAN and AI > 0", both_have), ("MAN only (AI = 0)", man_only),
                        ("AI only (MAN = 0)", ai_only), ("Both zero", both_zero)]:
        pr(f"| {label} | {len(grp):,} | {len(grp)/n*100:.1f}% |")
    pr()

    # ---- 2. Overall bias ----
    pr("## 2. Overall Bias")
    pr()
    if both_have:
        up  = sum(1 for r in both_have if r["direction"] == "UP")
        dn  = sum(1 for r in both_have if r["direction"] == "DOWN")
        fl  = sum(1 for r in both_have if r["direction"] == "FLAT")
        nb  = len(both_have)
        tot_man = sum(r["man_total"] for r in both_have)
        tot_ai  = sum(r["ai_total"]  for r in both_have)
        agg     = (tot_man - tot_ai) / tot_ai * 100 if tot_ai else 0
        valid_d = [r["delta_pct"] for r in both_have if 0 < r["delta_pct"] < 900]
        med_d   = sorted(valid_d)[len(valid_d)//2] if valid_d else 0
        pr(f"| Direction | Count | % |")
        pr("|---|---|---|")
        pr(f"| UP (MAN > AI by >5%) | {up:,} | {up/nb*100:.1f}% |")
        pr(f"| DOWN (MAN < AI by >5%) | {dn:,} | {dn/nb*100:.1f}% |")
        pr(f"| FLAT (within 5%) | {fl:,} | {fl/nb*100:.1f}% |")
        pr()
        pr(f"**Aggregate volume bias:** {agg:+.1f}%  "
           f"(Total MAN {tot_man:,.0f} vs AI {tot_ai:,.0f})")
        pr(f"**Median delta:** {med_d:+.1f}%")
    pr()

    # ---- 3. By customer ----
    pr("## 3. Patterns by Customer")
    pr()
    cust_grps = defaultdict(list)
    for r in both_have:
        cust_grps[r["customer"]].append(r)
    cust_stats = []
    for cust, recs in cust_grps.items():
        up = sum(1 for r in recs if r["direction"] == "UP")
        dn = sum(1 for r in recs if r["direction"] == "DOWN")
        tm = sum(r["man_total"] for r in recs)
        ta = sum(r["ai_total"]  for r in recs)
        ab = (tm - ta) / ta * 100 if ta else 0
        cust_stats.append({"cust": cust, "n": len(recs), "up": up, "dn": dn, "agg": ab,
                            "tm": tm, "ta": ta})
    cust_stats.sort(key=lambda x: x["agg"], reverse=True)
    pr("### Top 10 — Systematic UPWARD bias (AI under-projecting)")
    pr()
    pr("| Customer | N | UP | DN | Agg Bias% |")
    pr("|---|---|---|---|---|")
    for cs in cust_stats[:10]:
        pr(f"| {cs['cust']} | {cs['n']} | {cs['up']} | {cs['dn']} | {cs['agg']:+.1f}% |")
    pr()
    pr("### Top 10 — Systematic DOWNWARD bias (AI over-projecting)")
    pr()
    pr("| Customer | N | UP | DN | Agg Bias% |")
    pr("|---|---|---|---|---|")
    for cs in reversed(cust_stats[-10:]):
        pr(f"| {cs['cust']} | {cs['n']} | {cs['up']} | {cs['dn']} | {cs['agg']:+.1f}% |")
    pr()

    # ---- 4. By brand ----
    pr("## 4. Patterns by Brand")
    pr()
    brand_grps = defaultdict(list)
    for r in both_have:
        brand_grps[r["brand"]].append(r)
    brand_stats = []
    for brand, recs in brand_grps.items():
        up = sum(1 for r in recs if r["direction"] == "UP")
        dn = sum(1 for r in recs if r["direction"] == "DOWN")
        tm = sum(r["man_total"] for r in recs)
        ta = sum(r["ai_total"]  for r in recs)
        ab = (tm - ta) / ta * 100 if ta else 0
        brand_stats.append({"brand": brand, "n": len(recs), "up": up, "dn": dn, "agg": ab})
    brand_stats.sort(key=lambda x: x["agg"], reverse=True)
    pr("| Brand | N | UP | DN | Agg Bias% |")
    pr("|---|---|---|---|---|")
    for bs in brand_stats[:10]:
        pr(f"| {bs['brand']} | {bs['n']} | {bs['up']} | {bs['dn']} | {bs['agg']:+.1f}% |")
    pr("| … | | | | |")
    for bs in reversed(brand_stats[-8:]):
        pr(f"| {bs['brand']} | {bs['n']} | {bs['up']} | {bs['dn']} | {bs['agg']:+.1f}% |")
    pr()

    # ---- 5. By item status ----
    pr("## 5. Patterns by Item Status")
    pr()
    stat_grps = defaultdict(list)
    for r in both_have:
        stat_grps[r["item_status"]].append(r)
    pr("| Item Status | N | UP% | DN% | Agg Bias% |")
    pr("|---|---|---|---|---|")
    for status, recs in sorted(stat_grps.items(), key=lambda x: len(x[1]), reverse=True):
        up = sum(1 for r in recs if r["direction"] == "UP")
        dn = sum(1 for r in recs if r["direction"] == "DOWN")
        tm = sum(r["man_total"] for r in recs)
        ta = sum(r["ai_total"]  for r in recs)
        ab = (tm - ta) / ta * 100 if ta else 0
        pr(f"| {status} | {len(recs)} | {up/len(recs)*100:.1f}% | {dn/len(recs)*100:.1f}% | {ab:+.1f}% |")
    pr()

    # ---- 6. Volume tier ----
    pr("## 6. Magnitude vs Baseline (Volume Tier by L13W avg/wk)")
    pr()
    vol_grps = defaultdict(list)
    for r in both_have:
        vol_grps[r["vol_tier"]].append(r)
    pr("| Tier | Threshold | N | UP% | DN% | Avg Δ% | Agg Bias% |")
    pr("|---|---|---|---|---|---|---|")
    for tier, thresh in [("HIGH","≥500/wk"),("MED","100-499/wk"),("LOW","1-99/wk"),("ZERO","0/wk")]:
        recs = vol_grps.get(tier, [])
        if not recs: continue
        up = sum(1 for r in recs if r["direction"] == "UP")
        dn = sum(1 for r in recs if r["direction"] == "DOWN")
        vd = [r["delta_pct"] for r in recs if 0 < r["delta_pct"] < 900]
        avd = f"{avg(vd):+.1f}%" if vd else "N/A"
        tm = sum(r["man_total"] for r in recs)
        ta = sum(r["ai_total"]  for r in recs)
        ab = (tm - ta) / ta * 100 if ta else 0
        pr(f"| {tier} | {thresh} | {len(recs)} | {up/len(recs)*100:.1f}% | {dn/len(recs)*100:.1f}% | {avd} | {ab:+.1f}% |")
    pr()

    # ---- 7. Shape (week profile) ----
    pr("## 7. Week-by-Week Shape Analysis (MAN vs AI average)")
    pr()
    pr("| Wk | MAN avg | AI avg | Ratio |")
    pr("|---|---|---|---|")
    for wi in range(26):
        ml = man_labels[wi]
        al = ai_labels[wi]
        mv = avg([r[ml] for r in both_have]) or 0
        av = avg([r[al] for r in both_have]) or 0
        ratio_str = f"**{mv/av:.3f}**" if av > 0 and abs(mv/av - 1) > 0.15 else (f"{mv/av:.3f}" if av > 0 else "N/A")
        pr(f"| W{wi+1} | {mv:.1f} | {av:.1f} | {ratio_str} |")
    pr()

    # Front-load score
    fl_recs = [r for r in both_have if r["front_load_score"] is not None]
    if fl_recs:
        pr("**Front-load score by direction** (>1 = MAN heavier in W1-W6 relative to W7-W26):")
        pr()
        for dirn in ["UP","DOWN","FLAT"]:
            g = [r["front_load_score"] for r in fl_recs if r["direction"] == dirn]
            if g: pr(f"- {dirn}: {avg(g):.3f}")
    pr()

    # ---- 8. Kill patterns ----
    pr("## 8. Kill Patterns (Planner Zeros ≥ 20 Weeks, AI > 0)")
    pr()
    pr(f"**{len(killed_lst)} records killed** (planner zeroed out AI forecast)")
    pr()
    if killed_lst:
        kill_cust  = defaultdict(int)
        kill_brand = defaultdict(int)
        for r in killed_lst:
            kill_cust[r["customer"]] += 1
            kill_brand[r["brand"]]   += 1
        pr("**Top customers (kill count):**")
        pr()
        for c, cnt in sorted(kill_cust.items(), key=lambda x: x[1], reverse=True)[:10]:
            pr(f"- {c}: {cnt}")
        pr()
        pr("**Top brands (kill count):**")
        pr()
        for b, cnt in sorted(kill_brand.items(), key=lambda x: x[1], reverse=True)[:10]:
            pr(f"- {b}: {cnt}")
    pr()

    # ---- 9. Spike patterns ----
    pr("## 9. Spike Patterns")
    pr()
    spike_recs = [r for r in both_have if r["spike_weeks"]]
    pr(f"{len(spike_recs)} of {len(both_have)} records ({len(spike_recs)/len(both_have)*100:.1f}%) "
       f"have at least one spike week (MAN > 2× AI avg for that week).")
    pr()
    spike_wk_counts = defaultdict(int)
    for r in spike_recs:
        for wk in r["spike_weeks"]:
            spike_wk_counts[wk] += 1
    pr("**Most common spike weeks:**")
    pr()
    pr("| Week | Count |")
    pr("|---|---|")
    for wk, cnt in sorted(spike_wk_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        pr(f"| W{wk} | {cnt} |")
    pr()

    # ---- 10. L13W anchoring ----
    pr("## 10. L13W Anchoring")
    pr()
    pr("| Direction | N | avg MAN / L13W-basis | avg AI / L13W-basis |")
    pr("|---|---|---|---|")
    for dirn in ["UP","DOWN","FLAT"]:
        g = [r for r in both_have if r["direction"] == dirn and r["man_vs_l13"] is not None]
        if not g: continue
        mv = avg([r["man_vs_l13"] for r in g])
        av = avg([r["ai_vs_l13"]  for r in g if r["ai_vs_l13"] is not None])
        mv_str = f"{mv:.3f}x" if mv else "N/A"
        av_str = f"{av:.3f}x" if av else "N/A"
        pr(f"| {dirn} | {len(g)} | {mv_str} | {av_str} |")
    pr()

    # ---- 11. By Inventory Manager ----
    pr("## 11. By Inventory Manager")
    pr()
    mgr_grps = defaultdict(list)
    for r in both_have:
        mgr_grps[r["inv_mgr"]].append(r)
    mgr_stats = []
    for mgr, recs in mgr_grps.items():
        up = sum(1 for r in recs if r["direction"] == "UP")
        dn = sum(1 for r in recs if r["direction"] == "DOWN")
        tm = sum(r["man_total"] for r in recs)
        ta = sum(r["ai_total"]  for r in recs)
        ab = (tm - ta) / ta * 100 if ta else 0
        mgr_stats.append({"mgr": mgr or "(unknown)", "n": len(recs),
                           "up": up, "dn": dn, "agg": ab})
    mgr_stats.sort(key=lambda x: x["n"], reverse=True)
    pr("| Manager | N | UP% | DN% | Agg Bias% |")
    pr("|---|---|---|---|---|")
    for ms in mgr_stats[:15]:
        pr(f"| {ms['mgr']} | {ms['n']} | {ms['up']/ms['n']*100:.1f}% | "
           f"{ms['dn']/ms['n']*100:.1f}% | {ms['agg']:+.1f}% |")
    pr()

    # ---- 12. Algorithm hypotheses ----
    pr("## 12. Algorithm Improvement Hypotheses")
    pr()
    pr("Based on the patterns above. Review and confirm before implementing.")
    pr()
    pr("| Priority | Finding | Proposed Change | Effort |")
    pr("|---|---|---|---|")

    # Generate hypotheses from computed stats
    hyps = []

    # Spike / cadence
    top_spike_wks = sorted(spike_wk_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    if top_spike_wks:
        wk_list = ", ".join(f"W{w}" for w, _ in top_spike_wks)
        hyps.append(("Order-cadence modeling",
                     f"Spike weeks {wk_list} suggest 4-week order cycle encoding by planners",
                     "Model per-customer order cadence; project in cycle-aligned buckets not flat weekly",
                     "Medium"))

    # Back-half skepticism
    dn_fl = [r["front_load_score"] for r in both_have if r["direction"] == "DOWN" and r["front_load_score"]]
    if dn_fl and avg(dn_fl) > 1.05:
        hyps.append(("Horizon confidence decay",
                     f"DOWN records front-load score {avg(dn_fl):.2f} — planners cut AI back-half more than near-term",
                     "Apply exponential damping beyond W8 for items with no strong seasonal signal",
                     "Low"))

    # L4W/L13W trend
    dn_recs_ai = [r for r in both_have if r["direction"] == "DOWN" and r["ai_vs_l13"]]
    if dn_recs_ai:
        ai_ratio = avg([r["ai_vs_l13"] for r in dn_recs_ai])
        if ai_ratio and ai_ratio > 1.3:
            hyps.append(("L4W/L13W trend signal",
                         f"AI projects {ai_ratio:.2f}× L13W on DOWN records — not picking up recent decline",
                         "When L4W/L13W < 0.8, anchor base = L4W×0.6 + L13W×0.4",
                         "Low"))

    # Kill / channel
    if killed_lst:
        top_kill_cust = sorted(dict(
            (c, sum(1 for r in killed_lst if r["customer"] == c))
            for c in {r["customer"] for r in killed_lst}
        ).items(), key=lambda x: x[1], reverse=True)[:3]
        cust_names = ", ".join(c for c, _ in top_kill_cust)
        hyps.append(("Channel type suppression",
                     f"Planners kill 100% of AI forecasts for off-price/closeout accounts ({cust_names}, …)",
                     "Add channel-type flag; suppress AI for closeout/opportunistic accounts",
                     "Low"))

    # Zero velocity
    zero_recs = [r for r in both_have if r["vol_tier"] == "ZERO"]
    if zero_recs:
        hyps.append(("Zero-velocity suppression",
                     f"{len(zero_recs)} records with L13W=0 still receive AI projections",
                     "If both L4W and L13W = 0, AI projection = 0 unless launch/POG trigger flag set",
                     "Low"))

    # Multi-pack
    mp_recs = [r for r in both_have if "multi" in r["item_status"].lower()]
    if mp_recs:
        vd = [r["delta_pct"] for r in mp_recs if 0 < r["delta_pct"] < 900]
        avd = avg(vd)
        if avd and avd > 50:
            hyps.append(("Multi-pack unit conversion",
                         f"Multi-Pk Replen items avg delta {avd:.0f}% — AI under-projects",
                         "Derive demand from parent single-pack L13W ÷ units-per-pack instead of sparse multi-pack history",
                         "Medium"))

    for i, (name, finding, proposed, effort) in enumerate(hyps, 1):
        pr(f"| {i}. {name} | {finding} | {proposed} | {effort} |")
    pr()
    pr("---")
    pr(f"*Report generated by `scripts/analyze_manual_vs_ai.py` on {today}*")

    return "\n".join(lines), cust_stats, brand_stats, mgr_stats, spike_wk_counts

# ---------------------------------------------------------------------------
# Step 5 — Save outputs
# ---------------------------------------------------------------------------
def save_csv(enriched, man_labels, ai_labels, ord_labels):
    path = os.path.join(ANALYSIS_DIR, "manual_vs_ai_stats.csv")
    scalar_fields = ["rid","mstyle","customer","brand","inv_mgr","item_status","vol_tier",
                     "l13w_avg","l26w_avg","man_total","ai_total","ord_total",
                     "l13w_26basis","delta_pct","direction","man_zeros","ai_zeros",
                     "killed","front_load_score","man_vs_l13","ai_vs_l13","trend_ratio"]
    fieldnames = scalar_fields + man_labels + ai_labels + ord_labels
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in enriched:
            out = {k: r.get(k, "") for k in fieldnames}
            out["killed"]           = str(r.get("killed", False))
            out["front_load_score"] = f"{r['front_load_score']:.4f}" if r.get("front_load_score") else ""
            out["spike_weeks_str"]  = "|".join(str(w) for w in r.get("spike_weeks", []))
            w.writerow(out)
    print(f"CSV saved → {path}")
    return path

def save_json(data, name):
    path = os.path.join(ANALYSIS_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"JSON saved → {path}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Analyze MAN vs AI projections")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit records fetched (default: all active)")
    args = parser.parse_args()

    man_fids   = discover_man_fids()
    raw_rows   = fetch_projections(man_fids, limit=args.limit)
    enriched, man_labels, ai_labels, ord_labels = enrich(raw_rows, man_fids)

    report_md, cust_stats, brand_stats, mgr_stats, spike_wk_counts = build_report(
        enriched, man_labels, ai_labels, len(raw_rows)
    )

    # Save markdown report
    md_path = os.path.join(ANALYSIS_DIR, "manual_vs_ai_analysis.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"Report saved → {md_path}")

    save_csv(enriched, man_labels, ai_labels, ord_labels)

    both_have = [r for r in enriched if r["man_total"] > 0 and r["ai_total"] > 0]
    save_json({
        "generated": datetime.now().isoformat(),
        "n_total":     len(enriched),
        "n_both_have": len(both_have),
        "n_killed":    sum(1 for r in enriched if r["killed"]),
        "cust_stats":  cust_stats[:30],
        "brand_stats": brand_stats[:30],
        "mgr_stats":   mgr_stats,
        "spike_wk_counts": {str(k): v for k, v in spike_wk_counts.items()},
    }, "analysis_results.json")

    print("\nDone. Open analysis/manual_vs_ai_analysis.md for the full report.")

if __name__ == "__main__":
    main()
