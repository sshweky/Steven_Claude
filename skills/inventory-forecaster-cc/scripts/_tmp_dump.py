import json, re
with open(r'C:\Users\steven\.claude\skills\inventory-forecaster-cc\scripts\forecast_results.json') as f:
    d = json.load(f)
recs = d.get('records', d)
for r in recs:
    if r.get('key') != '1864-FF12508':
        continue
    print('Model:', r.get('model'))
    print('Cap_base:', r.get('cap_base'))
    print('MP:', r.get('mp'))
    print('Rule_fires:', r.get('rule_fires'))
    print('Forecast:', r.get('forecast'))
    print('F37 adjustments:')
    for a in r.get('f37_adjustments') or []:
        print(' ', a)
    meta = r.get('meta', {})
    print('Meta keys:', list(meta.keys()))
    for drv in meta.get('drivers', []):
        s = re.sub(r'<[^>]+>', ' ', drv) if isinstance(drv, str) else str(drv)
        print(' D:', s[:280])
    break
