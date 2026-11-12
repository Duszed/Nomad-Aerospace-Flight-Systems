"""
Tests for the additive telemetry extensions that feed Nomad Atlas.

  * gateway: forwards the sprayer signals (read-only) and the aircraft clock
  * coverage_logger: optional explicit sprayer source + track capture
  * simulate_telemetry: mission mode (pure sample generator)
  * spray_planner: field boundary helper

The original 50 tests are untouched; these only cover what was added.
"""

import inspect

import pytest

import mavlink_ground_gateway as gw_mod
import simulate_telemetry as sim
from coverage_logger import CoverageLogger
from mavlink_ground_gateway import NomadTelemetryGateway
from spray_planner import field_boundary_lonlat, plan_mission


class FakeMsg:
    def __init__(self, kind, **fields):
        self._kind = kind
        self.__dict__.update(fields)

    def get_type(self):
        return self._kind


@pytest.fixture
def gw():
    return NomadTelemetryGateway()


# --- gateway -----------------------------------------------------------------

def test_gateway_forwards_pump_servo_output(gw):
    servos = {"servo%d_raw" % n: 1500 for n in range(1, 17)}
    servos["servo9_raw"] = 1100
    row = gw.to_payload(FakeMsg("SERVO_OUTPUT_RAW", **servos))
    assert row["type"] == "SERVO_OUTPUT_RAW"
    assert len(row["servo_raw"]) == 16
    assert row["servo_raw"][8] == 1100          # SERVO9 = spray pump (SPECS.md)


def test_gateway_position_row_carries_aircraft_clock(gw):
    row = gw.to_payload(FakeMsg("GLOBAL_POSITION_INT", lat=404109830, lon=688413000,
                                relative_alt=3000, time_boot_ms=12345))
    assert row["lat"] == pytest.approx(40.410983)
    assert row["alt_rel_m"] == 3.0
    assert row["time_boot_ms"] == 12345


def test_gateway_forwards_flow_meter_and_relay(gw):
    row = gw.to_payload(FakeMsg("NAMED_VALUE_FLOAT", name="FLOW\x00\x00", value=4.41))
    assert row["name"] == "FLOW" and row["value"] == pytest.approx(4.41)
    row = gw.to_payload(FakeMsg("RELAY_STATUS", on=1, present=3))
    assert row["relay_on"] == 1 and row["relay_present"] == 3


def test_gateway_battery_row_unchanged(gw):
    row = gw.to_payload(FakeMsg("SYS_STATUS", voltage_battery=47000, current_battery=3600))
    assert row["battery_voltage_v"] == 47.0
    assert row["battery_state"] == "WARN"
    assert row["battery_current_a"] == 36.0


def test_gateway_never_commands_the_aircraft():
    """Observe-only: the single thing the gateway sends is a stream-rate request."""
    src = inspect.getsource(gw_mod)
    sends = [line.strip() for line in src.splitlines() if "_send(" in line]
    assert sends == ["self.vehicle.mav.command_long_send("]
    assert src.count("MAV_CMD_") == 2  # docstring mention + SET_MESSAGE_INTERVAL
    assert "MAV_CMD_SET_MESSAGE_INTERVAL" in src
    for forbidden in ("set_mode", "arducopter_arm", "mission_item", "rc_channels_override",
                      "param_set", "MAV_CMD_DO_", "MAV_CMD_NAV_", "COMPONENT_ARM"):
        assert forbidden not in src


# --- coverage logger ---------------------------------------------------------

def fix(lat, t_ms, lon=68.8413):
    return {"type": "GLOBAL_POSITION_INT", "ts": 1000.0 + t_ms / 1000, "lat": lat,
            "lon": lon, "alt_rel_m": 3.0, "time_boot_ms": t_ms}


def servo(pwm):
    raw = [1500] * 16
    raw[8] = pwm
    return {"type": "SERVO_OUTPUT_RAW", "servo_raw": raw}


def test_default_logger_behaviour_is_unchanged():
    lg = CoverageLogger("F")
    assert lg.spray_source == {"kind": "speed"} and lg.track == []
    lg.consume({"type": "VFR_HUD", "groundspeed_ms": 7.0})
    lg.consume(fix(40.0, 0))
    lg.consume(fix(40.0001, 1000))
    assert lg.rec.sprayed_distance_m > 0 and lg.track == []


def test_servo_source_overrides_speed_gate():
    lg = CoverageLogger("F", spray_source={"kind": "servo"}, keep_track=True)
    lg.consume({"type": "VFR_HUD", "groundspeed_ms": 7.0})
    lg.consume(servo(1100))                    # flying fast, pump off
    lg.consume(fix(40.0, 0))
    lg.consume(fix(40.0001, 1000))
    assert lg.rec.sprayed_distance_m == 0
    assert lg.rec.transit_distance_m > 0
    lg.consume(servo(1500))                    # pump on from this fix
    lg.consume(fix(40.0002, 2000))
    lg.consume(fix(40.0003, 3000))
    assert lg.rec.sprayed_distance_m == pytest.approx(11.1, abs=0.2)
    assert [s["spray_on"] for s in lg.track] == [False, False, True, True]
    assert lg.saw_spray_signal


def test_track_uses_aircraft_clock_and_keeps_height_and_speed():
    lg = CoverageLogger("F", spray_source={"kind": "servo"}, keep_track=True)
    lg.consume({"type": "VFR_HUD", "groundspeed_ms": 6.5})
    lg.consume(servo(1500))
    lg.consume(fix(40.0, 2500))
    s = lg.track[0]
    assert s["t"] == 2.5 and s["alt_rel_m"] == 3.0 and s["groundspeed_ms"] == 6.5


def test_relay_and_flow_sources():
    lg = CoverageLogger("F", spray_source={"kind": "relay", "relay": 1}, keep_track=True)
    lg.consume({"type": "RELAY_STATUS", "relay_on": 0b10, "relay_present": 0b11})
    lg.consume(fix(40.0, 0))
    assert lg.track[0]["spray_on"] is True

    lg = CoverageLogger("F", spray_source={"kind": "flow"}, keep_track=True)
    lg.consume({"type": "NAMED_VALUE_FLOAT", "name": "FLOW", "value": 0.0})
    lg.consume(fix(40.0, 0))
    lg.consume({"type": "NAMED_VALUE_FLOAT", "name": "FLOW", "value": 4.4})
    lg.consume(fix(40.0001, 1000))
    assert [s["spray_on"] for s in lg.track] == [False, True]
    assert lg.track[1]["flow_lpm"] == 4.4 and lg.saw_flow


def test_glitch_fix_is_not_added_to_track():
    lg = CoverageLogger("F", spray_source={"kind": "servo"}, keep_track=True)
    lg.consume(servo(1500))
    lg.consume(fix(40.0, 0))
    lg.consume(fix(41.0, 1000))                # 111 km jump
    assert lg.rec.rejected_fixes == 1 and len(lg.track) == 1


# --- simulator mission mode --------------------------------------------------

def test_demo_mission_is_4_2_ha_7m_swath_20_passes():
    plan = plan_mission(sim.DEMO_AREA_HA, sim.DEMO_WIDTH_M)
    assert plan.passes == 20 and plan.swath_m == 7.0
    assert plan.pass_length_m == pytest.approx(300.0)


def test_perfect_mission_sprays_exactly_the_passes():
    samples = sim.mission_samples(faults=None)
    spray_s = sum(0.2 for s in samples if s["spray_on"])
    assert spray_s == pytest.approx(20 * 300 / 7.0, rel=0.01)
    assert samples[-1]["spray_on"] is False
    assert all(s["pump_pwm"] == (sim.PUMP_PWM_ON if s["spray_on"] else sim.PUMP_PWM_OFF)
               for s in samples)


def test_mission_flow_matches_specs_required_flow():
    """SPECS.md: ~4.4 L/min at 15 L/ha x 7 m swath x 7 m/s cruise."""
    samples = sim.mission_samples(faults=None)
    assert samples[5]["flow_lpm"] == pytest.approx(4.41, abs=0.01)


def test_faulty_mission_has_a_dropout_and_is_deterministic():
    a = sim.mission_samples(faults=sim.DEFAULT_FAULTS)
    b = sim.mission_samples(faults=sim.DEFAULT_FAULTS)
    assert a == b
    perfect = sim.mission_samples(faults=None)
    lost = sum(s["spray_on"] for s in perfect) - sum(s["spray_on"] for s in a)
    assert lost * 0.2 * 7.0 == pytest.approx(60.0, abs=3.0)   # 60 m dropout


def test_mission_time_is_monotonic_and_battery_stays_above_low():
    samples = sim.mission_samples(faults=sim.DEFAULT_FAULTS)
    assert all(b["t"] > a["t"] for a, b in zip(samples, samples[1:]))
    assert min(s["battery_v"] for s in samples) > gw_mod.VOLT_WARN


# --- planner boundary helper -------------------------------------------------

def test_field_boundary_encloses_all_waypoints():
    ring = field_boundary_lonlat(4.2, 140.0)
    assert ring[0] == ring[-1] and len(ring) == 5
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    for wp in plan_mission(4.2, 140.0).waypoints:
        assert min(lons) < wp.lon < max(lons)
        assert min(lats) - 1e-9 <= wp.lat <= max(lats) + 1e-9


def test_field_boundary_validates_input():
    with pytest.raises(ValueError):
        field_boundary_lonlat(0, 140.0)
