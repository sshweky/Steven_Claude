---
name: QB codepage deploy policy — Chrome MCP is the standard method
description: Deploy rules for viewer.js, viewer.html, inv_mgmt.js, inv_mgmt_full.html
type: feedback
originSessionId: 192e4f01-3664-463b-bda7-b157c0280869
---

## Standard deploy method: Chrome MCP (not manual copy-paste)

Use the Chrome MCP browser tools to deploy QB codepages. Process:
1. Start CORS server: `python codepage/deploy_pages.py forecast` (background)
2. Navigate Chrome MCP tab to the QB page editor URL (e.g. pageID=49 for viewer.js)
3. Wait for Ace editor to mount, then execute the JS fetch+inject snippet via `javascript_tool`
4. Confirm save by checking for redirect to AppDBPages

The `deploy_pages.py` script now replaces `%%BUILD_TS%%` with `Date.now()` in the JS snippet,
so each deploy auto-busts the IndexedDB projection cache for all 80 users.

## Page map
- `viewer.js`          -> pageID=49  (Forecast Manager JS logic)
- `viewer.html`        -> pageID=50  (Forecast Manager HTML shell)
- `inv_mgmt.js`        -> pageID=56  (Inventory Management JS logic)
- `inv_mgmt_full.html` -> pageID=52  (Inventory Management HTML shell)

## Authorization & Deploy Prompt Rule (updated 2026-05-28)

**Always ask "Ready to deploy to QB now?" after editing any QB-deployable file.**

QB-deployable files (any change to these triggers the prompt):
- `codepage/viewer.js` (pageID=49)
- `codepage/viewer.html` (pageID=50)
- `codepage/inv_mgmt.js` (pageID=56)
- `codepage/inv_mgmt_full.html` (pageID=52)
- `scripts/viewer.py` (Python local viewer -- changes here should also prompt deploy since planners use the codepage viewer which mirrors the same features)

After every editing session that touches any of the above, end the response with:
> "Ready to deploy to QB now?"

Wait for explicit confirmation before running deploy_pages.py. Do NOT deploy proactively. Deploying overwrites the live QB page immediately for all 80 planners.

Once the user says "yes" / "deploy now" / similar, proceed with the Chrome MCP deploy flow (no extra confirmation needed).

## JS snippet template (viewer.js example)
```javascript
(async () => {
  const r = await fetch('http://localhost:8743/viewer.js');
  if (!r.ok) return 'fetch failed: ' + r.status;
  const content = (await r.text()).replace(/%%BUILD_TS%%/g, String(Date.now()));
  ace.edit(document.querySelector('.ace_editor')).setValue(content);
  document.getElementById('pagetext').value = content;
  document.getElementById('btnSaveDone').click();
  return 'viewer.js deployed, length=' + content.length;
})()
```
For HTML files, omit the `.replace(...)` line (no BUILD_TS substitution needed).
