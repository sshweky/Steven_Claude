#!/usr/bin/env python3
"""
inventory_forecaster.py
-----------------------
End-to-end AI inventory demand forecaster for Pets+People.
Pulls from Quickbase, runs EDA, computes 26-week forecasts, writes AI_PRJ_W1-W26 back.

Usage:
    python inventory_forecaster.py [options]

Scope filters (combine as needed):
    --acct 1864                  Filter by account number prefix
    --customer "AMAZON.COM.KYDC" Filter by customer name substring
    --mstyle FF8654              Filter to a single mstyle
    --brand "Glad for Pets"      Filter by Master_Brand
    --all                        All active records (Status A%)

Other options:
    --workers N      Parallel write threads (default: 6)
    --dry-run        Forecast only, no write-back
    --analyze        Run EDA analysis and generate HTML report before forecasting
    --analyze-only   Run EDA and report only — no forecasting or write-back
    --resume FILE    Skip keys already in a completed-keys file
    --out FILE       Save forecast JSON (default: forecast_results.json)
    --report FILE    Save HTML report (default: forecast_report.html)
"""

import os, sys, json, re, math, time, argparse, threading, textwrap, base64
import urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─── Dependency check ─────────────────────────────────────────────────────────
try:
    import numpy as np
except ImportError:
    sys.exit("ERROR: pip install numpy")

# ─── Config ───────────────────────────────────────────────────────────────────
# All constants imported from scripts/config.py.  That module is the single
# source of truth for thresholds and supports env-var override for A/B testing
# (e.g. F59I_WOS_HEALTHY_GATE=7.0 python run_forecast.py --all --validate).
from config import (
    # Connection
    CDATA_MCP_URL, CDATA_EMAIL, CDATA_PAT, MAX_RETRIES,
    QB_REALM, QB_USER_TOKEN, QB_PROJ_TABLE, QB_BULK_BATCH,
    QB_OPEN_POS_TABLE, QB_OPEN_POS_REPORT, QB_OPEN_POS_CACHE_HOURS,
    # Window sizes
    HORIZON_WEEKS, L52_WEEKS, L26_WEEKS, L13_WEEKS, L8_WEEKS, L4_WEEKS,
    # Model tuning
    ALERT_THRESHOLD, CR_ALPHA, DAMP_NORMAL, DAMP_F16_RELIEF,
    # Event calendar
    PRIME_DAY_BUMPS, FALL_PRIME_DAY_LIFT, PRIME_DAY_LIFT, FALL_DEAL_LIFT,
    TRADE_FALL_REPLEN_WEEKS, TRADE_FALL_SEASON2_WEEKS,
    TRADE_FALL_REPLEN_LIFT, TRADE_FALL_SEASON2_LIFT,
    AMZ_T5_HOLIDAY_BOOSTS,
    # Retailer lists
    AMAZON_CUST_SUBSTR, APL_CUST_SUBSTR, AMZ_DIV_PO_CUTOFF,
    INTERNATIONAL_CUST_SUBSTRS, OFFPRICE_CUST_SUBSTRS,
    # Amazon F59 inventory-health gates
    F59I_WOS_HEALTHY_GATE, F59J_WOS_RESTOCK_GATE,
    F59I_POS_ANCHOR_STRONG, F59I_POS_ANCHOR_BLEND, F59I_POS_FLOOR_FRAC,
    F59K_EOL_POS_DECLINE, F59K_REAL_HISTORY_THRESH, F59K_POS_CREDIBILITY,
    F59J_POS_FLOOR,
    # Writeback
    SCHEMA_VERSION,
)
# R4 removed 2026-05-05 -- see CHANGELOG.md
# T4 — E-commerce retailers (non-Amazon) where the planner has forward-looking
# POS insight that isn't visible in raw order history.  For Seasonal Baseline
# items at these accounts, shift baseline toward L4/L13 non-zero averages to
# capture late-cycle acceleration the AI can't see from historical orders alone.
ECOM_CUST_SUBSTRS = [
    "CHEWY",
    "PETCO.COM",
    "PETSMART.COM",
]

# F70 — Switchover variant suffixes.
# Styles ending in these suffixes (e.g. FF8654EC, FF8654COS, FF8654AMZ) are
# treated as drop-ship / ecom variants of the base mstyle (FF8654).  Because
# the retailer orders EITHER the base OR the variant in any given week -- never
# both -- having projections or open orders on a variant means those weeks
# should NOT also have demand planned on the base.  Extend this list as new
# variant types are introduced.
SWITCHOVER_SUFFIXES = ("EC", "COS", "AMZ", "DS", "DTC")

# F66 — Per-customer bias correction.  Customers where planners override AI
# in the same direction >75% of the time get a post-model calibration multiplier.
# Derived from manual-vs-AI analysis (2026-05-17).
# Multipliers: > 1.0 = AI under-projects, < 1.0 = AI over-projects.
# International accounts (Mexico) handled separately by is_international flag.
CUSTOMER_BIAS_CORRECTIONS = {
    "PSP DISTRIBUTION":          1.25,
    "THEIS DISTRIBUTING":        1.25,
    "IMPERIAL DISTRIBUTORS":     1.35,
    "ARMY-AIR-FORCE EXCH":       1.40,
    "TARGET CTRL INV PRCSNG":    1.40,  # P9 (2026-05-24): manual UP-bias on 13/15 records, $103K gap
    "PET PHARM":                 0.55,
    "H G BUYING":                0.45,
    "PETCO MEXICO":              0.45,
}

# F19 — Conservative inactive floor (on-by-default 2026-05-06).  Items
# classified Inactive with manual_total ≥ 5,000 get a 50% manual-shaped floor
# instead of a flat zero forecast — when there's evidence the item is "paused
# but alive" rather than truly dead.
# Liveness signal:
#   • Amazon path: Avg_Units_Wk_L52w > 0  (consumers still buying at retail)
#   • Non-Amazon path: last non-zero order in hist within 26 weeks
# Velocity cap on the floor:
#   • Amazon: POS L52 × 26 (consumer demand ceiling)
#   • Non-Amazon: half of L52 order total (historical-rate proxy)
# CLI flag preserved: pass --no-conservative-inactive (or set this False) to
# revert to the prior zero-forecast-on-Inactive behavior.
CONSERVATIVE_INACTIVE = True

# ── Rule-fire tracking (2026-05-06) ───────────────────────────────────────────
# Per-record collector populated during forecast/validate execution.  Reset at
# the start of each record's processing in forecast_record() / validate_record(),
# and surfaced in the output dict as `rule_fires`.  Used by the deck-builder
# harvest to map each refinement (F19, R5, T4, F37, VP-Q4, etc.) to a real
# acct-mstyle key it actually fires on.
import threading as _threading_for_rule_fires
_RULE_FIRES = _threading_for_rule_fires.local()

def _fire(code):
    """Tag the current record with a rule-firing code (e.g. 'F19', 'R5').
    LEGACY API: untyped, string-only. Prefer fire() (below) for new rules.
    """
    bucket = getattr(_RULE_FIRES, "bucket", None)
    if bucket is not None:
        bucket.add(code)


# ─── Structured rule fire (Phase 3 -- B2/C3) ─────────────────────────────────
# New rules should use this API. It records:
#   - The rule code (same as _fire())
#   - A typed payload (numbers/strings/dicts useful for downstream analysis)
#   - A narrative template that the AI_ALERT and AI_ANALYSIS renderers can use
#   - The phase the rule belongs to (one of HIS, CLS, BAS, GAT, HRD, FIN)
#   - Severity (info / warn / critical)
# Old _fire("Fxx") calls continue to work; the regex scanner picks them up.
# Migration is gradual: any rule can switch from _fire() to fire() incrementally.

_RULE_PHASES = {"HIS", "CLS", "BAS", "GAT", "HRD", "FIN"}

def fire(code, meta=None, phase=None, severity="info",
         narrative=None, **payload):
    """Structured rule fire. Records both the rule code AND a typed entry in
    meta['structured_drivers']. Also calls _fire(code) so the legacy regex
    scanner still picks it up.

    Args:
        code: rule code (e.g. "F18", "VP-Q4", "HRD-001")
        meta: the rule meta dict from forecast_record() / model body
        phase: one of {"HIS", "CLS", "BAS", "GAT", "HRD", "FIN"} (optional)
        severity: "info", "warn", or "critical"
        narrative: format string used by AI_ALERT/AI_ANALYSIS renderers,
                   e.g. "F18 POS-cap fired: implied {implied}/wk > 2x POS {pos}/wk"
        **payload: arbitrary structured data attached to this fire event
                   (e.g. implied=2693, pos=480, cap_to=540)

    Returns:
        The created structured_drivers entry dict (caller may mutate further).

    Example:
        fire("F18", meta, phase="BAS", severity="warn",
             narrative="F18 POS-anchored cap: implied {implied}/wk vs POS {pos}/wk",
             implied=2693.5, pos=480.0, cap_to=540.0)
    """
    # Always tag the legacy bucket so existing _scan_rule_fires() still works
    _fire(code)

    if not isinstance(meta, dict):
        return None

    entry = {
        "code":      code,
        "phase":     phase,
        "severity":  severity,
        "narrative": narrative,
        "payload":   payload,
    }
    meta.setdefault("structured_drivers", []).append(entry)

    # Also append a human-readable string to drivers[] for back-compat with the
    # text-scanning narrative renderer.  Skip if no narrative template.
    if narrative:
        try:
            rendered = narrative.format(**payload)
        except (KeyError, IndexError):
            rendered = f"{code} (payload: {payload})"
        meta.setdefault("drivers", []).append(rendered)

    return entry


def _start_rule_fires():
    _RULE_FIRES.bucket = set()

def _take_rule_fires():
    """Return sorted list of fires for this record, then reset."""
    bucket = getattr(_RULE_FIRES, "bucket", None) or set()
    _RULE_FIRES.bucket = set()
    return sorted(bucket)

import re as _re_for_rule_fires
_RULE_TAG_RE = _re_for_rule_fires.compile(
    r"\b(VP-Q[1-4]|VP-FL|VP-ATS(?:-Catch)?|R[1-9]|F\d+[a-z]?|T4|S6|M1)\b"
)

def _scan_rule_fires(meta=None, alert="", baseline_mode="", model="",
                     biweekly=False, is_amazon=False, is_international=False):
    """Derive rule_fires from existing driver/alert/mode/model signatures.
    Avoids per-branch _fire() instrumentation by scanning what the forecaster
    already records about itself."""
    fires = set(_take_rule_fires())  # any explicit _fire() calls
    if isinstance(meta, dict):
        for d in meta.get("drivers", []) or []:
            fires.update(_RULE_TAG_RE.findall(str(d)))
    if alert:
        fires.update(_RULE_TAG_RE.findall(alert))
    bm = baseline_mode or ""
    # Baseline-mode signatures that map to specific rules:
    if "L13 nz-avg" in bm and "OOS:" in bm:
        # Non-zero L13 average used because the all-weeks avg would be dragged
        # down by post-event quiet weeks (VP-Q1) AND OOS treated as demand
        # intent (VP-Q2).
        fires.add("VP-Q1"); fires.add("VP-Q2")
    if "L13 nz-avg" in bm and "post-event drawdown" in bm:
        fires.add("VP-Q1"); fires.add("VP-Q3")
    if "L13 all-weeks avg" in bm:
        fires.add("VP-Q1")
    if "L26 nz-avg" in bm and "sparse" in bm:
        fires.add("F4")
    # Model-based signatures:
    m = model or ""
    if m == "OTB (zero)":
        fires.add("R1")
    if m.startswith("Inactive+Floor"):
        fires.add("R3")
    if m.startswith("Inactive+S6"):
        fires.add("S6")
    if "Pre-launch NEW" in m:
        fires.add("F31"); fires.add("F5")
    if m == "Inactive (zero order history)":
        fires.add("F30")
    # Cadence enforcement:
    if biweekly:
        fires.add("VP-Q3")
    # International liveness extension:
    if is_international:
        fires.add("R5")
    # Returning sorted list
    return sorted(fires)

# ─── Category seasonality profiles ────────────────────────────────────────────
# Monthly demand multipliers (Jan=0 … Dec=11) for product categories with strong
# known seasonal demand that may not be captured in short order histories.
# 1.0 = average month. Normalized per projection window so mean stays at 1.0.
# Applied as a 70% category / 30% historical blend in seasonal_baseline()
# and heuristic(); as a per-week qty scaler in crostens().
# Keywords are checked as case-insensitive substrings of the item Description.
# First match wins — list more specific terms before broader ones.
CATEGORY_PROFILES = {
    # ── Outdoor cooking / grilling — RETAIL ORDERING lead-time adjusted ──────
    # Consumer grilling peaks May–Aug, but RETAILERS place orders Jan–Apr
    # (8–10 week lead before consumer demand).  Profile models ORDERING
    # behavior, not consumer demand: peak Feb–Apr, rapid fall-off after May.
    # Updated 2026-05-17: shifted from consumer-demand peak (May-Jun) to
    # retail ordering peak (Feb-Apr) to match planner override patterns
    # (-45.8% aggregate planners cutting AI on Kingsford, 19/31 records DOWN).
    # Aug (idx 7) reduced from 0.40/0.35 → 0.26: mid-August cliff to pre-season
    # levels per planner feedback (2026-05-20).  Jul=0.70 → Aug=0.26 → Sep=0.25
    # reflects that retailer grilling orders are essentially done by mid-August.
    "charcoal":      [0.50, 1.20, 1.90, 2.10, 1.70, 1.30, 0.70, 0.26, 0.25, 0.22, 0.22, 0.35],
    "chimney":       [0.50, 1.20, 1.90, 2.10, 1.70, 1.30, 0.70, 0.26, 0.25, 0.22, 0.22, 0.35],
    "fire starter":  [0.50, 1.15, 1.85, 2.05, 1.65, 1.25, 0.65, 0.26, 0.25, 0.22, 0.22, 0.35],
    "firestarter":   [0.50, 1.15, 1.85, 2.05, 1.65, 1.25, 0.65, 0.26, 0.25, 0.22, 0.22, 0.35],
    "lighter fluid": [0.50, 1.15, 1.85, 2.05, 1.65, 1.25, 0.65, 0.26, 0.25, 0.22, 0.22, 0.35],
    "grill brush":   [0.45, 1.10, 1.80, 2.00, 1.60, 1.20, 0.65, 0.26, 0.25, 0.22, 0.25, 0.35],
    "grill cleaner": [0.45, 1.10, 1.80, 2.00, 1.60, 1.20, 0.65, 0.26, 0.25, 0.22, 0.25, 0.35],
    "wooden fire":   [0.50, 1.20, 1.90, 2.10, 1.70, 1.30, 0.70, 0.26, 0.25, 0.22, 0.22, 0.35],
    "kingsford":     [0.50, 1.20, 1.90, 2.10, 1.70, 1.30, 0.70, 0.26, 0.25, 0.22, 0.22, 0.35],

    # ── Disposable Tabletop — dual peak: summer + holiday ────────────────────
    # Plates, bowls, cups, cutlery (paper + plastic + foam).
    # Everyday use year-round with two clear seasonal lifts:
    #   Summer (consumer May-Aug): retailers order Apr-Jul for cookouts/picnics
    #   Holiday (consumer Nov-Dec): retailers order Oct-Nov for Thanksgiving/Christmas
    #   Sep is early holiday ramp.  Updated 2026-05-20 per planner feedback
    #   (previous profile was summer-only; Thanksgiving/Christmas are also peaks).
    #   Fix A (2026-05-24): renamed from "Paper-goods"; added plastic/foam cutlery keywords.
    "snack bowl":    [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "paper bowl":    [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "paper plate":   [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "paper cup":     [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "plastic fork":  [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "plastic knife": [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "plastic spoon": [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "plastic cup":   [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "plastic bowl":  [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "plastic plate": [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "foam plate":    [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "foam bowl":     [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "foam cup":      [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "bath cup":      [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "cutlery set":   [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],
    "cutlery":       [0.50, 0.55, 0.80, 1.25, 1.55, 1.65, 1.55, 1.35, 1.10, 1.20, 1.30, 0.55],

    # ── Air cae (air care / home fragrance) — mild warm-weather lift Apr–Aug ───
    # Mild broad seasonal lift in warm months; falls off Oct-Dec.
    # deodorizing ball removed 2026-05-20 (planner feedback: everyday flat use).
    # Fix A (2026-05-24): renamed section "Air cae"; added fraganzia (brand keyword),
    #   air care, odor elim, room spray, linen spray.  fraganzia had been removed
    #   2026-05-20 for "no seasonality" but user confirmed it IS a brand that belongs
    #   in this profile group.
    "air freshener":      [0.70, 0.75, 0.90, 1.15, 1.30, 1.35, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65],
    "scent booster":      [0.70, 0.75, 0.90, 1.15, 1.30, 1.35, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65],
    "fraganzia":          [0.70, 0.75, 0.90, 1.15, 1.30, 1.35, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65],
    "air care":           [0.70, 0.75, 0.90, 1.15, 1.30, 1.35, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65],
    "odor elim":          [0.70, 0.75, 0.90, 1.15, 1.30, 1.35, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65],
    "room spray":         [0.70, 0.75, 0.90, 1.15, 1.30, 1.35, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65],
    "linen spray":        [0.70, 0.75, 0.90, 1.15, 1.30, 1.35, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65],

    # ── Cleaning tools / household cleaners — spring cleaning peak Mar–Apr ─────
    # Multi-purpose cleaners, floor cleaners, surface sprays.  Year-round with
    # a mild spring-cleaning bump (Feb-Apr) and modest holiday uptick (Nov-Dec).
    # Fix A (2026-05-24): added per planner feedback — Fabuloso is a cleaning
    #   product brand, not an air-care item.
    "fabuloso":           [0.80, 0.90, 1.25, 1.30, 1.05, 0.95, 0.90, 0.85, 0.90, 0.95, 1.05, 1.10],
    "cleaning spray":     [0.80, 0.90, 1.25, 1.30, 1.05, 0.95, 0.90, 0.85, 0.90, 0.95, 1.05, 1.10],
    "all purpose clean":  [0.80, 0.90, 1.25, 1.30, 1.05, 0.95, 0.90, 0.85, 0.90, 0.95, 1.05, 1.10],
    "floor cleaner":      [0.80, 0.90, 1.25, 1.30, 1.05, 0.95, 0.90, 0.85, 0.90, 0.95, 1.05, 1.10],
    "multi purpose clean":[0.80, 0.90, 1.25, 1.30, 1.05, 0.95, 0.90, 0.85, 0.90, 0.95, 1.05, 1.10],

    # ── Holiday / gifting — peak Sep–Nov retailer orders for Q4 sell-through ─
    # Retailer orders Aug–Nov so consumers can buy through Dec.
    "holiday":       [1.50, 0.60, 0.40, 0.40, 0.50, 0.60, 0.70, 1.10, 1.70, 2.20, 2.30, 1.50],
    "christmas":     [1.50, 0.60, 0.40, 0.40, 0.50, 0.60, 0.70, 1.10, 1.70, 2.20, 2.30, 1.50],
    "gift set":      [1.30, 0.65, 0.45, 0.45, 0.55, 0.65, 0.75, 1.05, 1.55, 2.00, 2.20, 1.40],

    # ─────────────────────────────────────────────────────────────────────────
    # PET-INDUSTRY SEASONALITY (added 2026-05-06)
    # Replaces sun-care / pest-control / ice-melt profiles which don't apply to
    # the P+P brand portfolio (Vibrant Life, Glad for Pets, Burt's Bees Pets,
    # Arm & Hammer Pet, BioSilk Pets, etc.).  Retailer ordering leads consumer
    # in-store demand by 4–8 weeks; profile months reflect ORDER timing.
    # ─────────────────────────────────────────────────────────────────────────

    # ── New-puppy season — consumer peak Jan (post-holiday + New Year) ───────
    # Retailer orders Nov–Dec for January floor sets. Items: training pads,
    # starter crates/leashes, puppy intro food, beginner toys, "new puppy" kits.
    "puppy":             [1.40, 1.10, 0.85, 0.75, 0.70, 0.70, 0.75, 0.85, 0.95, 1.20, 1.55, 1.55],
    "new puppy":         [1.40, 1.10, 0.85, 0.75, 0.70, 0.70, 0.75, 0.85, 0.95, 1.20, 1.55, 1.55],
    "puppy pad":         [1.40, 1.10, 0.85, 0.75, 0.70, 0.70, 0.75, 0.85, 0.95, 1.20, 1.55, 1.55],
    "training pad":      [1.40, 1.10, 0.85, 0.75, 0.70, 0.70, 0.75, 0.85, 0.95, 1.20, 1.55, 1.55],
    "potty pad":         [1.40, 1.10, 0.85, 0.75, 0.70, 0.70, 0.75, 0.85, 0.95, 1.20, 1.55, 1.55],
    "crate training":    [1.30, 1.10, 0.90, 0.80, 0.75, 0.75, 0.80, 0.90, 1.00, 1.20, 1.50, 1.50],

    # ── Pet dental month — February (in-store).  Retailer Dec–Jan peak. ──────
    # Items: dental chews, toothpaste, toothbrush, dental sticks, breath spray.
    "dental chew":       [1.85, 1.50, 0.85, 0.70, 0.70, 0.70, 0.70, 0.75, 0.80, 0.85, 1.05, 1.55],
    "dental stick":      [1.85, 1.50, 0.85, 0.70, 0.70, 0.70, 0.70, 0.75, 0.80, 0.85, 1.05, 1.55],
    "dental treat":      [1.85, 1.50, 0.85, 0.70, 0.70, 0.70, 0.70, 0.75, 0.80, 0.85, 1.05, 1.55],
    "pet toothpaste":    [1.85, 1.50, 0.85, 0.70, 0.70, 0.70, 0.70, 0.75, 0.80, 0.85, 1.05, 1.55],
    "pet toothbrush":    [1.85, 1.50, 0.85, 0.70, 0.70, 0.70, 0.70, 0.75, 0.80, 0.85, 1.05, 1.55],
    "dog dental":        [1.85, 1.50, 0.85, 0.70, 0.70, 0.70, 0.70, 0.75, 0.80, 0.85, 1.05, 1.55],
    "cat dental":        [1.85, 1.50, 0.85, 0.70, 0.70, 0.70, 0.70, 0.75, 0.80, 0.85, 1.05, 1.55],
    "breath fresh":      [1.55, 1.30, 0.85, 0.75, 0.75, 0.75, 0.75, 0.80, 0.85, 0.90, 1.10, 1.45],

    # ── Grooming season — consumer Mar–Aug; retailer Feb–Jul ─────────────────
    # Items: shampoo, conditioner, deshedding tools, brushes, grooming wipes,
    # detangling spray, paw care.
    "pet shampoo":       [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "dog shampoo":       [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "cat shampoo":       [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "pet conditioner":   [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "deshed":            [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "de-shed":           [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "shedding":          [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "grooming wipe":     [0.70, 1.25, 1.55, 1.65, 1.60, 1.50, 1.35, 1.10, 0.80, 0.60, 0.50, 0.45],
    "pet wipe":          [0.70, 1.25, 1.55, 1.65, 1.60, 1.50, 1.35, 1.10, 0.80, 0.60, 0.50, 0.45],
    "groom":             [0.70, 1.25, 1.55, 1.65, 1.60, 1.50, 1.35, 1.10, 0.80, 0.60, 0.50, 0.45],
    "detangl":           [0.70, 1.25, 1.55, 1.65, 1.60, 1.50, 1.35, 1.10, 0.80, 0.60, 0.50, 0.45],
    "slicker brush":     [0.70, 1.25, 1.55, 1.65, 1.60, 1.50, 1.35, 1.10, 0.80, 0.60, 0.50, 0.45],
    "pet brush":         [0.70, 1.25, 1.55, 1.65, 1.60, 1.50, 1.35, 1.10, 0.80, 0.60, 0.50, 0.45],
    "paw balm":          [0.80, 1.10, 1.40, 1.50, 1.50, 1.40, 1.25, 1.05, 0.85, 0.70, 0.65, 0.65],
    "biosilk":           [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
    "chi pet":           [0.65, 1.30, 1.65, 1.75, 1.65, 1.50, 1.30, 1.05, 0.75, 0.55, 0.45, 0.40],
}

# M2 fix (2026-05-21) -- Pre-sorted iteration order for _get_category_profile().
# Longest keyword first so "grooming wipe" beats "groom", "puppy pad" beats "puppy", etc.
# Avoids relying on dict insertion order for correctness.
_CATEGORY_PROFILES_BY_LEN = sorted(
    CATEGORY_PROFILES.items(),
    key=lambda kv: (-len(kv[0]), kv[0])
)

# ─── Explicit Season field → monthly profile ─────────────────────────────────
# Quickbase1.ProductTrack.Styles.[Season] contains a planner-curated seasonality
# tag per SKU.  When present, this takes priority over description/brand keyword
# matching.  Values observed in the data (2026-04-21):
#   Easter, Fall/Winter, Halloween, Holiday (Thanksgiving/Christmas),
#   July 4th, Pride, Spring/Summer, St Patrick's Day, Valentines Day
# Profiles are monthly demand multipliers (Jan=0 … Dec=11), normalized per
# projection window in _category_week_multipliers() so the mean stays at 1.0.
# Retail ordering leads consumer demand by ~4-8 weeks — peaks are shifted to
# reflect when retailers place orders, not when consumers buy.
SEASON_TO_PROFILE = {
    # Thanksgiving + Christmas paper goods / gifting — retailer orders Aug–Nov
    "Holiday":         [1.50, 0.60, 0.40, 0.40, 0.50, 0.60, 0.70, 1.10, 1.70, 2.20, 2.30, 1.50],
    # Halloween — retailer orders Jul–Sep, peaks Aug
    "Halloween":       [0.30, 0.30, 0.30, 0.35, 0.45, 0.90, 1.80, 2.40, 2.10, 1.50, 0.50, 0.30],
    # Independence Day — retailer orders Apr–Jun, peak May
    "July 4th":        [0.25, 0.30, 0.50, 1.20, 2.20, 2.00, 1.30, 0.80, 0.65, 0.55, 0.45, 0.35],
    # Easter — retailer orders Jan–Mar, peak Feb
    "Easter":          [1.10, 2.00, 2.10, 1.40, 0.80, 0.60, 0.55, 0.55, 0.60, 0.65, 0.80, 0.85],
    # Valentines Day — retailer orders Nov–Jan, peak Dec
    "Valentines Day":  [1.50, 0.70, 0.55, 0.55, 0.60, 0.65, 0.70, 0.75, 0.85, 1.00, 1.50, 2.15],
    # St Patrick's Day — retailer orders Dec–Feb, peak Jan
    "St Patrick's Day":[2.00, 1.70, 0.60, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 0.95, 1.25, 1.70],
    # Pride (June) — retailer orders Mar–May, peak Apr
    "Pride":           [0.35, 0.50, 1.20, 2.00, 1.90, 1.40, 0.90, 0.70, 0.60, 0.55, 0.45, 0.45],
    # Spring/Summer outdoor lifestyle — retailer orders Feb–Jun, peak Mar–May
    "Spring/Summer":   [0.50, 0.90, 1.45, 1.65, 1.55, 1.35, 1.15, 0.95, 0.75, 0.65, 0.55, 0.55],
    # Fall/Winter indoor / cold-weather — retailer orders Aug–Dec, peak Sep–Nov
    "Fall/Winter":     [0.70, 0.60, 0.55, 0.55, 0.60, 0.70, 0.90, 1.35, 1.65, 1.65, 1.50, 1.25],
}


# ─── Empirical (data-derived) category seasonality ───────────────────────────
# Built by build_category_profiles.py from 2024-2026 Invoices.Qty_Shpd.
# JSON shape: { "by_category": {<cat>: {"profile": [12], "stats": {...}}},
#               "by_subcategory": {"<cat>||<sub>": {"profile": [12], "stats": {...}}} }
# Loaded once at first use; missing file means we fall through to keyword logic.
_DERIVED_CACHE = None

def _load_derived_profiles():
    global _DERIVED_CACHE
    if _DERIVED_CACHE is not None:
        return _DERIVED_CACHE
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "derived_category_profiles.json")
    if not os.path.exists(path):
        _DERIVED_CACHE = {"by_category": {}, "by_subcategory": {}}
        return _DERIVED_CACHE
    try:
        with open(path) as f:
            data = json.load(f)
        _DERIVED_CACHE = {
            "by_category":    data.get("by_category", {}),
            "by_subcategory": data.get("by_subcategory", {}),
        }
    except Exception as e:
        print(f"  [warn] could not load derived_category_profiles.json: {e}")
        _DERIVED_CACHE = {"by_category": {}, "by_subcategory": {}}
    return _DERIVED_CACHE


# Forecasting rules for empirical category profiles (planner directive 2026-05-04):
#   1. Only apply seasonality from a category profile if that category has
#      MORE than this many consistent SKUs.  Below the gate, the profile is
#      considered too noisy and we fall through to next-priority match.
#   2. Apply seasonal indexes only as upward demand multipliers — floor every
#      month value at 1.0 so the index never DECREASES forecast demand.
#   3. Values <= 0.80 are noise — clamp to 1.0 (== rule #2 effectively).
SEASONAL_MIN_SKU_COUNT = 10        # require strictly > this many SKUs
SEASONAL_FLOOR         = 1.00      # never multiply demand below 1.0× baseline
SEASONAL_NOISE_GATE    = 0.80      # values <= 0.80 ignored (floored at 1.0)


def _apply_forecasting_rules(profile_values, n_skus):
    """Apply the planner-directive forecasting rules to a 12-element profile.

    Returns the rule-adjusted profile, or None if the SKU gate is not met
    (signaling the caller to fall through to next-priority match).
    """
    if profile_values is None:
        return None
    if n_skus is not None and n_skus <= SEASONAL_MIN_SKU_COUNT:
        return None  # SKU gate failed — let caller try next priority
    # Floor at 1.0 — only allow seasonality to INCREASE demand
    return [max(v, SEASONAL_FLOOR) for v in profile_values]


def _get_category_profile(description, product_category=None, product_subcategory=None,
                          brand=None, brand_pt=None, season=None):
    """
    Match priority:
      1. Explicit planner-applied Season tag from Styles.[Season]
      2. Empirical (Product_Category, Product_Subcategory) — data-derived
         from 2024-2026 invoice ship history (most specific shape match)
      3. Empirical Product_Category alone — data-derived (broader fallback)
      4. Keyword substring match on description / category / brand
         (hand-curated CATEGORY_PROFILES fallback for items missing tags)

    Forecasting rules applied to empirical profiles (priorities 2 & 3):
      - Skip if consistent_skus <= SEASONAL_MIN_SKU_COUNT  (fall to next priority)
      - Floor every month at SEASONAL_FLOOR (1.0) — only increase demand

    Returns a 12-element monthly multiplier list, or None if no match.
    """
    # Priority 1 — explicit Season tag from Quickbase.ProductTrack.Styles.
    # (Hand-curated tags; not subject to data-quality gate.)
    if season:
        profile = SEASON_TO_PROFILE.get(season.strip())
        if profile is not None:
            return [max(v, SEASONAL_FLOOR) for v in profile]

    # Priority 2 + 3 — empirical profiles built from invoice history,
    # subject to SKU gate and floor-at-1.0 rule.
    derived = _load_derived_profiles()
    cat = (product_category or "").strip()
    sub = (product_subcategory or "").strip()
    if cat and sub:
        sub_key = f"{cat}||{sub}"
        sub_payload = derived["by_subcategory"].get(sub_key)
        if sub_payload and sub_payload.get("profile"):
            n_skus = (sub_payload.get("stats") or {}).get("consistent_skus")
            adjusted = _apply_forecasting_rules(sub_payload["profile"], n_skus)
            if adjusted is not None:
                return adjusted
    if cat:
        cat_payload = derived["by_category"].get(cat)
        if cat_payload and cat_payload.get("profile"):
            n_skus = (cat_payload.get("stats") or {}).get("consistent_skus")
            adjusted = _apply_forecasting_rules(cat_payload["profile"], n_skus)
            if adjusted is not None:
                return adjusted

    # Priority 4 — keyword fallback for items missing structured category tags.
    # Hand-curated profiles; floor-at-1.0 still applies but no SKU gate.
    texts = [
        (description or "").lower(),
        (product_category or "").lower(),
        (product_subcategory or "").lower(),
        (brand or "").lower(),
        (brand_pt or "").lower(),
    ]
    combined = " | ".join(texts)
    # M2 (2026-05-21) -- Iterate by keyword length DESCENDING so longer/more
    # specific keywords win over short generic ones.  Without this, dict
    # insertion order matters; "groom" could match before "grooming wipe"
    # and the wrong profile would apply.  Building the sorted list each call
    # is O(n log n) but n is small (~50 keywords) and the function is hot --
    # cache the order at module load instead.
    for keyword, profile in _CATEGORY_PROFILES_BY_LEN:
        if keyword in combined:
            return [max(v, SEASONAL_FLOOR) for v in profile]
    return None


def _category_week_multipliers(description, product_category=None, product_subcategory=None,
                               brand=None, brand_pt=None, season=None):
    """
    Compute 26-element category seasonal multipliers for the current projection
    window. Anchors on ORIG_PRJ_COLS[0] (format MM_DD_W1) as the start date.
    Returns a normalized list (mean=1.0) or None if no category match.
    """
    profile = _get_category_profile(description, product_category, product_subcategory,
                                    brand, brand_pt, season=season)
    if profile is None:
        return None
    col = ORIG_PRJ_COLS[0]          # e.g. "03_29_W1"
    month, day = int(col[0:2]), int(col[3:5])
    from datetime import date as _dt, timedelta as _td
    today = _dt.today()
    prj_start = _dt(today.year, month, day)
    if (prj_start - today).days < -180:    # wrapped to prior year
        prj_start = _dt(today.year + 1, month, day)
    mults = [float(profile[(prj_start + _td(weeks=w)).month - 1])
             for w in range(26)]
    mean = sum(mults) / len(mults)
    if mean > 0:
        mults = [m / mean for m in mults]
    return mults


# Projection validation thresholds
VALID_HIGH_MULT  = 2.0   # projection > baseline*seasonal*this → WARNING
VALID_LOW_MULT   = 0.3   # projection < baseline*seasonal*this → WARNING
VALID_SPIKE_MULT = 5.0   # projection > baseline*seasonal*this → CRITICAL

# Demand pattern thresholds (based on L26W non-zero rate).
# ≥ DENSE_THRESHOLD    → Seasonal Baseline  (orders most weeks)
# ≥ CROSTON_THRESHOLD  → Croston's          (intermittent, every 2–5 weeks)
# <  CROSTON_THRESHOLD → Sparse Intermittent (truly lumpy, every 6–12 weeks)
DENSE_THRESHOLD   = 0.35   # ≥  9 active weeks in L26W
CROSTON_THRESHOLD = 0.25   # ≥  7 active weeks in L26W

# Initial Stocking Order (ISO) detection.
# ISO = retailer's first-ever purchase of an item: a large stocking order
# followed by low/no activity while product hits shelves and sales develop.
# ISO_SPIKE_RATIO : first order must be ≥ this × the post-ISO trickle avg.
# ISO_SETTLE_WEEKS: how many weeks after the ISO the retailer is expected to
#                   pull low quantities before regular ordering begins.
ISO_SPIKE_RATIO  = 4.0
ISO_SETTLE_WEEKS = 13

# Ord_LW column order: oldest (index 0) -> newest (index 51) — 52 weeks of order history.
# Orders are the primary demand signal: they reflect true customer demand even when
# partial shipments occurred due to stockouts (shipments would understate demand).
ORD_COLS = [f"Ord_LW_{i}" for i in range(51, 0, -1)] + ["Ord_LW"]

# Shp_LW column order: oldest (index 0) -> newest (index 51) — 52 weeks of ship history.
# Used for viewer display alongside orders; comparing the two reveals stockout weeks.
SHP_COLS = [f"Shp_LW_{i}" for i in range(51, 0, -1)] + ["Shp_LW"]

# Last 26 weeks of orders for the viewer display row (subset of ORD_COLS).
ORD_L26_COLS = [f"Ord_LW_{i}" for i in range(25, 0, -1)] + ["Ord_LW"]

# Suggested_Projection_Wk columns (written by the AI forecast run).
# Pulled at validation time and stored in validation_results.json so the viewer
# can display them instantly — no second CData round-trip needed per row click.
SUGG_COLS = [f"Suggested_Projection_Wk{w}" for w in range(1, 27)]
OPN_COLS  = [f"Opn_W{w}" for w in range(1, 27)]

# Anticipated on-hand by week (Projections table).  Inv_WkN = OH at end of
# week N AFTER subtracting the current AI projection for that week.  Used by
# F37 to detect forward inventory shortfalls and constrain ship qty.
INV_OH_COLS = [f"Inv_Wk{w}" for w in range(1, 27)]

def _make_prj_cols(ref_date=None):
    """
    Compute 26 date-stamped projection column names.
    Week 1 = the most recent Sunday on or before ref_date (today by default).
    If today IS Sunday, Week 1 = today.
    Format: MM_DD_W{n}  e.g. 03_29_W1
    """
    d = ref_date or date.today()
    # weekday(): Mon=0 … Sat=5, Sun=6  → days to subtract to reach prev Sunday
    days_since_sunday = (d.weekday() + 1) % 7   # Sun→0, Mon→1, …, Sat→6
    w1 = d - timedelta(days=days_since_sunday)
    return [
        f"{(w1 + timedelta(weeks=n)).strftime('%m_%d')}_W{n + 1}"
        for n in range(26)
    ]


def _discover_prj_cols():
    """
    Auto-discover the current 26 date-stamped projection columns by probing QB.

    Strategy: try candidate W1 Sunday dates going back from the most recent Sunday.
    For each candidate, run a SELECT on that column; cdata_query() returns [] when
    the column doesn't exist (CData returns an error inside the response content, so
    no Python exception is raised and no retry occurs — just an immediate []).
    Since active records always exist, a non-empty result confirms the column.

    Falls back to _make_prj_cols() if all probes fail.
    """
    d = date.today()
    days_since_sunday = (d.weekday() + 1) % 7   # Sun→0, Mon→1, …, Sat→6
    this_sunday = d - timedelta(days=days_since_sunday)

    for weeks_back in range(0, 9):               # probe up to 8 Sundays back
        candidate = this_sunday - timedelta(weeks=weeks_back)
        col_w1    = f"{candidate.strftime('%m_%d')}_W1"
        rows = cdata_query(
            f"SELECT [{col_w1}], [Acct_MStyle_Key_] "
            f"FROM [Quickbase1].[InventoryTrack].[Projections] "
            f"WHERE [Status_Cust] LIKE 'A%' LIMIT 1",
            f"probe_{col_w1}"
        )
        if rows:                                  # got a row → column exists
            cols = [
                f"{(candidate + timedelta(weeks=n)).strftime('%m_%d')}_W{n + 1}"
                for n in range(26)
            ]
            return cols

    print("  [WARN] Column probe exhausted — using computed fallback")
    return _make_prj_cols()


# Populated at runtime in main() via _discover_prj_cols()
ORIG_PRJ_COLS = _make_prj_cols()


def _compute_event_boosts():
    """
    Compute per-week Amazon event boost multipliers for the current 26-week
    forecast window based on actual calendar dates.

    Prime Day consumer event = last Tuesday of June (end of June).
    ORDERING bumps land in May (DC pre-buy, 4-8 weeks before consumer event):
      May 1 (x1.25), May 15 (x1.25), May 29 (x1.50).
    IMPORTANT: no ordering bump is placed in July.  Any July spike in the
    F_AMZ_RPL output is NOT Prime Day -- it is a variability-pattern artifact
    that must be diagnosed and fixed at the source (EC variant ATS inheritance,
    post-OOS catch-up normalization, etc.).

    Fall Prime Day (first Tuesday of October): single ordering bump =
      Tuesday after Labor Day (first Monday of September + 1 day) at x1.30.

    Returns:
        prime_boosts  dict {1-indexed week: multiplier}  -- Prime Day bumps
        fall_boosts   dict {1-indexed week: multiplier}  -- Fall Prime Day bump
    """
    from datetime import date, timedelta
    if not ORIG_PRJ_COLS:
        return {}, {}
    col = ORIG_PRJ_COLS[0]   # e.g. "05_26_W1"
    m, d = int(col[0:2]), int(col[3:5])
    today = date.today()
    prj_start = date(today.year, m, d)
    if (prj_start - today).days < -180:
        prj_start = date(today.year + 1, m, d)

    prime_boosts = {}
    for bump_month, bump_day, mult in PRIME_DAY_BUMPS:
        for yr_off in (0, 1):
            try:
                bump = date(prj_start.year + yr_off, bump_month, bump_day)
            except ValueError:
                continue
            delta = (bump - prj_start).days
            if 0 <= delta < 26 * 7:
                wk = delta // 7 + 1   # 1-indexed
                # Two bumps can land in the same week -- take the larger
                prime_boosts[wk] = max(prime_boosts.get(wk, 1.0), mult)
                break

    fall_boosts = {}
    for yr_off in (0, 1):
        yr = prj_start.year + yr_off
        sep1 = date(yr, 9, 1)
        # First Monday of September (Labor Day)
        labor_day = sep1 + timedelta(days=(0 - sep1.weekday()) % 7)
        fall_bump = labor_day + timedelta(days=1)               # Tuesday after Labor Day
        delta = (fall_bump - prj_start).days
        if 0 <= delta < 26 * 7:
            wk = delta // 7 + 1
            fall_boosts[wk] = max(fall_boosts.get(wk, 1.0), FALL_PRIME_DAY_LIFT)
            break

    return prime_boosts, fall_boosts


_EVENT_BOOSTS_CACHE = None   # (prime_boosts, fall_boosts) -- populated on first use


def _get_event_boosts():
    """Return cached (prime_day_boosts, fall_prime_day_boosts) for current window.
    Cache is invalidated when ORIG_PRJ_COLS changes (see main())."""
    global _EVENT_BOOSTS_CACHE
    if _EVENT_BOOSTS_CACHE is None:
        _EVENT_BOOSTS_CACHE = _compute_event_boosts()
    return _EVENT_BOOSTS_CACHE


_T5_SEASONAL_BOOSTS_CACHE = {}   # season_key -> {1-indexed week: multiplier}


def _compute_t5_seasonal_boosts(season_key):
    """
    Map AMZ_T5_HOLIDAY_BOOSTS calendar dates to 1-indexed projection weeks for
    the given Season tag.

    Lookup priority:
      1. Exact match on season_key in AMZ_T5_HOLIDAY_BOOSTS
      2. Fall back to standard ("") if season_key is not a recognised key
         (unknown / new Season tags get the standard T5 ramp as a safe default).

    Returns dict {1-indexed week: multiplier}.  Empty dict = no boosts.
    """
    from datetime import date, timedelta
    if not ORIG_PRJ_COLS:
        return {}
    col = ORIG_PRJ_COLS[0]           # e.g. "05_17_W1"
    m, d = int(col[0:2]), int(col[3:5])
    today = date.today()
    prj_start = date(today.year, m, d)
    if (prj_start - today).days < -180:
        prj_start = date(today.year + 1, m, d)

    if season_key in AMZ_T5_HOLIDAY_BOOSTS:
        bumps = AMZ_T5_HOLIDAY_BOOSTS[season_key]
    else:
        # Unknown season tag: use standard ramp as safe default
        bumps = AMZ_T5_HOLIDAY_BOOSTS.get("", [])

    boosts = {}
    for bump_month, bump_day, mult in bumps:
        for yr_off in (0, 1):
            try:
                bump = date(prj_start.year + yr_off, bump_month, bump_day)
            except ValueError:
                continue
            delta = (bump - prj_start).days
            if 0 <= delta < 26 * 7:
                wk = delta // 7 + 1           # 1-indexed
                boosts[wk] = max(boosts.get(wk, 1.0), mult)
                break
    return boosts


def _get_t5_seasonal_boosts(season):
    """Return cached T5/Holiday boost dict for the given Season tag.
    Cache is per season_key; invalidated when ORIG_PRJ_COLS changes (main())."""
    global _T5_SEASONAL_BOOSTS_CACHE
    season_key = (season or "").strip()
    if season_key not in _T5_SEASONAL_BOOSTS_CACHE:
        _T5_SEASONAL_BOOSTS_CACHE[season_key] = _compute_t5_seasonal_boosts(season_key)
    return _T5_SEASONAL_BOOSTS_CACHE[season_key]


# ─── CData helpers ────────────────────────────────────────────────────────────

def _cdata_auth():
    return "Basic " + base64.b64encode(f"{CDATA_EMAIL}:{CDATA_PAT}".encode()).decode()


def _mcp_call(method, params, timeout=90):
    # 90s hard ceiling per call.  A hung CData session that never closes the
    # socket would otherwise block for the full 300s default — with 5 retries
    # and backoff that adds up to 26+ minutes of silent hang (observed
    # 2026-05-13).  90s is plenty for any real query; connection problems
    # surface fast and cdata_query()'s retry loop handles them.
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(CDATA_MCP_URL, data=payload, method="POST")
    req.add_header("Authorization", _cdata_auth())
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read().decode("utf-8")
    for line in body.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise ValueError(f"No data line in MCP response: {body[:300]}")


def _parse_cdata_result(text):
    data = json.loads(text)
    result_set = data.get("results", [data])[0]
    schema = result_set.get("schema", [])
    col_names = [c["columnName"] for c in schema]
    rows = result_set.get("rows", [])
    return [{col_names[i]: row[i] for i in range(len(col_names))} for row in rows]


_CDATA_PRIMED = False

def _prime_cdata():
    """CData enforces a session prerequisite: queryData calls return
    IncompleteRead(0 bytes) until getInstructions has been called once.
    Prime the session lazily on first use."""
    global _CDATA_PRIMED
    if _CDATA_PRIMED:
        return
    print("  [CData] priming session (getInstructions) ...", flush=True)
    for attempt in range(1, 4):   # up to 3 attempts, 90s each
        try:
            _mcp_call("tools/call", {"name": "getInstructions",
                                      "arguments": {"driverName": "Quickbase1"}})
            _CDATA_PRIMED = True
            print("  [CData] session ready.", flush=True)
            return
        except Exception as e:
            if attempt == 3:
                # Non-fatal — queryData may still work; log and continue.
                print(f"  [warn] CData prime failed after 3 attempts: {e} "
                      f"— continuing anyway.", flush=True)
                _CDATA_PRIMED = True
                return
            delay = 4 * attempt
            print(f"  [warn] CData prime attempt {attempt}/3 failed: {str(e)[:100]} "
                  f"— retrying in {delay}s ...", flush=True)
            time.sleep(delay)


def cdata_query(sql, description="query"):
    _prime_cdata()
    global _CDATA_PRIMED
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = _mcp_call("tools/call", {"name": "queryData", "arguments": {"query": sql}})
            if result.get("error"):
                raise ValueError(result["error"])
            content = result.get("result", {}).get("content", [])
            text = "".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
            return _parse_cdata_result(text)
        except Exception as e:
            err_str = str(e)
            if attempt == MAX_RETRIES:
                print(f"  [FAIL] CData query failed ({description}) after "
                      f"{MAX_RETRIES} attempts: {e}", flush=True)
                return []
            # On IncompleteRead or timeout the server has likely dropped our
            # session — re-prime before the next retry so the session warm-up
            # runs again, otherwise we just retry into the same dead socket.
            need_reprime = "IncompleteRead" in err_str or "timed out" in err_str
            if need_reprime:
                _CDATA_PRIMED = False
            delay = 2 ** attempt  # 2, 4, 8, 16 s
            print(f"  [retry {attempt}/{MAX_RETRIES-1}] CData {description}: "
                  f"{err_str[:120]} — {'re-priming + ' if need_reprime else ''}"
                  f"retrying in {delay}s ...", flush=True)
            time.sleep(delay)
    return []


def clean_html(val):
    if isinstance(val, str):
        return re.sub(r"<[^>]+>", "", val).strip()
    return val


def _coerce_user_name(v):
    """Normalize a Quickbase User-type field value to a human-readable name.

    QB User-type fields come back as either:
      - a {"email", "id", "name"} dict (REST + sometimes CData)
      - a plain string with the name or email (CData ODBC)
      - None / empty
    Always prefer the name; fall back to email if name is missing so a
    populated cell never reads as blank.
    """
    if v is None:
        return ""
    if isinstance(v, dict):
        return (v.get("name") or v.get("email") or "").strip()
    if isinstance(v, str):
        return v.strip()
    return str(v).strip() if v else ""


def cdata_update(sql, key, retries=MAX_RETRIES):
    for attempt in range(1, retries + 1):
        try:
            result = _mcp_call("tools/call", {"name": "queryData", "arguments": {"query": sql}}, timeout=60)
            if result.get("error"):
                raise ValueError(result["error"])
            return True
        except Exception as e:
            if attempt == retries:
                print(f"\n  [FAIL] {key}: {e}", flush=True); return False
            time.sleep(2 * attempt)
    return False


# ─── Direct Quickbase REST API (bypasses CData for bulk write-back) ─────────
# Used when --bulk-writeback is on (default for --all scope).  Uses QB's native
# /v1/records endpoint to upsert hundreds of records in one HTTP call instead
# of N separate API_EditRecord calls through CData.  ~50× fewer hits on QB
# rate limits than the per-record SQL UPDATE path.

_QB_FIELD_MAP_CACHE = {}   # table_id -> {field_label: fid}

def _qb_request(method, path, body=None, timeout=60):
    url = f"https://api.quickbase.com/v1{path}"
    headers = {
        "QB-Realm-Hostname": QB_REALM,
        "Authorization":     f"QB-USER-TOKEN {QB_USER_TOKEN}",
        "Content-Type":      "application/json",
        "User-Agent":        "petspeople-inventory-forecaster/1.0",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _fetch_retailer_pos(rows):
    """
    Phase 2.6c — Fetch POS + OH data from Retailer Sales (bv2izcn5b) for
    non-Amazon projection records.

    Queries the table via QB REST API (known field IDs, no CData schema needed).
    Each week appears exactly twice in the source table; rows are deduplicated
    by date within each (acct, mstyle) group.

    Returns dict keyed by "ACCT-MSTYLE" (matching Acct_MStyle_Key_) ->
    {
        Avg_Units_Wk_L4w:   float  -- 4-week avg POS units/wk
        Avg_Units_Wk_L13w:  float  -- 13-week avg POS units/wk
        Avg_Units_Wk_L26w:  float  -- 26-week avg POS units/wk
        Avg_Units_Wk_L52w:  float  -- 52-week avg POS units/wk
        OH_Units_LW:        float  -- latest-week OH units at retailer
        Instock_LW:         float  -- latest-week instock % (fraction 0-1)
        OH_WOS:             float  -- OH_Units_LW / L4W avg (0 when no POS data)
    }
    """
    RTL_TID   = "bv2izcn5b"
    F_DATE    = 6
    F_MSTYLE  = 9
    F_POS_U   = 10
    F_OH_U    = 12
    F_INSTOCK = 15
    F_ACCT    = 17
    AMZ_ACCT  = "1864"          # Acct # for Amazon -- excluded

    # Collect non-Amazon mstyles from the projection rows
    non_amz_mstyles = set()
    for row in rows:
        cust = (row.get("Customr_Name") or "").upper()
        if AMAZON_CUST_SUBSTR in cust:
            continue
        ms = row.get("Mstyle", "")
        if ms:
            non_amz_mstyles.add(ms)

    if not non_amz_mstyles:
        return {}

    # Date cutoff: 56 weeks ago (enough for 52W avg + some buffer)
    cutoff = (date.today() - timedelta(weeks=56)).isoformat()

    # Batch by mstyle (25 per batch keeps WHERE clause manageable)
    BATCH   = 25
    ms_list = sorted(non_amz_mstyles)
    raw_rows = []

    for i in range(0, len(ms_list), BATCH):
        batch = ms_list[i : i + BATCH]
        # Build OR'd mstyle filter
        ms_filter = "OR".join(
            "{" + str(F_MSTYLE) + ".EX.'" + ms.replace("'", "''") + "'}"
            for ms in batch
        )
        where = (f"({ms_filter})"
                 f"AND{{{F_ACCT}.XCT.'{AMZ_ACCT}'}}"
                 f"AND{{{F_DATE}.AF.'{cutoff}'}}")
        skip = 0
        while True:
            try:
                resp = _qb_request("POST", "/records/query", {
                    "from":    RTL_TID,
                    "select":  [F_DATE, F_MSTYLE, F_POS_U, F_OH_U, F_INSTOCK, F_ACCT],
                    "where":   where,
                    "sortBy":  [{"fieldId": F_DATE, "order": "DESC"}],
                    "options": {"top": 1000, "skip": skip},
                }, timeout=90)
            except Exception as _e:
                print(f"      [WARN] retailer_pos batch {i // BATCH + 1} "
                      f"skip={skip} failed: {_e}", flush=True)
                break
            batch_data = resp.get("data", [])
            raw_rows.extend(batch_data)
            total = resp.get("metadata", {}).get("totalRecords", 0)
            if len(batch_data) < 1000 or (total > 0 and len(raw_rows) >= total):
                break
            skip += 1000
            time.sleep(0.15)

    if not raw_rows:
        return {}

    # Parse: group by (acct_str, mstyle_str), dedup by date
    from collections import defaultdict
    grouped = defaultdict(dict)   # (acct, mstyle) -> {date: {pos_u, oh_u, instock}}

    def _sv(row, fid):
        v = (row.get(str(fid)) or {}).get("value")
        return v

    for row in raw_rows:
        ms_v    = str(_sv(row, F_MSTYLE) or "").strip()
        acct_v  = str(_sv(row, F_ACCT)   or "").strip()
        date_v  = str(_sv(row, F_DATE)   or "")[:10]   # YYYY-MM-DD
        if not ms_v or not acct_v or not date_v:
            continue
        key = (acct_v, ms_v)
        if date_v not in grouped[key]:                  # deduplicate by date
            grouped[key][date_v] = {
                "pos_u":   float(_sv(row, F_POS_U)   or 0) or 0,
                "oh_u":    float(_sv(row, F_OH_U)    or 0) or 0,
                "instock": float(_sv(row, F_INSTOCK) or 0) or 0,
            }

    # Compute per-acct-mstyle metrics
    result = {}
    for (acct_str, ms_str), date_dict in grouped.items():
        sorted_dates = sorted(date_dict.keys(), reverse=True)
        if not sorted_dates:
            continue

        def _avg_pos(n):
            wks = sorted_dates[:n]
            if not wks:
                return 0.0
            return sum(date_dict[d]["pos_u"] for d in wks) / len(wks)

        lw_data  = date_dict[sorted_dates[0]]
        oh_lw    = lw_data["oh_u"]
        inst_lw  = lw_data["instock"]
        l4w      = _avg_pos(4)
        l13w     = _avg_pos(13)
        l26w     = _avg_pos(26)
        l52w     = _avg_pos(52)
        oh_wos   = oh_lw / max(l4w, 0.1) if l4w > 0 else 0.0

        am_key = f"{acct_str}-{ms_str}"
        result[am_key] = {
            "Avg_Units_Wk_L4w":  l4w,
            "Avg_Units_Wk_L13w": l13w,
            "Avg_Units_Wk_L26w": l26w,
            "Avg_Units_Wk_L52w": l52w,
            "OH_Units_LW":       oh_lw,
            "Instock_LW":        inst_lw,
            "OH_WOS":            oh_wos,
        }

    return result


def qb_get_field_map(table_id, force_refresh=False):
    """Returns {field_label: field_id} for a Quickbase table.  Cached."""
    if not force_refresh and table_id in _QB_FIELD_MAP_CACHE:
        return _QB_FIELD_MAP_CACHE[table_id]
    try:
        fields = _qb_request("GET", f"/fields?tableId={table_id}")
    except Exception as e:
        print(f"  [QB-REST] field map fetch failed for {table_id}: {e}", flush=True)
        return {}
    fmap = {f["label"]: f["id"] for f in fields if "label" in f and "id" in f}
    _QB_FIELD_MAP_CACHE[table_id] = fmap
    return fmap


def qb_run_report(report_id, table_id, top=10000, max_rows=200000):
    """Execute a saved QB report and return all rows as list of dicts keyed by
    field LABEL (not fid).  Paginates if needed.

    Defensive against QB report endpoints that ignore `skip` and re-return
    the same rows: stops pagination as soon as `metadata.totalRecords` is
    reached, regardless of how the server filled subsequent pages.

    Returns: list[dict[str, Any]] where each dict is {field_label: value}.
    """
    rows = []
    skip = 0
    total_records = None
    while skip < max_rows:
        body = {"options": {"top": top, "skip": skip}}
        resp = _qb_request(
            "POST",
            f"/reports/{report_id}/run?tableId={table_id}",
            body=body,
            timeout=120,
        )
        # Build fid -> label map for THIS response
        fid_to_label = {f["id"]: f["label"] for f in resp.get("fields", [])}
        chunk = resp.get("data", [])
        meta = resp.get("metadata", {}) or {}
        if total_records is None:
            total_records = meta.get("totalRecords")
        for r in chunk:
            row = {}
            for fid_str, cell in r.items():
                try:
                    fid = int(fid_str)
                except ValueError:
                    continue
                label = fid_to_label.get(fid)
                if label:
                    row[label] = cell.get("value")
            rows.append(row)
        # Stop if we've collected the full report (some QB report endpoints
        # ignore `skip` and return all rows on every page — without this guard
        # we'd accumulate duplicates).
        if total_records is not None and len(rows) >= total_records:
            del rows[total_records:]   # trim any over-fetch
            break
        # End of pagination signal: server returned fewer rows than requested
        if len(chunk) < top:
            break
        skip += len(chunk)
    return rows


def qb_bulk_update(table_id, records, merge_field_id, batch_size=None):
    """Upsert N records via POST /records, batched.

    records: list of {fid_int: value, ...}  (already mapped to field IDs)
    merge_field_id: int — the unique-key field for upsert (e.g. Acct_MStyle_Key_'s fid)
    Returns (n_success, n_fail, errors).
    """
    batch_size = batch_size or QB_BULK_BATCH
    n_ok = 0; n_fail = 0; errors = []
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        body = {
            "to":            table_id,
            "data":          [{str(k): {"value": v} for k, v in r.items()} for r in chunk],
            "mergeFieldId":  merge_field_id,
            "fieldsToReturn": [],
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = _qb_request("POST", "/records", body=body, timeout=120)
                # QB v1 returns metadata with arrays of RIDs:
                #   createdRecordIds[], updatedRecordIds[], unchangedRecordIds[]
                #   totalNumberOfRecordsProcessed (int)
                #   lineErrors {row_index: [msg]}  (when partial failures)
                meta = resp.get("metadata", {})
                _ok = (len(meta.get("createdRecordIds", []))
                       + len(meta.get("updatedRecordIds", []))
                       + len(meta.get("unchangedRecordIds", [])))
                _processed = meta.get("totalNumberOfRecordsProcessed", _ok)
                _failed_meta = max(0, _processed - _ok)
                n_ok   += _ok
                n_fail += _failed_meta + max(0, len(chunk) - _processed)
                if meta.get("lineErrors"):
                    errors.append({"batch_start": i, "lineErrors": meta["lineErrors"]})
                break
            except Exception as e:
                if attempt == MAX_RETRIES:
                    n_fail += len(chunk)
                    errors.append({"batch_start": i, "error": str(e)})
                    print(f"  [QB-REST] batch {i}-{i+len(chunk)} failed after "
                          f"{MAX_RETRIES} attempts: {e}", flush=True)
                    break
                time.sleep(2 ** attempt)
    return n_ok, n_fail, errors


# ─── SQL builders ─────────────────────────────────────────────────────────────

def build_prj_select(prj_cols):
    """Build the projection SELECT using dynamically computed weekly column names."""
    prj_col_sql  = ",".join(f"[{c}]" for c in prj_cols)
    shp_col_sql  = ",".join(f"[{c}]" for c in SHP_COLS)
    ord_col_sql  = ",".join(f"[{c}]" for c in ORD_COLS)   # full 52w — demand signal + viewer display
    sugg_col_sql = ",".join(f"[{c}]" for c in SUGG_COLS)
    inv_col_sql  = ",".join(f"[{c}]" for c in INV_OH_COLS)  # F37 anticipated OH per week
    opn_col_sql  = ",".join(f"[{c}]" for c in OPN_COLS)     # open customer PO qtys W1..W26
    return textwrap.dedent(f"""
        SELECT
          [Acct_MStyle_Key_], [Mstyle], [Customr_Name], [Description], [Status_Cust],
          [PT_Item_Status], [Div],
          [Shpd_Wk_L13W_cust_], [Last_Ord_Date], [Last_Shp_Date], [Inventory_Manager],
          [Flagged], [Auto_Project], [POG_Launch_Date], [POG_End_Date], [Store_Count],
          [AI_PRJ_W1],[AI_PRJ_W2],[AI_PRJ_W3],[AI_PRJ_W4],[AI_PRJ_W5],
          [AI_PRJ_W6],[AI_PRJ_W7],[AI_PRJ_W8],[AI_PRJ_W9],[AI_PRJ_W10],
          [AI_PRJ_W11],[AI_PRJ_W12],[AI_PRJ_W13],[AI_PRJ_W14],[AI_PRJ_W15],
          [AI_PRJ_W16],[AI_PRJ_W17],[AI_PRJ_W18],[AI_PRJ_W19],[AI_PRJ_W20],
          [AI_PRJ_W21],[AI_PRJ_W22],[AI_PRJ_W23],[AI_PRJ_W24],[AI_PRJ_W25],[AI_PRJ_W26],
          {prj_col_sql},
          {shp_col_sql},
          {ord_col_sql},
          {sugg_col_sql},
          {inv_col_sql},
          {opn_col_sql}
        FROM [Quickbase1].[InventoryTrack].[Projections]
        WHERE ([Status_Cust] LIKE 'A%' OR [Status_Cust] LIKE 'FD%')
    """).strip()


def build_scope_filter(args):
    clauses = []
    if args.acct:
        acct_list = [a.strip() for a in args.acct.split(',') if a.strip()]
        if len(acct_list) == 1:
            clauses.append(f"[Acct_MStyle_Key_] LIKE '{acct_list[0]}-%'")
        else:
            or_parts = " OR ".join(f"[Acct_MStyle_Key_] LIKE '{a}-%'" for a in acct_list)
            clauses.append(f"({or_parts})")
    if args.customer:
        clauses.append(f"[Customr_Name] LIKE '%{args.customer}%'")
    if args.mstyle:
        mstyle_list = [m.strip() for m in args.mstyle.split(',') if m.strip()]
        if len(mstyle_list) == 1:
            clauses.append(f"[Mstyle] = '{mstyle_list[0]}'")
        else:
            quoted = ",".join(f"'{m}'" for m in mstyle_list)
            clauses.append(f"[Mstyle] IN ({quoted})")
    if hasattr(args, '_brand_mstyles') and args._brand_mstyles:
        quoted = ",".join(f"'{m}'" for m in args._brand_mstyles)
        clauses.append(f"[Mstyle] IN ({quoted})")
    if hasattr(args, 'keys') and args.keys:
        key_list = [k.strip() for k in args.keys.split(',') if k.strip()]
        quoted = ",".join(f"'{k}'" for k in key_list)
        clauses.append(f"[Acct_MStyle_Key_] IN ({quoted})")
    return " AND ".join(clauses) if clauses else None


def build_update_sql(key, forecast, alert):
    k = key.replace("'", "''")
    a = alert.replace("'", "''")
    sets = ", ".join(f"[AI_PRJ_W{w}] = {forecast[w-1]}" for w in range(1, 27))
    sets += f", [AI_ALERT] = '{a}'"
    return (
        f"UPDATE [Quickbase1].[InventoryTrack].[Projections] "
        f"SET {sets} WHERE [Acct_MStyle_Key_] = '{k}'"
    )


def build_validation_update_sql(rec):
    """Build UPDATE SQL to push validation results back to QB Projections table.

    Writes to the 10 Validation_* fields created for the validation review page.
    Leaves Validation_Flag and Validation_Comments untouched — those are user-owned.
    """
    k = rec["key"].replace("'", "''")
    narr = (rec.get("narrative") or "").replace("'", "''")
    model = (rec.get("ai_model") or "").replace("'", "''")
    pri   = (rec.get("priority") or "LOW").replace("'", "''")
    pat   = (rec.get("pattern") or "").replace("'", "''")
    sev   = (rec.get("max_severity") or "OK").replace("'", "''")
    nfl   = int(rec.get("n_flags") or 0)
    bw    = 1 if rec.get("biweekly") else 0
    projwk = float(rec.get("proj_per_wk") or 0)

    sets = (
        f"[Validation_Priority]     = '{pri}', "
        f"[Validation_Pattern]      = '{pat}', "
        f"[Validation_Max_Severity] = '{sev}', "
        f"[Validation_N_Flags]      = {nfl}, "
        f"[Validation_Biweekly]     = {bw}, "
        f"[Validation_Narrative]    = '{narr}', "
        f"[Validation_AI_Model]     = '{model}', "
        f"[Validation_Proj_Per_Wk]  = {projwk}"
    )
    return (
        f"UPDATE [Quickbase1].[InventoryTrack].[Projections] "
        f"SET {sets} WHERE [Acct_MStyle_Key_] = '{k}'"
    )


# ─── Forecasting helpers ──────────────────────────────────────────────────────

def snap(qty, mp):
    """Round qty to nearest master-pack multiple (0 is always valid)."""
    if qty <= 0 or mp <= 1:
        return max(0, round(qty))
    return int(round(qty / mp) * mp)


def smooth_forecast(fcst, weight=0.3):
    """
    Light 3-week weighted moving average to dampen week-to-week spikes.
    Blends each week with its neighbors while preserving the 26-week total.
    weight = how much of the original value to keep (0.3 = 30% neighbors, 70% original).
    Zeros are left as zeros (don't smooth into bi-weekly gaps).
    """
    if not fcst or all(v == 0 for v in fcst):
        return fcst
    n = len(fcst)
    orig_total = sum(fcst)
    smoothed = list(fcst)
    for i in range(n):
        if fcst[i] == 0:
            continue
        prev_val = fcst[i - 1] if i > 0 and fcst[i - 1] > 0 else fcst[i]
        next_val = fcst[i + 1] if i < n - 1 and fcst[i + 1] > 0 else fcst[i]
        smoothed[i] = fcst[i] * (1 - weight) + (prev_val + next_val) / 2 * weight
    # Rescale to preserve original total
    new_total = sum(smoothed)
    if new_total > 0:
        scale = orig_total / new_total
        smoothed = [v * scale for v in smoothed]
    return smoothed


def amazon_pos_rate(pos):
    """
    Compute a trend-adjusted weekly POS (point-of-sale) rate from Amazon Catalog data.

    Inputs (all in units/week):
      Ordered_Units_LW   — consumer-to-Amazon orders last week
      Avg_Units_Wk_L4w   — 4-week avg consumer demand
      Avg_Units_Wk_L13w  — 13-week avg consumer demand
      Avg_Units_Wk_L26w  — 26-week avg consumer demand
      Avg_Units_Wk_L52w  — 52-week avg consumer demand

    Returns: (pos_rate, trend_label, trend_ratio)
      pos_rate    — weighted blend of the window averages, biased toward recency
                    when accelerating, toward longer windows when decelerating
      trend_label — "accelerating" | "decelerating" | "stable" | "no_data"
      trend_ratio — L4W / L13W ratio (>1.15 = accel, <0.85 = decel)
    """
    l4  = float(pos.get("Avg_Units_Wk_L4w")  or 0)
    l13 = float(pos.get("Avg_Units_Wk_L13w") or 0)
    l26 = float(pos.get("Avg_Units_Wk_L26w") or 0)
    l52 = float(pos.get("Avg_Units_Wk_L52w") or 0)

    if l13 == 0 and l4 == 0:
        return 0.0, "no_data", 1.0

    # 2026-05-08 — L13W-anomaly recovery (25 records cohort-wide).  The Amazon
    # Catalog source occasionally has Avg_Units_Wk_L13w = 0 while L4w and L26w
    # are healthy — a clean refresh/import bug at the source, not a real
    # consumer signal.  Detect and interpolate L13W from L4W/L26W so the
    # blend math doesn't put 45% weight on a phantom zero (the "stable"
    # branch dragged FF9297/24's pos_rate to ~1,073/wk vs the truth of
    # ~1,800/wk).  Pattern: L13W=0 AND L4W>0 AND L26W>0 → substitute the
    # arithmetic mean as the most defensible reconstruction.
    if l13 == 0 and l4 > 0 and l26 > 0:
        l13 = (l4 + l26) / 2.0

    base        = l13 if l13 > 0 else l4
    trend_ratio = (l4 / base) if base > 0 else 1.0

    if trend_ratio >= 1.15:           # accelerating — weight recent more heavily
        pos_rate = l4*0.55 + l13*0.30 + l26*0.15
        trend    = "accelerating"
    elif trend_ratio <= 0.85:         # decelerating — don't fully trust the dip
        pos_rate = l4*0.35 + l13*0.45 + l26*0.20
        trend    = "decelerating"
    else:                             # stable — spread across all windows
        pos_rate = l4*0.25 + l13*0.45 + l26*0.20 + l52*0.10
        trend    = "stable"

    return round(pos_rate, 1), trend, round(trend_ratio, 2)


def seasonal_baseline(history, mp, is_amazon=False, pos_data=None, description=None,
                      product_category=None, product_subcategory=None,
                      brand=None, brand_pt=None, shpd_l13=0.0, season=None,
                      is_ecom=False, is_new_launch=False, amz_catalog=None):
    """
    Forecasting model for dense CPG replenishment (orders most weeks):

    Baseline — L13W non-zero average (per-order qty, ignores zero/quiet weeks).
    For very data-sparse L13W (< 4 active weeks), falls back to L26W non-zero avg.
    This correctly handles post-event-buy drawdown: after a Prime Day or holiday
    pre-buy the item goes quiet, so the all-weeks avg understates the true run rate.

    Amazon POS blend — when pos_data is supplied for Amazon items, the order-history
    baseline is blended 55/45 with the trend-adjusted consumer POS rate from the
    Amazon Catalog.  This pulls the baseline toward actual consumer demand velocity,
    adjusting for acceleration/deceleration detected from L4W vs L13W trends.

    Shape -- damped seasonal profile.  Base DAMP=0.30 (30% historical / 70%
    flat), relief DAMP=0.85 when F16 detects strong-signal Halloween/Easter/
    July4 patterns the planner doesn't want flattened.  The position-based
    profile maps "26 weeks ago" to W1, so any large historical event buy
    (holiday, prior Prime Day) that happened to fall 26-21 weeks ago would
    inflate W1-W5 regardless of the actual forecast season.  Damping
    collapses those distortions while still preserving genuine slopes.
    (Docstring updated 2026-05-21 -- previously documented DAMP=0.1 which
    was the original value before F16 was added.)

    Events — explicit Prime Day (Amazon only, W7-W9) and Fall Deal (W23-W25) lifts
    applied AFTER the damped profile, so the event weeks always stand out above the
    background even when the position-based profile is flat in those positions.

    Smoothing — light 3-week weighted average (preserves total).
    Snap to master pack.
    """
    l13 = history[-13:]
    l13_avg = sum(l13) / 13

    if l13_avg == 0:
        return [0] * 26, 0, {"model": "seasonal_baseline", "l13_avg": 0}

    # Order-history baseline = L13W non-zero avg (per-order qty, excludes drawdown zeros).
    # Falls back to L26W non-zero avg when L13W has very few active weeks.
    l13_nz  = [v for v in l13 if v > 0]
    l26_nz  = [v for v in history[-26:] if v > 0]

    # Fix 3 — Outlier cap: if a single stocking spike dominates L13W non-zero values
    # (max > 3.0× median), cap it to 3.0× median before averaging. This prevents
    # one large replenishment order from inflating the per-order baseline.
    # (F12 reverted 2026-04-21 -- see CHANGELOG.md)
    #
    # F25 (2026-04-26) — Extreme-outlier DROP:  when a single value is >5× the
    # median AND there are ≥ 4 other non-zero weeks to lean on, drop it entirely
    # rather than capping. Capping a 744-unit lone order to 3×median=27 still
    # massively biases a 13-week mean drawn from otherwise-quiet weeks.  The
    # ≥ 4 supporting non-zero weeks gate ensures we don't drop the only signal.
    if len(l13_nz) >= 5:
        _sorted_nz = sorted(l13_nz)
        _median_nz = _sorted_nz[len(_sorted_nz) // 2]
        if _median_nz > 0 and max(l13_nz) > 5.0 * _median_nz:
            _max_nz = max(l13_nz)
            l13_nz = [v for v in l13_nz if v < _max_nz]   # drop the single outlier
    if len(l13_nz) >= 3:
        _sorted_nz = sorted(l13_nz)
        _median_nz = _sorted_nz[len(_sorted_nz) // 2]
        # Fix B (2026-05-24): single-occurrence check — if the max value
        # appears exactly once AND max > 2x mean of remaining values, cap
        # at 2x mean rather than 2.5x median.  One giant order should not
        # inflate the baseline even after a 2.5x cap.
        _l13_nz_mean = sum(l13_nz) / len(l13_nz)
        _l13_nz_max  = max(l13_nz)
        if (l13_nz.count(_l13_nz_max) == 1
                and _l13_nz_mean > 0
                and _l13_nz_max > 2.0 * _l13_nz_mean):
            _single_cap = 2.0 * _l13_nz_mean
            l13_nz = [min(v, _single_cap) for v in l13_nz]
        # F38-pre (2026-05-20): tighten spike cap to 2.0x when Amazon buy-box
        # price is below MAP — buy-box event drove a temporary order spike that
        # should not anchor the 26-week baseline.  3.0x applies otherwise.
        _f38pre_aur = float((amz_catalog or {}).get("AUR_L4w") or 0)
        _f38pre_map = float((amz_catalog or {}).get("MAP_Price") or 0)
        _bb_event   = (is_amazon and _f38pre_aur > 0 and _f38pre_map > 0
                       and _f38pre_aur < _f38pre_map)
        _spike_cap = (2.0 if _bb_event else 2.5) * _median_nz
        if max(l13_nz) > _spike_cap:
            l13_nz = [min(v, _spike_cap) for v in l13_nz]
    if len(l26_nz) >= 5:
        _sorted_l26 = sorted(l26_nz)
        _median_l26 = _sorted_l26[len(_sorted_l26) // 2]
        if _median_l26 > 0 and max(l26_nz) > 5.0 * _median_l26:
            _max_l26 = max(l26_nz)
            l26_nz = [v for v in l26_nz if v < _max_l26]
    if len(l26_nz) >= 3:
        _sorted_l26 = sorted(l26_nz)
        _median_l26 = _sorted_l26[len(_sorted_l26) // 2]
        _spike_cap26 = 2.5 * _median_l26
        if max(l26_nz) > _spike_cap26:
            l26_nz = [min(v, _spike_cap26) for v in l26_nz]

    # VP-Q1 (2026-04-28) — Evidence-based zero-week handling.
    #
    # PRIOR behavior:  always used L13 nz-avg when ≥ 4 active weeks, falling
    # back to L13 all-weeks avg only when ≥ 4 zero weeks (the F-A pulse rule).
    # This systematically inflated the baseline for steady customers who have
    # 1-3 legitimate light weeks (no OOS, no event drawdown — just normal
    # variance).  Multiplying nz-avg by the seasonal profile then compounded
    # the over-projection.
    #
    # NEW behavior:  default to L13 all-weeks avg.  Only switch to L13 nz-avg
    # when we can identify a real reason the zeros aren't demand signal:
    #   (A) Fulfillment gap (OOS proxy):  Shp_L13W << Ord_L13W.
    #       If shipments under-ran orders by ≥ 15% over a meaningful order
    #       volume (≥ 50 units total), the zeros likely reflect stock issues
    #       rather than real soft demand.
    #   (C) Pulsed ordering pattern (F-A retained):  ≥ 4 zero weeks in L13W
    #       indicates the account orders in chunks (Amazon pre-buys, promo-
    #       driven retailers).  Use all-weeks avg here too — nz-avg would be
    #       order size, not weekly rate.
    #
    # VP-Q5 (2026-05-07) — Removed signal B (post-event drawdown → nz-avg).
    # Per VP feedback: post-event lulls are REAL demand reality, not artifacts
    # to suppress.  The seasonal profile already captures the lull shape, so
    # excluding lull weeks from the baseline + multiplying by the seasonal
    # multiplier was double-counting and inflating projections.  Now we let
    # post-event drawdown weeks stay in the all-weeks avg; the seasonal
    # profile handles the lull positioning.
    #
    # In all other cases the zeros are real demand signal and the all-weeks
    # avg is the correct baseline.
    l13_zero_count = 13 - len(l13_nz)
    _fa_applied    = False
    _baseline_mode = ""

    # Signal A — fulfillment gap (OOS proxy).
    #
    # BUG-FIX (2026-05-07): `shpd_l13` from `Shpd_Wk_L13W_cust_` is a PER-WEEK
    # AVERAGE, not an L13 total.  Previously this code compared an avg against
    # a total, off by ~13×, which false-positived OOS for nearly every active
    # record (e.g. BB13437 reported 9% fill-rate when true was 120% catch-up).
    # The cascade then forced L13 nz-avg baseline + enabled F13 drawdown lift +
    # F38 trend lift, inflating cap_base by 50-100%.
    #
    # Fix: compare per-week-avg-shipped vs per-week-avg-ordered (apples-to-
    # apples).  Require meaningful order volume so a one-week noise blip
    # doesn't trigger.  Also raise the OOS threshold from 0.85 → 0.70 to be
    # more conservative — true fulfillment gaps are clearly under 70%.
    _ord_total_l13 = sum(l13)
    _ord_avg_l13   = (_ord_total_l13 / 13.0) if _ord_total_l13 else 0.0
    _shp_avg_l13   = float(shpd_l13 or 0)        # per-week avg (Shpd_Wk_L13W_cust_)
    _fill_rate     = (_shp_avg_l13 / _ord_avg_l13) if _ord_avg_l13 > 0 else 1.0
    _has_oos       = (_ord_total_l13 >= 50 and _fill_rate < 0.70)

    # Signal B (post-event drawdown) was removed in VP-Q5 (2026-05-07).
    # Lulls are real demand reality and stay in the all-weeks average; the
    # seasonal profile handles the lull-week positioning in the forecast.

    if len(l13_nz) >= 4:
        if _has_oos:
            ord_baseline   = sum(l13_nz) / len(l13_nz)
            _baseline_mode = (f"L13 nz-avg (OOS: fill-rate "
                              f"{_fill_rate*100:.0f}% over {_ord_total_l13:.0f} units)")
        elif l13_zero_count >= 4:
            # F-A retained — pulsed ordering pattern
            ord_baseline   = l13_avg
            _fa_applied    = True
            _baseline_mode = (f"L13 all-weeks avg (pulsed pattern: "
                              f"{l13_zero_count}/13 zero weeks)")
        else:
            # DEFAULT — zeros are real demand signal, include them in the avg.
            ord_baseline   = l13_avg
            _baseline_mode = (f"L13 all-weeks avg (default: "
                              f"{l13_zero_count} legitimate zero weeks, "
                              f"fill-rate {_fill_rate*100:.0f}%)")
    elif l26_nz:
        ord_baseline   = sum(l26_nz) / len(l26_nz)
        _baseline_mode = "L26 nz-avg (sparse L13)"
    else:
        ord_baseline   = l13_avg
        _baseline_mode = "L13 all-weeks avg (no nz data)"

    # F4 — thin-history window widening.  When L13 has <=4 non-zero weeks AND
    # L52 has >=8, the L13 signal is statistically thin and the baseline often
    # collapses to near-zero.  Pull in the L52 non-zero avg scaled by the
    # L52 activity rate (effective weekly rate).  Take the MAX of the existing
    # baseline and this wider-window estimate so we never lower a confident
    # recent signal — we only fire when the short window is unreliable.
    _l52_nz_f4 = [v for v in history[-52:] if v > 0]
    if len(l13_nz) <= 4 and len(_l52_nz_f4) >= 8:
        _l52_nz_avg_f4 = sum(_l52_nz_f4) / len(_l52_nz_f4)
        _l52_rate_f4   = len(_l52_nz_f4) / 52.0
        _f4_effective  = _l52_nz_avg_f4 * _l52_rate_f4 * 2.0   # 2x because baseline represents per-order qty, not per-wk rate
        if _f4_effective > ord_baseline:
            ord_baseline = _f4_effective
            # annotate via a sentinel we'll surface in drivers later
            _f4_applied = True
        else:
            _f4_applied = False
    else:
        _f4_applied = False

    # F6b (renamed from F6 2026-05-21 to break tag collision with F6a in
    # classify() and F6c in forecast_record sparse branch) -- L4/L13 decay
    # dampener.  Persistent recent softening is a history-only signal that
    # recent demand is dropping off.  When the L4 non-zero avg is <= 50% of
    # L13 non-zero avg AND there are >=2 active weeks in L4 (so this isn't
    # one zero week driving the signal), scale the baseline down by 0.65x --
    # a gentle one-tier step-down.  Complements M2 EOL logic which requires
    # status-token evidence; F6b uses pure data.
    _l4_nz_f6  = [v for v in history[-4:]  if v > 0]
    _l13_nz_f6 = l13_nz
    _f6_applied = False
    # F50 -- Stockout-pattern guard (2026-05-08, planner callout).
    # F6b's hard 0.65x cut and F26's 0.85x cut both trigger when L4 nz-avg is
    # much lower than L13 nz-avg.  But that ratio doesn't distinguish
    # legitimate demand decay (item is going away) from a stockout (item is
    # temporarily unavailable, demand still exists).  When the L4 window has
    # ≥2 zero weeks AND L13 was healthy (≥10 active weeks), the most likely
    # explanation is a stockout — and stockouts mean we should expect a
    # rebound, not a 35% baseline cut.
    #
    # Empirical callout (planner-flagged 2026-05-08):
    #   1864-FF7120EC (Amazon): L13 hist had 12/13 active wks averaging
    #   1492/wk steadily for months. Last 4 wks = [168, 0, 18, 84] (3 zeros
    #   or near-zeros) -- classic stockout signature. F6b applied 0.65x to a
    #   1492 baseline, giving 970/wk, which combined with downstream
    #   smoothing/dampening collapsed the 26w forecast to ~553/wk avg vs the
    #   planner's ~1900/wk LY benchmark and ~1492/wk healthy L13 run rate.
    # F50 detection broadened (2026-05-08 part 2): use "near-zero" rather
    # than strict-zero count so that token-quantity weeks (e.g. 18u, 84u
    # against a 1500/wk normal rate) read as stockout symptoms, not real
    # demand.  Threshold: < 10% of the healthy L13 nz-avg.
    _l13_nz_avg_f50 = (sum(_l13_nz_f6) / len(_l13_nz_f6)) if _l13_nz_f6 else 0
    _f50_near_zero_thresh = 0.10 * _l13_nz_avg_f50
    _l4_zeros_f50 = sum(1 for v in history[-4:]
                        if (v or 0) <= _f50_near_zero_thresh)
    _l13_active_f50 = len(_l13_nz_f6)
    # Trigger when ≥3 of last 4 weeks are near-zero AND L13 was healthy.
    # (Bumped from ≥2 → ≥3: with the broader near-zero definition, ≥2
    # would over-fire on legitimate slow weeks.)
    _f50_stockout   = (_l4_zeros_f50 >= 3 and _l13_active_f50 >= 10)
    if _f50_stockout:
        # Skip both F6b and F26 -- let the L13 nz-avg baseline stand;
        # downstream smoothing handles the recovery pace.
        _f6_applied = "F50_stockout_skip"
    elif (len(_l4_nz_f6) >= 2 and len(_l13_nz_f6) >= 3):
        _l4_avg_f6  = sum(_l4_nz_f6)  / len(_l4_nz_f6)
        _l13_avg_f6 = sum(_l13_nz_f6) / len(_l13_nz_f6)
        if _l13_avg_f6 > 0 and _l4_avg_f6 / _l13_avg_f6 <= 0.5:
            ord_baseline *= 0.65
            _f6_applied = True
        elif _l13_avg_f6 > 0 and _l4_avg_f6 / _l13_avg_f6 <= 0.70:
            # F26 (2026-04-26) -- mild-zone decay.  Between F6b's hard 0.5x rule
            # and "no action" was a gap: items showing 50-70% of L13 in the
            # last 4 weeks are clearly cooling but escaped F6.  Apply a softer
            # 0.85× scale so we lean toward the recent rate without overreacting.
            ord_baseline *= 0.85
            _f6_applied = "mild_decline"
        elif _l13_avg_f6 > 0 and _l4_avg_f6 / _l13_avg_f6 >= 1.30 and _l4_avg_f6 / _l13_avg_f6 < 1.60:
            # F27 (2026-04-26) — mild-zone ramp.  Symmetric counterpart to F26:
            # if recent 4 weeks run 30-60% above L13 avg AND ≥2 active weeks,
            # the account is gradually accelerating.  T4 already covers this
            # for ecom; this catches non-ecom non-Amazon ramp signal that
            # would otherwise stay anchored to the older L13 avg.
            ord_baseline *= 1.10
            _f6_applied = "mild_ramp"

    # T4 — E-commerce accelerator lift (2026-04-22).  Non-Amazon e-commerce
    # retailers (Chewy, Petco.com, PetSmart.com) have no POS blend path but
    # planners see forward consumer demand that AI can't from order history
    # alone.  Observed: Chewy Seasonal Baseline -19% (−78K on 176 recs).
    # When L4 is hot vs L13, shift baseline toward L4 to capture acceleration
    # the order history alone would miss.
    _t4_l4_nz = [v for v in history[-4:]  if float(v) > 0]
    _t4_l4_avg = (sum(_t4_l4_nz) / len(_t4_l4_nz)) if _t4_l4_nz else 0
    _t4_l13_nz_avg = ord_baseline if len(l13_nz) >= 4 else 0
    _t4_applied = False
    _t4_pre = ord_baseline
    if is_ecom and _t4_l4_avg > 0 and _t4_l13_nz_avg > 0:
        _t4_ratio = _t4_l4_avg / _t4_l13_nz_avg
        _t4_l26_nz_avg = (sum(l26_nz) / len(l26_nz)) if l26_nz else _t4_l13_nz_avg
        if _t4_ratio >= 1.05:
            # Accelerating — weight L4 heavily like Amazon POS accelerating blend
            ord_baseline = (_t4_l4_avg * 0.50 + _t4_l13_nz_avg * 0.35 +
                            _t4_l26_nz_avg * 0.15)
            _t4_applied = "accelerating"
        elif _t4_ratio >= 0.80:
            # Stable — blend L13 + L4 to capture late signal
            ord_baseline = _t4_l13_nz_avg * 0.60 + _t4_l4_avg * 0.40
            _t4_applied = "stable"
        else:
            # Decelerating (< 0.80) — blend down so baseline tracks demand
            # softening; same 60/40 formula avoids overcorrecting on a single
            # bad week (2026-05-20, Issue 2 — symmetric T4 response).
            ord_baseline = _t4_l13_nz_avg * 0.60 + _t4_l4_avg * 0.40
            _t4_applied = "decelerating"

    # R8 — Burst-interleaved-with-zeros median anchor (2026-04-22).
    # For items like FF4934AMZ2 / BB31553 where L13W has many non-zero weeks
    # but the top 2 values dominate (e.g. 2200 + 2100 vs a bunch of 100-200
    # values), the mean is inflated.  Detect: top 2 L13 nz values ≥ 70% of
    # L13 nz total AND L13 nz count ≥ 5.  Use median × 1.5 as baseline ceiling.
    _r8_applied = False
    if len(l13_nz) >= 5:
        _sorted_r8 = sorted(l13_nz, reverse=True)
        _top2_r8 = sum(_sorted_r8[:2])
        _total_r8 = sum(l13_nz)
        if _total_r8 > 0 and _top2_r8 >= _total_r8 * 0.70:
            _median_r8 = _sorted_r8[len(_sorted_r8) // 2]
            _r8_ceiling = _median_r8 * 1.5
            if ord_baseline > _r8_ceiling:
                ord_baseline = _r8_ceiling
                _r8_applied = True

    # L8W recency overlay (2026-05-05) — additive blend that runs AFTER all
    # prior rules.  Blends a recency-weighted estimate (50% L8 / 30% L13 /
    # 20% L26 non-zero averages) against the existing baseline at 60/40 so
    # the calibrated rules above (OOS, drawdown, F4/F6/T4/R8 etc.) still set
    # the floor — we just shift toward recent demand without overriding them.
    # Skipped when any of the three windows lacks signal, when the blend
    # differs from current baseline by < 5% (no-op), or when the recency
    # blend is dramatically lower than baseline (would clobber a hot signal).
    _l8_nz_overlay = [v for v in history[-8:] if v > 0]
    if (len(_l8_nz_overlay) >= 1 and len(l13_nz) >= 2 and len(l26_nz) >= 3
            and ord_baseline > 0):
        _l8_avg_overlay  = sum(_l8_nz_overlay) / len(_l8_nz_overlay)
        _l13_avg_overlay = sum(l13_nz) / len(l13_nz)
        _l26_avg_overlay = sum(l26_nz) / len(l26_nz)
        _blend_overlay   = (0.50 * _l8_avg_overlay
                          + 0.30 * _l13_avg_overlay
                          + 0.20 * _l26_avg_overlay)
        # Don't crush a baseline that's already been raised by an upstream
        # rule (T4, F4, F18) — only apply when blend is within ±50% of base.
        if 0.5 <= (_blend_overlay / ord_baseline) <= 2.0:
            _new_baseline_overlay = 0.60 * _blend_overlay + 0.40 * ord_baseline
            if abs(_new_baseline_overlay - ord_baseline) / ord_baseline >= 0.05:
                ord_baseline = _new_baseline_overlay

    # Fix 4 — Bi-weekly cadence correction: for items that consistently order every
    # other week, the non-zero avg is ~2× the actual weekly demand rate because half
    # the weeks are zero by design. apply_ordering_pattern() enforces the every-other-
    # week shape, so the baseline should be the all-weeks avg (not non-zero avg) to
    # keep the paired quantities correct after enforcement.
    if detect_biweekly(history) and ord_baseline > l13_avg * 1.05:
        ord_baseline = l13_avg

    # F22b superseded by F22c (see CHANGELOG.md).  F22c caps the FINAL
    # baseline after the POS/F13/F15 chain to avoid the F15 interaction.
    _l13_nz_count = len(l13_nz)

    # For Amazon items: blend order-history baseline with consumer POS demand rate.
    # POS tells us how fast Amazon is selling to consumers — a forward-looking signal
    # that complements the order history, especially after a large pre-buy event.
    #
    # F15 — Order-coverage anchor (2026-04-22).  When the planner's historical order
    # rate consistently exceeds consumer POS rate (ord_L13_nz / pos_L13 > 1.15) AND
    # POS is healthy, this is an "order-coverage premium" — the planner is sizing
    # orders off replenishment math (safety stock, lead-time buffer), not just POS
    # consumption.  In that case, shift the blend toward order history so the
    # baseline reflects the planner's intent, not the lower POS velocity.
    #   ratio 1.15 – 1.30 : 70% ord / 30% pos
    #   ratio > 1.30      : 100% ord / 0% pos (drop POS blend)
    # F15 — POS-anchored baseline (any customer with POS data, 2026-05-12).
    # When POS data is available, consumer sell-through is the primary demand
    # signal.  Over a 26-week horizon, a customer's orders MUST converge to
    # their POS rate — what they sell to consumers is what they'll reorder.
    # Recent large orders often reflect inventory positioning (stocking up),
    # not a sustained demand increase; those weeks will be followed by lighter
    # or zero orders until existing stock burns through.
    #
    # Blend table (ord_baseline / pos_rate):
    #   > 2.0  : 100% POS  — heavily stocked up; order history misleads
    #   1.0–2.0: 75% POS / 25% ord — moderately above POS; POS primary
    #   < 1.0  : 65% POS / 35% ord — orders below POS (depleting); POS anchors
    #
    # Applies to any customer with pos_data (currently Amazon; will auto-extend
    # to other customers if their POS data is ever added to the pull).
    # Falls back to order-history baseline when POS is absent or collapsing
    # (L4W < 50% of L13W — dying item, not a stocking-up scenario).
    pos_rate, pos_trend, pos_trend_ratio = 0.0, "n/a", 1.0
    _f15_driver = None
    # F15 — Amazon ordering is lumpy: a single buy event can represent 4-8 weeks
    # of supply.  The L13W order average naturally smooths over these events and
    # represents Amazon's true forward demand rate to us.  POS (consumer sell-
    # through) is a steady floor signal -- never a leading signal for our orders
    # because Amazon's inventory management drives WHEN they order, not what
    # consumers are buying.
    #
    # Blend tiers for Amazon (ord/POS ratio):
    #   > 2.0  : 100% POS     -- extreme stockup; order history misleads
    #   1.5-2.0: 50/50 blend  -- elevated; both signals informative
    #   1.0-1.5: ord-primary  -- normal Amazon ordering above POS; L13W ord IS
    #                            the demand proxy.  POS_L13W is the floor.
    #                            F38b suppressed (L13W already captures trend).
    #   < 1.0  : 65/35 POS/ord -- depleting; POS anchors the coming reorder
    #
    # _f15_amazon_ord_primary tracks when we used ord-primary so downstream
    # rules (F38b) can skip double-counting the growth signal.
    _f15_amazon_ord_primary = False
    if pos_data:
        pos_rate, pos_trend, pos_trend_ratio = amazon_pos_rate(pos_data)
        _pos_l4_f15  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
        _pos_l13_f15 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        _pos_healthy_f15 = _pos_l13_f15 > 0 and _pos_l4_f15 >= _pos_l13_f15 * 0.5
        if pos_rate > 0 and _pos_healthy_f15:
            _ord_cov_ratio = ord_baseline / pos_rate
            if _ord_cov_ratio > 2.0:
                # Extreme stockup: recent orders are inventory build, not demand.
                # 100% POS as the rate anchor.
                baseline = pos_rate
                _f15_driver = (f"F15 stocked-up {_ord_cov_ratio:.1f}x > 2.0 "
                               f"-> 100% POS ({pos_rate:.0f}/wk; "
                               f"ord {ord_baseline:.0f})")
            elif _ord_cov_ratio > 1.5:
                # Elevated ordering (1.5-2.0x POS): Amazon is ordering noticeably
                # above consumer velocity but not in extreme stockup territory.
                # 50/50 blend -- orders and POS are equally informative.
                baseline = pos_rate * 0.50 + ord_baseline * 0.50
                _f15_driver = (f"F15 elevated {_ord_cov_ratio:.2f}x "
                               f"-> 50/50 POS/ord "
                               f"({pos_rate:.0f}/{ord_baseline:.0f})")
            elif _ord_cov_ratio > 1.0 and is_amazon:
                # Normal Amazon ordering (1.0-1.5x POS): Amazon orders ahead
                # of consumer demand to cover lead times and safety stock.
                # The L13W order avg smooths over individual 4-8 week lump
                # events and IS the best forward demand proxy.  Use the higher
                # of L13W orders and POS_L13W as the baseline.
                # F38b is suppressed: L13W ord already captures recent POS
                # acceleration in its rolling window.
                baseline = max(ord_baseline, _pos_l13_f15)
                _f15_amazon_ord_primary = True
                _f15_driver = (f"F15 ord-primary {_ord_cov_ratio:.2f}x "
                               f"-> max(ord {ord_baseline:.0f}, "
                               f"POS_L13 {_pos_l13_f15:.0f}) = {baseline:.0f} "
                               f"(L13W order rate is demand proxy; F38b suppressed)")
            elif _ord_cov_ratio > 1.0:
                # Non-Amazon above-POS: keep original 75/25 POS/ord blend.
                baseline = pos_rate * 0.75 + ord_baseline * 0.25
                _f15_driver = (f"F15 above-POS {_ord_cov_ratio:.2f}x "
                               f"-> 75/25 POS/ord "
                               f"({pos_rate:.0f}/{ord_baseline:.0f})")
            else:
                # Orders at or below POS -- customer may be depleting stock.
                # POS still anchors the baseline; 65/35 POS/ord blend.
                baseline = pos_rate * 0.65 + ord_baseline * 0.35
                _f15_driver = (f"F15 depleting {_ord_cov_ratio:.2f}x "
                               f"-> 65/35 POS/ord "
                               f"({pos_rate:.0f}/{ord_baseline:.0f})")
        else:
            # POS present but collapsing (L4 < 50% of L13) or zero -- fall back
            # to order-history baseline (dying item, not a stocking-up scenario).
            baseline = ord_baseline
    else:
        baseline = ord_baseline

    # F13 — Drawdown-anchored replenishment (Amazon POS-gated, 2026-04-21).
    # Fires when all of the following hold:
    #   (a) Retailer's shipped-to-consumer rate (shpd_l13 from Shpd_Wk_L13W_cust_)
    #       exceeds the account's order-to-us rate (l13 all-weeks avg) by 15%+ —
    #       signaling on-hand inventory depletion at the retailer.
    #   (b) Amazon POS (pos_data) shows recent activity is not collapsing:
    #       L4W avg ≥ 50% of L13W avg. This distinguishes a true drawdown (where
    #       consumer demand continues strong while orders slow) from a dying SKU
    #       (where both POS and orders are collapsing together — F10 territory).
    # When fired, baseline is lifted to a replen floor = shpd_l13 + half of the
    # L13W depletion volume, capped at 1.5× ord_baseline so shpd noise can't
    # runaway. Applied AFTER the POS blend — if the blend already exceeds the
    # floor, F13 is a no-op.
    _f13_applied = False
    _f13_driver = None
    _pos_l4w_f13  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0) if (is_amazon and pos_data) else 0  # noqa
    _pos_l13w_f13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0) if (is_amazon and pos_data) else 0
    _pos_healthy  = _pos_l13w_f13 > 0 and _pos_l4w_f13 >= _pos_l13w_f13 * 0.5
    if (is_amazon and _pos_healthy and shpd_l13 > 0 and l13_avg > 0
            and shpd_l13 > l13_avg * 1.15 and ord_baseline > 0):
        _drawdown_ratio = shpd_l13 / l13_avg
        _depletion_per_wk = max(0.0, shpd_l13 - l13_avg) * 13.0 / 26.0
        _replen_floor = min(shpd_l13 + _depletion_per_wk, ord_baseline * 1.50)
        if _replen_floor > baseline:
            _prev_baseline = baseline
            baseline = _replen_floor
            _f13_applied = True
            _f13_driver = (
                f"drawdown: shpd {shpd_l13:.0f}/wk vs ord {l13_avg:.0f}/wk "
                f"({_drawdown_ratio:.2f}×), POS L4/L13={_pos_l4w_f13/_pos_l13w_f13:.2f} "
                f"→ replen floor {_replen_floor:.0f} (prev {_prev_baseline:.0f})"
            )

    # ── F38 — POS-trend sensitivity (Amazon-only, 2026-05-06) ──────────────────
    # Compares L4w vs L13w consumer sales trend and adjusts the baseline:
    #   F38a — Positive trend ≥+10% but buybox dropped ≥10% AND below MAP
    #          → temporary discount, IGNORE trend (no-op).
    #   F38b — Positive trend ≥+10% with stable/above-MAP price (or no price data)
    #          → legitimate uptick, baseline × (1 + trend) full-pct passthrough.
    #   F38c — Negative trend ≤-10% but Days_OOS_L30d > 0
    #          → OOS-driven dip, IGNORE (sales bounce back when restocked).
    #   F38d — Negative trend ≤-10% but Sellable_OH / L4w < 4 weeks-of-supply
    #          → low-stock dip, IGNORE.
    #   F38e — Negative trend ≤-10% with healthy stock and no OOS
    #          → permanent demand decrease, baseline × (1 + trend).
    # Applied AFTER POS blend / F13 / F15 / F22a so it sees the final composite
    # baseline, but BEFORE F22c sparse-cap and F24 hard ceiling so those still
    # govern the upper bound.  F38f (suppressed/not-buyable hard zero) is
    # applied later in forecast_record() since it overrides the forecast array,
    # not the baseline scalar.
    _f38_driver = None
    if is_amazon and amz_catalog and pos_data and baseline > 0:
        # F15 ord-primary: skip F38b entirely.  The L13W order rate used as
        # baseline already captures recent POS acceleration -- the rolling
        # 13-week window includes the recent up-trend weeks.  Applying F38b
        # on top would double-count the growth.
        if _f15_amazon_ord_primary:
            _f38_driver = (
                f"F38 skipped -- F15 ord-primary "
                f"(baseline = L13W ord {ord_baseline:.0f}; "
                f"POS trend already reflected in rolling L13W ord avg)"
            )
        else:
            try:
                _f38_l4   = float(pos_data.get("Avg_Units_Wk_L4w") or 0)
                _f38_l13  = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
                _f38_bb   = float(amz_catalog.get("Amazon_Buybox") or 0)
                _f38_aur  = float(amz_catalog.get("AUR_L4w") or 0)
                _f38_map  = float(amz_catalog.get("MAP_Price") or 0)
                _f38_oos  = float(amz_catalog.get("Days_Amazon_OOS_L30d_") or 0)
                _f38_soh  = float(amz_catalog.get("Sellable_On_Hand_Units") or 0)
            except (TypeError, ValueError):
                _f38_l4 = _f38_l13 = _f38_bb = _f38_aur = _f38_map = _f38_oos = _f38_soh = 0.0
            if _f38_l13 > 0:
                _f38_trend = (_f38_l4 / _f38_l13) - 1.0
                if _f38_trend >= 0.10:
                    # Positive branch -- check temp-discount caveat first.
                    _temp_discount = False
                    if _f38_bb > 0 and _f38_aur > 0:
                        _price_chg = (_f38_bb / _f38_aur) - 1.0
                        if _price_chg <= -0.10 and _f38_map > 0 and _f38_bb < _f38_map:
                            _temp_discount = True
                    if _temp_discount:
                        # F38a -- temporary discount-driven uptick, ignore trend.
                        _f38_driver = (
                            f"F38a POS trend +{_f38_trend*100:.0f}% but buybox "
                            f"${_f38_bb:.2f} down {_price_chg*100:.0f}% vs AUR_L4w "
                            f"${_f38_aur:.2f} AND below MAP ${_f38_map:.2f} -- "
                            f"temporary discount; ignoring trend"
                        )
                    else:
                        # F38b -- legitimate uptick, full-pct lift.
                        _pre_f38 = baseline
                        baseline = baseline * (1.0 + _f38_trend)
                        _f38_driver = (
                            f"F38b POS trend +{_f38_trend*100:.0f}% (L4w {_f38_l4:.0f}/wk "
                            f"vs L13w {_f38_l13:.0f}/wk); price stable/above-MAP -- "
                            f"baseline lifted {_pre_f38:.0f} -> {baseline:.0f} "
                            f"(full-pct passthrough)"
                        )
                elif _f38_trend <= -0.10:
                    # Negative branch -- check OOS / low-stock guards first.
                    _wos = (_f38_soh / _f38_l4) if _f38_l4 > 0 else 999.0
                    if _f38_oos > 0:
                        # F38c -- OOS-driven dip, ignore.
                        _f38_driver = (
                            f"F38c POS trend {_f38_trend*100:.0f}% but "
                            f"Days_OOS_L30d={_f38_oos:.0f} -- OOS-driven, ignoring"
                        )
                    elif _wos < 4.0:
                        # F38d -- low-stock dip, ignore.
                        _f38_driver = (
                            f"F38d POS trend {_f38_trend*100:.0f}% but Sellable WOS "
                            f"{_wos:.1f} (<4) -- low stock, ignoring"
                        )
                    else:
                        # F38e -- permanent decrease, full-pct cut.
                        _pre_f38 = baseline
                        baseline = baseline * (1.0 + _f38_trend)
                        _f38_driver = (
                            f"F38e POS trend {_f38_trend*100:.0f}% (L4w {_f38_l4:.0f}/wk "
                            f"vs L13w {_f38_l13:.0f}/wk); WOS {_wos:.1f}, no OOS -- "
                            f"baseline cut {_pre_f38:.0f} -> {baseline:.0f}"
                        )

    # 26-week seasonal shape with eased dampening (2026-05-06).  Updated to
    # let strong seasonals (Halloween, Holiday, July 4th, Easter) come through
    # at peak with up to 2.5× lift after normalization.  Previously even the
    # F16-relief path (DAMP=0.4) compressed Halloween's 2.40× raw peak down
    # to ~1.6× — too flat for genuinely sharp Q3/Q4 seasonal items.
    # Base DAMP=0.3 → ~30% historical shape (was 0.1).  Still smooths spurious
    # position-based spikes from a single big order in history but lets real
    # seasonality breathe.
    # Relief DAMP=0.85 → ~85% historical shape (was 0.4).  Items with a known
    # seasonal category OR raw peak-to-trough ≥1.8× get nearly the full curve.
    # Post-normalize cap clips S to [0.30, 2.5] so a single extreme historical
    # week can never blow up the forecast for that slot.
    #
    # F16 — Category-gated damping relief (2026-04-22, eased 2026-05-06).
    #
    # F16b — Volume gate (2026-04-22).  On low-volume items the seasonal
    # signal is too noisy to trust; relief amplifies tail-slice overshoot.
    # Require ord_baseline ≥ 50/wk OR Amazon POS L13 ≥ 50/wk before applying.
    # F22a — Trailing-zero drawdown discount (2026-04-22).  Dense-model items
    # whose order history ends in a run of zero weeks are mid-drawdown at the
    # retailer.  The L13W non-zero avg still produces a healthy per-order
    # baseline, but the planner has already slowed reorders — so the forecast
    # should be scaled down by how long the silence has been going.
    # Discount = min(trailing_zeros / 13, 0.8)  → 20% floor; 13 consecutive
    # trailing zeros = full drawdown → 20% of baseline.  Pure order-history
    # signal; no manual reference.
    _trailing_zeros = 0
    for _v_tz in reversed(history):
        if float(_v_tz) == 0:
            _trailing_zeros += 1
        else:
            break
    _f22a_mult = 1.0 - min(_trailing_zeros / 13.0, 0.8)
    _f22a_applied = False
    if _trailing_zeros >= 3:
        _pre_f22a_baseline = baseline
        baseline = baseline * _f22a_mult
        _f22a_applied = True

    # F22c — Sparse-L13 final-baseline ceiling (2026-04-22, replaces F22b).
    # Applied AFTER the POS blend / F13 replen floor / F15 order-coverage so
    # whatever final baseline the assembly produced can't exceed the L13W
    # all-weeks ceiling for items whose recent cadence is actually sparse.
    # (Earlier F22b variant capped ord_baseline BEFORE the blend, which let
    # F15 stop firing and the POS blend swing the final baseline UP instead.)
    # Pure order-history signal; no manual reference.
    _f22c_applied = False
    _f22c_pre_baseline = baseline
    if _l13_nz_count <= 6 and l13_avg > 0:
        _f22c_ceiling = l13_avg * 1.5
        if baseline > _f22c_ceiling:
            baseline = _f22c_ceiling
            _f22c_applied = True

    # F30 (2026-04-26) — HIGH-vol Seasonal Baseline cap.  Deep-deviation
    # analysis (n=42 HIGH-vol records) showed median bias of +13% vs L13W
    # with 57% running >+10% hot.  HIGH-volume items have buyer plans that
    # are well-disciplined relative to history; AI tends to over-project
    # because POS blends amplify and event lifts compound.  When the per-
    # order baseline exceeds 1.05× L13 weekly rate AND the item is HIGH-
    # volume (baseline >= 1000), cap to 1.05× L13 weekly.
    _f30_applied = False
    _f30_pre_baseline = baseline
    # F51 — F30 POS-acceleration guard (2026-05-08, planner callout).
    # F30 caps HIGH-vol Seasonal Baseline at L13_all-weeks × 1.05 to prevent
    # over-projection.  But when F38b just lifted the baseline based on
    # Amazon POS L4 vs L13 trend ≥+10%, F30's cap would undo that lift —
    # killing legitimate acceleration.  Skip F30 when POS confirms growth.
    #
    # Empirical callout (planner-flagged 2026-05-08):
    #   1864-BB30930 (Amazon): F38b lifted baseline +55% (POS L4=2616 vs
    #   L13=1686). Pre-F30 baseline = ~3049/wk. F30 then capped back to
    #   l13_avg × 1.05 = 2066/wk — wiping out the entire POS lift.
    #   Combined with downstream zero-injections, the 26w forecast collapsed
    #   to 12,828 vs planner ~80k+ expectation.
    _f51_skip = False
    _f51_l4_pos = _f51_l13_pos = 0.0
    if is_amazon and pos_data:
        _f51_l4_pos  = float(pos_data.get('Avg_Units_Wk_L4w') or 0)
        _f51_l13_pos = float(pos_data.get('Avg_Units_Wk_L13w') or 0)
        if (_f51_l4_pos > 0 and _f51_l13_pos > 0 and
                (_f51_l4_pos / _f51_l13_pos) >= 1.10):
            _f51_skip = True
    _f51_applied = False
    _f51_pre_baseline = baseline
    if (not _f51_skip and baseline >= 1000.0 and l13_avg > 0
            and baseline > l13_avg * 1.05):
        baseline = l13_avg * 1.05
        _f30_applied = True
    elif _f51_skip and baseline >= 1000.0 and l13_avg > 0 and baseline > l13_avg * 1.05:
        _f51_applied = True  # actually prevented a cap; surface in meta later

    # F24 moved below — must run AFTER F7 peak-anchored baseline, which
    # re-assigns `baseline = _peak_baseline / _max_S` and would undo F24.
    # Placeholder to keep naming/logging consistent.
    _f24_applied = False
    _f24_pre_baseline = baseline

    S_raw = seasonal_profile(history)
    _seasonal_cat = _get_category_profile(description, product_category,
                                          product_subcategory, brand, brand_pt,
                                          season=season)
    _raw_peak_trough = (max(S_raw) / min(S_raw)) if min(S_raw) > 0 else 1.0
    _pos_l13_f16 = float(pos_data.get("Avg_Units_Wk_L13w") or 0) if (is_amazon and pos_data) else 0.0
    _f16_vol_ok  = (ord_baseline >= 50.0) or (_pos_l13_f16 >= 50.0)
    # F16 relief threshold lowered 2026-05-03 from 2.5x → 1.8x.  Items with
    # raw peak/trough between 1.8x and 2.5x previously fell into the "no
    # category match AND not steep enough" gap and got their seasonal shape
    # squashed to ±20% by DAMP=0.1.  1.8x still represents a clear seasonal
    # signal (e.g. summer-skewed grilling adjacents, mild fall lift items)
    # but no longer compresses items the planners haven't tagged.
    _f16_relief  = (bool(_seasonal_cat) or _raw_peak_trough >= 1.8) and _f16_vol_ok
    # Eased 2026-05-06: 0.4 → 0.85 (relief), 0.1 → 0.3 (base).  Allows post-cap
    # peak up to 2.5× so sharp Q3/Q4 seasonals (Halloween, Holiday) come
    # through at full strength.
    DAMP  = 0.85 if _f16_relief else 0.3
    S = [1.0 + (s - 1.0) * DAMP for s in S_raw]
    s_mean = sum(S) / len(S)
    if s_mean > 0:
        S = [s / s_mean for s in S]  # renormalize so mean = 1.0
    # Hard cap each week's seasonal multiplier in [0.30, 2.5] so neither a
    # noisy historical trough nor an extreme peak can dominate the forecast.
    S = [min(2.5, max(0.30, s)) for s in S]

    # Fix 1 — Category seasonality: blend historical shape 30% with known category
    # profile 70% when the item description matches a seasonal keyword.
    # This corrects items with <52w of history or history biased to one season
    # (e.g. charcoal items whose history is all summer orders look flat when
    # extrapolated to an April-through-September forecast window).
    _cat_mults = _category_week_multipliers(
        description, product_category, product_subcategory, brand, brand_pt,
        season=season
    ) if (description or product_category or product_subcategory or brand or brand_pt or season) else None
    if _cat_mults:
        S = [0.30 * s + 0.70 * c for s, c in zip(S, _cat_mults)]
        s_mean = sum(S) / len(S)
        if s_mean > 0:
            S = [s / s_mean for s in S]

    # F7 — Peak-anchored baseline: if we have category seasonality AND L52 shows
    # strong peak relative to L13 trough, re-anchor baseline to the historical peak
    # instead of the current trough. This lets items heading INTO peak season
    # forecast at their true seasonal peak rather than at current quiet levels.
    _peak_anchor_driver = None
    if _cat_mults:
        _cat_profile_pa = _get_category_profile(description, product_category,
                                                product_subcategory, brand, brand_pt,
                                                season=season)
        if _cat_profile_pa:
            _max_cat = max(_cat_profile_pa)
            _peak_months = {m for m in range(12) if _cat_profile_pa[m] >= _max_cat * 0.85}
            # Align history weeks to calendar months. history[-1] is most recent.
            from datetime import date as _dt_pa, timedelta as _td_pa
            _today_pa = _dt_pa.today()
            _peak_hist_vals = []
            _hist_len_pa = len(history)
            for _i_pa in range(min(52, _hist_len_pa)):
                _wk_date_pa = _today_pa - _td_pa(weeks=_i_pa + 1)
                if _wk_date_pa.month - 1 in _peak_months:
                    _val_pa = history[-1 - _i_pa]
                    if _val_pa > 0:
                        _peak_hist_vals.append(float(_val_pa))
            if _peak_hist_vals and len(_peak_hist_vals) >= 2:
                _l13_nz_local = [v for v in history[-13:] if v > 0]
                _l13_nz_avg = sum(_l13_nz_local) / len(_l13_nz_local) if _l13_nz_local else 0
                _peak_baseline = sum(_peak_hist_vals) / len(_peak_hist_vals)
                # Only re-anchor if peak is materially higher than current baseline
                if _peak_baseline > _l13_nz_avg * 1.5 and _peak_baseline > baseline * 1.3:
                    _max_S = max(S) if S else 1.0
                    if _max_S > 0:
                        baseline = _peak_baseline / _max_S
                        _peak_anchor_driver = (
                            f"peak-anchored: L52 peak-month avg {_peak_baseline:.0f} "
                            f"replaces L13 trough {_l13_nz_avg:.0f}"
                        )

    # F78 (2026-05-24): Peak-anchor fallback for items with no category profile.
    # When _cat_mults is absent (no profile keyword match) but L52W peak month
    # avg is 3x+ the L13W nz avg, re-anchor the baseline to the historical peak.
    # Catches seasonal items like fire starters, dental kits, air fresheners,
    # Fraganzia deodorizers that lack a CATEGORY_PROFILES entry.
    if not _peak_anchor_driver and not _cat_mults and len(history) >= 26:
        from datetime import date as _dt_f78, timedelta as _td_f78
        _today_f78 = _dt_f78.today()
        _f78_month_vals = {}
        for _i_f78 in range(min(52, len(history))):
            _wk_date_f78 = _today_f78 - _td_f78(weeks=_i_f78 + 1)
            _m_f78 = _wk_date_f78.month - 1   # 0-indexed
            _v_f78 = float(history[-1 - _i_f78] or 0)
            if _v_f78 > 0:
                _f78_month_vals.setdefault(_m_f78, []).append(_v_f78)
        if len(_f78_month_vals) >= 2:
            _f78_month_avgs = {m: sum(v) / len(v) for m, v in _f78_month_vals.items()}
            _f78_peak_m  = max(_f78_month_avgs, key=_f78_month_avgs.get)
            _f78_peak_avg = _f78_month_avgs[_f78_peak_m]
            _f78_l13_nz  = [v for v in history[-13:] if v > 0]
            _f78_l13_avg = sum(_f78_l13_nz) / len(_f78_l13_nz) if _f78_l13_nz else 0
            if (_f78_l13_avg > 0
                    and _f78_peak_avg > _f78_l13_avg * 3.0
                    and _f78_peak_avg > baseline * 1.3):
                _f78_max_S = max(S) if S else 1.0
                if _f78_max_S > 0:
                    baseline = _f78_peak_avg / _f78_max_S
                    _peak_anchor_driver = (
                        f"F78 peak-anchor (no profile): L52 peak-month avg "
                        f"{_f78_peak_avg:.0f} = {_f78_peak_avg/_f78_l13_avg:.1f}x "
                        f"L13 trough {_f78_l13_avg:.0f}"
                    )

    # F24 — Final-baseline L13-all-weeks ceiling (placed AFTER F7 peak-anchor
    # so F7's baseline reassignment is also capped).  Observed pattern in
    # Seasonal Baseline top overshooters (BB13437, BB0098, BB11917, FF4934AMZ2):
    # POS blend / F13 / F15 / F7 pushed the final baseline to 3-4× the L13W
    # all-weeks avg, producing flat forecasts at 3-4× the planner's rate even
    # when order history was itself flat.
    # Eased 2026-05-06: cap raised l13_avg × 1.5 → × 2.0 so strongly seasonal
    # items (Halloween, Holiday, Grooming-tagged keywords) have more baseline
    # headroom for the profile multiplier (now allowed up to 2.5×) to lift
    # against without F24 stomping the underlying anchor.  Peak forecast week
    # for an Amazon Halloween-tagged item can now reach baseline × profile
    # × event_lift = (L13×2.0) × 2.5 × 1.25 ≈ 6.25× the L13W weekly rate.
    # Pure order-history signal; no manual reference.
    if l13_avg > 0:
        _f24_ceiling = l13_avg * 2.0
        if baseline > _f24_ceiling:
            _f24_pre_baseline = baseline
            baseline = _f24_ceiling
            _f24_applied = True

    # F48 — Post-OOS spike-and-cooldown anchor (2026-05-07).
    #
    # Detects when L13 baseline is inflated by a recent rebuild-order spike
    # (post-OOS catch-up), and the customer has since cooled toward a lower
    # ongoing pace.  Without this, F24's ×2.0 ceiling is anchored on the
    # already-inflated L13 average, allowing forecasts to compound 30-50%
    # above true demand for ~6 months after a stockout.
    #
    # Pattern (Trigger A — universal spike-and-cooldown):
    #   1. max ord in L13 ≥ 2.5× median of L13 (excluding the max itself)
    #   2. spike occurs in W-12..W-5 (older half of L13, with ≥4w of post-
    #      spike data showing the cooldown)
    #   3. L4 nz-avg < L13 nz-avg × 0.80 (recent cooling vs spike-inflated L13)
    #
    # Pattern (Trigger B — Amazon stable-POS):
    #   Amazon item with healthy POS where L4 ord < POS_blended × 0.85
    #   (current order pace materially below consumer demand → inventory
    #   drawdown, not a permanent decline; baseline shouldn't run hot)
    #
    # Action:
    #   Amazon:     cap = MAX(L4_nz_avg, POS_blended) × 1.30
    #   Non-Amazon: cap = MAX(L4_nz_avg, L26_avg, LY_same_window_avg) × 1.30
    #
    # Concrete cases (2026-05-07):
    #   BB13437 (Amazon): F24 capped at 3,412/wk; F48 anchors at POS×1.30
    #     ≈ 2,210/wk. Manual ≈ 2,055/wk → AI now ~10% over manual.
    #   FF15592 (Walmart): F24 capped at 4,836/wk; F48 anchors at L26×1.30
    #     ≈ 2,376/wk. Manual ≈ 1,562/wk → AI now closer to plan.
    _f48_applied  = False
    _f48_pre_baseline = baseline
    _f48_driver  = None
    if len(history) >= 13:
        _f48_l13         = list(history[-13:])
        # L8 ALL-WEEKS avg — wider than original L4 to smooth over biweekly
        # Walmart/Target order variability (single soft week in L4 was pulling
        # the anchor too low; L8 gives a more stable recent-pace signal while
        # still staying well inside the OOS spike window).  Zeros count as
        # real demand signal (buyer-side pause is meaningful).
        _f48_l4_avg      = (sum(history[-8:]) / 8.0) if len(history) >= 8 else 0.0
        _f48_l13_nz      = [v for v in history[-13:] if v > 0]
        _f48_l13_nz_avg  = (sum(_f48_l13_nz) / len(_f48_l13_nz)) if _f48_l13_nz else 0.0
        _f48_l26_avg     = (sum(history[-26:]) / 26.0) if len(history) >= 26 else _f48_l13_nz_avg
        # LY same-window: weeks 52..27 ago = same calendar window as forward 26w
        _f48_ly_avg      = (sum(history[-52:-26]) / 26.0) if len(history) >= 52 else 0.0
        _f48_l13_max     = max(_f48_l13) if _f48_l13 else 0
        _f48_l13_max_idx = _f48_l13.index(_f48_l13_max) if _f48_l13 else -1
        # Median of L13 EXCLUDING the max value (so a single spike doesn't
        # anchor itself as the median).
        _f48_l13_excl    = [v for i, v in enumerate(_f48_l13) if i != _f48_l13_max_idx]
        _f48_l13_med     = (sorted(_f48_l13_excl)[len(_f48_l13_excl) // 2]
                            if _f48_l13_excl else 0)

        # Trigger A — spike-and-cooldown
        # idx 0 = W-13, idx 12 = W-1.  Spike in W-12..W-5 means idx in [1..8].
        # Cooldown uses L8_avg (stored in _f48_l4_avg) vs L13 nz-avg.
        _f48_spike   = (_f48_l13_med > 0 and _f48_l13_max >= _f48_l13_med * 2.5
                        and 1 <= _f48_l13_max_idx <= 8)
        _f48_cooled  = (_f48_l13_nz_avg > 0 and _f48_l4_avg > 0
                        and _f48_l4_avg < _f48_l13_nz_avg * 0.80)
        _f48_trig_a  = _f48_spike and _f48_cooled

        # Trigger B — Amazon stable-POS but order pace below POS
        _f48_trig_b  = False
        _f48_pos_blend = 0.0
        if is_amazon and pos_data:
            _pos_l4_f48  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
            _pos_l13_f48 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
            _pos_l26_f48 = float(pos_data.get("Avg_Units_Wk_L26w") or 0)
            _f48_pos_healthy = _pos_l13_f48 > 0 and _pos_l4_f48 >= _pos_l13_f48 * 0.5
            if _f48_pos_healthy and _pos_l4_f48 > 0 and _pos_l13_f48 > 0:
                # POS blend: 40% L4, 40% L13, 20% L26 (recent-weighted)
                if _pos_l26_f48 > 0:
                    _f48_pos_blend = (_pos_l4_f48 * 0.40 + _pos_l13_f48 * 0.40
                                      + _pos_l26_f48 * 0.20)
                else:
                    _f48_pos_blend = (_pos_l4_f48 * 0.50 + _pos_l13_f48 * 0.50)
                if (_f48_l4_avg > 0
                        and _f48_l4_avg < _f48_pos_blend * 0.85):
                    _f48_trig_b = True

        if _f48_trig_a or _f48_trig_b:
            # Anchor on the lower-bound stable signals: L8 (current pace) and
            # L26 (medium-term avg).  LY is intentionally EXCLUDED — declining
            # items can have an inflated LY that would loosen the cap.
            # Multiplier 1.20 (was 1.30) — tight enough to actually bind for
            # records where prior rules already pulled baseline near L13_avg
            # but still above true pace.
            if is_amazon and _f48_pos_blend > 0:
                _f48_anchor = max(_f48_l4_avg, _f48_pos_blend)
                _f48_src    = (f"max(L8 {_f48_l4_avg:.0f}, "
                               f"POS_blend {_f48_pos_blend:.0f})")
            else:
                _f48_anchor = max(_f48_l4_avg, _f48_l26_avg)
                _f48_src    = (f"max(L8 {_f48_l4_avg:.0f}, "
                               f"L26 {_f48_l26_avg:.0f})")
            _f48_ceiling = _f48_anchor * 1.20
            if _f48_anchor > 0 and baseline > _f48_ceiling:
                _which = "A spike-cooldown" if _f48_trig_a else "B Amazon-POS-gap"
                _f48_driver = (
                    f"F48 post-OOS recovery anchor (Trigger {_which}): "
                    f"baseline {_f48_pre_baseline:.0f} → {_f48_ceiling:.0f} "
                    f"(anchor = {_f48_src} × 1.20; "
                    f"L13 spike {_f48_l13_max:.0f} vs median "
                    f"{_f48_l13_med:.0f} = "
                    f"{(_f48_l13_max / _f48_l13_med if _f48_l13_med else 0):.1f}×, "
                    f"L8/L13_nz = "
                    f"{(_f48_l4_avg / _f48_l13_nz_avg if _f48_l13_nz_avg else 0):.2f})"
                )
                baseline = _f48_ceiling
                _f48_applied = True

    # Raw forecast: damped profile + explicit event lifts
    raw = []
    _f66_floored = 0
    # F66 (2026-05-21) — Seasonal floor: the seasonal profile can only
    # INCREASE demand, never reduce it.  Any week where the multiplier
    # would fall below 1.0 is held at 1.0 (flat baseline).
    #
    # GATE: only applies when a category profile was blended in (_cat_mults
    # is set).  Category profiles are curated seasonal shapes (charcoal,
    # holiday, paper goods, etc.) that should only LIFT demand above the
    # flat baseline — a trough in those profiles is a modelling artefact,
    # not a real demand signal.
    #
    # Without a category match the profile is built purely from order history,
    # which for pulsed / Amazon accounts reflects ordering CADENCE (high in
    # order weeks, low in gap weeks).  Raising gap weeks to 1.0 there creates
    # phantom demand in weeks the customer won't order, producing an
    # artificially flat and elevated forecast.
    _f66_eligible = bool(_cat_mults)
    for i in range(26):
        wnum = i + 1
        s = S[i]
        if _f66_eligible and s < 1.0:
            s = 1.0
            _f66_floored += 1
        # F11 — Prime Day / Fall Prime Day ordering lift (Amazon-only, calendar-based).
        if is_amazon:
            _pb, _fb = _get_event_boosts()
            _ev = max(_pb.get(wnum, 1.0), _fb.get(wnum, 1.0))
            if _ev > 1.0:
                s *= _ev
        raw.append(baseline * s)

    # Light smoothing (smooth_forecast rescales internally to preserve total)
    raw = smooth_forecast(raw, weight=0.25)

    # Snap to master pack
    forecast = [snap(v, mp) for v in raw]

    # F10 — Declining-item end-of-life detection (YoY-gated, 2026-04-21).
    # Two tests must both pass before we scale down:
    #   1) L4W avg < 70% of L13W non-zero avg (current drop)
    #   2) L4W avg < 50% of same 4-week window ~1 year ago (YoY drop)
    # If YoY data is unavailable (<52 weeks of history), fall back to the
    # L13 test alone. The YoY gate prevents seasonal items in their
    # off-season trough (e.g. charcoal in April) from being mis-detected as
    # declining — their YoY ratio stays ~1.0 because last year's same window
    # was also low.
    _l4_avg_f10  = sum(history[-4:]) / 4 if len(history) >= 4 else 0
    _l13_nz_f10  = [v for v in history[-13:] if v > 0]
    _l13_nz_avg_f10 = sum(_l13_nz_f10) / len(_l13_nz_f10) if _l13_nz_f10 else 0
    _l4_yago_f10 = sum(history[-52:-48]) / 4 if len(history) >= 52 else 0
    _drop_vs_l13 = _l13_nz_avg_f10 > 0 and _l4_avg_f10 < _l13_nz_avg_f10 * 0.7
    _drop_yoy    = _l4_yago_f10 > 0 and _l4_avg_f10 < _l4_yago_f10 * 0.5
    _yoy_avail   = _l4_yago_f10 > 0
    # F14a — POS-healthy override on F10 (2026-04-21).
    # Amazon order-side data can show a sharp L4W drop (buyer-side ordering lag,
    # drawdown of on-hand inventory at the retailer) while consumer POS stays
    # strong. When POS L4/L13 ≥ 0.5, the "decline" signal from the order book is
    # not a true end-of-life — it's a replenishment pause. Skip F10's scale-down
    # in that case and let F13 + baseline drive the forecast instead.
    #
    # F14b — Volume gate on F14a (2026-04-22). The override was over-firing on
    # small-volume tail items, lifting tail forecasts +19%. Restrict F14a to
    # items with meaningful consumer demand: POS L13 ≥ 50/wk (≈1,300/26w).
    # Below that threshold, F10 applies normally.
    _f14a_override = False
    if _drop_vs_l13 and (_drop_yoy or not _yoy_avail) and is_amazon and pos_data:
        _pos_l4_f14  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
        _pos_l13_f14 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        _f14b_vol_ok = _pos_l13_f14 >= 50.0
        if _pos_l13_f14 > 0 and _pos_l4_f14 >= _pos_l13_f14 * 0.5 and _f14b_vol_ok:
            _f14a_override = True
    # F34: skip F10 entirely on new-launch items — pre-launch zeros aren't a
    # decline reference; the YoY check is meaningless against an empty window.
    _f10_applied = False
    _f77_applied = False
    _f77_driver  = None
    _f79_ratio   = 1.0
    if (_drop_vs_l13 and (_drop_yoy or not _yoy_avail)
            and not _f14a_override and not is_new_launch):
        _new_fcst = []
        for _w_i, _v_i in enumerate(forecast):
            _blended = 0.5 * _v_i + 0.5 * _l4_avg_f10
            if _w_i >= 13:
                _blended *= 0.85
            _new_fcst.append(snap(_blended, mp) if _blended > 0 else 0)
        forecast = _new_fcst
        _f10_applied = True

    # F77 (2026-05-24): Severe-decline blend without YoY gate.
    # F10 requires YoY confirmation, which blocks it on items declining within
    # their product lifecycle at the same seasonal stage as last year.
    # F77 fires independently when:
    #   - F10 did NOT fire (not _f10_applied)
    #   - L4W < L13W nz avg × 0.65 (>35% drop — more severe than F10's 0.70)
    #   - Seasonal profile variance is modest: max(S)/min_nz(S) < 2.5
    #     (protects genuinely high-seasonality items from getting blended down)
    #   - Not a new launch
    # Blend is lighter than F10: 0.30 × L4W + 0.70 × model; W14+ × 0.90.
    if (not _f10_applied and not is_new_launch
            and _l13_nz_avg_f10 > 0
            and _l4_avg_f10 < _l13_nz_avg_f10 * 0.65):
        # Gate on seasonal profile variance
        _f77_S_min_nz = min((v for v in S if v > 0), default=1.0) if S else 1.0
        _f77_S_max    = max(S) if S else 1.0
        _f77_seasonal = _f77_S_max > 0 and (_f77_S_max / _f77_S_min_nz) >= 2.5
        if not _f77_seasonal:
            _new_f77 = []
            for _w_f77, _v_f77 in enumerate(forecast):
                _blend = 0.30 * _l4_avg_f10 + 0.70 * _v_f77
                if _w_f77 >= 13:
                    _blend *= 0.90
                _new_f77.append(snap(_blend, mp) if _blend > 0 else 0)
            forecast = _new_f77
            _f77_applied = True
            _f77_driver = (
                f"F77 severe-decline blend (no YoY): L4W avg {_l4_avg_f10:.0f} "
                f"= {(_l4_avg_f10/_l13_nz_avg_f10)*100:.0f}% of L13W nz avg "
                f"{_l13_nz_avg_f10:.0f}; blended 30/70 toward L4W"
            )

    # F79 (2026-05-24): Amazon growth trend multiplier.
    # When Amazon is buying at an accelerating rate (L4W / L13W nz avg >= 1.20),
    # scale the forecast up by min(L4W / L13W_nz_avg, 1.50) to capture growth
    # the static baseline misses.  Only fires on Seasonal Baseline (this function),
    # not Croston's/Heuristic (they have their own trend handling).
    # Guard: skip if F10 or F77 already applied a decline correction.
    _f79_driver = None
    if (is_amazon
            and not _f10_applied
            and not _f77_applied
            and not is_new_launch
            and _l13_nz_avg_f10 > 0
            and _l4_avg_f10 >= _l13_nz_avg_f10 * 1.20):
        _f79_ratio  = min(_l4_avg_f10 / _l13_nz_avg_f10, 1.50)
        forecast    = [snap(v * _f79_ratio, mp) if v > 0 else 0 for v in forecast]
        _f79_driver = (
            f"F79 Amazon growth: L4W avg {_l4_avg_f10:.0f} = "
            f"{_f79_ratio:.2f}x L13W nz avg {_l13_nz_avg_f10:.0f}; "
            f"forecast scaled up x{_f79_ratio:.2f}"
        )

    l26_avg = sum(float(v) for v in history[-26:]) / 26
    cap_base = baseline
    meta = {
        "model":          "seasonal_baseline",
        "l13_avg":        round(l13_avg, 1),
        "l13_nz_avg":     round(ord_baseline, 1),
        "l26_avg":        round(l26_avg, 1),
        "baseline":       round(baseline, 1),
        "baseline_mode":  _baseline_mode,
        "seas_min":       round(min(S), 2),
        "seas_max":       round(max(S), 2),
    }
    # Surface the baseline-mode choice as a driver so planners can audit it
    # in the AI_ALERT narrative for every record (VP-Q1, 2026-04-28).
    if _baseline_mode:
        meta.setdefault("drivers", []).append(f"VP-Q1 baseline: {_baseline_mode}")
    if _f66_floored:
        meta.setdefault("drivers", []).append(
            f"F66 seasonal floor: {_f66_floored} week(s) where raw profile < 1.0 "
            f"raised to 1.0 (profile can only increase demand, raw min was "
            f"{round(min(S), 2)})"
        )
    if pos_rate > 0:
        meta["pos_rate"]         = pos_rate
        meta["pos_trend"]        = pos_trend
        meta["pos_trend_ratio"]  = pos_trend_ratio
    if _peak_anchor_driver:
        meta.setdefault("drivers", []).append(_peak_anchor_driver)
    if _f10_applied:
        meta.setdefault("drivers", []).append(
            f"declining: L4W avg {_l4_avg_f10:.0f} < 70% L13 nz avg {_l13_nz_avg_f10:.0f}"
        )
    if _f77_applied and _f77_driver:
        meta.setdefault("drivers", []).append(_f77_driver)
    if _f79_driver:
        meta.setdefault("drivers", []).append(_f79_driver)
    if _f14a_override:
        _pos_l4_m = float(pos_data.get("Avg_Units_Wk_L4w")  or 0) if pos_data else 0
        _pos_l13_m = float(pos_data.get("Avg_Units_Wk_L13w") or 0) if pos_data else 0
        _ratio_m = (_pos_l4_m / _pos_l13_m) if _pos_l13_m > 0 else 0
        meta.setdefault("drivers", []).append(
            f"F14a POS-healthy override on F10: POS L4/L13={_ratio_m:.2f} ≥ 0.50 "
            f"(order drop = retailer drawdown, not consumer decline)"
        )
    if _f13_applied and _f13_driver:
        meta.setdefault("drivers", []).append(_f13_driver)
    if _f15_driver:
        meta.setdefault("drivers", []).append(_f15_driver)
    if _f38_driver:
        meta.setdefault("drivers", []).append(_f38_driver)
    if _f4_applied:
        meta.setdefault("drivers", []).append(
            f"F4 thin-history window widened: L13_nz={len(l13_nz)} ≤ 4 AND "
            f"L52_nz={len(_l52_nz_f4)} ≥ 8 → effective L52 rate used as baseline"
        )
    if _f6_applied == "F50_stockout_skip":
        meta.setdefault("drivers", []).append(
            f"F50 stockout-pattern skip: L4 has {_l4_zeros_f50} zero week(s) but "
            f"L13 has {_l13_active_f50}/13 active weeks → likely stockout, not "
            f"decay; F6b/F26 cuts skipped to preserve L13 baseline"
        )
    if _f51_applied:
        meta.setdefault("drivers", []).append(
            f"F51 F30-skip POS-confirmed acceleration: Amazon POS L4 "
            f"{_f51_l4_pos:.0f}/wk vs L13 {_f51_l13_pos:.0f}/wk = "
            f"{_f51_l4_pos/_f51_l13_pos:.2f}× (≥1.10) → preserved F38b lift "
            f"(baseline {_f51_pre_baseline:.0f}/wk, would have capped to "
            f"{l13_avg*1.05:.0f}/wk)"
        )
    if _f6_applied and _f6_applied != "F50_stockout_skip":
        meta.setdefault("drivers", []).append(
            f"F6b L4/L13 decay: L4 nz avg <= 50% of L13 nz avg -> baseline x 0.65 "
            f"(recent softening detected from order history alone)"
        )
    if _f16_relief:
        meta.setdefault("drivers", []).append(
            f"F16 seasonal damping relief: DAMP=0.85 "
            f"(category={'yes' if _seasonal_cat else 'no'}, "
            f"raw peak/trough={_raw_peak_trough:.2f}, "
            f"ord_base={ord_baseline:.0f}/wk, pos_L13={_pos_l13_f16:.0f}/wk)"
        )
    if season:
        meta["season"] = season
        if season in SEASON_TO_PROFILE:
            meta.setdefault("drivers", []).append(
                f"Season tag '{season}' from Styles → seasonal profile applied"
            )
    if _f22a_applied:
        meta["trailing_zeros"] = _trailing_zeros
        meta.setdefault("drivers", []).append(
            f"F22a trailing-zero drawdown: {_trailing_zeros} consecutive zero weeks "
            f"→ baseline {_pre_f22a_baseline:.0f} × {_f22a_mult:.2f} = {baseline:.0f}"
        )
    if _f22c_applied:
        meta["l13_nz_count"] = _l13_nz_count
        meta.setdefault("drivers", []).append(
            f"F22c sparse-L13 ceiling: {_l13_nz_count}/13 non-zero weeks "
            f"→ final baseline capped at L13 all-avg × 1.5 "
            f"({_f22c_pre_baseline:.0f} → {baseline:.0f})"
        )
    if _f24_applied:
        meta.setdefault("drivers", []).append(
            f"F24 L13-all ceiling: baseline capped at L13_avg × 2.0 "
            f"({_f24_pre_baseline:.0f} → {baseline:.0f}, "
            f"L13_avg={l13_avg:.0f})"
        )
    if _f48_applied and _f48_driver:
        meta.setdefault("drivers", []).append(_f48_driver)
    if _r8_applied:
        meta.setdefault("drivers", []).append(
            f"R8 burst-median ceiling: top2 ≥ 70% of L13 nz total "
            f"→ ord_baseline capped at median × 1.5 = {ord_baseline:.0f}"
        )
    return forecast, round(cap_base, 1), meta


def get_history(row, oos_entry=None):
    """Return 52-week order history (oldest→newest) as the demand signal.
    Orders capture true demand even when stockouts cause partial shipments.

    VP-Q2: when an oos_entry dict is provided (from oos_history.fetch_clean_demand),
    we use the *clean_ord* series instead of raw Ord_LW_n.  This excludes Bucket-B
    cancels (customer order errors, Future-Delete, Low-Margin) so they don't
    inflate the demand baseline.  Compounding catch-up after hard-OOS weeks is
    also neutralized so a stockout-then-spike pattern doesn't double-count.
    """
    raw = [float(row.get(c) or 0) for c in ORD_COLS]
    if oos_entry and sum(oos_entry.get("raw_ord") or []) > 0:
        from oos_history import neutralize_compounding
        clean = oos_entry["clean_ord"]
        sev   = oos_entry["oos_severity"]
        # Sanity-check coverage: if the Order_History total is far below
        # the raw Ord_LW totals (data sync lag, partial coverage), fall
        # back to raw to avoid spuriously deflating demand.
        oh_l52  = sum(oos_entry["raw_ord"])
        raw_l52 = sum(raw)
        if oh_l52 < 0.5 * raw_l52:
            return raw
        return neutralize_compounding(clean, sev)
    return raw


def make_weighted_series(history):
    """
    Build 78-observation weighted series: full 52w + L13W repeated twice.
    This gives the most recent 13 weeks 3x the influence on level/trend
    estimates, matching the inventory-forecaster weighting scheme.
    """
    l13 = history[-13:]
    return list(history) + list(l13) + list(l13)


def detect_ramp(history):
    """
    Identify new-item ramp period.
    Returns (first_nz_idx, ramp_end_idx) where ramp_end = first_nz + 6.
    Returns (None, 0) if no activity found.
    """
    first_nz = next((i for i, v in enumerate(history) if v > 0), None)
    if first_nz is None:
        return None, 0
    return first_nz, min(first_nz + 6, len(history))


def detect_biweekly(history):
    """VP-Q3 (2026-05-03): Generalized to "low-cadence" detection.
    Bi-weekly (every-other-week) is now treated as effectively weekly — the
    forecast is just smoothed across weekly average rather than enforced into
    alternating zeros.  VP guidance: bi-weekly is frequent enough that getting
    inventory placement off by one week has small cost; cadence enforcement
    only adds value at MONTHLY+ intervals where 3+ weeks of zeros vs 1 chunk
    creates a big swing.

    Returns the cadence gap in weeks (int ≥ 3) when a consistent
    monthly-or-sparser pattern is detected, else 0 (False-y).

    Detection: median gap between non-zero L26W weeks must be ≥ 3 AND
    at least 60% of gaps must be within ±1 of the median (consistent rhythm).

    Function name kept for backward compatibility with callers — the boolean
    truthiness still works; callers that want the actual interval can use
    `gap = detect_biweekly(history); if gap: ... `.
    """
    h = history[-26:]
    if len(h) < 10:
        return 0
    nz_idx = [i for i, v in enumerate(h) if v > 0]
    if len(nz_idx) < 3:
        return 0  # too few orders to detect cadence

    gaps = [nz_idx[i+1] - nz_idx[i] for i in range(len(nz_idx) - 1)]
    if not gaps:
        return 0

    gaps_sorted = sorted(gaps)
    median_gap = gaps_sorted[len(gaps_sorted) // 2]

    # VP-Q3: only enforce at monthly+ cadence (gap ≥ 3 weeks).
    # Weekly (gap=1) and bi-weekly (gap=2) → just smooth across weeks.
    if median_gap < 3:
        return 0

    # Require the rhythm to be consistent: ≥60% of gaps within ±1 of median.
    consistent = sum(1 for g in gaps if abs(g - median_gap) <= 1)
    if consistent / len(gaps) < 0.60:
        return 0

    return int(median_gap)


def apply_ordering_pattern(forecast, history, mp):
    """
    Post-process forecast to enforce LOW-CADENCE cadence (monthly+) if detected.

    VP-Q3 (2026-05-03): Generalized from pair-merge bi-weekly enforcement to
    N-week-chunk merging for any cadence gap ≥ 3 weeks.  Bi-weekly patterns
    are no longer enforced (return forecast unchanged).

    For monthly cadence (gap=4), merges every 4 forecast weeks into one chunk
    placed on the active phase.  For quarterly (gap=13), merges every 13 weeks.

    Preserves master-pack divisibility on each chunk.
    """
    gap = detect_biweekly(history)
    if not gap:                                  # weekly / bi-weekly / irregular
        return forecast

    h = history[-26:]
    # Anchor: use the most-recent non-zero week to determine the active phase.
    nz_idx = [i for i, v in enumerate(h) if v > 0]
    if not nz_idx:
        return forecast
    last_active = nz_idx[-1]                     # in [0..25] of last 26w

    # Forecast week i corresponds to absolute week index (26 + i) when the
    # last 26w of history was h[0..25].  The cadence is "active" on weeks
    # where (abs_idx - last_active) % gap == 0.
    result = [0] * 26
    cycle_total = 0.0
    for i in range(26):
        cycle_total += forecast[i]
        abs_idx = 26 + i
        is_active = ((abs_idx - last_active) % gap == 0)
        is_last = (i == 25)
        if is_active or is_last:
            result[i] = snap(cycle_total, mp)
            cycle_total = 0.0
    return result


def normalize_stockout_recovery(hist):
    """
    F35 — Stockout backlog normalization (2026-05-05).

    During an out-of-stock window, the customer cannot get product but
    keeps re-ordering: the order they place each week of the gap is
    "this week's base + everything we still owe them".  When shipments
    resume, the catch-up week's order = base demand + recoverable
    backlog, NOT real demand intent for that week.  If we feed the raw
    catch-up qty into the forecaster, we over-project on a recurring
    basis — the model thinks the customer wants 2-3× the true rate.

    Empirical decay schedule (planner-provided):
        Week 1 of stockout:  25% lost,  75% recoverable as backlog
        Week 2:              50% lost,  50% recoverable
        Week 3:              75% lost,  25% recoverable
        Week 4+:            100% lost,   0% recoverable

    Algorithm:
      • Find runs of 2-8 consecutive zero-weeks ("stockout candidates")
        embedded in a dense ordering pattern (≥70% non-zero in the prior
        13 weeks, with ≥3 active pre-gap weeks).
      • Compute pre-gap baseline = avg of pre-gap non-zero weeks.
      • Recoverable backlog = Σ(decay factors over gap_len) × baseline.
      • Walk forward up to 4 post-gap weeks and subtract backlog from
        each week's order (capped at order−baseline) until the
        recoverable bank is exhausted.  What remains is true demand
        intent for that week.

    Returns:
        (normalized_history, corrections list).
        Each correction = {start, length, baseline, removed}.
    """
    n = len(hist)
    out = [float(v or 0) for v in hist]
    corrections = []
    if n < 8:
        return out, corrections

    factors = [0.75, 0.50, 0.25]   # recoverable share by week of stockout
    i = 0
    while i < n:
        if out[i] == 0:
            run_start = i
            while i < n and out[i] == 0:
                i += 1
            run_end = i  # first non-zero index after the run
            run_len = run_end - run_start
            # Stockout candidate: 2-8 weeks long, with prior history and post-gap data
            if 2 <= run_len <= 8 and run_start > 0 and run_end < n:
                pre_window = out[max(0, run_start - 13):run_start]
                pre_nz = [v for v in pre_window if v > 0]
                pre_density = (len(pre_nz) / len(pre_window)) if pre_window else 0
                if pre_density >= 0.70 and len(pre_nz) >= 3:
                    baseline = sum(pre_nz) / len(pre_nz)
                    if baseline >= 1:
                        # Total recoverable backlog over the full gap
                        recoverable_total = sum(factors[:min(run_len, 3)]) * baseline
                        remaining = recoverable_total
                        removed_total = 0.0
                        # Walk up to 4 catch-up weeks; subtract backlog from each
                        for k in range(min(4, n - run_end)):
                            idx = run_end + k
                            v = out[idx]
                            if remaining <= 0 or v <= baseline:
                                break
                            absorbed = min(v - baseline, remaining)
                            out[idx] = v - absorbed
                            remaining -= absorbed
                            removed_total += absorbed
                        if removed_total > 0:
                            corrections.append({
                                "start":    run_start,
                                "length":   run_len,
                                "baseline": round(baseline, 1),
                                "removed":  round(removed_total, 1),
                            })
            continue
        i += 1

    return [int(round(v)) for v in out], corrections


def normalize_ats_oos_weeks(hist, ats_l26):
    """
    VP-ATS (2026-05-17) — ATS-confirmed OOS zero-week fill.

    Uses Available-to-Sell (ATS) inventory data to identify weeks where
    zero orders were caused by us being out-of-stock rather than by genuine
    demand absence.  When ATS ≈ 0 AND orders were also near-zero, the
    customer stopped ordering because we had nothing to sell — those weeks
    should be treated as demand-intent = baseline, not as demand = 0.

    Running AFTER F35 is intentional: F35 first strips the post-gap
    catch-up spike (backlog normalization); VP-ATS then fills the confirmed
    OOS zero-weeks with baseline so the rest of the pipeline (F47, F41,
    F6, F50, Croston, seasonal) sees a clean demand signal instead of
    OOS-induced gaps.

    hist:     52-week order history list (oldest→newest, indices 0..51).
    ats_l26:  26-week ATS list (oldest→newest, indices 0..25), where
              ats_l26[k] aligns with hist[26+k].  Typically from
              oos_history.fetch_ats_history() — one record per Mstyle.

    Detection criteria for an ATS-confirmed OOS week at hist-index i (26..51):
      1. Orders near-zero: hist[i] < max(10, 10% of prior L13 nz-avg)
      2. ATS constrained:  ats_l26[i-26] < max(10, 25% of prior L13 nz-avg)
         (we had less than a quarter-week of supply available to ship)
      3. Prior L13 had ≥3 non-zero weeks (item has an established demand signal)

    When all conditions are met the week is filled with the L13 nz-avg
    computed from the ORIGINAL (pre-fill) history to prevent cascading
    inflation across consecutive OOS weeks.

    Guard: if ALL 26 ATS values are zero the data is likely missing/not yet
    loaded — skip all fills to avoid false positives.

    Returns:
        (normalized_hist, corrections)
        corrections: list of {week_idx, ats_val, baseline, filled_to}
    """
    n    = len(hist)
    orig = [float(v or 0) for v in hist]   # immutable baseline source
    out  = list(orig)
    corrections = []

    if n < 27 or not ats_l26 or len(ats_l26) < 26:
        return [int(round(v)) for v in out], corrections

    # Guard: all-zero ATS means data unavailable — skip to avoid false fills.
    if sum(ats_l26) == 0:
        return [int(round(v)) for v in out], corrections

    for i in range(26, min(52, n)):
        ats_idx = i - 26
        ats_val = float(ats_l26[ats_idx] or 0)
        # Negative ATS = returns/adjustments created a paper over-allocation;
        # this is a data quality artifact, not an actual stockout.  Skip.
        if ats_val < 0:
            continue

        # L13 nz-avg from ORIGINAL history prior to this week
        prior_lo = max(0, i - 13)
        prior_nz = [orig[j] for j in range(prior_lo, i) if orig[j] > 0]
        if len(prior_nz) < 3:
            continue   # sparse — no reliable baseline, skip
        baseline = sum(prior_nz) / len(prior_nz)
        if baseline < 10:
            continue

        near_zero_thresh = max(10.0, 0.10 * baseline)
        ats_thresh       = max(10.0, 0.25 * baseline)

        if orig[i] < near_zero_thresh and ats_val < ats_thresh:
            out[i] = baseline
            corrections.append({
                "week_idx":  i,
                "ats_val":   round(ats_val,  1),
                "baseline":  round(baseline, 1),
                "filled_to": round(baseline, 1),
            })

    return [int(round(v)) for v in out], corrections


def normalize_ats_catchup_spikes(hist, ats_l26):
    """
    VP-ATS-Catch (2026-05-17) — Cap post-OOS catch-up order spikes using ATS data.

    Companion to VP-ATS. VP-ATS fills zero-order weeks during OOS (suppressed demand).
    This rule handles the opposite end: inflated orders in the 1-3 weeks immediately
    after ATS restores, caused by pent-up / duplicate orders from the OOS period.

    Per planner feedback (1864-FF9297/24): weeks of 2/15 & 2/22 showed elevated
    catch-up orders immediately after an OOS period confirmed by near-zero ATS.
    Those weeks were included in L13W nz-avg, pulling the baseline — and therefore
    the AI forecast — too high.

    Detection at ATS index k (k ∈ 2..24):
      • Prior ≥2 OOS weeks confirmed: ats_l26[k-1] < ats_thresh
                                  AND ats_l26[k-2] < ats_thresh
      • ATS restoration: ats_l26[k] >= ats_thresh * 2 (supply meaningfully returned)
      • Pre-OOS baseline: L13 nz-avg from order history before the OOS onset
      • Catch-up spike: hist[26+k+offset] > pre_baseline * 1.5 for offset 0..2

    Action: Cap those catch-up weeks to pre_baseline (strip the backlog excess).

    Guards:
      • All-zero ATS → data unavailable, skip
      • Pre-OOS nz-count < 3 → no reliable baseline, skip
      • pre_baseline < 10 → too sparse, skip
      • Negative ATS values → data artifact, skip

    Returns:
        (normalized_hist, corrections)
        corrections: list of {week_idx, orig_val, capped_to, ats_at_restoration}
    """
    n = len(hist)
    orig = [float(v or 0) for v in hist]
    out  = list(orig)
    corrections = []

    if n < 27 or not ats_l26 or len(ats_l26) < 26:
        return [int(round(v)) for v in out], corrections
    if sum(ats_l26) == 0:
        return [int(round(v)) for v in out], corrections

    for k in range(2, 25):
        ats_cur = float(ats_l26[k] or 0)
        ats_p1  = float(ats_l26[k-1] or 0)
        ats_p2  = float(ats_l26[k-2] or 0)
        if ats_cur < 0:
            continue

        # Pre-OOS baseline: orders from before the OOS onset (before week k-2 in hist)
        hist_oos_onset = 26 + k - 2
        pre_lo = max(0, hist_oos_onset - 13)
        pre_nz = [orig[j] for j in range(pre_lo, hist_oos_onset) if orig[j] > 0]
        if len(pre_nz) < 3:
            continue
        pre_baseline = sum(pre_nz) / len(pre_nz)
        if pre_baseline < 10:
            continue

        ats_thresh = max(10.0, 0.25 * pre_baseline)

        # Both prior weeks must be OOS (ATS constrained)
        if ats_p1 >= ats_thresh or ats_p2 >= ats_thresh:
            continue
        # Current week: ATS must have meaningfully restored (2× threshold)
        if ats_cur < ats_thresh * 2:
            continue

        # Cap catch-up window: up to 3 weeks starting at restoration point
        catch_ceil = pre_baseline * 1.5
        for offset in range(min(3, n - (26 + k))):
            j = 26 + k + offset
            if out[j] > catch_ceil:
                orig_val = out[j]
                out[j] = int(round(pre_baseline))
                corrections.append({
                    "week_idx":           j,
                    "orig_val":           round(orig_val, 1),
                    "capped_to":          round(pre_baseline, 1),
                    "ats_at_restoration": round(ats_cur, 1),
                })

    return [int(round(v)) for v in out], corrections


def get_ship_history(row):
    """Return 52-week shipment history (oldest→newest), aligned 1:1 with
    get_history() order-side output.  Reads Shp_LW_n columns the same way
    get_history reads Ord_LW_n.  Used by F41 (shipment-confirmed phantom
    dedupe) to cross-check whether an order was actually fulfilled.
    """
    return [float(row.get(c) or 0) for c in SHP_COLS]


def normalize_phantom_orders(hist, ships, protected_indices=None):
    """
    F41 — Shipment-confirmed phantom-order dedupe (2026-05-06).

    Cross-references order history with shipment history to catch duplicate
    reorders the customer placed because the *previous* order didn't ship.
    This is a stronger signal than F39's qty-pattern matching because the
    ground truth (the warehouse's actual shipment record) confirms whether
    the original order was fulfilled.

    Why this rule exists (per VP feedback 2026-05-06):
      Amazon's typical ship window is 3-5 business days from receipt of order,
      so order-week N legitimately ships in week N or week N+1.  But if N+0
      and N+1 ship < 30% of order N's qty, the customer sees an unfulfilled
      order and reorders the same SKU at similar qty the next week.  Without
      shipment cross-check, BOTH orders look like real demand and the L13/L26
      baseline gets multiplied 2-3×.

    Detection (operates on the L26 window, requires hist[i] ≥ 100):
      • Compute ship_window = ships[i] + ships[i+1] (1-week lag tolerance)
      • If ship_window < 0.30 × hist[i] → order i is "unfulfilled"
      • Look ahead 1-2 weeks (j ∈ {i+1, i+2}) for next non-zero order
      • If hist[j] within ±15% of hist[i] → phantom reorder, zero hist[j]

    More permissive than F39 (±15% vs ±5%) because shipment evidence is
    itself the proof — we don't need tight qty matching when we can see
    the original order wasn't fulfilled.

    Empirical example (1864-SF8169, Amazon):
      LW_16: Ord 14328, Ship 432   → ship_window = 432+72 = 504 = 3.5%
      LW_15: Ord 14184, Ship 72    → next-order 14184 vs 14328 = 1.0% diff
      LW_14: Ord 0,     Ship 10368 (late catch-up of original order)
      → F41 fires: keep LW_16 (14328), zero LW_15 (14184).
      Customer placed two PO's; warehouse only ever shipped ~11k of 28k
      ordered.  Second order was a phantom reorder.

    Returns (hist, corrections-list).  Each correction:
      {kept_idx, zeroed_idx, kept_value, zeroed_value, ship_window,
       ship_pct, qty_diff_pct}
    """
    if not hist or not ships:
        return list(hist), []
    if len(hist) != len(ships):
        return list(hist), []
    if len(hist) < 3:
        return list(hist), []
    out = list(hist)
    n = len(out)
    L26_start = max(0, n - 26)
    corrections = []
    # Indices F47 already capped — F41 must skip these.  Otherwise the
    # capped (uniform) values look like phantom reorders even though they
    # are normalized rebuild-ramp orders, not duplicates.
    protected = set(protected_indices or [])
    i = L26_start
    while i < n - 1:
        if i in protected:
            i += 1
            continue
        v = float(out[i] or 0)
        if v < 100:
            i += 1
            continue
        # Ship window: ship[i] + ship[i+1] (allow 1-wk Amazon lag).
        ship_i  = float(ships[i] or 0)
        ship_i1 = float(ships[i+1] or 0) if i+1 < n else 0.0
        ship_window = ship_i + ship_i1
        ship_pct = ship_window / max(v, 1.0)
        # Order considered "unfulfilled" if < 30% shipped within lag window.
        if ship_pct >= 0.30:
            i += 1
            continue
        # Scan next 1-2 weeks for a similar-qty reorder (the phantom).
        for j in range(i + 1, min(i + 3, n)):
            if j in protected:
                continue
            vj = float(out[j] or 0)
            if vj < 100:
                continue
            qty_diff_pct = abs(vj - v) / max(v, 1.0)
            if qty_diff_pct <= 0.15:
                # Phantom — zero out the duplicate, keep the original.
                out[j] = 0
                corrections.append({
                    "kept_idx":     i,
                    "zeroed_idx":   j,
                    "kept_value":   round(v, 1),
                    "zeroed_value": round(vj, 1),
                    "ship_window":  round(ship_window, 1),
                    "ship_pct":     round(ship_pct, 3),
                    "qty_diff_pct": round(qty_diff_pct, 3),
                })
                break  # only zero ONE phantom per anchor
        i += 1
    return [int(round(x)) for x in out], corrections


def normalize_oos_rebuild_ramp(hist, ships):
    """
    F47 — OOS rebuild-ramp normalization (2026-05-07).

    Sister rule to F35.  F35 catches stockouts where the CUSTOMER stops
    ordering during the gap (order zeros for several weeks).  But many
    large retailers (Walmart, Target, Amazon) keep PLACING orders even
    when we cannot ship — they are rebuilding their on-hand position the
    moment shipments resume.  In that case:
        • ord_history shows continuous order activity (no zero run)
        • ship_history shows ≥2 consecutive zero ship weeks with ord>0
        • The 3-5 weeks AFTER ship resumes are inflated 1.5-3.5× normal
          because the customer is rebuilding safety stock

    Without correction, the L13W non-zero average gets pulled up by these
    rebuild orders, then multiplied by the seasonal profile → over-projection
    by 25-50%.  Example: FF12660 (Walmart) — rebuild ramp drove L13 nz-avg
    to 3,517/wk vs true normal pace of ~1,850/wk; AI projected 51k vs
    manual 40k for 26w (+27%).

    Detection (both required):
      (1) Ship-zero gap: ≥2 consecutive weeks where ships[i]+ships[i+1] < 30%
          of the order (1-week lag window; rules out normal Amazon/Walmart
          1-week order-to-ship lag as a false OOS signal; reduced from 3 to 2
          per planner feedback — FF15592 Walmart had exactly 2 zero-ship weeks
          and is the canonical case this rule was built for)
      (2) Pre-OOS pace established: ≥4 active ship weeks in the prior 13
          (we know what "normal" looked like)

    Action (VP-tuned, Option B):
      Cap each WITHIN-gap order at 1.3× the pre-OOS baseline.  This gives
      ~30% headroom for organic growth while still stripping the
      compounded rebuild ramp.  The FF12660 case had 4 weeks at
      4680→8640→9720→6840 vs a pre-OOS pace of ~1,800/wk; capping at
      1.3× = 2,340/wk strips the compounding without being draconian.
      Also cap the FIRST post-gap week if it runs ≥1.5× baseline (final
      catch-up burst when shipping resumes).  Cap fires only when an
      order exceeds 1.3× baseline.

    Returns:
      (normalized_history, corrections_list).  Each correction =
      {gap_start, gap_len, baseline, removed_total, weeks_capped}.
    """
    n = len(hist)
    if n < 8 or len(ships) != n:
        return hist, []

    out = [int(round(v)) for v in hist]
    s = [float(v or 0) for v in ships]
    corrections = []

    i = 0
    while i < n:
        # Find the next ship-zero run where the customer kept ordering.
        # Use 1-week lag window (matching F41): a week is OOS only if
        # ships[i] + ships[i+1] < 30% of the order — ruling out cases where
        # the order shipped normally one week later.
        if out[i] > 0:
            s_lag = s[i] + (s[i + 1] if i + 1 < n else 0.0)
            if s_lag / max(float(out[i]), 1.0) < 0.30:
                run_start = i
                while i < n and out[i] > 0:
                    s_lag_w = s[i] + (s[i + 1] if i + 1 < n else 0.0)
                    if s_lag_w / max(float(out[i]), 1.0) >= 0.30:
                        break
                    i += 1
                run_end = i  # first non-OOS index after the run
                run_len = run_end - run_start

                if run_len >= 2 and run_end < n:
                    # (2) Establish the pre-OOS pace from the prior shipping window.
                    pre_window_start = max(0, run_start - 13)
                    pre_ships = [s[k] for k in range(pre_window_start, run_start) if s[k] > 0]
                    if len(pre_ships) >= 4:
                        pre_sorted = sorted(pre_ships)
                        pre_median = pre_sorted[len(pre_sorted) // 2]
                        pre_avg    = sum(pre_ships) / len(pre_ships)
                        # Less aggressive of the two — avoid over-capping.
                        baseline = max(pre_median, pre_avg * 0.8)
                        # VP-tuned cap: 1.3× pre-OOS pace (gives ~30% organic-
                        # growth headroom while still stripping clear rebuild
                        # ramp.  Originally capped at exact baseline → too
                        # aggressive on FF12660-style cases.)
                        cap_level = baseline * 1.3
                        cap_int   = int(round(cap_level))

                        removed_total   = 0
                        weeks_capped    = 0
                        capped_indices  = []  # indices F47 touched — F39/F41 skip these

                        # Cap orders DURING the OOS gap (this is where the
                        # compounded rebuild happens — customer escalates weekly).
                        for idx in range(run_start, run_end):
                            if out[idx] > cap_level:
                                excess = out[idx] - cap_int
                                out[idx] = cap_int
                                removed_total += excess
                                weeks_capped  += 1
                                capped_indices.append(idx)

                        # Cap the first post-gap week IF it's still in catch-up
                        # mode (final rebuild burst when shipping resumes).
                        if run_end < n and out[run_end] > baseline * 1.5:
                            excess = out[run_end] - cap_int
                            out[run_end] = cap_int
                            removed_total += excess
                            weeks_capped  += 1
                            capped_indices.append(run_end)

                        if removed_total > 0:
                            corrections.append({
                                "gap_start":      run_start,
                                "gap_len":        run_len,
                                "baseline":       round(baseline, 1),
                                "removed_total":  removed_total,
                                "weeks_capped":   weeks_capped,
                                "capped_indices": capped_indices,
                            })
                continue
        i += 1

    return out, corrections


def attenuate_recent_spikes(hist, pos_data=None):
    """
    F43 — Recent-spike attenuation (2026-05-06).

    When the last 4 weeks of order history contain a spike that is far above
    the customer's prior baseline, treat it as a one-time anomaly rather than
    a new "lumpy event" pattern.  Without this, Croston's z-estimate inherits
    the spike and projects it forward as a recurring big event — turning a
    one-time stock-up into a giant W10/W11 forecast bunch with the rest of
    the 26 weeks zeroed out.

    Why this matters: Croston's classifier is sensitive to the L13 CV/zero%.
    A single huge week at the end of L13 (e.g., LW = 4× normal) inflates CV
    above 0.5 and routes a previously-steady customer into Croston's lumpy
    path.  The model then amplifies the spike (×2-3 via z) into a single
    forecast week far above any historical observation.

    Detection:
      • Compute median_pre = L26 non-zero median EXCLUDING the last 4 weeks
      • Require ≥ 8 non-zero values in that excluded baseline (else skip — too
        sparse to call any value an "outlier" reliably)
      • For each of the last 4 weeks (hist[-4:]):
          if value > 2.5 × median_pre  →  spike anomaly
          cap value to 2.0 × median_pre

    Capping (rather than zeroing) preserves the signal that activity occurred
    in those weeks; we just bring the magnitude in line with the customer's
    established pattern.  This lets a steady customer who has a genuinely
    rising trend still nudge the baseline up, without letting one-off spikes
    rewrite the model classification.

    Empirical example (1864-FF25895, Amazon):
      Prior 22w nz median = ~2400/wk.  LW = 12480, LW_1 = 9984
      Both > 2.5 × 2400 = 6000 → flagged.
      Capped to 2.0 × 2400 = 4800.
      L13 CV drops below 0.5; classifier routes to Holt-Winters → smooth
      weekly forecast distribution instead of W10/W11 bunch.

    Returns (hist, corrections-list).  Each correction:
      {idx, original, capped, median_pre, ratio}
    """
    if not hist or len(hist) < 12:
        return list(hist), []
    out = list(hist)
    n = len(out)
    last4_start = n - 4
    # Baseline: L26 nz EXCLUDING the last 4 weeks.
    L26_start = max(0, n - 26)
    baseline_window = [float(out[i] or 0) for i in range(L26_start, last4_start)]
    baseline_nz = sorted(x for x in baseline_window if x > 0)
    if len(baseline_nz) < 8:
        return out, []  # too thin to call anything an outlier
    median_pre = baseline_nz[len(baseline_nz) // 2]
    if median_pre <= 0:
        return out, []
    cap_threshold = 2.5 * median_pre
    cap_value     = 2.0 * median_pre

    # F49 — Sustained-acceleration guard for F43 (2026-05-08, planner callout).
    # F43 was designed for 1-off spikes (single big restock that shouldn't
    # rewrite the customer's run rate).  But when ≥3 of the last 4 weeks all
    # exceed cap_threshold, that's not a spike — it's a sustained run-rate
    # shift, often confirmed by Amazon POS.  Capping it kills genuine
    # acceleration and forecasts way under reality.
    #
    # Empirical callouts (planner-flagged 2026-05-08):
    #   1864-BB30930 (Amazon): hist[-4:] = [0, 3024, 3984, 4692]; baseline
    #     median ~1100. Three consecutive caps would clamp 3024/3984/4692
    #     down to ~2200 each, killing the +55% L4-vs-L13 acceleration that
    #     POS independently confirms (l4=2616/wk, l13=1686/wk).
    #   1864-BB22272 (Amazon): hist[-4:] = [0, 2328, 720, 3264]; bursts
    #     +103% accelerating per POS (l4=1279/wk, l13=631/wk).
    #
    # Skip rules:
    #   (a) ≥3 of last 4 would be capped → sustained, not 1-off; let through
    #   (b) ≥2 of last 4 capped AND Amazon POS l4/l13 ≥ 1.20 → POS confirms
    #       the acceleration is real; let through
    spike_count = sum(1 for i in range(last4_start, n)
                      if float(out[i] or 0) > cap_threshold)
    if spike_count >= 3:
        # F49b — Internal-spike check within sustained acceleration (2026-05-21).
        # When F49 fires because all (or most) of L4W is above the near-zero baseline
        # threshold, it may still contain an internal outlier: one week that is far
        # above the other three.  This happens when the item was dormant for most of
        # L26 (baseline median ~7/wk), then recently activated at 2,400/wk, with one
        # week spiking to 15,000+.  F49 sees "4/4 above 17.5" and calls it sustained
        # acceleration, but the 15,000 week is 6x the inner median of the other 3.
        # Without this sub-check, Croston's inherits the spike and over-projects.
        #
        # Rule: if max(L4W) > 5x inner-median(other 3), cap just the spike week to
        # 2x inner-median.  "Inner median" = mean of the 2nd and 3rd values when
        # the 4 weeks are sorted ascending (i.e., median of L4W excluding max/min).
        _l4_vals = sorted(float(out[i] or 0) for i in range(last4_start, n))
        if len(_l4_vals) == 4 and _l4_vals[1] > 0:
            _inner_med = (_l4_vals[1] + _l4_vals[2]) / 2.0
            if _inner_med > 0 and _l4_vals[3] > 5.0 * _inner_med:
                # Internal spike — cap just the outlier week(s) > 2x inner median
                _int_cap = 2.0 * _inner_med
                _int_corrections = []
                for i in range(last4_start, n):
                    v = float(out[i] or 0)
                    if v > _int_cap:
                        _int_corrections.append({
                            "idx":              i,
                            "original":         round(v, 1),
                            "capped":           round(_int_cap, 1),
                            "median_pre":       round(_inner_med, 1),
                            "ratio":            round(v / _inner_med, 2),
                            "f49b_internal":    True,
                        })
                        out[i] = int(round(_int_cap))
                if _int_corrections:
                    return out, _int_corrections
        return out, [{"f49_skip": "sustained_acceleration",
                      "spike_count": spike_count, "median_pre": round(median_pre, 1)}]
    if spike_count >= 2 and pos_data:
        l4_pos  = float(pos_data.get('l4w')  or pos_data.get('Avg_Units_Wk_L4w')  or 0)
        l13_pos = float(pos_data.get('l13w') or pos_data.get('Avg_Units_Wk_L13w') or 0)
        if l4_pos > 0 and l13_pos > 0 and (l4_pos / l13_pos) >= 1.20:
            return out, [{"f49_skip": "pos_confirmed_acceleration",
                          "spike_count": spike_count,
                          "l4_pos": round(l4_pos, 1), "l13_pos": round(l13_pos, 1),
                          "ratio": round(l4_pos / l13_pos, 2)}]

    corrections = []
    for i in range(last4_start, n):
        v = float(out[i] or 0)
        if v > cap_threshold:
            corrections.append({
                "idx":        i,
                "original":   round(v, 1),
                "capped":     round(cap_value, 1),
                "median_pre": round(median_pre, 1),
                "ratio":      round(v / median_pre, 2),
            })
            out[i] = int(round(cap_value))
    return out, corrections


def normalize_duplicate_orders(hist, protected_indices=None):
    """
    F39 — Duplicate-order run dedupe (2026-05-06).

    Customers (or their ordering systems) sometimes place the same large order
    multiple weeks in a row — buyer error, system glitch, or an automatic
    PO-replicator that fired twice.  These show up as a run of ≥2 consecutive
    weeks with the same exact (or near-identical) qty, where the qty is far
    above the customer's normal pattern.  None of the existing outlier rules
    catch this because each individual value isn't a freak — the *repetition*
    is.  Without dedup, the L13 non-zero average gets multiplied by the run
    length and the next 26-week forecast inherits 2-3× too much volume.

    Empirical example (1864-FF7618, Amazon):
      Order history (oldest → newest): [..., 5460, 5400, 0,0,0,0,0, 6480,
      6480, 6480, 0, 0, 60, 120, 360, 0]
      Three identical 6480-unit orders in weeks -8 to -6.  Shipments stayed
      flat near 0 — buyer placed three POs but only one was real.

    Detection (operates on the 52-week hist array):
      • Run length:    ≥ 2 consecutive non-zero weeks with values within ±5%
      • Cadence-aware magnitude gate (revised 2026-05-06):
          - Sparse customer  (<35% of L26 weeks have orders):
              run value ≥ 1.0× L26 nz-median excl run
              (for sparse customers the repetition itself is the anomaly)
          - Continuous customer (≥35% nz):
              run value ≥ 2.0× L26 nz-median excl run
              (strict gate prevents zeroing legitimate weekly bumps)
      • Absolute floor: run value ≥ 100 units (skip noise)

    Action — keep first-only:
      • Keep the OLDEST week of the run as-is (closest to the original
        customer demand event)
      • Replace all subsequent run weeks with 0 (buyer placed phantom POs)
      • Operates on L26 window so dedup also fixes L26 fallback baselines

    Returns (hist, corrections-list).  Corrections list each have:
      {start, length, value, median_excl, kept_idx}
    """
    if not hist or len(hist) < 2:
        return list(hist), []
    out = list(hist)
    corrections = []
    n = len(out)
    L26_start = max(0, n - 26)  # only look at L26 window
    # Indices F47 already normalized (rebuild-ramp caps).  Skip these in
    # both anchor selection and run extension — uniform cap values would
    # otherwise look like a duplicate-order run.
    protected = set(protected_indices or [])
    # Walk the L26 window forward, finding runs of near-equal consecutive values.
    i = L26_start
    while i < n - 1:
        if i in protected:
            i += 1
            continue
        v = float(out[i] or 0)
        if v < 100:
            i += 1
            continue
        # Find run extent: consecutive weeks within ±5% of v
        j = i + 1
        run_values = [v]
        while j < n:
            if j in protected:
                break
            vj = float(out[j] or 0)
            if vj < 1:
                break
            # Within ±5% tolerance (use the run's first value as anchor)
            if abs(vj - v) / max(v, 1.0) <= 0.05:
                run_values.append(vj)
                j += 1
            else:
                break
        run_len = j - i
        if run_len >= 2:
            # Magnitude + cadence gate (2026-05-06 fix):
            # The original 2.0× median gate was too strict for sparse-order
            # customers (e.g. 1864-FF7618: orders one ~5400-unit batch every
            # ~9 weeks).  When such a customer's "normal" order qty is the
            # *median*, any duplicate-order run of similar-sized batches
            # would fail a strict 2.0× gate even though the *repetition*
            # itself is the anomaly.  Solution: cadence-aware threshold —
            # for sparse customers (< 35% of L26 weeks have orders), the
            # repetition pattern is enough signal, so relax to 1.0×.  For
            # continuous customers (≥ 35% nz), keep the strict 2.0× gate
            # to avoid zeroing out legitimate weekly bumps.
            l26_excl_run = [float(out[k] or 0) for k in range(L26_start, n)
                            if k < i or k >= j]
            l26_excl_nz = sorted(x for x in l26_excl_run if x > 0)
            if l26_excl_nz:
                median_excl = l26_excl_nz[len(l26_excl_nz) // 2]
            else:
                median_excl = 0.0
            # Cadence: fraction of L26 (excl run) weeks with non-zero orders
            denom = len(l26_excl_run) if l26_excl_run else 1
            nz_fraction = len(l26_excl_nz) / denom
            # Sparse customer (<35% of weeks have orders): repetition
            # itself is the anomaly → relax magnitude gate to 1.0× median
            # Continuous customer (≥35% nz): repetition is normal →
            # keep strict 2.0× magnitude gate
            mag_threshold = 1.0 if nz_fraction < 0.35 else 2.0
            if median_excl > 0 and v >= mag_threshold * median_excl:
                # Keep first (oldest, hist[i]); zero the rest of the run.
                for k in range(i + 1, j):
                    out[k] = 0
                corrections.append({
                    "start":       i,
                    "length":      run_len,
                    "value":       round(v, 1),
                    "median_excl": round(median_excl, 1),
                    "nz_fraction": round(nz_fraction, 3),
                    "mag_thresh":  mag_threshold,
                    "kept_idx":    i,
                })
                i = j  # skip past the cleared run
                continue
        i += 1
    return [int(round(v)) for v in out], corrections


def detect_stockup_burnoff(hist, row, pos_data, big_mult=3.0):
    """
    F36 — Stock-up burn-off detection (Amazon-only) (2026-05-05).

    A customer that just received a stock-up shipment will NOT re-order until
    that inventory burns through at consumer-side POS rate.  The forecaster
    was reading the post-shipment quiet weeks as decline and projecting
    aggressively when in reality the customer is sitting on weeks of cover.

    Detect: a recent "big" shipment cluster in SHP history (≥ big_mult × POS
    weekly rate) followed by a quiet order-side period (avg post-cluster orders
    < 0.5 × POS rate).

    Compute: weeks-of-supply (WOS) = big_qty / pos_rate.  Subtract weeks
    elapsed since the cluster ended.  What remains is the suppression window
    for the AI forecast — those weeks should be ZERO because the customer
    won't replenish from us until their stores work through the cohort.

    Returns: dict with keys:
        applied        bool — fired or not
        wos_total      int  — total weeks-of-supply the cohort represents
        wos_remaining  int  — weeks still to burn (1..26)
        shipment_qty   int  — total qty in the stock-up cluster
        pos_rate       float — POS weekly rate used
        weeks_since_big int — weeks elapsed since the cluster ended
        cluster_len    int  — width of the stock-up cluster in weeks
    """
    if not pos_data:
        return {"applied": False}

    pos_l4  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
    pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
    pos_l26 = float(pos_data.get("Avg_Units_Wk_L26w") or 0)
    # Most-recent rate gets priority; fall back to broader windows
    if pos_l4 >= 1:
        pos_rate = pos_l4
    elif pos_l13 >= 1:
        pos_rate = pos_l13
    else:
        pos_rate = pos_l26
    if pos_rate < 1:
        return {"applied": False}

    # Pull last 26 weeks of shipments (oldest→newest within the window).
    # Window widened from 13 → 26 (2026-05-07): customer stockups that hold
    # >13 weeks of cover were missed by the narrower window (e.g. SF8169 had
    # a 28k unit stockup at W-11/-12 that was outside the L13W view).
    shipments = [float(row.get(c) or 0) for c in SHP_COLS[-26:]]
    if not shipments:
        return {"applied": False}

    # Active-orders guard (2026-05-07): if recent ORDERS run at ≥70% of POS
    # rate, the customer is actively replenishing — not in pure burn-off —
    # and F36 should not fire even if a recent shipment was big.  Catches
    # cases like FF12853 where a big shipment landed the same week as
    # ramping orders (L4 ord 2,600/wk vs POS 1,700/wk = 1.53× POS).
    if len(hist) >= 4:
        _l4_ord_avg = sum(hist[-4:]) / 4.0
        if _l4_ord_avg >= 0.70 * pos_rate:
            return {"applied": False}

    # Identify the LAST "big" shipment week.  Threshold: big_mult × POS rate
    # (so a stock-up that covers ≥3w of consumer demand qualifies).
    big_threshold = big_mult * pos_rate
    last_big_idx = -1
    for i in range(len(shipments) - 1, -1, -1):
        if shipments[i] >= big_threshold:
            last_big_idx = i
            break
    if last_big_idx < 0:
        return {"applied": False}

    # Expand the cluster backwards: grab consecutive prior weeks that are also
    # elevated (≥ big_threshold/2) — handles split shipments across 2-3 weeks.
    cluster_start = last_big_idx
    while (cluster_start > 0 and
           shipments[cluster_start - 1] >= big_threshold * 0.5):
        cluster_start -= 1

    cluster_qty = sum(shipments[cluster_start:last_big_idx + 1])
    cluster_len = last_big_idx - cluster_start + 1
    weeks_since_big = len(shipments) - 1 - last_big_idx  # 0 = cluster ended this week

    # Sanity check: orders since the cluster ended should be quiet.  If the
    # customer is already re-ordering at ≥ 0.5× POS rate, they are NOT in
    # burn-off — don't suppress.
    if weeks_since_big >= 1:
        post_orders = hist[-weeks_since_big:]
        if post_orders:
            avg_post = sum(post_orders) / len(post_orders)
            if avg_post > 0.5 * pos_rate:
                return {"applied": False}

    wos_total     = cluster_qty / pos_rate
    wos_remaining = max(0, int(round(wos_total - weeks_since_big)))

    # Only fire if there's a meaningful remaining window (≥2 weeks).
    if wos_remaining < 2:
        return {"applied": False}

    return {
        "applied":         True,
        "wos_total":       int(round(wos_total)),
        "wos_remaining":   min(wos_remaining, 26),
        "shipment_qty":    int(cluster_qty),
        "pos_rate":        round(pos_rate, 1),
        "weeks_since_big": weeks_since_big,
        "cluster_len":     cluster_len,
    }


def nz_rate(history, window=26):
    """Fraction of non-zero weeks over the last `window` weeks."""
    h = history[-window:]
    if not h:
        return 0.0
    return sum(1 for v in h if v > 0) / len(h)


def apply_oh_shortfall_adjustment(row, fcst):
    """
    F37 — Forward inventory-shortfall adjustment (2026-05-05).

    Reads anticipated on-hand for the next 26 weeks from QB columns
    Inv_Wk1..Inv_Wk26 (Projections table).  These values have the current
    AI projection ALREADY DEDUCTED, so Inv_WkN < 0 means we'd run out
    that week if we tried to ship the planned forecast.

    For weeks where we'd run short:
      • Cap that week's AI projection at what we can actually ship
      • Track the unmet demand as a backlog cohort that piles into future weeks
      • Decay schedule (matches F34/F35): each cohort loses 25% of its
        recoverable share per week of non-shipment
            age 1: 75% recoverable
            age 2: 50% recoverable
            age 3: 25% recoverable
            age 4+: 0% recoverable (drop the cohort — fully lost)
      • A week's "real demand" = original AI forecast + Σ(recoverable backlog
        from prior unmet cohorts).  We ship up to min(real demand, on-hand).
      • OH carries forward correctly: if we shipped less in week N, that
        savings boosts available OH in week N+1 and beyond.

    Returns: (adjusted_fcst, list of {week, original, adjusted, oh_avail,
    backlog_in, lost} dicts).  If Inv_Wk columns are missing or all zero,
    returns the original forecast unchanged.

    Tries multiple naming conventions for the QB column name.
    """
    # Pull Inv_Wk1..Inv_Wk26.  Try a few naming conventions so we work
    # whether QB returns "Inv_Wk1", "Inv Wk1", or "InvWk1".
    inv_oh = []
    for i in range(1, 27):
        v = (row.get(f"Inv_Wk{i}")
             or row.get(f"Inv Wk{i}")
             or row.get(f"InvWk{i}"))
        if v is None or v == "":
            return list(fcst), []
        try:
            inv_oh.append(float(v))
        except (TypeError, ValueError):
            return list(fcst), []

    # Sanity: if every Inv value is 0 or positive AND no negatives at all,
    # there's nothing to adjust — short-circuit.
    if all(v >= 0 for v in inv_oh):
        return list(fcst), []

    adjusted = list(fcst)
    cohorts = []   # list of [qty, age]
    saved   = 0.0  # OH preserved by shipping less than fcst in prior weeks
    adjustments = []

    for w in range(min(26, len(fcst))):
        # Available OH at start of week w (before shipping)
        # = Inv_Wk[w] + AI_orig[w]   (undo this week's deduction in Inv)
        # + saved                     (extra OH from prior under-shipments)
        available = inv_oh[w] + float(fcst[w]) + saved

        # Recoverable backlog from prior unmet cohorts
        backlog = sum(q * max(0.0, 1.0 - 0.25 * a) for q, a in cohorts)

        # This week's "real" demand intent = original forecast + recovered backlog
        real_demand = float(fcst[w]) + backlog

        if available >= real_demand:
            ship = real_demand
            cohorts = []        # fully fulfilled — clear all cohorts
        else:
            ship = max(0.0, available)
            unmet = real_demand - ship
            # Age existing cohorts by one week; drop those that hit age 4+
            cohorts = [[q, a + 1] for q, a in cohorts if a + 1 < 4]
            if unmet > 0:
                cohorts.append([unmet, 1])

        adjusted[w] = int(round(ship))
        delta = float(fcst[w]) - adjusted[w]   # +ve = saved OH; -ve = spent extra
        saved += delta

        if adjusted[w] != fcst[w]:
            adjustments.append({
                "week":     w + 1,
                "original": int(fcst[w]),
                "adjusted": adjusted[w],
                "oh_avail": int(available),
                "backlog":  int(round(backlog)),
            })

    # Sum of demand permanently lost (cohorts that decayed past age 4 or
    # were trimmed below recoverable share).  Approximation: original_total -
    # adjusted_total - any cohort qty still rolling at the end (those still
    # have a recoverable share that may carry beyond W26).
    return adjusted, adjustments


def detect_iso(history):
    """
    Detect an Initial Stocking Order (ISO) pattern.

    An ISO occurs when a retailer takes a new item for the first time:
    they place a large stocking order, then pull low-to-no quantities for
    several weeks while the product ships to stores and sales develop.

    Signature in order history (oldest → newest):
      • First non-zero week is ≥ ISO_SPIKE_RATIO × the subsequent trickle average.
      • There must be some trickle orders after it (not a one-time purchase).

    Returns a dict:
      is_iso          — bool
      iso_week_idx    — index of the ISO in history (0 = oldest week in 52w window)
      iso_qty         — quantity of the ISO
      trickle_avg     — avg of the 8 weeks immediately after the ISO (non-zero only)
      post_iso_avg    — avg of ALL post-ISO non-zero weeks
      weeks_since_iso — weeks elapsed from ISO to the end of history (≈ now)
      in_settle       — True if still within ISO_SETTLE_WEEKS of the ISO
    """
    h = [float(v) for v in history]
    n = len(h)

    # Find the very first non-zero week (earliest = oldest in window)
    first_nz_idx = next((i for i, v in enumerate(h) if v > 0), None)
    if first_nz_idx is None:
        return {"is_iso": False}

    iso_qty = h[first_nz_idx]

    # Trickle window: up to 8 weeks immediately after the ISO
    trickle_vals = [v for v in h[first_nz_idx + 1: first_nz_idx + 9] if v > 0]
    trickle_avg  = sum(trickle_vals) / len(trickle_vals) if trickle_vals else 0

    # All non-zero weeks after the ISO
    post_vals = [v for v in h[first_nz_idx + 1:] if v > 0]
    post_avg  = sum(post_vals) / len(post_vals) if post_vals else 0

    # Compare against trickle avg first; fall back to broader post-ISO avg
    compare_avg = trickle_avg if trickle_avg else post_avg

    # Must have subsequent orders AND spike is large enough to qualify
    is_iso = bool(compare_avg > 0 and iso_qty >= ISO_SPIKE_RATIO * compare_avg)

    weeks_since_iso = (n - 1) - first_nz_idx

    return {
        "is_iso":          is_iso,
        "iso_week_idx":    first_nz_idx,
        "iso_qty":         iso_qty,
        "trickle_avg":     round(trickle_avg, 1),
        "post_iso_avg":    round(post_avg, 1),
        "weeks_since_iso": weeks_since_iso,
        "in_settle":       is_iso and weeks_since_iso <= ISO_SETTLE_WEEKS,
    }


def _cluster_order_events(h52, gap=2):
    """
    Group consecutive (or near-consecutive) non-zero weeks into order events.
    Two non-zero weeks are in the same event if the gap between them is <= `gap` weeks.
    Returns a list of dicts: {start, end, qty, center}
    where center = (start+end)/2 (float), qty = sum of all weeks in the cluster.

    This prevents a run of weekly small replenishment deliveries from being
    treated as many separate orders when computing inter-order intervals.
    """
    nz_pos = [(i, h52[i]) for i in range(len(h52)) if h52[i] > 0]
    if not nz_pos:
        return []

    events = []
    cluster_start = nz_pos[0][0]
    cluster_qty   = nz_pos[0][1]
    cluster_end   = nz_pos[0][0]

    for i in range(1, len(nz_pos)):
        pos, qty = nz_pos[i]
        if pos - cluster_end <= gap:
            # Extend current cluster
            cluster_qty += qty
            cluster_end  = pos
        else:
            # Save old cluster, start new one
            events.append({
                "start":  cluster_start,
                "end":    cluster_end,
                "qty":    cluster_qty,
                "center": (cluster_start + cluster_end) / 2,
            })
            cluster_start = pos
            cluster_qty   = qty
            cluster_end   = pos

    events.append({
        "start":  cluster_start,
        "end":    cluster_end,
        "qty":    cluster_qty,
        "center": (cluster_start + cluster_end) / 2,
    })
    return events


def compute_account_cadences(rows):
    """
    Pre-compute per-account typical ordering interval by looking at ALL items
    for each account in the current scope.

    For each account (grouped by the prefix before '-' in Acct_MStyle_Key_),
    we collect the event-based avg_interval from every sparse item that has
    >= 2 clustered events in L52W.  We then return the MEDIAN of those
    per-item intervals as the account cadence.

    Median is used rather than mean so that a few very-long-gap items
    (e.g. once-a-year seasonal loads) don't skew the estimate upward.

    Returns: dict  {acct_prefix (str): median_interval_weeks (float)}
    """
    from statistics import median as _median

    acct_ivs: dict = {}   # acct_prefix -> [avg_interval, ...]

    for row in rows:
        key    = row.get("Acct_MStyle_Key_", "")
        prefix = key.split("-")[0] if "-" in key else key
        hist   = get_history(row)

        # Only sparse items inform the account cadence
        if nz_rate(hist, 26) >= DENSE_THRESHOLD:
            continue

        events = _cluster_order_events(hist, gap=2)
        if len(events) < 2:
            continue

        intervals = [events[k + 1]["start"] - events[k]["start"]
                     for k in range(len(events) - 1)]
        avg_iv = sum(intervals) / len(intervals)
        acct_ivs.setdefault(prefix, []).append(avg_iv)

    return {prefix: _median(ivs) for prefix, ivs in acct_ivs.items() if ivs}


# ─── F1/F2: Mstyle-family + Customer-baseline indexes ─────────────────────────
# Built once per run (after Phase 1) from in-scope rows.  Used by the
# Inactive/No-History branch of forecast_record() as history-only signals to
# replace zero forecasts with a defensible, data-driven baseline — never uses
# manual projection values as input.
MSTYLE_FAMILY_INDEX = {}   # Mstyle  -> {"median_wk_rate": float, "n": int}
CUST_BASELINE_INDEX = {}   # Cust    -> {"median_wk_rate": float, "n": int}
GLOBAL_WK_RATE      = 10.0  # fallback scalar when a cust has no active peers


def _build_mstyle_family_index(rows):
    """
    For each Mstyle, compute the median L52 weekly order rate across sibling
    keys (same Mstyle, different Acct_MStyle_Key_).  Sibling keys with zero
    L52 activity are excluded so a brand-new SKU cohort doesn't pull the
    median to zero.  The median resists outliers from a single very large
    customer.  No manual projection values touched.
    """
    from statistics import median as _median
    bucket = {}   # mstyle -> [wk_rate, ...]
    for r in rows:
        mstyle = r.get("Mstyle", "")
        if not mstyle: continue
        hist   = get_history(r)
        wk     = sum(hist) / 52.0
        bucket.setdefault(mstyle, []).append(wk)
    idx = {}
    for mstyle, rates in bucket.items():
        active = [v for v in rates if v > 0]
        if active:
            idx[mstyle] = {"median_wk_rate": float(_median(active)),
                           "n":              len(active)}
    return idx


def _build_switchover_index(rows):
    """
    F70 -- Switchover variant conflict detection.

    For every (acct, base_mstyle) pair where a variant suffix style exists in
    the same account (e.g. FF8654 + FF8654EC at account 1864), builds a map of
    which weeks the VARIANT has demand activity -- either manual projections > 0
    OR open customer PO qty > 0.  The base style AI forecast will zero those
    weeks (F70) because the retailer can only order one or the other in a given
    week.

    Returns:
        dict[str, dict[int, list[str]]]
            base_key (e.g. "1864-FF8654")
            -> {week_index_0based: [variant_mstyle, ...]}

    Only weeks with actual variant activity (man_prj > 0 or opn_w > 0) appear
    in the inner dict.  An empty dict for a key means no conflict found.
    """
    # Step 1 -- index every row by Acct_MStyle_Key_ for O(1) parent lookup
    row_by_key = {r.get("Acct_MStyle_Key_", ""): r for r in rows}

    # Step 2 -- for each variant row, find its base key and record active weeks
    result         = {}   # base_key   -> {week_idx: [variant_mstyles]}
    variant_result = {}   # variant_key -> {week_idx: [base_mstyle]}

    for vrow in rows:
        vkey = vrow.get("Acct_MStyle_Key_", "")
        vms  = vrow.get("Mstyle", "")
        if "-" not in vkey or not vms:
            continue

        # Detect suffix -- longest match wins (COS before C, etc.)
        sfx_len = 0
        for sfx in sorted(SWITCHOVER_SUFFIXES, key=len, reverse=True):
            if vms.upper().endswith(sfx):
                sfx_len = len(sfx)
                break
        if not sfx_len:
            continue

        base_ms  = vms[:-sfx_len]
        acct_pfx = vkey.split("-", 1)[0]
        base_key = f"{acct_pfx}-{base_ms}"

        # Only proceed if the base style is actually in scope this run
        if base_key not in row_by_key:
            continue

        # Determine which weeks the variant has demand activity
        man_prj = [float(vrow.get(c) or 0) for c in ORIG_PRJ_COLS]
        opn_w   = [float(vrow.get(c) or 0) for c in OPN_COLS]

        for wi in range(26):
            if man_prj[wi] > 0 or opn_w[wi] > 0:
                result.setdefault(base_key, {}).setdefault(wi, []).append(vms)

    # Step 3 -- build reverse map: variant should be zeroed for weeks BEFORE
    # the switchover (i.e. weeks where the base style is still active).
    for base_key, week_map in result.items():
        if not week_map:
            continue
        first_sw_week = min(week_map.keys())
        # Parse variant key from base_key: base_key is "acct-base_ms";
        # we need to find the corresponding variant key(s) in row_by_key.
        acct_pfx  = base_key.split("-", 1)[0]
        base_ms_b = base_key.split("-", 1)[1] if "-" in base_key else ""
        # Find all variant keys whose base resolves to this base_key
        for sfx in sorted(SWITCHOVER_SUFFIXES, key=len, reverse=True):
            variant_key = f"{acct_pfx}-{base_ms_b}{sfx}"
            if variant_key in row_by_key:
                for wi in range(first_sw_week):
                    variant_result.setdefault(variant_key, {}).setdefault(wi, []).append(base_ms_b)

    return result, variant_result


def _build_cust_baseline_index(rows):
    """
    Per-customer median L52 weekly order rate across that customer's active
    SKUs (rows with L52 > 0).  Small customers that buy in small qty get a
    small baseline; big customers (Amazon, Walmart) get a large one.
    Returns (index, global_median) so forecast_record() can scale between
    them.  History-only.
    """
    from statistics import median as _median
    bucket = {}   # cust -> [wk_rate for each of their active SKUs]
    for r in rows:
        cust = (r.get("Customr_Name") or "").strip()
        if not cust: continue
        hist = get_history(r)
        wk   = sum(hist) / 52.0
        bucket.setdefault(cust, []).append(wk)
    idx = {}
    all_active = []
    for cust, rates in bucket.items():
        active = [v for v in rates if v > 0]
        if active:
            idx[cust] = {"median_wk_rate": float(_median(active)),
                         "n":              len(active)}
            all_active.extend(active)
    glob = float(_median(all_active)) if all_active else 10.0
    return idx, glob


def _family_rate_for(row):
    """F1 — median weekly rate of sibling keys with same Mstyle.  Excludes
    self-contribution by construction (only active siblings, and a zero-
    history record contributes 0 which is filtered out of the 'active' list
    used to build the median)."""
    ms = (row.get("Mstyle") or "").strip()
    if not ms: return 0.0, 0
    entry = MSTYLE_FAMILY_INDEX.get(ms)
    if not entry: return 0.0, 0
    return entry["median_wk_rate"], entry["n"]


def _cust_rate_for(row):
    cust = (row.get("Customr_Name") or "").strip()
    entry = CUST_BASELINE_INDEX.get(cust)
    if not entry: return 0.0, 0
    return entry["median_wk_rate"], entry["n"]


# F5 — PT_Item_Status / Status_Cust EOL token detection helpers.
_EOL_TOKENS      = ("DISC", "DEL", "LIQ", "END", "OBSOLETE", "PHASE", "SUNSET")
_LAUNCHING_TOKENS = ("LAUNCH", "NEW", "PILOT")

def _is_eol(row):
    it = (row.get("PT_Item_Status") or "").upper()
    sc = (row.get("Status_Cust")    or "").upper()
    return any(t in it or t in sc for t in _EOL_TOKENS)

def _is_launching(row):
    it = (row.get("PT_Item_Status") or "").upper()
    return any(t in it for t in _LAUNCHING_TOKENS)


def _get_shp_history(row):
    """52-week shipment history (oldest→newest) used by F8 corroboration."""
    return [float(row.get(c) or 0) for c in SHP_COLS]


def sparse_intermittent_forecast(history, mp, account_interval=None, is_offprice=False):
    """
    For items that order infrequently (non-zero rate < DENSE_THRESHOLD):
    Instead of spreading demand across every week, this model:
      1. Clusters consecutive non-zero weeks into "order events" (≤2w gap = same event)
         so a run of daily replenishment deliveries isn't mistaken for many orders.
      2. Measures avg event qty from L26W events (falls back to L52W if < 2 events).
      3. Measures avg inter-event interval from L52W event starts, then blends with
         the account-level median cadence (account_interval) so items with thin history
         are anchored to the customer's known ordering rhythm.
      4. Projects forward at that interval, aligned to the phase of the last event.
    No smoothing — the lumpy, batch character of the orders is preserved.

    Blending weights by number of L52W events:
      1 event  → 10% item / 90% account  (almost no item-specific signal)
      2 events → 40% item / 60% account
      3 events → 55% item / 45% account
      4 events → 65% item / 35% account
      5 events → 73% item / 27% account
      6+ events→ 80% item / 20% account  (strong item history, light account anchor)
    """
    h52 = [float(v) for v in history[-52:]]
    events = _cluster_order_events(h52, gap=2)

    if not events:
        return [0] * 26, 0, {"model": "Sparse Intermittent", "avg_interval_wk": None}

    # ── Average event qty ─────────────────────────────────────────────────
    # Prefer L26W events for recency; fall back to L52W if < 2 events in L26W.
    # Use spike-resistant average: exclude events > 3× the median to prevent
    # one-off large orders (seasonal builds, reset loads) from inflating qty.
    l26_cutoff = len(h52) - 26
    l26_events = [e for e in events if e["start"] >= l26_cutoff]
    if len(l26_events) >= 2:
        src_events = l26_events
        qty_src = "L26W"
    else:
        src_events = events
        qty_src = "L52W"

    qtys = sorted(e["qty"] for e in src_events)
    median_qty = qtys[len(qtys) // 2]
    normal_qtys = [q for q in qtys if q <= median_qty * 3]
    avg_qty = (sum(normal_qtys) / len(normal_qtys)) if normal_qtys else (sum(qtys) / len(qtys))

    # F9 — For higher-volume sparse items (annual L52 total > 15k units), use the
    # MAX of the L13/L26/L52 non-zero weekly averages as event qty when it's
    # higher than the cluster-derived avg. Prevents a quiet L13W from under-
    # projecting genuinely high-volume lumpy items.
    _l52_total_f9s = sum(h52)
    if _l52_total_f9s > 0:   # Fix C (2026-05-24): apply MAX baseline to all active sparse items
        _l13_nz_s = [v for v in history[-13:] if v > 0]
        _l26_nz_s = [v for v in history[-26:] if v > 0]
        _l52_nz_s = [v for v in h52 if v > 0]
        _cands = []
        if _l13_nz_s: _cands.append(sum(_l13_nz_s) / len(_l13_nz_s))
        if _l26_nz_s: _cands.append(sum(_l26_nz_s) / len(_l26_nz_s))
        if _l52_nz_s: _cands.append(sum(_l52_nz_s) / len(_l52_nz_s))
        if _cands:
            _max_avg = max(_cands)
            if _max_avg > avg_qty:
                avg_qty = _max_avg

    # ── Average interval between event starts (L52W) ──────────────────────
    if len(events) >= 2:
        intervals = [events[k + 1]["start"] - events[k]["start"]
                     for k in range(len(events) - 1)]
        item_interval = sum(intervals) / len(intervals)
    else:
        # Only one event — use account cadence if available, else conservative fallback
        item_interval = None

    # ── Blend item interval with account-level cadence ────────────────────
    # The account cadence is the median ordering interval observed across ALL
    # items for this customer, giving us a strong prior even when item history
    # is thin.  Item-specific evidence gets more weight as event count grows.
    n_ev = len(events)
    if account_interval and item_interval:
        # Weight schedule: 10/90 → 40/60 → 55/45 → 65/35 → 73/27 → 80/20
        item_wt = min(0.80, max(0.10, 0.10 + 0.14 * (n_ev - 1)))
        acct_wt = 1.0 - item_wt
        avg_interval = item_interval * item_wt + account_interval * acct_wt
        interval_src = f"blended ({item_wt:.0%} item/{acct_wt:.0%} acct)"
    elif account_interval:
        avg_interval = account_interval   # no item signal → full account prior
        interval_src = "account cadence (no item events)"
    elif item_interval:
        avg_interval = item_interval      # no account data → item only
        interval_src = "item only"
    else:
        avg_interval = min(max(len(h52) - events[0]["start"], 6), 13)
        interval_src = "fallback"

    # L8W recency overlay (2026-05-05) — applied to BOTH avg_qty and
    # avg_interval in Sparse Intermittent.  Same 50/30/20 blend across
    # L8W/L13W/L26W non-zero averages, blended 60/40 against existing values.
    # Uses raw weekly history (not event clusters) so it captures the most
    # recent observed order sizes and gaps directly.
    _l8_nz_si  = [float(v) for v in history[-8:]  if float(v) > 0]
    _l13_nz_si = [float(v) for v in history[-13:] if float(v) > 0]
    _l26_nz_si = [float(v) for v in history[-26:] if float(v) > 0]
    if (len(_l8_nz_si) >= 1 and len(_l13_nz_si) >= 2 and len(_l26_nz_si) >= 3
            and avg_qty > 0):
        _l8_avg_si  = sum(_l8_nz_si)  / len(_l8_nz_si)
        _l13_avg_si = sum(_l13_nz_si) / len(_l13_nz_si)
        _l26_avg_si = sum(_l26_nz_si) / len(_l26_nz_si)
        _qty_blend  = 0.50 * _l8_avg_si + 0.30 * _l13_avg_si + 0.20 * _l26_avg_si
        if 0.5 <= (_qty_blend / avg_qty) <= 2.0:
            _new_avg_qty = 0.60 * _qty_blend + 0.40 * avg_qty
            if abs(_new_avg_qty - avg_qty) / avg_qty >= 0.05:
                avg_qty = _new_avg_qty
    # Inter-event interval — recency-weight the avg gap between non-zero
    # weeks across L8W/L13W/L26W.  Falls back to the existing avg_interval
    # when a window can't compute a reliable gap.
    def _avg_gap_si(vals):
        nz_idx = [i for i, v in enumerate(vals) if float(v) > 0]
        if len(nz_idx) >= 2:
            gaps = [nz_idx[i+1] - nz_idx[i] for i in range(len(nz_idx) - 1)]
            return sum(gaps) / len(gaps)
        elif len(nz_idx) == 1 and len(vals) >= 2:
            return float(len(vals))
        return None
    _g8_si  = _avg_gap_si(history[-8:])
    _g13_si = _avg_gap_si(history[-13:])
    _g26_si = _avg_gap_si(history[-26:])
    if avg_interval and _g13_si and _g26_si:
        _g8_use     = _g8_si if _g8_si else _g13_si
        _gap_blend  = 0.50 * _g8_use + 0.30 * _g13_si + 0.20 * _g26_si
        if 0.5 <= (_gap_blend / avg_interval) <= 2.0:
            _new_interval = 0.60 * _gap_blend + 0.40 * avg_interval
            if abs(_new_interval - avg_interval) / avg_interval >= 0.05:
                # GUARD (2026-05-05): same bi-weekly boundary guard as
                # crostens(). Don't reclassify the item across p_final=2,
                # since downstream pattern-enforcement and snap-mp combine
                # to zero out low-volume records when the cadence flips.
                _old_step = max(1, round(avg_interval))
                _new_step = max(1, round(_new_interval))
                _crosses  = ((_old_step == 2) != (_new_step == 2))
                if not _crosses:
                    avg_interval = _new_interval

    # ── Phase: weeks since the last event ended ───────────────────────────
    last_event_end   = events[-1]["end"]
    weeks_since_last = len(h52) - last_event_end   # weeks elapsed before W1

    # ── Next order week (1-indexed) ───────────────────────────────────────
    weeks_into_cycle = weeks_since_last % avg_interval
    weeks_until_next = avg_interval - weeks_into_cycle
    if weeks_until_next >= avg_interval:
        weeks_until_next = avg_interval
    first_order_w1 = max(1, round(weeks_until_next))

    # ── Build 26-week forecast ────────────────────────────────────────────
    forecast = [0] * 26
    pos  = first_order_w1 - 1          # 0-indexed
    step = max(1, round(avg_interval))
    while pos < 26:
        forecast[pos] = snap(avg_qty, mp)
        pos += step

    # R2 — Sparse Intermittent L26 ceiling (2026-04-22).  Observed +38% overall
    # Sparse overshoot (+594K) concentrated at off-price/lumpy retailers.  Cap
    # total 26w forecast at L26W all-weeks avg × 26 × 1.5.  L26W (not L13W)
    # because Sparse items often have L13 all-zero; L26 is the true recent rate.
    # S1 (2026-04-22): For off-price retailers (Burlington, Ross, TJ Maxx, Kohl's,
    # Ollie, Big Lots, Five Below, etc.) tighten ceiling to × 0.8.  Off-price buys
    # are opportunistic — planner-manual reflects "one-and-done" behavior.
    _r2_applied = False
    _r2_ceiling_mult = 0.8 if is_offprice else 1.5
    _l26_all_r2 = h52[-26:]
    _l26_avg_r2 = (sum(_l26_all_r2) / 26) if _l26_all_r2 else 0
    if _l26_avg_r2 > 0:
        _r2_ceiling = _l26_avg_r2 * 26 * _r2_ceiling_mult
        _cur_total = sum(forecast)
        if _cur_total > _r2_ceiling:
            _scale_r2 = _r2_ceiling / _cur_total
            forecast = [snap(v * _scale_r2, mp) if v > 0 else 0 for v in forecast]
            _r2_applied = True

    meta = {
        "model":            "Sparse Intermittent",
        "avg_interval_wk":  round(avg_interval, 1),
        "interval_src":     interval_src,
        "avg_qty":          round(avg_qty, 1),
        "qty_src":          qty_src,
        "weeks_since_last": weeks_since_last,
        "n_events_l52":     n_ev,
        "n_events_l26":     len(l26_events),
        "account_interval": round(account_interval, 1) if account_interval else None,
    }
    if _r2_applied:
        meta.setdefault("drivers", []).append(
            f"R2 Sparse L26 ceiling: L26_avg {_l26_avg_r2:.0f}/wk × 26 × 1.5 "
            f"→ total capped"
        )
    return forecast, round(avg_qty, 1), meta


def classify(history):
    """
    Classify demand pattern.
    Returns: 'inactive' | 'sparse_intermittent' | 'active'
    Simple: if the customer ordered anything in L13W, they're active.
    The Seasonal Baseline model handles all active items regardless of
    how many weeks they've been ordering or how variable the sizes are.

    F6a (renamed from F6 2026-05-21 to break tag collision) -- Inactive-with-
    Activity reclassification. Before declaring an item "inactive" (L13W all
    zeros), look further back. If the item has meaningful L26W or L52W order
    activity, re-route it to a heuristic path instead of zeroing its forecast.
    """
    l13 = history[-13:]
    if sum(l13) == 0:
        # F6a -- check further-back windows before giving up on the item.
        l26 = history[-26:]
        l52 = history[-52:] if len(history) >= 52 else history
        l26_nz_cnt = sum(1 for v in l26 if v > 0)
        l52_nz_cnt = sum(1 for v in l52 if v > 0)
        if l26_nz_cnt >= 4 or l52_nz_cnt >= 8:
            return "sparse_intermittent"
        return "inactive"
    return "active"


def _impute_ly_oos_gaps(l52, n=26):
    """F55 — LY OOS-gap imputation (2026-05-08).

    Scan LY portion of the 52-week history (positions 0..n-1, oldest→newest)
    for runs of ≥3 consecutive zero weeks bounded on BOTH sides by non-zero
    activity.  These look like stockouts — the customer wanted product but
    couldn't get it — not real seasonal demand.  Without imputation, the
    seasonal profile inherits these zeros as low indices for those calendar
    positions and projects the OOS forward into next year's forecast.

    Imputation policy: replace each zero in the run with the mean of the
    most-recent non-zero weeks on either side (or with the L52 non-zero
    mean if either bound is missing).  Only touches LY positions; current
    cycle is left as-is (recent zeros may be real cadence variance).

    Empirical example (Petsmart 16553-FF7258, 2026-05-08 callout):
      LY positions 18-21 (= 30-33 weeks ago) were [0, 0, 0, 0] surrounded
      by active weeks (~1500/wk each side).  Without imputation, forecast
      W19-W22 was suppressed to ~0.82× baseline (×4 weeks = ~1.9k units
      lost).  Imputed value ~1500/wk restores the seasonal profile to
      ~1.0× baseline at those positions.
    """
    out = list(l52)
    if len(out) < n + 1:
        return out
    nz_all = [v for v in out if v > 0]
    if len(nz_all) < 8:
        return out  # too sparse — can't trust imputation
    fallback = sum(nz_all) / len(nz_all)
    i = 0
    while i < n:
        if out[i] == 0:
            j = i
            while j < n and out[j] == 0:
                j += 1
            run_len = j - i
            if run_len >= 3:
                # Look for non-zero context on both sides (within full L52)
                prev_nz = next((v for v in reversed(out[:i]) if v > 0), None)
                next_nz = next((v for v in out[j:] if v > 0), None)
                if prev_nz is not None and next_nz is not None:
                    impute = (prev_nz + next_nz) / 2.0
                elif prev_nz is not None or next_nz is not None:
                    impute = fallback   # one bound only — use long-term mean
                else:
                    impute = None       # leading/trailing run — leave alone
                if impute is not None and impute > 0:
                    for k in range(i, j):
                        out[k] = impute
            i = j
        else:
            i += 1
    return out


def seasonal_profile(history, n=26):
    """
    26-week multiplicative seasonal indices built only from active history
    (trims leading zeros so pre-launch gaps don't corrupt the profile).
    Each slot blends 70% recent cycle / 30% prior cycle, normalized to
    mean=1.0, floored at 0.25 so no week is wiped out entirely.

    F55: LY OOS-gap imputation runs FIRST so multi-week stockout gaps
    don't propagate forward as next-year low forecasts.
    """
    l52 = [float(v) for v in history[-52:]]
    # F55 — impute LY OOS gaps (≥3 consecutive zeros surrounded by activity)
    l52 = _impute_ly_oos_gaps(l52, n=n)
    first_active = next((i for i, v in enumerate(l52) if v > 0), None)
    if first_active is None:
        return [1.0] * n

    active = l52[first_active:]
    m = len(active)
    mean_active = float(np.mean(active)) if float(np.mean(active)) > 0 else 1.0

    raw = []
    for i in range(n):
        recent_idx = m - n + i
        recent_val = active[recent_idx] / mean_active if recent_idx >= 0 else 1.0
        prior_idx  = recent_idx - n
        if prior_idx >= 0:
            prior_val = active[prior_idx] / mean_active
            combined  = recent_val * 0.7 + prior_val * 0.3
        else:
            combined = recent_val
        raw.append(combined)

    pmean = float(np.mean(raw)) if float(np.mean(raw)) > 0 else 1.0
    profile = [r / pmean for r in raw]
    return [max(p, 0.25) for p in profile]


# holt_winters() removed 2026-05-21 -- audit confirmed the function was
# defined but NEVER CALLED anywhere.  Dense buyers route through
# seasonal_baseline() which applies all the same concepts (level baseline,
# seasonal profile, caps) plus all the post-2025 calibration rules.
# HW_ALPHA / HW_BETA constants also removed (2026-05-23 audit) -- dead code.
# Restore from git commit before 2026-05-21 if needed.


def crostens(history, mp, is_amazon=False, description=None,
             product_category=None, product_subcategory=None,
             brand=None, brand_pt=None, pos_data=None, season=None,
             is_offprice=False, is_new_launch=False, is_international=False):
    """
    Croston's with α=0.3 smoothed over a 78-obs weighted series (3x L13W),
    then z and p are refined 70% from L13W actuals / 30% from smoothed values.
    Demand quantities scaled by L52W seasonal profile.
    Event calendar lifts applied at scheduled order weeks.
    Category seasonality applied when item description matches a known profile.
    """
    # P8 (2026-05-24): Pre-launch history trim.  For new-launch items, the
    # leading 18+ zero weeks in L26 are "item didn't exist", not "infrequent
    # buyer".  Including them inflates p (period) and z gets diluted by the
    # large prepended zero stretch in the weighted series.  When we detect
    # 8+ consecutive leading zeros in L26 followed by activity, trim the
    # history to start at the first non-zero week in L26.  This gives Croston
    # the correct cadence interpretation for the item's actual lifetime.
    _p8_l26 = list(history[-26:]) if len(history) >= 26 else list(history)
    _p8_first_nz_in_l26 = None
    for _i, _v in enumerate(_p8_l26):
        if float(_v or 0) > 0:
            _p8_first_nz_in_l26 = _i
            break
    _p8_trimmed = False
    if _p8_first_nz_in_l26 is not None and _p8_first_nz_in_l26 >= 8:
        # Leading 8+ zeros in L26 (item launched within last 18 weeks).
        # Trim full history at that point.
        _p8_trim_idx = len(history) - 26 + _p8_first_nz_in_l26
        history = history[_p8_trim_idx:]
        _p8_trimmed = True

    ws = make_weighted_series(history)   # 78 obs: 3x weight on L13W

    z, p, last_t = None, None, None
    for t, y in enumerate(ws):
        if float(y) > 0:
            interval = (t - last_t) if last_t is not None else 1
            z = CR_ALPHA * float(y) + (1 - CR_ALPHA) * z if z is not None else float(y)
            p = CR_ALPHA * interval  + (1 - CR_ALPHA) * p if p is not None else float(interval)
            last_t = t

    if z is None:
        return [0] * 26, 0, {}

    # Refine: 70% weight toward L13W actuals
    l13 = history[-13:]
    l13_vals  = [float(v) for v in l13 if v > 0]
    l13_weeks = [i for i, v in enumerate(l13) if float(v) > 0]

    # P7 (2026-05-24): Croston event-aware z.
    # For Amazon, if any of the L13 burst weeks fall within a +/-2-week window
    # of a known Prime Day / Fall Prime Day ordering bump in past calendar
    # (i.e. last year's bumps), the burst was event-driven, not steady-state.
    # Exclude those weeks from z computation -- future event boosts will
    # re-add them at the right calendar time, so keeping them in z =
    # double-counting.
    _p7_burst_weeks_excluded = 0
    if is_amazon and len(l13_vals) >= 2:
        from datetime import date as _p7_date, timedelta as _p7_td
        if ORIG_PRJ_COLS:
            try:
                _p7_col0 = ORIG_PRJ_COLS[0]
                _p7_m, _p7_d = int(_p7_col0[0:2]), int(_p7_col0[3:5])
                _p7_today = _p7_date.today()
                _p7_prj_start = _p7_date(_p7_today.year, _p7_m, _p7_d)
                if (_p7_prj_start - _p7_today).days < -180:
                    _p7_prj_start = _p7_date(_p7_today.year + 1, _p7_m, _p7_d)
                # Past Prime Day bumps (one year ago) and Labor Day bumps
                _p7_event_dates = []
                for _yr_off in (-1, 0):
                    _yr = _p7_prj_start.year + _yr_off
                    for _mo, _dy, _ in PRIME_DAY_BUMPS:
                        try:
                            _p7_event_dates.append(_p7_date(_yr, _mo, _dy))
                        except ValueError:
                            pass
                    # Labor Day Tuesday-after, same year
                    try:
                        _p7_sep1 = _p7_date(_yr, 9, 1)
                        _p7_labor = _p7_sep1 + _p7_td(days=(0 - _p7_sep1.weekday()) % 7)
                        _p7_event_dates.append(_p7_labor + _p7_td(days=1))
                    except ValueError:
                        pass
                # L13 covers weeks (prj_start - 13*7) through (prj_start - 1)
                _p7_l13_start = _p7_prj_start - _p7_td(days=13 * 7)
                _p7_excluded_indices = set()
                for _i in l13_weeks:
                    _p7_week_date = _p7_l13_start + _p7_td(days=_i * 7)
                    for _ev in _p7_event_dates:
                        if abs((_p7_week_date - _ev).days) <= 14:
                            _p7_excluded_indices.add(_i)
                            break
                if _p7_excluded_indices and len(_p7_excluded_indices) < len(l13_weeks):
                    _l13_vals_filtered = [float(l13[i]) for i in l13_weeks
                                          if i not in _p7_excluded_indices]
                    _l13_weeks_filtered = [i for i in l13_weeks
                                           if i not in _p7_excluded_indices]
                    if _l13_vals_filtered:
                        _p7_burst_weeks_excluded = len(_p7_excluded_indices)
                        l13_vals  = _l13_vals_filtered
                        l13_weeks = _l13_weeks_filtered
            except (ValueError, TypeError, AttributeError):
                pass

    if l13_vals:
        # M3 (2026-04-22, loosened v2) — Acceleration-aware z blend.
        # Default weight is 70% L13 actuals / 30% smoothed.  When L13 non-zero
        # avg runs ≥5% above L26 non-zero avg (was ≥15%), the account's order
        # SIZES have scaled up even mildly — the 30% smoothed weight pulls z
        # toward older, smaller orders and under-projects.  Shift to
        # 90% L13 / 10% smoothed so Croston's reflects the newer pace.
        # Threshold relaxed after 36-key review showed international /
        # distributor accounts with 1.02-1.10 ratios were being under-called.
        _m3_l26_nz = [float(v) for v in history[-26:] if float(v) > 0]
        _m3_l13_avg = float(np.mean(l13_vals))
        _m3_l26_avg = (sum(_m3_l26_nz) / len(_m3_l26_nz)) if _m3_l26_nz else 0
        if _m3_l26_avg > 0 and _m3_l13_avg >= _m3_l26_avg * 1.05:
            z = z * 0.1 + _m3_l13_avg * 0.9
        else:
            z = z * 0.3 + _m3_l13_avg * 0.7
    if len(l13_weeks) >= 2:
        intervals = [l13_weeks[i + 1] - l13_weeks[i] for i in range(len(l13_weeks) - 1)]
        p = p * 0.3 + float(np.mean(intervals)) * 0.7

    # L8W recency overlay (2026-05-05) — applied to BOTH z and p in Croston's.
    # Same logic as in seasonal_baseline: 50% L8 / 30% L13 / 20% L26 non-zero
    # averages, blended 60/40 against the existing values. Runs after the M3
    # z refinement above so calibrated rules still set the floor.
    _l8_nz_cr   = [float(v) for v in history[-8:]  if float(v) > 0]
    _l13_nz_cr  = [float(v) for v in history[-13:] if float(v) > 0]
    _l26_nz_cr  = [float(v) for v in history[-26:] if float(v) > 0]
    if (len(_l8_nz_cr) >= 1 and len(_l13_nz_cr) >= 2 and len(_l26_nz_cr) >= 3
            and z is not None and z > 0):
        _l8_avg_cr   = sum(_l8_nz_cr)  / len(_l8_nz_cr)
        _l13_avg_cr  = sum(_l13_nz_cr) / len(_l13_nz_cr)
        _l26_avg_cr  = sum(_l26_nz_cr) / len(_l26_nz_cr)
        _z_blend_cr  = 0.50 * _l8_avg_cr + 0.30 * _l13_avg_cr + 0.20 * _l26_avg_cr
        if 0.5 <= (_z_blend_cr / z) <= 2.0:
            _new_z_cr = 0.60 * _z_blend_cr + 0.40 * z
            if abs(_new_z_cr - z) / z >= 0.05:
                z = _new_z_cr
    # Inter-arrival p — recency-weight the period across L8W/L13W/L26W
    # using the AVERAGE GAP BETWEEN CONSECUTIVE NON-ZERO WEEKS within each
    # window. Falls back gracefully when a window is too sparse to compute.
    def _avg_gap(vals):
        nz_idx = [i for i, v in enumerate(vals) if float(v) > 0]
        if len(nz_idx) >= 2:
            gaps = [nz_idx[i+1] - nz_idx[i] for i in range(len(nz_idx) - 1)]
            return sum(gaps) / len(gaps)
        elif len(nz_idx) == 1 and len(vals) >= 2:
            # one event in window — period is at least window length / 1
            return float(len(vals))
        return None
    _p_l8  = _avg_gap(history[-8:])
    _p_l13 = _avg_gap(history[-13:])
    _p_l26 = _avg_gap(history[-26:])
    if p is not None and p > 0 and _p_l13 and _p_l26:
        _p_l8_use   = _p_l8 if _p_l8 else _p_l13
        _p_blend_cr = 0.50 * _p_l8_use + 0.30 * _p_l13 + 0.20 * _p_l26
        if 0.5 <= (_p_blend_cr / p) <= 2.0:
            _new_p_cr = 0.60 * _p_blend_cr + 0.40 * p
            if abs(_new_p_cr - p) / p >= 0.05:
                # GUARD (2026-05-05): VP-Q3 bi-weekly rule fires when
                # max(1, round(p)) == 2, halving z and setting p_final=1.
                # That can cascade through Fix 5 + F10 + master-pack snap and
                # zero out the forecast.  The recency overlay should refine
                # the cadence MAGNITUDE within a stable cadence class, NOT
                # reclassify an item into or out of bi-weekly handling.
                # Skip the p update if it would flip across the p_final=2
                # boundary in either direction.
                _old_p_final = max(1, round(p))
                _new_p_final = max(1, round(_new_p_cr))
                _crosses_biwk = ((_old_p_final == 2) != (_new_p_final == 2))
                if not _crosses_biwk:
                    p = _new_p_cr

    # Post-spike drawdown guard: if L13W weekly rate is <65% of L26W rate
    # AND the item is still actively ordering, the model is locking onto a
    # post-spike lull rather than the true ongoing demand.  Blend the forward
    # target rate toward L26W to avoid a structural under-forecast.
    l26 = history[-26:]
    l13_weekly = sum(float(v) for v in l13) / 13
    l26_weekly = sum(float(v) for v in l26) / 26
    still_active = any(float(v) > 0 for v in history[-4:])
    if still_active and l26_weekly > 0 and l13_weekly < 0.65 * l26_weekly:
        # Target weekly rate: 40% L26W + 60% L13W (conservative blend)
        target_rate = 0.4 * l26_weekly + 0.6 * l13_weekly
        # Back-compute z so that z/p produces the target weekly rate
        z = target_rate * max(1.0, p)

    p_final = max(1, round(p))
    profile  = seasonal_profile(history)

    # L26W volume floor: z/p implied weekly rate must be at least equal to the
    # L26W actual weekly run rate — provided L13W hasn't collapsed to < 50% of
    # L26W (which would signal a genuine decline, not just a post-spike lull).
    # Without this, stripping the seasonal profile from z causes systematic
    # under-forecasting when L13W non-zero avg understates the true run rate.
    l26_total   = sum(float(v) for v in history[-26:])
    l26_weekly  = l26_total / 26
    still_in_range = (l26_weekly > 0 and l13_weekly > 0.50 * l26_weekly)
    if still_in_range:
        l26_implied_z = l26_weekly * max(1.0, p)
        if z < l26_implied_z:
            z = l26_implied_z

    # F28 (2026-04-26, loosened 2026-04-26) — Croston volume floor against L13.
    # Deep deviation analysis (n=414 Croston records) showed median bias of
    # -15% vs L13W — the model systematically under-forecasts because every-
    # other-week zeros depress the 26-week sum, and the existing L26 floor
    # uses L26_weekly which is often lower than L13_weekly.
    #
    # First version (stable-band 0.85-1.15) didn't fire enough — Croston records
    # are intermittent by definition so the L4/L13 ratio is rarely tight.
    # Loosened: lift z to match L13_weekly whenever z/p < L13_weekly × 0.90
    # AND L13 still active (≥3 active weeks).  The existing F10 EOL guard
    # already prevents this from firing on declining items.
    _f28_applied = False
    if l13_weekly > 0 and len(l13_vals) >= 3:
        _p_f28          = max(1.0, round(p))
        _implied_wk_f28 = z / _p_f28 if _p_f28 > 0 else 0
        if _implied_wk_f28 < l13_weekly * 0.90:
            # Cap z so the lift doesn't exceed 1.5× original (defensive)
            _z_target_f28 = l13_weekly * _p_f28
            _z_max_f28    = z * 1.5 if z > 0 else _z_target_f28
            z = min(_z_target_f28, _z_max_f28)
            _f28_applied = True

    # F18 — Croston's z POS anchor (2026-04-22, expanded 2026-05-12).
    # POS is the primary demand signal for any customer with POS data.
    # The z/p implied weekly rate is adjusted toward POS in both directions:
    #   Uplift   (POS > implied): POS running faster than Croston's z/p implies
    #            → lift z so the forecast reflects consumer velocity.
    #   Drawdown (implied > POS × 2.0): customer is stocked up; Croston's z
    #            is inflated by recent large orders that won't repeat until
    #            stock burns through → cap z toward POS L13W rate.
    #   Moderate above-POS (POS × 1.0–2.0): blend 75% POS / 25% Croston's.
    # Volume-gated (POS L13 ≥ 50/wk) to avoid tail-item noise.
    _f18_applied     = False
    _f18_driver      = None
    _f18_capped_down = False   # True when F18 intentionally caps z DOWN (R6 must not re-lift)
    if pos_data:
        _pos_l4_f18  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
        _pos_l13_f18 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        _pos_l26_f18 = float(pos_data.get("Avg_Units_Wk_L26w") or 0)
        _pos_healthy_f18 = _pos_l13_f18 >= 50.0 and _pos_l4_f18 >= _pos_l13_f18 * 0.5
        if _pos_healthy_f18 and z > 0:
            _p_use       = max(1.0, p)
            _implied_wk  = z / _p_use
            if _pos_l4_f18 > _implied_wk:
                # POS above implied rate — lift z toward consumer velocity.
                _new_wk      = _implied_wk * 0.6 + _pos_l4_f18 * 0.4
                _max_wk      = _implied_wk * 1.5
                _new_wk      = min(_new_wk, _max_wk)
                _z_new       = _new_wk * _p_use
                _f18_driver  = (f"F18 POS uplift: implied wk {_implied_wk:.0f} → "
                                f"{_new_wk:.0f} (POS L4 {_pos_l4_f18:.0f}), "
                                f"z {z:.0f} → {_z_new:.0f}")
                z            = _z_new
                _f18_applied = True
                # uplift direction — R6 may still apply
            elif _implied_wk > _pos_l13_f18 * 2.0:
                # Croston's rate heavily above POS — customer stocked up;
                # recent large orders inflated z.  Cap toward POS rate.
                # F18b — recovery anchor (2026-05-21): when L4W > L13W × 1.5
                # AND L26W > L13W × 1.5, L13W is distorted by a dormancy trough
                # (DC was overstocked and drew down, suppressing both our orders
                # and Amazon POS).  Use max(L13W, L26W × 0.75) as the cap so we
                # don't penalise the recovery back to a dormancy-distorted floor.
                _recovering_f18 = (_pos_l4_f18  > _pos_l13_f18 * 1.5
                                   and _pos_l26_f18 > _pos_l13_f18 * 1.5
                                   and _pos_l26_f18 > 0)
                _capped_wk   = (max(_pos_l13_f18, _pos_l26_f18 * 0.75)
                                if _recovering_f18 else _pos_l13_f18)
                _z_new       = _capped_wk * _p_use
                _f18_driver  = (f"F18 stocked-up: implied {_implied_wk:.0f}/wk "
                                f"-> {'recovery-anchor ' if _recovering_f18 else ''}"
                                f"POS {_capped_wk:.0f}, "
                                f"z {z:.0f} -> {_z_new:.0f}")
                z                = _z_new
                _f18_applied     = True
                _f18_capped_down = True   # R6 must not re-lift
            elif _implied_wk > _pos_l13_f18 * 1.0:
                # Moderately above POS — 75/25 POS/Croston's blend.
                _blended_wk  = _pos_l13_f18 * 0.75 + _implied_wk * 0.25
                _z_new       = _blended_wk * _p_use
                _f18_driver  = (f"F18 above-POS {_implied_wk/_pos_l13_f18:.1f}x "
                                f"-> 75/25 POS/ord: {_implied_wk:.0f} -> {_blended_wk:.0f}, "
                                f"z {z:.0f} -> {_z_new:.0f}")
                z                = _z_new
                _f18_applied     = True
                _f18_capped_down = True   # R6 must not re-lift

    # P2 / F18b (2026-05-24): Burst carve-out for Amazon Croston's.
    # Variance deep-dive: items where the LAST 4 weeks of L13 dominate (e.g.
    # Prime Day pre-buy burst at the end of L13) but F18 didn't fire because
    # the lumpy earlier weeks in L13 muddled the implied rate. The burst
    # weeks aren't repeating soon - they were one-time event-driven.
    # Trigger:
    #   L4W_avg > L13W_avg * 1.8  (recent burst >> baseline)
    #   AND POS_L13W > 0  AND  L4W_avg > POS_L13W * 1.5  (burst not driven by POS)
    # Action: cap z to L13W average EXCLUDING the L4W burst weeks * 1.2
    if pos_data and not _f18_applied:
        _f18b_l4 = [float(v or 0) for v in history[-4:]]
        _f18b_l13 = [float(v or 0) for v in history[-13:]]
        _f18b_l4_avg  = sum(_f18b_l4)  / max(len(_f18b_l4),  1) if _f18b_l4  else 0
        _f18b_l13_avg = sum(_f18b_l13) / max(len(_f18b_l13), 1) if _f18b_l13 else 0
        _f18b_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        _f18b_burst_vs_baseline = (_f18b_l13_avg > 0
                                     and _f18b_l4_avg > _f18b_l13_avg * 1.8)
        _f18b_burst_vs_pos = (_f18b_pos_l13 > 0
                                and _f18b_l4_avg > _f18b_pos_l13 * 1.5)
        if _f18b_burst_vs_baseline and _f18b_burst_vs_pos and z > 0:
            # Compute pre-burst L13 average (exclude the recent 4 weeks)
            _f18b_l13_prior9 = _f18b_l13[:9] if len(_f18b_l13) >= 9 else []
            _f18b_l13_prior_nz = [v for v in _f18b_l13_prior9 if v > 0]
            if _f18b_l13_prior_nz:
                _f18b_baseline = sum(_f18b_l13_prior_nz) / len(_f18b_l13_prior_nz)
                _f18b_new_wk = _f18b_baseline * 1.2
                _f18b_p_use = max(1.0, p)
                _f18b_z_new = _f18b_new_wk * _f18b_p_use
                _f18_driver  = (f"F18b Burst carve-out: L4 avg {_f18b_l4_avg:.0f} vs "
                                f"L13 avg {_f18b_l13_avg:.0f} (burst {_f18b_l4_avg/_f18b_l13_avg:.1f}x); "
                                f"POS L13 {_f18b_pos_l13:.0f}; capped to pre-burst "
                                f"L13[:9]_nz_avg {_f18b_baseline:.0f} * 1.2 = {_f18b_new_wk:.0f}/wk; "
                                f"z {z:.0f} -> {_f18b_z_new:.0f}")
                z = _f18b_z_new
                _f18_applied = True
                _f18_capped_down = True

    # Fix 1 — Category seasonality for Croston's: precompute per-week multipliers.
    # Croston's normally uses z directly (no seasonal profile) to avoid noisy
    # position-based distortion.  Category profiles are reliable known curves, so
    # we apply them here as a per-week scaler on individual placed orders.
    _cat_mults_c = _category_week_multipliers(
        description, product_category, product_subcategory, brand, brand_pt,
        season=season
    ) if (description or product_category or product_subcategory or brand or brand_pt or season) else None

    # VP-Q3 — Bi-weekly Croston's smoothed to weekly (2026-05-03).
    # Per VP-Q3, bi-weekly cadence is treated as effectively weekly: rather
    # than placing the full quantity every other week (which leaves alternating
    # zeros that read to planners as enforced cadence), spread the same total
    # volume evenly across all 26 weeks.  Halve z and set p_final=1 so total
    # 26w volume is preserved while every week gets a placement.  Cadence
    # enforcement only kicks in for true monthly+ patterns (gap≥3) via
    # apply_ordering_pattern() downstream.
    #
    # F57 — VP-Q3 skip for irregular bulk buyers (2026-05-08).
    # International bulk-buyer accounts (R5: Loblaws Canada, etc.) and items
    # with very high order-size variance order in IRREGULAR LUMPS, not steady
    # bi-weekly.  VP-Q3's smoothing flattens those lumps into fake weekly
    # output that doesn't match how the account actually buys.  Skip VP-Q3
    # smoothing when:
    #   (a) R5 is set (international bulk buyer), OR
    #   (b) L13 non-zero CV > 0.6 (order-size variance is high — bulky/irregular)
    # so Croston's natural lumpy output (big-week / zero-week) is preserved.
    #
    # Empirical example (Loblaws Canada 12446-BB0234CAN, 2026-05-08 callout):
    #   L26 ord = [2520, 0, 0, 10080, 1380, ..., 5040, 2520] — order sizes
    #   1,380-10,080 (CV ~0.7), 14/26 nz weeks, irregular gaps.  Without F57,
    #   VP-Q3 turned this into smooth 2,202/wk → planner sees flat output that
    #   doesn't match the actual lumpy buying pattern.
    _vpq3_biweekly_smooth = False
    _f57_skipped = False
    if p_final == 2:
        # Compute L13 nz CV for the F57 gate
        _f57_l13_nz = [float(v) for v in history[-13:] if float(v) > 0]
        _f57_high_cv = False
        if len(_f57_l13_nz) >= 4:
            _mean_f57 = sum(_f57_l13_nz) / len(_f57_l13_nz)
            if _mean_f57 > 0:
                _var_f57 = sum((v - _mean_f57) ** 2 for v in _f57_l13_nz) / len(_f57_l13_nz)
                _cv_f57 = (_var_f57 ** 0.5) / _mean_f57
                _f57_high_cv = _cv_f57 > 0.6
        if is_international or _f57_high_cv:
            _f57_skipped = True
        else:
            z = z / 2.0
            p_final = 1
            _vpq3_biweekly_smooth = True

    forecast = [0] * 26
    w = 0   # always start at W1
    event_inserts = []
    while w < 26:
        week_num    = w + 1
        # F11 — Prime Day / Fall Prime Day ordering lift (Amazon-only, calendar-based).
        if is_amazon:
            _cb_prime, _cb_fall = _get_event_boosts()
            prime_boost = _cb_prime.get(week_num, 1.0)
            fall_boost  = _cb_fall.get(week_num, 1.0)
        else:
            prime_boost = fall_boost = 1.0
        event_boost = max(prime_boost, fall_boost)
        cat_mult    = _cat_mults_c[w] if _cat_mults_c else 1.0
        # Croston's: use z directly (no seasonal profile — intermittent buyers
        # don't follow a smooth seasonal curve; profile values are noisy and
        # would distort individual order sizes.  event_boost and cat_mult handle
        # known events and category seasonal patterns.)
        qty = snap(z * event_boost * cat_mult, mp)
        if event_boost > 1.0:
            event_inserts.append({"week": week_num, "boost": event_boost, "qty": qty})
        forecast[w] = qty
        w += p_final

    # Fix 5 — Rescale 26w total toward L13W avg.
    # Croston's z/p over-forecasts when non-zero avg >> all-weeks avg, e.g. ISO
    # post-spike items where z is inflated by large spike values. Only scale
    # DOWN (never up) — the L26W volume floor already handles under-projection.
    #
    # Amazon exception (2026-05-24): F59o will subsequently apply a seasonal
    # overlay whose floor is derived from the Croston's non-zero mean.  Using
    # the all-weeks avg here (which includes off-season zeros) as the cap
    # reference decimates that flat_ref, making F59o's peak lifts proportionally
    # too small.  For Amazon items, compare against the L13W NON-ZERO avg so the
    # seasonal baseline that F59o builds from is not artificially suppressed.
    _l13_all_avg = sum(float(v) for v in history[-13:]) / 13
    _l13_nz_list_f5 = [float(v) for v in history[-13:] if v > 0]
    _l13_nz_avg_f5  = (sum(_l13_nz_list_f5) / len(_l13_nz_list_f5)
                       if _l13_nz_list_f5 else _l13_all_avg)
    # F73 (2026-05-24): New-launch items get the same nz-avg reference as Amazon.
    # For items with <= 13 active weeks the all-weeks L13 avg is zero-diluted
    # (leading pre-launch weeks count as zeros) and drags Fix 5 too low, capping
    # Croston's output well below the true emerging run rate.  Using nz-avg means
    # Fix 5 only fires if Croston's exceeds the non-zero baseline by > 10%,
    # preserving the true demand signal during the ramp-up phase.
    _fix5_ref = _l13_nz_avg_f5 if (is_amazon or is_new_launch) else _l13_all_avg
    if _fix5_ref > 0 and sum(forecast) > 0:
        _ai_avg = sum(forecast) / 26
        if _ai_avg > _fix5_ref * 1.10:
            _scale = _fix5_ref / _ai_avg
            _scale = max(0.5, _scale)    # cap at 2x reduction
            forecast = [snap(v * _scale, mp) if v > 0 else 0 for v in forecast]

    # Ensure each event window gets at least one order -- but only if the
    # entire window is empty.  If the cadence already landed anywhere in the
    # window, it got the boost in the loop above; don't also force-insert
    # z-sized orders into the remaining zero weeks (that double/triple-counts).
    if is_amazon:
        _ins_prime, _ins_fall = _get_event_boosts()
        # Prime Day insertion
        if _ins_prime:
            prime_covered = any(forecast[ew - 1] > 0
                                for ew in _ins_prime if ew <= 26)
            if not prime_covered:
                ew   = min(ew for ew in _ins_prime if ew <= 26)
                bst  = _ins_prime[ew]
                forecast[ew - 1] = snap(z * bst, mp)
                event_inserts.append({"week": ew, "boost": bst,
                                       "qty": forecast[ew - 1], "inserted": True})
        # Fall Prime Day insertion (Tuesday after Labor Day)
        if _ins_fall:
            fall_covered = any(forecast[ew - 1] > 0
                               for ew in _ins_fall if ew <= 26)
            if not fall_covered:
                ew  = min(ew for ew in _ins_fall if ew <= 26)
                bst = _ins_fall[ew]
                forecast[ew - 1] = snap(z * bst, mp)
                event_inserts.append({"week": ew, "boost": bst,
                                       "qty": forecast[ew - 1], "inserted": True})

    # F10 — Declining-item end-of-life scale-down for Croston's (YoY-gated).
    _l4_avg_f10c   = sum(history[-4:]) / 4 if len(history) >= 4 else 0
    _l13_nz_f10c   = [v for v in history[-13:] if v > 0]
    _l13_nz_avg_f10c = sum(_l13_nz_f10c) / len(_l13_nz_f10c) if _l13_nz_f10c else 0
    _l4_yago_f10c  = sum(history[-52:-48]) / 4 if len(history) >= 52 else 0
    _drop_vs_l13_c = _l13_nz_avg_f10c > 0 and _l4_avg_f10c < _l13_nz_avg_f10c * 0.7
    _drop_yoy_c    = _l4_yago_f10c > 0 and _l4_avg_f10c < _l4_yago_f10c * 0.5
    _yoy_avail_c   = _l4_yago_f10c > 0
    # F14a — POS-healthy override on F10 (Croston's).
    # F14b — volume gate: POS L13 ≥ 50/wk to trip override.
    _f14a_override_c = False
    if _drop_vs_l13_c and (_drop_yoy_c or not _yoy_avail_c) and is_amazon and pos_data:
        _pos_l4_c  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
        _pos_l13_c = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        _f14b_vol_ok_c = _pos_l13_c >= 50.0
        if _pos_l13_c > 0 and _pos_l4_c >= _pos_l13_c * 0.5 and _f14b_vol_ok_c:
            _f14a_override_c = True
    # F34 — Pre-launch zeros aren't a decline reference (2026-05-05).  When
    # the item is new (only ~26w active), the YoY check is comparing against
    # pre-launch zeros, which makes _yoy_avail_c=False and trips the F10 gate
    # via the "not _yoy_avail_c" branch.  That's a false positive — block F10.
    _f10_applied_c = False
    if (_drop_vs_l13_c and (_drop_yoy_c or not _yoy_avail_c)
            and not _f14a_override_c and not is_new_launch):
        _new_fc = []
        for _wi, _vi in enumerate(forecast):
            if _vi > 0:
                _blended = 0.5 * _vi + 0.5 * _l4_avg_f10c
                if _wi >= 13:
                    _blended *= 0.85
                _new_fc.append(snap(_blended, mp) if _blended > 0 else 0)
            else:
                _new_fc.append(0)
        forecast = _new_fc
        _f10_applied_c = True

    # R6 — Croston's steady-cadence lift (2026-04-22, revised).
    # Observed pattern: 368 Croston records under by 511K total.  Biggest cases
    # are high-volume stable items where Croston z/p dampens too aggressively.
    # Proportional lift: the more stable L4 is vs L13 AND the more Croston
    # under-projects vs L13×26, the more we lift toward the L13 target.
    #
    # stability = min(L4/L13, 1.0)  — 1.0 when L4 matches L13 (fully stable)
    # undershoot = max(0, 1 - cr_total / (L13×26))  — 0 if not under, up to 1
    # lift_weight = stability × undershoot × 0.8   — max 80% toward L13×26
    # target = cr_total × (1-lift_weight) + L13×26 × lift_weight
    _l13_all_avg_r6 = sum(float(v) for v in history[-13:]) / 13
    _l4_avg_r6 = sum(float(v) for v in history[-4:]) / 4 if len(history) >= 4 else 0
    _r6_applied = False
    _r6_high_vol = False
    if _l13_all_avg_r6 > 0 and _l4_avg_r6 > 0:
        _cr_total  = sum(forecast)
        _l13_total = _l13_all_avg_r6 * 26
        _stability = min(_l4_avg_r6 / _l13_all_avg_r6, 1.0)
        _undershoot = max(0.0, 1.0 - (_cr_total / _l13_total)) if _l13_total > 0 else 0
        # S3 (2026-04-22) — High-volume steady Croston undershoots.  For
        # items where L13×26 ≥ 50,000 units AND stability ≥ 0.9, relax the
        # undershoot gate (>0.10 vs >0.15) and raise the lift multiplier
        # (×1.0 vs ×0.8) so the forecast fully pulls toward L13×26.  Fixes
        # Lowes BB22272 (AI=24K vs Man=98K), Walmart FF8882/2 (−38K), etc.
        _r6_high_vol = (_l13_total >= 50000 and _stability >= 0.9)
        if _r6_high_vol:
            _gate_undershoot = 0.10
            _lift_mult       = 1.0
        else:
            _gate_undershoot = 0.15
            _lift_mult       = 0.8
        # Only lift when BOTH stability >= 0.7 AND undershoot exceeds gate.
        # Skip R6 when F18 has intentionally capped z downward (stocked-up /
        # above-POS blend) — re-lifting toward order-history L13 defeats the
        # entire point of the POS anchor and would restore the inflated rate.
        if (_stability >= 0.7 and _undershoot > _gate_undershoot
                and _cr_total > 0 and not _f18_capped_down):
            _lift_weight = _stability * _undershoot * _lift_mult
            _target = _cr_total * (1 - _lift_weight) + _l13_total * _lift_weight
            _scale = _target / _cr_total
            forecast = [snap(v * _scale, mp) if v > 0 else 0 for v in forecast]
            _r6_applied = True

    cap_base = float(np.mean([float(v) for v in l13 if float(v) >= 0])) if any(float(v) > 0 for v in l13) else z
    meta = {
        "z": round(z, 1),
        "p": round(p, 2),
        "p_final": p_final,
        "n_l13": len(l13_vals),
        "avg_l13_ord": round(float(np.mean(l13_vals)), 1) if l13_vals else 0,
        "event_inserts": event_inserts,
    }
    if _p8_trimmed:
        meta.setdefault("drivers", []).append(
            f"P8 Pre-launch history trim: detected {_p8_first_nz_in_l26}-wk leading-zero gap "
            f"in L26; trimmed to {len(history)}w post-launch for z/p computation"
        )
    if _p7_burst_weeks_excluded > 0:
        meta.setdefault("drivers", []).append(
            f"P7 Croston event-aware z: excluded {_p7_burst_weeks_excluded} L13 week(s) "
            f"within +/-14 days of past Prime Day / Labor Day events from z computation "
            f"(future event boosts re-add them at correct calendar time)"
        )
    if _r6_applied:
        _hv_tag = " [S3 high-vol ×1.0]" if _r6_high_vol else ""
        meta.setdefault("drivers", []).append(
            f"R6 Croston's steady-cadence lift{_hv_tag}: L4={_l4_avg_r6:.0f} vs L13={_l13_all_avg_r6:.0f}, "
            f"forecast scaled up toward L13×26"
        )
    if _f10_applied_c:
        meta.setdefault("drivers", []).append(
            f"declining: L4W avg {_l4_avg_f10c:.0f} < 70% L13 nz avg {_l13_nz_avg_f10c:.0f}"
        )
    if _f14a_override_c:
        _pos_l4_cm  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0) if pos_data else 0
        _pos_l13_cm = float(pos_data.get("Avg_Units_Wk_L13w") or 0) if pos_data else 0
        _ratio_cm = (_pos_l4_cm / _pos_l13_cm) if _pos_l13_cm > 0 else 0
        meta.setdefault("drivers", []).append(
            f"F14a POS-healthy override on F10: POS L4/L13={_ratio_cm:.2f} ≥ 0.50"
        )
    if _f18_applied and _f18_driver:
        meta.setdefault("drivers", []).append(_f18_driver)
    if _f18_capped_down:
        meta["f18_capped_down"] = True   # signal to forecast_record(): F59a/F59b must not re-lift
    if _vpq3_biweekly_smooth:
        meta.setdefault("drivers", []).append(
            "VP-Q3 bi-weekly smoothed to weekly: p=2 → p=1, z halved, "
            "26w volume preserved, no alternating zeros"
        )
    if _f57_skipped:
        _reason_f57 = ("R5 international bulk buyer" if is_international
                       else "L13 nz CV >0.6 (irregular bulk pattern)")
        meta.setdefault("drivers", []).append(
            f"F57 VP-Q3 skip: {_reason_f57} — preserving Croston's lumpy "
            f"output (big-week / zero-week alternation) to match how the "
            f"account actually orders, not flattened bi-weekly smooth"
        )

    # T1 — Off-price Croston's ceiling (2026-04-22).  Off-price / closeout
    # retailers (Burlington, Ross, TJ Maxx, Kohl's, etc.) that don't hit OTB
    # detection but still run through Croston's tend to over-project because
    # Croston's treats their sparse pattern as repeatable cadence.  Observed
    # case: Burlington Croston top 4 items = +125K surplus vs manual.
    # Cap total 26w Croston forecast at L26 all-weeks avg × 26 × 1.0 for
    # off-price.  Matches S1 off-price Sparse ceiling.
    if is_offprice:
        _t1_l26 = [float(v) for v in history[-26:]]
        _t1_l26_avg = (sum(_t1_l26) / 26) if _t1_l26 else 0
        if _t1_l26_avg > 0:
            _t1_ceiling = _t1_l26_avg * 26 * 1.0
            _t1_total = sum(forecast)
            if _t1_total > _t1_ceiling:
                _t1_scale = _t1_ceiling / _t1_total
                forecast = [snap(v * _t1_scale, mp) if v > 0 else 0 for v in forecast]
                meta.setdefault("drivers", []).append(
                    f"T1 off-price Croston cap: L26_avg {_t1_l26_avg:.0f} × 26 × 1.0 "
                    f"→ total capped from {_t1_total:.0f} to {sum(forecast):.0f}"
                )

    return forecast, round(cap_base, 1), meta


def heuristic(history, mp, l13w, is_amazon=False, description=None,
              product_category=None, product_subcategory=None,
              brand=None, brand_pt=None, pos_data=None, season=None,
              is_new_launch=False):
    """
    Heuristic for sparse/new items.
    Baseline derived from post-ramp history (excluding launch ramp weeks 1-6).
    Falls back to L13W non-zero avg, then all-history avg, then l13w field.
    """
    l13 = history[-13:]
    active_l13 = [float(v) for v in l13 if v > 0]

    # Ramp detection: exclude weeks 1-6 post-launch
    first_nz, ramp_end = detect_ramp(history)
    post_ramp = [float(v) for v in history[ramp_end:] if float(v) > 0] if ramp_end > 0 else []

    if post_ramp:
        baseline = float(np.mean(post_ramp))
        n_active = len(post_ramp)
        src = "post-ramp history"
    elif active_l13:
        baseline = float(np.mean(active_l13))
        n_active = len(active_l13)
        src = "L13W non-zero avg"
    else:
        active_all = [float(v) for v in history if float(v) > 0]
        if active_all:
            baseline = float(np.mean(active_all))
            n_active = len(active_all)
            src = "L52W all-history avg"
        else:
            baseline = float(l13w or 0)
            n_active = 0
            src = "Shpd_Wk_L13W fallback"

    if baseline == 0:
        return [0] * 26, 0, {"baseline": 0, "n_active": 0, "src": src}

    # F9 — For higher-volume sparse items (annual L52 total > 15k units), use the
    # MAX of L13/L26/L52 non-zero averages as baseline. This guards against under-
    # projecting a strong-but-lumpy item whose L13W happens to fall in a lull.
    _l52_all_f9 = history[-52:] if len(history) >= 52 else history
    _l52_total_f9 = sum(_l52_all_f9)
    _f9_applied = False
    if _l52_total_f9 > 15000:
        _l13_nz_f9 = [v for v in history[-13:] if v > 0]
        _l26_nz_f9 = [v for v in history[-26:] if v > 0]
        _l52_nz_f9 = [v for v in _l52_all_f9 if v > 0]
        _candidates = []
        if _l13_nz_f9: _candidates.append(sum(_l13_nz_f9) / len(_l13_nz_f9))
        if _l26_nz_f9: _candidates.append(sum(_l26_nz_f9) / len(_l26_nz_f9))
        if _l52_nz_f9: _candidates.append(sum(_l52_nz_f9) / len(_l52_nz_f9))
        if _candidates:
            _max_baseline = max(_candidates)
            if _max_baseline > baseline:
                baseline = _max_baseline
                src = f"{src} + F9 MAX(L13/L26/L52 nz avg)"
                _f9_applied = True

    # F74 -- Amazon Heuristic initial-stock-up exclusion (2026-05-24).
    #
    # F9 boosts the Heuristic baseline to MAX(L13/L26/L52 nz avg) for high-
    # volume items (L52 total > 15k).  When the MAX comes from L26 or L52
    # because large early stock-up POs inflated those windows, but the most
    # recent 13 weeks show a much lower run rate AND POS confirms that lower
    # rate, the F9 boost is anchored against stale stock-up volume rather
    # than true replenishment demand.
    #
    # Trigger:
    #   is_amazon AND pos_data available AND F9 was applied
    #   current baseline > 3x POS L13w rate (over-anchored)
    #   L13 nz avg < L26 nz avg x 0.5 (recent demand settled below stock-up level)
    #
    # Action: cap baseline = max(L13_nz_avg, POS_L13w x 1.5)
    if is_amazon and pos_data and _f9_applied:
        _f74_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        if _f74_pos_l13 > 0:
            _f74_l13_nz = [float(v) for v in history[-13:] if float(v) > 0]
            _f74_l26_nz = [float(v) for v in history[-26:] if float(v) > 0]
            _f74_l13_avg = (sum(_f74_l13_nz) / len(_f74_l13_nz)) if _f74_l13_nz else 0.0
            _f74_l26_avg = (sum(_f74_l26_nz) / len(_f74_l26_nz)) if _f74_l26_nz else 0.0
            if (baseline > 3.0 * _f74_pos_l13
                    and _f74_l13_avg < _f74_l26_avg * 0.5):
                _f74_cap = max(
                    _f74_l13_avg if _f74_l13_avg > 0 else _f74_pos_l13 * 1.5,
                    _f74_pos_l13 * 1.5
                )
                if baseline > _f74_cap:
                    _f74_prev = baseline
                    baseline = _f74_cap
                    src = (f"{src} + F74 stock-up exclusion "
                           f"(F9 was {_f74_prev:.0f}/wk; L26nz avg "
                           f"{_f74_l26_avg:.0f} >> L13nz avg {_f74_l13_avg:.0f}; "
                           f"POS {_f74_pos_l13:.0f}/wk; capped to {_f74_cap:.0f}/wk)")

    # F23b -- Trailing-zero drawdown discount for Heuristic (2026-04-22).
    # Same pure-order-history signal used by F22a in seasonal_baseline.
    # Heuristic items that routed away from Inactive (F20 kept planner-nonzero
    # items here) can still be mid-drawdown; scale baseline by the trailing-
    # zero run.  Floor = 20% of baseline at 13+ zero weeks.
    _trailing_zeros_h = 0
    for _v_tzh in reversed(history):
        if float(_v_tzh) == 0:
            _trailing_zeros_h += 1
        else:
            break
    # F25 revised — floor raised from 0.2 → 0.3 (max discount 0.7 instead of 0.8).
    # F25@0.4 swung items like FF5952EC too high (F23=16K, F25=33K, manual 9K).
    # F25@0.3 is a middle ground: preserves the F23b correction most of the
    # way while leaving room for items recovering from a dip.
    _f23b_mult = 1.0 - min(_trailing_zeros_h / 13.0, 0.7)
    _f23b_applied = False
    _pre_f23b_baseline = baseline
    if _trailing_zeros_h >= 3:
        baseline = baseline * _f23b_mult
        _f23b_applied = True

    profile_raw = seasonal_profile(history)

    # F23a — Dampen Heuristic seasonal profile (2026-04-22, eased 2026-05-06).
    # Heuristic runs on sparse history where a handful of large orders create
    # extreme position-based profile multipliers (observed max 10×+).  One
    # historical 36K-unit spike at week index X would map straight through to
    # forecast week X as a single catastrophic value.
    # Eased 2026-05-06: 0.1 → 0.3 to mirror seasonal_baseline base damp.  We
    # no longer crush all curvature on Heuristic items — known seasonal SKUs
    # picked up via the category-profile blend below (30/70) get most of the
    # lift.  Hard cap at [0.30, 2.5] post-normalize keeps the rare extreme
    # historical spike from blowing up a single forecast week.
    DAMP_H = 0.3
    profile = [1.0 + (s - 1.0) * DAMP_H for s in profile_raw]
    _pm0 = sum(profile) / len(profile)
    if _pm0 > 0:
        profile = [p / _pm0 for p in profile]   # re-normalize mean=1.0
    profile = [min(2.5, max(0.30, p)) for p in profile]
    _raw_peak_trough_h = (max(profile_raw) / min(profile_raw)) if min(profile_raw) > 0 else 1.0

    # Fix 1 — Category seasonality blend for heuristic: 30% historical / 70% category.
    _cat_mults_h = _category_week_multipliers(
        description, product_category, product_subcategory, brand, brand_pt,
        season=season
    ) if (description or product_category or product_subcategory or brand or brand_pt or season) else None
    if _cat_mults_h:
        profile = [0.30 * s + 0.70 * c for s, c in zip(profile, _cat_mults_h)]
        _pm = sum(profile) / len(profile)
        if _pm > 0:
            profile = [p / _pm for p in profile]

    # R9 — Heuristic baseline ceiling at L52W all-weeks avg × 2.0 (2026-04-22).
    # T2 (2026-04-22): reverted S4's ×2.5 → ×2.0 after observed +24% Heuristic
    # overshoot.
    #
    # 2026-05-07 — VP-Q6 fix: R9 is now applied UNCONDITIONALLY, including
    # when F23b also fired.  Previously the "skip when F23b fired" gate let
    # single-PO patterns escape the ceiling.  Concrete case: FF7612 Petco
    # had L13 = single 5,208-unit PO ~12 weeks ago, then dormant.  F23b
    # discounted baseline by 0.30× to ~1,562, which then projected as
    # ~1,562/wk × 26 = ~37K units — far above the L26 all-weeks avg of
    # ~426/wk.  The discount alone is not enough — sparse single-PO items
    # also need the absolute ceiling.  Multiplier raised to 2.5× when F23b
    # also fired, to soften the double-discount on items that legitimately
    # have a heavy trailing-zero pattern (e.g. items just resuming).
    _l52_all_r9 = history[-52:] if len(history) >= 52 else history
    _l52_avg_r9 = (sum(_l52_all_r9) / len(_l52_all_r9)) if _l52_all_r9 else 0
    _r9_applied = False
    _r9_pre_baseline = baseline
    if _l52_avg_r9 > 0:
        _r9_mult    = 2.5 if _f23b_applied else 2.0
        _r9_ceiling = _l52_avg_r9 * _r9_mult
        if baseline > _r9_ceiling:
            baseline = _r9_ceiling
            _r9_applied = True

    forecast = []
    for h in range(1, 27):
        # F11 — Prime Day / Fall Prime Day ordering lift (Amazon-only, calendar-based).
        if is_amazon:
            _hp, _hf = _get_event_boosts()
            prime_mult = _hp.get(h, 1.0)
            fall_mult  = _hf.get(h, 1.0)
        else:
            prime_mult = fall_mult = 1.0
        event_mult = max(prime_mult, fall_mult)
        forecast.append(snap(baseline * profile[h - 1] * event_mult, mp))

    # T2 — Per-week Heuristic cap (2026-04-22).  Prevents any single forecast
    # week from exceeding max(L4_nz_avg, L13_nz_avg) × 1.5.  Combined with
    # profile dampening (DAMP_H=0.3) this double-locks against position-based
    # blow-ups on items where a single historical spike distorts the profile.
    # Observed case: FF7612 Petco Heuristic AI=24K vs manual 4.6K — one
    # forecast week hit 5× the recent non-zero rate.
    _t2_l4_nz  = [v for v in history[-4:]  if float(v) > 0]
    _t2_l13_nz = [v for v in history[-13:] if float(v) > 0]
    _t2_l4_avg  = (sum(_t2_l4_nz)  / len(_t2_l4_nz))  if _t2_l4_nz  else 0
    _t2_l13_avg = (sum(_t2_l13_nz) / len(_t2_l13_nz)) if _t2_l13_nz else 0
    _t2_rate   = max(_t2_l4_avg, _t2_l13_avg)
    _t2_applied = False
    if _t2_rate > 0:
        _t2_cap = _t2_rate * 1.5
        _t2_new = []
        for _t2_v in forecast:
            if _t2_v > _t2_cap:
                _t2_new.append(snap(_t2_cap, mp))
                _t2_applied = True
            else:
                _t2_new.append(_t2_v)
        forecast = _t2_new

    # F10 — Declining-item end-of-life scale-down for heuristic (YoY-gated).
    _l4_avg_f10h   = sum(history[-4:]) / 4 if len(history) >= 4 else 0
    _l13_nz_f10h   = [v for v in history[-13:] if v > 0]
    _l13_nz_avg_f10h = sum(_l13_nz_f10h) / len(_l13_nz_f10h) if _l13_nz_f10h else 0
    _l4_yago_f10h  = sum(history[-52:-48]) / 4 if len(history) >= 52 else 0
    _drop_vs_l13_h = _l13_nz_avg_f10h > 0 and _l4_avg_f10h < _l13_nz_avg_f10h * 0.7
    _drop_yoy_h    = _l4_yago_f10h > 0 and _l4_avg_f10h < _l4_yago_f10h * 0.5
    _yoy_avail_h   = _l4_yago_f10h > 0
    # F14a — POS-healthy override on F10 (heuristic).
    # F14b — volume gate: POS L13 ≥ 50/wk to trip override.
    _f14a_override_h = False
    if _drop_vs_l13_h and (_drop_yoy_h or not _yoy_avail_h) and is_amazon and pos_data:
        _pos_l4_h  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
        _pos_l13_h = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        _f14b_vol_ok_h = _pos_l13_h >= 50.0
        if _pos_l13_h > 0 and _pos_l4_h >= _pos_l13_h * 0.5 and _f14b_vol_ok_h:
            _f14a_override_h = True
    # F34: skip F10 on new-launch items — pre-launch zeros aren't a decline reference.
    _f10_applied_h = False
    if (_drop_vs_l13_h and (_drop_yoy_h or not _yoy_avail_h)
            and not _f14a_override_h and not is_new_launch):
        _new_fh = []
        for _wi, _vi in enumerate(forecast):
            _blended = 0.5 * _vi + 0.5 * _l4_avg_f10h
            if _wi >= 13:
                _blended *= 0.85
            _new_fh.append(snap(_blended, mp) if _blended > 0 else 0)
        forecast = _new_fh
        _f10_applied_h = True

    meta = {"baseline": round(baseline, 1), "n_active": n_active, "src": src}
    if _f23b_applied:
        meta["trailing_zeros"] = _trailing_zeros_h
        meta.setdefault("drivers", []).append(
            f"F23b Heuristic trailing-zero drawdown: {_trailing_zeros_h} consecutive "
            f"zero weeks → baseline {_pre_f23b_baseline:.0f} × {_f23b_mult:.2f} = {baseline:.0f}"
        )
    meta["f23a_profile_damp"] = True
    meta["raw_peak_trough"] = round(_raw_peak_trough_h, 2)
    if _r9_applied:
        meta.setdefault("drivers", []).append(
            f"R9 Heuristic L52 ceiling: {_r9_pre_baseline:.0f} → {baseline:.0f} "
            f"(L52 avg {_l52_avg_r9:.0f} × 2.0)"
        )
    if _f9_applied:
        meta.setdefault("drivers", []).append(
            f"F9 high-volume sparse MAX baseline {baseline:.0f}"
        )
    if _f10_applied_h:
        meta.setdefault("drivers", []).append(
            f"declining: L4W avg {_l4_avg_f10h:.0f} < 70% L13 nz avg {_l13_nz_avg_f10h:.0f}"
        )
    if _f14a_override_h:
        _pos_l4_hm  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0) if pos_data else 0
        _pos_l13_hm = float(pos_data.get("Avg_Units_Wk_L13w") or 0) if pos_data else 0
        _ratio_hm = (_pos_l4_hm / _pos_l13_hm) if _pos_l13_hm > 0 else 0
        meta.setdefault("drivers", []).append(
            f"F14a POS-healthy override on F10: POS L4/L13={_ratio_hm:.2f} ≥ 0.50"
        )
    return forecast, round(baseline, 1), meta


def _build_alert(model, new, prior, pct, cap, mp, meta,
                 fcst=None, manual=None, row=None, history=None):
    """
    Plain-English planner-to-planner note. Target: 2–3 short sentences that
    read like a colleague talking about the account, NOT algorithm output.

    Format:
      [Gap] What the model expects vs what's planned, with risk in real terms.
      [Why] One line on actual ordering behavior at the account.
      [Flag] Biggest single thing to look at in the planned 26 weeks, if any.
    """
    fcst   = list(fcst or [])
    manual = list(manual or [])
    gap_pct = pct * 100
    cust_label = _friendly_cust_name(row.get("Customr_Name", "") if isinstance(row, dict) else "")

    # Inactive — one sentence.
    if model == "Inactive":
        return (f"No orders in 13+ weeks but the plan still shows "
                f"{int(prior):,} units forward. Treat as discontinued and "
                f"zero unless someone confirms a relaunch.")

    # 1) Gap + risk in one short sentence — talk like a planner, not a model.
    delta_units = abs(new - int(prior))
    if new > prior:
        gap_line = (f"Model is projecting {new:,} units over the next 26 "
                    f"weeks; the plan only has {int(prior):,} ({delta_units:,} "
                    f"under, +{gap_pct:.0f}%). If {cust_label} keeps ordering at "
                    f"this pace, expect to be chasing inventory.")
    else:
        gap_line = (f"Model is projecting {new:,} units over the next 26 "
                    f"weeks; the plan has {int(prior):,} ({delta_units:,} "
                    f"over, -{gap_pct:.0f}%). If orders cool off as the model "
                    f"expects, that's overstock building at {cust_label}.")

    # 2) One line on how the account actually buys.
    why = ""
    if model == "Seasonal Baseline":
        l13_avg = meta.get("l13_avg", 0)
        l26_avg = meta.get("l26_avg", 0)
        if l13_avg > l26_avg * 1.05:
            why = (f"Recent ordering has been running ~{l13_avg:,.0f}/wk and "
                   f"the rate is climbing.")
        elif l13_avg < l26_avg * 0.95:
            why = (f"Recent ordering has been running ~{l13_avg:,.0f}/wk and "
                   f"the rate is cooling.")
        else:
            why = f"Steady weekly buyer here -- last 13 weeks averaging ~{l13_avg:,.0f}/wk."
    elif model == "Croston's":
        n_l13 = meta.get("n_l13", 0)
        z     = meta.get("z", 0)
        why = (f"This account doesn't order every week — they buy in bursts "
               f"({n_l13} weeks out of the last 13, roughly {z:,.0f} units "
               f"per order).")
    elif model == "Heuristic":
        baseline = meta.get("baseline", 0)
        why = (f"Not a lot of history to lean on — using the recent pace of "
               f"~{baseline:,.0f}/wk to project forward.")

    # 3) Call out the single biggest thing to eyeball in the planned weeks.
    flag = _top_manual_defect(manual, fcst, cust_label=cust_label)

    parts = [gap_line]
    if why:  parts.append(why)
    if flag: parts.append(flag)
    return " ".join(parts)


def _top_manual_defect(manual, fcst, cust_label="this account"):
    """Return the single most salient pattern issue in the planned 26 weeks,
    in one planner-to-planner sentence.  Returns "" if the plan reads cleanly
    against actual ordering behavior.
    """
    if not manual or not fcst:
        return ""
    manual = (list(manual) + [0] * 26)[:26]
    fcst   = (list(fcst)   + [0] * 26)[:26]
    m_tot  = sum(manual)
    a_tot  = sum(fcst)
    cl = cust_label or "this account"

    # Flat-line placeholder beats every other flag.
    nz_m = [v for v in manual if v > 0]
    if nz_m:
        from collections import Counter
        cnt = Counter(nz_m)
        top_val, top_n = cnt.most_common(1)[0]
        if top_n >= 13 and len(cnt) <= 3:
            return (f"The plan is a flat {int(top_val):,}/wk across all active "
                    f"weeks — verify this reflects the intended demand "
                    f"distribution for {cl}.")

    # Unsupported spike weeks (planned >> history).
    spikes = [(i, manual[i], fcst[i]) for i in range(26)
              if manual[i] > fcst[i] * 2 and manual[i] > 0 and fcst[i] > 0]
    if spikes:
        over = sum(mv - av for _, mv, av in spikes)
        wks  = ", ".join(f"W{i+1}" for i, _, _ in spikes[:3])
        return (f"There's a planned bump in {wks} that's roughly {over:,}+ "
                f"units above what {cl}'s order pattern supports — make sure "
                f"a promo, feature, or new-store build is locked in for that "
                f"window.")

    # Under-planned weeks (history >> plan).
    dips = [(i, manual[i], fcst[i]) for i in range(26)
            if fcst[i] > manual[i] * 2 and manual[i] > 0 and fcst[i] > 0]
    if dips:
        under = sum(av - mv for _, mv, av in dips)
        wks   = ", ".join(f"W{i+1}" for i, _, _ in dips[:3])
        return (f"The plan in {wks} sits ~{under:,} units below {cl}'s recent "
                f"ordering pace — verify plan captures full expected demand "
                f"for those weeks.")

    # Blank weeks against an active ordering pattern.
    blanks = [i for i in range(26) if manual[i] == 0 and fcst[i] > 0]
    if 0 < len(blanks) <= 10:
        gap_vol = sum(int(fcst[i]) for i in blanks)
        return (f"{len(blanks)} weeks have nothing planned even though {cl} "
                f"is actively ordering — that's roughly {gap_vol:,} units of "
                f"demand the plan isn't budgeting for.")

    # Front/back-loaded skew.
    if m_tot > 0 and a_tot > 0:
        m_front = sum(manual[:13]) / m_tot
        a_front = sum(fcst[:13])   / a_tot
        if m_front - a_front > 0.15:
            return (f"The plan is leaning heavy on the first half "
                    f"({int(m_front*100)}% in W1-W13 vs {int(a_front*100)}% "
                    f"in the model) — make sure that frontload matches a real "
                    f"event at {cl}.")
        if a_front - m_front > 0.15:
            return (f"The plan is leaning light on the first half "
                    f"({int(m_front*100)}% in W1-W13 vs {int(a_front*100)}% "
                    f"in the model) — possible miss on near-term demand.")
    return ""


def _is_international_cust(cust_name):
    """R5 — detect non-US retailers that order in seasonal lumps."""
    cu = (cust_name or "").upper()
    return any(sub in cu for sub in INTERNATIONAL_CUST_SUBSTRS)


def _is_offprice_cust(cust_name):
    """R1 — whitelist hint; pattern gate still required to fire OTB."""
    cu = (cust_name or "").upper()
    return any(sub in cu for sub in OFFPRICE_CUST_SUBSTRS)


def _is_ecom_cust(cust_name):
    """T4 — Non-Amazon e-commerce retailer (Chewy, Petco.com, PetSmart.com)."""
    cu = (cust_name or "").upper()
    return any(sub in cu for sub in ECOM_CUST_SUBSTRS)


def _detect_otb(history, is_amazon=False, is_offprice=False, manual_total=None):
    """
    R1 — One-Time-Buy pattern detector.  Pure order-history signal.
    Returns (is_otb, meta_dict) where meta describes the pattern.

    Amazon gate (2026-05-07):
      Amazon is NEVER classified as OTB regardless of pattern.  Amazon
      ordering is centrally managed and even sparse-looking histories
      reflect ongoing replenishment, not one-time buys.  Amazon items
      that look "OTB-shaped" route through the standard Inactive recipe
      instead, which restarts forecasting the moment orders resume.

    Three detection paths:

    PATH A (tight, original R1):
      - L52W has ≤ 3 non-zero weeks
      - Top 1 order accounts for ≥ 55% of L52W total
      - Most-recent order is ≥ 12 weeks old

    PATH B (S2 extended, 2026-04-22):
      - L52W has 4–5 non-zero weeks
      - All non-zero orders fall within any contiguous 16-week window
      - Top 2 orders account for ≥ 75% of L52W total
      - Most-recent order is ≥ 12 weeks old
      - Catches seasonal one-time buys at off-price / closeout retailers
        whose nz count exceeds 3 but whose orders all clustered in one window.

    PATH C (P3 off-price hard-zero, 2026-05-24):
      - Customer is in OFFPRICE_CUST_SUBSTRS (Ross, Burlington, DD's, etc.)
      - L4W = 0  AND
      - manual_total <= 100  (planner already zeroed/near-zeroed it)
      - Off-price = closeout channel; once planner stops projecting, no
        replenishment.  Catches the long tail where PATH A/B didn't fire
        but the off-price channel is clearly closed.
    """
    # Amazon gate — Amazon items NEVER get the OTB recipe.
    if is_amazon:
        return False, {}

    # PATH C — off-price closeout (very fast, doesn't even compute nz)
    if is_offprice and manual_total is not None and manual_total <= 100:
        if len(history) >= 4 and sum(float(v) for v in history[-4:]) == 0:
            return True, {
                "nz_count":         sum(1 for v in history[-52:] if float(v) > 0),
                "weeks_since_last": 99,
                "l4_avg":           0,
                "path":             "C",
                "reason":           "off-price closeout (L4=0, manual<=100)",
            }

    # Fix E (2026-05-24): Off-price accounts — skip PATH A/B when planner
    # has a meaningful forward projection (manual_total > 100).  These customers
    # buy opportunistically; historical lumpiness triggers false OTB positives.
    # PATH C (L4W=0, manual<=100) already ran above and handles true closeouts.
    if is_offprice and manual_total is not None and manual_total > 100:
        return False, {}

    h52 = [float(v) for v in history[-52:]]
    nz  = [v for v in h52 if v > 0]
    if not nz:
        return False, {}
    total = sum(nz)
    if total == 0:
        return False, {}

    last_nz_idx = max(i for i, v in enumerate(h52) if v > 0)
    weeks_since_last = (len(h52) - 1) - last_nz_idx
    l4_avg = sum(history[-4:]) / 4 if len(history) >= 4 else 0

    # PATH A — tight one-time-buy (≤3 nz, top1 dominant)
    if len(nz) <= 3:
        top1 = max(nz)
        if top1 >= total * 0.55 and weeks_since_last >= 12:
            return True, {
                "nz_count":         len(nz),
                "top1_share":       top1 / total,
                "weeks_since_last": weeks_since_last,
                "l4_avg":           l4_avg,
                "path":             "A",
            }

    # PATH B — S2 seasonal one-time buy (4-5 nz clustered in 16w window)
    if 4 <= len(nz) <= 5 and weeks_since_last >= 12:
        nz_idxs = [i for i, v in enumerate(h52) if v > 0]
        span = nz_idxs[-1] - nz_idxs[0] + 1   # weeks from first to last nz
        if span <= 16:
            top2 = sum(sorted(nz, reverse=True)[:2])
            if top2 >= total * 0.75:
                return True, {
                    "nz_count":         len(nz),
                    "top2_share":       top2 / total,
                    "span_weeks":       span,
                    "weeks_since_last": weeks_since_last,
                    "l4_avg":           l4_avg,
                    "path":             "B",
                }

    return False, {}


def _prep_record_signals(row, master_pack, oos_entry=None,
                         amazon_pos=None, season_map=None,
                         amazon_catalog_us=None, ats_hist_l26=None,
                         retailer_pos=None):
    """
    Shared initial prep used by both forecast_record() and validate_record()
    (extracted 2026-05-06 to eliminate near-duplicate code between the two
    pipelines and ensure both always see identical history / customer / POS
    signals, including F35 stockout-backlog normalization).

    Returns a dict so callers can pull what they need.  Both pipelines used to
    duplicate this prep inline; centralizing means a future refinement that
    touches history extraction / F35 / customer-flag derivation can't drift
    between the two modes.
    """
    mp        = float(master_pack.get(row.get("Mstyle"), 1) or 1)
    hist      = get_history(row, oos_entry=oos_entry)
    cust_name = row.get("Customr_Name") or ""
    is_amazon = AMAZON_CUST_SUBSTR in cust_name.upper()
    # APL (Amazon Private Label): no consumer POS or DC inventory data.
    # is_amazon = False strips POS-blend rules (F15, F38, F59i/m/n, F79).
    # pos_data IS fetched for APL — Amazon_Catalog carries Ordered_Units_LW and
    # Ordered_Units_Prior_Wk (B2B order qty) even though consumer Avg_Units_Wk_*
    # fields are absent.  F81 uses these two fields as a recency anchor.
    is_apl    = APL_CUST_SUBSTR in cust_name.upper()
    if is_apl:
        is_amazon = False
    _fetch_pos = is_amazon or is_apl   # APL: fetch pos for B2B order fields only
    is_international = _is_international_cust(cust_name)
    pos_data  = (amazon_pos or {}).get(row.get("Mstyle", "")) if _fetch_pos else None
    # F59i-EC POS inheritance (Amazon only — APL items don't have EC variants)
    if is_amazon and pos_data is None:
        _pos_ms = (row.get("Mstyle") or "").upper()
        if _pos_ms.endswith("EC") or _pos_ms.endswith("COS") or _pos_ms.endswith("AMZ"):
            import re as _re
            _parent_ms = _re.sub(r'(?:EC|COS|AMZ)$', '', row.get("Mstyle", ""),
                                  flags=_re.IGNORECASE)
            _parent_pos = (amazon_pos or {}).get(_parent_ms)
            if _parent_pos and float(_parent_pos.get("Avg_Units_Wk_L13w") or 0) > 0:
                pos_data = _parent_pos
    # Forward lookup (Amazon only — APL variant suffixes not expected)
    if is_amazon and pos_data is None:
        _fwd_base_ms = row.get("Mstyle", "")
        for _fwd_sfx in ("AMZ", "EC", "COS", "DS"):
            _fwd_data = (amazon_pos or {}).get(_fwd_base_ms + _fwd_sfx)
            if _fwd_data and float(_fwd_data.get("Avg_Units_Wk_L13w") or
                                   _fwd_data.get("l13w") or 0) > 0:
                pos_data = _fwd_data
                break
    # F38 — Amazon Catalog US signals (buybox, MAP, AUR, OOS days, sellable
    # inventory, buyability flag).  Keyed by Mstyle (matches Mstyle_model_).
    amz_catalog = (amazon_catalog_us or {}).get(row.get("Mstyle", "")) if is_amazon else None
    # EC parent fallback for amz_catalog: same pattern as pos_data above.
    # EC/COS items may not appear in Amazon_Catalog_US under their own mstyle;
    # fall back to the parent so F59h WOS / F59m restock logic gets DC data.
    if is_amazon and amz_catalog is None:
        _amzcat_ms = (row.get("Mstyle") or "").upper()
        if _amzcat_ms.endswith("EC") or _amzcat_ms.endswith("COS") or _amzcat_ms.endswith("AMZ"):
            import re as _re2
            _amzcat_parent = _re2.sub(r'(?:EC|COS|AMZ)$', '', row.get("Mstyle", ""),
                                       flags=_re2.IGNORECASE)
            _parent_cat = (amazon_catalog_us or {}).get(_amzcat_parent)
            if _parent_cat:
                amz_catalog = _parent_cat
    # Forward lookup for amz_catalog: base style falls back to variant catalog entry.
    if is_amazon and amz_catalog is None:
        _fwd_base_ms2 = row.get("Mstyle", "")
        for _fwd_sfx2 in ("AMZ", "EC", "COS", "DS"):
            _fwd_cat = (amazon_catalog_us or {}).get(_fwd_base_ms2 + _fwd_sfx2)
            if _fwd_cat:
                amz_catalog = _fwd_cat
                break
    # Retailer POS lookup — non-Amazon customers only.
    # When retailer POS data is available, populate pos_data with the same
    # field names used by Amazon POS so the existing F15 blend (seasonal_baseline),
    # F18 (Croston's z-adjustment), and F43 (spike attenuation) fire naturally.
    # Amazon-specific rules (F13, F36, F38, F59h, etc.) are gated by is_amazon
    # and will NOT fire even though pos_data is set.
    rtl_pos = None
    if not is_amazon and not is_international and retailer_pos:
        _rtl_key = row.get("Acct_MStyle_Key_", "")
        if _rtl_key:
            _rtl_entry = retailer_pos.get(_rtl_key)
            if _rtl_entry and float(_rtl_entry.get("Avg_Units_Wk_L13w") or 0) > 0:
                rtl_pos  = _rtl_entry
                pos_data = _rtl_entry  # same field names as Amazon POS
    season    = (season_map or {}).get(row.get("Mstyle", "")) or None
    # F35 — Stockout backlog normalization.  Strip pent-up backlog from
    # post-stockout catch-up weeks so the rest of the pipeline sees real
    # demand intent, not "base + accumulated owe".
    hist, f35_corrections = normalize_stockout_recovery(hist)
    # VP-ATS — ATS-confirmed OOS zero-week fill.  Runs AFTER F35 (so F35
    # can first normalize any post-gap catch-up spike) and BEFORE F47/F41
    # (so those normalizations see ATS-corrected demand intent).
    hist, f_ats_corrections = normalize_ats_oos_weeks(hist, ats_hist_l26)
    # VP-ATS-Catch — cap post-OOS catch-up spike weeks.  Runs immediately
    # after VP-ATS so the cap sees ATS-filled (not raw-zero) history for
    # pre-OOS baseline, then the rest of the pipeline (F47/F41/F39/F43)
    # sees a clean signal without inflated catch-up orders.
    hist, f_ats_catch_corrections = normalize_ats_catchup_spikes(hist, ats_hist_l26)
    # F41 — Shipment-confirmed phantom-order dedupe.  Pull per-week ship
    # history and cross-check: if order N wasn't fulfilled within the 1-wk
    # lag window, a similar-qty order in N+1 / N+2 is a phantom reorder.
    # Runs BEFORE F39 because shipment evidence is the strongest signal —
    # it catches duplicates F39 would miss (>5% qty drift) and prevents
    # F39 from re-evaluating zeroed phantoms.
    ships = get_ship_history(row)
    # F47 — OOS rebuild-ramp normalization.  Runs BEFORE F41 so F47 sees
    # the raw "customer ordered through OOS" pattern; F41 would otherwise
    # zero some of those compounded orders as phantom and break F47's
    # gap-detection signal.  Once F47 has capped the rebuild weeks at the
    # pre-OOS baseline, F41/F39 must SKIP those weeks (else uniform caps
    # look like phantom reorders / duplicate-runs and get zeroed twice).
    hist, f47_corrections = normalize_oos_rebuild_ramp(hist, ships)
    _f47_protected = set()
    for _c in f47_corrections:
        _f47_protected.update(_c.get("capped_indices", []))
    hist, f41_corrections = normalize_phantom_orders(hist, ships, _f47_protected)
    # F39 — Duplicate-order run dedupe.  When the same large order qty
    # repeats ≥2 weeks in a row (buyer error / phantom PO), keep only the
    # first occurrence and zero the rest.  Operates on L26 window.
    hist, f39_corrections = normalize_duplicate_orders(hist, _f47_protected)
    # F43 — Recent-spike attenuation.  When the last 4 weeks contain a
    # spike >2.5× the prior L26 nz median, cap it to 2.0× to prevent
    # Croston's classifier from misreading a one-time event as a lumpy
    # pattern.  Runs AFTER F39 so already-deduped phantoms don't trip it.
    # F49 (2026-05-08): pass pos_data so F43 can skip when POS-confirmed
    # acceleration (l4/l13 ≥ 1.20) explains the recent "spikes".
    hist, f43_corrections = attenuate_recent_spikes(hist, pos_data=pos_data)
    return {
        "mp":               mp,
        "hist":             hist,
        "cust_name":        cust_name,
        "is_amazon":        is_amazon,
        "is_international": is_international,
        "pos_data":         pos_data,
        "amz_catalog":      amz_catalog,
        "rtl_pos":          rtl_pos,
        "season":           season,
        "f35_corrections":  f35_corrections,
        "f39_corrections":  f39_corrections,
        "f41_corrections":  f41_corrections,
        "f43_corrections":  f43_corrections,
        "f47_corrections":  f47_corrections,
        "f_ats_corrections":       f_ats_corrections,
        "f_ats_catch_corrections": f_ats_catch_corrections,
    }


# ─── F58: Tell-AI comment replay ──────────────────────────────────────────────
#
# Reads "AI Adjusted" entries from the QB Projection Comments table during
# `--all` forecast runs and applies the planner's intent as overrides on top
# of the model's AI forecast.  Same regex parser as the codepage's Tell-AI
# preview — so the saved comment "+25% in May for grooming" gets re-applied
# every run until the planner marks it Resolved or it ages out (60-day TTL).
#
# This closes the feedback loop: planners no longer have to re-tell the AI
# the same context every week.  Their accumulated knowledge persists in the
# Comments table and gets baked into each new forecast generation.

# Calendar mapping (mirror of viewer.js _MONTH_TO_WEEK_RANGE).  Months are
# resolved relative to the current 26-week forecast horizon (W1 = ORIG_PRJ_COLS[0]
# date).  When the forecast horizon shifts forward (Sunday roll-up), past
# months drop out and the parser returns an empty range — naturally expiring
# stale month-based adjustments.
_F58_MONTH_NAMES = ('may','jun','jul','aug','sep','oct')

def _f58_month_to_week_range(month_str):
    """Resolve 'May'/'June'/etc to (start_idx, end_idx) within current horizon.
    Returns None if the month doesn't fall in the current 26-week window."""
    if not month_str:
        return None
    s = str(month_str).lower()[:9]
    base = {
        'may':5, 'jun':6, 'june':6, 'jul':7, 'july':7,
        'aug':8, 'august':8, 'sep':9, 'sept':9, 'september':9,
        'oct':10, 'october':10, 'nov':11, 'november':11, 'dec':12, 'december':12,
        'jan':1, 'january':1, 'feb':2, 'february':2, 'mar':3, 'march':3,
        'apr':4, 'april':4,
    }.get(s)
    if base is None:
        return None
    # ORIG_PRJ_COLS[0] = "MM_DD_W1" — anchor for forecast week 1
    col0 = ORIG_PRJ_COLS[0] if ORIG_PRJ_COLS else "05_03_W1"
    try:
        anchor_mo = int(col0[0:2])
        anchor_dd = int(col0[3:5])
    except (ValueError, IndexError):
        anchor_mo, anchor_dd = 5, 3
    from datetime import date as _date_f58, timedelta as _td_f58
    today = _date_f58.today()
    # Determine forecast year (handles year wrap)
    fyear = today.year
    try:
        anchor_date = _date_f58(fyear, anchor_mo, anchor_dd)
    except ValueError:
        anchor_date = _date_f58(fyear, 5, 3)
    if (anchor_date - today).days < -180:
        anchor_date = _date_f58(fyear + 1, anchor_mo, anchor_dd)
    # Find this month's calendar window in the forecast year (or the next year
    # if the month already passed).  Map month days to week indices off anchor.
    try:
        first_of_month = _date_f58(anchor_date.year, base, 1)
    except ValueError:
        first_of_month = anchor_date
    if first_of_month < anchor_date:
        try:
            first_of_month = _date_f58(anchor_date.year + 1, base, 1)
        except ValueError:
            return None
    days_offset_start = (first_of_month - anchor_date).days
    # Last day of month
    if base == 12:
        last_of_month = _date_f58(first_of_month.year + 1, 1, 1) - _td_f58(days=1)
    else:
        last_of_month = _date_f58(first_of_month.year, base + 1, 1) - _td_f58(days=1)
    days_offset_end = (last_of_month - anchor_date).days
    w_start = days_offset_start // 7
    w_end   = days_offset_end // 7
    # Clamp to the 26-week window
    if w_end < 0 or w_start > 25:
        return None
    return (max(0, w_start), min(25, w_end))

def _f58_date_to_week_idx(date_str):
    """Resolve "Aug 14" or "8/14" to a forecast week index (0-25), or None."""
    if not date_str:
        return None
    import re as _re_f58
    s = str(date_str).lower().strip()
    mo, dd = None, None
    m = _re_f58.match(r'^(\d{1,2})/(\d{1,2})', s)
    if m:
        mo, dd = int(m.group(1)), int(m.group(2))
    else:
        m = _re_f58.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})', s)
        if m:
            mo_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,
                      'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
            mo = mo_map.get(m.group(1))
            dd = int(m.group(2))
    if not mo or not dd:
        return None
    col0 = ORIG_PRJ_COLS[0] if ORIG_PRJ_COLS else "05_03_W1"
    try:
        anchor_mo = int(col0[0:2]); anchor_dd = int(col0[3:5])
    except (ValueError, IndexError):
        anchor_mo, anchor_dd = 5, 3
    from datetime import date as _date_f58
    today = _date_f58.today()
    fyear = today.year
    try:
        anchor_date = _date_f58(fyear, anchor_mo, anchor_dd)
    except ValueError:
        anchor_date = _date_f58(fyear, 5, 3)
    if (anchor_date - today).days < -180:
        anchor_date = _date_f58(fyear + 1, anchor_mo, anchor_dd)
    try:
        target_date = _date_f58(anchor_date.year, mo, dd)
    except ValueError:
        return None
    if target_date < anchor_date:
        try:
            target_date = _date_f58(anchor_date.year + 1, mo, dd)
        except ValueError:
            return None
    days = (target_date - anchor_date).days
    w = days // 7
    if w < 0 or w > 25:
        return None
    return w


def _f58_parse_intent_tag(text, current_forecast):
    """Parse a CALENDAR-STABLE [ai-intent YYYY-MM-DD=N ...] tag if present.
    Each date is the W1 of a target week; we map each to the current 26-week
    horizon and write the value at that index.  Dates outside the horizon
    are silently skipped (the comment naturally expires as the rolling
    window advances past those dates).

    Returns (parsed, new_forecast, summary) like _f58_parse_comment.
    Returns (False, None, "") if the tag is absent.
    """
    import re as _re_f58
    if not text:
        return False, None, ""
    m = _re_f58.search(r'\[ai-intent\s+([^\]]+)\]', text)
    if not m:
        return False, None, ""
    body = m.group(1)
    pairs = _re_f58.findall(r'(\d{4}-\d{2}-\d{2})\s*=\s*(\d+)', body)
    if not pairs:
        return False, None, ""
    # Determine W1 anchor date for current horizon
    col0 = ORIG_PRJ_COLS[0] if ORIG_PRJ_COLS else "05_03_W1"
    try:
        anchor_mo = int(col0[0:2]); anchor_dd = int(col0[3:5])
    except (ValueError, IndexError):
        anchor_mo, anchor_dd = 5, 3
    from datetime import date as _date_f58
    today = _date_f58.today()
    fyear = today.year
    try:
        anchor = _date_f58(fyear, anchor_mo, anchor_dd)
    except ValueError:
        anchor = _date_f58(fyear, 5, 3)
    if (anchor - today).days < -180:
        anchor = _date_f58(fyear + 1, anchor_mo, anchor_dd)
    out = [int(v or 0) for v in current_forecast]
    n_applied = 0
    n_skipped_past = 0
    n_skipped_future = 0
    for iso, vstr in pairs:
        try:
            target = _date_f58.fromisoformat(iso)
        except ValueError:
            continue
        days_off = (target - anchor).days
        # Each week index spans 7 days.  Allow ±3 days slop for week alignment.
        widx = days_off // 7
        if widx < 0:
            n_skipped_past += 1
            continue
        if widx > 25:
            n_skipped_future += 1
            continue
        try:
            out[widx] = max(0, int(vstr))
            n_applied += 1
        except ValueError:
            continue
    if n_applied == 0:
        return False, None, (
            f"all {len(pairs)} target dates outside current horizon "
            f"({n_skipped_past} past, {n_skipped_future} future)"
        )
    summary = f"{n_applied} week(s) replayed by absolute date"
    if n_skipped_past or n_skipped_future:
        summary += f" ({n_skipped_past} past dates expired, {n_skipped_future} future dates beyond horizon)"
    return True, out, summary


def _f58_parse_comment(text, current_forecast):
    """Python port of viewer.js _parseAiAdjustment.  Same patterns + same
    behavior so the planner's comment replays produce the same adjustment
    shape that the codepage previewed.

    PREFERRED PATH: if the comment contains a calendar-stable [ai-intent ...]
    tag (added by the codepage / local viewer when applying), use that —
    it's date-anchored so the same calendar weeks always get adjusted, even
    as the rolling 26-week horizon moves forward week by week.

    Returns (parsed: bool, new_forecast: list, summary: str) — or (False, None, msg).
    """
    import re as _re_f58
    if not text or not current_forecast or len(current_forecast) != 26:
        return False, None, "no forecast"
    # Try the calendar-stable structured tag first.
    parsed, new_fcst, summary = _f58_parse_intent_tag(text, current_forecast)
    if parsed:
        return True, new_fcst, summary
    # Fall back to text-based regex parser.  Only fires for OLD comments saved
    # before the [ai-intent] encoding was added — those are still buggy w.r.t.
    # week-number drift, but the warning in the meta drivers makes it visible.
    t = str(text).strip()
    lo = t.lower()
    cur = [float(v or 0) for v in current_forecast]
    out = list(cur)
    def _clamp(n): return max(0, min(25, n - 1))
    def _round(v): return max(0, int(round(v)))
    NEG_RE = _re_f58.compile(r'cut|drop|decrease|reduction|down|los|reduce|lower|pull[\s]*back|trim|slow|soften')
    POS_RE = _re_f58.compile(r'lift|boost|bump|gain|increase|up|raise|grow|ramp\s*up')

    # Layer 0: Promo / event notification with pre-event order ramp
    # Fires when the planner describes an upcoming event (promo, launch,
    # holiday push, seasonal sale) with an expected demand lift.
    # Pattern: "[month] [event keyword] [Nx or +N% demand lift]"
    # Examples: "January promo 20% off — 1.2x lift expected"
    #           "Holiday push Dec — 1.5x demand"
    #           "Back-to-school promo Aug +30% lift"
    # Behavior: event weeks → baseline × lift; 5 weeks BEFORE the event each
    #           get +extraDemand/5 units front-loaded to build inventory in time.
    # "N% off" is treated as price discount and ignored; planner must state lift.
    _PROMO_RE  = _re_f58.compile(r'promo(?:tion)?|event\b|sale\b|deal\b|launch\b|push\b|program\b|campaign|holiday|seasonal|back[\s-]+to[\s-]+school')
    if _PROMO_RE.search(lo):
        RAMP_WKS = 5
        lift_mult = None
        lift_label = ''
        lm = _re_f58.search(r'(\d+(?:\.\d+)?)\s*x\s+(?:lift|demand|increase|boost|expect|up)', lo)
        if lm:
            lift_mult  = float(lm.group(1))
            lift_label = f'{lm.group(1)}x'
        if not lift_mult:
            lm = _re_f58.search(r'([+-]?\d+(?:\.\d+)?)\s*%\s*(?:lift|demand|increase|boost|up|expected)', lo)
            if lm:
                lift_mult  = 1 + float(lm.group(1)) / 100
                lift_label = f'+{float(lm.group(1)):.0f}%'
        if lift_mult and lift_mult > 1.0:
            _mo_pat = r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
            lm = _re_f58.search(_mo_pat + r'(?:\s*(?:to|through|[-–])\s*' + _mo_pat + r')?', lo)
            if lm:
                r1 = _f58_month_to_week_range(lm.group(1))
                r2 = _f58_month_to_week_range(lm.group(2)) if lm.group(2) else None
                if r1:
                    evt_start, _  = r1
                    _, evt_end    = r2 if r2 else r1
                    evt_label     = lm.group(1) + (f'-{lm.group(2)}' if lm.group(2) else '')
                    # Apply lift during event weeks
                    for i in range(evt_start, evt_end + 1):
                        out[i] = _round(cur[i] * lift_mult)
                    # Pre-event ramp: spread extra demand over RAMP_WKS before event
                    extra_total = sum(out[i] - cur[i] for i in range(evt_start, evt_end + 1))
                    ramp_start  = max(0, evt_start - RAMP_WKS)
                    ramp_end    = evt_start - 1
                    ramp_wks    = max(0, ramp_end - ramp_start + 1) if ramp_end >= ramp_start else 0
                    if ramp_wks > 0 and extra_total > 0:
                        extra_per_wk = int(round(extra_total / ramp_wks))
                        for i in range(ramp_start, ramp_end + 1):
                            out[i] = _round(cur[i] + extra_per_wk)
                    ramp_desc = (f"pre-event ramp W{ramp_start+1}-W{ramp_end+1} "
                                 f"(+{round(extra_total/max(1,ramp_wks)):,}u/wk)"
                                 if ramp_wks > 0 else "no ramp window")
                    summary = (f"Event: {lift_label} lift in {evt_label} "
                               f"(W{evt_start+1}-W{evt_end+1}); {ramp_desc}")
                    return True, [int(v) for v in out], summary

    # Layer 1a: explicit Wn EOL
    m = _re_f58.search(r'(?:eol|wind[-\s]*down|discontinu(?:e|ed|ing)|phase[-\s]*out|end[-\s]*of[-\s]*life)[^\d]*w?(\d{1,2})', lo)
    if m:
        tgt = _clamp(int(m.group(1)))
        taper = {0:0.25, 1:0.45, 2:0.65, 3:0.85}
        for i in range(26):
            if i > tgt: out[i] = 0
            else:
                d = tgt - i
                if d in taper: out[i] = _round(cur[i] * taper[d])
        return True, [int(v) for v in out], f"EOL by W{tgt+1} (4-week taper, zero after)"

    # Layer 1b: zero / PO covers Wa[-Wb]
    # If an explicit range is given (W13-W26) use it; if "starting/from" precedes
    # the week number and no range end is given, auto-extend to W26.
    m = _re_f58.search(r'(?:zero|no\s*orders?|po\s*covers?|covered\s*by\s*po)[^\d]*w?(\d{1,2})(?:\s*[-–]\s*w?(\d{1,2}))?', lo)
    if m:
        a = _clamp(int(m.group(1)))
        b = _clamp(int(m.group(2))) if m.group(2) else a
        # "zero starting W13" / "no orders from W13" → extend to W26
        if not m.group(2):
            _ctx = lo[:m.start(1)]
            if _re_f58.search(r'starting|from\s+w|beginning', _ctx):
                b = 25
        for i in range(a, b + 1): out[i] = 0
        return True, [int(v) for v in out], f"Zero W{a+1}{f'-W{b+1}' if b!=a else ''}"

    # Layer 1b-2: "zero/reduce-to-zero + starting/from Wn" → zero W[n]–W26
    # Catches comments like "Transitioning to EC suffix starting W13 — reduce orders to zero"
    # where the zero keyword comes *after* the week reference.
    _HAS_ZERO_SIG = _re_f58.search(
        r'zero|no[\s-]*orders?|reduce[^\n]{0,40}?zero|set[^\n]{0,40}?zero', lo)
    if _HAS_ZERO_SIG:
        m = _re_f58.search(
            r'(?:starting|from|after|beginning)\s+w?(?:k|eek)?\s*(\d{1,2})', lo)
        if not m:
            m = _re_f58.search(
                r'w(?:k|eek)?\s*(\d{1,2})\s*(?:and\s+)?(?:beyond|forward|onward)', lo)
        if m:
            a = _clamp(int(m.group(1)))
            for i in range(a, 26): out[i] = 0
            return True, [int(v) for v in out], f"Zero W{a+1}-W26 (starting W{a+1})"

    # Layer 1c: set N/wk for Wa[-Wb]
    m = _re_f58.search(r'(?:set|baseline|target|hold[\s]+at|run\s*rate)[^\d]*([\d,]+)\s*(?:u(?:nits?)?\s*\/?\s*wk|\/\s*wk|per\s*wk|per\s*week|units|u)?(?:[^\d]*w?(\d{1,2}))?(?:\s*[-–]\s*w?(\d{1,2}))?', lo)
    if m:
        try: base_n = int(round(float(m.group(1).replace(',', ''))))
        except ValueError: base_n = 0
        if base_n > 0:
            a = _clamp(int(m.group(2))) if m.group(2) else 0
            b = _clamp(int(m.group(3))) if m.group(3) else 25
            for i in range(a, b + 1): out[i] = base_n
            return True, [int(v) for v in out], f"Set {base_n}/wk W{a+1}-W{b+1}"

    # Layer 1d: week-first pct ("adjust W14 by 50%", "W22-W26 +30%")
    m = _re_f58.search(r'(?:adjust|change|update|set|lift|cut|boost|bump|gain|drop|raise|reduce|increase|decrease)?\s*w(?:k|eek)?\s*(\d{1,2})(?:\s*[-–]\s*w(?:k|eek)?\s*(\d{1,2}))?[^\d%]*([+-]?)\s*(\d+(?:\.\d+)?)\s*%', lo)
    if m:
        sign = -1 if m.group(3) == '-' else 1
        if NEG_RE.search(lo): sign = -1
        if POS_RE.search(lo): sign = 1
        pct = float(m.group(4))
        a = _clamp(int(m.group(1)))
        b = _clamp(int(m.group(2))) if m.group(2) else a
        mult = 1 + sign * (pct / 100)
        for i in range(a, b + 1): out[i] = _round(cur[i] * mult)
        return True, [int(v) for v in out], f"{'+' if sign>0 else '-'}{pct:.0f}% W{a+1}{f'-W{b+1}' if b!=a else ''}"

    # Layer 1e: pct-first percent + week ("+25% W8-W26")
    m = _re_f58.search(r'([+-]?)\s*(\d+(?:\.\d+)?)\s*%\s*(?:lift|boost|bump|gain|increase|cut|drop|decrease|reduction|down|up)?[^\dwW]*(?:starting|from|in|on|for|across)?[^\dwW]*w?(\d{1,2})(?:\s*[-–]\s*w?(\d{1,2}))?', lo)
    if m:
        sign = -1 if m.group(1) == '-' else 1
        if NEG_RE.search(lo): sign = -1
        if POS_RE.search(lo): sign = 1
        pct = float(m.group(2))
        a = _clamp(int(m.group(3)))
        b = _clamp(int(m.group(4))) if m.group(4) else 25
        mult = 1 + sign * (pct / 100)
        for i in range(a, b + 1): out[i] = _round(cur[i] * mult)
        return True, [int(v) for v in out], f"{'+' if sign>0 else '-'}{pct:.0f}% W{a+1}-W{b+1}"

    # Layer 2a: date EOL ("EOL by Aug 14")
    m = _re_f58.search(r'(?:eol|wind[-\s]*down|discontinu(?:e|ed|ing)|phase[-\s]*out|end[-\s]*of[-\s]*life)[^\d]*((?:\d{1,2}/\d{1,2})|(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}))', lo)
    if m:
        tgt = _f58_date_to_week_idx(m.group(1))
        if tgt is not None:
            taper = {0:0.25, 1:0.45, 2:0.65, 3:0.85}
            for i in range(26):
                if i > tgt: out[i] = 0
                else:
                    d = tgt - i
                    if d in taper: out[i] = _round(cur[i] * taper[d])
            return True, [int(v) for v in out], f"EOL by {m.group(1)} (≈W{tgt+1})"

    # Layer 2b: month + pct (pct-first or month-first)
    month_re = r'(may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?)'
    m = _re_f58.search(rf'(?:adjust|change|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss)?\s*([+-]?)\s*(\d+(?:\.\d+)?)\s*%[^a-z]*(?:in|for|across|throughout|during|of)?\s*{month_re}(?:[^a-z]*(?:to|through|until|–|-)[^a-z]*{month_re})?', lo)
    if not m:
        m2 = _re_f58.search(rf'(?:adjust|change|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss)?\s*{month_re}(?:[^a-z]*(?:to|through|until|–|-)[^a-z]*{month_re})?[^\d%]*([+-]?)\s*(\d+(?:\.\d+)?)\s*%', lo)
        if m2:
            class _M:  # adapter to access groups by index
                def __init__(self, raw): self.raw = raw
                def group(self, i):
                    g = self.raw.groups()
                    return [None, g[2], g[3], g[0], g[1]][i] if i <= 4 else None
            m = _M(m2)
    if m:
        sign = -1 if m.group(1) == '-' else 1
        if NEG_RE.search(lo): sign = -1
        if POS_RE.search(lo): sign = 1
        pct = float(m.group(2))
        r1 = _f58_month_to_week_range(m.group(3))
        r2 = _f58_month_to_week_range(m.group(4)) if m.group(4) else None
        if r1:
            a = r1[0]
            b = r2[1] if r2 else r1[1]
            mult = 1 + sign * (pct / 100)
            for i in range(a, b + 1): out[i] = _round(cur[i] * mult)
            return True, [int(v) for v in out], f"{'+' if sign>0 else '-'}{pct:.0f}% {m.group(3)}{f'-{m.group(4)}' if m.group(4) else ''}"

    # Layer 2c: ramp up/down + month
    m = _re_f58.search(rf'(?:ramp\s*up|increase|grow|boost|build|lift|expand|gain|gained|adding|added|distribution[\s-]*gain).*?(?:starting|beginning|from|in)\s+{month_re}', lo)
    if m:
        r = _f58_month_to_week_range(m.group(1))
        if r:
            pct_m = _re_f58.search(r'(\d+)\s*%', lo)
            lift = float(pct_m.group(1)) / 100 if pct_m else 0.20
            for i in range(r[0], 26): out[i] = _round(cur[i] * (1 + lift))
            return True, [int(v) for v in out], f"+{lift*100:.0f}% from {m.group(1)} (W{r[0]+1}-W26)"
    m = _re_f58.search(rf'(?:ramp\s*down|decrease|cut|reduce|wind\s*down|slow)[^a-z]*(?:starting|beginning|from|in)\s+{month_re}', lo)
    if m:
        r = _f58_month_to_week_range(m.group(1))
        if r:
            pct_m = _re_f58.search(r'(\d+)\s*%', lo)
            cut = float(pct_m.group(1)) / 100 if pct_m else 0.20
            for i in range(r[0], 26): out[i] = _round(cur[i] * (1 - cut))
            return True, [int(v) for v in out], f"-{cut*100:.0f}% from {m.group(1)} (W{r[0]+1}-W26)"

    # Layer 2d: multipliers
    m = _re_f58.search(r'(double|triple|quadruple|halve)\s+(?:this[^a-z]*account|the[^a-z]*forecast|w(?:k|eek)?\s*(\d{1,2})|(?:in\s+)?([a-z]+))?', lo)
    if m:
        verbMul = {'double':2.0, 'triple':3.0, 'quadruple':4.0, 'halve':0.5}
        mult = verbMul[m.group(1)]
        a, b = 0, 25
        if m.group(2):
            a = b = _clamp(int(m.group(2)))
        elif m.group(3):
            r = _f58_month_to_week_range(m.group(3))
            if r: a, b = r
        for i in range(a, b + 1): out[i] = _round(cur[i] * mult)
        return True, [int(v) for v in out], f"{m.group(1).capitalize()} (×{mult})"

    # Layer 2e: absolute units
    sign_abs = 1
    m = _re_f58.search(r'(?:increase|boost|add|lift|raise|grow)[^\d]*(?:by\s+)?(\d+(?:,\d{3})*)\s*(?:units?\s*\/?\s*wk|\/\s*wk|per\s*wk|per\s*week|units?|u)', lo)
    if not m:
        m = _re_f58.search(r'(?:decrease|cut|drop|lower|reduce|subtract)[^\d]*(?:by\s+)?(\d+(?:,\d{3})*)\s*(?:units?\s*\/?\s*wk|\/\s*wk|per\s*wk|per\s*week|units?|u)', lo)
        if m: sign_abs = -1
    if m:
        try: incr = int(m.group(1).replace(',', '')) * sign_abs
        except ValueError: incr = 0
        a, b = 0, 25
        mm = _re_f58.search(rf'(?:in|through|across|during)\s+{month_re}(?:[^a-z]*(?:to|through|until|-)[^a-z]*{month_re})?', lo)
        if mm:
            r1 = _f58_month_to_week_range(mm.group(1))
            r2 = _f58_month_to_week_range(mm.group(2)) if mm.group(2) else None
            if r1: a, b = r1[0], (r2[1] if r2 else r1[1])
        for i in range(a, b + 1): out[i] = max(0, cur[i] + incr)
        return True, [int(v) for v in out], f"{'add' if sign_abs>0 else 'subtract'} {abs(incr)}/wk W{a+1}-W{b+1}"

    # Layer 2f: whole-period pct
    m = _re_f58.search(r'(?:adjust|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss|bump|grow)[^\d]*([+-]?)\s*(\d+(?:\.\d+)?)\s*%', lo)
    if not m:
        m = _re_f58.search(r'([+-])\s*(\d+(?:\.\d+)?)\s*%', lo)
    if m:
        sign = -1 if m.group(1) == '-' else 1
        if NEG_RE.search(lo): sign = -1
        if POS_RE.search(lo): sign = 1
        pct = float(m.group(2))
        if pct > 0:
            mult = 1 + sign * (pct / 100)
            for i in range(26): out[i] = _round(cur[i] * mult)
            return True, [int(v) for v in out], f"{'+' if sign>0 else '-'}{pct:.0f}% all 26 weeks"

    return False, None, "no pattern matched"


def _f58_fetch_active_comments(lookback_days=60):
    """Pull active AI adjustment comments from the dedicated QB AI Comments
    table (separate from the mgr-facing Projection Comments table).  Returns
    a dict: {acct_mstyle_key: most_recent_comment_text}.  Excludes rows
    where [Ignored]=true (× Ignore button) or older than lookback_days.

    Schema (table dbid `bv2jirwts` in the InventoryTrack app):
      [Acct#-MStyle]  text  fk → Projections
      [Note]          multi-line text  (planner instruction + [ai-intent ...] tag)
      [Author]        user  (auto-stamped on insert)
      [Ignored]       checkbox  (× Ignore flips to true to retire the row)
      [Date Created]  built-in
    """
    from datetime import date as _date_f58, timedelta as _td_f58
    cutoff = (_date_f58.today() - _td_f58(days=lookback_days)).isoformat()
    sql = (
        f"SELECT [Acct_MStyle], [Note], [Date_Created] "
        f"FROM [Quickbase1].[InventoryTrack].[AI_Comments] "
        f"WHERE [Date_Created] >= '{cutoff}' "
        f"  AND ([Ignored] = 0 OR [Ignored] IS NULL) "
        f"ORDER BY [Date_Created] ASC"
    )
    try:
        rows = cdata_query(sql, description="F58 AI Comments")
    except Exception as e:
        print(f"  [F58] Could not fetch AI Comments — comment-replay disabled this run: {e}")
        return {}
    by_key = {}
    for r in rows or []:
        key  = (r.get("Acct_MStyle") or r.get("Acct#-MStyle") or "").strip()
        note = (r.get("Note") or "").strip()
        if not key or not note:
            continue
        # Tolerant: legacy entries from Projection Comments may still carry
        # the '[AI-adjusted]' prefix.  Strip it when present.
        if note.lower().startswith("[ai-adjusted]"):
            note = note[len("[AI-adjusted]"):].strip()
        # ASC sort means later iterations overwrite earlier — most-recent wins
        by_key[key] = note
    print(f"  [F58] Loaded {len(by_key)} active AI adjustments (last {lookback_days} days)")
    return by_key


def forecast_record(row, master_pack, account_interval=None, amazon_pos=None,
                    season_map=None, oos_entry=None, open_po_wk=None,
                    amazon_catalog_us=None, ai_comments=None, ats_hist=None,
                    switchover_weeks=None, variant_zero_weeks=None,
                    retailer_pos=None):
    # Reset rule-fire tracker for this record (used by deck-harvest tooling).
    _start_rule_fires()
    # Shared prep (mp, hist + F35 stockout normalization, customer flags,
    # POS lookup, season tag) — kept identical across forecast and validate
    # via _prep_record_signals().
    _sig = _prep_record_signals(row, master_pack, oos_entry=oos_entry,
                                amazon_pos=amazon_pos, season_map=season_map,
                                amazon_catalog_us=amazon_catalog_us,
                                ats_hist_l26=ats_hist,
                                retailer_pos=retailer_pos)
    mp               = _sig["mp"]
    hist             = _sig["hist"]
    cust_name        = _sig["cust_name"]
    is_amazon        = _sig["is_amazon"]
    is_international = _sig["is_international"]
    pos_data         = _sig["pos_data"]
    amz_catalog      = _sig["amz_catalog"]
    season           = _sig["season"]
    _f35_corrections  = _sig["f35_corrections"]
    _f39_corrections  = _sig.get("f39_corrections")  or []
    _f41_corrections  = _sig.get("f41_corrections")  or []
    _f43_corrections  = _sig.get("f43_corrections")  or []
    _f47_corrections  = _sig.get("f47_corrections")  or []
    _f_ats_corrections = _sig.get("f_ats_corrections") or []
    if _f_ats_corrections:
        _fire("VP-ATS")
    _f_ats_catch_corrections = _sig.get("f_ats_catch_corrections") or []
    if _f_ats_catch_corrections:
        _fire("VP-ATS-Catch")
    l13w             = float(row.get("Shpd_Wk_L13W_cust_") or 0)
    rtl_pos          = _sig.get("rtl_pos")

    # R4 (Amazon Private Label skip) removed 2026-05-05.  APL items ARE shipped;
    # they now flow through the standard classification path below like any
    # other Amazon record.

    # F60 — EC-transition flag (2026-05-15).
    # History was already inherited from the parent mstyle in the main-loop
    # pre-pass.  Flag here so _fire() tags rule_fires; driver narrative is
    # added in the F59/post-model block below where `meta` is available.
    _f60_is_ec_transition = bool(row.get("_ec_transition"))
    if _f60_is_ec_transition:
        _fire("F60")

    # F34 — Pre-launch-zeros detection (2026-05-05).
    #
    # When weeks 27-51 ago are essentially empty (sum < 1% of L26 sum) but
    # the last 26 weeks have real activity, the item is a NEW LAUNCH that
    # only started shipping ~26 weeks ago.  The leading zeros are NOT a
    # "decline reference" — they're pre-launch noise.
    #
    # Effects (applied downstream where flagged):
    #   • Skip F10 decline detection (the YoY check compares against
    #     pre-launch zeros — meaningless)
    #   • Skip M1 L52×1.25 ceiling (caps near zero × 1.25 = trivial)
    #   • For Croston's z and p: anchor on weeks-since-first-activity only
    #   • Tag the alert so planners see why decline rules were skipped
    _f34_l52_26_sum = sum(float(v or 0) for v in hist[-52:-26]) if len(hist) >= 52 else 0
    _f34_l26_sum   = sum(float(v or 0) for v in hist[-26:])   if len(hist) >= 26 else 0
    _f34_is_new_launch = (
        _f34_l26_sum > 0 and
        _f34_l52_26_sum < 0.01 * _f34_l26_sum
    )
    # Compute weeks-since-first-activity for the alert text
    _f34_first_nz_idx = None
    if _f34_is_new_launch:
        for _i, _v in enumerate(hist):
            if float(_v or 0) > 0:
                _f34_first_nz_idx = _i
                break
    _f34_active_weeks = (len(hist) - _f34_first_nz_idx) if _f34_first_nz_idx is not None else 0

    # F73 (2026-05-24) — New-launch recency anchor.
    # F34 only fires when weeks 27-51 are < 1% of the L26 sum (i.e., item
    # launched within the last ~26 weeks with nearly empty prior-year history).
    # F73 broadens the new-launch flag to also cover items where Status_Cust
    # contains 'NEW' and the item still has <= 13 active non-zero weeks, so
    # mid-ramp items that cleared the F34 threshold still get the same
    # recency treatment.  Both paths share the same downstream effect: Fix 5
    # inside crostens() anchors against the L13W nz-avg instead of the
    # all-weeks avg (which is zero-diluted and pulls the forecast too low).
    _f73_l26_nz_wks  = sum(1 for v in hist[-26:] if float(v or 0) > 0)
    _f73_sc_new      = "NEW" in (row.get("Status_Cust") or "").upper()
    _f73_new_ramp    = (
        _f34_is_new_launch                                  # F34: leading-zero pattern
        or (_f73_sc_new and _f73_l26_nz_wks <= 13)         # Status=NEW, still ramping
    )

    # R1 — One-Time-Buy detection (2026-04-22).  Off-price / closeout retailers
    # (Burlington, Ross, Kohl's, CVS closeout, Variety Wholesalers, etc.) often
    # have L52W history of 1-4 big orders with nothing in between.  Sparse
    # Intermittent interprets these as cadence and multiplies them across the
    # 26-week window.  Detect via pure history pattern (top 1-2 orders ≥ 70%
    # of L52 total, ≤ 4 non-zero weeks) and route to:
    #   - Zero forecast if most-recent order is ≥ 8 weeks old
    #   - Single L4W-avg order at W1-W4 if recent order happened
    # P3 (2026-05-24): pass is_offprice + manual_total so PATH C off-price
    # hard-zero can fire on records that PATH A/B miss.
    _otb_is_offprice = _is_offprice_cust(cust_name)
    _otb_manual_total = sum(float(row.get(c) or 0) for c in ORIG_PRJ_COLS)
    _otb_is, _otb_meta = _detect_otb(hist, is_amazon=is_amazon,
                                     is_offprice=_otb_is_offprice,
                                     manual_total=_otb_manual_total)
    if _otb_is:
        _otb_forecast = [0] * 26
        _otb_model = "OTB (zero)"
        if (_otb_meta.get("weeks_since_last", 0) < 8
                and _otb_meta.get("l4_avg", 0) > 0
                and _otb_meta.get("path") != "C"):
            _otb_forecast[0] = snap(_otb_meta["l4_avg"], mp)
            _otb_model = "OTB (W1 single)"
        _otb_manual = [float(row.get(c) or 0) for c in ORIG_PRJ_COLS]
        _otb_prior = sum(_otb_manual)
        _otb_new = sum(_otb_forecast)
        return {
            "key":         row.get("Acct_MStyle_Key_", ""),
            "mstyle":      row.get("Mstyle", ""),
            "cust":        cust_name,
            "mp":          mp,
            "model":       _otb_model,
            "biweekly":    False,
            "iso":         False,
            "iso_settle":  False,
            "forecast":    _otb_forecast,
            "manual":      _otb_manual,
            "cap_base":    0,
            "new_total":   _otb_new,
            "prior_total": _otb_prior,
            "pct_diff":    abs(_otb_new - _otb_prior) / _otb_prior * 100 if _otb_prior > 0 else 0,
            "alert":       (
                f"R1 OTB (path {_otb_meta.get('path','A')}): {_otb_meta.get('nz_count', 0)} nz weeks, "
                + (
                    f"top1 {_otb_meta.get('top1_share', 0)*100:.0f}% of L52"
                    if _otb_meta.get('path') == 'A'
                    else f"top2 {_otb_meta.get('top2_share', 0)*100:.0f}% of L52, "
                         f"{_otb_meta.get('span_weeks', 0)}w span"
                    if _otb_meta.get('path') == 'B'
                    else _otb_meta.get('reason', 'off-price closeout')   # PATH C
                )
                + f", last order {_otb_meta.get('weeks_since_last', 0)}w ago"
            ),
        }

    # Detect ISO before any classification so we can strip the stocking spike
    # from the history used for baseline/nz_rate calculations.
    iso   = detect_iso(hist)
    if iso["is_iso"]:
        # Zero out the ISO week so it doesn't inflate baselines or nz_rate
        hist_for_model = list(hist)
        hist_for_model[iso["iso_week_idx"]] = 0.0
    else:
        hist_for_model = hist

    description = (row.get("Description") or "").strip()
    # F8 — additional category/brand fields for broader category matching
    product_category    = (row.get("Product_Category") or "").strip()
    product_subcategory = (row.get("Product_Subcategory") or "").strip()
    brand               = (row.get("Brand") or "").strip()
    brand_pt            = (row.get("Brand_PT_") or "").strip()

    pattern  = classify(hist_for_model)

    # R5 — International bulk-buyer relaxation (2026-04-22).  Retailers like
    # Petbarn (Australia), Loblaws (Canada), Comercializadora/Grup (Mexico)
    # order in seasonal lumps so L13W=0 is common.  If classify() routed to
    # inactive BUT the item has L26W activity, push it to sparse_intermittent
    # instead so the model projects a small seasonal re-buy.
    # T3 (2026-04-22): If L26 is empty but L52 has ≥3 non-zero weeks,
    # escape Inactive by routing to Heuristic (post-ramp avg will build a
    # sensible baseline from scattered L52 events).  Addresses Petbarn −58%
    # (121 of 155 Inactive recs = −85K) and Wakefern −29% (5 Inactive = −53K).
    if is_international and pattern == "inactive":
        _l26_nz_intl = sum(1 for v in hist_for_model[-26:] if v > 0)
        _l52_nz_intl = sum(1 for v in hist_for_model if v > 0)
        if _l26_nz_intl >= 1:
            pattern = "sparse_intermittent"
        elif _l52_nz_intl >= 3:
            # T3 escape: route to heuristic (its branch runs forecast through
            # heuristic() which will handle the sparse L52 history via post-ramp
            # avg / non-zero fallbacks).  We flag pattern="sparse_intermittent"
            # which is routed to heuristic() in the F6c branch below.
            pattern = "sparse_intermittent"

    nz_rate_ = nz_rate(hist_for_model, window=26)   # fraction of non-zero weeks over L26W
    is_dense   = nz_rate_ >= DENSE_THRESHOLD    # ≥ 35%: semi-regular ordering
    is_croston = nz_rate_ >= CROSTON_THRESHOLD  # ≥ 25%: intermittent (every 2–5 wks)

    # F-B (2026-04-22, updated 2026-05-24) — L13 burst-cadence override.
    # Original intent: catch lumpy accounts sitting at the 50% L26W threshold
    # (distributors, international, etc.) where orders come in bursts.
    # Updated for DENSE_THRESHOLD=0.35: items in the 35-49% range naturally
    # carry off-season zeros; the old >=4 threshold would always downgrade
    # them to Croston's, defeating the new threshold.  Raised to >=8 zeros
    # (>60% of L13W empty) so only truly sporadic recent behavior triggers
    # a downgrade -- seasonal off-months (4-7 zeros) stay on Seasonal Baseline.
    # ISO-protected so bi-weekly dense accounts are never misrouted.
    _l13_nz_count_fb = sum(1 for v in hist_for_model[-13:] if v > 0)
    _l13_zero_count_fb = 13 - _l13_nz_count_fb
    if is_dense and _l13_zero_count_fb >= 8 and not iso["is_iso"]:
        is_dense = False
        # nz_rate_ is still >= DENSE_THRESHOLD so is_croston=True if >= CROSTON_THRESHOLD

    # F44 — F43-aware dense override (2026-05-06).
    # When F43 capped recent-spike outliers, the L13 zero count is suspect:
    # those zeros may be artifacts of the same disruption that caused the
    # spike (customer's normal cadence broke, then placed catch-up orders).
    # If the customer was dense BEFORE the disruption (≥60% nz in L26
    # excluding last 4w), trust the longer-term steady pattern over the
    # recent noise and force is_dense=True.  This routes through
    # Holt-Winters (smooth weekly distribution) instead of Croston's
    # (lumpy event placement that bunches forecast at W10/W11).
    #
    # Only fires when F43 already attenuated the spike — which is itself
    # gated on having a stable prior baseline — so the condition is narrow.
    # F49 guard: don't fire F44 on F43-skip markers (they're not actual caps)
    _f43_actually_fired = (_f43_corrections and
                           not _f43_corrections[0].get("f49_skip"))
    if _f43_actually_fired and not is_dense and not iso["is_iso"]:
        _l26_prior_f44 = hist_for_model[-26:-4] if len(hist_for_model) >= 26 else []
        _l26_prior_nz_f44 = sum(1 for v in _l26_prior_f44 if v > 0)
        _l26_prior_frac_f44 = _l26_prior_nz_f44 / max(len(_l26_prior_f44), 1)
        if _l26_prior_frac_f44 >= 0.60:
            is_dense = True
            # Restore steady routing: Croston's classifier saw recent zeros +
            # spike as lumpy; F44 overrides that based on prior-disruption
            # baseline showing dense weekly orders.
            _f44_fired_meta = {
                "l26_prior_nz":     _l26_prior_nz_f44,
                "l26_prior_total":  len(_l26_prior_f44),
                "l26_prior_frac":   round(_l26_prior_frac_f44, 3),
            }
        else:
            _f44_fired_meta = None
    else:
        _f44_fired_meta = None

    # Fix 2 — ISO routing: if the stocking spike is within the last 26 weeks the item
    # is still in the post-ISO settle period.  Croston's z is contaminated by the large
    # spike value even after we zero it out (the weighted series still biases z high).
    # Force Heuristic so the forecast is based on the post-ISO trickle rate instead.
    force_heuristic = (
        iso["is_iso"] and
        iso.get("weeks_since_iso", 999) <= 26 and
        pattern != "inactive"
    )

    # F5 — PT_Item_Status routing override.  If the item is explicitly
    # flagged "Launching / New / Pilot" in PT_Item_Status and classify()
    # decided Inactive purely because L13 = 0, skip Inactive so the F1/F2/F3
    # fallback runs (it lives in the pattern=="inactive" branch below).
    # EOL-tagged items keep the Inactive routing (we want zero or decayed
    # forecast there, not a family-scaled floor).
    if pattern == "inactive" and _is_launching(row) and not _is_eol(row):
        # Stay in the inactive branch (so F1/F2/F3 fallback fires) — just
        # record the signal for alerting and don't early-return.
        pass

    if pattern == "inactive":
        fcst, cap, meta, model = [0] * 26, 0, {}, "Inactive"
        biweekly = False

        # F65 — Zero-velocity suppression (2026-05-17).
        # When BOTH L4W and L13W are completely zero, and the item is not a
        # new launch or international account, skip R3/S6/F19 floors entirely.
        # These items have no recent demand signal whatsoever; projecting any
        # floor volume adds noise without evidence of continued need.
        # P3 (2026-05-24): also force-zero for off-price customers when
        # zero-velocity, regardless of M1/R3/F19 floors that would otherwise
        # backfill. Off-price = closeout channel; no floor needed.
        _zero_velocity = (
            sum(float(v or 0) for v in hist_for_model[-13:]) == 0 and
            sum(float(v or 0) for v in hist_for_model[-4:])  == 0 and
            not _f34_is_new_launch and
            not is_international and
            not _is_launching(row)
        )
        _f65_zero_vel = _zero_velocity

        if _zero_velocity:
            # Keep fcst = [0]*26, model = "Inactive" — no floors.
            meta = {
                "model":   "Inactive",
                "drivers": [
                    "F65 Zero-velocity suppression: L4W and L13W both zero → "
                    "no AI floor projection (no recent demand signal)"
                ],
            }

        # R3 — Inactive conservative L26 floor (2026-04-22).
        # F65 gate: skip all floors when zero-velocity suppression fires.
        # When the item is Inactive (L13W all zero) BUT has meaningful L26W/L52W
        # activity, plane is likely "paused" not "dead".  Provide a small flat
        # floor forecast = L26W all-weeks avg × 0.3, snapped to master pack.
        # Skip for Halloween / July 4th seasonal tags (legitimately zero off-season).
        _r3_l26_avg = (sum(hist_for_model[-26:]) / 26) if len(hist_for_model) >= 26 else 0
        _r3_l26_nz  = sum(1 for v in hist_for_model[-26:] if v > 0)
        _r3_l52_nz  = sum(1 for v in hist_for_model if v > 0)
        _r3_one_shot_seasons = {"Halloween", "July 4th", "Valentines Day",
                                "St Patrick's Day", "Easter", "Pride"}
        _r3_skip = season in _r3_one_shot_seasons
        # R3 widened thresholds (2026-04-22): was L26_nz>=4 AND L52_nz>=8 —
        # missed items at Wakefern/Petbarn/Lowes with real seasonal demand but
        # fewer active weeks.  Now L26_nz>=2 AND L52_nz>=5.
        # S5 (2026-04-22): For international customers (Petbarn, Loblaws,
        # Comercializadora, Grup) drop gate further to L26_nz>=1 AND L52_nz>=3,
        # AND raise floor multiplier 0.3 → 0.5 since they buy less often but
        # in larger qty per order.  Addresses Loblaws −23%, Wakefern −29%.
        if is_international:
            _r3_gate_l26_nz = 1
            _r3_gate_l52_nz = 3
            _r3_floor_mult  = 0.5
            _r3_tag         = " [S5 international]"
        else:
            _r3_gate_l26_nz = 2
            _r3_gate_l52_nz = 5
            _r3_floor_mult  = 0.3
            _r3_tag         = ""
        if (not _zero_velocity and not _r3_skip and _r3_l26_avg > 0 and
                _r3_l26_nz >= _r3_gate_l26_nz and _r3_l52_nz >= _r3_gate_l52_nz):
            _r3_floor = _r3_l26_avg * _r3_floor_mult
            _r3_snapped = snap(_r3_floor, mp)
            if _r3_snapped > 0:
                fcst  = [_r3_snapped] * 26
                model = "Inactive+Floor (R3)"
                cap   = round(_r3_floor, 1)
                meta  = {
                    "model":      "Inactive+Floor (R3)",
                    "l26_avg":    round(_r3_l26_avg, 1),
                    "l26_nz":     _r3_l26_nz,
                    "l52_nz":     _r3_l52_nz,
                    "floor":      round(_r3_floor, 1),
                    "drivers":    [
                        f"R3 inactive floor{_r3_tag}: L26_avg {_r3_l26_avg:.0f} × "
                        f"{_r3_floor_mult} = {_r3_floor:.0f}/wk (snapped to MP {mp} "
                        f"= {_r3_snapped}/wk); L26_nz={_r3_l26_nz}, L52_nz={_r3_l52_nz}"
                    ],
                }

        # S6 — Off-price L52 placeholder (2026-04-22).  For off-price retailers
        # (Burlington, Ross, TJ Maxx, Kohl's, etc.) that have L52W activity but
        # zero L13W activity and didn't qualify for R3, place a single W1
        # placeholder = L52_avg × 0.5 snapped to master pack.  Off-price buyers
        # often return to re-order once per year; one placeholder captures that
        # without projecting the whole catalog forward.  Only fires when R3
        # did NOT fire (model still "Inactive"), has L52_nz ≥ 2, and customer
        # is in OFFPRICE_CUST_SUBSTRS.
        _s6_is_offprice = _is_offprice_cust(cust_name)
        _s6_l52_avg = (sum(hist_for_model) / len(hist_for_model)) if hist_for_model else 0
        if (not _zero_velocity and model == "Inactive" and _s6_is_offprice and _r3_l52_nz >= 2 and
                _s6_l52_avg > 0 and not _r3_skip):
            _s6_placeholder = snap(_s6_l52_avg * 0.5, mp)
            if _s6_placeholder > 0:
                fcst = [0] * 26
                fcst[0] = _s6_placeholder
                model = "Inactive+S6 (off-price)"
                cap   = round(_s6_l52_avg * 0.5, 1)
                meta  = {
                    "model":       "Inactive+S6 (off-price)",
                    "l52_avg":     round(_s6_l52_avg, 1),
                    "l52_nz":      _r3_l52_nz,
                    "placeholder": _s6_placeholder,
                    "drivers":     [
                        f"S6 off-price placeholder: L52_avg {_s6_l52_avg:.0f} × 0.5 "
                        f"= {_s6_l52_avg*0.5:.0f} → W1 placeholder {_s6_placeholder} "
                        f"(MP {mp}); L52_nz={_r3_l52_nz}"
                    ],
                }

        # F19 — Conservative inactive floor (on-by-default 2026-05-06).
        # When the item is Inactive (L13 all zero) but the planner has a
        # large manual projection AND there's evidence the item is still
        # alive at retail, give partial credit instead of a zero forecast.
        # Shape matches the manual curve, scaled so the total respects a
        # velocity ceiling.
        #
        # Two paths for the alive-signal:
        #   (A) Amazon path — Avg_Units_Wk_L52w > 0 (consumer-side POS).
        #       Velocity cap = POS L52 × 26.
        #   (B) Non-Amazon path — last non-zero order in hist within 26
        #       weeks (item ordered recently enough to be considered
        #       paused-not-dead).  Velocity cap = L52 order total ÷ 2.
        if CONSERVATIVE_INACTIVE and not _zero_velocity:
            _manual_tmp   = [float(row.get(c) or 0) for c in ORIG_PRJ_COLS]
            _manual_total = sum(_manual_tmp)
            # Path A — Amazon POS liveness
            _pos_l52_f19  = float(pos_data.get("Avg_Units_Wk_L52w") or 0) if (is_amazon and pos_data) else 0
            _amazon_alive_f19 = _pos_l52_f19 > 0
            # Path B — recent-order liveness (Inactive items have L13=0,
            # so we look for any non-zero week up to 26 weeks back)
            _weeks_since_last_f19 = None
            for _idx, _v in enumerate(reversed(hist)):
                if float(_v) > 0:
                    _weeks_since_last_f19 = _idx
                    break
            _recent_ord_alive_f19 = (_weeks_since_last_f19 is not None
                                     and _weeks_since_last_f19 <= 26)
            _is_alive_f19 = _amazon_alive_f19 or _recent_ord_alive_f19

            if _manual_total >= 5000 and _is_alive_f19:
                # Choose velocity cap based on which signal fired
                if _amazon_alive_f19:
                    _vel_cap_f19   = _pos_l52_f19 * 26
                    _vel_label_f19 = f"POS L52 × 26 = {_vel_cap_f19:.0f}"
                else:
                    _l52_sum_f19   = sum(float(v) for v in hist[-52:]) if len(hist) >= 52 else sum(float(v) for v in hist)
                    _vel_cap_f19   = _l52_sum_f19 / 2.0
                    _vel_label_f19 = f"L52 order total ÷ 2 = {_vel_cap_f19:.0f}"
                _floor_total = min(_manual_total * 0.5, _vel_cap_f19)
                if _floor_total > 0 and sum(_manual_tmp) > 0:
                    _scale = _floor_total / sum(_manual_tmp)
                    fcst   = [snap(v * _scale, mp) for v in _manual_tmp]
                    model  = "Inactive+Floor"
                    _path_label_f19 = "Amazon POS" if _amazon_alive_f19 else f"recent-order ({_weeks_since_last_f19}w ago)"
                    meta   = {
                        "model":       "Inactive+Floor",
                        "f19_path":    "amazon_pos" if _amazon_alive_f19 else "recent_order",
                        "pos_l52":     round(_pos_l52_f19, 1) if _amazon_alive_f19 else None,
                        "weeks_since_last_ord": _weeks_since_last_f19,
                        "floor_total": round(_floor_total, 0),
                        "drivers": [
                            f"F19 inactive floor ({_path_label_f19}): manual_total "
                            f"{_manual_total:.0f} × 0.5 = {_manual_total*0.5:.0f}, "
                            f"capped at {_vel_label_f19} → {_floor_total:.0f}"
                        ],
                    }

        # F1/F3/F7/F8 -- Data-driven Inactive fallback (history-only, no
        # manual-projection input).  Fires when the model is still "Inactive"
        # after earlier R3/S6/F19 passes and the item is NOT flagged EOL via
        # PT_Item_Status / Status_Cust tokens.  Produces a non-zero forecast
        # anchored on sibling SKUs (same Mstyle) or shipment history -- respects
        # category/season curves when present.
        #   F1 = Sibling-Mstyle fallback rate
        #   F3 = No 52-week history          -> New/Relaunch label
        #   F5 = PT_Item_Status EOL gate (skip the branch entirely if EOL)
        #   F7 = Some L52 signal but L13 = 0 -> Reactivating label
        #   F8 = Shipment corroboration (use Shp as fallback when Ord silent
        #        but Shp active -- captures stockout-suppressed demand)
        # (F2 customer-median floor removed 2026-05-04 -- see CHANGELOG.md.)
        if model == "Inactive" and not _is_eol(row) and not _zero_velocity:
            _fx_family_rate, _fx_n_sib = _family_rate_for(row)
            _fx_cust_rate,   _fx_n_cust = _cust_rate_for(row)

            # F8 — shipment-history corroboration.  When order history is silent
            # but shipments were going out recently, treat L13 shipments as a
            # demand signal (orders may be lagging or suppressed).
            _fx_shp_hist = _get_shp_history(row)
            _fx_shp_l13  = sum(_fx_shp_hist[-13:]) if len(_fx_shp_hist) >= 13 else 0
            _fx_shp_l52  = sum(_fx_shp_hist) if _fx_shp_hist else 0
            _fx_shp_wk   = _fx_shp_l52 / 52.0 if _fx_shp_l52 > 0 else 0

            # Pick base rate, in priority order -- all history/cross-record derived:
            #   (1) Sibling-Mstyle median (F1)
            #   (2) Shipment history (F8)
            # (F2 customer-median floor removed 2026-05-04 -- see CHANGELOG.md.
            # If neither F1 nor F8 fires, the item legitimately has no demand
            # signal and stays Inactive with zero forecast.)
            _fx_rate   = 0.0
            _fx_src    = ""
            if _fx_family_rate > 0:
                # Scale sibling rate by this customer's relative size.
                _fx_scale = (_fx_cust_rate / GLOBAL_WK_RATE) if GLOBAL_WK_RATE > 0 and _fx_cust_rate > 0 else 1.0
                _fx_scale = max(0.25, min(_fx_scale, 2.0))   # clamp so one oddball cust doesn't dominate
                _fx_rate  = _fx_family_rate * _fx_scale * 0.5   # 50% conservative
                _fx_src   = (f"F1 mstyle-family: {_fx_family_rate:.1f}/wk median across "
                             f"{_fx_n_sib} sibling SKUs × {_fx_scale:.2f} cust-scale × 0.5")
            elif _fx_shp_l13 > 0 and _fx_shp_wk > 0:
                _fx_rate = _fx_shp_wk * 0.6      # shipments are noisier than orders — 60% of rate
                _fx_src  = (f"F8 ship-corroboration: L13 ship total {int(_fx_shp_l13)} "
                            f"(wk rate {_fx_shp_wk:.1f} × 0.6)")

            if _fx_rate > 0:
                # Apply category/season curve if available, else flat.
                _fx_mults = _category_week_multipliers(description, product_category,
                                                       product_subcategory, brand, brand_pt,
                                                       season=season)
                _fx_fcst = []
                for _fw in range(26):
                    _fw_m = _fx_mults[_fw] if _fx_mults else 1.0
                    _fx_fcst.append(snap(_fx_rate * _fw_m, mp))
                if sum(_fx_fcst) > 0:
                    fcst = _fx_fcst
                    # F3 vs F7 labelling: no history at all → New/Relaunch,
                    # dormant but some 52w signal → Reactivating.
                    _fx_l52_sum = sum(hist_for_model)
                    _fx_trailZ  = 0
                    for _v in reversed(hist_for_model):
                        if _v == 0: _fx_trailZ += 1
                        else:       break
                    if _fx_l52_sum == 0 and _fx_trailZ >= 26:
                        model = "New/Relaunch"
                    else:
                        model = "Reactivating"
                    # F5 — bump model label when PT_Item_Status flags launching
                    if _is_launching(row):
                        model = "New/Relaunch (launch-tagged)"
                    cap  = round(_fx_rate, 1)
                    meta = {
                        "model":            model,
                        "fallback_rate_wk": round(_fx_rate, 1),
                        "family_rate_wk":   round(_fx_family_rate, 1),
                        "cust_rate_wk":     round(_fx_cust_rate, 1),
                        "shp_l13":          int(_fx_shp_l13),
                        "pt_status":        row.get("PT_Item_Status", ""),
                        "drivers":          [_fx_src],
                    }

        # F5 — PT_Item_Status EOL respect.  When item is flagged EOL and we
        # produced a non-zero Inactive+Floor earlier (R3/S6/F19), dampen by 0.5x
        # so phase-out items aren't over-projected in the catch-all floor.
        if model != "Inactive" and model.startswith("Inactive") and _is_eol(row):
            fcst = [int(round(v * 0.5 / mp)) * int(mp) if v > 0 else 0 for v in fcst]
            meta.setdefault("drivers", []).append(
                f"F5 EOL-dampen (PT_Item_Status={row.get('PT_Item_Status','')}): "
                f"cut Inactive-floor by 50%"
            )

    elif pattern == "sparse_intermittent":
        # F6c (renamed from F6 2026-05-21 to break tag collision with F6a in
        # classify() and F6b in seasonal_baseline()) -- L13W all zero but
        # meaningful L26/L52 activity -> route to Heuristic so the forecast
        # uses post-ramp / historical avg rather than zero.
        fcst, cap, meta = heuristic(hist_for_model, mp, l13w, is_amazon=is_amazon,
                                    description=description,
                                    product_category=product_category,
                                    product_subcategory=product_subcategory,
                                    brand=brand, brand_pt=brand_pt,
                                    pos_data=pos_data, season=season,
                                    is_new_launch=_f73_new_ramp)
        model    = "Heuristic"
        biweekly = False

    elif force_heuristic:
        # Post-ISO settle period: Heuristic uses post-ramp avg (trickle rate) as baseline.
        fcst, cap, meta = heuristic(hist_for_model, mp, l13w, is_amazon=is_amazon,
                                    description=description,
                                    product_category=product_category,
                                    product_subcategory=product_subcategory,
                                    brand=brand, brand_pt=brand_pt,
                                    pos_data=pos_data, season=season,
                                    is_new_launch=_f73_new_ramp)
        model    = "Heuristic"
        biweekly = False

    elif not is_croston:
        # FXX — Amazon Replenishment items order in pallet/MOQ batches, creating
        # a sparse appearance in the order history.  This is NOT true intermittent
        # demand — it's continuous demand expressed in bulk purchases.  Sparse
        # Intermittent uses non-zero event averages which massively overstates
        # the forward rate.  Route to Heuristic (L13W non-zero baseline) instead.
        _is_amz_replen = is_amazon and "replen" in (row.get("PT_Item_Status") or "").lower()
        if _is_amz_replen:
            fcst, cap, meta = heuristic(hist_for_model, mp, l13w, is_amazon=is_amazon,
                                        description=description,
                                        product_category=product_category,
                                        product_subcategory=product_subcategory,
                                        brand=brand, brand_pt=brand_pt,
                                        pos_data=pos_data, season=season,
                                        is_new_launch=_f73_new_ramp)
            model    = "Heuristic"
            biweekly = False
            meta.setdefault("drivers", []).append(
                "FXX Amazon-Replen rerouted from Sparse Intermittent: "
                "batch ordering is MOQ/pallet-driven, not true intermittent demand"
            )
        else:
            # P1 / F72 (2026-05-24): New-launch ramp detection.
            # Variance deep-dive showed Walmart "PDQ" items launching with 6
            # consecutive non-zero weeks (avg 10-30k/wk) were getting routed
            # to Sparse Intermittent and getting ONE order placed at W14.
            # The actual pattern is a new-launch ramp -- 18+ leading zeros
            # (item didn't exist) then dense recent ordering.
            # Detect: L26 has >=4 nz in last 6 weeks AND >=5 zeros in weeks 14-20.
            # Reroute to Heuristic which projects flat L13_nz_avg.
            _f72_l26 = list(hist_for_model[-26:]) if len(hist_for_model) >= 26 else list(hist_for_model)
            _f72_recent6_nz = sum(1 for v in _f72_l26[-6:] if float(v or 0) > 0)
            _f72_prior6_zero = sum(1 for v in _f72_l26[-12:-6] if float(v or 0) == 0) if len(_f72_l26) >= 12 else 0
            _f72_is_new_launch_ramp = (
                _f72_recent6_nz >= 4
                and _f72_prior6_zero >= 5
            )
            if _f72_is_new_launch_ramp:
                # Route to Heuristic with the recent dense data as the signal
                fcst, cap, meta = heuristic(hist_for_model, mp, l13w, is_amazon=is_amazon,
                                            description=description,
                                            product_category=product_category,
                                            product_subcategory=product_subcategory,
                                            brand=brand, brand_pt=brand_pt,
                                            pos_data=pos_data, season=season,
                                            is_new_launch=True)
                model    = "Heuristic (F72 new-launch ramp)"
                biweekly = False
                meta.setdefault("drivers", []).append(
                    f"F72 New-launch ramp detected: L26[-6:] has {_f72_recent6_nz} "
                    f"non-zero (recent), L26[-12:-6] has {_f72_prior6_zero} zeros "
                    f"(pre-launch); rerouted from Sparse Intermittent to Heuristic"
                )
            else:
                # Truly sparse buyer (< 25% non-zero = typically every 6–12 weeks).
                # Mimic the historical batch cadence, anchored to account-level cadence.
                _is_offprice_s1 = _is_offprice_cust(cust_name)
                fcst, cap, meta = sparse_intermittent_forecast(hist_for_model, mp,
                                                               account_interval=account_interval,
                                                               is_offprice=_is_offprice_s1)
                model    = "Sparse Intermittent"
                biweekly = False   # sparse items never get biweekly enforcement
                _cadence_gap_si = detect_biweekly(hist_for_model)
                biweekly = bool(_cadence_gap_si)
                fcst = apply_ordering_pattern(fcst, hist_for_model, mp)

    elif not is_dense:
        # Intermittent buyer (25–50% non-zero = every 2–5 weeks).
        # Croston's handles the gap/quantity pattern better than seasonal baseline,
        # with a post-spike drawdown guard to avoid locking onto a post-spike lull.
        _is_offprice_t1 = _is_offprice_cust(cust_name)
        fcst, cap, meta = crostens(hist_for_model, mp, is_amazon=is_amazon,
                                   description=description,
                                   product_category=product_category,
                                   product_subcategory=product_subcategory,
                                   brand=brand, brand_pt=brand_pt,
                                   pos_data=pos_data, season=season,
                                   is_offprice=_is_offprice_t1,
                                   is_new_launch=_f73_new_ramp,
                                   is_international=is_international)
        model    = "Croston's"
        biweekly = False
        _cadence_gap_c = detect_biweekly(hist_for_model)
        biweekly = bool(_cadence_gap_c)
        fcst = apply_ordering_pattern(fcst, hist_for_model, mp)

    else:
        # Dense buyer (≥ 50% non-zero): seasonal baseline + ordering pattern shape.
        _is_ecom_t4 = _is_ecom_cust(cust_name)
        fcst, cap, meta = seasonal_baseline(hist_for_model, mp, is_amazon=is_amazon,
                                            pos_data=pos_data, description=description,
                                            product_category=product_category,
                                            product_subcategory=product_subcategory,
                                            brand=brand, brand_pt=brand_pt,
                                            shpd_l13=l13w, season=season,
                                            is_ecom=_is_ecom_t4,
                                            is_new_launch=_f73_new_ramp,
                                            amz_catalog=amz_catalog)
        model    = "Seasonal Baseline"
        # VP-Q3: detect_biweekly() now returns the cadence gap (>=3 for monthly+)
        # or 0; cast to bool for backward-compat with JSON consumers expecting bool.
        _cadence_gap = detect_biweekly(hist_for_model)
        biweekly = bool(_cadence_gap)
        fcst     = apply_ordering_pattern(fcst, hist_for_model, mp)

        # F76 -- Seasonal Baseline thin-history ceiling guard (2026-05-24).
        #
        # EC items that inherit order history via F60 (or items reclassified
        # Dense by F6a but with only a few active weeks) have a small sample
        # of non-zero weeks in L26.  The category seasonal profile can then
        # amplify the baseline dramatically in peak weeks (e.g. 5x a modest
        # per-week baseline producing an enormous peak-month value).
        #
        # Guard: when active L26 weeks <= 13 (thin history), cap each forecast
        # week at the uncapped per-week baseline x 2.0.  This limits the
        # worst-case seasonal amplification to 2x rather than an unbounded
        # multiple.  Items with >= 14 active L26 weeks have enough history
        # for the seasonal profile to be meaningful; no cap is applied.
        _f76_l26_nz_wks = sum(1 for v in hist_for_model[-26:] if float(v or 0) > 0)
        if _f76_l26_nz_wks <= 13 and cap > 0:
            _f76_ceil = cap * 2.0
            _f76_any  = False
            for _fi in range(len(fcst)):
                if fcst[_fi] > _f76_ceil:
                    fcst[_fi] = (int(round(_f76_ceil / mp)) * int(mp)
                                 if mp > 0 else int(_f76_ceil))
                    _f76_any = True
            if _f76_any:
                _fire("F76")
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F76 thin-history SB ceiling: L26 has only "
                        f"{_f76_l26_nz_wks} active weeks; per-week cap = "
                        f"baseline {cap:.0f}/wk x2.0 = {_f76_ceil:.0f}/wk "
                        f"to prevent seasonal profile over-amplification"
                    )

    # F34 -- annotate meta when item was detected as a new launch so reviewers
    # see why decline rules (F10) and the M1 ceiling were skipped.
    if _f34_is_new_launch and isinstance(meta, dict):
        meta["new_launch"] = True
        meta["new_launch_active_weeks"] = _f34_active_weeks
        meta.setdefault("drivers", []).append(
            f"F34 New launch detected: weeks 27-51 sum < 1% of L26 sum "
            f"(~{_f34_active_weeks}w of activity); skipped F10 decline check "
            f"and M1 L52 ceiling so ramp-up volume is preserved"
        )

    # F35 — annotate meta when stockout backlog was stripped so reviewers
    # can audit which weeks were normalized and how much was removed.
    if _f35_corrections and isinstance(meta, dict):
        meta["stockout_corrections"] = _f35_corrections
        for _c in _f35_corrections:
            meta.setdefault("drivers", []).append(
                f"F35 Stockout backlog removed: {_c['length']}w gap at hist[{_c['start']}], "
                f"baseline={_c['baseline']:.0f}/wk; stripped {_c['removed']:.0f} units of "
                f"pent-up backlog from post-gap catch-up weeks (true demand intent restored)"
            )

    # F43 — annotate meta when recent-spike attenuation fired so reviewers
    # see which last-4w outliers were capped and what baseline drove the
    # decision.  Capping (not zeroing) preserves activity signal but stops
    # one-time stock-up events from rewriting the model classification.
    if _f43_corrections and isinstance(meta, dict):
        # F49 (2026-05-08): when the only "correction" is a skip marker, F43
        # bailed out because the spikes were sustained or POS-confirmed —
        # surface that as F49 instead of F43 so planners see the right rule.
        _first = _f43_corrections[0] if _f43_corrections else {}
        if _first.get("f49_skip"):
            _reason = _first["f49_skip"]
            if _reason == "sustained_acceleration":
                meta.setdefault("drivers", []).append(
                    f"F49 F43-skip sustained acceleration: {_first['spike_count']}/4 "
                    f"recent weeks all > 2.5x L26-prior nz median "
                    f"({_first['median_pre']:.0f}/wk) -> real run-rate shift, not "
                    f"a 1-off spike; preserved signal"
                )
            elif _reason == "pos_confirmed_acceleration":
                meta.setdefault("drivers", []).append(
                    f"F49 F43-skip POS-confirmed acceleration: "
                    f"{_first['spike_count']}/4 recent weeks > cap_threshold AND "
                    f"Amazon POS L4 {_first['l4_pos']:.0f}/wk vs L13 "
                    f"{_first['l13_pos']:.0f}/wk = {_first['ratio']:.2f}x "
                    f"(>=1.20) -> preserved signal"
                )
        elif _first.get("f49b_internal"):
            # F49b (2026-05-21): internal spike within F49 sustained-acceleration
            # window — cap just the outlier, leave the acceleration signal intact.
            meta["recent_spike_caps"] = _f43_corrections
            for _c in _f43_corrections:
                meta.setdefault("drivers", []).append(
                    f"F49b Internal-spike cap: hist[{_c['idx']}]={_c['original']:.0f} "
                    f"({_c['ratio']:.1f}x L4W inner-median {_c['median_pre']:.0f}/wk) "
                    f"-> {_c['capped']:.0f} (2.0x inner-median); "
                    f"F49 sustained-acceleration but one week was outlier vs its peers"
                )
        else:
            meta["recent_spike_caps"] = _f43_corrections
            for _c in _f43_corrections:
                meta.setdefault("drivers", []).append(
                    f"F43 Recent-spike capped: hist[{_c['idx']}]={_c['original']:.0f} "
                    f"({_c['ratio']:.1f}x L26-prior nz median {_c['median_pre']:.0f}) "
                    f"-> {_c['capped']:.0f} (2.0x median); prevents Croston's mis-classify "
                    f"of one-time spike as recurring lumpy event"
                )
    # F44 — annotate meta when recent-spike-aware re-classification fired,
    # forcing the customer back onto the steady (Holt-Winters) path despite
    # F-B's L13-zero override.  Reviewers see the prior-baseline density
    # that justified trusting the long-term steady pattern.
    if _f44_fired_meta and isinstance(meta, dict):
        meta["f44_dense_override"] = _f44_fired_meta
        meta.setdefault("drivers", []).append(
            f"F44 Dense-override (post-F43): L26-prior nz {_f44_fired_meta['l26_prior_nz']}/"
            f"{_f44_fired_meta['l26_prior_total']} = {_f44_fired_meta['l26_prior_frac']*100:.0f}% "
            f"≥ 60% → forced is_dense=True (override F-B L13-zero rule); "
            f"steady pre-disruption pattern routes to Seasonal Baseline smooth path "
            f"instead of Croston's lumpy-event placement"
        )

    # F47 — annotate meta when OOS rebuild-ramp weeks were capped so
    # reviewers see which post-OOS weeks were normalized and the pre-OOS
    # baseline that drove the cap.  Critical for explaining the FF12660
    # case (Walmart 5-wk ship-zero gap → rebuild ramp inflating L13 nz-avg).
    if _f47_corrections and isinstance(meta, dict):
        meta["oos_rebuild_caps"] = _f47_corrections
        for _c in _f47_corrections:
            meta.setdefault("drivers", []).append(
                f"F47 OOS rebuild-ramp capped: {_c['gap_len']}w ship-zero gap at "
                f"hist[{_c['gap_start']}] → {_c['weeks_capped']}w rebuild orders "
                f"capped at pre-OOS baseline {_c['baseline']:.0f}/wk "
                f"(removed {_c['removed_total']:,} compounded units); "
                f"L13W now reflects true ongoing demand, not stock-rebuild double-count"
            )

    # F41 — annotate meta when phantom orders (shipment-confirmed) were
    # stripped so reviewers see which orders were zeroed and the shipment
    # evidence that proved the original wasn't fulfilled.
    if _f41_corrections and isinstance(meta, dict):
        meta["phantom_order_corrections"] = _f41_corrections
        for _c in _f41_corrections:
            meta.setdefault("drivers", []).append(
                f"F41 Phantom order zeroed: hist[{_c['zeroed_idx']}]={_c['zeroed_value']:.0f} "
                f"(prev order at hist[{_c['kept_idx']}]={_c['kept_value']:.0f} "
                f"only shipped {_c['ship_window']:.0f} units in 1-wk lag window = "
                f"{_c['ship_pct']*100:.0f}% fulfilled; reorder qty within "
                f"{_c['qty_diff_pct']*100:.0f}% → phantom reorder)"
            )

    # F39 — annotate meta when duplicate-order runs were stripped so reviewers
    # see which weeks were zeroed out (buyer error / phantom POs).
    if _f39_corrections and isinstance(meta, dict):
        meta["duplicate_order_corrections"] = _f39_corrections
        for _c in _f39_corrections:
            meta.setdefault("drivers", []).append(
                f"F39 Duplicate-order run dedup'd: {_c['length']}w of "
                f"{_c['value']:.0f} units at hist[{_c['start']}] "
                f"(L26 nz-median excl run = {_c['median_excl']:.0f}, ratio "
                f"{_c['value']/max(_c['median_excl'],1):.1f}×); kept first, "
                f"zeroed remaining {_c['length']-1} weeks"
            )

    manual_wks = [float(row.get(c) or 0) for c in ORIG_PRJ_COLS]

    # F68 — Amazon inactive-channel long-term zero (2026-05-17).
    #
    # Two-gate hybrid:
    #   Gate 1 — ASIN Status: if the catalog flags the ASIN as "active" or
    #     "FD" (Forecasted Demand), Amazon's buying system believes it should
    #     be ordering — treat as active regardless of recent order silence
    #     (stockout, compliance hold, or short-term gap).  Skip F68.
    #   Gate 2 — Sparse-signal check: if Gate 1 does NOT confirm active AND
    #     L13W = 0 AND L26W has ≤ 2 non-zero weeks, there is no sustainable
    #     Amazon replenishment pattern.  Assume a long-term channel issue
    #     (item not converting on Amazon, brand not a fit for Amazon's
    #     demographic, listing compliance issue, or Vendor Central program
    #     ended) and zero out the AI forecast.
    #
    # Designed to catch brands like Fraganzia and Fabuloso (multicultural
    # market brands that perform at brick-and-mortar but not on Amazon),
    # A&H Core Grooming (high 3P competition → lost search position → buyer
    # stopped ordering), and any other Acct-MStyle where Amazon placed a
    # trial stocking order but never established a replenishment rhythm.
    #
    # Does NOT fire when amz_catalog is None (data load gap — conservative).
    # Does NOT replace F38f ("Not Buyable"/"ASIN Suppressed" flag — that is
    # handled upstream with W1-4 zero + catch-up assumption).
    if is_amazon and amz_catalog and model not in ("Inactive",):
        _f68_status = (amz_catalog.get("ASIN_Status") or "").strip()
        _f68_active = bool(
            "active" in _f68_status.lower() or
            "fd"     in _f68_status.lower()
        )
        if not _f68_active:
            _f68_l13_tot = sum(float(v or 0) for v in hist_for_model[-13:])
            _f68_l26_nz  = sum(1 for v in hist_for_model[-26:]
                               if float(v or 0) > 0)
            if _f68_l13_tot == 0 and _f68_l26_nz <= 2:
                fcst     = [0] * 26
                model    = "Inactive (F68)"
                biweekly = False
                meta     = {
                    "model":   "Inactive (F68)",
                    "drivers": [
                        f"F68 Amazon inactive channel: ASIN_Status="
                        f"'{_f68_status or 'unknown'}' (not Active/FD), "
                        f"L13W orders=0, L26W non-zero weeks={_f68_l26_nz} ≤ 2 — "
                        f"no sustainable Amazon replenishment pattern detected; "
                        f"long-term channel issue assumed (brand channel fit, "
                        f"listing compliance, or VC program ended)"
                    ],
                }

    # F20 — Heuristic deactivation check (2026-04-22).  The Heuristic model
    # reads post-ramp / historical avg baselines from items that classify()
    # routed away from Inactive.  On items the planner has explicitly zeroed
    # across all 26 manual projection weeks, that baseline is stale — the
    # planner's 0 is the strongest demand signal we have.  Downgrade Heuristic
    # to Inactive (forecast=0) in that case.  Keep Heuristic firing when
    # manual_total > 0 (planner still orders → the heuristic baseline has
    # a job to do).
    if model == "Heuristic" and sum(manual_wks) == 0:
        fcst     = [0] * 26
        cap      = 0
        model    = "Inactive"
        biweekly = False
        meta.setdefault("drivers", []).append(
            "F20 Heuristic → Inactive: planner manual_total = 0 across all 26w "
            "→ stronger signal than post-ramp baseline"
        )

    # F31 — Pre-launch NEW-item passthrough (2026-05-04).  When Status_Cust
    # contains "NEW" (sometimes with a launch month/year, e.g. "NEW 06/26")
    # and the item has zero L26W order history, the item simply hasn't
    # launched yet — no demand signal exists to model.  In that case the
    # planner's manual projections ARE the forecast (they encode the
    # launch curve / first-PO plan).
    #
    # Lifecycle pairing — F31 here covers Stage 1 (no orders yet); F29's
    # manual-deferral block (further below) covers Stage 2 (initial stocking
    # order received but <3 non-zero weeks total — customer is in their 2-3
    # week post-stocking pause).  Once non-zero count ≥ 3, normal F29 floor
    # logic takes over (Stage 3, sustained replen).  Both rules pass the
    # planner manual through verbatim because the customer's true demand
    # forecast comes via email and isn't in any system — only the planner
    # has it, encoded in their manual projections.  Copy them through verbatim so the
    # AI doesn't either invent demand (Sparse/Croston's/Heuristic), zero
    # out a planned launch (F30), or default to Inactive (the classifier
    # routes zero-L13W items there before F31 sees them).
    #
    # Overrides ALL prior model assignments — including Inactive — when the
    # NEW gate fires.  Preserves manual values verbatim (no MP snap), since
    # the planner already accounts for MP constraints in their first-PO
    # plan and rounding small launch quantities to MP can zero out the
    # ramp.
    # Broadened 2026-05-06 — also fires when PT_Item_Status flags
    # Launching / New / Pilot (via _is_launching()).  Previously F5 routed
    # those items into F1/F2/F3 family-based synthetic fallback, but with
    # no history we have no real signal — sibling SKUs and family rates
    # are guesses.  The planner's manual encodes their actual PO-discussion
    # context (which we don't have in any system) so it's the more reliable
    # signal.  Defer to manual when L26W=0 AND manual exists.
    _f31_status_cust = (row.get("Status_Cust") or "").upper()
    _f31_l26_ord_total = sum(float(v or 0) for v in hist[-26:]) if hist else 0
    _f31_status_cust_new = "NEW" in _f31_status_cust
    _f31_pt_launching    = _is_launching(row)
    if ((_f31_status_cust_new or _f31_pt_launching)
        and _f31_l26_ord_total == 0
        and sum(manual_wks) > 0):  # only when planner actually has a launch curve
        _prev_model_f31 = model
        _prev_total_f31 = sum(fcst)
        # Copy manual projections verbatim — no MP snap.  Planner has
        # already accounted for MP in their launch curve.
        fcst     = [int(round(v)) for v in manual_wks]
        cap      = max(manual_wks) if manual_wks else 0
        # Distinguish source of the launch signal in the model label so
        # planners can see which field triggered the passthrough.
        if _f31_status_cust_new and _f31_pt_launching:
            model = "Pre-launch (manual passthrough — Status_Cust + PT_Item_Status)"
        elif _f31_status_cust_new:
            model = "Pre-launch NEW (manual passthrough)"
        else:
            model = "Pre-launch Launching/New/Pilot (manual passthrough)"
        biweekly = False
        _f31_trigger = ("Status_Cust='" + row.get('Status_Cust', '') + "'"
                        if _f31_status_cust_new else
                        "PT_Item_Status='" + row.get('PT_Item_Status', '') + "'")
        meta.setdefault("drivers", []).append(
            f"F31 pre-launch passthrough: {_f31_trigger}, "
            f"L26W orders=0 → item not yet launched; replaced {_prev_model_f31} "
            f"{_prev_total_f31}u with planner manual projections "
            f"({sum(fcst)}u across 26w)"
        )

    # F30 — Zero-order-history hard guard (2026-05-04, tightened).
    # When an item has zero L26W ORDER history, no model should generate a
    # non-zero forecast — the customer hasn't placed an order in 26+ weeks,
    # so manual projections alone (often planner placeholders) and any
    # warehouse-side ship activity are not sufficient anchor.  Catches
    # Sparse Intermittent / New-Relaunch / Reactivating / Heuristic edge
    # cases that were previously firing on items with shipping signal but
    # no actual customer demand.  Skip if model is already Inactive or one
    # of the OTB-zeroed states.  F31 (above) runs first so pre-launch NEW
    # items get manual passthrough rather than zero.
    _f30_l26_ord_total = sum(float(v or 0) for v in hist[-26:]) if hist else 0
    if (model in ("Sparse Intermittent", "New/Relaunch", "Reactivating",
                  "New/Relaunch (launch-tagged)", "Heuristic", "Croston's")
        and _f30_l26_ord_total == 0
        and sum(fcst) > 0):
        _prev_total = sum(fcst)
        fcst     = [0] * 26
        cap      = 0
        prev_model = model
        model    = "Inactive (zero order history)"
        biweekly = False
        meta.setdefault("drivers", []).append(
            f"F30 zero-order-history guard: was {prev_model} {_prev_total}u; "
            f"L26W order history = 0 → forecast zeroed (no customer demand "
            f"signal regardless of warehouse ship activity)"
        )

    # F80 (2026-05-24): Active:Replen zero-L13W fallback.
    # When an item has Active:Replen status but zero L13W AND zero L26W order
    # history, and has been zeroed to "Inactive (zero order history)" by F30,
    # try to recover the forecast using:
    #   1. L26W non-zero avg (if any L26W orders exist)  -- unlikely since L26=0
    #   2. Sibling mstyle history already propagated into hist_for_model by F60/F69
    #      (those fill the history arrays before forecast_record runs this logic)
    # The key case this fixes: NEW DC placements and EC variants where order
    # history is zero because the item just activated.  item_status=Active:Replen
    # signals the planner intends to stock this item going forward.
    _f80_applied = False
    _pt_status_f80 = (row.get("PT_Item_Status") or "").strip().upper()
    _is_replen_f80 = "REPLEN" in _pt_status_f80
    _l26_total_f80 = sum(float(v or 0) for v in hist_for_model[-26:]) if hist_for_model else 0
    _l26_nz_f80    = [v for v in hist_for_model[-26:] if float(v or 0) > 0]
    if (model == "Inactive (zero order history)"
            and _is_replen_f80
            and sum(fcst) == 0
            and _l26_total_f80 > 0
            and _l26_nz_f80):
        # L26W has some activity -- build a small floor from it
        _f80_l26_avg = sum(_l26_nz_f80) / len(_l26_nz_f80)
        _f80_floor   = snap(_f80_l26_avg * 0.5, mp)   # conservative 50% of L26 nz avg
        if _f80_floor > 0:
            fcst  = [_f80_floor] * 26
            cap   = round(_f80_floor, 1)
            model = "Reactivating"
            _f80_applied = True
            meta.setdefault("drivers", []).append(
                f"F80 Active:Replen L26W fallback: L26W nz avg {_f80_l26_avg:.0f} "
                f"x 0.5 = {_f80_floor:.0f}/wk floor (item_status=Replen, L13W=0)"
            )

    # F81 (2026-05-24): APL recency anchor.
    # Amazon_Catalog carries Ordered_Units_LW and Ordered_Units_Prior_Wk for APL
    # mstyles (B2B purchase orders).  When the 2-week catalog avg diverges >=20%
    # from the L4W order-history avg, blend the recent signal into the forecast
    # at 35% weight (capped at +/-25% total adjustment) to capture near-term
    # trend shifts the longer history avg misses.
    # Guards: is_apl, pos_data available, non-zero forecast, catalog values within
    # 0.50x-2.00x of L4W avg (implausible outliers excluded).
    _f81_applied = False
    if is_apl and pos_data and sum(fcst) > 0:
        _f81_ord_lw = float((pos_data or {}).get("Ordered_Units_LW") or 0)
        _f81_ord_pw = float((pos_data or {}).get("Ordered_Units_Prior_Wk") or 0)
        _f81_recent = ((_f81_ord_lw + _f81_ord_pw) / 2.0
                       if _f81_ord_pw > 0 else _f81_ord_lw)
        _f81_l4_nz  = [float(v or 0) for v in hist_for_model[-4:]
                       if float(v or 0) > 0]
        _f81_l4_avg = sum(_f81_l4_nz) / len(_f81_l4_nz) if _f81_l4_nz else 0
        if (_f81_recent > 0 and _f81_l4_avg > 0
                and 0.50 <= (_f81_recent / _f81_l4_avg) <= 2.00
                and abs(_f81_recent / _f81_l4_avg - 1.0) >= 0.20):
            _f81_ratio = _f81_recent / _f81_l4_avg
            # 35% weight toward recent catalog signal, capped at +/-25% swing
            _f81_scale = max(0.75, min(0.35 * _f81_ratio + 0.65, 1.25))
            fcst = [snap(v * _f81_scale, mp) if v > 0 else 0 for v in fcst]
            _f81_applied = True
            meta.setdefault("drivers", []).append(
                f"F81 APL recency: catalog 2-wk avg {_f81_recent:.0f} = "
                f"{_f81_ratio:.2f}x L4W nz avg {_f81_l4_avg:.0f}; "
                f"forecast scaled x{_f81_scale:.2f} (35% blend toward recent signal)")

    # F17 — Sparse cadence W1 seed (2026-04-22).  When the Sparse Intermittent
    # model places its first non-zero slot several weeks out but the planner
    # expects an order in W1, the cadence phase is off.  Shift the AI cadence
    # left by the offset so the first order lands in W1 (or as close as possible),
    # preserving the inter-event interval.  Sanity gate: for Amazon items, POS
    # L13 must be at least 30% of manual_W1 (else we'd be seeding against a
    # dying SKU).  Non-Amazon items have no gate (no POS signal).
    #
    # F17b — Volume gate (2026-04-22).  Rotating + refilling tail cadence on
    # truly low-volume sparse items stacks qty that wasn't there before and
    # balloons the tail slice.  Require the item's own L13W non-zero avg ≥ 25
    # OR Amazon POS L13 ≥ 50.  Below that threshold, keep the original 0-seed
    # placement (no rotation).
    if model == "Sparse Intermittent" and manual_wks[0] > 0:
        _first_nz = next((i for i, v in enumerate(fcst) if v > 0), None)
        if _first_nz is not None and _first_nz > 0:
            _l13_nz_f17  = [v for v in hist[-13:] if v > 0]
            _l13_avg_f17 = (sum(_l13_nz_f17) / len(_l13_nz_f17)) if _l13_nz_f17 else 0.0
            _pos_l13_f17b = float(pos_data.get("Avg_Units_Wk_L13w") or 0) if (is_amazon and pos_data) else 0.0
            _vol_ok_f17   = (_l13_avg_f17 >= 25.0) or (_pos_l13_f17b >= 50.0)
            _gate_ok = _vol_ok_f17
            if _gate_ok and is_amazon and pos_data:
                _pos_l13_f17 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
                _gate_ok = _pos_l13_f17 >= manual_wks[0] * 0.3
            if _gate_ok:
                # Rotate left by _first_nz, pad right with additional cadence
                # iterations so W26 isn't prematurely zero.
                _step = next((i - _first_nz
                              for i in range(_first_nz + 1, 26)
                              if fcst[i] > 0), None)
                _shifted = fcst[_first_nz:] + [0] * _first_nz
                # Extend cadence to fill tail zeros
                if _step and _step > 0:
                    _tail_start = len(fcst) - _first_nz
                    _last_qty = next((v for v in reversed(_shifted[:_tail_start]) if v > 0), 0)
                    if _last_qty > 0:
                        for _pos in range(_tail_start, 26, _step):
                            _shifted[_pos] = _last_qty
                fcst = _shifted
                meta.setdefault("drivers", []).append(
                    f"F17 cadence W1 seed: shifted {_first_nz}w left so first order "
                    f"lands in W1 (manual_W1={manual_wks[0]:.0f}, "
                    f"L13_nz_avg={_l13_avg_f17:.0f}, pos_L13={_pos_l13_f17b:.0f})"
                )

    # M1 (2026-04-22, tightened v2) — L52 / L26 anchored ceiling.  Prevents
    # runaway over-projection on items with thin long-run history or items
    # where the model has compounded L13 acceleration + POS blend + seasonal
    # lifts onto a small base.  Ceiling = max(L52 × 1.25, L26 × 1.25).
    # Multiplier tightened from 1.5 → 1.25 after 36-key review showed
    # moderate 1.5-2× over-projections were passing through.  If long-run
    # total is very small (<1000) skip (brand-new / ramps need headroom).
    _m1_l26_total = sum(float(v) for v in hist[-26:])
    _m1_l52_total = sum(float(v) for v in hist[-52:])
    _m1_ceiling = max(_m1_l52_total * 1.25, _m1_l26_total * 1.25)
    # F34 — skip M1 ceiling on new launches: L52 total is dominated by pre-launch
    # zeros, so L52 × 1.25 ≈ L26 × 1.25 and the cap suffocates ramp-up volume.
    if (_m1_l52_total >= 1000 and model not in ("Inactive",)
            and sum(fcst) > _m1_ceiling and not _f34_is_new_launch):
        _m1_scale = _m1_ceiling / sum(fcst)
        fcst = [int(v * _m1_scale) for v in fcst]
        # Re-snap to master pack
        fcst = [int(round(v / mp)) * int(mp) if v > 0 else 0 for v in fcst]
        meta.setdefault("drivers", []).append(
            f"M1 L52-ceiling: capped 26w total at max(L52×1.25={_m1_l52_total*1.25:.0f}, "
            f"L26×1.25={_m1_l26_total*1.25:.0f})"
        )

    # M2 (2026-04-22, broadened v2) — Phase-out / EOL dampening.  Three
    # independent signals:
    #   (1) Status_Cust or PT_Item_Status token match
    #       (DISC / DEL / LIQ / END / OBSOLETE / PHASE)
    #   (2) Stale order-side: no orders in last 13 weeks AND last order ≥ 26
    #       weeks ago → account has quietly dropped the item even if the
    #       status field hasn't been updated
    #   (3) Stale ship-side: L13W ship total is zero AND L26W ship total is
    #       zero while L52W had activity → distribution ramped down
    # When any signal fires and model != Inactive, cut forecast to max(AI×30%,
    # manual).  Respects manual floor so we don't under-plan below planner.
    _m2_status_cust = (row.get("Status_Cust") or "").strip()
    _m2_item_status = (row.get("PT_Item_Status") or "").strip()
    _m2_eol_tokens = ("DISC", "DEL", "LIQ", "END", "OBSOLETE", "PHASE")
    _m2_status_hit = any(tok in _m2_status_cust.upper() or tok in _m2_item_status.upper()
                         for tok in _m2_eol_tokens)

    # Stale-order signal: L13 zero AND last non-zero week was ≥ 26 weeks ago
    _m2_l13_ord = sum(float(v) for v in hist[-13:])
    _m2_last_nz_weeks_ago = None
    for _i in range(len(hist) - 1, -1, -1):
        if float(hist[_i]) > 0:
            _m2_last_nz_weeks_ago = (len(hist) - 1) - _i
            break
    _m2_stale_order = (_m2_l13_ord == 0 and _m2_last_nz_weeks_ago is not None
                       and _m2_last_nz_weeks_ago >= 26)

    _m2_eol_hit = _m2_status_hit or _m2_stale_order
    if _m2_eol_hit and model != "Inactive":
        _m2_manual_tot = sum(manual_wks)
        _m2_target = max(sum(fcst) * 0.3, _m2_manual_tot)
        if _m2_target < sum(fcst):
            _m2_scale = _m2_target / sum(fcst) if sum(fcst) > 0 else 0
            fcst = [int(v * _m2_scale) for v in fcst]
            fcst = [int(round(v / mp)) * int(mp) if v > 0 else 0 for v in fcst]
            _m2_reason = (_m2_status_cust or _m2_item_status) if _m2_status_hit \
                         else f"no orders in {_m2_last_nz_weeks_ago}w"
            meta.setdefault("drivers", []).append(
                f"M2 EOL-dampen ({_m2_reason}): cut forecast to max(AI×30%, manual)"
            )

    # F66 — Per-customer bias correction (2026-05-17).
    # For customers where planners systematically override AI >75% of the time
    # in the same direction, apply a calibration multiplier derived from the
    # trailing planner-vs-AI bias analysis.  Multiplier > 1.0 = AI under-projects.
    # Only applies to non-zero, non-Inactive forecasts.
    if model != "Inactive" and sum(fcst) > 0:
        _f66_mult = 1.0
        _cu_upper = cust_name.upper()
        for _bias_cust, _bias_mult in CUSTOMER_BIAS_CORRECTIONS.items():
            if _bias_cust in _cu_upper:
                _f66_mult = _bias_mult
                break
        if _f66_mult != 1.0:
            fcst = [snap(v * _f66_mult, mp) if v > 0 else 0 for v in fcst]
            meta.setdefault("drivers", []).append(
                f"F66 Customer bias correction ({_bias_cust}): ×{_f66_mult:.2f} "
                f"(AI systematically {'under' if _f66_mult > 1 else 'over'}-projects "
                f"this account based on planner override history)"
            )

    # F62 — Soft L4W/L13W trend blend (2026-05-17).
    # Fills the gap between F26's hard 0.85× (L4W/L13W < 0.70) and no-action.
    # When L4W is moderately below L13W (ratio 0.70–0.88), apply a proportional
    # blend that smoothly damps the forecast toward recent trend.
    # For mild acceleration (ratio 1.10–1.28), apply a proportional lift.
    # Skip for Amazon (has its own POS blend), new launches, and Inactive.
    if model != "Inactive" and sum(fcst) > 0 and not is_amazon and not _f34_is_new_launch:
        _f62_l4_nz  = [float(v) for v in hist[-4:]  if float(v or 0) > 0]
        _f62_l13_nz = [float(v) for v in hist[-13:] if float(v or 0) > 0]
        if len(_f62_l4_nz) >= 2 and len(_f62_l13_nz) >= 3:
            _f62_l4_avg  = sum(_f62_l4_nz) / len(_f62_l4_nz)
            _f62_l13_avg = sum(_f62_l13_nz) / len(_f62_l13_nz)
            _f62_ratio   = _f62_l4_avg / _f62_l13_avg if _f62_l13_avg > 0 else 1.0
            # Mild decline: 0.70 <= ratio < 0.88 (F6b/F26 already cover < 0.70)
            if 0.70 <= _f62_ratio < 0.88:
                # Blend scale: at ratio=0.70 → ×0.82, at ratio=0.88 → ×0.93
                _f62_scale = 0.6 * _f62_ratio + 0.4
                fcst = [snap(v * _f62_scale, mp) if v > 0 else 0 for v in fcst]
                meta.setdefault("drivers", []).append(
                    f"F62 Soft trend blend: L4W nz avg {_f62_l4_avg:.0f} vs L13W nz "
                    f"{_f62_l13_avg:.0f} (ratio {_f62_ratio:.2f}) → ×{_f62_scale:.2f} "
                    f"(mild decline; fills F26 gap)"
                )
            # Mild acceleration: 1.12 ≤ ratio < 1.30 (F27 already covers 1.30+)
            elif 1.12 <= _f62_ratio < 1.30:
                _f62_scale = 0.6 * _f62_ratio + 0.4
                fcst = [snap(v * _f62_scale, mp) if v > 0 else 0 for v in fcst]
                meta.setdefault("drivers", []).append(
                    f"F62 Soft trend blend: L4W nz avg {_f62_l4_avg:.0f} vs L13W nz "
                    f"{_f62_l13_avg:.0f} (ratio {_f62_ratio:.2f}) → ×{_f62_scale:.2f} "
                    f"(mild acceleration lift)"
                )

    # F63 — Multi-pack baseline floor (2026-05-17).
    # Multi-Pk Replen items have sparse L13W order history (they order less
    # frequently per SKU) but the AI under-projects by 743% avg delta.
    # When L13W is very sparse and L26W shows a higher non-zero rate, lift
    # the forecast to at least 40% of the L26W nz average × 26w.
    # Skips: Inactive, new launches, Amazon (POS blend handles it).
    _f63_item_status = (row.get("PT_Item_Status") or "").upper()
    if ("MULTI-PK" in _f63_item_status or "MULTI PK" in _f63_item_status) and \
            model != "Inactive" and sum(fcst) > 0 and not _f34_is_new_launch:
        _f63_l13_nz = [float(v) for v in hist[-13:] if float(v or 0) > 0]
        _f63_l26_nz = [float(v) for v in hist[-26:] if float(v or 0) > 0]
        _f63_l13_avg = sum(_f63_l13_nz) / len(_f63_l13_nz) if _f63_l13_nz else 0
        _f63_l26_avg = sum(_f63_l26_nz) / len(_f63_l26_nz) if _f63_l26_nz else 0
        # If L26W nz avg is materially higher than L13W nz avg (≥ 1.5×),
        # the item has more history we should be anchoring to.
        if _f63_l26_avg > _f63_l13_avg * 1.5 and _f63_l26_avg > 0:
            _f63_floor_total = _f63_l26_avg * 26 * 0.40
            if sum(fcst) < _f63_floor_total:
                _f63_scale = _f63_floor_total / sum(fcst)
                fcst = [snap(v * _f63_scale, mp) if v > 0 else 0 for v in fcst]
                meta.setdefault("drivers", []).append(
                    f"F63 Multi-pack floor: L26W nz avg {_f63_l26_avg:.0f} >> "
                    f"L13W nz avg {_f63_l13_avg:.0f} (ratio "
                    f"{_f63_l26_avg/max(_f63_l13_avg,1):.1f}×); "
                    f"lifted forecast to 40% of L26W nz rate × 26w"
                )

    # F64 — Trade calendar fall events (2026-05-17).
    # Apply modest lifts to W17-W18 (fall replenishment) and W21-W22 (holiday
    # pre-order) for non-Amazon active items.  These are the two most common
    # planner spike weeks from the manual-vs-AI analysis.  Amazon-only
    # items get Prime Day / Fall Deal lifts instead.
    if not is_amazon and model != "Inactive" and sum(fcst) > 0:
        _f64_applied = []
        for _wk in range(1, 27):
            if _wk in TRADE_FALL_REPLEN_WEEKS and fcst[_wk - 1] > 0:
                fcst[_wk - 1] = snap(fcst[_wk - 1] * TRADE_FALL_REPLEN_LIFT, mp)
                _f64_applied.append(f"W{_wk}×{TRADE_FALL_REPLEN_LIFT:.2f}")
            elif _wk in TRADE_FALL_SEASON2_WEEKS and fcst[_wk - 1] > 0:
                fcst[_wk - 1] = snap(fcst[_wk - 1] * TRADE_FALL_SEASON2_LIFT, mp)
                _f64_applied.append(f"W{_wk}×{TRADE_FALL_SEASON2_LIFT:.2f}")
        if _f64_applied and isinstance(meta, dict):
            meta.setdefault("drivers", []).append(
                f"F64 Trade calendar lift: {', '.join(_f64_applied)} "
                f"(fall replenishment W17-18 +10%, holiday pre-order W21-22 +8%)"
            )

    # F61 — Horizon confidence decay (2026-05-17).
    # Planners systematically cut the AI back-half forecast (W9-W26) more
    # aggressively than the near-term.  For items without strong seasonal
    # signals (non-Amazon, no season tag, non-new-launch), apply a gentle
    # decay to W9-W26 to better match observed planner behavior.
    # Decay: W9-W26 × 0.88.  Skips: Amazon, seasonal items, new launches, Inactive.
    _f61_seasonal_tags = {"Halloween", "Christmas", "Holiday", "July 4th",
                          "Valentines Day", "Easter", "Back to School",
                          "Prime Day", "Fall Deal"}
    _f61_is_seasonal   = bool(season and any(t.lower() in (season or "").lower()
                                              for t in _f61_seasonal_tags))
    _f61_has_cat_prof  = isinstance(meta, dict) and any(
        "category profile" in str(d).lower() or "F64" in str(d)
        for d in meta.get("drivers", [])
    )
    # P5 (2026-05-24): status_cust signals are an additional NEW-launch escape
    # hatch. Variance deep-dive showed F61 over-decaying Walmart "A: NEW"
    # Croston's items where the historic _f34 signal was borderline. Also
    # short-circuit when L4W avg is strong relative to L13W (item is in
    # active growth -- back-half should not decay).
    _f61_status_new = "NEW" in (row.get("Status_Cust") or "").upper()
    _f61_l4_avg     = sum(float(v or 0) for v in hist[-4:])  / 4  if len(hist) >= 4  else 0
    _f61_l13_avg    = sum(float(v or 0) for v in hist[-13:]) / 13 if len(hist) >= 13 else 0
    _f61_active_growth = (_f61_l13_avg > 0 and _f61_l4_avg >= _f61_l13_avg * 0.80)
    if (not is_amazon and not _f34_is_new_launch and not _f61_is_seasonal
            and not _f61_has_cat_prof and not _f61_status_new
            and not _f61_active_growth
            and model != "Inactive" and sum(fcst) > 0):
        _f61_fired = 0
        for _wi in range(8, 26):       # W9-W26 (0-indexed: 8-25)
            if fcst[_wi] > 0:
                fcst[_wi] = snap(fcst[_wi] * 0.88, mp)
                _f61_fired += 1
        if _f61_fired > 0 and isinstance(meta, dict):
            meta.setdefault("drivers", []).append(
                f"F61 Horizon decay: W9-W26 ({_f61_fired} non-zero wks) × 0.88 "
                f"(planners systematically trim back-half AI forecast; "
                f"preserves near-term W1-W8 signal)"
            )

    # F29 (2026-04-26, loosened, deferral-gated 2026-05-06) — New-item floor.
    # First version required ≥2 L4 active weeks, but new items often have only
    # 1 active week in L4 because they just shipped.  Loosened to use the
    # widest non-zero window from L4..L8: any non-zero week in the last 8
    # signals real recent demand for a new item.
    #
    # 2026-05-06 — DEFERRAL GATE for thin order history.
    # When a new item gets ONE big initial stocking order, the customer
    # typically pauses 2-3 weeks to work through that inventory before
    # resuming based on their internal demand forecast.  That internal
    # forecast comes to us in email and is NOT in any system the forecaster
    # can read — only the planner has it (and it's reflected in the manual
    # projection columns).  In that scenario F29's mechanical floor (avg of
    # non-zero × activity rate) anchors on a single big spike and produces
    # an unreliable replen rate.
    # Gate: if pattern is new/sparse AND total non-zero weeks across full
    # history < 3, defer to the planner's manual — they have the email
    # forecast context.  Skip F31 and F32 below too so their thin-history
    # clamps don't claw back the manual we just deferred to.
    _f29_manual_deferred = False
    _total_nz_f29 = sum(1 for v in hist if float(v) > 0)
    # classify() emits "inactive" | "sparse_intermittent" | "active" -- the
    # legacy "new_item" / "sparse" values were dead checks (audit 2026-05-21).
    # ISO-detected items (Initial Stocking Order) also qualify as "new" -- they
    # have one big spike and need planner manual until trickle pace establishes.
    _is_new_or_sparse = (pattern == "sparse_intermittent") or iso.get("is_iso", False)
    _manual_tot_f29 = sum(manual_wks)
    if _is_new_or_sparse and _total_nz_f29 < 3 and _manual_tot_f29 > 0:
        # Replace forecast with planner's manual (snapped to master pack).
        fcst = [int(round(v / mp)) * int(mp) if v > 0 and mp > 0 else int(v)
                for v in manual_wks]
        _f29_manual_deferred = True
        if isinstance(meta, dict):
            meta.setdefault("drivers", []).append(
                f"F29 deferred to planner manual: only {_total_nz_f29} non-zero "
                f"week(s) in history — too thin for a reliable F29 replen-rate "
                f"floor.  After a one-shot stocking order the customer typically "
                f"pauses 2-3 weeks then resumes against their internal forecast, "
                f"which we receive by email but don't have in system.  Manual "
                f"({int(_manual_tot_f29):,} units) reflects that planner context."
            )
    elif _is_new_or_sparse and sum(fcst) >= 0:
        _recent_nz_f29 = [v for v in hist[-8:] if v > 0]
        if len(_recent_nz_f29) >= 1:
            _recent_avg_f29 = sum(_recent_nz_f29) / len(_recent_nz_f29)
            # Effective weekly rate = avg-when-active × activity-rate
            _activity_f29  = len(_recent_nz_f29) / 8.0
            _floor_wk_f29  = _recent_avg_f29 * _activity_f29
            _f_wk_f29      = sum(fcst) / 26.0 if fcst else 0
            if _floor_wk_f29 > 0 and _f_wk_f29 < 0.7 * _floor_wk_f29:
                _target_total_f29 = _floor_wk_f29 * 26
                _scale_f29 = _target_total_f29 / max(1, sum(fcst)) if sum(fcst) > 0 else 0
                # Cap lift at 2× to avoid over-correcting
                _scale_f29 = min(_scale_f29, 2.0)
                if _scale_f29 > 1.0:
                    fcst = [int(round(v * _scale_f29 / mp)) * int(mp) if v > 0 and mp > 0 else int(v * _scale_f29)
                            for v in fcst]

    # F71 (renamed from F31 2026-05-21 to break tag collision with F31
    # Pre-launch passthrough; original date 2026-04-26) -- Front-week (W1) tail
    # cap.  Deep-deviation analysis showed W1 mean bias of +177% (median -9%)
    # -- the average is dragged by outliers where trend extrapolation or
    # post-event rebound produces a spike in W1.  Cap W1 at 1.3x max(L4 avg,
    # L13 avg, baseline).  Median behavior is preserved; only extreme outliers
    # are clipped.
    # Skip when F29 manual-deferral fired -- the planner's W1 reflects the
    # email-provided customer forecast we just chose to trust over thin
    # history; clipping it against L4/L13 of that thin history would defeat
    # the deferral.
    if len(fcst) >= 1 and fcst[0] > 0 and not _f29_manual_deferred:
        _l4_f71  = sum(hist[-4:])  / 4 if len(hist) >= 4 else 0
        _l13_f71 = sum(hist[-13:]) / 13 if len(hist) >= 13 else 0
        _ref_f71 = max(_l4_f71, _l13_f71, cap or 0)
        if _ref_f71 > 0:
            _w1_cap_f71 = _ref_f71 * 1.3
            if fcst[0] > _w1_cap_f71:
                fcst[0] = int(round(_w1_cap_f71 / mp)) * int(mp) if mp > 0 else int(_w1_cap_f71)
                _fire("F71")

    # F32 (2026-04-26, loosened) — Sparse-intermittent per-week + tiny-signal clamp.
    # First version used a sum-of-26-weeks clamp at 2.5× L13 sum, which rarely
    # fired because sparse-intermittent records have very small absolute totals
    # so the ratio noise that drives +299% mean is per-week, not total.
    #
    # Loosened: TWO complementary clamps for sparse_intermittent records:
    #   (a) per-week clamp: no single week > 5× L13 weekly avg (handles event
    #       lifts that compound on top of an already-noisy z).
    #   (b) tiny-signal flatline: when L26 sum < 26 units (avg < 1/wk over
    #       half a year), AND the forecast diverges >50% from L13 weekly avg,
    #       flatline to L13 weekly rate. These items are too low-signal for
    #       seasonal lifts to be meaningful — just match the run rate.
    # Skip F32 entirely when F29 manual-deferral fired — the per-week clamp
    # and tiny-signal flatline both anchor on L13/L26 history that we just
    # decided was too thin to trust; applying them would claw back the
    # planner manual we deferred to.
    if pattern == "sparse_intermittent" and not _f29_manual_deferred:
        _l13_avg_f32 = sum(hist[-13:]) / 13.0
        _l26_sum_f32 = sum(hist[-26:])
        # (a) per-week clamp
        if _l13_avg_f32 > 0:
            _wk_cap_f32 = _l13_avg_f32 * 5.0
            for _i in range(len(fcst)):
                if fcst[_i] > _wk_cap_f32:
                    fcst[_i] = int(round(_wk_cap_f32 / mp)) * int(mp) if mp > 0 else int(_wk_cap_f32)
        # (b) tiny-signal flatline
        if _l26_sum_f32 < 26 and _l13_avg_f32 > 0:
            _f_wk_f32 = sum(fcst) / 26.0 if fcst else 0
            if _f_wk_f32 > 0 and (_f_wk_f32 / _l13_avg_f32 > 1.5 or _f_wk_f32 / _l13_avg_f32 < 0.5):
                _flat_qty_f32 = _l13_avg_f32
                _snap_f32 = int(round(_flat_qty_f32 / mp)) * int(mp) if mp > 0 else int(_flat_qty_f32)
                # Spread evenly: place a flat value each week when activity rate >=1/4,
                # else cluster at original cadence. Simpler: just flat per week.
                if _snap_f32 > 0:
                    fcst = [_snap_f32] * 26

    # F36 — Stock-up burn-off suppression (Amazon-only) (2026-05-05).
    # When a recent big shipment cluster has put the customer in stocked-up
    # state, they won't replenish from us until POS sell-through burns down
    # the cohort.  Compute weeks-of-supply (WOS) from cluster_qty / pos_rate,
    # subtract weeks already elapsed since the cluster, and zero out the
    # remaining-WOS front weeks of the forecast.  Later weeks (post-burnoff)
    # keep the model's projection so we resume normal replenishment cadence.
    if is_amazon and pos_data:
        _f36 = detect_stockup_burnoff(hist, row, pos_data)
        if _f36.get("applied") and model not in ("Inactive",):
            _suppress = _f36["wos_remaining"]
            if 1 <= _suppress <= 26:
                fcst = [0] * _suppress + list(fcst[_suppress:])
                if isinstance(meta, dict):
                    meta["stockup_burnoff"] = _f36
                    meta.setdefault("drivers", []).append(
                        f"F36 Stock-up burn-off: {_f36['shipment_qty']:,} units "
                        f"shipped {_f36['weeks_since_big']}w ago at POS rate "
                        f"{_f36['pos_rate']:.0f}/wk → {_f36['wos_total']}w supply, "
                        f"{_suppress}w remaining; AI W1-W{_suppress} forced to 0 "
                        f"until burn-off completes"
                    )

    # F40 — Order-rate deceleration scaling (all customers, 2026-05-06).
    # When the customer's last 3 actual orders are running ≤ 30% of their
    # L13 non-zero average, they're decelerating hard relative to historic
    # pace.  POS-side rules (F38e) catch this on Amazon when shipment data
    # is healthy, but on customers with zero shipment history (or non-Amazon
    # records without POS data) the deceleration goes undetected and the
    # forecast inherits the historical pace.  F40 detects the pattern from
    # raw orders directly and scales the forecast toward the recent rate.
    #
    # Action — 50/50 blend toward recent pace:
    #   target_avg = (L13_nz_avg + L3_nz_avg) / 2
    #   scale_factor = target_avg / current_fcst_weekly_avg
    #   fcst[i] *= scale_factor  (snap each to master pack)
    #
    # Empirical example (1864-FF7618):
    #   After F39 dedup: L13 nz_avg = 1,755; last 3 orders = 60/120/360,
    #   L3 nz_avg = 180.  Ratio 180/1755 = 0.10 ≤ 0.30 → fires.
    #   target = (1755 + 180)/2 = 968 → scale ~0.60.
    #   AI 26-wk total: 42,180 → ~25,300 (vs ~24k user expected).
    #
    # Skip when F36 (stock-up burn-off) or F38f (offline recovery) already
    # fired — those rules govern the W1-W4 zeroing pattern; F40 would
    # incorrectly scale down the W5 catch-up burst.
    if (model not in ("Inactive",) and isinstance(fcst, list) and len(fcst) >= 26
            and isinstance(meta, dict)
            and not meta.get("stockup_burnoff")
            and not meta.get("f38f_offline")):
        _l13 = hist[-13:] if len(hist) >= 13 else hist
        _l13_nz = [v for v in _l13 if v > 0]
        # Last 3 NON-ZERO orders (skips zero weeks; reflects actual recent
        # ordering rate, not zero-week noise).
        _last3_nz = []
        for v in reversed(hist):
            if v > 0:
                _last3_nz.append(v)
                if len(_last3_nz) >= 3:
                    break
        if len(_l13_nz) >= 4 and len(_last3_nz) >= 1:
            _l13_nz_avg = sum(_l13_nz) / len(_l13_nz)
            _l3_nz_avg = sum(_last3_nz) / len(_last3_nz)
            _decel_ratio = _l3_nz_avg / max(_l13_nz_avg, 1.0)
            if _decel_ratio <= 0.30 and _l13_nz_avg > 0:
                _curr_total = sum(fcst)
                _curr_avg = _curr_total / 26.0
                _target_avg = (_l13_nz_avg + _l3_nz_avg) / 2.0
                if _curr_avg > 0 and _target_avg < _curr_avg:
                    _scale = _target_avg / _curr_avg
                    _new_fcst = [(int(round(v * _scale / mp)) * int(mp))
                                 if v > 0 and mp > 0 else int(v * _scale)
                                 for v in fcst]
                    _new_total = sum(_new_fcst)
                    fcst = _new_fcst
                    meta["f40_decel"] = {
                        "l13_nz_avg": round(_l13_nz_avg, 1),
                        "l3_nz_avg":  round(_l3_nz_avg, 1),
                        "ratio":      round(_decel_ratio, 3),
                        "scale":      round(_scale, 3),
                        "before":     int(_curr_total),
                        "after":      int(_new_total),
                    }
                    meta.setdefault("drivers", []).append(
                        f"F40 Order-rate decel: last 3 nz orders avg "
                        f"{_l3_nz_avg:.0f}/wk vs L13 nz avg {_l13_nz_avg:.0f}/wk "
                        f"(ratio {_decel_ratio:.2f} ≤ 0.30); blended target "
                        f"{_target_avg:.0f}/wk → scaled forecast ×{_scale:.2f} "
                        f"({_curr_total:,} → {_new_total:,} units over 26 wks)"
                    )

    # F42 — POS-anchored Heuristic-baseline cap (Amazon-only, 2026-05-06).
    #
    # The Heuristic model derives its baseline from post-ramp order history
    # (avg of all non-zero weeks since first-launch).  For Amazon items where
    # the customer over-ordered historically — phantom POs, multi-week-late
    # shipments forcing rebuy, or simply early-life over-stocking — that
    # post-ramp avg can be 5-15× the actual consumer pull rate (POS).  After
    # F41 dedup + F39 dedup, the most egregious phantom orders are stripped,
    # but residual large-batch noise can still leave the Heuristic baseline
    # disconnected from real demand.
    #
    # When we have Amazon POS data, that's the ground-truth signal: it's how
    # many units consumers are actually buying per week through Amazon.  An
    # Amazon item should never need to forecast at >3× POS rate sustained over
    # 26 weeks — that would mean we're filling Amazon's warehouse far faster
    # than it can sell through, which spirals into stockup-burnoff cycles.
    #
    # Trigger conditions:
    #   • is_amazon AND pos_data has L13w rate > 0
    #   • model == "Heuristic" (the over-fitting failure mode)
    #   • forecast 26-wk avg > 3.0 × POS L13w rate
    #   • skip if F36 stockup-burnoff or F38f offline-recovery already fired
    #     (those rules govern W1-W4 zeroing — F42 would over-correct on top)
    #
    # Action — anchor target = blended POS rate × 1.3 buffer:
    #   blended_pos = L4×0.40 + L13×0.40 + L26×0.20 (recency-weighted)
    #   target_avg = blended_pos × 1.3   (30% safety/restock buffer)
    #   scale_factor = target_avg / current_fcst_avg
    #   fcst[i] *= scale; snap each week to master pack
    #
    # Empirical example (1864-SF8169, Amazon):
    #   POS L13w = 249/wk; AI Heuristic produced 3,262/wk avg (84,816 over 26w)
    #   ratio 13.1× → fires.  blended_pos = 236×0.4+249×0.4+274×0.2 = 248.6
    #   target = 248.6 × 1.3 = 323/wk.  scale ≈ 0.099 → ~8,400 over 26w
    #   (vs user's "around 7-8k" expectation given 250/wk POS).
    # 2026-05-24: extended from Heuristic-only to also cover Croston's.
    # Same POS-over-projection failure mode applies when Croston's z parameter
    # is anchored to inflated order history (stock-up, phantom POs).
    if (model in ("Heuristic", "Croston's") and is_amazon and pos_data
            and isinstance(fcst, list) and len(fcst) >= 26
            and isinstance(meta, dict)
            and not meta.get("stockup_burnoff")
            and not meta.get("f38f_offline")):
        _pos_l4_f42  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
        _pos_l13_f42 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        _pos_l26_f42 = float(pos_data.get("Avg_Units_Wk_L26w") or 0)
        # Need at least L13w POS to anchor; without it, no signal.
        if _pos_l13_f42 > 0:
            _curr_total_f42 = sum(fcst)
            _curr_avg_f42 = _curr_total_f42 / 26.0
            # Blended POS: recency-weighted (40% L4 / 40% L13 / 20% L26).
            # If L4 or L26 missing, fall back to L13 alone.
            if _pos_l4_f42 > 0 and _pos_l26_f42 > 0:
                _blended_pos_f42 = (_pos_l4_f42 * 0.40 +
                                    _pos_l13_f42 * 0.40 +
                                    _pos_l26_f42 * 0.20)
            else:
                _blended_pos_f42 = _pos_l13_f42
            # Trigger only when forecast >> POS rate (>3×).  This is intentionally
            # generous: legitimate restocking & safety stock can run 1.5-2× POS,
            # so 3× is reserved for the over-fit failure mode.
            if _curr_avg_f42 > 3.0 * _blended_pos_f42:
                # Target = POS × 1.3 (30% restock/safety buffer).
                _target_avg_f42 = _blended_pos_f42 * 1.3
                _scale_f42 = _target_avg_f42 / max(_curr_avg_f42, 0.001)
                _new_fcst_f42 = [(int(round(v * _scale_f42 / mp)) * int(mp))
                                 if v > 0 and mp > 0 else int(v * _scale_f42)
                                 for v in fcst]
                _new_total_f42 = sum(_new_fcst_f42)
                fcst = _new_fcst_f42
                _fire("F42")  # 2026-05-24: now tracked in rule_fires
                meta["f42_pos_anchor"] = {
                    "pos_l4":      round(_pos_l4_f42, 1),
                    "pos_l13":     round(_pos_l13_f42, 1),
                    "pos_l26":     round(_pos_l26_f42, 1),
                    "blended_pos": round(_blended_pos_f42, 1),
                    "target_avg":  round(_target_avg_f42, 1),
                    "ratio":       round(_curr_avg_f42 / _blended_pos_f42, 1),
                    "scale":       round(_scale_f42, 3),
                    "before":      int(_curr_total_f42),
                    "after":       int(_new_total_f42),
                }
                meta.setdefault("drivers", []).append(
                    f"F42 POS-anchor ({model}): 26w avg {_curr_avg_f42:.0f}/wk "
                    f"= {_curr_avg_f42 / _blended_pos_f42:.1f}x blended POS "
                    f"({_blended_pos_f42:.0f}/wk = L4 {_pos_l4_f42:.0f}/L13 "
                    f"{_pos_l13_f42:.0f}/L26 {_pos_l26_f42:.0f}); scaled "
                    f"forecast x{_scale_f42:.2f} to POS x1.3 buffer = "
                    f"{_target_avg_f42:.0f}/wk ({_curr_total_f42:,} -> "
                    f"{_new_total_f42:,} units over 26 wks)"
                )

    # F75 -- POS fallback ceiling when Amazon DC inventory data is absent (2026-05-24).
    #
    # When amz_catalog is None (ASIN absent from Amazon_Invtry_Health or catalog
    # data not loaded for this item), the WOS-based corrections in F59h and
    # F_AMZ_RPL were skipped.  Without a DC inventory anchor, the Heuristic or
    # Croston model can produce an unconstrained forecast that significantly
    # over-projects relative to consumer pull rate.
    #
    # If POS data is available, use it as a conservative upper-bound: an Amazon
    # item should not need to order more than 2x its consumer sell-through rate
    # on a sustained basis, even accounting for DC safety stock.
    #
    # Trigger: is_amazon AND amz_catalog is None AND pos_data (L13w > 0)
    #          AND model in (Heuristic, Croston's)
    #          AND 26w forecast avg > 2.0 x POS L13w
    # Action:  scale forecast to POS_L13w x 2.0
    if (is_amazon and amz_catalog is None and pos_data
            and model in ("Heuristic", "Croston's")
            and isinstance(fcst, list) and len(fcst) >= 26):
        _f75_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        if _f75_pos_l13 > 0:
            _f75_curr_avg = sum(fcst) / 26.0
            if _f75_curr_avg > 2.0 * _f75_pos_l13:
                _f75_target  = _f75_pos_l13 * 2.0
                _f75_scale   = _f75_target / max(_f75_curr_avg, 0.001)
                _f75_before  = sum(fcst)
                _f75_new     = [(int(round(v * _f75_scale / mp)) * int(mp))
                                if v > 0 and mp > 0 else int(v * _f75_scale)
                                for v in fcst]
                _f75_after   = sum(_f75_new)
                fcst = _f75_new
                _fire("F75")
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F75 POS fallback ceiling (DC data absent): "
                        f"26w avg {_f75_curr_avg:.0f}/wk = "
                        f"{_f75_curr_avg / _f75_pos_l13:.1f}x POS L13w "
                        f"{_f75_pos_l13:.0f}/wk; scaled x{_f75_scale:.2f} to "
                        f"POS x2.0 = {_f75_target:.0f}/wk "
                        f"({_f75_before:,} -> {_f75_after:,} over 26wks)"
                    )

    # F38f -- Suppressed / Not-Buyable hard zero (Amazon-only, 2026-05-06).
    # When ASIN_Buyability_Flag is "Not Buyable" (no buybox) or "ASIN Suppressed"
    # the listing is unavailable to consumers — orders won't come in for ~4 weeks
    # while the listing is restored.  Force AI W1-W4 to zero, then dump the
    # accumulated backlog into W5 using the F35-style 25%/wk decay schedule:
    #   W1 missed at age 4 → 0% recoverable
    #   W2 missed at age 3 → 25%
    #   W3 missed at age 2 → 50%
    #   W4 missed at age 1 → 75%
    #   W5 itself        → 100%
    #   W5 catch-up      = baseline × (1.00 + 0.75 + 0.50 + 0.25 + 0.0) = 2.50×
    # W6+ resumes normal baseline cadence.
    if (is_amazon and amz_catalog and model not in ("Inactive",)
            and isinstance(fcst, list) and len(fcst) >= 26):
        _buy = (amz_catalog.get("ASIN_Buyability_Flag") or "").strip()
        if _buy in ("Not Buyable", "ASIN Suppressed"):
            # Use the model's W6 value (or the median of W6..W26 non-zero) as
            # the per-week "baseline" reference for catch-up.  Avoids latching
            # onto any pre-existing F36 burn-off zeros in W1-W5.
            _w6plus_nz = [v for v in fcst[5:] if v > 0]
            if _w6plus_nz:
                _f38_baseline_wk = sum(_w6plus_nz) / len(_w6plus_nz)
            else:
                _f38_baseline_wk = (cap or 0)  # fall back to seasonal_baseline cap
            if _f38_baseline_wk > 0:
                _f38f_w5 = _f38_baseline_wk * 2.50  # full catch-up
                # Snap to master pack
                _snap_w5 = (int(round(_f38f_w5 / mp)) * int(mp)) if mp > 0 else int(_f38f_w5)
                _new_fcst = [0, 0, 0, 0, _snap_w5] + list(fcst[5:])
                fcst = _new_fcst[:26]
                if isinstance(meta, dict):
                    meta["f38f_offline"] = {
                        "buyability": _buy,
                        "baseline_wk": round(_f38_baseline_wk, 1),
                        "w5_catchup": _snap_w5,
                    }
                    meta.setdefault("drivers", []).append(
                        f"F38f ASIN {_buy}: forced W1-W4 = 0 (4-week recovery "
                        f"window) and W5 = {_snap_w5} units (= baseline "
                        f"{_f38_baseline_wk:.0f}/wk × 2.50 catch-up via 25%/wk "
                        f"decay); W6+ resumes normal cadence"
                    )

    # F67 — Amazon buy-box = $0 near-term dampener (2026-05-17).
    # Distinct from F38f (formal "Not Buyable"/"ASIN Suppressed" flag):
    # when Amazon_Buybox == 0 but the item isn't formally suppressed, the
    # listing is live but has no active buy-box price — often a temporary
    # pricing hold, 3P competition flush-out, or compliance review.
    # Pattern: usually resolves within 4 weeks.  Cut W1-W4 by 70%, leave
    # W5-W26 unchanged; flag so planners can see the signal.
    if (is_amazon and amz_catalog and model not in ("Inactive",)
            and isinstance(fcst, list) and len(fcst) >= 26):
        _f67_bb = float(amz_catalog.get("Amazon_Buybox") or 0)
        _f67_flag = (amz_catalog.get("ASIN_Buyability_Flag") or "").strip()
        # Only fire when buybox is 0 AND the item isn't already handled by F38f.
        if _f67_bb == 0 and _f67_flag not in ("Not Buyable", "ASIN Suppressed"):
            _f67_weeks_cut = 0
            for _wi in range(4):          # W1-W4 only
                if fcst[_wi] > 0:
                    fcst[_wi] = snap(fcst[_wi] * 0.30, mp)
                    _f67_weeks_cut += 1
            if _f67_weeks_cut > 0 and isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F67 Buy-box $0 dampener: W1-W4 cut 70% ({_f67_weeks_cut}w "
                    f"affected) — listing active but no buy-box price; "
                    f"W5-W26 unchanged (expect restoration within 4 weeks)"
                )

    # F37 — Forward inventory-shortfall adjustment (2026-05-05).
    # Reads anticipated on-hand for the next 26 weeks (Inv_Wk1..Inv_Wk26)
    # which already have the current AI projection deducted.  When a week
    # would run short, cap the AI projection to what we can ship and roll
    # the unmet portion forward as a backlog cohort that decays 25% per
    # week of non-shipment (matches F34/F35 schedule; fully lost at age 4+).
    # apply_oh_shortfall_adjustment returns int-rounded values; we snap to
    # MP here in the orchestrator (single snap pass).
    # P6 (2026-05-24): Skip F37 OH-shortfall capping on NEW-launch items and
    # items in active growth.  Variance deep-dive showed F37 zeroing W1-W2 on
    # Walmart "A: NEW" Croston's records (#4, 5, 9, 10) when planner has
    # 7,000-10,000 in those weeks.  Warehouse SOH being high doesn't mean
    # demand is satisfied -- new-launch ordering happens concurrently.
    _f37_status_new = "NEW" in (row.get("Status_Cust") or "").upper()
    _f37_l4_avg     = sum(float(v or 0) for v in hist[-4:])  / 4  if len(hist) >= 4  else 0
    _f37_l13_avg    = sum(float(v or 0) for v in hist[-13:]) / 13 if len(hist) >= 13 else 0
    _f37_active_growth = (_f37_l13_avg > 0 and _f37_l4_avg >= _f37_l13_avg * 0.80)
    _f37_skip       = _f37_status_new or _f37_active_growth
    if model not in ("Inactive",) and not _f37_skip:
        _adjusted_f37, _f37_adjustments = apply_oh_shortfall_adjustment(row, fcst)
        if _f37_adjustments:
            # Snap to master pack to keep ship qty consistent with cadence
            fcst = [int(round(v / mp)) * int(mp) if v > 0 and mp > 0 else int(v)
                    for v in _adjusted_f37]
            if isinstance(meta, dict):
                meta["oh_shortfall_adjustments"] = _f37_adjustments
                _changed_weeks = sorted({a["week"] for a in _f37_adjustments})
                meta.setdefault("drivers", []).append(
                    f"F37 OH-shortfall adjustment: {len(_f37_adjustments)} weeks "
                    f"capped/lifted by anticipated on-hand "
                    f"(weeks {','.join(map(str, _changed_weeks[:8]))}"
                    f"{'…' if len(_changed_weeks) > 8 else ''}); unmet demand "
                    f"rolled forward with 25%/wk decay until age 4 (fully lost)"
                )
    elif _f37_skip and isinstance(meta, dict):
        _f37_skip_reason = "Status_Cust=NEW" if _f37_status_new else "active-growth (L4>=0.8*L13)"
        # NOTE: don't put "F37" at the front of the driver string -- the
        # rule_fires regex would match it as a fire event when it's actually
        # a skip. Use "[F37-skip]" with brackets to make the intent explicit
        # and avoid the regex.
        meta.setdefault("drivers", []).append(
            f"P6 OH-shortfall guard activated: {_f37_skip_reason} -- "
            f"new-launch / growth items not subject to F37 W1-W2 zeroing"
        )

    # F45 — Per-week forecast cap (defensive guardrail, 2026-05-06).
    #
    # No single forecast week may exceed 2.0× the L26 non-zero mean, regardless
    # of which model produced it.  This catches model-artifact spikes — typically
    # from Seasonal Baseline's position-based seasonal index reading a historical
    # large order at the same calendar position, then amplifying it into a
    # forecast week far above any realistic order size — that survive even after
    # F43 attenuates recent spikes (since F43 is window-limited to the last 4
    # weeks of input history).
    #
    # Threshold rationale:
    #   2.0× L26-nz-mean is loose enough to allow legitimate seasonal lift
    #   (Prime Day, Fall Deal up to 1.25-1.50× baseline), promotional weeks,
    #   and natural cadence variance.  Tight enough to catch single-week
    #   model artifacts where one forecast week is 5-10× the customer's
    #   typical order size — those are virtually never legitimate demand.
    #
    # Skip conditions:
    #   • Inactive model (forecast already 0)
    #   • F36 stockup-burnoff (intentional W1-W4 zeros + W5 catch-up burst —
    #     the burst is by design, capping it would re-introduce phantom demand)
    #   • F38f offline-recovery (similar W5 catch-up pattern)
    #   • L26 has fewer than 6 non-zero weeks (insufficient baseline)
    #
    # Empirical example (1864-FF25895, AMAZON.COM.KYDC):
    #   L26 nz mean = ~2,508/wk.  F43 capped recent spikes but Seasonal Baseline
    #   read older spike at LW_15-16 (14,328) into seasonal index → W11 = 24,240.
    #   Cap at 2.0×2508 = 5,016 → W11 reduced to 5,016 (~80% reduction).
    if (model not in ("Inactive",) and isinstance(fcst, list) and len(fcst) >= 1
            and isinstance(meta, dict)
            and not meta.get("stockup_burnoff")
            and not meta.get("f38f_offline")):
        _l26_f45 = hist[-26:] if len(hist) >= 26 else hist
        _l26_nz_f45 = [v for v in _l26_f45 if v > 0]
        if len(_l26_nz_f45) >= 6:
            _l26_nz_mean_f45 = sum(_l26_nz_f45) / len(_l26_nz_f45)
            _f45_cap = 2.0 * _l26_nz_mean_f45
            _f45_caps = []
            for _i in range(len(fcst)):
                if fcst[_i] > _f45_cap:
                    _f45_caps.append({
                        "week":      _i + 1,
                        "original":  int(fcst[_i]),
                        "capped":    int(round(_f45_cap / mp) * int(mp)) if mp > 0 else int(_f45_cap),
                        "ratio":     round(fcst[_i] / max(_l26_nz_mean_f45, 1.0), 2),
                    })
                    fcst[_i] = (int(round(_f45_cap / mp)) * int(mp)) if mp > 0 else int(_f45_cap)
            if _f45_caps:
                meta["f45_per_week_caps"] = _f45_caps
                _capped_weeks = ",".join(str(c["week"]) for c in _f45_caps[:8])
                _max_orig = max(c["original"] for c in _f45_caps)
                _max_ratio = max(c["ratio"] for c in _f45_caps)
                meta.setdefault("drivers", []).append(
                    f"F45 Per-week cap: {len(_f45_caps)} forecast week(s) capped to "
                    f"{int(_f45_cap):,} (= 2.0× L26 nz-mean {int(_l26_nz_mean_f45):,}); "
                    f"weeks {_capped_weeks}{'…' if len(_f45_caps) > 8 else ''}; "
                    f"largest pre-cap = {_max_orig:,} ({_max_ratio:.1f}× nz-mean) — "
                    f"likely seasonal-index amplification from older historical spike"
                )

    # F46 — Post-F44 forecast rebuild (2026-05-06).
    #
    # When F44 fires (dense-override after F43 caps), we know two things:
    #   1. The customer was steady BEFORE the recent disruption (≥60% nz in
    #      L26 prior to last 4w)
    #   2. The recent 8 weeks contain spikes + zeros that are artifacts of
    #      that disruption (warehouse stockout, customer over-order, etc.)
    #
    # In that case, the underlying Seasonal Baseline output is still polluted
    # by the recent disruption pattern: it produces sparse output (zeros at
    # positions matching disrupted weeks) and uses an L13 baseline that's
    # depressed by zero weeks.  Result: total volume ends up well below the
    # customer's actual steady-state demand.
    #
    # F46 rebuilds the forecast from the customer's PRE-DISRUPTION baseline:
    #   • baseline = L26 non-zero mean (post-F43, post-F39, post-F41)
    #     - this is the customer's typical-order rate from the broader window
    #     - excludes the disrupted recent weeks' zeros from the average
    #   • profile = damped seasonal indices clamped to [0.7, 1.3]
    #     - allows ±30% week-to-week variation (matches typical customer
    #       cadence variation seen in manual planner data)
    #     - prevents the post-rebuild forecast from inheriting any extreme
    #       seasonal positions that survived F43
    #   • forecast[i] = baseline × profile[i], snapped to master pack
    #
    # Skip conditions:
    #   • Inactive model (no rebuild needed)
    #   • F36 / F38f firing (those rules govern intentional zeroing patterns)
    #   • Insufficient L26 baseline (<6 nz weeks)
    #
    # Empirical example (1864-FF25895, AMAZON.COM.KYDC):
    #   F43+F44+F45 produced 17,760/26w = 683/wk avg with mostly-zero output.
    #   L26 nz mean = ~2,508.  Damped seasonal profile evens out to 0.7-1.3.
    #   Rebuild gives 26 weeks at 1,800-3,200/wk → ~65,000 total.
    #   Matches manual planner total of 59,500.
    if (model not in ("Inactive",) and isinstance(fcst, list) and len(fcst) >= 26
            and isinstance(meta, dict)
            and meta.get("f44_dense_override")
            and not meta.get("stockup_burnoff")
            and not meta.get("f38f_offline")):
        _l26_f46 = hist[-26:] if len(hist) >= 26 else hist
        _l26_nz_f46 = [v for v in _l26_f46 if v > 0]
        if len(_l26_nz_f46) >= 6:
            _baseline_f46 = sum(_l26_nz_f46) / len(_l26_nz_f46)
            # Compute per-week seasonal indices from L26 hist, damped.
            _l26_mean_f46 = float(np.mean(_l26_f46)) if float(np.mean(_l26_f46)) > 0 else 1.0
            _raw_seasonal_f46 = [v / _l26_mean_f46 for v in _l26_f46]
            # Damp toward 1.0: blend 30% raw / 70% uniform → keeps modest
            # seasonal shape while flattening extremes.
            _damped_f46 = [0.7 * 1.0 + 0.3 * s for s in _raw_seasonal_f46]
            # Clamp to [0.7, 1.3] for additional safety against post-damp extremes.
            _profile_f46 = [max(0.7, min(1.3, s)) for s in _damped_f46]
            # Renormalize so profile mean = 1.0 (preserves total volume target).
            _pmean_f46 = sum(_profile_f46) / len(_profile_f46)
            if _pmean_f46 > 0:
                _profile_f46 = [s / _pmean_f46 for s in _profile_f46]
            _old_total_f46 = sum(fcst)
            _new_fcst_f46 = []
            for _i in range(26):
                _wk_qty_f46 = _baseline_f46 * _profile_f46[_i] if _i < len(_profile_f46) else _baseline_f46
                if mp > 0:
                    _new_fcst_f46.append(int(round(_wk_qty_f46 / mp) * int(mp)))
                else:
                    _new_fcst_f46.append(int(_wk_qty_f46))
            _new_total_f46 = sum(_new_fcst_f46)
            fcst = _new_fcst_f46
            meta["f46_post_f44_rebuild"] = {
                "baseline":  round(_baseline_f46, 1),
                "before":    int(_old_total_f46),
                "after":     int(_new_total_f46),
                "profile_min": round(min(_profile_f46), 3),
                "profile_max": round(max(_profile_f46), 3),
            }
            meta.setdefault("drivers", []).append(
                f"F46 Post-F44 rebuild: baseline = L26 nz-mean {int(_baseline_f46):,}/wk × "
                f"damped seasonal profile [{min(_profile_f46):.2f}-{max(_profile_f46):.2f}]; "
                f"replaced disruption-tainted forecast ({_old_total_f46:,}) with steady-state "
                f"distribution ({_new_total_f46:,}) over 26 wks"
            )

    # F33 reverted 2026-04-26 -- see CHANGELOG.md.

    # VP-Q4 (2026-05-03) — Don't double-count confirmed customer POs.
    # For any forward week where a confirmed PO already exists, zero out
    # AI_PRJ for that week.  Per VP guidance: strict zero (not subtract) —
    # the confirmed PO IS the demand signal for that week; downstream replen
    # already counts the PO, so the AI projection on top would be double-count.
    #
    # PO signal priority (2026-05-17 fix):
    #   1. Opn_W1..Opn_W26 from the Projections row  — already week-grid-aligned,
    #      matches exactly what planners see in the viewer.  Use this when the
    #      row has any nonzero open-PO quantity at all.
    #   2. fetch_open_pos_forward() (report #27, bucketed by cancel date) — fallback
    #      only when the Opn_W fields are all zero.  Its cancel-date bucketing can
    #      shift by ±1 week vs the forecast grid, so we prefer the QB row fields.
    _opn_row_wk = [float(row.get(c) or 0) for c in OPN_COLS]
    _opn_row_total = sum(_opn_row_wk)
    if _opn_row_total > 0:
        _effective_po_wk = _opn_row_wk          # use QB Opn_W fields (grid-aligned)
    elif open_po_wk:
        _effective_po_wk = list(open_po_wk)     # fall back to fetched PO report
    else:
        _effective_po_wk = []
    _vp_q4_zeroed_idx = set()   # 0-based indexes VP-Q4 set to 0 — F59d/F59a must skip
    # VP-Q4 — zero AI and MAN PRJ when a confirmed open PO covers that week.
    # W1: BOTH AI PRJ and MAN PRJ are zeroed when Opn_W1 > 0.  The open PO is
    # the confirmed demand signal; showing any projection on top would double-count
    # it in forward demand.  MAN PRJ W2+ are NOT auto-zeroed (planner handles via
    # codepage warning + Zero button).
    # The _po_qty > 0 condition is the only gate — if no PO exists the rule is
    # silent, so it naturally does nothing when Opn_W1 = 0.
    if _effective_po_wk:
        _po_zeroed = []
        for _i in range(0, min(26, len(fcst))):   # W1 through W26
            _po_qty = float(_effective_po_wk[_i]) if _i < len(_effective_po_wk) else 0.0
            if _po_qty > 0 and fcst[_i] > 0:
                _po_zeroed.append((_i + 1, fcst[_i], _po_qty))
                fcst[_i] = 0
                _vp_q4_zeroed_idx.add(_i)   # guard: F59d must not restore these
        # W1 MAN PRJ: zero whenever Opn_W1 > 0 regardless of AI value
        # (AI may already be 0 from a prior rule, but MAN PRJ still needs zeroing).
        _vp_q4_w1_po = float(_effective_po_wk[0]) if _effective_po_wk else 0.0
        if _vp_q4_w1_po > 0 and isinstance(manual_wks, list) and len(manual_wks) > 0:
            manual_wks[0] = 0
        if _po_zeroed and isinstance(meta, dict):
            _po_total_removed = sum(z[1] for z in _po_zeroed)
            _po_total_qty     = sum(z[2] for z in _po_zeroed)
            _po_weeks_str     = ",".join(f"W{z[0]}" for z in _po_zeroed[:6])
            if len(_po_zeroed) > 6:
                _po_weeks_str += f"+{len(_po_zeroed)-6}"
            meta.setdefault("drivers", []).append(
                f"VP-Q4 zeroed AI in {len(_po_zeroed)} weeks with confirmed POs "
                f"(removed {_po_total_removed:,} forecast units)"
            )
            # F56 — Surface PO-adjusted total in record so the alert can show
            # "AI 36k + open POs 6k = total forward demand 42k" rather than
            # the visible AI-only number that confuses planners (2026-05-08).
            meta["po_zeroed_weeks"]   = [z[0] for z in _po_zeroed]
            meta["po_total_qty"]      = _po_total_qty
            meta["po_total_removed"]  = _po_total_removed

    # VP-OP (2026-05-20) — Off-price PO buffer zone.
    # Off-price accounts buy in large, infrequent batches — once a PO is confirmed
    # they will not reorder within 4 weeks on either side of it.  Zero out AI
    # forecast in that ±4-week window unless a separate PO already exists in the
    # specific week (those are already handled by VP-Q4 above and represent a
    # distinct, independent order event).
    if _effective_po_wk and _is_offprice_cust(cust_name):
        _op_po_weeks = {i for i, qty in enumerate(_effective_po_wk[:26]) if qty > 0}
        _op_buf_zeroed = []
        for _po_idx in sorted(_op_po_weeks):
            for _offset in range(-4, 5):
                if _offset == 0:
                    continue  # PO week itself already handled by VP-Q4
                _tgt = _po_idx + _offset
                if 0 <= _tgt < 26 and _tgt not in _op_po_weeks and fcst[_tgt] > 0:
                    _op_buf_zeroed.append((_tgt + 1, fcst[_tgt]))
                    fcst[_tgt] = 0
        if _op_buf_zeroed and isinstance(meta, dict):
            _op_removed = sum(z[1] for z in _op_buf_zeroed)
            _op_wks_str = ",".join(f"W{z[0]}" for z in sorted(_op_buf_zeroed, key=lambda x: x[0])[:6])
            if len(_op_buf_zeroed) > 6:
                _op_wks_str += f"+{len(_op_buf_zeroed)-6}"
            meta.setdefault("drivers", []).append(
                f"VP-OP zeroed AI in {len(_op_buf_zeroed)} buffer wks around off-price POs "
                f"({_op_wks_str}; removed {_op_removed:,.0f} units)"
            )
            _fire("VP-OP")

    # VP-FL (2026-05-17) — Frontload dampening.
    # When a customer places a significantly above-normal order (W1 open PO or
    # last-week actual orders >= 2.5x the L13W average), they're pulling forward
    # demand.  Their inventory position will be elevated for the next 2-4 weeks,
    # meaning they'll reorder less than normal during that recovery window.
    #
    # Detection: use the max of W1 confirmed PO (Opn_W1) and last-week raw orders
    # (Ord_LW) as the spike signal — whichever fired most recently.
    #
    # Dampening: percentage reduction applied to the first N non-zeroed forecast
    # weeks after the spike, with a decay curve.  Capped at 30% per week to
    # avoid over-correcting on genuine demand acceleration or seasonal builds.
    _vfl_opn_w1    = float(row.get("Opn_W1") or 0)
    _vfl_ord_lw    = float(row.get("Ord_LW")  or 0)
    _vfl_spike_qty = max(_vfl_opn_w1, _vfl_ord_lw)
    # L13W average from cleaned hist (non-zero weeks only, more robust than all-weeks)
    _vfl_hist13 = [float(v or 0) for v in hist[-13:]] if len(hist) >= 13 else list(hist)
    _vfl_hist13_nz = [v for v in _vfl_hist13 if v > 0]
    _vfl_l13w_avg  = sum(_vfl_hist13_nz) / len(_vfl_hist13_nz) if _vfl_hist13_nz else 0
    _vfl_ratio     = (_vfl_spike_qty / _vfl_l13w_avg) if _vfl_l13w_avg > 0 else 0
    # Trigger: spike >= 2.5x normal AND at least 500 units above baseline (noise filter)
    _vfl_fires = (
        _vfl_ratio >= 2.5
        and _vfl_spike_qty >= _vfl_l13w_avg + 500
        and _vfl_l13w_avg >= 100              # don't fire on trivially low-volume items
    )
    if _vfl_fires:
        _fire("VP-FL")
        # dampen_pct scales with severity, capped at 30%
        _vfl_dampen = min(0.30, (_vfl_ratio - 1.5) * 0.10)
        # Decay weights for up to 4 affected weeks (diminishing impact over time)
        _vfl_decay  = [1.00, 0.65, 0.40, 0.20]
        # How many weeks to affect: 2 for mild spike, up to 4 for extreme
        _vfl_n_wks  = 2 if _vfl_ratio < 3.0 else (3 if _vfl_ratio < 5.0 else 4)
        _vfl_applied = []
        _vfl_slot = 0   # tracks how many non-zeroed weeks we've dampened
        for _i in range(min(26, len(fcst))):
            if _vfl_slot >= _vfl_n_wks:
                break
            if fcst[_i] <= 0:
                continue   # already zeroed (VP-Q4 or other) — skip, don't count
            _vfl_week_dampen = _vfl_dampen * _vfl_decay[_vfl_slot]
            _vfl_orig = fcst[_i]
            _vfl_cut  = _vfl_orig * _vfl_week_dampen
            _vfl_new  = max(0, _vfl_orig - _vfl_cut)
            if mp and mp > 0:
                _vfl_new = int(round(_vfl_new / mp)) * int(mp)
            else:
                _vfl_new = int(round(_vfl_new))
            fcst[_i] = _vfl_new
            _vfl_applied.append((_i + 1, _vfl_orig, _vfl_new))
            _vfl_slot += 1
        if _vfl_applied and isinstance(meta, dict):
            _vfl_wks_str = ",".join(f"W{z[0]}" for z in _vfl_applied)
            meta.setdefault("drivers", []).append(
                f"VP-FL frontload dampening: spike {_vfl_spike_qty:,.0f}u = "
                f"{_vfl_ratio:.1f}x L13W avg ({_vfl_l13w_avg:,.0f}/wk); "
                f"reduced {_vfl_wks_str} by {_vfl_dampen*100:.0f}% (decay curve)"
            )

    # F52 — Future-Delete (FD) wind-down (2026-05-08, planner request).
    # Items with Status_Cust starting "FD" are being phased out by the
    # customer.  The status sometimes encodes the last-order date as
    # MM/YY (e.g. "FD 09/26" = last order Sep 2026).  When present, we
    # truncate the forecast at that week and apply a 4-week linear taper
    # leading up to it (full → ~25% over the last 4 ordering weeks),
    # matching the gradual wind-down planners observe.  When the date
    # isn't parsable, we fall back to the LAST non-zero manual projection
    # week — the planner's own input becomes the cutoff.
    _f52_status_cust = (row.get("Status_Cust") or "").upper().strip()
    if _f52_status_cust.startswith("FD"):
        import re as _re_f52
        from datetime import date as _date_f52, timedelta as _td_f52
        # Parse first MM/YY or MM/YYYY pattern from the status string.
        _f52_m = _re_f52.search(r"(\d{1,2})/(\d{2,4})", _f52_status_cust)
        _f52_target_idx = None  # 0-based index of last forecast week to keep > 0
        _f52_source = None
        if _f52_m:
            _mm = int(_f52_m.group(1))
            _yy = int(_f52_m.group(2))
            if _yy < 100:
                _yy += 2000
            try:
                _target_date = _date_f52(_yy, _mm, 28)  # end-of-month anchor
                _col0 = ORIG_PRJ_COLS[0]  # "MM_DD_W1"
                _wm, _wd = int(_col0[0:2]), int(_col0[3:5])
                _today = _date_f52.today()
                _prj_start = _date_f52(_today.year, _wm, _wd)
                if (_prj_start - _today).days < -180:
                    _prj_start = _date_f52(_today.year + 1, _wm, _wd)
                _weeks_off = (_target_date - _prj_start).days // 7
                if 0 <= _weeks_off <= 25:
                    _f52_target_idx = _weeks_off
                    _f52_source = f"status date {_mm:02d}/{_yy}"
                elif _weeks_off > 25:
                    _f52_target_idx = 25  # extends beyond horizon — full window
                    _f52_source = f"status date {_mm:02d}/{_yy} (beyond W26)"
                else:
                    _f52_target_idx = -1  # already past — full zero
                    _f52_source = f"status date {_mm:02d}/{_yy} (in past)"
            except (ValueError, TypeError):
                pass
        if _f52_target_idx is None:
            # Fall back to last non-zero manual projection week.
            for _i in range(len(manual_wks) - 1, -1, -1):
                if float(manual_wks[_i] or 0) > 0:
                    _f52_target_idx = _i
                    _f52_source = f"last manual projection W{_i + 1}"
                    break
            if _f52_target_idx is None:
                _f52_target_idx = -1
                _f52_source = "no manual projections — full zero"
        # Apply wind-down + truncate.
        _f52_pre_total = sum(fcst)
        if _f52_target_idx < 0:
            for _i in range(len(fcst)):
                fcst[_i] = 0
        else:
            # Linear taper across the 4 weeks ending at target_idx (inclusive).
            # Multipliers: target-3 → 0.85, target-2 → 0.65, target-1 → 0.45,
            # target → 0.25.  Pre-target weeks unchanged.  Post-target zeroed.
            _taper = {0: 0.25, 1: 0.45, 2: 0.65, 3: 0.85}
            for _i in range(len(fcst)):
                if _i > _f52_target_idx:
                    fcst[_i] = 0
                else:
                    _dist = _f52_target_idx - _i
                    if _dist in _taper:
                        _scaled = fcst[_i] * _taper[_dist]
                        # Snap to master pack
                        if mp and mp > 0:
                            fcst[_i] = int(round(_scaled / mp)) * int(mp)
                        else:
                            fcst[_i] = int(round(_scaled))
        _f52_post_total = sum(fcst)
        if isinstance(meta, dict):
            meta.setdefault("drivers", []).append(
                f"F52 Future-Delete wind-down: Status_Cust='{_f52_status_cust[:30]}' "
                f"({_f52_source}) → last forecast week W{_f52_target_idx + 1 if _f52_target_idx >= 0 else 'none'}; "
                f"4-week taper applied; total {_f52_pre_total:,} → {_f52_post_total:,}"
            )

        # P4 (2026-05-24): F52 planner-residual anchor.
        # Variance deep-dive showed F52 wind-down still over-projecting vs
        # planner's stable residual rate (#8 BB13437CLR/12: AI 69k vs Man
        # 7.5k flat at 300/wk). When the planner has a flat low residual
        # they're signaling "this is the wind-down rate" -- cap AI to
        # [planner_rate * 1.5, planner_rate * 2.5] band per week.
        _f52_man_nz = [float(v or 0) for v in manual_wks if float(v or 0) > 0]
        if _f52_man_nz and len(_f52_man_nz) >= 4:
            _f52_planner_rate = sum(_f52_man_nz) / len(_f52_man_nz)
            # Only fire when planner residual is meaningful (rate <= 2000/wk
            # = wind-down territory) AND most planner weeks cluster within
            # 50% of the mean (stable residual signal).
            _f52_planner_cv = (max(_f52_man_nz) - min(_f52_man_nz)) / max(_f52_planner_rate, 1)
            if _f52_planner_rate <= 2000 and _f52_planner_cv <= 1.5:
                _f52_floor = _f52_planner_rate * 0.5
                _f52_ceil  = _f52_planner_rate * 2.5
                _f52_anchored_changes = 0
                for _i in range(len(fcst)):
                    if fcst[_i] <= 0:
                        continue
                    if fcst[_i] > _f52_ceil:
                        _f52_capped = _f52_ceil
                        if mp and mp > 0:
                            fcst[_i] = int(round(_f52_capped / mp)) * int(mp)
                        else:
                            fcst[_i] = int(round(_f52_capped))
                        _f52_anchored_changes += 1
                if _f52_anchored_changes > 0 and isinstance(meta, dict):
                    _post_anchor = sum(fcst)
                    meta.setdefault("drivers", []).append(
                        f"P4 F52 planner-residual anchor: planner rate "
                        f"{_f52_planner_rate:.0f}/wk (n={len(_f52_man_nz)} nz wks); "
                        f"capped {_f52_anchored_changes} AI weeks at "
                        f"{_f52_ceil:.0f}/wk ceiling; total {_f52_post_total:,} -> {_post_anchor:,}"
                    )

    # ── F59o — Amazon seasonal overlay for Heuristic / Croston's (2026-05-21) ──
    # Heuristic and Croston's blend the category profile normalized to mean=1.0,
    # which pulls off-month weeks BELOW the flat baseline to make room for peaks.
    # Per planner request (Option A), apply the category profile as an ADDITIVE
    # FLOOR instead: off-months stay at the flat rate, peak months get lifted.
    # Total 26w demand can only increase vs the flat model output.
    #
    # Algorithm:
    #   1. flat_ref = mean of non-zero fcst weeks (model's implied weekly rate).
    #   2. Get category profile via _get_category_profile() (already floored at
    #      SEASONAL_FLOOR=1.0 per month -- no month multiplier < 1.0).
    #   3. Damp the raw per-month uplift by DAMP_O=0.50.  Balances seasonal
    #      signal strength against model uncertainty on sparse histories.
    #   4. Per week: fcst[w] = max(fcst[w], snap(flat_ref * damped_mult, mp)).
    #      VP-Q4-zeroed weeks are never raised.
    #
    # Fires before F59a-F59n so those corrections work on the shaped forecast.
    if (is_amazon
            and model in ("Heuristic", "Croston's")
            and isinstance(fcst, list) and len(fcst) >= 26):
        _f59o_profile = _get_category_profile(
            description, product_category, product_subcategory,
            brand, brand_pt, season=season
        )
        if _f59o_profile is not None:
            _f59o_nz   = [v for v in fcst if v > 0]
            _f59o_flat = (sum(_f59o_nz) / len(_f59o_nz)) if _f59o_nz else 0.0
            if _f59o_flat > 0:
                from datetime import date as _dt59o, timedelta as _td59o
                _f59o_col   = ORIG_PRJ_COLS[0]        # e.g. "05_17_W1"
                _f59o_mo    = int(_f59o_col[0:2])
                _f59o_dy    = int(_f59o_col[3:5])
                _f59o_today = _dt59o.today()
                _f59o_start = _dt59o(_f59o_today.year, _f59o_mo, _f59o_dy)
                if (_f59o_start - _f59o_today).days < -180:
                    _f59o_start = _dt59o(_f59o_today.year + 1, _f59o_mo, _f59o_dy)
                DAMP_O        = 0.50
                _f59o_changed = False
                for _wi in range(26):
                    if _wi in _vp_q4_zeroed_idx:
                        continue        # VP-Q4 zeroed -- never restore
                    _wk_month  = (_f59o_start + _td59o(weeks=_wi)).month
                    _raw_mult  = float(_f59o_profile[_wk_month - 1])
                    _d_mult    = max(1.0, 1.0 + (_raw_mult - 1.0) * DAMP_O)
                    _f59o_fl   = snap(_f59o_flat * _d_mult, mp)
                    if fcst[_wi] < _f59o_fl:
                        fcst[_wi]     = _f59o_fl
                        _f59o_changed = True
                if _f59o_changed:
                    _fire("F59o")
                    if isinstance(meta, dict):
                        _f59o_pk_raw = max(_f59o_profile)
                        _f59o_pk_mo  = _f59o_profile.index(_f59o_pk_raw) + 1
                        _f59o_pk_d   = max(1.0, 1.0 + (_f59o_pk_raw - 1.0) * DAMP_O)
                        meta.setdefault("drivers", []).append(
                            f"F59o seasonal overlay ({model}): category profile "
                            f"applied as uplift floor (DAMP={DAMP_O}); "
                            f"flat ref {_f59o_flat:.0f}/wk; "
                            f"peak month {_f59o_pk_mo} raw {_f59o_pk_raw:.2f}x "
                            f"-> damped {_f59o_pk_d:.2f}x "
                            f"({snap(_f59o_flat * _f59o_pk_d, mp):.0f}/wk)"
                        )

    # ── F59/F60 — Amazon demand-signal corrections (2026-05-15) ───────────────
    # F59: Synthesized from planner review of 13 account-1864 items (10/13
    #      under-projected, 1 over).  Sub-rules:
    #   F59a — L4W floor, velocity-tiered (high-vol more aggressive per planner)
    #   F59b — Recency upweight when L4W >> L13W (structural acceleration)
    #   F59c — OOS-week exclusion from velocity baselines (annotation)
    #   F59d — Zero-week suppression, velocity-tiered floor multiplier
    #   F59e — Buy-box price-movement velocity buffer
    #   F59f — Deceleration cap (prevents HW trend over-extrapolation)
    #   F59g — High-volume forward buffer (≥500/wk: +8% across full window)
    #   F59o — Seasonal overlay floor for Heuristic/Croston's (Option A, see above)
    # F60: EC-transition narrative (history inherited in pre-pass above).
    #
    # Velocity tiers (all based on L13W non-zero avg, Amazon only):
    #   HIGH:  L13W_nz ≥ 500/wk  → more aggressive floors + F59g buffer
    #   MED:   L13W_nz 150–499   → moderately aggressive
    #   LOW:   L13W_nz < 150     → standard (original) settings
    #
    # Placement: BEFORE F58 so explicit Tell-AI comment replays supersede.
    if is_amazon and not model.startswith("Inactive") and not model.startswith("OTB"):

        # ── Velocity baselines with OOS-week exclusion (F59c) ────────────────
        # When Days_Amazon_OOS_L30d ≥ 7, the item has had material OOS recently.
        # Those weeks show as order zeros and depress all-weeks averages.
        # Use non-zero averages (in-stock velocity) so floors are grounded in
        # real demand, not demand + stockout weeks blended together.
        _f59_oos_days   = float((amz_catalog or {}).get("Days_Amazon_OOS_L30d_") or 0)
        _f59_oos_active = _f59_oos_days >= 7

        _f59_l4w_raw    = hist[-4:]  if len(hist) >= 4  else list(hist)
        _f59_l4w_nz     = [v for v in _f59_l4w_raw  if v > 0]
        _f59_l4w_avg    = (
            sum(_f59_l4w_nz) / len(_f59_l4w_nz)
            if (_f59_oos_active and _f59_l4w_nz)
            else sum(_f59_l4w_raw) / max(len(_f59_l4w_raw), 1)
        )

        _f59_l8w_raw    = hist[-8:]  if len(hist) >= 8  else list(hist)
        _f59_l8w_nz     = [v for v in _f59_l8w_raw  if v > 0]
        _f59_l8w_avg    = (
            sum(_f59_l8w_nz) / len(_f59_l8w_nz)
            if (_f59_oos_active and _f59_l8w_nz)
            else sum(_f59_l8w_raw) / max(len(_f59_l8w_raw), 1)
        )

        _f59_l13w_raw   = hist[-13:] if len(hist) >= 13 else list(hist)
        _f59_l13w_nz    = [v for v in _f59_l13w_raw if v > 0]
        _f59_l13w_avg   = (
            sum(_f59_l13w_nz) / len(_f59_l13w_nz) if _f59_l13w_nz else 0.0
        )

        # Annotate when OOS exclusion materially changed the L4W baseline
        if _f59_oos_active and _f59_l4w_nz and len(_f59_l4w_nz) < 4:
            _f59c_all_avg = sum(_f59_l4w_raw) / max(len(_f59_l4w_raw), 1)
            if isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F59c OOS velocity exclusion: {4 - len(_f59_l4w_nz)}/4 L4W "
                    f"weeks excluded ({_f59_oos_days:.0f} OOS days/L30d); "
                    f"in-stock L4W={_f59_l4w_avg:.0f} vs all-weeks={_f59c_all_avg:.0f}"
                )

        # ── F59f — Deceleration cap (runs BEFORE floor rules) ────────────────
        # When L4W < L8W < L13W (consistent decline across all three windows)
        # and the model projects above L4W, cap each week at L4W×1.15.
        # Prevents Holt-Winters from carrying a downtrend into the projection.
        # Runs first so the floor rules below see a corrected basis.
        _f59f_decel = (
            _f59_l4w_avg  > 0 and _f59_l8w_avg > 0 and _f59_l13w_avg > 0
            and _f59_l4w_avg  < _f59_l8w_avg  * 0.90  # 4w clearly below 8w
            and _f59_l8w_avg  < _f59_l13w_avg * 0.90  # 8w clearly below 13w
            and _f59_l4w_avg  < _f59_l13w_avg * 0.80  # sustained overall decline
        )
        if _f59f_decel:
            _f59f_cap   = _f59_l4w_avg * 1.15
            _f59f_snapped = snap(_f59f_cap, mp)
            _f59f_weeks = sum(1 for v in fcst if v > _f59f_cap)
            if _f59f_weeks > 0:
                fcst = [_f59f_snapped if v > _f59f_cap else v for v in fcst]
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F59f Deceleration cap: L4W {_f59_l4w_avg:.0f} < "
                        f"L8W {_f59_l8w_avg:.0f} < L13W {_f59_l13w_avg:.0f} "
                        f"(consistent decline) → {_f59f_weeks}w capped at "
                        f"L4W×1.15={_f59f_cap:.0f}/wk"
                    )

        # ── F59a — L4W floor, velocity-tiered (momentum-gated) ───────────────
        # Prevents non-zero forecast weeks from falling below a % of in-stock
        # L4W velocity when momentum is holding (L4W ≥ 85% of L8W).
        # Tiered by L13W non-zero avg to be more aggressive on high-vol items
        # per planner feedback: "much more risk in under projecting than over
        # projecting" on high-vol items.
        #   HIGH vol (L13W_nz ≥ 500):  floor = L4W × 0.95
        #   MED  vol (L13W_nz 150-499): floor = L4W × 0.90
        #   LOW  vol (L13W_nz < 150):   floor = L4W × 0.85
        #
        # Skip when F18 has already applied a POS-anchored cap (stocked-up or
        # above-POS blend).  In that case the L4W order history reflects a
        # front-loaded stock-up event — using it as the floor would undo the
        # entire point of F18 by restoring the inflated order rate.
        _f59_f18_capped = isinstance(meta, dict) and meta.get("f18_capped_down", False)
        if _f59_l13w_avg >= 500:
            _f59a_mult, _f59a_tier = 0.95, "HIGH"
        elif _f59_l13w_avg >= 150:
            _f59a_mult, _f59a_tier = 0.90, "MED"
        else:
            _f59a_mult, _f59a_tier = 0.85, "LOW"

        _f59a_momentum = (
            _f59_l8w_avg == 0
            or _f59_l4w_avg >= _f59_l8w_avg * 0.85
        )
        _f59a_floor = _f59_l4w_avg * _f59a_mult
        if _f59_l4w_avg > 0 and _f59a_momentum and _f59a_floor > 0 and not _f59_f18_capped:
            _f59a_fired = 0
            for _i in range(len(fcst)):
                if fcst[_i] > 0 and fcst[_i] < _f59a_floor:
                    fcst[_i] = snap(_f59a_floor, mp)
                    _f59a_fired += 1
            if _f59a_fired > 0 and isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F59a Amazon L4W floor ({_f59a_tier}-vol): {_f59a_fired}w raised to "
                    f"L4W×{_f59a_mult:.2f}={_f59a_floor:.0f}/wk "
                    f"(in-stock L4W={_f59_l4w_avg:.0f}, L13W_nz={_f59_l13w_avg:.0f})"
                )

        # ── F59b — Recency upweight when L4W >> L13W ─────────────────────────
        # When L4W is ≥1.4× L13W non-zero avg, the item has structurally
        # accelerated recently (keyword gain, buy-box win, distribution add).
        # Model is discounting this as noise; re-blend non-zero weeks toward
        # a 60% L4W / 40% L13W target to preserve the recent signal.
        # Skip when F18 POS-anchored cap fired (same reason as F59a above).
        if (not _f59_f18_capped
                and _f59_l4w_avg > 0 and _f59_l13w_avg > 0
                and _f59_l4w_avg >= _f59_l13w_avg * 1.40):
            _f59b_target = _f59_l4w_avg * 0.60 + _f59_l13w_avg * 0.40
            _f59b_fired  = 0
            for _i in range(len(fcst)):
                if fcst[_i] > 0 and fcst[_i] < _f59b_target:
                    fcst[_i] = snap(_f59b_target, mp)
                    _f59b_fired += 1
            if _f59b_fired > 0 and isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F59b Recency upweight: L4W {_f59_l4w_avg:.0f} ≥ 1.4× "
                    f"L13W {_f59_l13w_avg:.0f} → {_f59b_fired}w raised to "
                    f"60%×L4W+40%×L13W={_f59b_target:.0f}/wk"
                )

        # ── F59d — Zero-week suppression, velocity-tiered ────────────────────
        # Amazon items with meaningful weekly velocity should never produce
        # week-level zero forecasts — a zero tells the replenishment engine to
        # stop ordering, which triggers OOS within days on fast-movers.
        # Floor multiplier is tiered per planner's high-vol aggression request:
        #   HIGH vol (L13W_nz ≥ 500):  floor = L13W_nz × 0.65
        #   MED  vol (L13W_nz 200-499): floor = L13W_nz × 0.55
        #   LOW  vol (L13W_nz 75-199):  floor = L13W_nz × 0.50
        #   Below 75/wk: no zero-suppression (intermittent demand is expected)
        if _f59_l13w_avg >= 500:
            _f59d_mult, _f59d_tier, _f59d_thresh = 0.65, "HIGH", 500
        elif _f59_l13w_avg >= 200:
            _f59d_mult, _f59d_tier, _f59d_thresh = 0.55, "MED",  200
        elif _f59_l13w_avg >= 75:
            _f59d_mult, _f59d_tier, _f59d_thresh = 0.50, "LOW",  75
        else:
            _f59d_mult = 0.0  # no zero suppression below 75/wk

        if _f59d_mult > 0:
            _f59d_floor = _f59_l13w_avg * _f59d_mult
            _f59d_fired = 0
            for _i in range(len(fcst)):
                if fcst[_i] == 0 and _i not in _vp_q4_zeroed_idx:
                    fcst[_i] = snap(_f59d_floor, mp)
                    _f59d_fired += 1
            if _f59d_fired > 0 and isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F59d Zero-suppression ({_f59d_tier}-vol): {_f59d_fired}w raised "
                    f"0→L13W_nz×{_f59d_mult:.2f}={_f59d_floor:.0f}/wk "
                    f"(L13W_nz={_f59_l13w_avg:.0f} ≥ {_f59d_thresh})"
                )

        # ── F59e — Buy-box price-movement velocity buffer ─────────────────────
        # Two triggers:
        #   (1) Recent price drop: L4W avg unit revenue > current buybox × 1.10
        #       The avg L4W revenue was 10%+ above the current listed price,
        #       meaning the price dropped during this window.  The resulting
        #       sales lift should be treated as the new structural baseline,
        #       not noise around a prior higher-price mean.
        #   (2) Below-MAP pricing: buybox < MAP × 0.85
        #       Active buy-box competition is driving velocity above what the
        #       model captures from smooth order-history averages.
        # Response: apply +15% lift on all non-zero forecast weeks.
        if amz_catalog:
            _f59e_bb     = float(amz_catalog.get("Amazon_Buybox") or 0)
            _f59e_aur_l4 = float(amz_catalog.get("AUR_L4w")       or 0)
            _f59e_map    = float(amz_catalog.get("MAP_Price")      or 0)

            _f59e_price_drop = (
                _f59e_bb > 0 and _f59e_aur_l4 > 0
                and _f59e_aur_l4 > _f59e_bb * 1.10
            )
            _f59e_below_map = (
                _f59e_bb > 0 and _f59e_map > 0
                and _f59e_bb < _f59e_map * 0.85
            )

            if _f59e_price_drop or _f59e_below_map:
                fcst = [snap(v * 1.15, mp) if v > 0 else 0 for v in fcst]
                _f59e_reasons = []
                if _f59e_price_drop:
                    _f59e_reasons.append(
                        f"AUR_L4w ${_f59e_aur_l4:.2f} > BB ${_f59e_bb:.2f} "
                        f"(+{(_f59e_aur_l4 / _f59e_bb - 1) * 100:.0f}% — recent drop)"
                    )
                if _f59e_below_map:
                    _f59e_reasons.append(
                        f"BB ${_f59e_bb:.2f} < MAP ${_f59e_map:.2f} "
                        f"(−{(1 - _f59e_bb / _f59e_map) * 100:.0f}% below MAP)"
                    )
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F59e Buy-box price signal: {'; '.join(_f59e_reasons)} "
                        f"→ +15% velocity buffer applied"
                    )

        # ── F59g — High-volume forward buffer ────────────────────────────────
        # For items with L13W non-zero avg ≥ 500/wk (high-vol), apply an 8%
        # upward buffer across all non-zero forecast weeks.  These items have
        # disproportionate OOS risk (lost ranking, lost page share) that is
        # far more costly than carrying a few extra weeks of safety stock.
        # Planner feedback: "much more risk in under projecting than over
        # projecting" on high-volume items.
        if _f59_l13w_avg >= 500:
            fcst = [snap(v * 1.08, mp) if v > 0 else 0 for v in fcst]
            if isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F59g High-vol forward buffer: L13W_nz={_f59_l13w_avg:.0f} ≥ 500 "
                    f"→ +8% applied across all non-zero weeks "
                    f"(OOS asymmetric risk on high-velocity items)"
                )

        # ── F59h — Amazon DC inventory health balancing ──────────────────────
        # Uses Sellable On-Hand (SOH), Open PO Quantity (OPO), and Weeks-of-
        # Supply On-Hand (WOS) from Amazon_Invtry_Health to temper near-term
        # forecasts when Amazon is above their 8–12 wk target inventory range.
        #
        # Amazon's target inventory range is 8–12 weeks of supply.  Above 12
        # wks the aggregate position is overstocked.  However, because each DC
        # orders independently, even an overstocked network may see week-to-week
        # POs from individual DCs that need inventory — so the correction is
        # intentionally mild (soft taper, not a hard cut).  The further above
        # 12 wks, the steeper the taper, capped at 20% to preserve DC-level
        # order flow.
        #
        #   WOS > 12   → trim W1-W8 gently: each wk above 12 = ~1.5%, cap 20%
        #   WOS < 3, no OPO → flag only; do NOT suppress (OOS risk > overstock)
        #   OPO ≥ 8 wks of demand → note near-term supply is pre-covered
        #
        # Placement: after F59g (high-vol buffer) but before F58 (AI comment
        # replay), so planners can override via AI comments if needed.
        _f59h_soh = float((amz_catalog or {}).get("Inv_SOH") or 0)
        _f59h_opo = float((amz_catalog or {}).get("Inv_OPO") or 0)
        _f59h_wos = float((amz_catalog or {}).get("Inv_WOS") or 0)
        # Fallback: if Inv_WOS not populated (ASIN lookup miss or field absent)
        # but SOH/OPO are present, derive WOS from position / POS velocity.
        # Mirrors the same fallback already used in F69-WOS (2026-05-20).
        if _f59h_wos <= 0 and (_f59h_soh > 0 or _f59h_opo > 0):
            _f59h_pos_fb = float((pos_data or {}).get("Avg_Units_Wk_L13w") or 0)
            if _f59h_pos_fb > 0:
                _f59h_wos = (_f59h_soh + _f59h_opo) / _f59h_pos_fb

        if is_amazon and amz_catalog and _f59h_wos > 0:
            _f59h_vel     = _f59_l13w_avg if _f59_l13w_avg > 0 else max(sum(fcst) / 26, 1)
            _f59h_opo_wos = _f59h_opo / max(_f59h_vel, 1)
            # F59h replen gate: use order BEHAVIOR (model classification) as the
            # primary signal, not just the QB PT_Item_Status label.  Seasonal
            # Baseline = ≥50% non-zero weeks = orders most weeks = replenishment
            # behavior regardless of how the item is tagged in QB.  Heuristic
            # items also order regularly enough to warrant the power curve.
            # Croston's and Sparse Intermittent keep the mild taper — lumpy
            # demand makes WOS a less reliable overstock signal (2026-05-20).
            _f59h_is_replen = (
                "replen" in (row.get("PT_Item_Status") or "").lower()
                or model in ("Seasonal Baseline", "Heuristic")
            )

            if _f59h_wos > 12:
                if _f59h_is_replen:
                    # FXX — Amazon Replen overstock: power-curve dampening across
                    # all 26 weeks.  Amazon has many independent DCs so some sporadic
                    # demand persists even when aggregate WOS is high — floor at 10%.
                    # Formula: max(0.10, (12 / wos) ^ 1.5)
                    # wos=16 → 53%  wos=20 → 38%  wos=24 → 27%  wos=29 → 21%  wos=40+ → 10%
                    _f59h_dampen = max(0.10, (12.0 / _f59h_wos) ** 1.5)
                    fcst = [snap(max(0, v * _f59h_dampen), mp) if v > 0 else 0
                            for v in fcst]
                    _fire("F59h")
                    if isinstance(meta, dict):
                        meta.setdefault("drivers", []).append(
                            f"F59h Amazon-Replen overstock dampen: WOS={_f59h_wos:.1f}wks "
                            f"(target 12wks) — all 26W x{_f59h_dampen:.0%} "
                            f"(floor 10%); SOH={_f59h_soh:,.0f}u OPO={_f59h_opo:,.0f}u"
                        )
                else:
                    if _f59h_wos > 20:
                        # Extreme overstock: hard burn-down zero for near-term weeks,
                        # then anchor post-burn period to POS rate.
                        #
                        # At WOS > 20 the DC is so overstocked that orders stop
                        # entirely until inventory burns down to target.  The mild
                        # 1.5%/wk taper is not meaningful at this level.
                        #
                        # Burn-down weeks = round(WOS - 12), capped at 16 so stale
                        # WOS data does not zero out more than 60% of the horizon.
                        # Amazon's target range is 8-12 wks; after burn-down,
                        # anchor remaining weeks to POS L13W (true consumer velocity).
                        _f59h_burn = min(int(round(_f59h_wos - 12)), 16)
                        _f59h_pv   = float((pos_data or {}).get("Avg_Units_Wk_L13w") or 0)
                        for i in range(min(_f59h_burn, len(fcst))):
                            fcst[i] = 0
                        if _f59h_pv >= 50:
                            for i in range(_f59h_burn, len(fcst)):
                                fcst[i] = snap(_f59h_pv, mp)
                        _fire("F59h")
                        if isinstance(meta, dict):
                            _f59h_post = (
                                f"W{_f59h_burn + 1}-W26 anchored to POS L13W {_f59h_pv:.0f}/wk"
                                if _f59h_pv >= 50 else "post-burn held at model baseline"
                            )
                            meta.setdefault("drivers", []).append(
                                f"F59h extreme overstock: WOS={_f59h_wos:.1f}wks "
                                f"(target 12wks) -- W1-W{_f59h_burn} zeroed (burn-down); "
                                f"{_f59h_post}. "
                                f"SOH={_f59h_soh:,.0f}u OPO={_f59h_opo:,.0f}u"
                            )
                    else:
                        # Moderately above target range (12 < WOS <= 20): soft taper.
                        # 1.5% per week above 12, cap 20%.
                        # Mild by design: DC-level ordering from individual DCs
                        # continues even when the aggregate network WOS is above
                        # target, so we do not cut projections aggressively.
                        _f59h_trim = min(0.20, (_f59h_wos - 12) * 0.015)
                        for i in range(min(8, len(fcst))):
                            fcst[i] = snap(max(0, fcst[i] * (1 - _f59h_trim)), mp)
                        _fire("F59h")
                        if isinstance(meta, dict):
                            meta.setdefault("drivers", []).append(
                                f"F59h DC above target range: WOS={_f59h_wos:.1f}wks "
                                f"(target 8-12wks), SOH={_f59h_soh:,.0f}u, "
                                f"OPO={_f59h_opo:,.0f}u -- W1-W8 -{_f59h_trim*100:.0f}% soft taper"
                            )
            elif _f59h_wos < 3 and _f59h_opo == 0:
                _fire("F59h")
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F59h DC low-stock alert: WOS={_f59h_wos:.1f}wks, OPO=0 — "
                        f"reorder risk; projections NOT suppressed"
                    )
            elif _f59h_opo_wos >= 8 and isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F59h OPO coverage: {_f59h_opo:,.0f}u open PO ≈ {_f59h_opo_wos:.1f}wks "
                    f"forward supply — near-term gap pre-covered"
                )

    # ── F_RTL_WOS — Retailer OH inventory WOS adjustment ─────────────────────
    # When a retailer's on-hand WOS deviates from the normal 8-week target,
    # adjust the forecast proportionally.  Understocked retailers will reorder
    # more aggressively; overstocked retailers will slow replenishment until
    # inventory burns down.  Adjustment is gradual and capped:
    #   WOS < 8 : +4% per wk below target, max +20% (at WOS <= 3)
    #   WOS > 8 : -3.5% per wk above target, max -30% (at WOS >= 16.6)
    # Applied as a uniform multiplier across all non-zero forecast weeks.
    if rtl_pos and not is_amazon and model not in ("Inactive", "OTB (zero)",
                                                    "Pre-launch NEW (manual passthrough)"):
        _rtl_oh_wos = float(rtl_pos.get("OH_WOS") or 0)
        _rtl_oh_lw  = float(rtl_pos.get("OH_Units_LW") or 0)
        _rtl_l4w    = float(rtl_pos.get("Avg_Units_Wk_L4w") or 0)
        if _rtl_oh_wos > 0:
            if _rtl_oh_wos < 8.0:
                # Understocked: retailer will accelerate reorders
                _rtl_wos_mult = min(1.20, 1.0 + 0.04 * (8.0 - _rtl_oh_wos))
            else:
                # Overstocked: retailer will slow replenishment
                _rtl_wos_mult = max(0.70, 1.0 - 0.035 * (_rtl_oh_wos - 8.0))
            if abs(_rtl_wos_mult - 1.0) >= 0.02:   # only fire if >= 2% adjustment
                fcst = [snap(max(0, v * _rtl_wos_mult), mp) if v > 0 else 0
                        for v in fcst]
                _fire("F_RTL_WOS")
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F_RTL_WOS retailer OH WOS: {_rtl_oh_wos:.1f}wks "
                        f"(target 8wks) -> {_rtl_wos_mult:.0%} uniform adjust; "
                        f"OH={_rtl_oh_lw:,.0f}u "
                        f"POS_L4W={_rtl_l4w:,.0f}/wk"
                    )

        # ── F59i — POS anchor for Amazon items with healthy DC WOS ───────────
        # EC = "Ecomm Ready" -- standard Amazon DC items in poly-bag packaging.
        # They have their own ASINs, own order history, own DC inventory.
        # Treat EC items identically to non-EC items -- the only gates are
        # WOS (DC coverage) and the AI-vs-POS ratio.
        #
        # When the near-term forecast (W1-W4 non-zero avg) runs >15% above POS
        # L4W and the DC has adequate coverage (WOS >= 6), the order-history
        # baseline is likely inflated by inventory build rather than genuine
        # demand growth.  Blend toward POS L13W.
        #
        # Gates: is_amazon, POS data present, not DI-blended, WOS >= 6 or unknown.
        _f59i_ms = (row.get("Mstyle") or row.get("mstyle") or "").upper()
        if (is_amazon
                and model not in ("Inactive", "OTB (zero)",
                                  "Pre-launch NEW (manual passthrough)")
                and not row.get("_di_blend")
                and isinstance(fcst, list) and len(fcst) >= 26
                and pos_data):
            _f59i_pos_l4  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
            _f59i_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
            _f59i_pos_l52 = float(pos_data.get("Avg_Units_Wk_L52w") or 0)
            _f59i_wos     = _f59h_wos   # reuse WOS computed in F59h block above

            # F60 EC-transition override: when the EC item inherited parent
            # order history (F60 fired), the parent's historical order rate is
            # NOT a reliable forward signal for the EC item.  The parent may
            # have been ordered at higher rates for the parent ASIN, but the EC
            # ASIN starts fresh -- POS is the correct demand anchor.
            # Allow F59i to fire regardless of WOS when F60 is active and the
            # ratio is above the moderate threshold.  Use moderate blend only
            # (never severe anchor) for WOS 1-5 so we don't over-correct when
            # Amazon is also in a brief restock phase for the new EC variant.
            _f59i_ec_override = (
                _f60_is_ec_transition
                and _f59h_wos > 0    # known WOS (not 0 = unknown)
                and _f59h_wos < 6    # would normally be gated
            )
            if (_f59i_pos_l4 >= 100 and _f59i_pos_l13 > 0
                    and amz_catalog
                    and (_f59h_wos >= 6 or _f59h_wos == 0
                         or _f59i_ec_override)):
                # ── F59i: all Amazon models — tiered POS correction ───────────
                # Amazon orders include DC inventory management (restock, safety-
                # stock builds, catch-up after short-ship) on top of consumer
                # demand.  When DC WOS is healthy (>= 6) and the forecast
                # materially exceeds POS L4W, the excess is almost certainly
                # inventory management noise, not real demand growth.
                #
                # WOS gate: fires when WOS >= 6 (healthy DC) OR WOS == 0
                # (unresolvable) OR _f59i_ec_override (F60 EC-transition active
                # and WOS is explicitly low 1-5 -- inherited parent history
                # over-represents EC forward demand; POS is the correct anchor).
                # WOS == 0 means "unknown", not "zero inventory"; we still apply
                # a correction but cap it at moderate blend (never severe anchor)
                # because we cannot confirm the DC is actually well-stocked.
                # WOS 1-5 (explicitly low) is skipped: Amazon is actively
                # restocking and the elevated orders are real fill-in demand.
                #
                # Applies to ALL non-EC models (Seasonal Baseline, Heuristic,
                # Croston's, etc.) -- a flat Heuristic forecast at 1.7x POS
                # with a healthy DC is just as wrong as an inflated Seasonal one.
                #
                # _f59i_wos_capped: True when WOS is unknown (0) OR when the
                # EC-transition override fired (WOS 1-5 with inherited parent
                # history) -- in either case, restrict >1.40x cases to moderate
                # blend instead of severe anchor, since DC may be in a mild
                # restock phase for the new variant.
                _f59i_wos_capped = (_f59h_wos == 0 or _f59i_ec_override)
                #
                # Two-tier correction by severity:
                #   Moderate (1.15x-1.40x): 50/50 blend toward POS L13W
                #     -- gentle pull-back, preserves some model signal
                #   Severe (> 1.40x): direct POS L4W anchor (floor 0.60)
                #     -- at 40%+ above consumer demand with a healthy DC the
                #        excess is overwhelmingly inventory noise, not growth
                _f59i_w1_4_nz  = [v for v in fcst[:4] if v > 0]
                _f59i_w1_4_avg = sum(_f59i_w1_4_nz) / max(len(_f59i_w1_4_nz), 1)
                _f59i_ratio    = (_f59i_w1_4_avg / _f59i_pos_l4
                                  if _f59i_pos_l4 > 0 else 0)
                if _f59i_ratio > 1.15:
                    # ── Price-recovery bypass (2026-05-20) ───────────────────
                    # When AUR was recently corrected and POS is rapidly
                    # accelerating back toward the pre-problem run rate,
                    # anchoring to the depressed L4W POS would suppress the
                    # forecast to the mid-recovery level and under-project
                    # true forward demand.
                    #
                    # Pattern: AUR too low -> Amazon stops ordering (L13W/L26W
                    # goes dark). AUR corrected -> orders resume and POS
                    # snaps back.  L4W POS reflects partial recovery only;
                    # L52W POS is the pre-problem baseline.
                    #
                    # Detection (all must hold):
                    #   L4W POS > L13W POS * 2.0 -- rapid recent acceleration
                    #   L52W POS > L13W POS * 3.0 -- L13W was anomalously
                    #                                 depressed (dark period)
                    #   AUR >= MAP * 0.75          -- retail largely corrected
                    #                                 (or no MAP data available)
                    #                                 75% threshold: price was
                    #                                 corrected but may still be
                    #                                 slightly below MAP during
                    #                                 the recovery ramp
                    #
                    # Action: skip F59i suppression entirely.  The order-history
                    # baseline reflects genuine reactivation demand, not
                    # inventory management noise.
                    _f59i_aur = float((amz_catalog or {}).get("AUR_L4w")  or 0)
                    _f59i_map = float((amz_catalog or {}).get("MAP_Price") or 0)
                    _f59i_price_recovery = (
                        _f59i_pos_l13 > 0
                        and _f59i_pos_l4  > _f59i_pos_l13 * 2.0
                        and _f59i_pos_l52 > _f59i_pos_l13 * 3.0
                        and (_f59i_map == 0 or _f59i_aur >= _f59i_map * 0.75)
                    )
                    if _f59i_price_recovery:
                        if isinstance(meta, dict):
                            _f59i_aur_note = (
                                f"AUR {_f59i_aur:.2f} >= MAP {_f59i_map:.2f} * 75%"
                                f" -- retail largely corrected. "
                                if _f59i_map > 0 else ""
                            )
                            meta.setdefault("drivers", []).append(
                                f"F59i price-recovery bypass: POS L4W "
                                f"{_f59i_pos_l4:.0f}/wk is "
                                f"{_f59i_pos_l4/max(_f59i_pos_l13,1):.1f}x L13W "
                                f"{_f59i_pos_l13:.0f}/wk (rapid acceleration). "
                                f"L52W {_f59i_pos_l52:.0f}/wk shows healthy "
                                f"pre-problem run rate vs depressed L13W dark "
                                f"period. {_f59i_aur_note}"
                                f"Skipping POS suppression -- order-history "
                                f"baseline reflects reactivation demand, not "
                                f"inventory noise. Model: {model}."
                            )
                    else:
                        if _f59i_ratio > 1.40 and _f59i_ec_override:
                            # EC-transition anchor: inherited parent history
                            # over-represents forward demand for the new EC ASIN.
                            # Use max(POS_LW, POS_L4W) as the direct anchor --
                            # no 0.60 floor, because we have a confirmed genuine
                            # demand signal (AUR >= MAP checked in override gate).
                            # F59m will add gap-fill uplift on top.
                            _f59i_pos_lw_ec = float(
                                (pos_data or {}).get("Ordered_Units_LW") or 0)
                            _f59i_anchor = (
                                max(_f59i_pos_lw_ec, _f59i_pos_l4)
                                / max(_f59i_w1_4_avg, 1)
                            )
                            _f59i_mode   = "EC-anchor"
                        elif _f59i_ratio > 1.40 and not _f59i_wos_capped:
                            # Severe: anchor to POS L4W (floor 0.60 guards against
                            # temporarily-depressed POS reading).
                            # Only fires when WOS is confirmed >= 6 (known healthy).
                            # When WOS is unknown (capped), fall through to moderate
                            # blend -- we cannot confirm DC is well-stocked.
                            _f59i_anchor = max(0.60, _f59i_pos_l4 / _f59i_w1_4_avg)
                            _f59i_mode   = "strong"
                        else:
                            # Moderate: soft blend toward POS L13W.
                            # Used for 1.15x-1.40x ratio, OR when ratio > 1.40
                            # but WOS is unknown (capped) -- conservative action
                            # on uncertain DC-stock data.
                            _f59i_anchor = (
                                (_f59i_pos_l13 * 0.50 + _f59i_w1_4_avg * 0.50)
                                / _f59i_w1_4_avg
                            )
                            _f59i_mode   = (
                                "blend (WOS unknown)" if _f59i_wos_capped
                                else "blend"
                            )
                        _f59i_anchor = min(_f59i_anchor, 1.0)  # never inflate
                        for _wi in range(len(fcst)):
                            fcst[_wi] = snap(fcst[_wi] * _f59i_anchor, mp)
                        _fire("F59i")
                        if isinstance(meta, dict):
                            _f59i_pos_lw_disp = float(
                                (pos_data or {}).get("Ordered_Units_LW") or 0)
                            _f59i_desc = (
                                f"EC-transition POS anchor "
                                f"(max(POS_LW {_f59i_pos_lw_disp:.0f}, "
                                f"L4W {_f59i_pos_l4:.0f})/wk; "
                                f"parent history discarded as EC demand signal)"
                                if _f59i_mode == "EC-anchor"
                                else (
                                    f"direct POS L4W anchor (floor 60%)"
                                    if _f59i_mode == "strong"
                                    else f"50% blend toward POS L13W {_f59i_pos_l13:.0f}/wk"
                                )
                            )
                            _f59i_wos_label = (
                                f"F60 EC-transition (WOS={_f59h_wos:.1f}wks)"
                                if _f59i_ec_override
                                else (
                                    "DC WOS unknown"
                                    if _f59i_wos_capped
                                    else f"DC WOS {_f59h_wos:.1f}wks (healthy)"
                                )
                            )
                            meta.setdefault("drivers", []).append(
                                f"F59i POS anchor ({_f59i_mode}): AI W1-W4 avg "
                                f"{_f59i_w1_4_avg:.0f}/wk is "
                                f"{(_f59i_ratio - 1) * 100:.0f}% above consumer "
                                f"POS L4W {_f59i_pos_l4:.0f}/wk with {_f59i_wos_label} -- "
                                f"order history inflated by DC inventory management, "
                                f"not demand growth. Rescaled x{_f59i_anchor:.3f} via "
                                f"{_f59i_desc}. Model: {model}."
                            )

        # ── F59j — Amazon DC understock: POS floor + early-week restock lift ──
        # When Amazon DC WOS < 8 (below target range of 8-12 wks), Amazon will
        # order ABOVE consumer POS rate to rebuild DC inventory.  The AI should:
        #   1. Floor every non-zero week at POS L4W (never project below
        #      consumer demand -- that is always the minimum ordering rate)
        #   2. Add a restock lift to W1-W3 to bring DC back to 8 WOS target,
        #      accounting for units already in transit (OPO).
        #
        # Restock deficit = max(0, 8 * demand_rate - (SOH + OPO))
        # where demand_rate = SOH / WOS (Amazon's internal sell-through rate).
        # Spread deficit evenly over 3 weeks.
        #
        # This is directionally opposite to what F59i does: F59i reduces when
        # DC is healthy (WOS >= 6) and AI > POS; F59j lifts when DC is low.
        # They are mutually exclusive by WOS gate (F59i needs WOS >= 6).
        if (is_amazon
                and pos_data
                and model not in ("Inactive", "OTB (zero)",
                                  "Pre-launch NEW (manual passthrough)")
                and isinstance(fcst, list) and len(fcst) >= 26
                and 0 < _f59h_wos < 8):
            _f59j_pos_l4  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
            if _f59j_pos_l4 >= 50:
                # Amazon's internal demand rate (implied by their own WOS calc)
                _f59j_demand_rate = _f59h_soh / _f59h_wos if _f59h_wos > 0 else _f59j_pos_l4
                # How many units does Amazon need to reach 8 WOS?
                _f59j_target_inv  = 8.0 * _f59j_demand_rate
                _f59j_pipeline    = _f59h_soh + _f59h_opo   # OH + already-ordered OPO
                _f59j_deficit     = max(0.0, _f59j_target_inv - _f59j_pipeline)
                # Spread restock over W1-W3
                _f59j_restock_wks = 3
                _f59j_lift        = snap(_f59j_deficit / _f59j_restock_wks, mp) \
                                    if _f59j_deficit > 0 else 0
                _f59j_floor       = snap(_f59j_pos_l4, mp)
                _f59j_changed     = False
                for _wi in range(len(fcst)):
                    _orig = fcst[_wi]
                    if _wi < _f59j_restock_wks and _f59j_lift > 0:
                        # Restock weeks: base = max(model, POS floor) + lift
                        fcst[_wi] = snap(max(fcst[_wi], _f59j_floor) + _f59j_lift, mp)
                    elif fcst[_wi] > 0 and fcst[_wi] < _f59j_floor:
                        # Sustaining weeks: floor at POS L4W
                        fcst[_wi] = _f59j_floor
                    if fcst[_wi] != _orig:
                        _f59j_changed = True
                if _f59j_changed:
                    _fire("F59j")
                    if isinstance(meta, dict):
                        meta.setdefault("drivers", []).append(
                            f"F59j DC restock: WOS={_f59h_wos:.1f}wks below 8wk "
                            f"target. SOH={_f59h_soh:,.0f}u + OPO={_f59h_opo:,.0f}u "
                            f"pipeline vs target {_f59j_target_inv:,.0f}u "
                            f"(8wks x {_f59j_demand_rate:,.0f}/wk demand rate). "
                            + (f"Restock deficit {_f59j_deficit:,.0f}u spread over "
                               f"W1-W{_f59j_restock_wks} (+{_f59j_lift:,.0f}u/wk lift). "
                               if _f59j_deficit > 0 else "OPO already covers 8 WOS target. ") +
                            f"All weeks floored at POS L4W {_f59j_pos_l4:,.0f}/wk."
                        )

        # ── F59k — Amazon L4W=0 + POS also declining: EOL wind-down correction ──
        # When Amazon L4W orders have gone completely to zero AND consumer POS
        # also shows material decline (L4W POS < 40% of L13W POS), this is a
        # genuine EOL or channel wind-down scenario -- NOT a stockout recovery.
        # The F50 stockout guard (at the baseline level) may have preserved the
        # full L13W order baseline; this rule corrects the forward forecast here.
        #
        # Key discriminators vs stockout (F50):
        #   - Genuine OOS: L4W orders=0 because DC ran out; POS may also be 0
        #     but oos_days >= 14 signals the inventory gap.  F59k skips.
        #   - EOL/wind-down: L4W orders=0 AND consumer POS L4W < 40% of L13W POS.
        #     Both the DC and end consumer have stopped/slowed.  F59k fires.
        #
        # Anchor: MAX(pos_l4w, pos_l13w * 0.50) as target weekly rate.
        # Planners historically project 40-55% of L13W when facing this pattern
        # (observed: FF9298EC, FF9297/24, FF8649/24 in 2026-05-20 gap analysis).
        # Scale floor = 0.25 to avoid over-correction if POS data is stale.
        if (pos_data and isinstance(fcst, list) and len(fcst) >= 4
                and _f59_l4w_avg == 0           # no orders at all in L4W
                and _f59_oos_days < 14          # not a genuine OOS situation
                and _f59_l13w_avg >= 200):      # item had real order history
            _f59k_pos_l4  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
            _f59k_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
            if (_f59k_pos_l13 >= 100                         # credible POS signal
                    and _f59k_pos_l4 < _f59k_pos_l13 * 0.40):  # consumer also declining
                _f59k_target  = max(_f59k_pos_l4, _f59k_pos_l13 * 0.50)
                _f59k_nz      = [v for v in fcst if v > 0]
                _f59k_avg     = sum(_f59k_nz) / max(len(_f59k_nz), 1)
                if _f59k_avg > _f59k_target * 1.10:  # only correct if AI materially above target
                    _f59k_scale = max(0.25, _f59k_target / max(_f59k_avg, 1))
                    for _wi in range(len(fcst)):
                        fcst[_wi] = snap(fcst[_wi] * _f59k_scale, mp)
                    _fire("F59k")
                    if isinstance(meta, dict):
                        meta.setdefault("drivers", []).append(
                            f"F59k EOL/wind-down: L4W orders=0 (OOS days="
                            f"{_f59_oos_days:.0f}), POS L4W={_f59k_pos_l4:.0f}/wk "
                            f"({_f59k_pos_l4/max(_f59k_pos_l13,1)*100:.0f}% of "
                            f"POS L13W={_f59k_pos_l13:.0f}/wk) -- consumer demand "
                            f"declining, not stockout. Anchored to "
                            f"MAX(POS_L4W, POS_L13W*0.50)={_f59k_target:.0f}/wk; "
                            f"scaled x{_f59k_scale:.2f} (L13W orders were "
                            f"{_f59_l13w_avg:.0f}/wk)."
                        )

        # ── F59l — Sparse/intermittent Amazon: POS floor when DC is healthy ──
        # When Croston's or Heuristic projects less than 70% of consumer POS
        # L13W rate AND the Amazon DC is in the healthy steady-state range
        # (8-20 WOS), the shortfall is caused by lumpy order history
        # understating true consumer demand -- NOT by soft demand.
        #
        # Root cause: Amazon orders in large periodic batches (once every 4-5
        # weeks for intermittent items).  Croston's inter-order interval math
        # divides the per-order qty by the interval, yielding a low projected
        # weekly rate even when consumers are buying ~1,000/wk at retail.
        # Heuristic items have the same problem: sparse order history produces
        # a conservative baseline that misses the steady consumer pull.
        #
        # When DC WOS is at Amazon's 8-12wk steady-state target, orders will
        # continue matching consumer sell-through.  POS L13W is the correct
        # forward demand signal -- not the sparse order history average.
        #
        # Correction: scale the full 26-week forecast so the average weekly
        # rate equals POS L13W.  Preserves the lumpy shape (big/quiet weeks)
        # while anchoring total demand to consumer velocity.
        #
        # Guards:
        #   POS L13W >= 200:  credible consumer signal (not noise)
        #   POS L4W >= POS L13W * 0.40:  POS not in sharp recent decline
        #   DC WOS 8-20:  healthy steady-state (F59h handles extreme cases)
        #   AI avg < POS L13W * 0.70:  meaningful gap (30%+ below consumer)
        #   Scale cap 5.0:  guard against runaway uplift on very sparse history
        #   EC items treated identically to non-EC (both are standard DC replenishment)
        if (is_amazon and pos_data and amz_catalog
                and model in ("Croston's", "Heuristic")
                and isinstance(fcst, list) and sum(fcst) > 0):
            _f59l_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
            _f59l_pos_l4  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
            _f59l_wos     = _f59h_wos
            _f59l_ai_avg  = sum(fcst) / 26.0
            if (_f59l_pos_l13 >= 200
                    and _f59l_pos_l4 >= _f59l_pos_l13 * 0.40
                    and 8.0 <= _f59l_wos <= 20.0
                    and _f59l_ai_avg < _f59l_pos_l13 * 0.70):
                _f59l_target_total = _f59l_pos_l13 * 26.0
                _f59l_scale = min(5.0, _f59l_target_total / max(sum(fcst), 1))
                if _f59l_scale > 1.01:
                    fcst = [snap(v * _f59l_scale, mp) for v in fcst]
                    _fire("F59l")
                    if isinstance(meta, dict):
                        meta.setdefault("drivers", []).append(
                            f"F59l sparse POS anchor: {model} avg {_f59l_ai_avg:.0f}/wk "
                            f"< POS L13W {_f59l_pos_l13:.0f}/wk (70% floor) with "
                            f"DC WOS {_f59l_wos:.1f}wks (healthy 8-20wk range) -- "
                            f"lumpy order history understates consumer demand. "
                            f"Scaled x{_f59l_scale:.2f} to POS L13W rate "
                            f"(POS L4W={_f59l_pos_l4:.0f}/wk). Model: {model}."
                        )

        # ── F59n — Post-DC-restock spike normalization (2026-05-21) ────────
        # When Amazon placed a large DC restock order last week (LW order >>
        # L13W avg) AND the DC was running low (WOS < 8), the order-history
        # baseline is inflated by the catch-up buy.  But that restock already
        # happened -- going forward, orders should revert to consumer POS
        # velocity, not continue at the one-time restock rate.
        #
        # This rule normalizes the forward forecast back to the POS-based
        # demand rate BEFORE F59m adds the gap-fill uplift.  F59m then
        # correctly places the remaining gap above the POS floor.
        #
        # Gates:
        #   0 < WOS < 8          -- low DC confirms restock context
        #   LW order >= 5x L13W  -- spike magnitude (catch-up buy)
        #   AUR >= MAP * 0.90    -- genuine demand (not below-MAP deal)
        #   POS_LW >= 100/wk     -- credible consumer signal
        #   AI avg > POS_LW * 1.30 -- model is meaningfully too high
        if (is_amazon and amz_catalog and pos_data
                and isinstance(fcst, list) and len(fcst) >= 26
                and model not in ("Inactive", "OTB (zero)",
                                  "Pre-launch NEW (manual passthrough)")
                and 0 < _f59h_wos < 8):
            _f59n_lw_ord  = float(hist[-1]) if hist else 0
            _f59n_l13_ord = sum(hist[-13:]) / 13.0 if len(hist) >= 13 else 0
            _f59n_pos_lw  = float(pos_data.get("Ordered_Units_LW")  or 0)
            _f59n_pos_l4  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
            _f59n_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
            _f59n_aur     = float(amz_catalog.get("AUR_L4w")   or 0)
            _f59n_map     = float(amz_catalog.get("MAP_Price")  or 0)
            _f59n_spike   = _f59n_l13_ord > 0 and _f59n_lw_ord >= _f59n_l13_ord * 5
            _f59n_genuine = (_f59n_aur > 0 and _f59n_map > 0
                             and _f59n_aur >= _f59n_map * 0.90)
            _f59n_credible = _f59n_pos_lw >= 100
            _f59n_ai_avg  = sum(fcst) / max(len(fcst), 1)
            _f59n_ai_high = _f59n_ai_avg > _f59n_pos_lw * 1.30
            if _f59n_spike and _f59n_genuine and _f59n_credible and _f59n_ai_high:
                # Clamp all weeks to max(POS_LW, L4W, L13W) -- use the highest
                # available consumer rate so we don't anchor to a reading that
                # may still be ramping.  Only reduce -- never inflate.
                _f59n_target = max(_f59n_pos_lw, _f59n_pos_l4, _f59n_pos_l13)
                _f59n_snapped = snap(_f59n_target, mp)
                _f59n_changed = False
                for _wi in range(len(fcst)):
                    if _wi in _vp_q4_zeroed_idx:
                        continue
                    if fcst[_wi] > _f59n_snapped * 1.10:
                        fcst[_wi] = _f59n_snapped
                        _f59n_changed = True
                if _f59n_changed:
                    _fire("F59n")
                    if isinstance(meta, dict):
                        meta.setdefault("drivers", []).append(
                            f"F59n post-restock normalization: LW order "
                            f"{_f59n_lw_ord:,.0f}u = "
                            f"{_f59n_lw_ord / max(_f59n_l13_ord, 1):.1f}x "
                            f"L13W avg {_f59n_l13_ord:,.0f}u -- DC restock spike "
                            f"(WOS={_f59h_wos:.1f}wks). AUR {_f59n_aur:.2f} >= "
                            f"MAP {_f59n_map:.2f} (genuine demand). "
                            f"Anchored forecast from {_f59n_ai_avg:,.0f}/wk to "
                            f"POS {_f59n_target:,.0f}/wk. "
                            f"F59m will add gap-fill uplift. Model: {model}."
                        )

        # ── F59m — Amazon low-DC restock demand uplift ──────────────────────
        # When Amazon's DC is explicitly undersupplied (DC WOS < 8) and the
        # combination of on-hand + open POs (already in transit) does not cover
        # the standard 10-week target, Amazon will place orders ABOVE consumer
        # POS velocity to rebuild inventory.  These extra restock orders are real
        # forward demand that the model must project.
        #
        # Logic:
        #   steady_rate   = max(POS_LW, POS_L4W, POS_L13W) when AUR>=MAP*0.90
        #                   and POS_LW > POS_L4W * 1.5 (demand accelerating);
        #                   otherwise max(POS_L4W, POS_L13W).
        #                   Using POS_LW as the demand rate captures a genuine
        #                   step-change in consumer velocity that has not yet
        #                   worked its way into the 4- and 13-week averages.
        #   total_supply  = (SOH + OPO) / steady_rate -- if SOH known from catalog;
        #                   else WOS + OPO/steady       -- WOS as SOH proxy
        #   net_gap_wks   = max(0, 10 - total_supply)  -- weeks still short
        #   gap_units     = net_gap_wks * steady_rate
        #   ramp_weeks    = 3 when gap > 4wks (large gap: spread over 3 weeks);
        #                   2 otherwise (standard)
        #   W1-W(ramp)    = min(steady * 2.5, steady + gap/ramp_weeks)
        #   W(ramp+1)-W26 = max(current_forecast, steady) at least consumer velocity
        #   VP-Q4-zeroed weeks are left unchanged (those POs already placed).
        #
        # Gates:
        #   0 < DC_WOS < 8  -- explicitly low (WOS=0 = unknown, skip)
        #   steady_rate >= 100/wk  -- credible consumer signal
        #   POS_L4W >= POS_L13W * 0.40  -- not in EOL decline (F59k handles that)
        #   net_gap_wks > 0.5  -- meaningful remaining gap
        #   Not DI-blended (F69-wos handles that path)
        #   Not Inactive / OTB
        if (is_amazon and pos_data
                and isinstance(fcst, list) and len(fcst) >= 26
                and model not in ("Inactive", "OTB (zero)",
                                  "Pre-launch NEW (manual passthrough)")
                and not row.get("_di_blend")
                and 0 < _f59h_wos < 8):
            _f59m_pos_l4  = float(pos_data.get("Avg_Units_Wk_L4w")  or 0)
            _f59m_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
            _f59m_pos_lw  = float(pos_data.get("Ordered_Units_LW")  or 0)
            # Use POS_LW as the demand rate when AUR >= MAP (genuine signal) and
            # LW is meaningfully above L4W (demand step-change just occurred).
            # This prevents the pipeline from appearing healthy using a stale
            # average that doesn't yet reflect the new consumer velocity.
            _f59m_aur     = float((amz_catalog or {}).get("AUR_L4w")  or 0)
            _f59m_map     = float((amz_catalog or {}).get("MAP_Price") or 0)
            _f59m_genuine = (_f59m_aur > 0 and _f59m_map > 0
                             and _f59m_aur >= _f59m_map * 0.90)
            _f59m_steady  = (
                max(_f59m_pos_lw, _f59m_pos_l4, _f59m_pos_l13)
                if (_f59m_genuine
                    and _f59m_pos_lw > _f59m_pos_l4 * 1.5
                    and _f59m_pos_lw >= 100)
                else max(_f59m_pos_l4, _f59m_pos_l13)
            )
            if (_f59m_steady >= 100
                    and _f59m_pos_l13 > 0
                    and _f59m_pos_l4 >= _f59m_pos_l13 * 0.40):
                # Compute total supply in weeks.  Use raw SOH when available
                # (most accurate); fall back to WOS figure as SOH proxy.
                if _f59h_soh > 0:
                    _f59m_total_wks = (_f59h_soh + _f59h_opo) / max(_f59m_steady, 1)
                else:
                    # WOS from Amazon may already include OPO; add OPO separately
                    # only when SOH data is absent and WOS looks SOH-only.
                    _f59m_opo_wks   = _f59h_opo / max(_f59m_steady, 1)
                    _f59m_total_wks = _f59h_wos + _f59m_opo_wks
                _f59m_gap_wks = max(0.0, 10.0 - _f59m_total_wks)
                if _f59m_gap_wks > 0.5:
                    # For a large gap (> 4 weeks) spread restock over 3 weeks
                    # instead of 2 -- this is also more robust when W1 may get
                    # zeroed by F_PO_CUTOFF (gap-fill then lands in W2+W3).
                    _f59m_ramp_wks = 3 if _f59m_gap_wks > 4.0 else 2
                    _f59m_gap_units = _f59m_gap_wks * _f59m_steady
                    _f59m_wk_uplift = _f59m_gap_units / _f59m_ramp_wks
                    _f59m_w_ramp    = min(_f59m_steady * 2.5,
                                          _f59m_steady + _f59m_wk_uplift)
                    _f59m_changed = False
                    for _wi in range(len(fcst)):
                        if _wi in _vp_q4_zeroed_idx:
                            continue   # VP-Q4 already handled this week via open PO
                        if _wi < _f59m_ramp_wks:
                            _f59m_val = snap(_f59m_w_ramp, mp)
                            if _f59m_val > fcst[_wi]:
                                fcst[_wi] = _f59m_val
                                _f59m_changed = True
                        else:
                            _f59m_floor = snap(_f59m_steady, mp)
                            if _f59m_floor > fcst[_wi]:
                                fcst[_wi] = _f59m_floor
                                _f59m_changed = True
                    if _f59m_changed:
                        _fire("F59m")
                        if isinstance(meta, dict):
                            _f59m_soh_note = (
                                f"SOH={_f59h_soh:,.0f}u, OPO={_f59h_opo:,.0f}u"
                                if _f59h_soh > 0
                                else f"DC WOS={_f59h_wos:.1f}wks, OPO={_f59h_opo:,.0f}u"
                            )
                            _f59m_ramp_note = (
                                f"W1-W{_f59m_ramp_wks}"
                                if _f59m_ramp_wks == 2 else
                                f"W1-W{_f59m_ramp_wks} (extended: gap>4wks)"
                            )
                            meta.setdefault("drivers", []).append(
                                f"F59m low-DC restock: {_f59m_soh_note} -- "
                                f"total supply {_f59m_total_wks:.1f}wks vs 10wk target; "
                                f"net gap {_f59m_gap_wks:.1f}wks = {_f59m_gap_units:,.0f}u "
                                f"spread over {_f59m_ramp_note} ({_f59m_w_ramp:.0f}/wk each); "
                                f"W{_f59m_ramp_wks+1}-W26 floored at steady rate {_f59m_steady:.0f}/wk "
                                f"(POS LW={_f59m_pos_lw:.0f}/wk, "
                                f"L4W={_f59m_pos_l4:.0f}/wk, "
                                f"L13W={_f59m_pos_l13:.0f}/wk)."
                            )

        # ── F60 — EC-transition narrative ────────────────────────────────────
        # History was inherited from parent mstyle in the pre-pass.  Log the
        # driver text now that `meta` is available.
        if _f60_is_ec_transition and isinstance(meta, dict):
            _f60_parent   = row.get("_ec_parent_mstyle", "?")
            _f60_par_l13  = row.get("_ec_parent_l13",   0)
            _f60_orig_l13 = row.get("_ec_orig_l13",     0)
            meta.setdefault("drivers", []).append(
                f"F60 EC-transition: inherited 52w order+ship history from parent "
                f"{_f60_parent} (parent L13W={_f60_par_l13:.0f}, "
                f"EC own L13W={_f60_orig_l13:.0f} — "
                f"{_f60_orig_l13 / max(_f60_par_l13, 1) * 100:.0f}% of parent); "
                f"forecast reflects parent demand signal"
            )

        # ── F69 — DI direct-import blend narrative ───────────────────────────
        # Sibling (MPP/ADF) order history was added to this base record's
        # ORD_COLS in the pre-pass.  Log the additive contribution.
        if row.get("_di_blend") and isinstance(meta, dict):
            _fire("F69")
            meta.setdefault("drivers", []).append(
                f"F69 DI blend: {row.get('_di_label','?')} direct-import history "
                f"added to base demand signal (+{row.get('_di_l13_add', 0):.0f} units L13W); "
                f"forecast reflects total product demand (warehouse + factory-direct)"
            )

        # ── F69 DI WOS-excess correction ─────────────────────────────────────
        # For DI-blended Amazon records the combined warehouse + factory-direct
        # order history reflects total replenishment demand, which tracks the
        # underlying consumer POS rate.  The model's order-history baseline can
        # understate demand when DI orders are large and infrequent (lumpy
        # cadence), so we anchor the full 26-week forecast to POS L13W and apply
        # a WOS-excess reduction for any inventory Amazon holds above its ~12wk
        # target ceiling.
        #
        # Adjustment:
        #   excess_wos = max(0, current_wos − 12)   [12wk = Amazon's target max]
        #   wos_scale  = max(0.70, 1 − excess_wos/26)
        #   target/wk  = pos_l13w × wos_scale
        #
        # The model's seasonal shape is preserved by proportional rescaling;
        # this correction covers the full 26-week horizon (superseding F59h's
        # 8-week soft taper for DI-blended records where POS is the cleaner
        # demand signal).
        if (row.get("_di_blend") and is_amazon
                and isinstance(fcst, list) and len(fcst) >= 26
                and model not in ("Inactive",)):
            _f69w_pos_l13 = float((pos_data or {}).get("Avg_Units_Wk_L13w") or 0)
            if _f69w_pos_l13 > 0:
                _fire("F69-wos")
                _f69w_wos = float((amz_catalog or {}).get("Inv_WOS") or 0)
                if _f69w_wos <= 0:
                    _f69w_soh = float((amz_catalog or {}).get("Inv_SOH") or 0)
                    _f69w_opo = float((amz_catalog or {}).get("Inv_OPO") or 0)
                    _f69w_wos = (_f69w_soh + _f69w_opo) / _f69w_pos_l13
                _f69w_excess = max(0.0, _f69w_wos - 12.0)
                _f69w_scale  = max(0.70, 1.0 - _f69w_excess / 26.0)
                _f69w_target = _f69w_pos_l13 * _f69w_scale
                # Proportional rescale — preserve the model's seasonal shape
                _f69w_cur_avg = sum(fcst) / max(len(fcst), 1)
                if _f69w_cur_avg > 0:
                    _f69w_anchor = _f69w_target / _f69w_cur_avg
                    for _wi in range(len(fcst)):
                        fcst[_wi] = snap(fcst[_wi] * _f69w_anchor, mp)
                else:
                    for _wi in range(len(fcst)):
                        fcst[_wi] = snap(_f69w_target, mp)
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F69-WOS: DI-blended forecast anchored to consumer POS "
                        f"({_f69w_pos_l13:,.0f}/wk L13W); DC WOS={_f69w_wos:.1f}wks "
                        f"(excess {_f69w_excess:.1f}wks → ×{_f69w_scale:.2f}) → "
                        f"target {_f69w_target:,.0f}/wk (warehouse + DI combined demand)"
                    )

        # ── F69-shift — DI channel declining → boost domestic projection ──────
        # When DI (MPP/ADF sibling) orders are meaningfully lower in L4W vs L13W
        # (< 70% of historical avg), Amazon is likely shifting volume back to
        # domestic warehouse sourcing.  The blended order history already captures
        # total combined demand, so the model will naturally project the decline
        # forward — but domestic orders may not yet show the compensating uptick.
        # Correct by adding the per-week shortfall to all 26 forecast weeks.
        # Only fires when F69-WOS POS anchor did NOT run (no POS L13 data) —
        # if POS data is available, consumer demand governs and the channel-shift
        # correction is redundant (2026-05-20).
        _f69s_pos_l13 = float((pos_data or {}).get("Avg_Units_Wk_L13w") or 0)
        _f69s_l13_wk  = row.get("_di_l13_add", 0) / 13.0
        _f69s_l4_wk   = row.get("_di_l4_add",  0) / 4.0
        if (not (_f69s_pos_l13 > 0)
                and _f69s_l13_wk > 0
                and _f69s_l4_wk < _f69s_l13_wk * 0.70
                and isinstance(fcst, list) and len(fcst) >= 26
                and model not in ("Inactive",)):
            _f69s_delta = _f69s_l13_wk - _f69s_l4_wk
            for _wi in range(len(fcst)):
                fcst[_wi] = snap(max(0, fcst[_wi] + _f69s_delta), mp)
            _fire("F69-shift")
            if isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F69-shift: DI channel ({row.get('_di_label','?')}) orders "
                    f"declining — L4W avg {_f69s_l4_wk:.0f}/wk vs L13W avg "
                    f"{_f69s_l13_wk:.0f}/wk. Domestic forecast increased "
                    f"+{_f69s_delta:.0f}/wk across W1-W26 to compensate for "
                    f"expected DI-to-domestic channel shift."
                )

    # F58 — Tell-AI comment replay (2026-05-08 → option B).
    # Apply the planner's most-recent "AI Adjusted" comment from QB Projection
    # Comments table as an override on top of the model's forecast.  Same
    # parser as the codepage so the replay produces the SAME 26-week diff
    # the planner saw when they clicked "Preview Adjustments".  Closes the
    # feedback loop: the AI value itself reflects the planner's intent, not
    # just the manual projection column.
    if ai_comments and isinstance(ai_comments, dict):
        _f58_key = row.get("Acct_MStyle_Key_") or ""
        _f58_text = ai_comments.get(_f58_key)
        if _f58_text:
            _f58_pre_total = sum(fcst)
            _f58_parsed, _f58_new, _f58_summary = _f58_parse_comment(_f58_text, fcst)
            if _f58_parsed and _f58_new is not None:
                fcst[:] = _f58_new
                _f58_post_total = sum(fcst)
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F58 Tell-AI replay: \"{_f58_text[:80]}\" → {_f58_summary} "
                        f"(total {_f58_pre_total:,} → {_f58_post_total:,}u)"
                    )
            elif isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F58 Tell-AI replay: \"{_f58_text[:80]}\" → not auto-applied "
                    f"({_f58_summary}); planner's MAN override still in effect"
                )

    # ── F_PO_CUTOFF / F_PO_CUTOFF_ALL — REMOVED (2026-05-24) ───────────────────
    # These rules zeroed AI W1 when no confirmed open PO existed by a day-of-week
    # cutoff.  Per planner direction: AI W1 must always be populated when there
    # are NO open orders in W1 -- the planner needs to see the recommendation so
    # they know what to order.  VP-Q4 (above) already zeros AI W1 when there IS
    # a confirmed open PO, so no double-counting occurs.
    _po_cutoff_zero_w1 = False
    _div_code = (row.get("Div") or "").upper().strip()

    # ── F70 — Switchover variant conflict (2026-05-21) ───────────────────────────
    # When a variant style (e.g. FF8654EC/COS/AMZ) at the same account has
    # manual projections > 0 or open customer POs in a given week, the retailer
    # is already planning to order the variant -- not the base.  Zero those weeks
    # in the base style AI forecast so we don't double-count demand.
    # The validation pass (validate_record) issues a CRITICAL flag on the same
    # weeks prompting the planner to mark the base style as CLOSED.
    _f70_week_map  = {}   # week_idx -> [variant_mstyle, ...] -- weeks AI was zeroed
    _f70_sw_entry  = {}   # full variant-active weeks (man_prj>0 or opn_w>0); used by narrative
    # F70 skip-list: planner-driven manual passthroughs and explicit overrides
    # must not be overridden by the switchover heuristic.  Pre-launch items
    # have no historical signal -- the planner's manual is the only forecast
    # we have; F58 Tell-AI comments are explicit instructions the planner
    # typed in the last 60 days.  Both should beat the variant-conflict rule.
    _f70_planner_protected = model.startswith("Pre-launch")
    # F58 protection: get the weeks F58 touched so F70 leaves them alone
    _f58_touched_weeks = set()
    if isinstance(meta, dict):
        for _drv in meta.get("drivers", []) or []:
            if "F58 Tell-AI replay" in str(_drv) and "not auto-applied" not in str(_drv):
                # Whole record was F58-touched; mark all weeks
                _f58_touched_weeks = set(range(26))
                break
    if switchover_weeks and not _f70_planner_protected:
        _sw_entry = switchover_weeks.get(row.get("Acct_MStyle_Key_", ""))
        if _sw_entry:
            _f70_sw_entry = _sw_entry
            for _wi, _variants in _sw_entry.items():
                if 0 <= _wi < 26 and fcst[_wi] != 0 and _wi not in _f58_touched_weeks:
                    fcst[_wi] = 0
                    _f70_week_map[_wi] = _variants
            if _f70_week_map:
                _fire("F70")
                _zeroed_wks = ", ".join(f"W{wi+1}" for wi in sorted(_f70_week_map))
                _var_names  = sorted({v for vl in _f70_week_map.values() for v in vl})
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F70 Switchover conflict: variant style(s) "
                        f"{', '.join(_var_names)} have active projections/orders in "
                        f"{_zeroed_wks} -- AI zeroed those weeks on base style. "
                        f"Planner should mark base style CLOSED for those weeks."
                    )

    # ── F70b — Reverse switchover: zero VARIANT for base-active weeks ────────────────
    # Complementary to F70 (which zeros the BASE for variant-active weeks).
    # When the base style is active in W1..W(S-1) and the variant takes over from
    # week S, the VARIANT AI forecast for W1..W(S-1) is also zeroed to prevent
    # double-counting with the base style.
    if variant_zero_weeks and not _f70_planner_protected:
        _vz_entry = variant_zero_weeks.get(row.get("Acct_MStyle_Key_", ""))
        if _vz_entry:
            _vz_week_map = {}
            for _wi, _base_mss in _vz_entry.items():
                if 0 <= _wi < 26 and fcst[_wi] != 0:
                    fcst[_wi] = 0
                    _vz_week_map[_wi] = _base_mss
            if _vz_week_map:
                _fire("F70b")
                _vz_zeroed = ", ".join(f"W{wi+1}" for wi in sorted(_vz_week_map))
                _base_names = sorted({ms for msl in _vz_week_map.values() for ms in msl})
                if isinstance(meta, dict):
                    meta.setdefault("drivers", []).append(
                        f"F70b Reverse-switchover: base style(s) {', '.join(_base_names)} "
                        f"are active in {_vz_zeroed} -- zeroed variant AI for "
                        f"pre-switchover weeks to prevent double-counting."
                    )

    # ── F_AMZ_RPL — Amazon Active Replen baseline + DC inventory correction ──────
    # Rule (per planner, 2026-05-24): for EVERY Amazon "Active Replen" item,
    # the forecast follows these rules -- no exceptions:
    #
    #   (1) Establish demand baseline = max(POS L13W consumer velocity,
    #                                       Ord L13W all-weeks avg).
    #       POS is the primary signal (what consumers buy).  Ord L13W covers
    #       genuine demand above POS (e.g. active ramp buys).  Never go below POS.
    #
    #   (2) W1 always non-zero.  AI shows W1 demand regardless of VP-Q4/PO-cutoff.
    #
    #   (3) DC inventory correction in W1+W2 using POS L4W as the correction basis
    #       (most current consumer signal; AUR-trend guarded -- see Fix 3 comment):
    #
    #       Overstocked (DC WOS > 12):
    #         W1+W2 = 0.  Let DC drain naturally to 12 WOS via sell-through.
    #
    #       Understocked (DC WOS < 10):
    #         W1+W2 = (10 - dc_wos) * corr_demand / 2 per week (simple gap fill).
    #         Brings DC from current WOS to 10 WOS target.
    #
    #       Normal (10 <= dc_wos <= 12): no adjustment.
    #
    #   (4) W3+ applies L13W ordering variability pattern (natural week-to-week
    #       fluctuation) instead of a flat baseline.  T5/event boost weeks skipped.
    #
    # This is a FINAL override -- supersedes all prior model/correction logic.
    # Protected exceptions:
    #   - F58 Tell-AI explicit planner comment (planner intent always wins)
    #   - F69 DI-blended records (handled by F69-WOS separately)
    #   - Inactive / OTB / Pre-launch models (no forward demand to anchor)
    _f_amz_rpl_f58 = (
        is_amazon
        and any(
            "F58 Tell-AI replay" in str(d)
            for d in ((meta or {}).get("drivers") or [])
            if d and "not auto-applied" not in str(d)
        )
    )
    if (is_amazon
            and "replen" in (row.get("PT_Item_Status") or "").lower()
            and pos_data
            and amz_catalog
            and not row.get("_di_blend")
            and not _f_amz_rpl_f58
            and not model.startswith("Inactive")
            and not model.startswith("OTB")
            and not model.startswith("Pre-launch")
            and isinstance(fcst, list) and len(fcst) >= 26):

        _rpl_pos_l13 = float(pos_data.get("Avg_Units_Wk_L13w") or 0)
        # Fix A (2026-05-24): use normalized hist (post F41/F35/F43) so phantom
        # stock-up orders removed by F41 are not counted in the demand baseline.
        _rpl_ord_l13 = sum(float(v) for v in hist[-13:]) / 13  # all-weeks avg (normalized)
        _rpl_demand  = max(_rpl_pos_l13, _rpl_ord_l13)

        if _rpl_demand >= 50:
            # Step 1 -- build week-level rates: apply seasonal/event lifts on top
            # of the demand baseline.  Three lift layers (applied in order):
            #   (a) Category profile (empirical monthly index, F66-style floor 1.0).
            #   (b) Prime Day / Fall Prime Day calendar boosts (discrete events).
            #   (c) T5/Holiday + Season-specific ramp (AMZ_T5_HOLIDAY_BOOSTS).
            # Layers (a) and (c) use MAX to avoid double-counting when the empirical
            # category profile already captures some T5 lift.  Layer (b) multiplies
            # because Prime Day is a discrete discrete uplift on top of any baseline.
            _rpl_base = snap(_rpl_demand, mp)
            _rpl_cat_mults = _category_week_multipliers(
                description, product_category, product_subcategory, brand, brand_pt,
                season=season
            ) if (description or product_category or product_subcategory or brand or brand_pt or season) else None
            _rpl_pb, _rpl_fb = _get_event_boosts()
            _rpl_t5 = _get_t5_seasonal_boosts(season)   # Season-tag-aware T5/Halloween ramp
            _rpl_t5_applied = []
            _rpl_rates = []
            for _wi in range(26):
                _mult = 1.0
                # (a) category profile -- lifts only (floor at 1.0)
                if _rpl_cat_mults:
                    _mult = max(1.0, _rpl_cat_mults[_wi])
                wnum = _wi + 1
                # (b) Prime Day / Fall Prime Day -- multiplicative (discrete event)
                _ev = max(_rpl_pb.get(wnum, 1.0), _rpl_fb.get(wnum, 1.0))
                if _ev > 1.0:
                    _mult *= _ev
                # (c) T5/Holiday seasonal ramp -- MAX with existing mult (no stack)
                _t5 = _rpl_t5.get(wnum, 1.0)
                if _t5 > _mult:
                    _mult = _t5
                    _rpl_t5_applied.append(wnum)
                _rpl_rates.append(snap(_rpl_demand * _mult, mp))

            # Fix 1 (2026-05-24): W1 always non-zero for Amazon Active Replen.
            # VP-Q4 and F_PO_CUTOFF may zero W1, but the AI must always show a W1
            # recommendation -- the planner needs to see it even when a PO was already
            # submitted this week.  Downstream VP-Q4/F70 zeros on W2+ still apply.
            _rpl_new = (
                [_rpl_rates[0]] +
                [0 if fcst[_wi] == 0 else _rpl_rates[_wi] for _wi in range(1, 26)]
            )

            # Fix 2 (2026-05-24): L13W ordering-variability setup.
            # Amazon's actual weekly order quantities fluctuate naturally -- a flat
            # line misrepresents real ordering behavior.  Compute the L13W order
            # amounts as ratios relative to their mean (cap 2.5x, floor 0.5x) and
            # cycle that shape through steady-state weeks beyond the DC window.
            # Only activated for regular orderers (>= 8 of last 13 weeks non-zero).
            # Fix A (2026-05-24): use normalized hist so phantom orders zeroed by
            # F41 don't distort variability ratios (e.g., single stock-up at 2.5x cap).
            _rpl_l13w_raw  = [float(v) for v in hist[-13:]]
            _rpl_l13w_nz   = sum(1 for v in _rpl_l13w_raw if v > 0)
            _rpl_l13w_mean = sum(_rpl_l13w_raw) / 13   # all-weeks avg incl. zeros
            if _rpl_l13w_mean >= 50 and _rpl_l13w_nz >= 8:
                _rpl_var_ratios = [
                    min(2.5, max(0.5, v / _rpl_l13w_mean)) if v > 0 else 0.5
                    for v in _rpl_l13w_raw
                ]
            else:
                _rpl_var_ratios = None   # sparse history: keep flat baseline

            # Step 2 -- DC inventory correction
            # Fix 3 (2026-05-24): Use POS L4W as the correction demand basis
            # (most current consumer signal).  AUR trend guards: if L4W AUR differs
            # significantly from L13W AUR, fall back to L13W POS to avoid using a
            # price-distorted velocity as the fill basis.
            # Formula: simple gap fill to target WOS (no sell-through offset):
            #   Understocked (WOS < 10): fill = (10 - wos) * corr_demand
            #   Overstocked  (WOS > 12): fill = 0 per week (drain naturally)
            #   Normal      (10-12 WOS): no adjustment
            _rpl_pos_l4w  = float(pos_data.get("Avg_Units_Wk_L4w") or 0)
            _rpl_aur_l4w  = float(amz_catalog.get("AUR_L4w")  or 0)
            _rpl_aur_l13w = float(amz_catalog.get("AUR_L13w") or 0)
            _rpl_aur_note = ""
            if _rpl_aur_l4w > 0 and _rpl_aur_l13w > 0:
                _rpl_aur_ratio = _rpl_aur_l4w / _rpl_aur_l13w
                if _rpl_aur_ratio > 1.10:
                    # AUR rising > 10%: price increase likely compressing volume.
                    # L4W POS may understate true demand -- use L13W as stable basis.
                    _rpl_corr_demand = _rpl_pos_l13 if _rpl_pos_l13 >= 50 else (_rpl_pos_l4w or _rpl_pos_l13)
                    _rpl_aur_note = (
                        f" AUR: ${_rpl_aur_l4w:.2f} L4W vs ${_rpl_aur_l13w:.2f} L13W"
                        f" (ratio {_rpl_aur_ratio:.2f}x -- price rising, used L13W POS)."
                    )
                elif _rpl_aur_ratio < 0.90:
                    # AUR falling > 10%: possible deal/promo inflating L4W POS.
                    # Use L13W as deal-adjusted baseline.
                    _rpl_corr_demand = _rpl_pos_l13 if _rpl_pos_l13 >= 50 else (_rpl_pos_l4w or _rpl_pos_l13)
                    _rpl_aur_note = (
                        f" AUR: ${_rpl_aur_l4w:.2f} L4W vs ${_rpl_aur_l13w:.2f} L13W"
                        f" (ratio {_rpl_aur_ratio:.2f}x -- price dropping/promo, used L13W POS)."
                    )
                else:
                    # Stable AUR: POS L4W is the most current signal
                    _rpl_corr_demand = _rpl_pos_l4w if _rpl_pos_l4w >= 50 else _rpl_pos_l13
                    _rpl_aur_note = (
                        f" AUR: ${_rpl_aur_l4w:.2f} L4W vs ${_rpl_aur_l13w:.2f} L13W (stable)."
                    )
            else:
                # No AUR data: use L4W POS if sufficient, else L13W
                _rpl_corr_demand = _rpl_pos_l4w if _rpl_pos_l4w >= 50 else _rpl_pos_l13

            _rpl_wos = float(amz_catalog.get("Inv_WOS") or 0)
            _rpl_soh = float(amz_catalog.get("Inv_SOH") or 0)
            _rpl_opo = float(amz_catalog.get("Inv_OPO") or 0)
            if _rpl_wos <= 0 and _rpl_pos_l13 > 0:
                # Fallback: compute from SOH + OPO when Inv_WOS is absent
                _rpl_wos = (_rpl_soh + _rpl_opo) / _rpl_pos_l13
            # Fix B (2026-05-24): DC depletion inference.
            # When WOS=0, SOH=0, OPO=0, the ASIN is absent from
            # Amazon_Invtry_Health -- strong signal DC is fully depleted.
            # Amazon throttles replenishment orders after long OOS periods
            # because it doesn't trust supplier availability. In this state
            # we treat WOS=0.0 (fully depleted) so the W1+W2 catch-up fires.
            _rpl_dc_depleted = (
                _rpl_wos == 0.0
                and _rpl_soh == 0.0
                and _rpl_opo == 0.0
            )

            # Step 2a -- Pipeline adjustment: if last week's actual orders exceed
            # the baseline rate, the excess is likely a DC fill order still in
            # transit (not yet visible in DC open POs / SOH).  Add it back as
            # equivalent WOS so we don't double-refill inventory that's already
            # on its way.
            _rpl_ord_lw_actual = float(row.get("Ord_LW") or 0)
            _rpl_pipeline      = max(0.0, _rpl_ord_lw_actual - _rpl_base)
            _rpl_pipeline_wos  = (_rpl_pipeline / _rpl_demand) if _rpl_demand > 0 else 0.0
            if _rpl_pipeline_wos > 0 and _rpl_wos > 0:
                _rpl_wos += _rpl_pipeline_wos

            # Fix 1 ensures W1 is always set, so correction window is always W1+W2.
            _rpl_dc_end = 2   # steady-state starts at W3 (index 2)
            if _rpl_wos > 0 or _rpl_dc_depleted:
                _rpl_adj1, _rpl_adj2 = 0, 1

                if not _rpl_dc_depleted and _rpl_wos > 12:
                    # Overstocked: W1+W2 already hold the steady-state demand
                    # from Fix 1 (_rpl_rates[0/1]).  Do NOT zero them -- planner
                    # must always see the ongoing demand signal and decide whether
                    # to actually place an order.  No extra fill is added.
                    _rpl_inv_note = (
                        f"DC WOS={_rpl_wos:.1f} > 12 (overstocked) -- "
                        f"steady-state demand shown in W1+W2; no fill order added"
                    )
                elif _rpl_dc_depleted or _rpl_wos < 10:
                    # Understocked or DC fully depleted (WOS=0, SOH=0, OPO=0):
                    # simple gap fill -- order exactly enough to bridge current
                    # WOS to 10 WOS target.  _rpl_wos=0.0 when depleted, so
                    # fill = 10 * corr_demand split over W1+W2.
                    _rpl_window = max(0.0, (10.0 - _rpl_wos) * _rpl_corr_demand)
                    _rpl_each   = snap(_rpl_window / 2.0, mp)
                    # Guard: W1 must never fall below the steady-state demand rate.
                    # When corr_demand is near-zero (POS feed thin/absent) but
                    # order history drives _rpl_demand >= 50, the fill resolves
                    # to 0 and would silently erase Fix 1's non-zero W1.  Floor
                    # both windows at the rate-based value so the planner always
                    # sees the demand signal regardless of the fill calc outcome.
                    _rpl_w1_floor = _rpl_rates[_rpl_adj1]
                    _rpl_w2_floor = _rpl_rates[_rpl_adj2]
                    _rpl_new[_rpl_adj1] = max(_rpl_each, _rpl_w1_floor)
                    _rpl_new[_rpl_adj2] = max(_rpl_each, _rpl_w2_floor)
                    if _rpl_dc_depleted:
                        _rpl_inv_note = (
                            f"DC WOS=0.0 (depleted/inferred; SOH=0 OPO=0 -- "
                            f"no Inventory Health record) -- "
                            f"W{_rpl_adj1+1}+W{_rpl_adj2+1} set to "
                            f"{_rpl_each:.0f}/ea (gap fill to 10 WOS; "
                            f"corr basis={_rpl_corr_demand:.0f}/wk)"
                        )
                    else:
                        _rpl_inv_note = (
                            f"DC WOS={_rpl_wos:.1f} < 10 (understocked) -- "
                            f"W{_rpl_adj1+1}+W{_rpl_adj2+1} set to "
                            f"{_rpl_each:.0f}/ea (gap fill to 10 WOS; "
                            f"corr basis={_rpl_corr_demand:.0f}/wk)"
                        )
                else:
                    _rpl_inv_note = (
                        f"DC WOS={_rpl_wos:.1f} in target range (10-12) -- "
                        f"no inventory adjustment"
                    )
            else:
                _rpl_inv_note = "DC WOS unknown -- no adjustment applied"

            # Fix 2 (2026-05-24): Apply L13W variability pattern to W3+ so the
            # projection reflects Amazon's natural order fluctuation rather than a
            # flat line.  T5/Holiday and Prime Day boost weeks are skipped (those
            # seasonal lifts are already calibrated).  VP-Q4/F70 zeros preserved.
            #
            # F60-ATS safety (2026-05-24): EC variants that inherited parent order
            # history via F60 also inherit parent ATS data (see F60-ATS block in
            # main()).  But if the PARENT itself has no ATS record (rare -- old
            # mstyle or table not yet loaded), the variability pattern would replay
            # contaminated post-OOS catch-up ratios as spurious spikes in July or
            # other months.  Guard: disable variability pattern for EC-transitioned
            # records when ATS data is absent or all-zero (normalizers couldn't
            # run → history may still contain uncapped catch-up orders).
            if _rpl_var_ratios and row.get("_ec_transition") and not any(ats_hist or []):
                _rpl_var_ratios = None   # safer flat baseline than cycling bad ratios
            if _rpl_var_ratios:
                for _wi in range(_rpl_dc_end, 26):
                    if _rpl_new[_wi] != 0:
                        wnum = _wi + 1
                        _has_event = (
                            _rpl_t5.get(wnum, 1.0) > 1.0 or
                            _rpl_pb.get(wnum, 1.0) > 1.0 or
                            _rpl_fb.get(wnum, 1.0) > 1.0
                        )
                        if not _has_event:
                            _pat_i = (_wi - _rpl_dc_end) % 13
                            _rpl_new[_wi] = snap(
                                _rpl_rates[_wi] * _rpl_var_ratios[_pat_i], mp
                            )

            fcst[:] = _rpl_new
            _fire("F_AMZ_RPL")
            _rpl_t5_note = (
                f" T5/seasonal boost applied on W{',W'.join(str(w) for w in sorted(_rpl_t5_applied))}"
                f" (Season={season or 'standard'});"
                if _rpl_t5_applied else ""
            )
            _rpl_var_note = (
                " L13W variability pattern applied (W3+)."
                if _rpl_var_ratios else
                " L13W variability not applied (sparse history -- flat baseline)."
            )
            if isinstance(meta, dict):
                meta.setdefault("drivers", []).append(
                    f"F_AMZ_RPL Active Replen override: "
                    f"demand={_rpl_demand:.0f}/wk "
                    f"(POS L13W={_rpl_pos_l13:.0f}/wk, "
                    f"POS L4W={_rpl_pos_l4w:.0f}/wk, "
                    f"Ord L13W all-wks={_rpl_ord_l13:.0f}/wk); "
                    f"corr basis={_rpl_corr_demand:.0f}/wk; "
                    f"{_rpl_inv_note}.{_rpl_aur_note}{_rpl_t5_note}"
                    f"{_rpl_var_note} "
                    f"Supersedes prior model ({model})."
                )

    prior = sum(manual_wks)
    new   = sum(fcst)
    pct   = abs(new - prior) / prior if prior > 0 else 0

    # G2 (2026-05-21) -- All-zeroed-by-guards safety demotion.
    # When the active branch produced a non-zero forecast but downstream guards
    # (VP-Q4 + VP-OP + F70 + F_PO_CUTOFF + F36 + F38f + ...) zeroed all 26 weeks,
    # the model label is misleading.  Demote to a clear "Inactive (zeroed by
    # guards)" label so alert generation, narrative, and viewer display all
    # surface that the active model was effectively suppressed.  Skip for
    # records that are already in Inactive/OTB/Pre-launch families.
    if (new == 0 and model not in ("Inactive",)
            and not model.startswith("Inactive")
            and not model.startswith("OTB")
            and not model.startswith("Pre-launch")
            and not model.startswith("New/Relaunch")
            and not model.startswith("Reactivating")):
        _orig_model = model
        model = "Inactive (zeroed by guards)"
        if isinstance(meta, dict):
            meta.setdefault("drivers", []).append(
                f"G2 All-zero demotion: model was {_orig_model} but all 26 weeks "
                f"were zeroed by downstream guards (VP-Q4 PO / VP-OP buffer / "
                f"F70 switchover / F_PO_CUTOFF / F36 burnoff / F38f offline). "
                f"Surfaced as Inactive so the narrative reflects reality."
            )

    alert = ""
    if model == "Inactive" and prior > 0:
        alert = _build_alert(model, new, prior, pct, cap, mp, meta,
                             fcst=fcst, manual=manual_wks, row=row, history=hist)
    elif prior > 0 and pct > ALERT_THRESHOLD:
        alert = _build_alert(model, new, prior, pct, cap, mp, meta,
                             fcst=fcst, manual=manual_wks, row=row, history=hist)

    # Confidence score (0-100) ------------------------------------------------
    _confidence = compute_forecast_confidence(
        model        = model,
        meta         = meta,
        hist         = hist,
        manual       = manual_wks,
        pct_diff     = round(pct * 100, 1),
        is_new_launch= _f34_is_new_launch,
        is_otb       = (model == "OTB (zero)"),
        season       = season,
    )

    return {
        "key":         row["Acct_MStyle_Key_"],
        "mstyle":      row.get("Mstyle", ""),
        "cust":        clean_html(row.get("Customr_Name", "")),
        "mp":          int(mp),
        "model":       model,
        "biweekly":    biweekly,
        "iso":         iso["is_iso"],
        "iso_settle":  iso.get("in_settle", False),
        "forecast":    fcst,
        "manual":      [int(v) for v in manual_wks],
        "cap_base":    cap,
        "new_total":   new,
        "prior_total": int(prior),
        "pct_diff":    round(pct * 100, 1),
        "confidence":  _confidence,
        # F56 — surface VP-Q4 PO-zeroed context so narrative can show
        # "Total forward demand = AI + confirmed POs" alongside visible AI.
        "po_zeroed_weeks":   (meta.get("po_zeroed_weeks", []) if isinstance(meta, dict) else []),
        "po_total_qty":      (meta.get("po_total_qty", 0)    if isinstance(meta, dict) else 0),
        "po_total_removed":  (meta.get("po_total_removed",0) if isinstance(meta, dict) else 0),
        "alert":       alert,
        "baseline_mode": (meta.get("baseline_mode", "") if isinstance(meta, dict) else ""),
        # Per-rule fire tags for deck-builder harvest (added 2026-05-06).
        "rule_fires":  _scan_rule_fires(
            meta=meta if isinstance(meta, dict) else None,
            alert=alert,
            baseline_mode=(meta.get("baseline_mode", "") if isinstance(meta, dict) else ""),
            model=model,
            biweekly=biweekly,
            is_amazon=is_amazon,
            is_international=is_international,
        ),
        # L26W actual history (oldest→newest) — surfaced in viewer detail pane.
        "history_l26_shp":  [int(float(row.get(c) or 0)) for c in SHP_COLS[-26:]],
        "history_l26_ord":  [int(float(row.get(c) or 0)) for c in ORD_COLS[-26:]],
        # LY actuals — weeks 27-52 ago, aligned to W1..W26 of the forecast.
        # ORD_COLS / SHP_COLS are oldest→newest, so the OLDEST 26 entries
        # ([:26]) correspond to LW_51..LW_26, which are LY-W1..LY-W26
        # (the calendar week ~52 weeks before each forecast week).  Surfaced
        # in the viewer detail pane as Ordered LY (green) and Shipped LY (blue).
        "history_ly_shp":   [int(float(row.get(c) or 0)) for c in SHP_COLS[:26]],
        "history_ly_ord":   [int(float(row.get(c) or 0)) for c in ORD_COLS[:26]],
        # Viewer display fields — pulled fresh from QB every run so the viewer
        # never shows stale data without a round-trip enrichment query.
        "flagged_qb":    bool(row.get("Flagged") in (True, 1, "true", "1", "yes")),
        "auto_project":  bool(row.get("Auto_Project") in (True, 1, "true", "1", "yes")),
        "pog_launch":    (str(row.get("POG_Launch_Date") or ""))[:10],
        "pog_end":       (str(row.get("POG_End_Date") or ""))[:10],
        "store_count":   int(float(row.get("Store_Count") or 0)),
        "opn_w":         [int(float(row.get(c) or 0)) for c in OPN_COLS],
        "status_cust":   (str(row.get("Status_Cust") or "")).strip(),
        "item_status":   (str(row.get("PT_Item_Status") or "")).strip(),
        # F_PO_CUTOFF: True when W1 was zeroed because no PO received by cutoff day.
        # Writeback uses this to also zero MAN PRJ W1 in QB.
        "zero_man_w1_cutoff": _po_cutoff_zero_w1,
        # Division code (FF/BB/etc.) -- used by writeback W1 MAN PRJ gate.
        "div": (row.get("Div") or "").upper().strip(),
        # F70 -- Switchover conflict maps.  build_ai_analysis() uses these to
        # prepend the switchover narrative bullet.  Empty dicts when no conflict.
        # `f70_switchover`        = full variant-active weeks (man_prj>0 or opn_w>0)
        # `f70_zeroed_weeks`      = weeks where AI was actually zeroed
        # `f70_planner_protected` = True when F70 was skipped (Pre-launch model)
        "f70_switchover":        dict(_f70_sw_entry),
        "f70_zeroed_weeks":      dict(_f70_week_map),
        "f70_planner_protected": _f70_planner_protected,
    }


# ─── Projection validation ────────────────────────────────────────────────────

def validate_record(row, master_pack, high_mult=VALID_HIGH_MULT,
                    low_mult=VALID_LOW_MULT, spike_mult=VALID_SPIKE_MULT,
                    oos_entry=None, open_po_wk=None, ats_hist=None,
                    switchover_weeks=None):
    """
    Compare manual projections against historical order patterns.
    Flags weeks where the projection looks anomalous relative to what
    the order history says is normal for this item + customer.
    """
    # Shared prep with forecast_record() — see _prep_record_signals().
    # Includes F35 stockout-backlog normalization so validation flags are
    # computed against true demand intent, not pile-up artifacts.
    _sig                = _prep_record_signals(row, master_pack, oos_entry=oos_entry,
                                               ats_hist_l26=ats_hist)
    mp                  = _sig["mp"]
    hist                = _sig["hist"]
    is_amazon           = _sig["is_amazon"]
    _f35_corrections_v  = _sig["f35_corrections"]

    # Detect ISO first; strip the stocking spike from history used for baselines.
    iso  = detect_iso(hist)
    hist_for_model = list(hist)
    if iso["is_iso"]:
        hist_for_model[iso["iso_week_idx"]] = 0.0

    pattern    = classify(hist_for_model)
    nz_rate_   = nz_rate(hist_for_model, window=26)
    is_dense   = nz_rate_ >= DENSE_THRESHOLD    # ≥ 35%: semi-regular ordering (Seasonal Baseline)
    is_croston = nz_rate_ >= CROSTON_THRESHOLD  # ≥ 25%: intermittent (Croston's)
    season     = seasonal_profile(hist_for_model)
    biweekly   = bool(detect_biweekly(hist_for_model)) if is_dense else False  # VP-Q3: monthly+ only

    # Determine active parity for bi-weekly items
    bw_active_parity = None
    if biweekly:
        h26 = hist[-26:]
        even_sum = sum(h26[i] for i in range(0, len(h26), 2))
        odd_sum  = sum(h26[i] for i in range(1, len(h26), 2))
        bw_active_parity = 0 if even_sum >= odd_sum else 1

    # Compute baseline from ISO-stripped L13W. Zeros are real (customer didn't
    # order that week). ISO spike excluded so it doesn't inflate the baseline.
    l13 = hist_for_model[-13:]
    l13_sum = sum(l13)

    if l13_sum > 0:
        baseline     = float(l13_sum / 13)
        baseline_src = "L13W avg"
    else:
        baseline     = 0.0
        baseline_src = "no L13W orders"

    # Read manual projections
    manual = [float(row.get(c) or 0) for c in ORIG_PRJ_COLS]

    # F70 -- resolve switchover conflict weeks for this base style (if any)
    _f70_sw_entry = {}
    if switchover_weeks:
        _f70_sw_entry = switchover_weeks.get(row.get("Acct_MStyle_Key_", ""), {})

    weeks_out = []
    flags_total   = 0
    max_severity  = "OK"
    severity_rank = {"CRITICAL": 0, "WARNING": 1, "OK": 2}

    for w in range(26):
        proj = manual[w]
        sf   = season[w]
        wnum = w + 1   # 1-indexed week number

        # Event lift for this week -- Prime Day AND Fall Prime Day are Amazon-only.
        if is_amazon:
            _vp, _vf = _get_event_boosts()
            ev_lift = max(_vp.get(wnum, 1.0), _vf.get(wnum, 1.0))
        else:
            ev_lift = 1.0

        expected_center = baseline * sf * ev_lift
        expected_low    = expected_center * low_mult
        expected_high   = expected_center * high_mult

        flag     = None
        severity = None
        reason   = None

        # Event context for messages -- both events are Amazon-only
        if is_amazon:
            _mn, _mf = _get_event_boosts()
            ev_note = ""
            if wnum in _mn:
                ev_note = " This is a Prime Day pre-order week (Amazon only)."
            elif wnum in _mf:
                ev_note = " This is a Fall Prime Day pre-order week (Amazon only)."
        else:
            ev_note = ""

        # ISO settle-period: retailer just took the item; low/zero projections
        # are expected while product ships to stores and sales develop.
        # Suppress undershoot and sudden_stop flags -- they are false positives.
        iso_settling = iso["is_iso"] and iso.get("in_settle", False)

        # F70 -- Switchover conflict check (highest priority; overrides other flags).
        # The retailer orders either the base style or the variant -- not both.
        # If the variant has demand in this week, any projection on the base is
        # double-counting.  Alert the planner to mark the base as CLOSED.
        if w in _f70_sw_entry:
            _sw_variants = _f70_sw_entry[w]
            flag     = "switchover_conflict"
            severity = "CRITICAL"
            _var_str = ", ".join(sorted(set(_sw_variants)))
            reason   = (
                f"Switchover conflict: variant style(s) {_var_str} already "
                f"have projections or open orders in W{wnum} -- the customer "
                f"will order one or the other, not both. Remove the projection "
                f"from this base style for W{wnum} and consider marking this "
                f"record CLOSED."
            )

        elif pattern == "inactive" and proj > 0:
            flag     = "inactive_with_demand"
            severity = "CRITICAL"
            reason   = (f"This account hasn't ordered in 13 weeks — they've "
                        f"gone dark on this item. You have {int(proj):,} units "
                        f"projected here. Is this a planned relaunch? "
                        f"If not, clear it out.{ev_note}")

        elif baseline > 0 and proj > expected_center * spike_mult:
            ratio    = proj / baseline
            flag     = "massive_spike"
            severity = "CRITICAL"
            reason   = (f"This account has been buying around {int(baseline):,}/week. "
                        f"You're projecting {int(proj):,} here — that's {ratio:.1f}x "
                        f"their normal pace. What's driving this? "
                        f"If it's a promotion or new store opening, document it. "
                        f"Otherwise this is a significant overstock risk.{ev_note}")

        elif baseline > 0 and proj > expected_high and proj > 0:
            pct_over = ((proj - baseline) / baseline) * 100
            flag     = "overshoot"
            severity = "WARNING"
            reason   = (f"Account is buying around {int(baseline):,}/week right now. "
                        f"Your {int(proj):,} is {pct_over:.0f}% above that pace. "
                        f"Is there a promotion, seasonal build, or distribution "
                        f"gain backing this up?{ev_note}")

        elif baseline > 0 and 0 < proj < expected_low and not iso_settling:
            pct_under = ((baseline - proj) / baseline) * 100
            flag      = "undershoot"
            severity  = "WARNING"
            reason    = (f"Account is buying around {int(baseline):,}/week but "
                         f"you only have {int(proj):,} here — {pct_under:.0f}% "
                         f"below their current pace. Are you planning for a "
                         f"distribution loss or a deliberate cut?{ev_note}")

        elif proj == 0 and baseline > 0 and pattern != "inactive":
            is_bw_off = (biweekly and bw_active_parity is not None
                         and w % 2 != bw_active_parity)
            # Sparse buyers (< 2/4 weeks) have lots of legitimate zero weeks —
            # a zero projection is normal and expected for them.
            # ISO items in settle period: low/zero demand is expected while
            # the product ships to store shelves — not a sudden stop.
            if not is_bw_off and is_dense and not iso_settling:
                flag     = "sudden_stop"
                severity = "WARNING"
                reason   = (f"Account is actively buying ~{int(baseline):,}/week "
                            f"but this week is blank. Was this intentional or "
                            f"did it get missed?")

        elif biweekly and bw_active_parity is not None and proj > 0:
            if w % 2 != bw_active_parity:
                flag     = "biweekly_off_week"
                severity = "WARNING"
                reason   = (f"This account orders every other week — W{wnum} "
                            f"is their off-week. The {int(proj):,} you have here "
                            f"should shift to the adjacent week or it won't get "
                            f"ordered.")

        # (Master pack divisibility not flagged — too granular to be actionable)

        if flag:
            flags_total += 1
            if severity_rank.get(severity, 2) < severity_rank.get(max_severity, 2):
                max_severity = severity

        weeks_out.append({
            "week":            wnum,
            "col":             ORIG_PRJ_COLS[w],
            "projection":      int(proj),
            "expected_center": round(expected_center, 0),
            "expected_low":    round(expected_low, 0),
            "expected_high":   round(expected_high, 0),
            "seasonal":        round(sf, 3),
            "event_lift":      ev_lift,
            "flag":            flag,
            "severity":        severity,
            "reason":          reason,
        })

    proj_total = sum(manual)
    proj_per_wk = round(proj_total / 26, 1)
    # Ord/Wk L13W: average weekly orders over last 13 weeks (including zeros)
    ord_per_wk_l13 = round(sum(l13) / 13, 1)
    # Shpd/Wk L13W from QB field
    shpd_per_wk_l13 = round(float(row.get("Shpd_Wk_L13W_cust_") or 0), 1)

    exp_total  = sum(baseline * season[w] for w in range(26))
    pct_diff   = ((proj_total - exp_total) / exp_total * 100) if exp_total > 0 else 0

    # Volume-based priority: how critical is it to fix this record?
    # On-Plan override (AI vs Man <= 7.5%, plan entered) is applied at the
    # call site after the AI forecast is available -- not computable here.
    if baseline >= 1000:
        priority = "CRITICAL"
    elif baseline >= 500:
        priority = "HIGH"
    elif baseline >= 200:
        priority = "MID"
    else:
        priority = "LOW"

    # Map to QB Validation Pattern dropdown values -- mirrors forecast_record() 3-tier routing.
    # M4 (2026-05-21): When F70 switchover conflicts cover >= 50% of weeks,
    # surface "switchover_closed" so planners see a single record-level
    # explanation (matches the per-week CRITICAL switchover_conflict flags).
    _f70_wk_count = sum(1 for w in weeks_out if w.get("flag") == "switchover_conflict")
    if _f70_wk_count >= 13:
        qb_pattern = "switchover_closed"
    elif pattern == "inactive":
        qb_pattern = "inactive"
    elif iso["is_iso"]:
        qb_pattern = "new_item"            # ISO = first time this retailer carries the item
    elif not is_croston:
        qb_pattern = "sparse_intermittent" # < 25% non-zero: truly lumpy (every 6-12 wks)
    elif not is_dense:
        qb_pattern = "intermittent"        # 25-50% non-zero: Croston's (every 2-5 wks)
    else:
        qb_pattern = "steady"              # >= 50% non-zero: Seasonal Baseline

    return {
        "key":              row["Acct_MStyle_Key_"],
        "mstyle":           row.get("Mstyle", ""),
        "desc":             clean_html(row.get("Description", "")),
        "cust":             clean_html(row.get("Customr_Name", "")),
        # Pull plain name from the User-type field [Inventory_Manager] (fid 936)
        # via _coerce_user_name(). Previously we preferred the formula field
        # "Inv Mgr (name)" (fid 1586, UserToName([Inventory Manager])) because it
        # returns a pre-cleaned string, but CData rejects that column in SELECT
        # ("Invalid column name 'Inv Mgr (name)'") because of the parens — so
        # we go straight to the underlying User-type field.
        "inv_manager":      _coerce_user_name(row.get("Inventory_Manager")),
        "mp":               int(mp),
        "pattern":          qb_pattern,
        "biweekly":         biweekly,
        "iso":              iso["is_iso"],
        "iso_settle":       iso.get("in_settle", False),
        "iso_qty":          iso.get("iso_qty", 0) if iso["is_iso"] else 0,
        "iso_weeks_ago":    iso.get("weeks_since_iso", 0) if iso["is_iso"] else 0,
        "baseline":         round(baseline, 1),
        "baseline_src":     baseline_src,
        "proj_per_wk":      proj_per_wk,
        "ord_per_wk_l13":   ord_per_wk_l13,
        "shpd_per_wk_l13":  shpd_per_wk_l13,
        "max_severity":     max_severity,
        "priority":         priority,
        "n_flags":          flags_total,
        "projection_total": int(proj_total),
        "expected_total":   round(exp_total, 0),
        "pct_diff":         round(pct_diff, 1),
        "weeks":            weeks_out,
        # L26W actual shipments (most recent 26 of 52, oldest→newest) — viewer display
        "history_l26_shp":  [int(float(row.get(c) or 0)) for c in SHP_COLS[-26:]],
        # L26W orders (oldest→newest) — viewer display alongside shipments (last 26 of ORD_COLS)
        "history_l26_ord":  [int(float(row.get(c) or 0)) for c in ORD_COLS[-26:]],
        # LY actuals — weeks 27-52 ago, aligned to W1..W26 of the forecast.
        # ORD_COLS / SHP_COLS are oldest→newest, so the OLDEST 26 entries
        # ([:26]) correspond to LW_51..LW_26, which are LY-W1..LY-W26 (i.e.,
        # the calendar week ~52 weeks before each forecast week).  Surfaced
        # in the viewer detail pane below the Suggested row.
        "history_ly_shp":   [int(float(row.get(c) or 0)) for c in SHP_COLS[:26]],
        "history_ly_ord":   [int(float(row.get(c) or 0)) for c in ORD_COLS[:26]],
        # AI-suggested (Suggested_Projection_Wk* columns) — preloaded so viewer needs no CData call
        "suggested":        [int(float(row.get(c) or 0)) for c in SUGG_COLS],
        # F35 audit trail — list of stockout corrections applied to history
        # before validation flags were computed.  Each entry records the
        # zero-run start index, length, pre-gap baseline, and units stripped.
        "stockout_corrections": _f35_corrections_v if _f35_corrections_v else [],
        # Viewer display fields — pulled fresh from QB every run.
        "flagged_qb":    bool(row.get("Flagged") in (True, 1, "true", "1", "yes")),
        "auto_project":  bool(row.get("Auto_Project") in (True, 1, "true", "1", "yes")),
        "pog_launch":    (str(row.get("POG_Launch_Date") or ""))[:10],
        "pog_end":       (str(row.get("POG_End_Date") or ""))[:10],
        "store_count":   int(float(row.get("Store_Count") or 0)),
        "opn_w":         [int(float(row.get(c) or 0)) for c in OPN_COLS],
        # Status fields — needed by narrative to detect unexplained planner truncations
        "status_cust":   (str(row.get("Status_Cust") or "")).strip(),
        "item_status":   (str(row.get("PT_Item_Status") or "")).strip(),
    }


def _build_record_narrative(r):
    """
    Business-focused narrative written in the voice of a seasoned retail
    inventory planner. Calls out specific problems with the manual projection,
    explains why they matter in retail terms, and flags what needs attention.
    No algorithmic jargon — talk like you're reviewing the plan with a buyer.
    """
    manual  = [w["projection"] for w in r["weeks"]]
    ai      = r["ai_forecast"]
    bl      = r["baseline"]
    mp      = r["mp"]
    proj_t  = r["projection_total"]
    ai_t    = r["ai_total"]
    pattern = r["pattern"]
    run_rate = r.get("ord_per_wk_l13", 0)   # actual weekly order pace last 13w

    # ── Inactive account ──────────────────────────────────────────────────────
    if pattern == "inactive":
        if proj_t > 0:
            return (f"This account hasn't placed a single order on this item in "
                    f"13 weeks. If this isn't a confirmed relaunch, the projection "
                    f"should be zeroed out — you don't want to be holding inventory "
                    f"for a customer who has walked away from the product.")
        return ""

    parts = []

    # ── ISO (Initial Stocking Order) ──────────────────────────────────────────
    if r.get("iso"):
        weeks_ago = r.get("iso_weeks_ago", 0)
        iso_qty   = r.get("iso_qty", 0)
        if r.get("iso_settle"):
            parts.append(
                f"This account just picked up the item — that {int(iso_qty):,}-unit "
                f"opening order {weeks_ago} weeks ago was their initial stocking fill "
                f"to get product onto shelves. They're in the settle period now, "
                f"waiting to see how it moves at retail before they reorder. "
                f"Keep projections conservative here — the real demand signal "
                f"won't show up until POS velocity is established."
            )
        else:
            parts.append(
                f"This account took the item for the first time {weeks_ago} weeks ago "
                f"({int(iso_qty):,}-unit stocking order). They're past the initial "
                f"settle period, so you should start seeing a more normal replenishment "
                f"pattern emerge. Forecast is based on their post-launch buying pace, "
                f"not that opening fill."
            )

    # ── 1) Flat-line plan — placeholder, not a real projection ───────────────
    non_zero_manual = [v for v in manual if v > 0]
    if non_zero_manual:
        from collections import Counter
        val_counts = Counter(non_zero_manual)
        most_common_val, most_common_ct = val_counts.most_common(1)[0]
        if most_common_ct >= 13 and len(val_counts) <= 3:
            parts.append(
                f"The plan is flat at {most_common_val:,} units/week for "
                f"{most_common_ct} of 26 weeks — that's a placeholder, not a "
                f"real projection. No retail account buys on a perfectly even "
                f"cadence like that. Their actual buying pace over the last "
                f"13 weeks averages {int(bl):,}/week with normal week-to-week "
                f"variation. A flat plan will either leave you short when they "
                f"buy heavy or overstocked when they pull back."
            )

    # ── 2) Volume is lopsided vs. how the account actually buys ──────────────
    first_half_man = sum(manual[:13])
    second_half_man = sum(manual[13:])
    first_half_ai   = sum(ai[:13])
    second_half_ai  = sum(ai[13:])
    if proj_t > 0 and ai_t > 0:
        man_ratio = first_half_man / proj_t
        ai_ratio  = first_half_ai  / ai_t
        if abs(man_ratio - ai_ratio) > 0.15:
            man_pct = int(man_ratio * 100)
            ai_pct  = int(ai_ratio * 100)
            if man_ratio > ai_ratio:
                parts.append(
                    f"The plan is heavily front-loaded — {man_pct}% of the "
                    f"volume is in the first 13 weeks vs. {ai_pct}% based on "
                    f"how this account actually buys. If the back half doesn't "
                    f"materialize as planned, you'll be sitting on inventory "
                    f"heading into the next cycle."
                )
            else:
                parts.append(
                    f"The plan is back-loaded — only {man_pct}% of the volume "
                    f"is in the first 13 weeks. But this account's buying pattern "
                    f"shows stronger near-term demand ({ai_pct}% front-half). "
                    f"If they pull early, you may not have the inventory to support it."
                )

    # ── 3) Blank weeks where the account has historically ordered ─────────────
    man_zeros = [i for i in range(26) if manual[i] == 0 and ai[i] > 0]
    if man_zeros and len(man_zeros) <= 10:
        gap_vol  = sum(ai[i] for i in man_zeros)
        wk_list  = ", ".join(f"W{i+1}" for i in man_zeros[:6])
        more     = f" and {len(man_zeros)-6} more" if len(man_zeros) > 6 else ""
        parts.append(
            f"There are {len(man_zeros)} blank weeks ({wk_list}{more}) where "
            f"this account has historically been buying. That's roughly "
            f"{gap_vol:,} units of volume that may be missing from the plan. "
            f"Double-check whether those weeks are intentionally zeroed out "
            f"or just overlooked."
        )

    # ── 4) Plan is way above what buying history supports ─────────────────────
    spikes = [(i, manual[i], ai[i]) for i in range(26)
              if manual[i] > 0 and ai[i] > 0 and manual[i] > ai[i] * 2]
    if spikes:
        spike_vol = sum(mv - av for _, mv, av in spikes)
        wk_list   = ", ".join(
            f"W{i+1} ({int(mv):,} planned vs. {int(av):,} expected)"
            for i, mv, av in spikes[:3]
        )
        more = f" and {len(spikes)-3} more weeks" if len(spikes) > 3 else ""
        parts.append(
            f"The plan is more than double what buying history supports in "
            f"{len(spikes)} weeks: {wk_list}{more}. That's {spike_vol:,} units "
            f"above what we'd expect. If there's a known driver — promotion, "
            f"new store rollout, distribution gain — this needs documentation. "
            f"Otherwise this is a high risk of overstock."
        )

    # ── 5) Plan is well below what the account is actually pulling ────────────
    dips = [(i, manual[i], ai[i]) for i in range(26)
            if ai[i] > 0 and manual[i] > 0 and ai[i] > manual[i] * 2]
    if dips:
        dip_vol = sum(av - mv for _, mv, av in dips)
        wk_list = ", ".join(
            f"W{i+1} ({int(mv):,} planned vs. {int(av):,} expected)"
            for i, mv, av in dips[:3]
        )
        more = f" and {len(dips)-3} more weeks" if len(dips) > 3 else ""
        parts.append(
            f"The plan is significantly below what this account has been "
            f"buying in {len(dips)} weeks: {wk_list}{more}. That's roughly "
            f"{dip_vol:,} units of unplanned demand. If the account pulls at "
            f"their normal pace, you could be looking at out-of-stocks or "
            f"rushed replenishment orders."
        )

    # ── 6) Unexplained planner truncation ─────────────────────────────────────
    # When the planner has zeroed out a consecutive tail of weeks (W-N through
    # W26) but the item is still Active at the customer with no POG End Date,
    # something is missing.  The planner knows something the AI doesn't — a
    # POG ending, a listing drop, a distribution cut, a seasonal exit — and
    # that context needs to be documented so the AI can plan properly.
    # Threshold: >= 6 trailing zero weeks (anything less could be natural
    # intermittent cadence).
    _sc_raw   = (r.get("status_cust") or "").upper().strip()
    _it_raw   = (r.get("item_status") or "").upper().strip()
    _pog_end  = (r.get("pog_end") or "").strip()
    _is_fd    = _sc_raw.startswith("FD")
    _is_active_cust = _sc_raw.startswith("A") and not _is_fd
    _is_eol_item    = any(tok in _it_raw for tok in ("DISC", "PHASE", "EOL", "DELETE"))
    if _is_active_cust and not _pog_end and not _is_eol_item:
        # Find where the trailing zero block starts (scan backward from W26)
        _trunc_start_idx = None   # 0-based index of first trailing zero
        for _w in range(25, -1, -1):
            if manual[_w] > 0:
                _trunc_start_idx = _w + 1   # zero block begins at index _w+1
                break
        if _trunc_start_idx is None:
            _trunc_start_idx = 0            # all weeks are zero
        _trunc_len   = 26 - _trunc_start_idx
        _trunc_ai_vol = sum(ai[_trunc_start_idx:])
        if _trunc_len >= 6 and _trunc_ai_vol > 0:
            _trunc_wk1 = _trunc_start_idx + 1  # 1-indexed week label
            parts.insert(0,
                f"<b>Critical AI Flag:</b> The plan goes to zero at W{_trunc_wk1} "
                f"and stays flat through W26, but Status @ Cust is Active with no "
                f"POG End Date on file. The AI would forecast {_trunc_ai_vol:,} "
                f"units across those {_trunc_len} weeks based on buying history. "
                f"If there is an event driving this - POG ending, listing drop, "
                f"distribution cut, or seasonal exit - please document it: enter a "
                f"POG End Date, update the item status, or add a comment. Without "
                f"context this looks like missing demand and creates an inventory "
                f"blind spot."
            )

    if not parts:
        return ""

    return " ".join(parts)


def run_validation(rows, master_pack, args, amazon_pos=None, season_map=None,
                   oos_data=None, open_pos_data=None, amazon_catalog_us=None,
                   ats_data=None, switchover_weeks=None, acct_cadences=None,
                   retailer_pos=None):
    """Run projection validation + AI forecast for each record."""
    high = getattr(args, "threshold", VALID_HIGH_MULT)
    oos_data        = oos_data        or {}
    open_pos_data   = open_pos_data   or {}
    ats_data        = ats_data        or {}
    switchover_weeks = switchover_weeks or {}

    # S5 fix (2026-05-21) -- acct_cadences now pre-built once in main() and
    # passed in.  Fall back to local build if called standalone.
    if acct_cadences is None:
        acct_cadences = compute_account_cadences(rows)

    results = []
    for i, row in enumerate(rows, 1):
        key      = row.get("Acct_MStyle_Key_", "")
        oos_ent  = oos_data.get(key)
        po_wk    = open_pos_data.get(key)
        ats_hist = ats_data.get(row.get("Mstyle", ""))
        r = validate_record(row, master_pack, high_mult=high,
                            oos_entry=oos_ent, open_po_wk=po_wk,
                            ats_hist=ats_hist,
                            switchover_weeks=switchover_weeks)
        # Also run the AI forecast so we can show it in the viewer
        prefix = key.split("-")[0] if "-" in key else key
        acct_iv = acct_cadences.get(prefix)
        fr = forecast_record(row, master_pack, account_interval=acct_iv,
                             amazon_pos=amazon_pos, season_map=season_map,
                             oos_entry=oos_ent, open_po_wk=po_wk,
                             amazon_catalog_us=amazon_catalog_us,
                             ats_hist=ats_hist,
                             switchover_weeks=switchover_weeks,
                             retailer_pos=retailer_pos)
        r["ai_forecast"] = fr["forecast"]
        r["ai_model"]    = fr["model"]
        r["ai_total"]    = fr["new_total"]
        r["ai_per_wk"]   = round(fr["new_total"] / 26, 1) if fr["new_total"] else 0
        # On-Plan override: AI and Man are aligned -- nothing to review.
        # Two cases: (1) both zero; (2) plan entered and gap <= 7.5%.
        # Must run after ai_total is set so both sides are available.
        _man_tot = r.get("projection_total", 0)
        _ai_tot  = r.get("ai_total", 0)
        _both_zero = _man_tot == 0 and _ai_tot == 0
        if _both_zero or (_man_tot > 0 and abs(_ai_tot - _man_tot) / _man_tot <= 0.075):
            r["priority"] = "On-Plan"
        # Build the record-level narrative with both validation + forecast data
        r["narrative"]   = _build_record_narrative(r)
        results.append(r)
        if i % 100 == 0:
            print(f"      {i}/{len(rows)} validated ...")

    # Sort: CRITICAL priority first, then by severity, then by flag count
    pri_order = {"CRITICAL": 0, "MEDIUM": 1, "LOW": 2}
    sev_order = {"CRITICAL": 0, "WARNING": 1, "OK": 2}
    results.sort(key=lambda r: (pri_order.get(r["priority"], 2),
                                sev_order.get(r["max_severity"], 2),
                                -r["n_flags"]))
    return results


def _print_validation_summary(results):
    """Print a concise summary of validation findings."""
    total     = len(results)
    critical  = sum(1 for r in results if r["max_severity"] == "CRITICAL")
    warning   = sum(1 for r in results if r["max_severity"] == "WARNING")
    clean     = sum(1 for r in results if r["max_severity"] == "OK")
    tot_flags = sum(r["n_flags"] for r in results)

    print(f"\n  ── Validation Summary ──")
    print(f"  Total records:   {total}")
    print(f"  CRITICAL:        {critical}")
    print(f"  WARNING:         {warning}")
    print(f"  Clean:           {clean}")
    print(f"  Total flags:     {tot_flags}")

    flagged = [r for r in results if r["n_flags"] > 0]
    if flagged:
        print(f"\n  Top flagged records:")
        for r in flagged[:10]:
            sev_icon = "\u2622" if r["max_severity"] == "CRITICAL" else "\u26a0"
            print(f"    {sev_icon} {r['key']:30s}  {r['max_severity']:8s}  "
                  f"{r['n_flags']} flags  "
                  f"Proj: {r['projection_total']:>8,}  "
                  f"Exp: {int(r['expected_total']):>8,}  "
                  f"\u0394 {r['pct_diff']:+.1f}%")
    else:
        print("\n  All projections look reasonable!")


# ─── EDA analysis ─────────────────────────────────────────────────────────────

def run_eda(rows, master_pack):
    """
    Run full exploratory data analysis across all records.
    Covers: data quality, stationarity (rolling), seasonality, intermittency
    (ADI/CV²), calendar effects, outlier detection, panel structure.
    Returns a findings dict consumed by build_html_report().
    """
    findings = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_records": len(rows),
        "quality": [],
        "intermittency": [],
        "intermittency_summary": {},
        "stationarity": [],
        "outliers": [],
        "panel": {},
        "calendar": {},
        "model_recommendations": {},
    }

    week_totals = [[] for _ in range(52)]   # for calendar analysis

    for row in rows:
        hist  = get_history(row)
        key   = row.get("Acct_MStyle_Key_", "?")
        cust  = clean_html(row.get("Customr_Name", ""))
        mstyle = row.get("Mstyle", "")

        # ── Quality audit ──────────────────────────────────────────
        active_weeks  = sum(1 for v in hist if v > 0)
        zero_weeks    = 52 - active_weeks
        first_nz      = next((i for i, v in enumerate(hist) if v > 0), None)
        leading_zeros = first_nz if first_nz is not None else 52
        l13_active    = sum(1 for v in hist[-13:] if v > 0)
        l13_sum       = sum(hist[-13:])
        max_gap       = 0
        cur_gap       = 0
        for v in hist:
            if v == 0:
                cur_gap += 1
                max_gap = max(max_gap, cur_gap)
            else:
                cur_gap = 0

        findings["quality"].append({
            "key": key, "cust": cust, "mstyle": mstyle,
            "active_weeks": active_weeks, "zero_weeks": zero_weeks,
            "leading_zeros": leading_zeros, "max_gap": max_gap,
            "l13_active": l13_active, "l13_sum": int(l13_sum),
        })

        # ── Stationarity (rolling mean coefficient of variation) ───
        # Simple proxy: CV of rolling 4-week means across active history
        active_vals = [float(v) for v in hist if v > 0]
        if len(active_vals) >= 8:
            windows = [float(np.mean(active_vals[i:i+4])) for i in range(0, len(active_vals) - 3, 4)]
            roll_cv = float(np.std(windows) / np.mean(windows)) if np.mean(windows) > 0 else 0
            stationary = roll_cv < 0.3
        else:
            roll_cv, stationary = 0, True

        findings["stationarity"].append({
            "key": key, "roll_cv": round(roll_cv, 3), "stationary": stationary,
        })

        # ── Intermittency (ADI / CV²) ──────────────────────────────
        if active_weeks > 0:
            adi  = 52.0 / active_weeks
            mean_v = float(np.mean(active_vals))
            cv   = float(np.std(active_vals) / mean_v) if mean_v > 0 else 0
            cv2  = cv ** 2
        else:
            adi, cv, cv2 = 99.0, 0.0, 0.0

        # ADI/CV² quadrant classification
        if   adi < 1.32 and cv2 < 0.49:  adi_class = "Smooth"
        elif adi >= 1.32 and cv2 < 0.49: adi_class = "Intermittent"
        elif adi < 1.32 and cv2 >= 0.49: adi_class = "Erratic"
        else:                             adi_class = "Lumpy"

        findings["intermittency"].append({
            "key": key, "cust": cust, "mstyle": mstyle,
            "adi": round(adi, 2), "cv2": round(cv2, 3),
            "class": adi_class, "active_weeks": active_weeks,
        })

        # ── Outlier detection (IQR method, 3× fence) ──────────────
        if active_weeks >= 4:
            q1  = float(np.percentile(active_vals, 25))
            q3  = float(np.percentile(active_vals, 75))
            iqr = q3 - q1
            upper_fence = q3 + 3.0 * iqr
            for i, v in enumerate(hist):
                if float(v) > upper_fence:
                    findings["outliers"].append({
                        "key": key, "week": i + 1, "value": int(v),
                        "upper_fence": round(upper_fence),
                        "note": "spike — possible Prime Day pre-order or data error",
                    })

        # ── Calendar: accumulate weekly demand ─────────────────────
        for i, v in enumerate(hist):
            if float(v) > 0:
                week_totals[i].append(float(v))

    # ── Intermittency summary ──────────────────────────────────────
    classes = {}
    for r in findings["intermittency"]:
        classes[r["class"]] = classes.get(r["class"], 0) + 1
    findings["intermittency_summary"] = classes

    # ── Panel / hierarchy analysis ─────────────────────────────────
    custs   = {}
    mstyles = {}
    for q in findings["quality"]:
        custs[q["cust"]]     = custs.get(q["cust"], 0) + 1
        mstyles[q["mstyle"]] = mstyles.get(q["mstyle"], 0) + 1

    findings["panel"] = {
        "n_customers": len(custs),
        "n_mstyles":   len(mstyles),
        "top_customers": sorted(custs.items(), key=lambda x: -x[1])[:10],
        "top_mstyles":   sorted(mstyles.items(), key=lambda x: -x[1])[:10],
    }

    # ── Calendar effects ───────────────────────────────────────────
    all_vals = [v for wt in week_totals for v in wt]
    all_mean = float(np.mean(all_vals)) if all_vals else 1.0
    week_lift = {}
    for i, wt in enumerate(week_totals):
        if wt:
            week_lift[i + 1] = round(float(np.mean(wt)) / all_mean, 2)
        else:
            week_lift[i + 1] = 1.0

    _eda_prime_wks, _eda_fall_wks = _get_event_boosts()
    prime_lift = (float(np.mean([week_lift.get(w, 1.0) for w in _eda_prime_wks]))
                  if _eda_prime_wks else 1.0)
    fall_lift  = (float(np.mean([week_lift.get(w, 1.0) for w in _eda_fall_wks]))
                  if _eda_fall_wks else 1.0)

    findings["calendar"] = {
        "prime_day_lift": round(prime_lift, 2),
        "fall_deal_lift": round(fall_lift, 2),
        "week_lift_profile": week_lift,
    }

    # Model recommendation summary
    # classify() emits "inactive" | "sparse_intermittent" | "active"; the
    # routing in forecast_record() then maps active to either Seasonal Baseline
    # (dense >=50% nz) or Croston's (intermittent 25-50% nz), and sparse_intermittent
    # to Heuristic or Sparse Intermittent depending on volume.
    recs = {"Seasonal Baseline": 0, "Croston's": 0, "Heuristic": 0, "Inactive": 0}
    for row in rows:
        hist = get_history(row)
        pat  = classify(hist)
        if pat == "inactive":
            recs["Inactive"] += 1
        elif pat == "sparse_intermittent":
            recs["Heuristic"] += 1
        else:
            # active: Seasonal Baseline vs Croston's based on nz density
            if nz_rate(hist, window=26) >= DENSE_THRESHOLD:
                recs["Seasonal Baseline"] += 1
            else:
                recs["Croston's"] += 1
    findings["model_recommendations"] = recs

    return findings


# ─── HTML report ──────────────────────────────────────────────────────────────

def build_html_report(findings, scope_desc, results=None):
    """
    Generate a self-contained HTML analysis report.
    findings = output of run_eda()
    results  = optional list of forecast dicts (from Phase 3) for summary table
    """
    def tbl_row(*cells, header=False):
        tag = "th" if header else "td"
        return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"

    qdata     = findings["quality"]
    imm       = findings["intermittency"]
    imm_sum   = findings["intermittency_summary"]
    cal       = findings["calendar"]
    panel     = findings["panel"]
    outliers  = findings["outliers"]
    stat      = findings["stationarity"]
    mrec      = findings["model_recommendations"]

    # Data quality grade
    bad_q = sum(1 for q in qdata if q["active_weeks"] < 13 or q["max_gap"] > 8)
    grade_pct = 1 - bad_q / max(len(qdata), 1)
    grade = "A" if grade_pct >= 0.9 else "B" if grade_pct >= 0.75 else "C" if grade_pct >= 0.5 else "D"
    non_stationary = sum(1 for s in stat if not s["stationary"])

    # Top quality issues
    issues_rows = ""
    for q in sorted(qdata, key=lambda x: x["active_weeks"])[:10]:
        issues_rows += tbl_row(
            q["key"], q["cust"], q["mstyle"],
            q["active_weeks"], q["zero_weeks"], q["leading_zeros"], q["max_gap"]
        )

    # Intermittency table (top 15 by ADI)
    imm_rows = ""
    for r in sorted(imm, key=lambda x: -x["adi"])[:15]:
        imm_rows += tbl_row(r["key"], r["cust"], r["mstyle"],
                            r["adi"], r["cv2"], r["class"], r["active_weeks"])

    # Outlier table
    out_rows = ""
    for o in findings["outliers"][:20]:
        out_rows += tbl_row(o["key"], o["week"], f"{o['value']:,}", f"{o['upper_fence']:,}", o["note"])

    # Calendar lift table -- use actual window weeks from _get_event_boosts()
    _html_prime_wks, _html_fall_wks = _get_event_boosts()
    prime_weeks_str = (", ".join(f"W{w}" for w in sorted(_html_prime_wks))
                       if _html_prime_wks else "none in window")
    fall_weeks_str  = (", ".join(f"W{w}" for w in sorted(_html_fall_wks))
                       if _html_fall_wks else "none in window")

    # Forecast results table (if provided)
    fcst_section = ""
    if results:
        fcst_rows = ""
        total_26w = sum(r["new_total"] for r in results)
        for r in results:
            flag = " ⚠" if r.get("alert") else ""
            bw   = " [BW]" if r.get("biweekly") else ""
            fcst_rows += tbl_row(
                r["key"], r["cust"], r["mstyle"],
                r["model"] + bw,
                f"{r['cap_base']:,.0f}",
                f"{r['new_total']:,}",
                f"{r['prior_total']:,}",
                f"{r['pct_diff']:+.1f}%" + flag,
            )
        fcst_section = f"""
        <h2>9. Forecast Results</h2>
        <p>Total 26-week demand: <strong>{total_26w:,}</strong> across {len(results)} records.</p>
        <table>
          <tr>
            {tbl_row("Key","Customer","Mstyle","Model","Cap Base/wk","AI 26w","Manual 26w","Δ%", header=True)}
          </tr>
          {fcst_rows}
        </table>
        <p><em>[BW] = bi-weekly cadence enforced &nbsp;|&nbsp; ⚠ = ALERT written to Quickbase</em></p>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Inventory Forecast Analysis — {scope_desc}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 40px; color: #222; max-width: 1200px; }}
  h1   {{ color: #1a1a2e; }}
  h2   {{ color: #16213e; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; margin-top: 40px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
  th   {{ background: #f0f4f8; font-weight: 600; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .grade {{ font-size: 2em; font-weight: bold; color: #2e7d32; }}
  .kpi {{ display: inline-block; margin: 10px 20px 10px 0; padding: 14px 20px;
          background: #f5f5f5; border-radius: 8px; min-width: 120px; }}
  .kpi .val {{ font-size: 1.8em; font-weight: bold; color: #1565c0; }}
  .kpi .lbl {{ font-size: 0.8em; color: #666; margin-top: 4px; }}
  .warn {{ color: #c62828; }}
  .ok   {{ color: #2e7d32; }}
</style>
</head>
<body>
<h1>Inventory Forecast Analysis Report</h1>
<p>Scope: <strong>{scope_desc}</strong> &nbsp;|&nbsp; Generated: {findings['generated_at']}</p>

<h2>1. Executive Summary</h2>
<div>
  <div class="kpi"><div class="val">{findings['n_records']}</div><div class="lbl">Records</div></div>
  <div class="kpi"><div class="val">{panel['n_customers']}</div><div class="lbl">Customers</div></div>
  <div class="kpi"><div class="val">{panel['n_mstyles']}</div><div class="lbl">Mstyles</div></div>
  <div class="kpi"><div class="val grade">{grade}</div><div class="lbl">Data Quality</div></div>
  <div class="kpi"><div class="val">{len(outliers)}</div><div class="lbl">Outliers</div></div>
  <div class="kpi"><div class="val">{non_stationary}</div><div class="lbl">Non-stationary</div></div>
</div>
<h3>Model Recommendation Split</h3>
<table style="width:auto">
  <tr>{tbl_row("Model","Records", header=True)}</tr>
  {"".join(tbl_row(k, v) for k, v in mrec.items())}
</table>
<h3>Intermittency Summary (ADI/CV²)</h3>
<table style="width:auto">
  <tr>{tbl_row("Class","Records","Recommended Model", header=True)}</tr>
  {tbl_row("Smooth",      imm_sum.get("Smooth",0),      "Seasonal Baseline")}
  {tbl_row("Erratic",     imm_sum.get("Erratic",0),     "Seasonal Baseline (with caution)")}
  {tbl_row("Intermittent",imm_sum.get("Intermittent",0),"Croston's")}
  {tbl_row("Lumpy",       imm_sum.get("Lumpy",0),       "Croston's")}
</table>

<h2>2. Data Quality &amp; Gaps</h2>
<p>Records with &lt;13 active weeks or gaps &gt;8 weeks: <strong class="{'warn' if bad_q > 0 else 'ok'}">{bad_q}</strong></p>
<table>
  <tr>{tbl_row("Key","Customer","Mstyle","Active Wks","Zero Wks","Leading Zeros","Max Gap", header=True)}</tr>
  {issues_rows}
</table>
<p><em>Showing bottom 10 by active weeks.</em></p>

<h2>3. Stationarity Analysis</h2>
<p>Rolling 4-week mean CV &gt; 0.30 = non-stationary (trend or variance shift detected).</p>
<p>Non-stationary records: <strong>{non_stationary}</strong> of {len(stat)}</p>
<table style="width:auto">
  <tr>{tbl_row("Key","Rolling CV","Stationary?", header=True)}</tr>
  {"".join(tbl_row(s['key'], s['roll_cv'], '✓' if s['stationary'] else '⚠ No') for s in sorted(stat, key=lambda x: -x['roll_cv'])[:15])}
</table>

<h2>4. Seasonality &amp; Calendar Effects</h2>
<p>Observed demand lift in event windows (vs overall mean):</p>
<table style="width:auto">
  <tr>{tbl_row("Event","Weeks","Observed Lift","Configured Lift", header=True)}</tr>
  {tbl_row("Prime Day (May 1/15/29)", prime_weeks_str, f"{cal['prime_day_lift']:.2f}x", f"{PRIME_DAY_LIFT:.2f}x")}
  {tbl_row("Fall Prime Day (Tue after Mem Day)", fall_weeks_str, f"{cal['fall_deal_lift']:.2f}x", f"{FALL_PRIME_DAY_LIFT:.2f}x")}
</table>
<p><em>Configured lifts are applied as caps (Seasonal Baseline) or boosts (Croston's, Heuristic).
Observed lift is informational -- update PRIME_DAY_BUMPS / FALL_PRIME_DAY_LIFT constants if
historical data shows materially different values.</em></p>

<h2>5. Intermittency Analysis (ADI / CV²)</h2>
<p>ADI &lt; 1.32 = demand occurs frequently | CV² &lt; 0.49 = demand size is consistent</p>
<table>
  <tr>{tbl_row("Key","Customer","Mstyle","ADI","CV²","Class","Active Wks", header=True)}</tr>
  {imm_rows}
</table>
<p><em>Showing 15 most intermittent records.</em></p>

<h2>6. Outlier &amp; Anomaly Detection (IQR 3× fence)</h2>
<p>Outliers detected: <strong>{len(outliers)}</strong></p>
{"<table><tr>" + tbl_row("Key","Week","Value","Upper Fence","Note", header=True) + "</tr>" + out_rows + "</table>" if outliers else "<p>No outliers detected.</p>"}

<h2>7. Panel / Hierarchy Structure</h2>
<h3>Top Customers by Record Count</h3>
<table style="width:auto">
  <tr>{tbl_row("Customer","# Records", header=True)}</tr>
  {"".join(tbl_row(c, n) for c, n in panel['top_customers'])}
</table>
<h3>Top Mstyles by Record Count</h3>
<table style="width:auto">
  <tr>{tbl_row("Mstyle","# Records", header=True)}</tr>
  {"".join(tbl_row(m, n) for m, n in panel['top_mstyles'])}
</table>

<h2>8. Forecasting Roadmap</h2>
<ul>
  <li><strong>Seasonal Baseline:</strong> L13W non-zero avg with VP-Q1 baseline-mode
      gating, position-based seasonal profile damped to flat (DAMP=0.1).
      Bi-weekly cadence enforcement applied post-forecast for steady items.</li>
  <li><strong>Croston's:</strong> a={CR_ALPHA}, demand/interval refined 70% L13W / 30% smoothed.
      Event calendar insertions applied at Prime Day and Fall Deal windows.</li>
  <li><strong>Heuristic:</strong> Ramp weeks 1-6 post-launch excluded. Post-ramp avg → L13W → L52W fallback chain.</li>
  <li><strong>Inactive:</strong> Zero forecast. Alert written if prior projection &gt; 0.</li>
  <li><strong>Alert threshold:</strong> {ALERT_THRESHOLD*100:.4g}% variance vs manual projections.</li>
  <li><strong>Risk flags:</strong> {bad_q} records with sparse history | {len(outliers)} outlier weeks detected |
      {non_stationary} non-stationary series.</li>
</ul>

{fcst_section}

</body>
</html>"""
    return html


# ─── Progress tracking ────────────────────────────────────────────────────────

_lock     = threading.Lock()
_done     = 0
_ok       = 0
_failed   = 0
_total_wb = 0
_failures = []


def tick(key, success):
    global _done, _ok, _failed
    with _lock:
        _done += 1
        if success: _ok += 1
        else: _failed += 1; _failures.append(key)
        if _done % 50 == 0 or _done == _total_wb:
            pct = 100 * _done / _total_wb if _total_wb else 0
            print(f"  [{_done:4d}/{_total_wb}] {pct:5.1f}%  ok={_ok}  fail={_failed}", flush=True)


# ─── AI Analysis narrative builder ────────────────────────────────────────────
#
# Pre-computes the per-record narrative that the QB codepage viewer reads from
# the [AI Analysis] field (fid 1590).  Mirrors the logic that the local
# viewer's _adapt_forecast_to_validation() runs at viewer load time, so both
# viewers show identical analysis text without the codepage having to re-derive
# it client-side.  Returns rich-text HTML.

def _friendly_cust_name(cust):
    """Return a short, planner-readable customer label for narrative headers.
    Falls back to "Retailer" for unknown / empty inputs.

    Generalized 2026-05-08 to support any customer's POS data — not just
    Amazon.  As Walmart/Petsmart/Petco POS sources come online, this mapping
    handles their labeling automatically.
    """
    if not cust:
        return "Retailer"
    s = str(cust).strip().upper()
    # Known short names — covers our top retailers cleanly.
    _MAP = (
        ("AMAZON",       "Amazon"),
        ("WAL MART",     "Walmart"),
        ("WALMART",      "Walmart"),
        ("PETSMART",     "Petsmart"),
        ("PETCO",        "Petco"),
        ("CHEWY",        "Chewy"),
        ("TARGET",       "Target"),
        ("KROGER",       "Kroger"),
        ("LOWES",        "Lowes"),
        ("HOME DEPOT",   "Home Depot"),
        ("ROSS",         "Ross"),
        ("BURLINGTON",   "Burlington"),
        ("CVS",          "CVS"),
        ("DOLLAR GENERAL","Dollar General"),
        ("DOLLAR TREE",  "Dollar Tree"),
        ("FAMILY DOLLAR","Family Dollar"),
    )
    for needle, label in _MAP:
        if needle in s:
            return label
    # Fallback: take first word of the cust string, title-cased.
    first = s.split()[0] if s.split() else "Retailer"
    return first.title()


def compute_forecast_confidence(model, meta, hist, manual, pct_diff,
                                is_new_launch=False, is_otb=False,
                                season=None):
    """Compute a 0-100 confidence score for an AI forecast record.

    Five components of 20 points each:
      C1  History depth     -- L13W non-zero week count
      C2  Model quality     -- model type hierarchy
      C3  Trend stability   -- penalty for decline/surge corrections
      C4  Seasonal signal   -- quality of seasonal profile match
      C5  Planner alignment -- how close AI is to the manual projection

    Special caps: new launches <= 55, OTB items <= 40, Inactive models <= 25.
    Returns int in [0, 100].
    """
    drivers  = (meta.get("drivers", [])    if isinstance(meta, dict) else [])
    rf_list  = (meta.get("rule_fires", []) if isinstance(meta, dict) else [])
    drv_text = " ".join(str(d) for d in drivers)
    rf_text  = " ".join(str(f) for f in rf_list)
    all_text = drv_text + " " + rf_text

    # C1: History depth (0-20) ------------------------------------------------
    l13    = hist[-13:] if len(hist) >= 13 else hist
    l13_nz = sum(1 for v in l13 if float(v or 0) > 0)
    if   l13_nz >= 10: c1 = 20
    elif l13_nz >=  7: c1 = 16
    elif l13_nz >=  4: c1 = 11
    elif l13_nz >=  2: c1 =  6
    elif l13_nz ==  1: c1 =  3
    else:              c1 =  0

    # C2: Model quality (0-20) ------------------------------------------------
    _model_pts = [
        ("Seasonal Baseline",                   20),
        ("Croston's",                           17),
        ("Sparse Intermittent",                 14),
        ("Heuristic (F72 new-launch ramp)",      8),
        ("Heuristic",                           10),
        ("Reactivating",                         6),
        ("Inactive+Floor",                       5),
        ("Inactive+S6 (off-price)",              5),
        ("OTB (zero)",                           5),
        ("Pre-launch NEW (manual passthrough)",  4),
    ]
    c2 = 2  # default: any Inactive variant
    for prefix, pts in _model_pts:
        if model.startswith(prefix):
            c2 = pts
            break

    # C3: Trend stability (0-20) ----------------------------------------------
    c3 = 20
    if "F77" in all_text:   c3 -= 15   # severe decline blend
    elif "F10" in all_text: c3 -= 8    # YoY-gated decline blend
    if "F79" in all_text:   c3 -= 4    # growth acceleration (uncertain)
    if "F81" in all_text:   c3 -= 3    # APL recency divergence
    if "F78" in drv_text:   c3 -= 3    # peak-anchor fallback (no keyword match)
    c3 = max(0, c3)

    # C4: Seasonal signal quality (0-20) --------------------------------------
    if season:
        c4 = 20                            # planner-curated Season tag
    elif ("empirical" in drv_text.lower()
          or "derived" in drv_text.lower()
          or "F64" in drv_text):
        c4 = 17                            # empirical derived category profile
    elif ("category profile" in drv_text.lower()
          or "keyword" in drv_text.lower()):
        c4 = 13                            # CATEGORY_PROFILES keyword match
    elif "F78" in drv_text:
        c4 = 8                             # peak-anchor fallback (no keyword)
    else:
        c4 = 11                            # no profile -- flat/neutral item

    # C5: Planner alignment (0-20) --------------------------------------------
    manual_total = sum(float(v or 0) for v in (manual or []))
    if manual_total == 0:
        c5 = 10                            # no manual plan -- neutral
    else:
        ap = abs(pct_diff or 0)
        if   ap <=  5: c5 = 20
        elif ap <= 15: c5 = 16
        elif ap <= 30: c5 = 10
        elif ap <= 60: c5 =  5
        else:          c5 =  0

    score = c1 + c2 + c3 + c4 + c5

    # Special caps ------------------------------------------------------------
    if is_new_launch:          score = min(score, 55)
    if is_otb:                 score = min(score, 40)
    if model.startswith("Inactive"): score = min(score, 25)

    return max(0, min(100, score))


def _smart_order_trend(hist_l26, ly_hist_26=None, cust_label="this account"):
    """Build a 2-sentence data-backed Order trend explanation from order history.

    Returns HTML or "" when too flat to be insightful.  Picks the FIRST matching
    discriminator from a priority-ordered list (gap-week vs cadence-drop vs
    qty-shrinkage vs YoY softening etc.) so the sentence is specific to this
    record's pattern AND reads like a sales/planning rep talking about real
    retailer behavior rather than generic seasonality boilerplate.
    """
    if not hist_l26 or len(hist_l26) < 4:
        return ""
    h = [float(v or 0) for v in hist_l26]
    l4  = h[-4:]
    l13 = h[-13:] if len(h) >= 13 else h
    l26 = h[-26:] if len(h) >= 26 else h
    l4_avg  = sum(l4)  / 4.0
    l13_avg = sum(l13) / 13.0
    l26_avg = sum(l26) / 26.0 if len(l26) >= 26 else (sum(l26) / max(len(l26), 1))
    if l13_avg <= 0 and l4_avg <= 0:
        return ""
    short_pct = (l4_avg / l13_avg - 1.0) * 100 if l13_avg > 0 else 0.0
    l13_nz = [v for v in l13 if v > 0]
    l4_nz  = [v for v in l4  if v > 0]
    per_l13 = (sum(l13_nz) / len(l13_nz)) if l13_nz else 0.0
    per_l4  = (sum(l4_nz)  / len(l4_nz))  if l4_nz  else 0.0
    freq_l13 = len(l13_nz) / 13.0
    freq_l4  = len(l4_nz)  / 4.0
    lw = h[-1]; pw = h[-2] if len(h) >= 2 else 0
    medium_flat = (abs(l26_avg - l13_avg) / max(l13_avg, 1)) < 0.15
    # L52: from full history when available (hist is 52w); fallback to LY+L26 splice
    l52_avg = None
    if len(h) >= 52:
        l52_avg = sum(h[-52:]) / 52.0
    elif ly_hist_26 and len(ly_hist_26) >= 13:
        full52 = [float(v or 0) for v in ly_hist_26] + list(l26)
        if len(full52) >= 40:
            l52_avg = sum(full52) / len(full52)

    # Compact run-rate header: LW, Avg L4W, L13W, L26W, L52W
    # (mirrors Amazon POS Sales format; shown for all non-Amazon records)
    _hdr_parts = []
    if lw > 0:
        _hdr_parts.append(f"LW {int(lw):,}u")
    if l4_avg > 0:
        _hdr_parts.append(f"Avg L4W {l4_avg:.0f}/wk")
    if l13_avg > 0:
        _hdr_parts.append(f"L13W {l13_avg:.0f}/wk")
    if l26_avg > 0:
        _hdr_parts.append(f"L26W {l26_avg:.0f}/wk")
    if l52_avg and l52_avg > 0:
        _hdr_parts.append(f"L52W {l52_avg:.0f}/wk")
    if not _hdr_parts:
        return ""
    header = "<b>Order Trends:</b> " + ", ".join(_hdr_parts) + "."

    # Only surface trend explanation when the shift is meaningful (>=10%)
    if abs(short_pct) < 10:
        return header

    direction = "up" if short_pct > 0 else "down"
    arrow = ('<span style="color:#2e7d32;font-weight:700">&#x25B2;</span>'
             if short_pct > 0 else
             '<span style="color:#c62828;font-weight:700">&#x25BC;</span>')

    cl = cust_label or "this account"
    expl = None
    # Priority-ordered discriminators — first match wins.
    # 1) Gap-week: LW=0 after a normal Prior-Wk order, otherwise stable.
    if (short_pct < 0 and lw == 0 and pw > 0 and per_l13 > 0 and
            pw <= per_l13 * 1.6 and medium_flat and len(l4_nz) >= 1):
        expl = (f"LW=0 after a normal {pw:.0f}u Prior Wk order; L26W "
                f"({l26_avg:.0f}/wk) still tracks L13W ({l13_avg:.0f}/wk) "
                f"with {len(l13_nz)}/13W active. Single zero within an "
                f"otherwise active cadence -- watch next 2-3 weeks; if no "
                f"order lands, that is the real signal.")
    # 2) Per-order qty shrinkage with stable cadence.
    elif (short_pct < 0 and per_l13 > 0 and per_l4 > 0 and
          per_l4 / per_l13 <= 0.80 and
          abs(freq_l4 - freq_l13) / max(freq_l13, 0.01) < 0.30):
        expl = (f"Per-order qty dropped from ~{per_l13:.0f}u (L13W avg) to "
                f"~{per_l4:.0f}u (L4W avg) while reorder cadence held steady "
                f"({len(l4_nz)}/L4W vs {len(l13_nz)}/L13W). Smaller builds "
                f"at same frequency -- confirm with sales rep what changed.")
    # 3) Cadence drop (qty stable, fewer orders).
    elif (short_pct < 0 and per_l13 > 0 and per_l4 > 0 and
          0.85 <= per_l4 / per_l13 <= 1.20 and
          freq_l4 < freq_l13 * 0.70):
        expl = (f"L4W had {len(l4_nz)} order(s) at ~{per_l4:.0f}u vs the "
                f"L13W pattern of {len(l13_nz)} orders at ~{per_l13:.0f}u -- "
                f"fewer orders, same per-PO size. No POS data available to "
                f"confirm retail velocity; verify with sales rep.")
    # 4) Multi-quarter softening (L26 below L52).
    elif (short_pct < 0 and l52_avg and l52_avg > 0 and
          l26_avg < l52_avg * 0.85):
        yoy_pct = (l26_avg / l52_avg - 1.0) * 100
        expl = (f"L26W ({l26_avg:.0f}/wk) is {yoy_pct:+.0f}% vs L52W "
                f"({l52_avg:.0f}/wk) -- below the same period last year and "
                f"below the L26W window. Multi-quarter pattern at {cl}; "
                f"investigate with the sales team.")
    # 5) YoY momentum (up direction confirmed by L26 > L52).
    elif (short_pct > 0 and l52_avg and l52_avg > 0 and
          l26_avg > l52_avg * 1.10):
        yoy_pct = (l26_avg / l52_avg - 1.0) * 100
        expl = (f"L26W ({l26_avg:.0f}/wk) is +{yoy_pct:.0f}% vs L52W "
                f"({l52_avg:.0f}/wk) -- above the same period last year and "
                f"sustained across the L26W window. Multi-quarter positive "
                f"trend at {cl}.")
    # 6) Per-order qty growth (cadence stable, qty up).
    elif (short_pct > 0 and per_l13 > 0 and per_l4 > 0 and
          per_l4 / per_l13 >= 1.20 and
          abs(freq_l4 - freq_l13) / max(freq_l13, 0.01) < 0.30):
        expl = (f"Per-order qty grew from ~{per_l13:.0f}u (L13W avg) to "
                f"~{per_l4:.0f}u (L4W avg) while reorder cadence held steady "
                f"({len(l4_nz)}/L4W vs {len(l13_nz)}/L13W). Larger builds "
                f"at same frequency -- confirm with sales rep whether "
                f"distribution or store count changed.")
    # 7) Burst rebound (LW > 0 after Prior Wk zero).
    elif short_pct > 0 and lw > 0 and pw == 0 and freq_l13 > 0:
        expl = (f"LW {lw:.0f}u after a Prior Wk zero at {cl}. L13W cadence "
                f"was {len(l13_nz)} orders/13W. Monitor next 2-3 weeks to "
                f"confirm whether this is a sustained return to ordering.")
    # 8) Sustained quiet (both recent weeks zero, declining).
    elif lw == 0 and pw == 0 and short_pct < 0:
        expl = (f"Two consecutive zero weeks at {cl}. L13W cadence was "
                f"{len(l13_nz)}/13W active -- two zeros in a row is below "
                f"the established pattern. Verify order status before "
                f"treating as a trend change.")
    # 9) Fallback — use medium-term context.
    else:
        if short_pct > 0:
            expl = (f"L26W ({l26_avg:.0f}/wk) tracks L13W ({l13_avg:.0f}/wk) -- "
                    f"the uptick is concentrated in L4W only ({l4_avg:.0f}/wk). "
                    f"Monitor next 2-3 weeks for confirmation before treating "
                    f"as a baseline shift.")
        else:
            if medium_flat:
                expl = (f"L26W ({l26_avg:.0f}/wk) ≈ L13W ({l13_avg:.0f}/wk), "
                        f"so {cl}'s medium-term run rate is flat and the "
                        f"recent dip looks like normal cadence variance "
                        f"over a short window. No action unless it persists.")
            else:
                expl = (f"L26W ({l26_avg:.0f}/wk) and L13W ({l13_avg:.0f}/wk) "
                        f"are both off baseline — this is a broader cooling "
                        f"pattern at {cl}, not just last 4 weeks. Worth "
                        f"checking POS or distribution for what changed.")
    return (f"{header} {arrow} {direction} {abs(short_pct):.0f}% (L4W vs L13W). {expl}"
            if expl else header)


def _smart_pos_trend(l4, l13, l26, l52, ord_lw=0, ord_pw=0, l13_anomaly=False,
                     cust_label="this account"):
    """2-sentence data-backed Sales-trend explanation from POS rates.
    Reads like a sales/planning rep talking about real consumer-demand
    behavior rather than algorithm output.
    """
    l13_for_trend = ((l4 + l26) / 2.0) if l13_anomaly else l13
    if l13_for_trend <= 0 or l4 <= 0:
        return ""
    short_pct = (l4 / l13_for_trend - 1.0) * 100
    if abs(short_pct) < 10:
        return ""
    medium_pct = (l13 / l26 - 1.0) * 100 if l26 > 0 and l13 > 0 else None
    yoy_pct    = (l26 / l52 - 1.0) * 100 if l52 > 0 and l26 > 0 else None
    direction = "up" if short_pct > 0 else "down"
    arrow = ('<span style="color:#2e7d32;font-weight:700">&#x25B2;</span>'
             if short_pct > 0 else
             '<span style="color:#c62828;font-weight:700">&#x25BC;</span>')
    yoy_str = f"; YoY {yoy_pct:+.0f}%" if yoy_pct is not None else ""
    header = (f"<b>Sales trend:</b> {arrow} {direction} {abs(short_pct):.0f}% "
              f"L4W vs L13W{yoy_str}.")

    cl = cust_label or "this account"
    expl = None
    # 1) All windows aligned (sustained direction)
    if (yoy_pct is not None and medium_pct is not None and
        ((short_pct > 0 and medium_pct >= 5 and yoy_pct >= 10) or
         (short_pct < 0 and medium_pct <= -5 and yoy_pct <= -10))):
        verb = "growth" if short_pct > 0 else "softening"
        expl = (f"Consumer demand aligned across all windows: L4W "
                f"{l4:.0f}/wk, L13W {medium_pct:+.0f}% vs L26W, YoY "
                f"{yoy_pct:+.0f}%. Consistent multi-window {verb} at {cl}.")
    # 2) Recent down with hot medium-term (cooling from peak)
    elif short_pct < 0 and medium_pct is not None and medium_pct >= 10:
        expl = (f"POS was +{medium_pct:.0f}% vs L26W through L13W and has "
                f"cooled to {l4:.0f}/wk in L4W (vs L13W {l13:.0f}/wk). "
                f"Data does not confirm whether the L4W dip is temporary "
                f"or a sustained change.")
    # 3) Recent up but flat medium-term (fresh acceleration)
    elif short_pct > 0 and medium_pct is not None and abs(medium_pct) < 5:
        expl = (f"L13W ({l13:.0f}/wk) matches L26W ({l26:.0f}/wk) -- the "
                f"recent uptick is concentrated in L4W ({l4:.0f}/wk) only. "
                f"Monitor 2-3 more weeks before treating as a rate change.")
    # 4) Recent down but flat medium-term (short-window dip)
    elif short_pct < 0 and medium_pct is not None and abs(medium_pct) < 5:
        expl = (f"L13W ({l13:.0f}/wk) matches L26W ({l26:.0f}/wk) -- the "
                f"recent dip is concentrated in L4W ({l4:.0f}/wk) only. "
                f"Monitor L4 over next 2-3 weeks to determine if trend "
                f"persists.")
    # 5) Up but YoY negative (rebound from softer year)
    elif short_pct > 0 and yoy_pct is not None and yoy_pct <= -10:
        expl = (f"L26W ({l26:.0f}/wk) is {yoy_pct:+.0f}% vs L52W "
                f"({l52:.0f}/wk) -- the recent L4W uptick ({l4:.0f}/wk) "
                f"is set against a softer trailing year. Year-over-year "
                f"baseline is still negative.")
    # 6) Recent ordered-units context
    elif ord_lw > 0 or ord_pw > 0:
        if ord_pw > 0 and ord_lw > 0:
            wow = (ord_lw / ord_pw - 1.0) * 100
            expl = (f"LW {ord_lw:,.0f}u, Prior Wk {ord_pw:,.0f}u "
                    f"({wow:+.0f}% WoW). POS L4W {l4:.0f}/wk, L13W "
                    f"{l13:.0f}/wk. Monitor L4 POS over next 4 weeks "
                    f"for confirmation.")
        elif ord_lw == 0 and ord_pw > 0:
            expl = (f"LW orders=0 after Prior Wk {ord_pw:,.0f}u while POS "
                    f"is still moving ({l4:.0f}/wk L4W). Ordering paused "
                    f"with consumer demand active -- verify inventory "
                    f"position with sales rep.")
        else:
            expl = (f"L4W consumer rate {l4:.0f}/wk vs L13W {l13:.0f}/wk. "
                    f"Watch how L13 and L26 trend over the next 2-3 weeks "
                    f"to confirm whether this is a real shift.")
    # 7) Fallback
    else:
        if l52 > 0:
            anchor_pct = (l4 / l52 - 1.0) * 100
            expl = (f"L4W {l4:.0f}/wk is {anchor_pct:+.0f}% vs L52W baseline "
                    f"({l52:.0f}/wk). Insufficient medium-term data to "
                    f"confirm trend direction -- monitor next 4 weeks.")
        else:
            expl = (f"L4W {l4:.0f}/wk vs L13W {l13:.0f}/wk — limited "
                    f"history to read multi-window context. Worth a quick "
                    f"sales-rep check on what's happening at retail.")
    return f"{header} {expl}"


# Phrases that flag a generic/obvious alert sentence — planners already know
# these; they just add noise and dilute the real callouts.
_GENERIC_ALERT_PHRASES = [
    # Gap / model-expectation boilerplate (visible in the grid already)
    "overstock building",
    "chasing inventory",
    "if orders cool off",
    "the model expects",
    "expect to be chasing",
    "orders cool off",
    # Ordering-pattern observations (obvious from the order history numbers)
    "buy in bursts",
    "doesn't order every week",
    # Flat-plan observations (obvious from the projection table)
    "looks like a copy-paste",
    "flat placeholder",
]

def _is_generic_alert(s: str) -> bool:
    sl = s.lower()
    return any(ph.lower() in sl for ph in _GENERIC_ALERT_PHRASES)


def build_ai_analysis(rec, row, ec_superseded=False, pos=None, amz_catalog=None):
    """Build the AI Analysis narrative as rich-text HTML.

    rec: forecast record (forecast[26], manual[26], model, alert, pct_diff, ...)
    row: raw QB row (used only for L26W order history fields)
    ec_superseded: True when this acct-mstyle has an EC variant in the same account
    pos: POS dict for any customer (Amazon Catalog today; Walmart/Petsmart/Petco
         coming).  Keys: 'l4w','l13w','l26w','l52w','ordered_lw','ordered_prior_wk'
         or upstream-formatted 'Avg_Units_Wk_*' / 'Ordered_Units_*'.
    amz_catalog: Amazon Catalog US + Invtry Health merged dict for this mstyle.
         Keys used here: 'Inv_SOH', 'Inv_OPO', 'Inv_WOS',
         'AUR_L4w', 'AUR_L13w', 'AUR_L26w', 'AUR_L52w'.
    """
    from html import escape as _e
    MAX_BULLETS = 5  # POS + DC inv + AUR are 3 pinned; keep 2 slots for specific/critical
    _cust_label = _friendly_cust_name(rec.get("cust") or "")
    is_apl      = APL_CUST_SUBSTR in (rec.get("cust") or "").upper()
    is_amazon   = (AMAZON_CUST_SUBSTR in (rec.get("cust") or "").upper()) and not is_apl

    forecast = list(rec.get("forecast") or [])
    manual   = list(rec.get("manual")   or [])
    while len(forecast) < 26: forecast.append(0)
    while len(manual)   < 26: manual.append(0)
    ai_total     = sum(forecast)
    manual_total = sum(manual)

    # L26W order history from raw row fields (Ord LW + Ord LW-1..Ord LW-25)
    hist = []
    for col in ORD_L26_COLS:  # already-computed list, oldest→newest
        try:
            hist.append(float(row.get(col) or 0))
        except Exception:
            hist.append(0.0)
    hist_total = sum(hist)

    import re as _re

    # Four priority buckets — filled in order until MAX_BULLETS is reached.
    critical    = []   # critical flags (G2, F70, EC, truncation) — always shown first
    specific    = []   # Non-obvious specific callouts (alert sentences, PO context, smart trend)
    gap_pill    = []   # Plan vs AI gap summary — only if >= 15% gap; lowest priority
    pinned_last = []   # Amazon POS Sales + DC Inv + AUR -- always the final 3 bullets (Amazon only)

    # ── Critical: Inactive-looking record warning (2026-05-21) ───────────────
    # When a record has no manual projections, no recent/future POG launch
    # date, and no "NEW" in Status_Cust, it is almost certainly an abandoned
    # distribution slot that should be closed rather than forecasted.  Surface
    # a red hazard warning at the very top of the analysis so planners don't
    # overlook it while reviewing the queue.
    #
    # "Recent" POG = launched within the last 26 weeks (still ramping up).
    # "Future" POG = launch date is after today.
    # Either exempts the record from this warning.
    _inactive_warn_status = str(rec.get("status_cust") or "").upper()
    _inactive_warn_pog    = str(rec.get("pog_launch") or "").strip()
    _pog_recent_or_future = False
    if _inactive_warn_pog:
        try:
            from datetime import date as _dt_iw, timedelta as _td_iw
            _pog_iw   = _dt_iw.fromisoformat(_inactive_warn_pog[:10])
            _today_iw = _dt_iw.today()
            _pog_recent_or_future = _pog_iw >= (_today_iw - _td_iw(weeks=26))
        except Exception:
            pass
    if (manual_total == 0
            and not _pog_recent_or_future
            and "NEW" not in _inactive_warn_status):
        critical.append(
            '<span style="color:#dc2626;font-weight:700;">&#9888; LOOKS INACTIVE</span>'
            ' <span style="color:#991b1b;">'
            'No manual projections entered, no active or upcoming POG, and status '
            'does not indicate a new item. This record should likely be closed.'
            '</span>'
        )

    # ── Critical: F70 Switchover variant conflict ─────────────────────────────
    # When a variant style (EC/COS/AMZ/...) at the same account has demand in
    # specific weeks, prepend a top-of-analysis bullet explaining the switchover
    # so the planner understands why those weeks show 0 in the AI forecast.
    # Uses f70_switchover (all conflict weeks) for the "as of" week, and
    # f70_zeroed_weeks (only weeks actually zeroed) for the action statement.
    _f70 = rec.get("f70_switchover") or {}
    _f70_zeroed = rec.get("f70_zeroed_weeks") or {}
    _f70_protected = rec.get("f70_planner_protected", False)
    if _f70:
        _f70_variants = sorted({v for vl in _f70.values() for v in vl})
        _f70_weeks    = sorted(_f70.keys())
        _f70_first_wk = _f70_weeks[0] + 1   # 1-indexed
        _f70_var_str  = ", ".join(_f70_variants)
        # Action statement reflects what actually changed in the AI
        if _f70_protected:
            _f70_action = (
                "AI was NOT auto-zeroed because the model is Pre-launch passthrough "
                "(planner manual is the only signal for unlaunched items)."
            )
        elif _f70_zeroed:
            _zw = sorted(_f70_zeroed.keys())
            if len(_zw) == 1:
                _zw_desc = f"W{_zw[0]+1}"
            elif _zw == list(range(_zw[0], _zw[-1] + 1)):
                _zw_desc = f"W{_zw[0]+1}-W{_zw[-1]+1}"
            else:
                _zw_desc = ", ".join(f"W{w+1}" for w in _zw[:6])
                if len(_zw) > 6:
                    _zw_desc += f" (+{len(_zw)-6} more)"
            _f70_action = f"AI projections zeroed for {_zw_desc} on this base style."
        else:
            _f70_action = (
                "AI was not changed because all conflict weeks were already 0 "
                "or protected by explicit Tell-AI override."
            )
        critical.insert(0,
            f"<b>Demand switched to {_e(_f70_var_str)}</b> as of W{_f70_first_wk} -- "
            f"{_f70_action} Consider marking those weeks CLOSED."
        )

    # ── Critical: G2 demotion -- active item zeroed by guards ────────────────
    # When G2 demotes a record to "Inactive (zeroed by guards)" every forecast
    # week is already covered by confirmed POs or buffers -- but if Status @ Cust
    # is still Active or orders landed this week, the planner needs to review
    # whether their manual plan and item status reflect reality.
    if rec.get("model") == "Inactive (zeroed by guards)":
        _g2_sc     = (rec.get("status_cust") or row.get("Status_Cust") or "").upper().strip()
        _g2_active = _g2_sc.startswith("A") and not _g2_sc.startswith("FD")
        _g2_man_zero = sum(manual) == 0
        _g2_ord_lw   = float(row.get("Ord_LW") or 0)
        _g2_parts    = []
        if _g2_active:
            # Flag regardless of whether manual is zero or not -- any G2 + Active
            # combination means the AI shows Inactive but the item is still live.
            if _g2_man_zero:
                _g2_parts.append(
                    "Manual PRJs are all zero but Status @ Cust is still Active -- "
                    "no manual demand is on file for this item."
                )
            else:
                _g2_parts.append(
                    "AI forecast is all zeros (guards covered demand) but Status @ "
                    "Cust is still Active -- confirm manual PRJ reflects current demand."
                )
        if _g2_ord_lw > 0:
            _g2_parts.append(
                f"Orders came in on this item this week ({_g2_ord_lw:,.0f} units) -- "
                f"this item is actively shipping."
            )
        if _g2_parts:
            _g2_body = " ".join(_g2_parts)
            critical.insert(0,
                f"<span style='color:#c62828'>"
                f"<b>&#9888; ACTION REQUIRED -- AI zeroed all 26 weeks "
                f"(confirmed POs or guards cover demand):</b> "
                f"{_g2_body} "
                f"Please validate item status and manual projections."
                f"</span>"
            )

    # ── Critical: EC supersession warning ────────────────────────────────────
    if ec_superseded:
        critical.append(
            f"<span style='color:#c62828'>! EC variant ({_e(rec.get('mstyle',''))}EC) "
            f"exists for this account - this parent SKU is being phased out. "
            f"AI forecast zeroed; verify in Quickbase before accepting.</span>"
        )

    # ── Critical: Unexplained planner truncation ─────────────────────────────
    # When the planner zeros out >= 6 consecutive tail weeks but Status @ Cust
    # is Active and no POG End Date exists, the planner knows something the AI
    # doesn't.  Surface this FIRST and insist on documentation.
    _sc = (row.get("Status_Cust") or "").upper().strip()
    _it = (row.get("PT_Item_Status") or "").upper().strip()
    _pe = (str(row.get("POG_End_Date") or "")).strip()
    _is_trunc_active  = _sc.startswith("A") and not _sc.startswith("FD") if _sc else False
    _is_trunc_eol     = any(tok in _it for tok in ("DISC", "PHASE", "EOL", "DELETE"))
    if _is_trunc_active and not _pe and not _is_trunc_eol and manual_total > 0:
        # Find where the planner's trailing zero block starts (scan backward)
        _trunc_idx = None
        for _w in range(25, -1, -1):
            if manual[_w] > 0:
                _trunc_idx = _w + 1   # 0-based index of first trailing zero
                break
        if _trunc_idx is None:
            _trunc_idx = 0
        _trunc_len    = 26 - _trunc_idx
        _trunc_ai_vol = sum(forecast[_trunc_idx:])
        if _trunc_len >= 6 and _trunc_ai_vol > 0:
            _trunc_wk1 = _trunc_idx + 1
            critical.insert(0,
                f"<b>Critical AI Flag:</b> The plan goes to zero at W{_trunc_wk1} "
                f"and stays flat through W26, but Status @ Cust is Active with no "
                f"POG End Date on file. The AI would forecast {_trunc_ai_vol:,}u "
                f"across those {_trunc_len} weeks based on buying history. If there "
                f"is an event driving this - POG ending, listing drop, distribution "
                f"cut, or seasonal exit - please document it: enter a POG End Date, "
                f"update the item status, or add a comment. Without context this "
                f"looks like missing demand and creates an inventory blind spot."
            )

    # ── Critical: Zero-history guard ─────────────────────────────────────────
    if hist_total == 0 and ai_total > 0:
        model_lbl = _e(rec.get("model", "model"))
        critical.append(
            f"<span style='color:#ef6c00'>! Zero L26W order history - AI projects "
            f"{ai_total:,}u ({model_lbl}). Verify item is actively shipping "
            f"before accepting.</span>"
        )

    # ── Specific: Non-generic alert sentences ────────────────────────────────
    # Sentence 0 is the vol/gap summary (redundant with gap_pill below).
    # Sentences 1+ may contain specific-week callouts, unusual patterns, or
    # account-level risks worth surfacing — but skip any that are generic
    # observations the planner can already see in the grid.
    base_alert = (rec.get("alert") or "").strip()
    if base_alert:
        sentences = [s.strip() for s in _re.split(r'(?<=[.!?])\s+', base_alert)
                     if s.strip()]
        for s in sentences[1:]:
            if not _is_generic_alert(s):
                specific.append(_e(s))

    # ── Specific: Confirmed-PO context ───────────────────────────────────────
    _po_zeroed_weeks = rec.get("po_zeroed_weeks") or []
    _po_total_qty    = float(rec.get("po_total_qty") or 0)
    if _po_zeroed_weeks and _po_total_qty > 0:
        _wk_str = ", ".join(f"W{w}" for w in _po_zeroed_weeks[:5])
        if len(_po_zeroed_weeks) > 5:
            _wk_str += f" +{len(_po_zeroed_weeks)-5} more"
        _total_demand = ai_total + _po_total_qty
        _vs_str = (f" ({((_total_demand / manual_total - 1) * 100):+.0f}% vs plan)"
                   if manual_total > 0 else "")
        specific.append(
            f"{_wk_str} zeroed - confirmed POs cover {int(_po_total_qty):,}u. "
            f"True demand = AI {ai_total:,} + POs {int(_po_total_qty):,} = "
            f"<b>{int(_total_demand):,}u</b>{_vs_str}."
        )

    # ── Specific: Smart trend insight ─────────────────────────────────────────
    # For POS-connected accounts: consumer velocity patterns (stocking-up,
    # acceleration, deceleration). For APL: B2B order activity bullet.
    # For all others: order-pattern anomalies from order history.
    if pos:
        ord_lw = float(pos.get("Ordered_Units_LW")       or pos.get("ordered_lw")       or 0)
        ord_pw = float(pos.get("Ordered_Units_Prior_Wk")  or pos.get("ordered_prior_wk")  or 0)
        l4  = float(pos.get("Avg_Units_Wk_L4w")  or pos.get("l4w")  or 0)
        l13 = float(pos.get("Avg_Units_Wk_L13w") or pos.get("l13w") or 0)
        l26 = float(pos.get("Avg_Units_Wk_L26w") or pos.get("l26w") or 0)
        l52 = float(pos.get("Avg_Units_Wk_L52w") or pos.get("l52w") or 0)

        if is_apl:
            # APL: no consumer POS; show B2B order fields from Amazon Catalog
            # as the POS-equivalent bullet, then fall through to Order Trends.
            specific.append(
                "<b>Amazon Private Label:</b> No consumer POS or DC inventory "
                "data available. Forecast uses order history + seasonal/category "
                "profiles."
            )
            if ord_lw > 0 or ord_pw > 0:
                _apl_parts = []
                if ord_lw > 0:
                    _apl_parts.append(f"LW {int(ord_lw):,}u")
                if ord_pw > 0:
                    _apl_parts.append(f"Prior Wk {int(ord_pw):,}u")
                pinned_last.append(
                    "<b>Amazon B2B Orders:</b> " + ", ".join(_apl_parts) + "."
                )
        else:
            l13_anomaly = (l13 == 0 and l4 > 0 and l26 > 0)
            # Always emit a compact POS run-rate line so planners see consumer
            # velocity even when the trend is flat (<10% change L4W vs L13W).
            # "Amazon POS Sales:" matches the viewer's idempotency check.
            if l4 > 0 or l13 > 0:
                _l13_display = ((l4 + l26) / 2.0) if l13_anomaly else l13
                _trend_ratio = (l4 / _l13_display) if _l13_display > 0 else 1.0
                _trend_lbl   = ("accel" if _trend_ratio >= 1.15
                                else "decel" if _trend_ratio <= 0.85
                                else "stable")
                _pos_parts = []
                if ord_lw > 0:
                    _pos_parts.append(f"LW {int(ord_lw):,}u")
                if l4 > 0:
                    _pos_parts.append(f"L4W {l4:.0f}/wk")
                if _l13_display > 0:
                    _pos_parts.append(f"L13W {_l13_display:.0f}/wk")
                if l26 > 0:
                    _pos_parts.append(f"L26W {l26:.0f}/wk")
                if l52 > 0:
                    _pos_parts.append(f"L52W {l52:.0f}/wk")
                if _pos_parts:
                    pinned_last.append(
                        f"<b>Amazon POS Sales:</b> "
                        + ", ".join(_pos_parts)
                        + f" ({_trend_lbl})."
                    )
            _smart = _smart_pos_trend(l4, l13, l26, l52,
                                      ord_lw=ord_lw, ord_pw=ord_pw,
                                      l13_anomaly=l13_anomaly,
                                      cust_label=_cust_label)
            if _smart:
                specific.append(_smart)

        # Order Trends bullet: B2B order history run-rate (all records with pos data).
        if len(hist) >= 4:
            _ly_hist_pos = rec.get("history_ly_ord") or []
            _smart_ord = _smart_order_trend(hist,
                                            ly_hist_26=_ly_hist_pos if _ly_hist_pos else None,
                                            cust_label=_cust_label)
            if _smart_ord:
                specific.append(_smart_ord)
    else:
        # No POS data at all.
        if is_amazon:
            critical.append(
                "Amazon POS / DC data not available for this mstyle "
                "(not found in Amazon Catalog). "
                "Forecast uses order history only -- "
                "verify item is set up in the Amazon Catalog table in QB."
            )
        if not is_amazon and len(hist) >= 4:
            _ly_hist = rec.get("history_ly_ord") or []
            _smart_ord = _smart_order_trend(hist,
                                            ly_hist_26=_ly_hist if _ly_hist else None,
                                            cust_label=_cust_label)
            if _smart_ord:
                specific.append(_smart_ord)

    # ── Specific: Amazon DC inventory health ─────────────────────────────────
    # Surface SOH, Open PO, and WOS so planners can see Amazon's actual DC
    # position alongside the forecast.  Colour-coded WOS for quick triage:
    #   < 3 wks  → red   (risk of OOS)
    #   3–7 wks  → amber (watch)
    #   8–15 wks → normal (no colour)
    #   ≥ 16 wks → orange (overstocked)
    if amz_catalog:
        _ih_soh = float(amz_catalog.get("Inv_SOH") or 0)
        _ih_opo = float(amz_catalog.get("Inv_OPO") or 0)
        _ih_wos = float(amz_catalog.get("Inv_WOS") or 0)
        if _ih_soh > 0 or _ih_opo > 0 or _ih_wos > 0:
            _ih_parts = []
            if _ih_soh > 0:
                _ih_parts.append(f"SOH {int(_ih_soh):,}u")
            if _ih_opo > 0:
                _ih_parts.append(f"Open PO {int(_ih_opo):,}u")
            if _ih_wos > 0:
                if _ih_wos < 3:
                    _wos_str = (f"<span style='color:#c62828'><b>WOS "
                                f"{_ih_wos:.1f}wks ⚠</b></span>")
                elif _ih_wos < 8:
                    _wos_str = (f"<span style='color:#e65100'>WOS "
                                f"{_ih_wos:.1f}wks</span>")
                elif _ih_wos >= 16:
                    _wos_str = (f"<span style='color:#f57f17'>WOS "
                                f"{_ih_wos:.1f}wks (overstocked)</span>")
                else:
                    _wos_str = f"WOS {_ih_wos:.1f}wks"
                _ih_parts.append(_wos_str)
            if _ih_parts:
                pinned_last.append(
                    f"<b>Amazon DC inventory:</b> " + " &nbsp;&middot;&nbsp; ".join(_ih_parts) + "."
                )
        else:
            # amz_catalog exists but all SOH/OPO/WOS fields are zero or missing.
            # This can happen when Amazon_Invtry_Health had no row for this ASIN.
            # Fix 3 (2026-05-24): surface a note so planners know the data gap.
            if is_amazon and (rec.get("model") or "").strip() not in ("Inactive",):
                pinned_last.append(
                    "<b>Amazon DC inventory:</b> data not available in health feed "
                    "-- WOS-based adjustments skipped."
                )
    elif is_amazon and (rec.get("model") or "").strip() not in ("Inactive",):
        # Fix 3 (2026-05-24): no amz_catalog entry at all for this mstyle/ASIN.
        # Tell the planner explicitly so they know DC position is unknown.
        pinned_last.append(
            "<b>Amazon DC inventory:</b> data unavailable for this ASIN "
            "-- WOS-based adjustments skipped."
        )

    # ── Amazon AUR (Average Unit Revenue) ─────────────────────────────────────
    # Pinned last so planners see pricing context on every Amazon record.
    # Python writes L4W / L13W / L26W / L52W (LW is computed live by codepage JS).
    if is_amazon and amz_catalog:
        _aur_l4  = float(amz_catalog.get("AUR_L4w")  or 0)
        _aur_l13 = float(amz_catalog.get("AUR_L13w") or 0)
        _aur_l26 = float(amz_catalog.get("AUR_L26w") or 0)
        _aur_l52 = float(amz_catalog.get("AUR_L52w") or 0)
        # Interpolate L13W from L4W + L26W when catalog value is missing
        # (same logic as codepage JS fallback).
        if _aur_l13 == 0 and _aur_l4 > 0 and _aur_l26 > 0:
            _aur_l13 = (_aur_l4 + _aur_l26) / 2.0
        _aur_parts = []
        if _aur_l4  > 0: _aur_parts.append(f"<b>L4W avg</b> ${_aur_l4:.2f}")
        if _aur_l13 > 0: _aur_parts.append(f"<b>L13W avg</b> ${_aur_l13:.2f}")
        if _aur_l26 > 0: _aur_parts.append(f"<b>L26W avg</b> ${_aur_l26:.2f}")
        if _aur_l52 > 0: _aur_parts.append(f"<b>L52W avg</b> ${_aur_l52:.2f}")
        if _aur_parts:
            pinned_last.append(
                "<b>Amazon AUR:</b> " + " &nbsp;|&nbsp; ".join(_aur_parts) + "."
            )

    # ── Gap pill: Plan vs AI summary ──────────────────────────────────────────
    # Only surfaced when the gap is ≥ 15% (enough to warrant a review) or when
    # there is no manual plan at all. It's the lowest-priority bullet because
    # the delta is visible in the grid — we only want it here when it's large
    # enough that a planner might miss it scanning the row.
    if ai_total > 0 or manual_total > 0:
        ai_wk  = round(ai_total  / 26.0)
        man_wk = round(manual_total / 26.0)
        if manual_total > 0:
            gap_pct  = (ai_total - manual_total) / manual_total * 100.0
            gap_abs  = abs(gap_pct)
            if gap_pct < -1:
                gap_str = f"plan is <b>{gap_abs:.0f}% above AI</b>"
            elif gap_pct > 1:
                gap_str = f"plan is <b>{gap_abs:.0f}% below AI</b>"
            else:
                gap_str = "plan matches AI"
            if gap_abs >= 15:
                gap_pill.append(
                    f"AI {ai_wk:,}/wk ({ai_total:,} total 26W) | "
                    f"Plan {man_wk:,}/wk ({manual_total:,} total) - {gap_str}."
                )
        else:
            gap_pill.append(
                f"AI {ai_wk:,}/wk ({ai_total:,} total 26W) - no manual plan entered."
            )

    # ── Assemble in priority order: critical → specific → gap_pill → pinned ──
    # pinned_last (Amazon POS Sales, DC Inv, AUR) always anchor the last positions
    # so planners see a consistent layout on every Amazon record.
    parts = critical[:]                                 # always shown, no cap
    _pinned_count = len(pinned_last)
    remaining = MAX_BULLETS - len(parts) - _pinned_count
    parts.extend(specific[:max(0, remaining)])
    remaining = MAX_BULLETS - len(parts) - _pinned_count
    if remaining > 0:
        parts.extend(gap_pill[:remaining])
    parts.extend(pinned_last)                           # always last

    if not parts:
        return ""
    # Join paragraphs with <br><br> for QB rich-text display
    return _sanitize_for_qb("<br><br>".join(parts))


# ─── QB write-back charset sanitizer ──────────────────────────────────────────
#
# QB's rich-text storage + the codepage's downstream rendering occasionally
# round-trip Unicode punctuation through Windows-1252, which corrupts em dashes
# and curly quotes into mojibake (e.g. "—" rendered as "â€"").  We avoid the
# entire round-trip by replacing the handful of characters we actually emit
# with ASCII equivalents before the bulk REST upsert.  Keeps the narrative
# readable in every environment (QB report grid, codepage viewer, CSV exports,
# Outlook preview).

_QB_CHAR_MAP = {
    "—": " - ",   # — em dash
    "–": "-",     # – en dash
    "−": "-",     # − minus sign
    "‘": "'",     # ' left single
    "’": "'",     # ' right single / apostrophe
    "“": '"',     # " left double
    "”": '"',     # " right double
    "…": "...",   # … ellipsis
    " ": " ",     # non-breaking space
    "•": "*",     # • bullet
    "⚠": "!",     # ⚠ warning sign (keep readable, drop the glyph)
    "→": "->",    # right arrow
    "←": "<-",    # left arrow
    "±": "+/-",   # plus-minus
    "×": "x",     # multiplication sign
    "≥": ">=",    # greater or equal
    "≤": "<=",    # less or equal
    "≈": "~",     # approximately equal
    "·": "-",     # middle dot
    "′": "'",     # prime
    "″": '"',     # double prime
    "≠": "!=",    # not equal
    "÷": "/",     # division
    "Δ": "d",     # delta
    "α": "a",     # alpha
    "β": "b",     # beta
    "☢": "!!",    # radioactive (CRITICAL labels)
    "☑": "[x]",   # checked
    "☐": "[ ]",   # unchecked
}

def _sanitize_for_qb(text):
    """Replace Unicode punctuation with ASCII equivalents so QB's rich-text
    storage never produces mojibake on the codepage viewer.  Safe to call on
    plain strings or HTML-bearing rich-text alike — only the punctuation map
    above is touched, all other characters pass through unchanged.
    """
    if not text:
        return text
    s = str(text)
    for k, v in _QB_CHAR_MAP.items():
        if k in s:
            s = s.replace(k, v)
    return s


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global _total_wb

    p = argparse.ArgumentParser(description="Inventory Demand Forecaster — Pets+People")
    p.add_argument("--acct",          help="Filter by account number prefix, comma-separated (e.g. 1864 or 1864,20006)")
    p.add_argument("--customer",      help="Filter by customer name substring")
    p.add_argument("--mstyle",        help="Filter by mstyle, comma-separated (e.g. FF8654 or FF8654,FF10159)")
    p.add_argument("--brand",         help="Filter by Master_Brand in Styles")
    p.add_argument("--all",           action="store_true", help="All active records")
    p.add_argument("--keys",          help="Comma-separated list of Acct_MStyle_Key_ values to target")
    p.add_argument("--workers",       type=int, default=-1,
                   help="Parallel write workers (default: 2 for --all, else 6)")
    p.add_argument("--rate-limit-ms", type=int, default=-1, dest="rate_limit_ms",
                   help="Throttle: ms to sleep between QB writes (default: 150 for "
                        "--all, else 0). Only applies to non-bulk path.")
    p.add_argument("--bulk-writeback",   dest="bulk_writeback", action="store_true",  default=None,
                   help="Use direct QB REST /v1/records bulk upsert (~50× fewer "
                        "QB API hits). Default: ON for --all scope, OFF otherwise.")
    p.add_argument("--no-bulk-writeback", dest="bulk_writeback", action="store_false",
                   help="Force per-record SQL UPDATE writeback (legacy path).")
    p.add_argument("--dry-run",       action="store_true", help="Forecast only, no write-back")
    p.add_argument("--pipeline",      action="store_true",
                   help="Route through the new explicit-phase pipeline (scripts/pipeline.py). "
                        "Currently delegates Phases C-F to the legacy code path; only "
                        "Phase A/B/G are fully lifted. Use for A/B testing.")
    p.add_argument("--analyze",       action="store_true", help="Run EDA and generate HTML report")
    p.add_argument("--analyze-only",  action="store_true", help="Run EDA only, skip forecasting")
    p.add_argument("--validate",      action="store_true", help="Validate manual projections against history")
    p.add_argument("--push-validation", action="store_true", help="After --validate, push results to QB Validation_* fields")
    p.add_argument("--no-validation-flags", action="store_true",
                   help="Skip the per-week validation flags that forecast mode "
                        "now generates by default (2026-05-06).  By default, every "
                        "forecast run also computes validation flags on the manual "
                        "projection and merges them into forecast_results.json under "
                        "each record's 'validation' field.")
    p.add_argument("--allow-stale-cache", action="store_true",
                   help="Allow the 24-hour open-PO disk cache to be reused on this "
                        "run.  By default (2026-05-06), forecast runs ignore the cache "
                        "and re-fetch all QB data fresh — guarantees no stale POs / "
                        "history.  Pass this flag for quick re-runs where you don't "
                        "need the latest QB state.")
    p.add_argument("--no-conservative-inactive", action="store_true",
                   help="Disable F19 inactive floor (which is on-by-default since "
                        "2026-05-06).  When set, items classified Inactive get a "
                        "zero forecast even if planner has a large manual projection.")
    p.add_argument("--threshold",     type=float, default=VALID_HIGH_MULT,
                   help=f"Validation high-deviation multiplier (default: {VALID_HIGH_MULT})")
    p.add_argument("--resume",        help="Path to completed-keys JSON — skips already written")
    p.add_argument("--out",           default="forecast_results.json", help="Forecast output JSON")
    p.add_argument("--report",        default="forecast_report.html",  help="HTML report path")
    p.add_argument("--oos-smoothing", action="store_true",
                   help="VP-Q2: pull Order_History per-week cancellations and "
                        "reconstruct clean demand (excludes Bucket-B customer "
                        "errors / Future-Delete / Low-Margin cancels) before "
                        "forecasting; also neutralizes compounding catch-up.")
    p.add_argument("--no-po-zero", action="store_true",
                   help="VP-Q4: disable zeroing AI_PRJ in weeks where a "
                        "confirmed customer PO already exists (default: enabled). "
                        "Use this flag to opt out for testing.")
    p.add_argument("--conservative-inactive", action="store_true",
                   help="F19: for items classified Inactive with large manual projections "
                        "(≥5k) and non-zero POS L52, set forecast to 50%% of manual total "
                        "shaped to manual curve (capped at POS L52 × 26).")
    args = p.parse_args()

    # ── Auto-resolve concurrency / throttle / bulk-mode defaults based on scope.
    # Wide --all runs trip QB's per-realm rate limit at high concurrency, so we
    # back off automatically.  Explicit user-passed values always win.
    _is_wide_scope = bool(getattr(args, "all", False))
    if args.workers < 0:
        args.workers = 2 if _is_wide_scope else 6
    if args.rate_limit_ms < 0:
        args.rate_limit_ms = 150 if _is_wide_scope else 0
    if args.bulk_writeback is None:
        args.bulk_writeback = _is_wide_scope    # True for --all, False otherwise
    if _is_wide_scope:
        print(f"  [auto-throttle] --all detected → workers={args.workers}, "
              f"rate_limit_ms={args.rate_limit_ms}, "
              f"bulk_writeback={'on' if args.bulk_writeback else 'off'}")

    # F19 — expose flag globally so forecast_record can consult it.
    # 2026-05-06: F19 is now ON by default (CONSERVATIVE_INACTIVE = True at
    # module load); this line lets users explicitly opt OUT via env var or
    # by-extension a future --no-conservative-inactive flag.  The current
    # --conservative-inactive flag remains as a no-op opt-in marker.
    global CONSERVATIVE_INACTIVE
    if getattr(args, "no_conservative_inactive", False):
        CONSERVATIVE_INACTIVE = False
    elif getattr(args, "conservative_inactive", False):
        CONSERVATIVE_INACTIVE = True
    # else: keep the module-level default (True since 2026-05-06)

    # Fresh-data mode (2026-05-06): forecast runs by default re-fetch all QB
    # data without using disk caches.  The 24-hour open-PO disk cache is
    # bypassed unless --allow-stale-cache is passed.  In-memory derived /
    # field-map caches are also reset so a long-running interactive session
    # gets fresh data per run.
    if not getattr(args, "allow_stale_cache", False):
        # Bypass open-PO disk cache by setting TTL to 0 in oos_history (which
        # imported the constant at module load — patch the imported symbol).
        try:
            import oos_history
            oos_history.QB_OPEN_POS_CACHE_HOURS = 0
        except Exception:
            pass
        # Reset in-memory caches that could survive across interactive runs.
        global _DERIVED_CACHE
        _DERIVED_CACHE = None
        _QB_FIELD_MAP_CACHE.clear()
        print("  [fresh-data] disk cache bypassed; all QB data will be "
              "re-fetched on this run (use --allow-stale-cache to override)")

    # Interactive mode menu when no mode flag is passed
    if (not any([args.analyze, args.analyze_only, args.validate, args.dry_run])
            and sys.stdin.isatty()):
        print("\n  Select mode:")
        print("    1) Forecast               \u2014 run AI projections and write back")
        print("    2) Validate Projections   \u2014 check manual projections for anomalies")
        print("    3) Analyze Only           \u2014 run EDA report, no forecasting")
        print()
        # Default to mode 1 (Forecast) on EOF \u2014 happens when stdin is non-
        # interactive (background invocation, piped from null, CI, etc.).
        # Without this fallback, --all from a background shell crashes with
        # EOFError before reaching write-back (2026-05-07 incident).
        try:
            choice = input("  Enter choice [1]: ").strip()
        except EOFError:
            choice = ""
            print("  [non-interactive] defaulting to Forecast mode")
        if choice == "2":
            args.validate = True
        elif choice == "3":
            args.analyze_only = True

    if not any([args.acct, args.customer, args.mstyle, args.brand, args.all, args.keys]):
        p.error("Specify a scope: --acct, --customer, --mstyle, --brand, --keys, or --all")

    print("\n" + "=" * 66)
    print("  Inventory Forecaster — Pets+People")
    print("=" * 66)

    # Auto-discover the 26 date-stamped projection columns from QB schema
    global ORIG_PRJ_COLS, _EVENT_BOOSTS_CACHE, _T5_SEASONAL_BOOSTS_CACHE
    ORIG_PRJ_COLS = _discover_prj_cols()
    _EVENT_BOOSTS_CACHE = None      # invalidate so _get_event_boosts() recomputes from real W1 date
    _T5_SEASONAL_BOOSTS_CACHE = {}  # invalidate T5/seasonal boosts for same reason
    print(f"  Manual projection columns: {ORIG_PRJ_COLS[0]} -> {ORIG_PRJ_COLS[-1]}")

    # ── Resolve brand filter to mstyle list ───────────────────────
    if args.brand:
        b = args.brand.replace("'", "''")
        brand_rows = cdata_query(
            f"SELECT [Mstyle] FROM [Quickbase1].[ProductTrack].[Styles] WHERE [Master_Brand] = '{b}'",
            "brand_mstyles")
        args._brand_mstyles = [r["Mstyle"] for r in brand_rows if r.get("Mstyle")]
        if not args._brand_mstyles:
            sys.exit(f"ERROR: No mstyles found for brand '{args.brand}'.")
        print(f"      Brand '{args.brand}': {len(args._brand_mstyles)} mstyles")
    else:
        args._brand_mstyles = []

    scope_parts = []
    if args.acct:     scope_parts.append(f"acct={args.acct}")
    if args.customer: scope_parts.append(f"customer={args.customer}")
    if args.mstyle:   scope_parts.append(f"mstyle={args.mstyle}")
    if args.brand:    scope_parts.append(f"brand={args.brand}")
    if args.all:      scope_parts.append("all active")
    scope_desc = " | ".join(scope_parts) if scope_parts else "all active"

    # ── Phase 1: Pull projection records ──────────────────────────
    scope_filter = build_scope_filter(args)
    sql = build_prj_select(ORIG_PRJ_COLS)
    if scope_filter:
        sql += f"\nAND {scope_filter}"

    print(f"\n[1/4] Pulling projections from Quickbase ...", flush=True)
    raw_rows = cdata_query(sql, "projections")
    if not raw_rows:
        sys.exit("ERROR: No records returned. Check scope filters and CData connection.")

    rows = [{k: clean_html(v) for k, v in r.items()} for r in raw_rows]
    print(f"      {len(rows)} records retrieved", flush=True)

    # ── Phase 2: Pull master pack + Season ─────────────────────────
    print(f"\n[2/4] Pulling master pack + Season from Styles ...", flush=True)
    # Filter to only mstyles in our projection rows to avoid large response timeouts
    mstyles_needed = list({r["Mstyle"] for r in rows if r.get("Mstyle")})
    master_pack = {}
    season_map  = {}   # Mstyle -> Season string (or missing if null/blank)
    BATCH = 200
    for i in range(0, len(mstyles_needed), BATCH):
        batch = mstyles_needed[i:i + BATCH]
        in_clause = ", ".join(f"'{m}'" for m in batch)
        mp_rows = cdata_query(
            f"SELECT [Mstyle], [Master_Pack], [Season] FROM [Quickbase1].[ProductTrack].[Styles] WHERE [Mstyle] IN ({in_clause})",
            f"master_pack batch {i//BATCH + 1}")
        for r in mp_rows:
            if r.get("Mstyle"):
                master_pack[r["Mstyle"]] = float(r.get("Master_Pack") or 1)
                _sv = (r.get("Season") or "").strip()
                if _sv:
                    season_map[r["Mstyle"]] = _sv
    print(f"      {len(master_pack)} master pack records loaded "
          f"({len(season_map)} with Season tag)")

    # ── Phase 2.5: Pull Amazon Catalog POS data (Amazon items only) ──
    amazon_pos = {}
    # EC/COS items (e.g. "FF12302/24EC") have POS data stored under the parent
    # mstyle ("FF12302/24") in the Amazon Catalog table.  Build a query set that
    # includes both the raw mstyle AND its parent variant so the WHERE IN clause
    # covers both cases.  The downstream fallback lookup (_ec_parent) then finds
    # the data via the parent key.
    def _ec_parent_for_query(ms):
        msu = ms.upper()
        if msu.endswith("EC"):
            return ms[:-2]
        if msu.endswith("COS"):
            return ms[:-3]
        if msu.endswith("AMZ"):
            return ms[:-3]
        return ms

    _amz_raw = {r["Mstyle"] for r in rows
                if AMAZON_CUST_SUBSTR in (r.get("Customr_Name") or "").upper()
                and r.get("Mstyle")}
    amazon_mstyles = list(_amz_raw | {_ec_parent_for_query(m) for m in _amz_raw})
    if amazon_mstyles:
        print(f"\n[2.5] Pulling Amazon catalog POS for {len(amazon_mstyles)} mstyles ...", flush=True)
        POS_COLS = ["Mstyle", "Ordered_Units_LW", "Ordered_Units_Prior_Wk",
                    "Avg_Units_Wk_L4w", "Avg_Units_Wk_L13w",
                    "Avg_Units_Wk_L26w", "Avg_Units_Wk_L52w"]
        pos_sel = ", ".join(f"[{c}]" for c in POS_COLS)
        BATCH = 200
        for i in range(0, len(amazon_mstyles), BATCH):
            batch     = amazon_mstyles[i:i + BATCH]
            in_clause = ", ".join(f"'{m}'" for m in batch)
            pos_rows  = cdata_query(
                f"SELECT {pos_sel} FROM [Quickbase1].[InventoryTrack].[Amazon_Catalog]"
                f" WHERE [Mstyle] IN ({in_clause})",
                f"amazon_pos batch {i // BATCH + 1}")
            for r in pos_rows:
                if r.get("Mstyle"):
                    amazon_pos[r["Mstyle"]] = r
        print(f"      {len(amazon_pos)} mstyles with POS data loaded")
        # Write a viewer-friendly POS cache (lowercase short keys) so the
        # viewer always sees fresh POS data on next launch.  Includes
        # ordered_lw, ordered_prior_wk, l4w, l13w, l26w, l52w (added
        # 2026-05-08 — was previously read-only and got stale).
        try:
            from pathlib import Path as _Path
            _viewer_pos_cache = {}
            for _mst, _r in amazon_pos.items():
                _viewer_pos_cache[_mst] = {
                    "ordered_lw":       float(_r.get("Ordered_Units_LW")      or 0),
                    "ordered_prior_wk": float(_r.get("Ordered_Units_Prior_Wk") or 0),
                    "l4w":              float(_r.get("Avg_Units_Wk_L4w")  or 0),
                    "l13w":             float(_r.get("Avg_Units_Wk_L13w") or 0),
                    "l26w":             float(_r.get("Avg_Units_Wk_L26w") or 0),
                    "l52w":             float(_r.get("Avg_Units_Wk_L52w") or 0),
                }
            _vp_path = _Path(__file__).parent.parent / "viewer_pos_cache.json"
            json.dump(_viewer_pos_cache, open(_vp_path, "w"))
            print(f"      viewer_pos_cache.json refreshed ({len(_viewer_pos_cache)} mstyles)")
        except Exception as _e:
            print(f"      [WARN] could not refresh viewer_pos_cache: {_e}")

    # ── Phase 2.6: Pull Amazon Catalog US (price + stock signals) ──
    # F38 inputs: Buybox, MAP, AUR L4w, OOS days, sellable inventory, buyability flag
    # Only fetched for Amazon records; keyed by Mstyle_model_ which equals the
    # InventoryTrack Mstyle.
    amazon_catalog_us = {}
    if amazon_mstyles:
        print(f"\n[2.6] Pulling Amazon Catalog US (F38 signals) for "
              f"{len(amazon_mstyles)} mstyles ...", flush=True)
        AMZUS_COLS = ["Mstyle_model_", "Amazon_Buybox", "MAP_Price",
                      "AUR_L4w", "AUR_L13w", "AUR_L26w", "AUR_L52w",
                      "Days_Amazon_OOS_L30d_", "Sellable_On_Hand_Units",
                      "ASIN_Buyability_Flag", "ASIN", "ASIN_Status"]
        amzus_sel = ", ".join(f"[{c}]" for c in AMZUS_COLS)
        BATCH_AMZUS = 200
        for i in range(0, len(amazon_mstyles), BATCH_AMZUS):
            batch     = amazon_mstyles[i:i + BATCH_AMZUS]
            in_clause = ", ".join(f"'{m}'" for m in batch)
            amzus_rows = cdata_query(
                f"SELECT {amzus_sel} FROM [Quickbase1].[ProductTrack].[Amazon_Catalog_US]"
                f" WHERE [Mstyle_model_] IN ({in_clause})",
                f"amazon_catalog_us batch {i // BATCH_AMZUS + 1}")
            for r in amzus_rows:
                _key = r.get("Mstyle_model_")
                if _key:
                    amazon_catalog_us[_key] = r
        print(f"      {len(amazon_catalog_us)} mstyles with Amazon Catalog US "
              f"signals loaded")

    # ── Phase 2.6b: Amazon Inventory Health (SOH, OPO, WOS) ──────────
    # Fetch Sellable On-Hand, Open PO Quantity, and Weeks-of-Supply from
    # the Amazon_Invtry_Health table in ProductTrack and merge into
    # amazon_catalog_us so forecast_record() and build_ai_analysis() can
    # use them for balancing projections against Amazon's actual DC position.
    # Join path: Amazon_Catalog_US.[ASIN] → Amazon_Invtry_Health.[ASIN]
    if amazon_catalog_us:
        _asin_to_ms = {}
        for _ms, _rec in amazon_catalog_us.items():
            _asin = (_rec.get("ASIN") or "").strip()
            if _asin:
                _asin_to_ms[_asin] = _ms
        if _asin_to_ms:
            print(f"\n[2.6b] Pulling Amazon Inventory Health for "
                  f"{len(_asin_to_ms)} ASINs ...", flush=True)
            IH_COLS = ["ASIN", "Sellable_On_Hand_Units",
                       "Open_Purchase_Order_Quantity", "WOS_OH"]
            ih_sel    = ", ".join(f"[{c}]" for c in IH_COLS)
            IH_BATCH  = 200
            _n_ih     = 0
            _asins    = list(_asin_to_ms.keys())
            for i in range(0, len(_asins), IH_BATCH):
                _batch     = _asins[i:i + IH_BATCH]
                _in_clause = ", ".join(f"'{a}'" for a in _batch)
                ih_rows    = cdata_query(
                    f"SELECT {ih_sel} "
                    f"FROM [Quickbase1].[ProductTrack].[Amazon_Invtry_Health] "
                    f"WHERE [ASIN] IN ({_in_clause})",
                    f"inv_health batch {i // IH_BATCH + 1}")
                for _r in ih_rows:
                    _a  = (_r.get("ASIN") or "").strip()
                    _ms = _asin_to_ms.get(_a)
                    if _ms and _ms in amazon_catalog_us:
                        amazon_catalog_us[_ms]["Inv_SOH"] = float(
                            _r.get("Sellable_On_Hand_Units") or 0)
                        amazon_catalog_us[_ms]["Inv_OPO"] = float(
                            _r.get("Open_Purchase_Order_Quantity") or 0)
                        amazon_catalog_us[_ms]["Inv_WOS"] = float(
                            _r.get("WOS_OH") or 0)
                        _n_ih += 1
            print(f"      {_n_ih} mstyles enriched with DC inventory health data")
        else:
            print(f"\n[2.6b] Amazon Inventory Health skipped "
                  f"(no ASINs in Catalog US -- field may not exist in that table)")

    # ── Phase 2.6c: Retailer POS + OH data (non-Amazon customers) ──────
    # Fetch consumer POS sell-through and retailer on-hand inventory from
    # the Retailer Sales table (bv2izcn5b) for all non-Amazon projection
    # records.  The same pos_data mechanism used for Amazon (F15 blend,
    # F18 Croston z-adjustment) is reused; the retailer WOS rule (F_RTL_WOS)
    # is then applied post-model in forecast_record().
    retailer_pos = {}
    _rtl_rows = [r for r in rows if AMAZON_CUST_SUBSTR not in
                 (r.get("Customr_Name") or "").upper()]
    if _rtl_rows:
        print(f"\n[2.6c] Retailer POS + OH: fetching for "
              f"{len({r.get('Mstyle') for r in _rtl_rows})} non-Amazon mstyles ...",
              flush=True)
        try:
            retailer_pos = _fetch_retailer_pos(_rtl_rows)
            print(f"      {len(retailer_pos)} acct-mstyle combos with retailer POS data")
        except Exception as _e:
            print(f"      [WARN] retailer_pos fetch failed: {_e} -- "
                  f"F15 POS blend and F_RTL_WOS disabled this run", flush=True)
    else:
        print(f"\n[2.6c] Retailer POS skipped (no non-Amazon records in scope)",
              flush=True)
        retailer_pos = {}

    # ── Save Amazon catalog viewer cache (AUR + DC inv for viewer.py) ──────────
    # Written after Phase 2.6b so Inv_SOH/OPO/WOS are already merged in.
    # viewer.py reads this at launch to add DC inv + AUR bullets to the narrative.
    if amazon_catalog_us:
        try:
            from pathlib import Path as _Path2
            _viewer_amz_cache = {}
            for _mst, _r in amazon_catalog_us.items():
                _viewer_amz_cache[_mst] = {
                    "aur_l4w":  float(_r.get("AUR_L4w")  or 0),
                    "aur_l13w": float(_r.get("AUR_L13w") or 0),
                    "aur_l26w": float(_r.get("AUR_L26w") or 0),
                    "aur_l52w": float(_r.get("AUR_L52w") or 0),
                    "inv_soh":  float(_r.get("Inv_SOH")  or 0),
                    "inv_opo":  float(_r.get("Inv_OPO")  or 0),
                    "inv_wos":  float(_r.get("Inv_WOS")  or 0),
                }
            _vamz_path = _Path2(__file__).parent.parent / "viewer_amz_cache.json"
            json.dump(_viewer_amz_cache, open(_vamz_path, "w"))
            print(f"      viewer_amz_cache.json refreshed ({len(_viewer_amz_cache)} mstyles)")
        except Exception as _e:
            print(f"      [WARN] could not refresh viewer_amz_cache: {_e}")

    # ── Phase 2.7: VP-Q2 OOS-aware demand reconstruction ────────────
    oos_data = {}
    if getattr(args, "oos_smoothing", False):
        from oos_history import fetch_clean_demand
        keys = [r.get("Acct_MStyle_Key_") for r in rows if r.get("Acct_MStyle_Key_")]
        print(f"\n[2.7] VP-Q2 OOS smoothing: reconstructing clean demand for {len(keys)} keys ...", flush=True)
        oos_data = fetch_clean_demand(keys)
        n_with_oos_week = sum(
            1 for e in oos_data.values()
            if any(s >= 0.15 for s in e["oos_severity"])
        )
        n_with_bucket_b = sum(
            1 for e in oos_data.values()
            if any(e["raw_ord"][w] > e["clean_ord"][w] for w in range(52))
        )
        print(f"      {len(oos_data)} keys with order history; "
              f"{n_with_oos_week} have ≥1 OOS week, "
              f"{n_with_bucket_b} have Bucket-B (demand-invalid) cancels")
        # Guard: abort if --oos-smoothing was requested but the pull came
        # back empty (transient CData IncompleteRead, expired PAT, etc.).
        # Continuing would silently fall back to raw-demand forecasting and
        # quietly invalidate the run.
        if len(oos_data) == 0:
            sys.exit(
                "\n[ABORT] --oos-smoothing requested but Order_History pull "
                "returned 0 keys.\n         Likely transient CData failure "
                "(check Phase 2.7 [FAIL] lines).\n         Refusing to "
                "forecast on empty OOS data — re-run the command."
            )

    # ── Phase 2.8: VP-Q4 forward-window confirmed-PO pull ───────────
    open_pos_data = {}
    if not getattr(args, "no_po_zero", False):
        from oos_history import fetch_open_pos_forward
        keys = [r.get("Acct_MStyle_Key_") for r in rows if r.get("Acct_MStyle_Key_")]
        # Decode W1_DATE from ORIG_PRJ_COLS[0] (e.g. "05_10_W1" -> 2026-05-10).
        # Passing this to fetch_open_pos_forward fixes VP-Q4's bucketing alignment:
        # cancel dates must be bucketed relative to the forecast grid's Sunday
        # anchor, not relative to today, otherwise a 1-week shift zeroes the
        # wrong forecast weeks when the run date != W1_DATE.
        _prj_w1_date = None
        if ORIG_PRJ_COLS:
            try:
                _col0 = ORIG_PRJ_COLS[0]            # "MM_DD_W1"
                _wm, _wd = int(_col0[0:2]), int(_col0[3:5])
                _prj_w1_date = date(date.today().year, _wm, _wd)
                # If the decoded date is more than 6 months in the past, it
                # likely wraps to the next calendar year (rare edge case).
                if (date.today() - _prj_w1_date).days > 180:
                    _prj_w1_date = date(date.today().year + 1, _wm, _wd)
            except Exception as _e:
                print(f"      [2.8] Warning: could not decode W1_DATE from "
                      f"'{ORIG_PRJ_COLS[0]}': {_e} — using today-relative bucketing")
        print(f"\n[2.8] VP-Q4 forward-PO zero: fetching confirmed open POs "
              f"in forward 26w window for {len(keys)} keys "
              f"(w1_date={_prj_w1_date}) ...", flush=True)
        open_pos_data = fetch_open_pos_forward(keys, w1_date=_prj_w1_date)
        total_open_qty = sum(sum(v) for v in open_pos_data.values())
        n_with_any_po = sum(1 for v in open_pos_data.values() if any(q > 0 for q in v))
        print(f"      {n_with_any_po} keys have confirmed forward POs "
              f"(total open qty: {total_open_qty:,.0f} units)")
        # Guard: abort if VP-Q4 is enabled but the underlying PO report
        # returned 0 rows (indicating a CData transient failure).
        # IMPORTANT: do NOT abort just because 0 of our specific keys matched --
        # that is a legitimate result for single-item runs or items with no POs.
        # Check the bulk report cache: if it has rows, the pull itself succeeded.
        if len(open_pos_data) == 0:
            try:
                from oos_history import _open_pos_cache_path as _po_cache_fn
                _po_cache = _po_cache_fn()
                import json as _json2
                _po_raw = _json2.load(open(_po_cache)) if _po_cache.exists() else []
                _po_report_ok = len(_po_raw) > 0
            except Exception:
                _po_report_ok = False
            if not _po_report_ok:
                sys.exit(
                    "\n[ABORT] VP-Q4 PO zero-out is enabled but open-PO pull "
                    "returned 0 rows.\n         Likely transient CData failure "
                    "(check Phase 2.8 [FAIL] lines).\n         Refusing to "
                    "forecast -- re-run the command, or pass --no-po-zero "
                    "to skip VP-Q4 intentionally."
                )

    # ── Phase 2.9: VP-ATS ATS inventory history ─────────────────────
    # Fetch Available-to-Sell (ATS) L26W data so the engine can distinguish
    # weeks where zero/short orders were inventory-constrained vs. genuine
    # demand absence.  Keyed by Mstyle (not acct-mstyle) since ATS is a
    # warehouse-level signal shared across all customers.  Non-fatal: a
    # failed fetch emits a warning and the pipeline runs without ATS.
    ats_data = {}
    _ats_mstyles = list({r.get("Mstyle") for r in rows if r.get("Mstyle")})
    print(f"\n[2.9] VP-ATS: fetching ATS L26W history for "
          f"{len(_ats_mstyles)} mstyles ...", flush=True)
    try:
        from oos_history import fetch_ats_history
        ats_data = fetch_ats_history(mstyle_set=_ats_mstyles)
        _n_ats_nz = sum(1 for v in ats_data.values() if any(x > 0 for x in v))
        print(f"      {len(ats_data)} mstyles loaded, "
              f"{_n_ats_nz} with at least one non-zero ATS week")
    except Exception as _e:
        print(f"      [WARN] ATS fetch failed: {_e} — VP-ATS disabled this run",
              flush=True)

    # F70 -- Switchover variant conflict index.
    # Built here (before both validate and forecast) so both passes can use it.
    # Identifies weeks where a variant style (EC/COS/AMZ/...) already has demand,
    # meaning the base style should not also have projections in those weeks.
    switchover_index, variant_zero_index = _build_switchover_index(rows)
    _sw_conflict_ct  = len(switchover_index)
    if _sw_conflict_ct:
        print(f"\n[F70] Switchover index: {_sw_conflict_ct} base style(s) "
              f"have active variant conflicts", flush=True)
    if variant_zero_index:
        print(f"         {len(variant_zero_index)} variant style(s) have base-territory "
              f"pre-switchover weeks to zero (F70b)", flush=True)

    # B8/S5 fix (2026-05-21) -- Pre-build mstyle-family, customer-baseline,
    # and account-cadence indexes BEFORE validation.  Previously these were
    # built only in Phase 3 (forecast) which meant validation-pass forecasts
    # used empty globals -> silently different AI projections vs the forecast
    # pass.  Built once and shared across both phases.
    global MSTYLE_FAMILY_INDEX, CUST_BASELINE_INDEX, GLOBAL_WK_RATE
    MSTYLE_FAMILY_INDEX = _build_mstyle_family_index(rows)
    CUST_BASELINE_INDEX, GLOBAL_WK_RATE = _build_cust_baseline_index(rows)
    acct_cadences = compute_account_cadences(rows)
    print(f"      Mstyle-family index: {len(MSTYLE_FAMILY_INDEX)} mstyles with active siblings")
    print(f"      Customer-baseline  : {len(CUST_BASELINE_INDEX)} customers  (global median wk-rate: {GLOBAL_WK_RATE:.1f})")
    print(f"      Account-cadence    : {len(acct_cadences)} accounts indexed")

    # ── Validate Projections (if requested) ─────────────────────────
    if args.validate:
        print(f"\n[3/3] Validating manual projections for {len(rows)} records ...", flush=True)
        t_val = time.time()
        val_results = run_validation(rows, master_pack, args, amazon_pos=amazon_pos,
                                     amazon_catalog_us=amazon_catalog_us,
                                     season_map=season_map, oos_data=oos_data,
                                     open_pos_data=open_pos_data,
                                     ats_data=ats_data,
                                     switchover_weeks=switchover_index,
                                     acct_cadences=acct_cadences)
        elapsed_val = time.time() - t_val
        print(f"      Validation complete in {elapsed_val:.1f}s")

        # Save validation JSON
        val_out_path = Path(args.out).parent / "validation_results.json"
        critical_ct = sum(1 for r in val_results if r["max_severity"] == "CRITICAL")
        warning_ct  = sum(1 for r in val_results if r["max_severity"] == "WARNING")
        clean_ct    = sum(1 for r in val_results if r["max_severity"] == "OK")
        pri_crit    = sum(1 for r in val_results if r["priority"] == "CRITICAL")
        pri_high    = sum(1 for r in val_results if r["priority"] == "HIGH")
        pri_mid     = sum(1 for r in val_results if r["priority"] == "MID")
        pri_low     = sum(1 for r in val_results if r["priority"] == "LOW")
        pri_onplan  = sum(1 for r in val_results if r["priority"] == "On-Plan")
        val_output = {
            "_schema_version": SCHEMA_VERSION,
            "meta": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "scope":        scope_desc,
                "prj_cols":     ORIG_PRJ_COLS,
                "thresholds":   {
                    "high":  args.threshold,
                    "low":   VALID_LOW_MULT,
                    "spike": VALID_SPIKE_MULT,
                },
            },
            "summary": {
                "total_records":     len(val_results),
                "records_with_flags": critical_ct + warning_ct,
                "critical_records":  critical_ct,
                "warning_records":   warning_ct,
                "clean_records":     clean_ct,
                "total_flags":       sum(r["n_flags"] for r in val_results),
                "priority_critical": pri_crit,
                "priority_high":     pri_high,
                "priority_mid":      pri_mid,
                "priority_low":      pri_low,
                "priority_on_plan":  pri_onplan,
            },
            "records": val_results,
        }
        with open(val_out_path, "w") as f:
            json.dump(val_output, f)
        print(f"      Saved \u2192 {val_out_path}")

        _print_validation_summary(val_results)

        # ── Push validation results back to QB (populates validation code page) ──
        if getattr(args, "push_validation", False):
            print(f"\n[4/3] Pushing validation results to Quickbase ({len(val_results)} records) ...", flush=True)
            t_push = time.time()
            ok_ct = fail_ct = 0
            for rec in val_results:
                sql = build_validation_update_sql(rec)
                if cdata_update(sql, rec["key"]):
                    ok_ct += 1
                else:
                    fail_ct += 1
                if (ok_ct + fail_ct) % 200 == 0:
                    print(f"      {ok_ct + fail_ct}/{len(val_results)} pushed ...")
            print(f"      Pushed {ok_ct} rows in {time.time() - t_push:.1f}s  "
                  f"({fail_ct} failed)")

        print(f"\n      Open viewer: python scripts/viewer.py --results {val_out_path.name}")
        # Fall through to forecast + writeback — validation and forecasting
        # are NOT mutually exclusive.  --validate adds the validation pass
        # before forecasting; it does not skip the forecast.

    # ── EDA analysis (optional) ────────────────────────────────────
    findings = None
    if args.analyze or args.analyze_only:
        print(f"\n[EDA] Running analysis on {len(rows)} records ...")
        t_eda = time.time()
        findings = run_eda(rows, master_pack)
        print(f"      EDA complete in {time.time() - t_eda:.1f}s")
        imm_sum = findings["intermittency_summary"]
        print(f"      Intermittency: Smooth={imm_sum.get('Smooth',0)} "
              f"Erratic={imm_sum.get('Erratic',0)} "
              f"Intermittent={imm_sum.get('Intermittent',0)} "
              f"Lumpy={imm_sum.get('Lumpy',0)}")
        print(f"      Outliers detected: {len(findings['outliers'])}")
        cal = findings["calendar"]
        print(f"      Calendar lifts -- Prime Day: {cal['prime_day_lift']:.2f}x  "
              f"Fall Prime Day: {cal['fall_deal_lift']:.2f}x")

    if args.analyze_only:
        # Write EDA-only report and exit
        rpt_path = Path(args.report)
        html = build_html_report(findings, scope_desc, results=None)
        with open(rpt_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n      Report saved → {rpt_path}")
        print("\n[analyze-only] Done. No forecasts run.")
        return

    # ── Phase 3: Forecast ──────────────────────────────────────────
    print(f"\n[3/4] Running forecasts ...", flush=True)
    t_fcst = time.time()

    # MSTYLE_FAMILY_INDEX, CUST_BASELINE_INDEX, GLOBAL_WK_RATE, acct_cadences
    # were pre-built ABOVE (before validation) so both passes share the same
    # references (B8/S5 fix 2026-05-21).  No rebuild here.

    # Pre-compute EC-supersession set: for every (acct, mstyle) where an EC
    # variant ({mstyle}EC) exists in the same account, the original parent SKU
    # is being phased out.  Used by build_ai_analysis() to surface the warning
    # in the AI Analysis narrative.
    ec_parents = set()
    acct_mstyles = {}
    for _row in rows:
        _k = _row.get("Acct_MStyle_Key_", "")
        if "-" not in _k:
            continue
        _acct, _ms = _k.split("-", 1)
        acct_mstyles.setdefault(_acct, set()).add(_ms)
    for _acct, _mss in acct_mstyles.items():
        for _ms in _mss:
            _ds_sfx = (2 if _ms.endswith("EC")
                       else 3 if (_ms.endswith("COS") or _ms.endswith("AMZ"))
                       else 0)
            if _ds_sfx and _ms[:-_ds_sfx] in _mss:
                ec_parents.add(f"{_acct}-{_ms[:-_ds_sfx]}")

    # F60 — EC-transition history inheritance (2026-05-15).
    # When an EC variant ({mstyle}EC) exists for the same account as the
    # original parent mstyle, the EC item is the same product prepped for
    # ecommerce fulfillment — consumer demand is identical.  If the EC
    # variant has sparse order history (<25% of parent L13W), it will
    # misclassify as Inactive or New/Sparse and receive a near-zero forecast.
    # Fix: copy the parent's order + shipment history columns directly into
    # the EC row so the forecaster sees the real demand signal, then tag
    # the row so the alert narrative explains why.
    #
    # Threshold: EC L13W total < 25% of parent L13W total (EC is new/sparse)
    # AND parent L13W total > 0 (parent has meaningful history to inherit).
    # Safe: row mutation is done before any parallel/thread work begins.
    print(f"\n[2.9] F60 — EC transition history inheritance ...", flush=True)
    row_by_key = {_r.get("Acct_MStyle_Key_", ""): _r for _r in rows}
    _f60_count = 0
    for _row in rows:
        _k   = _row.get("Acct_MStyle_Key_", "")
        _ms  = _row.get("Mstyle", "")
        _f60_sfx = (2 if _ms.endswith("EC")
                    else 3 if (_ms.endswith("COS") or _ms.endswith("AMZ"))
                    else 0)
        if not _f60_sfx or "-" not in _k:
            continue
        _parent_ms  = _ms[:-_f60_sfx]
        _acct_pfx   = _k.split("-", 1)[0]
        _parent_key = f"{_acct_pfx}-{_parent_ms}"
        _parent_row = row_by_key.get(_parent_key)
        if _parent_row is None:
            continue
        # Compare L13W totals to decide whether inheritance is warranted
        _ec_l13     = sum(float(_row.get(c) or 0)      for c in ORD_COLS[-13:])
        _par_l13    = sum(float(_parent_row.get(c) or 0) for c in ORD_COLS[-13:])
        if _par_l13 <= 0 or _ec_l13 >= _par_l13 * 0.25:
            # EC variant already has meaningful history — no inheritance needed
            continue
        # Copy full 52w order + shipment history from parent into EC row.
        # This lets get_history() / get_ship_history() see the real demand
        # signal without any changes to those functions.
        for _c in ORD_COLS:
            _row[_c] = _parent_row.get(_c, 0)
        for _c in SHP_COLS:
            _row[_c] = _parent_row.get(_c, 0)
        # Tag for alert/driver annotation in forecast_record()
        _row["_ec_transition"]   = True
        _row["_ec_parent_mstyle"] = _parent_ms
        _row["_ec_parent_key"]    = _parent_key
        _row["_ec_parent_l13"]    = _par_l13
        _row["_ec_orig_l13"]      = _ec_l13
        _f60_count += 1
    print(f"      {_f60_count} EC variants inherited parent history", flush=True)

    # F60-ATS (2026-05-24): propagate parent ATS + OOS data to EC variants.
    #
    # F60 copies ORD_COLS + SHP_COLS from parent into EC row so the forecaster
    # sees real demand.  But two downstream normalizers are keyed differently:
    #
    #   VP-ATS / VP-ATS-Catch: keyed by Mstyle in ats_data.
    #     Inventory History - Weekly has a record for "FF35147" (parent) but
    #     NOT "FF35147EC" (EC variant) -- ats_data.get("FF35147EC") = None.
    #     Result: normalize_ats_catchup_spikes() is silently skipped for all
    #     EC variants, leaving post-OOS catch-up spike weeks uncapped in L13W.
    #
    #   VP-Q2 OOS clean demand: keyed by Acct_MStyle_Key_ in oos_data.
    #     Order_History rows exist for "1864-FF35147" but not "1864-FF35147EC"
    #     -- oos_data.get("1864-FF35147EC") = None.
    #
    # Consequence: inflated catch-up orders remain in EC variant L13W -->
    # _rpl_ord_l13 and _rpl_var_ratios both inherit the contamination -->
    # demand baseline too high AND spurious spikes cycle into July / other
    # months (because the variability pattern replays the catch-up ratio).
    #
    # Fix: for each EC row that used F60 inheritance, copy parent's ATS and
    # OOS entries into the EC mstyle / EC key slots so both normalizers fire.
    _f60_ats_ct = 0
    _f60_oos_ct = 0
    for _row in rows:
        if not _row.get("_ec_transition"):
            continue
        _ec_ms   = _row.get("Mstyle", "")
        _ec_key  = _row.get("Acct_MStyle_Key_", "")
        _par_ms  = _row.get("_ec_parent_mstyle", "")
        _par_key = _row.get("_ec_parent_key", "")
        # ATS inheritance
        if _par_ms and ats_data:
            _ec_ats  = ats_data.get(_ec_ms)
            _par_ats = ats_data.get(_par_ms)
            if _par_ats and (not _ec_ats or sum(_ec_ats) == 0):
                ats_data[_ec_ms] = _par_ats
                _f60_ats_ct += 1
        # VP-Q2 OOS inheritance (when --oos-smoothing is active)
        if _par_key and oos_data:
            _ec_oos  = oos_data.get(_ec_key)
            _par_oos = oos_data.get(_par_key)
            if _par_oos and not _ec_oos:
                oos_data[_ec_key] = _par_oos
                _f60_oos_ct += 1
    if _f60_ats_ct or _f60_oos_ct:
        print(f"      [F60-ATS] {_f60_ats_ct} EC mstyles mapped to parent ATS; "
              f"{_f60_oos_ct} EC keys mapped to parent OOS data", flush=True)

    # ── Phase 2.9b: F69 — DI direct-import sibling history pull ─────────────
    # Amazon (and sometimes other customers) order product direct from P+P's
    # overseas factory — "Direct Import" (DI).  These variants share the base
    # mstyle but carry MPP or ADF suffix (e.g. 1864-FF8654MPP alongside
    # 1864-FF8654).  Amazon writes its own POs 35-65 days before factory
    # shipment (~10 weeks transit); P+P does not project for these.
    #
    # Because MPP/ADF have no Projections record, their orders only exist in
    # the Order History table.  They can be concurrent with warehouse orders —
    # in any given week Amazon may order via warehouse, DI, or both.
    #
    # Strategy: generate candidate sibling keys for every base row, query
    # Order History via fetch_clean_demand(), then accumulate raw_ord weekly
    # arrays into the base row's ORD_COLS in-place.  raw_ord[i] aligns 1:1
    # with ORD_COLS[i] (both oldest→newest, 52 slots).  The forecaster then
    # sees total demand (warehouse + factory-direct) without any changes to
    # model logic.
    _DI_SUFFIXES    = ("MPP", "ADF")
    _DI_IMPORT_ACCT = "61865"   # Amazon's DI (direct-import) account in Order History
    print(f"\n[2.9b] F69 — DI direct-import sibling history pull ...", flush=True)

    # Build candidate sibling keys for Amazon records only.
    # DI orders land under acct 61865 regardless of which Amazon acct placed them.
    _di_candidate_keys  = []
    _di_sib_to_base_row = {}   # sibling_key -> base row dict
    for _row in rows:
        _base_key = _row.get("Acct_MStyle_Key_", "")
        _ms       = (_row.get("Mstyle") or "").strip()
        _cust     = (_row.get("Customr_Name") or "").upper()
        if "-" not in _base_key or not _ms:
            continue
        if AMAZON_CUST_SUBSTR not in _cust:
            continue   # DI only applies to Amazon
        for _sfx in _DI_SUFFIXES:
            _sib_key = f"{_DI_IMPORT_ACCT}-{_ms}{_sfx}"
            _di_candidate_keys.append(_sib_key)
            _di_sib_to_base_row[_sib_key] = _row

    # Query Order History for all candidate sibling keys in one batched pull
    _di_oh = {}
    try:
        from oos_history import fetch_clean_demand as _fetch_clean_demand
        _di_oh = _fetch_clean_demand(_di_candidate_keys, verbose=False)
    except Exception as _e:
        print(f"      [WARN] F69 DI fetch failed: {_e} — DI blending disabled",
              flush=True)

    # Blend each found sibling's raw_ord into its base row's ORD_COLS.
    # Also detect DI cadence pause: Amazon orders on the 10th monthly with
    # 65-day lead time, so any PO placed up to ~9 weeks ago should already
    # be in Order History.  If DI has been silent for ≥ 6 weeks but had
    # prior history, Amazon has consciously skipped ≥ 1 monthly order window.
    # That silence is itself a suppression signal — tag the base row so
    # forecast_record() can dampen the near-term forecast proportionally.
    _f69_blend_count  = 0
    _f69_base_touched = set()
    for _sib_key, _oh in _di_oh.items():
        _base_row = _di_sib_to_base_row.get(_sib_key)
        if _base_row is None:
            continue
        _raw_ord = _oh.get("raw_ord", [0.0] * 52)   # 52 floats oldest→newest
        _sib_ms  = _sib_key.split("-", 1)[1] if "-" in _sib_key else _sib_key
        _sib_l13 = sum(_raw_ord[-13:])
        _sib_l4  = sum(_raw_ord[-4:])   # F69-shift: track L4W DI separately
        # ORD_COLS[i] aligns with raw_ord[i]: index 0 = oldest, 51 = newest
        for _ci, _c in enumerate(ORD_COLS):
            _base_row[_c] = float(_base_row.get(_c) or 0) + _raw_ord[_ci]
        # Accumulate metadata for driver annotation
        _base_row["_di_blend"]    = True
        _base_row["_di_l13_add"]  = float(_base_row.get("_di_l13_add", 0)) + _sib_l13
        _base_row["_di_l4_add"]   = float(_base_row.get("_di_l4_add",  0)) + _sib_l4
        _base_row.setdefault("_di_sib_labels", []).append(f"{_sib_ms}(+{_sib_l13:.0f} L13)")

        # ── DI cadence-pause detection ──────────────────────────────────────
        # Find the most-recent non-zero DI week across the full 52-week window.
        _di_nz_idxs = [i for i, v in enumerate(_raw_ord) if v > 0]
        if _di_nz_idxs:
            _di_last_nz  = max(_di_nz_idxs)          # 0=oldest, 51=most-recent
            _di_weeks_since = 51 - _di_last_nz        # weeks elapsed since last order
        else:
            _di_weeks_since = 52                      # no history at all
        # Monthly cadence ≈ 4.33 weeks; 65-day lead ≈ 9.3 weeks.
        # Any PO placed ≥ 9 weeks ago is already factored into Order History.
        # Silence ≥ 6 weeks = ≥ 1 missed monthly window; count how many.
        _DI_MONTH_WEEKS = 4.33
        if _di_weeks_since >= 6 and _di_nz_idxs:
            _di_missed = max(1, round(_di_weeks_since / _DI_MONTH_WEEKS))
            # Keep the worst (longest) pause across multiple siblings
            _prev = _base_row.get("_di_pause_weeks", 0)
            if _di_weeks_since > _prev:
                _base_row["_di_pause"]         = True
                _base_row["_di_pause_weeks"]   = _di_weeks_since
                _base_row["_di_missed_windows"] = _di_missed
                _base_row["_di_pause_sib"]     = _sib_ms

        _f69_blend_count += 1
        _f69_base_touched.add(_base_row.get("Acct_MStyle_Key_", ""))

    # Consolidate label list → single string for driver text
    for _row in rows:
        if _row.get("_di_blend"):
            _row["_di_label"] = ", ".join(_row.get("_di_sib_labels", []))

    print(f"      {_f69_blend_count} DI sibling variant(s) blended into "
          f"{len(_f69_base_touched)} base record(s)", flush=True)

    # F58 — Pull active "AI Adjusted" comments once (lookback 60 days).
    # Bucketed by acct-mstyle key.  Most-recent comment per key wins.
    ai_comments = _f58_fetch_active_comments(lookback_days=60)

    results = []
    for row in rows:
        key     = row.get("Acct_MStyle_Key_", "")
        prefix  = key.split("-")[0] if "-" in key else key
        acct_iv = acct_cadences.get(prefix)
        oos_ent  = oos_data.get(key) if oos_data else None
        po_wk    = open_pos_data.get(key) if open_pos_data else None
        ats_hist = ats_data.get(row.get("Mstyle", "")) if ats_data else None
        r = forecast_record(row, master_pack, account_interval=acct_iv,
                            amazon_pos=amazon_pos, season_map=season_map,
                            oos_entry=oos_ent, open_po_wk=po_wk,
                            amazon_catalog_us=amazon_catalog_us,
                            ai_comments=ai_comments, ats_hist=ats_hist,
                            switchover_weeks=switchover_index,
                            variant_zero_weeks=variant_zero_index,
                            retailer_pos=retailer_pos)
        # Build AI Analysis narrative — stored as a rich-text HTML string so
        # the QB codepage viewer can display it without re-deriving on the
        # client.  Mirrors the same logic the local viewer's
        # _adapt_forecast_to_validation() runs at viewer load time.
        # POS data is mstyle-keyed but Amazon-specific — only pass it through
        # when the record's customer actually IS Amazon, otherwise the
        # "Amazon POS run rate ..." paragraph leaks onto Walmart/Petsmart/etc
        # records that happen to share the same mstyle.
        _cust_name = (row.get("Customr_Name") or r.get("cust") or "")
        _is_apl_rec    = APL_CUST_SUBSTR in _cust_name.upper()
        # APL is_amazon_rec = True so _pos_for_rec is fetched (has Ordered_Units_LW/Prior_Wk).
        # build_ai_analysis() routes APL through its own bullet via is_apl flag.
        _is_amazon_rec = AMAZON_CUST_SUBSTR in _cust_name.upper()
        # EC items (e.g. "FF12302/24EC") have POS and DC Inv stored under
        # the parent mstyle ("FF12302/24") in the Amazon catalog tables.
        # Try the literal mstyle first; fall back to parent if not found.
        def _ec_parent(ms):
            msu = ms.upper()
            if msu.endswith("EC"):
                return ms[:-2]
            if msu.endswith("COS"):
                return ms[:-3]
            if msu.endswith("AMZ"):
                return ms[:-3]
            return ms
        _pos_for_rec    = None
        _amz_cat_for_rec = None
        if _is_amazon_rec:
            _ms_key = r.get("mstyle", "")
            _pos_for_rec    = (amazon_pos or {}).get(_ms_key) \
                              or (amazon_pos or {}).get(_ec_parent(_ms_key))
            if _pos_for_rec is None:
                for _sfx in ("AMZ", "EC", "COS", "DS"):
                    _pos_for_rec = (amazon_pos or {}).get(_ms_key + _sfx)
                    if _pos_for_rec:
                        break
            _amz_cat_for_rec = (amazon_catalog_us or {}).get(_ms_key) \
                               or (amazon_catalog_us or {}).get(_ec_parent(_ms_key))
            if _amz_cat_for_rec is None:
                for _sfx in ("AMZ", "EC", "COS", "DS"):
                    _amz_cat_for_rec = (amazon_catalog_us or {}).get(_ms_key + _sfx)
                    if _amz_cat_for_rec:
                        break
        try:
            r["ai_analysis"] = build_ai_analysis(
                r, row,
                ec_superseded=(key in ec_parents),
                pos=_pos_for_rec,
                amz_catalog=_amz_cat_for_rec,
            )
        except Exception as _e:
            # Don't let narrative bugs block the forecast — just leave it blank.
            r["ai_analysis"] = ""
        # Validate-after-forecast (default 2026-05-06).  Run validate_record()
        # on the same row so the per-week flag report (CRITICAL/WARNING/OK on
        # each manual week) lands alongside the AI forecast in one pass.  Both
        # functions share initial prep via _prep_record_signals(), so the
        # extra cost is just the validator's per-week threshold checks.
        # CLI: pass --no-validation-flags to skip.
        if not getattr(args, "no_validation_flags", False):
            try:
                _v = validate_record(row, master_pack, oos_entry=oos_ent,
                                     open_po_wk=po_wk)
                # On-Plan override: AI and Man are aligned -- nothing to review.
                # Two cases: (1) both zero; (2) plan entered and gap <= 7.5%.
                _man_tot = sum(float(row.get(c) or 0) for c in ORIG_PRJ_COLS)
                _ai_tot  = r.get("new_total", 0)
                _both_zero = _man_tot == 0 and _ai_tot == 0
                if _both_zero or (_man_tot > 0 and abs(_ai_tot - _man_tot) / _man_tot <= 0.075):
                    _v["priority"] = "On-Plan"
                # Slim the merge to validation-specific fields — forecast
                # record already has key/mstyle/cust/mp/biweekly/iso etc.
                r["validation"] = {
                    "max_severity":          _v.get("max_severity"),
                    "priority":              _v.get("priority"),
                    "n_flags":               _v.get("n_flags"),
                    "weeks":                 _v.get("weeks"),
                    "expected_total":        _v.get("expected_total"),
                    "baseline":              _v.get("baseline"),
                    "baseline_src":          _v.get("baseline_src"),
                    "qb_pattern":            _v.get("pattern"),
                    "stockout_corrections":  _v.get("stockout_corrections"),
                }
            except Exception as _e:
                # Validation failure shouldn't block the forecast write-back.
                r["validation"] = {"error": str(_e)[:200]}
        results.append(r)

    pat_counts  = {}
    biweekly_ct = 0
    alert_count = 0
    for r in results:
        pat_counts[r["model"]] = pat_counts.get(r["model"], 0) + 1
        if r.get("biweekly"):    biweekly_ct += 1
        if r["alert"]:           alert_count += 1

    elapsed_fcst = time.time() - t_fcst
    print(f"      {len(results)} forecasts complete in {elapsed_fcst:.1f}s")
    model_summary = "  ".join(f"{k}: {v}" for k, v in sorted(pat_counts.items()))
    print(f"      {model_summary}  Bi-weekly enforced: {biweekly_ct}  "
          f"Alerts (>{ALERT_THRESHOLD*100:.4g}%): {alert_count}")

    # Save forecast JSON — wrapped with meta so viewer.py knows the prj_cols
    out_path = Path(args.out)
    output = {
        "_schema_version": SCHEMA_VERSION,
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scope":        scope_desc,
            "prj_cols":     ORIG_PRJ_COLS,
        },
        "records": results,
    }
    with open(out_path, "w") as f:
        json.dump(output, f)
    print(f"      Saved → {out_path}")
    print(f"      Open viewer: python scripts/viewer.py --results {out_path.name}")

    # Write HTML report (with forecast results if we ran EDA)
    if args.analyze or findings is not None:
        if findings is None:
            findings = run_eda(rows, master_pack)
        rpt_path = Path(args.report)
        html = build_html_report(findings, scope_desc, results=results)
        with open(rpt_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"      Report saved → {rpt_path}")

    if args.dry_run:
        print("\n[DRY RUN] Skipping write-back.")
        _print_summary(results, 0, 0)
        return

    # ── Phase 4: Write-back ────────────────────────────────────────
    completed = set()
    if args.resume and Path(args.resume).exists():
        with open(args.resume) as f:
            completed = set(json.load(f))
        print(f"\n[4/4] Writing back (skipping {len(completed)} already done) ...", flush=True)
    else:
        print(f"\n[4/4] Writing {len(results)} records to Quickbase ...", flush=True)

    to_write = [r for r in results if r["key"] not in completed]
    _total_wb = len(to_write)

    if _total_wb == 0:
        print("      Nothing to write — all records already completed.")
        _print_summary(results, 0, 0)
        return

    completed_path = Path(args.out).with_suffix(".completed.json")
    done_keys      = list(completed)
    done_lock      = threading.Lock()
    t_wb = time.time()

    # ── Bulk path: direct QB REST /v1/records upsert ───────────────────
    if getattr(args, "bulk_writeback", False):
        print(f"      {_total_wb} records  |  bulk REST → "
              f"{QB_REALM}/v1/records, batch={QB_BULK_BATCH}")
        # Build field name → fid map for Projections
        fmap = qb_get_field_map(QB_PROJ_TABLE)
        if not fmap:
            sys.exit("\n[ABORT] qb_get_field_map() returned empty. "
                     "Check QB_USER_TOKEN / QB_PROJ_TABLE / network. "
                     "Falling back: re-run with --no-bulk-writeback for legacy SQL UPDATE path.")
        # Try several candidate labels for the merge key — QB displays the
        # label with formatting characters (#, -, parens) that don't match
        # the SQL-friendly alias the rest of the script uses.
        merge_fid = (fmap.get("Acct# - MStyle (Key)")
                     or fmap.get("Acct - MStyle Key")
                     or fmap.get("Acct_MStyle_Key_"))
        if not merge_fid:
            sys.exit("\n[ABORT] Projections table is missing the Acct-MStyle key field "
                     "(tried 'Acct# - MStyle (Key)', 'Acct - MStyle Key', 'Acct_MStyle_Key_'). "
                     "Cannot upsert.")
        # Compose payload — QB labels use spaces, not underscores
        ai_alert_fid    = fmap.get("AI ALERT") or fmap.get("AI_ALERT")
        ai_analysis_fid = fmap.get("AI Analysis")  # fid 1590 — rich-text narrative
        wk_fids         = [fmap.get(f"AI PRJ W{i}") or fmap.get(f"AI_PRJ_W{i}")
                           for i in range(1, 27)]
        if not all(wk_fids) or not ai_alert_fid:
            sys.exit("\n[ABORT] Projections table is missing one or more AI_PRJ_W*/AI_ALERT fields. "
                     "Field map fetched but could not resolve required fids.")
        if not ai_analysis_fid:
            print("      [WARN] [AI Analysis] field not found in Projections — narratives will not be written.")
        # POG End Date default (2026-05-24): write pog_end = pog_launch + 364 days for
        # records that have a launch date but no end date.  FID 1595 on Projections table.
        pog_end_fid = fmap.get("POG End Date") or fmap.get("POG_End_Date") or 1595
        # Auto Project: discover MAN PRJ FIDs dynamically (date-stamped labels like "05 19 W1").
        # Used to copy AI forecast values into manual projection columns for auto-project records.
        import re as _re
        _man_prj_fids = {}  # week_number (1..26) -> fid
        for label, fid in fmap.items():
            m = _re.match(r'^\d{2} \d{2} W(\d+)$', label)
            if m:
                _man_prj_fids[int(m.group(1))] = fid
        _auto_proj_count    = sum(1 for r in to_write if r.get("auto_project"))
        _w1_po_count        = sum(1 for r in to_write
                                  if (r.get("opn_w") or [0])[0] > 0)
        _w2_po_count        = sum(1 for r in to_write
                                  if len(r.get("opn_w") or []) > 1 and (r.get("opn_w") or [])[1] > 0)
        _po_cutoff_w1_count = sum(1 for r in to_write if r.get("zero_man_w1_cutoff"))
        if _auto_proj_count:
            print(f"      Auto Project: {_auto_proj_count} records will have manual projections replaced with AI values")
            if len(_man_prj_fids) < 26:
                print(f"      [WARN] Auto Project: only {len(_man_prj_fids)} MAN PRJ week FIDs found (expected 26) -- partial copy")
        if _w1_po_count:
            print(f"      W1 open POs: {_w1_po_count} records -- AI PRJ W1 and MAN PRJ W1 zeroed (confirmed PO covers W1 demand)")
        if _w2_po_count:
            print(f"      W2 open POs: {_w2_po_count} records have an open PO in W2 (MAN PRJ W2 left as-is -- planner controls via codepage Zero button)")
        if _po_cutoff_w1_count:
            print(f"      F_PO_CUTOFF W1: {_po_cutoff_w1_count} Fetch/BrandBuzz records past PO cutoff -- AI+MAN PRJ W1 zeroed")
        _man_w1_fid = _man_prj_fids.get(1)   # FID for the current MAN PRJ W1 column (F_PO_CUTOFF only)
        if _po_cutoff_w1_count and not _man_w1_fid:
            print(f"      [WARN] F_PO_CUTOFF: MAN PRJ W1 FID not found in field map -- manual W1 not zeroed in QB")
        payload = []
        for rec in to_write:
            row = {merge_fid: rec["key"], ai_alert_fid: _sanitize_for_qb(rec.get("alert", ""))}
            if ai_analysis_fid:
                row[ai_analysis_fid] = _sanitize_for_qb(rec.get("ai_analysis", ""))
            for i, fid in enumerate(wk_fids):
                row[fid] = int(round(rec["forecast"][i])) if i < len(rec["forecast"]) else 0
            # Auto Project: copy AI forecast values into MAN PRJ columns for flagged records
            if rec.get("auto_project") and _man_prj_fids:
                for wk, fid in _man_prj_fids.items():
                    idx = wk - 1
                    row[fid] = int(round(rec["forecast"][idx])) if idx < len(rec["forecast"]) else 0
            # MAN PRJ W1 zeroed in two cases:
            #   1. Opn_W1 > 0: confirmed open order exists -- zero to avoid double-count.
            #      (VP-Q4 already zeroed AI W1; this keeps MAN PRJ consistent.)
            #   2. zero_man_w1_cutoff (F_PO_CUTOFF / F_PO_CUTOFF_ALL): past cutoff
            #      with no open PO -- zero both AI and MAN PRJ.
            _opn_w1 = float((rec.get("opn_w") or [0])[0] if rec.get("opn_w") else 0)
            if _man_w1_fid and (_opn_w1 > 0 or rec.get("zero_man_w1_cutoff")):
                row[_man_w1_fid] = 0
            # POG End Date default: if pog_launch is set but pog_end is empty,
            # write pog_end = pog_launch + 364 days so planners always have an
            # end date without having to fill it in manually.
            _pl = (rec.get("pog_launch") or "").strip()
            _pe = (rec.get("pog_end") or "").strip()
            if _pl and not _pe:
                try:
                    _default_pog_end = (date.fromisoformat(_pl) + timedelta(days=364)).isoformat()
                    row[pog_end_fid] = _default_pog_end
                except ValueError:
                    pass  # malformed date — skip silently
            payload.append(row)
        n_ok, n_fail, errors = qb_bulk_update(QB_PROJ_TABLE, payload, merge_fid)
        # Track completed keys: assume in-order success for the batches that returned OK.
        # qb_bulk_update returns aggregate counts, not per-record IDs; we mark all keys
        # in successful batches as done. (errors[] flags the partial batches.)
        bad_batches = {e["batch_start"] for e in errors if "error" in e}
        for i, rec in enumerate(to_write):
            batch_start = (i // QB_BULK_BATCH) * QB_BULK_BATCH
            if batch_start not in bad_batches:
                done_keys.append(rec["key"])
        with open(completed_path, "w") as f:
            json.dump(done_keys, f)
        elapsed_wb = time.time() - t_wb
        print(f"      Bulk upsert: {n_ok:,} OK · {n_fail:,} failed · "
              f"{elapsed_wb:.1f}s ({n_ok/max(elapsed_wb,0.01):.0f} rec/s)")
        if errors:
            err_path = Path(args.out).with_suffix(".bulk_errors.json")
            with open(err_path, "w") as f:
                json.dump(errors, f, indent=2)
            print(f"      Errors saved → {err_path}")
        _print_summary(results, elapsed_wb, n_fail)
        return

    # ── Legacy per-record path (CData SQL UPDATE) ──────────────────────
    print(f"      {_total_wb} records  |  {args.workers} parallel workers"
          + (f"  |  rate_limit={args.rate_limit_ms}ms" if args.rate_limit_ms > 0 else ""))

    _rate_lock  = threading.Lock()
    _last_call  = [0.0]   # mutable closure

    def _rate_pace():
        if args.rate_limit_ms <= 0:
            return
        with _rate_lock:
            wait = args.rate_limit_ms / 1000.0 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()

    def write_one(rec):
        _rate_pace()
        sql = build_update_sql(rec["key"], rec["forecast"], rec["alert"])
        ok  = cdata_update(sql, rec["key"])
        if ok:
            with done_lock:
                done_keys.append(rec["key"])
                if len(done_keys) % 50 == 0:
                    with open(completed_path, "w") as f:
                        json.dump(done_keys, f)
        return rec["key"], ok

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(write_one, r): r for r in to_write}
        for fut in as_completed(futs):
            key, success = fut.result()
            tick(key, success)

    with open(completed_path, "w") as f:
        json.dump(done_keys, f)

    elapsed_wb = time.time() - t_wb
    _print_summary(results, elapsed_wb, _failed)

    if _failures:
        fail_path = Path(args.out).with_suffix(".failures.json")
        with open(fail_path, "w") as f:
            json.dump(_failures, f, indent=2)
        print(f"\n  Failures saved → {fail_path}")
        print(f"  To retry: python inventory_forecaster.py [same scope] --resume {completed_path}")


def _print_week_detail(results):
    """
    Print a week-by-week comparison table for every record.
    Columns: Key | Model | W1..W26 (AI / Man) | AI Avg/wk | Man Avg/wk | Δ%
    Two data rows per record: AI projections then Manual projections.
    """
    week_hdr = "  " + f"{'Key / Model':<28}" + "".join(f" W{w:>2}" for w in range(1, 27)) + "  Avg/wk   Δ%"
    div = "  " + "-" * (len(week_hdr) - 2)

    print(f"\n{'='*66}")
    print("  WEEKLY DETAIL — AI vs Manual projections")
    print(week_hdr)
    print(div)

    for r in results:
        key_label   = r["key"][:27]
        model_label = f"  ({r['model'][:25]})"
        ai_avg  = r["new_total"]   / 26
        man_avg = r["prior_total"] / 26
        sign    = "+" if r["pct_diff"] >= 0 else ""
        bw_flag = "[BW]" if r.get("biweekly") else ""

        # AI row
        ai_vals = "".join(f" {v:>4}" for v in r["forecast"])
        print(f"  {'AI  ' + key_label:<28}{ai_vals}  {ai_avg:>6,.0f}  {sign}{r['pct_diff']:.1f}% {bw_flag}")

        # Manual row
        man_vals = "".join(f" {v:>4}" for v in r.get("manual", [0]*26))
        print(f"  {'Man ' + key_label:<28}{man_vals}  {man_avg:>6,.0f}")

        print(div)

    print()


def _print_summary(results, elapsed_wb, failed):
    total_26w   = sum(r["new_total"] for r in results)
    alerts      = [r for r in results if r["alert"]]
    pat_counts  = {"Seasonal Baseline": 0, "Croston's": 0, "Heuristic": 0, "Inactive": 0}
    biweekly_ct = 0
    for r in results:
        pat_counts[r["model"]] = pat_counts.get(r["model"], 0) + 1
        if r.get("biweekly"): biweekly_ct += 1

    crostons_n = pat_counts["Croston's"]
    print(f"\n{'='*66}")
    print(f"  COMPLETE  |  {len(results)} records  |  Total 26w demand: {total_26w:,}")
    print(f"  Models -- Seasonal: {pat_counts['Seasonal Baseline']}  "
          f"Croston's: {crostons_n}  "
          f"Heuristic: {pat_counts['Heuristic']}  "
          f"Inactive: {pat_counts['Inactive']}  "
          f"Bi-weekly: {biweekly_ct}")
    if elapsed_wb:
        print(f"  Write-back: {elapsed_wb/60:.1f} min  |  ok={_ok}  fail={failed}")
    if alerts:
        print(f"\n  ALERTS ({len(alerts)} records — >{ALERT_THRESHOLD*100:.4g}% variance or inactive):")
        hdr = f"  {'Key':<32} {'Δ%':>7}  {'Model':<14}  {'AI 26w':>10}  {'Manual':>10}"
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in alerts:
            bw = "[BW]" if r.get("biweekly") else "    "
            print(f"  {r['key']:<32} {r['pct_diff']:>+7.1f}%  "
                  f"{r['model']:<14} {bw}  {r['new_total']:>10,}  {r['prior_total']:>10,}")

    # Always print week-by-week detail
    _print_week_detail(results)
    print()


if __name__ == "__main__":
    main()
