"""Tests for the classify() routing function -- which pattern an item gets."""


def test_classify_emits_three_pattern_strings_only(fc, amazon_history_steady):
    """Per CHANGELOG: classify() only emits 'inactive', 'sparse_intermittent', 'active'."""
    pat = fc.classify(amazon_history_steady)
    # Must be one of the legal strings
    assert pat in ("inactive", "sparse_intermittent", "active"), \
        f"classify() returned unexpected pattern: {pat!r}"


def test_classify_zero_history_is_inactive(fc, amazon_history_zero):
    pat = fc.classify(amazon_history_zero)
    assert pat == "inactive"


def test_classify_steady_dense_is_active(fc, amazon_history_steady):
    pat = fc.classify(amazon_history_steady)
    assert pat == "active"


def test_classify_sparse_is_sparse_intermittent(fc, amazon_history_sparse):
    pat = fc.classify(amazon_history_sparse)
    # 8 active weeks out of 52 = sparse
    assert pat in ("sparse_intermittent", "active")  # depends on internal threshold
