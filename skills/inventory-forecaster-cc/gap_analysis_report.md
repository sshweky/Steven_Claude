# Manual vs AI Projection Gap Analysis

- **Source**: `validation_results.json`  
- **Generated**: 2026-04-21 10:42:07  
- **Scope**: top **100** records by manual projection volume  

## Overall

| Metric | Value |
|---|---|
| Total manual projection | 3,673,935 |
| Total AI projection | 2,859,620 (-22.2%) |
| Total expected (baseline × profile) | 2,531,504 (-31.1%) |
| Absolute unit gap | 814,315 |

### Gap distribution

| Bucket | Records |
|---|---|
| AI << M (<-30%) | 43 |
| AI < M (-30..-10) | 32 |
| close (±10%) | 10 |
| AI > M (+10..+30) | 7 |
| AI >> M (>+30) | 8 |

## Root-cause buckets

### Other under-forecast

**28 records · absolute unit gap: 517,148**

**Proposed fix:** Manual inspection needed — review description and history pattern.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF22031 | 91,750 | 37,224 | -59.4% | Wags & Wiggles Cleanse Hypoallergenic Wipes 1 | Gap -59.4%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF17554 | 40,000 | 0 | -100.0% | Amazon Private Label: Rawhide-free Twists, Ba | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF15589 | 66,770 | 34,008 | -49.1% | Palmer's for Pets Paw Pad & Nose Balm with Co | Gap -49.1%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF17574 | 32,750 | 0 | -100.0% | Amazon Private Label: Extruded Dental Stick,  | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF7120EC | 82,505 | 52,398 | -36.5% | BioSilk for Dogs Detangling and Shine Spray f | Gap -36.5%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF17578 | 29,500 | 0 | -100.0% | Amazon Private Label: Extruded Dental Stick,  | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-BB22116 | 56,485 | 32,238 | -42.9% | Clorox Fraganzia - In-Wash Scent Booster Crys | Gap -42.9%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF12689 | 114,905 | 91,248 | -20.6% | A&H Pet Scents Deodorizing Gel Beads - Fresh  | Gap -20.6%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF10530 | 33,515 | 12,024 | -64.1% | A&H Heavy Duty Multi-Purpose Pet Wipes - Mang | Gap -64.1%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF7112 | 19,935 | 0 | -100.0% | BioSilk for Dogs Whitening Shampoo, 12 Ounces | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF12656 | 19,090 | 0 | -100.0% | Arm & Hammer Ultra Fresh No-Rinse Deodorizing | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF5766AMZ | 46,535 | 28,902 | -37.9% | Burt's Bees Hypoallergenic Shampoo for Cats 1 | Gap -37.9%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF16704 | 37,425 | 20,580 | -45.0% | Arm and Hammer Cat Litter Box Crystals- Laven | Gap -45.0%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF5769AMZ | 25,505 | 9,264 | -63.7% | Burt's Bees Tearless Kitten Shampoo 10oz | Gap -63.7%. Baseline=L13W avg. Pattern=new_item. |
| 1864-BB14812 | 28,680 | 13,700 | -52.2% | G4K Paw Patrol - 8.5" Round Paper Plates, 20c | Gap -52.2%. Baseline=L13W avg. Pattern=steady. |

### Seasonal-ramp under-forecast (peak >> trough)

**15 records · absolute unit gap: 208,783**

**Proposed fix:** Add peak-anchored baseline: when category profile matches AND L52 peak > 3x L13 non-zero avg, compute `peak_baseline = avg(L52 weeks falling in category peak months)` and anchor the seasonal curve to that. Then each week = peak_baseline * (category_multiplier[w] / max(category_multiplier)).

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB0466 | 103,080 | 59,724 | -42.1% | Kingsford - Deluxe Charcoal Chimney Starter | Peak 27,180 is 17.6x L13 avg 1546. Forecast anchored to trough, not peak. |
| 1864-BB12068 | 31,680 | 9,264 | -70.8% | Kingsford - Wooden Fire Starter Rolls - 32ct | Peak 2,688 is 3.8x L13 avg 708. Forecast anchored to trough, not peak. |
| 1864-FF12508 | 51,925 | 33,648 | -35.2% | A&H Pet Scents Deodorizing Gel Beads - Lavend | Peak 8,208 is 3.7x L13 avg 2224. Forecast anchored to trough, not peak. |
| 1864-FF8654 | 79,500 | 63,438 | -20.2% | Glad for Pets Activated Carbon Training Pads  | Peak 3,168 is 3.3x L13 avg 961. Forecast anchored to trough, not peak. |
| 1864-FF31068 | 33,785 | 18,144 | -46.3% | Palmer's for Pets Facial Cleansing Pads with  | Peak 3,168 is 4.2x L13 avg 749. Forecast anchored to trough, not peak. |
| 1864-FF7221 | 22,790 | 7,656 | -66.4% | Chi Deodorizing Spray For Dogs | Peak 3,648 is 3.8x L13 avg 970. Forecast anchored to trough, not peak. |
| 1864-BB21250 | 52,350 | 38,736 | -26.0% | Kingsford - Grill Brush | Peak 5,424 is 8.1x L13 avg 670. Forecast anchored to trough, not peak. |
| 1864-SF8168 | 21,445 | 9,168 | -57.2% | Arm & Hammer Fresh Breath Dental Kit for Dogs | Peak 7,248 is 5.3x L13 avg 1379. Forecast anchored to trough, not peak. |
| 1864-FF5773 | 20,950 | 9,426 | -55.0% | Burts Bees Kitten wipes -50ct | Peak 1,068 is 4.7x L13 avg 227. Forecast anchored to trough, not peak. |
| 1864-FF8653 | 29,355 | 20,960 | -28.6% | Glad for Pets Activated Carbon Training Pads  | Peak 3,712 is 3.3x L13 avg 1114. Forecast anchored to trough, not peak. |
| 1864-BB14658 | 20,915 | 13,240 | -36.7% | Glad for Kids Paw Patrol  - 9oz Paper Cup - 2 | Peak 1,700 is 3.2x L13 avg 527. Forecast anchored to trough, not peak. |
| 1864-FF13876 | 24,325 | 17,610 | -27.6% | Arm & Hammer Med. Diapers-16.5"-21" Waist - 1 | Peak 1,440 is 3.5x L13 avg 416. Forecast anchored to trough, not peak. |
| 1864-BB35096 | 23,310 | 17,058 | -26.8% | Gladware - Deep Dish 64oz - Large Rectangle - | Peak 1,752 is 6.0x L13 avg 294. Forecast anchored to trough, not peak. |
| 1864-FF9060 | 18,390 | 12,144 | -34.0% | Chi Gentle 2 In 1 Shampoo And Conditioner | Peak 4,332 is 5.3x L13 avg 812. Forecast anchored to trough, not peak. |
| 1864-FF7228 | 18,135 | 12,936 | -28.7% | Chi Detangling Finishing Spray For Dogs 10oz | Peak 1,392 is 3.2x L13 avg 440. Forecast anchored to trough, not peak. |

### Declining item over-forecast

**7 records · absolute unit gap: 130,495**

**Proposed fix:** Detect end-of-life / declining items: if L4W avg < L13W non-zero avg × 0.7, blend the 26-week forecast toward L4W avg using weight = 0.5 on L4W and 0.5 on the model forecast. Further down-weight weeks W14-W26.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB13437 | 59,375 | 118,620 | +99.8% | G4K - 8.5" Round Paper Plates, 20ct - Unicorn | L4W avg 746 is 45% of L13 avg 1660. Item declining but AI held baseline. |
| 1864-BB30188 | 16,030 | 38,196 | +138.3% | Fabuloso Small Scrub Brush w/ Handle (White P | L4W avg 213 is 10% of L13 avg 2044. Item declining but AI held baseline. |
| 1864-BB28473 | 24,155 | 45,792 | +89.6% | Clorox Fraganzia - Gel Air Freshener Cone - 6 | L4W avg 165 is 38% of L13 avg 431. Item declining but AI held baseline. |
| 1864-FF17831 | 38,055 | 47,592 | +25.1% | CLEANZE Antibacterial Hand Wipes-Indiv. Wrapp | L4W avg 540 is 68% of L13 avg 792. Item declining but AI held baseline. |
| 1864-FF12655EC | 46,050 | 53,652 | +16.5% | Arm & Hammer Ultra Fresh Waterless Bath Spray | L4W avg 1028 is 50% of L13 avg 2039. Item declining but AI held baseline. |
| 1864-FF12806 | 18,035 | 24,384 | +35.2% | BioSilk Eco Friendly Detangling Pin Brush | L4W avg 708 is 68% of L13 avg 1035. Item declining but AI held baseline. |
| 1864-FF5952PCS2 | 15,685 | 19,644 | +25.2% | Arm & Hammer Clinical Care Dental Rinse for A | L4W avg 114 is 8% of L13 avg 1371. Item declining but AI held baseline. |

### Isolated spike over-forecast (outlier cap)

**5 records · absolute unit gap: 114,465**

**Proposed fix:** Tighten Fix 3 outlier cap: lower the threshold from 3.0x median to 2.5x median, OR add a secondary check — if max(L13_nz) > 2x L13_all_avg AND max occurs only once, cap at 2x L13_all_avg.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF5952EC | 24,455 | 88,368 | +261.3% | Arm & Hammer Clinical Care Dental Rinse for A | L13 non-zero max 9,240 is 8.0x median 1152. Outlier cap didn't fully neutralize. |
| 1864-FF20206EC | 27,590 | 53,628 | +94.4% | Arm & Hammer Itch Relief Spray | L13 non-zero max 5,586 is 4.4x median 1272. Outlier cap didn't fully neutralize. |
| 1864-BB27206 | 21,210 | 33,512 | +58.0% | G4K Disney - 8.5" Round Paper Plates, 40ct -  | L13 non-zero max 1,504 is 7.5x median 200. Outlier cap didn't fully neutralize. |
| 1864-BB31552 | 25,700 | 33,306 | +29.6% | Glad Kids - 12oz Paper Snack Bowls no lid - 2 | L13 non-zero max 4,872 is 4.1x median 1176. Outlier cap didn't fully neutralize. |
| 1864-FF5715 | 16,130 | 20,736 | +28.6% | Arm & Hammer Fresh Breath Enzymatic Toothpast | L13 non-zero max 1,368 is 3.8x median 360. Outlier cap didn't fully neutralize. |

### Sparse/intermittent baseline too conservative

**6 records · absolute unit gap: 94,152**

**Proposed fix:** For sparse_intermittent / intermittent with high manual projection, set baseline = MAX(L13W non-zero avg, L26W non-zero avg, L52W non-zero avg) instead of the current L13W-first-fallback chain.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF7297AMZ | 38,025 | 4,524 | -88.1% | Burt's Bees Waterless Shampoo for Cats 10oz | Pattern 'sparse_intermittent' but manual projects 38,025. L13nz_avg 0 vs L26nz_avg 1146. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF8991 | 25,140 | 8,208 | -67.4% | Fresh Step Litter Box Scent Crystals in Summe | Pattern 'sparse_intermittent' but manual projects 25,140. L13nz_avg 1260 vs L26nz_avg 1370. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF6989 | 23,835 | 10,416 | -56.3% | Arm & Hammer Tartar Control Dental Kit for Do | Pattern 'intermittent' but manual projects 23,835. L13nz_avg 2234 vs L26nz_avg 2029. Consider MAX(L13, L26, L52) baseline. |
| 1864-BB0150 | 23,075 | 10,344 | -55.2% | Clorox Fraganzia - Air Freshener Beads - 12oz | Pattern 'intermittent' but manual projects 23,075. L13nz_avg 2112 vs L26nz_avg 784. Consider MAX(L13, L26, L52) baseline. |
| 1864-SF8173EC | 25,715 | 15,912 | -38.1% | Arm & Hammer Fresh Breath Dental Spray, Mint | Pattern 'sparse_intermittent' but manual projects 25,715. L13nz_avg 2565 vs L26nz_avg 3250. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF4776AMZ | 21,170 | 13,404 | -36.7% | Burt's Bees Oatmeal Dog Conditioner for Dogs  | Pattern 'intermittent' but manual projects 21,170. L13nz_avg 1742 vs L26nz_avg 1558. Consider MAX(L13, L26, L52) baseline. |

### Seasonal category not in CATEGORY_PROFILES

**6 records · absolute unit gap: 90,827**

**Proposed fix:** Add missing category profiles to CATEGORY_PROFILES in inventory_forecaster.py. Also match on Brand (e.g., 'Kingsford' → outdoor_grill) and Product_Category / Product_Subcategory fields, not description alone. Queue the listed keywords.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB30424 | 40,825 | 11,832 | -71.0% | Clorox Fraganzia - 6pk Deodorizing Balls - La | L52 peak 5,388 / L13 avg 1331 = 4.0x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB0131EC | 44,200 | 23,592 | -46.6% | Kingsford - Grill Cleaner - Aerosol - 14.5oz | L52 peak 5,628 / L13 avg 188 = 29.9x. Description keyword 'kingsford' → 'outdoor_grill' not in CATEGORY_PROFILES. |
| 1864-BB21641 | 16,800 | 3,912 | -76.7% | Clorox Fraganzia - Air Freshener Beads - 12oz | L52 peak 1,788 / L13 avg 414 = 4.3x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB26922 | 28,340 | 15,522 | -45.2% | Clorox Fraganzia - Air Freshener Beads Twin P | L52 peak 6,162 / L13 avg 548 = 11.2x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB11483 | 17,450 | 8,736 | -49.9% | Kingsford - 10" Heavy Duty Round Paper Plates | L52 peak 834 / L13 avg 12 = 69.5x. Description keyword 'kingsford' → 'outdoor_grill' not in CATEGORY_PROFILES. |
| 1864-BB27359 | 28,370 | 21,564 | -24.0% | Clorox Fraganzia - 6pk Deodorizing Balls - Fr | L52 peak 8,040 / L13 avg 2067 = 3.9x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |

### Amazon Prime Day pre-buy gap (W5-W9 under-forecast)

**3 records · absolute unit gap: 69,463**

**Proposed fix:** Extend PRIME_DAY_WEEKS lift schedule to a tapered ramp: W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). Currently W7-W9 only at flat 1.25.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF10479EC | 91,810 | 53,742 | -41.5% | Arm & Hammer Super Deodorizing Spray - Kiwi B | Manual W5-W9 avg 4344 vs overall 3531 (+23%). AI W5-W9 = 12,024. |
| 1864-FF17546 | 19,250 | 0 | -100.0% | Amazon Private Label: Rawhide-free Twists, Pe | Manual W5-W9 avg 1150 vs overall 740 (+55%). AI W5-W9 = 0. |
| 1864-BB0237 | 71,425 | 59,280 | -17.0% | Glad - Assorted Cutlery, 240ct - Clear | Manual W5-W9 avg 3462 vs overall 2747 (+26%). AI W5-W9 = 12,744. |

### Other over-forecast

**2 records · absolute unit gap: 24,061**

**Proposed fix:** Manual inspection needed — review description and history pattern.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF15982 | 41,500 | 53,772 | +29.6% | CLEANZE Antibacterial Hand Wipes- 6" x 8" -10 | Gap +29.6%. Baseline=no L13W orders. Pattern=intermittent. |
| 1864-FF8298 | 23,815 | 35,604 | +49.5% | Fresh Step Litter Box Attractant Powder to Ai | Gap +49.5%. Baseline=no L13W orders. Pattern=sparse_intermittent. |

### Aligned with manual (within ±10%)

**28 records · absolute unit gap: 122,701**

**Proposed fix:** No fix needed.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF8990 | 135,985 | 122,424 | -10.0% | Fresh Step Litter Box Scent Crystals in Fresh | Gap -10.0% — within tolerance. |
| 1864-FF10159EC | 85,145 | 75,648 | -11.2% | Arm & Hammer Super Deodorizing Shampoo 20oz | Gap -11.2% — within tolerance. |
| 1864-FF7258AMZ | 75,070 | 65,886 | -12.2% | Burt's Bees Oatmeal Shampoo for Dogs 16oz | Gap -12.2% — within tolerance. |
| 1864-FF12853 | 53,025 | 61,188 | +15.4% | Wet Ones Multipurpose Wipe for Cats - 50 ct C | Gap +15.4% — within tolerance. |
| 1864-FF12887 | 61,435 | 53,508 | -12.9% | A&H Pet Scents Solid Gel Deodorizer - Fresh B | Gap -12.9% — within tolerance. |
| 1864-FF8423 | 48,750 | 40,860 | -16.2% | Fresh Step Drawstring Litter Box Liners Scent | Gap -16.2% — within tolerance. |
| 1864-BB0098 | 44,455 | 36,600 | -17.7% | Glad - 10" Round Paper Plates, 50ct - Blue Fl | Gap -17.7% — within tolerance. |
| 1864-FF7372 | 34,175 | 27,918 | -18.3% | Burts Bees Cat Dander Wipes-50ct | Gap -18.3% — within tolerance. |
| 1864-FF4775AMZ | 91,480 | 85,338 | -6.7% | Burts Bees Tearless 2 in 1 Shampoo and Condit | Gap -6.7% — within tolerance. |
| 1864-BB20227 | 23,235 | 18,648 | -19.7% | Glad Kids - Disposable Paper Bibs - Unicorns  | Gap -19.7% — within tolerance. |
| 1864-BB20228 | 21,565 | 17,856 | -17.2% | Glad Kids - Disposable Paper Bibs - Sharks -  | Gap -17.2% — within tolerance. |
| 1864-FF12823 | 51,035 | 47,352 | -7.2% | Wags & Wiggles Nourish Moisturizing Wipes 100 | Gap -7.2% — within tolerance. |
| 1864-BB0578 | 28,605 | 25,044 | -12.4% | Glad - Plastic Spoons, 24ct - Clear | Gap -12.4% — within tolerance. |
| 1864-FF31287 | 26,830 | 23,600 | -12.0% | Glad for Pets Heavy Duty Activated Carbon Tra | Gap -12.0% — within tolerance. |
| 1864-BB0705EC | 21,400 | 18,222 | -14.9% | Clorox Scentiva ÃÂ Refresher Spray ÃÂ 16. | Gap -14.9% — within tolerance. |

## Unmatched seasonal keywords (add to CATEGORY_PROFILES)

| Keyword | Records | Suggested category |
|---|---:|---|
| `fraganzia` | 4 | cleaning |
| `kingsford` | 2 | outdoor_grill |
| `deodorizing ball` | 2 | spring_cleaning |
| `air freshener` | 2 | spring_cleaning |
| `grill cleaner` | 1 | outdoor_grill |
| `paper plate` | 1 | party_summer |

## Priority-ordered model fixes

1. **Seasonal-ramp under-forecast (peak >> trough)** (15 records, 208,783 unit gap)
   - Add peak-anchored baseline: when category profile matches AND L52 peak > 3x L13 non-zero avg, compute `peak_baseline = avg(L52 weeks falling in category peak months)` and anchor the seasonal curve to that. Then each week = peak_baseline * (category_multiplier[w] / max(category_multiplier)).

2. **Seasonal category not in CATEGORY_PROFILES** (6 records, 90,827 unit gap)
   - Add missing category profiles to CATEGORY_PROFILES in inventory_forecaster.py. Also match on Brand (e.g., 'Kingsford' → outdoor_grill) and Product_Category / Product_Subcategory fields, not description alone. Queue the listed keywords.

3. **Amazon Prime Day pre-buy gap (W5-W9 under-forecast)** (3 records, 69,463 unit gap)
   - Extend PRIME_DAY_WEEKS lift schedule to a tapered ramp: W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). Currently W7-W9 only at flat 1.25.

4. **Sparse/intermittent baseline too conservative** (6 records, 94,152 unit gap)
   - For sparse_intermittent / intermittent with high manual projection, set baseline = MAX(L13W non-zero avg, L26W non-zero avg, L52W non-zero avg) instead of the current L13W-first-fallback chain.

5. **Declining item over-forecast** (7 records, 130,495 unit gap)
   - Detect end-of-life / declining items: if L4W avg < L13W non-zero avg × 0.7, blend the 26-week forecast toward L4W avg using weight = 0.5 on L4W and 0.5 on the model forecast. Further down-weight weeks W14-W26.

6. **Isolated spike over-forecast (outlier cap)** (5 records, 114,465 unit gap)
   - Tighten Fix 3 outlier cap: lower the threshold from 3.0x median to 2.5x median, OR add a secondary check — if max(L13_nz) > 2x L13_all_avg AND max occurs only once, cap at 2x L13_all_avg.
