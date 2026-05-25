---
name: QB Codepage viewer architecture - which files go to which pages
description: Two separate viewers in InventoryTrack app (bpd24h9wy). Forecast Manager = viewer.html/viewer.js on pages 50/49. Inventory Management = inv_mgmt_full.html/inv_mgmt.js on pages 52/56.
type: reference
originSessionId: 192e4f01-3664-463b-bda7-b157c0280869
---
## Two Viewers -- MEMORIZE THIS

Both live in: `C:\Users\steven\.claude\skills\inventory-forecaster-cc\codepage\`

**CANONICAL PATH -- the ONLY correct location. Never use the Dropbox path. The stale Dropbox copy at C:\Users\steven\Desktop\Dropbox (Personal)\Working Docs\Claude Home\inventory-forecaster-cc\ has been permanently deleted.**

### Forecast Manager Viewer
Projections, flag comments, AI analysis, planner responses.

| File | QB Page ID |
|------|-----------|
| `viewer.html` | 50 (HTML shell) |
| `viewer.js` | 49 (JS logic, loaded by page 50 via pageID=49) |

### Inventory Management Viewer
OOS gap analysis, PO recommendations, inventory flow.

| File | QB Page ID |
|------|-----------|
| `inv_mgmt_full.html` | 52 (HTML shell) |
| `inv_mgmt.js` | 56 (JS logic, loaded by page 52 via pageID=56) |

## Deploy Method (updated 2026-05-25)

**The legacy `API_AddReplaceDBPage` XML API is BROKEN** -- returns errcode=0 but saves nothing. QB REST API has no /v1/pages endpoint. Never use `deploy_pages.py` with the old API approach.

### Working deploy procedure:
1. Run `python codepage/deploy_pages.py` from the repo root -- starts a local CORS server on localhost:8743
2. For each page (viewer.js p49, viewer.html p50):
   a. Navigate to the QB page editor: `https://pim.quickbase.com/nav/app/bpd24h9wy/action/pageedit?pageID=49` (or 50)
   b. Open DevTools console (F12)
   c. Paste and run the JS snippet printed by the script
   d. "Page saved" toast confirms success
3. Ctrl-C the script when done

**Alternative (Claude-automated):** Use the Chrome MCP to navigate to each editor, inject content via `fetch('http://localhost:8743/viewer.js')`, set Ace editor value, click Save.

**Verify**: `fetch('https://pim.quickbase.com/db/bpd24h9wy?a=dbpage&pageID=49', {credentials:'include'})` from a QB tab -- check `.length > 0`.

**NEVER run without explicit user instruction.**

## Notes

- `API_GetDBPage` (legacy XML API) always returns empty pagebody -- broken, do not use to verify.
- Verification must be done via the `dbpage` endpoint from a logged-in browser session.
- `viewer_qb_current.js` in the same folder is a stale backup snapshot -- not needed.
