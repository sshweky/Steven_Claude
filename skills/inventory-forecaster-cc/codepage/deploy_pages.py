#!/usr/bin/env python3
"""
Deploy BOTH viewers to QB InventoryTrack app (bpd24h9wy).

  FORECAST MANAGER VIEWER (projections, flag comments, AI analysis):
    pageID=49  ->  viewer.js          (JS logic)
    pageID=50  ->  viewer.html        (HTML shell, loads viewer.js via pageID=49)

  INVENTORY MANAGEMENT VIEWER (OOS gap analysis, PO recommendations):
    pageID=52  ->  inv_mgmt_full.html (HTML shell, loads inv_mgmt.js via pageID=56)
    pageID=56  ->  inv_mgmt.js        (JS logic)

Handles U+FFFF (invalid in XML 1.0) by replacing with U+FFFD before upload.
"""
import urllib.request, urllib.error, re
from pathlib import Path

TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
REALM  = "pim.quickbase.com"
APP_ID = "bpd24h9wy"
URL    = f"https://{REALM}/db/{APP_ID}"
HERE   = Path(__file__).parent

# Map filename -> production page ID
PAGE_IDS = {
    "viewer.js":          49,   # Forecast Manager Viewer - JS
    "viewer.html":        50,   # Forecast Manager Viewer - HTML
    "inv_mgmt_full.html": 52,   # Inventory Management Viewer - HTML
    "inv_mgmt.js":        56,   # Inventory Management Viewer - JS
}

def upload_page(filename: str):
    page_id = PAGE_IDS[filename]
    path    = HERE / filename
    content = path.read_text(encoding="utf-8")

    FFFF  = "￿"
    FFFD  = "�"
    n_replaced = content.count(FFFF)
    content_xml = content.replace(FFFF, FFFD)

    def is_invalid(c):
        n = ord(c)
        if n in (0x9, 0xA, 0xD): return False
        if 0x20 <= n <= 0xD7FF:  return False
        if 0xE000 <= n <= 0xFFFD: return False
        if 0x10000 <= n <= 0x10FFFF: return False
        return True
    still_bad = [c for c in content_xml if is_invalid(c)]
    if still_bad:
        print(f"  [WARN] {len(still_bad)} remaining invalid XML chars: "
              f"{[hex(ord(c)) for c in set(still_bad)]}")

    # QB requires <pagebody> with CDATA -- <pagetext> returns errcode=0 but writes blank content
    body = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<qdbapi>'
        f'<usertoken>{TOKEN}</usertoken>'
        f'<pageID>{page_id}</pageID>'
        f'<pagetype>1</pagetype>'
        f'<pagebody><![CDATA[{content_xml}]]></pagebody>'
        f'</qdbapi>'
    )

    xml_bytes = body.encode("utf-8")
    print(f"{filename} -> pageID={page_id}: {len(content):,} chars  ->  XML {len(xml_bytes):,}B  "
          f"(U+FFFF->FFFD: {n_replaced})")

    req = urllib.request.Request(
        f"{URL}?a=API_AddReplaceDBPage",
        data=xml_bytes,
        headers={"Content-Type": "text/xml; charset=UTF-8", "QB-Realm-Hostname": REALM},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = r.read().decode("utf-8")
        errm = re.search(r"<errcode>(\d+)</errcode>", resp)
        pid  = re.search(r"<pageID>(\d+)</pageID>", resp)
        errt = re.search(r"<errtext>(.*?)</errtext>", resp)
        code = errm.group(1) if errm else "?"
        print(f"  -> errcode={code}  pageID={pid.group(1) if pid else '?'}"
              f"  ({errt.group(1) if errt else ''})")
        return code == "0"
    except urllib.error.HTTPError as e:
        print(f"  -> HTTP {e.code}: {e.read().decode('utf-8')[:300]}")
        return False

if __name__ == "__main__":
    import sys
    # Usage: python deploy_pages.py [forecast|invmgmt|all]
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    results = []
    if target in ("forecast", "all"):
        print("--- Forecast Manager Viewer (pages 49/50) ---")
        results.append(upload_page("viewer.js"))
        results.append(upload_page("viewer.html"))
    if target in ("invmgmt", "all"):
        print("--- Inventory Management Viewer (pages 52/56) ---")
        results.append(upload_page("inv_mgmt.js"))
        results.append(upload_page("inv_mgmt_full.html"))

    if all(results):
        print("\n[OK] All pages deployed. Hard-refresh open tabs (Ctrl+Shift+R).")
    else:
        print("\n[WARN] One or more uploads failed.")
