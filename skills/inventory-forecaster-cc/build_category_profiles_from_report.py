"""
Build empirically-derived monthly seasonality profiles by Product_Category and
(Category, Subcategory) from the Invoices table.

Source : Direct /v1/records/query against bpaxk2v8t (ProductTrack.Invoices)
         WHERE Shpd Date >= 2024-01-01 (same filter as saved report qid=63)

Why direct query, not the saved report:
  /v1/reports/63/run returned malformed metadata (numRecords inconsistent with
  top, totalRecords reported as 737,973 but direct table query for the same
  filter shows 1,290,768 rows).  The report endpoint pagination silently
  underfetched.  /v1/records/query honours top/skip cleanly and lets us
  project just the 4 fields we need.

Pipeline:
  1. Probe field map for the table.
  2. Page through the table with top=10000, paced at ~5 req/s (QB rules for
     large tables).  Tight 4-field projection (Shpd Date, Qty Shpd, Product
     Category, Product Subcategory) keeps payload small.
  3. Cache to cache/invoices_query_2024plus.json so retries are free.
  4. Aggregate to category-only and (category, subcategory) levels.
  5. 12-element monthly index (mean=1.0), clamped to [0.10, 4.00], gates as
     before.
  6. Save scripts/derived_category_profiles.json.
"""
import sys, json, time, os, re, threading
from collections import defaultdict
from datetime import date

sys.path.insert(0, "scripts")
from inventory_forecaster import _qb_request, qb_get_field_map

TABLE_ID     = "bpaxk2v8t"
START_YEAR   = 2024
END_YEAR     = date.today().year     # 2026 right now
OUT_PATH     = "scripts/derived_category_profiles.json"
CACHE_PATH   = "cache/invoices_query_2024plus_v3.json"   # v3 = includes Mstyle + Customer Name
PAGE_SIZE    = 10_000
RATE_LIMIT_MS = 200                 # 5 req/s sustained, well under QB large-table cap
MAX_TOTAL    = 5_000_000             # safety cap; current size is ~1.3M

# MStyle "consistency" gates — exclude 1-off promos, test launches, drops.
# A MStyle qualifies if it ships in enough months and over a long enough span
# to plausibly be a true replenishment SKU rather than a one-time event.
MIN_ACTIVE_MONTHS_PER_STYLE  = 10    # ≥10 distinct year-months with qty > 0
MIN_LIFESPAN_MONTHS          = 12    # first→last shipment span ≥12 months
MIN_ACTIVITY_RATE            = 0.50  # active_months / lifespan_months ≥ 0.50

# Category-level minimum consistent-SKU count.  Profiles built from <3 SKUs
# are too noisy to be representative.
MIN_CONSISTENT_SKUS          = 3

# Year weighting — 2024 is the cleanest year (pre-tariff supply chain).
# 2025 was disrupted by tariffs (esp. May-Sep OOS).  2026 YTD is partial.
# Weights apply per-row when summing into monthly buckets.
YEAR_WEIGHTS = {
    2024: 2.0,
    2025: 1.0,
    2026: 1.0,
}

# Tariff-driven OOS window — drop these (year, month) pairs entirely so that
# artificially suppressed shipments don't bias the seasonal shape downward.
OOS_DROP_MONTHS = {
    (2025, 5), (2025, 6), (2025, 7), (2025, 8), (2025, 9),
}

# Customer-weight uplift — strategic accounts get 2× pull on the seasonal shape.
# Matched via case-insensitive substring on Customer Name.  These compound with
# YEAR_WEIGHTS, so a 2024 Amazon row carries 4.0× weight (2.0 year × 2.0 cust).
STRATEGIC_CUSTOMER_KEYWORDS = ["AMAZON", "WAL MART", "WALMART", "PETSMART"]
STRATEGIC_CUSTOMER_WEIGHT   = 2.0
DEFAULT_CUSTOMER_WEIGHT     = 1.0

def _customer_weight(name):
    """Return weight multiplier for a customer name, case-insensitive substring match."""
    if not name:
        return DEFAULT_CUSTOMER_WEIGHT
    upper = name.upper()
    for kw in STRATEGIC_CUSTOMER_KEYWORDS:
        if kw in upper:
            return STRATEGIC_CUSTOMER_WEIGHT
    return DEFAULT_CUSTOMER_WEIGHT


# Planner overrides — when business knowledge says the data-derived shape is
# wrong, hard-replace the profile here.  Each value is a 12-element list
# [Jan..Dec], will be normalized to mean=1.0 automatically.
#
# Disposable Tabletop: paper plates / cups / picnic gear.  Multi-event category:
#   - Summer entertaining (Memorial Day → Labor Day): Apr-Aug heavy
#   - Thanksgiving paper goods: Oct shipping peak
#   - Christmas/Holiday: Nov shipping peak
#   - Jan/Feb/Dec lulls
PLANNER_OVERRIDES = {
    "Disposable Tabletop": [0.50, 0.55, 1.10, 1.50, 1.50, 1.30, 1.30, 1.20,
                            1.10, 1.55, 1.40, 0.50],
}

# Holiday lead-time uplift — Nov/Dec consumer demand → Sep-Nov retailer
# shipping.  Applied multiplicatively to every (non-overridden) profile after
# data aggregation, before clamping & renormalization.  Ensures every
# category shows at minimum a +10% lift in those months even when 2024-only
# data didn't capture it cleanly.
HOLIDAY_LEAD_UPLIFT = {
    9:  1.10,   # Sep — early holiday pre-buy
    10: 1.20,   # Oct — peak Thanksgiving prep shipping
    11: 1.15,   # Nov — peak Christmas prep shipping
}

# ─── Demand-distortion cleaning ───────────────────────────────────────────────
# Applied per (mstyle, customer) before rows flow into the category aggregate.
#
# 1. OOS catch-up spikes — when a (mstyle, customer) combo had a gap of
#    OOS_GAP_MONTHS or more consecutive zero-shipment months, the first month
#    back contains pent-up make-up demand from multiple periods.  That catch-up
#    is NOT genuine monthly demand for the purpose of the seasonal shape.
#    Cap the bucket at OOS_CAP_MULT × that combo's median monthly qty.
#
# 2. ISO / isolated-spike orders — any (mstyle, customer, year_month) bucket
#    where total shipped qty > ISO_CAP_MULT × median monthly qty for that combo.
#    These are one-time bulk buys (annual pre-buys, display programs, promo
#    fills) that would inflate one calendar month and falsely lift the index.
#    Cap at ISO_CAP_MULT × median.
#
# Both caps share the same mechanism (scale_factor on the bucket); OOS catch-up
# uses a tighter cap because the demand was already truly lost.
OOS_GAP_MONTHS = 2          # gap >= this many calendar months → flag as OOS return
OOS_CAP_MULT   = 1.5        # OOS catch-up month capped at 1.5× median (some catch-up is real)
ISO_CAP_MULT   = 2.5        # ISO spike capped at 2.5× median monthly qty

# Quality gates
MIN_TOTAL_UNITS   = 100_000
MIN_ACTIVE_MONTHS = 3
MIN_PEAK_TROUGH   = 1.30
MIN_YEARS         = 2

# Profile clamps — protect against single-month outliers from polluting
# forecasts. With 70%-weight blend in seasonal_baseline(), an unclamped
# 0.0 month would drive forecasts to 30% of the no-category baseline,
# and a 10x outlier peak would inflate forecasts 7x.  Clamp keeps lift
# bounded at ±300% (0.10x ↔ 4.00x) and renormalizes so mean stays 1.0.
PROFILE_FLOOR = 0.10
PROFILE_CEIL  = 4.00

# Heuristic field-label matchers (case-insensitive substring) — the report may
# label them differently than the underlying table.
DATE_HINTS  = ["shpd date", "ship date", "shipped date", "invoice date",
               "shipdate", "shipdte", "shpd_date", "shpd"]
QTY_HINTS   = ["qty shpd", "qty_shpd", "qty shipped", "shipped qty",
               "qty", "units"]
CAT_HINTS   = ["product category", "product_category", "category"]
SUB_HINTS   = ["product subcategory", "product_subcategory", "subcategory",
               "sub category", "sub-category"]
YEAR_HINTS  = ["yyyy", "year"]
MONTH_HINTS = ["month"]


def _find_field(labels, hints, exclude=()):
    """Return first label matching any hint. Lower-case substring match."""
    lower_labels = [(l, l.lower()) for l in labels]
    excluded_lower = {x.lower() for x in exclude}
    for hint in hints:
        h = hint.lower()
        for orig, low in lower_labels:
            if low in excluded_lower:
                continue
            if h in low:
                return orig
    return None


_PACE_LOCK = threading.Lock()
_LAST_T    = [0.0]

def _pace(min_ms=RATE_LIMIT_MS):
    """Thread-safe pacer — caps QPS regardless of caller concurrency."""
    with _PACE_LOCK:
        wait = (min_ms / 1000.0) - (time.time() - _LAST_T[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_T[0] = time.time()


def probe_and_pull():
    """Page through Invoices table via /v1/records/query.  Disk-cached.

    Returns (rows, labels) where rows are dicts keyed by field LABEL (not fid).
    """
    os.makedirs("cache", exist_ok=True)
    if os.path.exists(CACHE_PATH):
        try:
            cached = json.load(open(CACHE_PATH))
            rows   = cached.get("rows", [])
            labels = cached.get("labels", [])
            if rows and labels:
                print(f"[cache] using cached query ({len(rows):,} rows, {len(labels)} cols)", flush=True)
                return rows, labels
        except Exception:
            pass

    # Resolve field map once
    fmap   = qb_get_field_map(TABLE_ID)
    fid_dt   = fmap.get("Shpd Date")
    fid_qty  = fmap.get("Qty Shpd")
    fid_cat  = fmap.get("Product Category")
    fid_sub  = fmap.get("Product Subcategory")
    fid_mst  = fmap.get("Mstyle")
    fid_cust = fmap.get("Customer Name")
    if not all([fid_dt, fid_qty, fid_cat, fid_sub, fid_mst, fid_cust]):
        sys.exit(f"[ABORT] missing fids: dt={fid_dt} qty={fid_qty} cat={fid_cat} "
                 f"sub={fid_sub} mst={fid_mst} cust={fid_cust}")
    select_fids = [fid_dt, fid_qty, fid_cat, fid_sub, fid_mst, fid_cust]
    fid_to_label = {
        fid_dt:   "Shpd Date",  fid_qty: "Qty Shpd",
        fid_cat:  "Product Category", fid_sub: "Product Subcategory",
        fid_mst:  "Mstyle",
        fid_cust: "Customer Name",
    }
    print(f"[fids] dt={fid_dt} qty={fid_qty} cat={fid_cat} sub={fid_sub} "
          f"mst={fid_mst} cust={fid_cust}", flush=True)

    # First call: get totalRecords so we can show progress
    where = f"{{{fid_dt}.OAF.'01-01-{START_YEAR}'}}"
    print(f"[query] WHERE {where} on table {TABLE_ID}", flush=True)

    rows   = []
    skip   = 0
    total_records = None
    t0 = time.time()
    while skip < MAX_TOTAL:
        _pace()
        body = {
            "from": TABLE_ID,
            "select": select_fids,
            "where": where,
            "options": {"top": PAGE_SIZE, "skip": skip},
        }
        resp = _qb_request("POST", "/records/query", body=body, timeout=180)
        meta = resp.get("metadata", {}) or {}
        if total_records is None:
            total_records = meta.get("totalRecords")
            print(f"[query] totalRecords = {total_records:,}", flush=True)
        chunk = resp.get("data", []) or []
        if not chunk:
            break
        for r in chunk:
            row = {}
            for fid_str, cell in r.items():
                try:
                    fid = int(fid_str)
                except ValueError:
                    continue
                lbl = fid_to_label.get(fid)
                if lbl:
                    row[lbl] = cell.get("value")
            rows.append(row)
        elapsed = time.time() - t0
        rate    = len(rows) / max(elapsed, 0.01)
        eta_s   = ((total_records - len(rows)) / max(rate, 1)) if total_records else 0
        print(f"[query] page skip={skip:>7,}  +{len(chunk):>5,}  total={len(rows):>9,}/{total_records:>9,}  "
              f"({elapsed:>5.1f}s, {rate:>5.0f}/s, ETA {eta_s/60:.1f} min)", flush=True)
        # Defensive: stop if we've hit totalRecords
        if total_records is not None and len(rows) >= total_records:
            del rows[total_records:]
            break
        # End-of-pagination signal
        if len(chunk) < PAGE_SIZE:
            break
        skip += len(chunk)

    elapsed = time.time() - t0
    print(f"[query] done — {len(rows):,} rows in {elapsed:.1f}s", flush=True)
    if not rows:
        sys.exit("[ABORT] direct query returned 0 rows")

    labels = sorted(rows[0].keys())
    json.dump({"rows": rows, "labels": labels}, open(CACHE_PATH, "w"))
    print(f"[cache] saved to {CACHE_PATH}", flush=True)
    return rows, labels


MST_HINTS   = ["mstyle", "m-style", "m_style"]
CUST_HINTS  = ["customer name", "customer_name", "customer"]

def resolve_fields(labels):
    """Identify which labels in the report correspond to date/qty/cat/subcat/mstyle."""
    qty_field   = _find_field(labels, QTY_HINTS)
    cat_field   = _find_field(labels, CAT_HINTS, exclude=[_find_field(labels, SUB_HINTS) or ""])
    sub_field   = _find_field(labels, SUB_HINTS)
    date_field  = _find_field(labels, DATE_HINTS)
    year_field  = _find_field(labels, YEAR_HINTS)
    month_field = _find_field(labels, MONTH_HINTS)
    mst_field   = _find_field(labels, MST_HINTS)
    cust_field  = _find_field(labels, CUST_HINTS)

    print(f"[resolve] qty   = {qty_field}")
    print(f"[resolve] mst   = {mst_field}")
    print(f"[resolve] cust  = {cust_field}")
    print(f"[resolve] cat   = {cat_field}")
    print(f"[resolve] sub   = {sub_field}")
    print(f"[resolve] date  = {date_field}")
    print(f"[resolve] year  = {year_field}")
    print(f"[resolve] month = {month_field}")

    if not (qty_field and cat_field):
        sys.exit(f"[ABORT] could not resolve required fields. labels={labels}")
    if not (date_field or (year_field and month_field)):
        sys.exit(f"[ABORT] need either a date field OR year+month fields. labels={labels}")

    return {
        "qty": qty_field, "cat": cat_field, "sub": sub_field,
        "date": date_field, "year": year_field, "month": month_field,
        "mst": mst_field, "cust": cust_field,
    }


_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
def _parse_year_month(row, fields):
    """Return (year, month) tuple or (None, None)."""
    if fields["date"]:
        v = row.get(fields["date"])
        if isinstance(v, str):
            m = _DATE_RE.match(v)
            if m:
                return int(m.group(1)), int(m.group(2))
        return None, None
    # year + month fields
    try:
        y = int(row.get(fields["year"]))
        m = int(row.get(fields["month"]))
        return y, m
    except (TypeError, ValueError):
        return None, None


def build_consistent_mstyle_set(rows, fields):
    """First pass — for each MStyle, compute month-activity stats, then return
    the set of MStyle keys that meet the consistency gates.

    "Consistent" means the SKU plausibly ships every month (or near-every),
    not a 1-3 month promo blast or a single-quarter drop.
    """
    if "mst" not in fields or not fields["mst"]:
        print("[consistency] WARNING: no Mstyle field — skipping consistency filter", flush=True)
        return None  # signal: no filtering

    mst_months = defaultdict(set)   # mstyle -> set of (year,month) with qty>0
    mst_qty    = defaultdict(float) # mstyle -> total qty
    for r in rows:
        try:
            qty = float(r.get(fields["qty"]) or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        mst = (r.get(fields["mst"]) or "").strip()
        if not mst:
            continue
        y, m = _parse_year_month(r, fields)
        if not y or not m or y < START_YEAR or y > END_YEAR:
            continue
        if (y, m) in OOS_DROP_MONTHS:
            continue       # OOS months don't count toward consistency
        mst_months[mst].add((y, m))
        mst_qty[mst] += qty

    consistent = set()
    for mst, ymset in mst_months.items():
        n_active = len(ymset)
        if n_active < MIN_ACTIVE_MONTHS_PER_STYLE:
            continue
        # lifespan = months between first and last shipment, inclusive
        first = min(ymset);  last = max(ymset)
        lifespan = (last[0] - first[0]) * 12 + (last[1] - first[1]) + 1
        if lifespan < MIN_LIFESPAN_MONTHS:
            continue
        if n_active / max(lifespan, 1) < MIN_ACTIVITY_RATE:
            continue
        consistent.add(mst)

    n_total       = len(mst_months)
    n_consistent  = len(consistent)
    consistent_qty= sum(mst_qty[m] for m in consistent)
    total_qty     = sum(mst_qty.values())
    print(f"[consistency] {n_consistent:,}/{n_total:,} MStyles qualify "
          f"({n_consistent/max(n_total,1)*100:.1f}%) — "
          f"capturing {consistent_qty:,.0f}/{total_qty:,.0f} units "
          f"({consistent_qty/max(total_qty,1)*100:.1f}%)", flush=True)
    return consistent


def compute_demand_caps(rows, fields):
    """Pre-pass: build per-(mstyle, customer) monthly totals, detect OOS catch-up
    spikes and ISO outlier orders, and return a scale_factors dict.

    scale_factors[(mst, cust, y, m)] = float in (0, 1]
    Any bucket absent from the dict has scale = 1.0 (no cap needed).
    """
    if not fields.get("mst") or not fields.get("cust"):
        print("[clean] WARNING: mst or cust field missing — skipping demand cap pass", flush=True)
        return {}

    # Step 1 — aggregate monthly totals per (mst, cust, y, m)
    buckets = defaultdict(float)
    bucket_meta = {}   # (mst, cust, y, m) -> (cat, sub) for logging
    for r in rows:
        try:
            qty = float(r.get(fields["qty"]) or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        mst  = (r.get(fields["mst"])  or "").strip()
        cust = (r.get(fields["cust"]) or "").strip()
        y, m = _parse_year_month(r, fields)
        if not y or not m or y < START_YEAR or y > END_YEAR:
            continue
        if (y, m) in OOS_DROP_MONTHS:
            continue
        key = (mst, cust, y, m)
        buckets[key] += qty
        if key not in bucket_meta:
            cat = (r.get(fields["cat"]) or "").strip()
            sub = (r.get(fields["sub"]) or "").strip() if fields["sub"] else ""
            bucket_meta[key] = (cat, sub)

    # Step 2 — group by (mst, cust), compute median, detect gaps
    combo_months = defaultdict(dict)  # (mst, cust) -> {(y, m): qty}
    for (mst, cust, y, m), qty in buckets.items():
        combo_months[(mst, cust)][(y, m)] = qty

    scale_factors = {}
    n_oos = 0
    n_iso = 0

    for (mst, cust), month_qtys in combo_months.items():
        if not month_qtys:
            continue
        all_months = sorted(month_qtys.keys())
        qtys_sorted = sorted(month_qtys[ym] for ym in all_months)
        if not qtys_sorted:
            continue
        # Median of all non-zero monthly shipment quantities for this combo
        median_qty = qtys_sorted[len(qtys_sorted) // 2]
        if median_qty <= 0:
            continue

        oos_cap = OOS_CAP_MULT * median_qty
        iso_cap = ISO_CAP_MULT * median_qty

        for i, ym in enumerate(all_months):
            qty = month_qtys[ym]

            # Detect OOS gap: how many calendar months since the previous shipment?
            # Count only real months (subtract known OOS_DROP_MONTHS from the gap).
            if i == 0:
                gap_real = 0  # no prior history — not a catch-up
            else:
                prev_ym   = all_months[i - 1]
                prev_idx  = prev_ym[0] * 12 + prev_ym[1]
                curr_idx  = ym[0]     * 12 + ym[1]
                gap_total = curr_idx - prev_idx - 1  # calendar months in between
                # Subtract OOS_DROP_MONTHS that fall inside the gap (not a real silent period)
                oos_in_gap = 0
                for (dy, dm) in OOS_DROP_MONTHS:
                    di = dy * 12 + dm
                    if prev_idx < di < curr_idx:
                        oos_in_gap += 1
                gap_real = max(0, gap_total - oos_in_gap)

            is_oos_catchup = (gap_real >= OOS_GAP_MONTHS)
            cap            = oos_cap if is_oos_catchup else iso_cap

            if qty > cap:
                scale_factors[(mst, cust, ym[0], ym[1])] = cap / qty
                if is_oos_catchup:
                    n_oos += 1
                else:
                    n_iso += 1

    total_capped = len(scale_factors)
    print(f"[clean] demand cap pass complete:", flush=True)
    print(f"  OOS catch-up months capped : {n_oos:,}  (gap >= {OOS_GAP_MONTHS} mo, cap = {OOS_CAP_MULT}x median)", flush=True)
    print(f"  ISO spike months capped    : {n_iso:,}  (cap = {ISO_CAP_MULT}x median)", flush=True)
    print(f"  Total (mst,cust,ym) buckets scaled down: {total_capped:,}", flush=True)
    return scale_factors


def build_profiles(rows, fields):
    consistent_mstyles = build_consistent_mstyle_set(rows, fields)
    scale_factors      = compute_demand_caps(rows, fields)

    cat_month  = defaultdict(lambda: [0.0] * 12)
    sub_month  = defaultdict(lambda: [0.0] * 12)
    cat_years  = defaultdict(set)
    sub_years  = defaultdict(set)
    cat_total  = defaultdict(float)
    sub_total  = defaultdict(float)
    cat_styles = defaultdict(set)
    sub_styles = defaultdict(set)

    skipped_rows = 0
    out_of_window = 0
    excluded_inconsistent = 0
    excluded_oos = 0

    for r in rows:
        try:
            qty = float(r.get(fields["qty"]) or 0)
        except (TypeError, ValueError):
            skipped_rows += 1; continue
        if qty <= 0:
            continue
        cat = (r.get(fields["cat"]) or "").strip()
        sub = (r.get(fields["sub"]) or "").strip() if fields["sub"] else ""
        mst = (r.get(fields["mst"]) or "").strip() if fields.get("mst") else ""
        if not cat:
            skipped_rows += 1; continue
        # Apply consistency filter at the SKU level
        if consistent_mstyles is not None and mst not in consistent_mstyles:
            excluded_inconsistent += 1
            continue
        y, m = _parse_year_month(r, fields)
        if not y or not m:
            skipped_rows += 1; continue
        if y < START_YEAR or y > END_YEAR:
            out_of_window += 1; continue
        # Drop tariff-driven OOS months entirely so they don't depress the index
        if (y, m) in OOS_DROP_MONTHS:
            excluded_oos += 1
            continue

        # Apply demand-distortion cap (OOS catch-up / ISO spike scaling).
        # scale_factors key is (mst, cust, y, m); absent = no cap (scale=1.0).
        cust_name = (r.get(fields["cust"]) or "").strip() if fields.get("cust") else ""
        sf_key = (mst, cust_name, y, m)
        demand_scale = scale_factors.get(sf_key, 1.0)

        # Apply year weight (2024 clean baseline) and customer weight (Amazon /
        # Walmart / Petsmart get 2× pull on the seasonal shape).
        wqty = qty * demand_scale * YEAR_WEIGHTS.get(y, 1.0) * _customer_weight(cust_name)

        cat_month[cat][m-1] += wqty
        cat_total[cat]      += wqty
        cat_years[cat].add(y)
        if mst: cat_styles[cat].add(mst)
        if sub:
            key = f"{cat}||{sub}"
            sub_month[key][m-1] += wqty
            sub_total[key]      += wqty
            sub_years[key].add(y)
            if mst: sub_styles[key].add(mst)
    print(f"[aggregate] excluded {excluded_inconsistent:,} rows from inconsistent MStyles", flush=True)
    print(f"[aggregate] excluded {excluded_oos:,} rows from OOS months {sorted(OOS_DROP_MONTHS)}", flush=True)
    print(f"[aggregate] year weights: {YEAR_WEIGHTS}", flush=True)

    print(f"[aggregate] skipped {skipped_rows:,} bad rows, "
          f"{out_of_window:,} out-of-window rows", flush=True)
    print(f"[aggregate] {len(cat_month)} categories, {len(sub_month)} subcategories", flush=True)

    def _to_index(monthly_totals, total_units, years, n_consistent_skus):
        active_months = sum(1 for v in monthly_totals if v > 0)
        if n_consistent_skus < MIN_CONSISTENT_SKUS:
            return None, None, f"only {n_consistent_skus} consistent SKU(s) < {MIN_CONSISTENT_SKUS}"
        if total_units < MIN_TOTAL_UNITS:
            return None, None, f"total {int(total_units):,} < {MIN_TOTAL_UNITS:,}"
        if active_months < MIN_ACTIVE_MONTHS:
            return None, None, f"active_months {active_months} < {MIN_ACTIVE_MONTHS}"
        if len(years) < MIN_YEARS:
            return None, None, f"years {sorted(years)} < {MIN_YEARS}"
        mean = total_units / 12.0
        if mean <= 0:
            return None, None, "mean=0"
        idx = [v / mean for v in monthly_totals]
        peak = max(idx)
        nonzero = [i for i in idx if i > 0]
        trough = min(nonzero) if nonzero else 0
        peak_trough = (peak / trough) if trough > 0 else float("inf")
        if peak_trough < MIN_PEAK_TROUGH:
            return None, None, f"peak/trough {peak_trough:.2f} < {MIN_PEAK_TROUGH:.2f} (flat)"

        # Holiday lead-time uplift — boost Sep-Nov to reflect 4-6 week shipping
        # lead before consumer Nov/Dec demand peak.  Floor: even if data shows
        # Sep below 1.10, lift to at least 1.10× neutral (per planner directive).
        idx_lifted = list(idx)
        for m_idx, mult in HOLIDAY_LEAD_UPLIFT.items():
            current = idx_lifted[m_idx - 1]
            # Lift = max(current * mult, mult).  Floors at the multiplier so a
            # depressed historical month still hits the minimum +10% lift.
            idx_lifted[m_idx - 1] = max(current * mult, mult)
        # Renormalize so mean stays 1.0 after the uplift
        lmean = sum(idx_lifted) / len(idx_lifted)
        if lmean > 0:
            idx_lifted = [v / lmean for v in idx_lifted]

        # Clamp + renormalize so mean stays at 1.0 after clamping.
        clamped = [min(PROFILE_CEIL, max(PROFILE_FLOOR, v)) for v in idx_lifted]
        cmean = sum(clamped) / len(clamped)
        if cmean > 0:
            clamped = [v / cmean for v in clamped]

        idx_rounded     = [round(v, 3) for v in clamped]
        raw_idx_rounded = [round(v, 3) for v in idx]
        stats = {
            "total_units":      int(round(total_units)),
            "consistent_skus":  n_consistent_skus,
            "years":            sorted(years),
            "peak_month":       idx.index(peak) + 1,
            "raw_peak_idx":     round(peak, 2),
            "raw_trough_idx":   round(trough, 4),
            "raw_peak_trough":  round(peak_trough, 2),
            "clamped_peak":     round(max(clamped), 2),
            "clamped_trough":   round(min(clamped), 2),
            "active_months":    active_months,
            "raw_profile":      raw_idx_rounded,
        }
        return idx_rounded, stats, None

    def _apply_override(cat_name, prof, stats):
        """If a planner override exists, replace the profile and tag stats."""
        ov = PLANNER_OVERRIDES.get(cat_name)
        if not ov or len(ov) != 12:
            return prof, stats
        # normalize override to mean 1.0, then apply clamps
        mean = sum(ov) / 12.0
        if mean <= 0:
            return prof, stats
        normalized = [v / mean for v in ov]
        clamped = [min(PROFILE_CEIL, max(PROFILE_FLOOR, v)) for v in normalized]
        cmean = sum(clamped) / 12.0
        if cmean > 0:
            clamped = [v / cmean for v in clamped]
        new_stats = dict(stats)
        new_stats["planner_override"] = True
        new_stats["pre_override_profile"] = prof  # keep data-derived for audit
        return [round(v, 3) for v in clamped], new_stats

    by_category, by_subcategory = {}, {}
    skipped_cat, skipped_sub = [], []
    for cat, monthly in cat_month.items():
        prof, stats, why = _to_index(monthly, cat_total[cat], cat_years[cat],
                                     len(cat_styles[cat]))
        if prof:
            prof, stats = _apply_override(cat, prof, stats)
            by_category[cat] = {"profile": prof, "stats": stats}
        else:
            skipped_cat.append((cat, cat_total[cat], why))
    for key, monthly in sub_month.items():
        prof, stats, why = _to_index(monthly, sub_total[key], sub_years[key],
                                     len(sub_styles[key]))
        if prof:
            by_subcategory[key] = {"profile": prof, "stats": stats}
        else:
            skipped_sub.append((key, sub_total[key], why))
    return by_category, by_subcategory, skipped_cat, skipped_sub


def main():
    print(f"[start] building category profiles from direct query "
          f"on {TABLE_ID} ({START_YEAR}-{END_YEAR})", flush=True)

    rows, labels = probe_and_pull()
    fields = resolve_fields(labels)
    by_cat, by_sub, skipped_cat, skipped_sub = build_profiles(rows, fields)

    print(f"\n[result] kept {len(by_cat)} categories, {len(by_sub)} subcategories", flush=True)
    print(f"[result] skipped {len(skipped_cat)} categories, {len(skipped_sub)} subcategories")

    print("\nTop categories by volume (kept):")
    sorted_cats = sorted(by_cat.items(), key=lambda kv: -kv[1]["stats"]["total_units"])
    for cat, payload in sorted_cats[:25]:
        s = payload["stats"]
        peak_m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][s["peak_month"]-1]
        print(f"  {cat:35s}  {s['total_units']:>14,}  peak={peak_m} (raw {s['raw_peak_idx']:.2f}, clamped {s['clamped_peak']:.2f})  p/t={s['raw_peak_trough']:.2f}")

    print("\nTop skipped categories (by volume):")
    for cat, total, why in sorted(skipped_cat, key=lambda x: -x[1])[:10]:
        print(f"  {cat:35s}  {int(total):>14,}  ({why})")

    out = {
        "generated_at": date.today().isoformat(),
        "source":       f"direct /v1/records/query on table {TABLE_ID}",
        "window":       {"start_year": START_YEAR, "end_year": END_YEAR},
        "method":       "monthly Qty_Shpd index, mean=1.0, weighted+OOS-adjusted, "
                        "consistent-MStyle filter (no promo blasts)",
        "year_weights":            YEAR_WEIGHTS,
        "oos_dropped":             sorted(OOS_DROP_MONTHS),
        "strategic_customer_keywords": STRATEGIC_CUSTOMER_KEYWORDS,
        "strategic_customer_weight":   STRATEGIC_CUSTOMER_WEIGHT,
        "planner_overrides":           sorted(PLANNER_OVERRIDES.keys()),
        "holiday_lead_uplift":         {str(k): v for k, v in HOLIDAY_LEAD_UPLIFT.items()},
        "gates": {
            "min_total_units":           MIN_TOTAL_UNITS,
            "min_active_months":         MIN_ACTIVE_MONTHS,
            "min_peak_trough":           MIN_PEAK_TROUGH,
            "min_years":                 MIN_YEARS,
            "min_consistent_skus":       MIN_CONSISTENT_SKUS,
            "mstyle_min_active_months":  MIN_ACTIVE_MONTHS_PER_STYLE,
            "mstyle_min_lifespan":       MIN_LIFESPAN_MONTHS,
            "mstyle_min_activity_rate":  MIN_ACTIVITY_RATE,
        },
        "by_category":    by_cat,
        "by_subcategory": by_sub,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[saved] {OUT_PATH}  ({len(by_cat)} cats + {len(by_sub)} subcats)")


if __name__ == "__main__":
    main()
