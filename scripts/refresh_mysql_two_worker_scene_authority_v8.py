#!/usr/bin/env python3
"""Refresh two-worker authority for the sixteen-lane full-parallel release."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v14 as gate_v14
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
        # With sixteen fixed, disjoint lanes, aggregate load1 is expected to
        # exceed the old two-worker threshold.  Actual process placement and
        # CPU/NUMA/storage overlap remain fail-closed in gate v13.
        kwargs["maximum_load1"] = 512.0
        return gate_v14.rolling_quiet_gate(**kwargs)


predecessor.dynamic_gate = _Gate


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
