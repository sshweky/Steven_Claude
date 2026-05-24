# Algorithm Improvement Suggestions

Generated 2026-05-23 from the full `--all --validate` run (5,571 records).

**Top-line numbers:**
- **2,120 records (38.1%)** have |AI - manual| / manual > 5%
- **DOWN bias is 2.8x heavier than UP** (1,557 DOWN vs 563 UP) — AI is over-projecting on 73% of disagreements
- **Total |unit gap|: 5.94M units** across all flagged records
- **Median |delta|: 43.9%** — when AI and planner disagree, they disagree a LOT

The pattern is unambiguous: **AI is systematically too aggressive on intermittent / ramping / off-price / wind-down items, and too conservative on dense new-launches**. Most of the gap concentrates in a small number of fixable structural mistakes.

---

## Priority 1 — New-Launch Walmart "PDQ" items misclassified as Sparse Intermittent (~$700K aggregate gap)

**Pattern observed:** Walmart launches a new SKU (e.g. `23011-BB38905PDQ`). Last 6 weeks have orders averaging 10k-20k/wk steadily ramping. First 20 weeks were 0 because the item hadn't launched.

**Planner does:** projects flat ~10k/wk for the full 26 weeks (236k total).

**AI does:** classifies as **Sparse Intermittent** (only ~6/52 weeks have orders), then the Sparse model places ONE big order at W14 = 35k. Total = 35k, an 85% miss.

**Root cause:** `classify()` and `_detect_otb()` count non-zero weeks across the full L52W window. A new launch with 6 consecutive non-zero weeks at the END of the window looks identical to a sparse intermittent buyer with 6 scattered orders.

**Proposed fix (new rule CLS-010 / F72):**
```
If L52W non-zero count < 13 (would route to Sparse Intermittent)
  AND L26[-6:] non-zero count >= 4   (recent dense activity)
  AND L26[-12:-6] all-zero count >= 5 (the pre-launch gap)
THEN
  reroute to Heuristic with baseline = mean(L26[-6:] non-zero)
  driver: "F72 New-launch ramp detected: 6w continuous ordering after pre-launch gap"
```

**Estimated impact:** Recovers ~$700K of unit gap across ~50 Walmart launches plus similar PetSmart/Target patterns.

---

## Priority 2 — Amazon items with a recent BURST then drawdown (~$1.9M Amazon gap)

**Pattern observed (#3 `1864-BB30930`):** Amazon ordered lumpy small for 22 weeks, then suddenly 11,004 and 9,108 in the last 2 weeks (clear Prime-Day stock-up).

**Planner does:** front-load W1-W6 at 2,250-8,000 (consuming the stock-up bubble), then taper to 1,775/wk steady. Total 73k.

**AI does:** sees the burst weeks dominating L13, projects 7,392/wk steady all 26 weeks. Total 164k (+124%).

**Root cause:** F18 (POS anchor) should have detected `implied weekly order rate >> POS_L13W` and capped down — but for this record it didn't fire because L26 average is muddled by the lumpy earlier weeks. The L13_nz_avg includes the burst.

**Proposed fix (refine F18 / add F18b):**
```
After F18 z compute:
  Compute L4W_avg and L13W_avg from history (NOT just nz).
  If L4W_avg > L13W_avg * 1.8 (recent burst >> L13 baseline)
    AND POS_L13W > 0
    AND L4W_avg > POS_L13W * 1.5  (burst not explained by POS):
       Treat burst as one-time pre-buy.
       Cap z to L13W_avg (excluding the L4W burst weeks) * 1.2
       Set meta["f18b_burst_carved_out"] = True
       narrative: "F18b Recent burst treated as one-time pre-buy"
```

**Estimated impact:** Reduces Amazon over-projection on ~150 records by 20-40% each.

---

## Priority 3 — Off-price retailers (Ross / Burlington / DD's / Kohl's / Variety) — manual says 0, AI says >0 ($512K gap)

**Pattern observed:** 256 records across off-price retailers (Ross, Burlington, DD's, Variety, Kohl's, Big Lots, Five Below). Planner sets manual = 0 (the item is being closed out, no more orders). AI projects something based on history.

**Planner intent:** these are *closeout / OTB* channels — once the deal is done, no replenishment. Manual = 0 is the right answer.

**AI does:** R1 (OTB detection) catches *some* of these but not all. The remainder fall through to Croston's / Sparse Intermittent with non-zero output.

**Proposed fix (tighten R1 / add CLS-011):**
```
Expand R1 OTB detection: a "pure OTB" record is one where
  customer is in OFFPRICE_CUST_SUBSTRS
  AND manual_total < 100 OR L4W = 0
  AND L26 has a recognizable concentration pattern (>=70% units in single 1-3w window)
Treat as OTB(zero) -- forecast all weeks = 0.

ALSO: if F65 (zero-velocity) detected AND customer in OFFPRICE_CUST_SUBSTRS,
hard-zero regardless of M1/R3 floors.
```

**Estimated impact:** Eliminates ~$500K of false positives across 200+ off-price records. Eliminates planner overrides from ~14% of total disagreements.

---

## Priority 4 — Status_Cust = "FD" items: F52 wind-down too generous ($301K gap)

**Pattern observed (#8 `23011-BB13437CLR/12`):** Status_Cust = "FD" (Forecasted Demand wind-down). L26 history shows the item peaked in early weeks then dropped to single-order activity. Manual: flat 300/wk total 7,500. AI: ramps from 3,300 down to 696 (still 69k, +823% over manual).

**Root cause:** F52 (FD wind-down) is using its own decay schedule. The planner already knows the residual demand and put 300/wk. AI is ignoring that signal and computing its own decay.

**Proposed fix (F52 enhancement):**
```
When Status_Cust starts with "FD":
  Compute planner_rate = sum(manual[i] for i in last 8 non-zero weeks) / count
  If planner_rate > 0:
    Cap each AI week to max(AI[i], planner_rate * 1.5)
    Cap each AI week to min(AI[i], planner_rate * 2.5)  -- hard ceiling
    Narrative: "F52 FD wind-down anchored to planner residual rate"
```

**Estimated impact:** Recovers $200-300K across ~80 FD records. Aligns AI with planner's domain knowledge on wind-downs.

---

## Priority 5 — Walmart NEW Croston's items: F61 horizon decay over-applies ($800K gap)

**Pattern observed (#4, 5, 9, 10):** Status_Cust = "A: NEW", item launched recently. L26[-6:] shows ramping orders. Manual: planner projects flat ~6-9k/wk for 26 weeks. AI: Croston's path produces declining schedule (5661 → 4023) due to F61 horizon decay × 0.88 on W9-W26.

**Root cause:** F61 says "decay back half because planners trim back half." But for **NEW items** the opposite is true — planners RAMP back half because the channel is loading.

**Proposed fix (F61 guard):**
```
Currently F61 fires unless _f61_has_cat_prof.
Add condition: AND status_cust does NOT contain "NEW"
              AND L26[-4:] non-zero count >= 3 (item is currently active)
i.e. F61 should NOT decay forecasts of items in launch/ramp state.
```

**Estimated impact:** ~$600K-$800K of new-launch under-projection corrected across ~70 records. This is in addition to the Priority 1 fix.

---

## Priority 6 — F37 (forward inventory shortfall) zeroes W1-W2 too aggressively on Walmart Croston's items

**Pattern observed:** Across the top-50 gap records, AI W1 and W2 are 0 for most Walmart Croston's items even though manual has 7,000-10,000 in those weeks.

**Root cause:** F37 is keying off OPN_W1 (open inventory at start of week 1) — when warehouse SOH > some threshold, it zeros W1-W2 AI assuming the warehouse can cover. But for **new SKUs in growth phase**, this is wrong: warehouse SOH and incoming orders both happen.

**Proposed fix (F37 guard):**
```
Skip F37 zeroing when:
  - status_cust contains "NEW", OR
  - L4W avg > 0.8 * L13W avg (item is in active growth)
```

**Estimated impact:** Recovers ~$300K of W1-W2 demand signal correctly attributed to new-launch ordering.

---

## Priority 7 — Amazon Croston's burst-history misinterpretation ($400K F59g over-projection)

**Pattern observed (#12 `1864-FF9297/24`):** L26 history has 4 active weeks scattered (5760, 11808, 7776, 11520) — Amazon Prime Day pre-buys. Croston z = 7,000ish, p = ~5. AI projects 4,296/wk steady (110k total). Manual: 2,500-3,000/wk (69k). F59g (HIGH tier Amazon +8% buffer) is firing.

**Root cause:** Croston's interprets the Prime Day burst weeks as "average order size" rather than "event-driven outlier." F59g adds 8% on top.

**Proposed fix (Croston's event-aware z):**
```
Before computing z:
  If item is Amazon AND event_boost_weeks intersect with the burst weeks in L26 history:
    Exclude those burst weeks from z computation
    (Future event boosts will re-add them at the right calendar time)
    Narrative: "Croston z computed excluding event-burst weeks ({list})"
```

**Estimated impact:** ~$200-400K reduction in Amazon over-projection on items that ride the Prime Day cycle.

---

## Priority 8 — Pre-launch zeros in `L26[:18]` confuse the model into low z

This is the inverse of Priority 1. When a new item has 18+ zeros at the start of L26 (because it didn't exist) and 8 active weeks at the end, Croston interprets it as "infrequent buyer" — z is high (correct) but p is large (wrong, leading to spread-thin orders).

**Proposed fix (Croston pre-launch trim):**
```
In crostens(), if first non-zero week index in L26 is >= 8:
  Trim history to only the period since first non-zero week (effectively new_age = 26 - first_nz)
  Recompute z and p on the trimmed history.
  This gives the correct cadence interpretation for new launches.
```

**Estimated impact:** Synergistic with Priority 1; together they would close most of the $1M+ new-launch gap.

---

## Priority 9 — TARGET CTRL INV PRCSNG: only customer where AI is too LOW

**Pattern observed:** Of 15 disagreements at Target CTRL, 13 are UP-bias (manual > AI). Total gap $103K, all in the same direction.

**Hypothesis:** Target's ordering pattern doesn't match what F66 customer-bias corrections handle. Could be a planning calendar mismatch (Target orders monthly in bulk, not weekly).

**Proposed fix:** Add `TARGET CTRL INV PRCSNG` to CUSTOMER_BIAS_CORRECTIONS with multiplier ~1.4 (consistent with manual being 40% above AI).

**Estimated impact:** ~$100K recovered.

---

## Priority 10 — Trade fall calendar (F64) fires on disagreeing records 1,050 times

F64 is the #1 firing rule on disagreements (49% of all flagged records). This either means:
- F64 is correctly identifying high-volatility weeks (and disagreement is unrelated)
- F64 is mis-targeting weeks (W17-18 / W21-22) for items where those weeks aren't the right peak

**Proposed investigation (not a fix yet):** Run `tune_thresholds.py` with grid over `TRADE_FALL_REPLEN_LIFT` and `TRADE_FALL_SEASON2_LIFT` to see if lower values reduce gap. If F64 +10%/+8% is over-projecting, drop to +5%/+3%.

---

## Cross-cutting observations

1. **The model is over-confident in dense-history projection of intermittent buyers.** Across categories, when L26 shows 4-8 lumpy orders, Croston's projects steady 4k-7k/wk forever. Planners consistently halve this.

2. **The "NEW" lifecycle stage is under-served.** Every fix above that mentions Status_Cust = "A: NEW" or "A: NEW M/YY" is recovering gap. A dedicated NEW-launch model branch (instead of trying to make Sparse/Croston's behave) would be more honest.

3. **Off-price retailers should be treated as a separate fulfillment mode, not a customer.** Off-price = closeout = zero forward projection by default. R1 currently catches some, but the residual is still $500K+.

4. **The F59 family (Amazon DC inventory) has 8 sub-rules touching 564 records, with a net negative effect ($415K under-projection on Croston's).** Worth running `tune_thresholds.py` on the F59i WOS gate to see if there's a better operating point.

5. **F65 (zero-velocity suppression) fires on 1,096 records but is the #3 most-disagreeing rule.** If both L4 and L13 are zero, F65 zeros AI. But planner sometimes sets manual > 0 anyway (FD residuals, new-launch with sparse history). F65 should respect status_cust = "A: NEW" / "FD".

---

## Suggested implementation order

| Order | Fix | Est. gap recovered | Risk |
|---|---|---|---|
| 1 | Priority 3 (off-price hard-zero) | $500K | Low |
| 2 | Priority 4 (F52 FD anchor to planner) | $300K | Low |
| 3 | Priority 1 + 8 (new-launch ramp detection + Croston pre-launch trim) | $1.0M | Medium |
| 4 | Priority 5 (F61 NEW guard) | $700K | Low |
| 5 | Priority 6 (F37 NEW skip) | $300K | Low |
| 6 | Priority 2 + 7 (F18 burst carve-out + Croston event-aware z) | $700K | Medium |
| 7 | Priority 9 (Target bias correction) | $100K | Trivial |
| 8 | Priority 10 (tune F64 lifts) | TBD | Investigation |

**Conservative total recoverable gap: ~$3.6M of the $5.94M (60%).**

After implementation:
1. Re-run `python scripts/run_forecast.py --all --validate`
2. Re-run `python scripts/variance_deep_dive.py`
3. Compare new |unit gap| to baseline
4. Use `scripts/backtest_ci.py` to enforce the improvement holds across runs

---

## What this exercise validates about the infrastructure built today

The variance deep-dive was only possible because of the Phase 2 work:
- `rule_attribution.py` ranked all 87 rules by impact
- `variance_deep_dive.py` segmented by customer / shape / status / model
- `_schema_version` confirms the results JSON is fresh
- Unit tests gave confidence the calendar fix and fire() refactor didn't break the run

**Without this scaffolding, the same analysis would have taken days of ad-hoc grep + spreadsheet work.** With it, the cycle becomes: run → analyze → propose → test → iterate.
