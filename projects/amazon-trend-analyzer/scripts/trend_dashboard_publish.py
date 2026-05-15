#!/usr/bin/env python3
"""
trend_dashboard_publish.py — Publish the amazon-trend-analyzer dashboard
to Quickbase as two Code Pages:

    1. amazon-trend-data.json      — the data payload (refreshed each run)
    2. amazon-trend-dashboard.html — the React shell (refreshed only when
                                     the UI itself changes)

Uses the QB JSON-RPC endpoint API_AddReplaceDBPage. Modeled on the
existing nielsen_dashboard_publish.py.

CONFIG via environment variables:
    QB_REALM        e.g. petspeople.quickbase.com   (NOT pim.quickbase.com — that's the data realm)
    QB_USER_TOKEN   user token with write access to QB_APP_DBID
    QB_APP_DBID     parent app DBID where the Code Pages live
    QB_DATA_PAGE_ID (optional) numeric pageid for the data page after first publish
    QB_HTML_PAGE_ID (optional) numeric pageid for the html page after first publish

USAGE:
    # First publish (creates pages, prints the assigned pageids):
    python scripts/trend_dashboard_publish.py

    # After first publish, pin the pageids so we replace in place:
    export QB_DATA_PAGE_ID=12
    export QB_HTML_PAGE_ID=13
    python scripts/trend_dashboard_publish.py
"""

from __future__ import annotations

import os
import sys
import re
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Install dependencies first:  pip install requests")


# ─── CONFIG ───────────────────────────────────────────────────────────────

REALM        = os.environ.get("QB_REALM",        "petspeople.quickbase.com")
USER_TOKEN   = os.environ.get("QB_USER_TOKEN",   "")
APP_DBID     = os.environ.get("QB_APP_DBID",     "")
DATA_PAGE_ID = os.environ.get("QB_DATA_PAGE_ID", "")
HTML_PAGE_ID = os.environ.get("QB_HTML_PAGE_ID", "")

# File locations relative to this script's parent (project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = PROJECT_ROOT / "qb_chunks" / "amazon-trend-data.json"
HTML_PATH = PROJECT_ROOT / "assets"    / "dashboard_template_codepage.html"

# Page names as they will appear in Quickbase
DATA_PAGE_NAME = "amazon-trend-data.json"
HTML_PAGE_NAME = "amazon-trend-dashboard.html"


# ─── QB API (JSON-RPC, like nielsen_dashboard_publish.py) ─────────────────

def api_add_replace_dbpage(page_id: str | int | None,
                            page_name: str,
                            page_body: str,
                            page_type: int = 1) -> str:
    """Call API_AddReplaceDBPage. Returns the numeric pageid assigned by QB.

    Args:
        page_id:  existing pageid to replace, or "" / None to create new
        page_name: filename (must end in .html for type=1, anything for type=2)
        page_body: full page content as a string
        page_type: 1 = HTML/web page, 2 = Exact Forms / Code Page (text)

    NOTE: page_type=1 includes JSON and HTML pages alike — QB treats both as
    "web pages" served via dbpage. The distinction is just the file extension.
    """
    url = f"https://{REALM}/db/{APP_DBID}"

    # The body must be wrapped in <pagebody> within the XML envelope.
    # Special chars (<, >, &) inside the body need escaping for the XML
    # wrapper, but NOT inside CDATA. We use CDATA to keep the body literal.
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<qdbapi>
  <usertoken>{USER_TOKEN}</usertoken>
  <pagename>{page_name}</pagename>
  <pagetype>{page_type}</pagetype>
  <pagebody><![CDATA[{page_body}]]></pagebody>"""
    if page_id:
        xml += f"\n  <pageid>{page_id}</pageid>"
    xml += "\n</qdbapi>"

    headers = {
        "Content-Type": "application/xml",
        "QUICKBASE-ACTION": "API_AddReplaceDBPage",
    }

    r = requests.post(url, data=xml.encode("utf-8"), headers=headers, timeout=120)

    # Don't use raise_for_status — it hides the response body. QB sends useful
    # error details in the body that we need to see for 400 / 4xx errors.
    if not r.ok:
        print(f"\n  ✗ HTTP {r.status_code} from QB")
        print(f"    Response headers: {dict(r.headers)}")
        print(f"    Response body (first 2000 chars):")
        print("    " + r.text[:2000].replace("\n", "\n    "))
        sys.exit(f"[ABORT] Publish failed with HTTP {r.status_code}")

    body = r.text
    # Parse <errcode>0</errcode> for success
    err = re.search(r"<errcode>(\d+)</errcode>", body)
    if err and err.group(1) != "0":
        detail = re.search(r"<errtext>(.*?)</errtext>", body, re.DOTALL)
        sys.exit(f"[ABORT] QB API error code {err.group(1)}: "
                  f"{detail.group(1) if detail else body[:500]}")

    pageid_match = re.search(r"<pageid>(\d+)</pageid>", body)
    if not pageid_match:
        sys.exit(f"[ABORT] No pageid in response: {body[:500]}")
    return pageid_match.group(1)


# ─── MAIN ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-only", action="store_true",
                    help="Publish only the HTML shell (skip the JSON data)")
    ap.add_argument("--json-only", action="store_true",
                    help="Publish only the JSON data (skip the HTML shell)")
    args = ap.parse_args()

    # Validate config
    if not USER_TOKEN or "PASTE" in USER_TOKEN.upper():
        sys.exit("[ABORT] Set QB_USER_TOKEN env var")
    if not APP_DBID:
        sys.exit("[ABORT] Set QB_APP_DBID env var (parent app DBID for the "
                  "Code Pages — e.g. bqkdiemav for Amazon_AdTrack)")

    # Validate files exist
    if not args.html_only and not JSON_PATH.exists():
        sys.exit(f"[ABORT] JSON payload not found at {JSON_PATH}\n"
                  f"        Run:  python scripts/build_dashboard_from_chunks.py "
                  f"--emit-json {JSON_PATH}")
    if not args.json_only and not HTML_PATH.exists():
        sys.exit(f"[ABORT] HTML template not found at {HTML_PATH}")

    json_size_mb = JSON_PATH.stat().st_size / (1024 * 1024) if JSON_PATH.exists() else 0
    html_size_kb = HTML_PATH.stat().st_size / 1024 if HTML_PATH.exists() else 0

    print("─" * 60)
    print(f"realm:    {REALM}")
    print(f"app:      {APP_DBID}")
    if not args.html_only:
        print(f"data:     {JSON_PATH.name}  ({json_size_mb:.1f} MB)")
    if not args.json_only:
        print(f"html:     {HTML_PATH.name}  ({html_size_kb:.1f} KB)")
    print("─" * 60)

    data_pid = DATA_PAGE_ID
    html_pid = HTML_PAGE_ID

    # 1. Publish data page (unless skipped)
    if not args.html_only:
        print(f"\nPublishing {DATA_PAGE_NAME} ...")
        json_body = JSON_PATH.read_text(encoding="utf-8")
        data_pid = api_add_replace_dbpage(
            page_id=DATA_PAGE_ID or None,
            page_name=DATA_PAGE_NAME,
            page_body=json_body,
            page_type=1,
        )
        print(f"  ✓ pageid={data_pid}")

    # 2. Publish HTML page (unless skipped)
    if not args.json_only:
        print(f"\nPublishing {HTML_PAGE_NAME} ...")
        html_body = HTML_PATH.read_text(encoding="utf-8")
        html_pid = api_add_replace_dbpage(
            page_id=HTML_PAGE_ID or None,
            page_name=HTML_PAGE_NAME,
            page_body=html_body,
            page_type=1,
        )
        print(f"  ✓ pageid={html_pid}")

    # 3. Print URLs + setup notes
    dash_url = f"https://{REALM}/db/{APP_DBID}?a=dbpage&pagename={HTML_PAGE_NAME}"
    data_url = f"https://{REALM}/db/{APP_DBID}?a=dbpage&pagename={DATA_PAGE_NAME}"
    print(f"\n{'='*60}")
    print(f"✅ Published. Dashboard URL:")
    print(f"   {dash_url}")
    print(f"\nData page (for debugging):")
    print(f"   {data_url}")

    if data_pid and not DATA_PAGE_ID:
        print(f"\n⚡ FIRST RUN — pin the data pageid in your environment:")
        print(f"     export QB_DATA_PAGE_ID={data_pid}")
    if html_pid and not HTML_PAGE_ID:
        print(f"     export QB_HTML_PAGE_ID={html_pid}")
    print("="*60)


if __name__ == "__main__":
    main()
