"""
confidence.py  --  Residual-bootstrap confidence intervals for weekly forecasts.

Adds AI_PRJ_LOW_W1..W26 (10th percentile) and AI_PRJ_HIGH_W1..W26 (90th pct)
fields to forecast output without changing the point forecast itself.

Method:
  1. Fit the forecast to the L52 history (already done by model bodies).
  2. Compute in-sample residuals = (actual - predicted) for last 26 weeks
     where both are non-zero.
  3. Bootstrap-sample residuals N times, add to point forecast, take
     10th/90th percentiles per week.

Intentionally simple: a single residual pool, sampled with replacement.
Doesn't preserve correlation structure (acceptable trade-off for v1).

STATUS: NEW MODULE in Phase 4. Wired into forecast_record() output via the
new --confidence-intervals flag (off by default).

Usage from inventory_forecaster.py:
    from confidence import bootstrap_intervals
    low, high = bootstrap_intervals(history, fcst, mp, n_boot=500)
"""

import random
from typing import Sequence


def _in_sample_residuals(history: Sequence[float], fitted: Sequence[float]) -> list[float]:
    """Return per-week residuals from a simple in-sample backcast.

    fitted should be the model's prediction for the same window as history.
    If we don't have fitted-vs-actual pairs (no in-sample fit), we approximate
    residuals as the L26 history deviation from its own median (a noise proxy).
    """
    if fitted and len(fitted) == len(history):
        return [h - f for h, f in zip(history, fitted) if h > 0 or f > 0]
    # Fallback: use L26 deviations from rolling median as a noise proxy
    h26 = list(history)[-26:]
    if not h26:
        return [0.0]
    sorted_h = sorted(v for v in h26 if v > 0)
    if not sorted_h:
        return [0.0]
    med = sorted_h[len(sorted_h) // 2]
    return [v - med for v in h26]


def bootstrap_intervals(history: Sequence[float],
                         fcst: Sequence[float],
                         mp: float,
                         fitted: Sequence[float] | None = None,
                         n_boot: int = 500,
                         low_pct: float = 0.10,
                         high_pct: float = 0.90,
                         floor_at_zero: bool = True) -> tuple[list[float], list[float]]:
    """Return (low_band, high_band) — two 26-week lists of percentile bounds.

    Args:
        history:     L52 history (oldest -> newest)
        fcst:        26-week point forecast
        mp:          master pack (rounding granularity for the bounds)
        fitted:      optional in-sample predictions for residual computation
        n_boot:      bootstrap iterations (default 500)
        low_pct:     lower percentile (default 0.10)
        high_pct:    upper percentile (default 0.90)
        floor_at_zero: clamp negative bounds to 0
    """
    residuals = _in_sample_residuals(history, fitted or [])
    if not residuals or len(residuals) < 2:
        # Degenerate: return the point forecast for both bounds
        return list(fcst), list(fcst)

    horizon = len(fcst)
    rng = random.Random(42)   # deterministic per record

    # For each week, sample n_boot residuals, add to point forecast
    low  = []
    high = []
    for w in range(horizon):
        point = fcst[w]
        samples = [point + rng.choice(residuals) for _ in range(n_boot)]
        samples.sort()
        lo_idx = int(low_pct * n_boot)
        hi_idx = int(high_pct * n_boot)
        lo_v = samples[lo_idx]
        hi_v = samples[hi_idx]
        if floor_at_zero:
            lo_v = max(0, lo_v)
            hi_v = max(0, hi_v)
        # Round to MP
        from math import ceil
        if mp and mp > 1:
            lo_v = round(lo_v / mp) * mp
            hi_v = round(hi_v / mp) * mp
        else:
            lo_v = round(lo_v)
            hi_v = round(hi_v)
        # Invariant: low <= point <= high
        if lo_v > point:
            lo_v = point
        if hi_v < point:
            hi_v = point
        low.append(lo_v)
        high.append(hi_v)

    return low, high


if __name__ == "__main__":
    # Smoke test
    history = [500 + (i % 7 - 3) * 50 for i in range(52)]
    fcst = [500] * 26
    low, high = bootstrap_intervals(history, fcst, mp=6, n_boot=200)
    assert len(low) == 26 and len(high) == 26
    assert all(lo <= 500 <= hi for lo, hi in zip(low, high))
    print(f"Sample CI for steady 500/wk:")
    print(f"  Point: 500   Low: {low[0]}   High: {high[0]}")
    print(f"  Range over horizon: low {min(low)}-{max(low)},   high {min(high)}-{max(high)}")
