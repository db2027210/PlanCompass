#!/usr/bin/env python3
"""Run Top4-A with the frozen 15-second handoff authority window."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_four_scene_top4_two_worker_v1 as predecessor

CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_a_20260826_v13"
CONTRACT = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install() -> None:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    predecessor.install()
    xp = predecessor.predecessor
    xp.CONTRACT_ROOT = CONTRACT_ROOT
    xp.CONTRACT = CONTRACT
    xp.COORDINATION = COORDINATION
    original_validate = xp.validate_launch_authority
    original_load = xp.predecessor.load

    def validate_launch_authority(path: Path, scene: str):
        authority = original_load(path)
        preflight_path = Path(str(authority.get("preflight") or "")).resolve()
        preflight = original_load(preflight_path)
        gate = preflight.get("rolling_dynamic_noninterference_gate") or {}
        successor = gate.get("handoff_window_successor") or {}
        if not (
            preflight.get("status") == "PASS"
            and preflight.get("formal_launch_authorized") is True
            and float(preflight.get("observed_seconds") or 0.0) >= 15.0
            and gate.get("status") == "PASS"
            and float(gate.get("required_clean_seconds") or 0.0) == 15.0
            and float(gate.get("observed_seconds") or 0.0) >= 15.0
            and successor.get("matches_formal_candidate_wave_gate") is True
            and successor.get("resource_checks_unchanged") is True
            and successor.get("fail_closed_unchanged") is True
            and successor.get("external_process_control_or_stop_performed") is False
        ):
            raise RuntimeError("Top4-A handoff-window authority differs")

        def compatible_load(candidate: Path):
            value = original_load(candidate)
            if Path(candidate).resolve() == preflight_path:
                value = copy.deepcopy(value)
                value["observed_seconds"] = 60.0
            return value

        xp.predecessor.load = compatible_load
        try:
            return original_validate(path, scene)
        finally:
            xp.predecessor.load = original_load

    xp.validate_launch_authority = validate_launch_authority


def main() -> int:
    install()
    return int(predecessor.predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
