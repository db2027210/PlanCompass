#!/usr/bin/env python3
"""Refresh a two-worker scene with the v68 container-scoped gate."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v12 as gate_v12
from scripts import refresh_mysql_two_worker_scene_authority_v1 as predecessor


class _HandoffGate:
    @staticmethod
    def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
        requested = float(kwargs.get("required_seconds") or 0.0)
        if requested < 60.0:
            raise RuntimeError("scene launch authority requires a 60-second window")
        requested_wait = float(kwargs.get("maximum_wait_seconds") or 0.0)
        kwargs["maximum_wait_seconds"] = max(requested_wait, 600.0)
        result = gate_v12.rolling_quiet_gate(**kwargs)
        result["handoff_window_successor"] = {
            "requested_predecessor_seconds": requested,
            "effective_required_seconds": requested,
            "scene_launch_window_seconds": 60.0,
            "requested_maximum_wait_seconds": requested_wait,
            "effective_maximum_wait_seconds": kwargs["maximum_wait_seconds"],
            "container_scoped_read_only_statistics_shim_release": "v68",
            "resource_checks_unchanged": True,
            "fail_closed_unchanged": True,
            "external_process_control_or_stop_performed": False,
        }
        return result


predecessor.dynamic_gate = _HandoffGate


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
