#!/usr/bin/env python3
"""Launch the v158 startup-baseline-lineage correction."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v157_full_resume as base
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v68 as controller


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v68.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v158/"
    "algorithm_c_full_resume_contract.json"
)


def main() -> int:
    controller.validate_contract()
    old_controller, old_contract = base.CONTROLLER, base.CONTRACT
    base.CONTROLLER, base.CONTRACT = CONTROLLER, CONTRACT
    try:
        result = int(base.main())
    finally:
        base.CONTROLLER, base.CONTRACT = old_controller, old_contract
    if result == 0 and "--run-prefix" in sys.argv:
        prefix = sys.argv[sys.argv.index("--run-prefix") + 1]
        manifest_path = base.HISTORY / prefix / "launch_manifest.json"
        manifest = base.load(manifest_path)
        manifest.update({
            "schema_version": "mysql_algorithm_c_v158_full_resume_launch_v1",
            "release_version": "v158-startup-baseline-lineage-resume",
            "startup_paper_baseline_gate_is_campaign_invariant": True,
            "post_outer_incumbent_is_not_startup_baseline": True,
        })
        base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
