"""Pydantic models: the job details an operator supplies, and their validation."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RECORD_SCHEMA = "nomad-atlas/spray-record/v1"
LANGUAGES = ("uz", "ru", "en")


def normalize_phone(raw: str) -> str:
    """Normalise to +<digits>. Nine-digit numbers are taken as Uzbek (+998)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    if not 10 <= len(digits) <= 15:
        raise ValueError(f"not a valid phone number: {raw!r}")
    return "+" + digits


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FarmerIn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    phone: str
    language: Literal["uz", "ru", "en"] = "uz"

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class WeatherIn(_Strict):
    """Entered by the operator at the field."""
    temperature_c: float | None = Field(default=None, ge=-40, le=60)
    wind_ms: float | None = Field(default=None, ge=0, le=40)
    wind_direction: str | None = Field(default=None, max_length=20)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)


class JobIn(_Strict):
    """Everything about a job that does not come from the flight log."""
    farmer: FarmerIn
    field_name: str = Field(min_length=1, max_length=200)
    crop: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=200)
    batch: str = Field(default="", max_length=100)
    planned_rate_l_ha: float = Field(gt=0, le=1000)
    swath_m: float = Field(default=7.0, gt=0, le=50)
    pilot: str = Field(min_length=1, max_length=200)
    drone_id: str = Field(min_length=1, max_length=100)
    weather: WeatherIn = WeatherIn()
    tank_litres_used: float | None = Field(default=None, ge=0)
    sprayer: dict | None = None
    sample_data: bool = False
