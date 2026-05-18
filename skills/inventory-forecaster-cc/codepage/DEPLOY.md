# Deploying the QB Codepage Viewer

End-to-end steps to publish the viewer as a Quickbase code page that mirrors
the local Python viewer (`scripts/viewer.py`) one-for-one.

## Files in this folder

| File          | Purpose |
|---------------|---------|
| `viewer.html` | Layout, CSS, filter dropdowns, table headers, header badges, loading overlay |
| `viewer.js`   | Bootstrap, QB REST calls, filters, sort, render, expand panel, Use AI / Use Sugg / Flag / Comment |
| `DEPLOY.md`   | This file |

`viewer.html` (pageID=52) loads `viewer.js` (pageID=56) via
`<script src="/db/bpd24h9wy?a=dbpage&pageID=56">`.

**Page IDs (InventoryTrack app `bpd24h9wy`):**
- pageID=52 = Inventory Management Viewer HTML shell
- pageID=56 = Inventory Management Viewer JS logic
- pageID=49 = Forecast Viewer JS (separate tool - do NOT touch)
- pageID=50 = Forecast Viewer HTML (separate tool - do NOT touch)

---

## One-time QB schema work (already done)

These fields were created on the Projections table (`bpd237tvm`):

| Field          | fid  | Type           | Notes |
|----------------|------|----------------|-------|
| `AI Analysis`  | 1590 | rich text      | Auto-populated by the forecaster's bulk write-back |
| `Ord /Wk L26w` | 1591 | numeric formula| `(Nz([Ord LW]) + Nz([Ord LW-1]) + … + Nz([Ord LW-25])) / 26` |
| `Flagged`      | 1592 | checkbox       | Shared boolean flag for inventory mgr review |

A dedicated **AI Comments** table was added (2026-05-10) — see
`scripts/create_ai_comments_table.py`:

| Table / Field        | dbid / fid              | Purpose |
|----------------------|-------------------------|---------|
| `AI Comments` table  | dbid `bv2jirwts`        | Audit trail for planner-↔-AI dialogue (separate from `Projection Comments`) |
| `Acct#-MStyle`       | fid 6 (text)            | FK to Projections |
| `Note`               | fid 7 (multi-line text) | Planner instruction + `[ai-intent ...]` machine tag |
| `Author`             | fid 8 (user)            | Auto-stamps from QB session user |
| `Ignored`            | fid 9 (checkbox)        | × Ignore button flips to true so F58 stops replaying |

Codepage / viewer.py read from this table for the AI Adjustment History pane;
the F58 rule in `scripts/inventory_forecaster.py` reads from it at forecast-
run time to replay non-Ignored adjustments. The table is referenced via
`CFG.AI_COMMENTS_TID` / `CFG.AI_COMMENT_FID` in `viewer.html`.

The codepage also reads (no schema changes — pre-existing fields):
- **Inventory Flow** (`bpsaju5pm`) — `Wk1..Wk26`, `RcvWk1..RcvWk26`, `Prj Wk1..Wk26`, `Opt WOS Final`, `Next Avl Rcpt Dt`, `Country`, `Open_Supplier_POs`, `LT_Trans_Days`, `Transit_Days` — for the Inventory Flow + Gap Analysis sections
- **Projections** (`bpd237tvm`) — `Store Count`, `POG Launch Date`, `POG End Date` — for the editable POG / ISO context block

No further schema changes needed unless we eventually add `AI Adjusted` /
`Resolved` to the multi-choice options on `Projection Comments.Flag` —
optional cleanup, the AI Comments table makes it unnecessary.

---

## Step 1 — Populate `AI Analysis`

The forecaster writes the per-record narrative directly to fid 1590 during
its existing bulk write-back. Run it once before the codepage will show the
analysis text in the detail panel:

```bash
cd C:\Users\steven\.claude\skills\inventory-forecaster-cc
echo "1" | python scripts/inventory_forecaster.py --all
```

Until this runs, the codepage falls back to the existing `AI ALERT` text
(works fine — just less rich).

---

## Step 2 — Authentication (no tokens, no setup)

The codepage authenticates each visitor against QB by calling
`GET /v1/auth/temporary/{dbid}` with `credentials: 'include'`.  That
endpoint exchanges the user's existing QB session cookie (already set
because they're signed into `pim.quickbase.com`) for a 5-minute scoped
temp token, which `viewer.js` then passes as
`Authorization: QB-TEMP-TOKEN <value>` on all REST calls.

What this means in practice:

- **Nothing sensitive in the page.**  No user token to paste, no service
  account secret embedded — the QB "Insecure code: This page appears to
  include references to credentials" warning goes away.
- **Per-user permissions.**  Each visitor's token carries their own QB
  rights, so they can only see / write what their role allows.
- **Auto-refresh.**  Tokens expire after 5 minutes; `viewer.js` caches
  per-table tokens for 4 minutes and silently refreshes on `401`.

The viewer requests temp tokens for two dbids: `bpd237tvm` (Projections)
and `bpt35zccg` (Projection Comments).  Make sure every user who needs
the viewer has at least Modify rights on both.

---

## Step 3 — Deploy to QB

Run the deploy script from this folder:

```bash
cd C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\codepage
python deploy_pages.py
```

This uploads `viewer.js` to pageID=56 and `viewer.html` to pageID=52 in the
InventoryTrack app (`bpd24h9wy`) using `API_AddReplaceDBPage` targeted by ID.

After deploying, hard-refresh the viewer tab (Ctrl+Shift+R) to pick up the new version.

---

## Step 4 — Open it

```
https://pim.quickbase.com/db/bpd24h9wy?a=dbpage&pageID=52
```

Bookmark that URL or add it as an app menu link (Settings → App Properties → Variables → Pages → "Add").

---

## What the codepage does on load

1. Calls `GET /v1/fields?tableId=bpd237tvm` once to discover the 26 rolling manual-projection field IDs (they re-stamp every Sunday — labels look like `05 03 W1`).
2. Calls `POST /v1/records/query` with `Status @ Cust starts with 'A' OR starts with 'FD'`, paginated 1,000 at a time, until all ~5,100 records arrive.
3. Adapts each row into the same shape the local viewer uses (volume tier, priority, per-week severity, etc.).
4. Sorts Inv Mgr → Brand → Customer → Mstyle, populates filter dropdowns, renders 100 rows per page.

Total cold load: ~4-6 seconds for ~5,100 records over the wire (one initial fields lookup + ~6 paginated query calls).

---

## What works identically to the local viewer

- Header badges (clickable to filter by volume tier; click "X records" to clear all filters)
- All filters (search, severity, volume, priority, pattern, AI-diff, brand, inv mgr, customer, Show Flagged Only)
- Sort: Inv Mgr → Brand → Customer → Mstyle (blanks last)
- Row click expands into the W1-W26 grid with Projection / AI Forecast / Suggested / Ordered LY / Shipped LY rows + per-week severity highlighting
- 📦 **Inventory Flow section** — Beg Inv / Expected Receipts / WOS OH (1-decimal) rows from QB Inventory Flow
- ⚠️ **Gap Analysis banner** — Opt WOS / Next Avl Rcpt Dt awareness, Replen-items-only
- 📅 **POG / ISO context block** — editable POG dates + Store Count + computed ISO order window
- AI Analysis narrative shown above the grid (rich text, supports `<b>` and `<span style>`)
- Use AI / Use Sugg buttons — write directly to the rolling manual columns via `POST /v1/records` with `mergeFieldId`
- Inline Status @ Cust editor (dropdown)
- Flag (QB-backed boolean, shared across users — fid 1592)
- Auto-flag on mgr-comment textarea typing
- 🤖 Adjust AI Forecast (Tell-AI) — writes to AI Comments table; F58 replays non-Ignored entries
- × Ignore button on AI Adjustment History entries
- Two-pane comment history (planner-↔-mgr + planner-↔-AI threads)
- Add Comment — INSERTs into `Projection Comments` (`bpt35zccg`) with Note / Acct#-MStyle / Date of Week / Flag
- Last Comment display in detail panel
- Pagination, search, export-flagged-to-CSV, clear-flags-and-comments
- Loading overlay with progress messages
- **localStorage cache for Inventory Flow data** (`pp_invflow_v2`, 4-hour TTL) — bypass with `?nocache=1`

## What's deliberately omitted (no QB equivalent)

- **Send to Manager (email drafts)** — the local viewer uses Outlook COM (Windows-desktop Python). Codepages can't drive Outlook. Use the Export Flagged to CSV button + your normal email workflow.
- **L26W per-week order/ship history table inside the detail pane** — the codepage shows the `Ord /Wk L26w` aggregated average instead of the 26 individual columns. (To restore the per-week table, add 52 fids to `buildSelectFids()` in `viewer.js` for `Ord LW + Ord LW-1..LW-25 + Shp LW + Shp LW-1..LW-25`.)
- **Pattern column** — not stored in QB, left blank in the table.

---

## Updating the codepage

When you edit either file in this folder:

1. Open the page in QB (Settings ⚙ → Pages → click the page name)
2. Click **Edit**
3. Paste the new file contents over the old (Ctrl+A, Ctrl+V)
4. Save

Browsers cache codepages aggressively. After updating, hit Ctrl+Shift+R in any open viewer tabs to pick up the new version.

**Inventory Flow cache:** the codepage stores per-mstyle Inv Flow data in
`localStorage` (key `pp_invflow_v1`, 4h TTL) to avoid re-pulling on every
page load. When QB Inventory Flow schema changes (new columns, renamed
fids), bump the version string (`pp_invflow_v2` → `pp_invflow_v3`) in
`viewer.js` — every client's old cache becomes ineligible on next load
and re-pulls. Users can also force-refresh by appending `?nocache=1` to
the codepage URL.

---

## Troubleshooting

**"Could not authenticate against pim.quickbase.com"** — the temp-token call (`GET /v1/auth/temporary/{dbid}`) failed. The visitor isn't signed into QB in this browser, or the realm doesn't match `CFG.REALM`. Sign in to `pim.quickbase.com` and reload.

**"HTTP 401" / "HTTP 403"** — the visitor's QB account doesn't have access to Projections / Projection Comments. Grant them at least Modify rights on both tables. (The codepage no longer uses a service-account user token, so permissions are now per-user.)

**"Expected 26 manual projection fields, found N"** — QB's weekly column-shift action ran partially. The codepage looks for labels matching `/^\d\d\s\d\d\sW\d{1,2}$/`. Check the Projections field list and re-run the QB action that creates the next week's columns.

**Detail pane shows no narrative** — `AI Analysis` (fid 1590) is empty. Run the forecaster (Step 1) to populate it.

**Use AI / Use Sugg button shows "Fail"** — open the browser console; the error appears next to the button as a tooltip and in the `console.error` log. Most common cause: the rolling manual prj column labels were renamed mid-week; reload the page so `discoverManPrjFids()` re-runs.
