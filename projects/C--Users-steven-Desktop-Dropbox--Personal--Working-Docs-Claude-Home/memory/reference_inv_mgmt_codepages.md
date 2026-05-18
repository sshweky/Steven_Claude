---
name: QB Codepage viewer architecture - which files go to which pages
description: Two separate viewers in InventoryTrack app (bpd24h9wy). Forecast Manager = viewer.html/viewer.js on pages 50/49. Inventory Management = inv_mgmt_full.html/inv_mgmt.js on pages 52/56.
type: reference
originSessionId: 192e4f01-3664-463b-bda7-b157c0280869
---
## Two Viewers -- MEMORIZE THIS

Both live in: `C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\codepage\`

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

## Deploy Script

`deploy_pages.py` -- supports targeted deploys:
- `python deploy_pages.py forecast`  -> deploys viewer.js (49) + viewer.html (50)
- `python deploy_pages.py invmgmt`   -> deploys inv_mgmt.js (56) + inv_mgmt_full.html (52)
- `python deploy_pages.py all`       -> deploys all four pages

**NEVER run without explicit user instruction.**

## Notes

- `API_GetDBPage` (legacy XML API) returns empty pagebody even for pages that have content -- do not use it to verify deploys.
- `viewer_qb_current.js` in the same folder is a stale backup snapshot -- not needed.
