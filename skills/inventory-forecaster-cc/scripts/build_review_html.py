"""Generate self-contained interactive HTML training review page.

Reads:
  analysis/systemic_data_2026-05-29.json  -- all active non-Amazon records

Writes:
  analysis/training_review_proposals.json  -- structured proposals
  analysis/training_review_2026-05-29.html -- interactive HTML
"""
import json
import os
from math import gcd
from functools import reduce


def snap(v, mp):
    return round(v / mp) * mp if mp > 0 else round(v)


def infer_mp(arr):
    nz = [x for x in arr if x > 0]
    if not nz:
        return 1
    return max(1, reduce(gcd, nz))


def f93a_apply(ai_arr, cust_opn):
    new_ai = list(ai_arr)
    opn = cust_opn
    for i in range(min(3, len(new_ai))):
        if opn >= new_ai[i] and new_ai[i] > 0:
            opn -= new_ai[i]
            new_ai[i] = 0
    return new_ai


def main():
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(skill_dir)

    recs_all = json.load(open("analysis/systemic_data_2026-05-29.json"))
    by_key = {r["key"]: r for r in recs_all}
    proposals = []

    # === Proposal 1: Walmart -- F92 Retailer WOS POS baseline floor ===
    r = by_key["23011-FF10159"]
    mp = 6
    floor = snap(r["l13w"] * 0.85, mp)
    new_ai = [max(v, floor) if v > 0 else 0 for v in r["ai"]]
    if new_ai[16] == 0 and r["msty_opn"] > 4 * floor:
        new_ai[16] = floor

    f92_scope = [x for x in recs_all if x["model"] == "Retailer WOS (POS)" and x["l13w"] > 0]
    f92_before = sum(x["man_total"] - x["ai_total"] for x in f92_scope)
    f92_after = 0
    f92_man_total = sum(x["man_total"] for x in f92_scope)
    for x in f92_scope:
        mp_x = infer_mp(x["ai"])
        fl_x = snap(x["l13w"] * 0.85, mp_x)
        na = [max(v, fl_x) if v > 0 else 0 for v in x["ai"]]
        if len(na) > 16 and na[16] == 0 and x["msty_opn"] > 4 * fl_x:
            na[16] = fl_x
        f92_after += x["man_total"] - sum(na)

    proposals.append({
        "id": 1, "key": r["key"], "cust": r["cust"], "brand": r["brand"], "mstyle": r["mstyle"],
        "model": r["model"], "mp": mp,
        "comment": "baseline at 7470 u is too week when POS rate looks more around 8500 pcs. ALso, there is no reason you should have left W17 as zero since we will have plenty of stock that week.",
        "rule_fn_id": "f92",
        "params_schema": [
            {"name": "floor_mult", "label": "L13W floor multiplier", "type": "number", "default": 0.85, "min": 0.5, "max": 1.2, "step": 0.05},
            {"name": "restore_zeros", "label": "Restore zeroed weeks (W17-style)", "type": "checkbox", "default": True},
            {"name": "restore_thresh", "label": "Restore threshold (msty_opn / floor)", "type": "number", "default": 4.0, "min": 1.0, "max": 20.0, "step": 0.5}
        ],
        "default_params": {"floor_mult": 0.85, "restore_zeros": True, "restore_thresh": 4.0},
        "scope_key": "f92",
        "rule_id": "F92",
        "rule_title": "Retailer WOS (POS) baseline floor + zeroed-week restore",
        "rule_summary": "Raise baseline to L13W * 0.85 when below; restore zeroed weeks when Msty Open PO > 4 * baseline.",
        "rule_criterion": "model == 'Retailer WOS (POS)' AND computed_baseline < L13W_shipped_pace * 0.85",
        "rule_logic": "baseline = max(computed_baseline, snap(L13W * 0.85, mp)); for any week zeroed by upstream fill rule but msty_opn > 4 * baseline -> restore to baseline",
        "rule_loc": "_retailer_wos_forecast() after _compute_pos_baseline()",
        "ai_now": r["ai"], "ai_new": new_ai, "man": r["man"],
        "l13w": r["l13w"], "msty_opn": r["msty_opn"], "cust_opn": r["cust_opn"],
        "item_gap_before": r["man_total"] - sum(r["ai"]),
        "item_gap_after":  r["man_total"] - sum(new_ai),
        "item_ai_before": sum(r["ai"]), "item_ai_after": sum(new_ai),
        "item_man": r["man_total"],
        "sys_scope": len(f92_scope),
        "sys_man_total": f92_man_total,
        "sys_gap_before": f92_before,
        "sys_gap_after": f92_after,
        "sys_closed_abs": abs(f92_before) - abs(f92_after),
        "is_item_level": False,
    })

    # === Systemic F93A (used by proposals 2/4/5) ===
    f93_scope = [x for x in recs_all if x["model"] in ("Seasonal Baseline", "Sparse Intermittent") and x["cust_opn"] > 0]
    f93_before = sum(x["man_total"] - x["ai_total"] for x in f93_scope)
    f93_after = 0
    f93_man_total = sum(x["man_total"] for x in f93_scope)
    for x in f93_scope:
        na = f93a_apply(x["ai"], x["cust_opn"])
        f93_after += x["man_total"] - sum(na)

    f93_specs = [
        (2, "3102-FF8990"),
        (4, "1579-BB28473"),
        (5, "3466-FF8990"),
    ]
    for cid, key in f93_specs:
        r = by_key[key]
        new_ai = f93a_apply(r["ai"], r["cust_opn"])
        proposals.append({
            "id": cid, "key": r["key"], "cust": r["cust"], "brand": r["brand"], "mstyle": r["mstyle"],
            "model": r["model"], "mp": infer_mp(r["ai"]),
            "comment": "Always zero W1,W2,W3 if their open order exist in the same week in account " + r["cust"],
            "rule_fn_id": "f93",
            "params_schema": [
                {"name": "num_weeks", "label": "Weeks from start to evaluate", "type": "number", "default": 3, "min": 1, "max": 8, "step": 1},
                {"name": "coverage_mode", "label": "Coverage mode", "type": "select", "options": ["greedy", "full", "per_week"], "default": "greedy"},
                {"name": "per_week_threshold", "label": "Per-week threshold (per_week mode only)", "type": "number", "default": 0.8, "min": 0.1, "max": 1.5, "step": 0.1}
            ],
            "default_params": {"num_weeks": 3, "coverage_mode": "greedy", "per_week_threshold": 0.8},
            "scope_key": "f93",
            "rule_id": "F93",
            "rule_title": "Forward-PO greedy zero W1-W3",
            "rule_summary": "For Seasonal Baseline / Sparse Intermittent non-Amazon: greedy-consume cust_open_po across W1, W2, W3. Zero a week only if remaining PO qty >= AI for that week.",
            "rule_criterion": "model in ('Seasonal Baseline','Sparse Intermittent') AND cust_opn > 0 AND customer not Amazon",
            "rule_logic": "opn = cust_opn; for w in [0,1,2]: if opn >= ai[w] and ai[w] > 0: opn -= ai[w]; ai[w] = 0",
            "rule_loc": "After F37 in forecast_record() retailer branch",
            "ai_now": r["ai"], "ai_new": new_ai, "man": r["man"],
            "l13w": r["l13w"], "msty_opn": r["msty_opn"], "cust_opn": r["cust_opn"],
            "item_gap_before": r["man_total"] - sum(r["ai"]),
            "item_gap_after":  r["man_total"] - sum(new_ai),
            "item_ai_before": sum(r["ai"]), "item_ai_after": sum(new_ai),
            "item_man": r["man_total"],
            "sys_scope": len(f93_scope),
            "sys_man_total": f93_man_total,
            "sys_gap_before": f93_before,
            "sys_gap_after": f93_after,
            "sys_closed_abs": abs(f93_before) - abs(f93_after),
            "is_item_level": False,
        })

    # === Proposal 3: Burlington -- F94 ITEM-LEVEL ===
    r = by_key["13640-BB21626"]
    new_ai = list(r["ai"])
    new_ai[9] = 0
    proposals.append({
        "id": 3, "key": r["key"], "cust": r["cust"], "brand": r["brand"], "mstyle": r["mstyle"],
        "model": r["model"], "mp": infer_mp(r["ai"]),
        "comment": "zero out W10 prj as it have open POs add up to similar volume withing 4 weeks",
        "rule_fn_id": "f94",
        "params_schema": [
            {"name": "week_to_zero", "label": "Week to zero (1-indexed)", "type": "number", "default": 10, "min": 1, "max": 26, "step": 1}
        ],
        "default_params": {"week_to_zero": 10},
        "scope_key": "f94",
        "rule_id": "F94-ITEM",
        "rule_title": "Burlington BB21626 W10 zero (item-level via Tell-AI comment)",
        "rule_summary": "Apply via F58 AI Comment: zero W10 for 13640-BB21626. No broad systemic rule -- tested 3 variants across Sparse Intermittent + msty_opn, all WIDENED the gap (over-zeroed legitimate spikes).",
        "rule_criterion": "item key == '13640-BB21626'",
        "rule_logic": "Insert AI Comment row: zero W10 because forward POs cover similar volume in W11-W13 window",
        "rule_loc": "AI Comments table (bv2jirwts) via F58 pickup",
        "ai_now": r["ai"], "ai_new": new_ai, "man": r["man"],
        "l13w": r["l13w"], "msty_opn": r["msty_opn"], "cust_opn": r["cust_opn"],
        "item_gap_before": r["man_total"] - sum(r["ai"]),
        "item_gap_after":  r["man_total"] - sum(new_ai),
        "item_ai_before": sum(r["ai"]), "item_ai_after": sum(new_ai),
        "item_man": r["man_total"],
        "sys_scope": 1,
        "sys_man_total": r["man_total"],
        "sys_gap_before": r["man_total"] - sum(r["ai"]),
        "sys_gap_after":  r["man_total"] - sum(new_ai),
        "sys_closed_abs": abs(r["man_total"] - sum(r["ai"])) - abs(r["man_total"] - sum(new_ai)),
        "is_item_level": True,
    })

    proposals.sort(key=lambda p: p["id"])

    with open("analysis/training_review_proposals.json", "w") as f:
        json.dump(proposals, f, indent=2)

    # === Build scope data per rule (slim records only) ===
    def slim(x):
        return {
            "key": x["key"], "ai": x["ai"], "man": x["man"],
            "l13w": x["l13w"], "msty_opn": x["msty_opn"], "cust_opn": x["cust_opn"],
            "ai_total": x["ai_total"], "man_total": x["man_total"],
        }
    scope_data = {
        "f92": [slim(x) for x in recs_all if x["model"] == "Retailer WOS (POS)" and x["l13w"] > 0],
        "f93": [slim(x) for x in recs_all if x["model"] in ("Seasonal Baseline", "Sparse Intermittent") and x["cust_opn"] > 0],
        "f94": [slim(by_key["13640-BB21626"])],  # item-only scope
    }
    # Include item records for the per-card view
    item_recs = {p["key"]: slim(by_key[p["key"]]) for p in proposals}

    # === BUILD HTML ===
    html = build_html(proposals, scope_data, item_recs)
    out_path = "analysis/training_review_2026-05-29.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out_path}")
    for p in proposals:
        item_change = p["item_gap_after"] - p["item_gap_before"]
        sys_change = p["sys_gap_after"] - p["sys_gap_before"]
        print(f"  [{p['id']}] {p['key']} {p['rule_id']:9} item {p['item_gap_before']:+,} -> {p['item_gap_after']:+,}  sys {p['sys_gap_before']:+,} -> {p['sys_gap_after']:+,}  (|sys| closed {p['sys_closed_abs']:+,})")


def build_html(proposals, scope_data, item_recs):
    proposals_json = json.dumps(proposals).replace("</", "<\\/")
    scope_json = json.dumps(scope_data).replace("</", "<\\/")
    item_json = json.dumps(item_recs).replace("</", "<\\/")

    css = """
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #f4f6f8; color: #1f2937; }
header { background: linear-gradient(135deg, #1565c0 0%, #0d47a1 100%); color: white; padding: 18px 28px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
header h1 { margin: 0; font-size: 22px; font-weight: 600; }
header .sub { font-size: 13px; opacity: .85; margin-top: 3px; }
.container { max-width: 1280px; margin: 0 auto; padding: 22px 28px 60px; }
.global-panel { background: white; border-radius: 8px; padding: 16px 20px; margin-bottom: 22px; box-shadow: 0 1px 4px rgba(0,0,0,.06); position: sticky; top: 12px; z-index: 50; }
.global-panel h2 { margin: 0 0 10px; font-size: 14px; text-transform: uppercase; letter-spacing: .5px; color: #6b7280; }
.global-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
.gm { background: #f9fafb; border-radius: 6px; padding: 10px 14px; }
.gm .lbl { font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: .5px; }
.gm .val { font-size: 18px; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }
.gm.gap .val { color: #c62828; }
.gm.gap-good .val { color: #2e7d32; }
.gm.delta .val { color: #1565c0; }
.proposals { display: grid; gap: 18px; }
.card { background: white; border-radius: 10px; padding: 18px 22px; box-shadow: 0 1px 4px rgba(0,0,0,.06); border-left: 5px solid #cbd5e1; transition: border-color .2s, opacity .2s; }
.card.approved { border-left-color: #2e7d32; }
.card.rejected { border-left-color: #c62828; opacity: .6; }
.card.modified { border-left-color: #ed6c02; }
.card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; }
.card-head .title { font-size: 16px; font-weight: 600; }
.card-head .meta { font-size: 12px; color: #6b7280; margin-top: 3px; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 11px; font-size: 11px; font-weight: 600; letter-spacing: .4px; margin-left: 6px; }
.tag.rule { background: #e3f2fd; color: #1565c0; }
.tag.item-lvl { background: #fff3e0; color: #ed6c02; }
.tag.status-approved { background: #e8f5e9; color: #2e7d32; }
.tag.status-rejected { background: #ffebee; color: #c62828; }
.tag.status-modified { background: #fff3e0; color: #ed6c02; }
.tag.status-pending { background: #f5f5f5; color: #6b7280; }
.section { margin-top: 14px; }
.section-title { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: #6b7280; font-weight: 600; margin-bottom: 6px; }
.comment-box { background: #fffbeb; border-left: 3px solid #f59e0b; padding: 10px 14px; border-radius: 4px; font-size: 14px; line-height: 1.45; font-style: italic; color: #78350f; }
.rule-box { background: #f0f9ff; border-left: 3px solid #0ea5e9; padding: 10px 14px; border-radius: 4px; font-size: 13px; line-height: 1.5; }
.rule-box .ml { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #075985; background: #e0f2fe; padding: 1px 5px; border-radius: 3px; }
.rule-box .code { display: block; margin-top: 6px; padding: 8px 10px; background: #f8fafc; border-radius: 4px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #0c4a6e; white-space: pre-wrap; }
.impact-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.impact-card { background: #f9fafb; border-radius: 6px; padding: 12px 14px; }
.impact-card.item { border-top: 3px solid #1565c0; }
.impact-card.sys { border-top: 3px solid #6d28d9; }
.impact-card h4 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #6b7280; }
.impact-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 13px; font-variant-numeric: tabular-nums; }
.impact-row .k { color: #6b7280; }
.impact-row .v { font-weight: 600; }
.impact-row.delta .v.good { color: #2e7d32; }
.impact-row.delta .v.bad  { color: #c62828; }
.chart-wrap { margin-top: 14px; background: #fafafa; border-radius: 6px; padding: 8px 4px 0; }
.chart-wrap canvas { max-height: 220px; }
.actions { margin-top: 14px; display: flex; gap: 8px; align-items: center; }
.btn { padding: 8px 16px; border: 0; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; transition: filter .15s, transform .05s; }
.btn:hover { filter: brightness(1.08); }
.btn:active { transform: translateY(1px); }
.btn.approve { background: #2e7d32; color: white; }
.btn.reject  { background: #c62828; color: white; }
.btn.modify  { background: #ed6c02; color: white; }
.btn.save    { background: #1565c0; color: white; padding: 10px 22px; font-size: 14px; }
.btn:disabled { opacity: .4; cursor: not-allowed; }
.modify-box { display: none; margin-top: 10px; padding: 10px; background: #fff7ed; border-radius: 6px; }
.modify-box.open { display: block; }
.modify-box textarea { width: 100%; min-height: 70px; font-family: -apple-system, Segoe UI, sans-serif; font-size: 13px; border: 1px solid #e5e7eb; border-radius: 4px; padding: 8px; }
.commit-bar { margin-top: 24px; padding: 18px; background: white; border-radius: 8px; text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
.help-text { font-size: 12px; color: #6b7280; margin-top: 6px; }
"""

    head = (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>AI Training Review 2026-05-29</title>'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'
        f'<style>{css}</style>'
        '</head><body>'
    )

    body = (
        '<header>'
        '<h1>AI Training Review &mdash; 2026-05-29</h1>'
        '<div class="sub">5 planner comments &middot; 3 rule changes &middot; goal: close MAN-AI gap</div>'
        '</header>'
        '<div class="container">'
        '  <div class="global-panel">'
        '    <h2>Global Impact (live)</h2>'
        '    <div class="global-grid">'
        '      <div class="gm"><div class="lbl">Records in scope</div><div class="val" id="g-scope">--</div></div>'
        '      <div class="gm"><div class="lbl">MAN 26w total</div><div class="val" id="g-man">--</div></div>'
        '      <div class="gm"><div class="lbl">AI 26w before</div><div class="val" id="g-ai-before">--</div></div>'
        '      <div class="gm delta"><div class="lbl">AI 26w after</div><div class="val" id="g-ai-after">--</div></div>'
        '      <div class="gm gap"><div class="lbl">|Gap| MAN-AI before / after</div><div class="val" id="g-gap">--</div><div class="lbl" id="g-gap-pct" style="margin-top:4px"></div></div>'
        '    </div>'
        '    <div class="help-text" id="g-help">Approve / Reject / Modify each proposal below. Global panel updates live.</div>'
        '  </div>'
        '  <div class="proposals" id="proposals"></div>'
        '  <div class="commit-bar">'
        '    <button class="btn save" id="save-btn" disabled>Save Decisions (download JSON)</button>'
        '    <div class="help-text">All proposals must be decided (Approve/Reject/Modify) before saving.</div>'
        '  </div>'
        '</div>'
    )

    js = """
const PROPOSALS = __PROPOSALS_JSON__;
const SCOPE_DATA = __SCOPE_JSON__;
const ITEM_RECS = __ITEM_JSON__;
const STATE = { decisions: {}, modifications: {}, params: {}, charts: {} };
PROPOSALS.forEach(p => {
  STATE.decisions[p.id] = null;
  STATE.params[p.id] = JSON.parse(JSON.stringify(p.default_params));
});

// ---- helpers ----
function snap(v, mp) { return mp > 0 ? Math.round(v / mp) * mp : Math.round(v); }
function inferMp(arr) {
  const nz = arr.filter(x => x > 0);
  if (!nz.length) return 1;
  function gcd(a, b) { return b === 0 ? a : gcd(b, a % b); }
  return nz.reduce(gcd);
}
function sum(arr) { return arr.reduce((a,b) => a+b, 0); }

// ---- rule functions (port of Python logic) ----
const RULE_FNS = {
  f92(rec, params) {
    const mp = inferMp(rec.ai);
    const floor = snap(rec.l13w * params.floor_mult, mp);
    const new_ai = rec.ai.map(v => v > 0 ? Math.max(v, floor) : 0);
    if (params.restore_zeros && new_ai.length > 16 && new_ai[16] === 0 && rec.msty_opn > params.restore_thresh * floor) {
      new_ai[16] = floor;
    }
    return new_ai;
  },
  f93(rec, params) {
    const new_ai = [...rec.ai];
    const n = Math.min(params.num_weeks, new_ai.length);
    if (params.coverage_mode === 'greedy') {
      let opn = rec.cust_opn;
      for (let i = 0; i < n; i++) {
        if (opn >= new_ai[i] && new_ai[i] > 0) { opn -= new_ai[i]; new_ai[i] = 0; }
      }
    } else if (params.coverage_mode === 'full') {
      const sum_n = new_ai.slice(0, n).reduce((a,b) => a+b, 0);
      if (rec.cust_opn >= sum_n && sum_n > 0) {
        for (let i = 0; i < n; i++) new_ai[i] = 0;
      }
    } else if (params.coverage_mode === 'per_week') {
      for (let i = 0; i < n; i++) {
        if (rec.cust_opn >= params.per_week_threshold * new_ai[i] && new_ai[i] > 0) new_ai[i] = 0;
      }
    }
    return new_ai;
  },
  f94(rec, params) {
    const new_ai = [...rec.ai];
    const w = params.week_to_zero - 1;
    if (w >= 0 && w < new_ai.length) new_ai[w] = 0;
    return new_ai;
  },
};

// ---- recompute: re-run rule with current params, refresh card ----
function recompute(id) {
  const p = PROPOSALS.find(x => x.id === id);
  const params = STATE.params[id];
  const fn = RULE_FNS[p.rule_fn_id];
  if (!fn) return;

  // Per-item recompute
  const itemRec = ITEM_RECS[p.key];
  const new_ai = fn(itemRec, params);
  p.ai_new = new_ai;
  p.item_ai_after = sum(new_ai);
  p.item_gap_after = p.item_man - p.item_ai_after;

  // Systemic recompute
  if (p.is_item_level) {
    p.sys_gap_after = p.item_gap_after;
  } else {
    const scope = SCOPE_DATA[p.scope_key] || [];
    let sys_after = 0;
    for (const rec of scope) {
      const na = fn(rec, params);
      sys_after += rec.man_total - sum(na);
    }
    p.sys_gap_after = sys_after;
  }
  p.sys_closed_abs = Math.abs(p.sys_gap_before) - Math.abs(p.sys_gap_after);

  refreshCard(id);
  updateGlobal();
}

function refreshCard(id) {
  const p = PROPOSALS.find(x => x.id === id);
  // Replace inner impact + chart sections
  document.getElementById(`impact-${id}`).innerHTML = renderImpactGrid(p);
  // Replace chart data
  const ch = STATE.charts[id];
  if (ch) {
    ch.data.datasets[2].data = p.ai_new;
    ch.update();
  }
}

function fmt(n) {
  const s = Math.round(n).toString();
  return (n >= 0 ? '+' : '-') + Math.abs(Math.round(n)).toLocaleString();
}
function fmtSimple(n) { return Math.round(n).toLocaleString(); }
function fmtPct(gap, man) {
  if (!man || man === 0) return 'n/a';
  const pct = (gap / man) * 100;
  return (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
}
function fmtPctAbs(gap, man) {
  if (!man || man === 0) return 'n/a';
  return (Math.abs(gap) / man * 100).toFixed(1) + '%';
}

function renderImpactGrid(p) {
  const item_closed = Math.abs(p.item_gap_before) - Math.abs(p.item_gap_after);
  const sys_closed = Math.abs(p.sys_gap_before) - Math.abs(p.sys_gap_after);
  return `
    <div class="impact-grid">
      <div class="impact-card item">
        <h4>This item only (${p.key})</h4>
        <div class="impact-row"><span class="k">MAN 26w</span><span class="v">${fmtSimple(p.item_man)}</span></div>
        <div class="impact-row"><span class="k">AI 26w before</span><span class="v">${fmtSimple(p.item_ai_before)}</span></div>
        <div class="impact-row"><span class="k">AI 26w after</span><span class="v">${fmtSimple(p.item_ai_after)}</span></div>
        <div class="impact-row"><span class="k">Gap MAN-AI before</span><span class="v">${fmt(p.item_gap_before)}u (${fmtPct(p.item_gap_before, p.item_man)})</span></div>
        <div class="impact-row"><span class="k">Gap MAN-AI after</span><span class="v">${fmt(p.item_gap_after)}u (${fmtPct(p.item_gap_after, p.item_man)})</span></div>
        <div class="impact-row delta"><span class="k">|Gap| % before / after</span><span class="v">${fmtPctAbs(p.item_gap_before, p.item_man)}  ->  ${fmtPctAbs(p.item_gap_after, p.item_man)}</span></div>
        <div class="impact-row delta"><span class="k">|Gap| closed</span><span class="v ${item_closed >= 0 ? 'good':'bad'}">${fmt(item_closed)}u</span></div>
      </div>
      <div class="impact-card sys">
        <h4>${p.is_item_level ? 'Item-level only (no systemic rule)' : `Systemic across ${fmtSimple(p.sys_scope)} matching records`}</h4>
        <div class="impact-row"><span class="k">Records in scope</span><span class="v">${fmtSimple(p.sys_scope)}</span></div>
        <div class="impact-row"><span class="k">MAN 26w total</span><span class="v">${fmtSimple(p.sys_man_total)}</span></div>
        <div class="impact-row"><span class="k">Gap before</span><span class="v">${fmt(p.sys_gap_before)}u (${fmtPct(p.sys_gap_before, p.sys_man_total)})</span></div>
        <div class="impact-row"><span class="k">Gap after</span><span class="v">${fmt(p.sys_gap_after)}u (${fmtPct(p.sys_gap_after, p.sys_man_total)})</span></div>
        <div class="impact-row delta"><span class="k">|Gap| % before / after</span><span class="v">${fmtPctAbs(p.sys_gap_before, p.sys_man_total)}  ->  ${fmtPctAbs(p.sys_gap_after, p.sys_man_total)}</span></div>
        <div class="impact-row delta"><span class="k">|Gap| closed</span><span class="v ${sys_closed >= 0 ? 'good':'bad'}">${fmt(sys_closed)}u</span></div>
        ${p.is_item_level ? '<div class="impact-row"><span class="k" style="font-size:11px;font-style:italic;color:#a16207">Per script: tested 3 systemic variants, all widened gap. Item-level Tell-AI is the right path.</span></div>' : ''}
      </div>
    </div>`;
}

function renderParamInputs(p) {
  const params = STATE.params[p.id];
  return p.params_schema.map(s => {
    const v = params[s.name];
    if (s.type === 'checkbox') {
      return `<label class="pin"><input type="checkbox" data-pid="${p.id}" data-name="${s.name}" ${v ? 'checked' : ''} onchange="onParamChange(this)"><span>${s.label}</span></label>`;
    }
    if (s.type === 'select') {
      const opts = s.options.map(o => `<option value="${o}" ${o === v ? 'selected' : ''}>${o}</option>`).join('');
      return `<label class="pin"><span>${s.label}</span><select data-pid="${p.id}" data-name="${s.name}" onchange="onParamChange(this)">${opts}</select></label>`;
    }
    // number
    return `<label class="pin"><span>${s.label}</span><input type="number" step="${s.step}" min="${s.min}" max="${s.max}" value="${v}" data-pid="${p.id}" data-name="${s.name}" data-type="number" oninput="onParamChange(this)"></label>`;
  }).join('');
}

function onParamChange(el) {
  const pid = +el.dataset.pid;
  const name = el.dataset.name;
  let val;
  if (el.type === 'checkbox') val = el.checked;
  else if (el.dataset.type === 'number') val = parseFloat(el.value);
  else val = el.value;
  STATE.params[pid][name] = val;
  recompute(pid);
}

function resetParams(id) {
  const p = PROPOSALS.find(x => x.id === id);
  STATE.params[id] = JSON.parse(JSON.stringify(p.default_params));
  document.getElementById(`modparams-${id}`).innerHTML = renderParamInputs(p);
  recompute(id);
}

function renderProposal(p) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = `card-${p.id}`;

  card.innerHTML = `
    <div class="card-head">
      <div>
        <div class="title">[${p.id}/5] ${p.cust} &mdash; ${p.mstyle}
          <span class="tag rule">${p.rule_id}</span>
          ${p.is_item_level ? '<span class="tag item-lvl">ITEM-LEVEL</span>' : ''}
        </div>
        <div class="meta">${p.brand} &middot; Model: <b>${p.model || '(none)'}</b> &middot; MP: ${p.mp} &middot; L13W: ${fmtSimple(p.l13w)}/wk &middot; Cust Open PO: ${fmtSimple(p.cust_opn)} &middot; Msty Open PO: ${fmtSimple(p.msty_opn)}</div>
      </div>
      <div><span class="tag status-pending" id="status-${p.id}">PENDING</span></div>
    </div>
    <div class="section">
      <div class="section-title">Planner Comment</div>
      <div class="comment-box">&ldquo;${p.comment}&rdquo;</div>
    </div>
    <div class="section">
      <div class="section-title">Recommendation (${p.rule_id})</div>
      <div class="rule-box">
        <b>${p.rule_title}</b><br>
        ${p.rule_summary}
        <span class="code">Criterion: ${p.rule_criterion}
Logic:     ${p.rule_logic}
Location:  ${p.rule_loc}</span>
      </div>
    </div>
    <div class="section">
      <div class="section-title">Impact (live)</div>
      <div id="impact-${p.id}">${renderImpactGrid(p)}</div>
    </div>
    <div class="section">
      <div class="section-title">26-Week Projections (${p.key})</div>
      <div class="chart-wrap"><canvas id="chart-${p.id}"></canvas></div>
    </div>
    <div class="actions">
      <button class="btn approve" onclick="decide(${p.id}, 'approve')">Approve</button>
      <button class="btn reject"  onclick="decide(${p.id}, 'reject')">Reject</button>
      <button class="btn modify"  onclick="openModify(${p.id})">Modify</button>
    </div>
    <div class="modify-box" id="modbox-${p.id}">
      <div class="section-title">Tune parameters (impact + chart update live)</div>
      <div class="param-grid" id="modparams-${p.id}">${renderParamInputs(p)}</div>
      <div class="section-title" style="margin-top:14px">Extra notes (optional, for things not in the parameters)</div>
      <textarea id="modtext-${p.id}" placeholder="e.g. only apply when item_status starts with 'Active: Replen', or skip Burlington-style off-price items..."></textarea>
      <div style="margin-top:8px;display:flex;gap:8px;">
        <button class="btn modify" onclick="saveModify(${p.id})">Save Modification</button>
        <button class="btn" style="background:#6b7280;color:#fff" onclick="resetParams(${p.id})">Reset to defaults</button>
        <button class="btn reject" onclick="cancelModify(${p.id})">Cancel</button>
      </div>
    </div>
  `;
  return card;
}

function renderChart(p) {
  const ctx = document.getElementById(`chart-${p.id}`);
  const weeks = Array.from({length: 26}, (_, i) => 'W' + (i + 1));
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: weeks,
      datasets: [
        { label: 'MAN (planner)', data: p.man, backgroundColor: 'rgba(21,101,192,.85)' },
        { label: 'AI (now)',      data: p.ai_now, backgroundColor: 'rgba(150,150,150,.65)' },
        { label: 'AI (new)',      data: p.ai_new, backgroundColor: 'rgba(46,125,50,.85)' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'top', labels: { font: { size: 11 } } } },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 10 } } },
        y: { ticks: { font: { size: 10 }, callback: v => v.toLocaleString() } }
      }
    }
  });
}

function updateGlobal() {
  let man_total = 0, ai_before_total = 0, ai_after_total = 0;
  let total_scope = 0, decided = 0;
  PROPOSALS.forEach(p => {
    man_total += p.item_man;
    ai_before_total += p.item_ai_before;
    const d = STATE.decisions[p.id];
    if (d === 'approve' || d === 'modify') {
      ai_after_total += p.item_ai_after;
    } else if (d === 'reject') {
      ai_after_total += p.item_ai_before;
    } else {
      ai_after_total += p.item_ai_before;
    }
    if (d) decided++;
  });
  // Systemic side: sum only unique rules' systemic impacts for approved/modified
  const seen_rules = new Set();
  let sys_scope = 0, sys_before = 0, sys_after = 0, sys_man = 0;
  PROPOSALS.forEach(p => {
    if (seen_rules.has(p.rule_id)) return;
    seen_rules.add(p.rule_id);
    const d = STATE.decisions[p.id];
    sys_scope += p.sys_scope;
    sys_before += p.sys_gap_before;
    sys_man += p.sys_man_total;
    if (d === 'approve' || d === 'modify') {
      sys_after += p.sys_gap_after;
    } else {
      sys_after += p.sys_gap_before;
    }
  });
  document.getElementById('g-scope').innerText = sys_scope.toLocaleString();
  document.getElementById('g-man').innerText = sys_man.toLocaleString();
  document.getElementById('g-ai-before').innerText = (sys_man - sys_before).toLocaleString();
  document.getElementById('g-ai-after').innerText = (sys_man - sys_after).toLocaleString();
  const gap_b = Math.abs(sys_before);
  const gap_a = Math.abs(sys_after);
  const pct_b = sys_man ? (gap_b / sys_man * 100).toFixed(1) : '0';
  const pct_a = sys_man ? (gap_a / sys_man * 100).toFixed(1) : '0';
  document.getElementById('g-gap').innerText = gap_b.toLocaleString() + 'u  ->  ' + gap_a.toLocaleString() + 'u';
  document.getElementById('g-gap-pct').innerText = pct_b + '%  ->  ' + pct_a + '%';
  document.getElementById('g-help').innerText = decided + ' of ' + PROPOSALS.length + ' proposals decided. ' + (decided === PROPOSALS.length ? 'You can Save Decisions now.' : '');
  document.getElementById('save-btn').disabled = (decided < PROPOSALS.length);
}

function decide(id, d) {
  STATE.decisions[id] = d;
  if (d !== 'modify') STATE.modifications[id] = null;
  const card = document.getElementById('card-' + id);
  card.classList.remove('approved','rejected','modified');
  card.classList.add(d + 'd');  // 'approved' | 'rejected' | 'modified'
  const status = document.getElementById('status-' + id);
  status.className = 'tag status-' + d + 'd';
  status.innerText = d.toUpperCase() + 'D';
  updateGlobal();
}

function openModify(id) {
  document.getElementById('modbox-' + id).classList.add('open');
}
function cancelModify(id) {
  document.getElementById('modbox-' + id).classList.remove('open');
  document.getElementById('modtext-' + id).value = '';
}
function saveModify(id) {
  const text = document.getElementById('modtext-' + id).value.trim();
  if (!text) { alert('Enter a modification description first.'); return; }
  STATE.modifications[id] = text;
  decide(id, 'modify');
  document.getElementById('modbox-' + id).classList.remove('open');
}

function saveAll() {
  const out = {
    generated_at: new Date().toISOString(),
    decisions: STATE.decisions,
    modifications: STATE.modifications,
    proposals: PROPOSALS.map(p => ({id:p.id, key:p.key, rule_id:p.rule_id, rule_title:p.rule_title}))
  };
  const blob = new Blob([JSON.stringify(out, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'training_review_decisions_2026-05-29.json';
  a.click();
  URL.revokeObjectURL(url);
}

document.addEventListener('DOMContentLoaded', () => {
  const container = document.getElementById('proposals');
  PROPOSALS.forEach(p => container.appendChild(renderProposal(p)));
  PROPOSALS.forEach(p => renderChart(p));
  document.getElementById('save-btn').addEventListener('click', saveAll);
  updateGlobal();
});
"""

    js = js.replace("__PROPOSALS_JSON__", proposals_json)
    return head + body + '<script>' + js + '</script></body></html>'


if __name__ == "__main__":
    main()
