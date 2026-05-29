#!/usr/bin/env python3
"""
Deploy viewer.html / viewer.js / inv_mgmt.js / inv_mgmt_full.html to
QB InventoryTrack codepages.

HOW IT WORKS (updated 2026-05-29):
  The legacy API_AddReplaceDBPage endpoint is BROKEN -- returns errcode=0
  but saves nothing (QB platform regression, May 2026). The QB REST API
  has no /v1/pages endpoint.

  Working method:
    1. Start a local CORS server on localhost:8743 that serves the files.
    2. Chrome (logged in to QB) fetches each file and injects it into the
       Ace editor on the QB page-editor UI, then clicks Save.

USAGE (Claude-automated) -- REVISED 2026-05-29:
  Run:
      python deploy_pages.py [forecast|invmgmt|all]

  Claude must follow the steps in the === CLAUDE AUTOMATION PROCEDURE ===
  block that this script prints.  The critical first step is calling
  switch_browser so Claude connects to the SAME Chrome window where the
  user is signed into QB.  Without this, navigation lands on the sign-in
  page every time.

USAGE (manual fallback):
  1. Run this script -- it starts the CORS server and prints snippets.
  2. For each page, open the URL in Chrome (signed into QB).
  3. Press F12 -> Console, paste the snippet, press Enter.
  4. Watch for "deployed, length=XXXXX" in the console.
  5. Ctrl-C this script when done.

PAGE MAP:
    viewer.js          -> pageID=49  (Forecast Manager JS logic)
    viewer.html        -> pageID=50  (Forecast Manager HTML shell)
    inv_mgmt.js        -> pageID=56  (Inventory Management JS logic)
    inv_mgmt_full.html -> pageID=52  (Inventory Management HTML shell)
"""
import http.server, socketserver, threading, time, sys, os
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
    try:
        httpd = socketserver.TCPServer(("", PORT), CORSHandler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        print(f"CORS server started on http://localhost:{PORT}/")
        return httpd
    except OSError:
        print(f"CORS server already running on port {PORT} -- reusing it.")
        return None


def js_snippet(filename):
    """One-liner that fetches the file from the local CORS server and saves it."""
    build_ts_sub = (
        "(await r.text()).replace(/%%BUILD_TS%%/g, String(Date.now()))"
        if filename.endswith('.js') else
        "await r.text()"
    )
    return (
        f"(async () => {{ "
        f"const r = await fetch('http://localhost:{PORT}/{filename}'); "
        f"if (!r.ok) return console.error('fetch failed:', r.status); "
        f"const c = {build_ts_sub}; "
        f"ace.edit(document.querySelector('.ace_editor')).setValue(c); "
        f"document.getElementById('pagetext').value = c; "
        f"document.getElementById('btnSaveDone').click(); "
        f"console.log('{filename} DEPLOYED length=' + c.length); "
        f"}})();"
    )


def check_snippet():
    """Returns 'READY', 'SIGNIN', or 'LOADING' -- run this after navigate to
    confirm the page editor loaded correctly before running the deploy snippet."""
    return (
        "document.querySelector('.ace_editor') ? 'READY' : "
        "(document.title.includes('Sign In') ? 'SIGNIN' : 'LOADING')"
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

    print(f"\n=== QB Codepage Deployer ({target}) ===\n")
    start_server()
    time.sleep(0.3)

    # ------------------------------------------------------------------ #
    #  CLAUDE AUTOMATION PROCEDURE                                          #
    #  Follow these steps exactly. Do NOT skip Step 1.                     #
    # ------------------------------------------------------------------ #
    print("""
=== CLAUDE AUTOMATION PROCEDURE ===

STEP 1 -- Connect to the correct Chrome browser (REQUIRED every session):
  Call switch_browser.  A "Connect" button will pop up in every Chrome window
  that has the extension installed.  Ask the user to click it in the Chrome
  window where they are signed into pim.quickbase.com.
  Wait for switch_browser to confirm the connection before proceeding.
  This ensures the MCP tab shares the QB session cookie.

STEP 2 -- For each page listed below:
  a. Call tabs_create_mcp to create a fresh MCP tab (avoids chrome:// restrictions).
  b. Call navigate on that tab to the page editor URL.
  c. Wait 4 seconds (page load).
  d. Call javascript_tool with the CHECK SNIPPET to verify state:
       READY  -> proceed to deploy
       SIGNIN -> QB session not found; tell user to sign into QB in this tab,
                 wait for confirmation, then retry from step (b).
       LOADING -> wait 2 more seconds and recheck.
  e. Call javascript_tool with the DEPLOY SNIPPET.
  f. Wait 3 seconds, then verify with javascript_tool:
       document.title  ->  should still contain 'Edit Page' (not redirect).
  g. Confirm "DEPLOYED length=XXXXX" appears in the console output.
""")

    for filename, page_id in pages:
        path = HERE / filename
        size = path.stat().st_size if path.exists() else 0
        print(f"--- {filename} (page {page_id}, {size:,} bytes) ---")
        print(f"  URL:          https://{REALM}/nav/app/{APP}/action/pageedit?pageID={page_id}")
        print(f"  CHECK:        {check_snippet()}")
        print(f"  DEPLOY:       {js_snippet(filename)}")
        print()

    print("CORS server running. Claude: proceed with Step 1 (switch_browser) now.")
    print("Ctrl-C to stop the server when all pages are deployed.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nServer stopped.")
