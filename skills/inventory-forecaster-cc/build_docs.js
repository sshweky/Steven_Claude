// Builds inventory_forecaster_methodology.docx — planner-facing reference doc
const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
  BorderStyle, WidthType, ShadingType, PageBreak, PageNumber,
  TableOfContents,
} = require('docx');

const FONT = "Calibri";
const PRIMARY = "1F4E79";   // header blue
const ACCENT  = "2E75B6";
const ROW_HDR = "D5E8F0";
const ROW_ALT = "F2F2F2";

const border = { style: BorderStyle.SINGLE, size: 6, color: "BFBFBF" };
const borders = { top: border, bottom: border, left: border, right: border };

const P = (text, opts = {}) =>
  new Paragraph({
    spacing: { after: 100 },
    ...opts,
    children: Array.isArray(text)
      ? text
      : [new TextRun({ text, font: FONT, size: 22, ...opts.run })],
  });

const H1 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_1,
    pageBreakBefore: true,
    spacing: { before: 240, after: 200 },
    children: [new TextRun({ text, font: FONT, size: 36, bold: true, color: PRIMARY })],
  });

const H2 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text, font: FONT, size: 28, bold: true, color: PRIMARY })],
  });

const H3 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 100 },
    children: [new TextRun({ text, font: FONT, size: 24, bold: true, color: ACCENT })],
  });

const BULLET = (text, level = 0) =>
  new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 60 },
    children: Array.isArray(text)
      ? text
      : [new TextRun({ text, font: FONT, size: 22 })],
  });

const NOTE = (label, text) =>
  new Paragraph({
    spacing: { before: 120, after: 120 },
    indent: { left: 360 },
    border: { left: { style: BorderStyle.SINGLE, size: 18, color: ACCENT, space: 8 } },
    children: [
      new TextRun({ text: label + ": ", font: FONT, size: 22, bold: true, color: ACCENT }),
      new TextRun({ text, font: FONT, size: 22 }),
    ],
  });

function table(headers, rows, colWidths) {
  const totalWidth = colWidths.reduce((a, b) => a + b, 0);
  const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders, width: { size: colWidths[i], type: WidthType.DXA },
      shading: { fill: PRIMARY, type: ShadingType.CLEAR },
      margins: cellMargins,
      children: [new Paragraph({
        children: [new TextRun({ text: h, font: FONT, size: 22, bold: true, color: "FFFFFF" })],
      })],
    })),
  });

  const dataRows = rows.map((row, rIdx) => new TableRow({
    children: row.map((cell, i) => new TableCell({
      borders, width: { size: colWidths[i], type: WidthType.DXA },
      shading: { fill: rIdx % 2 === 0 ? "FFFFFF" : ROW_ALT, type: ShadingType.CLEAR },
      margins: cellMargins,
      children: [new Paragraph({
        children: [new TextRun({ text: String(cell), font: FONT, size: 20 })],
      })],
    })),
  }));

  return new Table({
    width: { size: totalWidth, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [headerRow, ...dataRows],
  });
}

// ─── DOCUMENT CONTENT ─────────────────────────────────────────────────

const titlePage = [
  new Paragraph({ spacing: { before: 2400 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "AI Inventory Forecaster", font: FONT, size: 56, bold: true, color: PRIMARY })] }),
  new Paragraph({ spacing: { before: 120 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Methodology & Decision Reference", font: FONT, size: 36, color: ACCENT })] }),
  new Paragraph({ spacing: { before: 600 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Pets+People Inventory Planning", font: FONT, size: 26, italics: true })] }),
  new Paragraph({ spacing: { before: 2400 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Version: 2026-04-26", font: FONT, size: 20, color: "808080" })] }),
  new Paragraph({ alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Audience: Inventory Planners & Buyers", font: FONT, size: 20, color: "808080" })] }),
];

const intro = [
  H1("1. Introduction"),
  P("This document describes exactly how the AI inventory forecaster decides what quantity to project for every (account × mstyle) record over the next 26 weeks. It is written for inventory planners and buyers who want to understand—and challenge—the algorithm’s decisions on individual items."),
  P("The forecaster is rule-based and fully deterministic. It does not use machine learning or external API calls. Every decision can be traced back to a documented rule with a documented threshold."),
  H2("Inputs the model uses"),
  BULLET("52 weeks of order history (Quickbase Projections.Ord_LW_0 through Ord_LW_51)"),
  BULLET("52 weeks of shipped history (Quickbase Projections.Shp_LW_*)"),
  BULLET("Manual projections (Original_PRJ_Wk1..26) — used only for variance comparison, never as input"),
  BULLET("Master pack quantity (Quickbase Styles.Master_Pack) — used to round all forecasts"),
  BULLET("Customer name, item description, brand, product category, and Season tag"),
  BULLET("For Amazon items only: Amazon Catalog POS data (Avg_Units_Wk_L4w / L13w / L26w / L52w)"),
  H2("Output"),
  P("For each record, the forecaster produces a 26-element list of integer quantities (forecast for Wk 1 through Wk 26), each rounded to a multiple of the master pack."),
];

const pipeline = [
  H1("2. The Forecasting Pipeline"),
  P("Each record passes through a fixed sequence of phases. Every phase is logged in the forecast output so any decision can be audited."),
  H2("Phase 1 — Pull projection records"),
  P("Read all active records (Status_Cust LIKE 'A%') from Quickbase Projections, including 52 weeks of order and shipped history."),
  H2("Phase 2 — Pull master pack table"),
  P("Read Master_Pack from Quickbase Styles for every active mstyle. Default to 1 if missing."),
  H2("Phase 2.5 — Pull Amazon Catalog POS (Amazon items only)"),
  P("For records where customer name contains \"AMAZON\", pull L4W / L13W / L26W / L52W consumer POS averages from Quickbase Amazon_Catalog."),
  H2("Phase 3 — Forecast each record"),
  P("Classify each record by demand pattern, route it to the right model, then run all refinement rules. Snap final values to master pack multiples."),
  H2("Phase 4 — Write back to Quickbase (skipped on dry-run)"),
  P("Update AI_PRJ_W1..W26 and AI_ALERT fields. Records with > 5% variance vs the manual projection get an explanatory alert."),
];

const classification = [
  H1("3. Pattern Classification"),
  P("The first decision is which model to use. This depends on how often the item ordered in the last 26 weeks."),
  P("The system computes the non-zero rate (NZ rate) — the fraction of the last 26 weeks that had at least one order. It then routes to a model:"),
  table(
    ["NZ rate (last 26 wks)", "Active weeks", "Pattern", "Model used"],
    [
      ["≥ 0.50", "≥ 13 of 26", "Steady / dense", "Seasonal Baseline"],
      ["0.25 – 0.49", "7 – 12 of 26", "Intermittent", "Croston's"],
      ["< 0.25", "1 – 6 of 26", "Sparse intermittent", "Croston's (sparse mode)"],
      ["0", "0 of 26", "Inactive", "Forecast = 0 for all 26 weeks"],
      ["Insufficient history", "< 26 weeks data", "New item", "Heuristic"],
    ],
    [2160, 1500, 2400, 3300]
  ),
  NOTE("Why NZ rate, not total volume", "Two items with the same total annual volume need very different forecasting approaches if one orders weekly and the other orders three times a year. NZ rate captures cadence, which is what the choice of model depends on."),
];

const seasonalBaseline = [
  H1("4. Model — Seasonal Baseline (steady items)"),
  P("Used when an item orders most weeks (NZ rate ≥ 50%). The model assumes the recent run-rate is a reliable starting point, then applies seasonal shape and event lifts."),
  H2("Step 1 — Compute the order-history baseline"),
  P("Per-order weekly rate. The default is the L13W all-weeks average (zeros included). The model only switches to the L13W non-zero average when there is a confirmed, evidence-based reason that the zeros are not real demand signal — either a fulfillment gap (proxy for stockout) or a post-event drawdown pattern. Otherwise, light weeks are real and stay in the average. (Updated 2026-04-28 per VP-Q1.)"),
  table(
    ["Condition", "Baseline used", "Rationale"],
    [
      ["Fulfillment gap detected: L13W shipments / L13W orders < 85% over ≥ 50 total units",
       "L13W non-zero average",
       "Zeros likely reflect OOS / fill-rate issues, not soft demand. Filter them out so the baseline reflects what would have shipped if stock were available."],
      ["Post-event drawdown: ≥ 3 of last 4 weeks zero AND prior 9 weeks ≥ 60% non-zero active",
       "L13W non-zero average",
       "Customer drained inventory after an elevated period (Prime Day, holiday pre-buy, big promo). The recent zeros are drawdown, not new demand level."],
      ["Pulsed-ordering pattern: ≥ 4 zero weeks in L13W (rule F-A retained)",
       "L13W all-weeks average",
       "Account orders in chunks (Amazon pre-buy cycles, promo retailers). Non-zero average would be order size, not weekly rate."],
      ["Default — none of the above (e.g., 0–3 zero weeks with normal fill-rate)",
       "L13W all-weeks average",
       "Zeros are real demand signal — including them prevents systematic over-projection on steady customers with light weeks."],
      ["L13W has < 4 active weeks",
       "L26W non-zero average",
       "Sparse-window fallback. L13W signal is too thin to use directly."],
      ["L26W also has < 4 active weeks",
       "L13W all-weeks average",
       "Final fallback when neither window provides a usable signal."],
    ],
    [2700, 1860, 4800]
  ),
  NOTE("Why the change", "The earlier default — always using L13W non-zero average when ≥ 4 active weeks were available — systematically inflated the baseline for steady customers who had legitimate light weeks (no OOS, no event drawdown, just normal variance). Multiplying that inflated baseline by the seasonal profile and any event lifts then compounded the over-projection. The evidence-based gate keeps the original drawdown-protection benefit but defaults to including zeros when no real-world reason exists to exclude them."),
  NOTE("Auditability", "Every record now carries a baseline-mode driver line in its AI_ALERT (e.g., \"VP-Q1 baseline: L13 all-weeks avg (default: 2 legitimate zero weeks, fill-rate 98%)\"). Planners can spot-check any forecast and immediately see which mode fired and why."),
  H2("Step 2 — Outlier handling"),
  BULLET("Drop rule (F25): if a single value in L13W is greater than 5× the median AND there are ≥ 4 other non-zero weeks, drop that single outlier completely."),
  BULLET("Cap rule (Fix 3): if any value still exceeds 3× the median, cap it at 3× median. Same logic for L26W."),
  H2("Step 3 — Trend-aware adjustments"),
  P("The model compares the last 4 weeks (L4) to the last 13 weeks (L13) to detect acceleration or decline:"),
  table(
    ["L4 / L13 ratio", "Action", "Multiplier", "Rule"],
    [
      ["≤ 0.50", "Steep decline — recent demand has fallen sharply", "× 0.65", "F6"],
      ["0.50 – 0.70", "Mild decline — recent softening", "× 0.85", "F26"],
      ["0.70 – 1.30", "Stable — no adjustment", "× 1.00", "—"],
      ["1.30 – 1.60", "Mild ramp — recent acceleration", "× 1.10", "F27"],
      ["≥ 1.60", "Strong ramp — handled by ecom / Amazon POS rules below", "varies", "T4 / Amazon POS"],
    ],
    [1800, 4200, 1500, 1860]
  ),
  H2("Step 4 — Amazon POS blend (Amazon customers only)"),
  P("For records where customer name contains \"AMAZON\", the order-history baseline is blended 55/45 with the consumer POS rate from Amazon Catalog. The POS rate itself is trend-weighted:"),
  table(
    ["L4W POS / L13W POS ratio", "POS rate weighting"],
    [
      ["≥ 1.15 (accelerating)", "55% L4W + 30% L13W + 15% L26W"],
      ["0.85 – 1.14 (stable)", "25% L4W + 45% L13W + 20% L26W + 10% L52W"],
      ["< 0.85 (decelerating)", "35% L4W + 45% L13W + 20% L26W"],
    ],
    [3600, 5760]
  ),
  P("If the order-history baseline runs > 1.30× the POS rate (rule F15), the POS blend is dropped entirely — this protects items where the planner is sizing orders for safety stock above POS demand."),
  H2("Step 5 — E-commerce trend lift (Chewy / Petco.com / PetSmart.com)"),
  P("For e-commerce customers, when L4W non-zero average exceeds L13W non-zero average by ≥ 5%, the baseline is shifted toward L4W to capture acceleration that order history alone misses (rule T4)."),
  H2("Step 6 — Bi-weekly cadence enforcement"),
  P("If the system detects the item orders every other week (≥ 70% of one parity is zero in L26W), it enforces the every-other-week pattern: pairs are merged onto active weeks and off-weeks are zeroed out. The baseline is also switched to the all-weeks average to keep paired quantities correct."),
  H2("Step 7 — High-volume cap (F30)"),
  P("If the final baseline ≥ 1,000 units/week AND it exceeds L13W weekly average × 1.05, cap the baseline at L13W × 1.05. This protects against POS-blend amplification on items where planner discipline is already tight."),
  H2("Step 8 — Apply seasonal shape"),
  P("A 26-week seasonal profile is computed from L52W history (or category template — see Section 8). The raw profile is heavily damped: each value is pulled toward 1.0 by a damping factor."),
  table(
    ["Damping mode", "DAMP value", "When triggered"],
    [
      ["Standard", "0.10 (profile stays within ±20% of 1.0)", "Default for all items"],
      ["Relief (F16)", "0.40 (profile preserves ±60% range)", "Item has a known category profile OR raw L52 profile shows ≥ 2.5× peak-to-trough AND volume ≥ 50/week"],
    ],
    [1860, 1500, 6000]
  ),
  H2("Step 9 — Apply event lifts"),
  P("On top of the seasonal profile, explicit event multipliers are applied to specific forecast weeks for Amazon-only items:"),
  table(
    ["Event", "Forecast weeks", "Multiplier", "Notes"],
    [
      ["Prime Day pre-order", "W5 – W9", "W5: 1.10, W6: 1.15, W7: 1.25, W8: 1.25, W9: 1.20", "Tapered ramp; Amazon only"],
      ["Fall Deal pre-order", "W23 – W25", "1.12 flat", "Amazon only"],
    ],
    [2400, 1800, 3000, 2160]
  ),
  H2("Step 10 — Cap each forecast week"),
  BULLET("Normal weeks: cap at L13W average × 1.25"),
  BULLET("Event weeks (W5-9 or W23-25): cap at L13W average × 1.50"),
  H2("Step 11 — Snap to master pack"),
  P("Every non-zero week is rounded to the nearest multiple of Master_Pack. Zeros stay zero."),
];

const croston = [
  H1("5. Model — Croston's (intermittent items)"),
  P("Used when NZ rate is between 25% and 50% (roughly every 2-5 weeks). Croston's separately estimates the average order size (z) and the average interval between orders (p), then places forecast quantities at the cadence interval."),
  H2("Step 1 — Smoothing pass"),
  P("Walk a 78-observation weighted series (L13W appears 3× to overweight recent activity). For each non-zero observation:"),
  BULLET("z (size) = 0.30 × current value + 0.70 × prior z (CR_ALPHA = 0.30)"),
  BULLET("p (interval) = 0.30 × current interval + 0.70 × prior p"),
  H2("Step 2 — Refine z and p with L13W actuals"),
  P("Blend the smoothed values with L13W actuals. The default blend is 70% L13W actuals / 30% smoothed."),
  P("Acceleration override (M3): if L13W non-zero average is ≥ 5% higher than L26W non-zero average, the blend shifts to 90% L13W / 10% smoothed to capture the recent step-up."),
  H2("Step 3 — Post-spike drawdown guard"),
  P("If the item is still actively ordering AND L13W weekly rate < 65% of L26W weekly rate (signs of post-event drawdown), reset z so that z/p produces a target weekly rate of 60% L13W + 40% L26W."),
  H2("Step 4 — L26W volume floor"),
  P("Compute the implied weekly rate as z / p_final (where p_final = max(1, round(p))). If it's lower than the L26W weekly average — and L13W hasn't collapsed (≥ 50% of L26W) — lift z to match the L26W rate. This prevents systematic under-forecasting on items with valid historical volume."),
  H2("Step 5 — L13W volume floor (F28)"),
  P("Additionally, if the implied weekly rate < 90% of L13W weekly average AND L13 has ≥ 3 active weeks, lift z to match L13W weekly rate (capped at 1.5× original z to prevent runaway lifts)."),
  H2("Step 6 — Amazon POS uplift (F18)"),
  P("For Amazon items where POS L13W ≥ 50/week AND POS L4W ≥ 50% of POS L13W: if POS L4W > implied weekly rate, lift z so the new implied rate becomes 60% × (current implied) + 40% × POS L4W. The lift is capped at 1.5× original z."),
  H2("Step 7 — Place orders at cadence interval"),
  P("Starting at week 1, place a quantity of size z × event_boost × category_multiplier every p_final weeks. All other weeks remain zero. Each placed quantity is snapped to master pack."),
  H2("Step 8 — Rescale toward L13W all-weeks total"),
  P("If the 26-week forecast total exceeds L13W all-weeks weekly average × 1.10 × 26, scale down so it lands at that ceiling. Cap at 2× reduction to avoid over-correction. (Only scales down — under-projection is handled by the floors above.)"),
  H2("Step 9 — Event window coverage"),
  P("If Amazon AND no forecast falls in the Prime Day window (W5-W9), insert a forced order at W5 of size z × 1.25. Same logic for Fall Deal at W23 if no forecast in W23-W25."),
  H2("Step 10 — End-of-life dampener (F10)"),
  P("If L4W average drops to < 70% of L13W non-zero average AND year-ago L4W shows > 50% drop year-over-year, scale forecast down. Override (F14a): if Amazon POS is healthy, don't apply the EOL dampener."),
];

const heuristic = [
  H1("6. Model — Heuristic (sparse / new items)"),
  P("Used when an item has very thin history (< 13 active weeks total or classified as new). The heuristic builds a simple flat-shape forecast using the most defensible recent average it can find."),
  H2("Step 1 — Choose a baseline"),
  P("Picks the most recent reliable signal in this priority order:"),
  BULLET("Post-launch average (excluding the first 6 ramp weeks if the item is newer than 26 weeks)"),
  BULLET("L13W non-zero average"),
  BULLET("L52W non-zero average"),
  BULLET("Customer / mstyle peer median (when no usable history at all)"),
  H2("Step 2 — F9 high-volume MAX rule"),
  P("If L52W total > 15,000 units, use the MAX of L13W / L26W / L52W non-zero averages. This protects high-volume sparse items whose L13W happens to fall in a quiet period."),
  H2("Step 3 — Trailing-zero discount (F23b)"),
  P("Count consecutive zero weeks from the most recent week backwards. Multiply baseline by (1 − zeros/13), floored at 0.30. So 13+ trailing zeros = 70% discount; 6 trailing zeros = 46% discount; 0 trailing zeros = no discount."),
  H2("Step 4 — Apply dampened seasonal profile"),
  P("Same DAMP=0.10 dampening as Seasonal Baseline. Category profile blended in if the description matches."),
  H2("Step 5 — Apply event lifts"),
  P("Same Prime Day (W5-W9) and Fall Deal (W23-W25) lifts as Seasonal Baseline (Amazon only)."),
  H2("Step 6 — F29 new-item L4 floor"),
  P("If pattern is new_item or sparse AND the last 8 weeks contain ≥ 1 active week: compute floor weekly rate = (L8 non-zero average × activity rate). If forecast weekly rate < 0.70× this floor, scale up to match. Lift capped at 2× to avoid over-correction."),
];

const holtWinters = [
  H1("7. Model — Holt-Winters (legacy steady items)"),
  P("Holt-Winters exponential smoothing is a legacy code path retained for high-CV steady items where Seasonal Baseline's flat-rate assumption may miss a real underlying trend. In current production it's rarely the primary model — Seasonal Baseline + dampened profile handles most cases."),
  H2("How it works (when used)"),
  P("Recursive level + trend smoothing over the 78-observation weighted series:"),
  BULLET("Level: L_new = 0.30 × y + 0.70 × (L + T)"),
  BULLET("Trend: T_new = 0.10 × (L_new − L) + 0.90 × T"),
  P("Forecasts are the 26 values of L + T × h, multiplied by the seasonal profile and capped per-week."),
];

const inactive = [
  H1("8. Inactive Items"),
  P("If an item has zero orders in the last 13 weeks, its forecast is 0 for all 26 weeks by default. This is the correct behavior for retired items."),
  H2("Conservative inactive floor (opt-in)"),
  P("When run with --conservative-inactive, items classified Inactive that meet ALL of these conditions get a 50%-of-manual floor instead of zero:"),
  BULLET("Manual projection total ≥ 5,000 units"),
  BULLET("Amazon POS L52W > 0"),
  BULLET("Floor is capped at POS L52W × 26 weeks"),
];

const events = [
  H1("9. Event Calendar"),
  P("Two retail events drive forecast lifts. Both apply only to Amazon customers."),
  H2("Prime Day"),
  table(
    ["Forecast Week", "Multiplier"],
    [["W5", "1.10"], ["W6", "1.15"], ["W7", "1.25"], ["W8", "1.25"], ["W9", "1.20"]],
    [3000, 3000]
  ),
  P("Why W5-9? Prime Day occurs in mid-July. Amazon places pre-buy orders ~6-8 weeks before the consumer event, so the order surge lands in our forecast at W5-W9 (May)."),
  H2("Fall Deal"),
  table(
    ["Forecast Week", "Multiplier"],
    [["W23", "1.12"], ["W24", "1.12"], ["W25", "1.12"]],
    [3000, 3000]
  ),
  P("Fall Deal pre-order is early September; orders lift at W23-25."),
  H2("Forced event-window coverage"),
  P("For Croston's items: if no forecast naturally falls in the event window, the system inserts a forced order at the first week of the window (W5 for Prime Day, W23 for Fall Deal)."),
];

const customerCohorts = [
  H1("10. Customer Cohort Handling"),
  P("Different customer types behave differently. The model detects the cohort from the customer name and applies cohort-specific logic."),
  H2("Amazon (substring \"AMAZON\")"),
  BULLET("Prime Day & Fall Deal event lifts apply"),
  BULLET("POS data blended into baseline (55/45 default, trend-weighted)"),
  BULLET("Croston's z gets POS uplift (F18) when POS L13W ≥ 50/week"),
  H2("E-commerce (Chewy / Petco.com / PetSmart.com)"),
  BULLET("T4 trend lift: when L4W average ≥ 5% above L13W, baseline shifts toward L4W to capture late-cycle acceleration"),
  H2("Off-price (Burlington, Ross, TJ Maxx, Marshalls, Kohl's, Sam's Club, Variety, Ollie's, Big Lots, Five Below, FragranceNet)"),
  BULLET("Treated as one-time-buy retailers — special pattern detection in classify()"),
  H2("International (Petbarn, Loblaws, Mexico distributors)"),
  BULLET("Treated separately; tend to have lumpy cadence"),
  H2("Amazon Private Label (substring \"AMAZON PRIVATE LABEL\")"),
  BULLET("Skipped entirely — not forecast (rule R4)"),
];

const categorySeasonality = [
  H1("11. Category & Seasonal Profiles"),
  P("Some products have known seasonal patterns that may not be captured in short order histories. The system applies category-specific monthly demand profiles."),
  H2("Source 1 — Explicit Season tag (highest priority)"),
  P("If Quickbase Styles has a Season tag for the mstyle, that profile is used directly. Available tags:"),
  table(
    ["Season tag", "Peak retail-order month(s)"],
    [
      ["Holiday (Thanksgiving/Christmas)", "Aug – Nov (peak Oct-Nov)"],
      ["Halloween", "Jul – Sep (peak Aug)"],
      ["July 4th", "Apr – Jun (peak May)"],
      ["Easter", "Jan – Mar (peak Feb-Mar)"],
      ["Valentines Day", "Nov – Jan (peak Dec)"],
      ["St Patrick's Day", "Dec – Feb (peak Jan)"],
      ["Pride", "Mar – May (peak Apr)"],
      ["Spring/Summer", "Feb – Jun"],
      ["Fall/Winter", "Aug – Dec"],
    ],
    [3000, 6060]
  ),
  H2("Source 2 — Description / brand keyword match (fallback)"),
  P("If no Season tag, the description is matched against a keyword library covering charcoal, mosquito repellent, sunscreen, paper plates, ice melt, etc."),
  H2("How profiles are blended"),
  P("Category profile blends with raw historical profile at 70% category / 30% historical (the F1 rule). Final profile is always renormalized so the mean stays at 1.0."),
];

const refinementRules = [
  H1("12. Refinement Rules — Quick Reference"),
  P("The forecaster includes 30+ named refinement rules built up from prior production tuning. Each is documented inline in the code with its rule tag (F1 - F33, R-, T-, M-). This section summarizes the most commonly-firing ones for planner reference."),
  table(
    ["Tag", "What it does", "Threshold / parameter"],
    [
      ["Fix 3", "Cap any L13W value at 3× the median to prevent spike inflation", "max > 3× median"],
      ["F4", "Window-widen to L52W when L13W has ≤ 4 active weeks but L52W has ≥ 8", "L13 ≤ 4 nz AND L52 ≥ 8 nz"],
      ["F6", "Decay scale 0.65× when recent demand collapses", "L4/L13 ≤ 0.50"],
      ["F7", "Re-anchor baseline to historical peak when entering peak season with a known category profile", "Peak season detected"],
      ["F9", "Use MAX of L13/L26/L52 non-zero avgs for high-volume sparse items", "L52 total > 15,000"],
      ["F10", "End-of-life dampen on Croston (YoY-gated)", "L4 < 70% of L13 nz AND L4 < 50% of L4-yr-ago"],
      ["F14a", "Cancel F10 EOL dampening when Amazon POS is healthy", "POS L13 ≥ 50/wk AND POS L4 ≥ 50% of POS L13"],
      ["F15", "Drop Amazon POS blend when order-coverage premium > 1.30×", "ord_baseline / pos_rate > 1.30"],
      ["F16", "Loosen seasonal damping (DAMP=0.4) for items with category profile or strong raw seasonality", "category profile OR ≥ 2.5× peak-to-trough AND ≥ 50/wk"],
      ["F18", "Lift Croston z toward POS L4W rate", "POS L13 ≥ 50/wk AND POS L4W > implied wk rate"],
      ["F22a", "Trailing-zero drawdown discount (Seasonal Baseline)", "Up to 0.20× at 13+ trailing zeros"],
      ["F22c", "Final-baseline ceiling for sparse-L13 items", "≤ 6 nz weeks → cap at L13_avg × 1.50"],
      ["F23a", "Heuristic seasonal-profile damping (DAMP=0.10)", "Always for Heuristic"],
      ["F23b", "Trailing-zero discount (Heuristic)", "Up to 0.30× floor at 13+ trailing zeros"],
      ["F25", "Drop the single max in L13W when it's > 5× median", "max > 5× median AND ≥ 5 other nz weeks"],
      ["F26", "Mild-zone decay × 0.85", "L4/L13 in 0.50 – 0.70"],
      ["F27", "Mild-zone ramp × 1.10", "L4/L13 in 1.30 – 1.60"],
      ["F28", "Croston volume floor against L13W weekly rate", "z/p < 0.90 × L13_weekly AND ≥ 3 nz weeks"],
      ["F29", "New-item L4-or-L8 floor", "L8 has ≥ 1 nz week AND fcst < 0.70× floor"],
      ["F30", "HIGH-vol Seasonal Baseline cap", "baseline ≥ 1,000/wk AND > L13_avg × 1.05"],
      ["F31", "Front-week (W1) tail cap at 1.30× max(L4 avg, L13 avg, baseline)", "Always evaluated"],
      ["F32", "Sparse-intermittent per-week clamp + tiny-signal flatline", "L13_avg × 5.0 per-week cap; flatline when L26 sum < 26"],
      ["M2", "End-of-life status-token dampen (cuts forecast to max(AI×30%, manual))", "Status_Cust contains DISC/DEL/LIQ/END/OBSOLETE/PHASE/SUNSET, OR no orders in 26+ weeks"],
      ["M3", "Croston acceleration-aware z blend", "L13_nz_avg ≥ 1.05 × L26_nz_avg → 90/10 instead of 70/30"],
      ["R7", "Fall Deal lift inserted Amazon-only", "Always for Amazon"],
      ["R8", "Burst-interleaved-with-zeros median anchor", "Top 2 of L13_nz ≥ 70% of L13_nz total AND ≥ 5 nz weeks"],
      ["T4", "E-commerce trend lift (Chewy/Petco/PetSmart)", "L4 ≥ 1.05 × L13"],
    ],
    [820, 5400, 3140]
  ),
];

const validation = [
  H1("13. Validation Mode"),
  P("Validation mode (--validate) checks the buyer's manual projections against historical patterns instead of generating new forecasts. It writes no data back."),
  H2("How it works"),
  P("For each of the 26 manual projection weeks:"),
  BULLET("Compute expected center = baseline × seasonal × event_lift"),
  BULLET("Compute expected band: low = center × 0.30, high = center × 2.00"),
  BULLET("Flag the manual projection if it falls outside this band"),
  H2("Flag types and thresholds"),
  table(
    ["Flag", "Severity", "Trigger"],
    [
      ["massive_spike", "CRITICAL", "Manual > expected × 5.00 (default VALID_SPIKE_MULT)"],
      ["overshoot", "WARNING", "Manual > expected × 2.00 (default VALID_HIGH_MULT)"],
      ["undershoot", "WARNING", "Manual < expected × 0.30 (default VALID_LOW_MULT)"],
      ["sudden_stop", "WARNING", "Manual = 0 in a week where the item is otherwise active"],
      ["biweekly_off_week", "WARNING", "Manual > 0 in an off-week of a biweekly cadence"],
      ["not_master_pack_multiple", "WARNING", "Manual > 0 but not a multiple of Master_Pack"],
      ["inactive_item_with_demand", "CRITICAL", "Item is inactive but manual projects demand"],
    ],
    [3000, 1500, 4560]
  ),
  P("Thresholds are configurable via --threshold (sets VALID_HIGH_MULT)."),
];

const alerts = [
  H1("14. Forecast Alerts (AI_ALERT)"),
  P("After the forecast is computed, the system generates a human-readable alert message when the AI total differs materially from the manual projection."),
  H2("Alert trigger"),
  BULLET("Variance > 5% (ALERT_THRESHOLD = 0.05) between AI total and manual total, OR"),
  BULLET("Item is classified Inactive but manual projection > 0"),
  H2("Alert content"),
  P("Each alert includes:"),
  BULLET("Direction (AI projects more / less than manual) and percent difference"),
  BULLET("Model used (Seasonal Baseline / Croston / Heuristic / Inactive)"),
  BULLET("Key drivers (which refinement rules fired and what they did)"),
  BULLET("Specific weeks where AI and manual diverge most"),
  BULLET("Plain-English risk statement (out-of-stock vs. overstock)"),
];

const glossary = [
  H1("15. Glossary"),
  table(
    ["Term", "Definition"],
    [
      ["L4W / L13W / L26W / L52W", "Last 4 / 13 / 26 / 52 weeks of order history"],
      ["NZ rate (non-zero rate)", "Fraction of weeks in a window that had ≥ 1 order"],
      ["Master pack", "Case-pack quantity from Quickbase Styles. All forecasts are rounded to multiples of this."],
      ["Baseline", "The per-week order volume used as the model's starting point before applying seasonal and event lifts"],
      ["Seasonal profile", "26-week vector of multipliers (mean = 1.0) that captures historical seasonal shape"],
      ["DAMP", "Damping factor that pulls each seasonal profile value toward 1.0. DAMP=0.10 → ±20% range; DAMP=0.40 → ±60% range"],
      ["z (Croston's)", "Smoothed estimate of average order size when an order occurs"],
      ["p (Croston's)", "Smoothed estimate of average interval (in weeks) between orders"],
      ["ISO (Initial Stock Order)", "Single large order at item launch, detected as 4× the running median; stripped from baseline calculation so it doesn't inflate forecasts"],
      ["Event lift", "Multiplier applied to specific forecast weeks for known retail events (Prime Day, Fall Deal)"],
      ["Cap base", "Per-record ceiling computed from L13W avg; per-week forecast can't exceed cap_base × 1.25 (or × 1.50 in event weeks)"],
      ["Bi-weekly cadence", "Item that orders every other week, detected when ≥ 70% of one parity (even or odd weeks) is zero in L26W"],
    ],
    [2400, 6660]
  ),
];

const closing = [
  H1("16. Auditing a Specific Forecast"),
  P("Every forecast record's output JSON includes a drivers array that lists which rules fired and what they did. To audit a specific record:"),
  BULLET("Open the validation viewer at http://127.0.0.1:8765 after running --validate"),
  BULLET("Find the record by Acct_MStyle_Key_"),
  BULLET("The detail panel shows: pattern classification, model used, baseline source, all 52 weeks of order and shipped history, the AI forecast, and the narrative explanation"),
  BULLET("If the AI total differs from manual by > 5%, the AI_ALERT field will spell out the reasoning in plain English"),
  P("If a forecast looks wrong: check the L13 history first (often the issue is a single anomalous week or a recent ramp the model captured but the buyer hadn't noticed). If you believe the model is structurally wrong on this record, document the case with a screenshot and the actual realized demand once it comes in — these become the basis for future rule adjustments."),
  H2("Where to find this code"),
  P("All logic lives in scripts/inventory_forecaster.py within the Pets+People inventory-forecaster-cc skill. Each rule is tagged with its identifier (F1, F25, R8, etc.) in code comments and can be searched directly."),
];

// ─── ASSEMBLE ─────────────────────────────────────────────────────────

const doc = new Document({
  creator: "Pets+People Inventory Planning",
  title: "AI Inventory Forecaster Methodology",
  description: "Methodology and decision reference for inventory planners",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: FONT, color: PRIMARY },
        paragraph: { spacing: { before: 240, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: FONT, color: PRIMARY },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: FONT, color: ACCENT },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
          { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
        ],
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        alignment: AlignmentType.RIGHT,
        children: [new TextRun({ text: "AI Inventory Forecaster — Methodology", font: FONT, size: 18, color: "808080" })],
      })] }),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [
          new TextRun({ text: "Page ", font: FONT, size: 18, color: "808080" }),
          new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: "808080" }),
          new TextRun({ text: " of ", font: FONT, size: 18, color: "808080" }),
          new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 18, color: "808080" }),
        ],
      })] }),
    },
    children: [
      ...titlePage,
      ...intro,
      ...pipeline,
      ...classification,
      ...seasonalBaseline,
      ...croston,
      ...heuristic,
      ...holtWinters,
      ...inactive,
      ...events,
      ...customerCohorts,
      ...categorySeasonality,
      ...refinementRules,
      ...validation,
      ...alerts,
      ...glossary,
      ...closing,
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("inventory_forecaster_methodology.docx", buf);
  console.log("Wrote inventory_forecaster_methodology.docx (" + buf.length + " bytes)");
});
