import json
f = open('forecast_results.json')
data = json.load(f)
f.close()
recs = data['records']
yoy = [r for r in recs if 'F_YOY_CADENCE' in r.get('rule_fires', [])]
print("F_YOY_CADENCE records:", len(yoy))

# Check first 3 records for W17-W18, W21-W22 forecast values
for r in yoy[:3]:
    print("\n--- " + r['key'] + " (" + r['model'] + ") ---")
    fc = r.get('forecast', [])
    mn = r.get('manual', [])
    hist = r.get('history_ly_ord', [])
    if len(fc) >= 22:
        print("  FC  W17=" + str(fc[16]) + " W18=" + str(fc[17]) + " W21=" + str(fc[20]) + " W22=" + str(fc[21]))
    if len(mn) >= 22:
        print("  Man W17=" + str(mn[16]) + " W18=" + str(mn[17]) + " W21=" + str(mn[20]) + " W22=" + str(mn[21]))
    if len(hist) >= 22:
        print("  LY  W17=" + str(hist[16]) + " W18=" + str(hist[17]) + " W21=" + str(hist[20]) + " W22=" + str(hist[21]))
    ai_an = r.get('ai_analysis', '')
    if ai_an and 'F_YOY' in str(ai_an):
        print("  ai_analysis YOY:", str(ai_an)[:300])
    alert = r.get('alert', '')
    if 'F_YOY' in alert:
        print("  alert YOY:", alert[:300])
