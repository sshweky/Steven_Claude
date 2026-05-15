#!/usr/bin/env python3
"""Discover the field IDs and weekly column labels we need for:
    1. POG Start Date / POG End Date / Store Count on Projections
    2. Rolling weekly columns on Inventory Flow (labels like '05 10 W1')
Run once; copy the printed constants into viewer.html + viewer.py.
"""
import json
import re
import urllib.request

REALM      = "pim.quickbase.com"
USER_TOKEN = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
HEADERS = {
    "QB-Realm-Hostname": REALM,
    "Authorization":     f"QB-USER-TOKEN {USER_TOKEN}",
    "Content-Type":      "application/json",
}

PROJECTIONS_TID  = "bpd237tvm"
INVFLOW_TID      = "bpsaju5pm"


def get_fields(tid):
    url = f"https://api.quickbase.com/v1/fields?tableId={tid}"
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    print("\n=== Projections (bpd237tvm): POG / Store Count fields ===")
    pf = get_fields(PROJECTIONS_TID)
    needles = ("pog start", "pog end", "pog ", "store count", "stores",
               "iso", "lead time", "in store", "in-store")
    matches = []
    for f in pf:
        lbl = (f.get("label") or "").strip()
        ll  = lbl.lower()
        if any(n in ll for n in needles):
            matches.append((f["id"], f.get("fieldType"), lbl))
    matches.sort()
    for fid, ft, lbl in matches:
        print(f"  fid {fid:>4}  [{ft:14}]  {lbl}")

    print("\n=== Inventory Flow (bpsaju5pm): weekly rolling fields ===")
    inv = get_fields(INVFLOW_TID)
    # First — Gap Analysis fields (Next Avl Rcpt Dt, Opt WOS, related)
    gap_needles = ("next avl", "next available", "opt wos", "optimal wos",
                   "min wos", "target wos", "safety stock", "lead time")
    gap_matches = []
    for f in inv:
        ll = (f.get("label") or "").strip().lower()
        if any(n in ll for n in gap_needles):
            gap_matches.append((f["id"], f.get("fieldType"), f.get("label")))
    if gap_matches:
        print("\n  -- Gap Analysis fields (Next Avl Rcpt Dt, Opt WOS, etc.) --")
        for fid, ft, lbl in sorted(gap_matches):
            print(f"    fid {fid:>4}  [{ft:14}]  {lbl}")
    # Rolling W1..W26 labels look like "05 10 W1" or "05/10 W1" — same pattern
    # the codepage's discoverManPrjFids() uses.  Also surface the FK column
    # (likely "Acct#-MStyle" or similar) to know how to join.
    # Allow space, slash, OR underscore between MM and DD ("05 10 W1", "05/10 W1", "05_10 W1")
    week_re = re.compile(r"^\s*\d\d[\s/_]\d\d[\s_]+W(\d{1,2})\b", re.IGNORECASE)
    # Receipts pattern: "RcvWk1" ... "RcvWk26"
    rcv_re  = re.compile(r"^\s*Rcv\s*Wk\s*(\d{1,2})\s*$", re.IGNORECASE)
    # Projections pattern: "Prj Wk1" ... "Prj Wk26"
    prj_re  = re.compile(r"^\s*Prj\s*Wk\s*(\d{1,2})\s*$", re.IGNORECASE)
    # Beginning-of-week balance: bare "Wk1" .. "Wk26"
    wkraw_re = re.compile(r"^\s*Wk\s*(\d{1,2})\s*$", re.IGNORECASE)
    weekly, rcv_weekly, prj_weekly, wk_weekly = [], [], [], []
    fk_candidates = []
    debug_w = []
    for f in inv:
        lbl = (f.get("label") or "").strip()
        if (m := rcv_re.match(lbl)):
            rcv_weekly.append((int(m.group(1)), f["id"], lbl, f.get("fieldType")))
            continue
        if (m := prj_re.match(lbl)):
            prj_weekly.append((int(m.group(1)), f["id"], lbl, f.get("fieldType")))
            continue
        if (m := wkraw_re.match(lbl)):
            wk_weekly.append((int(m.group(1)), f["id"], lbl, f.get("fieldType")))
            continue
        m = week_re.match(lbl)
        if m:
            weekly.append((int(m.group(1)), f["id"], lbl, f.get("fieldType")))
        elif "W" in lbl and re.search(r"\bW\d{1,2}\b", lbl):
            debug_w.append((f["id"], f.get("fieldType"), lbl))
        elif "acct" in lbl.lower() or "mstyle" in lbl.lower() or "key" in lbl.lower():
            fk_candidates.append((f["id"], f.get("fieldType"), lbl))
    if not weekly and debug_w:
        print("\n  -- DEBUG: fields with 'Wn' but didn't match strict regex --")
        for fid, ft, lbl in debug_w[:30]:
            print(f"    fid {fid:>4}  [{ft:14}]  {lbl!r}")
    print("\n  -- FK candidates (for joining to Projections) --")
    for fid, ft, lbl in fk_candidates:
        print(f"    fid {fid:>4}  [{ft:14}]  {lbl}")
    print(f"\n  -- Weekly W1..W26 columns (n={len(weekly)}) --")
    weekly.sort()
    for w, fid, lbl, ft in weekly:
        print(f"    W{w:<2}  fid {fid:>4}  [{ft:12}]  {lbl}")

    if weekly:
        print("\n  As Python list (W1..Wn order, MM_DD_Wn pattern):")
        wlist = [str(t[1]) for t in weekly]
        print("    INV_FLOW_WK_FIDS = [" + ", ".join(wlist) + "]")
    if wk_weekly:
        wk_weekly.sort()
        print(f"\n  -- Beg Inv 'Wk1..Wkn' columns (n={len(wk_weekly)}) --")
        for w, fid, lbl, ft in wk_weekly:
            print(f"    Wk{w:<2}  fid {fid:>4}  [{ft:12}]  {lbl}")
        print("\n  As Python list (W1..Wn):")
        print("    INV_FLOW_BEG_FIDS = [" + ", ".join(str(t[1]) for t in wk_weekly) + "]")
    if rcv_weekly:
        rcv_weekly.sort()
        print(f"\n  -- Receipts 'RcvWk1..RcvWkn' columns (n={len(rcv_weekly)}) --")
        for w, fid, lbl, ft in rcv_weekly:
            print(f"    RcvWk{w:<2}  fid {fid:>4}  [{ft:12}]  {lbl}")
        print("\n  As Python list (W1..Wn):")
        print("    INV_FLOW_RCV_FIDS = [" + ", ".join(str(t[1]) for t in rcv_weekly) + "]")
    if prj_weekly:
        prj_weekly.sort()
        print(f"\n  -- Projections 'Prj Wk1..Wkn' columns (n={len(prj_weekly)}) --")
        for w, fid, lbl, ft in prj_weekly:
            print(f"    PrjWk{w:<2}  fid {fid:>4}  [{ft:12}]  {lbl}")
        print("\n  As Python list (W1..Wn):")
        print("    INV_FLOW_PRJ_FIDS = [" + ", ".join(str(t[1]) for t in prj_weekly) + "]")
    print()


if __name__ == "__main__":
    main()
