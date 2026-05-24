# Variance Deep-Dive: AI vs Manual (>5.0% delta)

**Source:** `C:\Users\steven\.claude\skills\inventory-forecaster-cc\forecast_results.json`
**Total records:** 5571
**Records flagged:** 2120 (38.1% of total)
**Direction split:** UP=563 DOWN=1557

**Total |unit gap|:** 5,938,902
**Median |delta %|:** 43.9%

---

## 1. By customer (top 15 by absolute unit gap)

| Customer | Records | UP | DOWN | |unit gap| | Bias |
|---|---:|---:|---:|---:|---|
| `AMAZON.COM.KYDC,INC` | 548 | 182 | 366 | 1,915,063 |  |
| `WAL MART STORES` | 119 | 43 | 76 | 1,313,253 |  |
| `ROSS STORES INC - MERCHANDISE` | 46 | 7 | 39 | 220,538 | Manual DOWN-bias |
| `VARIETY WHOLESALERS INC` | 83 | 12 | 71 | 215,187 | Manual DOWN-bias |
| `PETSMART INC` | 59 | 10 | 49 | 185,454 | Manual DOWN-bias |
| `BURLINGTON COAT FACTORY` | 70 | 8 | 62 | 184,762 | Manual DOWN-bias |
| `CHEWY.COM` | 150 | 35 | 115 | 143,690 | Manual DOWN-bias |
| `AMAZON PRIVATE LABEL` | 12 | 2 | 10 | 125,224 | Manual DOWN-bias |
| `LOWES COMPANIES, INC.` | 12 | 3 | 9 | 108,453 | Manual DOWN-bias |
| `TARGET CTRL INV PRCSNG` | 15 | 13 | 2 | 103,912 | Manual UP-bias |
| `DD'S DISCOUNTS` | 73 | 6 | 67 | 91,514 | Manual DOWN-bias |
| `PSP DISTRIBUTION, LLC` | 29 | 6 | 23 | 86,805 | Manual DOWN-bias |
| `BISEK & COMPANY INC` | 35 | 7 | 28 | 78,943 | Manual DOWN-bias |
| `KOHL'S DEPT STR` | 37 | 1 | 36 | 62,772 | Manual DOWN-bias |
| `LOBLAWS INC` | 7 | 0 | 7 | 61,414 | Manual DOWN-bias |

## 2. By manual / AI shape combo (top 15 by |unit gap|)

Reveals the *shape mismatches* where AI and planner disagree on WHEN demand falls.

| Manual shape | -> | AI shape | Records | |unit gap| |
|---|---|---|---:|---:|
| `FLAT` | -> | `FLAT` | 559 | 1,477,708 |
| `FLAT` | -> | `VARIABLE` | 302 | 885,869 |
| `FLAT` | -> | `SPARSE` | 294 | 781,541 |
| `SPARSE` | -> | `SPARSE` | 404 | 762,826 |
| `VARIABLE` | -> | `FLAT` | 54 | 278,923 |
| `SPARSE` | -> | `ZERO` | 85 | 223,604 |
| `FRONT_LOADED` | -> | `VARIABLE` | 8 | 188,026 |
| `VARIABLE` | -> | `VARIABLE` | 32 | 160,296 |
| `SPARSE` | -> | `FLAT` | 47 | 150,216 |
| `FLAT` | -> | `FRONT_LOADED` | 45 | 113,068 |
| `SPARSE` | -> | `VARIABLE` | 30 | 97,742 |
| `BACK_LOADED` | -> | `FLAT` | 23 | 89,834 |
| `FRONT_LOADED` | -> | `FLAT` | 17 | 74,983 |
| `BACK_LOADED` | -> | `BACK_LOADED` | 23 | 73,197 |
| `FLAT` | -> | `BACK_LOADED` | 18 | 70,746 |

## 3. By status_cust (lifecycle stage)

| Status_Cust | Records | |unit gap| |
|---|---:|---:|
| `A` | 1563 | 4,038,558 |
| `A: NEW` | 22 | 480,067 |
| `(unknown)` | 146 | 384,215 |
| `A: Off-Price` | 143 | 342,302 |
| `FD` | 81 | 301,023 |
| `A: New 2/26` | 24 | 130,101 |
| `A: Reactivated` | 3 | 34,710 |
| `A: NEW 10/25` | 9 | 31,662 |
| `A: NEW 9/25` | 6 | 25,272 |
| `A: NEW 3/26` | 15 | 21,926 |

## 4. By AI model class

| Model | Records | UP | DOWN | |unit gap| |
|---|---:|---:|---:|---:|
| `Seasonal Baseline` | 584 | 225 | 359 | 1,885,548 |
| `Croston's` | 585 | 121 | 464 | 1,819,317 |
| `Sparse Intermittent` | 649 | 169 | 480 | 1,483,610 |
| `OTB (zero)` | 146 | 0 | 146 | 384,215 |
| `Heuristic` | 151 | 48 | 103 | 361,893 |
| `Pre-launch NEW (manual passthrough)` | 4 | 0 | 4 | 3,767 |
| `Reactivating` | 1 | 0 | 1 | 552 |

## 5. Rules that most often fire on disagreeing records

| Rule | Times fired on disagreements |
|---|---:|
| `F64` | 1050 |
| `VP-Q4` | 795 |
| `VP-Q1` | 584 |
| `F37` | 531 |
| `F16` | 492 |
| `F10` | 450 |
| `VP-Q3` | 431 |
| `M1` | 350 |
| `F35` | 347 |
| `F71` | 345 |
| `R2` | 329 |
| `F34` | 327 |
| `F_PO_CUTOFF` | 296 |
| `F66` | 286 |
| `F61` | 242 |
| `F43` | 235 |
| `F15` | 231 |
| `F59h` | 229 |
| `F47` | 226 |
| `F41` | 219 |
| `F62` | 214 |
| `F59d` | 182 |
| `F59a` | 178 |
| `F17` | 177 |
| `F59g` | 164 |

## 6. Top 50 records by absolute unit gap

| Key | Customer | Model | Manual | AI | Δ | M shape | AI shape |
|---|---|---|---:|---:|---:|---|---|
| `23011-BB38905PDQ` | WAL MART STORES | Sparse Intermit | 236,023 | 34,956 | -85.2% | FLAT | SPARSE |
| `23011-BB38904PDQ` | WAL MART STORES | Sparse Intermit | 187,237 | 54,192 | -71.1% | FLAT | SPARSE |
| `1864-BB30930` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 73,460 | 164,496 | +123.9% | FRONT_LOADED | VARIABLE |
| `23011-BB38906PDQ` | WAL MART STORES | Croston's | 184,007 | 107,838 | -41.4% | FLAT | FLAT |
| `23011-BB38504PDQ` | WAL MART STORES | Croston's | 142,471 | 71,874 | -49.6% | FLAT | FLAT |
| `23011-BB38585PDQ` | WAL MART STORES | Sparse Intermit | 73,659 | 5,940 | -91.9% | FLAT | SPARSE |
| `23011-BB38564PDQ` | WAL MART STORES | Sparse Intermit | 73,175 | 11,280 | -84.6% | FLAT | SPARSE |
| `23011-BB13437CLR/12` | WAL MART STORES | Croston's | 7,500 | 69,264 | +823.5% | FLAT | VARIABLE |
| `23011-BB13436CLR/12` | WAL MART STORES | Croston's | 94,427 | 154,896 | +64.0% | FLAT | FLAT |
| `23011-BB38480PDQ` | WAL MART STORES | Croston's | 168,184 | 117,420 | -30.2% | FLAT | VARIABLE |
| `23011-BB33706PDQ` | WAL MART STORES | Sparse Intermit | 62,637 | 15,054 | -76.0% | FLAT | SPARSE |
| `1864-FF9297/24` | AMAZON.COM.KYDC,INC | Croston's | 69,515 | 110,664 | +59.2% | FLAT | FLAT |
| `23011-FF8651/8` | WAL MART STORES | Seasonal Baseli | 306,000 | 346,344 | +13.2% | FLAT | FLAT |
| `1864-BB22272` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 29,965 | 68,736 | +129.4% | FRONT_LOADED | VARIABLE |
| `12835-BB12022/12` | LOWES COMPANIES, INC. | Sparse Intermit | 11,544 | 49,992 | +333.1% | VARIABLE | SPARSE |
| `23011-FF38640` | WAL MART STORES | Seasonal Baseli | 198,000 | 160,236 | -19.1% | FLAT | VARIABLE |
| `23011-FF38641` | WAL MART STORES | Seasonal Baseli | 71,700 | 34,800 | -51.5% | VARIABLE | VARIABLE |
| `1885-FF17554` | AMAZON PRIVATE LABEL | Croston's | 44,850 | 81,612 | +82.0% | VARIABLE | FLAT |
| `1864-BB33708` | AMAZON.COM.KYDC,INC | Croston's | 46,350 | 83,070 | +79.2% | VARIABLE | FLAT |
| `1864-FF12655EC` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 50,419 | 83,424 | +65.5% | VARIABLE | FLAT |
| `23011-BB13435CLR/12` | WAL MART STORES | Seasonal Baseli | 66,597 | 98,844 | +48.4% | FLAT | FLAT |
| `1864-FF15982` | AMAZON.COM.KYDC,INC | Heuristic | 31,450 | 144 | -99.5% | BACK_LOADED | SPARSE |
| `23011-BB13447CLR/12` | WAL MART STORES | Croston's | 7,200 | 38,244 | +431.2% | FLAT | VARIABLE |
| `1864-FF8654` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 94,500 | 64,050 | -32.2% | FLAT | FLAT |
| `1885-FF17574` | AMAZON PRIVATE LABEL | Croston's | 36,300 | 6,600 | -81.8% | VARIABLE | FLAT |
| `1864-FF15584` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 34,490 | 5,400 | -84.3% | FLAT | FLAT |
| `20006-BB38638` | TARGET CTRL INV PRCSNG | Croston's | 100,435 | 72,144 | -28.2% | FLAT | FLAT |
| `1885-FF17578` | AMAZON PRIVATE LABEL | Croston's | 32,750 | 5,400 | -83.5% | VARIABLE | FLAT |
| `1864-BB0131EC` | AMAZON.COM.KYDC,INC | Croston's | 28,465 | 53,844 | +89.2% | FRONT_LOADED | VARIABLE |
| `1864-BB26922` | AMAZON.COM.KYDC,INC | Croston's | 27,290 | 1,998 | -92.7% | FLAT | VARIABLE |
| `1864-BB24877` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 28,055 | 51,440 | +83.4% | FLAT | FLAT |
| `1864-FF8649/24` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 61,600 | 84,816 | +37.7% | FLAT | FLAT |
| `12835-BB38259` | LOWES COMPANIES, INC. | OTB (zero) | 22,496 | 0 | -100.0% | FRONT_LOADED | ZERO |
| `12446-BB0761CAN` | LOBLAWS INC | Croston's | 49,000 | 26,544 | -45.8% | SPARSE | VARIABLE |
| `23011-BB31552CLR/9` | WAL MART STORES | Seasonal Baseli | 7,200 | 28,710 | +298.8% | FLAT | VARIABLE |
| `1864-BB13437` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 57,054 | 35,574 | -37.6% | VARIABLE | VARIABLE |
| `1864-FF31075` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 17,093 | 38,556 | +125.6% | VARIABLE | FLAT |
| `12446-BB0234CAN` | LOBLAWS INC | Croston's | 66,000 | 45,264 | -31.4% | SPARSE | FLAT |
| `22008-BB25519` | VARIETY WHOLESALERS INC | OTB (zero) | 20,000 | 0 | -100.0% | SPARSE | ZERO |
| `1864-FF7372` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 30,080 | 10,176 | -66.2% | FLAT | FLAT |
| `1864-FF7120EC` | AMAZON.COM.KYDC,INC | Seasonal Baseli | 50,184 | 31,536 | -37.2% | FLAT | FLAT |
| `13640-BB34403` | BURLINGTON COAT FACTORY | Croston's | 26,000 | 7,530 | -71.0% | SPARSE | SPARSE |
| `1864-FF20206EC` | AMAZON.COM.KYDC,INC | Croston's | 30,450 | 12,228 | -59.8% | FLAT | VARIABLE |
| `18360-BB26923` | ROSS STORES INC - MERCHAN | OTB (zero) | 18,000 | 0 | -100.0% | SPARSE | ZERO |
| `1864-FF12508` | AMAZON.COM.KYDC,INC | Croston's | 63,025 | 45,096 | -28.4% | FLAT | VARIABLE |
| `1864-FF17831` | AMAZON.COM.KYDC,INC | Heuristic | 29,880 | 12,000 | -59.8% | BACK_LOADED | VARIABLE |
| `1864-FF10479EC` | AMAZON.COM.KYDC,INC | Croston's | 45,702 | 28,266 | -38.2% | FLAT | BACK_LOADED |
| `23011-BB33708PDQ` | WAL MART STORES | Seasonal Baseli | 43,316 | 26,004 | -40.0% | FLAT | BACK_LOADED |
| `1864-FF12302/24EC` | AMAZON.COM.KYDC,INC | Croston's | 49,800 | 32,640 | -34.5% | FLAT | FLAT |
| `22008-BB32892` | VARIETY WHOLESALERS INC | Sparse Intermit | 21,000 | 3,888 | -81.5% | BACK_LOADED | SPARSE |