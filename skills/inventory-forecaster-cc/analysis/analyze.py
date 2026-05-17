import json
import csv
import os
import math
from collections import defaultdict

out_dir = r"C:\Users\steven\.claude\skills\inventory-forecaster-cc\analysis"
raw_path = os.path.join(out_dir, "raw_projections.json")

with open(raw_path) as f:
    raw = json.load(f)

records = raw["records"]
MAN_FIDS = [str(x) for x in raw["man_fids"]]
AI_FIDS  = [str(x) for x in raw["ai_fids"]]
ORD_FIDS = [str(x) for x in raw["ord_fids"]]

MAN_LABELS = [f"MAN_W{i+1}" for i in range(26)]
AI_LABELS  = [f"AI_W{i+1}"  for i in range(26)]
ORD_LABELS = [f"ORD_W{i+1}" for i in range(26)]

def fval(rec, fid):
    v = rec.get(fid, {}).get("value")
    if v is None:
        return 0.0
    try:
        return float(v)
    except:
        return 0.0

def user_name(rec, fid):
    v = rec.get(fid, {}).get("value")
    if isinstance(v, dict):
        return v.get("name", "")
    return str(v) if v else ""

parsed = []
for rec in records:
    row = {}
    row["rid"]         = fval(rec, "3")
    row["status"]      = rec.get("10", {}).get("value", "")
    row["mstyle"]      = rec.get("196", {}).get("value", "")
    row["customer"]    = rec.get("363", {}).get("value", "")
    row["brand"]       = rec.get("197", {}).get("value", "")
    row["inv_mgr"]     = user_name(rec, "936")
    row["item_status"] = rec.get("374", {}).get("value", "")
    row["l13w_avg"]    = fval(rec, "1593")
    row["l26w_avg"]    = fval(rec, "1591")

    for i, fid in enumerate(MAN_FIDS):
        row[MAN_LABELS[i]] = fval(rec, fid)
    for i, fid in enumerate(AI_FIDS):
        row[AI_LABELS[i]] = fval(rec, fid)
    for i, fid in enumerate(ORD_FIDS):
        row[ORD_LABELS[i]] = fval(rec, fid)

    parsed.append(row)

print(f"Parsed {len(parsed)} records")

def avg(lst):
    return sum(lst)/len(lst) if lst else None

enriched = []
for row in parsed:
    man_vals = [row[l] for l in MAN_LABELS]
    ai_vals  = [row[l] for l in AI_LABELS]
    ord_vals = [row[l] for l in ORD_LABELS]

    man_total = sum(man_vals)
    ai_total  = sum(ai_vals)
    ord_total = sum(ord_vals)

    l13w_26basis = row["l13w_avg"] * 26
    l26w_26basis = row["l26w_avg"] * 26

    if ai_total > 0:
        delta_pct = (man_total - ai_total) / ai_total * 100
    elif man_total > 0:
        delta_pct = 999.0
    else:
        delta_pct = 0.0

    if ai_total == 0 and man_total == 0:
        direction = "BOTH_ZERO"
    elif abs(delta_pct) <= 5:
        direction = "FLAT"
    elif delta_pct > 5:
        direction = "UP"
    else:
        direction = "DOWN"

    man_zeros = sum(1 for v in man_vals if v == 0)
    ai_zeros  = sum(1 for v in ai_vals  if v == 0)

    killed = (man_zeros >= 20) and (ai_total > 0)

    wk_ratios = []
    for m, a in zip(man_vals, ai_vals):
        if a > 0:
            wk_ratios.append(m / a)
        else:
            wk_ratios.append(None)

    front_ratios = [r for r in wk_ratios[:6] if r is not None]
    back_ratios  = [r for r in wk_ratios[6:]  if r is not None]
    front_avg_v  = avg(front_ratios)
    back_avg_v   = avg(back_ratios)

    if front_avg_v is not None and back_avg_v is not None and back_avg_v > 0:
        front_load_score = front_avg_v / back_avg_v
    else:
        front_load_score = None

    ai_avg_nz = ai_total / 26 if ai_total > 0 else 0
    spike_weeks = []
    for wi, (m, a) in enumerate(zip(man_vals, ai_vals)):
        if ai_avg_nz > 0 and m > 2 * ai_avg_nz and (m - (a if a > 0 else 0)) > ai_avg_nz:
            spike_weeks.append(wi + 1)

    if l13w_26basis > 0 and man_total > 0:
        man_vs_l13 = man_total / l13w_26basis
        ai_vs_l13  = ai_total  / l13w_26basis
    else:
        man_vs_l13 = None
        ai_vs_l13  = None

    l13 = row["l13w_avg"]
    if l13 >= 500:
        vol_tier = "HIGH"
    elif l13 >= 100:
        vol_tier = "MED"
    elif l13 > 0:
        vol_tier = "LOW"
    else:
        vol_tier = "ZERO"

    r = dict(row)
    r.update({
        "man_total": man_total,
        "ai_total": ai_total,
        "ord_total": ord_total,
        "l13w_26basis": l13w_26basis,
        "l26w_26basis": l26w_26basis,
        "delta_pct": delta_pct,
        "direction": direction,
        "man_zeros": man_zeros,
        "ai_zeros": ai_zeros,
        "killed": killed,
        "front_load_score": front_load_score,
        "spike_weeks": spike_weeks,
        "man_vs_l13": man_vs_l13,
        "ai_vs_l13": ai_vs_l13,
        "vol_tier": vol_tier,
    })
    enriched.append(r)

print(f"Enriched {len(enriched)} records")

comparable  = [r for r in enriched if r["man_total"] > 0 or r["ai_total"] > 0]
both_have   = [r for r in comparable if r["man_total"] > 0 and r["ai_total"] > 0]
man_only    = [r for r in comparable if r["man_total"] > 0 and r["ai_total"] == 0]
ai_only     = [r for r in comparable if r["man_total"] == 0 and r["ai_total"] > 0]
both_zero   = [r for r in enriched   if r["man_total"] == 0 and r["ai_total"] == 0]

output_lines = []

def pr(line=""):
    print(line)
    output_lines.append(line)

pr("=== COMPOSITION ===")
pr(f"Total active records: {len(enriched)}")
pr(f"Both MAN and AI > 0: {len(both_have)}")
pr(f"MAN only (AI=0): {len(man_only)}")
pr(f"AI only (MAN=0, killed by planner): {len(ai_only)}")
pr(f"Both zero: {len(both_zero)}")

pr()
pr("=== OVERALL BIAS ===")
for grp_name, grp in [("All comparable", comparable), ("Both have projections", both_have)]:
    if not grp:
        continue
    up_ct   = sum(1 for r in grp if r["direction"] == "UP")
    dn_ct   = sum(1 for r in grp if r["direction"] == "DOWN")
    flat_ct = sum(1 for r in grp if r["direction"] == "FLAT")

    valid_deltas = [r["delta_pct"] for r in grp if r["delta_pct"] != 999 and r["ai_total"] > 0]
    avg_delta    = avg(valid_deltas)
    sorted_d     = sorted(valid_deltas)
    median_d     = sorted_d[len(sorted_d)//2] if sorted_d else 0

    total_man = sum(r["man_total"] for r in grp)
    total_ai  = sum(r["ai_total"]  for r in grp)
    agg_bias  = (total_man - total_ai) / total_ai * 100 if total_ai > 0 else 0

    pr(f"\n{grp_name} (n={len(grp)}):")
    pr(f"  Direction: UP={up_ct} ({up_ct/len(grp)*100:.1f}%), DOWN={dn_ct} ({dn_ct/len(grp)*100:.1f}%), FLAT={flat_ct} ({flat_ct/len(grp)*100:.1f}%)")
    pr(f"  Avg delta_pct:    {avg_delta:+.1f}%" if avg_delta is not None else "  Avg delta_pct: N/A")
    pr(f"  Median delta_pct: {median_d:+.1f}%")
    pr(f"  Aggregate MAN vs AI bias: {agg_bias:+.1f}%")
    pr(f"  Total MAN units: {total_man:,.0f}")
    pr(f"  Total AI  units: {total_ai:,.0f}")

pr()
pr("=== BY CUSTOMER ===")
cust_groups = defaultdict(list)
for r in both_have:
    cust_groups[r["customer"]].append(r)

cust_stats = []
for cust, recs in cust_groups.items():
    up = sum(1 for r in recs if r["direction"] == "UP")
    dn = sum(1 for r in recs if r["direction"] == "DOWN")
    fl = sum(1 for r in recs if r["direction"] == "FLAT")
    total_man = sum(r["man_total"] for r in recs)
    total_ai  = sum(r["ai_total"]  for r in recs)
    agg_bias  = (total_man - total_ai) / total_ai * 100 if total_ai > 0 else 0
    avg_delta = avg([r["delta_pct"] for r in recs if r["delta_pct"] != 999])
    cust_stats.append({
        "customer": cust, "n": len(recs), "up": up, "dn": dn, "fl": fl,
        "total_man": total_man, "total_ai": total_ai,
        "agg_bias": agg_bias, "avg_delta": avg_delta if avg_delta else 0
    })

cust_stats.sort(key=lambda x: abs(x["agg_bias"]), reverse=True)
pr(f"{'Customer':<45} {'N':>5} {'UP':>5} {'DN':>5} {'FL':>5} {'AggBias%':>10}")
pr("-" * 80)
for cs in cust_stats[:25]:
    pr(f"{cs['customer'][:44]:<45} {cs['n']:>5} {cs['up']:>5} {cs['dn']:>5} {cs['fl']:>5} {cs['agg_bias']:>+10.1f}")

pr()
pr("=== BY BRAND ===")
brand_groups = defaultdict(list)
for r in both_have:
    brand_groups[r["brand"]].append(r)

brand_stats = []
for brand, recs in brand_groups.items():
    up = sum(1 for r in recs if r["direction"] == "UP")
    dn = sum(1 for r in recs if r["direction"] == "DOWN")
    total_man = sum(r["man_total"] for r in recs)
    total_ai  = sum(r["ai_total"]  for r in recs)
    agg_bias  = (total_man - total_ai) / total_ai * 100 if total_ai > 0 else 0
    brand_stats.append({"brand": brand, "n": len(recs), "up": up, "dn": dn,
                         "total_man": total_man, "total_ai": total_ai, "agg_bias": agg_bias})

brand_stats.sort(key=lambda x: abs(x["agg_bias"]), reverse=True)
pr(f"{'Brand':<40} {'N':>5} {'UP':>5} {'DN':>5} {'AggBias%':>10}")
pr("-" * 65)
for bs in brand_stats[:25]:
    pr(f"{bs['brand'][:39]:<40} {bs['n']:>5} {bs['up']:>5} {bs['dn']:>5} {bs['agg_bias']:>+10.1f}")

pr()
pr("=== BY ITEM STATUS ===")
status_groups = defaultdict(list)
for r in both_have:
    status_groups[r["item_status"]].append(r)

for status, recs in sorted(status_groups.items(), key=lambda x: len(x[1]), reverse=True):
    up = sum(1 for r in recs if r["direction"] == "UP")
    dn = sum(1 for r in recs if r["direction"] == "DOWN")
    fl = sum(1 for r in recs if r["direction"] == "FLAT")
    total_man = sum(r["man_total"] for r in recs)
    total_ai  = sum(r["ai_total"]  for r in recs)
    agg_bias  = (total_man - total_ai) / total_ai * 100 if total_ai > 0 else 0
    avg_delta = avg([r["delta_pct"] for r in recs if r["delta_pct"] != 999])
    pr(f"\n{status} (n={len(recs)}):")
    pr(f"  UP={up} ({up/len(recs)*100:.1f}%), DN={dn} ({dn/len(recs)*100:.1f}%), FLAT={fl} ({fl/len(recs)*100:.1f}%)")
    pr(f"  Aggregate bias: {agg_bias:+.1f}%,  Avg delta: {avg_delta:+.1f}%" if avg_delta else f"  Aggregate bias: {agg_bias:+.1f}%")

pr()
pr("=== MAGNITUDE VS BASELINE (Volume tier by L13W avg/wk) ===")
vol_groups = defaultdict(list)
for r in both_have:
    vol_groups[r["vol_tier"]].append(r)

for tier in ["HIGH", "MED", "LOW", "ZERO"]:
    recs = vol_groups.get(tier, [])
    if not recs:
        continue
    up = sum(1 for r in recs if r["direction"] == "UP")
    dn = sum(1 for r in recs if r["direction"] == "DOWN")
    avg_delta = avg([r["delta_pct"] for r in recs if r["delta_pct"] != 999])
    total_man = sum(r["man_total"] for r in recs)
    total_ai  = sum(r["ai_total"]  for r in recs)
    agg_bias  = (total_man - total_ai) / total_ai * 100 if total_ai > 0 else 0
    threshold = ">=500 units/wk" if tier == "HIGH" else "100-499 units/wk" if tier == "MED" else "1-99 units/wk"
    pr(f"\n{tier} vol ({threshold}): n={len(recs)}")
    pr(f"  UP={up} ({up/len(recs)*100:.1f}%), DN={dn} ({dn/len(recs)*100:.1f}%)")
    pr(f"  Avg delta_pct: {avg_delta:+.1f}%,  Aggregate bias: {agg_bias:+.1f}%" if avg_delta else f"  Aggregate bias: {agg_bias:+.1f}%")

pr()
pr("=== SHAPE ANALYSIS: Week-by-week MAN vs AI averages ===")
wk_man_avgs = []
wk_ai_avgs  = []
for wi in range(26):
    ml = MAN_LABELS[wi]
    al = AI_LABELS[wi]
    man_vals = [r[ml] for r in both_have if r["ai_total"] > 0]
    ai_vals  = [r[al] for r in both_have if r["ai_total"] > 0]
    wk_man_avgs.append(avg(man_vals) or 0)
    wk_ai_avgs.append(avg(ai_vals)   or 0)

pr(f"\nAvg per-week MAN vs AI (both_have, n={len(both_have)}):")
pr(f"{'Wk':<5} {'MAN_avg':>10} {'AI_avg':>10} {'Ratio':>8}")
for wi in range(26):
    ratio = wk_man_avgs[wi] / wk_ai_avgs[wi] if wk_ai_avgs[wi] > 0 else None
    ratio_str = f"{ratio:.3f}" if ratio is not None else "  N/A"
    pr(f"W{wi+1:<4} {wk_man_avgs[wi]:>10.1f} {wk_ai_avgs[wi]:>10.1f} {ratio_str:>8}")

pr()
pr("=== FRONT-LOAD SCORE (W1-W6 vs W7-W26 ratio, >1 = front-heavy) ===")
fl_records = [r for r in both_have if r["front_load_score"] is not None]
up_fl   = [r["front_load_score"] for r in fl_records if r["direction"] == "UP"]
dn_fl   = [r["front_load_score"] for r in fl_records if r["direction"] == "DOWN"]
flat_fl = [r["front_load_score"] for r in fl_records if r["direction"] == "FLAT"]

pr(f"n={len(fl_records)}")
pr(f"  UP   records: avg FL score = {avg(up_fl):.3f}" if up_fl else "  UP:   no data")
pr(f"  DOWN records: avg FL score = {avg(dn_fl):.3f}" if dn_fl else "  DOWN: no data")
pr(f"  FLAT records: avg FL score = {avg(flat_fl):.3f}" if flat_fl else "  FLAT: no data")

pr()
pr("=== KILL PATTERNS (MAN zeros >= 20 weeks but AI > 0) ===")
killed = [r for r in enriched if r["killed"]]
pr(f"Killed records: {len(killed)}")
if killed:
    kill_status = defaultdict(int)
    kill_cust   = defaultdict(int)
    kill_brand  = defaultdict(int)
    for r in killed:
        kill_status[r["item_status"]] += 1
        kill_cust[r["customer"]] += 1
        kill_brand[r["brand"]] += 1
    pr("Top item statuses:")
    for s, cnt in sorted(kill_status.items(), key=lambda x: x[1], reverse=True)[:10]:
        pr(f"  {s}: {cnt}")
    pr("Top customers:")
    for c, cnt in sorted(kill_cust.items(), key=lambda x: x[1], reverse=True)[:10]:
        pr(f"  {c}: {cnt}")
    pr("Top brands:")
    for b, cnt in sorted(kill_brand.items(), key=lambda x: x[1], reverse=True)[:10]:
        pr(f"  {b}: {cnt}")

pr()
pr("=== SPIKE PATTERNS ===")
spike_recs = [r for r in both_have if r["spike_weeks"]]
pr(f"Records with spike weeks: {len(spike_recs)}")
spike_wk_counts = defaultdict(int)
for r in spike_recs:
    for wk in r["spike_weeks"]:
        spike_wk_counts[wk] += 1
pr("Spike week distribution (most common first):")
for wk, cnt in sorted(spike_wk_counts.items(), key=lambda x: x[1], reverse=True)[:15]:
    pr(f"  W{wk}: {cnt}")

pr()
pr("=== L13W ANCHORING ===")
up_recs = [r for r in both_have if r["direction"] == "UP" and r["man_vs_l13"] is not None]
dn_recs = [r for r in both_have if r["direction"] == "DOWN" and r["man_vs_l13"] is not None]
fl_recs = [r for r in both_have if r["direction"] == "FLAT" and r["man_vs_l13"] is not None]

if up_recs:
    avg_man_l13_up = avg([r["man_vs_l13"] for r in up_recs])
    avg_ai_l13_up  = avg([r["ai_vs_l13"]  for r in up_recs if r["ai_vs_l13"] is not None])
    pr(f"UP   records (n={len(up_recs)}): avg MAN/L13W={avg_man_l13_up:.3f}, avg AI/L13W={avg_ai_l13_up:.3f}")
if dn_recs:
    avg_man_l13_dn = avg([r["man_vs_l13"] for r in dn_recs])
    avg_ai_l13_dn  = avg([r["ai_vs_l13"]  for r in dn_recs if r["ai_vs_l13"] is not None])
    pr(f"DOWN records (n={len(dn_recs)}): avg MAN/L13W={avg_man_l13_dn:.3f}, avg AI/L13W={avg_ai_l13_dn:.3f}")
if fl_recs:
    avg_man_l13_fl = avg([r["man_vs_l13"] for r in fl_recs])
    avg_ai_l13_fl  = avg([r["ai_vs_l13"]  for r in fl_recs if r["ai_vs_l13"] is not None])
    pr(f"FLAT records (n={len(fl_recs)}): avg MAN/L13W={avg_man_l13_fl:.3f}, avg AI/L13W={avg_ai_l13_fl:.3f}")

pr()
pr("=== BY INVENTORY MANAGER ===")
mgr_groups = defaultdict(list)
for r in both_have:
    mgr_groups[r["inv_mgr"]].append(r)

mgr_stats = []
for mgr, recs in mgr_groups.items():
    up = sum(1 for r in recs if r["direction"] == "UP")
    dn = sum(1 for r in recs if r["direction"] == "DOWN")
    fl = sum(1 for r in recs if r["direction"] == "FLAT")
    avg_delta = avg([r["delta_pct"] for r in recs if r["delta_pct"] != 999])
    total_man = sum(r["man_total"] for r in recs)
    total_ai  = sum(r["ai_total"]  for r in recs)
    agg_bias  = (total_man - total_ai) / total_ai * 100 if total_ai > 0 else 0
    mgr_stats.append({"mgr": mgr, "n": len(recs), "up": up, "dn": dn, "fl": fl,
                       "avg_delta": avg_delta if avg_delta else 0, "agg_bias": agg_bias})

mgr_stats.sort(key=lambda x: x["n"], reverse=True)
pr(f"{'Manager':<30} {'N':>5} {'UP%':>7} {'DN%':>7} {'AggBias%':>10}")
pr("-" * 60)
for ms in mgr_stats[:15]:
    up_pct = ms["up"]/ms["n"]*100
    dn_pct = ms["dn"]/ms["n"]*100
    pr(f"{ms['mgr'][:29]:<30} {ms['n']:>5} {up_pct:>7.1f} {dn_pct:>7.1f} {ms['agg_bias']:>+10.1f}")

# Save CSV summary
csv_path = os.path.join(out_dir, "manual_vs_ai_stats.csv")
fieldnames = ["rid", "mstyle", "customer", "brand", "inv_mgr", "item_status", "vol_tier",
              "l13w_avg", "l26w_avg", "man_total", "ai_total", "ord_total",
              "l13w_26basis", "delta_pct", "direction", "man_zeros", "ai_zeros",
              "killed", "front_load_score", "man_vs_l13", "ai_vs_l13",
              "spike_weeks"] + MAN_LABELS + AI_LABELS + ORD_LABELS

with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in enriched:
        row_out = {k: r.get(k, "") for k in fieldnames}
        row_out["spike_weeks"] = "|".join(str(w) for w in r.get("spike_weeks", []))
        row_out["killed"] = str(r.get("killed", False))
        row_out["front_load_score"] = f"{r['front_load_score']:.4f}" if r.get("front_load_score") else ""
        row_out["delta_pct"] = f"{r['delta_pct']:.2f}" if r.get("delta_pct") not in (None, 999.0) else str(r.get("delta_pct", ""))
        writer.writerow(row_out)

pr(f"\nCSV saved to {csv_path}")
pr("Analysis complete.")

# Save analysis results for report
analysis_path = os.path.join(out_dir, "analysis_results.json")
analysis_data = {
    "n_total": len(enriched),
    "n_both_have": len(both_have),
    "n_man_only": len(man_only),
    "n_ai_only": len(ai_only),
    "n_both_zero": len(both_zero),
    "wk_man_avgs": wk_man_avgs,
    "wk_ai_avgs": wk_ai_avgs,
    "cust_stats": cust_stats[:30],
    "brand_stats": brand_stats[:30],
    "killed_count": len(killed),
    "spike_wk_counts": {str(k): v for k, v in spike_wk_counts.items()},
    "mgr_stats": mgr_stats,
}
with open(analysis_path, "w") as f:
    json.dump(analysis_data, f, indent=2, default=str)
pr(f"Analysis JSON saved to {analysis_path}")
