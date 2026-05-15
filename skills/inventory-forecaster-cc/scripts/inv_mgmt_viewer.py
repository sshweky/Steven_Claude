#!/usr/bin/env python3
"""
Inventory Management Viewer — local Python HTTP server (port 8766).

ONE ROW PER MSTYLE.  Aggregates demand across all customer Acct-MStyles for
a given mstyle and surfaces inventory health from Inventory Flow.

Separate from `viewer.py` (Forecast Management, port 8765) — different grain,
different audience, different actions.

Build status (2026-05-11):
  ✅ Phase 1 — data layer (this file): Inv Flow pull, PO parse, rec engine
  🔨 Phase 2 — UI (table + filters + detail pane)
  🔨 Phase 3 — Excel "Generate PO Change List" via openpyxl

Run:
    python scripts/inv_mgmt_viewer.py             # serves http://127.0.0.1:8766
    python scripts/inv_mgmt_viewer.py --dry-run   # build data, print summary, exit (no server)
    python scripts/inv_mgmt_viewer.py --mstyle FF11899/24   # filter to one mstyle for debugging
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time as time_mod
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# ─── Configuration ────────────────────────────────────────────────────────────

REALM     = "pim.quickbase.com"
PROJ_TID  = "bpd237tvm"   # Projections
INVF_TID  = "bpsaju5pm"   # Inventory Flow
INVC_TID  = "bv2ne5qx5"   # Inventory Flow Comments  (created 2026-05-11)

OPT_WOS_DEFAULT     = 4.0     # fallback when QB Opt WOS is empty
WAREHOUSE_LAG_DAYS  = 10      # ETA (port) → warehouse availability
USA_WAREHOUSE_LAG   = 3       # domestic truck — shorter lag
FAST_VESSEL_TRANSIT = 18      # days (used when recommending faster vessel)
PARTIAL_MIN_PCS     = 2500    # min qty for any partial-shipment leg
ETD_LOCK_DAYS       = 7       # PO is locked from expedite when ETD - today < 7
FASTER_VESSEL_HI    = 15      # 8 ≤ days_to_ETD ≤ 15 triggers faster-vessel rec
CANCEL_HORIZON_DAYS = 60      # PO ETD > today + 60 days qualifies for cancel
OVERSTOCK_EXCESS_TH = 2500    # excess pcs > this triggers overstock flag
OVERSTOCK_WOS_TH    = 33      # pipeline_wos > this also triggers overstock flag

VIEWER_PORT_DEFAULT = 8766    # 8765 is Forecast Mgmt, use a different port

# Cache files (separate from forecast viewer's so they don't collide)
SKILL_DIR  = Path(__file__).resolve().parent.parent
CACHE_DIR  = SKILL_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
INVF_CACHE = CACHE_DIR / "inv_mgmt_invflow.json"

# Warm-cache: full gzipped payload from a prior successful pull.  Loaded at
# launch when fresh (< CACHE_TTL_HOURS); subsequent launches skip the entire
# CData pipeline and serve instantly.
RECORDS_CACHE_PATH = CACHE_DIR / "inv_mgmt_records.json.gz"
CACHE_TTL_HOURS    = 4


# ─── CData helpers (reuse pattern from viewer.py) ─────────────────────────────

# Reuse the inventory_forecaster module's cdata_query so we don't duplicate
# the MCP-client plumbing.  This is the same approach viewer.py takes.
try:
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    from inventory_forecaster import cdata_query, _discover_prj_cols  # type: ignore
except ImportError as e:
    print(f"[FATAL] Could not import from inventory_forecaster: {e}")
    print(f"  Expected file: {SKILL_DIR / 'scripts' / 'inventory_forecaster.py'}")
    sys.exit(1)

# Polish thresholds (Phase 1 → Phase 2 refinements)
MIN_DEFICIT_FOR_REC      = 1.5    # only emit a pull-up rec if gap deficit >= this (in WOS)
MIN_PULLUP_DAYS          = 7      # don't recommend pull-ups smaller than this (not worth supplier effort)
MIN_PUSHOUT_WEEKS        = 4      # don't recommend push-outs smaller than this
MIN_RESOLVED_GAP_DEFICIT = 1.0    # individual gap weeks with deficit < this don't justify a rec on their own


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class OpenPO:
    """One PO line parsed from the [Open_Supplier_POs] text field."""
    po_number: str
    supplier:  str
    in_transit_qty:    int           # I/T pcs (already shipped)
    in_work_qty:       int           # I/W pcs (still at supplier)
    etd:               Optional[date]
    eta:               Optional[date]
    raw:               str           # original line for audit

    @property
    def qty(self) -> int:
        return self.in_transit_qty + self.in_work_qty

    @property
    def is_in_transit(self) -> bool:
        return self.in_transit_qty > 0

    def days_to_etd(self, today: date) -> Optional[int]:
        if self.etd is None: return None
        return (self.etd - today).days

    def transit_days(self) -> Optional[int]:
        if self.etd is None or self.eta is None: return None
        return (self.eta - self.etd).days

    def status(self, today: date, country: str) -> str:
        """Classify the PO's mutability per the action-engine rules."""
        if self.is_in_transit:
            return "IN_TRANSIT"            # on the water, can't change ETD
        dtd = self.days_to_etd(today)
        if dtd is None:
            return "UNKNOWN"
        if dtd < ETD_LOCK_DAYS:
            return "LOCKED"                # within 7 days, nothing we can do
        if 8 <= dtd <= FASTER_VESSEL_HI:
            return "FASTER_VESSEL_WINDOW" if country.upper() not in ("USA","UNITED STATES") else "PULL_UP_NARROW"
        return "MOVABLE"                   # > 15 days out, full pull-up flexibility


@dataclass
class Recommendation:
    """One actionable recommendation surfaced for a mstyle."""
    action:          str               # PULL_UP / FASTER_VESSEL / PUSH_OUT / SPLIT / CANCEL / NO_LEVER
    po_number:       str
    supplier:        str
    qty_affected:    int
    orig_etd:        Optional[date]
    proposed_etd:    Optional[date]
    orig_eta:        Optional[date]
    proposed_eta:    Optional[date]
    delta_days:      int               # negative = pull-up, positive = push-out
    delta_qty:       int               # negative = reduction, 0 = timing-only
    priority:        str               # CRITICAL / HIGH / MEDIUM / LOW
    reason:          str               # human-readable explanation
    # Current PO state (so the card can show "before" details)
    in_transit_qty:  int = 0           # current I/T on the affected PO
    in_work_qty:     int = 0           # current I/W on the affected PO
    po_total_qty:    int = 0           # current full PO qty (I/T + I/W)
    # SPLIT-only: post-split distribution
    keep_qty:        int = 0           # qty staying at the original ETD/ETA
    push_qty:        int = 0           # qty being pushed to proposed_etd
    # Coverage
    gap_weeks_fixed: list[int] = field(default_factory=list)   # W#s this rec resolves


@dataclass
class MStyleRecord:
    """One row in the Inventory Management table."""
    mstyle:           str
    description:      str
    brand:            str
    inv_manager:      str
    country:          str
    item_status:      str               # most-common PT_Item_Status across acct-mstyles
    customer_count:   int               # # active Acct-MStyles
    is_replen:        bool

    # Inventory positions (from Inventory_Flow)
    beg_inv:          list[int]         # Wk1..Wk26
    rcv:              list[int]         # RcvWk1..Wk26
    prj:              list[int]         # Prj Wk1..Wk26
    opt_wos:          float
    next_rcpt_dt:     Optional[date]
    lt_trans_days:    int
    transit_days:     int

    # Parsed POs
    open_pos:         list[OpenPO]

    # Aggregated demand (rolled up from Projections)
    manual_demand_26w:    int           # sum of acct-mstyle manual projections
    customer_demand:      list[dict] = field(default_factory=list)  # [{customer, weekly[26], total}] sorted desc by total

    # Multi-pack (kit) context
    is_multi:         bool = False      # this mstyle is a multi-pack of root_mstyle
    pcs_per_kit:      int = 1           # how many root pcs make up one kit
    root_mstyle:      str = ""          # the root mstyle this is built from
    qty_oh_root:      int = 0           # root mstyle's qty on hand (pcs)
    it_iw_root:       int = 0           # root mstyle's in-transit + in-work pcs
    ats_oh_oo_root:   int = 0           # root mstyle's OH + on-order pcs

    # Identity / context (extended)
    style_:           str = ""
    season:           str = ""
    item_rank:        str = ""
    item_status_flow: str = ""          # Inv-Flow's own Item Status field
    sub_status:       str = ""
    nvo:              bool = False
    new_item_no_prj:  bool = False
    active_kl:        bool = False
    amz_do_not_ship:  bool = False
    amz_suppression:  bool = False
    transfer_qty_open: bool = False

    # PO timing extras
    opt_oh:           int = 0
    lt_wks:           float = 0.0
    cny_weeks:        float = 0.0
    lt_opt_weeks:     float = 0.0
    moq:              int = 0     # supplier min order qty — used for partial-shipment floor (MOQ/2)

    # Inventory positions (numeric)
    qty_oh:                int = 0
    ats_qty_oh:            int = 0
    ats_now:               int = 0
    ats_oh_oo:             int = 0
    ats_oh_oo_w_kits:      int = 0
    ats_qty_not_alloc:     int = 0
    nj_ats_oh:             int = 0
    ca_ats_oh:             int = 0
    hold_qty:              int = 0
    it_qty:                int = 0
    iw_qty:                int = 0
    it_iw:                 int = 0
    it_iw_kits:            int = 0
    open_cust_po_qty:      int = 0
    test_order_qty:        int = 0
    exclude_po_wos:        int = 0

    # WOS metrics
    ats_wos_oh:               float = 0.0
    ats_wos_oh_oo:            float = 0.0
    ats_wos_oh_oo_w_kits:     float = 0.0
    ats_wos_oh_oo_wo_test:    float = 0.0
    ats_oh_it_booked_wos:     float = 0.0

    # OOS metrics
    days_oos_next_rcpt:    int = 0
    days_oos_l12m:         int = 0
    last_oos_date:         Optional[date] = None

    # Demand single-values
    prj_wk:                float = 0.0
    max_prj_wk:            float = 0.0
    prj_l4w_change:        float = 0.0
    prj_26wks:             int = 0

    # Shipments
    shp_wk_l4:             float = 0.0
    shp_wk_l13:            float = 0.0
    tot_shpd_l13w:         int = 0
    tot_shpd_l4:           int = 0
    tot_shpd_ltd:          int = 0
    last_shp_date:         Optional[date] = None

    # Key dates
    date_1st_rcvd:         Optional[date] = None
    last_whs_rcvd:         Optional[date] = None
    first_shpd_date:       Optional[date] = None
    first_out_date:        Optional[date] = None

    # Text summaries (detail pane)
    shipment_status_summary: str = ""
    ats_summary:           str = ""
    inventory_notes:       str = ""
    style_alert:           str = ""
    oos_priority_notes:    str = ""
    active_replen_customers: str = ""

    # Product attributes (detail pane)
    size_ct:          str = ""
    fragrance:        str = ""
    pvt_lbl_excl:     bool = False
    commit_item:      bool = False
    inner_pack:       int = 0
    master_pack:      int = 0
    oos_dates:        str = ""

    # Overstock management (detail pane)
    over_committed_qty: int = 0
    ovr_comt_wos:     float = 0.0

    # Aged inventory (detail pane)
    invtry_age_days:  int = 0
    aged_inv_0_90:    int = 0
    aged_inv_91_180:  int = 0
    aged_inv_181_365: int = 0
    aged_inv_365plus: int = 0
    pct_time_in_stock: float = 0.0

    # Derived (computed in the rec engine)
    pipeline_total:       int = 0
    demand_26w:           int = 0
    pipeline_excess:      int = 0
    pipeline_wos:         float = 0.0
    gap_weeks:            list[dict] = field(default_factory=list)
    overstocked:          bool = False
    recommendations:      list[Recommendation] = field(default_factory=list)
    priority:             str = "LOW"   # rollup: CRITICAL > HIGH > MEDIUM > LOW

    # Comments (loaded on detail expand)
    flag:                 str = ""      # latest comment flag


# ─── Open_Supplier_POs parser ─────────────────────────────────────────────────

# Format observed in QB:
#   "FC607491 - JIANGSU SNOW LEOPARD CHEMICAL - I/T: 0 pcs / I/W: 1200 pcs - ETD: 05-17-2026 - ETA: 06-04-2026\r\n;FC607491 ..."
# Lines separated by ";\r\n" (or just ";"). Each line is one PO shipment.

_PO_LINE_RE = re.compile(
    r"""^\s*
        (?P<po>[A-Z0-9\-]+)                                 # PO number
        \s*-\s*
        (?P<sup>[^-]+?)                                     # supplier (stop at next ' - ')
        \s*-\s*
        I/T:\s*(?P<it>[\d,]+)\s*pcs                         # in-transit qty
        \s*/\s*
        I/W:\s*(?P<iw>[\d,]+)\s*pcs                         # in-work qty
        \s*-\s*
        ETD:\s*(?P<etd>\d{2}-\d{2}-\d{4})                   # ETD MM-DD-YYYY
        \s*-\s*
        ETA:\s*(?P<eta>\d{2}-\d{2}-\d{4})                   # ETA MM-DD-YYYY
        """,
    re.IGNORECASE | re.VERBOSE,
)


def _parse_date(s: str) -> Optional[date]:
    """MM-DD-YYYY → date.  Returns None if unparseable."""
    if not s: return None
    try:
        return datetime.strptime(s.strip(), "%m-%d-%Y").date()
    except ValueError:
        return None


def parse_open_pos(raw: str) -> list[OpenPO]:
    """Split [Open_Supplier_POs] multi-line text into structured PO records.
    Empty/malformed lines are silently skipped."""
    if not raw:
        return []
    out: list[OpenPO] = []
    # Lines can be separated by ; \r\n or just ; or just \n — normalize
    chunks = re.split(r"[;\r\n]+", raw)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _PO_LINE_RE.match(chunk)
        if not m:
            continue
        try:
            it = int(m.group("it").replace(",", ""))
            iw = int(m.group("iw").replace(",", ""))
        except ValueError:
            continue
        out.append(OpenPO(
            po_number=m.group("po").strip(),
            supplier=m.group("sup").strip(),
            in_transit_qty=it,
            in_work_qty=iw,
            etd=_parse_date(m.group("etd")),
            eta=_parse_date(m.group("eta")),
            raw=chunk,
        ))
    return out


# ─── Data pull ────────────────────────────────────────────────────────────────

# CData sanitizes QB labels (spaces → underscores).  Use the sanitized form
# in SELECT clauses; the data accessor tries both forms when reading back.
_INV_FLOW_BEG = [f"Wk{i}"      for i in range(1, 27)]
_INV_FLOW_RCV = [f"RcvWk{i}"   for i in range(1, 27)]
_INV_FLOW_PRJ = [f"Prj_Wk{i}"  for i in range(1, 27)]   # CData form (was "Prj Wk1")
_INV_FLOW_META = [
    # Identity
    "Mstyle", "Country", "Style_", "Season",
    "Item_Rank", "Item_Status", "Sub_Status",
    "NVO", "New_Item_No_Prj_",

    # PO timing
    "Opt_WOS", "Opt_WOS_Final", "Opt_OH",
    "Next_Avl_Rcpt_Dt",
    "LT_Trans_Days", "Transit_Days", "LT_Wks_", "CNY_Weeks", "LT_Opt_Weeks",
    "Open_Supplier_POs",
    "MOQ",                                # supplier min order qty (drives partial-min)

    # Multi-pack (Kit)
    "Kit_Style_", "_Pcs_Kit_USE_", "Root_Mstyle", "ATS_Qty_OH_root_",
    "I_T_I_W_root_", "ATS_OH_OO_Root_",

    # Inventory positions (numeric DOUBLE versions)
    "Qty_OH_",                       # total OH all warehouses
    "ATS_Qty_OH_",                   # available-to-sell on hand
    "ATS_Now",                       # available-now (after holds/allocs)
    "ATS_OH_OO_",                    # OH + on-order
    "ATS_OH_OO_w_kits_",
    "ATS_Qty_not_alloc_d_",
    "NJ_ATS_OH_", "CA_ATS_OH_",
    "Hold_Order_Qty",
    "I_T_", "I_W_", "I_T_I_W_", "I_W_I_T_w_Kits",
    "Open_Cust_PO_Qty",
    "Test_Order_Qty", "Exclude_PO_from_WOS_Qty",

    # WOS metrics
    "ATS_WOS_OH_", "ATS_WOS_OH_OO_", "ATS_WOS_OH_OO_w_kits_",
    "ATS_WOS_OH_OO_w_o_test_exclusions_",
    "ATS_OH_I_T_Booked_not_O_W_WOS",

    # OOS metrics
    "Days_OOS_till_Next_Rcpt", "Days_OOS_L12m", "Last_OOS_Date",

    # Demand (single-value)
    "Prj_Wk", "Maximum_Prj_Wk", "_Prj_L4w_all_", "Prj_26Wks",

    # Shipments
    "Shp_Wk_L4", "Shp_Wk_L13", "Tot_Shpd_L13w", "Tot_Shpd_L4",
    "Tot_Shpd_LTD", "Last_Shp_Date_raw_",

    # Key dates
    "Date_1st_Rcvd", "Last_Whs_Rcvd_Date", "1st_Shpd_Date", "1st_Out_Date",

    # Flags
    "Active_KL_", "AMZ_DO_NOT_SHIP", "AMZ_Suppression_", "Transfer_Qty_Open_",

    # Summary text (detail pane)
    "Shipment_Status_Summary_", "ATS_Summary_", "Inventory_Notes_",
    "Style_Alert_Message", "OOS_Priority_Notes",
    "Active_Replen_Customers",

]

# Extended detail-pane fields pulled separately (so a bad name here never
# breaks the main pull).  Populated by pull_inv_flow_extended().
_INV_FLOW_META_EXT = [
    # Product attributes
    "Size_Ct", "Fragrance",
    "Prvte_Lbl_Excl_", "Commit_Item_",
    "Inner_Pack", "Master_Pack",
    "OOS_Dates_MM_DD_YY_",
    # Overstock management
    "Over_Committed_Qty", "Ovr_Comt_WOS",
    # Aged inventory
    "Invtry_Age_Days_",
    "Aged_Inv_0_90_days", "Aged_Inv_91_180_days",
    "Aged_Inv_181_365_days", "Aged_Inv_365_days",
    "_Time_In_Stock_since_2_16_22_",
]

# NOTE: [Inv Mgr (name)] (the QB formula field returning plain string) does
# NOT query through CData — parentheses in the label break the column ref.
# Use [Inventory_Manager] (User-type field) instead and parse the name
# client-side (email → "Local Name" via the helper below).
# [Customr_Name] preserves the QB-side typo (no 'e').
_PROJ_PULL_COLS = (
    "[Mstyle], [Description], [Brand_Name], [Inventory_Manager], "
    "[PT_Item_Status], [Status_Cust], [Acct_MStyle_Key_], [Customr_Name]"
)


def _email_to_name(email_or_dict) -> str:
    """Convert 'taek@fetch4pets.com' or {'name':...} into a friendly display
    name.  Used as a fallback when [Inv Mgr (name)] formula field isn't
    accessible."""
    if not email_or_dict:
        return ""
    if isinstance(email_or_dict, dict):
        return (email_or_dict.get("name") or email_or_dict.get("email") or "").strip()
    s = str(email_or_dict).strip()
    # Email: take the local part, drop digits, title-case
    if "@" in s:
        local = s.split("@")[0]
        # camelCase / snake-case → spaced
        local = re.sub(r"([a-z])([A-Z])", r"\1 \2", local)
        local = local.replace("_", " ").replace(".", " ")
        return " ".join(w.capitalize() for w in local.split() if w)
    return s


def _to_num(v, default=0):
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (ValueError, TypeError):
        try:
            return int(float(str(v).replace(",", "").strip()))
        except (ValueError, TypeError):
            return default


def _to_date(v) -> Optional[date]:
    if not v: return None
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(v.strip()[:10], fmt).date()
            except ValueError:
                continue
        return None
    if hasattr(v, "isoformat"):
        try:
            return date.fromisoformat(v.isoformat()[:10])
        except (ValueError, AttributeError):
            return None
    return None


def _as_str(v) -> str:
    if v is None: return ""
    if isinstance(v, dict):
        return (v.get("name") or v.get("email") or "").strip()
    return str(v).strip()


def pull_inv_flow(mstyle_filter: Optional[list[str]] = None) -> dict[str, dict]:
    """Pull Inventory Flow rows (one per mstyle).  Returns {mstyle: row_dict}.

    Full-table pull is batched by mstyle (500/batch) because the unfiltered
    141-column × 11k-row SELECT exceeds CData's response-size limit.
    Step 0 fetches just [Mstyle] to bootstrap the batch list (1 tiny call).
    If mstyle_filter is given, step 0 is skipped.
    Side effect: populates module-level _INV_FLOW_MSTYLES for downstream rollup."""
    global _INV_FLOW_MSTYLES
    cols = "[" + "], [".join(_INV_FLOW_META + _INV_FLOW_BEG + _INV_FLOW_RCV + _INV_FLOW_PRJ) + "]"

    # Step 0 — get mstyle list (1-column query, tiny response)
    if mstyle_filter:
        all_mstyles = list(mstyle_filter)
    else:
        list_rows = cdata_query(
            "SELECT [Mstyle] FROM [Quickbase1].[InventoryTrack].[Inventory_Flow]",
            description="inv-flow mstyle list")
        all_mstyles = [_as_str(r.get("Mstyle") or r.get("mstyle", ""))
                       for r in (list_rows or [])]
        all_mstyles = [m for m in all_mstyles if m]
        print(f"  [InvMgmt] {len(all_mstyles)} mstyles found — batching full pull...", flush=True)

    # Step 1 — batch-pull all columns in chunks of 200 mstyles.
    # Larger batches (500, 1000) cause the IN-clause SQL to exceed CData's
    # query-string limit and hang indefinitely.  200 gives ~56 fast batches
    # (~2-3s each = ~2 min cold pull), then cached for 4h.
    BATCH = 200
    n_batches = max(1, (len(all_mstyles) + BATCH - 1) // BATCH)
    out: dict[str, dict] = {}

    def _b(v):
        return v is True or v == 1 or (isinstance(v, str) and v.lower() in ("true", "1", "yes"))

    for bi in range(0, len(all_mstyles), BATCH):
        batch = all_mstyles[bi:bi + BATCH]
        in_clause = ", ".join("'" + m.replace("'", "''") + "'" for m in batch)
        sql = (f"SELECT {cols} FROM [Quickbase1].[InventoryTrack].[Inventory_Flow] "
               f"WHERE [Mstyle] IN ({in_clause})")
        rows = cdata_query(sql, description=f"inv-flow {bi // BATCH + 1}/{n_batches}")
        print(f"  [InvMgmt] inv-flow batch {bi // BATCH + 1}/{n_batches} — {len(rows or [])} rows", flush=True)
        for r in rows or []:
            # Tolerate either CData-sanitized or original QB label form
            def get(col, _r=r):
                if col in _r: return _r[col]
                alt1 = col.replace("_", " ")
                if alt1 in _r: return _r[alt1]
                alt2 = col.replace(" ", "_")
                if alt2 in _r: return _r[alt2]
                return None
            ms = _as_str(get("Mstyle"))
            if not ms:
                continue
            # Multi-pack / Kit detection: Kit_Style_ is the boolean.  When True,
            # this mstyle is built from _Pcs_Kit_USE_ pcs of Root_Mstyle.
            _kit_flag = get("Kit_Style_")
            is_multi = (_kit_flag is True or _kit_flag == 1 or
                        (isinstance(_kit_flag, str) and _kit_flag.lower() in ("true","1","yes")))
            pcs_per_kit = max(1, _to_num(get("_Pcs_Kit_USE_"), default=1))
            out[ms] = {
            "country":      _as_str(get("Country")),
            "style_":       _as_str(get("Style_")),
            "season":       _as_str(get("Season")),
            "item_rank":    _as_str(get("Item_Rank")),
            "item_status_flow": _as_str(get("Item_Status")),  # Inv-Flow's own item status
            "sub_status":   _as_str(get("Sub_Status")),
            "nvo":          _b(get("NVO")),
            "new_item_no_prj": _b(get("New_Item_No_Prj_")),

            "opt_wos_base": float(_to_num(get("Opt_WOS"))),
            "opt_wos_final":float(_to_num(get("Opt_WOS_Final"))),
            "opt_oh":       _to_num(get("Opt_OH")),
            "next_rcpt":    _to_date(get("Next_Avl_Rcpt_Dt")),
            "lt_trans_days":_to_num(get("LT_Trans_Days")),
            "transit_days": _to_num(get("Transit_Days")),
            "lt_wks":       float(_to_num(get("LT_Wks_"))),
            "cny_weeks":    float(_to_num(get("CNY_Weeks"))),
            "lt_opt_weeks": float(_to_num(get("LT_Opt_Weeks"))),
            "moq":          _to_num(get("MOQ")),

            "open_pos_raw": _as_str(get("Open_Supplier_POs")),

            "is_multi":     bool(is_multi),
            "pcs_per_kit":  int(pcs_per_kit),
            "root_mstyle":  _as_str(get("Root_Mstyle")),
            "qty_oh_root":  _to_num(get("ATS_Qty_OH_root_")),
            "it_iw_root":   _to_num(get("I_T_I_W_root_")),
            "ats_oh_oo_root": _to_num(get("ATS_OH_OO_Root_")),

            # Inventory positions
            "qty_oh":       _to_num(get("Qty_OH_")),
            "ats_qty_oh":   _to_num(get("ATS_Qty_OH_")),
            "ats_now":      _to_num(get("ATS_Now")),
            "ats_oh_oo":    _to_num(get("ATS_OH_OO_")),
            "ats_oh_oo_w_kits": _to_num(get("ATS_OH_OO_w_kits_")),
            "ats_qty_not_alloc": _to_num(get("ATS_Qty_not_alloc_d_")),
            "nj_ats_oh":    _to_num(get("NJ_ATS_OH_")),
            "ca_ats_oh":    _to_num(get("CA_ATS_OH_")),
            "hold_qty":     _to_num(get("Hold_Order_Qty")),
            "it_qty":       _to_num(get("I_T_")),
            "iw_qty":       _to_num(get("I_W_")),
            "it_iw":        _to_num(get("I_T_I_W_")),
            "it_iw_kits":   _to_num(get("I_W_I_T_w_Kits")),
            "open_cust_po_qty": _to_num(get("Open_Cust_PO_Qty")),
            "test_order_qty": _to_num(get("Test_Order_Qty")),
            "exclude_po_wos": _to_num(get("Exclude_PO_from_WOS_Qty")),

            # WOS metrics
            "ats_wos_oh":   float(_to_num(get("ATS_WOS_OH_"))),
            "ats_wos_oh_oo": float(_to_num(get("ATS_WOS_OH_OO_"))),
            "ats_wos_oh_oo_w_kits": float(_to_num(get("ATS_WOS_OH_OO_w_kits_"))),
            "ats_wos_oh_oo_wo_test": float(_to_num(get("ATS_WOS_OH_OO_w_o_test_exclusions_"))),
            "ats_oh_it_booked_wos": float(_to_num(get("ATS_OH_I_T_Booked_not_O_W_WOS"))),

            # OOS
            "days_oos_next_rcpt": _to_num(get("Days_OOS_till_Next_Rcpt")),
            "days_oos_l12m":      _to_num(get("Days_OOS_L12m")),
            "last_oos_date":      _to_date(get("Last_OOS_Date")),

            # Demand single-values
            "prj_wk":          float(_to_num(get("Prj_Wk"))),
            "max_prj_wk":      float(_to_num(get("Maximum_Prj_Wk"))),
            "prj_l4w_change":  float(_to_num(get("_Prj_L4w_all_"))),
            "prj_26wks":       _to_num(get("Prj_26Wks")),

            # Shipments
            "shp_wk_l4":       float(_to_num(get("Shp_Wk_L4"))),
            "shp_wk_l13":      float(_to_num(get("Shp_Wk_L13"))),
            "tot_shpd_l13w":   _to_num(get("Tot_Shpd_L13w")),
            "tot_shpd_l4":     _to_num(get("Tot_Shpd_L4")),
            "tot_shpd_ltd":    _to_num(get("Tot_Shpd_LTD")),
            "last_shp_date":   _to_date(get("Last_Shp_Date_raw_")),

            # Key dates
            "date_1st_rcvd":   _to_date(get("Date_1st_Rcvd")),
            "last_whs_rcvd":   _to_date(get("Last_Whs_Rcvd_Date")),
            "first_shpd_date": _to_date(get("1st_Shpd_Date")),
            "first_out_date":  _to_date(get("1st_Out_Date")),

            # Flags
            "active_kl":       _b(get("Active_KL_")),
            "amz_do_not_ship": _b(get("AMZ_DO_NOT_SHIP")),
            "amz_suppression": _b(get("AMZ_Suppression_")),
            "transfer_qty_open": _b(get("Transfer_Qty_Open_")),

            # Text summaries (detail pane)
            "shipment_status_summary": _as_str(get("Shipment_Status_Summary_")),
            "ats_summary":     _as_str(get("ATS_Summary_")),
            "inventory_notes": _as_str(get("Inventory_Notes_")),
            "style_alert":     _as_str(get("Style_Alert_Message")),
            "oos_priority_notes": _as_str(get("OOS_Priority_Notes")),
            "active_replen_customers": _as_str(get("Active_Replen_Customers")),

            # Extended detail-pane fields — populated by pull_inv_flow_extended()
            "size_ct": "", "fragrance": "", "pvt_lbl_excl": False, "commit_item": False,
            "inner_pack": 0, "master_pack": 0, "oos_dates": "",
            "over_committed_qty": 0, "ovr_comt_wos": 0.0,
            "invtry_age_days": 0, "aged_inv_0_90": 0, "aged_inv_91_180": 0,
            "aged_inv_181_365": 0, "aged_inv_365plus": 0, "pct_time_in_stock": 0.0,

            "beg": [_to_num(get(c)) for c in _INV_FLOW_BEG],
            "rcv": [_to_num(get(c)) for c in _INV_FLOW_RCV],
            "prj": [_to_num(get(c)) for c in _INV_FLOW_PRJ],
        }
    _INV_FLOW_MSTYLES = list(out.keys())
    pull_inv_flow_extended(out)
    return out


def pull_inv_flow_extended(out: dict) -> None:
    """Pull extended detail-pane fields in a single unfiltered SELECT — same
    approach as pull_inv_flow(), so it's one CData call, not 56.
    Any bad column name here fails gracefully without affecting the main pull."""
    if not out:
        return
    cols = "[Mstyle], " + ", ".join("[" + c + "]" for c in _INV_FLOW_META_EXT)
    sql = f"SELECT {cols} FROM [Quickbase1].[InventoryTrack].[Inventory_Flow]"
    try:
        rows = cdata_query(sql, description="inv-flow-ext pull")
        merged = 0
        for r in (rows or []):
            def _get(col, _r=r):
                if col in _r:
                    return _r[col]
                alt = col.replace("_", " ")
                if alt in _r:
                    return _r[alt]
                return None
            def _bx(v):
                return v is True or v == 1 or (isinstance(v, str) and v.lower() in ("true", "1", "yes"))
            ms = _as_str(_get("Mstyle"))
            if ms not in out:
                continue
            out[ms].update({
                "size_ct":            _as_str(_get("Size_Ct")),
                "fragrance":          _as_str(_get("Fragrance")),
                "pvt_lbl_excl":       _bx(_get("Prvte_Lbl_Excl_")),
                "commit_item":        _bx(_get("Commit_Item_")),
                "inner_pack":         _to_num(_get("Inner_Pack")),
                "master_pack":        _to_num(_get("Master_Pack")),
                "oos_dates":          _as_str(_get("OOS_Dates_MM_DD_YY_")),
                "over_committed_qty": _to_num(_get("Over_Committed_Qty")),
                "ovr_comt_wos":       float(_to_num(_get("Ovr_Comt_WOS"))),
                "invtry_age_days":    _to_num(_get("Invtry_Age_Days_")),
                "aged_inv_0_90":      _to_num(_get("Aged_Inv_0_90_days")),
                "aged_inv_91_180":    _to_num(_get("Aged_Inv_91_180_days")),
                "aged_inv_181_365":   _to_num(_get("Aged_Inv_181_365_days")),
                "aged_inv_365plus":   _to_num(_get("Aged_Inv_365_days")),
                "pct_time_in_stock":  float(_to_num(_get("_Time_In_Stock_since_2_16_22_"))),
            })
            merged += 1
        if merged:
            print(f"  [OK] extended fields: {merged} mstyles enriched", flush=True)
        else:
            print("  [warn] extended pull returned no data — detail pane will show defaults", flush=True)
    except Exception as e:
        print(f"  [warn] inv-flow-ext failed: {e} — detail pane will show defaults", flush=True)


# Populated once at startup by build_records — the 26 rolling manual prj labels
_MANUAL_PRJ_COLS: list[str] = []
# Populated by pull_inv_flow — used by pull_projections_rollup to batch the meta query
_INV_FLOW_MSTYLES: list[str] = []


def pull_projections_rollup(mstyle_filter: Optional[list[str]] = None) -> dict[str, dict]:
    """Pull active Projections, group by Mstyle, return per-mstyle aggregates
    including 26-week summed manual demand.

    Strategy:
      1) BULK pull (one query) of just the metadata columns — small payload,
         reliable.  Builds the rollup of description/brand/mgr/status/customers.
      2) BATCHED pull of the 26 manual prj columns in groups of 100 mstyles to
         stay under CData's response-size limit (the combined select of meta+26
         manual cols × 5000 rows was returning empty due to size).
    """
    global _MANUAL_PRJ_COLS
    if not _MANUAL_PRJ_COLS:
        print(f"[InvMgmt] Discovering current manual projection columns from QB...", flush=True)
        _MANUAL_PRJ_COLS = _discover_prj_cols()
        print(f"  [OK] {len(_MANUAL_PRJ_COLS)} cols: {_MANUAL_PRJ_COLS[0]} -> {_MANUAL_PRJ_COLS[-1]}", flush=True)

    # ── Step 1: BATCHED metadata pull (CData rejects unfiltered Projections
    # selects — must filter by a key list).  Batch by Mstyle in chunks of 1000.
    mstyles_to_pull = mstyle_filter or list(_INV_FLOW_MSTYLES)
    META_BATCH = 1000
    n_batches = max(1, (len(mstyles_to_pull) + META_BATCH - 1) // META_BATCH)
    print(f"[InvMgmt]   batched projections metadata pull ({n_batches} batches × {META_BATCH} mstyles)...", flush=True)
    all_rows: list[dict] = []
    n_skipped = 0
    for i in range(0, len(mstyles_to_pull), META_BATCH):
        slice_ = mstyles_to_pull[i:i + META_BATCH]
        in_clause = ", ".join("'" + m.replace("'", "''") + "'" for m in slice_)
        sql_meta = (f"SELECT {_PROJ_PULL_COLS} "
                    f"FROM [Quickbase1].[InventoryTrack].[Projections] "
                    f"WHERE ([Status_Cust] LIKE 'A%' OR [Status_Cust] LIKE 'FD%') "
                    f"AND [Mstyle] IN ({in_clause})")
        try:
            br = cdata_query(sql_meta, description=f"proj-meta {i//META_BATCH+1}/{n_batches}")
            all_rows.extend(br or [])
        except Exception as e:
            n_skipped += len(slice_)
            print(f"  [WARN] proj-meta batch {i//META_BATCH+1} failed: {e}", flush=True)
        if (i // META_BATCH + 1) % 10 == 0 or (i + META_BATCH) >= len(mstyles_to_pull):
            print(f"  [InvMgmt] proj-meta {i//META_BATCH+1}/{n_batches} — {len(all_rows)} acct-mstyle rows accumulated", flush=True)
    print(f"  [OK] {len(all_rows)} acct-mstyle rows total"
          f"{f' ({n_skipped} mstyles skipped due to errors)' if n_skipped else ''}", flush=True)
    rows = all_rows

    by_mstyle: dict[str, dict] = {}
    acct_msyle_keys: list[str] = []   # all keys for the manual-cols batched pull
    acct_to_customer: dict[str, str] = {}   # acct_mstyle_key -> customer name
    acct_to_mstyle: dict[str, str]   = {}   # acct_mstyle_key -> mstyle
    for r in rows or []:
        ms = _as_str(r.get("Mstyle"))
        if not ms:
            continue
        d = by_mstyle.setdefault(ms, {
            "description":      _as_str(r.get("Description")),
            "brand":            _as_str(r.get("Brand_Name")),
            "inv_managers":     set(),
            "item_statuses":    [],
            "customers":        0,
            "manual_demand_26w": 0,
            "customer_demand":  [],   # list of {customer, weekly[26]} per acct-mstyle
        })
        mgr = _email_to_name(r.get("Inventory_Manager"))
        if mgr:
            d["inv_managers"].add(mgr)
        status = _as_str(r.get("PT_Item_Status"))
        if status:
            d["item_statuses"].append(status)
        d["customers"] += 1
        akey = _as_str(r.get("Acct_MStyle_Key_"))
        if akey:
            acct_msyle_keys.append(akey)
            acct_to_customer[akey] = _as_str(r.get("Customr_Name"))
            acct_to_mstyle[akey]   = ms

    # Flatten now (before optional manual rollup)
    for ms, d in by_mstyle.items():
        d["inv_manager"] = ", ".join(sorted(d.pop("inv_managers"))) or ""
        statuses = d.pop("item_statuses")
        if statuses:
            from collections import Counter
            d["item_status"] = Counter(statuses).most_common(1)[0][0]
        else:
            d["item_status"] = ""
        d["is_replen"] = bool(re.search(r"\breplen\b", d["item_status"], re.IGNORECASE))

    # ── Step 2: batched manual-demand pull (26 cols × N rows) ───────────
    # Keyed by Acct_MStyle_Key_ (one row per acct-mstyle).  Batched 1000 keys at
    # a time to keep response size under CData's limit.
    if not acct_msyle_keys:
        return by_mstyle

    manual_cols_sql = ", ".join(f"[{c}]" for c in _MANUAL_PRJ_COLS)
    BATCH = 1000
    n_batches = (len(acct_msyle_keys) + BATCH - 1) // BATCH
    n_loaded = 0
    for i in range(0, len(acct_msyle_keys), BATCH):
        slice_ = acct_msyle_keys[i:i + BATCH]
        in_clause = ", ".join("'" + k.replace("'", "''") + "'" for k in slice_)
        # Include Acct_MStyle_Key_ so we can map back to customer name
        sql = (f"SELECT [Acct_MStyle_Key_], [Mstyle], {manual_cols_sql} "
               f"FROM [Quickbase1].[InventoryTrack].[Projections] "
               f"WHERE [Acct_MStyle_Key_] IN ({in_clause})")
        label = f"manual-demand {i//BATCH+1}/{n_batches}"
        try:
            mrows = cdata_query(sql, description=label)
        except Exception as e:
            print(f"  [WARN] {label} failed: {e} — manual rollup incomplete", flush=True)
            continue
        for r in mrows or []:
            ms = _as_str(r.get("Mstyle"))
            akey = _as_str(r.get("Acct_MStyle_Key_"))
            if not ms or ms not in by_mstyle:
                continue
            weekly = [_to_num(r.get(c)) for c in _MANUAL_PRJ_COLS]
            row_sum = sum(weekly)
            by_mstyle[ms]["manual_demand_26w"] += row_sum
            # Keep per-customer weekly breakdown for the Prj cell hover tooltip
            customer = acct_to_customer.get(akey, "")
            # Only retain customers with non-zero demand to keep payload lean
            if row_sum > 0:
                by_mstyle[ms]["customer_demand"].append({
                    "customer": customer,
                    "weekly":   weekly,
                    "total":    row_sum,
                })
        n_loaded += len(mrows or [])
        if (i // BATCH + 1) % 5 == 0 or (i + BATCH) >= len(acct_msyle_keys):
            print(f"  [InvMgmt] manual-demand batch {i//BATCH+1}/{n_batches} — {n_loaded} acct-mstyles loaded", flush=True)

    # Sort each mstyle's customer_demand list by total desc so the hover
    # shows biggest customers first
    for d in by_mstyle.values():
        d["customer_demand"].sort(key=lambda x: -x["total"])

    return by_mstyle


# ─── Recommendation engine ────────────────────────────────────────────────────

def _wk_date(today: date, wi: int) -> date:
    """Convert week index (0-25, where 0 = current week W1) → Sunday-start date."""
    # W1 is the Sunday on/before today
    days_since_sun = (today.weekday() + 1) % 7   # Mon=0..Sun=6 → Sun=0
    w1 = today - timedelta(days=days_since_sun)
    return w1 + timedelta(days=wi * 7)


def _wk_idx_for_date(today: date, target: date) -> int:
    """Which 0-based week index does `target` fall into?"""
    w1 = _wk_date(today, 0)
    return (target - w1).days // 7


def _warehouse_lag_for_country(country: str) -> int:
    return USA_WAREHOUSE_LAG if country.upper() in ("USA", "UNITED STATES") else WAREHOUSE_LAG_DAYS


def compute_derived(rec: MStyleRecord, today: date) -> None:
    """Populate pipeline_*, gap_weeks, overstocked, recommendations on the record."""

    # ── Pipeline + demand totals ─────────────────────────────────────────
    rec.pipeline_total = (rec.beg_inv[0] if rec.beg_inv else 0) + sum(p.qty for p in rec.open_pos)
    rec.demand_26w     = sum(rec.prj) if rec.prj else 0
    if rec.demand_26w > 0:
        safety = rec.opt_wos * (rec.demand_26w / 26.0)
        rec.pipeline_excess = rec.pipeline_total - rec.demand_26w - int(safety)
        rec.pipeline_wos    = rec.pipeline_total * 26.0 / rec.demand_26w
    else:
        rec.pipeline_excess = rec.pipeline_total
        rec.pipeline_wos    = float("inf") if rec.pipeline_total > 0 else 0.0
    rec.overstocked = (rec.pipeline_excess > OVERSTOCK_EXCESS_TH
                       or (rec.demand_26w > 0 and rec.pipeline_wos > OVERSTOCK_WOS_TH))

    # ── Gap detection (Replen items only) ───────────────────────────────
    if rec.is_replen and rec.opt_wos > 0:
        nr_idx = _wk_idx_for_date(today, rec.next_rcpt_dt) if rec.next_rcpt_dt else 25
        check_until = min(25, nr_idx) if nr_idx >= 0 else -1
        for i in range(check_until + 1):
            bv = rec.beg_inv[i] if i < len(rec.beg_inv) else 0
            pv = rec.prj[i]     if i < len(rec.prj)     else 0
            if pv > 0 and (bv / pv) < rec.opt_wos:
                rec.gap_weeks.append({
                    "wi": i + 1,
                    "date": _wk_date(today, i).isoformat(),
                    "beg": bv, "prj": pv,
                    "wos": round(bv / pv, 1),
                    "deficit": round(rec.opt_wos - (bv / pv), 1),
                })

    # ── Action recommendations ──────────────────────────────────────────
    # Filter out gap weeks with sub-threshold deficits (small dips not worth fixing)
    actionable_gaps = [g for g in rec.gap_weeks if g["deficit"] >= MIN_RESOLVED_GAP_DEFICIT]
    if not actionable_gaps and rec.gap_weeks:
        # All gaps are small — note but don't pull-up
        pass

    movable = [p for p in rec.open_pos
               if p.status(today, rec.country) in ("MOVABLE", "PULL_UP_NARROW", "FASTER_VESSEL_WINDOW")]
    wh_lag  = _warehouse_lag_for_country(rec.country)
    is_usa  = rec.country.upper() in ("USA", "UNITED STATES")

    # Pre-build a "PO pull-up plan" per gap, then DEDUPE: identical (po, new_etd)
    # tuples get merged into one rec covering all the gaps it solves.
    raw_proposals: list[tuple[OpenPO, date, list[int], str]] = []   # (po, new_etd, gap_wks, action)

    for gap in actionable_gaps:
        if not movable:
            continue
        # Target: warehouse availability by SUNDAY of gap week → ETA = target − warehouse_lag
        gap_week_start = _wk_date(today, gap["wi"] - 1)
        target_eta = gap_week_start - timedelta(days=wh_lag)
        # Find a movable PO whose adjusted ETD lands the receipt in time
        candidates = sorted(movable, key=lambda p: p.etd or date.max)
        for po in candidates:
            if po.etd is None:
                continue
            transit = po.transit_days() or rec.transit_days or 26
            min_etd = today + timedelta(days=ETD_LOCK_DAYS)
            in_fast_window = (po.days_to_etd(today) is not None
                              and 8 <= po.days_to_etd(today) <= FASTER_VESSEL_HI)
            # USA only allows pull-up; imported can use faster vessel
            if in_fast_window and not is_usa:
                # Faster vessel: keep ETD, swap to fast transit
                proposed_eta = po.etd + timedelta(days=FAST_VESSEL_TRANSIT)
                if proposed_eta <= target_eta + timedelta(days=3):   # tolerate 3-day slack
                    raw_proposals.append((po, po.etd, [gap["wi"]], "FASTER_VESSEL"))
                    break
            new_etd = max(min_etd, target_eta - timedelta(days=transit))
            if new_etd >= po.etd:
                continue  # PO already early enough — would be a no-op
            pullup_days = (po.etd - new_etd).days
            if pullup_days < MIN_PULLUP_DAYS:
                continue  # too small to be worth supplier effort
            raw_proposals.append((po, new_etd, [gap["wi"]], "PULL_UP"))
            break  # only one PO per gap

    # Deduplicate: group by (action, po_number) only — keep the MOST AGGRESSIVE
    # (earliest) revised ETD per PO, since pulling that early implicitly fixes
    # every later gap too.  Merge ALL gap labels into the surviving rec.
    grouped: dict[tuple, list] = {}   # (action, po_num) -> [po, earliest_new_etd, all_gap_wks]
    for po, new_etd, gap_wks, action in raw_proposals:
        key = (action, po.po_number)
        if key not in grouped:
            grouped[key] = [po, new_etd, list(gap_wks)]
        else:
            # Keep the earliest revised ETD; accumulate gap weeks
            if new_etd < grouped[key][1]:
                grouped[key][1] = new_etd
            grouped[key][2].extend(gap_wks)

    for (action, po_num), (po, new_etd, wks) in grouped.items():
        wks = sorted(set(wks))
        if len(wks) <= 4:
            wk_str = ", ".join(f"W{w}" for w in wks)
        else:
            wk_str = f"W{wks[0]}-W{wks[-1]}" if (wks[-1] - wks[0] + 1) == len(wks) else f"W{wks[0]}-W{wks[-1]} ({len(wks)} weeks)"
        transit = po.transit_days() or rec.transit_days or 26
        new_eta = new_etd + timedelta(days=transit)
        wh_avail = new_eta + timedelta(days=wh_lag)
        if action == "FASTER_VESSEL":
            rec.recommendations.append(Recommendation(
                action="FASTER_VESSEL",
                po_number=po.po_number, supplier=po.supplier,
                qty_affected=po.qty,
                orig_etd=po.etd, proposed_etd=po.etd,
                orig_eta=po.eta, proposed_eta=po.etd + timedelta(days=FAST_VESSEL_TRANSIT),
                delta_days=0, delta_qty=0,
                priority="HIGH",
                reason=f"Request faster vessel — covers gaps in {wk_str}",
                in_transit_qty=po.in_transit_qty, in_work_qty=po.in_work_qty,
                po_total_qty=po.qty,
                gap_weeks_fixed=wks,
            ))
        else:
            rec.recommendations.append(Recommendation(
                action="PULL_UP",
                po_number=po.po_number, supplier=po.supplier,
                qty_affected=po.qty,
                orig_etd=po.etd, proposed_etd=new_etd,
                orig_eta=po.eta, proposed_eta=new_eta,
                delta_days=(new_etd - po.etd).days, delta_qty=0,
                priority="HIGH",
                reason=f"Pull-up — covers gaps in {wk_str}",
                in_transit_qty=po.in_transit_qty, in_work_qty=po.in_work_qty,
                po_total_qty=po.qty,
                gap_weeks_fixed=wks,
            ))

    # If we have actionable gaps and NO recs were generated, emit one NO_LEVER note
    if actionable_gaps and not rec.recommendations:
        rec.recommendations.append(Recommendation(
            action="NO_LEVER", po_number="", supplier="", qty_affected=0,
            orig_etd=None, proposed_etd=None, orig_eta=None, proposed_eta=None,
            delta_days=0, delta_qty=0,
            priority="HIGH",
            reason=(f"{len(actionable_gaps)} actionable gap weeks but no movable PO "
                   f"can land in time. Live with the gap or escalate."),
        ))

    # 2) Overstock fix: cancel furthest PO if ETD > today + 60 days, else push out
    # Partial-shipment minimum is MOQ/2 if known, else PARTIAL_MIN_PCS fallback.
    partial_min = max(int(rec.moq // 2) if rec.moq > 0 else PARTIAL_MIN_PCS, 1)
    if rec.overstocked and rec.open_pos:
        furthest = max(rec.open_pos, key=lambda p: p.etd or date.min)
        excess = max(0, rec.pipeline_excess)
        if furthest.etd and (furthest.etd - today).days > CANCEL_HORIZON_DAYS:
            cancel_qty = min(excess, furthest.qty)
            if cancel_qty >= partial_min:
                rec.recommendations.append(Recommendation(
                    action="CANCEL",
                    po_number=furthest.po_number, supplier=furthest.supplier,
                    qty_affected=cancel_qty,
                    orig_etd=furthest.etd, proposed_etd=furthest.etd,
                    orig_eta=furthest.eta, proposed_eta=furthest.eta,
                    delta_days=0,
                    delta_qty=-cancel_qty,
                    priority="MEDIUM",
                    reason=(f"Pipeline excess {excess:,} pcs · {furthest.po_number} ETD "
                           f"{furthest.etd} is >60d out → cancel {cancel_qty:,} pcs (partial floor MOQ/2 = {partial_min:,})"),
                    in_transit_qty=furthest.in_transit_qty, in_work_qty=furthest.in_work_qty,
                    po_total_qty=furthest.qty,
                ))
        else:
            push_qty = min(excess, furthest.qty)
            if push_qty < partial_min:
                pass
            elif (furthest.qty - push_qty) >= partial_min:
                # SPLIT: keep (qty - push_qty), push push_qty later
                keep_qty = furthest.qty - push_qty
                transit_days = furthest.transit_days() or rec.transit_days or 26
                new_etd = furthest.etd + timedelta(weeks=8) if furthest.etd else None
                new_eta = new_etd + timedelta(days=transit_days) if new_etd else None
                rec.recommendations.append(Recommendation(
                    action="SPLIT",
                    po_number=furthest.po_number, supplier=furthest.supplier,
                    qty_affected=push_qty,
                    orig_etd=furthest.etd, proposed_etd=new_etd,
                    orig_eta=furthest.eta, proposed_eta=new_eta,
                    delta_days=(new_etd - furthest.etd).days if (new_etd and furthest.etd) else 0,
                    delta_qty=0,
                    priority="MEDIUM",
                    reason=f"Split shipment — keep {keep_qty:,} at original ETD, push {push_qty:,} to free up early-window inventory",
                    in_transit_qty=furthest.in_transit_qty, in_work_qty=furthest.in_work_qty,
                    po_total_qty=furthest.qty,
                    keep_qty=keep_qty, push_qty=push_qty,
                ))
            else:
                # PUSH whole PO out
                transit_days = furthest.transit_days() or rec.transit_days or 26
                new_etd = furthest.etd + timedelta(weeks=8) if furthest.etd else None
                new_eta = new_etd + timedelta(days=transit_days) if new_etd else None
                rec.recommendations.append(Recommendation(
                    action="PUSH_OUT",
                    po_number=furthest.po_number, supplier=furthest.supplier,
                    qty_affected=furthest.qty,
                    orig_etd=furthest.etd, proposed_etd=new_etd,
                    orig_eta=furthest.eta, proposed_eta=new_eta,
                    delta_days=(new_etd - furthest.etd).days if (new_etd and furthest.etd) else 0,
                    delta_qty=0,
                    priority="MEDIUM",
                    reason=f"Push entire PO out ~8 weeks — splitting would leave a partial below MOQ/2 = {partial_min:,}",
                    in_transit_qty=furthest.in_transit_qty, in_work_qty=furthest.in_work_qty,
                    po_total_qty=furthest.qty,
                    push_qty=furthest.qty,
                ))

    # 3) Priority roll-up
    if any(r.priority == "CRITICAL" for r in rec.recommendations):
        rec.priority = "CRITICAL"
    elif any(g["deficit"] >= 2.0 for g in rec.gap_weeks):
        rec.priority = "HIGH"
    elif rec.gap_weeks or rec.overstocked:
        rec.priority = "MEDIUM"
    else:
        rec.priority = "LOW"


# ─── Main build ───────────────────────────────────────────────────────────────

def build_records(mstyle_filter: Optional[list[str]] = None,
                  today: Optional[date] = None) -> list[MStyleRecord]:
    today = today or date.today()
    print(f"[InvMgmt] Pulling Inventory Flow (mstyle grain)...")
    inv = pull_inv_flow(mstyle_filter)
    print(f"  [OK] {len(inv)} mstyles loaded from Inventory Flow")

    print(f"[InvMgmt] Pulling Projections rollup...")
    proj = pull_projections_rollup(list(inv.keys()) if not mstyle_filter else mstyle_filter)
    print(f"  [OK] {len(proj)} mstyles loaded from Projections")

    records: list[MStyleRecord] = []
    for ms, ivf in inv.items():
        pr = proj.get(ms, {})
        opt_wos = ivf["opt_wos_final"] or ivf["opt_wos_base"] or OPT_WOS_DEFAULT
        rec = MStyleRecord(
            mstyle=ms,
            description=pr.get("description", ""),
            brand=pr.get("brand", ""),
            inv_manager=pr.get("inv_manager", ""),
            country=ivf["country"],
            item_status=pr.get("item_status", ""),
            customer_count=pr.get("customers", 0),
            is_replen=pr.get("is_replen", False),
            beg_inv=ivf["beg"],
            rcv=ivf["rcv"],
            prj=ivf["prj"],
            opt_wos=opt_wos,
            next_rcpt_dt=ivf["next_rcpt"],
            lt_trans_days=ivf["lt_trans_days"],
            transit_days=ivf["transit_days"],
            open_pos=parse_open_pos(ivf["open_pos_raw"]),
            manual_demand_26w=pr.get("manual_demand_26w", 0),
            customer_demand=pr.get("customer_demand", []),
            # Multi-pack
            is_multi=ivf["is_multi"],
            pcs_per_kit=ivf["pcs_per_kit"],
            root_mstyle=ivf["root_mstyle"],
            qty_oh_root=ivf["qty_oh_root"],
            it_iw_root=ivf.get("it_iw_root", 0),
            ats_oh_oo_root=ivf.get("ats_oh_oo_root", 0),
            # Identity / context
            style_=ivf.get("style_", ""),
            season=ivf.get("season", ""),
            item_rank=ivf.get("item_rank", ""),
            item_status_flow=ivf.get("item_status_flow", ""),
            sub_status=ivf.get("sub_status", ""),
            nvo=ivf.get("nvo", False),
            new_item_no_prj=ivf.get("new_item_no_prj", False),
            active_kl=ivf.get("active_kl", False),
            amz_do_not_ship=ivf.get("amz_do_not_ship", False),
            amz_suppression=ivf.get("amz_suppression", False),
            transfer_qty_open=ivf.get("transfer_qty_open", False),
            # PO timing extras
            opt_oh=ivf.get("opt_oh", 0),
            lt_wks=ivf.get("lt_wks", 0.0),
            cny_weeks=ivf.get("cny_weeks", 0.0),
            lt_opt_weeks=ivf.get("lt_opt_weeks", 0.0),
            moq=ivf.get("moq", 0),
            # Inventory positions
            qty_oh=ivf.get("qty_oh", 0),
            ats_qty_oh=ivf.get("ats_qty_oh", 0),
            ats_now=ivf.get("ats_now", 0),
            ats_oh_oo=ivf.get("ats_oh_oo", 0),
            ats_oh_oo_w_kits=ivf.get("ats_oh_oo_w_kits", 0),
            ats_qty_not_alloc=ivf.get("ats_qty_not_alloc", 0),
            nj_ats_oh=ivf.get("nj_ats_oh", 0),
            ca_ats_oh=ivf.get("ca_ats_oh", 0),
            hold_qty=ivf.get("hold_qty", 0),
            it_qty=ivf.get("it_qty", 0),
            iw_qty=ivf.get("iw_qty", 0),
            it_iw=ivf.get("it_iw", 0),
            it_iw_kits=ivf.get("it_iw_kits", 0),
            open_cust_po_qty=ivf.get("open_cust_po_qty", 0),
            test_order_qty=ivf.get("test_order_qty", 0),
            exclude_po_wos=ivf.get("exclude_po_wos", 0),
            # WOS metrics
            ats_wos_oh=ivf.get("ats_wos_oh", 0.0),
            ats_wos_oh_oo=ivf.get("ats_wos_oh_oo", 0.0),
            ats_wos_oh_oo_w_kits=ivf.get("ats_wos_oh_oo_w_kits", 0.0),
            ats_wos_oh_oo_wo_test=ivf.get("ats_wos_oh_oo_wo_test", 0.0),
            ats_oh_it_booked_wos=ivf.get("ats_oh_it_booked_wos", 0.0),
            # OOS
            days_oos_next_rcpt=ivf.get("days_oos_next_rcpt", 0),
            days_oos_l12m=ivf.get("days_oos_l12m", 0),
            last_oos_date=ivf.get("last_oos_date"),
            # Demand single-values
            prj_wk=ivf.get("prj_wk", 0.0),
            max_prj_wk=ivf.get("max_prj_wk", 0.0),
            prj_l4w_change=ivf.get("prj_l4w_change", 0.0),
            prj_26wks=ivf.get("prj_26wks", 0),
            # Shipments
            shp_wk_l4=ivf.get("shp_wk_l4", 0.0),
            shp_wk_l13=ivf.get("shp_wk_l13", 0.0),
            tot_shpd_l13w=ivf.get("tot_shpd_l13w", 0),
            tot_shpd_l4=ivf.get("tot_shpd_l4", 0),
            tot_shpd_ltd=ivf.get("tot_shpd_ltd", 0),
            last_shp_date=ivf.get("last_shp_date"),
            # Key dates
            date_1st_rcvd=ivf.get("date_1st_rcvd"),
            last_whs_rcvd=ivf.get("last_whs_rcvd"),
            first_shpd_date=ivf.get("first_shpd_date"),
            first_out_date=ivf.get("first_out_date"),
            # Text summaries
            shipment_status_summary=ivf.get("shipment_status_summary", ""),
            ats_summary=ivf.get("ats_summary", ""),
            inventory_notes=ivf.get("inventory_notes", ""),
            style_alert=ivf.get("style_alert", ""),
            oos_priority_notes=ivf.get("oos_priority_notes", ""),
            active_replen_customers=ivf.get("active_replen_customers", ""),
            # Product attributes
            size_ct=ivf.get("size_ct", ""),
            fragrance=ivf.get("fragrance", ""),
            pvt_lbl_excl=ivf.get("pvt_lbl_excl", False),
            commit_item=ivf.get("commit_item", False),
            inner_pack=ivf.get("inner_pack", 0),
            master_pack=ivf.get("master_pack", 0),
            oos_dates=ivf.get("oos_dates", ""),
            # Overstock management
            over_committed_qty=ivf.get("over_committed_qty", 0),
            ovr_comt_wos=ivf.get("ovr_comt_wos", 0.0),
            # Aged inventory
            invtry_age_days=ivf.get("invtry_age_days", 0),
            aged_inv_0_90=ivf.get("aged_inv_0_90", 0),
            aged_inv_91_180=ivf.get("aged_inv_91_180", 0),
            aged_inv_181_365=ivf.get("aged_inv_181_365", 0),
            aged_inv_365plus=ivf.get("aged_inv_365plus", 0),
            pct_time_in_stock=ivf.get("pct_time_in_stock", 0.0),
        )
        compute_derived(rec, today)
        records.append(rec)

    # Sort: priority desc → gap count desc → mstyle asc
    pri_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    records.sort(key=lambda r: (pri_order.get(r.priority, 9), -len(r.gap_weeks), r.mstyle))
    return records


def print_summary(records: list[MStyleRecord]) -> None:
    """Console-friendly dry-run output for sanity-checking."""
    print()
    print(f"  {'Mstyle':<15} {'Country':<10} {'Pri':<8} {'Gaps':>5} {'PipeExc':>8} {'PipeWOS':>8} {'POs':>4} {'Recs':>5}")
    print(f"  {'-'*15} {'-'*10} {'-'*8} {'-'*5} {'-'*8} {'-'*8} {'-'*4} {'-'*5}")
    for r in records[:40]:
        print(f"  {r.mstyle:<15} {r.country[:10]:<10} {r.priority:<8} "
              f"{len(r.gap_weeks):>5} {r.pipeline_excess:>8,} {r.pipeline_wos:>8.1f} "
              f"{len(r.open_pos):>4} {len(r.recommendations):>5}")
    print()
    print(f"  {len(records)} mstyles total · {sum(1 for r in records if r.gap_weeks)} with gaps · "
          f"{sum(1 for r in records if r.overstocked)} overstocked · "
          f"{sum(1 for r in records if r.recommendations)} with recommendations")


# ─── HTTP server + HTML UI (minimal Phase 2) ──────────────────────────────────

import gzip
import http.server
import socketserver
import threading
import webbrowser

# Globals populated at server startup
_RECORDS: list[MStyleRecord] = []
_PAYLOAD_JSON_GZ: bytes = b""
_DATA_AS_OF: str = ""          # ISO timestamp of last QB pull, served via /api/meta.json


def _rec_to_json(r: MStyleRecord) -> dict:
    """Serialize a MStyleRecord to a JSON-safe dict for the browser."""
    def _d(x): return x.isoformat() if x else None
    return {
        "mstyle":             r.mstyle,
        "description":        r.description,
        "brand":              r.brand,
        "inv_manager":        r.inv_manager,
        "country":            r.country,
        "item_status":        r.item_status,
        "customer_count":     r.customer_count,
        "is_replen":          r.is_replen,
        "beg_inv":            r.beg_inv,
        "rcv":                r.rcv,
        "prj":                r.prj,
        "opt_wos":            r.opt_wos,
        "next_rcpt_dt":       _d(r.next_rcpt_dt),
        "lt_trans_days":      r.lt_trans_days,
        "transit_days":       r.transit_days,
        "open_pos": [
            {
                "po_number":      p.po_number,
                "supplier":       p.supplier,
                "in_transit_qty": p.in_transit_qty,
                "in_work_qty":    p.in_work_qty,
                "qty":            p.qty,
                "etd":            _d(p.etd),
                "eta":            _d(p.eta),
                "transit_days":   p.transit_days(),
                "status":         p.status(date.today(), r.country),
            }
            for p in r.open_pos
        ],
        "manual_demand_26w":  r.manual_demand_26w,
        "customer_demand":    r.customer_demand,   # per-customer weekly breakdown for Prj hover
        # Multi-pack / kit context — surfaced in the master table + detail pane
        "is_multi":           r.is_multi,
        "pcs_per_kit":        r.pcs_per_kit,
        "root_mstyle":        r.root_mstyle,
        "qty_oh_root":        r.qty_oh_root,
        # Derived: kits assembleable from current root inventory
        "assembleable_kits":  (r.qty_oh_root // r.pcs_per_kit) if (r.is_multi and r.pcs_per_kit > 0) else 0,
        "pipeline_total":     r.pipeline_total,
        "demand_26w":         r.demand_26w,
        "pipeline_excess":    r.pipeline_excess,
        "pipeline_wos":       round(r.pipeline_wos, 1) if r.pipeline_wos != float("inf") else None,
        "gap_weeks":          r.gap_weeks,
        "overstocked":        r.overstocked,
        "recommendations": [
            {
                "action":         rc.action,
                "po_number":      rc.po_number,
                "supplier":       rc.supplier,
                "qty_affected":   rc.qty_affected,
                "orig_etd":       _d(rc.orig_etd),
                "proposed_etd":   _d(rc.proposed_etd),
                "orig_eta":       _d(rc.orig_eta),
                "proposed_eta":   _d(rc.proposed_eta),
                "delta_days":     rc.delta_days,
                "delta_qty":      rc.delta_qty,
                "priority":       rc.priority,
                "reason":         rc.reason,
                # Current PO state
                "in_transit_qty": rc.in_transit_qty,
                "in_work_qty":    rc.in_work_qty,
                "po_total_qty":   rc.po_total_qty,
                # SPLIT distribution
                "keep_qty":       rc.keep_qty,
                "push_qty":       rc.push_qty,
                "gap_weeks_fixed": rc.gap_weeks_fixed,
            }
            for rc in r.recommendations
        ],
        "priority":           r.priority,
        "flag":               r.flag,
        # Multi-pack root extras
        "it_iw_root":         r.it_iw_root,
        "ats_oh_oo_root":     r.ats_oh_oo_root,
        # Identity / context (extended)
        "style_":             r.style_,
        "season":             r.season,
        "item_rank":          r.item_rank,
        "item_status_flow":   r.item_status_flow,
        "sub_status":         r.sub_status,
        "nvo":                r.nvo,
        "new_item_no_prj":    r.new_item_no_prj,
        "active_kl":          r.active_kl,
        "amz_do_not_ship":    r.amz_do_not_ship,
        "amz_suppression":    r.amz_suppression,
        "transfer_qty_open":  r.transfer_qty_open,
        # PO timing extras
        "opt_oh":             r.opt_oh,
        "lt_wks":             r.lt_wks,
        "cny_weeks":          r.cny_weeks,
        "lt_opt_weeks":       r.lt_opt_weeks,
        "moq":                r.moq,
        # Inventory positions
        "qty_oh":             r.qty_oh,
        "ats_qty_oh":         r.ats_qty_oh,
        "ats_now":            r.ats_now,
        "ats_oh_oo":          r.ats_oh_oo,
        "ats_oh_oo_w_kits":   r.ats_oh_oo_w_kits,
        "ats_qty_not_alloc":  r.ats_qty_not_alloc,
        "nj_ats_oh":          r.nj_ats_oh,
        "ca_ats_oh":          r.ca_ats_oh,
        "hold_qty":           r.hold_qty,
        "it_qty":             r.it_qty,
        "iw_qty":             r.iw_qty,
        "it_iw":              r.it_iw,
        "it_iw_kits":         r.it_iw_kits,
        "open_cust_po_qty":   r.open_cust_po_qty,
        "test_order_qty":     r.test_order_qty,
        "exclude_po_wos":     r.exclude_po_wos,
        # WOS metrics
        "ats_wos_oh":             r.ats_wos_oh,
        "ats_wos_oh_oo":          r.ats_wos_oh_oo,
        "ats_wos_oh_oo_w_kits":   r.ats_wos_oh_oo_w_kits,
        "ats_wos_oh_oo_wo_test":  r.ats_wos_oh_oo_wo_test,
        "ats_oh_it_booked_wos":   r.ats_oh_it_booked_wos,
        # OOS
        "days_oos_next_rcpt": r.days_oos_next_rcpt,
        "days_oos_l12m":      r.days_oos_l12m,
        "last_oos_date":      _d(r.last_oos_date),
        # Demand single-values
        "prj_wk":             r.prj_wk,
        "max_prj_wk":         r.max_prj_wk,
        "prj_l4w_change":     r.prj_l4w_change,
        "prj_26wks":          r.prj_26wks,
        # Shipments
        "shp_wk_l4":          r.shp_wk_l4,
        "shp_wk_l13":         r.shp_wk_l13,
        "tot_shpd_l13w":      r.tot_shpd_l13w,
        "tot_shpd_l4":        r.tot_shpd_l4,
        "tot_shpd_ltd":       r.tot_shpd_ltd,
        "last_shp_date":      _d(r.last_shp_date),
        # Key dates
        "date_1st_rcvd":      _d(r.date_1st_rcvd),
        "last_whs_rcvd":      _d(r.last_whs_rcvd),
        "first_shpd_date":    _d(r.first_shpd_date),
        "first_out_date":     _d(r.first_out_date),
        # Text summaries (detail pane)
        "shipment_status_summary": r.shipment_status_summary,
        "ats_summary":        r.ats_summary,
        "inventory_notes":    r.inventory_notes,
        "style_alert":        r.style_alert,
        "oos_priority_notes": r.oos_priority_notes,
        "active_replen_customers": r.active_replen_customers,
        # Product attributes
        "size_ct":            r.size_ct,
        "fragrance":          r.fragrance,
        "pvt_lbl_excl":       r.pvt_lbl_excl,
        "commit_item":        r.commit_item,
        "inner_pack":         r.inner_pack,
        "master_pack":        r.master_pack,
        "oos_dates":          r.oos_dates,
        # Overstock management
        "over_committed_qty": r.over_committed_qty,
        "ovr_comt_wos":       round(r.ovr_comt_wos, 1),
        # Aged inventory
        "invtry_age_days":    r.invtry_age_days,
        "aged_inv_0_90":      r.aged_inv_0_90,
        "aged_inv_91_180":    r.aged_inv_91_180,
        "aged_inv_181_365":   r.aged_inv_181_365,
        "aged_inv_365plus":   r.aged_inv_365plus,
        "pct_time_in_stock":  round(r.pct_time_in_stock, 1),
    }


def _build_payload(records: list[MStyleRecord]) -> bytes:
    """Pre-serialize + gzip the records payload for instant /api/records.json."""
    raw = json.dumps([_rec_to_json(r) for r in records], separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=5)


_HTML_PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>P+P Inventory Manager</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin:0; padding:0; }
  body { font-family:"Segoe UI",Arial,sans-serif; font-size:13px; background:#f0f2f5; color:#222; }

  /* ── Loading screen ── */
  #loadingScreen {
    position:fixed; inset:0; background:#0d47a1; z-index:9999;
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    color:#fff; transition:opacity 0.4s ease;
  }
  #loadingScreen.hidden { opacity:0; pointer-events:none; }
  #loadIcon  { font-size:52px; margin-bottom:16px; }
  #loadTitle { font-size:22px; font-weight:700; margin-bottom:4px; }
  #loadSub   { font-size:13px; color:rgba(255,255,255,0.6); margin-bottom:36px; }
  #loadBarWrap { width:340px; height:5px; background:rgba(255,255,255,0.2); border-radius:3px; overflow:hidden; margin-bottom:20px; }
  #loadBar   { height:100%; background:#fff; width:0%; transition:width 0.6s ease; border-radius:3px; }
  #loadStatus { font-size:13px; color:rgba(255,255,255,0.9); margin-bottom:28px; min-height:18px; }
  #loadSteps { font-size:12px; color:rgba(255,255,255,0.5); line-height:2; min-width:240px; }
  #loadSteps .done  { color:rgba(255,255,255,0.9); }
  #loadSteps .done::before { content:"✓ "; }
  #loadSteps .active { color:#fff; font-weight:600; }
  #loadSteps .active::before { content:"▶ "; }
  #loadSteps .pending::before { content:"○ "; }
  .topbar { background:#0d47a1; color:#fff; padding:10px 18px; display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .topbar h1 { font-size:18px; font-weight:600; }
  .stats { display:flex; gap:8px; font-size:12px; align-items:center; flex-wrap:wrap; }
  .stats .stat { background:rgba(255,255,255,0.12); padding:4px 10px; border-radius:4px; }
  .stats .stat b { font-size:14px; }
  .pri-btn {
    font-size:12px; padding:5px 12px; border-radius:18px; cursor:pointer;
    font-family:inherit; font-weight:600;
    transition: transform 0.1s, box-shadow 0.1s;
  }
  .pri-btn:hover { transform: translateY(-1px); box-shadow:0 2px 4px rgba(0,0,0,0.2); }
  .pri-btn.active { box-shadow:0 0 0 2px rgba(255,255,255,0.4); }
  .pri-btn b { font-weight:700; }
  .toolbar { padding:10px 18px; background:#fff; border-bottom:1px solid #d8dce3; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .toolbar input, .toolbar select { font-size:12px; padding:4px 8px; border:1px solid #ccc; border-radius:3px; font-family:inherit; }
  .toolbar input[type=text] { width:240px; }
  table.main { width:100%; background:#fff; border-collapse:collapse; font-size:12px; }
  table.main th { background:#eceff1; padding:6px 8px; text-align:left; border-bottom:2px solid #cfd8dc; white-space:nowrap; cursor:pointer; user-select:none; position:sticky; top:0; }
  table.main td { padding:5px 8px; border-bottom:1px solid #eceff1; vertical-align:top; }
  table.main tr.row { cursor:pointer; }
  table.main tr.row:hover { background:#f5fafd; }
  .pri-CRITICAL { color:#b71c1c; font-weight:700; }
  .pri-HIGH     { color:#e65100; font-weight:600; }
  .pri-MEDIUM   { color:#5d4037; }
  .pri-LOW      { color:#555; }
  .badge { display:inline-block; font-size:10px; font-weight:600; padding:1px 7px; border-radius:8px; }
  .badge-red    { background:#ffebee; color:#b71c1c; }
  .badge-amber  { background:#fff3e0; color:#e65100; }
  .badge-purple { background:#f3e5f5; color:#4a148c; }
  .badge-green  { background:#e8f5e9; color:#1b5e20; }
  .badge-gray   { background:#eeeeee; color:#666; }
  .detail-pane { background:#fafafa; }
  .detail-pane td { padding:0; }
  .detail-pane .dwrap { padding:14px 18px; }
  .section { background:#fff; border:1px solid #e0e0e0; border-radius:5px; padding:10px 14px; margin-bottom:10px; }
  .section h3 { font-size:13px; font-weight:600; color:#0d47a1; margin-bottom:8px; }
  table.subtbl { width:100%; border-collapse:collapse; font-size:11px; }
  table.subtbl th { background:#f5f5f5; padding:3px 6px; text-align:left; border-bottom:1px solid #ddd; font-weight:600; }
  table.subtbl td { padding:3px 6px; border-bottom:1px solid #f3f3f3; }
  table.subtbl td.right { text-align:right; }
  .rec-box { padding:10px 14px; border-left:3px solid #1565c0; background:#f0f7ff; margin-bottom:8px; font-size:12px; border-radius:0 4px 4px 0; }
  .rec-box.priority-HIGH { border-color:#e65100; background:#fff8e1; }
  .rec-box.priority-CRITICAL { border-color:#b71c1c; background:#ffebee; }
  .rec-box.action-NO_LEVER { border-color:#888; background:#f5f5f5; color:#666; }
  .rec-action { font-weight:700; font-size:11px; letter-spacing:0.5px; padding:2px 8px; border-radius:3px; background:rgba(255,255,255,0.6); }
  .rec-action.NO_LEVER       { color:#888; background:#eeeeee; }
  .rec-action.PULL_UP        { color:#fff; background:#1565c0; }
  .rec-action.FASTER_VESSEL  { color:#fff; background:#5e35b1; }
  .rec-action.PUSH_OUT       { color:#fff; background:#e65100; }
  .rec-action.SPLIT          { color:#fff; background:#bf360c; }
  .rec-action.CANCEL         { color:#fff; background:#b71c1c; }
  .rec-body { margin-top:6px; padding-left:4px; }
  .rec-row { padding:2px 0; font-size:11px; color:#333; }
  .rec-row .rec-lbl { display:inline-block; min-width:80px; color:#666; font-weight:600; font-size:10px; text-transform:uppercase; letter-spacing:0.3px; }
  .rec-reason { margin-top:5px; padding:4px 8px; background:rgba(255,255,255,0.5); border-radius:3px; font-size:11px; color:#444; }
  .grid26 { font-size:10px; }
  .grid26 th, .grid26 td { padding:2px 5px; text-align:right; white-space:nowrap; }
  .grid26 th { font-size:9px; }
  .grid26 .lbl { text-align:left; font-weight:600; background:#fafafa; }
  .grid26 .gap { background:#ffebee; color:#b71c1c; font-weight:600; }
  .grid26 .ok { color:#555; }
  .grid26 .neg { color:#b71c1c; }
  .stat-text { font-size:11px; color:#555; }
  .stat-text b { color:#222; }
  .kv-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:6px 18px; font-size:11px; }
  .kv-grid div { padding:2px 0; }
  .kpi-strip { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; padding:8px; background:#fafbfc; border:1px solid #e0e3e8; border-radius:5px; }
  .kpi { flex:0 0 auto; min-width:100px; padding:5px 10px; background:#fff; border:1px solid #e8ecf0; border-radius:4px; cursor:help; }
  .kpi-lbl { font-size:10px; color:#888; text-transform:uppercase; letter-spacing:0.3px; }
  .kpi-val { font-size:14px; font-weight:700; color:#0d47a1; margin-top:1px; }
  /* Force-visible browser tooltip styling — title="" pops the OS tooltip natively */
  .grid26 td[title] { position:relative; }
  table.main td, table.main th { font-size:11px; padding:4px 6px; }
  table.main th.right, table.main td.right { text-align:right; }
  /* Sortable headers + per-column filter row */
  table.main thead tr:first-child th { cursor:pointer; user-select:none; }
  table.main thead tr:first-child th:hover { background:#dde2e6; }
  table.main thead tr.filter-row { background:#f5f7fa; position:sticky; top:24px; }
  table.main thead tr.filter-row th { padding:2px 4px; font-weight:400; }
  table.main thead tr.filter-row input { width:100%; box-sizing:border-box; font-size:10px; padding:2px 4px; border:1px solid #ccc; border-radius:2px; font-family:inherit; background:#fff; }
  table.main thead tr.filter-row input:focus { outline:1px solid #0d47a1; border-color:#0d47a1; }
  table.main thead tr:first-child th { position:sticky; top:0; }
  .sort-arrow { display:inline-block; margin-left:3px; color:#0d47a1; font-size:10px; }
</style>
</head><body>

<!-- Full-screen loading overlay -->
<div id="loadingScreen">
  <div id="loadIcon">🏭</div>
  <div id="loadTitle">P+P Inventory Manager</div>
  <div id="loadSub">Pets + People</div>
  <div id="loadBarWrap"><div id="loadBar"></div></div>
  <div id="loadStatus">Connecting to server…</div>
  <div id="loadSteps">
    <div id="ls1" class="pending">Connect to server</div>
    <div id="ls2" class="pending">Download records</div>
    <div id="ls3" class="pending">Process data</div>
    <div id="ls4" class="pending">Build view</div>
  </div>
</div>

<div class="topbar">
  <img src="/logo.png" alt="Pets+People" style="height:36px;width:auto;object-fit:contain;flex-shrink:0;">
  <h1>🏭 P+P Inventory Manager</h1>
  <div style="display:flex;flex-direction:column;gap:2px;">
    <div class="stats" id="statsBar"></div>
    <div id="dataAsOf" style="font-size:11px;color:rgba(255,255,255,0.6);letter-spacing:0.2px;"></div>
  </div>
  <button onclick="generateRecoSheet()" style="margin-left:auto;padding:6px 14px;background:#fff;color:#0d47a1;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px;white-space:nowrap;">
    📋 Generate Reco Spreadsheet
    <span id="recoBadge" style="background:#e65100;color:#fff;border-radius:10px;padding:1px 8px;font-size:11px;display:none;">0</span>
  </button>
</div>

<div class="toolbar">
  <input type="text" id="searchInput" placeholder="Search mstyle / description / brand">
  <select id="actionFilter">
    <option value="">All actions</option>
    <option value="PULL_UP">PULL_UP</option>
    <option value="FASTER_VESSEL">FASTER_VESSEL</option>
    <option value="PUSH_OUT">PUSH_OUT</option>
    <option value="SPLIT">SPLIT</option>
    <option value="CANCEL">CANCEL</option>
    <option value="NO_LEVER">NO_LEVER</option>
    <option value="__NONE__">No recs (clean)</option>
  </select>
  <select id="countryFilter">
    <option value="">All countries</option>
  </select>
  <select id="brandFilter">
    <option value="">All brands</option>
  </select>
  <select id="invMgrFilter">
    <option value="">All inv mgrs</option>
  </select>
  <label style="font-size:11px;"><input type="checkbox" id="replenOnly" checked> Replen only</label>
  <label style="font-size:11px;"><input type="checkbox" id="gapsOnly"> OOS Risk</label>
  <label style="font-size:11px;"><input type="checkbox" id="overstockOnly"> Overstock</label>
  <label style="font-size:11px;"><input type="checkbox" id="hideMulti" checked> Hide multi-packs</label>
  <button id="clearBtn" style="padding:4px 10px;background:#fff;border:1px solid #999;border-radius:3px;cursor:pointer;font-size:11px;">Clear filters</button>
</div>

<div style="overflow:auto;max-height:calc(100vh - 120px);">
<table class="main" id="mainTable">
  <thead id="theadMain"></thead>
  <tbody id="tbody"></tbody>
</table>
</div>

<script>
let ALL = [];
let FILTERED = [];
// Default sort = inv_manager → brand → mstyle (composite).  When the user
// clicks a column header, currentSort.id switches to that single column.
let currentSort = { id: null, dir: 1 };   // null = composite default; dir: 1=asc, -1=desc
const DEFAULT_SORT_CHAIN = ['inv_manager', 'brand', 'mstyle'];
const colFilters = {};   // colId -> filter string (case-insensitive substring)
let priorityFilter = '';   // '' = all, else 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'

// ── Reco Spreadsheet ────────────────────────────────────────────────────────
let recoSheet = [];

function addToRecoSheet(btn) {
  const d = btn.dataset;
  recoSheet.push({
    mstyle:    d.mstyle,
    action:    d.action,
    po_number: d.po,
    supplier:  d.supplier,
    qty_open:  d.qty,
    curr_etd:  d.currEtd,
    curr_eta:  d.currEta,
    req_etd:   d.reqEtd,
    req_eta:   d.reqEta,
  });
  btn.textContent = '✓ Added';
  btn.disabled = true;
  btn.style.background = '#e8f5e9';
  btn.style.color = '#2e7d32';
  btn.style.borderColor = '#a5d6a7';
  const badge = document.getElementById('recoBadge');
  if (badge) { badge.textContent = recoSheet.length; badge.style.display = 'inline'; }
}

function generateRecoSheet() {
  if (!recoSheet.length) {
    alert('No recommendations added yet.\\nOpen a record and click "Add to Excel" on any recommendation card.');
    return;
  }
  const headers = ['Mstyle','Action','PO #','Supplier','Qty Open','Current ETD','Current ETA','Requested ETD','Requested ETA'];
  const rows = recoSheet.map(r => [
    r.mstyle, r.action, r.po_number, r.supplier,
    r.qty_open, r.curr_etd, r.curr_eta, r.req_etd, r.req_eta
  ]);
  const csv = [headers, ...rows]
    .map(row => row.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    .join('\\n');
  const blob = new Blob(['\\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `PO_Recommendations_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
}

async function boot() {
  const scr    = document.getElementById('loadingScreen');
  const bar    = document.getElementById('loadBar');
  const status = document.getElementById('loadStatus');
  const stats  = document.getElementById('statsBar');

  const setStep = (n, state) => {
    for (let i = 1; i <= 4; i++) {
      const el = document.getElementById('ls' + i);
      if (!el) continue;
      el.className = i < n ? 'done' : i === n ? state : 'pending';
    }
  };
  const setBar = (pct) => { if (bar) bar.style.width = pct + '%'; };
  const setStatus = (msg) => {
    if (status) status.textContent = msg;
    if (stats)  stats.textContent  = msg;
  };

  setStep(1, 'active'); setBar(5);
  setStatus('Connecting to server…');

  let attempt = 0;
  while (true) {
    try {
      if (attempt > 0) setStatus(`Retrying connection… (${attempt})`);
      const res = await fetch('/api/records.json');

      if (res.status === 503) {
        // Server is still pulling from QB — show QB pull message and wait
        let msg = 'Pulling from Quickbase — please wait…';
        try { const j = await res.json(); msg = j.message || msg; } catch(_) {}
        setStep(1, 'done'); setStep(2, 'active'); setBar(15);
        setStatus(msg);
        await new Promise(r => setTimeout(r, 5000));
        attempt++;
        continue;
      }

      // Got a real response — start downloading
      setStep(1, 'done'); setStep(2, 'active'); setBar(25);
      setStatus('Downloading records…');
      ALL = await res.json();

      // Parse complete
      setStep(2, 'done'); setStep(3, 'active'); setBar(60);
      setStatus('Processing data…');
      await new Promise(r => setTimeout(r, 50));   // let browser paint step 3
      buildFilterDropdowns();

      // Build view
      setStep(3, 'done'); setStep(4, 'active'); setBar(85);
      setStatus('Building view…');
      await new Promise(r => setTimeout(r, 50));   // let browser paint step 4 before heavy DOM work
      buildTableHead();
      applyFilters();

      // Done — fade out loading screen
      // Only update the loading screen status — do NOT touch #statsBar here
      // because applyFilters() → renderStats() just populated it with priority buttons.
      setStep(4, 'done'); setBar(100);
      if (status) status.textContent = 'Ready!';

      // Populate "Data as of" timestamp below the stats bar
      fetch('/api/meta.json').then(r => r.json()).then(meta => {
        if (meta.as_of) {
          const el = document.getElementById('dataAsOf');
          if (el) el.textContent = 'Data as of ' + meta.as_of;
        }
      }).catch(() => {});
      await new Promise(r => setTimeout(r, 350));
      if (scr) {
        scr.classList.add('hidden');
        setTimeout(() => { scr.style.display = 'none'; }, 500);
      }
      break;

    } catch (err) {
      setStatus(`Connecting… (${attempt + 1})`);
      await new Promise(r => setTimeout(r, 3000));
      attempt++;
    }
  }
}

// ── Column descriptor ── one entry per master-table column.
// id     : unique key, used for sort + filter input keys
// label  : header text
// align  : 'left' | 'right'
// get(r) : raw value (used by sort + per-column filter)
// render(r): full <td>...</td> HTML
// numeric: hint for sort comparator (default text comparison)
const COLS = [
  { id:'priority',         label:'Pri',          align:'left',  get: r => ({CRITICAL:0,HIGH:1,MEDIUM:2,LOW:3}[r.priority] ?? 9), filterValue: r => r.priority,
    render: r => `<td class="pri-${r.priority}">${r.priority}</td>` },
  { id:'mstyle',           label:'Mstyle',       align:'left',  get: r => r.mstyle,
    render: r => `<td><b>${esc(r.mstyle)}</b>${r.is_multi ? ' <span class="badge badge-purple" title="Multi-pack (kit)">KIT</span>' : ''}</td>` },
  { id:'description',      label:'Description',  align:'left',  get: r => r.description,
    render: r => `<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(r.description)}">${esc(r.description)}</td>` },
  { id:'brand',            label:'Brand',        align:'left',  get: r => r.brand,
    render: r => `<td>${esc(r.brand)}</td>` },
  { id:'country',          label:'Country',      align:'left',  get: r => r.country,
    render: r => `<td>${esc(r.country)}</td>` },
  { id:'inv_manager',      label:'Inv Mgr',      align:'left',  get: r => r.inv_manager,
    render: r => `<td>${esc(r.inv_manager)}</td>` },
  { id:'status_sub',       label:'Status / Sub', align:'left',  get: r => (r.item_status_flow || r.item_status || ''), filterValue: r => `${r.item_status_flow || r.item_status || ''} ${r.sub_status || ''}`,
    render: r => { const s = r.item_status_flow || r.item_status || ''; const sub = r.sub_status ? `<br><span style="color:#888;font-size:10px;">${esc(r.sub_status)}</span>` : ''; return `<td>${esc(s)}${sub}</td>`; } },
  { id:'item_rank',        label:'Rank',         align:'left',  get: r => r.item_rank,
    render: r => `<td>${esc(r.item_rank)}</td>` },
  { id:'customer_count',   label:'Cust',         align:'right', get: r => r.customer_count, numeric:true,
    render: r => `<td class="right">${r.customer_count}</td>` },
  // Inventory positions
  { id:'qty_oh',           label:'Qty OH',       align:'right', get: r => r.qty_oh, numeric:true,
    render: r => `<td class="right ${r.qty_oh < 0 ? 'neg' : ''}">${fmt(r.qty_oh)}</td>` },
  { id:'ats_now',          label:'ATS Now',      align:'right', get: r => r.ats_now, numeric:true,
    render: r => `<td class="right ${r.ats_now < 0 ? 'neg' : ''}">${fmt(r.ats_now)}</td>` },
  { id:'qty_oh_root',      label:'Pcs OH (root)',align:'right', get: r => r.is_multi ? r.qty_oh_root : -1, numeric:true,
    render: r => `<td class="right" ${r.is_multi ? `title="Root ${esc(r.root_mstyle)}: ${fmt(r.qty_oh_root)} pcs OH ÷ ${r.pcs_per_kit} pcs/kit = ${r.assembleable_kits} more kits assembleable"` : ''}>${r.is_multi ? `<b>${fmt(r.qty_oh_root)}</b> <span style="color:#888;font-size:10px;">(+${fmt(r.assembleable_kits)})</span>` : '<span style="color:#bbb;">—</span>'}</td>` },
  { id:'it_qty',           label:'I/T',          align:'right', get: r => r.it_qty, numeric:true,
    render: r => `<td class="right">${fmt(r.it_qty)}</td>` },
  { id:'iw_qty',           label:'I/W',          align:'right', get: r => r.iw_qty, numeric:true,
    render: r => `<td class="right">${fmt(r.iw_qty)}</td>` },
  { id:'hold_qty',         label:'Hold',         align:'right', get: r => r.hold_qty, numeric:true,
    render: r => `<td class="right ${r.hold_qty > 0 ? 'pri-MEDIUM' : ''}">${fmt(r.hold_qty)}</td>` },
  { id:'open_cust_po_qty', label:'Open Cust PO', align:'right', get: r => r.open_cust_po_qty, numeric:true,
    render: r => `<td class="right">${fmt(r.open_cust_po_qty)}</td>` },
  // Demand / Shipments
  { id:'shp_wk_l4',        label:'Shpd/Wk L4',   align:'right', get: r => r.shp_wk_l4, numeric:true,
    render: r => `<td class="right">${fmt(r.shp_wk_l4)}</td>` },
  { id:'shp_wk_l13',       label:'Shpd/Wk L13',  align:'right', get: r => r.shp_wk_l13, numeric:true,
    render: r => `<td class="right">${fmt(r.shp_wk_l13)}</td>` },
  { id:'prj_wk',           label:'Prj/Wk',       align:'right', get: r => r.prj_wk, numeric:true,
    render: r => `<td class="right">${fmt(r.prj_wk)}</td>` },
  { id:'prj_l4w_change',   label:'+/- L4w',      align:'right', get: r => r.prj_l4w_change, numeric:true,
    render: r => { const l4w = r.prj_l4w_change; const arr = l4w > 5 ? '<span style="color:#2e7d32;font-weight:700;">▲</span>' : l4w < -5 ? '<span style="color:#c62828;font-weight:700;">▼</span>' : ''; return `<td class="right">${arr} ${fmt(l4w)}%</td>`; } },
  // Targets / health
  { id:'opt_wos',          label:'Opt WOS',      align:'right', get: r => r.opt_wos, numeric:true,
    render: r => `<td class="right">${fmt(r.opt_wos)}</td>` },
  { id:'ats_wos_oh',       label:'ATS WOS',      align:'right', get: r => r.ats_wos_oh, numeric:true,
    render: r => `<td class="right ${r.ats_wos_oh > 0 && r.ats_wos_oh < r.opt_wos ? 'pri-HIGH' : ''}">${fmt(r.ats_wos_oh)}</td>` },
  { id:'opt_oh',           label:'Opt OH',       align:'right', get: r => r.opt_oh, numeric:true,
    render: r => `<td class="right">${fmt(r.opt_oh)}</td>` },
  { id:'lt_wks',           label:'LT Wks',       align:'right', get: r => r.lt_wks, numeric:true,
    render: r => `<td class="right">${fmt(r.lt_wks)}</td>` },
  { id:'cny_weeks',        label:'CNY',          align:'right', get: r => r.cny_weeks, numeric:true,
    render: r => `<td class="right">${fmt(r.cny_weeks)}</td>` },
  { id:'days_oos_next_rcpt', label:'Days OOS→Rcpt', align:'right', get: r => r.days_oos_next_rcpt, numeric:true,
    render: r => `<td class="right ${r.days_oos_next_rcpt > 0 ? 'pri-CRITICAL' : ''}">${fmt(r.days_oos_next_rcpt)}</td>` },
  { id:'next_rcpt_dt',     label:'Next Rcpt',    align:'left',  get: r => r.next_rcpt_dt || 'zzzz',
    render: r => `<td>${fmtDate(r.next_rcpt_dt)}</td>` },
  // Computed
  { id:'gap_weeks_n',      label:'OOS Wks',      align:'right', get: r => r.gap_weeks.length, numeric:true,
    render: r => `<td class="right ${r.gap_weeks.length > 0 ? 'pri-CRITICAL' : ''}">${r.gap_weeks.length}</td>` },
  { id:'pipeline_excess',  label:'OH+OO Excess', align:'right', get: r => r.pipeline_excess, numeric:true,
    tooltip: 'OH+OO Excess = (Week 1 OH + all open PO qty) - 26w projected demand - safety stock&#10;Safety stock = Opt WOS x (26w demand / 26)&#10;Positive = more inventory than needed (overstock)&#10;Negative = shortfall&#10;> 2,500 units triggers Overstock flag',
    render: r => `<td class="right ${r.pipeline_excess > 2500 ? 'pri-HIGH' : ''}">${fmt(r.pipeline_excess)}</td>` },
  { id:'pipeline_wos',     label:'OH+OO WOS',    align:'right', get: r => r.pipeline_wos == null ? 1e9 : r.pipeline_wos, numeric:true,
    render: r => `<td class="right">${r.pipeline_wos == null ? '∞' : fmt(r.pipeline_wos)}</td>` },
  { id:'action',           label:'Action',       align:'left',  get: r => { if (!r.recommendations.length) return 'CLEAN'; const c={}; for(const rc of r.recommendations) c[rc.action]=(c[rc.action]||0)+1; return Object.keys(c).sort((a,b)=>c[b]-c[a])[0]; },
    render: r => `<td>${actionTag(r)}</td>` },
];

function _filterValue(c, r) {
  return c.filterValue ? c.filterValue(r) : c.get(r);
}

// Active column set — drops Pcs OH (root) when Hide Multi-Packs is checked
// since the column is only meaningful for multi-pack items.
function visibleCols() {
  const hideMulti = (document.getElementById('hideMulti') || {}).checked;
  return COLS.filter(c => !(hideMulti && c.id === 'qty_oh_root'));
}

function buildTableHead() {
  const head = document.getElementById('theadMain');
  const cols = visibleCols();
  // Row 1 — sortable header
  let h1 = '<tr>';
  for (const c of cols) {
    const a = (c.align === 'right') ? ' class="right"' : '';
    const arrow = (currentSort.id === c.id) ? `<span class="sort-arrow">${currentSort.dir > 0 ? '▲' : '▼'}</span>` : '';
    const tip = c.tooltip ? ` title="${c.tooltip.replace(/"/g, '&quot;')}" style="cursor:help;"` : '';
    h1 += `<th${a}${tip} data-col="${c.id}">${esc(c.label)}${arrow}</th>`;
  }
  h1 += '</tr>';
  // Row 2 — per-column filter inputs
  let h2 = '<tr class="filter-row">';
  for (const c of cols) {
    const a = (c.align === 'right') ? ' class="right"' : '';
    const v = colFilters[c.id] || '';
    h2 += `<th${a}><input data-filter="${c.id}" type="text" placeholder="filter…" value="${esc(v)}"></th>`;
  }
  h2 += '</tr>';
  head.innerHTML = h1 + h2;
  // Wire up sort clicks
  head.querySelectorAll('th[data-col]').forEach(th => {
    th.onclick = () => {
      const id = th.dataset.col;
      if (currentSort.id === id) currentSort.dir = -currentSort.dir;
      else { currentSort.id = id; currentSort.dir = 1; }
      buildTableHead();   // rebuild to update arrows
      applyFilters();
    };
  });
  // Wire up per-column filter inputs
  head.querySelectorAll('input[data-filter]').forEach(inp => {
    inp.oninput = () => {
      const id = inp.dataset.filter;
      const v = inp.value.trim();
      if (v) colFilters[id] = v; else delete colFilters[id];
      applyFilters();
    };
    inp.onclick = (e) => e.stopPropagation();   // don't trigger sort when clicking the input
  });
}

function buildFilterDropdowns() {
  const countries = new Set(), brands = new Set(), mgrs = new Set();
  for (const r of ALL) {
    if (r.country) countries.add(r.country);
    if (r.brand)   brands.add(r.brand);
    if (r.inv_manager) mgrs.add(r.inv_manager);
  }
  const fill = (id, values) => {
    const el = document.getElementById(id);
    [...values].sort().forEach(v => {
      const o = document.createElement('option'); o.value = v; o.textContent = v;
      el.appendChild(o);
    });
  };
  fill('countryFilter', countries);
  fill('brandFilter', brands);
  fill('invMgrFilter', mgrs);
}

function applyFilters() {
  const q = document.getElementById('searchInput').value.toLowerCase().trim();
  const af = document.getElementById('actionFilter').value;
  const cf = document.getElementById('countryFilter').value;
  const bf = document.getElementById('brandFilter').value;
  const mf = document.getElementById('invMgrFilter').value;
  const replen = document.getElementById('replenOnly').checked;
  const gaps   = document.getElementById('gapsOnly').checked;
  const over   = document.getElementById('overstockOnly').checked;
  const hideMulti = document.getElementById('hideMulti').checked;

  // Pre-resolve column descriptors that have an active per-column filter so we
  // don't look them up inside the hot filter loop.
  const activeCols = [];
  for (const cid in colFilters) {
    const c = COLS.find(x => x.id === cid);
    if (c) activeCols.push({ c, needle: colFilters[cid].toLowerCase() });
  }

  FILTERED = ALL.filter(r => {
    // Global toolbar filters
    if (q) {
      const hay = (r.mstyle + ' ' + r.description + ' ' + r.brand).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (cf && r.country !== cf) return false;
    if (bf && r.brand !== bf) return false;
    if (mf && r.inv_manager !== mf) return false;
    if (replen && !r.is_replen) return false;
    if (gaps && r.gap_weeks.length === 0) return false;
    if (over && !r.overstocked) return false;
    if (hideMulti && r.is_multi) return false;
    if (af === '__NONE__') {
      if (r.recommendations.length !== 0) return false;
    } else if (af) {
      if (!r.recommendations.some(rc => rc.action === af)) return false;
    }
    // Priority button filter (top banner)
    if (priorityFilter && r.priority !== priorityFilter) return false;
    // Per-column filter inputs (substring, case-insensitive)
    for (const { c, needle } of activeCols) {
      const val = _filterValue(c, r);
      if (val == null) return false;
      if (!String(val).toLowerCase().includes(needle)) return false;
    }
    return true;
  });

  // Sort
  const cmpCol = (c, a, b, dir) => {
    const va = c.get(a), vb = c.get(b);
    if (c.numeric) return (Number(va) - Number(vb)) * dir;
    const sa = String(va == null ? '' : va).toLowerCase();
    const sb = String(vb == null ? '' : vb).toLowerCase();
    if (sa < sb) return -dir;
    if (sa > sb) return  dir;
    return 0;
  };
  if (currentSort.id) {
    // Single-column sort (user clicked a header)
    const sortCol = COLS.find(c => c.id === currentSort.id);
    if (sortCol) {
      const dir = currentSort.dir;
      FILTERED.sort((a, b) => cmpCol(sortCol, a, b, dir));
    }
  } else {
    // Default composite sort: inv_manager → brand → mstyle (all ascending)
    const chain = DEFAULT_SORT_CHAIN.map(id => COLS.find(c => c.id === id)).filter(Boolean);
    FILTERED.sort((a, b) => {
      for (const c of chain) {
        const r = cmpCol(c, a, b, 1);
        if (r !== 0) return r;
      }
      return 0;
    });
  }

  renderStats();
  renderTable();
}

function renderStats() {
  // Always count from the post-NON-priority-filter set so the priority button
  // counts reflect "what's left after all other filters" — that way clicking
  // a priority button shows exactly that count of rows.
  const inScope = FILTERED.length;
  const gaps    = FILTERED.filter(r => r.gap_weeks.length > 0).length;
  const over    = FILTERED.filter(r => r.overstocked).length;
  // Priority bucket counts (computed against ALL with non-priority filters applied
  // — but for simplicity we use FILTERED, which excludes priority filter applied
  // earlier.  This loop counts within current view.)
  const pri = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };
  // We need pre-priority-filter counts; recompute against ALL with same non-pri filters
  const nonPriFiltered = _filterRecords(true);
  for (const r of nonPriFiltered) pri[r.priority] = (pri[r.priority] || 0) + 1;
  const total = nonPriFiltered.length;

  const PRI_TIPS = {
    CRITICAL: 'CRITICAL\\nA recommendation has been escalated to critical severity — the most severe stockout situations.',
    HIGH:     'HIGH\\nAt least one gap week where WOS shortfall is ≥ 2.0 weeks below Opt WOS.\\nExample: Opt WOS = 4.0, current WOS = 1.5 → deficit 2.5 → HIGH.',
    MEDIUM:   'MEDIUM\\nHas any gap week (even a small dip below Opt WOS), OR pipeline excess > 2,500 units (overstocked).',
    LOW:      'LOW\\nNo gap weeks and not overstocked. No action needed.',
  };
  const btn = (key, label, color) => {
    const active = priorityFilter === key;
    return `<button class="pri-btn ${active ? 'active' : ''}" data-pri="${key}"
      title="${PRI_TIPS[key]}"
      style="background:${active ? color : '#ffffff'};color:${active ? '#fff' : color};border:1.5px solid ${color};">
      ${label} <b style="margin-left:4px;">${(pri[key]||0).toLocaleString()}</b>
    </button>`;
  };
  const allBtn = `<button class="pri-btn ${priorityFilter === '' ? 'active' : ''}" data-pri=""
      title="All priorities — click a priority button to filter."
      style="background:${priorityFilter === '' ? '#37474f' : '#ffffff'};color:${priorityFilter === '' ? '#fff' : '#37474f'};border:1.5px solid #37474f;">
      All <b style="margin-left:4px;">${total.toLocaleString()}</b>
    </button>`;

  document.getElementById('statsBar').innerHTML =
      allBtn +
      btn('CRITICAL', '🔴 Critical', '#b71c1c') +
      btn('HIGH',     '🟠 High',     '#e65100') +
      btn('MEDIUM',   '🟡 Medium',   '#f9a825') +
      btn('LOW',      '⚪ Low',      '#5d4037') +
      `<div class="stat" style="margin-left:14px;"><b>${gaps}</b> with gaps</div>` +
      `<div class="stat"><b>${over}</b> overstocked</div>` +
      `<div class="stat"><b>${inScope.toLocaleString()}</b> shown</div>`;

  // Wire up priority buttons
  document.getElementById('statsBar').querySelectorAll('.pri-btn').forEach(b => {
    b.onclick = () => {
      const key = b.dataset.pri;
      priorityFilter = (priorityFilter === key) ? '' : key;
      applyFilters();
    };
  });
}

// Helper: re-apply all filters EXCEPT priority, used to compute priority-bucket counts
function _filterRecords(skipPriority) {
  const q = document.getElementById('searchInput').value.toLowerCase().trim();
  const af = document.getElementById('actionFilter').value;
  const cf = document.getElementById('countryFilter').value;
  const bf = document.getElementById('brandFilter').value;
  const mf = document.getElementById('invMgrFilter').value;
  const replen = document.getElementById('replenOnly').checked;
  const gaps   = document.getElementById('gapsOnly').checked;
  const over   = document.getElementById('overstockOnly').checked;
  const hideMulti = document.getElementById('hideMulti').checked;
  const activeCols = [];
  for (const cid in colFilters) {
    const c = COLS.find(x => x.id === cid);
    if (c) activeCols.push({ c, needle: colFilters[cid].toLowerCase() });
  }
  return ALL.filter(r => {
    if (q) {
      const hay = (r.mstyle + ' ' + r.description + ' ' + r.brand).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (cf && r.country !== cf) return false;
    if (bf && r.brand !== bf) return false;
    if (mf && r.inv_manager !== mf) return false;
    if (replen && !r.is_replen) return false;
    if (gaps && r.gap_weeks.length === 0) return false;
    if (over && !r.overstocked) return false;
    if (hideMulti && r.is_multi) return false;
    if (af === '__NONE__') { if (r.recommendations.length !== 0) return false; }
    else if (af) { if (!r.recommendations.some(rc => rc.action === af)) return false; }
    if (!skipPriority && priorityFilter && r.priority !== priorityFilter) return false;
    for (const { c, needle } of activeCols) {
      const val = c.filterValue ? c.filterValue(r) : c.get(r);
      if (val == null) return false;
      if (!String(val).toLowerCase().includes(needle)) return false;
    }
    return true;
  });
}

function fmt(n) { if (n == null) return ''; return Number(n).toLocaleString('en-US', {maximumFractionDigits:1}); }
function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtDate(d) { if (!d) return '—'; try { return new Date(d).toLocaleDateString('en-US', {month:'short', day:'numeric'}); } catch(e) { return d; } }

function actionTag(r) {
  if (r.recommendations.length === 0) return '<span class="badge badge-green">CLEAN</span>';
  // Show the dominant action
  const counts = {};
  for (const rc of r.recommendations) counts[rc.action] = (counts[rc.action] || 0) + 1;
  const top = Object.keys(counts).sort((a,b) => counts[b] - counts[a])[0];
  const cls = {
    PULL_UP:       'badge-amber',
    FASTER_VESSEL: 'badge-purple',
    PUSH_OUT:      'badge-amber',
    SPLIT:         'badge-amber',
    CANCEL:        'badge-red',
    NO_LEVER:      'badge-gray',
  }[top] || 'badge-gray';
  return `<span class="badge ${cls}">${top}</span>` + (r.recommendations.length > 1 ? ` <span style="color:#888;font-size:10px;">+${r.recommendations.length-1}</span>` : '');
}

function renderTable() {
  const tb = document.getElementById('tbody');
  const cols = visibleCols();
  const nCols = cols.length;
  // Build the entire table as one HTML string — ~5× faster than createElement/appendChild
  // because the browser's native HTML parser batches all DOM work in one pass.
  let html = '';
  for (const r of FILTERED) {
    const safeMs = r.mstyle.replace(/[^a-zA-Z0-9]/g, '_');
    // Use data-ms attribute so mstyle values with slashes/quotes need no JS escaping
    html += '<tr class="row" data-ms="' + esc(r.mstyle) + '" onclick="toggleDetail(this.dataset.ms)">';
    for (const c of cols) html += c.render(r);
    html += '</tr>';
    html += '<tr class="detail-pane" id="detail-' + safeMs + '" style="display:none"><td colspan="' + nCols + '"></td></tr>';
  }
  tb.innerHTML = html;
}

function toggleDetail(mstyle) {
  const id = 'detail-' + mstyle.replace(/[^a-zA-Z0-9]/g,'_');
  const dtr = document.getElementById(id);
  if (!dtr) return;
  if (dtr.style.display === 'table-row') { dtr.style.display = 'none'; return; }
  dtr.style.display = 'table-row';
  if (dtr.dataset.loaded === '1') return;
  const r = ALL.find(x => x.mstyle === mstyle);
  if (!r) return;
  dtr.querySelector('td').innerHTML = renderDetail(r);
  dtr.dataset.loaded = '1';
}

function renderDetail(r) {
  // ── Precompute: bucket open POs by forecast-week (ETA → warehouse + lag) ──
  // Used to populate the "Expected Receipts" cell hovers.
  const lag = (String(r.country).toUpperCase() === 'USA') ? 3 : 10;
  // W1 = Sunday on/before today
  const today = new Date(); today.setHours(0,0,0,0);
  const w1 = new Date(today);
  const daysSinceSun = today.getDay();   // Sun=0..Sat=6
  w1.setDate(today.getDate() - daysSinceSun);
  const poByWeek = {};   // week_idx (0..25) -> [po, po, ...]
  for (const p of (r.open_pos || [])) {
    if (!p.eta) continue;
    const wh = new Date(p.eta);
    wh.setDate(wh.getDate() + lag);
    const wi = Math.floor((wh - w1) / 86400000 / 7);
    if (wi < 0 || wi > 25) continue;
    (poByWeek[wi] = poByWeek[wi] || []).push(p);
  }
  const fmtPoHover = (wi) => {
    const pos = poByWeek[wi] || [];
    if (!pos.length) return '';
    const wkDate = (new Date(w1.getTime() + wi * 7 * 86400000)).toLocaleDateString('en-US', {month:'short', day:'numeric'});
    let h = `Week of ${wkDate}  (wh-avail = ETA + ${lag}d)\n${'─'.repeat(38)}\n`;
    for (const p of pos) {
      const it = p.in_transit_qty || 0, iw = p.in_work_qty || 0;
      h += `PO #: ${p.po_number}\n`;
      h += `Supplier: ${p.supplier || '—'}\n`;
      h += `Open (I/W): ${fmt(iw)}  ·  In Transit (I/T): ${fmt(it)}\n`;
      h += `ETD: ${p.etd || '—'}  ·  ETA: ${p.eta || '—'}\n`;
      if (pos.indexOf(p) < pos.length - 1) h += '\\n';
    }
    return h;
  };
  // ── Precompute: per-customer Prj qty for each forecast week ──────────────
  // Used to populate the "Prj Demand" cell hovers.  Customer-level data comes
  // from Projections rollup (customer_demand: [{customer, weekly[26], total}]).
  const fmtPrjHover = (wi) => {
    const cd = r.customer_demand || [];
    const wk = (new Date(w1.getTime() + wi * 7 * 86400000)).toLocaleDateString('en-US', {month:'short', day:'numeric'});
    let h = `Prj Demand — W${wi+1} (week of ${wk})\nPer-customer rollup from QB Projections:\n\n`;
    let any = false;
    const sorted = cd.map(c => ({ customer: c.customer, qty: (c.weekly && c.weekly[wi]) || 0 }))
                     .filter(c => c.qty !== 0)
                     .sort((a, b) => b.qty - a.qty);
    for (const c of sorted) {
      any = true;
      h += `• ${c.customer || '(unknown)'}: ${fmt(c.qty)}\n`;
    }
    if (!any) h += '(no per-customer projection for this week)';
    return h;
  };

  // 1) Inventory Flow 26-week grid (with hover tooltips on Receipts & Prj rows)
  let invFlow = '<table class="subtbl grid26"><tr><th class="lbl"></th>';
  for (let i = 1; i <= 26; i++) {
    const wkSun = new Date(w1.getTime() + (i - 1) * 7 * 86400000);
    const lbl = (wkSun.getMonth() + 1) + '/' + wkSun.getDate();
    invFlow += `<th title="W${i} — week of ${wkSun.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}">${lbl}</th>`;
  }
  invFlow += '<th>Total</th></tr>';
  const renderRow = (label, arr, opts = {}) => {
    let html = `<tr><td class="lbl">${label}</td>`;
    let tot = 0;
    for (let i = 0; i < 26; i++) {
      const v = arr[i] || 0; tot += v;
      const c = (v < 0 ? 'neg' : 'ok');
      let styleStr = '', extraAttrs = '';
      if (opts.hoverFn) {
        const tip = opts.hoverFn(i);
        if (tip) { extraAttrs += ` title="${tip.replace(/"/g, '&quot;')}"`; styleStr += 'cursor:help;'; }
      }
      if (opts.highlightFn) {
        const hl = opts.highlightFn(i);
        if (hl) styleStr += hl;
      }
      const styleAttr = styleStr ? ` style="${styleStr}"` : '';
      html += `<td class="${c}"${extraAttrs}${styleAttr}>${v === 0 ? '—' : fmt(v)}</td>`;
    }
    html += `<td><b>${fmt(tot)}</b></td></tr>`;
    return html;
  };

  // Build week-level action map for Expected Receipts highlighting
  // rec po_number -> action (PUSH_OUT = orange, PULL_UP = blue)
  const recByPo = {};
  for (const rc of (r.recommendations || [])) {
    if (rc.action === 'PUSH_OUT' || rc.action === 'PULL_UP') {
      recByPo[rc.po_number] = rc.action;
    }
  }
  // week_idx -> action (PULL_UP takes priority if both in same week)
  const weekAction = {};
  for (const [wi, wpos] of Object.entries(poByWeek)) {
    for (const p of wpos) {
      const action = recByPo[p.po_number];
      if (action && (!weekAction[wi] || action === 'PULL_UP')) weekAction[wi] = action;
    }
  }
  const rcvHighlight = (wi) => {
    const a = weekAction[wi];
    if (a === 'PUSH_OUT') return 'background:#fff3e0;color:#e65100;font-weight:600;';
    if (a === 'PULL_UP')  return 'background:#e3f2fd;color:#1565c0;font-weight:600;';
    return null;
  };

  invFlow += renderRow('Beg Inv', r.beg_inv);
  invFlow += renderRow('Expected Receipts', r.rcv, { hoverFn: fmtPoHover, highlightFn: rcvHighlight });
  invFlow += renderRow('Prj Demand', r.prj, { hoverFn: fmtPrjHover });
  // WOS row computed inline (no hover)
  let wosRow = '<tr><td class="lbl">WOS OH</td>';
  for (let i = 0; i < 26; i++) {
    const b = r.beg_inv[i] || 0, p = r.prj[i] || 0;
    let v = '—', cls = 'ok';
    if (p > 0) { const w = b / p; v = w.toFixed(1); if (w < r.opt_wos) cls = 'gap'; if (w < 0) cls = 'neg'; }
    else if (b > 0) { v = '∞'; }
    wosRow += `<td class="${cls}">${v}</td>`;
  }
  wosRow += '<td></td></tr>';
  invFlow += wosRow + '</table>';

  // 2) Open POs table
  let pos = '<table class="subtbl"><tr><th>PO #</th><th>Supplier</th><th class="right">I/T</th><th class="right">I/W</th><th>ETD</th><th>ETA</th><th class="right">Transit</th><th>Status</th></tr>';
  for (const p of r.open_pos) {
    const sc = {LOCKED:'badge-gray', IN_TRANSIT:'badge-purple', MOVABLE:'badge-green', FASTER_VESSEL_WINDOW:'badge-amber', PULL_UP_NARROW:'badge-amber'}[p.status] || 'badge-gray';
    pos += `<tr><td><b>${esc(p.po_number)}</b></td><td>${esc(p.supplier)}</td>
      <td class="right">${fmt(p.in_transit_qty)}</td><td class="right">${fmt(p.in_work_qty)}</td>
      <td>${fmtDate(p.etd)}</td><td>${fmtDate(p.eta)}</td>
      <td class="right">${p.transit_days || '—'}d</td>
      <td><span class="badge ${sc}">${p.status}</span></td></tr>`;
  }
  if (r.open_pos.length === 0) pos += '<tr><td colspan="8" style="color:#888;font-style:italic;">No open POs.</td></tr>';
  pos += '</table>';

  // 3) Recommendations — multi-line card with current PO state + before/after
  let recs = '';
  if (r.recommendations.length === 0) {
    recs = '<div style="color:#1b5e20;font-style:italic;">✓ No actions recommended.</div>';
  } else {
    const arrow = '<span style="color:#888;margin:0 4px;">→</span>';
    const beforeAfter = (before, after, isDate) => {
      const a = isDate ? fmtDate(before) : fmt(before);
      const b = isDate ? fmtDate(after)  : fmt(after);
      if (before === after) return `<span>${a}</span>`;
      return `<span style="color:#888;text-decoration:line-through;">${a}</span>${arrow}<span style="color:#0d47a1;font-weight:600;">${b}</span>`;
    };
    for (const rc of r.recommendations) {
      const cls = `priority-${rc.priority} action-${rc.action}`;
      // Header: action chip + PO# + supplier
      let header = `<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">
        <span class="rec-action ${rc.action}">${rc.action}</span>
        <b style="font-size:13px;">${esc(rc.po_number || '—')}</b>
        ${rc.supplier ? `<span style="color:#555;font-size:11px;">· ${esc(rc.supplier)}</span>` : ''}
      </div>`;

      // Body sections: each rec type has its own layout
      let body = '';

      if (rc.action === 'PULL_UP' || rc.action === 'FASTER_VESSEL') {
        // Current state row
        body += `<div class="rec-row"><span class="rec-lbl">Current qty:</span>
          <b>${fmt(rc.po_total_qty)}</b> pcs
          <span style="color:#888;">(I/T ${fmt(rc.in_transit_qty)}, I/W ${fmt(rc.in_work_qty)})</span></div>`;
        body += `<div class="rec-row"><span class="rec-lbl">ETD:</span> ${beforeAfter(rc.orig_etd, rc.proposed_etd, true)}</div>`;
        body += `<div class="rec-row"><span class="rec-lbl">ETA:</span> ${beforeAfter(rc.orig_eta, rc.proposed_eta, true)}
          ${rc.delta_days ? `<span style="color:#888;margin-left:8px;">(${rc.delta_days > 0 ? '+' : ''}${rc.delta_days} days)</span>` : ''}</div>`;
        if (rc.action === 'FASTER_VESSEL') {
          body += `<div class="rec-row" style="color:#5e35b1;font-style:italic;font-size:11px;">Same ETD — request faster vessel only (transit ~18 days vs ~26)</div>`;
        }

      } else if (rc.action === 'SPLIT') {
        body += `<div class="rec-row"><span class="rec-lbl">Original PO:</span>
          <b>${fmt(rc.po_total_qty)}</b> pcs  ·  ETD ${fmtDate(rc.orig_etd)} → ETA ${fmtDate(rc.orig_eta)}
          <span style="color:#888;">(I/T ${fmt(rc.in_transit_qty)}, I/W ${fmt(rc.in_work_qty)})</span></div>`;
        body += `<div class="rec-row"><span class="rec-lbl">Keep:</span>
          <b style="color:#1b5e20;">${fmt(rc.keep_qty)}</b> pcs at original ETD ${fmtDate(rc.orig_etd)} → ETA ${fmtDate(rc.orig_eta)}</div>`;
        body += `<div class="rec-row"><span class="rec-lbl">Push:</span>
          <b style="color:#e65100;">${fmt(rc.push_qty)}</b> pcs to new ETD ${fmtDate(rc.proposed_etd)} → ETA ${fmtDate(rc.proposed_eta)}
          <span style="color:#888;margin-left:6px;">(+${rc.delta_days} days)</span></div>`;

      } else if (rc.action === 'PUSH_OUT') {
        body += `<div class="rec-row"><span class="rec-lbl">Current qty:</span>
          <b>${fmt(rc.po_total_qty)}</b> pcs
          <span style="color:#888;">(I/T ${fmt(rc.in_transit_qty)}, I/W ${fmt(rc.in_work_qty)})</span></div>`;
        body += `<div class="rec-row"><span class="rec-lbl">ETD:</span> ${beforeAfter(rc.orig_etd, rc.proposed_etd, true)}</div>`;
        body += `<div class="rec-row"><span class="rec-lbl">ETA:</span> ${beforeAfter(rc.orig_eta, rc.proposed_eta, true)}
          ${rc.delta_days ? `<span style="color:#888;margin-left:8px;">(+${rc.delta_days} days)</span>` : ''}</div>`;

      } else if (rc.action === 'CANCEL') {
        body += `<div class="rec-row"><span class="rec-lbl">Original PO:</span>
          <b>${fmt(rc.po_total_qty)}</b> pcs  ·  ETD ${fmtDate(rc.orig_etd)} → ETA ${fmtDate(rc.orig_eta)}</div>`;
        body += `<div class="rec-row"><span class="rec-lbl">Cancel:</span>
          <b style="color:#c62828;">${fmt(rc.qty_affected)}</b> pcs
          <span style="color:#888;">(remaining: ${fmt(rc.po_total_qty - rc.qty_affected)})</span></div>`;
      }

      // Gap coverage chip
      const wks = rc.gap_weeks_fixed || [];
      let gapChip = '';
      if (wks.length) {
        if (wks.length <= 4) gapChip = `Covers gaps in <b>${wks.map(w => 'W' + w).join(', ')}</b>`;
        else gapChip = `Covers <b>${wks.length}</b> gap weeks (W${wks[0]}–W${wks[wks.length-1]})`;
      }
      const reasonRow = (rc.reason && !gapChip) ? `<div class="rec-reason">${esc(rc.reason)}</div>` :
                        gapChip ? `<div class="rec-reason">${gapChip}</div>` : '';

      const hasEtd = rc.orig_etd || rc.proposed_etd;
      const addBtn = rc.po_number ? `
        <div style="margin-top:8px;text-align:right;">
          <button class="add-excel-btn"
            data-mstyle="${esc(r.mstyle)}"
            data-action="${rc.action}"
            data-po="${esc(rc.po_number || '')}"
            data-supplier="${esc(rc.supplier || '')}"
            data-qty="${rc.po_total_qty || 0}"
            data-curr-etd="${rc.orig_etd || ''}"
            data-curr-eta="${rc.orig_eta || ''}"
            data-req-etd="${rc.proposed_etd || ''}"
            data-req-eta="${rc.proposed_eta || ''}"
            onclick="addToRecoSheet(this)"
            style="font-size:11px;padding:3px 10px;background:#e3f2fd;color:#0d47a1;border:1px solid #90caf9;border-radius:3px;cursor:pointer;font-family:inherit;">
            ➕ Add to Excel
          </button>
        </div>` : '';
      recs += `<div class="rec-box ${cls}">
        ${header}
        <div class="rec-body">${body}</div>
        ${reasonRow}
        ${addBtn}
      </div>`;
    }
  }

  // 4) Gap detail
  let gapDetail = '';
  if (r.gap_weeks.length) {
    gapDetail = '<table class="subtbl"><tr><th>Wk</th><th>Date</th><th class="right">Beg</th><th class="right">Prj</th><th class="right">WOS</th><th class="right">Deficit</th></tr>';
    for (const g of r.gap_weeks) {
      gapDetail += `<tr><td>W${g.wi}</td><td>${fmtDate(g.date)}</td><td class="right">${fmt(g.beg)}</td><td class="right">${fmt(g.prj)}</td><td class="right">${g.wos}</td><td class="right pri-CRITICAL">${g.deficit}</td></tr>`;
    }
    gapDetail += '</table>';
  } else {
    gapDetail = '<div style="color:#1b5e20;font-style:italic;">✓ No gap weeks before next receipt.</div>';
  }

  // ── Identity strip (badges) ─────────────────────────────────────────
  const flagBadges = [];
  if (r.active_kl)         flagBadges.push('<span class="badge badge-green">Active KL</span>');
  if (r.nvo)               flagBadges.push('<span class="badge badge-purple">NVO</span>');
  if (r.new_item_no_prj)   flagBadges.push('<span class="badge badge-amber">New Item · No Prj</span>');
  if (r.amz_do_not_ship)   flagBadges.push('<span class="badge badge-red">AMZ DO NOT SHIP</span>');
  if (r.amz_suppression)   flagBadges.push('<span class="badge badge-red">AMZ Suppression</span>');
  if (r.transfer_qty_open) flagBadges.push('<span class="badge badge-amber">Transfer Qty Open</span>');
  if (r.pvt_lbl_excl)      flagBadges.push('<span class="badge badge-amber">Pvt Lbl / Excl</span>');
  if (r.commit_item)        flagBadges.push('<span class="badge badge-green">Commit Item</span>');
  if (r.is_multi)          flagBadges.push('<span class="badge badge-purple">Multi-Pack (Kit)</span>');

  // Compact subsection box helpers
  const kvRow = (lbl, val) => `<div style="display:flex;gap:6px;padding:1px 0;font-size:11px;line-height:1.45;"><span style="color:#666;min-width:90px;flex-shrink:0;">${lbl}</span><span style="font-weight:500;color:#222;">${val}</span></div>`;
  const kvBox = (lbl, rows) => `<div style="flex:1;min-width:160px;background:#f8f9fa;border:1px solid #e4e7eb;border-radius:4px;padding:8px 10px;">${lbl ? `<div style="font-size:10px;font-weight:700;color:#9e9e9e;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:5px;">${lbl}</div>` : ''}${rows}</div>`;

  const identityBox = kvBox('Identity', `
    ${kvRow('Mstyle',   `<b>${esc(r.mstyle)}</b>`)}
    ${kvRow('Rank',     esc(r.item_rank) || '—')}
    ${kvRow('Status',   esc(r.item_status_flow) || '—')}
    ${kvRow('Sub Stat', esc(r.sub_status) || '—')}
    ${r.season    ? kvRow('Season',    esc(r.season))    : ''}
    ${r.size_ct   ? kvRow('Size/Ct',   esc(r.size_ct))   : ''}
    ${r.fragrance ? kvRow('Fragrance', esc(r.fragrance)) : ''}
    ${flagBadges.length ? `<div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap;">${flagBadges.join('')}</div>` : ''}
  `);

  // ── ATS / Inventory positions grid ─────────────────────────────────
  const atsHtml = `
    <table class="subtbl">
      <tr>
        <th>Position</th><th class="right">Qty</th>
        <th>Position</th><th class="right">Qty</th>
        <th>WOS Metric</th><th class="right">Value</th>
      </tr>
      <tr><td>Qty OH (total)</td><td class="right"><b>${fmt(r.qty_oh)}</b></td>
          <td>I/T (in transit)</td><td class="right">${fmt(r.it_qty)}</td>
          <td>ATS WOS OH</td><td class="right">${fmt(r.ats_wos_oh)}</td></tr>
      <tr><td>ATS Qty OH</td><td class="right">${fmt(r.ats_qty_oh)}</td>
          <td>I/W (in work)</td><td class="right">${fmt(r.iw_qty)}</td>
          <td>ATS WOS OH+OO</td><td class="right">${fmt(r.ats_wos_oh_oo)}</td></tr>
      <tr><td>ATS Now</td><td class="right"><b>${fmt(r.ats_now)}</b></td>
          <td>I/T + I/W</td><td class="right">${fmt(r.it_iw)}</td>
          <td>ATS WOS OH+OO (w/ kits)</td><td class="right">${fmt(r.ats_wos_oh_oo_w_kits)}</td></tr>
      <tr><td>ATS OH + OO</td><td class="right">${fmt(r.ats_oh_oo)}</td>
          <td>I/W + I/T w/ Kits</td><td class="right">${fmt(r.it_iw_kits)}</td>
          <td>ATS WOS (w/o test/excl)</td><td class="right">${fmt(r.ats_wos_oh_oo_wo_test)}</td></tr>
      <tr><td>ATS OH + OO (w/ kits)</td><td class="right">${fmt(r.ats_oh_oo_w_kits)}</td>
          <td>Open Cust PO Qty</td><td class="right">${fmt(r.open_cust_po_qty)}</td>
          <td>ATS OH + I/T Booked WOS</td><td class="right">${fmt(r.ats_oh_it_booked_wos)}</td></tr>
      <tr><td>ATS Qty (not alloc'd)</td><td class="right">${fmt(r.ats_qty_not_alloc)}</td>
          <td>Hold Order Qty</td><td class="right ${r.hold_qty > 0 ? 'pri-MEDIUM' : ''}">${fmt(r.hold_qty)}</td>
          <td>Opt WOS</td><td class="right"><b>${fmt(r.opt_wos)}</b></td></tr>
      <tr><td>NJ ATS OH</td><td class="right">${fmt(r.nj_ats_oh)}</td>
          <td>Test Order Qty</td><td class="right">${fmt(r.test_order_qty)}</td>
          <td>Opt OH</td><td class="right">${fmt(r.opt_oh)}</td></tr>
      <tr><td>CA ATS OH</td><td class="right">${fmt(r.ca_ats_oh)}</td>
          <td>Exclude PO from WOS</td><td class="right">${fmt(r.exclude_po_wos)}</td>
          <td>LT (Wks) · CNY · LT+Opt</td><td class="right">${fmt(r.lt_wks)} · ${fmt(r.cny_weeks)} · ${fmt(r.lt_opt_weeks)}</td></tr>
    </table>`;

  // ── Demand & Shipments ─────────────────────────────────────────────
  const demandHtml = `
    <table class="subtbl">
      <tr>
        <th>Demand</th><th class="right">Qty</th>
        <th>Shipments</th><th class="right">Qty</th>
        <th>Date</th><th>Value</th>
      </tr>
      <tr><td>Prj / Wk</td><td class="right"><b>${fmt(r.prj_wk)}</b></td>
          <td>Shpd / Wk L4</td><td class="right"><b>${fmt(r.shp_wk_l4)}</b></td>
          <td>Last Shp Date</td><td>${fmtDate(r.last_shp_date)}</td></tr>
      <tr><td>Max Prj / Wk</td><td class="right">${fmt(r.max_prj_wk)}</td>
          <td>Shpd / Wk L13</td><td class="right"><b>${fmt(r.shp_wk_l13)}</b></td>
          <td>1st Shpd Date</td><td>${fmtDate(r.first_shpd_date)}</td></tr>
      <tr><td>+/- Prj L4w</td><td class="right">${fmt(r.prj_l4w_change)}%</td>
          <td>Total Shpd L4</td><td class="right">${fmt(r.tot_shpd_l4)}</td>
          <td>Date 1st Rcvd</td><td>${fmtDate(r.date_1st_rcvd)}</td></tr>
      <tr><td>Prj 26 Wks</td><td class="right">${fmt(r.prj_26wks)}</td>
          <td>Total Shpd L13w</td><td class="right">${fmt(r.tot_shpd_l13w)}</td>
          <td>Last Whs Rcvd</td><td>${fmtDate(r.last_whs_rcvd)}</td></tr>
      <tr><td>Manual demand (rollup)</td><td class="right">${fmt(r.manual_demand_26w)}</td>
          <td>Total Shpd LTD</td><td class="right">${fmt(r.tot_shpd_ltd)}</td>
          <td>1st Out Date</td><td>${fmtDate(r.first_out_date)}</td></tr>
      <tr><td>Demand (Inv Flow 26w Σ)</td><td class="right">${fmt(r.demand_26w)}</td>
          <td colspan="2"></td>
          <td>Last OOS Date</td><td>${fmtDate(r.last_oos_date)}</td></tr>
    </table>
    <div class="stat-text" style="margin-top:4px;">
      <b>Days OOS till Next Rcpt:</b> ${fmt(r.days_oos_next_rcpt)} ·
      <b>Days OOS L12m:</b> ${fmt(r.days_oos_l12m)}
    </div>`;

  // ── KPI strip: high-signal numbers grouped by category ─────────────
  // Replaces the prior "Pipeline: X · Demand: Y · ..." line.  Each KPI is
  // a small block: label / value / color-coded relative to thresholds.
  const kpi = (label, value, hint, color) => `
    <div class="kpi" ${hint ? `title="${esc(hint)}"` : ''}>
      <div class="kpi-lbl">${label}</div>
      <div class="kpi-val" style="${color ? 'color:' + color + ';' : ''}">${value}</div>
    </div>`;
  const wosColor = (w) => {
    if (w == null || w === 0) return '#888';
    if (w < r.opt_wos) return '#c62828';
    if (w > 26) return '#1b5e20';
    return '#0d47a1';
  };
  const excessColor = (e) => e > 2500 ? '#c62828' : (e < -2500 ? '#e65100' : '#1b5e20');
  const oosColor = (d) => d > 14 ? '#c62828' : (d > 0 ? '#e65100' : '#1b5e20');

  const kpiStripHtml = `
    <div class="kpi-strip">
      ${kpi('ATS Now',         fmt(r.ats_now),         'Available to sell — after holds / allocations',           r.ats_now < 0 ? '#c62828' : '')}
      ${kpi('ATS WOS OH',      fmt(r.ats_wos_oh),      'Weeks of supply on hand (per QB)',                        wosColor(r.ats_wos_oh))}
      ${kpi('Open Cust PO',    fmt(r.open_cust_po_qty),'Outstanding customer PO qty awaiting shipment',           '')}
      ${kpi('Hold Qty',        fmt(r.hold_qty),        'Hold Order Qty — orders parked, not shipping',            r.hold_qty > 0 ? '#e65100' : '')}
      ${kpi('Days→Next Rcpt',  fmt(r.days_oos_next_rcpt), 'Days OOS until next supplier receipt arrives',         oosColor(r.days_oos_next_rcpt))}
      ${kpi('Pipe Excess',     fmt(r.pipeline_excess), 'Total pipeline - 26w demand - safety stock.  Positive = overstock, negative = under', excessColor(r.pipeline_excess))}
      ${kpi('PipeWOS',         (r.pipeline_wos == null ? '∞' : fmt(r.pipeline_wos)), 'Pipeline weeks of supply (all I/T + I/W + OH ÷ 26w demand)', '')}
      ${kpi('LT + CNY',        `${fmt(r.lt_wks)} + ${fmt(r.cny_weeks)}`, 'Lead time (weeks) + Chinese New Year shutdown weeks', '')}
      ${kpi('Days OOS L12m',   fmt(r.days_oos_l12m),   'Days out-of-stock in trailing 12 months — historical pain score', r.days_oos_l12m > 30 ? '#c62828' : '')}
      ${kpi('Customers',       fmt(r.customer_count),  'Active Acct-MStyles rolled up into this mstyle',          '')}
      ${kpi('Manual Demand',   fmt(r.manual_demand_26w), '26-week sum of customer manual projections (rolled up)', '')}
    </div>
    ${r.is_multi ? `
    <div style="margin-top:8px;padding:6px 10px;background:#fff8e1;border:1px solid #ffe082;border-radius:4px;font-size:11px;color:#5d4037;">
      🎁 <b>Multi-pack:</b> Each unit = ${r.pcs_per_kit} pcs of root <b>${esc(r.root_mstyle)}</b>.
      Root OH: <b>${fmt(r.qty_oh_root)}</b> pcs → can assemble <b>${fmt(r.assembleable_kits)}</b> more kits.
      Total effective kit availability: ${fmt((r.beg_inv && r.beg_inv[0]) || 0)} on-hand + ${fmt(r.assembleable_kits)} buildable = <b>${fmt(((r.beg_inv && r.beg_inv[0]) || 0) + r.assembleable_kits)}</b> kits.
    </div>` : ''}`;

  // ── Item Data box ──────────────────────────────────────────────────
  const itemDataBox = kvBox('Item Data', `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 14px;">
      <div>
        ${kvRow('Inner Pack',   r.inner_pack  || '—')}
        ${kvRow('Master Pack',  r.master_pack || '—')}
        ${kvRow('MOQ',          r.moq ? fmt(r.moq) : '—')}
        ${kvRow('Opt OH',       fmt(r.opt_oh))}
      </div>
      <div>
        ${kvRow('Opt WOS',      fmt(r.opt_wos))}
        ${kvRow('LT (Wks)',     fmt(r.lt_wks))}
        ${kvRow('LT + Opt Wks', fmt(r.lt_opt_weeks))}
        ${r.oos_dates ? kvRow('OOS Dates', esc(r.oos_dates)) : ''}
      </div>
    </div>
  `);

  // ── Overstock box ──────────────────────────────────────────────────
  const overstockBox = kvBox('Overstock', `
    ${kvRow('Over Cmtd Qty', `<span style="color:${r.over_committed_qty > 0 ? '#c62828' : 'inherit'}">${fmt(r.over_committed_qty)}</span>`)}
    ${kvRow('Ovr Comt WOS',  `<span style="color:${r.ovr_comt_wos  > 0 ? '#c62828' : 'inherit'}">${fmt(r.ovr_comt_wos)}</span>`)}
  `);

  // ── Aged Inventory ─────────────────────────────────────────────────
  const totalAged = (r.aged_inv_0_90 || 0) + (r.aged_inv_91_180 || 0) + (r.aged_inv_181_365 || 0) + (r.aged_inv_365plus || 0);
  const agePct = (n) => totalAged > 0 ? ' (' + Math.round(n / totalAged * 100) + '%)' : '';
  const ageColor = (days) => days > 180 ? '#c62828' : days > 90 ? '#e65100' : 'inherit';
  const agedInvHtml = `
    <div class="kv-grid">
      <div><b>Inv Age (Days):</b> <span style="color:${ageColor(r.invtry_age_days)}">${fmt(r.invtry_age_days)}</span></div>
      <div><b>% Time In Stock:</b> ${r.pct_time_in_stock > 0 ? fmt(r.pct_time_in_stock) + '%' : '—'}</div>
      <div><b>0–90 Days:</b> ${fmt(r.aged_inv_0_90)}${agePct(r.aged_inv_0_90)}</div>
      <div><b>91–180 Days:</b> <span style="color:${r.aged_inv_91_180 > 0 ? '#e65100' : 'inherit'}">${fmt(r.aged_inv_91_180)}${agePct(r.aged_inv_91_180)}</span></div>
      <div><b>181–365 Days:</b> <span style="color:${r.aged_inv_181_365 > 0 ? '#c62828' : 'inherit'}">${fmt(r.aged_inv_181_365)}${agePct(r.aged_inv_181_365)}</span></div>
      <div><b>&gt;365 Days:</b> <span style="color:${r.aged_inv_365plus > 0 ? '#c62828' : 'inherit'};font-weight:${r.aged_inv_365plus > 0 ? '700' : '400'}">${fmt(r.aged_inv_365plus)}${agePct(r.aged_inv_365plus)}</span></div>
    </div>`;

  return `
    <div class="dwrap">
      <div class="section" style="padding:10px 14px;">
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;">
          ${identityBox}${itemDataBox}${overstockBox}
        </div>
      </div>
      <div class="section">
        <h3>📦 Inventory Flow <span style="font-size:10px;font-weight:400;color:#888;">— hover Expected Receipts cells for PO detail · hover Prj Demand cells for customer breakdown</span></h3>
        <div style="overflow-x:auto">${invFlow}</div>
        ${kpiStripHtml}
      </div>
      <div class="section">
        <h3>📅 Aged Inventory</h3>
        ${agedInvHtml}
      </div>
      <div class="section">
        <h3>🎯 Recommended Actions</h3>
        ${recs}
      </div>
    </div>`;
}

// Wire up filter inputs
document.getElementById('searchInput').oninput = applyFilters;
document.getElementById('actionFilter').onchange = applyFilters;
document.getElementById('countryFilter').onchange = applyFilters;
document.getElementById('brandFilter').onchange = applyFilters;
document.getElementById('invMgrFilter').onchange = applyFilters;
document.getElementById('replenOnly').onchange = applyFilters;
document.getElementById('gapsOnly').onchange = applyFilters;
document.getElementById('overstockOnly').onchange = applyFilters;
document.getElementById('hideMulti').onchange = () => { buildTableHead(); applyFilters(); };
document.getElementById('clearBtn').onclick = () => {
  document.getElementById('searchInput').value = '';
  document.getElementById('actionFilter').value = '';
  document.getElementById('countryFilter').value = '';
  document.getElementById('brandFilter').value = '';
  document.getElementById('invMgrFilter').value = '';
  document.getElementById('replenOnly').checked = false;
  document.getElementById('gapsOnly').checked = false;
  document.getElementById('overstockOnly').checked = false;
  document.getElementById('hideMulti').checked = true;   // default on — multi-packs hidden
  // Clear per-column filters
  for (const k of Object.keys(colFilters)) delete colFilters[k];
  // Clear priority button selection
  priorityFilter = '';
  // Reset to default composite sort (inv_mgr → brand → mstyle)
  currentSort = { id: null, dir: 1 };
  buildTableHead();
  applyFilters();
};

boot();
</script>
</body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quieter than default — only log errors
        if "404" in (fmt % args) or "500" in (fmt % args):
            super().log_message(fmt, *args)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = _HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/logo.png":
            logo_path = os.path.join(os.path.dirname(__file__), "..", "codepage", "p+p_Logo.png")
            try:
                with open(logo_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
        elif self.path == "/api/meta.json":
            body = json.dumps({
                "as_of": _DATA_AS_OF,
                "record_count": len(_RECORDS),
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/records.json":
            if not _PAYLOAD_JSON_GZ:
                msg = json.dumps({"status": "loading",
                                  "message": "Pulling from Quickbase — please wait…"}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            accept = (self.headers.get("Accept-Encoding") or "").lower()
            if "gzip" in accept and _PAYLOAD_JSON_GZ:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(_PAYLOAD_JSON_GZ)))
                self.end_headers()
                self.wfile.write(_PAYLOAD_JSON_GZ)
            else:
                raw = gzip.decompress(_PAYLOAD_JSON_GZ) if _PAYLOAD_JSON_GZ else b"[]"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")


class _ThreadedServer(socketserver.ThreadingTCPServer):
    """Threaded so concurrent browser requests don't queue behind each other."""
    allow_reuse_address = True   # avoids WinError 10048 on quick restart


def serve(port: int = VIEWER_PORT_DEFAULT, open_browser: bool = True, host: str = "0.0.0.0") -> None:
    import socket
    handler = Handler
    httpd = _ThreadedServer((host, port), handler)
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"\n[InvMgmt] Serving at http://127.0.0.1:{port}  (local)")
    print(f"[InvMgmt] Team access:  http://{local_ip}:{port}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[InvMgmt] Shutting down.")
        httpd.shutdown()


def _load_records_cache(max_age_hours: float) -> Optional[bytes]:
    """Return the gzipped JSON payload from disk if fresh, else None.
    Uses file mtime as the freshness signal (no separate metadata file needed)."""
    if not RECORDS_CACHE_PATH.exists():
        return None
    age_sec = time_mod.time() - RECORDS_CACHE_PATH.stat().st_mtime
    age_hours = age_sec / 3600
    if age_hours > max_age_hours:
        print(f"[InvMgmt] Cache stale ({age_hours:.1f}h old, TTL {max_age_hours}h) — re-pulling from QB.", flush=True)
        return None
    try:
        data = RECORDS_CACHE_PATH.read_bytes()
        # Sanity-check: confirm gzip header
        if len(data) < 10 or data[:2] != b"\x1f\x8b":
            print(f"[InvMgmt] Cache file missing gzip header — ignoring.", flush=True)
            return None
        print(f"[InvMgmt] Warm cache hit: loading from {RECORDS_CACHE_PATH.name} ({age_hours:.1f}h old, {len(data):,} bytes)", flush=True)
        return data
    except Exception as e:
        print(f"[InvMgmt] Cache read failed ({e}) — re-pulling from QB.", flush=True)
        return None


def _save_records_cache(payload_gz: bytes) -> None:
    """Atomically write the gzipped payload to disk for warm-cache reuse."""
    try:
        tmp = RECORDS_CACHE_PATH.with_suffix(RECORDS_CACHE_PATH.suffix + ".tmp")
        tmp.write_bytes(payload_gz)
        tmp.replace(RECORDS_CACHE_PATH)
        print(f"[InvMgmt] Cached {len(payload_gz):,} bytes to {RECORDS_CACHE_PATH.name} (TTL {CACHE_TTL_HOURS}h)", flush=True)
    except Exception as e:
        print(f"[InvMgmt] [WARN] Could not write records cache: {e}", flush=True)


def main():
    p = argparse.ArgumentParser(description="Inventory Management Viewer")
    p.add_argument("--mstyle", action="append", help="Filter to specific mstyle(s) — repeatable")
    p.add_argument("--dry-run", action="store_true", help="Build data, print summary, exit (no server)")
    p.add_argument("--port", type=int, default=VIEWER_PORT_DEFAULT, help=f"HTTP port (default {VIEWER_PORT_DEFAULT})")
    p.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser")
    p.add_argument("--refresh", action="store_true", help="Force a fresh pull from QB (ignore disk cache)")
    p.add_argument("--cache-ttl", type=float, default=CACHE_TTL_HOURS, help=f"Disk cache TTL in hours (default {CACHE_TTL_HOURS})")
    args = p.parse_args()

    global _RECORDS, _PAYLOAD_JSON_GZ, _DATA_AS_OF

    # ── Warm-cache fast path ───────────────────────────────────────────
    # If a fresh cache exists and --refresh wasn't passed, serve from disk
    # in seconds instead of re-running the ~10-min CData pipeline.
    # Filtered runs (--mstyle / --dry-run) always bypass the cache.
    cache_usable = (not args.refresh) and (not args.mstyle) and (not args.dry_run)
    if cache_usable:
        cached = _load_records_cache(args.cache_ttl)
        if cached is not None:
            _PAYLOAD_JSON_GZ = cached
            # Use the cache file's mtime as the "as of" timestamp
            try:
                mtime = RECORDS_CACHE_PATH.stat().st_mtime
                _dt = datetime.fromtimestamp(mtime)
                _DATA_AS_OF = f"{_dt.month}/{_dt.day}/{_dt.year} {_dt.hour % 12 or 12}:{_dt.minute:02d} {'AM' if _dt.hour < 12 else 'PM'}"
            except Exception:
                _DATA_AS_OF = "cached"
            # Decompress just to count records for the welcome line
            try:
                _count = len(json.loads(gzip.decompress(cached)))
            except Exception:
                _count = "?"
            print(f"[InvMgmt] Serving {_count} cached records — to refresh from QB, restart with --refresh", flush=True)
            serve(port=args.port, open_browser=not args.no_browser)
            return

    # ── Start server immediately so browser can connect (returns 503 until data ready) ──
    if not args.dry_run:
        _srv_thread = threading.Thread(
            target=serve,
            kwargs={"port": args.port, "open_browser": not args.no_browser},
            daemon=True,
        )
        _srv_thread.start()

    _RECORDS = build_records(mstyle_filter=args.mstyle)
    print_summary(_RECORDS)

    if args.dry_run:
        return

    _dt = datetime.now()
    _DATA_AS_OF = f"{_dt.month}/{_dt.day}/{_dt.year} {_dt.hour % 12 or 12}:{_dt.minute:02d} {'AM' if _dt.hour < 12 else 'PM'}"

    print(f"\n[InvMgmt] Pre-serializing {len(_RECORDS)} records for the browser...")
    _PAYLOAD_JSON_GZ = _build_payload(_RECORDS)
    print(f"  [OK] {len(_PAYLOAD_JSON_GZ):,} bytes gzipped")

    # Save to disk so the next launch within TTL is instant
    if not args.mstyle:   # don't pollute the cache with filtered runs
        _save_records_cache(_PAYLOAD_JSON_GZ)

    print(f"[InvMgmt] Data ready — browser will load automatically.")
    _srv_thread.join()   # keep main thread alive


if __name__ == "__main__":
    main()
