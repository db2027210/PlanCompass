#!/usr/bin/env python3
"""Refresh scene authority with the same 15-second gate as formal waves."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v10 as gate_v10
from scripts import refresh_mysql_two_worker_scene_authority_v1 as predecessor


class _HandoffGate:
    @staticmethod
    def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
        requested = float(kwargs.get("required_seconds") or 0.0)
        kwargs["required_seconds"] = 15.0
        result = gate_v10.rolling_quiet_gate(**kwargs)
        result["handoff_window_successor"] = {
            "requested_predecessor_seconds": requested,
            "effective_required_seconds": 15.0,
            "matches_formal_candidate_wave_gate": True,
            "maximum_wait_seconds_unchanged": float(kwargs["maximum_wait_seconds"]),
            "resource_checks_unchanged": True,
            "fail_closed_unchanged": True,
            "external_process_control_or_stop_performed": False,
        }
        return result


predecessor.dynamic_gate = _HandoffGate

if __name__ == "__main__":
    raise SystemExit(predecessor.main())
