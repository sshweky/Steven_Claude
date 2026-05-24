"""Tests for the new structured fire() API."""


def test_fire_appends_structured_driver(fc):
    meta = {}
    fc.fire("F18", meta, phase="BAS", severity="warn",
            narrative="F18 POS cap: implied {implied}/wk vs POS {pos}/wk",
            implied=2693, pos=480)
    assert "structured_drivers" in meta
    assert len(meta["structured_drivers"]) == 1
    entry = meta["structured_drivers"][0]
    assert entry["code"] == "F18"
    assert entry["phase"] == "BAS"
    assert entry["severity"] == "warn"
    assert entry["payload"]["implied"] == 2693
    assert entry["payload"]["pos"] == 480


def test_fire_also_appends_to_drivers(fc):
    """For backwards compatibility with the text-scanning narrative builder."""
    meta = {}
    fc.fire("F18", meta, narrative="F18 POS cap: implied {x}/wk", x=2693)
    assert "drivers" in meta
    assert any("F18" in str(d) for d in meta["drivers"])


def test_fire_without_narrative_skips_drivers(fc):
    meta = {}
    fc.fire("R5", meta, phase="CLS", severity="info", reason="international")
    # structured_drivers gets the entry
    assert len(meta["structured_drivers"]) == 1
    # drivers[] should NOT have a generic stub if narrative was None
    assert "drivers" not in meta or all("R5" not in str(d) for d in meta.get("drivers", []))


def test_fire_handles_format_errors_gracefully(fc):
    meta = {}
    # narrative references {missing} which isn't in payload
    fc.fire("F99", meta, narrative="F99 {missing} placeholder",
            something_else="value")
    # Should not raise; falls back to {payload} dump
    assert "drivers" in meta
    assert "F99" in meta["drivers"][0]


def test_fire_tags_legacy_bucket(fc):
    """fire() should also populate the legacy _RULE_FIRES bucket for back-compat."""
    fc._start_rule_fires()
    fc.fire("F77", {}, narrative="F77 test")
    fires = fc._take_rule_fires()
    assert "F77" in fires


def test_fire_with_no_meta(fc):
    """fire(None) shouldn't blow up -- still tags legacy bucket."""
    fc._start_rule_fires()
    result = fc.fire("F88", None, narrative="F88 test")
    assert result is None
    fires = fc._take_rule_fires()
    assert "F88" in fires
