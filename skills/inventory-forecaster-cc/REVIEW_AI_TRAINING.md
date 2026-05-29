# Review AI Training -- Interactive Approval Workflow

**Triggered by:** user types `Review AI Training` (case-insensitive).

**Goal:** Walk Steven through each proposed model change from today's AI Training
Review one at a time. Show concrete impact (gap + 26-week chart) for each.
Accept Approve / Reject / Modify per change. At the end, summarize global
impact across all approved changes, get a final Commit, then edit
`scripts/inventory_forecaster.py` to apply them.

**Hard rule (from Steven):** Every recommendation MUST close the MAN vs AI
divergence. If a recommendation would WIDEN the gap (REJECTED status in the
training MD), do NOT propose it -- find or design a better solution that
narrows the gap. Skip REJECTED entries silently or replace them with a
directional-guard version per the training script's REJECTED block.

---

## Step 0 -- Load context

1. Find the latest training report:
   `Glob C:/Users/steven/.claude/skills/inventory-forecaster-cc/analysis/ai_training_*.md`
   Pick the most recent by date in filename.

2. Parse the report:
   - **Section 1 (Executive Summary):** total comments, net unit gap
   - **Section 3 (Proposed Model Changes):** each `### [N] [STATUS] <Title>` block
   - **Section 4 (Comment Detail):** raw comment text per item
   - **Section 5 (Systemic Impact Estimate):** before/after MAN-AI table

3. Build an in-memory list of `proposals`:
   ```
   [
     {
       "id": 1,
       "status": "VALIDATED" | "ISOLATED" | "REJECTED",
       "title": "...",
       "intent": "wrong_model" | "eol" | "zero_out" | "boost" | "cut",
       "recommendation": "...",   # full text from MD
       "rationale": "...",
       "items": [
         {"key": "1864-FF8654", "cust": "...", "brand": "...",
          "ai_26w": 62292, "man_26w": 81750, "gap": +19458,
          "comment": "this is a horrible projection..."}
       ],
       "systemic": {"before": +720568, "before_pct": +42.5,
                    "after": +443485,  "after_pct": +22.5,
                    "ai_change": -277083}
     },
     ...
   ]
   ```

4. **Handling each systemic_status:**
   - **VALIDATED**: present as-is (script already proved it narrows the gap)
   - **ISOLATED**: present as-is with the item-level fix
   - **NEUTRAL**: do NOT show the script's generic "investigate" stub.
     SYNTHESIZE a concrete model-change recommendation from the comment
     text + per-item data (AI weekly, MAN weekly, L13W history, open POs,
     master pack, model). The synthesized rec must include: exact code
     location, criterion, per-week math, and a computed impact that
     narrows MAN-AI for the affected items. Per Steven's hard rule, do
     not present a NEUTRAL as-is -- always upgrade it to actionable.
   - **REJECTED**: skip unless the training MD provided a replacement
     directional-guard version inside the same block.

5. **One COMMENT at a time, not one proposal-group.** Steven's design is
   per-comment. If 4 comments share the same recommendation pattern,
   walk through 4 individual steps -- but on later items, you may write
   "(same recommendation as comment #X, applied to this item)" and just
   show the per-item chart + gap. The user still gets Approve / Reject /
   Modify for each item individually.

5. Load supporting data:
   - `forecast_results.json` -- current AI 26-week values per key
     (fields: `forecast` array, `manual` array, `norm_l4w`, `norm_l13w`,
     `norm_l26w`, `mp`)
   - If the affected key is missing from forecast_results.json, fetch from
     QB REST (table bpd237tvm, project AI_PRJ_W1-W26 + MAN PRJ W1-W26 +
     L4W/L13W/L26W + Master_Pack).

6. Announce to the user:
   ```
   Loaded AI Training Review for <date>.
   <N> proposals to review (<X> VALIDATED + <Y> ISOLATED, <Z> REJECTED skipped).
   Starting with #1...
   ```

---

## Step 1 -- Present each proposal (loop)

For each proposal in order, render this block:

```
============================================================
  PROPOSAL [<id>/<total>]  --  <STATUS> -- <title>
============================================================
COMMENT (from planner on <key>):
"<full comment text>"

RECOMMENDATION:
<recommendation paragraph from training MD>

WHY THIS CLOSES THE GAP:
<rationale paragraph>

IMPACT (this change only):
  Items affected: <N>
  MAN-AI before:  <+X,XXX>u   (<+XX.X%>)
  MAN-AI after:   <+X,XXX>u   (<+XX.X%>)
  Gap closed:     <-X,XXX>u   (<-XX.X pp>)

26-WEEK CHART -- <key>   (mp=<MP>)
Week:        W1   W2   W3   W4   W5   W6  ... W26   |  Avg/wk  Total
MAN:        <v>  <v>  <v>  ... values from forecast_results.manual
AI (now):   <v>  <v>  <v>  ... values from forecast_results.forecast
AI (new):   <v>  <v>  <v>  ... values from simulating the recommendation

(If multiple items affected: show the chart for the FIRST item only,
plus a one-line summary per other item:
  - <key2>  MAN <total>  AI now <total>  AI new <total>  gap <before>->after
)
```

After showing the block, ask:
```
Approve / Reject / Modify?
```
Then stop and wait for the user's reply.

### Computing AI (new) for the chart

Translate the recommendation into a per-week value array by intent:

| Intent | Logic for AI (new) per week |
|---|---|
| `wrong_model` w/ trend criterion (FF8654-style) | `rate = max(norm_l4w, norm_l13w)`; per-week = `snap(rate, mp)` flat across W1-W26. If recommendation specifies seasonal mults, apply them. |
| `eol` / zero-out | All 26 weeks = 0 |
| `boost` (planner raised) | `rate = norm_l4w * boost_factor` per rec; snap to mp |
| `cut` (planner lowered) | `rate = norm_l4w * cut_factor`; snap to mp |
| `decline_guard` | `rate = norm_l4w` (use latest, not L13W) |
| Custom (override) | Implement per the recommendation's explicit logic |

When in doubt, fall back to the affected item's current `forecast` array
adjusted by the systemic before->after ratio: `new_w = current_w * (1 + ai_change / current_total)`.

Always re-snap to master pack.

Render the 3-line chart compactly. Each cell is 4 chars wide, padded.
If values span large ranges, format with `,` thousand separator and let
columns expand. Skip the chart if all values across all 3 lines are 0.

---

## Step 2 -- Handle user reply

- **"Approve"** (or "approve", "a", "yes to this one") -> mark proposal as
  `decision = "approve"`, move to next proposal.
- **"Reject"** (or "reject", "r", "skip", "no") -> mark `decision = "reject"`,
  move to next proposal.
- **"Modify"** (or "modify", "m", "change", "edit") -> ask:
  > "What do you want to modify? You can change the recommendation logic,
  > the affected scope (which items it applies to), the threshold/criterion,
  > or replace the recommendation entirely."
  >
  > Wait for the user's modification. Apply it to the proposal in memory.
  > Re-show the IMPACT and CHART with the modified logic.
  > Then ask Approve / Reject / Modify again. Loop until the user finalizes.
  > Mark `decision = "modify"` with the modified recommendation stored.

After every reply (approve / reject / final modify), automatically advance
to the next proposal. Do NOT ask "ready for next?" -- just show the next
block.

---

## Step 3 -- Global Impact Summary (after the last proposal)

When all proposals have a decision, present:

```
============================================================
  GLOBAL IMPACT  --  <K> approved + <M> modified, <R> rejected
============================================================

Aggregate across all approved/modified changes:

                    Before all changes     After all changes
Total items:        <N>                    <N>
MAN PRJ 26w total:  <M units>              <M units>           (unchanged)
AI  PRJ 26w total:  <A_before units>       <A_after units>
MAN - AI gap:       <+G_before>u (<XX%>)   <+G_after>u (<YY%>)
Gap closed:                                <-G_diff>u (<ZZ pp>)

Per-proposal breakdown:
  [1] APPROVE  -- <title>  gap <before>u -> <after>u  (<change>)
  [2] MODIFY   -- <title>  gap <before>u -> <after>u  (<change>)
  [3] REJECT   -- <title>  (no impact)
  ...

Items that received changes (preview):
  1864-FF8654:  AI 62,292u -> 75,816u   (MAN 81,750u, gap 81% -> 92%)
  ...

Ready to commit? Type Commit / OK / yes to apply.
Type Cancel to abort without changes.
```

Wait for user reply.

---

## Step 4 -- Commit (apply changes)

When the user types "Commit", "OK", "yes", or "commit":

1. For each approved or modified proposal, generate the inventory_forecaster.py
   edit:
   - Locate the relevant rule block (F85, F87, F88, F91, etc.)
   - Insert a new FXX rule with a unique number (next available; check
     `Grep "# F[0-9]+ " scripts/inventory_forecaster.py` for the highest
     in use, then add `# F<next> --` block)
   - Include in the new block:
     - Date stamp (`2026-MM-DD`)
     - The full rationale (paste from training MD)
     - The exact criterion / threshold
     - The per-week computation
     - Snap-to-mp at the end
     - `_fire("F<next>")` and a clear meta driver string
   - For "Modify" proposals: use the modified logic the user finalized.

2. Use the Edit tool (not Write) to apply each insertion. Read the relevant
   section first, then Edit precisely.

3. After all edits land, summarize:
   ```
   Applied <K+M> code changes to scripts/inventory_forecaster.py:
     - F<n1>: <title>  (lines <X-Y>)
     - F<n2>: <title>  (lines <X-Y>)
     ...

   Next steps:
     - Run a dry-run on the affected items to verify:
       python scripts/inventory_forecaster.py --mstyle <key> --dry-run
     - When happy, drop --dry-run to write to QB.
     - The reviewed comments will be marked Reviewed by the daily
       ai_training_review.py run; if you want to mark them now, run:
       python scripts/ai_training_review.py --mark-reviewed-only
   ```

4. Append to `analysis/ai_training_processed.json` the IDs of the comments
   that drove approved/modified proposals so they are not re-presented
   tomorrow.

5. Stop. Do NOT auto-run the forecast or auto-write to QB. Wait for the
   user to drive the next step.

---

## Step 5 -- Cancel (abort)

If the user types "Cancel" or "abort" at the final commit prompt:
- Discard all decisions
- Print: "Aborted. No changes made to inventory_forecaster.py."
- Do NOT modify ai_training_processed.json.

---

## Important behaviors

- **Be concise.** Each proposal block fits on one screen. No
  meta-commentary between proposals.
- **No emojis** in any output (per global rule).
- **No em-dashes, ellipsis, or non-ASCII Unicode** (per global rule).
- **Don't pre-empt the user.** After every Approve / Reject / Modify, move
  straight to the next proposal -- don't say "moving on" or "next up".
- **Never present a REJECTED proposal as-is.** If the training script
  flagged it REJECTED (gap widens), either skip it or transform it into
  the directional-guard version described in the rationale.
- **Be honest about uncertainty.** If you can't compute a clean AI (new)
  for the chart from the available data, say so and show only MAN and
  AI (now), plus the systemic before/after totals.
- **Master pack rule:** every per-week AI (new) value snaps to the item's
  Master_Pack. Never show a non-MP-multiple in the chart.
- **Owned brands:** never recommend SKU reduction or rationalization for
  A&H, Burt's Bees, BioSilk, CHI, Vibrant Life, Glad for Pets, Kingsford,
  GladWare (per global rules). If a proposal would do that, REJECT it
  with a brief note and move on.
