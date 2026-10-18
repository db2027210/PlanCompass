#!/usr/bin/env python3
"""Launch the script-safe JOB outer-2 boundary resume."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v162_job_resume as base
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v73 as controller


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v73.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v163/"
    "algorithm_c_full_resume_contract.json"
)
FAILED_V162_AUDIT = base.base.HISTORY / (
    "c_v162_job_outer2_resume_20260923/failed_resume_attempt_audit.json"
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
        manifest_path = base.base.HISTORY / prefix / "launch_manifest.json"
        manifest = base.base.load(manifest_path)
        manifest.update({
            "schema_version": "mysql_algorithm_c_v163_job_resume_launch_v1",
            "release_version": "v163-job-script-import-path-resume",
            "script_safe_repository_import_path": True,
            "failed_v162_audit": str(FAILED_V162_AUDIT),
            "failed_v162_audit_sha256": base.base.sha(FAILED_V162_AUDIT),
            "failed_v162_outer_credit": 0,
        })
        base.base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
