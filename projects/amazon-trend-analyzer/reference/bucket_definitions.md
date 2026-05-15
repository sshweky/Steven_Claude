# Trend bucket definitions

Each ASIN gets a **composite trend score** at three windows:
**L4W vs L13W**, **L4W vs L26W**, and **L4W vs L52W**.

The score for each window is `L4W mean ÷ baseline mean` for a 50/50 blend of
Ordered Units and Ordered Revenue. We convert each score to a sign:

| Score range | Sign |
|---|---|
| > 1.05 | **+** (up >5%) |
| 0.95 – 1.05 | **0** (flat ±5%) |
| < 0.95 | **−** (down >5%) |

The **pattern of three signs** (vs L13, vs L26, vs L52) lookup-maps to a bucket.

## Buckets

### 🚀 Strong Winner — `strong_winner`
**Pattern:** `(+, +, +)` — accelerating across every time horizon.
Genuine sustained growth. Protect and scale. Make sure inventory keeps up.

### 🔥 Accelerating — `accelerating`
**Patterns:** `(+, +, 0)`, `(+, +, -)`
L4W is hotter than both L13W and L26W, but L52W is flat or slightly down — usually
means the ASIN is lapping a softer prior-year period. Hot but check whether the
comp is just easy.

### 🔄 Recovering — `recovering`
**Patterns:** `(+, 0, +)`, `(+, 0, 0)`, `(+, -, +)`
Recent uptick, mid-term flat or down, but long-term healthy. Either bouncing
back from a dip or genuinely cyclical — open the chart to tell which.

### ⚠️ Surge on Decline — `surge_on_decline`
**Patterns:** `(+, 0, -)`, `(+, -, -)`, `(+, -, 0)`
L4 is up but the trend at every longer horizon is down. Usually a promo blip,
a competitor stock-out, an OOS recovery, or one big atypical week. Look hard
before drawing conclusions.

### 😴 Stable — `stable`
**Patterns:** `(0, 0, 0)`, `(0, 1, 0)`, also catches anything not otherwise mapped.
Steady-state. Cash cow.

### 💤 Soft — `soft`
**Patterns:** `(0, -1, 0)`, `(0, 0, -1)`, `(-1, 0, 0)`
Slight softness without crisis. Watch but don't escalate.

### 🧊 Cooling Winner — `cooling_winner`
**Patterns:** `(0, 1, 1)`, `(0, 0, 1)`, `(-1, 1, 1)`, `(-1, 1, 0)`
Historically strong, lapping easy comps, but L4W has lost momentum. This is
the **watch list** — early warning before a real decline takes hold. Investigate
drivers: lost buy-box, content change, competitor entry?

### 🔄 Lapping Softness — `lapping_softness`
**Pattern:** `(-1, 1, -1)`
L4 down vs L13 (recent weakness) BUT up vs L26 (recovered from earlier valley)
AND L52 down. The trend isn't really moving — looks like a noisy lap of a
weird period. Low priority.

### 📉 New Decline — `new_decline`
**Patterns:** `(-1, 0, 1)`, `(-1, -1, 1)`
Recent break from a healthy long-term baseline. **Urgent** — something
changed in the last quarter. Open drivers: OOS? Lost buy-box? Price change?
Content edit?

### 💀 Sustained Decline — `sustained_decline`
**Patterns:** `(-, -, -)`, `(-, -, 0)`, `(-, 0, -)`
Falling across every horizon. Decide: fix the listing, kill the SKU, or
accept the decline as managed wind-down.

### — No data — `no_data`
Fewer than 13 weeks of history. Excluded from analysis until enough data
accumulates.

## Layered flags

These attach to any bucket and add nuance:

### 🔀 Mixed Signal — `mixed_signal`
Set when **Units and Revenue indices for L4 vs L13 go in opposite directions**
(units up, revenue down, or vice versa). Usually means ASP shifted significantly
— a price cut driving volume, a price increase squeezing volume, or mix shift
to/from a cheaper variant. Always worth investigating.

### 📊 Volatile — `volatile`
Set when **coefficient of variation on L13W weekly units > 0.30**.
Trend math is noisier on these — treat the bucket as a guideline rather than
a verdict, and look at the chart for context.

## Bucket counts in the dashboard summary tiles

| Tile | Buckets aggregated |
|---|---|
| **Growth revenue** | Strong Winner + Accelerating |
| **At-risk revenue** | Cooling Winner + New Decline + Sustained Decline |
| **Mixed-signal flag count** | any ASIN where mixed_signal = true |
| **Total revenue (L4W)** | Sum across all buckets including stable |

## Driver decomposition

When an ASIN is clicked in the table, the side panel runs `driver_decomp.decompose()`
and shows the **5 drivers** ranked by magnitude:

1. **Availability** — OOS days in L4W (mentioned if ≥ 1)
2. **Traffic** — glance views % change L4 vs L13 (exclusive)
3. **Conversion** — conversion rate % change
4. **Price** — average sales price % change
5. **Rank** — subcategory BSR shift (improvement = lower number)

A one-line narrative summarizes the primary driver(s). When the top two drivers
are within 50% magnitude of each other, both are mentioned.
