"""
Deep deviation analysis for AI forecasts.

For every record where the AI weekly avg is more than +/-25% off from
either the manual projection OR the L13W ordered avg, classify the likely
root cause and aggregate patterns to spot algorithm-level issues.

Inputs:
  validation_results.json  - per-record validator output (includes ai_forecast,
                              manual proj weeks, L13/L26 history, baseline_src,
                              pattern, biweekly, iso flags, narrative)
  forecast_results.json    - actual capped/dampened forecast (overlay source)
"""
import json, statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---- Load -------------------------------------------------------------
val = json.load(open(ROOT / "validation_results.json"))
fcst = json.load(open(ROOT / "forecast_results.json"))
fcst_by_key = {r["key"]: r for r in fcst.get("records", [])}

records = val.get("records", [])
EVENT_WEEKS = set(range(7, 10)) | set(range(23, 26))    # Prime Day W7-9, Fall Deal W23-25

# ---- Classification helpers ------------------------------------------
def classify_cause(rec, ai_fcst, ai_wk, man_wk, ord_l13):
    """Return list of contributing causes (small set, severity-ordered)."""
    causes = []
    pattern = rec.get("pattern")
    biweekly = rec.get("biweekly")
    iso = rec.get("iso")
    bsrc   = rec.get("baseline_src", "") or ""
    history_ord = rec.get("history_l26_ord") or []
    history_shp = rec.get("history_l26_shp") or []
    weeks  = rec.get("weeks") or []
    L13o   = history_ord[-13:] if len(history_ord) >= 13 else history_ord
    nz_l13 = sum(1 for x in L13o if x > 0)
    cv     = 0
    if L13o:
        mu = sum(L13o) / len(L13o)
        if mu > 0:
            sd = statistics.pstdev(L13o)
            cv = sd / mu

    # 1) ISO / inactive
    if iso or "inactive" in pattern.lower() if pattern else False:
        causes.append("inactive_or_iso")
    # 2) Bi-weekly cadence
    if biweekly:
        causes.append("biweekly_cadence")
    # 3) Sparse / heuristic
    if pattern == "sparse" or "Heuristic" in (rec.get("ai_model") or ""):
        causes.append("sparse_heuristic")
    # 4) Intermittent / Croston
    if pattern in ("intermittent", "lumpy") or "Croston" in (rec.get("ai_model") or ""):
        causes.append("intermittent_croston")
    # 5) High CV — choppy history
    if cv > 0.8 and pattern != "sparse":
        causes.append("high_cv_lumpy_history")
    # 6) Manual-front-loads-then-flatlines (W1 huge vs later)
    plans = [float(w.get("projection") or 0) for w in weeks]
    if plans and plans[0] > 0:
        front_ratio = plans[0] / max(1, statistics.mean(plans[1:]) if len(plans) > 1 else plans[0])
        if front_ratio > 1.5:
            causes.append("manual_front_loaded")
    # 7) Manual is flat (single value)
    if plans and len(set(plans)) <= 3 and max(plans) > 0:
        causes.append("manual_flat_plan")
    # 8) Manual missing (zero or empty)
    if man_wk == 0 and ai_wk > 0:
        causes.append("no_manual_plan")
    # 9) AI heavily seasonal (event lift weeks dominating)
    if ai_fcst:
        ai_event = sum(ai_fcst[w-1] for w in EVENT_WEEKS if w-1 < len(ai_fcst))
        ai_total = sum(ai_fcst)
        if ai_total > 0 and ai_event / ai_total > 0.30:
            causes.append("event_lift_dominant")
    # 10) Recent trend up/down
    if len(L13o) >= 13 and ord_l13 > 0:
        l4  = sum(L13o[-4:]) / 4
        l13 = ord_l13
        if l4 > l13 * 1.30:
            causes.append("recent_trend_up")
        elif l4 < l13 * 0.70 and l4 < l13:
            causes.append("recent_trend_down")
    # 11) Baseline source = L26W or all-weeks (suggests L13 had too many zeros)
    if "L26W" in bsrc:
        causes.append("baseline_fell_back_to_L26")
    if "all-weeks" in bsrc.lower() or "fallback" in bsrc.lower():
        causes.append("baseline_fallback_used")
    # 12) Outlier in last 13 (single week >3x of median)
    if L13o:
        med = statistics.median([x for x in L13o if x > 0]) if any(L13o) else 0
        if med > 0 and max(L13o) > 3 * med:
            causes.append("outlier_week_in_l13")
    return causes or ["unclassified"]


# ---- Walk records ----------------------------------------------------
buckets = {
    "ai_vs_manual_high": [],   # AI > manual by >25%
    "ai_vs_manual_low":  [],   # AI < manual by >25%
    "ai_vs_l13_high":    [],   # AI > L13 by >25%
    "ai_vs_l13_low":     [],   # AI < L13 by >25%
}
cause_counter = Counter()
cause_by_bucket = defaultdict(Counter)
deviation_total = 0

for r in records:
    key = r["key"]
    f = fcst_by_key.get(key, {})
    ai_fcst = f.get("forecast") or r.get("ai_forecast") or []
    if not ai_fcst:
        continue
    ai_wk  = sum(ai_fcst) / 26.0
    man_wk = float(r.get("proj_per_wk") or 0)
    ord_l13= float(r.get("ord_per_wk_l13") or 0)

    flagged = False
    if man_wk > 0:
        d = (ai_wk - man_wk) / man_wk
        if d >  0.25: buckets["ai_vs_manual_high"].append((key, d, r, ai_fcst, ai_wk, man_wk, ord_l13)); flagged=True
        if d < -0.25: buckets["ai_vs_manual_low"].append((key, d, r, ai_fcst, ai_wk, man_wk, ord_l13)); flagged=True
    if ord_l13 > 0:
        d = (ai_wk - ord_l13) / ord_l13
        if d >  0.25: buckets["ai_vs_l13_high"].append((key, d, r, ai_fcst, ai_wk, man_wk, ord_l13)); flagged=True
        if d < -0.25: buckets["ai_vs_l13_low"].append((key, d, r, ai_fcst, ai_wk, man_wk, ord_l13));  flagged=True

    if flagged:
        deviation_total += 1
        causes = classify_cause(r, ai_fcst, ai_wk, man_wk, ord_l13)
        for c in causes:
            cause_counter[c] += 1
        # Tag against bucket(s) it hit
        for bname in ("ai_vs_manual_high","ai_vs_manual_low","ai_vs_l13_high","ai_vs_l13_low"):
            if buckets[bname] and buckets[bname][-1][0] == key:
                for c in causes:
                    cause_by_bucket[bname][c] += 1

# ---- Report ---------------------------------------------------------
print("="*72)
print(f"DEVIATION ANALYSIS  -  AI vs Manual / L13W   (>+/-25% threshold)")
print("="*72)
print(f"Total records:           {len(records):>6d}")
print(f"Records with deviation:  {deviation_total:>6d}  ({deviation_total/len(records)*100:.1f}%)")
print()
print(f"{'Bucket':<26s}  {'count':>6s}  {'% of total':>10s}")
for b, items in buckets.items():
    print(f"  {b:<24s}  {len(items):>6d}  {len(items)/len(records)*100:>9.1f}%")
print()

print("-"*72)
print("ROOT-CAUSE FREQUENCY (records can have multiple causes)")
print("-"*72)
for cause, n in cause_counter.most_common():
    print(f"  {cause:<32s}  {n:>5d}  ({n/deviation_total*100:>5.1f}% of deviating records)")
print()

print("-"*72)
print("ROOT-CAUSE BY BUCKET (top 5 each)")
print("-"*72)
for b, cc in cause_by_bucket.items():
    print(f"\n  {b}  (n={sum(cc.values())})")
    for cause, n in cc.most_common(8):
        print(f"     {cause:<32s} {n:>5d}")

# ---- Sample worst 5 in each bucket -----------------------------------
print()
print("-"*72)
print("SAMPLE: WORST 5 DEVIATIONS PER BUCKET")
print("-"*72)
for b, items in buckets.items():
    if not items:
        continue
    items.sort(key=lambda x: -abs(x[1]))
    print(f"\n  {b}:")
    for key, d, r, ai_fcst, ai_wk, man_wk, ord_l13 in items[:5]:
        causes = classify_cause(r, ai_fcst, ai_wk, man_wk, ord_l13)
        print(f"     {key:<24s}  AI={ai_wk:>7.0f}  Man={man_wk:>7.0f}  L13={ord_l13:>7.0f}  diff={d*100:+.0f}%")
        print(f"        pattern={r.get('pattern')}  biweek={r.get('biweekly')}  baseline_src={r.get('baseline_src')}")
        print(f"        causes={causes}")
        print(f"        L13 ord: {[int(x) for x in (r.get('history_l26_ord') or [])[-13:]]}")
