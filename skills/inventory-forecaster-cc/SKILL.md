---
name: inventory-forecaster
description: "End-to-end AI inventory demand forecaster and projection validator for Pets+People. Runs as a self-contained Python script inside Claude Code. Use this skill whenever the user wants to forecast, project, or predict future order quantities, OR validate/sanity-check existing manual projections against historical order patterns, OR analyze the gap between manual projections and AI forecasts to improve the model, OR build a summary dashboard of Manual vs AI vs Suggested totals. Triggers include: 'run forecaster', 'project next 26 weeks', 'update the GPT projections', 'run AI projections for [scope]', 'validate projections', 'check my projections', 'are my projections reasonable', 'sanity check the numbers', 'run analysis first then forecast', 'analyze gaps', 'compare my projections to AI', 'where is AI wrong', 'improve forecasting accuracy', 'build dashboard', 'summary dashboard', 'projection totals dashboard', or any request to generate AI_PRJ_W1-W26 write-back for Quickbase. Handles the entire workflow: pulls order history from Quickbase, optionally runs EDA, classifies SKUs, runs Holt-Winters / Croston's / Heuristic / Inactive models, validates manual projections against historical baselines, detects anomalies (spikes, sudden stops, bi-weekly misalignment, master pack violations), writes results back, logs alerts, runs a reusable gap-analysis report (manual vs AI) that proposes concrete model fixes, and builds a Manual/AI/Suggested totals dashboard (`build_dashboard.py` → `dashboard.html`)."
---

# Inventory Forecaster — Claude Code Skill

Runs `scripts/inventory_forecaster.py` to execute the full forecasting pipeline.
The script calls the CData MCP server directly (Basic auth) for all Quickbase I/O — no Anthropic SDK or API key required.

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
   - **Step 3 — Enrich** each record with computed metrics: `delta_pct` (manual vs AI % gap), `direction` (UP/DOWN/FLAT), `man_zeros` (zero-week count in manual plan), `killed` (manual plan is all zeros), `front_load_score` (what % of demand is in first 13 weeks), `spike_weeks` (weeks where manual is ≥3× median), `man_vs_l13` and `ai_vs_l13` (each plan total relative to L13W avg × 26), `trend_ratio` (L4W/L13W), `vol_tier` (HIGH ≥ 1,000/wk · MEDIUM 200-999 · LOW < 200).
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
CData connection.` (or any other transient pull failure), **do not assume the
PAT expired** and **do not stop**. The PAT is permanent; the failure is almost
always transient (CData rate-limit, QB hiccup, brief network blip).

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

`run_forecast.py` monitors the child process and restarts it automatically
up to **3 times** if:
- No output for 1200s (20 min) → process hung, kill and restart
- Process exits with non-zero return code → restart after 5s

If all 3 watchdog attempts are exhausted, Claude should **wait 5 minutes
then restart the whole watchdog** — up to 3 such outer retries before
escalating to the user. Total maximum attempts = 9 (3 watchdog × 3 outer).

Track outer retries explicitly:
```
Outer attempt 1/3 → python run_forecast.py --all --validate (watchdog handles inner 3)
Outer attempt 2/3 → (after 5 min wait)
Outer attempt 3/3 → (after 5 min wait)
```

A run is **successful** when the log contains both:
- `Validation complete` (step 3/3 finished)
- `COMPLETE` (step 4/4 writeback finished — the watchdog summary line)

### Step 4 — Report results to the user

After the script completes, summarize:
- Total records processed and model split (Seasonal Baseline / Croston's / Heuristic / Inactive)
- Number of bi-weekly cadence records enforced
- Total 26-week demand volume
- Number of ALERT records (>7.5% variance vs manual projections)
- Path to HTML report (if `--analyze` was used)
- Any failures and how to retry

After a **validate** run, immediately launch the Projection Validation Viewer:

```bash
cd <skill_directory>
python scripts/viewer.py --results validation_results.json
```

The viewer opens automatically in the browser at `http://127.0.0.1:8765`.

### Step 5 — Projection Validation Viewer (viewer.py)

The viewer is read-only — it displays validation flags only, no write-back.

- Compact review table showing Projected vs Expected totals per record with severity (CRITICAL / WARNING / CLEAN)
- Click any row to expand the full W1–W26 week-by-week flag detail
- Filter by severity, search by key/mstyle
- **Does not** have Accept / Accept All / Email Summary — those have been removed

```bash
# Re-open the viewer at any time against a prior results file:
python scripts/viewer.py --results validation_results.json
```

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
4. Amazon Prime Day pre-buy gap — manual front-loads W5-W9, AI only lifts W7-W9
5. Sparse/intermittent baseline too conservative — use MAX(L13, L26, L52) non-zero avg
6. Declining item over-forecast — L4W << L13 avg, AI held baseline
7. Isolated spike over-forecast — outlier cap didn't neutralize

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
  │             Steady (CV≤0.5, zeros≤20%) → Holt-Winters
  │             Intermittent (CV>0.5 or zeros>20%) → Croston's
  │             Sparse (<13 active weeks) → Heuristic
  │
  ├── [Fix 2] ISO routing override: if detect_iso() finds a stocking spike
  │   within L26W and pattern ≠ inactive → force Heuristic regardless of CV/zeros
  │   (prevents Croston's from projecting repeat stocking spikes as recurring demand)
  │
  ├── Build 78-obs weighted series (3x L13W weight)
  │   Appends L13W twice to history so recent 13 weeks
  │   have 3× influence on level and trend estimates
  │
  ├── Holt-Winters: recursive α=0.3/β=0.1 over 78-obs series
  │   Level L and trend T converge with 3x weight on L13W
  │   26 unique seasonal factors from L52W active history
  │   (70% recent cycle / 30% prior cycle, normalized, floor 0.25)
  │   Cap: L13W avg×1.25 normal, ×1.50 event weeks (downward only)
  │   Post-forecast: bi-weekly cadence enforcement if detected
  │   (≥70% zero on one parity over L26W → merge pairs, zero off-weeks)
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
  │   Damped seasonal profile: DAMP=0.1 → profile stays within ±20% of 1.0
  │     (prevents position-based distortion from e.g. holiday pre-buys
  │      landing in the wrong forecast-week slots)
  │   [Fix 1] Category seasonality blend (after DAMP, before event lifts):
  │     S = 0.30×historical_S + 0.70×category_profile (re-normalized)
  │     Applied in seasonal_baseline(), crostens(), and heuristic()
  │   Explicit event lifts applied on top of damped+category profile:
  │     Prime Day W7-W9 ×1.25 (Amazon only — May ordering, ~6-8 wks before July event)
  │     Fall Deal  W23-W25 ×1.12
  │
  ├── Croston's: α=0.3 over 78-obs weighted series
  │   z and p refined 70% L13W / 30% smoothed model output
  │   Quantities scaled by L52W seasonal profile + category blend
  │   Event calendar: insertions at Prime Day W7-W9 (Amazon only), Fall Deal W23-W25
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
| Event windows | Prime Day: **W7-W9** (+25% lift, Amazon only — May ordering) · Fall Deal: W23-W25 (+12% lift) |
| Amazon gate | `AMAZON_CUST_SUBSTR = "AMAZON"` — all Prime Day lifts and POS pulls conditioned on this |
| Alert threshold | >7.5% variance vs manual projections |

---

## Model Fixes (applied 2026-05-17 — DI direct-import sibling blending)

| Fix | Rule Code | Description |
|---|---|---|
| **F69 — DI sibling history blend** | `F69`, Phase 2.9b pre-pass | Amazon (and sometimes other customers) order product direct from P+P's overseas factory ("Direct Import" / "DI"). These variants share the base mstyle but carry a suffix of **MPP** or **ADF** (e.g., FF8654MPP, FF8654ADF). Amazon writes its own POs 35–65 days before factory shipment (~10 week transit to Amazon DC); P+P does not project for them. Crucially: MPP/ADF have **no Projections record** — they exist only in Order History (`bpe4maa4c`). They also order **concurrently** with the warehouse base record (unlike EC which is a switchover). Fix: in a pre-pass (Phase 2.9b), generate candidate sibling keys for every base record, query Order History via `fetch_clean_demand()`, and **accumulate the sibling's `raw_ord` 52-week array into the base row's ORD_COLS in-place** (`raw_ord[i]` aligns 1:1 with `ORD_COLS[i]`, both oldest→newest). The forecaster then sees total demand (warehouse + factory-direct) without any model logic changes. Driver note added to base record: "F69 DI blend: FF8654MPP(+NNN L13), FF8654ADF(+NNN L13) direct-import history added to base demand signal." |
| **F69-WOS — DI POS-anchor + WOS-excess correction** | `F69-wos`, post-model | After the model runs on the blended history, the combined order signal may still understate demand due to lumpy DI cadence. For DI-blended Amazon records, **anchor the 26-week forecast to the consumer POS L13W rate**, then apply a WOS-excess reduction for any inventory Amazon holds above its ~12-week target. Formula: `excess = max(0, wos − 12)`, `scale = max(0.70, 1 − excess/26)`, `target/wk = pos_l13w × scale`. Proportional rescale preserves seasonal shape. Covers all 26 weeks (supersedes F59h's 8-week soft taper for DI-blended records). Example: POS=2,858/wk, WOS=16.2→ excess=4.2 → scale=0.838 → target≈2,394/wk. |

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
PRIME_DAY_WEEKS    = {7, 8, 9}      # mid-May pre-order (Amazon only — orders ~6-8 wks before July consumer event)
FALL_DEAL_WEEKS    = {23, 24, 25}   # early-Sep pre-order
EVENT_WEEKS        = PRIME_DAY_WEEKS | FALL_DEAL_WEEKS
PRIME_DAY_LIFT     = 1.25
FALL_DEAL_LIFT     = 1.12
AMAZON_CUST_SUBSTR = "AMAZON"
```

**seasonal_baseline() profile dampening:**
- `DAMP = 0.1` → profile stays within ±20% of 1.0
- Prevents position-based distortion (e.g. holiday pre-buys in Oct/Nov history
  landing in W1-W5 forecast slots and inflating front-weeks to 3-4×)
- Explicit Prime Day and Fall Deal event lifts are applied on top of the dampened profile

**Baseline logic (seasonal_baseline):**
- Order-history baseline = **L13W non-zero avg** (excludes post-event drawdown zeros
  that suppress the all-weeks avg; reflects true per-order quantity rate)
- Fallback: L26W non-zero avg → L13W all-weeks avg
- Amazon POS blend: 55% order-history baseline + 45% consumer POS demand rate

---

## Model Fixes (applied 2026-04-21)

| Fix | Description |
|---|---|
| **Fix 1 — Category seasonality** | CATEGORY_PROFILES dict keyed by description keyword. Monthly multipliers blended 70% category / 30% historical profile, re-normalized. Applied in `seasonal_baseline()`, `crostens()`, `heuristic()`. |
| **Fix 2 — ISO routing** | `detect_iso()` flags records with a stocking spike within L26W. These are routed to Heuristic regardless of CV/zeros, preventing Croston's from projecting repeat spikes. |
| **Fix 3 — Outlier cap** | Before computing L13W / L26W non-zero avg: if max > 3× median, cap spike values. Prevents a single order event from inflating the baseline. |
| **Fix 4 — Bi-weekly baseline** | If `detect_biweekly()` and non-zero avg > all-weeks avg × 1.05, substitute all-weeks avg. Non-zero avg is ~2× the true weekly rate for bi-weekly cadence items. |
| **Fix 5 — Croston's rescaling** | After Croston's produces 26 forecast values: if AI avg > L13W all-weeks avg × 1.10, scale down (floor 0.5×) to keep total demand grounded to observed weekly rate. |

**CATEGORY_PROFILES keywords → monthly multipliers [Jan…Dec]:**

```python
CATEGORY_PROFILES = {
    # Outdoor cooking / grilling — peak Apr–Aug
    "charcoal":      [0.20, 0.25, 0.65, 1.50, 1.90, 2.05, 1.80, 1.50, 0.80, 0.40, 0.22, 0.20],
    "chimney":       [0.20, 0.25, 0.65, 1.50, 1.90, 2.05, 1.80, 1.50, 0.80, 0.40, 0.22, 0.20],
    "fire starter":  [0.20, 0.25, 0.65, 1.45, 1.85, 2.00, 1.75, 1.45, 0.80, 0.40, 0.22, 0.20],
    "firestarter":   [0.20, 0.25, 0.65, 1.45, 1.85, 2.00, 1.75, 1.45, 0.80, 0.40, 0.22, 0.20],
    "lighter fluid": [0.20, 0.25, 0.65, 1.45, 1.85, 2.00, 1.75, 1.45, 0.80, 0.40, 0.22, 0.20],
    "grill brush":   [0.25, 0.30, 0.70, 1.40, 1.80, 1.95, 1.70, 1.40, 0.80, 0.40, 0.25, 0.22],
    # Insect / sun — peak May–Sep
    "mosquito":      [0.20, 0.20, 0.45, 1.10, 1.65, 1.95, 2.05, 1.80, 1.40, 0.60, 0.25, 0.20],
    "insect repel":  [0.20, 0.20, 0.45, 1.05, 1.60, 1.90, 2.00, 1.75, 1.35, 0.60, 0.25, 0.20],
    "bug repel":     [0.20, 0.20, 0.45, 1.05, 1.60, 1.90, 2.00, 1.75, 1.35, 0.60, 0.25, 0.20],
    "sunscreen":     [0.20, 0.25, 0.60, 1.25, 1.75, 2.05, 2.05, 1.65, 0.90, 0.40, 0.25, 0.20],
    "sun care":      [0.20, 0.25, 0.60, 1.25, 1.75, 2.05, 2.05, 1.65, 0.90, 0.40, 0.25, 0.20],
    "sunblock":      [0.20, 0.25, 0.60, 1.25, 1.75, 2.05, 2.05, 1.65, 0.90, 0.40, 0.25, 0.20],
    # Holiday — peak Nov–Jan
    "holiday":       [1.50, 0.60, 0.40, 0.40, 0.50, 0.60, 0.70, 0.80, 1.20, 1.85, 2.30, 2.50],
    "christmas":     [1.50, 0.60, 0.40, 0.40, 0.50, 0.60, 0.70, 0.80, 1.20, 1.85, 2.30, 2.50],
    # Winter — peak Dec–Feb
    "ice melt":      [1.80, 1.70, 0.90, 0.25, 0.15, 0.15, 0.15, 0.20, 0.40, 1.00, 1.80, 2.00],
    "de-icer":       [1.80, 1.70, 0.90, 0.25, 0.15, 0.15, 0.15, 0.20, 0.40, 1.00, 1.80, 2.00],
}
```

Matching logic: `(description or "").lower()` — first keyword found wins. No match → no category blend (pure historical + DAMP).

**Always consult these QB fields for seasonality context when forecasting a record:**
- `Description` / `Product_Title` — primary keyword source
- `Product_Category` (e.g. "Pet Air Care") and `Product_Subcategory` (e.g. "Beads")
- `Brand` / `Brand_PT_` (e.g. "Kingsford" → outdoor grill season Apr–Aug)
- `Status_Cust` — **this is the projection's active status for that specific customer** and is the correct filter for "is this projection active?" (`Status_Cust LIKE 'A%'`). Do **not** use `Item_Status` for this — `Item_Status` only indicates the item is generally active for the customer, not that the projection itself is active.

---

## Queued Model Fixes (surfaced by gap_analysis.py — 2026-04-21 run)

Top 104 high-volume acct 1864 records: AI total **2.73M units** vs manual **3.73M units**
= **-26.8%** (-1.0M unit gap). Root-cause buckets ranked by absolute gap:

| # | Bucket | Records | Unit Gap | Proposed Fix |
|---|---|---:|---:|---|
| **F6** | Inactive-with-Activity (misclassification) | 4 | 131,600 | In `classify()`: don't return "inactive" if L26W non-zero weeks ≥ 4 OR L52W non-zero weeks ≥ 8. Route to Heuristic with `baseline = MAX(L26W nz avg, L52W nz avg)`. Keep true zero-activity SKUs (whole L52W zero) inactive. |
| **F7** | Seasonal-ramp under-forecast (peak >> trough) | 13 | 164,874 | Add peak-anchored baseline: when category profile matches AND L52 peak > 3× L13 non-zero avg, compute `peak_baseline = avg(L52 weeks in category peak months)` and anchor seasonal curve: `week_qty = peak_baseline × cat_mult[w] / max(cat_mult)`. |
| **F8** | Seasonal category not in CATEGORY_PROFILES | 9 | 102,281 | Expand match inputs to include `Product_Category`, `Product_Subcategory`, `Brand`, `Brand_PT_`. Add new profiles: `kingsford` / `fabuloso` / `fraganzia` / `air freshener` / `deodorizing ball` / `scent booster` / `paper bowl` / `snack bowl` / `grill cleaner` / `wooden fire`. |
| **F9** | Sparse/intermittent baseline too conservative | 6 | 93,221 | For `sparse_intermittent` & `intermittent` with annual_volume > 15K: baseline = `MAX(L13 nz avg, L26 nz avg, L52 nz avg)` instead of L13-first-fallback chain. |
| **F10** | Declining item over-forecast | 5 | 74,729 | Detect end-of-life: if L4W avg < L13W nz avg × 0.7, blend 26w forecast = 0.5×model + 0.5×L4W avg. Further down-weight W14-W26 by 0.85×. |
| **F11** | Amazon Prime Day pre-buy gap (W5-W9) | 3 | 36,350 | Replace flat W7-W9 ×1.25 lift with tapered ramp: W5=1.10, W6=1.15, W7=1.25, W8=1.25, W9=1.20 (Amazon only). Matches buyer pre-buy behavior observed in manuals. |
| **F12** | Isolated spike over-forecast (outlier cap) | 2 | 14,878 | Tighten Fix 3: lower cap from 3.0× → 2.5× median. Add secondary check: if max(L13 nz) > 2× L13_all_avg AND max occurs only once, cap at 2× L13_all_avg. |

**Workflow for applying queued fixes:**
1. Pick highest-gap bucket (F6/F7/F8 first).
2. Implement in `inventory_forecaster.py`, add reference to this table.
3. Re-run `--validate --acct 1864` → `gap_analysis.py` to verify the bucket narrowed.
4. Move the fix from Queued → applied (Fix N).
5. Update SKILL.md with the diff in impact.

---

## Model Fixes (applied 2026-05-17 — 8-priority algorithm improvements)

Sourced from `scripts/analyze_manual_vs_ai.py` run on 2,000 active projections.

| Fix | Rule Code | Description |
|---|---|---|
| **Priority 1 — Order-cadence for all branches** | `apply_ordering_pattern` | Previously only called for dense (Seasonal Baseline) items. Now also called for Croston's and Sparse Intermittent branches, so monthly+ cadence items get demand clustered into order-week chunks across ALL model paths. Bi-weekly (gap=2) is still smoothed to weekly per VP-Q3. |
| **Priority 2 — Horizon confidence decay** | `F61` | W9-W26 forecast weeks multiplied by ×0.88 for non-Amazon, non-seasonal, non-new-launch items. Planners systematically trim the back half of AI forecasts; this matches observed behavior without cutting near-term W1-W8 signal. |
| **Priority 3 — Channel suppression** | OFFPRICE_CUST_SUBSTRS | Added DD'S DISCOUNTS, DD'S DISCOUNT, GABRIEL BROTHERS to the off-price customer list. These now get R1/OTB-zero routing (same as Ross, Burlington, etc.). |
| **Priority 4 — Soft L4W/L13W trend blend** | `F62` | Fills the gap between F26 (hard ×0.85 at 50-70%) and no-action. Ratio 0.70-0.88 → proportional blend ×(0.6×ratio+0.4) ≈ ×0.82 to ×0.93. Ratio 1.12-1.30 → proportional uplift. Skips Amazon (POS blend handles it). |
| **Priority 5 — Multi-pack baseline floor** | `F63` | For Multi-Pk Replen items where L26W nz avg is ≥1.5× L13W nz avg, lifts the forecast to at least 40% of L26W nz avg × 26w. Addresses the 743% avg delta gap on multi-pack items. |
| **Priority 6 — Trade calendar fall events** | `F64` | W17-W18 (early Sept fall replenishment) ×1.10; W21-W22 (early Oct holiday pre-order) ×1.08 for all non-Amazon items. Most common planner spike weeks in manual projections. |
| **Priority 7 — Zero-velocity suppression** | `F65` | When BOTH L4W and L13W are completely zero (not new launch, not international), skips R3/S6/F19 floors entirely. Prevents the AI from inventing demand with no recent signal. |
| **Priority 8 — Per-customer bias correction** | `F66`, `CUSTOMER_BIAS_CORRECTIONS` | Applies a calibration multiplier for customers where planners override AI >75% of the time in the same direction. PSP Distribution/Theis ×1.25 (AI under-projects); Imperial Distributors ×1.35; Army-Air-Force Exch ×1.40; Pet Pharm ×0.55; H G Buying ×0.45; Petco Mexico ×0.45. |

---

## Model Fixes (applied 2026-05-17 — Kingsford lead-time + Amazon buybox)

| Fix | Rule Code | Description |
|---|---|---|
| **Kingsford profile shift** | `CATEGORY_PROFILES["kingsford"]` | Shifted all grilling profiles (kingsford, charcoal, chimney, fire starter, grill brush, etc.) from consumer-demand peak (May–Jun) to **retail ordering peak (Feb–Apr)**. Retailers place orders 8–10 weeks before consumer grilling season. Prior profile over-projected May–Aug; planners were cutting AI by -45.8% aggregate. New profile: Jan 0.50 → Feb 1.20 → Mar 1.90 → **Apr 2.10** → May 1.70 → Jun 1.30 → Jul 0.70 → Aug 0.40 → Sep–Nov 0.22–0.25. |
| **F61 category-profile guard** | `F61` | Added `not _f61_has_cat_prof` to F61 horizon-decay condition. Category-profiled items (Kingsford, charcoal, sunscreen, holiday, ice melt, etc.) are already getting the correct seasonal shape — F61's ×0.88 back-half decay should not overwrite a known seasonal curve. |
| **F67 — Amazon buy-box $0 dampener** | `F67` | When `Amazon_Buybox == 0` (listing live but no active buy-box price — pricing hold, compliance review, 3P flush) and the item is NOT already in F38f's "Not Buyable" path: W1–W4 cut by 70% (×0.30), W5–W26 unchanged. Assumes buybox restoration within ~4 weeks. Item flagged with driver note. |
| **F68 — Amazon inactive-channel long-term zero** | `F68`, `ASIN_Status` fetched | Two-gate hybrid. Gate 1: if `ASIN_Status` contains "active" or "FD" (Forecasted Demand), Amazon's buying system confirms the item should be ordering — skip F68 regardless of order silence. Gate 2: if not confirmed active AND L13W all-weeks = 0 AND L26W ≤ 2 non-zero weeks → zero out AI (long-term channel issue assumed: brand-Amazon fit, listing compliance, VC program ended). Catches brands like Fraganzia/Fabuloso (multicultural market brands that don't convert on Amazon) and hyper-competitive pet grooming categories where the ASIN lost search position and the buyer stopped ordering. `ASIN_Status` added to Phase 2.6 catalog fetch. |

---

## Model Fixes (applied 2026-05-17 — ATS catch-up spike cap)

| Fix | Description |
|---|---|
| **VP-ATS-Catch** | Companion to VP-ATS. After ATS restores from an OOS period (≥2 prior weeks near-zero ATS), caps orders in the 1–3 weeks following restoration to the pre-OOS baseline when they exceed 1.5× that baseline. Prevents duplicate / pent-up catch-up orders from inflating the L13W nz-avg and over-projecting. Per planner feedback: 1864-FF9297/24, weeks 2/15 & 2/22. |

---

## Model Fixes (applied 2026-04-22/23 — cadence & over-projection control)

These supplement the `Fix 1`–`Fix 5` table above. All live in
`scripts/inventory_forecaster.py`.

| Fix | Where | Rule |
|---|---|---|
| **F-A — L13 burst baseline** | `seasonal_baseline()` (just after L13 non-zero avg computed) | When L13 has ≥ 4 zero weeks (≥ 25% L13 zero-rate), switch the baseline from **L13 non-zero avg** to **L13 all-weeks avg**. The non-zero avg captures order SIZE, not the weekly rate, and × 26 over-projects for burst/drawdown accounts (Amazon pre-buy cycles, promo-driven retailers). |
| **F-B — Burst-cadence override** | `classify()` / dense-route branch in `forecast_record()` | When `is_dense` (L26 nz-rate ≥ 50%) AND L13 has ≥ 4 zero weeks AND not ISO: downgrade to Croston's path. Forces international / distributor accounts (Loblaws, Wakefern, Petbarn) off the smooth weekly-rate model onto lumpy order-size × cadence. |
| **M1 — L52/L26 ceiling** | End of `forecast_record()` before prior/pct | `26w total ≤ max(L52 × 1.25, L26 × 1.25)`. Prevents runaway over-projection on items with thin long-run history or items where recent acceleration + POS blend + event lifts compound. Skipped for `model == "Inactive"` and for items with L52 total < 1,000 units (legitimate ramps need headroom). Scales all weeks proportionally and re-snaps to master pack. |
| **M2 — Phase-out / EOL dampening** | After M1 in `forecast_record()` | Three OR-signals fire dampening:<br>• `Status_Cust` / `PT_Item_Status` contains one of: `DISC` / `DEL` / `LIQ` / `END` / `OBSOLETE` / `PHASE`<br>• **Stale-order**: L13 orders = 0 AND last non-zero order ≥ 26 weeks ago<br>When any signal fires (and model ≠ Inactive), cut forecast to `max(AI × 30%, manual)` and re-snap. Requires `PT_Item_Status` in the Projections SELECT (see `build_prj_select`). |
| **M3 — Croston's acceleration-aware z blend** | Inside `crostens()` at z refinement | Default blend is 70% L13 actuals / 30% smoothed. When **L13 non-zero avg ≥ 1.05× L26 non-zero avg** (mild acceleration), shift to **90% L13 / 10% smoothed** so order sizes reflect the newer, larger orders instead of being pulled down by older smoothed values. |

**Order of operations inside `forecast_record()` after the model produces `fcst`:**
1. Cadence shaping (bi-weekly, ISO, etc.)
2. **M1 ceiling** (scales + snaps to MP)
3. **M2 EOL dampening** (scales + snaps to MP)
4. `prior`, `new`, `pct` computed
5. Business-voice narrative built via `_build_alert()` (new short-form retailer language, replaces the old algorithmic-jargon alert)

### Projections table SELECT additions

`build_prj_select()` must pull these fields for M2 and for viewer enrichment:

- `Status_Cust` (already present — used for M2 EOL-token match)
- `PT_Item_Status` (**added** — item-level status for M2)

### Narrative voice (new `_build_alert()`)

Two-to-three short sentence retailer-planning style:
```
AI reads 30,468 units vs 1,920 planned (+1487%). Plan looks light — risk of
out-of-stock if orders hold pace. Plan is back-loaded (0% in first 13w vs 40%
implied).
```
Drops all algorithmic jargon (no α/β, no "78-obs series", no model names).
Helper `_describe_manual_defects()` → `_top_manual_defect()` surfaces the
single biggest defect in the manual plan (flat-line placeholder, front/back-
load skew, zero-plan weeks, unsupported spike, or under-plan gap) and appends
it as the final sentence.

### Viewer enrichment (scripts/viewer.py)

- `_enrich_from_quickbase()` fires at viewer load. One CData query pulls:
  Description, `Status_Cust`, `PT_Item_Status`, `Inventory_Manager`, and
  `Ord_LW`…`Ord_LW_25` (26 weeks). Results merged by `Acct_MStyle_Key_`.
- Viewer CData parser matches `inventory_forecaster._parse_cdata_result()`
  (unwraps `results[0].rows` — important).
- Columns added: Description, Status @ Cust (pulled from `Status_Cust`),
  Item Status (from `PT_Item_Status`), Ord/Wk L13W, AI vs L13, Man vs L13.
- Volume tier (HIGH ≥ 1,000 prj/wk · MEDIUM 200–999 · LOW < 200) and layered
  priority (CRITICAL = HIGH vol + |Δ|>10% · MEDIUM = MEDIUM vol + |Δ|>10% ·
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

## Model Fixes (applied 2026-05-21 -- EC parent lookup + VP-Q4 false abort)

| Fix | Rule / Location | Description |
|---|---|---|
| **EC parent POS lookup** | Phase 2.5 -- `amazon_mstyles` set construction | EC and COS mstyle variants (e.g. "FF12302/24EC") have their POS and DC inventory data stored in Amazon_Catalog under the parent mstyle ("FF12302/24"). Fix: `amazon_mstyles` query set is expanded to include parent variants before the QB pull -- `amazon_mstyles = list(_amz_raw | {_ec_parent_for_query(m) for m in _amz_raw})` -- so the cache is keyed correctly and F59i / narrative can anchor to real POS data for EC items. |
| **VP-Q4 false abort on zero open-PO items** | VP-Q4 guard at `len(open_pos_data) == 0` | VP-Q4 zero-out requires open-PO data to be present. When running a single-item scope for an item that genuinely has no open POs the old guard fired the abort incorrectly (treated "no matched keys" same as "CData returned nothing"). Fix: when `len(open_pos_data) == 0`, check `open_pos_report.json` cache for row count. If cache has rows, the pull was healthy -- item just has no POs, no abort needed. Only abort if cache itself is empty (real pull failure). |

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

## Model Fixes (applied 2026-05-21 -- Option A: seasonal overlay for Heuristic/Croston's)

| Fix | Rule / Location | Description |
|---|---|---|
| **F59o seasonal overlay** | `forecast_record()` -- inserted before F59/F60 block | Applies the category profile as an additive floor to Amazon Heuristic and Croston's forecasts (Option A). The existing heuristic()/crostens() category blend normalizes to mean=1.0, which can pull off-months BELOW the flat baseline. F59o uses `_get_category_profile()` (profile already floored at SEASONAL_FLOOR=1.0), computes a damped per-week uplift (DAMP_O=0.50), and sets each week to `max(fcst[w], snap(flat_ref * damped_mult, mp))`. Off-months stay at flat rate; peak months (e.g. Q4 holiday ramp) get lifted. VP-Q4-zeroed weeks are skipped. Fired on 228/880 Heuristic+Croston's Amazon records in first test run. |
| **F59n + F59i EC-override + F59m steady-rate** | `forecast_record()` | Three fixes for FF35147EC case (documented in previous session): F59n normalizes post-restock spike (LW order >= 5x L13W avg, AUR >= MAP); F59i EC-override anchors EC-transition items to POS_LW when WOS < 6 (bypasses the WOS >= 6 gate); F59m uses POS_LW as steady-rate when AUR >= MAP and demand is accelerating, spreads gap-fill over W1-W3 when gap > 4 weeks. |
