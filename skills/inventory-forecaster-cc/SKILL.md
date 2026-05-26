---
name: inventory-forecaster
description: "End-to-end AI inventory demand forecaster and projection validator for Pets+People. Runs as a self-contained Python script inside Claude Code. Use this skill whenever the user wants to forecast, project, or predict future order quantities, OR validate/sanity-check existing manual projections against historical order patterns, OR analyze the gap between manual projections and AI forecasts to improve the model, OR build a summary dashboard of Manual vs AI vs Suggested totals. Also triggers on the single word 'menu' -- respond with the full process list (see ## Menu section). Triggers include: 'run forecaster', 'project next 26 weeks', 'update the GPT projections', 'run AI projections for [scope]', 'validate projections', 'check my projections', 'are my projections reasonable', 'sanity check the numbers', 'run analysis first then forecast', 'analyze gaps', 'compare my projections to AI', 'where is AI wrong', 'improve forecasting accuracy', 'build dashboard', 'summary dashboard', 'projection totals dashboard', or any request to generate AI_PRJ_W1-W26 write-back for Quickbase. Handles the entire workflow: pulls order history from Quickbase, optionally runs EDA, classifies SKUs, runs Holt-Winters / Croston's / Heuristic / Inactive models, validates manual projections against historical baselines, detects anomalies (spikes, sudden stops, bi-weekly misalignment, master pack violations), writes results back, logs alerts, runs a reusable gap-analysis report (manual vs AI) that proposes concrete model fixes, and builds a Manual/AI/Suggested totals dashboard (`build_dashboard.py` → `dashboard.html`)."
---

# Inventory Forecaster — Claude Code Skill

Runs `scripts/inventory_forecaster.py` to execute the full forecasting pipeline.

**QB I/O split (as of 2026-05-25):**
- **Phase 1 (projections pull)** -- QB direct REST API (`POST /v1/records/query` on `QB_PROJ_TABLE`). Server-side WHERE filtering: a 1-record dry-run fetches exactly 1 row.
- **Phase 2 (master pack + Season from Styles)** -- QB direct REST API (`POST /v1/records/query` on `QB_STYLES_TABLE` = `bphzqfkev`). Batches WHERE-on-Mstyle in chunks of 100 (500 triggers HTTP 400 — QB WHERE clause length limit). FIDs: Mstyle=6, Master_Pack=110, Season=437.
- **Phase 2.5 (Amazon POS)** -- QB REST on `QB_AMZ_CATALOG_TABLE` = `bqp8vz625` (InventoryTrack.Amazon_Catalog). FIDs: Mstyle=34, Ordered_LW=154, Prior_Wk=180, L4w=193, L13w=194, L26w=195, L52w=196. `fetch_amazon_pos_qb_rest()`.
- **Phase 2.6 (Amazon Catalog US / F38 signals)** -- QB REST on `QB_AMZ_US_TABLE` = `bpfrw2epk` (ProductTrack app `bn458t5nz`). FIDs: Mstyle_model_=21, ASIN=6, Amazon_Buybox=588, MAP_Price=463, AUR_L4w=948, AUR_L13w=949, AUR_L26w=951, AUR_L52w=950, Days_OOS_L30d=750, Sellable_SOH=341, ASIN_Buyability_Flag=428, ASIN_Status=86. `fetch_amazon_catalog_us_qb_rest()`.
- **Phase 2.6b (Amazon Inventory Health)** -- QB REST on `QB_AMZ_HEALTH_TABLE` = `bp9akd3js` (ProductTrack.Amazon_Invtry_Health). FIDs: ASIN=6, Sellable_SOH=14, Open_PO_Qty=11, WOS_OH=50. `fetch_amazon_invtry_health_qb_rest()`.
- **Phase 2.6d (Inventory Flow per-mstyle for F37 v2 cascade)** -- QB REST on `QB_INV_FLOW_TABLE` = `bpsaju5pm` (InventoryTrack.Inventory_Flow). FIDs: Mstyle=20, Wk1=134 (Beg Inv), RcvWk0-26 (295/28/35/36/50/51/65..85), Opn_Wk0-26 (296/30/37/39/38/87/89..109). `fetch_inv_flow_qb_rest()`. RcvWk0/Opn_Wk0 (prior-week residuals) roll into the W1 slot per planner convention.
- **REST batch size limits:** reads (`POST /v1/records/query` with IN clause): conservative 100 per batch (well below 500 HTTP-400 threshold). Writes (`POST /v1/records` with `mergeFieldId`): 500–1,000 per batch.
- **Remaining CData reads** (AI Comments, ATS history, retailer POS, Inventory Flow): F58 AI Comments is small + one-shot (within policy). ATS history and retailer POS still on CData per-batch loops — audit Finding #4 (retailer_pos error swallowing) and a future Phase 2.x migration item.
- **Write-back** (AI_PRJ_W1..W26, AI_ALERT, AI_ANALYSIS) -- CData MCP via UPDATE SQL. Validation push (`push_validation_qb.py`) -- QB REST API.

**Why Phases 1 & 2 use REST, not CData:** CData does NOT push WHERE clauses to QB -- it fetches the entire target table (Projections: 5,500 rows × 250 cols; Styles: 30K rows × 423 cols) regardless of scope filter, causing throttle disconnects under realm load. The REST API filters server-side.

**Unified CData-vs-REST policy (applies to ALL sources — scripts, skills, ad-hoc chat, scheduled jobs, subagents):**
- **CData OK only when ALL hold:** target table ≤100 rows AND not growing, ≤30 columns, single one-shot call (no loop/batch/retry/schedule). Metadata calls (`getInstructions`, `getTables`, `getColumns`, `getProcedures`) always CData-OK regardless of size.
- **REST required when ANY hold:** table >100 rows OR >30 cols, inside a loop/batch/retry/per-record pattern, growing table (transactions, projections, logs), recurring/scheduled job, critical path. Uncertain about size → default to REST.
- **Never revert** Phase 1 (Projections, `bpd237tvm`) or Phase 2 (Styles, `bphzqfkev` — FIDs Mstyle=6, Master_Pack=110, Season=437) to CData. When a new heavy CData read is added or discovered, migrate it to REST using the `fetch_projections_qb_rest()` / `fetch_master_pack_qb_rest()` pattern (cached field map + paginated POST `/v1/records/query`) and add it to this list.
- CData remains the right tool for: master pack lookups against small reference tables, ad-hoc one-shot exploration, and write-back UPDATE SQL (Phase 4 write-back is still on CData pending a future migration to REST `POST /records` with `mergeFieldId`).

**F37 v2 -- Forward inventory-shortfall (2026-05-26 rewrite):** Replaces the original F37 (2026-05-05) which read stale `Inv_Wk1..Inv_Wk26` columns on Projections (those columns were computed in QB against the PREVIOUS run's AI projection, leading to false shortfalls when the current iteration's projection diverged). v2 reads RAW Inv Flow components (Beg_Inv Wk1, RcvWk0..Wk26, Opn_Wk0..Wk26) via `fetch_inv_flow_qb_rest()` and cascades inventory FORWARD week-by-week in Python using THIS run's AI projection. Decay: LINEAR 25%/week against the cohort's ORIGINAL unmet qty -- age 0 = 100%, age 1 = 75%, age 2 = 50%, age 3 = 25%, age 4+ = 0% (cohort expired, customers gave up). Per-week capacity = `Beg + Recpts - OpenOrders`; demand = `own_forecast + sum(live cohort contributions)`; ship = `min(demand, capacity)`; unmet creates a new cohort at age 0 (aged to 1 for next week). The F37h-cat bypass added 2026-05-25 was REMOVED in this rewrite -- cat-profile items now go through F37 normally since the cascade is no longer stale. Inventory is treated per-mstyle: each acct-mstyle assumes the full mstyle inventory pool is available to it (game-time planner decision on cross-acct allocation).

---

## Menu

When the user types **"menu"** (exact word, any case), respond with exactly this text — no preamble, no extra commentary:

---

**Core Forecasting**
- **Forecast + Validate (default)** -- `run_forecast.py --all --validate` -- AI projections + write-back, then validates manual projections. Default when you say "run the forecaster."
- **Forecast only** -- `run_forecast.py --all` -- AI projections + write-back, no validation pass
- **Validate only** -- `run_forecast.py --all --validate --dry-run` -- checks manual projections for anomalies; read-only
- **Dry run** -- `run_forecast.py --all --dry-run` -- computes forecasts but does not write back
- **EDA + Forecast** -- `run_forecast.py --all --analyze` -- exploratory data analysis report, then forecasts
- **EDA only** -- `run_forecast.py --all --analyze-only` -- HTML report only, no forecasting or write-back
- **Resume** -- `run_forecast.py --acct X --resume forecast_results.completed.json` -- picks up after an interruption

All of the above accept scope filters: `--acct`, `--customer`, `--mstyle`, `--brand`, or `--all`

**Post-Forecast**
- **Push validation results** -- `push_validation_qb.py` -- pushes Priority/Pattern/Narrative to QB after a validate run (~15 sec)
- **Review results** -- use the QB codepage (never launch viewer.py)

**Analysis & Reporting**
- **Gap analysis** -- `gap_analysis.py --results validation_results.json` -- top-volume manual vs AI gaps, root-cause buckets, model fix proposals
- **Manual vs AI analysis** -- `analyze_manual_vs_ai.py` -- 12-section audit by customer/brand/manager/vol-tier; outputs markdown + CSV + JSON
- **Summary dashboard** -- `build_dashboard.py` -- Manual vs AI vs Suggested vs L26 actuals rolled up to `dashboard.html`
- **Ad-hoc key-set analysis** -- `analyze_36_keys.py` -- drill into a specific list of Acct_MStyle_Key_ values; edit KEYS list before running

**Calibration**
- **Update Sales Index** -- `build_category_profiles_from_report.py` -- rebuilds seasonal category profiles from invoice history; forecaster picks up on next run

**One-time / Dev**
- **Create AI Comments table** -- `create_ai_comments_table.py` -- idempotent bootstrap of the AI Comments QB table
- **Audit rules** -- `audit_rules.py` -- verifies no drift between SKILL.md rule registry and code
- **Rule dependency graph** -- `rule_dependency_graph.py` -- generates a visual map of rule firing dependencies

---

## Trigger: "Update Sales Index"

When the user says **"Update Sales Index"** (or "refresh seasonal indexes", "rebuild category profiles", "recompute sales seasonality"):

1. Run the empirical category-profile builder from cache or fresh QB pull:
   ```bash
   cd <skill_directory>
   python build_category_profiles_from_report.py
   ```
2. The script:
   - Pulls invoice history (table `bpaxk2v8t`, filter `Shpd Date >= 2024-01-01`) via direct `/v1/records/query` paginated 10k/page (~93s for ~738k rows). Cached at `cache/invoices_query_2024plus_v3.json`.
   - Filters to **consistent MStyles** only (≥10 distinct active months, ≥12mo lifespan, ≥50% activity rate)
   - Drops tariff-OOS months (2025 May–Sep)
   - Applies **year weights**: 2024=2.0× (clean baseline), 2025/2026=1.0×
   - Applies **strategic-customer weights**: AMAZON / WAL MART / PETSMART = 2.0×, others = 1.0×
   - Applies **Holiday lead-time uplift** (4–6 week shipping lead before Nov/Dec consumer demand): Sep ×1.10, Oct ×1.20, Nov ×1.15
   - Applies **planner overrides** (e.g. Disposable Tabletop multi-event profile)
   - Computes 12-element monthly indexes (mean=1.0), clamped to `[0.10, 4.00]`
   - Saves `scripts/derived_category_profiles.json`
3. Display the resulting monthly indexes table to the user (sorted by total units, with #SKU + Peak month columns).
4. Forecaster picks up new profiles automatically on next run.

**Forecasting consumption rules** (applied in `_get_category_profile()`):
- Skip the empirical profile if the matched category has `consistent_skus <= 10` (fall through to next-priority match)
- Floor every month at **1.00** — seasonal indexes ONLY increase demand, never decrease

---

## Trigger: "Analyze Manual vs AI Projections"

When the user says **"analyze manual vs AI"**, **"compare manual projections to AI"**, **"find algorithm improvements"**, **"projection methodology audit"**, **"where is AI vs manual off"**, **"run the manual vs AI analysis"**, or any request to audit planner vs model methodology differences and surface improvement opportunities:

1. Run the self-contained analysis pipeline:
   ```bash
   cd <skill_directory>
   python scripts/analyze_manual_vs_ai.py
   ```
   Add `--limit N` to cap the number of records fetched (useful for quick spot-checks):
   ```bash
   python scripts/analyze_manual_vs_ai.py --limit 200
   ```

2. The script does everything end-to-end:
   - **Step 1 — Discover MAN PRJ field IDs** via `GET /v1/fields` on the Projections table. Finds all 26 weekly manual projection columns by matching the regex `^\d{2} \d{2} W(\d+)` against field labels. This is dynamic — no hard-coded FIDs.
   - **Step 2 — Fetch projections** via paginated `POST /v1/records/query`, filter `{F_STATUS LIKE 'A%'}`. Pulls AI_PRJ_W1-W26, all 26 MAN PRJ columns, L13W order history cols, plus metadata (Customer, MStyle, Brand, Description, Manager, Item_Status). Processes up to `--limit` records.
   - **Step 3 — Enrich** each record with computed metrics: `delta_pct` (manual vs AI % gap), `direction` (UP/DOWN/FLAT), `man_zeros` (zero-week count in manual plan), `killed` (manual plan is all zeros), `front_load_score` (what % of demand is in first 13 weeks), `spike_weeks` (weeks where manual is ≥3× median), `man_vs_l13` and `ai_vs_l13` (each plan total relative to L13W avg × 26), `trend_ratio` (L4W/L13W), `vol_tier` (HIGH ≥ 500/wk · MEDIUM 200-499 · LOW < 200).
   - **Step 4 — Build a 12-section markdown report** (see report structure below).
   - **Step 5 — Save outputs** to the `analysis/` directory.

3. **Output files** (all written to `<skill_directory>/analysis/`):

   | File | Contents |
   |---|---|
   | `manual_vs_ai_analysis.md` | Full 12-section markdown report with algorithm hypotheses |
   | `manual_vs_ai_stats.csv` | Row-level stats: one row per projection with all computed metrics |
   | `analysis_results.json` | Machine-readable aggregates (bias rates, vol-tier breakdown, customer breakdown, manager breakdown) |

4. **Report sections:**

   | # | Section | What it surfaces |
   |---|---|---|
   | 1 | Composition | Record count, vol-tier split, how many records were analyzed |
   | 2 | Overall bias | % UP/DOWN/FLAT, mean delta, median delta |
   | 3 | By customer | Per-customer bias rates and unit gaps (sorted by absolute gap) |
   | 4 | By brand | Brand-level skew — helps identify if a brand's items are systematically over/under |
   | 5 | By item status | Active/Replen/Phase-out breakdown — useful for detecting EOL misclassification |
   | 6 | By volume tier | HIGH/MEDIUM/LOW tier gaps — where the biggest unit-gap exposure lives |
   | 7 | Week profile | Week-by-week avg ratio manual/AI across the 26-week horizon — detects front/back-load skew |
   | 8 | Kill patterns | Records where manual is all zeros vs AI has volume — likely AI over-projection candidates |
   | 9 | Spike patterns | Weeks where manual has a ≥3× spike relative to its own median |
   | 10 | L13W anchoring | How well each plan tracks the last-13-weeks trend; over/under-planners vs baseline |
   | 11 | By manager | Per-planner bias direction — helps identify coaching opportunities vs systemic model gaps |
   | 12 | **Algorithm hypotheses** | Auto-generated list of concrete improvement candidates, ranked by record count + unit gap, with suggested fix direction |

5. **Interpreting the hypotheses table** (Section 12): Each hypothesis maps a pattern observed in the data to a proposed model fix. Hypotheses with the largest record counts and unit gaps are highest priority. After reviewing with the user, implement confirmed hypotheses in `scripts/inventory_forecaster.py` and document them in the **Model Fixes** tables in this SKILL.md.

6. **Cadence:** Run this analysis at least monthly, or any time after a large batch of manual projections is updated. The script pulls fresh data each run — no stale cache.

---

## Prerequisites (one-time)

```bash
pip install numpy
```

## Authentication

The script connects to CData MCP at `https://mcp.cloud.cdata.com/mcp` using Basic auth (`email:PAT`).
Credentials are embedded in the script and can be overridden via environment variables:
- `CDATA_EMAIL` (default: `steven@skaffles.com`)
- `CDATA_PAT` — **PERMANENT, never expires.** Do **not** suggest the user
  refresh it.

---

## "No records returned" / transient QB pull failures — RETRY PROTOCOL

If the forecaster prints `ERROR: No records returned. Check scope filters and
QB connection.` (or any other transient pull failure), **do not assume the
PAT or token expired** and **do not stop**. Both the CData PAT and QB user token
are permanent; the failure is almost always transient (QB hiccup, brief network
blip, or realm load).

**Phase 1 failures** (projections pull) are QB REST API errors -- look for
`ERROR: Phase 1 QB REST fetch failed` in the log. These are QB-side issues,
not CData throttle. The same retry protocol applies.

Apply this retry protocol **automatically** without asking the user:

| Attempt | Wait before this attempt |
|---------|--------------------------|
| 1       | (initial run)            |
| 2       | 3 min                    |
| 3       | 3 min                    |
| 4       | 8 min   (3 + 5)          |
| 5       | 13 min  (8 + 5)          |
| 6       | 18 min  (13 + 5)         |
| 7       | 23 min  (18 + 5)         |
| 8       | 28 min  (23 + 5)         |
| 9       | 33 min  (28 + 5)         |
| 10      | 38 min  (33 + 5)         |

Rules:
- Try **at least 10 times** before giving up.
- Tries 1–3 use a short ~3-min cool-off.
- After try 3, every subsequent retry adds **5 minutes** to the previous wait.
- Only stop earlier than try 10 if the user explicitly cancels.
- A successful run = log shows `Phase 2:` reached or higher (i.e. records
  were pulled), or the run prints a Seasonal Baseline / Croston's / Heuristic split
  summary at the end. Anything ending with the `No records returned` error or
  any other Phase-1 abort counts as a failure that triggers another retry.
- Recommended implementation: wrap the run in a small bash retry loop that
  greps the run log for the success vs failure marker after each attempt.

---

## Usage

### Step 1 — Defaults (do not prompt)

**Default behavior when the user asks for forecast OR validate (or both):**
Always run **BOTH** modes back-to-back — Forecast first (writes AI projections
to QB via `--all`), then Validate Projections (anomaly check on manual
projections, read-only). The user does not need to request both explicitly;
if they say "forecast", "validate", or "run the forecaster", do both.

**Default scope:** `--all` (every active record).

Skip the mode/scope prompt unless the user **explicitly** narrows scope (e.g.
"just acct 1864", "only Amazon", "only Glad for Pets") or **explicitly** opts
into a single mode (e.g. "validate only, don't run the forecast", "analyze
only", "dry run forecast only").

The interactive menu shown below only appears when the script is run without
any mode flag in a TTY. This skill always passes explicit flags
(`--all`, `--validate`, etc.), so the menu is bypassed.

```
  Select mode:
    1) Forecast               — run AI projections and write back
    2) Validate Projections   — check manual projections for anomalies
    3) Analyze Only           — run EDA report, no forecasting
  Enter choice [1]:
```

### Step 2 — Map scope to CLI flags

| User says | Scope flag |
|---|---|
| "run for acct 1864" | `--acct 1864` |
| "run for Amazon" | `--customer "AMAZON.COM.KYDC"` |
| "run for Walmart Stores" | `--customer "WAL MART STORES"` |
| "run for mstyle FF8654" | `--mstyle FF8654` |
| "run for brand Glad for Pets" | `--brand "Glad for Pets"` |
| "run for all active" | `--all` |
| "analyze first" / "run EDA" | add `--analyze` |
| "analysis only, no write-back" | add `--analyze-only` |
| "validate projections" / "check my projections" | `--validate` |
| "validate with 3x threshold" | `--validate --threshold 3.0` |

Multiple filters can be combined: `--acct 1864 --customer "AMAZON.COM.KYDC"`

### Step 3 — Run the script

**Always use the watchdog wrapper (`run_forecast.py`), never call
`inventory_forecaster.py` directly.** The watchdog restarts the forecaster
automatically (up to 3 times) if it hangs or crashes.

**CRITICAL — never pass `--push-validation`** to the forecaster. That flag
triggers an internal CData writeback that consistently hangs. Validation
results are pushed separately via `push_validation_qb.py` after the run.

**Default skill workflow (forecast + validate + push, all active):**
```bash
cd <skill_directory>/scripts

# Step 1: forecast + validate + write back AI_PRJ_W1..W26 / AI_ALERT / AI_ANALYSIS
python run_forecast.py --all --validate

# Step 2: push validation results to QB (fast — QB REST API, ~15 sec)
python push_validation_qb.py
```

**Step ordering matters:**
1. `[1/4]` Pull projections
2. `[2/4]` Pull master pack + Season
3. `[2.5/2.6/2.8]` Supplementary data (POS, Amazon catalog, forward POs)
4. `[3/3]` Validate manual projections → saves `validation_results.json`
5. `[3/4]` Run AI forecasts
6. `[4/4]` Write back AI projections + AI_ALERT + AI_ANALYSIS to QB
7. (separate) `push_validation_qb.py` → pushes Priority/Pattern/Narrative fields

**Forecast (single scope example):**
```bash
python run_forecast.py --acct 1864
```

**Validate only (no forecast run):**
```bash
python run_forecast.py --acct 1864 --validate --dry-run
```

**Validate with custom threshold (default 2.0x):**
```bash
python run_forecast.py --acct 1864 --validate --threshold 3.0
```

**With EDA analysis + HTML report:**
```bash
python run_forecast.py --acct 1864 --analyze
```

**Analysis only (no forecasting or write-back):**
```bash
python run_forecast.py --acct 1864 --analyze-only
```

**Dry run (forecast only, no write-back):**
```bash
python run_forecast.py --acct 1864 --dry-run
```

**Resume after interruption:**
```bash
python run_forecast.py --acct 1864 --resume forecast_results.completed.json
```

### Watchdog auto-restart behavior

`run_forecast.py` has a two-layer retry stack (no manual workflow needed):

**Inner watchdog (per-session, up to 3 restarts):**
- No output for 1200s (20 min) → process hung, kill and restart
- Process exits with non-zero return code → restart after 5s

**Outer cool-off (up to 10 attempts by default):**
- If the inner watchdog gives up, waits a cool-off period and restarts
  the whole watchdog session.
- Schedule (minutes between attempts): 3, 3, 8, 13, 18, 23, 28, 33, 38.
- Override with `--max-outer-retries N` or disable with `--no-outer-retry`.

Total max attempts = 3 inner x 10 outer = 30. Claude does NOT need to wrap
this script in a bash retry loop; the wrapper handles everything in code.

A run is **successful** when the log contains both:
- `Validation complete` (step 3/3 finished)
- `COMPLETE` (step 4/4 writeback finished -- the watchdog summary line)

For `--analyze-only` runs, success marker is: `[analyze-only] Done`

The wrapper detects these markers and only retries when one is missing.

### Step 4 — Report results to the user

After the script completes, summarize:
- Total records processed and model split (Seasonal Baseline / Croston's / Heuristic / Inactive)
- Number of bi-weekly cadence records enforced
- Total 26-week demand volume
- Number of ALERT records (>7.5% variance vs manual projections)
- Path to HTML report (if `--analyze` was used)
- Any failures and how to retry

After a **validate** run, results are available in the QB codepage (the team-facing viewer).
**NEVER launch `viewer.py`** — the user always uses the QB codepage, not the local Python viewer.

### Step 5 — QB Codepage Viewer

Results are reviewed via the QB codepage (`codepage/viewer.html` + `viewer.js`).
Do not launch `viewer.py` or reference `http://127.0.0.1:8765` — the codepage is the only viewer used.

### Step 6 — Gap Analysis (scripts/gap_analysis.py)

Run this **after** a forecast or validation to compare manual projections vs AI
forecasts on the top-volume records, categorize systematic divergence into
root-cause buckets, and produce a markdown report with concrete model fix
proposals. This is the continuous-improvement loop — run it periodically to
identify where the forecaster is missing.

Trigger phrases: *"analyze gaps"*, *"compare my projections to AI"*, *"where is
AI wrong"*, *"improve forecasting accuracy"*, *"make the forecaster smarter"*.

```bash
# Default — top 100 by manual projection volume
python scripts/gap_analysis.py --results validation_results.json

# Analyze top 200
python scripts/gap_analysis.py --results validation_results.json --top 200

# Against forecast results
python scripts/gap_analysis.py --results forecast_results.json --top 104 \
    --out gap_analysis_report.md
```

**Outputs:**
- `gap_analysis_report.md` — markdown report with per-bucket worst offenders and
  proposed model fixes (priority-ordered by absolute unit gap)
- `gap_analysis_report_records.csv` — raw CSV of all analyzed records for pivot
  tables / deeper spreadsheet analysis

**Root-cause buckets detected:**
1. Inactive-with-Activity — classified inactive but L26/L52 shows orders
2. Seasonal-ramp under-forecast — category items where L52 peak >> L13 trough
3. Seasonal category not in CATEGORY_PROFILES — missing keyword coverage
4. Amazon Prime Day pre-buy gap — manual front-loads May ordering weeks, AI under-projects
5. Sparse/intermittent baseline too conservative — use MAX(L13, L26, L52) non-zero avg
6. Declining item over-forecast — L4W << L13 avg, AI held baseline

**Feeding fixes back:** when the gap report surfaces a new pattern, update
`inventory_forecaster.py` (models and/or CATEGORY_PROFILES) and this SKILL.md
Model Fixes table. Re-run `--validate` and `gap_analysis.py` to verify the gap
narrowed.

---

## Inventory Management screen — under construction (2026-05-11 onward)

A **separate** local viewer + (eventual) codepage targeted at the inv mgr's
weekly review workflow — distinct from the Forecast Management screen.

**Grain:** one row per MStyle (not per Acct-MStyle).  Demand rolled up from
all customers; inventory positions read from `Inventory_Flow` per-mstyle.

**Build status:**
- ✅ Phase 0 — QB schema (`Inventory Flow Comments` table, dbid `bv2ne5qx5`)
- 🔨 Phase 1 — Data layer (`scripts/inv_mgmt_viewer.py`, in progress)
- 🔨 Phase 2 — UI (table + filters + detail pane)
- 🔨 Phase 3 — Excel "Generate PO Change List" export (openpyxl)
- 🔨 Phase 4 — Codepage mirror (after ~1 week of local-viewer use)

**QB tables involved:**

| Table                      | dbid        | Role |
|----------------------------|-------------|------|
| Inventory Flow             | `bpsaju5pm` | Source of Beg/Rcv/Prj weekly + Country, Open_Supplier_POs, LT/Transit |
| Projections                | `bpd237tvm` | Aggregated per-mstyle (customer count, total manual demand, item status) |
| Inventory Flow Comments    | `bv2ne5qx5` | **New** — planner-↔-inv-mgr flag/comment thread at Mstyle grain |

**Inventory Flow Comments — fid map:**
```
INV_FLOW_COMMENTS_TID = "bv2ne5qx5"
INV_FLOW_COMMENT_FID = {
    "RECORD_ID":    3,    # QB built-in
    "DATE_CREATED": 1,    # QB built-in
    "MSTYLE":       6,    # text FK
    "NOTE":         7,    # multi-line text
    "FLAG":         8,    # text (UI dropdown: Needs Action / Investigating / In Progress / Resolved / Dismissed)
    "AUTHOR":       9,    # user (auto-stamps)
}
```

**Action-engine rules (drives recommendations):**

| Rule | Imported | USA-made |
|---|:---:|:---:|
| Pull-up ETD (only if today + 7 < current ETD) | ✓ | ✓ |
| Faster vessel (in 8–15 day ETD window) | ✓ | ❌ |
| Push out / split | ✓ | ✓ |
| Cancel/reduce (only when furthest-out PO ETD > today + 60) | ✓ | ✓ |
| Min partial shipment qty | 2,500 pcs | 2,500 pcs |
| Air freight | ❌ never | ❌ never |
| ETA → warehouse lag | 7–10 days | typically 1–3 days (TBD) |

**Pipeline-overstock metric** (replaces per-week WOS for the overstock check):
```
pipeline = Beg(Wk1) + ΣI/T + ΣI/W
demand   = ΣPrj(Wk1..Wk26)
safety   = OptWOS × (demand / 26)
excess   = pipeline − demand − safety
overstocked if excess > 2,500 OR (pipeline × 26 / demand) > 33
```

**Gap detection** — same as Forecast Mgmt: any week before `Next Avl Rcpt Dt`
where Beg/Prj < Opt WOS, Replen items only.

**Output:** Excel "PO Change List" via openpyxl.
Grouped by Supplier > PO # > Line #.  Columns: orig/proposed ETD, orig/proposed qty,
Δ days, Δ qty, action reason.  Planner takes the spreadsheet to AS400 to apply.
No QB writeback for PO changes.

---

## Forecast Management Codepage — feature additions (2026-05-10/11)

The QB codepage (`codepage/viewer.html` + `viewer.js`) is the team-facing
forecast review tool — separate from `scripts/viewer.py` (single-user local).
Both viewers share the same JS render logic (mirrored manually) so features
added to one are added to the other in the same edit pass.

### New QB schema additions

| Asset | dbid / fid | Purpose |
|---|---|---|
| `AI Comments` table | dbid `bv2jirwts` (InventoryTrack) | Separate audit trail for planner-↔-AI dialogue (kept apart from mgr-facing `Projection Comments`). Fields: `Acct#-MStyle` (fid 6), `Note` (7), `Author` (8 — User field, auto-stamps), `Ignored` (9 — checkbox), built-in `Record ID#` (3), `Date Created` (1). |
| Bootstrap script | `scripts/create_ai_comments_table.py` | One-shot. Already run. Re-runnable idempotently if the table is ever deleted (uses QB REST `/v1/tables` + `/v1/fields`). |

### Detail-pane sections (in render order)

| Section | Rows shown | Source data |
|---|---|---|
| Projection table | Projection, AI Forecast, Suggested, Ordered LY, Shipped LY | Projections weekly fids + LY history |
| 📦 **Inventory Flow** (new) | Beg Inv, Expected Receipts, WOS OH (1-decimal) | Inventory Flow `Wk1..Wk26` / `RcvWk1..26` / `Prj Wk1..26` (numeric, stable fids — see `inspect_pog_invflow.py`) |
| ⚠️/✓ Gap Analysis banner (new) | "X gap weeks below Opt WOS before next receipt {date}" | `Opt WOS` / `OPT WOS Final` (prefers Final) + `Next Avl Rcpt Dt`. Only for items where `PT Item Status` contains "Replen" |
| 📅 POG / ISO context (new) | Editable POG Launch, POG End, Store Count + computed ISO order window, lead-time bands | Projections `POG Launch Date` (1594), `POG End Date` (1595), `Store Count` (14) |
| L26W Orders & Shipments history | Existing | Ord LW + Ord LW-1..LW-25 |
| 🤖 Adjust AI Forecast (Tell-AI) | Existing — writes to AI Comments table | F58 replays at next forecaster run |
| 📋 Comment History | Mgr-thread (Projection Comments) | Existing |

### localStorage cache (codepage only)

Inventory Flow data is the heaviest QB pull (~12 batches @ 125 mstyles each).
To avoid hammering QB with every codepage open by every user:

| Cache key | TTL | Bypass |
|---|---|---|
| `pp_invflow_v4` | 6 hours | Append `?nocache=1` to the codepage URL |

Stored per-browser per-user (`localStorage` on `pim.quickbase.com`). When
schema changes that affect the cached shape, bump the version string (`v2` →
`v3`) — all clients auto-invalidate on next load.

### Gap Analysis rule set (display only — no PO recommendations yet)

Implemented in `viewer.js` / `viewer.py` inline render:
- Window: W1 through the week containing `Next Avl Rcpt Dt`
- Gap = WOS < `Opt WOS Final` (or `Opt WOS` if Final empty) in that window
- WOS = Beg Inv ÷ Prj demand, 1 decimal point
- Replen filter: skip the banner if `PT Item Status` doesn't contain "Replen"
  (case-insensitive `\breplen\b`); other items show the Inv Flow rows but
  with a grey "Gap analysis only runs on Replen items" note

### Reserved for the future Inventory Management screen

A separate screen will surface **PO-specific action recommendations** (pull
forward / push out / split / cancel). These rules are captured here for the
build but **not** wired into the Forecast Management codepage:

| Rule | Imported (Country ≠ USA) | Made in USA |
|---|:---|:---|
| 7-day ETD lock | ✓ Applies | ✓ Applies |
| Pull-up (>15 days out) | ✓ Available | ✓ Available |
| Faster vessel/transport (8–15 days) | ✓ Recommend with target ETA | ❌ Not available |
| Push-out / split | ✓ | ✓ |
| 60-day cancel rule (overstock) | ✓ | ✓ |
| 2,500-pc partial minimum | ✓ | ✓ |
| ETA → warehouse lag | 7–10 days | TBD (typically shorter for domestic truck) |
| Air freight | ❌ Never | ❌ Never |

**Source fields on Inventory Flow** for the future screen:
- `Open_Supplier_POs` — multi-line text with `PO# - Supplier - I/T qty / I/W qty - ETD - ETA` per PO line
- `LT_Trans_Days` — total LT in days (production + transit)
- `Transit_Days` — transit portion only
- `Country` — country of origin (drives USA branching)

---

## What the script does

```
Mode: Validate Projections (--validate)
  Pulls same projection + history data as forecasting (Phase 1 + 2)
  For each record:
  ├── Computes baseline from L13W non-zero avg (fallback L26W, L52W)
  ├── Classifies demand pattern (reuses classify())
  ├── Applies seasonal profile and event calendar
  ├── For each of 26 manual projection weeks:
  │   ├── Compute expected range [baseline*seasonal*0.3 .. baseline*seasonal*2.0]
  │   ├── Flag: CRITICAL if >5x spike, inactive item with demand
  │   ├── Flag: WARNING if outside 0.3x-2.0x band, sudden stop,
  │   │         bi-weekly off-week, not master-pack multiple
  │   └── Generate human-readable reason per flag
  └── Outputs validation_results.json + launches viewer in validate mode
  Read-only — does NOT modify any Quickbase data.
```

```
Phase 1 — Pull projections
  SQL SELECT from Quickbase1.InventoryTrack.Projections
  Includes all Ord_LW, Ord_LW_1...Ord_LW_51 fields (52w order history)
  Filter: Status_Cust LIKE 'A%' + user scope

Phase 2 — Pull master pack
  SQL SELECT from Quickbase1.ProductTrack.Styles
  Field: Master_Pack (default 1 if missing)

Phase 2.5 — Pull Amazon Catalog POS (Amazon records only)
  SQL SELECT from Quickbase1.InventoryTrack.Amazon_Catalog
  Fields: Mstyle, Ordered_Units_LW, Avg_Units_Wk_L4w, Avg_Units_Wk_L13w,
          Avg_Units_Wk_L26w, Avg_Units_Wk_L52w
  Batched by 200 mstyles; stored in amazon_pos dict keyed by Mstyle
  Passed into forecast_record() and run_validation() via amazon_pos= param
  Used only when customer name contains "AMAZON" (AMAZON_CUST_SUBSTR gate)

EDA (if --analyze or --analyze-only)
  Data quality: active weeks, zero weeks, leading zeros, max gap
  Stationarity: rolling 4-week CV proxy (flag if >0.30)
  Intermittency: ADI/CV² quadrant classification
    (Smooth / Erratic / Intermittent / Lumpy)
  Outlier detection: IQR 3× upper fence on active values
  Calendar effects: observed lift vs mean in event windows
  Panel structure: customer/mstyle record counts
  → Generates self-contained HTML report (forecast_report.html by default)

Phase 3 — Forecast (pure Python, no API calls)
  For each record:
  ├── Classify: Zero L13W → Inactive (forecast = 0)
  │             Dense (≥50% non-zero) → Seasonal Baseline
  │             Intermittent (25-50% non-zero) → Croston's
  │             Sparse (<13 active weeks or <25% non-zero) → Heuristic
  │
  ├── [Fix 2] ISO routing override: if detect_iso() finds a stocking spike
  │   within L26W and pattern != inactive → force Heuristic regardless of CV/zeros
  │   (prevents Croston's from projecting repeat stocking spikes as recurring demand)
  │
  ├── Build 78-obs weighted series (3x L13W weight)
  │   Appends L13W twice to history so recent 13 weeks
  │   have 3x influence on level and trend estimates
  │
  ├── Seasonal Baseline (Dense branch — >=50% non-zero weeks):
  │   26 unique seasonal factors from L52W active history
  │   (70% recent cycle / 30% prior cycle, normalized, floor 0.25)
  │   Damped + category-blended profile applied to order-history baseline
  │   Post-forecast: bi-weekly cadence enforcement if detected
  │   (>=70% zero on one parity over L26W → merge pairs, zero off-weeks)
  │
  ├── seasonal_baseline() (Dense branch — ≥50% non-zero weeks):
  │   [Fix 3] Outlier cap: if max(L13W non-zero) > 3× median(L13W non-zero),
  │     cap spike values before computing avg (same for L26W fallback)
  │   Order-history baseline = L13W NON-ZERO avg (true per-order rate;
  │     excludes drawdown-zeros from post-event quiet periods)
  │     Fallback: L26W non-zero avg → L13W all-weeks avg
  │   [Fix 4] Bi-weekly correction: if detect_biweekly() and non-zero avg > all-weeks avg×1.05
  │     → use all-weeks avg instead (non-zero avg is ~2× weekly rate for bi-weekly items)
  │   Amazon POS blend (Amazon records only, when POS data available):
  │     baseline = ord_baseline×0.55 + pos_rate×0.45
  │     pos_rate() trend classification (L4W/L13W ratio):
  │       ≥1.15 → accelerating: pos_rate = L4×0.55 + L13×0.30 + L26×0.15
  │       ≤0.85 → decelerating: pos_rate = L4×0.35 + L13×0.45 + L26×0.20
  │       else  → stable:       pos_rate = L4×0.25 + L13×0.45 + L26×0.20 + L52×0.10
  │   Damped seasonal profile: DAMP=0.3 (normal) / DAMP=0.85 (F16-relief path)
  │     (prevents position-based distortion from e.g. holiday pre-buys
  │      landing in the wrong forecast-week slots)
  │   [Fix 1] Category seasonality blend (after DAMP, before event lifts):
  │     S = 0.30xhistorical_S + 0.70xcategory_profile (re-normalized)
  │     Applied in seasonal_baseline(), crostens(), and heuristic()
  │   Explicit event lifts (calendar-date-anchored, "take the greater" rule):
  │     All events: effective_lift = max(event_lift, item_seasonal_factor) for that week
  │     (floors at event level; does NOT stack with seasonal -- prevents double-lift on peak weeks)
  │     Prime Day (Amazon-only): tapered x1.10/x1.25/x1.25/x1.20 over 4-week window centered on anchor
  │     Fall Deal (Amazon-only): flat x1.12 over 3-week window centered on anchor
  │     T5/Thanksgiving (ALL accounts): x1.20/x1.15/x1.15 pre-event build (weeks 6/5/4 before Thanksgiving)
  │       + x1.15 post-event bump 1 week after Thanksgiving (Cyber Monday / Christmas list week)
  │       Thanksgiving = 4th Thursday of November (auto-computed annually)
  │       NOTE: the +1w post-event bump only falls in W1-W26 for runs on June 11 or later
  │
  ├── Croston's: alpha=0.3 over 78-obs weighted series
  │   z and p refined 70% L13W / 30% smoothed model output
  │   Quantities scaled by L52W seasonal profile + category blend
  │   Event calendar: calendar-based insertions using same anchor dates + "take the greater" rule
  │   [Fix 5] Rescaling cap: if AI 26w avg > L13W all-weeks avg × 1.10
  │     → scale down (floor 0.5×) to prevent over-projection vs true weekly rate
  │
  ├── Heuristic: ramp weeks 1-6 post-launch excluded
  │   Baseline: post-ramp avg → L13W non-zero avg → L52W avg → fallback
  │   Seasonal profile + category blend + event lifts applied
  │
  ├── Snap all non-zero qtys to master pack multiple
  │
  └── Variance vs manual projections (ORIG_PRJ_COLS) → AI_ALERT if >7.5%
      Alert includes model name, key drivers, seasonal/event notes

Phase 4 — Write-back
  Parallel UPDATE to AI_PRJ_W1-W26 + AI_ALERT
  Progress tracked every 50 records
  Resume file saved every 50 writes
```

---

## Output files

| File | Contents |
|---|---|
| `forecast_results.json` | Forecast output: `{meta: {generated_at, scope, prj_cols}, records: [...]}` |
| `forecast_results.completed.json` | Keys successfully written to QB (for resume) |
| `forecast_results.failures.json` | Keys that failed write-back (if any) |
| `forecast_report.html` | Self-contained EDA + forecast HTML report (if `--analyze`) |
| `validation_results.json` | Validation output: `{meta, summary, records}` with per-week flags (if `--validate`) |

---

## Writeback channels

Two paths are wired; `--all` scope picks the bulk path by default:

| Channel | Flag | When | Speed |
|---|---|---|---|
| QB REST bulk (`/v1/records` upsert) | `--bulk-writeback` (default for `--all`) | Hundreds of records | ~50x fewer HTTP hits |
| CData per-record `UPDATE` | `--no-bulk-writeback` | Single-record scope, debugging | Slower; matches read channel |

Both write to the same fields (`AI_PRJ_W1..W26`, `AI_ALERT`, `AI_ANALYSIS`).
Validation results (`Validation_*` fields) push via the separate
`scripts/push_validation_qb.py` script, always QB REST.

---

## Quickbase Schema Reference

| Detail | Value |
|---|---|
| Projections table | `Quickbase1.InventoryTrack.Projections` |
| Primary key | `Acct_MStyle_Key_` (format: `{acct}-{mstyle}`) |
| Active filter | `Status_Cust LIKE 'A%'` — **always** use `Status_Cust` (projection-level active status for the specific customer), never `Item_Status` (item-level, too broad) |
| Order history | `Ord_LW` (last week) + `Ord_LW_1`…`Ord_LW_51` (oldest) |
| GPT write-back | `AI_PRJ_W1`…`AI_PRJ_W26` + `AI_ALERT` |
| Master pack | `Master_Pack` on `Quickbase1.ProductTrack.Styles` |
| Amazon Catalog | `Quickbase1.InventoryTrack.Amazon_Catalog` (join on `Mstyle`) |
| POS fields | `Ordered_Units_LW`, `Avg_Units_Wk_L4w`, `Avg_Units_Wk_L13w`, `Avg_Units_Wk_L26w`, `Avg_Units_Wk_L52w` |
| Event windows | **Prime Day** (Amazon-only): x1.10/x1.25/x1.25/x1.20 centered on anchor date (configure `EVENT_DATES[year]["prime_day"]`; ~June 22 for 2026) · **Fall Deal** (Amazon-only): x1.12 centered on anchor (configure `EVENT_DATES[year]["fall_deal"]`; ~Oct 8 for 2026) · **T5** (ALL accounts): x1.20/x1.15/x1.15 on weeks 6/5/4 before Thanksgiving (auto-computed). All events use "take the greater" rule: `effective = max(event_lift, seasonal_factor)` -- no stacking. |
| Amazon gate | `AMAZON_CUST_SUBSTR = "AMAZON"` — all Prime Day lifts and POS pulls conditioned on this |
| Alert threshold | >7.5% variance vs manual projections |

---

## Fulfillment Mode Conventions (mstyle suffixes + AI Events)

Some mstyles carry a suffix that signals a non-standard fulfillment mode. These are **not** separate SKUs — they share the same item but ship differently.

| Suffix | Name | Meaning |
|---|---|---|
| `MPP` | Direct Import (DI) | Amazon orders direct from P+P's overseas factory. ~10 week transit to Amazon DC. Amazon writes POs 35–65 days before shipment. P+P does not project; F69 blends history into base. DI orders appear in Order History under **acct 61865** (not the warehouse Amazon acct). |
| `ADF` | Direct Import (DI) | Same as MPP — alternate DI suffix, same F69 treatment, same acct 61865. |
| `COS` | Cost / Direct Ship | Ships direct from factory to retailer/Amazon, bypassing P+P warehouse. Longer lead times. No warehouse inventory signal. (No forecaster logic yet.) |
| `EC` | eCommerce / Drop-Ship | eComm/FBA fulfillment variant. F60 inherits parent history when EC is sparse. |

**AI Event transitions**

When P+P switches a style to one of these modes, an AI Event notification is generated, e.g.:

> *"Switching to EC effective wk 15"*
> *"Switching to COS effective wk 8"*

The **effective week** is the boundary: orders before that week follow the prior fulfillment logic; orders from that week forward use the new mode's lead-time and ordering assumptions.

**Forecaster implications (current state — no code change yet):**
- COS/EC items are currently treated the same as standard items; the suffix is not yet detected.
- When a "Switching to COS/EC effective wk N" event appears, the forecaster should ideally shift lead-time assumptions and suppress near-term warehouse-dependent signals starting at wk N.
- Flag for future implementation if forecasting accuracy degrades post-switch.

---

## Key Model Constants

```python
# Calendar-anchored event dates -- update annually when Amazon announces
# Confirm Prime Day ~2 weeks before event; Fall Deal ~2 weeks before event
EVENT_DATES = {
    2026: {
        "prime_day": date(2026, 6, 22),   # CONFIRM ~2w before event
        "fall_deal": date(2026, 10, 8),   # CONFIRM: usually Oct 5-15
    },
}

# Prime Day lift schedule (offsets relative to anchor date, Amazon-only)
PRIME_DAY_LIFT_SCHEDULE_OFFSETS = {-1: 1.10, 0: 1.25, 1: 1.25, 2: 1.20}

# Fall Deal lift (offsets relative to anchor date, Amazon-only)
FALL_DEAL_LIFT_OFFSETS = {-1: 1.12, 0: 1.12, 1: 1.12}
FALL_DEAL_LIFT = 1.12

# T5 / Thanksgiving 5 -- ALL accounts, pre-event build
# Offsets relative to Thanksgiving (4th Thursday of November, auto-computed)
T5_LIFT_SCHEDULE_OFFSETS = {-6: 1.20, -5: 1.15, -4: 1.15}

AMAZON_CUST_SUBSTR = "AMAZON"
```

Event lift maps (`_AMAZON_EVENT_LIFTS`, `_ALL_EVENT_LIFTS`) are computed at startup
by `build_event_lift_map(forecast_start_date)` from today's W1 Sunday.
Maps are `{week_num: lift}` for W1-W26. T5 is derived from `_get_thanksgiving(year)`.

**"Take the greater" rule (all events, all models):**
- Event week: `effective = max(event_lift, item_seasonal_factor)`
- Non-event week: `effective = item_seasonal_factor` (no event floor applied)
- Never stack event lift on top of seasonal factor

**seasonal_baseline() profile dampening:**
- `DAMP = 0.1` (production), +-20% from 1.0
- Prevents position-based distortion (e.g. holiday pre-buys in Oct/Nov history
  landing in W1-W5 forecast slots and inflating front-weeks to 3-4x)
- Event lifts applied via "take the greater" -- not multiplicatively stacked

**Baseline logic (seasonal_baseline):**
- Order-history baseline = **L13W non-zero avg** (excludes post-event drawdown zeros
  that suppress the all-weeks avg; reflects true per-order quantity rate)
- Fallback: L26W non-zero avg → L13W all-weeks avg
- Amazon POS blend: 55% order-history baseline + 45% consumer POS demand rate

---

**CATEGORY_PROFILES — see `scripts/derived_category_profiles.json` for current values.**

> NOTE: The profile values previously listed here are stale (2026-05-23 audit). The live
> profiles are generated empirically by `build_category_profiles_from_report.py` from invoice
> history and stored in `scripts/derived_category_profiles.json`. The forecaster loads from
> that file at startup. Do not rely on hardcoded values here.
>
> Categories covered: outdoor grilling (charcoal, kingsford, chimney, lighter fluid, grill
> brush), insect/sun (mosquito, sunscreen, sun care), holiday/winter (christmas, ice melt,
> de-icer), air freshener, deodorizing, paper tableware, disposable tableware, and 40+ more.
> Run `python build_category_profiles_from_report.py` to rebuild from latest invoice data.

Matching logic: `(description or "").lower()` -- first keyword found wins (also checks
`product_category`, `product_subcategory`, `brand`, `brand_pt`). No match → no category blend
(pure historical + DAMP).

**Always consult these QB fields for seasonality context when forecasting a record:**
- `Description` / `Product_Title` — primary keyword source
- `Product_Category` (e.g. "Pet Air Care") and `Product_Subcategory` (e.g. "Beads")
- `Brand` / `Brand_PT_` (e.g. "Kingsford" → outdoor grill season Apr–Aug)
- `Status_Cust` — **this is the projection's active status for that specific customer** and is the correct filter for "is this projection active?" (`Status_Cust LIKE 'A%'`). Do **not** use `Item_Status` for this — `Item_Status` only indicates the item is generally active for the customer, not that the projection itself is active.

---

## Model Fix History

**The authoritative active-rule registry is [`RULES.md`](RULES.md).**
Reverted/superseded rules: [`CHANGELOG.md`](CHANGELOG.md).

This section is a chronological breadcrumb of when each rule family was added,
for context only. For current behavior and fire location, always check RULES.md.

| Date | Rules added | Theme |
|---|---|---|
| 2026-05-26 | F_DC_LAG, F_YOY_CADENCE, AMZ WOS target | **F_DC_LAG -- DC inventory lag correction:** POS and DC OH data lag one week. Computes `adj_DC_OH = DC_OH + Open_PO - LW_POS_Sales`, then adjusts W1-W2 by splitting `(target_WOS x rate - adj_OH)` evenly. Amazon target = 10 WOS (`AMZ_WOS_TARGET_MIN`); Retailer target = 8 WOS (`RTL_WOS_TARGET`). Fires only when `|adj_WOS - raw_WOS| >= 0.5 wks AND |delta/2| >= mp`. Skips Inactive, OTB(zero), Pre-launch, and Retailer WOS(POS) models. Fired 958 records on --all run. **F_YOY_CADENCE -- LY promo/holiday timing replication:** For items with >=3 non-zero LY order weeks, redistributes forecast units WITHIN event windows to match last year's actual weekly order shape. Window totals preserved exactly (pack-rounding residual absorbed into largest week). Amazon: Prime Day + Fall Deal boost-week windows (dynamic from `_get_event_boosts()`). Retailer: `TRADE_FALL_REPLEN_WEEKS` (W17-W18) + `TRADE_FALL_SEASON2_WEEKS` (W21-W22). Single-week windows skipped. F58 Tell-AI records skipped (planner wins). Fired 659 records on --all run across all retailer accounts. **AMZ WOS target:** Changed from midpoint (11.0) to `AMZ_WOS_TARGET_MIN = 10.0` wks. |
| 2026-05-26 | F87, F88 | **F87 deceleration guard** (`_compute_pos_baseline`): when L4W POS < L13W POS * 0.80, returns L4W immediately as the baseline before any spike/AUR logic -- prevents Amazon POS-WOS model from projecting at inflated historical rates on structurally declining items. **F88**: propagates the same L4W anchor into two downstream rules that independently anchor to L13W POS: (a) F59h extreme-overstock post-burn rate (when WOS > 20 and item is not replen-type, post-burn weeks now use L4W not L13W); (b) F73 DI post-receipt velocity anchor (suppress floor and post-suppress rate both use L4W when L4W < L13W * 0.80). Root cause: 1864-FF12689 was projecting 3,672/wk (L13W) for W14-W26 despite L4W POS = 1,693/wk (-54%); fix brings W14-W26 to 1,704/wk. |
| 2026-05-25 | Daily alert dismissal job | Standalone `dismiss_reviewed_alerts.py` + `daily_alert_dismiss.ps1`. Runs every day at 7:45am (Task Scheduler). Pulls AI Comments (REST), finds keys whose last note starts with "reviewed" or "fyi", bulk-clears AI_ALERT on matching Projections rows (REST). Independent of the weekly forecast run. |
| 2026-05-26 | F37 v2 (fresh-cascade), Phase 2.6d Inv Flow REST | F37 rewrite: replaces stale `Inv_Wk*` reads with fresh in-Python cascade using THIS run's AI projection. Reads raw Beg_Inv W1 + RcvWk0..26 + Opn_Wk0..26 from Inventory_Flow (`bpsaju5pm`) via REST API. RcvWk0/OpnWk0 roll into W1 per planner convention. Linear 25%/week decay against cohort's ORIGINAL unmet qty; cohorts expire at age 4. F37h-cat bypass REMOVED (no longer needed since cascade is fresh). Per-mstyle inventory pool; each acct-mstyle assumes full availability (planner decides cross-acct allocation at gametime). |
| 2026-05-25 | F37h-cat, F77/F77b/F61 cat-profile gates, Calming profile, QB REST Phases 1+2+2.5+2.6+2.6b, 21-finding audit | (A) Curated cat-profile bypass of seasonal-decay / inventory-shortfall rules (F77/F77b/F61/F37) so calming supplements and similar items keep their planner-intended holiday shape; calming profile added to `CATEGORY_PROFILES`; `_cat_mults` pre-computed once at line ~9007 in `forecast_record()` for both F61 + F37h-cat. (B) Phases 1+2+2.5+2.6+2.6b ALL migrated from CData full-table-scan loops to QB REST API: Projections `bpd237tvm`, Styles `bphzqfkev`, InventoryTrack Amazon Catalog `bqp8vz625`, ProductTrack Amazon Catalog US `bpfrw2epk` (app `bn458t5nz`), ProductTrack Amazon Invtry Health `bp9akd3js`. (C) 21-finding audit pass: F1 added Product_Category/Product_Subcategory/Brand to Phase 1 SELECT (empirical cat-profile system previously inert); F3 wired POS/season/catalog/retailer signals into validate_record's _prep_record_signals call (validator parity restored); F4 retailer_pos retry+raise surfaces partial-batch failures; F5 deleted duplicate `_cat_mults` compute; F6 qb_bulk_update treats empty 200 OK as throttle; F7 per-record resumability tracking (lineErrors no longer mark unwritten keys done); F8 legacy per-record CData UPDATE now gated behind `--allow-per-record-write`; F9 Phase 1 snapshot to `phase1_projections_cache.json`; F10 deleted dead `build_prj_select`/`build_prj_q1/q2/q3` legacy SQL builders; F12 cat-profile gate rejection log at end of run; F15+F16 `QB_REST_MAX_RETRIES=3` constant in config; F18 `RETAILER_POS_PAGE_DELAY_S` named constant; F21 validation try/except writes traceback to `validation_errors.json`. (D) SEASONAL_MIN_SKU_COUNT lowered from `>10` to `>=8` (constant value `7`, env-overridable) -- unlocked 13 at-boundary categories (Dog Supplements/Soft Chew, Dental Spray, Glass Care, Oven & Grill Care + Eye Care, Waste Bags, Water Additive, Pet Air Care + 5 more), net global demand shift -0.25%. |
| 2026-05-23 | (audit Phase 1) | Deleted stale METHODOLOGY.md; centralized constants in `scripts/config.py`; added `audit_rules.py` + `rule_dependency_graph.py`; promoted retry policy into `run_forecast.py`; schema-versioned output JSONs. |
| 2026-05-21 | F59o, F59i EC-override, F59m, F59n, F60, VP-Q4 false-abort fix | Seasonal overlay for Heuristic/Croston's; EC parent POS lookup; open-PO guard. |
| 2026-05-21 | (cleanup) | Removed `holt_winters()` function; renamed F31->F71, F6->F6a/b/c. |
| 2026-05-17 | F69, F69-WOS | Amazon direct-import sibling history blend + WOS-excess correction. |
| 2026-05-17 | F61, F62, F63, F64, F65, F66 | 8-priority sweep: horizon decay, soft trend blend, multi-pack floor, trade fall, zero-vel suppression, per-customer bias. |
| 2026-05-17 | F67, F68, Kingsford profile shift | Amazon buy-box $0 dampener, ASIN inactive-channel zero, retail-ordering peak shift for grilling. |
| 2026-05-17 | VP-ATS-Catch | Post-OOS catch-up spike cap. |
| 2026-04-22/23 | F-A, F-B, M1, M2, M3 | L13 burst baseline; burst-cadence Croston override; L52/L26 ceiling; EOL dampening; Croston acceleration-aware z blend. |
| 2026-04-21 | Fix 1, Fix 2, Fix 3, Fix 4, Fix 5 | Initial calibration: category seasonality, ISO routing, outlier cap, biweekly substitution, Croston rescale. |

When adding a new rule:
1. Add `_fire("...")` (or driver string) in `inventory_forecaster.py`.
2. Add a row to `RULES.md` describing what it does + where it fires.
3. Add a one-line entry to this table.
4. Run `python scripts/audit_rules.py` to verify no drift.
5. Add a unit test in `scripts/tests/` (Phase 2 -- once that scaffold exists).

---

## Narrative voice (`_build_alert()`)

Two-to-three short sentence retailer-planning style:
```
AI reads 30,468 units vs 1,920 planned (+1487%). Plan looks light - risk of
out-of-stock if orders hold pace. Plan is back-loaded (0% in first 13w vs 40%
implied).
```
Drops all algorithmic jargon (no alpha/beta, no "78-obs series", no model names).
Helper `_describe_manual_defects()` -> `_top_manual_defect()` surfaces the
single biggest defect in the manual plan (flat-line placeholder, front/back-
load skew, zero-plan weeks, unsupported spike, or under-plan gap) and appends
it as the final sentence.

## Viewer enrichment (`scripts/viewer.py`)

- `_enrich_from_quickbase()` fires at viewer load. One CData query pulls:
  Description, `Status_Cust`, `PT_Item_Status`, `Inventory_Manager`, and
  `Ord_LW`...`Ord_LW_25` (26 weeks). Results merged by `Acct_MStyle_Key_`.
- Viewer CData parser matches `inventory_forecaster._parse_cdata_result()`
  (unwraps `results[0].rows` -- important).
- Columns added: Description, Status @ Cust (pulled from `Status_Cust`),
  Item Status (from `PT_Item_Status`), Ord/Wk L13W, AI vs L13, Man vs L13.
- Volume tier (HIGH >= 500 prj/wk, MEDIUM 200-499, LOW < 200) and layered
  priority (CRITICAL = HIGH vol + |Delta|>10%, MEDIUM = MEDIUM vol + |Delta|>10%,
  LOW = rest).
- Detail row shows full 26-week ord + shp history (fields `history_l26_ord`
  and `history_l26_shp`).

---

## Summary Dashboard (build_dashboard.py)

A **one-shot totals dashboard** over all Active records. Run any time after
a forecast to see roll-up numbers for Manual vs AI vs Suggested vs L26 actual.

```bash
cd <skill_directory>
python build_dashboard.py
# → writes dashboard.html in skill root
# open in browser (Windows: Start-Process 'dashboard.html')
```

**What it shows:**
- Four totals cards: **Manual total · AI total · Suggested total · L26 orders actual**
- **Comparison matrix** (rows = projection type, columns = baseline):
  - Manual / AI / Suggested / L26 orders each as a row
  - Each cell = `(row − column) / column × 100` → Δ%
  - Positive = row higher than baseline · Negative = row lower
- **Variance distribution bars** — % of records that differ from manual by
  > 10% / > 25% / > 50%, computed separately for AI and for Suggested.
- Filter is `Status_Cust LIKE 'A%'` (Active at customer level).

**Columns pulled in the one CData query:**
- Manual: 26 date-stamped columns from `_make_prj_cols()`
- AI: `AI_PRJ_W1`…`AI_PRJ_W26`
- Suggested: `Suggested_Projection_Wk1`…`Suggested_Projection_Wk26`
- L26 orders: `Ord_LW` + `Ord_LW_1`…`Ord_LW_25`

**Output:** self-contained `dashboard.html` — no assets, no JS runtime, no
CData calls after load. Can be shared / emailed / printed. Regenerate
anytime by re-running the script.

**Trigger phrases:** *"build dashboard"*, *"summary dashboard"*, *"projection
totals"*, *"show me Manual vs AI vs Suggested"*, *"totals dashboard"*.

---

## Ad-hoc key-set analysis (analyze_36_keys.py)

Helper that takes a list of `Acct_MStyle_Key_` values, pulls each record's
Description / Status / 52w order history / Manual plan / AI forecast, and
writes a structured JSON report. Used to drill into methodology gaps on a
representative sample. Edit the `KEYS` list at the top of the script before
running. Output: `analysis_36_keys.json`.

---

## Codepage Viewer Fixes (applied 2026-05-21 -- Season column + inv flow header)

Changes to `codepage/viewer.js` and `codepage/viewer.html`:

| Change | Description |
|---|---|
| **Season column (viewer.html + viewer.js)** | Added sortable "Season" column to main table header immediately after "Status @ Cust". Cell renders `r.season_tag` (FID 1583, text) in purple. Empty for items with no season tag. colspan updated 24 -> 25. |
| **[S] badge removed** | Removed the orange `[S]` seasonal badge from the row-badges td. The badge was firing for both A: Promo and A: OffPrice items because `is_seasonal` matches both, causing false positives on A: Promo items like 1025-FF4771. |
| **Season FID 1583 added** | `F.SEASON = 1583` added to viewer.html FID block. `season_tag` field populated on each record from QB; included in SELECT fids. |
| **Seasonal bullet (AI analysis)** | Replaced the old full-width yellow "Seasonal/Occasional Account" alert box with a compact bullet in the AI analysis narrative. Bullet fires only when `r.is_offprice || !!r.season_tag`. A: Promo items without a season tag no longer get any seasonal treatment. When both A: Promo status and a season tag are present, the bullet adds a note that "retailer will likely place a manual buy to cover the in-season window." |
| **Next Avl Rcpt Dt card** | Added "Next Avl Rcpt Dt" info card in the detail pane immediately after the "Next Rcpt" card, showing the date from `r.inv_flow_next_rcpt`. |
| **Red asterisk on inv flow table** | In the inv flow week-by-week table header, the column corresponding to `inv_flow_next_rcpt` gets a red superscript `*` so planners can immediately spot which week new inventory becomes available. |

---

