import json
with open(r'C:\Users\steven\.claude\skills\inventory-forecaster-cc\scripts\forecast_results.json') as f:
    d = json.load(f)
print('Top type:', type(d).__name__)
if isinstance(d, dict):
    print('Top keys:', list(d.keys()))
    recs = d.get('records', d.get('results', []))
else:
    recs = d
print('Record count:', len(recs))
if recs:
    print('First record keys:', list(recs[0].keys())[:8] if isinstance(recs[0], dict) else type(recs[0]).__name__)
    print('First key:', recs[0].get('key') if isinstance(recs[0], dict) else recs[0])
matched = [r for r in recs if isinstance(r, dict) and r.get('key') == '1864-FF12508']
print('FF12508 matches:', len(matched))
