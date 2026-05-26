import json
f = open('forecast_results.json')
data = json.load(f)
f.close()
recs = data['records']
yoy = [r for r in recs if 'F_YOY_CADENCE' in r.get('rule_fires', [])]
print("F_YOY_CADENCE records:", len(yoy))
r = yoy[0]
print("keys:", list(r.keys()))
print("rule_fires:", r.get('rule_fires'))
alert = r.get('alert', '')
print("alert[:500]:", alert[:500])
