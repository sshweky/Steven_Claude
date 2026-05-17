# Manual vs AI Projection Analysis
**Generated:** 2026-05-17  
**Source table:** Projections (bpd237tvm), InventoryTrack app  
**Filter:** Status @ Cust contains 'A' (active records)  
**Sample:** 2,000 records (first 2,000 active by QB default sort)

---

## 1. Sample Composition

| Segment | Count | % of Total |
|---|---|---|
| Total active records fetched | 2,000 | 100% |
| Both MAN and AI projections > 0 | 1,553 | 77.7% |
| MAN only (AI = 0) | 265 | 13.3% |
| AI only (MAN = 0, planner killed forecast) | 49 | 2.5% |
| Both zero | 133 | 6.7% |

**Meaningful comparison base (both > 0): 1,553 records.**  
265 records have manual projections with no AI counterpart - these are cases the AI engine hasn't generated suggestions for (likely new items or recently activated records).

The 49 "AI only" records (MAN = 0, AI > 0) represent cases where planners deliberately zeroed out a forecast the AI proposed. A much larger kill pattern exists within the 298-record "killed" group (>=20 MAN zero weeks while AI > 0), which is discussed in Section 7.

---

## 2. Overall Bias: Are Planners Systematically Above or Below AI?

### 2a. Direction Distribution (both MAN and AI > 0, n=1,553)

| Direction | Count | % |
|---|---|---|
| UP (MAN > AI by >5%) | 915 | 58.9% |
| DOWN (MAN < AI by >5%) | 544 | 35.0% |
| FLAT (within 5%) | 94 | 6.1% |

**Planners go UP on 3 out of every 5 records where both projections exist.** This is a strong systematic upward bias at the record level.

### 2b. Aggregate Volume Bias

Despite the directional bias toward UP, the **aggregate volume bias is actually slightly NEGATIVE (-6.7%)**: total MAN units = 10,098,549 vs total AI units = 10,824,265.

This apparent contradiction reveals a key dynamic:
- **Planners go UP frequently on small-volume items** (adding relatively small absolute quantities)
- **Planners go DOWN on large-volume items** (subtracting large absolute quantities)
- The net effect is: many small upward bumps, fewer but larger downward cuts

This is confirmed by the volume tier analysis (Section 5). **High-volume items have a -13.2% aggregate bias (planners below AI), while low-volume items have a +9.0% aggregate bias (planners above AI).**

### 2c. Avg vs Median Delta

- Average delta: +180.6% (severely skewed by extreme outliers on low-volume items)
- Median delta: +20.4%

The median is the better signal: planners typically raise projections by about 20% over what the AI suggests for items where they go UP.

---

## 3. Patterns by Customer

### Top customers with systematic UPWARD bias (MAN > AI):

| Customer | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| ARMY-AIR-FORCE EXCH SR | 5 | 5 | 0 | +122.6% |
| IMPERIAL DISTRIBUTORS | 13 | 12 | 1 | +106.3% |
| THEIS DISTRIBUTING COMPANY INC | 27 | 25 | 2 | +81.2% |
| PSP DISTRIBUTION, LLC | 28 | 23 | 3 | +79.8% |
| BLAIN SUPPLY INC | 14 | 13 | 1 | +72.5% |
| C & S WHOLESALE | 23 | 15 | 8 | +45.9% |
| MEIJER THRIFTY ACRES | 24 | 19 | 5 | +45.8% |
| CVS CORPORATION | 13 | 9 | 3 | +43.4% |
| PET SUPERMARKET, INC | 12 | 10 | 1 | +41.6% |
| SUPERVALU | 17 | 15 | 2 | +34.3% |

### Top customers with systematic DOWNWARD bias (MAN < AI):

| Customer | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| H G BUYING INC | 2 | 0 | 2 | -79.3% |
| PETCO MEXICO | 2 | 0 | 2 | -67.7% |
| DD'S DISCOUNTS | 46 | 4 | 40 | -59.2% |
| PET PHARM LTD | 15 | 0 | 15 | -58.8% |
| MORALE W & R | 6 | 3 | 3 | -59.7% |
| COMERCIALIZADORA MEXICO AMERIC | 17 | 0 | 15 | -39.5% |
| HONG CHI PETCARE CO., LTD | 9 | 1 | 8 | -35.6% |

**Key observations:**
- **DD'S DISCOUNTS** (n=46) is the highest-volume customer with a strong systematic DOWN bias (-59.2%, 40 of 46 records are DOWN). This is also the top customer in the "kill" pattern (48 killed records). The AI is systematically over-projecting for DD's Discounts.
- **PSP DISTRIBUTION** and **THEIS DISTRIBUTING** are consistently UP (+80%, +81%). The AI appears to underweight order rates for distributors.
- **COMERCIALIZADORA MEXICO AMERIC** and **PETCO MEXICO** are both Mexican accounts with DOWN bias - the AI may not account for different ordering cadence in international markets.
- **PET PHARM LTD** (15 records, 100% DOWN) - consistent over-projection by AI for this customer.

### Algorithm Improvement Hypothesis (Customer):
- Fit a **per-customer bias correction factor** from historical planner adjustments. Several customers show 80%+ consistent direction, meaning the AI can learn a customer-level multiplier.
- For **off-price / closeout channels** (DD's, Ross, Burlington, Gabriel Brothers - all top kill-list customers), the AI should apply a heavy discount or require minimum order history before projecting.
- International accounts (Mexico, international distributors) should be treated as a separate segment with lower baseline projections.

---

## 4. Patterns by Brand

### Brands with highest upward overrides:

| Brand | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| Cleanze | 3 | 3 | 0 | +366.7% |
| Arm & Hammer / Treadz | 4 | 4 | 0 | +218.3% |
| Biosilk [OTHER] | 1 | 1 | 0 | +189.7% |
| Play On [PSP] | 18 | 17 | 0 | +111.5% |
| Full Cheeks [PSM] | 2 | 2 | 0 | +94.0% |
| Pine Sol | 11 | 8 | 3 | +52.4% |
| Clorox | 29 | 20 | 9 | +25.7% |
| Arm & Hammer Waste Mgmt | 62 | 40 | 18 | +27.7% |

### Brands with highest downward overrides:

| Brand | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| Kingsford | 31 | 11 | 19 | -45.8% |
| Wags & Wiggles | 19 | 11 | 7 | -36.0% |
| Arm & Hammer Specialty | 101 | 44 | 51 | -28.1% |
| Arm & Hammer Core Dental | 49 | 23 | 24 | -29.1% |
| Wet Ones | 52 | 27 | 21 | -30.7% |
| Fabuloso | 27 | 13 | 14 | -35.4% |

**Key observations:**
- **Play On [PSP]**: 17 of 18 records UP, +111.5% aggregate bias. The AI is significantly underestimating this brand. Likely a newer or growing brand where the AI lacks sufficient history.
- **Kingsford**: 19 of 31 records DOWN (-45.8%). Kingsford is a seasonally driven product (grilling season). The AI appears to over-project in off-season windows or not correctly identify the seasonal pattern.
- **Arm & Hammer Specialty vs Arm & Hammer Waste Mgmt**: These two sub-brands within the same master brand go in opposite directions. Specialty is DOWN (-28%), Waste Mgmt is UP (+27%). This suggests the AI uses a brand-level signal that doesn't differentiate between product sub-types within a brand.
- **Fabuloso**: Mixed (-35%), with planners cutting AI projections. May reflect channel distribution limits or category competition not in the model.

### Algorithm Improvement Hypothesis (Brand):
- **Brand growth trajectory**: New/fast-growing brands (Play On, Full Cheeks) likely have insufficient history to produce accurate AI projections. Apply a growth multiplier based on recent L4W vs L26W trend ratio.
- **Seasonal brands** (Kingsford): The AI needs a seasonality index at the brand or category level. Kingsford projections should ramp in spring, decline in fall. The consistently negative planner adjustments on this brand suggest the AI is not correctly timing the seasonal decline.
- **Sub-brand differentiation**: Brand-level features need to be split into sub-brand features where the business logic differs (e.g., Arm & Hammer pet waste vs Arm & Hammer dental).

---

## 5. Patterns by Item Status

| Item Status | N | UP% | DN% | Agg Bias% |
|---|---|---|---|---|
| Active: Replen | 1,324 | 57.3% | 36.3% | -6.7% |
| Active: Multi-Pk Replen | 225 | 68.4% | 27.1% | -19.6% |

**Multi-Pack Replen items** show the most pronounced discrepancy: 68% UP direction but -19.6% aggregate bias. This is the clearest sign of the "many small UP on low-volume, few large DOWN on high-volume" pattern. Multi-pack items appear to have particularly unreliable AI projections.

**Active: Multi-Pk Replen avg delta = +743%** - the mean is this extreme because small AI projections get large percentage multipliers. The AI may not properly account for the unit conversion factor inherent in multipacks (a planner knows a 6-pack sells 6x the units per SKU-level order).

### Algorithm Improvement Hypothesis (Item Status):
- **Multi-pack items**: Apply a conversion factor from units-per-pack to the demand signal. If a multi-pack has 6 units and the single-pack L13W rate is 600/wk, the multi-pack projection should be based on 100/wk, not on sparse multi-pack order history alone.
- **New items** (Active: New Item - not present in this sample, which means they may have Status_Cust = 'A: New' or similar - worth a separate query): The AI should use a launch ramp curve rather than history-based projection.

---

## 6. Magnitude vs Baseline: Do Planners Override High or Low Volume Items More?

| Volume Tier | L13W avg/wk | N | UP% | DN% | Avg Delta% | Agg Bias% |
|---|---|---|---|---|---|---|
| HIGH | >=500 units/wk | 190 | 43.2% | 44.7% | +11.5% | -13.2% |
| MED | 100-499 units/wk | 387 | 49.6% | 43.9% | +30.1% | +2.6% |
| LOW | 1-99 units/wk | 915 | 65.8% | 29.6% | +249.1% | +9.0% |
| ZERO | 0 units/wk | 61 | 63.9% | 29.5% | +634.6% | +75.7% |

**This is one of the most important findings:**

1. **High-volume items**: Planners are **below AI** (-13.2% aggregate). The AI appears to over-project on high-volume items, possibly because it anchors to L13W and high-volume items may be slowing down.

2. **Low-volume items**: Planners are **above AI** (+9% aggregate, +249% avg delta%). The AI under-projects on low-velocity items. Many of these are niche items where any seasonal order or sporadic customer demand creates a high percentage swing that the AI doesn't capture.

3. **ZERO baseline items** (L13W avg = 0, meaning no orders in 13 weeks): Planners project +634% more than AI. These are either reactivating items, items the AI doesn't know how to project, or items where planners are entering speculative projections. The AI placing non-trivial projections on zero-velocity items and getting heavily overridden by planners is a reliability concern.

### Algorithm Improvement Hypothesis (Volume):
- **Trend-weighted baseline**: Rather than anchoring to L13W alone, use a recency-weighted blend (L4W × 0.5 + L13W × 0.3 + L26W × 0.2). When L4W trend is declining, reduce the multiplier; when accelerating, increase it.
- **Velocity floor**: For ZERO-velocity items, the AI should require a minimum order signal before generating a non-trivial projection, or output a zero with a flag.
- **High-volume dampening**: For items above 500 units/wk, the AI appears to be over-optimistic. Apply a regression-to-mean dampener for extreme high-velocity items, especially when L4W is below L13W.

---

## 7. Shape Analysis: Week-by-Week Pattern of MAN vs AI

### 7a. Overall Week Profile (avg across 1,553 both-have records)

| Wk | MAN avg | AI avg | Ratio |
|---|---|---|---|
| W1 | 223.3 | 185.6 | **1.203** |
| W2 | 223.6 | 224.3 | 0.997 |
| W3 | 229.7 | 311.9 | **0.737** |
| W4 | 313.4 | 258.3 | **1.213** |
| W5 | 251.2 | 251.7 | 0.998 |
| W6 | 234.7 | 255.1 | 0.920 |
| W7 | 224.8 | 301.6 | **0.745** |
| W8 | 300.1 | 268.0 | **1.120** |
| W9 | 250.4 | 308.0 | 0.813 |
| W10 | 230.3 | 276.3 | 0.834 |
| W11 | 228.9 | 317.1 | **0.722** |
| W12 | 352.9 | 276.1 | **1.278** |
| W13 | 242.3 | 281.2 | 0.862 |
| W14 | 236.2 | 270.8 | 0.872 |
| W15 | 221.4 | 276.0 | 0.802 |
| W16 | 224.0 | 288.6 | 0.776 |
| W17 | 339.8 | 273.8 | **1.241** |
| W18 | 240.2 | 260.1 | 0.923 |
| W19 | 222.4 | 359.9 | **0.618** |
| W20 | 267.3 | 231.2 | **1.156** |
| W21 | 337.1 | 257.1 | **1.311** |
| W22 | 225.2 | 260.4 | 0.865 |
| W23 | 223.5 | 293.3 | 0.762 |
| W24 | 229.2 | 231.3 | 0.991 |
| W25 | 215.2 | 240.5 | 0.895 |
| W26 | 215.2 | 211.8 | 1.016 |

### 7b. Shape Pattern Analysis

**The week profile reveals a striking "every-4-weeks spike" pattern in MAN projections.** Weeks with ratios above 1.10 cluster at W1, W4, W8, W12, W17, W20, W21. This is approximately a 4-week interval.

**This is almost certainly a MOQ/order cadence artifact.** Planners are entering projections on a monthly or 4-week ordering cycle. When a customer orders every 4 weeks, the planner enters a large projection for the "order week" and smaller projections (or zeros) for the off-weeks. The AI projects a smooth weekly rate, while planners encode a lumpy order-cycle pattern.

The spike at **W19** is highly anomalous on the AI side (ratio 0.618 - AI projects far MORE than planners in W19). This may indicate the AI is over-projecting into a period planners know will be quiet (possibly around a holiday/back-to-school transition), or an artifact of missing order history in that period.

**W3, W7, W11 consistently show AI > MAN** (ratios 0.737, 0.745, 0.722). These are the "trough weeks" that fall between the 4-week order spikes in MAN projections.

### 7c. Front-Load Score by Direction

- UP records: avg front-load score = **0.952** (slightly back-loaded)
- DOWN records: avg front-load score = **1.115** (front-loaded)
- FLAT records: avg front-load score = **0.911** (back-loaded)

This is counter-intuitive. **DOWN records are front-loaded** - planners are cutting the AI forecast most heavily in the back half of the window (W7-W26), while keeping the near-term weeks (W1-W6) closer to or above AI. This suggests planners have near-term demand visibility (open orders, customer commitments) that justifies maintaining the short-horizon projection, but are skeptical about the AI's long-range optimism.

**UP records are slightly back-loaded** - when planners raise the AI forecast, they tend to apply the increase more in the near-term than far out, but the pattern is mild.

### Algorithm Improvement Hypotheses (Shape):
1. **Order-cadence smoothing**: Planners are encoding lumpy order cycles; the AI should model and forecast in terms of the customer's typical order frequency. Identify whether each Acct-MStyle combination has a 2-week, 4-week, or monthly order cadence from order history, and cluster the projection into those buckets rather than flat-weekly.
2. **Horizon confidence decay**: The AI projects with equal confidence across W1-W26. Planners consistently cut the back half more aggressively. The AI should apply a confidence/damping curve that reduces projection weight beyond W8-W10, especially for items without strong seasonal signals.
3. **W19 anomaly**: Investigate the AI's W19 over-projection systematically. This may be a specific seasonal artifact in the training data that needs recalibration.

---

## 8. Zero/Kill Patterns: Where Planners Completely Eliminate AI Forecasts

**298 records have >= 20 MAN zero weeks while AI projects > 0** - these are cases where planners effectively killed the AI forecast.

### Top customers in kill list:
| Customer | Killed Records |
|---|---|
| DD'S DISCOUNTS | 48 |
| AMAZON.COM.KYDC,INC | 44 |
| BURLINGTON COAT FACTORY | 34 |
| VARIETY WHOLESALERS INC | 32 |
| ROSS STORES INC - MERCHANDISE | 23 |
| COMERCIALIZADORA MEXICO AMERIC | 17 |
| GABRIEL BROTHERS INC | 16 |
| MENARD INC | 13 |

### Top brands in kill list:
| Brand | Killed Records |
|---|---|
| Clorox Fraganzia | 70 |
| Arm & Hammer Core Grooming | 43 |
| Glad | 21 |
| Clorox | 17 |
| Arm & Hammer Specialty | 15 |
| Burt's Bees | 13 |
| Fabuloso | 12 |
| Biosilk [LIQUIDS] | 11 |
| Arm & Hammer Ultra Fresh | 10 |

**Key observations:**
- **Off-price channels dominate**: DD's Discounts, Burlington Coat Factory, Ross Stores, Gabriel Brothers, Variety Wholesalers are all off-price/closeout channels. These customers order sporadically when a deal is available, not on a replenishment cycle. The AI cannot predict these opportunities - planners know this and zero out AI projections.
- **Amazon (44 killed records)**: The AI is likely generating projections for items that planners know are not currently buyable on Amazon (out-of-stock, suspended ASINs, catalog issues), or for items where the planner manages Amazon separately through a different mechanism.
- **Clorox Fraganzia (70 killed records)**: This is the single largest brand in the kill list. Either this brand is being discontinued/wound down in certain channels, or it is distributed through specific promotional mechanisms that don't support a standing replenishment projection.
- **Arm & Hammer Core Grooming (43 killed records)**: Another large kill cluster. May indicate a product line with limited distribution or specific customer program requirements the AI doesn't know about.

### Algorithm Improvement Hypothesis (Kill Pattern):
- **Channel type flag**: Off-price / closeout channels (DD's, Ross, Burlington, etc.) should be automatically excluded from AI replenishment projection or given a near-zero baseline. The system can learn this from the historical kill rate - if planners zero out >70% of AI projections for a customer, suppress AI generation for that customer.
- **Amazon inventory health flag**: Before generating AI projections for Amazon Acct-MStyles, check the ASIN's current buyability status. Non-buyable ASINs should get zero AI projections.
- **Kill rate threshold**: Add a field/metric for "planner kill rate" per Acct-MStyle pair over the trailing 13 weeks. Items with high kill rates should be flagged and potentially given zero AI baseline.

---

## 9. Spike Patterns: Where Planners Place Single-Week Peaks

641 records (41.3% of both_have) have at least one week where MAN exceeds 2x the AI weekly average by a margin greater than the AI average.

### Spike week distribution (most common):
| Wk | Count | Wk | Count |
|---|---|---|---|
| W17 | 460 | W18 | 350 |
| W21 | 436 | W4 | 336 |
| W12 | 414 | W14 | 334 |
| W8 | 396 | W24 | 330 |
| W9 | 326 | W20 | 324 |

**The spike distribution clusters around W4, W8, W12, W17, W21** - again the 4-week interval signature, confirming the order-cycle artifact identified in Section 7.

**W17 is the single most common spike week** (460 records). W17 in the current window is approximately early September (week of 09-06-2026). This corresponds to **fall replenishment season** - planners are loading a large order spike into the first major fall buy window. The AI does not model this calendar-driven procurement event.

**W21 (436 spikes, week of 10-04-2026)** is another key calendar event - likely the second wave of fall/holiday season ordering.

**The W8-W9 cluster** (396+326 spikes, week of ~07-12 to 07-19-2026) corresponds to **summer reorder season**.

### Algorithm Improvement Hypothesis (Spikes):
- **Calendar event encoding**: Planners are encoding known trade calendar events (spring buy, summer reorder, fall buy, holiday buy). The AI should incorporate a "trade calendar" feature that identifies historically high-order-volume weeks for each customer/brand combination. If a customer historically places large orders in W4, W8, W12 of each period, the AI should anticipate these.
- **Seasonal spike vs noise**: Differentiate between spikes that repeat across years (seasonal) vs spikes that appear in only one year (promotional or one-time). Only model the repeating ones.

---

## 10. L13W Anchoring: Is the AI Missing the Baseline?

| Direction | N | avg MAN / L13W-basis | avg AI / L13W-basis |
|---|---|---|---|
| UP | 876 | **5.216x** | 1.349x |
| DOWN | 526 | 0.839x | **1.639x** |
| FLAT | 90 | 1.133x | 1.133x |

**This is one of the most revealing findings.**

- When planners go **UP**: MAN projects **5.2x the L13W basis**, while AI projects only 1.35x. This is not planners anchoring to L13W - they are projecting far beyond it. This happens primarily for **low-volume and zero-velocity items** (confirmed by the volume tier analysis). Planners are adding speculative demand for items the AI conservatively anchors to near-zero history.

- When planners go **DOWN**: MAN projects **0.84x the L13W basis**, while AI projects **1.64x**. **The AI is over-projecting at 1.64x the recent run rate for items planners are cutting.** This is the clearest signal that the AI is systematically optimistic relative to recent order history on items heading into a period of softening demand.

- **FLAT records**: Both MAN and AI project at 1.133x L13W - these are the "well-calibrated" records where the AI and planner agree. This is the target state.

### Algorithm Improvement Hypothesis (Anchoring):
- **AI over-projection on declining items**: When L4W avg is significantly below L13W avg (e.g., L4W/L13W ratio < 0.7), the AI should anchor closer to L4W rather than L13W. The AI appears to be using a longer lookback that doesn't adequately weight recent demand decline.
- **AI under-projection on low-volume items with upcoming demand**: Planners are raising projections 5x the L13W basis on low-volume items. This is likely associated with known upcoming distribution events (new door counts, promotional placement) that are not in the AI model. Consider incorporating distribution change signals (door count changes, new POG placements) if available.
- **Calibration target**: The ideal AI/L13W ratio for stable replenishment items should be in the 1.0-1.3x range. Items where AI/L13W > 1.5 and planners consistently go DOWN are strong candidates for algorithmic dampening.

---

## 11. By Inventory Manager

| Manager | N | UP% | DN% | Agg Bias% |
|---|---|---|---|---|
| John Grossi | 541 | 57.5% | 38.1% | **-23.4%** |
| Amy Rodriguez | 301 | 63.8% | 27.9% | +12.5% |
| Tae Kang | 357 | 51.8% | 41.2% | +12.2% |
| Shina Yang | 244 | 64.8% | 32.8% | +7.9% |
| Jonathan Pichardo | 106 | 64.2% | 24.5% | +8.5% |
| Mikey Scott | 4 | 25.0% | 25.0% | -5.7% |

**John Grossi** manages 541 records (the largest portfolio) and has a **-23.4% aggregate downward bias** despite 57.5% UP direction. This means Grossi is making large absolute cuts on high-volume items while adding small adjustments upward on low-volume ones. Grossi's portfolio likely includes the largest accounts (the aggregate downward skew at company level matches this profile).

**Amy Rodriguez, Shina Yang, and Jonathan Pichardo** all show consistent +7-12% upward aggregate bias with high UP direction rates (64-65%). These managers are systematically raising AI projections.

**Tae Kang** (357 records) is close to flat at +12.2%, with a more balanced UP/DOWN split (51.8%/41.2%) - this manager's portfolio may be where AI performance is best-calibrated.

### Algorithm Improvement Hypothesis (Manager):
- Per-manager bias patterns could be used to audit AI calibration by portfolio segment rather than correcting for manager "style." If one manager consistently overrides in the same direction, it may indicate their accounts have a systematic characteristic the AI is missing (e.g., Grossi's accounts being large/declining, Rodriguez's accounts being growth-stage).
- **Do not** simply add a manager bias correction - this would paper over the underlying misalignment. Instead, use manager override patterns as a diagnostic to identify which account/brand/item segments the AI is poorly calibrated on.

---

## 12. Summary of Algorithm Improvement Hypotheses

In priority order (highest impact first):

### Priority 1: Order-Cadence Modeling
**Finding:** The week-by-week profile shows a clear 4-week spike pattern in MAN projections (W1, W4, W8, W12, W17, W21) vs a smooth AI weekly rate. 641 of 1,553 records (41%) have spike weeks.  
**Hypothesis:** The AI projects flat weekly demand while planners project in order-cycle chunks. For each Acct-MStyle combination, identify the customer's typical order cadence (e.g., every 4 weeks) from order history and project demand in cycle-aligned buckets rather than flat weekly rates.  
**Effort:** Medium. Requires per-customer cadence detection from order history (already available as ORD_W1-W26 fields).

### Priority 2: Horizon Confidence Decay
**Finding:** Planners systematically cut the AI's back-half forecast (W7-W26). DOWN records have a front-load score of 1.115 - near-term is kept near AI but the back half is cut. FLAT records are slightly back-loaded too.  
**Hypothesis:** Apply exponential confidence decay to AI projections beyond W8. For items without strong seasonal signals, weeks W9-W26 should be projected at 85-90% of the W1-W8 rate, not extrapolated flat.  
**Effort:** Low. Parameterized decay curve applied post-model.

### Priority 3: Channel Type Classification
**Finding:** Off-price/closeout customers (DD's, Ross, Burlington, Gabriel Brothers) account for a disproportionate share of AI kills. Planners zero out 100% of AI projections for these accounts.  
**Hypothesis:** Add a channel-type flag to the Customer table. Closeout/opportunistic channels get suppressed AI projections. Replenishment channels get full AI projections.  
**Effort:** Low. Rule-based classification, no ML required.

### Priority 4: L4W/L13W Trend Signal
**Finding:** AI over-projects at 1.64x L13W on items planners are cutting. When L4W trend is below L13W, the AI should dampen toward L4W.  
**Hypothesis:** Compute `trend_ratio = L4W_avg / L13W_avg`. When `trend_ratio < 0.8`, use `projected_base = L4W_avg * 0.6 + L13W_avg * 0.4`. When `trend_ratio > 1.2`, use `projected_base = L4W_avg * 0.7 + L13W_avg * 0.3`.  
**Effort:** Low. Arithmetic adjustment to existing baseline calculation.

### Priority 5: Multi-Pack Unit Conversion
**Finding:** Multi-Pk Replen items show +743% avg delta - the highest of any item status category. AI systematically under-projects multi-pack items.  
**Hypothesis:** For multi-pack SKUs, derive demand signal from the parent single-pack item's L13W rate divided by the units-per-pack. The AI may be projecting from sparse multi-pack order history rather than scaled single-unit demand.  
**Effort:** Medium. Requires linking multi-pack SKUs to their parent SKUs and fetching units-per-pack from style data.

### Priority 6: Seasonal Calendar Events
**Finding:** Spike weeks W17 (early Sept) and W21 (early Oct) are the most common planner spike weeks. These align with known seasonal buying calendar events.  
**Hypothesis:** Encode a trade calendar feature: for each customer, identify historically high-volume weeks from 2+ years of order history. Apply a seasonal index multiplier for those weeks.  
**Effort:** High. Requires multi-year order history aggregation, but highest fidelity signal.

### Priority 7: Zero-Velocity Item Suppression
**Finding:** Items with L13W avg = 0 (no orders in 13 weeks) have AI/L13W = N/A but AI still projects non-trivial amounts. Planners override by +634% - they often use these records for speculative new-demand projections, not replenishment.  
**Hypothesis:** For items with 0 units ordered in both L4W and L13W, AI projection should be 0 unless a specific trigger exists (new item flag, promotional commitment, known distribution expansion).  
**Effort:** Low. Suppression rule based on existing L13W field.

### Priority 8: Per-Customer Bias Correction
**Finding:** Several customers have 80-100% consistent direction (up or down) in planner overrides.  
**Hypothesis:** After generating the base AI projection, apply a customer-level calibration multiplier derived from the trailing 26-week history of planner override ratios. Start with customers where direction rate > 75%.  
**Effort:** Medium. Automated calibration update from planner history.

---

## Appendix: Data Definitions

**MAN W1-W26**: Current manual projection fields (date-stamped, e.g., "05 17 W1" = week starting May 17). FIDs 22-97.  
**AI PRJ W1-W26**: AI-generated projection fields. FIDs 1511-1536.  
**ORD W1-W26**: Weekly order history (Wk1M = most recent week = LW, Wk26M = LW-25). FIDs 457, 464-489.  
**L13W avg**: Ord/Wk L13w # (FID 1593) - numeric average units ordered per week over trailing 13 weeks.  
**L26W avg**: Ord/Wk L26w (FID 1591) - numeric average over trailing 26 weeks.  
**delta_pct**: (MAN_total - AI_total) / AI_total * 100.  
**front_load_score**: avg(MAN/AI ratio for W1-W6) / avg(MAN/AI ratio for W7-W26). Values > 1 indicate MAN is higher relative to AI in the early weeks.  
**killed**: man_zeros >= 20 AND ai_total > 0.  
**spike_week**: Any week where MAN > 2x AI_avg_per_week AND (MAN - AI) > AI_avg_per_week.
