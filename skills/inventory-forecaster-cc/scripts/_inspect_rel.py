import json, urllib.request
HEADERS = {'QB-Realm-Hostname':'pim.quickbase.com','Authorization':'QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s','Content-Type':'application/json'}
BASE = 'https://api.quickbase.com/v1'

def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# All relationships on Inventory History (br6dcnv35)
print("=== Relationships on Inventory History ===")
rels = get('/tables/br6dcnv35/relationships')
print(json.dumps(rels, indent=2))

# All fields — show new ones (id > 33) and any with 'mirror' or 'weekly'
print("\n=== New / relevant fields in Inventory History ===")
fields = get('/fields?tableId=br6dcnv35')
for f in fields:
    label_lo = f['label'].lower()
    if f['id'] > 33 or 'mirror' in label_lo or 'weekly' in label_lo:
        print(f"  [{f['id']:4d}] {f['label']:<45} type={f['fieldType']}")
