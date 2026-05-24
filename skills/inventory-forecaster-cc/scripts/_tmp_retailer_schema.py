import urllib.request, json

TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
REALM  = "pim.quickbase.com"
HDRS   = {
    "QB-Realm-Hostname": REALM,
    "Authorization":     f"QB-USER-TOKEN {TOKEN}",
    "Content-Type":      "application/json",
}
TABLE  = "bp4rr2ckt"   # Retailer Sales

# ── fields ───────────────────────────────────────────────────────────────────
req = urllib.request.Request(
    f"https://api.quickbase.com/v1/fields?tableId={TABLE}&includeFieldPerms=false",
    headers=HDRS)
with urllib.request.urlopen(req, timeout=30) as r:
    fields = json.loads(r.read())

print(f"Retailer Sales  ({TABLE})  --  {len(fields)} fields\n")
for f in sorted(fields, key=lambda x: x["id"]):
    mode = f.get("mode", "")
    tag  = f"[{mode}]" if mode else ""
    print(f"  {f['id']:4d}  {f['fieldType']:<18}  {tag:<12}  {f.get('label','')}")

# ── quick record count ───────────────────────────────────────────────────────
import time
payload = json.dumps({"from": TABLE, "select": [3], "where": "{3.GT.0}",
                      "options": {"skip": 0, "top": 1}}).encode()
req2 = urllib.request.Request("https://api.quickbase.com/v1/records/query",
                               data=payload, headers=HDRS, method="POST")
with urllib.request.urlopen(req2, timeout=30) as r:
    meta = json.loads(r.read()).get("metadata", {})
print(f"\n  Total records: {meta.get('totalRecords', '?')}")
