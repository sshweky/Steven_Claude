---
name: customer-deep-dives
description: Build Customer Deep Dive presentations for Pets+People (P+P) — multi-slide PowerPoint decks comparing FY2025 actuals, FY2026 budget, YTD shipped, open orders, and 2026 estimated sales at the customer × brand level. Trigger this skill whenever the user asks to build, refresh, rebuild, regenerate, or update customer review decks, sales review decks, salesperson review decks, customer scorecards, customer deep dives, budget vs actual decks, or anything resembling "the customer review deck" / "the FY26 budget deck". Three variants supported — Combined (single deck spanning all customers), Side-Split (one Fetch deck + one Brand Buzz deck), or Per-Salesperson (one deck per salesperson, with separate sections for FF and BB if they cover both sides). Use this skill for any P+P customer deck request even when the user doesn't name a specific variant — clarify the variant once and proceed.
---

# Customer Deep Dives

Generates P+P customer review PowerPoint decks from Sales_Budgets. Three variants share a single data pull and a single customer-slide layout — see `references/` for the canonical specs.

## Step 0 — Confirm variant

If the request doesn't already name a variant, ask Steven once before pulling data:

> "Happy to help put a customer deck together — which view would you like?"
>
> - **The big picture** — single Combined deck spanning all customers
> - **Pet vs. People** — two decks, one for each side (Fetch + Brand Buzz)
> - **By salesperson** — one deck per salesperson, FF and BB as separate sections

If the request already names a variant ("build the salesperson decks", "rebuild Caroline's deck", "regenerate the combined deck"), skip the menu and proceed.

## Reference docs (read first)

These are authoritative. Don't reinvent the math, layout, or data flow — read them.

| File | What it covers |
|---|---|
| `references/business_rules.md` | Estimation formula, coverage %, risk classifier, thresholds, brand selection, programs/entries rules, reconciliation reference totals |
| `references/data_pull_strategy.md` | The exact Sales_Budgets SQL (9 columns), Accounts code-name lookup, column meanings, what NOT to query (Invoices, Customer_POs, Orders_and_Shipments — they all time out) |
| `references/entity_groupings.md` | Walmart / PetSmart / Petco / Ross / Dollar Tree rollup rules, Amazon special case, per-salesperson rule (group only within a rep's territory) |
| `references/layout_specs.md` | Color tokens, typography, every slide component's dimensions (customer slide, cover, section divider), pagination, sort order, risk colors |

## Data pull — 3 CData calls, in order

1. `getInstructions("QuickBase")` — required session-start call per org throttle rules
2. Accounts query → build `code_name_map.json` (Slsprsn_Code → Salesperson)
3. Sales_Budgets query → 9-column raw rows → `sb_raw.json`

Throttle protection (org rule): pre-declare CData budget; two failures → 15-min stop; real wall-clock backoff (2s/4s/8s); never use the "Please wait - Retrying Connection" phrase outside SP-API.

## Script pipeline

```
scripts/
  fetch_accounts_map.sql       # SQL for Accounts lookup
  fetch_sales_budgets.sql      # SQL for Sales_Budgets (9 cols incl. Brand_Type)
  lib_aggregate.py             # shared logic: load_data, get_entity_group, calc_est, calc_risk, build_customer_slide
  aggregate_combined.py        # produces combined_data.json
  aggregate_side_split.py      # produces side_ff.json, side_bb.json
  aggregate_per_salesperson.py # produces sp_decks/<Name>.json — one per qualifying rep
  build_combined_deck.js       # combined_data.json → PP_Combined_Deck.pptx
  build_side_deck.js           # side_<div>.json → PP_<Side>_Deck.pptx
  build_salesperson_deck.js    # sp_decks/<Name>.json → PP_<Name>_Deck.pptx
```

All builders use `pptxgenjs` and `LAYOUT_WIDE` (13.33 × 7.5"). Run from `/home/claude/` or equivalent — output goes to `/mnt/user-data/outputs/`.

## Updates — May 10, 2026

**Brand_Type column added to the pull.** Sales_Budgets now exposes `Brand_Type` with values `CPG | Entertainment | Fetch Owned | Private Label | Other | Lifestyle`. The aggregators carry this field through to the brand row dict.

**Entertainment-brand projection override.** P+P is discontinuing all entertainment-branded items. Brand rows where `Brand_Type == 'Entertainment'` get `Est = YTD + OO` (booked only — no forward projection). Implemented in `lib_aggregate.calc_est()`. 23 brands currently affected (Disney, Star Wars, Care Bears, DC Comics, Dr. Seuss, Friends, Harry Potter, Looney Tunes, Peanuts, Peeps, Rudolph, Scooby Doo, Spongebob, Universal-Horror, Warner Bros, etc.) — roughly 250 rows. Detection is data-driven (Brand_Type field) — no hardcoded brand list, so new licensed lines tagged Entertainment will pick up the rule automatically.

**DISC tag rendering** (salesperson builder only as of this update). Entertainment brand rows render with a small italic `*DISC*` suffix on the brand name (e.g., `Disney  *DISC*`). Combined and side-split builders carry `r.disc` in the data but don't render the tag yet — add the same one-line patch when those builders are next touched.

**Budget bar removed from `build_salesperson_deck.js`.** The bottom budget bar (y=6.25, h=1.10) duplicated information already shown in the left-panel stat stack and the brand table TOTAL row. Removed for cleaner whitespace. `build_combined_deck.js` and `build_side_deck.js` still render the bar — remove there next time those decks are rebuilt for consistency.

**House-account codes (`FC000`, `BB000`) added to `SKIP_SALESPEOPLE`.** Sales_Budgets references these codes for Amazon-house and Walmart-import buckets. They don't appear in the Accounts lookup (filtered out as "House Account"), so they were leaking through as $31M and $15M phantom "salespeople". Cleaner long-term fix: remove the `[Salesperson] <> 'House Account'` filter from `fetch_accounts_map.sql` so those codes map to "House Account" and skip via the existing rule. Left as a quick patch in `lib_aggregate.py` for now.

## Variant-specific notes

**Per-salesperson** uses `Brand Entries / Exits` in the third callout panel (no Product_Category in Sales_Budgets). Combined and side-split use `Programs Won / Lost` — but those require a Product_Category source (Invoices or Customer_POs), which times out. If you need true Programs Won/Lost, fall back to brand-level Entries/Exits across all three variants.

**Per-salesperson qualification threshold:** $750K raw portfolio. **Customer threshold:** $100K. **Side threshold:** $100K. **Brand floor:** $5K. All defined in `lib_aggregate.py`.

## Output convention

```
/mnt/user-data/outputs/PP_<Variant>_Deck.pptx          # Combined
/mnt/user-data/outputs/PP_FETCH_Deck.pptx              # Side-split FF
/mnt/user-data/outputs/PP_BRAND_BUZZ_Deck.pptx         # Side-split BB
/mnt/user-data/outputs/PP_<Salesperson_Name>_Deck.pptx # Per-salesperson, one per qualifying rep
```

Always `present_files` after building so Steven sees them inline.
