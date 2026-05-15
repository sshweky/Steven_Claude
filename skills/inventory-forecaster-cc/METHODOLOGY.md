# Inventory Forecaster — Decision Logic & Methodology

Audience: Pets+People inventory planners (VP review).
Scope: 26-week forward demand forecast per `Acct_MStyle_Key_`.

---

## 1. Pipeline at a glance

```
Quickbase Projections + Order_History + Amazon_Catalog
        │
        ▼
  Classify each record into one of 9 models
        │
        ▼
  Run the matched model on a weighted 78-obs history
  (52w order history + L13W appended twice for 3× weight)
        │
        ▼
  Apply event lifts (Prime Day W7-W9, Fall Deal W23-W25)
        │
        ▼
  Snap to master pack multiple
        │
        ▼
  VP-Q4: zero-out forward weeks with confirmed customer POs
        │
        ▼
  Compare vs manual projections → AI_ALERT if >5% variance
        │
        ▼
  Write-back AI_PRJ_W1..W26 + AI_ALERT
```

---

## 2. Model classification

Routing is deterministic, evaluated in this order:

| Test | Result | Model |
|---|---|---|
| Zero L13W AND zero L26W | "Inactive" → 0 | **Inactive** |
| Brand new (≤6 weeks of history) | Ramp pattern | **New/Relaunch** |
| Recently inactive, now reactivating | Heuristic | **Reactivating** |
| <13 active weeks in L52W | Sparse | **Sparse Intermittent** |
| Dense (≥50% non-zero L13W) | Smooth | **Seasonal Baseline** |
| Intermittent (CV>0.5 or zeros>20%) | Lumpy | **Croston's** |
| Steady (CV≤0.5, zeros≤20%) | Smooth | **Holt-Winters** |
| Out-of-policy / discontinued | OTB / EOL | **OTB (zero)** |

---

## 3. Models — what each one does

**Holt-Winters** (steady demand)
- α=0.3 level smoothing, β=0.1 trend
- 78-obs weighted series (3× recent 13w)
- 26 unique seasonal factors from L52W
- Cap: L13W avg × 1.25 normal, × 1.50 event weeks (downward only)

**Croston's** (intermittent demand)
- α=0.3 over weighted series
- z (size) and p (period) refined: 70% L13W / 30% smoothed model
- Quantities scaled by L52W seasonal profile
- Event weeks insert extra orders at lift multipliers

**Seasonal Baseline** (smooth dense)
- Order-history baseline = **L13W non-zero average** (excludes post-event drawdown zeros)
  - Fallback: L26W non-zero avg → L13W all-weeks avg
- Amazon POS blend (Amazon only):
  - `baseline = ord_baseline × 0.55 + pos_rate × 0.45`
  - POS trend tilts weights toward L4W (accelerating) or L26W (decelerating)
- Damped seasonal profile (DAMP=0.1, ±20% from 1.0)
- Explicit event lifts on top of profile

**Sparse Intermittent** (<13 active weeks)
- Average over active weeks only
- L52W seasonal scaling
- No HW/Croston (insufficient signal)

**Heuristic** (Reactivating, complex edge cases)
- Post-ramp average → L13W non-zero → L52W → fallback
- Same seasonal+event treatment as baseline

**New/Relaunch / OTB / Inactive / Inactive+Floor (R3)** — deterministic small models for narrow patterns.

---

## 4. Event calendar

| Event | Weeks (forward) | Lift | Applies to |
|---|---|---|---|
| Prime Day pre-order | W7-W9 | ×1.25 | **Amazon only** (orders ~6-8w before July event) |
| Fall Deal pre-order | W23-W25 | ×1.12 | All accounts |

`AMAZON_CUST_SUBSTR = "AMAZON"` gates Prime Day and Amazon POS pulls.

---

## 5. VP-led changes (May 2026 stack)

The current production logic incorporates four feedback items from VP of Planning:

### VP-Q1 — Evidence-based baseline mode

**Before:** baseline used L13W *all-weeks* average (suppressed by post-event drawdown zeros).
**After:** `seasonal_baseline()` defaults to L13W *non-zero* average. Falls through to L26W non-zero, then L13W all-weeks, then last-resort.
**Why:** Post-Prime Day quiet periods were dragging baselines down ~15-20%, causing AI to under-project ongoing demand.
**Back-test impact (acct 1864):** +18.5% aggregate 26w demand, broadly distributed.

### VP-Q2 — OOS-aware demand reconstruction

**Before:** raw `Ord_LW_n` used as demand; OOS-cancelled orders looked like genuine zero demand.
**After:** Order_History pulled per-week; cancellations classified into:
- **Bucket A** (OOS-driven — `Inventory Error`, `Supplier Delay`, etc.) → keep demand intent (raw order qty)
- **Bucket B** (demand-invalidating — `Customer Order Error`, `Future Delete`, `Low Margin`) → subtract from clean demand
- **Bucket C** (ambiguous — `Other`, `Any`, null) → keep as-is

Clean demand (`clean_ord`) replaces `raw_ord` in the model fit. OOS severity (≥15% in any week) is logged as a driver.
**Back-test impact (acct 1864):** modest aggregate (≤2%); large per-record swings on items with chronic OOS history.

### VP-Q3 — Cadence relaxation (no bi-weekly enforcement)

**Before:** `detect_biweekly()` flagged any record with ≥70% zeros on one parity over L26W as bi-weekly, then forced parity zeros in the forecast.
**After:** `detect_biweekly()` only fires for **monthly+** cadences (median gap ≥3 weeks AND ≥60% gap consistency). Returns the median gap (3, 4, 5...) so `apply_ordering_pattern()` does N-week chunk merging.
**Why:** Bi-weekly enforcement was synthesizing zeros for items that just happened to have one quiet parity in the recent past — over-constrained the forecast and discarded recent recovery signal.

### VP-Q4 — Don't double-count confirmed customer POs

**Before:** AI projected demand on top of confirmed open customer POs that downstream replen logic was already counting → systematic over-projection in the front weeks.
**After:** for each `Acct_MStyle_Key_`, pull all `Order_History` rows with `Qty_Open > 0`, bucket by **Cancel_Date** (the customer ship-by deadline; *not* `Next_Rcpt_Date`, which is incoming supplier inventory) into forward weeks W1-W26, then **strict zero** AI_PRJ in any week with confirmed PO qty > 0.
**Why strict zero (not subtract):** the confirmed PO IS the demand signal for that week. Subtraction risks negative residuals; strict zero is conservative and matches replen's worldview.

---

## 6. Master pack & rounding

All non-zero forecast weeks are snapped to the nearest multiple of `Master_Pack` (default 1 if missing). Snapping happens **before** VP-Q4 zero-out.

---

## 7. Alert generation

After forecasting, compare AI to manual projections:
- AI_ALERT fires if absolute variance > 5%
- Alert text includes model name, key drivers, OOS notes, event-window flags, VP-Q4 zero-out summary
- Alerts surface in the viewer as the planner-facing first read

---

## 8. Validation mode (read-only)

`--validate` reuses the same data pull and pattern classifier but flags manual projections rather than writing AI ones:
- **CRITICAL:** >5× spike, demand on inactive item
- **WARNING:** outside [0.3×, 2.0×] of expected band, sudden stop, off-cadence for monthly+ patterns, not master-pack multiple

---

## 9. Configuration knobs

```python
PRIME_DAY_WEEKS    = {7, 8, 9}      # Amazon-only pre-order
FALL_DEAL_WEEKS    = {23, 24, 25}
PRIME_DAY_LIFT     = 1.25
FALL_DEAL_LIFT     = 1.12
AMAZON_CUST_SUBSTR = "AMAZON"
DAMP               = 0.1            # seasonal profile dampening
ALERT_THRESHOLD    = 0.05           # 5% variance
```

CLI gates:
- `--oos-smoothing` enables VP-Q2 (default off; on under back-test runner)
- `--no-po-zero` disables VP-Q4 (default on)
- VP-Q1, VP-Q3 always on (baked into model logic)

---

## 10. Quickbase schema (write-back)

| Field | Source |
|---|---|
| `AI_PRJ_W1`..`AI_PRJ_W26` | 26-week forecast vector |
| `AI_ALERT` | Generated alert text |
| `Acct_MStyle_Key_` | Primary key (filter: `Status_Cust LIKE 'A%'`) |

Write-back uses parallel UPDATE with progress tracking every 50 records. Resume via `--resume forecast_results.completed.json` if interrupted.
