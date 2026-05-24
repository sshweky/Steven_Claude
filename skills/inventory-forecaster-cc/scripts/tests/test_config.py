"""Tests for the central config.py module: env-var override, schema check."""

import io
import os
import sys
import importlib


def test_constants_have_expected_defaults():
    if "config" in sys.modules:
        del sys.modules["config"]
    # Clear any test env overrides first
    for k in ("ALERT_THRESHOLD", "HORIZON_WEEKS", "F59I_WOS_HEALTHY_GATE"):
        os.environ.pop(k, None)
    import config
    importlib.reload(config)
    assert config.ALERT_THRESHOLD == 0.075
    assert config.HORIZON_WEEKS == 26
    assert config.L52_WEEKS == 52
    assert config.L13_WEEKS == 13
    assert config.L4_WEEKS == 4
    assert config.F59I_WOS_HEALTHY_GATE == 6.0
    assert config.F59J_WOS_RESTOCK_GATE == 8.0
    assert config.FALL_PRIME_DAY_LIFT == 1.30
    assert len(config.PRIME_DAY_BUMPS) == 3


def test_env_var_override():
    os.environ["ALERT_THRESHOLD"] = "0.20"
    os.environ["F59I_WOS_HEALTHY_GATE"] = "9.5"
    if "config" in sys.modules:
        del sys.modules["config"]
    import config
    importlib.reload(config)
    assert config.ALERT_THRESHOLD == 0.20
    assert config.F59I_WOS_HEALTHY_GATE == 9.5
    # Cleanup so other tests aren't polluted
    del os.environ["ALERT_THRESHOLD"]
    del os.environ["F59I_WOS_HEALTHY_GATE"]
    importlib.reload(config)


def test_check_schema_version_match():
    import config
    err = io.StringIO()
    sys.stderr = err
    try:
        ok = config.check_schema_version(
            {"_schema_version": config.SCHEMA_VERSION}, "test.json"
        )
    finally:
        sys.stderr = sys.__stderr__
    assert ok is True
    assert err.getvalue() == ""


def test_check_schema_version_missing():
    import config
    err = io.StringIO()
    sys.stderr = err
    try:
        ok = config.check_schema_version({}, "missing.json")
    finally:
        sys.stderr = sys.__stderr__
    assert ok is False
    assert "no _schema_version" in err.getvalue()


def test_check_schema_version_strict_raises():
    import config
    import pytest
    with pytest.raises(ValueError, match="schema version mismatch"):
        config.check_schema_version(
            {"_schema_version": "1900.01.01"}, "stale.json", strict=True
        )
