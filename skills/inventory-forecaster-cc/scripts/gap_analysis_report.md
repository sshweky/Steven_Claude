# Manual vs AI Projection Gap Analysis

- **Source**: `validation_results.json`  
- **Generated**: 2026-05-20 16:18:50  
- **Scope**: top **150** records by manual projection volume  

## Overall

| Metric | Value |
|---|---|
| Total manual projection | 4,383,540 |
| Total AI projection | 3,895,663 (-11.1%) |
| Total expected (baseline × profile) | 3,476,445 (-20.7%) |
| Absolute unit gap | 487,877 |

### Gap distribution

| Bucket | Records |
|---|---|
| AI << M (<-30%) | 54 |
| AI < M (-30..-10) | 27 |
| close (±10%) | 37 |
| AI > M (+10..+30) | 20 |
| AI >> M (>+30) | 12 |

## Root-cause buckets

### Other under-forecast

**27 records · absolute unit gap: 428,764**

**Proposed fix:** Manual inspection needed — review description and history pattern.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF8654 | 94,500 | 5,534 | -94.1% | Glad for Pets Activated Carbon Training Pads  | Gap -94.1%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF7297AMZ | 31,325 | 0 | -100.0% | Burt's Bees Waterless Shampoo for Cats 10oz | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF15584 | 34,490 | 5,976 | -82.7% | Palmer's for Pets Direct Relief Lotion Spray  | Gap -82.7%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF30755EC | 22,875 | 0 | -100.0% | Wet One's - Antibacterial Paw Cleansing Foam  | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF15589 | 46,710 | 25,260 | -45.9% | Palmer's for Pets Paw Pad & Nose Balm with Co | Gap -45.9%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF10479PX2 | 20,720 | 0 | -100.0% | Arm & Hammer Super Deodorizing Spray - Kiwi B | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF8991 | 20,300 | 0 | -100.0% | Fresh Step Litter Box Scent Crystals in Summe | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF7372 | 30,080 | 10,758 | -64.2% | Burts Bees Cat Dander Wipes-50ct | Gap -64.2%. Baseline=L13W avg. Pattern=steady. |
| 1864-SF8168 | 17,765 | 48 | -99.7% | Arm & Hammer Fresh Breath Dental Kit for Dogs | Gap -99.7%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF12376 | 26,770 | 9,684 | -63.8% | Biosilk for Dogs Silk Therapy Detangling Cond | Gap -63.8%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF31068 | 17,420 | 1,872 | -89.3% | Palmer's for Pets Facial Cleansing Pads with  | Gap -89.3%. Baseline=L13W avg. Pattern=steady. |
| 1864-BB30188 | 16,875 | 2,016 | -88.1% | Fabuloso Small Scrub Brush w/ Handle (White P | Gap -88.1%. Baseline=L13W avg. Pattern=steady. |
| 1864-BB21951 | 12,445 | 780 | -93.7% | Clorox Fraganzia - In-Wash Scent Booster Crys | Gap -93.7%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF12806 | 17,390 | 6,432 | -63.0% | BioSilk Eco Friendly Detangling Pin Brush | Gap -63.0%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF31326EC | 10,700 | 0 | -100.0% | Arm & Hammer Odor Control 5-in-1 Shampoo | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |

### Declining item over-forecast

**22 records · absolute unit gap: 375,329**

**Proposed fix:** Detect end-of-life / declining items: if L4W avg < L13W non-zero avg × 0.7, blend the 26-week forecast toward L4W avg using weight = 0.5 on L4W and 0.5 on the model forecast. Further down-weight weeks W14-W26.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF9298EC | 82,130 | 148,392 | +80.7% | Arm & Hammer Complete Care Dog Dental Rinse,  | L4W avg 0 is 0% of L13 avg 6492. Item declining but AI held baseline. |
| 1864-FF9297/24 | 69,515 | 114,072 | +64.1% | A&H Complete Care Dog Dental Kit - Includes:  | L4W avg 0 is 0% of L13 avg 5760. Item declining but AI held baseline. |
| 1864-FF30755 | 17,280 | 51,108 | +195.8% | Wet One's - Antibacterial Paw Cleansing Foam  | L4W avg 970 is 35% of L13 avg 2771. Item declining but AI held baseline. |
| 1864-FF8649/24 | 61,600 | 88,224 | +43.2% | Glad for Pets Heavy-Duty Scented Waste Bags R | L4W avg 0 is 0% of L13 avg 7168. Item declining but AI held baseline. |
| 1864-FF12655EC | 50,419 | 73,950 | +46.7% | Arm & Hammer Ultra Fresh Waterless Bath Spray | L4W avg 519 is 24% of L13 avg 2198. Item declining but AI held baseline. |
| 1864-BB30930 | 81,460 | 103,872 | +27.5% | Clorox Fraganzia - 6pk Garbage Can Stick Ups  | L4W avg 741 is 60% of L13 avg 1243. Item declining but AI held baseline. |
| 1864-FF7618 | 20,145 | 41,100 | +104.0% | Arm & Hammer Adv Care Fresh Breath Dental Min | L4W avg 0 is 0% of L13 avg 5430. Item declining but AI held baseline. |
| 1864-FF8425 | 10,370 | 27,792 | +168.0% | Fresh Step Drawstring Litter Box Liners Scent | L4W avg 324 is 25% of L13 avg 1296. Item declining but AI held baseline. |
| 1864-FF10159EC | 77,124 | 92,040 | +19.3% | Arm & Hammer Super Deodorizing Shampoo 20oz | L4W avg 0 is 0% of L13 avg 2936. Item declining but AI held baseline. |
| 1864-BB33708 | 46,350 | 59,928 | +29.3% | G4K Disney - 3oz Bath Cups, 100ct -Princess | L4W avg 195 is 20% of L13 avg 978. Item declining but AI held baseline. |
| 1864-BB22272 | 29,965 | 43,272 | +44.4% | Fabuloso Microfiber Cleaning Cloths Rainbow 8 | L4W avg 0 is 0% of L13 avg 800. Item declining but AI held baseline. |
| 1864-BB13435 | 30,050 | 42,642 | +41.9% | G4K - 8.5" Round Paper Plates, 20ct - Dinosau | L4W avg 0 is 0% of L13 avg 582. Item declining but AI held baseline. |
| 1864-FF10479EC | 47,892 | 59,832 | +24.9% | Arm & Hammer Super Deodorizing Spray - Kiwi B | L4W avg 1575 is 46% of L13 avg 3447. Item declining but AI held baseline. |
| 1864-FF5766AMZ | 42,840 | 54,696 | +27.7% | Burt's Bees Hypoallergenic Shampoo for Cats 1 | L4W avg 1622 is 64% of L13 avg 2553. Item declining but AI held baseline. |
| 1864-BB21250 | 45,050 | 55,536 | +23.3% | Kingsford - Grill Brush | L4W avg 402 is 60% of L13 avg 674. Item declining but AI held baseline. |

### Sparse/intermittent baseline too conservative

**9 records · absolute unit gap: 126,842**

**Proposed fix:** For sparse_intermittent / intermittent with high manual projection, set baseline = MAX(L13W non-zero avg, L26W non-zero avg, L52W non-zero avg) instead of the current L13W-first-fallback chain.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF15982 | 31,450 | 144 | -99.5% | CLEANZE Antibacterial Hand Wipes- 6" x 8" -10 | Pattern 'sparse_intermittent' but manual projects 31,450. L13nz_avg 1887 vs L26nz_avg 1887. Consider MAX(L13, L26, L52) baseline. |
| 1864-SF8170 | 26,715 | 3,744 | -86.0% | Arm & Hammer Tartar Control Dental Kit for Do | Pattern 'intermittent' but manual projects 26,715. L13nz_avg 3653 vs L26nz_avg 3072. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF12301/24 | 29,755 | 8,736 | -70.6% | A&H Complete Care Puppy Boxed Toothpaste in P | Pattern 'intermittent' but manual projects 29,755. L13nz_avg 3941 vs L26nz_avg 2282. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF17831 | 29,880 | 11,928 | -60.1% | CLEANZE Antibacterial Hand Wipes-Indiv. Wrapp | Pattern 'sparse_intermittent' but manual projects 29,880. L13nz_avg 864 vs L26nz_avg 864. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF7112 | 17,090 | 9,660 | -43.5% | BioSilk for Dogs Whitening Shampoo, 12 Ounces | Pattern 'intermittent' but manual projects 17,090. L13nz_avg 1798 vs L26nz_avg 1168. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF4776AMZ | 17,285 | 9,942 | -42.5% | Burt's Bees Oatmeal Dog Conditioner for Dogs  | Pattern 'intermittent' but manual projects 17,285. L13nz_avg 1801 vs L26nz_avg 1081. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF28459 | 17,280 | 10,260 | -40.6% | Dole: Dog Chews, Assorted (Strawberry + Pinea | Pattern 'sparse_intermittent' but manual projects 17,280. L13nz_avg 0 vs L26nz_avg 780. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF12846 | 10,900 | 4,656 | -57.3% | Wet Ones Anti-Bacterial Paw/Tushie Wipe for D | Pattern 'intermittent' but manual projects 10,900. L13nz_avg 2213 vs L26nz_avg 1390. Consider MAX(L13, L26, L52) baseline. |
| 1864-BB35095 | 11,905 | 6,348 | -46.7% | Gladware Everyday - Entree 25oz, Medium Squar | Pattern 'intermittent' but manual projects 11,905. L13nz_avg 0 vs L26nz_avg 470. Consider MAX(L13, L26, L52) baseline. |

### Seasonal-ramp under-forecast (peak >> trough)

**12 records · absolute unit gap: 122,612**

**Proposed fix:** Add peak-anchored baseline: when category profile matches AND L52 peak > 3x L13 non-zero avg, compute `peak_baseline = avg(L52 weeks falling in category peak months)` and anchor the seasonal curve to that. Then each week = peak_baseline * (category_multiplier[w] / max(category_multiplier)).

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF12689 | 97,350 | 61,008 | -37.3% | A&H Pet Scents Deodorizing Gel Beads - Fresh  | Peak 21,888 is 3.6x L13 avg 6158. Forecast anchored to trough, not peak. |
| 1864-FF7258AMZ | 68,750 | 54,156 | -21.2% | Burt's Bees Oatmeal Shampoo for Dogs 16oz | Peak 16,860 is 3.2x L13 avg 5348. Forecast anchored to trough, not peak. |
| 1864-FF5952EC | 12,950 | 1,560 | -88.0% | Arm & Hammer Clinical Care Dental Rinse for A | Peak 1,586 is 4.7x L13 avg 334. Forecast anchored to trough, not peak. |
| 1864-FF13876 | 24,030 | 14,688 | -38.9% | Arm & Hammer Med. Diapers-16.5"-21" Waist - 1 | Peak 1,440 is 3.4x L13 avg 421. Forecast anchored to trough, not peak. |
| 1864-FF13877 | 19,780 | 10,560 | -46.6% | Arm & Hammer Lrg. Diapers-18"-23" Waist - 12  | Peak 1,896 is 3.1x L13 avg 615. Forecast anchored to trough, not peak. |
| 1864-BB15776 | 12,415 | 3,924 | -68.4% | Glad - Plastic Forks, 24ct - Clear | Peak 7,512 is 3.3x L13 avg 2262. Forecast anchored to trough, not peak. |
| 1864-BB20228 | 26,184 | 19,008 | -27.4% | G4K - Disposable Paper Bibs, 30ct - Sharks | Peak 1,656 is 3.5x L13 avg 476. Forecast anchored to trough, not peak. |
| 1864-FF5773 | 20,369 | 13,500 | -33.7% | Burts Bees Kitten wipes -50ct | Peak 1,356 is 6.0x L13 avg 225. Forecast anchored to trough, not peak. |
| 1864-FF6305 | 14,290 | 7,536 | -47.3% | Arm & Hammer: Nubbies TriBone Chew Toy for Do | Peak 4,320 is 4.2x L13 avg 1035. Forecast anchored to trough, not peak. |
| 1864-FF9298PCS3 | 11,005 | 6,176 | -43.9% | Arm & Hammer Complete Care Dental Rinse, Odor | Peak 1,920 is 4.9x L13 avg 396. Forecast anchored to trough, not peak. |
| 1864-FF31062 | 10,135 | 6,108 | -39.7% | Arm & Hammer Ultra Fresh Itch Relief Shampoo  | Peak 2,562 is 11.2x L13 avg 228. Forecast anchored to trough, not peak. |
| 1864-BB38155 | 16,100 | 12,522 | -22.2% | Kingsford - Compact Charcoal Chimney Starter | Peak 1,230 is 8.4x L13 avg 147. Forecast anchored to trough, not peak. |

### Seasonal category not in CATEGORY_PROFILES

**9 records · absolute unit gap: 112,365**

**Proposed fix:** Add missing category profiles to CATEGORY_PROFILES in inventory_forecaster.py. Also match on Brand (e.g., 'Kingsford' → outdoor_grill) and Product_Category / Product_Subcategory fields, not description alone. Queue the listed keywords.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB26922 | 28,290 | 1,974 | -93.0% | Clorox Fraganzia - Air Freshener Beads Twin P | L52 peak 6,162 / L13 avg 678 = 9.1x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB27359 | 31,225 | 9,204 | -70.5% | Clorox Fraganzia - 6pk Deodorizing Balls - Fr | L52 peak 8,040 / L13 avg 1828 = 4.4x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB21058/6 | 27,202 | 12,534 | -53.9% | Fabuloso - Microfiber Mitt Purple 1 CT (6PC F | L52 peak 1,392 / L13 avg 251 = 5.5x. Description keyword 'fabuloso' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB28473 | 22,175 | 8,220 | -62.9% | Clorox Fraganzia - Gel Air Freshener Cone - 6 | L52 peak 1,764 / L13 avg 560 = 3.1x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB11483 | 14,920 | 5,808 | -61.1% | Kingsford - 10" Heavy Duty Round Paper Plates | L52 peak 2,628 / L13 avg 74 = 35.5x. Description keyword 'kingsford' → 'outdoor_grill' not in CATEGORY_PROFILES. |
| 1864-BB27773 | 25,880 | 17,900 | -30.8% | G4K Disney - 10" Round Paper Plates, 30ct - F | L52 peak 2,060 / L13 avg 272 = 7.6x. Description keyword 'paper plate' → 'party_summer' not in CATEGORY_PROFILES. |
| 1864-BB30424 | 17,380 | 10,152 | -41.6% | Clorox Fraganzia - 6pk Deodorizing Balls - La | L52 peak 5,388 / L13 avg 1247 = 4.3x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB28360 | 21,635 | 15,312 | -29.2% | G4K Disney Pixar - 8.5" Round Paper Plates, 4 | L52 peak 6,456 / L13 avg 876 = 7.4x. Description keyword 'paper plate' → 'party_summer' not in CATEGORY_PROFILES. |
| 1864-BB30189 | 16,990 | 12,228 | -28.0% | Fabuloso Hand-Held Scrub Brush (White Perf Ca | L52 peak 2,988 / L13 avg 212 = 14.1x. Description keyword 'fabuloso' → 'cleaning' not in CATEGORY_PROFILES. |

### Other over-forecast

**4 records · absolute unit gap: 54,291**

**Proposed fix:** Manual inspection needed — review description and history pattern.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB0466 | 80,000 | 102,066 | +27.6% | Kingsford - Deluxe Charcoal Chimney Starter | Gap +27.6%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF22031 | 54,825 | 70,452 | +28.5% | Wags & Wiggles Cleanse Hypoallergenic Wipes 1 | Gap +28.5%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF25895 | 41,105 | 50,928 | +23.9% | A&H Litter Box Deodorizing Pod 2pk - 8pc Prin | Gap +23.9%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF19546 | 17,465 | 24,240 | +38.8% | Glad for Pets OPP Scented Waste Bags Refill R | Gap +38.8%. Baseline=L13W avg. Pattern=steady. |

### Amazon Prime Day pre-buy gap (W5-W9 under-forecast)

**8 records · absolute unit gap: 51,817**

**Proposed fix:** Extend PRIME_DAY_WEEKS lift schedule to a tapered ramp: W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). Currently W7-W9 only at flat 1.25.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF35147EC | 18,282 | 0 | -100.0% | Arm & Hammer Complete Care Dog Dental Rinse w | Manual W5-W9 avg 943 vs overall 703 (+34%). AI W5-W9 = 0. |
| 1864-BB31552 | 35,995 | 23,736 | -34.1% | G4K - 12oz Paper Snack Bowls w/o lid, 20ct -  | Manual W5-W9 avg 1759 vs overall 1384 (+27%). AI W5-W9 = 6,288. |
| 1864-BB35032 | 10,935 | 4,866 | -55.5% | Gladware Freezerware - 24oz, Small Rectangle, | Manual W5-W9 avg 509 vs overall 421 (+21%). AI W5-W9 = 1,140. |
| 1864-FF31289 | 10,485 | 6,120 | -41.6% | Glad for Pets Activated Carbon Training Pads  | Manual W5-W9 avg 582 vs overall 403 (+44%). AI W5-W9 = 1,236. |
| 1864-BB35098 | 10,865 | 7,308 | -32.7% | Gladware Everyday - Big Bowl 48oz, Large Roun | Manual W5-W9 avg 506 vs overall 418 (+21%). AI W5-W9 = 1,506. |
| 1864-FF7221 | 15,145 | 12,168 | -19.7% | Chi Deodorizing Spray For Dogs | Manual W5-W9 avg 749 vs overall 582 (+29%). AI W5-W9 = 2,724. |
| 1864-FF13885 | 15,865 | 13,374 | -15.7% | Glad for Pets Day to Night 30"x36" Activated  | Manual W5-W9 avg 824 vs overall 610 (+35%). AI W5-W9 = 2,904. |
| 1864-BB13436 | 12,005 | 10,188 | -15.1% | G4K- 8.5" Round Paper Plates - 20ct - Sharks | Manual W5-W9 avg 969 vs overall 462 (+110%). AI W5-W9 = 2,052. |

### Aligned with manual (within ±10%)

**59 records · absolute unit gap: 184,467**

**Proposed fix:** No fix needed.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-FF8990 | 144,935 | 163,320 | +12.7% | Fresh Step Litter Box Scent Crystals in Fresh | Gap +12.7% — within tolerance. |
| 1864-FF12508 | 63,025 | 51,648 | -18.1% | A&H Pet Scents Deodorizing Gel Beads - Lavend | Gap -18.1% — within tolerance. |
| 1864-BB20552 | 86,232 | 74,904 | -13.1% | Glad - Plastic Forks, 70ct - Clear | Gap -13.1% — within tolerance. |
| 1864-FF12302/24EC | 51,792 | 42,672 | -17.6% | A&H Complete Care Dog Dental Spray in Mint Fl | Gap -17.6% — within tolerance. |
| 1864-FF12887 | 57,765 | 49,056 | -15.1% | A&H Pet Scents Solid Gel Deodorizer - Fresh B | Gap -15.1% — within tolerance. |
| 1864-FF4775AMZ | 82,150 | 74,070 | -9.8% | Burts Bees Tearless 2 in 1 Shampoo and Condit | Gap -9.8% — within tolerance. |
| 1864-FF12842 | 45,796 | 37,716 | -17.6% | Wet Ones Antibacterial All-Purpose Wipe for D | Gap -17.6% — within tolerance. |
| 1864-BB0098 | 37,560 | 30,228 | -19.5% | Glad - 10" Round Paper Plates, 50ct - Blue Fl | Gap -19.5% — within tolerance. |
| 1864-FF20208 | 66,624 | 60,324 | -9.5% | Arm & Hammer Gentle Puppy Wipes - Coconut Wat | Gap -9.5% — within tolerance. |
| 1864-FF16704 | 31,285 | 25,092 | -19.8% | Arm and Hammer Cat Litter Box Crystals- Laven | Gap -19.8% — within tolerance. |
| 1864-FF12853 | 58,010 | 51,852 | -10.6% | Wet Ones Multipurpose Wipe for Cats - 50 ct C | Gap -10.6% — within tolerance. |
| 1864-BB13437 | 57,054 | 51,060 | -10.5% | G4K - 8.5" Round Paper Plates, 20ct - Unicorn | Gap -10.5% — within tolerance. |
| 1864-BB35096 | 27,768 | 22,356 | -19.5% | Gladware Everyday - Deep Dish 64oz, Large Rec | Gap -19.5% — within tolerance. |
| 1864-FF12823 | 59,305 | 54,504 | -8.1% | Wags & Wiggles Nourish Moisturizing Wipes 100 | Gap -8.1% — within tolerance. |
| 1864-BB0237 | 88,476 | 84,054 | -5.0% | Glad - Assorted Cutlery, 240ct - Clear | Gap -5.0% — within tolerance. |

## Unmatched seasonal keywords (add to CATEGORY_PROFILES)

| Keyword | Records | Suggested category |
|---|---:|---|
| `fraganzia` | 4 | cleaning |
| `paper plate` | 3 | party_summer |
| `deodorizing ball` | 2 | spring_cleaning |
| `air freshener` | 2 | spring_cleaning |
| `fabuloso` | 2 | cleaning |
| `kingsford` | 1 | outdoor_grill |

## Priority-ordered model fixes

1. **Seasonal-ramp under-forecast (peak >> trough)** (12 records, 122,612 unit gap)
   - Add peak-anchored baseline: when category profile matches AND L52 peak > 3x L13 non-zero avg, compute `peak_baseline = avg(L52 weeks falling in category peak months)` and anchor the seasonal curve to that. Then each week = peak_baseline * (category_multiplier[w] / max(category_multiplier)).

2. **Seasonal category not in CATEGORY_PROFILES** (9 records, 112,365 unit gap)
   - Add missing category profiles to CATEGORY_PROFILES in inventory_forecaster.py. Also match on Brand (e.g., 'Kingsford' → outdoor_grill) and Product_Category / Product_Subcategory fields, not description alone. Queue the listed keywords.

3. **Amazon Prime Day pre-buy gap (W5-W9 under-forecast)** (8 records, 51,817 unit gap)
   - Extend PRIME_DAY_WEEKS lift schedule to a tapered ramp: W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). Currently W7-W9 only at flat 1.25.

4. **Sparse/intermittent baseline too conservative** (9 records, 126,842 unit gap)
   - For sparse_intermittent / intermittent with high manual projection, set baseline = MAX(L13W non-zero avg, L26W non-zero avg, L52W non-zero avg) instead of the current L13W-first-fallback chain.

5. **Declining item over-forecast** (22 records, 375,329 unit gap)
   - Detect end-of-life / declining items: if L4W avg < L13W non-zero avg × 0.7, blend the 26-week forecast toward L4W avg using weight = 0.5 on L4W and 0.5 on the model forecast. Further down-weight weeks W14-W26.
