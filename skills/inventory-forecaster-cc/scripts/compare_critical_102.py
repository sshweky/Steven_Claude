#!/usr/bin/env python3
"""Compare the 102 critical-priority records before/after the model fixes."""
import json

# Old snapshot from before fixes
with open('critical_102_join.json') as f:
    old = {r['key']: r for r in json.load(f)}

# Fresh forecast + validation after fixes
with open('validation_results.json') as f:
    val = {r['key']: r for r in json.load(f)['records']}
with open('forecast_results.json') as f:
    fcst = {r['key']: r for r in json.load(f)['records']}

direction_change = {'CLOSED': 0, 'IMPROVED': 0, 'UNCHANGED': 0, 'WORSE': 0,
                    'NEW_AGREE': 0, 'NEW_DISAGREE': 0}
total_gap_before = 0
total_gap_after  = 0

per_key = []
for k, r_old in old.items():
    r_val = val.get(k)
    r_f   = fcst.get(k)
    if not r_val or not r_f:
        continue
    new_ai  = sum(r_f.get('forecast') or [])
    new_man = r_val.get('projection_total', 0)
    new_gap = new_ai - new_man
    new_pct = ((new_ai - new_man) / new_man * 100) if new_man > 0 else (100.0 if new_ai > 0 else 0)

    old_gap = r_old['ai_vs_man_abs']
    old_pct = r_old['ai_vs_man_pct']

    total_gap_before += abs(old_gap)
    total_gap_after  += abs(new_gap)

    if abs(new_pct) < 7.5:
        if abs(old_pct) >= 7.5: direction_change['NEW_AGREE'] += 1
        else: direction_change['UNCHANGED'] += 1
    elif abs(new_pct) < abs(old_pct) * 0.5:
        direction_change['CLOSED'] += 1
    elif abs(new_pct) < abs(old_pct) * 0.9:
        direction_change['IMPROVED'] += 1
    elif abs(new_pct) > abs(old_pct) * 1.1:
        direction_change['WORSE'] += 1
    else:
        direction_change['UNCHANGED'] += 1

    per_key.append({
        'key': k, 'bucket': r_old.get('bucket'),
        'old_ai': r_old['ai_total'], 'new_ai': new_ai,
        'manual': new_man,
        'old_pct': old_pct, 'new_pct': round(new_pct, 1),
        'old_gap': old_gap, 'new_gap': new_gap,
        'improvement': abs(old_gap) - abs(new_gap),
        'rules_new': r_f.get('rule_fires') or [],
    })

print(f"=== Comparing {len(per_key)} CRITICAL records (pre-fix vs post-fix) ===\n")
print(f"Total |gap| before fixes: {total_gap_before:>10,} units")
print(f"Total |gap| after fixes:  {total_gap_after:>10,} units")
print(f"Reduction:                {total_gap_before - total_gap_after:>10,} units "
      f"({(total_gap_before - total_gap_after)/total_gap_before*100:.1f}%)")
print()
print("Direction change:")
for d, n in direction_change.items():
    print(f"  {d:18s}  {n:3d}")
print()

# Per-bucket improvement
from collections import defaultdict
bucket_old = defaultdict(int)
bucket_new = defaultdict(int)
bucket_n   = defaultdict(int)
for r in per_key:
    b = r['bucket'] or 'unknown'
    bucket_old[b] += abs(r['old_gap'])
    bucket_new[b] += abs(r['new_gap'])
    bucket_n[b]   += 1
print("Per-bucket improvement:")
print(f"  {'Bucket':40s} {'n':>3s} {'|gap| before':>14s} {'|gap| after':>14s} {'Delta':>14s} {'%':>7s}")
for b in sorted(bucket_old.keys(), key=lambda b: -bucket_old[b]):
    delta = bucket_old[b] - bucket_new[b]
    pct = (delta / bucket_old[b] * 100) if bucket_old[b] > 0 else 0
    print(f"  {b:40s} {bucket_n[b]:>3d} {bucket_old[b]:>14,} {bucket_new[b]:>14,} {delta:>+14,} {pct:>+6.1f}%")
print()

# Top improvers
per_key.sort(key=lambda r: -r['improvement'])
print("Top 15 improved records (|gap| reduction):")
print(f"  {'Key':22s} {'Bucket':36s} {'Old AI':>9s} {'New AI':>9s} {'Manual':>9s} {'old%':>8s} {'new%':>8s} {'Saved':>9s}")
for r in per_key[:15]:
    print(f"  {r['key']:22s} {(r['bucket'] or '')[:36]:36s} {r['old_ai']:>9,} {r['new_ai']:>9,} {r['manual']:>9,} {r['old_pct']:>+7.1f}% {r['new_pct']:>+7.1f}% {r['improvement']:>+9,}")
print()

# Top WORSE records
per_key.sort(key=lambda r: r['improvement'])
print("Top 10 records that got WORSE (|gap| increased):")
print(f"  {'Key':22s} {'Bucket':36s} {'Old AI':>9s} {'New AI':>9s} {'Manual':>9s} {'old%':>8s} {'new%':>8s} {'Cost':>9s}")
for r in per_key[:10]:
    if r['improvement'] >= 0: break
    print(f"  {r['key']:22s} {(r['bucket'] or '')[:36]:36s} {r['old_ai']:>9,} {r['new_ai']:>9,} {r['manual']:>9,} {r['old_pct']:>+7.1f}% {r['new_pct']:>+7.1f}% {r['improvement']:>+9,}")
