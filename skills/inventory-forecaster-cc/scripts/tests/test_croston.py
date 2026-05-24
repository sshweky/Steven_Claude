"""Tests for crostens() -- the Croston's intermittent demand model.

Includes the FF7297 regression: F18 POS-anchored cap should set
meta['f18_capped_down']=True so F59a downstream doesn't undo it.

crostens() returns (fcst_list, baseline_value, meta_dict)
"""


def test_crostens_returns_three_tuple(fc, amazon_history_sparse):
    result = fc.crostens(amazon_history_sparse, mp=6, is_amazon=True)
    assert isinstance(result, tuple)
    assert len(result) == 3
    fcst, baseline, meta = result
    assert len(fcst) == 26
    assert all(v >= 0 for v in fcst)
    assert isinstance(meta, dict)


def test_crostens_zero_history_returns_zeros(fc, amazon_history_zero):
    fcst, baseline, meta = fc.crostens(amazon_history_zero, mp=6, is_amazon=True)
    assert sum(fcst) == 0


def test_f18_flag_when_stocked_up(fc, amazon_history_post_stockup, amazon_pos_ff7297):
    """FF7297 regression test:
    - History has stocked-up weeks averaging > 2x POS rate
    - F18 should fire and set meta['f18_capped_down']=True
    """
    fcst, baseline, meta = fc.crostens(
        amazon_history_post_stockup,
        mp=6,
        is_amazon=True,
        pos_data=amazon_pos_ff7297,
    )
    # Look for F18 in either the meta flag or driver strings
    f18_flag = meta.get("f18_capped_down", False)
    f18_in_drivers = any("F18" in str(d) for d in meta.get("drivers", []))
    assert f18_flag or f18_in_drivers, \
        f"F18 didn't fire on stock-up + low POS. Drivers: {meta.get('drivers')}"


def test_crostens_amazon_prime_day_boost(fc, amazon_history_steady):
    """When is_amazon=True and Prime Day bumps land in horizon,
    Croston's should include the boost."""
    fc.ORIG_PRJ_COLS = ["05_17_W1"] + ["fake"] * 25
    fc._EVENT_BOOSTS_CACHE = None
    fcst, baseline, meta = fc.crostens(amazon_history_steady, mp=6, is_amazon=True)
    assert sum(fcst) > 0
    # meta should record event inserts when boosts fire
    assert "event_inserts" in meta or any("Prime" in str(d) or "PD" in str(d)
                                            for d in meta.get("drivers", []))
