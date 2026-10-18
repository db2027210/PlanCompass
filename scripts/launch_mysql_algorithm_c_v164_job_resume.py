#!/usr/bin/env python3
"""Launch the JOB resume with the controller runtime interface exported."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v163_job_resume as base
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v74 as controller


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v74.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v164/"
    "algorithm_c_full_resume_contract.json"
)
FAILED_V163_AUDIT = base.base.base.HISTORY / (
    "c_v163_job_outer2_resume_20260923_prelaunch_failure/"
    "failed_prelaunch_audit.json"
)


def main() -> int:
    controller.validate_contract()
    old = (base.CONTROLLER, base.CONTRACT, base.controller)
    base.CONTROLLER, base.CONTRACT, base.controller = (
        CONTROLLER, CONTRACT, controller,
    )
    try:
        result = int(base.main())
    finally:
        base.CONTROLLER, base.CONTRACT, base.controller = old
    if result == 0 and "--run-prefix" in sys.argv:
        prefix = sys.argv[sys.argv.index("--run-prefix") + 1]
        manifest_path = base.base.base.HISTORY / prefix / "launch_manifest.json"
        manifest = base.base.base.load(manifest_path)
        manifest.update({
            "schema_version": "mysql_algorithm_c_v164_job_resume_launch_v1",
            "release_version": "v164-job-launcher-runtime-interface-resume",
            "launcher_runtime_interface_exported": True,
            "failed_v163_audit": str(FAILED_V163_AUDIT),
            "failed_v163_audit_sha256": base.base.base.sha(FAILED_V163_AUDIT),
            "failed_v163_outer_credit": 0,
        })
        base.base.base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
