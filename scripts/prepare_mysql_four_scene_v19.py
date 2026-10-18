#!/usr/bin/env python3
"""Prepare the eight v19 four-scene data planes from sealed templates."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as base


FREEZE_SCHEMA = "mysql_four_scene_two_worker_qps_successor_v19"
PREFLIGHT_SCHEMA = "mysql_four_scene_runtime_preflight_v19_transition_v1"


def validate_authority(
    freeze_path: Path, preflight_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze_path = freeze_path.resolve(strict=True)
    preflight_path = preflight_path.resolve(strict=True)
    if freeze_path.is_symlink() or preflight_path.is_symlink():
        raise RuntimeError("freeze and preflight must be regular non-symlink files")
    freeze_value = json.loads(freeze_path.read_text(encoding="utf-8"))
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if (
        freeze_value.get("schema_version") != FREEZE_SCHEMA
        or freeze_value.get("status") != "FROZEN_RUNTIME_READBACK_PENDING"
        or freeze_value.get("formal_scope") != "mysql_only"
        or freeze_value.get("cross_engine_comparison_prohibited") is not True
        or freeze_value.get("formal_launch_authorized") is not False
        or freeze_value.get("launch_performed") is not False
        or len(freeze_value.get("workers") or []) != 8
        or set(freeze_value.get("active_scenes") or []) != {
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
        }
        or (freeze_value.get("full_parallel_successor") or {}).get(
            "physical_workers_per_scene"
        ) != 2
        or (freeze_value.get("full_parallel_successor") or {}).get(
            "four_scene_barrier"
        ) is not False
        or (freeze_value.get("full_parallel_successor") or {}).get(
            "sibling_scene_wait"
        ) is not False
        or (freeze_value.get("full_parallel_successor") or {}).get(
            "lease_mode"
        ) != "none"
    ):
        raise RuntimeError("v19 four-scene freeze identity differs")
    if (
        preflight.get("schema_version") != PREFLIGHT_SCHEMA
        or preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or preflight.get("launch_performed") is not False
        or preflight.get("freeze_path") != str(freeze_path)
        or preflight.get("freeze_sha256") != base._sha(freeze_path)
        or preflight.get("blockers") != []
        or float(preflight.get("observed_seconds") or 0) < 60.0
        or (preflight.get("rolling_dynamic_noninterference_gate") or {}).get(
            "status"
        ) != "PASS"
        or preflight.get("external_process_control_or_stop_performed") is not False
        or preflight.get("postgresql_process_control_or_stop_performed") is not False
    ):
        raise RuntimeError("v19 runtime preflight authority differs or is not PASS")
    image = base._docker_json("image", "inspect", base.IMAGE)
    if image.get("Id") != base.IMAGE_ID:
        raise RuntimeError("local MySQL image identity differs")
    prepared_freeze = copy.deepcopy(freeze_value)
    prepared_freeze["container_owner_label"] = "mysql_four_scene_v5"
    return prepared_freeze, preflight


def main() -> int:
    original = base._validate_authority
    base._validate_authority = validate_authority
    try:
        return base.main()
    finally:
        base._validate_authority = original


if __name__ == "__main__":
    raise SystemExit(main())
