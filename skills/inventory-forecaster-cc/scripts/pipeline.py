"""
pipeline.py  --  Explicit-phase pipeline for forecast_record() (Phase 4 / A3).

The legacy forecast_record() in inventory_forecaster.py is 3,150 lines of
implicit ordering invariants. This module provides a parallel codepath that
runs the same logic through explicit phases with typed phase contracts:

    PhaseA  HistoryNormalization     Mutates history only
    PhaseB  Classify                 Reads-only -> emits pattern
    PhaseC  Model                    Reads history+pattern -> emits initial fcst
    PhaseD  BaselineGates            Reads fcst, may scale/lift
    PhaseE  DemandSignalGates        F58 comment override, F38 buyability, F59 series
    PhaseF  HardConstraints          VP-Q4 PO-zero, F70 switchover, F_PO_CUTOFF
    PhaseG  GuardsAndFinalize        G2 demotion, alert, MP snap

Each phase has a defined contract: what it reads, what it writes, what
invariants must hold afterward. This makes:
  - Unit testing per-phase trivial
  - Rule ordering explicit (no more implicit "F59h must fire before F59i")
  - Adding a new rule = adding it to the right phase, not finding the right line

STATUS: SCAFFOLDED but NOT WIRED INTO PRODUCTION.
  - The legacy forecast_record() remains the default.
  - Enable the new pipeline with `--pipeline` flag in the forecaster CLI.
  - Initial Phase 4 commit: only PhaseA, PhaseB, PhaseG are fully lifted.
    Phases C/D/E/F currently delegate back to legacy code inside
    forecast_record(). The migration is gradual; each phase moves over
    as it's tested and de-risked.

This file is intentionally read-only relative to inventory_forecaster -- it
imports from it but doesn't modify it. The pipeline can be exercised via:

    from pipeline import forecast_record_v2
    result = forecast_record_v2(row, master_pack, ...)
"""

from dataclasses import dataclass, field
from typing import Optional, Any


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline context: the typed shared state passed phase-to-phase
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ForecastContext:
    """Shared mutable state across pipeline phases. Each phase has a defined
    contract for what it may read/write.
    """
    # Inputs (set in Phase 0, then read-only)
    row: dict
    master_pack: float
    is_amazon: bool = False
    is_international: bool = False
    is_offprice: bool = False
    is_ecom: bool = False
    pos_data: Optional[dict] = None

    # Phase A output (history after normalization passes)
    history: list[float] = field(default_factory=list)
    history_for_model: list[float] = field(default_factory=list)
    history_corrections: dict = field(default_factory=dict)   # {F35: [...], F39: [...]}

    # Phase B output
    pattern: str = ""             # "inactive" | "sparse_intermittent" | "active"
    iso: dict = field(default_factory=dict)
    model: str = ""               # "Seasonal Baseline" | "Croston's" | etc.

    # Phase C output
    fcst: list[float] = field(default_factory=list)
    baseline: float = 0.0
    baseline_mode: str = ""

    # Accumulating across all phases
    meta: dict = field(default_factory=lambda: {"drivers": [], "structured_drivers": []})
    rule_fires: set[str] = field(default_factory=set)
    locked_weeks: set[int] = field(default_factory=set)   # weeks that downstream may not touch


# ─────────────────────────────────────────────────────────────────────────────
# Phase implementations (skeletons; lift logic from inventory_forecaster gradually)
# ─────────────────────────────────────────────────────────────────────────────

def phase_a_history(ctx: ForecastContext, fc) -> ForecastContext:
    """PhaseA -- History normalization.

    Reads: ctx.row
    Writes: ctx.history, ctx.history_for_model, ctx.history_corrections

    Currently delegates to fc._prep_record_signals() (which already does this
    correctly in the legacy path). A future migration would lift the individual
    F35/F39/F41/F43/F47 normalization passes into separate methods here.
    """
    sig = fc._prep_record_signals(ctx.row, ctx.master_pack,
                                  is_amazon=ctx.is_amazon,
                                  pos_data=ctx.pos_data)
    ctx.history = sig["history"]
    ctx.history_for_model = sig.get("history_for_model", sig["history"])
    for key in ("f35_corrections", "f39_corrections", "f41_corrections",
                "f43_corrections", "f47_corrections", "f_ats_corrections"):
        if sig.get(key):
            ctx.history_corrections[key] = sig[key]
    return ctx


def phase_b_classify(ctx: ForecastContext, fc) -> ForecastContext:
    """PhaseB -- Classification.

    Reads: ctx.history
    Writes: ctx.pattern, ctx.iso, ctx.model (preliminary)
    """
    ctx.pattern = fc.classify(ctx.history)
    ctx.iso = fc.detect_iso(ctx.history) if hasattr(fc, "detect_iso") else {"is_iso": False}
    # Initial model selection (may be overridden in later phases by routing rules)
    if ctx.pattern == "inactive":
        ctx.model = "Inactive"
    elif ctx.pattern == "sparse_intermittent":
        ctx.model = "Sparse Intermittent"
    else:  # active
        # Dense vs intermittent split happens inside the model bodies for now
        ctx.model = "TBD-active"
    return ctx


def phase_g_finalize(ctx: ForecastContext, fc) -> ForecastContext:
    """PhaseG -- Guards + finalization.

    Reads: ctx.fcst (final), ctx.manual
    Writes: ctx.fcst (after MP snap), G2 demotion if all-zero, alert generation

    G2 demotion: if a non-Inactive model produced all-zero fcst, relabel it.
    Then snap to master pack one final time.
    """
    if sum(ctx.fcst) == 0 and ctx.model not in ("Inactive", "OTB (zero)"):
        ctx.meta.setdefault("drivers", []).append(
            f"G2 Model {ctx.model!r} produced all-zero forecast after gates"
        )
        ctx.rule_fires.add("G2")
        ctx.model = f"Inactive (zeroed by guards: {ctx.model})"

    # Re-snap every week to MP
    ctx.fcst = [fc.snap(v, ctx.master_pack) for v in ctx.fcst]
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Top-level entry point
# ─────────────────────────────────────────────────────────────────────────────

def forecast_record_v2(row: dict, master_pack: float,
                       fc_module=None, **kwargs) -> dict:
    """Run the new explicit-phase pipeline. Returns the same dict shape as
    legacy forecast_record() for back-compat with the writeback loop.

    CURRENT BEHAVIOR (Phase 4 initial commit):
      - PhaseA, PhaseB, PhaseG run via the new code path.
      - PhaseC (model), PhaseD/E/F (gates) delegate to the legacy
        forecast_record() body via a focused re-entry. The legacy function
        produces fcst + meta + rule_fires; we wrap them in ForecastContext
        and re-run Phase G.

    As tests cover more rules in scripts/tests/, individual phases will be
    fully lifted out of the legacy function.
    """
    import inventory_forecaster as fc
    if fc_module is None:
        fc_module = fc

    ctx = ForecastContext(row=row, master_pack=master_pack,
                          is_amazon=kwargs.get("is_amazon", False),
                          is_international=kwargs.get("is_international", False),
                          is_offprice=kwargs.get("is_offprice", False),
                          is_ecom=kwargs.get("is_ecom", False),
                          pos_data=kwargs.get("pos_data"))

    # Phase A
    phase_a_history(ctx, fc_module)
    # Phase B
    phase_b_classify(ctx, fc_module)

    # Phases C/D/E/F still delegate -- call legacy code path to populate fcst
    # then re-enter Phase G for final guards.
    legacy_result = fc_module.forecast_record(row, master_pack, **kwargs)
    ctx.fcst = legacy_result.get("fcst", [])
    ctx.model = legacy_result.get("model", ctx.model)
    ctx.baseline = legacy_result.get("baseline", 0)
    ctx.baseline_mode = legacy_result.get("baseline_mode", "")
    ctx.meta.update({k: v for k, v in (legacy_result.get("meta") or {}).items()
                     if k not in ("drivers", "structured_drivers")})
    for d in legacy_result.get("meta", {}).get("drivers", []) or []:
        ctx.meta.setdefault("drivers", []).append(d)
    ctx.rule_fires.update(legacy_result.get("rule_fires", []))

    # Phase G
    phase_g_finalize(ctx, fc_module)

    # Map ForecastContext back to the legacy dict shape
    out = dict(legacy_result)
    out["fcst"]  = ctx.fcst
    out["model"] = ctx.model
    out["_pipeline_version"] = "v2"
    out["rule_fires"] = sorted(ctx.rule_fires | set(legacy_result.get("rule_fires", [])))
    return out
