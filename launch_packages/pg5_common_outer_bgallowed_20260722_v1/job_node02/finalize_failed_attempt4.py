#!/usr/bin/env python3
"""Recover only a4 cleanup and publish a fail-closed gate without rewriting results."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ROOT = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_4"
FROZEN = LANE_ROOT / "bundle" / "attempt4_frozen"
OWNED_PIDS = (60193, 60198, 60199, 60200, 60201)
EXPECTED_SCORING_SHA256 = (
    "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588"
)
EXPECTED_R0_GATE_SHA256 = (
    "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    alive = [pid for pid in OWNED_PIDS if Path(f"/proc/{pid}").exists()]
    if alive:
        raise RuntimeError(f"attempt4 owned processes still alive: {alive}")
    result_path = ROOT / "point_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "failed" or result.get("error_type") != "ValueError":
        raise RuntimeError("attempt4 is not the expected cleanup-guard failure")
    expected_fragment = "is not in the subpath of"
    if expected_fragment not in str(result.get("error")):
        raise RuntimeError("attempt4 failed for an unexpected reason")
    inspect = subprocess.run(
        ["docker", "inspect", "ra_pg5_job_node02_v1_a4"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if inspect.returncode == 0:
        raise RuntimeError("attempt4 container unexpectedly remains")

    runtime_root = Path(str(result["restore"]["runtime_root"])).resolve()
    allowed_root = (LANE_ROOT / "work" / "runtime_attempt4").resolve()
    runtime_root.relative_to(allowed_root)
    existed_before = runtime_root.is_dir()
    if existed_before:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--volume",
                f"{runtime_root}:/lane-runtime",
                "--entrypoint",
                "sh",
                "postgres:17",
                "-c",
                "find /lane-runtime -mindepth 1 -depth -delete",
            ],
            check=True,
        )
        runtime_root.rmdir()
    if runtime_root.exists() or runtime_root.is_symlink():
        raise RuntimeError("exact attempt4 runtime root still exists after recovery")

    measurement = result["measurement"]
    utilization = measurement["resource_mean_utilization"]
    scoring_path = FROZEN / "common_scoring_contract.json"
    r0_gate_path = FROZEN / "r0_resource_gate_contract.json"
    protocol = json.loads((FROZEN / "measurement_protocol.json").read_text(encoding="utf-8"))
    checks = {
        "point_complete": False,
        "measurement_evidence_complete": (
            int(measurement.get("effective_measurement_seconds", 0)) == 600
            and int(measurement.get("sample_count", 0)) == 599
        ),
        "correctness": measurement.get("correctness_passed") is True,
        "error_rate": float(measurement.get("error_rate", 1.0))
        <= float(protocol["baseline_gates"]["error_rate_max"]),
        "sample_coverage": float(measurement.get("sample_coverage", 0.0))
        >= float(protocol["timing"]["sample_coverage_min"]),
        "stability": measurement.get("resource_stability", {}).get("passed") is True,
        "memory_strictly_above_80": float(utilization["memory"]) > 0.80,
        "cpu_30_to_50": 0.30 <= float(utilization["cpu"]) <= 0.50,
        "io_30_to_50": 0.30 <= float(utilization["io"]) <= 0.50,
        "no_oom": result.get("oom_killed") is False,
        "no_database_restart": int(result.get("database_restart_count", -1)) == 0,
        "common_scoring_contract_hash": sha256_file(scoring_path)
        == EXPECTED_SCORING_SHA256,
        "r0_resource_gate_contract_hash": sha256_file(r0_gate_path)
        == EXPECTED_R0_GATE_SHA256,
        "cleanup_recovered": True,
    }
    cleanup = {
        "schema_version": 1,
        "status": "recovered",
        "recovered_at_utc": utc_now(),
        "failure_scope": "post_measurement_cleanup_path_guard_only",
        "original_error": result["error"],
        "container_already_removed_by_original_cleanup": True,
        "runtime_root": str(runtime_root),
        "runtime_root_existed_before_recovery": existed_before,
        "runtime_root_absent_after_recovery": True,
        "template_preserved": True,
        "external_processes_or_paths_touched": False,
        "point_result_rewritten": False,
        "raw_evidence_rewritten": False,
    }
    cleanup_path = ROOT / "cleanup_recovery.json"
    atomic_json(cleanup_path, cleanup)
    gate = {
        "schema_version": 1,
        "status": "failed",
        "outer_allowed": False,
        "evaluated_at_utc": utc_now(),
        "freeze_id": result["freeze_id"],
        "point_id": "R0",
        "attempt": 4,
        "failure_reasons": [
            name for name, passed in checks.items() if passed is not True
        ],
        "checks": checks,
        "observed_resource_mean_utilization": utilization,
        "measurement_termination_reason": measurement.get("measurement_termination_reason"),
        "accepted_shrink_count": 0,
        "point_result_path": str(result_path),
        "point_result_sha256": sha256_file(result_path),
        "cleanup_recovery_path": str(cleanup_path),
        "cleanup_recovery_sha256": sha256_file(cleanup_path),
        "common_scoring_contract_sha256": sha256_file(scoring_path),
        "r0_resource_gate_contract_sha256": sha256_file(r0_gate_path),
    }
    gate_path = ROOT / "baseline_gate_decision.json"
    atomic_json(gate_path, gate)
    state_path = ROOT / "point_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["post_failure_cleanup_recovery"] = {
        "status": "recovered",
        "path": str(cleanup_path),
        "sha256": sha256_file(cleanup_path),
    }
    state["baseline_hard_gate"] = {
        "status": "failed",
        "outer_allowed": False,
        "path": str(gate_path),
        "sha256": sha256_file(gate_path),
    }
    state["accepted_shrink_count"] = 0
    atomic_json(state_path, state)
    print(json.dumps({"cleanup": cleanup, "gate": gate}, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
