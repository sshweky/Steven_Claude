import json, urllib.request, urllib.error
HEADERS = {'QB-Realm-Hostname':'pim.quickbase.com','Authorization':'QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s','Content-Type':'application/json'}
BASE = "https://api.quickbase.com/v1"

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=HEADERS, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:500]}"

# We now know the correct format from existing summary field fid 1231:
#   fieldType = output type (numeric)
#   mode = "summary"
#   properties.summaryFunction      = "MAX" (uppercase)
#   properties.summaryReferenceFieldId = 6  (foreign key fid in child = Mstyle)
#   properties.summaryTargetFieldId    = 10 (fid to aggregate = ATS Qty OH#)
#   properties.summaryQuery            = QB query string to filter child records

# The test formula checkbox we created earlier is fid=34 in Inventory History
# ("Is LW": [Date] = Today() - Days(If(DayOfWeek(Today()) = 1, 7, DayOfWeek(Today()) - 1)))

IS_LW_FID = 34

# Test: add "ATS LW (test)" summary to Inventory Flow (bpsaju5pm) - HAS relationship
print("=== Test: summary field in Inventory Flow (existing relationship) ===")
resp, err = post("/fields?tableId=bpsaju5pm", {
    "label": "ATS LW (test)",
    "fieldType": "numeric",
    "mode": "summary",
    "properties": {
        "summaryFunction": "MAX",
        "summaryReferenceFieldId": 6,   # Mstyle fid in Inventory History (child)
        "summaryTargetFieldId": 10,      # ATS Qty OH# fid in Inventory History
        "summaryQuery": f"{{{IS_LW_FID}.EX.'true'}}",
        "decimalPlaces": 0,
    }
})
if err:
    print(f"FAILED: {err}")
else:
    print(f"SUCCESS: fid={resp['id']}")
    print(json.dumps(resp, indent=2)[:400])

# Test: add "ATS LW (test)" summary to new Weekly table (bv2sxg2ji) - NO relationship yet
print("\n=== Test: summary field in Weekly table (no relationship yet) ===")
resp2, err2 = post("/fields?tableId=bv2sxg2ji", {
    "label": "ATS LW (test)",
    "fieldType": "numeric",
    "mode": "summary",
    "properties": {
        "summaryFunction": "MAX",
        "summaryReferenceFieldId": 6,
        "summaryTargetFieldId": 10,
        "summaryQuery": f"{{{IS_LW_FID}.EX.'true'}}",
        "decimalPlaces": 0,
    }
})
if err2:
    print(f"FAILED: {err2}")
else:
    print(f"SUCCESS: fid={resp2['id']}")
