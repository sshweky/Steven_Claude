# Manual vs AI Projection Gap Analysis

- **Source**: `validation_results.json`  
- **Generated**: 2026-05-24 17:03:41  
- **Scope**: top **200** records by manual projection volume  

## Overall

| Metric | Value |
|---|---|
| Total manual projection | 9,860,014 |
| Total AI projection | 9,844,617 (-0.2%) |
| Total expected (baseline × profile) | 8,909,454 (-9.6%) |
| Absolute unit gap | 15,397 |

### Gap distribution

| Bucket | Records |
|---|---|
| AI << M (<-30%) | 33 |
| AI < M (-30..-10) | 32 |
| close (±10%) | 81 |
| AI > M (+10..+30) | 20 |
| AI >> M (>+30) | 34 |

## Root-cause buckets

### Declining item over-forecast

**24 records · absolute unit gap: 590,516**

**Proposed fix:** Detect end-of-life / declining items: if L4W avg < L13W non-zero avg × 0.7, blend the 26-week forecast toward L4W avg using weight = 0.5 on L4W and 0.5 on the model forecast. Further down-weight weeks W14-W26.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 23011-BB38905PDQ | 245,529 | 387,696 | +57.9% | G4K- 9oz Plastic Cups, 20ct- Zoomie Dogs | L4W avg 9933 is 63% of L13 avg 15823. Item declining but AI held baseline. |
| 23011-BB38564PDQ | 76,122 | 125,976 | +65.5% | G4K- 9oz Plastic Cups, 20ct  - Dinosaur | L4W avg 3216 is 63% of L13 avg 5138. Item declining but AI held baseline. |
| 23011-BB13436CLR/12 | 97,993 | 135,372 | +38.1% | G4K - 8.5" Round Paper Plates - 20ct - Sharks | L4W avg 5472 is 69% of L13 avg 7979. Item declining but AI held baseline. |
| 12446-BB0234CAN | 66,000 | 99,468 | +50.7% | Glad - 16oz Paper Bowls, 15ct - Green Victori | L4W avg 1260 is 32% of L13 avg 3960. Item declining but AI held baseline. |
| 20006-BB38635 | 33,202 | 64,080 | +93.0% | G4K- 6oz Paper Snack Bowls w/o Lid, 24ct - Di | L4W avg 1568 is 47% of L13 avg 3357. Item declining but AI held baseline. |
| 12835-FF33094 | 31,250 | 60,948 | +95.0% | MOXIE 16.9oz Scntd Hand Gel Pump - 5696380 | L4W avg 1422 is 22% of L13 avg 6494. Item declining but AI held baseline. |
| 12446-BB0125CAN | 28,400 | 57,288 | +101.7% | Glad - 10'  Square Paper Plates,  50ct - Gree | L4W avg 908 is 48% of L13 avg 1878. Item declining but AI held baseline. |
| 1864-FF12689 | 91,300 | 119,256 | +30.6% | A&H Pet Scents Deodorizing Gel Beads - Fresh  | L4W avg 0 is 0% of L13 avg 3660. Item declining but AI held baseline. |
| 20006-BB38489 | 31,674 | 57,588 | +81.8% | G4K - 8.5" Round Paper Plate, 24ct - Dinosaur | L4W avg 1701 is 45% of L13 avg 3812. Item declining but AI held baseline. |
| 20006-BB38634 | 28,833 | 50,112 | +73.8% | G4K- 9oz Plastic Cups, 24ct  - Dinosaur Roar  | L4W avg 1573 is 47% of L13 avg 3365. Item declining but AI held baseline. |
| 20006-BB38671 | 42,484 | 62,256 | +46.5% | G4K - 10oz Paper Bowls, 24ct - Happy Sea Crea | L4W avg 1833 is 56% of L13 avg 3273. Item declining but AI held baseline. |
| 20006-BB38484 | 34,518 | 53,184 | +54.1% | G4K - 10oz Paper Bowls, 24ct - Dinosaur Roar  | L4W avg 1635 is 46% of L13 avg 3529. Item declining but AI held baseline. |
| 1864-FF10479EC | 45,702 | 63,996 | +40.0% | Arm & Hammer Super Deodorizing Spray - Kiwi B | L4W avg 1156 is 36% of L13 avg 3178. Item declining but AI held baseline. |
| 20006-BB38672 | 38,264 | 55,044 | +43.9% | G4K - 8.5" Paper Plate, 24ct - Happy Sea Crea | L4W avg 1862 is 51% of L13 avg 3615. Item declining but AI held baseline. |
| 20006-BB38673 | 32,989 | 46,278 | +40.3% | G4K- 9oz Plastic Cups, 24ct  - Happy Sea Crea | L4W avg 1521 is 50% of L13 avg 3072. Item declining but AI held baseline. |

### Sparse/intermittent baseline too conservative

**10 records · absolute unit gap: 413,605**

**Proposed fix:** For sparse_intermittent / intermittent with high manual projection, set baseline = MAX(L13W non-zero avg, L26W non-zero avg, L52W non-zero avg) instead of the current L13W-first-fallback chain.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 23011-BB38904PDQ | 194,740 | 58,704 | -69.9% | G4K - 8.5" Round Paper Plates, 20ct - Zoomie  | Pattern 'sparse_intermittent' but manual projects 194,740. L13nz_avg 11170 vs L26nz_avg 11170. Consider MAX(L13, L26, L52) baseline. |
| 23011-BB38906PDQ | 191,381 | 104,949 | -45.2% | G4K - 6oz Paper Snack Bowls w/o lid, 24ct - Z | Pattern 'intermittent' but manual projects 191,381. L13nz_avg 7470 vs L26nz_avg 7470. Consider MAX(L13, L26, L52) baseline. |
| 23011-BB33706PDQ | 65,147 | 16,230 | -75.1% | G4K Disney - 3oz Bath Cups, 100ct -Mickey Mou | Pattern 'sparse_intermittent' but manual projects 65,147. L13nz_avg 4095 vs L26nz_avg 4095. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF15982 | 33,300 | 384 | -98.8% | CLEANZE Antibacterial Hand Wipes- 6" x 8" -10 | Pattern 'sparse_intermittent' but manual projects 33,300. L13nz_avg 0 vs L26nz_avg 1292. Consider MAX(L13, L26, L52) baseline. |
| 22008-BB38466 | 32,800 | 0 | -100.0% | Clorox Fraganzia - Duo Cube Toilet Bowl Clean | Pattern 'sparse_intermittent' but manual projects 32,800. L13nz_avg 7596 vs L26nz_avg 7596. Consider MAX(L13, L26, L52) baseline. |
| 22008-BB32892 | 22,750 | 3,888 | -82.9% | Clorox Fraganzia - Toilet Bowl Cleaner Tablet | Pattern 'sparse_intermittent' but manual projects 22,750. L13nz_avg 9720 vs L26nz_avg 9720. Consider MAX(L13, L26, L52) baseline. |
| 18360-FF5716PS | 21,000 | 4,032 | -80.8% | Arm & Hammer Fresh Breath Dental Kit for Dogs | Pattern 'sparse_intermittent' but manual projects 21,000. L13nz_avg 3744 vs L26nz_avg 2880. Consider MAX(L13, L26, L52) baseline. |
| 1864-BB14500CLR/9 | 18,351 | 2,340 | -87.2% | G4K - 12oz Paper Snack Bowls w/o lid, 20ct -  | Pattern 'sparse_intermittent' but manual projects 18,351. L13nz_avg 1066 vs L26nz_avg 1066. Consider MAX(L13, L26, L52) baseline. |
| 1864-FF17831 | 31,575 | 15,888 | -49.7% | CLEANZE Antibacterial Hand Wipes-Indiv. Wrapp | Pattern 'sparse_intermittent' but manual projects 31,575. L13nz_avg 0 vs L26nz_avg 912. Consider MAX(L13, L26, L52) baseline. |
| 18360-BB34390 | 25,800 | 16,824 | -34.8% | Fabuloso-Sponges Purple 12 CT | Pattern 'sparse_intermittent' but manual projects 25,800. L13nz_avg 8136 vs L26nz_avg 5130. Consider MAX(L13, L26, L52) baseline. |

### Other over-forecast

**16 records · absolute unit gap: 353,904**

**Proposed fix:** Manual inspection needed — review description and history pattern.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 23011-BB38480PDQ | 174,958 | 244,356 | +39.7% | G4K- 9oz Plastic Cups, 20ct  - Sharks | Gap +39.7%. Baseline=L13W avg. Pattern=intermittent. |
| 1864-BB21250 | 42,550 | 85,824 | +101.7% | Kingsford - Grill Brush | Gap +101.7%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF12842 | 45,731 | 73,476 | +60.7% | Wet Ones Antibacterial All-Purpose Wipe for D | Gap +60.7%. Baseline=L13W avg. Pattern=steady. |
| 20006-FF9297PDQ | 53,026 | 80,448 | +51.7% | A&H Complete Care Dog Dental Kit - Includes:  | Gap +51.7%. Baseline=L13W avg. Pattern=steady. |
| 20006-FF8990 | 71,580 | 98,376 | +37.4% | Fresh Step Litter Box Scent Crystals in Fresh | Gap +37.4%. Baseline=L13W avg. Pattern=steady. |
| 23011-BB13435CLR/12 | 69,112 | 91,764 | +32.8% | G4K - 8.5" Round Paper Plates, 20ct - Dinosau | Gap +32.8%. Baseline=L13W avg. Pattern=steady. |
| 23011-FF12858 | 56,622 | 75,900 | +34.0% | Vibrant Life Cat Flexible Slicker Massage Bru | Gap +34.0%. Baseline=L13W avg. Pattern=steady. |
| 23011-BB21058/6 | 20,900 | 39,912 | +91.0% | Fabuloso - Microfiber Mitt Purple 1 CT (6PC F | Gap +91.0%. Baseline=L13W avg. Pattern=steady. |
| 1864-BB22272 | 26,635 | 45,000 | +69.0% | Fabuloso Microfiber Cleaning Cloths Rainbow 8 | Gap +69.0%. Baseline=L13W avg. Pattern=steady. |
| 1864-BB24877 | 27,515 | 45,696 | +66.1% | G4K Disney - 8.5" Round Paper Plates, 40ct -  | Gap +66.1%. Baseline=L13W avg. Pattern=steady. |
| 20006-FF19341 | 30,387 | 45,372 | +49.3% | Fresh Step Litter Box Charcoal Odor Eliminati | Gap +49.3%. Baseline=L13W avg. Pattern=steady. |
| 20595-FF9297/24 | 20,120 | 33,768 | +67.8% | A&H Complete Care Dog Dental Kit - Includes:  | Gap +67.8%. Baseline=L13W avg. Pattern=steady. |
| 20006-FF10159 | 22,714 | 34,056 | +49.9% | Arm & Hammer Super Deodorizing Shampoo 20oz | Gap +49.9%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF22031 | 54,725 | 66,012 | +20.6% | Wags & Wiggles Cleanse Hypoallergenic Wipes 1 | Gap +20.6%. Baseline=L13W avg. Pattern=steady. |
| 1864-FF31287 | 23,280 | 29,152 | +25.2% | Glad for Pets Heavy Duty Activated Carbon Tra | Gap +25.2%. Baseline=L13W avg. Pattern=steady. |

### Other under-forecast

**19 records · absolute unit gap: 338,276**

**Proposed fix:** Manual inspection needed — review description and history pattern.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 12446-BB0761CAN | 49,000 | 20,484 | -58.2% | Glad - 8.5" Square Paper Plates, 50ct - Purpl | Gap -58.2%. Baseline=L13W avg. Pattern=steady. |
| 13225-BB11417FL/12 | 25,384 | 0 | -100.0% | Kingsford - Wooden Fire Starter Rolls - 16ct | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 12835-BB0237 | 25,000 | 0 | -100.0% | Glad - Assorted Cutlery, 240ct - Clear | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF30755EC | 24,405 | 0 | -100.0% | Wet One's - Antibacterial Paw Cleansing Foam  | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF7297AMZ | 23,650 | 0 | -100.0% | Burt's Bees Waterless Shampoo for Cats 10oz | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF8991 | 21,325 | 0 | -100.0% | Fresh Step Litter Box Scent Crystals in Summe | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 1864-FF10479PX2 | 20,055 | 0 | -100.0% | Arm & Hammer Super Deodorizing Spray - Kiwi B | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 22008-BB25519 | 20,000 | 0 | -100.0% | Glad - 18oz Plastic Cups, 40ct - Red (Same as | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 23011-FF12859 | 77,213 | 58,056 | -24.8% | Vibrant Life Dual Action Grooming Glove - Des | Gap -24.8%. Baseline=L13W avg. Pattern=steady. |
| 12835-BB38259 | 18,932 | 0 | -100.0% | Kingsford - Heavy Duty Grill Liners (New styl | Gap -100.0%. Baseline=no L13W orders. Pattern=inactive. |
| 16553-FF26580 | 18,695 | 0 | -100.0% | ONP DOG ATTRACTANT SPRAY 16 FL OZ | Gap -100.0%. Baseline=L13W avg. Pattern=steady. |
| 1864-BB33708 | 46,850 | 28,350 | -39.5% | G4K Disney - 3oz Bath Cups, 100ct -Princess | Gap -39.5%. Baseline=L13W avg. Pattern=steady. |
| 23011-BB33708PDQ | 45,052 | 32,166 | -28.6% | G4K Disney - 3oz Bath Cups, 100ct - Princess  | Gap -28.6%. Baseline=L13W avg. Pattern=steady. |
| 16553-SF8168PS | 43,347 | 31,200 | -28.0% | Arm & Hammer Fresh Breath Dental Kit for Dogs | Gap -28.0%. Baseline=L13W avg. Pattern=steady. |
| 1864-BB0578 | 50,577 | 39,924 | -21.1% | Glad - Plastic Spoons, 24ct - Clear | Gap -21.1%. Baseline=L13W avg. Pattern=steady. |

### Seasonal-ramp under-forecast (peak >> trough)

**10 records · absolute unit gap: 189,450**

**Proposed fix:** Add peak-anchored baseline: when category profile matches AND L52 peak > 3x L13 non-zero avg, compute `peak_baseline = avg(L52 weeks falling in category peak months)` and anchor the seasonal curve to that. Then each week = peak_baseline * (category_multiplier[w] / max(category_multiplier)).

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 23011-FF38640 | 206,500 | 151,200 | -26.8% | Fresh Step Litter Box Scent Crystals in Fresh | Peak 44,064 is 6.6x L13 avg 6712. Forecast anchored to trough, not peak. |
| 23011-FF38641 | 75,000 | 36,972 | -50.7% | Fresh Step Litter Box Scent Crystals in Summe | Peak 24,624 is 12.3x L13 avg 1996. Forecast anchored to trough, not peak. |
| 1864-SF8168 | 18,810 | 912 | -95.2% | Arm & Hammer Fresh Breath Dental Kit for Dogs | Peak 7,248 is 15.6x L13 avg 464. Forecast anchored to trough, not peak. |
| 3102-FF12846 | 22,320 | 6,840 | -69.4% | Wet Ones Anti-Bacterial Paw/Tushie Wipe for D | Peak 5,664 is 4.6x L13 avg 1218. Forecast anchored to trough, not peak. |
| 1864-FF20206EC | 30,450 | 16,038 | -47.3% | Arm & Hammer Itch Relief Spray | Peak 4,704 is 4.3x L13 avg 1085. Forecast anchored to trough, not peak. |
| 1864-FF12508 | 62,775 | 49,032 | -21.9% | A&H Pet Scents Deodorizing Gel Beads - Lavend | Peak 8,208 is 3.9x L13 avg 2129. Forecast anchored to trough, not peak. |
| 1864-BB12068 | 23,620 | 13,560 | -42.6% | Kingsford - Wooden Fire Starter Rolls - 32ct | Peak 7,344 is 7.6x L13 avg 966. Forecast anchored to trough, not peak. |
| 1864-SF8174EC | 33,228 | 24,864 | -25.2% | Arm & Hammer Tartar Control Dental Spray for  | Peak 10,344 is 5.2x L13 avg 1972. Forecast anchored to trough, not peak. |
| 1885-FF17546 | 19,620 | 11,472 | -41.5% | Amazon Private Label: Rawhide-free Twists, Pe | Peak 5,232 is 9.4x L13 avg 556. Forecast anchored to trough, not peak. |
| 1864-FF13876 | 24,175 | 16,158 | -33.2% | Arm & Hammer Med. Diapers-16.5"-21" Waist - 1 | Peak 2,958 is 4.0x L13 avg 739. Forecast anchored to trough, not peak. |

### Isolated spike over-forecast (outlier cap)

**4 records · absolute unit gap: 94,484**

**Proposed fix:** Tighten Fix 3 outlier cap: lower the threshold from 3.0x median to 2.5x median, OR add a secondary check — if max(L13_nz) > 2x L13_all_avg AND max occurs only once, cap at 2x L13_all_avg.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB0466 | 77,350 | 117,492 | +51.9% | Kingsford - Deluxe Charcoal Chimney Starter | L13 non-zero max 27,180 is 4.1x median 6600. Outlier cap didn't fully neutralize. |
| 1864-BB30930 | 72,985 | 98,280 | +34.7% | Clorox Fraganzia - 6pk Garbage Can Stick Ups  | L13 non-zero max 11,004 is 3.6x median 3072. Outlier cap didn't fully neutralize. |
| 23011-FF7258COS | 112,565 | 131,196 | +16.6% | Burt's Bees Oatmeal Shampoo for Dogs 16oz | L13 non-zero max 13,182 is 3.1x median 4248. Outlier cap didn't fully neutralize. |
| 1864-FF7120EC | 49,032 | 59,448 | +21.2% | BioSilk for Dogs Detangling and Shine Spray f | L13 non-zero max 26,167 is 27.6x median 948. Outlier cap didn't fully neutralize. |

### Seasonal category not in CATEGORY_PROFILES

**5 records · absolute unit gap: 69,773**

**Proposed fix:** Add missing category profiles to CATEGORY_PROFILES in inventory_forecaster.py. Also match on Brand (e.g., 'Kingsford' → outdoor_grill) and Product_Category / Product_Subcategory fields, not description alone. Queue the listed keywords.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB27359 | 31,775 | 10,980 | -65.4% | Clorox Fraganzia - 6pk Deodorizing Balls - Fr | L52 peak 8,040 / L13 avg 400 = 20.1x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 12835-BB22272 | 78,000 | 57,216 | -26.6% | Fabuloso Microfiber Cleaning Cloths Rainbow 8 | L52 peak 27,360 / L13 avg 6847 = 4.0x. Description keyword 'fabuloso' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB26922 | 27,420 | 14,340 | -47.7% | Clorox Fraganzia - Air Freshener Beads Twin P | L52 peak 6,162 / L13 avg 1298 = 4.7x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |
| 1864-BB27773 | 25,880 | 15,640 | -39.6% | G4K Disney - 10" Round Paper Plates, 30ct - F | L52 peak 2,060 / L13 avg 616 = 3.3x. Description keyword 'paper plate' → 'party_summer' not in CATEGORY_PROFILES. |
| 1864-BB28473 | 21,950 | 17,076 | -22.2% | Clorox Fraganzia - Gel Air Freshener Cone - 6 | L52 peak 12,324 / L13 avg 508 = 24.2x. Description keyword 'fraganzia' → 'cleaning' not in CATEGORY_PROFILES. |

### Amazon Prime Day pre-buy gap (W5-W9 under-forecast)

**2 records · absolute unit gap: 9,428**

**Proposed fix:** Extend PRIME_DAY_WEEKS lift schedule to a tapered ramp: W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). Currently W7-W9 only at flat 1.25.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 1864-BB14812 | 32,450 | 26,350 | -18.8% | G4K Paw Patrol - 8.5" Round Paper Plates, 20c | Manual W5-W9 avg 1442 vs overall 1248 (+16%). AI W5-W9 = 3,320. |
| 1864-FF5773 | 19,834 | 16,506 | -16.8% | Burts Bees Kitten wipes -50ct | Manual W5-W9 avg 1053 vs overall 763 (+38%). AI W5-W9 = 2,424. |

### Aligned with manual (within ±10%)

**110 records · absolute unit gap: 387,735**

**Proposed fix:** No fix needed.

| Key | Manual | AI | Gap% | Description | Diagnosis |
|---|---:|---:|---:|---|---|
| 23011-FF8654 | 136,000 | 113,598 | -16.5% | Glad for Pets Activated Carbon Training Pads  | Gap -16.5% — within tolerance. |
| 23011-FF10159 | 229,836 | 212,538 | -7.5% | Arm & Hammer Super Deodorizing Shampoo 20oz | Gap -7.5% — within tolerance. |
| 1864-BB20552 | 84,648 | 68,292 | -19.3% | Glad - Plastic Forks, 70ct - Clear | Gap -19.3% — within tolerance. |
| 23011-FF7266COS | 112,239 | 126,582 | +12.8% | Burt's Bees Itch Soothing Shampoo for Dogs 16 | Gap +12.8% — within tolerance. |
| 1864-FF8990 | 145,335 | 156,924 | +8.0% | Fresh Step Litter Box Scent Crystals in Fresh | Gap +8.0% — within tolerance. |
| 1864-FF12300/24 | 70,594 | 81,552 | +15.5% | A&H Complete Care Adult Boxed Toothpaste in C | Gap +15.5% — within tolerance. |
| 23011-FF15585 | 85,687 | 75,384 | -12.0% | Palmer's for Pets Dog Wipes with Cocoa Butter | Gap -12.0% — within tolerance. |
| 23011-FF4775COS | 135,130 | 145,254 | +7.5% | Burts Bees Tearless 2 in 1 Shampoo and Condit | Gap +7.5% — within tolerance. |
| 1864-FF12302/24EC | 51,792 | 42,120 | -18.7% | A&H Complete Care Dog Dental Spray in Mint Fl | Gap -18.7% — within tolerance. |
| 23011-FF15584 | 52,006 | 42,456 | -18.4% | Palmer's for Pets Direct Relief Lotion Spray  | Gap -18.4% — within tolerance. |
| 23011-FF19998 | 57,840 | 67,260 | +16.3% | Vibrant Life Slicker Brush + Cleaning Comb | Gap +16.3% — within tolerance. |
| 23011-FF8882/2 | 93,100 | 101,242 | +8.7% | Glad for Pets Jumbo Activated Carbon Training | Gap +8.7% — within tolerance. |
| 1864-BB0237 | 89,796 | 82,476 | -8.2% | Glad - Assorted Cutlery, 240ct - Clear | Gap -8.2% — within tolerance. |
| 23011-FF15589 | 51,922 | 44,760 | -13.8% | Palmer's for Pets Paw Pad & Nose Balm with Co | Gap -13.8% — within tolerance. |
| 20006-BB38638 | 100,153 | 93,024 | -7.1% | G4K Disney Pixar - 3oz Paper Bath Cups,20ct ( | Gap -7.1% — within tolerance. |

## Unmatched seasonal keywords (add to CATEGORY_PROFILES)

| Keyword | Records | Suggested category |
|---|---:|---|
| `fraganzia` | 3 | cleaning |
| `air freshener` | 2 | spring_cleaning |
| `fabuloso` | 1 | cleaning |
| `deodorizing ball` | 1 | spring_cleaning |
| `paper plate` | 1 | party_summer |

## Priority-ordered model fixes

1. **Seasonal-ramp under-forecast (peak >> trough)** (10 records, 189,450 unit gap)
   - Add peak-anchored baseline: when category profile matches AND L52 peak > 3x L13 non-zero avg, compute `peak_baseline = avg(L52 weeks falling in category peak months)` and anchor the seasonal curve to that. Then each week = peak_baseline * (category_multiplier[w] / max(category_multiplier)).

2. **Seasonal category not in CATEGORY_PROFILES** (5 records, 69,773 unit gap)
   - Add missing category profiles to CATEGORY_PROFILES in inventory_forecaster.py. Also match on Brand (e.g., 'Kingsford' → outdoor_grill) and Product_Category / Product_Subcategory fields, not description alone. Queue the listed keywords.

3. **Amazon Prime Day pre-buy gap (W5-W9 under-forecast)** (2 records, 9,428 unit gap)
   - Extend PRIME_DAY_WEEKS lift schedule to a tapered ramp: W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). Currently W7-W9 only at flat 1.25.

4. **Sparse/intermittent baseline too conservative** (10 records, 413,605 unit gap)
   - For sparse_intermittent / intermittent with high manual projection, set baseline = MAX(L13W non-zero avg, L26W non-zero avg, L52W non-zero avg) instead of the current L13W-first-fallback chain.

5. **Declining item over-forecast** (24 records, 590,516 unit gap)
   - Detect end-of-life / declining items: if L4W avg < L13W non-zero avg × 0.7, blend the 26-week forecast toward L4W avg using weight = 0.5 on L4W and 0.5 on the model forecast. Further down-weight weeks W14-W26.

6. **Isolated spike over-forecast (outlier cap)** (4 records, 94,484 unit gap)
   - Tighten Fix 3 outlier cap: lower the threshold from 3.0x median to 2.5x median, OR add a secondary check — if max(L13_nz) > 2x L13_all_avg AND max occurs only once, cap at 2x L13_all_avg.
