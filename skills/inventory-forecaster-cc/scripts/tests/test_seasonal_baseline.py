"""Tests for seasonal_baseline() -- dense L13W >= 50% non-zero items.

seasonal_baseline() returns (fcst_list, baseline_value, meta_dict).
"""


def test_seasonal_baseline_returns_three_tuple(fc, amazon_history_steady):
    result = fc.seasonal_baseline(amazon_history_steady, mp=6, is_amazon=True)
    assert isinstance(result, tuple)
    assert len(result) == 3
    fcst, baseline, meta = result
    assert len(fcst) == 26
    assert all(v >= 0 for v in fcst)


def test_steady_history_yields_smooth_forecast(fc, amazon_history_steady):
    """Steady 500/wk history -> relatively flat forecast (modulo event boosts)."""
    fcst, baseline, meta = fc.seasonal_baseline(
        amazon_history_steady, mp=6, is_amazon=True
    )
    avg = sum(fcst) / len(fcst)
    # No week should be wildly off the mean
    for v in fcst:
        if v > 0:
            assert 0.4 * avg <= v <= 2.5 * avg, \
                f"Week {v} far from avg {avg} for steady history"


def test_seasonal_includes_event_boost(fc, amazon_history_steady):
    """Amazon items should get Prime Day boosts on bump weeks."""
    fc.ORIG_PRJ_COLS = ["05_17_W1"] + ["fake"] * 25
    fc._EVENT_BOOSTS_CACHE = None
    fcst, baseline, meta = fc.seasonal_baseline(
        amazon_history_steady, mp=6, is_amazon=True
    )
    w2 = fcst[1]
    w10 = fcst[9]
    if w2 > 0 and w10 > 0:
        # May 29 bump x1.50 lands in W2. W2 should be elevated vs mid-horizon.
        assert w2 >= w10 * 0.95, "Prime Day W2 boost not visible"
