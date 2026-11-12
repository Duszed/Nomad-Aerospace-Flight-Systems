"""Where the "sprayer on/off" signal comes from. Configurable per aircraft.

kind     live MAVLink (via gateway NDJSON)        DataFlash .bin
-------  ---------------------------------------  ---------------------------
servo    SERVO_OUTPUT_RAW servo<channel>_raw      RCOU.C<channel>   >= pwm_on
relay    RELAY_STATUS bit <relay>                 RELY.State bit <relay>
flow     NAMED_VALUE_FLOAT <flow_name> (L/min)    <flow_name>.<flow_field>  >= flow_on_lpm
speed    ground speed >= SPRAY_SPEED_MIN          GPS.Spd  (inferred, weakest)

The NOMAD K30 default is the pump on SERVO9 (SERVO9_FUNCTION,22 - SPECS.md).
Whatever decides on/off, a flow meter - if present - is also read so litres
can be measured instead of estimated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

KINDS = ("servo", "relay", "flow", "speed")


@dataclass(frozen=True)
class SprayerConfig:
    kind: str = "servo"
    channel: int = 9
    pwm_on: int = 1150
    relay: int = 0
    flow_name: str = "FLOW"
    flow_field: str = "Rate"
    flow_on_lpm: float = 0.2

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"sprayer kind must be one of {KINDS}, got {self.kind!r}")
        if not 1 <= self.channel <= 16:
            raise ValueError("servo channel must be 1-16")

    @classmethod
    def from_dict(cls, d: dict | None) -> "SprayerConfig":
        if not d:
            return cls()
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        """Short human-readable description, stored in the record."""
        if self.kind == "servo":
            return f"pump servo output, channel {self.channel}, on at PWM >= {self.pwm_on}"
        if self.kind == "relay":
            return f"relay {self.relay}"
        if self.kind == "flow":
            return f"flow meter '{self.flow_name}', on at >= {self.flow_on_lpm:g} L/min"
        return "inferred from ground speed (no sprayer signal)"
