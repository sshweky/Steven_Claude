"""One-off audit: list the 28 Amazon Australia records with mismatched
Acct_MStyle_Key_, show what the key SHOULD become, check for collisions
on the target keys, and count related comment references that would orphan
if we update only Projections without touching the comment tables.
"""
import urllib.request
import json

HEADERS = {
    "QB-Realm-Hostname": "pim.quickbase.com",
    "Authorization": "QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s",
    "Content-Type": "application/json",
}


def qb_query(body):
    req = urllib.request.Request(
        "https://api.quickbase.com/v1/records/query",
        data=json.dumps(body).encode(),
        headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    # 1) Pull the 28 candidate records
    result = qb_query({
        "from":    "bpd237tvm",
        "select":  [292, 196, 363, 11, 1187, 10, 374, 197, 821, 1586],
        "where":   "({1187.EX.'1864'}OR{1187.EX.'01864'}OR{292.SW.'1864-'})"
                   "AND({196.CT.'AU'}OR{363.CT.'AUSTRALIA'})",
        "options": {"top": 500},
    })
    recs = result.get("data", [])
    print(f"Total candidate records: {len(recs)}\n")

    rows = []
    for r in recs:
        cur_key = r.get("292", {}).get("value", "") or ""
        if cur_key.startswith("1864-"):
            new_key = "1884-" + cur_key.split("-", 1)[1]
        else:
            new_key = cur_key
        rows.append({
            "cur_key": cur_key,
            "new_key": new_key,
            "ms":      r.get("196", {}).get("value", "") or "",
            "cust":    r.get("363", {}).get("value", "") or "",
            "status":  r.get("10",  {}).get("value", "") or "",
            "pt":      r.get("374", {}).get("value", "") or "",
            "brand":   r.get("197", {}).get("value", "") or "",
            "sku":     r.get("821", {}).get("value", "") or "",
            "mgr":     r.get("1586",{}).get("value", "") or "",
        })

    # Print
    print(f'{"#":>3} | {"Current Key":30s} | {"-> New Key":30s} | {"Mstyle":18s} | {"Status":7s} | {"PT Item":15s} | {"Brand":25s} | {"Cust SKU#":15s} | {"Inv Mgr":12s}')
    print("-" * 180)
    for i, r in enumerate(rows, 1):
        print(f'{i:>3} | {r["cur_key"]:30s} | {r["new_key"]:30s} | {r["ms"]:18s} | {r["status"]:7s} | {r["pt"][:15]:15s} | {r["brand"][:25]:25s} | {r["sku"][:15]:15s} | {r["mgr"][:12]:12s}')

    # 2) Collision check
    target_keys = [r["new_key"] for r in rows if r["new_key"] != r["cur_key"]]
    if target_keys:
        where = "(" + "OR".join(f"{{292.EX.'{k}'}}" for k in target_keys) + ")"
        cresult = qb_query({
            "from":    "bpd237tvm",
            "select":  [292, 196, 363],
            "where":   where,
            "options": {"top": 500},
        })
        coll = cresult.get("data", [])
        print(f"\nCollision check (existing 1884- records that would block update): {len(coll)} found")
        for cr in coll:
            print(f"  COLLISION: key={cr.get('292',{}).get('value')} | ms={cr.get('196',{}).get('value')} | cust={(cr.get('363',{}).get('value') or '')[:40]}")

    # 3) Related comment counts (AI Comments table bv2jirwts -- Acct#-MStyle FID 6)
    cur_keys = [r["cur_key"] for r in rows]
    if cur_keys:
        where = "(" + "OR".join(f"{{6.EX.'{k}'}}" for k in cur_keys) + ")"
        try:
            ai = qb_query({"from": "bv2jirwts", "select": [3, 6], "where": where, "options": {"top": 1000}})
            print(f"\nAI Comments referencing these 28 keys: {len(ai.get('data', []))}")
        except Exception as e:
            print(f"\nAI Comments lookup error: {e}")

    # 4) Projection Comments (table bpt35zccg). The FK field for Acct#-MStyle is FID 7 there.
    if cur_keys:
        where = "(" + "OR".join(f"{{7.EX.'{k}'}}" for k in cur_keys) + ")"
        try:
            pc = qb_query({"from": "bpt35zccg", "select": [3, 7], "where": where, "options": {"top": 1000}})
            print(f"Projection Comments referencing these 28 keys: {len(pc.get('data', []))}")
        except Exception as e:
            print(f"Projection Comments lookup error: {e}")


if __name__ == "__main__":
    main()
