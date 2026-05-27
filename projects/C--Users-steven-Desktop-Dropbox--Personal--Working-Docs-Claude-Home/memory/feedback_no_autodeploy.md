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

## Authorization
User has authorized Claude to deploy using Chrome MCP **when the user explicitly confirms the deploy scope** in the conversation (e.g. "deploy viewer.js", "ok to deploy", "deploy now"). No separate "are you sure?" prompt needed once scope is confirmed.

**Do NOT deploy proactively or without explicit confirmation.** Deploying overwrites the live QB page immediately for all planners. After editing codepage files, summarize what changed and wait for the user to say to deploy.

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
