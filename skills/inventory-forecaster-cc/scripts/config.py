"""
config.py -- Central configuration for the inventory forecaster.

Single source of truth for thresholds, window sizes, event calendar, retailer
substring lists, and Amazon inventory-health gates.

Every constant supports env-var override so tune_thresholds.py can A/B-test
threshold changes without code edits:

    F59I_WOS_HEALTHY_GATE=7.0 python run_forecast.py --all --validate

Style:
- ALL CAPS for constants (Python convention).
- Group by phase: window sizes, model params, event calendar, customer lists,
  Amazon-only F59 gates, history-normalization gates.
- Each constant has a 1-line comment explaining "what does increasing this do?"
  so future tuning has context.
"""

import os


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION / RUNTIME
# ─────────────────────────────────────────────────────────────────────────────
CDATA_MCP_URL   = "https://mcp.cloud.cdata.com/mcp"
CDATA_EMAIL     = os.environ.get("CDATA_EMAIL", "steven@skaffles.com")
CDATA_PAT       = os.environ.get("CDATA_PAT",   "VaTIPqklo14D1yMkfqKRi1punowIvp/6XEHtBSgybad2Jbyl")
MAX_RETRIES         = int(os.environ.get("MAX_RETRIES", "5"))         # CData retries
QB_REST_MAX_RETRIES = int(os.environ.get("QB_REST_MAX_RETRIES", "3"))  # QB REST (faster, more reliable)
# Audit Finding #15+16 (2026-05-25): REST sites previously used hard-coded 3;
# centralised here.  Total backoff budget per CLAUDE.md = 2+4+8 = 14s.

# Pacer for paginated retailer-POS reads.  Keeps the realm from saturating
# while iterating Retailer Sales (bv2izcn5b) pages.  Audit Finding #18
# (2026-05-25): previously a bare time.sleep(0.15) literal.
RETAILER_POS_PAGE_DELAY_S = float(os.environ.get("RETAILER_POS_PAGE_DELAY_S", "0.15"))


# ─────────────────────────────────────────────────────────────────────────────
# RULE THRESHOLDS (Audit Finding #17, 2026-05-25)
# Skeleton/registry for inline magic numbers in F40/F42/F45/F61/F75 and similar.
# Anyone adding a new threshold should put it here with: name, owning rule
# code, default, env-var override, one-line description.  Migration of EXISTING
# inline thresholds is deferred -- too many sites to migrate safely without a
# test suite, and most are already conservative defaults that don't need
# real-time tuning.  Add to this dict as you touch the relevant rule.
# ─────────────────────────────────────────────────────────────────────────────
RULE_THRESHOLDS = {
    # F40 -- baseline-ratio guard (active growth detection)
    "F40_BASELINE_RATIO":   float(os.environ.get("F40_BASELINE_RATIO",  "0.30")),
    # F42 -- spike trigger (median multiplier)
    "F42_SPIKE_MULT":       float(os.environ.get("F42_SPIKE_MULT",      "3.0")),
    "F42_BUFFER_MULT":      float(os.environ.get("F42_BUFFER_MULT",     "1.3")),
    # F45 -- per-week cap factor (vs L26 nz-mean)
    "F45_CAP_FACTOR":       float(os.environ.get("F45_CAP_FACTOR",      "2.5")),
    # F61 -- horizon decay multiplier per week
    "F61_DECAY":            float(os.environ.get("F61_DECAY",           "0.88")),
    # F75 -- end-of-window dampener trigger
    "F75_TRIGGER_MULT":     float(os.environ.get("F75_TRIGGER_MULT",    "2.0")),
}

# Direct Quickbase REST API -- bypasses CData for bulk write-back.
QB_REALM        = os.environ.get("QB_REALM",      "pim.quickbase.com")
QB_USER_TOKEN   = os.environ.get("QB_USER_TOKEN", "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s")
QB_PROJ_TABLE         = os.environ.get("QB_PROJ_TABLE",         "bpd237tvm")
QB_STYLES_TABLE       = os.environ.get("QB_STYLES_TABLE",       "bphzqfkev")  # InventoryTrack.Styles
QB_AMZ_CATALOG_TABLE  = os.environ.get("QB_AMZ_CATALOG_TABLE",  "bqp8vz625")  # InventoryTrack.Amazon_Catalog (Phase 2.5 POS)
QB_AMZ_US_TABLE       = os.environ.get("QB_AMZ_US_TABLE",       "bpfrw2epk")  # ProductTrack.Amazon_Catalog_US  (Phase 2.6)
QB_AMZ_HEALTH_TABLE   = os.environ.get("QB_AMZ_HEALTH_TABLE",   "bp9akd3js")  # ProductTrack.Amazon_Invtry_Health (Phase 2.6b)
QB_BULK_BATCH         = int(os.environ.get("QB_BULK_BATCH", "500"))

# QB report for VP-Q4 open-PO data -- bulk fetch in 1 API call.
QB_OPEN_POS_TABLE       = os.environ.get("QB_OPEN_POS_TABLE",  "bp8r4dejr")
QB_OPEN_POS_REPORT      = int(os.environ.get("QB_OPEN_POS_REPORT", "27"))
QB_OPEN_POS_CACHE_HOURS = int(os.environ.get("QB_OPEN_POS_CACHE_HOURS", "24"))


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST WINDOW + HISTORY WINDOW SIZES
# (used everywhere; current code has these as magic numbers)
# ─────────────────────────────────────────────────────────────────────────────
HORIZON_WEEKS   = int(os.environ.get("HORIZON_WEEKS", "26"))   # forecast horizon
L52_WEEKS       = int(os.environ.get("L52_WEEKS",     "52"))   # 1-year history
L26_WEEKS       = int(os.environ.get("L26_WEEKS",     "26"))   # 2-quarter history
L13_WEEKS       = int(os.environ.get("L13_WEEKS",     "13"))   # 1-quarter history
L8_WEEKS        = int(os.environ.get("L8_WEEKS",      "8"))    # recency overlay
L4_WEEKS        = int(os.environ.get("L4_WEEKS",      "4"))    # last-month trend


# ─────────────────────────────────────────────────────────────────────────────
# ALERT + MODEL TUNING
# ─────────────────────────────────────────────────────────────────────────────
ALERT_THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", "0.075"))
"""Above this absolute variance vs prior, AI_ALERT is written.
Higher = fewer alerts (less noise; more silent over/under-projections).
Lower  = more alerts (planners drown in red flags)."""

CR_ALPHA        = float(os.environ.get("CR_ALPHA", "0.3"))
"""Croston's demand + interval exponential smoothing factor.
Higher = more weight on recent observations (z, p adapt faster).
Lower  = smoother but slower to react to demand shifts."""

# Seasonal profile dampening (F16-relief path uses higher DAMP)
DAMP_NORMAL     = float(os.environ.get("DAMP_NORMAL",     "0.3"))
DAMP_F16_RELIEF = float(os.environ.get("DAMP_F16_RELIEF", "0.85"))
"""Dampens the seasonal profile around 1.0.
Higher = profile shape closer to flat (less seasonality applied).
Lower  = profile shape closer to raw (more seasonality applied).
F16-relief fires for high-seasonality categories that need the raw shape."""


# ─────────────────────────────────────────────────────────────────────────────
# EVENT CALENDAR -- Amazon-ONLY promotions (Prime Day + Fall Prime Day)
# Calendar-date-based via _get_event_boosts() in inventory_forecaster.py
# ─────────────────────────────────────────────────────────────────────────────
PRIME_DAY_BUMPS = [
    (5, 1,  float(os.environ.get("PRIME_DAY_BUMP_MAY1",  "1.25"))),
    (5, 15, float(os.environ.get("PRIME_DAY_BUMP_MAY15", "1.25"))),
    (5, 29, float(os.environ.get("PRIME_DAY_BUMP_MAY29", "1.50"))),
]
"""DC ORDERING bumps ahead of Prime Day consumer event (end of June / last Tuesday of June).
Ramp window = May. May 29 is peak pre-buy. NO July dates -- any July spike in
F_AMZ_RPL output is a data artifact (e.g. post-OOS variability cycling), NOT Prime Day.
Each tuple = (month, day, multiplier)."""

FALL_PRIME_DAY_LIFT = float(os.environ.get("FALL_PRIME_DAY_LIFT", "1.30"))
"""Fall Prime Day (first Tuesday of October) ordering bump.
Single discrete order event the Tuesday after Labor Day, ~4-5w before
the October consumer event."""

PRIME_DAY_LIFT = float(os.environ.get("PRIME_DAY_LIFT", "1.25"))
"""Representative Prime Day lift (for EDA reporting / messaging only)."""

FALL_DEAL_LIFT = float(os.environ.get("FALL_DEAL_LIFT", "1.12"))
"""Legacy trade fall deal lift (retained for backwards compatibility)."""


# ─────────────────────────────────────────────────────────────────────────────
# T5/HOLIDAY + SEASON-SPECIFIC ORDER BOOST CURVES (Amazon Active Replen only)
# Applied via _get_t5_seasonal_boosts(season) in inventory_forecaster.py.
#
# Format: {season_tag: [(month, day, multiplier), ...]}
# Calendar-date based -- same engine as PRIME_DAY_BUMPS.  Each (month, day)
# names the START of the week that gets the lift; the forecaster maps it to
# whichever projection week contains that date.
#
# Empirically derived from LY order history (2025) across 871 Amazon Active
# Replen items with sufficient non-tariff history.  Analysis run 2026-05-24.
# Season tags sourced from QB Styles.[Season] field (NOT the BB/FF prefix).
#
# Multipliers are applied via MAX with the category-profile mult in F_AMZ_RPL
# (prevents stacking with empirical category profiles that already capture
# some seasonal lift).
#
# Key findings:
#   Standard (no Season): modest T5 ramp Oct 19+; median 0.85x vs active-week
#     baseline, mean 1.17x -- indicates ordering FREQUENCY rises, not magnitude.
#     Conservative boost applied to capture incremental pre-T5 orders.
#   Holiday:  W25(Nov 2)=1.59x, W26(Nov 9)=1.57x vs active-week baseline.
#     Strong pre-T5 order build confirmed in data.
#   Halloween: spike Sep 21-28 (pre-Halloween consumer demand, ~4-6w before Oct 31).
#   Fall/Winter: moderate T5 ramp similar to standard but starting one week earlier.
#   July 4th: pre-holiday inventory build in early-mid June.
#   Seasons with no boost in the May-Nov window (off-season):
#     Easter, Spring/Summer, St Patrick's Day, Pride.
# ─────────────────────────────────────────────────────────────────────────────
AMZ_T5_HOLIDAY_BOOSTS = {
    "": [
        # Standard -- T5 pre-build ramp, Oct W3 onwards (no Season tag)
        (10, 19, float(os.environ.get("AMZ_T5_STD_W23", "1.10"))),
        (10, 26, float(os.environ.get("AMZ_T5_STD_W24", "1.10"))),
        (11,  2, float(os.environ.get("AMZ_T5_STD_W25", "1.12"))),
        (11,  9, float(os.environ.get("AMZ_T5_STD_W26", "1.15"))),
    ],
    "Holiday": [
        # Holiday-tagged items: strong T5 pre-build (backed by data)
        (10, 12, float(os.environ.get("AMZ_T5_HOL_W22", "1.15"))),
        (10, 19, float(os.environ.get("AMZ_T5_HOL_W23", "1.30"))),
        (10, 26, float(os.environ.get("AMZ_T5_HOL_W24", "1.50"))),
        (11,  2, float(os.environ.get("AMZ_T5_HOL_W25", "1.65"))),
        (11,  9, float(os.environ.get("AMZ_T5_HOL_W26", "1.75"))),
    ],
    "Fall/Winter": [
        # Fall/Winter tagged: moderate T5 ramp (data: 1.17-1.27x)
        (10, 12, float(os.environ.get("AMZ_T5_FW_W22", "1.15"))),
        (10, 19, float(os.environ.get("AMZ_T5_FW_W23", "1.25"))),
        (10, 26, float(os.environ.get("AMZ_T5_FW_W24", "1.15"))),
        (11,  2, float(os.environ.get("AMZ_T5_FW_W25", "1.20"))),
        (11,  9, float(os.environ.get("AMZ_T5_FW_W26", "1.15"))),
    ],
    "Halloween": [
        # Pre-Halloween ordering ramp (data: 1.29x on Sep 21+)
        # Amazon orders P+P product 4-6 weeks before Oct 31 consumer demand.
        (9,  7, float(os.environ.get("AMZ_HAL_SEP7",  "1.10"))),
        (9, 14, float(os.environ.get("AMZ_HAL_SEP14", "1.25"))),
        (9, 21, float(os.environ.get("AMZ_HAL_SEP21", "1.30"))),
        (9, 28, float(os.environ.get("AMZ_HAL_SEP28", "1.30"))),
        (10,  5, float(os.environ.get("AMZ_HAL_OCT5",  "1.20"))),
        (10, 12, float(os.environ.get("AMZ_HAL_OCT12", "1.10"))),
        # No T5 boost: Halloween is over by end of October
    ],
    "July 4th": [
        # Pre-4th of July inventory build in June
        (6,  7, float(os.environ.get("AMZ_J4_JUN7",  "1.20"))),
        (6, 14, float(os.environ.get("AMZ_J4_JUN14", "1.25"))),
        (6, 21, float(os.environ.get("AMZ_J4_JUN21", "1.25"))),
        (6, 28, float(os.environ.get("AMZ_J4_JUN28", "1.20"))),
        # No T5 boost: July 4th items are off-season in Oct-Nov
    ],
    # Off-season items in the May-Nov projection window: no boost applied
    "Easter":           [],
    "Spring/Summer":    [],
    "St Patrick's Day": [],
    "Pride":            [],
}
"""Season-specific T5/Holiday order boost curves for Amazon Active Replen.
Keyed by QB Styles.[Season] field value (NOT item prefix/div).
Empty list = no boost in current May-Nov projection window.
Unknown season tags fall back to standard ('') boosts."""


# ─────────────────────────────────────────────────────────────────────────────
# TRADE FALL CALENDAR -- non-Amazon retailers
# F64 (2026-05-17): top-2 planner spike weeks in trade replenishment
# ─────────────────────────────────────────────────────────────────────────────
TRADE_FALL_REPLEN_WEEKS  = {17, 18}    # early September fall replen
TRADE_FALL_SEASON2_WEEKS = {21, 22}    # early October holiday pre-order
TRADE_FALL_REPLEN_LIFT   = float(os.environ.get("TRADE_FALL_REPLEN_LIFT",  "1.10"))
TRADE_FALL_SEASON2_LIFT  = float(os.environ.get("TRADE_FALL_SEASON2_LIFT", "1.08"))


# ─────────────────────────────────────────────────────────────────────────────
# AMAZON / RETAILER SUBSTRING LISTS
# ─────────────────────────────────────────────────────────────────────────────
AMAZON_CUST_SUBSTR = "AMAZON"
# Amazon Private Label: orders placed by Amazon for their own branded products.
# No POS or DC inventory data is available for APL accounts.
# Forecast uses order history + seasonal/category profiles only.
APL_CUST_SUBSTR    = "PRIVATE LABEL"

AMZ_DIV_PO_CUTOFF = {
    "FF": 2,   # Fetch: cutoff = Tuesday night  -- zero W1 on Wed (weekday >= 2)
    "BB": 3,   # Brand Buzz: cutoff = Wed night -- zero W1 on Thu (weekday >= 3)
}
"""F_PO_CUTOFF: weekday-of-week beyond which W1 is zeroed (no time to ship).
weekday: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6."""

INTERNATIONAL_CUST_SUBSTRS = [
    "PETBARN",              # Australia
    "LOBLAWS",              # Canada
    "COMERCIALIZADORA",     # Mexico
    "GRUP CONOCIDO",        # Mexico
    "GRUP ",                # generic Mexico retail group prefix
]
"""R5: international bulk-buyer retailers. Relax Inactive classification."""

OFFPRICE_CUST_SUBSTRS = [
    "BURLINGTON", "ROSS STORES", "TJ MAXX", "T J MAXX", "MARSHALLS",
    "KOHL", "SAM'S CLUB", "VARIETY WHOLESALERS", "OLLIE", "BIG LOTS",
    "FIVE BELOW", "FRAGRANCENET", "DD'S DISCOUNTS", "DD'S DISCOUNT",
    "GABRIEL BROTHERS",
]
"""R1: off-price / one-time-buy retailers. Route through OTB detection."""


# ─────────────────────────────────────────────────────────────────────────────
# AMAZON DC INVENTORY-HEALTH GATES (F59 series)
# These were buried in forecast_record() at lines ~7900-8200. Exposing them
# here unlocks D1 (auto-tuning) and makes A/B testing trivial.
# ─────────────────────────────────────────────────────────────────────────────
F59I_WOS_HEALTHY_GATE   = float(os.environ.get("F59I_WOS_HEALTHY_GATE",   "6.0"))
"""F59i fires when Amazon DC WOS >= this many weeks (DC is "healthy").
Higher = harder to be considered healthy = F59i fires on fewer items."""

F59J_WOS_RESTOCK_GATE   = float(os.environ.get("F59J_WOS_RESTOCK_GATE",   "8.0"))
"""F59j fires when Amazon DC WOS < this many weeks (needs restock lift).
Lower = restock lift fires on fewer items.
NOTE: F59i and F59j are mutually exclusive; tune the gap with care."""

F59I_POS_ANCHOR_STRONG  = float(os.environ.get("F59I_POS_ANCHOR_STRONG",  "1.40"))
"""F59i hard-anchors AI to POS when AI/POS >= this ratio (AI too high)."""

F59I_POS_ANCHOR_BLEND   = float(os.environ.get("F59I_POS_ANCHOR_BLEND",   "1.15"))
"""F59i blends 50/50 with POS when AI/POS in [1.15, 1.40]."""

F59I_POS_FLOOR_FRAC     = float(os.environ.get("F59I_POS_FLOOR_FRAC",     "0.60"))
"""Minimum floor as fraction of POS rate after F59i anchor."""

F59K_EOL_POS_DECLINE    = float(os.environ.get("F59K_EOL_POS_DECLINE",    "0.40"))
"""F59k fires when POS_L4W / POS_L13W <= this (consumer demand is dying)."""

F59K_REAL_HISTORY_THRESH = float(os.environ.get("F59K_REAL_HISTORY_THRESH", "200"))
"""F59k requires L13W weekly ord rate < this to consider "no real history"."""

F59K_POS_CREDIBILITY    = float(os.environ.get("F59K_POS_CREDIBILITY",    "100"))
"""F59k requires POS L13W >= this for the EOL signal to be credible."""

F59J_POS_FLOOR          = float(os.environ.get("F59J_POS_FLOOR",          "50"))
"""F59j requires POS L13W >= this for the restock lift to fire."""


# ─────────────────────────────────────────────────────────────────────────────
# WRITEBACK
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA_VERSION = "2026.05.23"
"""Stamped on forecast_results.json + validation_results.json output.
Bump when output structure changes (new fields, renamed fields).
Tooling that loads these JSONs should call check_schema_version() below."""


def check_schema_version(loaded_json, source_path="(unknown)", strict=False):
    """Compare a loaded results JSON's _schema_version against current code.

    Args:
        loaded_json:  the dict returned by json.load()
        source_path:  filename for the warning message
        strict:       if True, raises ValueError on mismatch; else just warns

    Returns:
        True if version matches current, False otherwise.

    Caller pattern (in viewer.py / gap_analysis.py):
        with open(path) as f:
            data = json.load(f)
        from config import check_schema_version
        check_schema_version(data, path)        # warn-only
    """
    import sys
    found = loaded_json.get("_schema_version") if isinstance(loaded_json, dict) else None
    if found == SCHEMA_VERSION:
        return True
    if found is None:
        msg = (f"[WARN] {source_path}: no _schema_version found "
               f"(pre-2026.05.23 output). Some fields may be missing or "
               f"have different semantics.")
    else:
        msg = (f"[WARN] {source_path}: schema version mismatch -- "
               f"file is {found!r}, code expects {SCHEMA_VERSION!r}. "
               f"Re-run the forecaster to refresh.")
    if strict:
        raise ValueError(msg)
    print(msg, file=sys.stderr, flush=True)
    return False
