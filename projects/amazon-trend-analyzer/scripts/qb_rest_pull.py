#!/usr/bin/env python3
"""
qb_rest_pull.py — Pull Daily_Metrics from Quickbase REST via SAVED REPORT.

Uses the pre-designed report (qid=46) on table brgxdpadi:
    https://pim.quickbase.com/nav/app/bqkdiemav/table/brgxdpadi/action/q?qid=46

This is the recommended pattern per the QB API Rules doc §1.4 — saved reports
are pre-filtered, pre-projected, and cached server-side by QB.

Follows every rule in reference_quickbase_api_rules.md:
  §1.4 Use saved reports when available  ← we're doing this
  §3.1 Throttle to ≤5 req/s
  §3.2 Pacer between requests
  §3.3 Exponential backoff, 62s max budget
  §3.5 Abort cleanly on empty when data was expected
  §6.1 Smoke test before bulk
  §6.2 Persist progress to disk → fully resumable

USAGE:
    pip install requests pandas
    export QB_USER_TOKEN="b9xxx_xxxx_xxxx"
    python qb_rest_pull.py
"""

from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path
from http.client import IncompleteRead

try:
    import requests
    import pandas as pd
except ImportError:
    sys.exit("Install dependencies first:  pip install requests pandas")


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG — everything below this line is pulled from the report URL.
# Only QB_USER_TOKEN is required from the user.
# ═══════════════════════════════════════════════════════════════════════════

REALM      = os.environ.get("QB_REALM",      "pim.quickbase.com")
USER_TOKEN = os.environ.get("QB_USER_TOKEN", "PASTE_YOUR_USER_TOKEN_HERE")
TABLE_DBID = os.environ.get("QB_TABLE_DBID", "brgxdpadi")   # Daily_Metrics
REPORT_ID  = os.environ.get("QB_REPORT_ID",  "46")

OUT_DIR = Path(os.environ.get("QB_OUT_DIR", "./qb_chunks"))

# Throttle & retry (rule §3.x)
REQUESTS_PER_SECOND = float(os.environ.get("QB_RPS", "4"))
PAGE_SIZE   = int(os.environ.get("QB_PAGE_SIZE", "5000"))   # QB caps at ~10K
MAX_RETRIES = 5

# ═══════════════════════════════════════════════════════════════════════════

API_BASE = "https://api.quickbase.com/v1"


# ───────────────────────────────────────────────────────────────────────────
# HTTP layer — pacing, retry, auth
# ───────────────────────────────────────────────────────────────────────────

_pacer_lock = threading.Lock()
_last_request_t = [0.0]


def _pace():
    """Rule §3.2 — sleep so we don't exceed REQUESTS_PER_SECOND."""
    min_gap = 1.0 / REQUESTS_PER_SECOND
    with _pacer_lock:
        wait = min_gap - (time.time() - _last_request_t[0])
        if wait > 0:
            time.sleep(wait)
        _last_request_t[0] = time.time()


def _headers() -> dict[str, str]:
    return {
        "QB-Realm-Hostname": REALM,
        "Authorization": f"QB-USER-TOKEN {USER_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "amazon-trend-analyzer/1.0",
    }


def _request(method: str, path: str, *, json_body=None, params=None) -> dict:
    """One QB REST call with retry + exponential backoff."""
    url = f"{API_BASE}{path}"
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        _pace()
        try:
            t0 = time.time()
            r = requests.request(method, url, headers=_headers(),
                                  json=json_body, params=params, timeout=120)

            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "30"))
                print(f"  429 throttled — sleeping Retry-After {retry_after}s")
                time.sleep(retry_after)
                continue
            if r.status_code in (401, 403):
                # Hard auth failure — don't retry, surface immediately
                sys.exit(f"[ABORT] HTTP {r.status_code} {r.text[:200]}\n"
                          f"        → Check your QB_USER_TOKEN is valid and "
                          f"scoped to the Amazon_AdTrack app.")
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code} {r.text[:200]}")
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
            if r.headers.get("Content-Length") == "0" or not r.text:
                raise RuntimeError("empty 200 body (likely throttle)")
            return r.json()

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                IncompleteRead, RuntimeError) as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            backoff = 2 ** attempt  # 2,4,8,16,32 = 62s
            print(f"  attempt {attempt}/{MAX_RETRIES} failed ({e}); sleep {backoff}s")
            time.sleep(backoff)

    raise SystemExit(f"[ABORT] {method} {path} failed after {MAX_RETRIES} retries: {last_err}")


# ───────────────────────────────────────────────────────────────────────────
# Report API (the simpler, recommended path — rule §1.4)
# ───────────────────────────────────────────────────────────────────────────

def get_report_metadata() -> dict:
    """GET /reports/{id} — returns report info (name, description).
    Note: the 'fields' array in this response can be empty even when the
    report has columns. We pull the field map from /run response instead.
    """
    print(f"Fetching report metadata: report {REPORT_ID} on table {TABLE_DBID}")
    return _request("GET", f"/reports/{REPORT_ID}",
                     params={"tableId": TABLE_DBID})


def run_report_page(skip: int, top: int) -> dict:
    """POST /reports/{id}/run — execute the report and return one page."""
    return _request("POST", f"/reports/{REPORT_ID}/run",
                     params={"tableId": TABLE_DBID, "skip": skip, "top": top})


def get_table_field_map() -> dict[int, str]:
    """Last-resort: pull ALL fields for the table via /fields. Slower
    but always works. Used if both /reports/{id} and the run response have
    no fields array."""
    print("Fetching full table field map from /fields...")
    fields = _request("GET", "/fields", params={"tableId": TABLE_DBID})
    return {int(f["id"]): (f.get("label") or f.get("name") or f"fid_{f['id']}")
            for f in fields if "id" in f}


def smoke_test() -> tuple[dict[int, str], list[dict]]:
    """Rule §6.1 — single-row probe. Also returns the field map from the
    run response (where it actually lives) and the smoke row itself."""
    print("Smoke test (TOP 1 page from report)...")
    res = run_report_page(skip=0, top=1)
    rows = res.get("data", [])
    if not rows:
        sys.exit("[ABORT] smoke test got 0 rows — does the report return data? "
                 "Check it runs in the QB UI first.")
    fields = res.get("fields", [])
    id_to_label = {int(f["id"]): (f.get("label") or f.get("name") or f"fid_{f['id']}")
                   for f in fields if "id" in f}
    print(f"  ✓ {len(rows)} row, {len(id_to_label)} columns mapped")
    return id_to_label, rows


def field_map_from_metadata(meta: dict) -> dict[int, str]:
    """Build {field_id: label} from /reports/{id} response.
    Often empty — kept for completeness."""
    out = {}
    for f in meta.get("fields", []):
        fid = f.get("id")
        label = f.get("label") or f.get("name")
        if fid is not None and label:
            out[int(fid)] = label
    return out


def rows_to_dataframe(rows: list[dict], id_to_label: dict[int, str]) -> pd.DataFrame:
    """QB rows: {fid_str: {value: v}}. Flatten using the report's field map."""
    records = []
    for r in rows:
        rec = {}
        for fid_str, cell in r.items():
            try:
                fid = int(fid_str)
            except (TypeError, ValueError):
                continue
            label = id_to_label.get(fid)
            if label is None:
                continue
            rec[label] = cell.get("value") if isinstance(cell, dict) else cell
        records.append(rec)
    return pd.DataFrame.from_records(records)


# Mapping QB labels → trend-engine short names. We accept several spellings
# because reports may use slightly different labels than raw columns.
LABEL_TO_ENGINE = {
    "ASIN": "asin",
    "Date": "date",
    # Metrics
    "Ordered Units": "units",         "Ordered_Units": "units",
    "Ordered Revenue": "revenue",     "Ordered_Revenue": "revenue",
    "Glance Views": "gv",             "Glance_Views": "gv",
    "CVR": "cr",                      "CVR_": "cr",  "Conversion Rate": "cr",
    "Average Sales Price": "asp",     "Average_Sales_Price": "asp",
    "Rep OOS": "oos_signal",          "Rep_OOS": "oos_signal",
    "Lost Sales Units Due to OOS": "lost_oos",
    "Lost_Sales_Units_Due_to_OOS_": "lost_oos",
    "Lost Sales Units Due to LBB": "lost_lbb",
    "Lost_Sales_Units_Due_to_LBB_": "lost_lbb",
    "Bestseller Rank": "bsr",         "Bestseller_Rank": "bsr",
    "Buybox Price": "bb_price",       "Buybox_Price": "bb_price",
    # Catalog attrs
    "Master Brand": "brand",          "Master_Brand": "brand", "Brand": "brand",
    "ASIN Description": "description","ASIN_Description_": "description",
    "Listing Title": "description",
    "Master Pack": "pack_size",       "Master_Pack": "pack_size",
    "Product Category": "category",   "Product_Category": "category",
    "ASIN Status": "asin_status",     "ASIN_Status": "asin_status",
}


def rename_for_engine(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={k: v for k, v in LABEL_TO_ENGINE.items()
                              if k in df.columns})


# ───────────────────────────────────────────────────────────────────────────
# Main driver — paginate the report, save each page, stitch at the end
# ───────────────────────────────────────────────────────────────────────────

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not USER_TOKEN or "PASTE" in USER_TOKEN.upper() or "your_token" in USER_TOKEN.lower():
        sys.exit("[ABORT] QB_USER_TOKEN is not set to a real token.\n"
                  "        Set it via:  export QB_USER_TOKEN='your_actual_token'")

    print("─" * 60)
    print(f"realm:     {REALM}")
    print(f"table:     {TABLE_DBID}")
    print(f"report:    {REPORT_ID}")
    print(f"out_dir:   {OUT_DIR}")
    print(f"pacing:    {REQUESTS_PER_SECOND} req/s")
    print(f"page_size: {PAGE_SIZE}")
    print("─" * 60)

    # 1. Report metadata — informational only (the 'fields' here is often empty)
    meta = get_report_metadata()
    print(f"\nReport: '{meta.get('name', '(unnamed)')}'")
    desc = meta.get("description") or "(no description)"
    print(f"Description: {desc[:200]}\n")

    # 2. Smoke test + extract real field map from the run response
    id_to_label, smoke_rows = smoke_test()

    # 3. Fallback if the run response also gave no fields
    if not id_to_label:
        print("  ⚠️  Run response had no fields — falling back to /fields...")
        id_to_label = get_table_field_map()
    if not id_to_label:
        sys.exit("[ABORT] No field map available from any source.")

    print(f"\nProjected columns ({len(id_to_label)}):")
    for fid, label in sorted(id_to_label.items()):
        engine_name = LABEL_TO_ENGINE.get(label, "")
        note = f"  → {engine_name}" if engine_name else ""
        print(f"  {fid:>4}  {label}{note}")
    print()

    # 4. Paginate. Each page saved to its own CSV so the job is resumable.
    #    Defensively skip cached files that are empty (e.g. from a prior
    #    run where field discovery failed).
    print("Paginating report...")
    skip = 0
    page_idx = 0
    all_pages = []

    while True:
        page_path = OUT_DIR / f"page_{page_idx:03d}_skip{skip:06d}.csv"
        if page_path.exists() and page_path.stat().st_size > 200:
            # Treat tiny files (just a header or empty) as not cached
            print(f"  page {page_idx:>3}  skip={skip:>6}  cached ✓")
            df = pd.read_csv(page_path)
            all_pages.append(df)
            # Advance by actual row count — don't assume "< PAGE_SIZE means end"
            if len(df) == 0:
                break
            skip += len(df)
            page_idx += 1
            continue
        elif page_path.exists():
            # Stale empty file from a prior failed run; delete and re-fetch
            page_path.unlink()

        t0 = time.time()
        res = run_report_page(skip=skip, top=PAGE_SIZE)
        rows = res.get("data", [])
        elapsed = time.time() - t0
        print(f"  page {page_idx:>3}  skip={skip:>6}  got={len(rows):>5}  "
              f"in {elapsed:5.1f}s")

        if not rows:
            if page_idx == 0:
                sys.exit("[ABORT] First page returned 0 rows — likely a throttle "
                          "or permissions issue. Check the report runs in the UI.")
            break

        df = rows_to_dataframe(rows, id_to_label)
        df = rename_for_engine(df)
        df.to_csv(page_path, index=False)
        all_pages.append(df)

        # QB /reports/run caps at 1000 rows/call regardless of `top`. Advance
        # the skip cursor by the actual count and keep paginating until we
        # get an empty response. Do NOT break on len(rows) < PAGE_SIZE — that
        # only worked for /records/query which honors `top`.
        skip += len(rows)
        page_idx += 1

    if not all_pages:
        sys.exit("[ABORT] No pages collected")

    # 4. Stitch + dedup
    combined = pd.concat(all_pages, ignore_index=True)
    if "asin" in combined.columns and "date" in combined.columns:
        before = len(combined)
        combined = combined.drop_duplicates(subset=["asin", "date"])
        if len(combined) < before:
            print(f"\nDeduped {before - len(combined)} duplicate rows")

    out_path = OUT_DIR / "all_daily.csv"
    combined.to_csv(out_path, index=False)

    print(f"\n✅ Done. {len(combined):,} rows → {out_path}")
    if "asin" in combined.columns:
        print(f"   ASINs:  {combined['asin'].nunique():,}")
    if "date" in combined.columns:
        print(f"   Dates:  {combined['date'].nunique()}  "
              f"({combined['date'].min()} → {combined['date'].max()})")
    print(f"\nNext:  python build_dashboard_from_chunks.py "
          f"--chunks-dir {OUT_DIR} --out amazon_trend_dashboard.html")


if __name__ == "__main__":
    main()
