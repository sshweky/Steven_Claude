#!/usr/bin/env python3
"""
Deep-dive analysis of the 102 CRITICAL priority records (baseline >= 1000/wk).

Goal: identify where the AI model itself (not planner mistakes) is making
systematic errors that contribute to large AI-vs-Manual gaps.

Approach:
  1. Load validation_results.json (manual projection flags + expected baseline)
  2. Load forecast_results.json (AI forecast + ai_model + rule_fires)
  3. Join on key, filter to priority=CRITICAL
  4. For each record compute:
       - AI vs Manual gap (% and abs units)
       - L4W/L13W/L26W/L52W from history
       - Which direction is AI biased (over, under, zero)
       - Which rules fired
  5. Bucket records into error-pattern categories
  6. Surface worst offenders per bucket with concrete diagnoses
"""
import json
import statistics
from collections import defaultdict, Counter

with open('validation_results.json') as f:
    val = {r['key']: r for r in json.load(f)['records']}
with open('forecast_results.json') as f:
    fcst = {r['key']: r for r in json.load(f)['records']}

# Join + filter
joined = []
for k, v in val.items():
    if v.get('priority') != 'CRITICAL':
        continue
    f_rec = fcst.get(k, {})
    ai_total = sum(f_rec.get('forecast') or [0]*26)
    man_total = v.get('projection_total', 0)
    exp_total = v.get('expected_total', 0)
    history = f_rec.get('history_l26_ord') or []
    if not history:
        history = f_rec.get('history_l26_shp') or []
    history_ly = f_rec.get('history_ly_ord') or f_rec.get('history_ly_shp') or []

    def safe_avg(lst):
        return sum(lst) / len(lst) if lst else 0
    def safe_nz_avg(lst):
        nz = [x for x in lst if x > 0]
        return sum(nz)/len(nz) if nz else 0

    l4  = safe_avg(history[-4:])
    l13 = safe_avg(history[-13:])
    l26 = safe_avg(history[-26:])
    l52_combined = (history + history_ly)[-52:] if (history or history_ly) else []
    l52 = safe_avg(l52_combined)
    l13_nz = safe_nz_avg(history[-13:])
    l26_nz_count = sum(1 for x in history[-26:] if x > 0)

    ai_per_wk  = ai_total / 26
    man_per_wk = man_total / 26
    ai_vs_man_pct = ((ai_total - man_total) / man_total * 100) if man_total > 0 else (100.0 if ai_total > 0 else 0)
    ai_vs_man_abs = ai_total - man_total

    # Direction: which side is AI on
    if abs(ai_vs_man_pct) < 7.5:
        direction = 'AGREE'
    elif ai_total == 0 and man_total > 0:
        direction = 'AI_ZERO_MAN_HAS'
    elif man_total == 0 and ai_total > 0:
        direction = 'MAN_ZERO_AI_HAS'
    elif ai_total > man_total:
        direction = 'AI_HIGHER'
    else:
        direction = 'AI_LOWER'

    joined.append({
        'key':         k,
        'mstyle':      v.get('mstyle'),
        'cust':        v.get('cust'),
        'inv_mgr':     v.get('inv_manager'),
        'pattern':     v.get('pattern'),
        'biweekly':    v.get('biweekly'),
        'iso_settle':  v.get('iso_settle'),
        'baseline':    v.get('baseline'),
        'baseline_src':v.get('baseline_src'),
        'item_status': v.get('item_status'),
        'status_cust': v.get('status_cust'),
        'pog_launch':  v.get('pog_launch'),
        'pog_end':     v.get('pog_end'),
        'ai_model':    f_rec.get('model'),
        'rule_fires':  f_rec.get('rule_fires') or [],
        'ai_total':    ai_total,
        'man_total':   man_total,
        'exp_total':   exp_total,
        'ai_per_wk':   round(ai_per_wk, 1),
        'man_per_wk':  round(man_per_wk, 1),
        'l4':          round(l4, 1),
        'l13':         round(l13, 1),
        'l26':         round(l26, 1),
        'l52':         round(l52, 1),
        'l13_nz':      round(l13_nz, 1),
        'l26_nz_count':l26_nz_count,
        'forecast':    f_rec.get('forecast') or [0]*26,
        'manual':      f_rec.get('manual') or [0]*26,
        'history_l26': history[-26:] if history else [],
        'ai_vs_man_pct': round(ai_vs_man_pct, 1),
        'ai_vs_man_abs': ai_vs_man_abs,
        'direction':   direction,
        'n_flags':     v.get('n_flags', 0),
        'pct_diff':    v.get('pct_diff', 0),
        'po_total':    f_rec.get('po_total_qty', 0),
        'po_zeroed_weeks': f_rec.get('po_zeroed_weeks') or [],
        'narrative':   (v.get('narrative') or '')[:200],
    })

print(f"=== CRITICAL Priority Records: {len(joined)} ===")
print()

# Direction breakdown
dir_count = Counter(r['direction'] for r in joined)
print("Direction breakdown (AI vs Manual):")
for d, n in dir_count.most_common():
    abs_units = sum(abs(r['ai_vs_man_abs']) for r in joined if r['direction']==d)
    print(f"  {d:24s}  {n:3d} records   abs gap: {abs_units:>10,} units")
print()

# AI model breakdown
model_count = Counter(r['ai_model'] for r in joined)
print("AI model used:")
for m, n in model_count.most_common():
    print(f"  {m:50s}  {n}")
print()

# Pattern breakdown
pat_count = Counter(r['pattern'] for r in joined)
print("Validation pattern:")
for p, n in pat_count.most_common():
    print(f"  {p or '(blank)':30s}  {n}")
print()

# Baseline source breakdown - how the "expected" was calculated
src_count = Counter(r['baseline_src'] for r in joined)
print("Baseline source (expected calc):")
for s, n in src_count.most_common():
    print(f"  {s or '(blank)':30s}  {n}")
print()

# Top variances absolute
print("=== TOP 30 by absolute AI-Manual gap ===")
print(f"{'Key':22s} {'Model':30s} {'AI':>9s} {'Man':>9s} {'Gap':>9s} {'%':>7s} {'Dir':22s} {'L4':>7s} {'L13':>7s} {'L26':>7s} {'L52':>7s}")
top_abs = sorted(joined, key=lambda r: -abs(r['ai_vs_man_abs']))[:30]
for r in top_abs:
    print(f"{r['key']:22s} {(r['ai_model'] or '')[:30]:30s} {r['ai_total']:>9,} {r['man_total']:>9,} {r['ai_vs_man_abs']:>+9,} {r['ai_vs_man_pct']:>+6.1f}% {r['direction']:22s} {r['l4']:>7.0f} {r['l13']:>7.0f} {r['l26']:>7.0f} {r['l52']:>7.0f}")
print()

# Save full join for follow-up
with open('critical_102_join.json', 'w') as f:
    json.dump(joined, f, indent=2, default=str)
print("Saved: critical_102_join.json")
