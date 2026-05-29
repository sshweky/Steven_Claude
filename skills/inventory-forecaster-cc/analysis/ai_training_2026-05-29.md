# AI Training Comment Review
**Generated:** 2026-05-29
**Lookback:** 2 days
**Comments analyzed:** 6

---

## 1. Executive Summary

| Metric | Value |
|---|---|
| Total comments | 6 |
| Net unit gap (MAN - AI) | +47,438 |
| AI over-projects (planner cut >100u) | 1 |
| AI under-projects (planner boosted >100u) | 3 |
| EOL / wind-down signals | 0 |
| Zero-out signals | 4 |

## 2. Pattern Groups (sorted by unit impact)

| Intent | Model Fit | Count | Unit Gap | Primary Recommendation |
|---|---|---|---|---|
| increase | needs_context | 1 | +22,698 | Pattern requires manual review. Check the comment text, AI model, and MAN vs AI... |
| wrong_model | wrong_model | 1 | +18,614 | Planner says model should be Seasonal but current model is Manual Baseline (over... |
| zero | over_projecting | 4 | +6,126 | Confirm VP-Q4 forward-PO zeroing is working for this account. If PO exists but W... |

## 3. Proposed Model Changes

*All recommendations below are validated against systemic impact before being shown. VALIDATED = original fix is good. REJECTED = original fix widened gap, replaced with a directional-guard version. ISOLATED = one-off item, item-level fix proposed.*

### [1] [NEUTRAL] Investigate -- INCREASE / needs context
**Impact:** +22,698 units across 1 item(s)  | **Confidence:** LOW  | **Systemic Status:** NEUTRAL

**Proposed Change:**  
Pattern requires manual review. Check the comment text, AI model, and MAN vs AI projection detail in the QB viewer.

**Rationale:** Insufficient signal to generate specific recommendation.

**Affected items:**
- `23011-FF10159` (WAL MART STORES / Arm & Hammer Core Gr) Model: Retailer WOS (POS) | Gap: +22,698u  Comment: "baseline at 7470 u is too week when POS rate looks more around 8500 pcs. ALso, t"

### [2] [ISOLATED] Model Switch -- WRONG_MODEL / wrong model
**Impact:** +18,614 units across 1 item(s)  | **Confidence:** MEDIUM  | **Systemic Status:** ISOLATED

**Proposed Change:**  
Item-level model fix: no systemic criterion found across 4 Manual Baseline (override) records that narrows the gap. Targeted fix for 1 item(s): (1) Check if 'FF86' is in derived_category_profiles.json -- if missing, add it to route to Seasonal or Croston's. (2) Immediate: add a Tell-AI comment targeting MAN PRJ level. Affected: 1864-FF8654 (AMAZON.COM.KYDC, AI 63,136u vs MAN 81,750u, gap +18,614u).

**Rationale:** Exhaustive variation testing on 4 Manual Baseline (override) records found no criterion that systematically narrows the gap. Root cause is item-specific model selection; a category-profile registration or Tell-AI override is the right path.

**Affected items:**
- `1864-FF8654` (AMAZON.COM.KYDC,INC / Glad for Pets) Model: Manual Baseline (override) | Gap: +18,614u  Comment: "this is a horrible projection!! You should NEVER forecast a flat qty! You are su"

### [3] [NEUTRAL] Suppression -- ZERO / over projecting
**Impact:** +6,126 units across 4 item(s)  | **Confidence:** MEDIUM  | **Systemic Status:** NEUTRAL

**Proposed Change:**  
Confirm VP-Q4 forward-PO zeroing is working for this account. If PO exists but W1 is not zeroed, check bucket logic in vp_q4 module. Consider extending zero window when PO covers all 26 weeks.

**Rationale:** 'PO covers' comment suggests VP-Q4 logic may not be zeroing the full window or the PO is future-dated beyond lookback.

**Affected items:**
- `1579-BB28473` (ACE HARDWARE CORPORATION / Clorox Fraganzia) Model: Seasonal Baseline | Gap: +0u  Comment: "Always zero W1,W2,W3, if the order exist in the same week in account ACE HARDWAR"
- `3466-FF8990` (C & S WHOLESALE / Fresh Step) Model: Seasonal Baseline | Gap: +0u  Comment: "Always zero W1,W2,W3 if their open order exist in the same week in account C & S"
- `3102-FF8990` (CHEWY.COM / Fresh Step) Model: Seasonal Baseline | Gap: +9,228u  Comment: "Always zero W1,W2,W3 if their open order exist in the same week in account CHEWY"
- `13640-BB21626` (BURLINGTON COAT FACTORY / Fabuloso) Model: Sparse Intermittent | Gap: -3,102u  Comment: "zero out W10 prj as it have open POs add up to similar volume withing 4 weeks"

## 4. Comment Detail

| Key | Customer | Model | Intent | Fit | AI 26w | MAN 26w | Gap | Comment |
|---|---|---|---|---|---|---|---|---|
| 23011-FF10159 | WAL MART STORES | Retailer WOS (POS) | increase | needs_context | 200,898 | 223,596 | +22,698 | baseline at 7470 u is too week when POS rate looks more arou |
| 1864-FF8654 | AMAZON.COM.KYDC,INC | Manual Baseline (ove | wrong_model | wrong_model | 63,136 | 81,750 | +18,614 | this is a horrible projection!! You should NEVER forecast a  |
| 3102-FF8990 | CHEWY.COM | Seasonal Baseline | zero | over_projecting | 26,376 | 35,604 | +9,228 | Always zero W1,W2,W3 if their open order exist in the same w |
| 13640-BB21626 | BURLINGTON COAT FACTORY | Sparse Intermittent | zero | over_projecting | 11,502 | 8,400 | -3,102 | zero out W10 prj as it have open POs add up to similar volum |
| 1579-BB28473 | ACE HARDWARE CORPORATION | Seasonal Baseline | zero | over_projecting | 3,864 | 3,864 | +0 | Always zero W1,W2,W3, if the order exist in the same week in |
| 3466-FF8990 | C & S WHOLESALE | Seasonal Baseline | zero | over_projecting | 1,493 | 1,493 | +0 | Always zero W1,W2,W3 if their open order exist in the same w |

---
*Report generated by `scripts/ai_training_review.py` on 2026-05-29*

## 5. Systemic Impact Estimate

*Systemic impact was computed BEFORE recommendations were finalized. VALIDATED = fix narrows MAN-AI gap. REJECTED = fix widens gap. ISOLATED = 0 records match criteria. Variance = MAN PRJ 26w - AI PRJ 26w (flagged records only). After = MAN - estimated new AI once fix is applied.*

| Change # | Model | In Scope | Flagged | MAN-AI Before | Before% | MAN-AI After | After% | AI Change | Direction | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| [1] | Retailer WOS (POS) | 204 | 204 (100%) | +1,071,364 | +25.7% | +1,071,364 | +25.7% | +0 | MIXED | NEUTRAL |
| [2] | Manual Baseline (override) | 4 | 0 (0%) | +0 | n/a | +0 | n/a | +0 | MIXED | ISOLATED |
| [3] | Seasonal | 1,040 | 1,040 (100%) | +622,660 | +11.5% | +622,660 | +11.5% | +0 | DOWN | NEUTRAL |
| **Combined** | Retailer WOS (POS) + Manual Baseline (override) + Seasonal | 1,248 | 1,244 (100%) | +1,694,024 | +17.7% | +1,694,024 | +17.7% | +0 | MIXED | COMBINED |
