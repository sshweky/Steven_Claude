# Variance Deep-Dive: AI vs Manual (>5.0% delta)

**Source:** `C:\Users\steven\.claude\skills\inventory-forecaster-cc\forecast_results.json`
**Total records:** 5571
**Records flagged:** 2124 (38.1% of total)
**Direction split:** UP=600 DOWN=1524

**Total |unit gap|:** 5,590,805
**Median |delta %|:** 44.0%

---

## 1. By customer (top 15 by absolute unit gap)

| Customer | Records | UP | DOWN | |unit gap| | Bias |
|---|---:|---:|---:|---:|---|
| `AMAZON.COM.KYDC,INC` | 562 | 170 | 392 | 1,936,811 |  |
| `WAL MART STORES` | 120 | 39 | 81 | 935,661 |  |
| `TARGET CTRL INV PRCSNG` | 21 | 16 | 5 | 268,087 | Manual UP-bias |
| `ROSS STORES INC - MERCHANDISE` | 44 | 8 | 36 | 211,902 | Manual DOWN-bias |
| `VARIETY WHOLESALERS INC` | 80 | 9 | 71 | 207,612 | Manual DOWN-bias |
| `BURLINGTON COAT FACTORY` | 74 | 13 | 61 | 162,834 | Manual DOWN-bias |
| `PETSMART INC` | 51 | 14 | 37 | 146,159 |  |
| `CHEWY.COM` | 140 | 46 | 94 | 140,060 |  |
| `LOWES COMPANIES, INC.` | 13 | 5 | 8 | 104,386 |  |
| `AMAZON PRIVATE LABEL` | 12 | 3 | 9 | 102,873 | Manual DOWN-bias |
| `DD'S DISCOUNTS` | 71 | 1 | 70 | 84,486 | Manual DOWN-bias |
| `LOBLAWS INC` | 7 | 0 | 7 | 76,750 | Manual DOWN-bias |
| `BISEK & COMPANY INC` | 31 | 4 | 27 | 73,696 | Manual DOWN-bias |
| `PSP DISTRIBUTION, LLC` | 30 | 11 | 19 | 72,833 |  |
| `KOHL'S DEPT STR` | 36 | 2 | 34 | 57,581 | Manual DOWN-bias |

## 2. By manual / AI shape combo (top 15 by |unit gap|)

Reveals the *shape mismatches* where AI and planner disagree on WHEN demand falls.

| Manual shape | -> | AI shape | Records | |unit gap| |
|---|---|---|---:|---:|
| `FLAT` | -> | `FLAT` | 590 | 1,948,719 |
| `FLAT` | -> | `VARIABLE` | 292 | 656,688 |
| `SPARSE` | -> | `SPARSE` | 395 | 650,335 |
| `FLAT` | -> | `SPARSE` | 303 | 308,406 |
| `SPARSE` | -> | `ZERO` | 105 | 288,186 |
| `VARIABLE` | -> | `VARIABLE` | 42 | 271,437 |
| `VARIABLE` | -> | `FLAT` | 55 | 230,697 |
| `FRONT_LOADED` | -> | `FLAT` | 13 | 214,252 |
| `SPARSE` | -> | `FLAT` | 52 | 186,646 |
| `FLAT` | -> | `ZERO` | 42 | 99,154 |
| `BACK_LOADED` | -> | `VARIABLE` | 14 | 81,018 |
| `BACK_LOADED` | -> | `BACK_LOADED` | 22 | 65,872 |
| `SPARSE` | -> | `VARIABLE` | 21 | 62,513 |
| `BACK_LOADED` | -> | `SPARSE` | 9 | 61,194 |
| `FLAT` | -> | `SPIKE` | 16 | 56,839 |

## 3. By status_cust (lifecycle stage)

| Status_Cust | Records | |unit gap| |
|---|---:|---:|
| `A` | 1563 | 3,781,164 |
| `(unknown)` | 164 | 425,185 |
| `A: NEW` | 19 | 403,972 |
| `A: Off-Price` | 134 | 265,916 |
| `FD` | 78 | 214,713 |
| `A: New 2/26` | 18 | 172,177 |
| `A: Reactivated` | 3 | 43,082 |
| `A: NEW 10/25` | 9 | 37,440 |
| `A: NEW 2/25` | 6 | 33,632 |
| `A: Promo` | 18 | 27,426 |

## 4. By AI model class

| Model | Records | UP | DOWN | |unit gap| |
|---|---:|---:|---:|---:|
| `Seasonal Baseline` | 561 | 226 | 335 | 1,818,167 |
| `Croston's` | 623 | 148 | 475 | 1,725,585 |
| `Sparse Intermittent` | 615 | 181 | 434 | 912,790 |
| `Heuristic` | 156 | 44 | 112 | 491,735 |
| `OTB (zero)` | 164 | 0 | 164 | 425,185 |
| `Heuristic (F72 new-launch ramp)` | 2 | 0 | 2 | 184,953 |
| `Pre-launch NEW (manual passthrough)` | 1 | 1 | 0 | 28,594 |
| `Inactive+Floor` | 1 | 0 | 1 | 3,244 |

## 5. Rules that most often fire on disagreeing records

| Rule | Times fired on disagreements |
|---|---:|
| `F37` | 1296 |
| `F64` | 1023 |
| `VP-Q4` | 818 |
| `VP-Q1` | 561 |
| `F16` | 473 |
| `F10` | 451 |
| `VP-Q3` | 429 |
| `F35` | 347 |
| `M1` | 341 |
| `R2` | 317 |
| `F71` | 317 |
| `F34` | 312 |
| `F66` | 300 |
| `F_PO_CUTOFF` | 264 |
| `F41` | 238 |
| `F15` | 238 |
| `F43` | 237 |
| `F59h` | 232 |
| `F47` | 215 |
| `F62` | 198 |
| `F59g` | 159 |
| `F38b` | 154 |
| `F59a` | 153 |
| `F14a` | 151 |
| `F17` | 146 |

## 6. Top 50 records by absolute unit gap

| Key | Customer | Model | Manual | AI | Δ | M shape | AI shape |
|---|---|---|---:|---:|---:|---|---|
| `23011-BB38904PDQ` | WAL MART STORES | Heuristic (F72  | 194,740 | 58,704 | -69.9% | FLAT | FLAT |
| `1864-BB30930` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 72,985 | 159,816 | +119.0% | FRONT_LOADED | FLAT |
| `23011-BB38906PDQ` | WAL MART STORES | Croston's | 191,381 | 104,949 | -45.2% | FLAT | FLAT |
| `23011-BB13436CLR/12` | WAL MART STORES | Croston's | 97,993 | 157,920 | +61.2% | FLAT | FLAT |
| `1864-BB22272` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 26,635 | 82,896 | +211.2% | FRONT_LOADED | FLAT |
| `23011-FF38640` | WAL MART STORES | Seasonal Baseli | 206,500 | 151,200 | -26.8% | FLAT | VARIABLE |
| `23011-BB33706PDQ` | WAL MART STORES | Heuristic (F72  | 65,147 | 16,230 | -75.1% | FLAT | FLAT |
| `23011-BB38504PDQ` | WAL MART STORES | Croston's | 148,209 | 100,179 | -32.4% | FLAT | FLAT |
| `12835-BB12022/12` | LOWES COMPANIES, INC. | Sparse Intermit | 11,544 | 56,568 | +390.0% | VARIABLE | SPARSE |
| `1885-FF17554` | AMAZON PRIVATE LABEL | Croston's | 47,250 | 89,280 | +89.0% | VARIABLE | VARIABLE |
| `23011-BB38585PDQ` | WAL MART STORES | Croston's | 76,611 | 37,377 | -51.2% | FLAT | FLAT |
| `23011-FF38641` | WAL MART STORES | Seasonal Baseli | 75,000 | 36,972 | -50.7% | VARIABLE | VARIABLE |
| `1864-FF12655EC` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 49,473 | 87,390 | +76.6% | VARIABLE | FLAT |
| `1864-FF25895` | AMAZON.COM.KYDC,INC | Croston's | 43,260 | 77,616 | +79.4% | BACK_LOADED | VARIABLE |
| `1864-FF15982` | AMAZON.COM.KYDC,INC | Heuristic | 33,300 | 132 | -99.6% | BACK_LOADED | SPARSE |
| `12446-BB0761CAN` | LOBLAWS INC | Croston's | 49,000 | 16,644 | -66.0% | SPARSE | FLAT |
| `20006-BB38635` | TARGET CTRL INV PRCSNG | Heuristic | 33,202 | 64,080 | +93.0% | FLAT | FLAT |
| `23011-BB38905PDQ` | WAL MART STORES | Croston's | 245,529 | 216,072 | -12.0% | FLAT | FLAT |
| `1864-FF8654` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 93,250 | 64,050 | -31.3% | FLAT | FLAT |
| `23011-BB29490/6` | WAL MART STORES | Pre-launch NEW  | 81,750 | 110,344 | +35.0% | VARIABLE | FLAT |
| `20006-FF9297PDQ` | TARGET CTRL INV PRCSNG | Seasonal Baseli | 53,026 | 80,448 | +51.7% | FLAT | FLAT |
| `1864-FF15584` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 34,470 | 7,200 | -79.1% | FLAT | FLAT |
| `1864-FF8990` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 145,335 | 172,164 | +18.5% | FLAT | VARIABLE |
| `20006-FF8990` | TARGET CTRL INV PRCSNG | Seasonal Baseli | 71,580 | 98,376 | +37.4% | FLAT | FLAT |
| `20006-BB38489` | TARGET CTRL INV PRCSNG | Heuristic | 31,674 | 57,588 | +81.8% | FLAT | FLAT |
| `1864-BB26922` | AMAZON.COM.KYDC,INC | Croston's | 27,420 | 1,650 | -94.0% | FLAT | FLAT |
| `1885-FF17574` | AMAZON PRIVATE LABEL | Croston's | 37,300 | 12,000 | -67.8% | VARIABLE | FLAT |
| `1864-FF10159EC` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 77,062 | 51,798 | -32.8% | VARIABLE | VARIABLE |
| `1864-BB28360` | AMAZON.COM.KYDC,INC | Croston's | 21,205 | 45,276 | +113.5% | FLAT | FLAT |
| `1864-BB28480` | AMAZON.COM.KYDC,INC | Croston's | 10,285 | 33,408 | +224.8% | FLAT | FLAT |
| `23011-BB13435CLR/12` | WAL MART STORES | Seasonal Baseli | 69,112 | 91,764 | +32.8% | FLAT | FLAT |
| `1864-BB13437` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 56,064 | 34,782 | -38.0% | VARIABLE | VARIABLE |
| `20006-BB38634` | TARGET CTRL INV PRCSNG | Heuristic | 28,833 | 50,112 | +73.8% | FLAT | FLAT |
| `12835-BB22272` | LOWES COMPANIES, INC. | Croston's | 78,000 | 57,552 | -26.2% | FLAT | FLAT |
| `22008-BB25519` | VARIETY WHOLESALERS INC | OTB (zero) | 20,000 | 0 | -100.0% | SPARSE | ZERO |
| `12446-BB0234CAN` | LOBLAWS INC | Croston's | 66,000 | 46,194 | -30.0% | SPARSE | FLAT |
| `20006-BB38671` | TARGET CTRL INV PRCSNG | Heuristic | 42,484 | 62,256 | +46.5% | FLAT | FLAT |
| `1864-FF17831` | AMAZON.COM.KYDC,INC | Heuristic | 31,575 | 12,024 | -61.9% | BACK_LOADED | VARIABLE |
| `23011-FF12858` | WAL MART STORES | Seasonal Baseli | 56,622 | 75,900 | +34.0% | FLAT | VARIABLE |
| `23011-FF12859` | WAL MART STORES | Seasonal Baseli | 77,213 | 58,056 | -24.8% | FLAT | FLAT |
| `12835-BB38259` | LOWES COMPANIES, INC. | OTB (zero) | 18,932 | 0 | -100.0% | SPARSE | ZERO |
| `22008-BB32892` | VARIETY WHOLESALERS INC | Sparse Intermit | 22,750 | 3,888 | -82.9% | BACK_LOADED | SPARSE |
| `20006-BB38484` | TARGET CTRL INV PRCSNG | Heuristic | 34,518 | 53,184 | +54.1% | FLAT | FLAT |
| `23011-FF7258COS` | WAL MART STORES | Seasonal Baseli | 112,565 | 131,196 | +16.6% | FLAT | VARIABLE |
| `18360-BB26923` | ROSS STORES INC - MERCHAN | OTB (zero) | 18,000 | 0 | -100.0% | SPARSE | ZERO |
| `1864-FF12508` | AMAZON.COM.KYDC,INC | Croston's | 62,775 | 45,000 | -28.3% | FLAT | FLAT |
| `1864-BB0466` | AMAZON.COM.KYDC,INC | Croston's | 77,350 | 59,610 | -22.9% | FRONT_LOADED | FLAT |
| `1864-BB18890` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 1,950 | 19,500 | +900.0% | SPARSE | FLAT |
| `1864-FF31068` | AMAZON.COM.KYDC,INC | Croston's | 18,340 | 900 | -95.1% | FLAT | FLAT |
| `23011-FF10159` | WAL MART STORES | Seasonal Baseli | 229,836 | 212,538 | -7.5% | VARIABLE | VARIABLE |