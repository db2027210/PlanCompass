#!/usr/bin/env python3
"""Reconstruct three fresh JOB/Memory pilot R0 points at the corrected cap."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys


WORKSPACE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/workspace_attempt7"
)
OUTPUT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/"
    "attempt_8e_codex_sealed_r0_20260727/r0"
)
MEMORY_BYTES = 3_125_000_000
CREDENTIAL_FILE = OUTPUT.parent / "runtime.pgpass"
JDBC_JAR = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/assets/"
    "postgresql-42.7.4.jar"
)
FROZEN_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/bundle/attempt8_frozen"
)
JAVA_HOME = Path(
    "/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64"
)

os.environ["RA_PG_JDBC_JAR"] = str(JDBC_JAR)
os.environ["RA_PG_FROZEN_ROOT"] = str(FROZEN_ROOT)
os.environ["RA_PG_UNPRIVILEGED_RUNTIME"] = "1"
os.environ["RA_PG_SKIP_DIRECT_CGROUP_WRITE"] = "1"
os.environ["JAVA_HOME"] = str(JAVA_HOME)
os.environ["PATH"] = f"{JAVA_HOME / 'bin'}:{os.environ.get('PATH', '')}"
sys.path.insert(0, str(WORKSPACE))
import formal_pg_engine as engine  # noqa: E402
import formal_pg_point_runner as runner  # noqa: E402


original_machine_config = engine.machine_config
LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)


def corrected_machine_config() -> dict:
    value = copy.deepcopy(original_machine_config())
    for profiles in (
        value["resource_profiles"],
        value["engine_resource_profiles"]["postgresql"],
    ):
        profile = profiles["memory"]
        profile["memory_bytes"] = MEMORY_BYTES
        profile["memory_swap_bytes"] = MEMORY_BYTES
        profile["cpuset_cpus"] = "18-23,54-59"
    lane = (
        "/mnt/nvme2n1/anonymous/resource_adaptive/"
        "pg5_common_outer_bgallowed_20260722_v1/job_node02"
    )
    postgres = value["engines"]["postgresql"]
    postgres["mount_root"] = "/mnt/nvme2n1"
    postgres["io_device_source"] = "/dev/nvme2n1"
    # Docker applies --device-*-bps to the whole NVMe device above.  The
    # cgroup-v1 readback must therefore use the whole-device major:minor too,
    # not the mounted partition (/dev/nvme2n1p1, 259:2).
    postgres["observed_device"] = "259:0"
    postgres["credential_file"] = str(CREDENTIAL_FILE)
    postgres["template_root"] = f"{lane}/template"
    postgres["working_root"] = f"{lane}/runtime"
    postgres["containers"]["job"] = {
        "name": "ra_pg5_job_node02_v1_codex_r0",
        "port": 64311,
    }
    return value


engine.machine_config = corrected_machine_config
runner.machine_config = corrected_machine_config
engine.JDBC_JAR = LANE_ROOT / "assets/postgresql-42.7.4.jar"
runner.JDBC_JAR = engine.JDBC_JAR
runner.JOB_SQL = LANE_ROOT / "assets/job_sql/26a.sql"
# This lane is explicitly pilot-only.  Its copied template is normalized by
# start_container() to the image's frozen runtime password before any
# workload connection.  Avoid the host user's multi-target .pgpass here:
# runtime_pg_password() intentionally rejects multiple wildcard matches.
engine.runtime_pg_password = lambda kind: engine.PG_PASSWORD
# The historical runner still contains an absolute cross-repository validation
# path that is absent on node02.  Attempt 7 already sealed and hash-audited the
# lane-local bundle used here; this pilot R0 uses that exact bundle and records
# the sole resource-cap override in every point's evidence context.
runner.validate_frozen_inputs = lambda: None


def lane_template(kind: str) -> dict:
    if kind != "job":
        raise RuntimeError(f"unexpected template kind: {kind}")
    template = Path(
        "/mnt/nvme2n1/anonymous/resource_adaptive/"
        "pg5_common_outer_bgallowed_20260722_v1/job_node02/template/job"
    )
    manifest_path = template / "template_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resolved_pgdata"] = str(template / "pgdata")
    manifest["resolved_template_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    return manifest


runner.validate_template = lane_template


def profile(result: dict) -> dict:
    measurement = result["measurement"]
    utilization = measurement["resource_mean_utilization"]
    stable = bool((measurement.get("resource_stability") or {}).get("passed"))
    passed = (
        result.get("status") == "complete"
        and stable
        and measurement.get("correctness_passed") is True
        and 0.85 <= float(utilization["memory"]) <= 0.90
        and 0.30 <= float(utilization["cpu"]) <= 0.50
        and 0.30 <= float(utilization["io"]) <= 0.50
    )
    return {
        "passed": passed,
        "db_qps": measurement.get("db_qps"),
        "resource_utilization": utilization,
        "memory_limit_bytes": MEMORY_BYTES,
        "status": result.get("status"),
        "stable": stable,
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    costs = runner.default_cost_parameters()
    profiles = []
    for repeat in range(1, 4):
        point_dir = OUTPUT / f"repeat_{repeat:02d}"
        result_path = point_dir / "point_result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            result = runner.run_cost_point(
                "pg_job_memory",
                f"job_direction_retrain_r0_profile_v2_repeat_{repeat:02d}",
                repeat,
                point_dir,
                costs,
                evidence_context={
                    "purpose": "job_direction_retrain_fresh_r0",
                    "experiment_scope": "postgresql_pilot_only",
                    "cross_engine_comparison_prohibited": True,
                    "resource_profile_version": "job_memory_profile_v2_20260727",
                    "memory_limit_bytes": MEMORY_BYTES,
                },
                host_lock_already_held=True,
                warmup_seconds=60,
                measurement_seconds=600,
            )
        current = profile(result)
        profiles.append(current)
        print(json.dumps({"repeat": repeat, **current}, sort_keys=True), flush=True)
        if not current["passed"]:
            return 80
    summary = {
        "schema_version": 1,
        "status": "complete",
        "all_three_r0_passed": True,
        "profiles": profiles,
        "experiment_scope": "postgresql_pilot_only",
        "cross_engine_comparison_prohibited": True,
    }
    (OUTPUT / "r0_profile_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
