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
MAX_RETRIES     = int(os.environ.get("MAX_RETRIES", "5"))

# Direct Quickbase REST API -- bypasses CData for bulk write-back.
QB_REALM        = os.environ.get("QB_REALM",      "pim.quickbase.com")
QB_USER_TOKEN   = os.environ.get("QB_USER_TOKEN", "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s")
QB_PROJ_TABLE   = os.environ.get("QB_PROJ_TABLE", "bpd237tvm")
QB_BULK_BATCH   = int(os.environ.get("QB_BULK_BATCH", "500"))

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
"""Prime Day (last Tuesday of June) ordering bump dates + multipliers.
Each tuple = (month, day, multiplier). May 29 is the peak pre-buy."""

FALL_PRIME_DAY_LIFT = float(os.environ.get("FALL_PRIME_DAY_LIFT", "1.30"))
"""Fall Prime Day (first Tuesday of October) ordering bump.
Single discrete order event the Tuesday after Labor Day, ~4-5w before
the October consumer event."""

PRIME_DAY_LIFT = float(os.environ.get("PRIME_DAY_LIFT", "1.25"))
"""Representative Prime Day lift (for EDA reporting / messaging only)."""

FALL_DEAL_LIFT = float(os.environ.get("FALL_DEAL_LIFT", "1.12"))
"""Legacy trade fall deal lift (retained for backwards compatibility)."""


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
Tooling that loads these JSONs should compare against the schema version
they were built for."""
