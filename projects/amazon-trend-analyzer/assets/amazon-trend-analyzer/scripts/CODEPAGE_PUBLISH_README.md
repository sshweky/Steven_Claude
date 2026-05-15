# Publishing the dashboard as a Quickbase Code Page

Once the dashboard design is stable, you can host it inside QB itself as
two Code Pages. Pattern modeled on the existing nielsen_dashboard_publish.py.

## Architecture

```
   ┌─ amazon-trend-dashboard.html  (~50 KB shell, rarely changes)
   │
   │   on load, fetches ↓
   │
   └─ amazon-trend-data.json       (~14 MB data, refreshed nightly)
```

Both live under the same QB app. Users open the HTML page; their browser
makes one fetch to the sibling JSON page; React renders the dashboard.

## One-time setup

```bash
# Generate a user token with write access to the parent QB app.
# Scope it to a single app for least privilege.
export QB_REALM="petspeople.quickbase.com"          # the realm where Code Pages live
export QB_USER_TOKEN="b9xxx_xxxx_xxxx"
export QB_APP_DBID="bqkdiemav"                       # parent app DBID (Amazon_AdTrack)
```

> ⚠️ `QB_REALM` for publishing may be different from `QB_REALM` for the
> data pull. The data pull uses `pim.quickbase.com` (Daily_Metrics lives
> there). Code Pages typically live in the operational realm. Verify both
> by checking the URL when you're logged into each in a browser.

## Nightly refresh workflow

Three commands chained in sequence:

```bash
# 1. Pull fresh daily metrics from QB
export QB_USER_TOKEN_PIM="b9xxx_xxxx_xxxx"          # for pim.quickbase.com
QB_USER_TOKEN=$QB_USER_TOKEN_PIM \
  python scripts/qb_rest_pull.py

# 2. Build the JSON payload (not the full HTML — just the data)
python scripts/build_dashboard_from_chunks.py \
    --emit-json ./qb_chunks/amazon-trend-data.json

# 3. Publish both Code Pages
export QB_USER_TOKEN_APP="d4yyy_yyyy_yyyy"          # for petspeople.quickbase.com
QB_USER_TOKEN=$QB_USER_TOKEN_APP \
  python scripts/trend_dashboard_publish.py
```

On first run, the publish script will print two pageids. Pin them:

```bash
export QB_DATA_PAGE_ID=12     # whatever first run shows
export QB_HTML_PAGE_ID=13
```

After that, subsequent runs **replace** the pages in place rather than
creating new ones.

## Scheduling

Add the three-command sequence above to your existing nightly job (Windows
Task Scheduler entry that runs the Nielsen refresh, presumably). Same
machine, same time slot, same token-rotation discipline.

If you want the trend refresh to **fail-soft** (so a Nielsen problem doesn't
break trend or vice versa), wrap each in a try/except so they don't share
fate.

## Verification

After publishing, open the dashboard URL:

```
https://<realm>.quickbase.com/db/<APP_DBID>?a=dbpage&pagename=amazon-trend-dashboard.html
```

If you see "Loading trend data..." for more than 5 seconds, open browser
devtools and check the Network tab — the fetch to `amazon-trend-data.json`
should be 200 OK and ~14 MB. If it's 403, the JSON page wasn't published
with the same visibility settings; check page permissions in QB.

## Re-publishing only the JSON (most common case)

Once the HTML shell is stable, **you only need to re-emit JSON** — the HTML
page never changes between refreshes. To skip the HTML upload, comment out
the HTML block in `trend_dashboard_publish.py` or add a `--json-only` flag.

## Files involved

```
scripts/
├── qb_rest_pull.py                  pulls Daily_Metrics → qb_chunks/page_*.csv
├── build_dashboard_from_chunks.py   stitches + analyzes → either:
│                                      - all_daily.csv + HTML (default mode)
│                                      - amazon-trend-data.json (--emit-json mode)
└── trend_dashboard_publish.py       uploads JSON + HTML to QB as Code Pages

assets/
├── dashboard_template.html          embedded-data version (for local viewing)
└── dashboard_template_codepage.html fetch-based version (for QB Code Page)
```
