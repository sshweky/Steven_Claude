import json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from config import QB_REALM, QB_USER_TOKEN, QB_PROJ_TABLE, QB_REST_MAX_RETRIES

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_USER_TOKEN}",
    "Content-Type":      "application/json",
}

def post(body):
    req = urllib.request.Request(
        "https://api.quickbase.com/v1/records/query",
        data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

fid_key = 292; fid_ms = 196; fid_status = 10
fid_sw_act = 1602; fid_sw_ms = 1603; fid_sw_dt = 1604

# Fetch the ORD history FIDs by getting field map
def get_field_map():
    req = urllib.request.Request(
        f"https://api.quickbase.com/v1/fields?tableId={QB_PROJ_TABLE}",
        headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        fields = json.loads(r.read())
    l2f = {}
    for f in fields:
        l2f[f["label"]] = f["id"]
        l2f[re.sub(r"\W+", "_", f["label"])] = f["id"]
    return l2f

l2f = get_field_map()

# ORD history cols (Ord_LW, Ord_LW_1 .. Ord_LW_12 = L13W)
ord_fids = []
for i in range(13):
    col = "Ord_LW" if i == 0 else f"Ord_LW_{i}"
    fid = l2f.get(col)
    if fid:
        ord_fids.append(fid)

# PRJ cols W1-W26
prj_fids = []
for label, fid in l2f.items():
    if re.match(r"^\d{2}_\d{2}_W\d+$", label):
        prj_fids.append(fid)

print(f"L13W ord FIDs found: {len(ord_fids)}")
print(f"MAN PRJ FIDs found: {len(prj_fids)}")

select = list({fid_key, fid_ms, fid_status, fid_sw_act, fid_sw_ms, fid_sw_dt}
              | set(ord_fids) | set(prj_fids))

for target in ["1864-FF12655", "1864-FF12655EC"]:
    resp = post({
        "from":    QB_PROJ_TABLE,
        "select":  select,
        "where":   f"{{{fid_key}.EX.'{target}'}}",
        "options": {"top": 1},
    })
    rows = resp.get("data") or []
    if not rows:
        print(f"\n{target}: NOT FOUND in QB (no record at any status)")
        continue
    row = rows[0]
    def nv(fid):
        return (row.get(str(fid)) or {}).get("value")
    status   = nv(fid_status)
    sw_act   = nv(fid_sw_act)
    sw_ms    = nv(fid_sw_ms)
    sw_dt    = nv(fid_sw_dt)
    ord_vals = [float(nv(f) or 0) for f in ord_fids]
    prj_vals = [float(nv(f) or 0) for f in prj_fids]
    l13_sum  = sum(ord_vals)
    prj_sum  = sum(prj_vals)
    print(f"\n{target}:")
    print(f"  Status: {status}")
    print(f"  Switchover Active: {sw_act}")
    print(f"  Switchover To MStyle: {sw_ms}")
    print(f"  Switchover Date: {sw_dt}")
    print(f"  L13W order history sum: {l13_sum:.0f} (has_orders={l13_sum > 0})")
    print(f"  MAN PRJ sum (W1-W26): {prj_sum:.0f} (has_prj={prj_sum > 0})")
