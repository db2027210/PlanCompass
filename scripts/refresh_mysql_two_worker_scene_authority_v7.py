#!/usr/bin/env python3
"""Refresh two-worker authority with the v69 boundary-sealed gate."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v13 as gate_v13
from scripts import refresh_mysql_two_worker_scene_authority_v1 as predecessor


class _Gate:
    @staticmethod
    def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
        requested = float(kwargs.get("required_seconds") or 0.0)
        if requested < 60.0:
            raise RuntimeError("scene launch authority requires a 60-second window")
        kwargs["maximum_wait_seconds"] = max(
            float(kwargs.get("maximum_wait_seconds") or 0.0), 600.0,
        )
        return gate_v13.rolling_quiet_gate(**kwargs)


predecessor.dynamic_gate = _Gate


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
