"""
html_builder.py — Generate the self-contained HTML dashboard.

Takes:
  - results: list[dict] from trend_engine.analyze_all()
  - weekly_long: pd.DataFrame with the weekly time series
  - catalog: pd.DataFrame with ASIN attributes (brand, master_brand, description, ...)

Emits:
  - A single-file HTML with React + Recharts + Tailwind (via CDN) and all
    data embedded as JSON. No external data dependencies.
"""
from __future__ import annotations

import json
import math
import pandas as pd
from pathlib import Path
from typing import Iterable
from . import driver_decomp as dd

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "assets" / "dashboard_template.html"


def _clean_float(x):
    """JSON can't serialize NaN/Inf — coerce to None."""
    if x is None:
        return None
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
    return x


def _round(x, n=2):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def build_payload(results: Iterable[dict],
                  weekly_long: pd.DataFrame,
                  catalog: pd.DataFrame,
                  baseline_mode: str = "exclusive") -> dict:
    """Build the JSON payload the HTML dashboard consumes."""
    catalog_idx = catalog.set_index("asin").to_dict(orient="index")
    weekly_grouped = dict(tuple(weekly_long.groupby("asin")))

    asins_out = []
    for r in results:
        asin = r["asin"]
        cat = catalog_idx.get(asin, {})
        wkly = weekly_grouped.get(asin)

        # 52w weekly series (rounded for compact JSON)
        if wkly is not None and len(wkly):
            wkly = wkly.sort_values("week_start")
            weekly_records = [{
                "wk":  row["week_start"] if isinstance(row["week_start"], str)
                       else row["week_start"].isoformat() if hasattr(row["week_start"], "isoformat")
                       else str(row["week_start"]),
                "u":   _round(row.get("units"), 1),
                "r":   _round(row.get("revenue"), 0),
                "gv":  _round(row.get("gv"), 0),
                "cr":  _round(row.get("cr"), 4),
                "asp": _round(row.get("asp"), 2),
                "oos": int(row.get("oos_signal", 0) or 0),
                "bsr": _round(row.get("bsr"), 0),
            } for _, row in wkly.iterrows()]
            drv = dd.decompose(wkly)
        else:
            weekly_records = []
            drv = {"ranked": [], "narrative": "No data.", "baseline_weeks": 13}

        # Clean composite + indices for JSON
        composite = {k: _round(v, 4) for k, v in (r.get("composite") or {}).items()}
        units_indices   = {k: _round(v, 4) for k, v in (r.get("units_indices")   or {}).items()}
        revenue_indices = {k: _round(v, 4) for k, v in (r.get("revenue_indices") or {}).items()}
        drivers_l13 = {k: _round(v, 4) if isinstance(v, (int, float)) else v
                       for k, v in (r.get("drivers_l13") or {}).items()}
        totals = {k: _round(v, 2) for k, v in (r.get("totals") or {}).items()}

        asins_out.append({
            "asin":          asin,
            "brand":         cat.get("brand", "—"),
            "master_brand":  cat.get("master_brand", "—"),
            "description":   cat.get("description", ""),
            "pack_size":     cat.get("pack_size"),
            "list_price":    _round(cat.get("list_price"), 2),

            "bucket":        r.get("bucket"),
            "bucket_label":  r.get("bucket_label"),
            "pattern":       list(r.get("pattern", [0, 0, 0])),
            "mixed_signal":  bool(r.get("mixed_signal")),
            "volatile":      bool(r.get("volatile")),

            "composite":         composite,
            "units_indices":     units_indices,
            "revenue_indices":   revenue_indices,
            "drivers_l13":       drivers_l13,
            "drivers_ranked":    drv["ranked"],
            "drivers_narrative": drv["narrative"],
            "totals":            totals,
            "weekly":            weekly_records,
        })

    # Bucket summary
    from . import trend_engine as te
    bucket_df = te.bucket_summary(results)
    bucket_summary = bucket_df.to_dict(orient="records") if not bucket_df.empty else []

    payload = {
        "generated_at":   pd.Timestamp.now().isoformat(),
        "baseline_mode":  baseline_mode,
        "asin_count":     len(asins_out),
        "bucket_summary": bucket_summary,
        "asins":          asins_out,
    }
    return payload


def _sanitize(obj):
    """Recursively walk a dict/list tree and replace NaN/Inf floats with None.
    JSON spec forbids NaN; this is a belt-and-suspenders pass before encoding."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj


def render(results: Iterable[dict],
           weekly_long: pd.DataFrame,
           catalog: pd.DataFrame,
           out_path: str | Path,
           baseline_mode: str = "exclusive") -> Path:
    """Render the dashboard HTML to disk and return the path."""
    payload = build_payload(results, weekly_long, catalog, baseline_mode=baseline_mode)
    payload = _sanitize(payload)
    payload_json = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = template.replace("__PAYLOAD_JSON__", payload_json)
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
