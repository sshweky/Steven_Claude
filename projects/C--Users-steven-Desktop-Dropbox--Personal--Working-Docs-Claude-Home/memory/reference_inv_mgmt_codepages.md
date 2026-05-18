---
name: Inventory Management Viewer - correct files and page IDs
description: The real production files for the Inventory Management Viewer are inv_mgmt_full.html and inv_mgmt.js — NOT viewer.html/viewer.js
type: reference
originSessionId: 192e4f01-3664-463b-bda7-b157c0280869
---
## Production Files (Inventory Management Viewer)

| File | QB Page ID | Size |
|------|-----------|------|
| `inv_mgmt_full.html` | 52 | ~136 KB |
| `inv_mgmt.js` | 56 | ~81 KB |

Both files live in:
`C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\codepage\`

## Other Pages (DO NOT TOUCH)

| Page ID | Files | What it is |
|---------|-------|------------|
| 49 | (Forecast Viewer JS) | Forecast Viewer - separate tool |
| 50 | (Forecast Viewer HTML) | Forecast Viewer - separate tool |

## viewer.html / viewer.js

These files in the same codepage folder are NOT the production Inventory Management Viewer. Do not deploy them to pages 52/56.

## Deploy Script

`deploy_pages.py` in the same folder — deploys inv_mgmt_full.html -> pageID=52, inv_mgmt.js -> pageID=56.

**WARNING:** As of 2026-05-18, `API_AddReplaceDBPage` returns errcode=0 but does NOT write content (silent failure). Pages must be restored manually via QB UI (Settings > Pages > Edit > paste content). Root cause unknown — investigate before next deploy.

## Manual Restore Steps

1. QB Settings ⚙ → Pages
2. Find page 52 or 56 → Edit
3. Select all → delete → paste from local file → Save
