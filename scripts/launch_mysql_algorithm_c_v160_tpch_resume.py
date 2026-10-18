#!/usr/bin/env python3
"""Launch TPC-H with normalization installed in the live measurement module."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v159_tpch_resume as base
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v70 as controller


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v70.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v160/"
    "algorithm_c_full_resume_contract.json"
)
FAILED_SOURCE = base.HISTORY / (
    "c_v159_tpch_normalization_resume_20260923_tpch/run_state.json"
)


def main() -> int:
    controller.validate_contract()
    old = (base.CONTROLLER, base.CONTRACT, base.FAILED_SOURCE)
    base.CONTROLLER, base.CONTRACT, base.FAILED_SOURCE = (
        CONTROLLER, CONTRACT, FAILED_SOURCE,
    )
    try:
        result = int(base.main())
    finally:
        base.CONTROLLER, base.CONTRACT, base.FAILED_SOURCE = old
    if result == 0 and "--run-prefix" in sys.argv:
        prefix = sys.argv[sys.argv.index("--run-prefix") + 1]
        manifest_path = base.HISTORY / prefix / "launch_manifest.json"
        manifest = base.load(manifest_path)
        manifest.update({
            "schema_version": "mysql_algorithm_c_v160_tpch_resume_launch_v1",
            "release_version": "v160-live-tpch-normalization-resume",
            "tpch_live_measurement_normalization_installed": True,
            "failed_v159_outer_credit": 0,
        })
        base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
