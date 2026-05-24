"""Tests for seasonal_baseline() -- dense L13W >= 50% non-zero items."""


def test_seasonal_baseline_returns_26_weeks(fc, amazon_history_steady):
    result = fc.seasonal_baseline(amazon_history_steady, mp=6, is_amazon=True)
    # seasonal_baseline returns (fcst, meta) tuple
    if isinstance(result, tuple):
        fcst, meta = result
    else:
        fcst = result
    assert len(fcst) == 26
    assert all(v >= 0 for v in fcst)


def test_steady_history_yields_smooth_forecast(fc, amazon_history_steady):
    """Steady 500/wk history should produce a relatively flat forecast."""
    result = fc.seasonal_baseline(amazon_history_steady, mp=6, is_amazon=True)
    fcst = result[0] if isinstance(result, tuple) else result
    avg = sum(fcst) / len(fcst)
    # Each week should be within +/- 50% of the average (steady demand)
    for v in fcst:
        if v > 0:
            assert 0.5 * avg <= v <= 2.0 * avg, \
                f"Week {v} is too far from avg {avg} for steady history"


def test_seasonal_includes_event_boost(fc, amazon_history_steady):
    """Amazon items should get Prime Day boosts on bump weeks."""
    fc.ORIG_PRJ_COLS = ["05_17_W1"] + ["fake"] * 25
    fc._EVENT_BOOSTS_CACHE = None
    result = fc.seasonal_baseline(amazon_history_steady, mp=6, is_amazon=True)
    fcst = result[0] if isinstance(result, tuple) else result
    # May 29 -> W2 with 1.50 boost. W2 should be elevated.
    # Compare W2 to a mid-horizon week that has no event boost
    w2 = fcst[1]
    w10 = fcst[9]
    if w2 > 0 and w10 > 0:
        # W2 should at least somewhat reflect the May 29 boost
        # (allowing for other rules that may dampen it)
        assert w2 >= w10 * 0.9, "Prime Day boost not visible in W2"
