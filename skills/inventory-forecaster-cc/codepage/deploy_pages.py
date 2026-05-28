#!/usr/bin/env python3
"""
Deploy viewer.html and viewer.js to QB InventoryTrack codepages.

HOW IT WORKS (updated 2026-05-25):
  The legacy API_AddReplaceDBPage endpoint is BROKEN -- returns errcode=0
  but saves nothing (QB platform regression, May 2026). The QB REST API
  has no /v1/pages endpoint.

  Working method:
    1. Start a local CORS server on localhost:8743 that serves the files.
    2. Chrome (logged in to QB) fetches them and injects into the Ace editor
       on the QB page-editor UI, then clicks Save.
  This matches exactly what QB's own UI does and is the only method that works.

USAGE (Claude-automated):
    python deploy_pages.py [forecast|invmgmt|all]
    -> starts CORS server, prints JS snippets to run in each page editor
    -> Claude uses Chrome MCP to navigate and inject automatically

USAGE (manual):
    1. Run this script to start the CORS server.
    2. For each page, open the QB page editor URL printed below.
    3. Open DevTools console (F12).
    4. Paste and run the JS snippet printed for that page.
    5. "Page saved" toast confirms success.
    6. Ctrl-C this script when done.

PAGE MAP:
    viewer.js          -> pageID=49  (Forecast Manager JS logic)
    viewer.html        -> pageID=50  (Forecast Manager HTML shell)
    inv_mgmt.js        -> pageID=56  (Inventory Management JS logic)
    inv_mgmt_full.html -> pageID=52  (Inventory Management HTML shell)
"""
import http.server, socketserver, threading, time, sys, os, datetime
from pathlib import Path

PORT  = 8743
HERE  = Path(__file__).parent
REALM = "pim.quickbase.com"
APP   = "bpd24h9wy"

PAGE_IDS = {
    "viewer.js":          49,
    "viewer.html":        50,
    "inv_mgmt.js":        56,
    "inv_mgmt_full.html": 52,
}


class CORSHandler(http.server.BaseHTTPRequestHandler):
    """Minimal raw handler -- avoids Python 3.13+ SimpleHTTPRequestHandler CORS
    validation that hangs cross-origin GET requests (regression vs 3.12)."""

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-cache")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        path = self.path.lstrip("/") or "index.html"
        filepath = HERE / path
        if not filepath.exists():
            self.send_response(404)
            self.end_headers()
            return
        data = filepath.read_bytes()
        ctype = "application/javascript" if path.endswith(".js") else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"  [CORS] {fmt % args}")


def start_server():
    os.chdir(HERE)
    httpd = socketserver.TCPServer(("", PORT), CORSHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def js_snippet(filename):
    # For viewer.js: replace %%BUILD_TS%% with the current epoch so the
    # IndexedDB projection-cache key changes on every deploy and all users
    # get a fresh QB fetch automatically (no manual ?nocache=1 required).
    build_ts_sub = (
        "  const content = (await r.text()).replace(/%%BUILD_TS%%/g, String(Date.now()));\n"
        if filename.endswith('.js') else
        "  const content = await r.text();\n"
    )
    return (
        f"(async () => {{\n"
        f"  const r = await fetch('http://localhost:{PORT}/{filename}');\n"
        f"  if (!r.ok) return console.error('fetch failed:', r.status);\n"
        f"{build_ts_sub}"
        f"  ace.edit(document.querySelector('.ace_editor')).setValue(content);\n"
        f"  document.getElementById('pagetext').value = content;\n"
        f"  document.getElementById('btnSaveDone').click();\n"
        f"  console.log('{filename} deployed, length=' + content.length);\n"
        f"}})();"
    )


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target == "forecast":
        pages = [("viewer.js", 49), ("viewer.html", 50)]
    elif target == "invmgmt":
        pages = [("inv_mgmt.js", 56), ("inv_mgmt_full.html", 52)]
    else:
        pages = [("viewer.js", 49), ("viewer.html", 50),
                 ("inv_mgmt.js", 56), ("inv_mgmt_full.html", 52)]

    print(f"\n=== QB Codepage Deployer ({target}) ===")
    print(f"Serving {HERE} on http://localhost:{PORT}/\n")

    httpd = start_server()
    time.sleep(0.5)
    print("Server started.\n")

    for filename, page_id in pages:
        path = HERE / filename
        size = path.stat().st_size if path.exists() else 0
        print(f"--- {filename} (page {page_id}, {size:,} bytes) ---")
        print(f"Open: https://{REALM}/nav/app/{APP}/action/pageedit?pageID={page_id}")
        print("Paste in DevTools console:")
        print(js_snippet(filename))
        print()

    print("Waiting for deployments... Ctrl-C when done.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        httpd.shutdown()
        print("\nServer stopped. Done.")
