---
name: feedback_forecasting_methodology
description: Key learnings from iterating on the inventory forecasting model — what works, what doesn't, and what needs fixing next
type: feedback
originSessionId: ef36e6c4-ceed-4be8-808f-d35dfe3766a0
---
## Forecasting Model Tuning — Lessons Learned (April 2026)

**HW vs Croston's classification is still not right.** The current thresholds (CV<=1.0 AND zero_rate<=40% → HW, else Croston's) produce 109 HW / 647 Croston's. Both models have issues:

- **Holt-Winters collapses on trailing zeros.** If the last few weeks of L13W are zero (common in CPG — customer just hasn't ordered yet this cycle), the trend component goes deeply negative and drives the entire 26-week forecast to near-zero. A trend floor was added (midpoint can't go below 50% of level) but it's not enough — BB0098 still projects 237/wk vs 1,058/wk actual.

- **Croston's over-projects.** It only averages non-zero demand events, ignoring zero weeks entirely. BB0578 example: L13W all-weeks avg is 934/wk but Croston's projects 1,342/wk because it uses the 1,104 non-zero avg. The fix is to scale Croston's output so the 26-week total aligns with the L13W all-weeks average, not the non-zero average.

**Why:** The user wants the AI forecast to be close to what the customer is actually ordering (L13W avg including zeros). Any model that consistently over- or under-projects vs L13W avg is not credible.

**How to apply:** Next session should focus on adding a post-model rescaling step: after any model (HW or Croston's) produces its 26-week forecast, scale the total so the weekly average matches the L13W all-weeks average. This preserves the week-to-week shape from the model while anchoring the volume to reality.

## Validation Baseline

Changed from L13W non-zero avg to **L13W all-weeks avg** (including zeros). This is correct — zeros are real weeks where the customer didn't order, and the baseline should reflect that.

## Issue Narratives

- Per-week comments are noisy and repetitive. Use **one record-level narrative** instead.
- Never assume data entry mistakes. Ask questions, present data.
- Don't explain how the AI model works. Just show where manual projection diverges from data.
- The narrative should focus on: flat-line detection, front/back-loaded distribution, gap weeks, spike weeks, and master pack violations.

## Viewer Preferences

- Hide LOW priority by default (filter defaults to Critical + Medium)
- All header numbers should be whole numbers (no decimals)
- Description column from QB `Description` field
- Column order: Key, Customer, Mstyle, Description, Priority, Ord/Wk L13W, Shpd/Wk L13W, Proj/Wk, AI Fcst/Wk, AI vs Proj, AI vs Shpd, Flags, Flag, Comments
- AI vs Proj and AI vs Shpd: 14px bold, green if positive, red if negative
- Detail view: Projection row + AI Forecast row + Analysis paragraph (no per-week issues)
- Week labels computed from current Sunday, not CData column names (CData caches stale names)
- W1 in QB is `04_12_W1` but CData still calls it `03_29_W1` — data is correct, labels are stale
