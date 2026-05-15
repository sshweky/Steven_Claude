"""
Diagnostic: records where AI weekly volume < 100 AND |AI vs Manual| >= 50%.

Pulls full history + manual + AI for each flagged record, computes diagnostic
features, classifies failure modes, and writes a summary report.
"""
import sys, json, os
from collections import Counter, defaultdict
sys.path.insert(0, 'scripts')
from inventory_forecaster import cdata_query, _make_prj_cols

PRJ_COLS = _make_prj_cols()
AI_COLS   = [f"AI_PRJ_W{w}" for w in range(1, 27)]
# 52-week order history, oldest -> newest
ORD_HIST_COLS = [f"Ord_LW_{i}" for i in range(51, 0, -1)] + ["Ord_LW"]

SEL = (
    "[Acct_MStyle_Key_],[Cust_Name],[Description],[Status_Cust],[Mstyle],"
    + ",".join(f"[{c}]" for c in PRJ_COLS) + ","
    + ",".join(f"[{c}]" for c in AI_COLS)  + ","
    + ",".join(f"[{c}]" for c in ORD_HIST_COLS)
)
SQL = (f"SELECT {SEL} FROM [Quickbase1].[InventoryTrack].[Projections] "
       f"WHERE [Status_Cust] LIKE 'A%'")

print("Pulling full projections + 52w history ...")
rows = cdata_query(SQL, "lowvol_highvar pull")
print(f"  got {len(rows)} records")

def f(v):
    try: return float(v or 0)
    except: return 0.0

flagged = []
for r in rows:
    manual = [f(r.get(c)) for c in PRJ_COLS]
    ai     = [f(r.get(c)) for c in AI_COLS]
    hist   = [f(r.get(c)) for c in ORD_HIST_COLS]
    M, A   = sum(manual), sum(ai)
    ai_wk  = A / 26.0
    if M <= 0:                       # can't % with no denom
        continue
    var_pct = (A - M) / M * 100.0
    if ai_wk >= 100:                 # volume filter
        continue
    if abs(var_pct) < 50:            # variance filter
        continue

    L4   = hist[-4:]
    L13  = hist[-13:]
    L26  = hist[-26:]
    L52  = hist
    nz13 = [v for v in L13 if v > 0]
    nz26 = [v for v in L26 if v > 0]
    nz52 = [v for v in L52 if v > 0]

    # longest zero run in most recent weeks
    trail_zero = 0
    for v in reversed(hist):
        if v == 0: trail_zero += 1
        else: break

    # order cadence gaps
    idx = [i for i,v in enumerate(hist) if v > 0]
    gaps = [idx[i+1]-idx[i] for i in range(len(idx)-1)]
    med_gap = sorted(gaps)[len(gaps)//2] if gaps else 0

    manual_nz = [v for v in manual if v > 0]
    ai_nz     = [v for v in ai     if v > 0]

    # shape indicators
    manual_front = sum(manual[:13])
    manual_back  = sum(manual[13:])
    manual_week_range = (max(manual_nz) if manual_nz else 0) - (min(manual_nz) if manual_nz else 0)

    # classify likely model by L26 non-zero rate (mirrors classifier in forecaster)
    nz_rate26 = len(nz26) / 26.0
    if len(nz13) == 0:
        likely_model = "Inactive"
    elif nz_rate26 >= 0.50:
        likely_model = "Seasonal/HW"
    elif nz_rate26 >= 0.25:
        likely_model = "Croston"
    else:
        likely_model = "Sparse"

    flagged.append({
        "key":      r.get("Acct_MStyle_Key_",""),
        "cust":     (r.get("Cust_Name") or "").split("<")[0][:40],
        "desc":     (r.get("Description") or "")[:60],
        "status":   r.get("Status_Cust",""),
        "manual_total": M,
        "ai_total":     A,
        "ai_wk":        ai_wk,
        "var_pct":      var_pct,
        "direction":    "AI under" if var_pct < 0 else "AI over",
        "L13_sum":   sum(L13), "L13_nz": len(nz13),
        "L26_sum":   sum(L26), "L26_nz": len(nz26),
        "L52_sum":   sum(L52), "L52_nz": len(nz52),
        "L13_nz_avg":  (sum(nz13)/len(nz13)) if nz13 else 0,
        "L26_nz_avg":  (sum(nz26)/len(nz26)) if nz26 else 0,
        "L52_nz_avg":  (sum(nz52)/len(nz52)) if nz52 else 0,
        "trail_zero":  trail_zero,
        "med_gap":     med_gap,
        "likely_model":likely_model,
        "manual_nz_ct":len(manual_nz),
        "ai_nz_ct":    len(ai_nz),
        "manual_front_half": manual_front,
        "manual_back_half":  manual_back,
        "manual_nz_avg":  (sum(manual_nz)/len(manual_nz)) if manual_nz else 0,
        "ai_nz_avg":      (sum(ai_nz)/len(ai_nz)) if ai_nz else 0,
    })

print(f"  {len(flagged)} records flagged (AI<100/wk AND |var|≥50%)")

# -------- Failure-mode classification --------
def classify(rec):
    """Return (primary_mode, tags_list)."""
    tags = []
    ai_under = rec["var_pct"] < 0
    if ai_under: tags.append("AI_UNDER")
    else:        tags.append("AI_OVER")

    # AI forecasts ~zero but planner expects real demand
    if rec["ai_total"] < rec["manual_total"] * 0.25:
        tags.append("AI_NEAR_ZERO")

    # Long trailing gap in history but planner still expects orders
    if rec["trail_zero"] >= 8 and rec["manual_total"] > 0:
        tags.append("LONG_GAP_WITH_PLAN")

    # History is truly zero in L13 — likely Inactive classifier
    if rec["L13_nz"] == 0 and rec["manual_total"] > 0:
        tags.append("INACTIVE_PATH")

    # Intermittent cadence ≥ 6 weeks but planner has a near-flat book
    if rec["med_gap"] >= 6 and rec["manual_nz_ct"] >= 13:
        tags.append("PLANNER_FLATLINE_VS_SPARSE_HIST")

    # Planner loads one half of the window heavily (>70/30)
    if rec["manual_total"] > 0:
        front_pct = rec["manual_front_half"] / rec["manual_total"]
        if front_pct > 0.7:   tags.append("PLAN_FRONT_LOADED")
        elif front_pct < 0.3: tags.append("PLAN_BACK_LOADED")

    # Planner qty per nz-week is much larger than historical nz-week qty
    if rec["manual_nz_avg"] > 0 and rec["L52_nz_avg"] > 0:
        r = rec["manual_nz_avg"] / rec["L52_nz_avg"]
        if r >= 2.0:  tags.append("PLAN_QTY_GT_HIST")
        if r <= 0.5:  tags.append("PLAN_QTY_LT_HIST")

    # Primary bucket
    if "INACTIVE_PATH" in tags:
        primary = "A_Inactive_but_planner_has_demand"
    elif "LONG_GAP_WITH_PLAN" in tags:
        primary = "B_Long_gap_but_planner_has_demand"
    elif "AI_NEAR_ZERO" in tags and ai_under:
        primary = "C_AI_near_zero_planner_not"
    elif "PLANNER_FLATLINE_VS_SPARSE_HIST" in tags and ai_under:
        primary = "D_Sparse_hist_planner_flatline_AI_under"
    elif "PLAN_QTY_GT_HIST" in tags and ai_under:
        primary = "E_Planner_qty_bigger_than_history_AI_under"
    elif "PLAN_QTY_LT_HIST" in tags and not ai_under:
        primary = "F_Planner_qty_smaller_than_history_AI_over"
    elif ai_under:
        primary = "G_AI_under_other"
    else:
        primary = "H_AI_over_other"
    return primary, tags

for rec in flagged:
    rec["primary"], rec["tags"] = classify(rec)

# -------- Aggregate --------
by_primary = Counter(r["primary"] for r in flagged)
by_model   = Counter(r["likely_model"] for r in flagged)
by_dir     = Counter(r["direction"] for r in flagged)
by_tag     = Counter(t for r in flagged for t in r["tags"])

# Sum totals by primary bucket — to see which drives most under-projection $
gap_by_primary = defaultdict(lambda: {"n":0, "manual":0, "ai":0})
for r in flagged:
    g = gap_by_primary[r["primary"]]
    g["n"]      += 1
    g["manual"] += r["manual_total"]
    g["ai"]     += r["ai_total"]

out = {
    "n_flagged":  len(flagged),
    "by_primary": dict(by_primary),
    "by_likely_model": dict(by_model),
    "by_direction":    dict(by_dir),
    "by_tag":          dict(by_tag),
    "gap_by_primary":  {k: dict(v) for k,v in gap_by_primary.items()},
    "records": flagged,
}
with open("analysis_lowvol_highvar.json","w") as fh:
    json.dump(out, fh, indent=2)
print("Wrote analysis_lowvol_highvar.json")

# -------- Console summary --------
print()
print("=== DIRECTION ===")
for k,v in by_dir.most_common():
    print(f"  {k:12s} {v:,}")
print()
print("=== LIKELY MODEL USED ===")
for k,v in by_model.most_common():
    print(f"  {k:12s} {v:,}")
print()
print("=== FAILURE MODE (primary bucket) ===")
for k,v in sorted(by_primary.items(), key=lambda x:-x[1]):
    g = gap_by_primary[k]
    print(f"  {k:50s} n={v:4d}   manual={int(g['manual']):>8,}  ai={int(g['ai']):>7,}  gap={int(g['manual']-g['ai']):>8,}")
print()
print("=== TAG COUNTS (non-exclusive) ===")
for k,v in by_tag.most_common():
    print(f"  {k:34s} {v:,}")
print()
print("=== TOP-20 LARGEST UNDER-PROJECTION GAPS ===")
under = [r for r in flagged if r["var_pct"] < 0]
under.sort(key=lambda r: r["ai_total"] - r["manual_total"])  # most negative first
for r in under[:20]:
    print(f"  {r['key']:32s} man={int(r['manual_total']):>6,} ai={int(r['ai_total']):>5,} "
          f"L13nz={r['L13_nz']:>2d} L26nz={r['L26_nz']:>2d} trailZ={r['trail_zero']:>2d} "
          f"gap={r['med_gap']:>2d} model={r['likely_model']:>10s}  {r['primary']}")
