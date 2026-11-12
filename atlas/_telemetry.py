"""Bridge to the existing `telemetry/` modules.

`telemetry/` is a folder of standalone scripts (not a package), so Atlas puts
it on sys.path once and re-exports the modules it builds on.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TELEMETRY_DIR = REPO_ROOT / "telemetry"

if str(TELEMETRY_DIR) not in sys.path:
    sys.path.insert(0, str(TELEMETRY_DIR))

import coverage_logger  # noqa: E402
import spray_planner  # noqa: E402

__all__ = ["coverage_logger", "spray_planner", "REPO_ROOT", "TELEMETRY_DIR"]
