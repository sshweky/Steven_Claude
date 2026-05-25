#!/usr/bin/env python3
"""
Second-pass analysis: bucket the 102 CRITICAL records into error-pattern
categories and surface what rules fired in each bucket.

Buckets are mutually exclusive — first match wins, ordered by priority.
"""
import json
from collections import Counter, defaultdict

with open('critical_102_join.json') as f:
    recs = json.load(f)

# Buckets
def classify(r):
    """Return (bucket, hypothesis_notes) for each record."""
    key = r['key']
    ms = r['mstyle'] or ''
    is_pdq = 'PDQ' in ms.upper()
    is_ec  = ms.upper().endswith('EC') or '/' in ms and ms.split('/')[-1].endswith('EC')
    l4, l13, l26, l52 = r['l4'], r['l13'], r['l26'], r['l52']
    nz = r['l26_nz_count']
    ai = r['ai_total']
    man = r['man_total']
    direction = r['direction']
    model = r['ai_model'] or ''
    pat = r['pattern'] or ''
    gap = r['ai_vs_man_abs']

    # PDQ over-projection (display/endcap items where AI treats one-time placement as recurring)
    if is_pdq and direction == 'AI_HIGHER' and l13 > 0 and l4 / max(l13, 1) >= 1.1:
        return 'PDQ_recent_spike_overprojected'

    # PDQ under-projection (new launches AI may be ramping too slowly)
    if is_pdq and direction == 'AI_LOWER' and 'ramp' in model.lower():
        return 'PDQ_F72_ramp_too_conservative'

    # Manual zero but AI sees demand (planner says dead, AI says growing)
    if direction == 'MAN_ZERO_AI_HAS':
        return 'MAN_ZERO_AI_growth_signal'

    # AI zero but Manual planned (Inactive guard fired aggressively)
    if direction == 'AI_ZERO_MAN_HAS':
        return 'AI_ZERO_inactive_guard_fired'

    # Declining item AI over-projects (L52 > L26 > L13 but AI > Manual)
    if direction == 'AI_HIGHER' and l52 > 0 and l52 > l26 > 0 and l26 > l13 > 0:
        return 'declining_item_overprojected'

    # Genuine growth (L4 >> prior) AI matches, manual is the off one
    if direction == 'AI_HIGHER' and l13 > 0 and l4 / l13 >= 1.30:
        return 'genuine_step_up_manual_too_low'

    # Sparse intermittent (low non-zero count) AI over-projects
    if direction == 'AI_HIGHER' and nz <= 13 and pat in ('sparse_intermittent', 'intermittent'):
        return 'sparse_intermittent_overprojected'

    # Croston's specifically over-projects (steady model treating burst as recurring)
    if direction == 'AI_HIGHER' and 'Croston' in model:
        return 'crostons_overprojected'

    # Heuristic over-projects
    if direction == 'AI_HIGHER' and model == 'Heuristic':
        return 'heuristic_overprojected'

    # Seasonal Baseline over-projects with no L4W signal
    if direction == 'AI_HIGHER' and 'Seasonal Baseline' in model:
        return 'seasonal_baseline_overprojected'

    # AI lower than manual when L13>L4 (genuine decline but model can't keep up)
    if direction == 'AI_LOWER' and l13 > 0 and l4 / max(l13, 1) >= 1.20:
        return 'rising_demand_AI_lower'

    if direction == 'AI_LOWER':
        return 'AI_lower_other'

    return 'other_' + direction

# Apply
buckets = defaultdict(list)
for r in recs:
    r['bucket'] = classify(r)
    buckets[r['bucket']].append(r)

# Report
print(f"=== {len(recs)} CRITICAL Priority Records — Bucketed ===\n")

bucket_summary = sorted(buckets.items(),
                        key=lambda kv: -sum(abs(r['ai_vs_man_abs']) for r in kv[1]))
for b, lst in bucket_summary:
    total_gap = sum(abs(r['ai_vs_man_abs']) for r in lst)
    avg_pct = sum(abs(r['ai_vs_man_pct']) for r in lst) / len(lst)
    print(f"  {b:40s}  {len(lst):3d} records   |Gap|: {total_gap:>10,}u   avg |%|: {avg_pct:>6.1f}%")
print()

# Per-bucket detail
for bname, lst in bucket_summary:
    print(f"\n{'='*88}")
    print(f"BUCKET: {bname}  ({len(lst)} records)")
    print('='*88)
    lst_sorted = sorted(lst, key=lambda r: -abs(r['ai_vs_man_abs']))
    # Aggregate rule_fires across the bucket
    rule_freq = Counter()
    for r in lst:
        for f in r.get('rule_fires', []):
            rule_freq[f] += 1
    if rule_freq:
        print("  Most-common rules fired in this bucket:")
        for rule, n in rule_freq.most_common(12):
            pct = n / len(lst) * 100
            print(f"    {rule:10s}  {n:3d}/{len(lst)} ({pct:>5.1f}%)")
        print()
    # Top 10 worst offenders in bucket
    print("  Worst offenders (top 10 by |gap|):")
    print(f"  {'Key':22s} {'Model':32s} {'AI':>9s} {'Man':>9s} {'Gap':>9s} {'%':>7s} {'L4':>6s} {'L13':>6s} {'L26':>6s} {'L52':>6s} nz")
    for r in lst_sorted[:10]:
        print(f"  {r['key']:22s} {(r['ai_model'] or '')[:32]:32s} {r['ai_total']:>9,} {r['man_total']:>9,} {r['ai_vs_man_abs']:>+9,} {r['ai_vs_man_pct']:>+6.1f}% {r['l4']:>6.0f} {r['l13']:>6.0f} {r['l26']:>6.0f} {r['l52']:>6.0f} {r['l26_nz_count']:>2}")

print("\n\nDone.")
