# Amazon Trend Analyzer — Quickbase REST workflow

When CData Connect AI isn't available, use this two-script workflow to pull
data directly from Quickbase via REST API. The pull script uses the
**pre-designed report** at:

> https://pim.quickbase.com/nav/app/bqkdiemav/table/brgxdpadi/action/q?qid=46

so there's no field-ID discovery, no manual WHERE clauses, no guessing column
names. Per the QB API Rules doc §1.4, saved reports are the recommended
pattern — pre-filtered, pre-projected, cached server-side.

## One-time setup

```bash
pip install requests pandas
```

## Step 1 — Get a user token

In Quickbase top-right menu → **My preferences** → **Manage user tokens** →
**New user token** → scope to the Amazon_AdTrack app for least privilege.
Copy the token.

## Step 2 — Pull the data

```bash
# Only the token is required — everything else comes from the report URL
export QB_USER_TOKEN="b9xxx_xxxx_xxxx"

python scripts/qb_rest_pull.py
```

That's it. The script:
1. Fetches the report's column list (one `/reports/{id}` call)
2. Smoke-tests with a TOP 1 query
3. Paginates through the report at 5K rows per page
4. Throttles to 4 req/s with exponential backoff on errors
5. Writes each page to `qb_chunks/page_NNN_skipNNNNNN.csv`
6. Stitches everything into `qb_chunks/all_daily.csv`
7. Deduplicates on (ASIN × Date)

**Resumable:** re-running picks up from the last cached page. Safe to Ctrl+C
and restart anytime.

**Overrides** (rarely needed):
```bash
export QB_REALM="pim.quickbase.com"      # default — from your report URL
export QB_TABLE_DBID="brgxdpadi"         # Daily_Metrics — from URL
export QB_REPORT_ID="46"                 # qid=46 — from URL
export QB_RPS="4"                        # throttle (req/s)
export QB_PAGE_SIZE="5000"               # rows per call (QB cap ~10K)
```

## Step 3 — Build the dashboard

```bash
python scripts/build_dashboard_from_chunks.py \
    --chunks-dir ./qb_chunks \
    --out amazon_trend_dashboard.html
```

This:
1. Loads `qb_chunks/all_daily.csv` (or all page CSVs)
2. Aggregates daily → weekly via Monday anchors
3. Runs the trend engine (composite Units + Revenue, three windows)
4. Generates the self-contained interactive HTML

Open `amazon_trend_dashboard.html` in any browser.

## Troubleshooting

**"Smoke test got 0 rows":** the report might be empty for the current user,
or you don't have permission to run it. Open qid=46 in QB UI first.

**429 throttled:** the script auto-honors `Retry-After`. If it happens
repeatedly, drop `QB_RPS` to 2 and re-run.

**Column missing from output:** the report's projection doesn't include it.
Either edit qid=46 in QB to add the column, or edit `LABEL_TO_ENGINE` at the
top of `qb_rest_pull.py` to map a different report column to that engine name.

**"Fewer than 5 weeks of data per ASIN" warning:** the report's filter only
covers a short window. Clone qid=46 in QB and widen its date filter, or use
a 52-week version of the report.

## Following the QB API Rules

| Rule | Implementation |
|---|---|
| §1.4 Use saved reports | Primary path — qid=46 |
| §1.2 Cached field map | `id_to_label` built once from `/reports/{id}` |
| §3.1 Throttle | 4 req/s default |
| §3.2 Pacer | Thread-safe `_pace()` between requests |
| §3.3 Exponential backoff | 2/4/8/16/32 = 62s budget |
| §3.5 Abort on empty | `SystemExit` on first-page zero rows |
| §6.1 Smoke test | TOP 1 page before bulk |
| §6.2 Persist progress | Per-page CSVs; re-running resumes |
| §6.3 Latency logging | Per-page timing in stdout |
