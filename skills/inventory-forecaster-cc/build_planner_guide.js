// Build the Inventory Forecaster Planner Guide as a polished .docx.
// Run: node build_planner_guide.js
//
// This produces "Inventory_Forecaster_Planner_Guide.docx" — a planner-facing
// reference covering decision logic, demand pattern classification, the three
// forecasting models, order cadence detection, baseline/seasonality math, and
// the rules that drive accept-vs-override calls.

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, PageOrientation, LevelFormat,
  HeadingLevel, BorderStyle, WidthType, ShadingType, PageBreak,
  TabStopType, TabStopPosition, PageNumber, ExternalHyperlink,
} = require(path.join("C:/Users/steven/AppData/Roaming/npm/node_modules/docx"));

// ── Brand colors ──────────────────────────────────────────────────────────
const NAVY   = "1F3864";   // headings
const ACCENT = "2E75B6";   // section dividers
const SUBTLE = "595959";   // body grey
const LIGHT  = "F2F2F2";   // table header fill
const ALERT  = "C00000";   // critical callouts

// ── Reusable builders ────────────────────────────────────────────────────
const FONT = "Calibri";

function p(text, opts = {}) {
  return new Paragraph({
    spacing: { before: opts.before ?? 80, after: opts.after ?? 80, line: 300 },
    alignment: opts.align ?? AlignmentType.LEFT,
    children: [new TextRun({
      text, font: FONT, size: opts.size ?? 22,
      color: opts.color ?? "000000",
      bold: opts.bold ?? false,
      italics: opts.italic ?? false,
    })],
  });
}

function rich(runs, opts = {}) {
  return new Paragraph({
    spacing: { before: opts.before ?? 80, after: opts.after ?? 80, line: 300 },
    alignment: opts.align ?? AlignmentType.LEFT,
    children: runs.map(r => new TextRun({
      text: r.text, font: FONT, size: r.size ?? 22,
      bold: r.bold ?? false, italics: r.italic ?? false,
      color: r.color ?? "000000",
    })),
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 320, after: 140 },
    children: [new TextRun({ text, font: FONT, size: 36, bold: true, color: NAVY })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text, font: FONT, size: 28, bold: true, color: NAVY })],
  });
}
function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 180, after: 80 },
    children: [new TextRun({ text, font: FONT, size: 24, bold: true, color: ACCENT })],
  });
}

function bullet(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { before: 40, after: 40, line: 280 },
    children: [new TextRun({ text, font: FONT, size: 22 })],
  });
}
function bulletRich(runs, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { before: 40, after: 40, line: 280 },
    children: runs.map(r => new TextRun({
      text: r.text, font: FONT, size: 22,
      bold: r.bold ?? false, italics: r.italic ?? false,
      color: r.color ?? "000000",
    })),
  });
}

function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

// Bordered, full-width table with a styled header row.
const cellBorder = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
const cellBorders = { top: cellBorder, bottom: cellBorder, left: cellBorder, right: cellBorder };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

function tableCell(text, opts = {}) {
  return new TableCell({
    borders: cellBorders,
    width: { size: opts.width, type: WidthType.DXA },
    margins: cellMargins,
    shading: opts.shade
      ? { fill: opts.shade, type: ShadingType.CLEAR }
      : undefined,
    verticalAlign: VerticalAlignTopValue,
    children: [new Paragraph({
      spacing: { before: 0, after: 0 },
      alignment: opts.align ?? AlignmentType.LEFT,
      children: [new TextRun({
        text, font: FONT, size: opts.size ?? 20,
        bold: opts.bold ?? false, color: opts.color ?? "000000",
      })],
    })],
  });
}
const VerticalAlignTopValue = "top";

function buildTable(rows, columnWidths) {
  const totalWidth = columnWidths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: totalWidth, type: WidthType.DXA },
    columnWidths,
    rows: rows.map((row, i) => new TableRow({
      tableHeader: i === 0,
      children: row.map((c, j) => tableCell(
        typeof c === "string" ? c : c.text,
        {
          width: columnWidths[j],
          shade: i === 0 ? LIGHT : undefined,
          bold: i === 0 || (typeof c === "object" && c.bold),
          color: typeof c === "object" && c.color ? c.color : undefined,
          align: typeof c === "object" && c.align ? c.align : undefined,
        }
      )),
    })),
  });
}

// ── Document content ─────────────────────────────────────────────────────

const today = new Date();
const dateStr = today.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });

const TOTAL_WIDTH = 9360; // US Letter, 1" margins
const TWO_COL = [3120, 6240];
const THREE_COL = [2400, 3600, 3360];
const FOUR_COL = [1800, 2300, 2700, 2560];

const children = [];

// ── Cover ─────────────────────────────────────────────────────────────────
children.push(
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1800, after: 240 },
    children: [new TextRun({
      text: "Inventory Forecaster",
      font: FONT, size: 56, bold: true, color: NAVY,
    })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 120 },
    children: [new TextRun({
      text: "Planner Guide",
      font: FONT, size: 40, bold: true, color: ACCENT,
    })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 80, after: 600 },
    children: [new TextRun({
      text: "Decision Logic, Models & Order-Cadence Rules",
      font: FONT, size: 26, italics: true, color: SUBTLE,
    })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    border: {
      bottom: { style: BorderStyle.SINGLE, size: 12, color: ACCENT, space: 6 },
    },
    spacing: { before: 0, after: 200 },
    children: [new TextRun({ text: "", font: FONT, size: 4 })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({
      text: "Pets+People  •  Inventory Planning Team",
      font: FONT, size: 24, color: SUBTLE,
    })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 0 },
    children: [new TextRun({
      text: dateStr,
      font: FONT, size: 22, color: SUBTLE,
    })],
  }),
  pageBreak(),
);

// ── Section 1: How to read this guide ─────────────────────────────────────
children.push(
  h1("1.  How to Read This Guide"),
  p("This guide explains, in plain language, what the Inventory Forecaster does behind the scenes when it produces a 26-week projection for one of your accounts. It is written for inventory planners who use the AI Forecast values in Quickbase or in the local viewer — not for engineers."),
  p("The forecaster does not invent demand. Every number it writes back to Quickbase is the output of one of three transparent statistical models, conditioned on real order history, master-pack rules, and a small set of well-defined event windows. Wherever the AI value differs from your manual projection by more than 5%, the model also writes a short narrative explaining why."),
  rich([
    { text: "Three things to know up front:", bold: true },
  ]),
  bullet("The AI never overrides your manual plan automatically. It writes to a separate set of columns (AI_PRJ_W1..W26 + AI Analysis). You decide whether to accept."),
  bullet("All inputs come straight from Quickbase. Order history (52 weeks), master-pack, item status, customer, brand, inventory manager, and Amazon POS where available."),
  bullet("Every record gets exactly one model. The choice is deterministic, based on the demand pattern of that record's L13W (last-13-week) order history."),
);

// ── Section 2: The three models ───────────────────────────────────────────
children.push(
  h1("2.  Forecasting Patterns: The Three Models"),
  p("Before forecasting any record, the engine classifies its demand pattern by looking at L13W order history. The classification picks the model and the model never changes mid-record."),
  h2("2.1  Pattern classification (the decision tree)"),
  buildTable([
    ["Pattern", "Trigger condition (over L13W)", "Model used"],
    ["Inactive",     "Zero orders in L13W",                                   "All-zero forecast"],
    ["Sparse",      "Fewer than 13 active weeks (i.e. brand-new SKU or just relaunched)", "Heuristic"],
    ["Intermittent", "CV > 0.5 OR more than 20% of L13W weeks are zero",      "Croston's"],
    ["Steady",       "CV ≤ 0.5 AND zero-week share ≤ 20%",                    "Holt-Winters"],
  ], FOUR_COL),
  rich([
    { text: "CV ", bold: true },
    { text: "(coefficient of variation) is the standard deviation of weekly orders divided by the mean. It is a single number that tells the model whether order quantities are tightly clustered (low CV) or all over the place (high CV)." },
  ], { before: 120 }),

  h2("2.2  Holt-Winters — the workhorse for steady demand"),
  p("Used when L13W shows consistent ordering: most weeks have non-zero quantities and weekly volume does not swing wildly."),
  bulletRich([{ text: "Smoothing parameters: ", bold: true }, { text: "α=0.3 for level, β=0.1 for trend. Recent observations weigh more, but trends do not whipsaw on one outlier." }]),
  bulletRich([{ text: "L13W gets 3× influence. ", bold: true }, { text: "The model is fit on a weighted 78-observation series (52 + 13 + 13) so the latest cycle dominates. This is the single biggest reason the AI tracks recent shifts faster than a vanilla moving average." }]),
  bulletRich([{ text: "Seasonal factors are blended. ", bold: true }, { text: "70% recent cycle / 30% prior cycle, normalized to mean 1.0, floored at 0.25 to prevent any week from dropping below 25% of the seasonal baseline." }]),
  bulletRich([{ text: "Capped against runaway upside. ", bold: true }, { text: "Output cannot exceed L13W average × 1.25 in normal weeks, or × 1.50 in event weeks (Prime Day / Fall Deal). The cap is one-sided — the model can pull forecasts down freely, but cannot inflate them past these multiples without explicit event lift." }]),

  h2("2.3  Croston's — the lumpy/intermittent specialist"),
  p("Used when ordering happens in bursts: 7 of 13 weeks active with the rest at zero, or weekly quantities that swing 5×."),
  bulletRich([{ text: "Two parallel smoothings: ", bold: true }, { text: "z (average non-zero order size) and p (average gap between orders). Both updated with α=0.3 across the 78-observation weighted series." }]),
  bulletRich([{ text: "Refined with a recency anchor: ", bold: true }, { text: "z and p are pulled 70% toward the L13W observed values and 30% toward the smoothed model output. This prevents Croston's from over-projecting flat lines when the recent pattern is clearly slowing." }]),
  bulletRich([{ text: "Order quantities are scaled by L52W seasonal profile. ", bold: true }, { text: "A burst landing on a high-demand week is sized larger than a burst landing on a quiet week." }]),
  bulletRich([{ text: "Event insertions: ", bold: true }, { text: "If Prime Day weeks (W7-W9, Amazon only) or Fall Deal weeks (W23-W25) fall on a predicted gap, an order is forced in at the event-lifted quantity." }]),

  h2("2.4  Heuristic — for items with too little history"),
  p("Used when the SKU has fewer than 13 weeks of active orders (new launch, returned-to-line item, or recently transitioned from another customer)."),
  bullet("Ramp weeks 1-6 post-launch are excluded from the baseline — these are usually distorted by initial fill."),
  bullet("Baseline preference order: post-ramp non-zero average → L13W non-zero average → L52W average → category-default fallback."),
  bullet("Same seasonal profile and event lifts as Holt-Winters, applied on top of the baseline."),

  h2("2.5  Inactive treatment"),
  p("If L13W is all zero, the forecast is all zero — no exceptions, no extrapolation. The narrative will note 'inactive item' so a planner can confirm before accepting. If you have a manual projection for an inactive record, the AI Analysis will flag the disagreement explicitly."),

  pageBreak(),
);

// ── Section 3: Baseline ───────────────────────────────────────────────────
children.push(
  h1("3.  Baselines: How the Model Picks Its Anchor"),
  p("The baseline is the per-week demand floor before seasonality, events, or trend are applied. Getting this number right is the single most important step — every downstream multiplier is applied to it."),
  h2("3.1  Order-history baseline (default)"),
  rich([
    { text: "L13W non-zero average. ", bold: true },
    { text: "We sum the order quantities only on weeks where the customer actually placed an order, then divide by the count of those weeks. This gives the true per-order rate, not a diluted average that includes drawdown-zeros from post-event quiet periods." },
  ]),
  p("Fallback chain when L13W is too sparse:"),
  bullet("L26W non-zero average"),
  bullet("L13W all-weeks average (only when no non-zero weeks exist in 26W)"),
  bullet("Category default (Heuristic only)"),
  rich([
    { text: "Why non-zero average? ", bold: true },
    { text: "Many SKUs run a buy-then-burn-down pattern: customer places a large order, then sits idle for 4-8 weeks. An all-weeks average dilutes the true reorder size by counting those idle weeks. The non-zero average reflects what the customer actually buys when they buy." },
  ], { before: 100 }),

  h2("3.2  Amazon POS blend (Amazon customers only)"),
  p("When the customer name contains 'AMAZON', the engine pulls consumer point-of-sale (POS) data from the Amazon Catalog table and blends it into the baseline:"),
  rich([
    { text: "  baseline  =  order-history baseline × 0.55  +  POS rate × 0.45",
      italic: true, color: ACCENT },
  ], { before: 100, after: 100 }),
  p("The POS rate itself is a trend-aware blend of L4W, L13W, L26W and L52W consumer demand, weighted by what direction the trend is moving:"),
  buildTable([
    ["Trend (L4W vs L13W)", "POS rate weights", "What it means"],
    ["L4W ≥ L13W × 1.15",  "L4 ×0.55  L13 ×0.30  L26 ×0.15",                "Accelerating — recent demand surging"],
    ["L4W ≤ L13W × 0.85",  "L4 ×0.35  L13 ×0.45  L26 ×0.20",                "Decelerating — pulling weight back to L13"],
    ["Otherwise (stable)", "L4 ×0.25  L13 ×0.45  L26 ×0.20  L52 ×0.10",     "Stable — broad-based blend, anchor on L13"],
  ], THREE_COL),
  p("If the AI forecast comes in 10%+ above the order-history rate while POS is also above order pace, the narrative will say so explicitly — that's the 'POS exceeds order pace' callout you'll see in the AI Analysis field. It usually means the customer is about to place a catch-up order.", { before: 100 }),

  pageBreak(),
);

// ── Section 4: Seasonality & Events ───────────────────────────────────────
children.push(
  h1("4.  Seasonality, Events & the Damp Profile"),
  h2("4.1  Why we damp the seasonal profile"),
  p("A naïve seasonal model would take the 52-week observed profile, normalize it to mean 1.0, and apply it directly. This is dangerous because:"),
  bullet("Holiday pre-buys land in October-November order history; if applied positionally to the next forecast cycle, they would inflate weeks W1-W5 by 3-4× without justification."),
  bullet("One-off promotional spikes get baked in as recurring seasonality, even when they were not."),
  rich([
    { text: "The fix:", bold: true },
    { text: " we damp the profile by ", italic: false },
    { text: "DAMP = 0.1", bold: true, color: ACCENT },
    { text: ", which compresses every week toward 1.0. Concretely, the seasonal multiplier for any given week stays within ±20% of 1.0 — a quiet week reads 0.80, a peak week reads 1.20, and the model never goes outside that band based on history alone." },
  ], { before: 100 }),

  h2("4.2  Explicit event lifts (applied on top of the damp profile)"),
  buildTable([
    ["Event", "Forecast weeks", "Lift", "Scope"],
    ["Prime Day (Amazon)", "W7 – W9 (≈ mid-May ordering for July consumer event)",  "+25%", "Amazon customers only"],
    ["Fall Deal Days",     "W23 – W25 (≈ early-September ordering)",                 "+12%", "All customers"],
  ], [1800, 3500, 1300, 2760]),
  p("The Prime Day window is intentionally early — Amazon orders 6-8 weeks before the consumer event, so 'Prime Day prep' shows up in May purchase orders, not in July.", { before: 100 }),

  h2("4.3  Category-level seasonal priorities"),
  p("Some product categories have known seasonal shapes that the engine recognizes by category tag or keyword match. These provide a secondary multiplier applied as a floor (only allowed to increase demand, never decrease):"),
  bullet("Outdoor cooking / grilling — peak Apr-Aug, near-zero Nov-Feb"),
  bullet("Pest control / bug repellent — peak May-Sep"),
  bullet("Sun care / sunscreen — peak May-Aug"),
  bullet("Outdoor party / picnic disposables — peak Apr-Aug"),
  bullet("Holiday / gift items — peak Nov-Dec"),
  bullet("Ice melt / de-icer — peak Nov-Feb"),
  p("Category seasonality is overlaid as a floor — the model uses the higher of (observed seasonality × 1.0) or (category seasonality). It never drags forecasts down below what the order history would otherwise produce.", { before: 60 }),

  pageBreak(),
);

// ── Section 5: Order Cadence ──────────────────────────────────────────────
children.push(
  h1("5.  Order Cadence Detection"),
  p("Many accounts order on a strict bi-weekly rhythm — every other week, never weekly. A forecast that ignores this rhythm produces 26 small orders where the customer actually places 13 doubled-up ones, and the AI looks wrong every alternating week."),

  h2("5.1  How bi-weekly cadence is detected"),
  rich([
    { text: "Rule: ", bold: true },
    { text: "look at the L26W order history. Split it into even-indexed and odd-indexed weeks. If at least one of those parities is ≥ 70% zero, the account is flagged bi-weekly." },
  ]),
  p("This is intentionally loose. We accept some noise (one-off mid-cycle orders) as long as the dominant rhythm is unmistakable. A 75% zero-rate on odd weeks tells us the customer is not actually ordering in odd weeks — they may have placed an emergency fill once, but the forecast should not budget for it every cycle."),

  h2("5.2  How the forecast is reshaped"),
  p("Once cadence is detected, the post-forecast pass rewrites the 26-week output:"),
  bullet("The forecast values for paired weeks (e.g. W1+W2, W3+W4, …) are summed."),
  bullet("The total is placed entirely on the 'on' week."),
  bullet("The 'off' week is set to zero."),
  p("This preserves total volume but matches the customer's actual ordering rhythm. The AI Analysis narrative will note 'bi-weekly cadence enforced' so a planner reviewing the projection knows why the off-weeks read zero.", { before: 60 }),

  h2("5.3  Master-pack snapping"),
  rich([
    { text: "After cadence enforcement, every non-zero week is rounded to the nearest master-pack multiple. ", bold: true },
    { text: "If the master pack is 12 and the model produced 47, it becomes 48. If the model produced 0, it stays 0. This guarantees that every forecast value matches what the customer can actually order." },
  ]),

  pageBreak(),
);

// ── Section 6: Variance Alerts ────────────────────────────────────────────
children.push(
  h1("6.  Variance Alerts & the AI Analysis Narrative"),
  p("After the forecast is built, the engine compares the AI total against your manual projection total. Any record where the absolute variance exceeds 5% gets:"),
  bullet("A short alert string written to the AI ALERT field"),
  bullet("A longer rich-text narrative in the AI Analysis field"),
  bullet("A row badge in the viewer showing percent variance up or down"),

  h2("6.1  What the narrative tells you"),
  p("The narrative is designed to surface the highest-leverage explanation first. It will mention only the drivers that actually moved the forecast for that record:"),
  buildTable([
    ["Phrase you'll see", "What it means", "Action it suggests"],
    ["'Manual is X% above L13W run rate'", "Your plan is heavier than recent demand justifies", "Verify there is a known deal or shift; otherwise consider trimming"],
    ["'Manual is X% below L13W run rate'", "Your plan is light vs. recent demand",          "Confirm coverage; AI may be tracking a real surge"],
    ["'Manual has 13 zero weeks, AI has 5'", "Plan likely under-coverage on quiet weeks",    "Check whether bi-weekly was assumed when it shouldn't be"],
    ["'AI has 13 zero weeks (sees inactive)'", "AI thinks SKU is dead",                      "Manual override only if you have confirmed re-activation"],
    ["'Manual is front-loaded, AI is back-loaded'", "Shape mismatch between you and the model",  "Usually means manual has H1 promo plan AI doesn't see"],
    ["'POS exceeds order pace'",          "Amazon consumer demand is outpacing orders",   "Customer likely about to place catch-up order"],
    ["'Bi-weekly cadence enforced'",      "Off-weeks were zeroed",                         "Confirm cadence is still right; one-off mid-cycle orders are OK"],
    ["'EC variant exists for this account'", "Parent SKU being phased out",                "Verify in Quickbase before accepting"],
    ["'Zero L26W history but AI projects N units'", "Forecast anchored on category fallback", "Verify item is actually shipping before accepting"],
  ], [3000, 3360, 3000]),

  h2("6.2  Conservative inactive guardrail (--conservative-inactive flag)"),
  p("When this opt-in flag is enabled, items classified Inactive that still carry large manual projections (≥ 5,000 total) and have non-zero Amazon POS in L52 get a softer treatment: forecast = 50% of manual total, shaped to your manual curve, capped at POS L52 × 26. This prevents the AI from showing 0 across the board for items where there is consumer demand but the wholesale order pattern has gone quiet."),

  pageBreak(),
);

// ── Section 7: Validation Mode ────────────────────────────────────────────
children.push(
  h1("7.  Validation Mode (Pre-Forecast Sanity Check)"),
  p("Validation is a separate, read-only mode that audits your manual projections against history without producing any AI forecast. It is the recommended first pass when you sit down to review a fresh week."),

  h2("7.1  What it flags"),
  buildTable([
    ["Severity", "Trigger condition"],
    ["CRITICAL — > 5× spike",                "A single week's manual projection exceeds 5× the seasonal baseline"],
    ["CRITICAL — inactive with demand",       "Manual projection > 0 on a record where L13W is all zero"],
    ["WARNING — outside expected band",       "Manual is below baseline × 0.30 or above baseline × 2.00"],
    ["WARNING — sudden stop",                 "Manual drops to zero after a stretch of consistent ordering"],
    ["WARNING — bi-weekly off-week breach",   "Manual has a non-zero value on a week that should be cadence-zero"],
    ["WARNING — not a master-pack multiple",  "Manual value is not divisible by the SKU's master pack"],
  ], TWO_COL),
  p("Validation produces validation_results.json and launches the viewer in validation mode. No data is written back to Quickbase.", { before: 100 }),

  pageBreak(),
);

// ── Section 8: Glossary ───────────────────────────────────────────────────
children.push(
  h1("8.  Glossary"),
  buildTable([
    ["Term", "Meaning"],
    ["L13W / L26W / L52W", "Last 13 / 26 / 52 weeks of order history (from Quickbase Ord_LW columns)"],
    ["Non-zero average",   "Sum of order quantities on active weeks ÷ count of active weeks (excludes drawdown zeros)"],
    ["CV",                 "Coefficient of variation = stddev ÷ mean. < 0.5 = steady, ≥ 0.5 = lumpy"],
    ["α (alpha) / β (beta)", "Smoothing weights for level (α=0.3) and trend (β=0.1). Higher = more weight on recent observations"],
    ["Bi-weekly cadence",  "Detected when ≥ 70% of either even or odd indexed weeks are zero in L26W"],
    ["Master pack",        "The minimum order multiple for a SKU. Every non-zero forecast week is rounded to this"],
    ["Damp profile",       "Seasonal multipliers compressed within ±20% of 1.0 to prevent positional distortion"],
    ["Event window",       "Predefined weeks where a known explicit lift is applied (Prime Day W7-9, Fall Deal W23-25)"],
    ["Inactive",           "L13W is all zero. Forecast is all zero unless --conservative-inactive override is used"],
    ["AI ALERT",           "Quickbase rich-text field (fid 1590 = AI Analysis) holding the per-record narrative"],
    ["AI vs Proj",         "Percent variance between AI 26-week total and your manual 26-week total"],
    ["Variance threshold", "5% — beyond this, the record gets an AI ALERT and a narrative"],
  ], [2400, 6960]),

  pageBreak(),
);

// ── Section 9: Quick Reference ────────────────────────────────────────────
children.push(
  h1("9.  Quick Reference"),
  h2("Decision flow at a glance"),
  bullet("L13W all zero?  →  Inactive (forecast = 0)"),
  bullet("Less than 13 active weeks?  →  Heuristic"),
  bullet("CV > 0.5 OR > 20% zero weeks?  →  Croston's"),
  bullet("Otherwise  →  Holt-Winters"),

  h2("Outputs the engine writes back to Quickbase"),
  buildTable([
    ["Field",          "fid",  "Source"],
    ["AI_PRJ_W1..W26", "varies", "Per-week forecast values, master-pack snapped"],
    ["AI ALERT",       "—",    "Short text alert (only if |variance| > 5%)"],
    ["AI Analysis",    "1590", "Rich-text per-record narrative explaining drivers"],
  ], THREE_COL),

  h2("When to trust the AI vs override"),
  bullet("Trust the AI when: order history is dense, no announced deal, narrative cites L13W run rate, no EC supersession warning"),
  bullet("Override toward your plan when: you have customer-specific intel (a confirmed promo, a forward PO, a discontinuation), or the SKU is brand new (Heuristic class) and you have launch volumes from the customer"),
  bullet("Investigate before accepting when: the narrative cites POS divergence, EC variant exists, zero history with non-zero forecast, or the AI total is more than 50% off your plan"),

  // Footer-style line
  new Paragraph({
    spacing: { before: 600, after: 0 },
    alignment: AlignmentType.CENTER,
    border: { top: { style: BorderStyle.SINGLE, size: 8, color: ACCENT, space: 6 } },
    children: [new TextRun({
      text: " ",
      font: FONT, size: 4,
    })],
  }),
  new Paragraph({
    spacing: { before: 200, after: 0 },
    alignment: AlignmentType.CENTER,
    children: [new TextRun({
      text: "Pets+People  •  Inventory Forecaster  •  Planner Guide",
      font: FONT, size: 18, color: SUBTLE, italics: true,
    })],
  }),
);

// ── Build doc ────────────────────────────────────────────────────────────
const doc = new Document({
  creator: "Pets+People Inventory Planning",
  title: "Inventory Forecaster — Planner Guide",
  description: "Decision logic, models, and order-cadence rules for the Pets+People AI inventory forecaster",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, color: NAVY, font: FONT },
        paragraph: { spacing: { before: 320, after: 140 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, color: NAVY, font: FONT },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 },
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, color: ACCENT, font: FONT },
        paragraph: { spacing: { before: 180, after: 80 }, outlineLevel: 2 },
      },
    ],
  },
  numbering: {
    config: [{
      reference: "bullets",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 270 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "◦",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1080, hanging: 270 } } } },
      ],
    }],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },         // US Letter
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }, // 1"
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          spacing: { after: 0 },
          children: [new TextRun({
            text: "Inventory Forecaster — Planner Guide",
            font: FONT, size: 18, color: SUBTLE, italics: true,
          })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 0 },
          children: [
            new TextRun({ text: "Page ", font: FONT, size: 18, color: SUBTLE }),
            new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: SUBTLE }),
            new TextRun({ text: " of ",                   font: FONT, size: 18, color: SUBTLE }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 18, color: SUBTLE }),
          ],
        })],
      }),
    },
    children,
  }],
});

const outPath = path.join(__dirname, "Inventory_Forecaster_Planner_Guide.docx");
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(outPath, buf);
  console.log("Wrote " + outPath + " (" + buf.length + " bytes)");
}).catch(err => {
  console.error("Failed to build docx:", err);
  process.exit(1);
});
