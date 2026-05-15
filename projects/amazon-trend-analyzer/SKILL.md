---
name: amazon-trend-analyzer
description: "Detect and explain sales trends across thousands of Amazon ASINs using triangulated L4-vs-L13/L26/L52 time-window analysis. TRIGGER for any request about Amazon sales trends, ASIN performance, what's selling, what's declining, trend dashboards, finding hidden winners or losers, who's at risk, or 'why is X selling differently' — even if the user doesn't use the word 'trend.' Also trigger when the user shares an ASIN or list of ASINs and asks what's happening to them. Pulls daily metrics from Quickbase Amazon_AdTrack.Daily_Metrics, aggregates to weekly, classifies every ASIN into 10 trend buckets (Strong Winner → Sustained Decline) plus Mixed Signal and Volatile flags, decomposes individual ASIN trends into 5 drivers (availability, traffic, conversion, price, rank), and outputs a self-contained interactive HTML dashboard with sortable table, 52-week chart, and per-ASIN drill-down panel."
---

# Amazon Trend Analyzer

You are an expert Amazon catalog analyst. This skill helps Steven detect sales
trends across 2,000+ Amazon 1P ASINs and decompose what's driving each trend.

It triangulates **L4W against three baselines** (L13W, L26W, L52W) on a composite
Units + Revenue index, classifies every ASIN into one of 10 buckets, flags
mixed-signal and volatile ASINs, and decomposes individual trends into 5 drivers
(availability, traffic, conversion, price, rank).

Output is a **self-contained interactive HTML dashboard**.

## Modes

This skill operates in three modes:

- **Full refresh** (default): Pull all active ASINs, run the engine, render the dashboard
- **Brand filter**: Scope to a single brand or master brand
- **Single ASIN deep dive**: Skip the dashboard, give a detailed analysis of one ASIN inline

Decide which mode based on the user's request, then proceed.

---

## Phase 0 — Scope confirmation

Ask the user **once** (skip if obvious from context):

> Before I pull data:
> - **Scope** — all active ASINs, a specific brand, or a single ASIN?
> - **Baseline mode for bucket assignment** — exclusive (default, cleaner signal) or inclusive (smoother)?
> - **Output** — full HTML dashboard, or just the inline summary?

---

## Phase 1 — Pull data from Quickbase

### 1a. Mandatory first call
```
Call CData Connect AI: getInstructions (driverName = "Quickbase")
```
Per the organization throttle protocol, this is required before any other CData call.

### 1b. Smoke test (Quickbase API Rules §6.1)
```sql
SELECT TOP 1 [ASIN], [Date], [Ordered_Units] FROM [Amazon_AdTrack].[Daily_Metrics]
```
**If this fails or hangs, abort.** Two CData failures in a session triggers
the 15-minute stop. Don't bulk-pull without smoking first.

### 1c. Source table choice — use Daily_Metrics, NOT Amazon_Daily_Sales

`Amazon_AdTrack.Daily_Metrics` is the right table for this skill because:
- **`Bestseller_Rank` is populated** (100% NULL on Amazon_Daily_Sales)
- **`Lost_Sales_Units_Due_to_OOS_`** and **`Lost_Sales_Units_Due_to_LBB_`**
  give concrete lost-demand counts (real driver signals)
- Catalog attributes (`Master_Brand`, `Master_Pack`, `Product_Category`,
  `ASIN_Description_`) are pre-joined — no separate catalog pull
- `Buybox_Price` enables buy-box loss as a driver
- `Has_A_Content` and `Has_Coupon_` flags add drill-down context

### 1d. Chunked bulk pull — Daily_Metrics

CData has a **1MB tool-result cap**. With our 12-column projection:
- 2-week chunks = ~800KB → safe
- 4-week chunks = ~1.7MB → exceeds cap
- Plan: **2-week chunks**, 26 of them to cover 52 weeks (or 8-9 for the
  17-week Phase A subset that powers L4-vs-L13)

Use `lib/pull_data.py::daily_metrics_sql(start_date, end_date)` to generate
each chunk's SQL. Fire them one at a time, save each result to a CSV in the
cache dir, and verify the row count before proceeding.

The static catalog attributes can be pulled SEPARATELY in one narrow query
since they don't change daily (use the most recent chunk's ASIN list).

### 1e. Cache to local CSV
Save each chunk to `~/.cache/amazon-trend-analyzer/chunks/{start}_{end}.csv`.
Subsequent dashboard rebuilds within the same day reuse the cache.

**Total CData calls for Phase A (last 17 weeks): ~10**
**Total CData calls for full 52 weeks: ~28**

Both are well-paced if fired sequentially with verification between calls.

---

## Phase 2 — Run trend engine

Local Python only — no QB calls.

```python
from lib import pull_data, trend_engine as te, html_builder as hb
import pandas as pd

weekly  = pull_data.normalize_weekly(pd.read_csv("~/.cache/amazon-trend-analyzer/weekly.csv"))
catalog = pull_data.normalize_catalog(pd.read_csv("~/.cache/amazon-trend-analyzer/catalog.csv"))

# If user picked a brand scope, filter here
if brand_filter:
    asin_keep = catalog[catalog["brand"].eq(brand_filter)]["asin"]
    weekly = weekly[weekly["asin"].isin(asin_keep)]
    catalog = catalog[catalog["asin"].isin(asin_keep)]

results = te.analyze_all(weekly, baseline_mode="exclusive")
```

`results` is a list of per-ASIN dicts with bucket, composite indices, drivers,
totals, and flags. See `reference/bucket_definitions.md` for the taxonomy.

---

## Phase 3 — Render the dashboard

```python
out = hb.render(results, weekly, catalog,
                out_path="/mnt/user-data/outputs/amazon_trend_dashboard.html",
                baseline_mode="exclusive")
```

The output is a single self-contained HTML file:
- React + Chart.js loaded from cdnjs
- All data embedded as JSON in the page
- No external data dependencies — opens offline
- ~5–15 MB for a full 2,000-ASIN refresh

Present the file with `present_files`.

---

## Phase 4 — Single ASIN deep dive (alternative path)

If the user wants one ASIN analyzed inline rather than a full dashboard:

```python
from lib import trend_engine as te, driver_decomp as dd
asin_weekly = weekly[weekly["asin"] == asin].sort_values("week_start")
result = te.analyze_asin(asin_weekly, asin, baseline_mode="exclusive")
drv = dd.decompose(asin_weekly)
```

Format a markdown response with:
- ASIN header (brand, description, status)
- Bucket assignment + flags
- The three composite indices with units + revenue split
- The driver narrative + ranked driver table
- One-paragraph interpretation

---

## Defensive rules (do not skip)

1. **Always call getInstructions first** — every session, no exceptions.
2. **Always smoke-test before bulk** — single TOP 1 query.
3. **Two CData failures = STOP** for 15 minutes minimum.
4. **Project narrowly, filter server-side** — never `SELECT *`, never pull
   then filter in Python.
5. **Cache to disk** — don't re-pull on every dashboard rebuild.
6. **Read-only** — this skill never writes back to Quickbase.

If the user requests features not present (e.g. "compare to last year's Prime
Day specifically"), say so and offer the closest in-skill alternative.

---

## Known gotchas (learned from prior sessions)

These cost CData calls to discover — burn them into the skill so the next
session doesn't relearn them:

1. **`[1st_Day_of_Current_Wk]` is not a per-row weekly bucket.** It's the
   current week as of query time, the same value for every row. Do NOT
   use it as a GROUP BY key. Aggregate to weekly **in pandas** via
   `pull_data.aggregate_daily_to_weekly()`.

2. **`Amazon_Catalog` lives in `[Amazon_AdTrack]`, NOT `[ProductTrack]`.**
   Querying `[ProductTrack].[Amazon_Catalog]` returns an empty schema.

3. **Status filter uses exact match.** `[ASIN_Status] = 'ACTIVE'` works.
   Values seen in the wild: `ACTIVE`, `FD`, `NOT ACTIVE`. Don't use
   `LIKE 'A%'` — it's both imprecise (catches future statuses) and may
   bypass an index on the equality predicate.

4. **`[Ordered_Units] > 0` is much faster than `[Ordered_Units] IS NOT NULL`**
   on `Amazon_Daily_Sales`. `IS NOT NULL` forces a full row scan even
   with a tight date range; `> 0` uses the index. Both naturally exclude
   zero-sales rows that we don't want anyway.

5. **`COUNT DISTINCT` queries time out** on this table. Don't use them
   for size estimation. If you need a row count, use a tight WHERE +
   plain `COUNT(*)` — or skip the count entirely.

6. **`Amazon_Catalog` has 700+ columns.** Always project narrowly. The
   columns we use are in `pull_data.catalog_attributes_sql()`.

---

## File map

```
amazon-trend-analyzer/
├── SKILL.md                       # this file
├── lib/
│   ├── pull_data.py               # CData SQL strings + CSV normalization
│   ├── trend_engine.py            # indices, bucket assignment, mixed-signal
│   ├── driver_decomp.py           # 5-driver decomposition + narrative
│   └── html_builder.py            # JSON payload + HTML render
├── assets/
│   └── dashboard_template.html    # React/Chart.js template
├── reference/
│   └── bucket_definitions.md      # bucket taxonomy reference
└── scripts/
    ├── generate_synthetic_data.py # fixture generator for testing
    ├── test_trend_engine.py       # engine validation
    └── build_demo_dashboard.py    # end-to-end smoke test
```

Tests in `scripts/` can run anytime without QB access — they use synthetic data.
