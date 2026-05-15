#!/usr/bin/env python3
"""
Deploy viewer.html and viewer.js to QB InventoryTrack codepages.
Handles U+FFFF (invalid in XML 1.0) by replacing with U+FFFD before upload.
U+FFFD sorts just below U+FFFE/FFFF so sort-last semantics are preserved.
"""
import urllib.request, urllib.error, urllib.parse, re
from html import escape as html_escape
from pathlib import Path

TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
REALM  = "pim.quickbase.com"
APP_ID = "bpd24h9wy"
URL    = f"https://{REALM}/db/{APP_ID}"
HERE   = Path(__file__).parent

def upload_page(filename: str):
    path = HERE / filename
    content = path.read_text(encoding="utf-8")
    # U+FFFF is an invalid XML 1.0 character — replace with U+FFFD (replacement
    # character) which sorts just below FFFF so sort-last semantics are preserved.
    FFFF  = "￿"   # literal U+FFFF
    FFFD  = "�"   # literal U+FFFD (valid in XML 1.0, ≤ FFFF)
    n_replaced = content.count(FFFF)
    content_xml = content.replace(FFFF, FFFD)

    # Verify no more invalid XML 1.0 chars remain
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

    esc  = html_escape(content_xml)
    body = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<qdbapi>\n'
        f'  <usertoken>{TOKEN}</usertoken>\n'
        f'  <pagename>{filename}</pagename>\n'
        f'  <pagetype>1</pagetype>\n'
        f'  <pagetext>{esc}</pagetext>\n'
        f'</qdbapi>'
    )

    xml_bytes = body.encode("utf-8")
    print(f"{filename}: {len(content):,} chars  →  XML {len(xml_bytes):,}B  "
          f"(U+FFFF→FFFD: {n_replaced})")

    req = urllib.request.Request(
        f"{URL}?a=API_AddReplaceDBPage",
        data=xml_bytes,
        headers={"Content-Type": "application/xml", "QB-Realm-Hostname": REALM},
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
    ok_js   = upload_page("viewer.js")
    ok_html = upload_page("viewer.html")
    if ok_js and ok_html:
        print("\n[OK] Both pages deployed. Hard-refresh the viewer tab (Ctrl+Shift+R).")
    else:
        print("\n[WARN] One or more uploads failed.")
