# Inventory Forecaster - Active Rule Registry

Snapshot 2026-05-21.  Every rule that fires in `forecast_record()` or
`validate_record()`.  See `CHANGELOG.md` for reverted/removed rules.

---

## Routing rules (run BEFORE model selection)

| Rule | Description | Location |
|---|---|---|
| R1   | One-Time-Buy detection (off-price closeout pattern) | `forecast_record` early-return |
| R3   | Inactive conservative L26 floor | `forecast_record` inactive branch |
| R5   | International bulk-buyer relaxation (Petbarn/Loblaws/Mexico) | `forecast_record` |
| S5   | International R3 multiplier (0.5x floor mult) | `forecast_record` |
| S6   | Off-price L52 placeholder (single W1 order) | `forecast_record` inactive branch |
| F5   | PT_Item_Status EOL gate / Launching routing override | `forecast_record` |
| F6a  | Inactive-with-Activity reclassification (L26/L52 lookback) | `classify()` |
| F6c  | Sparse_intermittent -> Heuristic routing | `forecast_record` sparse branch |
| F60  | EC/COS/AMZ-transition history inheritance | main pre-pass |
| F68  | Amazon inactive-channel hard zero (ASIN status + L26 sparse) | `forecast_record` |
| Force-Heuristic | Post-ISO settle period override | `forecast_record` |
| FXX  | Amazon Replen rerouted from Sparse Intermittent to Heuristic | `forecast_record` |
| F-B  | L13 burst-cadence override (forces Croston's despite dense L26) | `forecast_record` |
| F44  | F43-aware dense override (post-disruption density check) | `forecast_record` |

## Pre-launch / NEW-item handling

| Rule | Description |
|---|---|
| F31  | Pre-launch NEW-item manual passthrough (Status_Cust=NEW or PT_Item_Status=Launching) |
| F34  | Pre-launch zeros detection (skips F10 decline + M1 ceiling) |

## Baseline rules (inside `seasonal_baseline()`)

| Rule | Description |
|---|---|
| VP-Q1 | Baseline-mode gating: L13 all-weeks avg default; nz-avg only for OOS or pulsed |
| VP-Q5 | Removed post-event drawdown trigger for nz-avg (lulls are real demand) |
| F3   | Outlier cap: spike > 3x median capped to 3x; F38-pre tightens to 2x on below-MAP |
| F25  | Extreme-outlier DROP (single value > 5x median, with >=4 supporting nz weeks) |
| F4   | Thin-history window widening (L13_nz <= 4, L52_nz >= 8 -> use L52 effective rate) |
| F6b  | L4/L13 decay dampener (L4 nz avg <= 50% of L13 nz avg -> baseline x 0.65) |
| F26  | Mild-zone decay (L4/L13 ratio <= 0.70 -> baseline x 0.85) |
| F27  | Mild-zone ramp (L4/L13 ratio in 1.30-1.60 AND >=2 active L4 weeks -> baseline x 1.10) |
| F50  | Stockout-pattern guard (skip F6b/F26 when L4 has near-zeros but L13 was healthy) |
| F51  | F30-skip POS-confirmed acceleration (preserve F38b lift when POS accelerating) |
| T4   | E-commerce accelerator lift (Chewy/Petco.com/PetSmart.com L4-L13 shift) |
| R8   | Burst-interleaved-with-zeros median anchor (top2 >= 70% of L13 nz -> median x 1.5) |
| L8W-overlay | Recency-weighted blend (50% L8 / 30% L13 / 20% L26 nz averages) |
| F13  | Drawdown-anchored replenishment (Amazon POS-gated) |
| F15  | POS-anchored baseline (any customer with POS data) |
| F38b/c/d/e/f | POS trend lift / Buy-box price recovery / ASIN suppressed offline |
| F22a | Trailing-zero drawdown discount (3+ trailing zeros -> baseline x mult) |
| F22c | Sparse-L13 final-baseline ceiling (caps at L13_all_avg x 1.5) |
| F24  | L13-all ceiling (caps at L13_avg x 2.0) |
| F16  | Category-gated damping relief (DAMP 0.85 vs 0.3) |
| F48  | Post-OOS spike-and-cooldown anchor |
| F10  | Declining-item EOL scale-down (L4 < 70% L13_nz AND YoY drop) |
| F14a | POS-healthy override on F10 |
| F14b | Volume gate on F14a (POS L13 >= 50/wk) |

## Croston's rules (inside `crostens()`)

| Rule | Description |
|---|---|
| M3   | Acceleration-aware z blend (L13 nz avg >= 1.05x L26 -> 90/10 vs 70/30) |
| L8W-overlay | Same as seasonal_baseline (50/30/20 recency) |
| Post-spike drawdown | Re-anchor z when L13 < 65% L26 |
| L26 volume floor | z/p must be >= L26 weekly run rate |
| F28  | Croston's volume floor against L13 (loosened: z lifts to L13_weekly when below x0.90) |
| F18  | Croston's z POS anchor (uplift / blend / stocked-up) |
| Fix 1 | Category seasonality per-week scaler |
| VP-Q3 | Bi-weekly Croston smoothed to weekly (p=2 -> p=1, z halved) |
| F57  | VP-Q3 skip for international R5 / high-CV irregular bulk |
| Fix 5 | Rescale 26w total toward L13W all-weeks avg (cap at 2x reduction) |
| F11  | Tapered Prime Day per-week lift (Amazon only) |
| R7   | Fall Deal Amazon-only |
| F10  | Declining-item EOL scale-down (same as seasonal_baseline) |
| F14a | POS-healthy override on F10 |
| R6   | Croston steady-cadence lift (high-vol items pulling toward L13x26) |
| S3   | High-volume steady Croston (L13x26 >= 50k AND stability >= 0.9) |
| T1   | Off-price Croston ceiling (L26_avg x 26 x 1.0) |

## Heuristic rules (inside `heuristic()`)

| Rule | Description |
|---|---|
| F9   | High-volume sparse MAX baseline (L52 >= 15k -> MAX of L13/L26/L52 nz avg) |
| F23a | Profile dampening DAMP_H=0.3 (clamped to [0.30, 2.50]) |
| F23b | Trailing-zero drawdown discount (3+ trailing zeros -> baseline x mult) |
| Fix 1 | Category seasonality 30% historical / 70% category blend |
| R9/T2 | L52 ceiling at L52_avg x 2.0 (x2.5 if F23b also fired) |
| T2 per-week | Cap each week at max(L4_nz, L13_nz) x 1.5 |
| F10  | Declining-item EOL scale-down (same as above) |
| F14a | POS-healthy override on F10 |

## Sparse Intermittent rules (inside `sparse_intermittent_forecast()`)

| Rule | Description |
|---|---|
| S1   | Off-price Sparse ceiling (L26_avg x 26 x 1.0) |
| Account-cadence | Honor account-level cadence when own history is too thin |

## Post-model rules (inside `forecast_record()`)

| Rule | Description |
|---|---|
| F20  | Heuristic -> Inactive when manual_total=0 (planner zero is strongest signal) |
| F30  | Zero-order-history hard guard (L26 ord=0 -> zero AI regardless of model) |
| F65  | Zero-velocity suppression (L4=0 AND L13=0 -> skip R3/S6/F19 floors) |
| F19  | Conservative inactive floor (manual >= 5000 AND alive signal) |
| F17  | Sparse cadence W1 seed (rotate cadence so first order lands in W1) |
| F17b | F17 volume gate |
| M1   | L52/L26 anchored ceiling (max(L52, L26) x 1.25) |
| M2   | Phase-out / EOL dampening (status token OR stale-order OR stale-ship) |
| F66  | Per-customer bias correction (planner-override-driven calibration) |
| F62  | Soft L4W/L13W trend blend (0.70-0.88 decline / 1.12-1.30 acceleration) |
| F63  | Multi-pack baseline floor (L26 nz avg >> L13 nz avg lift) |
| F64  | Trade fall calendar events (W17-18 +10%, W21-22 +8%) |
| F61  | Horizon confidence decay (W9-W26 x 0.88 for non-Amazon/non-seasonal) |
| F29  | New-item floor (L4-L8 recent activity, with deferral gate for thin history) |
| F71  | Front-week (W1) tail cap at 1.3x max(L4, L13, baseline) |
| F72  | New-launch ramp detection (P1, 2026-05-24): reroute Sparse Intermittent -> Heuristic when L26[-6:] has >=4 nz AND L26[-12:-6] has >=5 zeros |
| F73  | New-launch recency anchor (2026-05-24): for F34 new-launch OR Status_Cust='NEW' items with <=13 active weeks, Fix 5 inside Croston's uses L13W nz-avg reference instead of all-weeks avg so zero-diluted L13 doesn't cap the forecast below the emerging run rate |
| F18b | Croston burst carve-out (P2, 2026-05-24): cap z to pre-burst L13[:9]_nz_avg * 1.2 when L4W >> L13W AND POS doesn't justify burst |
| R1 PATH C | Off-price hard-zero (P3, 2026-05-24): OFFPRICE_CUST_SUBSTRS customers with L4=0 AND manual<=100 -> OTB(zero) |
| P4   | F52 planner-residual anchor (2026-05-24): cap each AI week at planner_rate * 2.5 on stable FD wind-down records |
| P5   | F61 NEW guard (2026-05-24): skip horizon decay when Status_Cust contains "NEW" or item is in active growth |
| P6   | F37 NEW + active-growth skip (2026-05-24): skip OH-shortfall capping on new-launch items |
| P7   | Croston event-aware z (2026-05-24): exclude L13 weeks within +/-14d of past Prime Day / Labor Day from z computation |
| P8   | Croston pre-launch history trim (2026-05-24): trim L52 history at first non-zero index when 8+ leading zeros in L26 |
| F32  | Sparse-intermittent per-week + tiny-signal clamp |
| F36  | Stock-up burn-off suppression (Amazon-only WOS-based front-zeroing) |
| F40  | Order-rate deceleration scaling (L3_nz_avg / L13_nz_avg <= 0.30) |
| F42  | POS-anchored baseline cap (Amazon-only, >3x POS) -- extended to Croston's 2026-05-24; now tracked in rule_fires |
| F74  | Amazon Heuristic initial-stock-up exclusion (2026-05-24): when F9 fired AND baseline >3x POS L13w AND L13nz avg < L26nz avg x0.5, cap baseline = max(L13nz avg, POS x1.5) |
| F75  | POS fallback ceiling when DC data absent (2026-05-24): Heuristic/Croston's, Amazon, no amz_catalog, pos_l13w available, 26w avg >2x POS -> scale to POS x2.0 |
| F76  | Seasonal Baseline thin-history ceiling guard (2026-05-24): when L26 has <=13 active weeks, cap each forecast week at baseline x2.0 to prevent category seasonal profile over-amplification |
| F38f | Suppressed/Not-Buyable hard zero (Amazon, W1-W4=0 + W5 catchup) |
| F67  | Amazon buy-box = $0 near-term dampener (W1-W4 cut 70%) |
| F37  | Forward inventory-shortfall adjustment (Inv_WkN-based capping + backlog) |
| F45  | Per-week cap at 2.0x L26 nz mean (model-artifact spike guard) |
| F46  | Post-F44 forecast rebuild (steady-state distribution after disruption) |
| VP-Q4 | Don't double-count confirmed customer POs (Opn_W zero-out) |
| VP-OP | Off-price PO buffer zone (+/-4 wk window around confirmed POs zeroed) |
| VP-FL | Frontload dampening (W1 spike >= 2.5x L13W -> taper next 2-4 wks) |
| F52  | Future-Delete (FD) wind-down (Status_Cust=FD MM/YY -> truncate + taper) |
| F59o | Amazon seasonal overlay for Heuristic/Croston's (additive uplift floor) |
| F59a-n | Amazon demand-signal corrections (L4W floor, recency, OOS, decel, POS anchor, WOS) |
| F69  | DI direct-import sibling history pull |
| F69-WOS | F69 with POS anchor when WOS high |
| F69-shift | F69 declining channel -> boost domestic |
| F58  | Tell-AI comment replay (planner-driven overrides) |
| F_PO_CUTOFF     | Amazon Fetch/BrandBuzz no-PO-by-cutoff W1 zero (fires Wed-Sat FF / Thu-Sat BB) |
| F_PO_CUTOFF_ALL | Non-Amazon no-orders-by-Wed-9am-EST W1 zero (fires Wed 14:00 UTC onward through Sat) |
| F70  | Switchover variant conflict (EC/COS/AMZ has activity -> zero base) |
| G2   | All-zero-by-guards safety demotion (model -> Inactive when all 26w zeroed) |
| F_AMZ_RPL Fix A | (2026-05-24) _rpl_ord_l13 and variability raw data now use normalized hist[-13:] instead of raw QB row columns -- phantom stock-up orders removed by F41/F35/F43 are no longer counted in the demand baseline |
| F_AMZ_RPL Fix B | (2026-05-24) DC depletion inference: when WOS=0, SOH=0, OPO=0 (ASIN absent from Inventory Health), treat as fully depleted (WOS=0.0) so W1+W2 catch-up order fires at 10-WOS gap fill; previously silently skipped as "DC WOS unknown" |
| F60-ATS | (2026-05-24) EC variants that inherit parent order history via F60 now also inherit parent ATS data in ats_data and parent OOS data in oos_data. Previously VP-ATS-Catch silently skipped all EC variants (ats_data keyed by Mstyle; Inventory History - Weekly has parent record, not EC variant). Result: post-OOS catch-up spikes were uncapped in L13W, inflating _rpl_ord_l13 and causing _rpl_var_ratios to cycle contaminated ratios into steady-state weeks (e.g. July W8 spike). Propagation runs immediately after F60 history copy in main(). |
| F60-ATS safety | (2026-05-24) If EC variant still has all-zero ATS after parent propagation (rare -- parent has no Inventory History record), _rpl_var_ratios is disabled (set None) so contaminated history cannot cycle into forecast weeks. Falls back to flat baseline. |
| Prime Day calendar | Consumer event = last Tuesday of June. ORDERING bumps = May 1/15/29. NO July boosts exist. Any July spike in F_AMZ_RPL output is a data artifact (post-OOS variability cycling or contaminated history), never a Prime Day boost. |

## VP-ATS rules (history normalization)

| Rule | Description |
|---|---|
| VP-Q2 | OOS-aware demand reconstruction (clean_ord from oos_history) |
| VP-ATS | ATS L26W OOS-week imputation in `_prep_record_signals` |
| VP-ATS-Catch | ATS catch-up spike normalization |

## History-normalization rules (inside `_prep_record_signals()`)

| Rule | Description |
|---|---|
| F35  | Stockout-backlog removal (strip pent-up demand from post-gap catch-up weeks) |
| F39  | Duplicate-order run dedup (zero out N-week run of identical values) |
| F41  | Phantom-order detection (unfulfilled within 1 week -> zero) |
| F43  | Recent-spike attenuation (cap last 4w at 2.0x L26-prior nz median) |
| F47  | OOS rebuild-ramp cap (post-OOS rebuild orders capped at pre-OOS baseline) |
| F49  | F43-skip when spikes are sustained or POS-confirmed |
| F55  | LY OOS-gap imputation (>= 3 consecutive zeros surrounded by activity) |
| F57  | (Croston-specific, see above) |

## Validation-only rules (inside `validate_record()`)

| Rule | Description |
|---|---|
| F35 verification | Stockout corrections audited but kept (no re-write) |
| F70 (validation side) | Per-week CRITICAL flag when variant has activity |
| switchover_closed | M4 record-level qb_pattern when >= 13/26 wks F70-flagged |

---

## Reserved / Skip codes

- **F33** - reverted, see CHANGELOG (do not reuse)
- **F12** - reverted, see CHANGELOG (do not reuse)
- **F22b** - superseded by F22c (do not reuse)
- **R4** - removed 2026-05-05 (do not reuse)
- **F2** - removed 2026-05-04 (do not reuse)
- **Holt-Winters function** - removed 2026-05-21 (do not reintroduce without
  wiring it into routing)

## Next available numbers

- **F77** and above
- **R10** and above
- **M4+** (M1, M2, M3 only)
- **T5+** (T1, T2, T3, T4 only)

When adding a new rule, update this registry AND `CHANGELOG.md`.
