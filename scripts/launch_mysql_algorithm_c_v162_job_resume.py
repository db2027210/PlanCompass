#!/usr/bin/env python3
"""Launch the non-recursive JOB outer-2 boundary resume."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v161_job_resume as base
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v72 as controller


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v72.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v162/"
    "algorithm_c_full_resume_contract.json"
)
FAILED_V161_AUDIT = base.HISTORY / (
    "c_v161_job_outer2_resume_20260923/failed_resume_attempt_audit.json"
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
        manifest_path = base.HISTORY / prefix / "launch_manifest.json"
        manifest = base.load(manifest_path)
        manifest.update({
            "schema_version": "mysql_algorithm_c_v162_job_resume_launch_v1",
            "release_version": "v162-job-outer2-nonrecursive-resume",
            "startup_gate_wrapper_nonrecursive": True,
            "failed_v161_audit": str(FAILED_V161_AUDIT),
            "failed_v161_audit_sha256": base.sha(FAILED_V161_AUDIT),
            "failed_v161_outer_credit": 0,
        })
        base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
