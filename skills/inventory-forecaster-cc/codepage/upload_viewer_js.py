#!/usr/bin/env python3
"""Upload viewer.js to QuickBase page 49 (InventoryTrack app) via XML API."""

import urllib.request
import urllib.error
import os

APP_DB     = "bpd24h9wy"
PAGE_ID    = "49"
PAGE_NAME  = "viewer.js"
USER_TOKEN = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"

script_dir = os.path.dirname(os.path.abspath(__file__))
js_file    = os.path.join(script_dir, "viewer.js")

print(f"Reading {js_file}...")
with open(js_file, "r", encoding="utf-8") as f:
    content = f.read()

# Strip BOM if present
content = content.replace('﻿', '')

print(f"File size: {len(content):,} chars")

url = f"https://pim.quickbase.com/db/{APP_DB}?a=API_AddReplaceDBPage"

xml_body = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<qdbapi>'
    f'<usertoken>{USER_TOKEN}</usertoken>'
    f'<pageid>{PAGE_ID}</pageid>'
    '<pagetype>1</pagetype>'
    f'<pagename>{PAGE_NAME}</pagename>'
    f'<pagebody><![CDATA[{content}]]></pagebody>'
    '</qdbapi>'
)

print(f"XML body size: {len(xml_body):,} chars")
print(f"Uploading to page {PAGE_ID} ({PAGE_NAME})...")

req = urllib.request.Request(
    url,
    data=xml_body.encode("utf-8"),
    headers={"Content-Type": "text/xml; charset=UTF-8"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        response_text = resp.read().decode("utf-8")
        print("\n--- QB Response ---")
        print(response_text)
        if "<errcode>0</errcode>" in response_text:
            print("\nUpload successful!")
        else:
            print("\nUpload failed -- check errcode above")
except urllib.error.URLError as e:
    print(f"\nRequest failed: {e}")
