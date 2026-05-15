# Layout Specifications

All decks use `pptxgenjs` with `LAYOUT_WIDE` (13.33 × 7.5 inches). Below is the canonical spec — change a dimension here and the whole deck restyles consistently.

## Color tokens

```javascript
const C = {
  navy:    "0B1E3A",   // Primary header / cover background
  white:   "FFFFFF",
  light:   "E2E8F0",   // Borders, dividers
  muted:   "8896B0",   // Secondary text
  slate:   "475569",   // Body text on light backgrounds
  text:    "1E293B",   // Primary body text
  red:     "DC2626",   // High risk / loss
  darkred: "991B1B",   // Critical risk
  lred:    "FEF2F2",   // Loss panel background
  amber:   "D97706",   // Medium risk / warning
  lamber:  "FFFBEB",
  green:   "15803D",   // On-track / gain
  lgreen:  "F0FDF4",   // Gain panel background
  teal:    "0D7490",   // YTD / coverage indicator
  lteal:   "EFF6FF",   // Light teal for budget bar
  // Side accents — used for accent strips, side labels, section dividers
  ff:      "7C3AED",   // FETCH (Pet) — purple
  bb:      "EA580C",   // BRAND BUZZ (People) — orange
};
```

## Typography

- **Display** (cover headlines, big numbers, titles): `Georgia`
- **Body** (labels, table cells, comments): `Calibri`

Font sizes match the table sizes exactly. Don't scale up "for readability" — it breaks the layout grid.

## Customer slide (the canonical layout)

This is the most important layout. Every variant uses it for customer deep-dive slides.

### Header strip (y: 0 → 0.88)
- Navy rectangle full width (13.33 × 0.88)
- Risk-color accent bar on left edge (0.28 × 0.88)
- Customer name: x=0.42, y=0.06, w=4.8, h=0.44, fontSize=20, Georgia, bold, white
- Side label tag (FETCH or BRAND BUZZ): x=0.42, y=0.50, w=4.8, h=0.20, fontSize=9, Calibri, bold, charSpacing=3, side accent color
- Top-line metrics box at right: x=5.5, y=0.10, w=4.1, h=0.68, fill=`0D2545` (dark navy), border=`1E4080`
  - "LY 2025" label + value (left half)
  - "FY 2026 Budget" label + value (right half)
  - Vertical divider at x=7.38
  - Budget vs LY % delta to the right at x=9.70, y=0.28
- Risk pill: x=11.65, y=0.20, w=1.55, h=0.48, filled with risk color

### Left panel (x: 0.30, w: 2.80, y: 1.00, h: 5.10)
Status panel with:
- Background: green-tinted `lgreen` if on-pace, red-tinted `lred` if behind
- Border: `green` or `red`
- Big miss number at top (fontSize=26, Georgia, bold)
- "Potential Budget Miss" label
- Coverage % (fontSize=22, Georgia, color=red/amber/green by threshold)
- Coverage progress bar (PANEL_W-0.4 × 0.09)
- Estimation method explainer box (`FFF7ED` / `FED7AA`, fontSize=7)
- 5-row metric stack: LY 2025 / FY 2026 Budget / YTD Shipped / Open Orders / 2026 Est. Sales
  - Each row: x=PANEL_X+0.1, w=PANEL_W-0.2, h=0.38, white fill, light border

### Brand table (x: 3.20, y: 1.00, w: 9.85)
- Column widths: `[2.0, 1.2, 1.35, 1.1, 0.9, 0.95, 1.2, 1.15]` (sums to 9.85)
- Headers: `BRAND | LY 2025 Actual | FY 2026 Budget | YTD 2026 Shipped | Open Orders | YTD+OO % Budget | 2026 Est. Sales | Potential Budget Miss`
- Header row height: 0.28
- Data row height: 0.15 (tight — fontSize 7.5)
- Total row height: 0.20
- Header style: bold, white on navy, fontSize=7, align=right (left for BRAND)
- Data row backgrounds:
  - `FFF1F2` (red-tint) if miss < −$300K
  - `FFF8F0` (amber-tint) if miss < −$30K
  - `F0FDF4` (green-tint) if miss > 0
  - `FFFFFF` otherwise
- Border: 0.4pt, color `E2E8F0`

**Entertainment DISC tag (added May 10, 2026):** brand rows where `brand_type == 'Entertainment'` append a small italic `*DISC*` suffix to the brand name in the same cell — informational tag, not a separate column. Numeric columns unchanged. See `build_salesperson_deck.js` line ~256:
```js
const brandLabel = r.disc ? (r.brand + '  *DISC*') : r.brand;
```

### Three-section callout (y: 2.93, just below the brand table)
Total width = 9.85, split into:
- **LOSS_W = 4.10** — top 3 budget misses (3 cards side-by-side)
- 0.05 gap
- **GAIN_W = 1.55** — top 1 ahead-of-budget brand
- 0.05 gap
- **PROG_W = 1.75** — programs/brand entries-exits list

Each section:
- Header bar: y=2.93, h=0.18, dark color (red/green/blue), white text fontSize=8 bold
- Content box: y=3.15, h=3.04
- Card title: h=0.32, wrap=true, fontSize=8.5 bold Georgia
- Big miss/gain number: y+0.42, h=0.18, fontSize=11 bold Georgia
- Divider line: y+0.62
- Comment text: y+0.67, fontSize=6.5, wrap=true, paraSpaceAfter=2

### Budget bar (y: 6.25, h: 1.10)

> **NOTE (May 10, 2026): removed from `build_salesperson_deck.js` only.** Per Steven, the bottom budget bar duplicated information shown in the left panel stat stack and the brand table TOTAL row. The salesperson builder now leaves clean whitespace below the three-section callout. The `build_combined_deck.js` and `build_side_deck.js` builders still render this bar — they were not modified in the May 10 update. Remove from those builders too for consistency the next time they're rebuilt.

Original spec (still applies to combined and side-split):

Full-width strip with:
- Background: `lteal` with `BFDBFE` border
- Header: "BV 0326 — COVERAGE & ESTIMATION" (left) + status text (right)
- Stacked progress bar at y=6.53, h=0.08, segments:
  - YTD Shipped: dark navy `0D4E6B`
  - Open Orders: teal
  - H2 Estimate: light blue `BAE6FD`
  - Budget Gap: light grey `E2E8F0`
- Legend chips above bar (4 dots + labels)
- 5 metric blocks below bar at y=6.69
  - LY 2025 / FY 2026 Budget / YTD+OO / 2026 Est / Potential Budget Miss
  - Each: w=2.4, h=0.36 for value (fontSize=11 Georgia bold), h=0.22 for label (fontSize=7 Calibri muted)

## Cover slide

Background: navy.

### Combined / Side-Split cover
- Top eyebrow: "Pets+People" at y=0.8, w=11, fontSize=11, color `6B9EFF`, charSpacing=4, bold
- Accent bar: x=0.6, y=1.3, w=0.16, h=1.1
- Big variant name (Fetch / Brand Buzz / Customer Deep Dives): x=0.85, y=1.25, w=8, h=1.2, fontSize=64, Georgia, bold
- Tagline: x=0.85, y=2.45, w=8, fontSize=14, italic, charSpacing=2, color `A5C0FF`
- Subtitle: "Customer Sales & Budget Analysis" at y=3.3, fontSize=28, Georgia, bold, white
- Detail line: "FY 2025 Actual vs FY 2026 Budget vs 2026 Estimated Sales" at y=3.9, fontSize=13, italic, color `A5C0FF`
- Customer count line at y=4.35, fontSize=10, italic, color `5570A0`
- Right column 4 metric boxes: x=9.5, w=3.6, h=1.2 each, vertical spacing 1.32, starting y=2.0
  - FY 2025 Actual / FY 2026 Budget / 2026 Estimated Sales / Potential Budget Miss
  - Box style: `0D2545` fill, color-coded border
- Footer at y=7.1: "Generated [month] [year] · Internal Use Only", fontSize=8.5, color `5570A0`

### Per-Salesperson cover
Same structure but:
- Salesperson name as the big headline (fontSize=54, bigger than variant name)
- Tagline reflects coverage: "Pet & People Combined Territory" / "Pet Products Territory" / "People Products Territory"
- Accent color: purple if FF-only, orange if BB-only, light blue `6B9EFF` if dual
- Customer count line shows section split: "X customer accounts ≥$100K · across Fetch & Brand Buzz"
- Metric boxes show *combined* portfolio (sum across both sides)
- Box height bumped to 1.35 for better balance with name size

## Section divider slide

Background: navy.

- Vertical accent bar: x=0.4, y=2.6, w=0.07, h=2.0, side accent color
- Big title (FETCH / BRAND BUZZ / Customer Deep Dives): x=0.62, y=2.55-2.65, w=12, h=1.0-1.1, fontSize=42-48, Georgia, bold, white
- Subtitle: x=0.62, y=3.78-3.85, w=12, h=0.5, fontSize=14, italic, side accent color
- Per-salesperson decks add a third line at y=4.45 with the salesperson name in `A5C0FF`

## Pagination rule

If a customer has more than 9 brands in their brand table, split the slide into "1 of 2" / "2 of 2":
- Slide 1: brands 0-8 (the 9 worst-miss)
- Slide 2: brands 9-17 (the next 9)

Page label appears in the customer name string in the header, e.g., `WAL MART STORES (1 of 2)`.

## Sort order conventions

- **Brand selection** for a customer: sort by `abs(miss)` DESCENDING, take top 8, roll remainder into "Other (N brands)" row
- **Brand display** in the table: sort by `miss` ASCENDING (most negative first, gains last)
- **Customer slide order** within a section: sort by total `miss` ASCENDING (worst-miss customers first)
- **Per-salesperson decks**: same sort within each side

## Risk classification

```python
def calc_risk(cov):
    if cov >= 90: return 'ON TRACK'
    if cov >= 60: return 'WATCH'
    if cov >= 35: return 'MEDIUM'
    if cov >= 20: return 'HIGH'
    return 'CRITICAL'
```

Color map:
- ON TRACK → green
- WATCH → teal
- MEDIUM → amber
- HIGH → red
- CRITICAL → dark red
- NO BUDGET → muted grey `94A3B8`
