"""Post-run summary: cohort impact + 6 callout spot-check + new-fix rule fires."""
import json, sys

with open('forecast_results.json') as f:
    d = json.load(f)
records = d.get('records', [])

if not records:
    print("No records found in forecast_results.json"); sys.exit(1)

# Cohort summary
total_ai     = sum(int(r.get('new_total',0))   for r in records)
total_manual = sum(int(r.get('prior_total',0)) for r in records)
n_alerts     = sum(1 for r in records if r.get('alert'))

models = {}
for r in records:
    m = r.get('model','?')
    models[m] = models.get(m,0)+1

# New-fix rule fires
new_fix_fires = {'F47': 0, 'F48': 0}
for r in records:
    rules = r.get('rule_fires') or []
    for k in new_fix_fires:
        if k in rules:
            new_fix_fires[k] += 1

# Records by customer-type that are alerts
amz_alerts = sum(1 for r in records if r.get('alert') and 'AMAZON' in str(r.get('cust','')).upper())
wmt_alerts = sum(1 for r in records if r.get('alert') and 'WAL MART' in str(r.get('cust','')).upper())

# Largest aggressive (AI vs Manual) and most under-projected
diffs = []
for r in records:
    new = r.get('new_total',0); prior = r.get('prior_total',0)
    if prior > 0:
        diffs.append((new - prior, (new/prior - 1.0)*100, r))
diffs_above = sorted([d for d in diffs if d[0]>0], key=lambda x: -x[0])[:8]
diffs_below = sorted([d for d in diffs if d[0]<0], key=lambda x: x[0])[:8]

print(f"\n{'='*80}")
print(f"COHORT SUMMARY  ({len(records):,} records)")
print(f"{'='*80}")
print(f"  Total AI 26w demand: {total_ai:>14,d}")
print(f"  Total Manual 26w   : {total_manual:>14,d}")
print(f"  Cohort delta       : {total_ai-total_manual:+14,d} ({(total_ai/total_manual-1)*100:+.1f}%)")
print(f"  Alerts (>5% var)   : {n_alerts:,}/{len(records)}  ({n_alerts/len(records)*100:.0f}%)")
print(f"    Amazon          : {amz_alerts:,}")
print(f"    Walmart         : {wmt_alerts:,}")
print(f"\nModel split:")
for m, n in sorted(models.items(), key=lambda x: -x[1]):
    print(f"    {m[:30]:30s} {n:>6,d}")
print(f"\nNew-fix rule fires:")
for k, n in new_fix_fires.items():
    print(f"    {k}  {n:>6,d}")

# 6 callouts spot check
print(f"\n{'='*80}")
print(f"6 CALLOUT SPOT-CHECK")
print(f"{'='*80}")
targets = [('23011','FF12660','~40K','Walmart pre-OOS ~1800/wk'),
           ('23011','FF15592','~40K','Walmart L4 ~1500/wk'),
           ('1864','BB13437','~53K','Amazon POS stable 1500-1800/wk'),
           ('16579','FF7612','~5K','Petco single-PO inflation'),
           ('1864','SF8169','~0K','Amazon massive stockup ~28k 11w ago'),
           ('1864','FF12853','~50K','Amazon L4 ramping 2,600/wk')]
for acct, m, expected, desc in targets:
    found = [r for r in records if r.get('mstyle','').upper()==m.upper()
             and str(r.get('key','')).startswith(acct+'-')]
    if not found:
        print(f"  {acct}-{m:8s}  MISSING")
        continue
    r = found[0]
    new = int(r.get('new_total',0))
    prior = int(r.get('prior_total',0))
    pct = (new/prior-1)*100 if prior else 0
    rules = r.get('rule_fires') or []
    print(f"  {acct}-{m:8s}  AI={new:>7,d}  Manual={prior:>7,d}  {pct:+5.0f}%  "
          f"target {expected}  ({desc})")
    print(f"             rules: {','.join(rules)}")

# Top aggressive
print(f"\n{'='*80}")
print(f"TOP 8 LARGEST AI > MANUAL (potential remaining over-projections)")
print(f"{'='*80}")
for delta, pct, r in diffs_above:
    print(f"  {r.get('key','?'):20s} {(r.get('cust','?') or '')[:18]:18s}  "
          f"AI={int(r['new_total']):>7,d}  Manual={int(r['prior_total']):>7,d}  "
          f"{pct:+6.0f}%  model={r.get('model','?')[:15]}")

print(f"\n{'='*80}")
print(f"TOP 8 LARGEST MANUAL > AI (potential remaining under-projections)")
print(f"{'='*80}")
for delta, pct, r in diffs_below:
    print(f"  {r.get('key','?'):20s} {(r.get('cust','?') or '')[:18]:18s}  "
          f"AI={int(r['new_total']):>7,d}  Manual={int(r['prior_total']):>7,d}  "
          f"{pct:+6.0f}%  model={r.get('model','?')[:15]}")
