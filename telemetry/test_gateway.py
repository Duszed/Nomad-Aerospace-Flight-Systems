"""
Nomad Aerospace - Ground Gateway Unit Tests
============================================
Run with:  pytest test_gateway.py -v

These tests exercise the gateway's battery-state classification logic
directly (no MAVLink connection, no hardware required) and verify it
against the 14S failsafe ladder published in SPECS.md:

    Arming gate  50.4 V (3.6 V/cell)
    LOW          47.6 V (3.4 V/cell)  -> FC will RTL
    CRITICAL     46.2 V (3.3 V/cell)  -> FC will Land

The gateway itself never commands the aircraft (see the authority model
in mavlink_ground_gateway.py) - these tests only confirm the gateway
classifies and alerts correctly, matching what the flight controller
is independently configured to do in /config.
"""

import pytest

from mavlink_ground_gateway import NomadTelemetryGateway, VOLT_WARN, VOLT_CRIT


@pytest.fixture
def gw():
    """A gateway instance with no live connection - safe to unit test."""
    return NomadTelemetryGateway()


def test_thresholds_match_specs():
    """The ladder must match SPECS.md exactly: 47.6V LOW, 46.2V CRIT."""
    assert VOLT_WARN == pytest.approx(47.6)
    assert VOLT_CRIT == pytest.approx(46.2)


def test_full_battery_is_ok(gw):
    assert gw._check_battery(58.8) == "OK"


def test_just_above_low_threshold_is_ok(gw):
    assert gw._check_battery(47.7) == "OK"


def test_at_low_threshold_is_warn(gw):
    # VOLT_WARN is an exclusive upper bound: voltage < VOLT_WARN triggers WARN
    assert gw._check_battery(47.5) == "WARN"


def test_at_critical_threshold_is_crit(gw):
    assert gw._check_battery(46.0) == "CRIT"


def test_deeply_discharged_is_crit(gw):
    assert gw._check_battery(40.0) == "CRIT"


def test_state_only_logs_on_transition(gw, caplog):
    """Repeated readings in the same band should not spam the log."""
    import logging
    caplog.set_level(logging.WARNING)

    gw._check_battery(47.0)   # OK -> WARN, should log
    gw._check_battery(46.9)   # still WARN, should NOT log again
    gw._check_battery(46.8)   # still WARN, should NOT log again

    warn_records = [r for r in caplog.records if "LOW" in r.message]
    assert len(warn_records) == 1


def test_recovery_from_warn_to_ok(gw):
    gw._check_battery(47.0)          # -> WARN
    assert gw._check_battery(50.0) == "OK"   # pack swapped / recovered


def test_full_ladder_sequence(gw):
    """Simulates a full discharge run, as produced by simulate_telemetry.py.
    Both thresholds are exclusive (v < threshold), so a reading exactly AT
    47.6 or 46.2 has not yet crossed into the next band."""
    states = [gw._check_battery(v) for v in
              [58.8, 52.0, 48.0, 47.6, 47.0, 46.5, 46.2, 46.0, 45.0]]
    assert states == ["OK", "OK", "OK", "OK", "WARN", "WARN", "WARN", "CRIT", "CRIT"]
