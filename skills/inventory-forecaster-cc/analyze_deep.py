"""
Deep ±10% deviation analysis — find systemic biases.

Goes beyond the categorical root-cause classification by decomposing:
  - Per-WEEK bias (AI minus L13 baseline by week 1..26)
  - Per-MODEL bias (Seasonal Baseline vs Croston vs Heuristic)
  - Per-PATTERN bias (steady vs intermittent vs sparse)
  - Distribution of AI/L13 ratio (skew, fat tails)
  - Sign-balance: does the algo systematically over- or under-project?
  - Mid-horizon vs front-week bias (does forecast 'sag' or 'overshoot' at any zone?)
"""
import json, statistics
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
val  = json.load(open(ROOT / "validation_results.json"))
fcst = json.load(open(ROOT / "forecast_results.json"))
fb   = {r["key"]: r for r in fcst.get("records", [])}

records = val.get("records", [])
WK_EVENT_PRIME = set(range(7, 10))
WK_EVENT_FALL  = set(range(23, 26))

# ---- 1. Headline distributions ---------------------------------------
ai_minus_l13_pct  = []
ai_minus_man_pct  = []
ai_per_wk_per_rec = []
deviating10 = []
for r in records:
    f = fb.get(r["key"], {})
    fcst_arr = f.get("forecast") or r.get("ai_forecast") or []
    if not fcst_arr:
        continue
    ai_wk  = sum(fcst_arr) / 26.0
    l13    = float(r.get("ord_per_wk_l13") or 0)
    man    = float(r.get("proj_per_wk")    or 0)
    if l13 > 0:
        ai_minus_l13_pct.append((ai_wk - l13) / l13 * 100)
    if man > 0:
        ai_minus_man_pct.append((ai_wk - man) / man * 100)
    if (l13 > 0 and abs((ai_wk-l13)/l13) > 0.10) or (man > 0 and abs((ai_wk-man)/man) > 0.10):
        deviating10.append((r, fcst_arr, ai_wk, man, l13))

def percentile(xs, p):
    if not xs: return 0
    s = sorted(xs); n = len(s)
    k = max(0, min(n-1, int(n * p / 100)))
    return s[k]

def describe(xs, name):
    if not xs:
        print(f"  {name}: empty"); return
    print(f"  {name}:")
    print(f"     n={len(xs):,d}  mean={statistics.mean(xs):+6.1f}%  median={statistics.median(xs):+6.1f}%  stdev={statistics.pstdev(xs):.1f}%")
    print(f"     pct: p10={percentile(xs,10):+5.0f}  p25={percentile(xs,25):+5.0f}  p50={percentile(xs,50):+5.0f}  p75={percentile(xs,75):+5.0f}  p90={percentile(xs,90):+5.0f}")
    n_under_10 = sum(1 for x in xs if x < -10)
    n_in       = sum(1 for x in xs if -10 <= x <= 10)
    n_over_10  = sum(1 for x in xs if x > 10)
    print(f"     <-10%: {n_under_10:>5d} ({n_under_10/len(xs)*100:.1f}%)   |-10..+10|: {n_in:>5d} ({n_in/len(xs)*100:.1f}%)   >+10%: {n_over_10:>5d} ({n_over_10/len(xs)*100:.1f}%)")

print("="*78)
print("HEADLINE — DISTRIBUTION OF (AI - reference) AS % OF REFERENCE")
print("="*78)
describe(ai_minus_l13_pct, "AI vs L13W ord/wk")
describe(ai_minus_man_pct, "AI vs Manual proj/wk")
print()
print(f"Total records with usable forecast: {len(ai_minus_l13_pct)}")
print(f"Records deviating >±10% on either reference: {len(deviating10)}")
print(f"  {len(deviating10)/max(1,len(records))*100:.1f}% of all validation records")

# ---- 2. Per-week bias -----------------------------------------------
print()
print("="*78)
print("PER-WEEK BIAS — average (AI[w] - L13W avg) / L13W avg, signed")
print("Reveals whether forecast over/under-projects at specific positions")
print("="*78)
wk_diffs = defaultdict(list)
for r, fcst_arr, ai_wk, man, l13 in deviating10:
    if l13 <= 0 or len(fcst_arr) < 26: continue
    for w in range(26):
        diff = (fcst_arr[w] - l13) / l13 * 100
        wk_diffs[w+1].append(diff)

print(f"  {'Wk':>3s} {'n':>5s}  {'mean':>7s} {'median':>7s} {'p25':>5s} {'p75':>5s}  {'note':<20s}")
for w in range(1, 27):
    xs = wk_diffs.get(w, [])
    if not xs:
        continue
    note = ""
    if w in WK_EVENT_PRIME: note = "&lt;- Prime Day window"
    if w in WK_EVENT_FALL:  note = "&lt;- Fall Deal window"
    print(f"  W{w:<2d} {len(xs):>5d}  {statistics.mean(xs):>+6.0f}% {statistics.median(xs):>+6.0f}% {percentile(xs,25):>+4.0f}% {percentile(xs,75):>+4.0f}%  {note}")

# ---- 3. Per-model bias ----------------------------------------------
print()
print("="*78)
print("PER-MODEL SIGNED BIAS — does each model run hot or cold?")
print("="*78)
by_model = defaultdict(list)
for r, fcst_arr, ai_wk, man, l13 in deviating10:
    model = r.get("ai_model") or "Unknown"
    if l13 > 0:
        by_model[model].append((ai_wk - l13) / l13 * 100)

for m, xs in sorted(by_model.items(), key=lambda x: -len(x[1])):
    if not xs: continue
    n_high = sum(1 for x in xs if x > 10)
    n_low  = sum(1 for x in xs if x < -10)
    print(f"  {m:<22s}  n={len(xs):>4d}  mean={statistics.mean(xs):+6.0f}%  median={statistics.median(xs):+5.0f}%  high>10%={n_high} ({n_high/len(xs)*100:.0f}%)  low<-10%={n_low} ({n_low/len(xs)*100:.0f}%)")

# ---- 4. Per-pattern bias --------------------------------------------
print()
print("="*78)
print("PER-PATTERN SIGNED BIAS")
print("="*78)
by_pattern = defaultdict(list)
for r, fcst_arr, ai_wk, man, l13 in deviating10:
    pat = r.get("pattern") or "Unknown"
    if l13 > 0:
        by_pattern[pat].append((ai_wk - l13) / l13 * 100)
for p, xs in sorted(by_pattern.items(), key=lambda x: -len(x[1])):
    if not xs: continue
    n_high = sum(1 for x in xs if x > 10)
    n_low  = sum(1 for x in xs if x < -10)
    print(f"  {p:<22s}  n={len(xs):>4d}  mean={statistics.mean(xs):+6.0f}%  median={statistics.median(xs):+5.0f}%  high>10%={n_high} ({n_high/len(xs)*100:.0f}%)  low<-10%={n_low} ({n_low/len(xs)*100:.0f}%)")

# ---- 5. Volume-tier bias --------------------------------------------
print()
print("="*78)
print("PER-VOL-TIER SIGNED BIAS")
print("="*78)
by_tier = defaultdict(list)
for r, fcst_arr, ai_wk, man, l13 in deviating10:
    tier = "HIGH" if ai_wk >= 1000 else ("MEDIUM" if ai_wk >= 200 else "LOW")
    if l13 > 0:
        by_tier[tier].append((ai_wk - l13) / l13 * 100)
for t in ("HIGH","MEDIUM","LOW"):
    xs = by_tier.get(t, [])
    if not xs: continue
    n_high = sum(1 for x in xs if x > 10)
    n_low  = sum(1 for x in xs if x < -10)
    print(f"  {t:<10s}  n={len(xs):>4d}  mean={statistics.mean(xs):+6.0f}%  median={statistics.median(xs):+5.0f}%  high>10%={n_high} ({n_high/len(xs)*100:.0f}%)  low<-10%={n_low} ({n_low/len(xs)*100:.0f}%)")

# ---- 6. Front-week vs mid-horizon vs back-week bias -----------------
print()
print("="*78)
print("WEEK-ZONE BIAS")
print("="*78)
zones = {"W1-W4 (front)": range(1,5), "W5-W9 (early-mid)": range(5,10),
         "W10-W17 (mid)": range(10,18), "W18-W25 (late)": range(18,26),
         "W26 (back)": [26]}
for name, ws in zones.items():
    xs = []
    for w in ws:
        xs.extend(wk_diffs.get(w, []))
    if xs:
        print(f"  {name:<22s}  mean={statistics.mean(xs):+5.0f}%  median={statistics.median(xs):+5.0f}%  n={len(xs):,d}")

# ---- 7. Largest signed-bias subgroups -------------------------------
print()
print("="*78)
print("WHERE BIAS CONCENTRATES — model × pattern × tier")
print("="*78)
by_combo = defaultdict(list)
for r, fcst_arr, ai_wk, man, l13 in deviating10:
    if l13 <= 0: continue
    model = r.get("ai_model") or "Unknown"
    pat   = r.get("pattern") or "Unknown"
    tier  = "HIGH" if ai_wk >= 1000 else ("MEDIUM" if ai_wk >= 200 else "LOW")
    key = (model, pat, tier)
    by_combo[key].append((ai_wk - l13) / l13 * 100)

# Show only combos with ≥ 20 records
combos = [(k, xs, statistics.mean(xs)) for k, xs in by_combo.items() if len(xs) >= 20]
combos.sort(key=lambda x: -abs(x[2]))
print(f"  {'Model':<22s}  {'Pattern':<14s}  {'Tier':<6s}  n     mean    median")
for (model, pat, tier), xs, mean_bias in combos[:15]:
    print(f"  {model:<22s}  {pat:<14s}  {tier:<6s}  {len(xs):>4d}  {mean_bias:>+6.0f}% {statistics.median(xs):>+6.0f}%")

# ---- 8. Bi-weekly cadence accuracy ----------------------------------
print()
print("="*78)
print("BIWEEKLY-CADENCE RECORDS — does cadence enforcement hurt us?")
print("="*78)
biw, non_biw = [], []
for r, fcst_arr, ai_wk, man, l13 in deviating10:
    if l13 <= 0: continue
    bucket = biw if r.get("biweekly") else non_biw
    bucket.append((ai_wk - l13) / l13 * 100)
print(f"  biweekly=True   n={len(biw):>4d}  mean={statistics.mean(biw or [0]):+5.0f}%  median={statistics.median(biw or [0]):+5.0f}%")
print(f"  biweekly=False  n={len(non_biw):>4d}  mean={statistics.mean(non_biw or [0]):+5.0f}%  median={statistics.median(non_biw or [0]):+5.0f}%")

# ---- 9. ISO / inactive-but-still-projects ---------------------------
print()
print("="*78)
print("ISO / INACTIVE FLAG INTERACTION")
print("="*78)
iso, non_iso = [], []
for r, fcst_arr, ai_wk, man, l13 in deviating10:
    if l13 <= 0: continue
    bucket = iso if r.get("iso") or "inactive" in (r.get("pattern") or "").lower() else non_iso
    bucket.append((ai_wk - l13) / l13 * 100)
print(f"  iso/inactive    n={len(iso):>4d}  mean={statistics.mean(iso or [0]):+5.0f}%  median={statistics.median(iso or [0]):+5.0f}%")
print(f"  active          n={len(non_iso):>4d}  mean={statistics.mean(non_iso or [0]):+5.0f}%  median={statistics.median(non_iso or [0]):+5.0f}%")
