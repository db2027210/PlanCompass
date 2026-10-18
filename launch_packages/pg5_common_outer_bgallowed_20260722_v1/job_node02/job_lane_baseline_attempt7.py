#!/usr/bin/env python3
"""Run JOB R0 attempt7 with two terminals and proportional resources."""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence


CAMPAIGN_ID = "pg5_common_outer_bgallowed_20260722_v1"
LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
CONTAINER_NAME = "ra_pg5_job_node02_v1_a7"
PORT = 64307


def lane_run(
    command: Sequence[str],
    *,
    check: bool = True,
    capture: bool = True,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    argv = list(command)
    if argv and argv[0] == "sudo":
        argv = argv[1:]
    result = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=dict(env) if env is not None else None,
        timeout=timeout,
        cwd=str(cwd) if cwd is not None else None,
        input=input_text,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed exit={result.returncode}: {argv!r}\n"
            f"stdout={(result.stdout or '')[-4000:]}\nstderr={(result.stderr or '')[-4000:]}"
        )
    return result


@contextlib.contextmanager
def lane_lock() -> Iterator[Dict[str, Any]]:
    path = LANE_ROOT / "lease" / "job_lane.lock"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner = {
            "campaign_id": CAMPAIGN_ID,
            "lane_id": "job_node02",
            "pid": os.getpid(),
            "acquired_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "implementation": "posix_flock_exclusive_nonblocking",
            "background_load_allowed": True,
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(owner, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        try:
            yield {"path": str(path), **owner}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cleanup_runtime(engine: Any, kind: str, restore: Mapping[str, Any]) -> Dict[str, Any]:
    name, _ = engine.container_spec(kind)
    engine.remove_stopped_container(name)
    root = Path(str(restore["runtime_root"])).resolve()
    root.relative_to((LANE_ROOT / "work" / "runtime_attempt7").resolve())
    if root.exists():
        engine.run(
            [
                "docker", "run", "--rm", "--network", "none",
                "--volume", f"{root}:/lane-runtime",
                "--entrypoint", "sh", engine.IMAGE,
                "-c", "find /lane-runtime -mindepth 1 -depth -delete",
            ],
            capture=False,
            timeout=1800,
        )
        root.rmdir()
    return {
        "cleanup_complete": not root.exists(),
        "template_preserved": True,
        "cleanup_transport": "exact validated runtime root via isolated Docker helper",
    }


def start_lane_container(
    engine: Any,
    kind: str,
    pgdata: Path,
    profile_name: str = "none",
    cost_parameters: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    name, port = engine.container_spec(kind)
    if name != CONTAINER_NAME or port != PORT:
        raise engine.FormalPgError(f"unexpected lane endpoint: {name}:{port}")
    if engine.run(["docker", "inspect", name], check=False).returncode == 0:
        raise engine.FormalPgError(f"lane container already exists: {name}")
    tmp_dir = LANE_ROOT / "tmp" / "postgres_attempt7"
    socket_dir = LANE_ROOT / "socket" / "postgresql_attempt7"
    command = [
        "docker", "run", "--detach", "--name", name,
        "--label", f"resource_adaptive.campaign={CAMPAIGN_ID}",
        "--label", "resource_adaptive.lane=job_node02",
        "--publish", f"127.0.0.1:{port}:5432",
        "--volume", f"{pgdata}:/var/lib/postgresql/data",
        "--volume", f"{tmp_dir}:/tmp",
        "--volume", f"{socket_dir}:/var/run/postgresql",
        "--log-driver", "local",
        "--shm-size", str(engine.RUNTIME_SHM_SIZE_BYTES),
        "--health-cmd", "pg_isready -U postgres -d imdb_job",
        "--health-interval", "5s", "--health-timeout", "3s", "--health-retries", "36",
    ]
    command.extend(engine.docker_resource_arguments(profile_name))
    command.append(engine.IMAGE)
    command.extend(engine.fixed_postgres_arguments(profile_name, cost_parameters))
    container_id = engine.run(command).stdout.strip()
    inspect = json.loads(engine.run(["docker", "inspect", container_id]).stdout)[0]
    expected_rate = 268435456
    for field in ("BlkioDeviceReadBps", "BlkioDeviceWriteBps"):
        values = inspect["HostConfig"].get(field) or []
        if not any(row.get("Path") == "/dev/nvme2n1" and int(row.get("Rate", 0)) == expected_rate for row in values):
            raise engine.FormalPgError(f"Docker I/O readback mismatch for {field}: {values}")
    import time
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        health = engine.run(["docker", "inspect", "--format", "{{.State.Health.Status}}", name], check=False)
        if health.stdout.strip() == "healthy":
            break
        state = json.loads(engine.run(["docker", "inspect", name]).stdout)[0]["State"]
        if not state["Running"]:
            logs = engine.run(["docker", "logs", name], check=False)
            raise engine.FormalPgError(f"PostgreSQL stopped before healthy: {(logs.stdout or '')[-8000:]} {(logs.stderr or '')[-8000:]}")
        time.sleep(2)
    else:
        raise engine.FormalPgError("PostgreSQL did not become healthy")
    engine.run(
        ["docker", "exec", "-i", "-u", "postgres", name, "psql", "-v", "ON_ERROR_STOP=1", "-d", "postgres"],
        input_text="ALTER ROLE postgres PASSWORD 'CHANGE_ME';\n",
    )
    return {
        "name": name,
        "port": port,
        "container_id": container_id,
        "profile": profile_name,
        "runtime_shm_size_bytes": engine.RUNTIME_SHM_SIZE_BYTES,
        "docker_command": command,
        "healthcheck": "healthy",
        "io_limit_application": "Docker device BPS flags plus HostConfig readback",
        "data_path": str(pgdata),
        "tmp_path": str(tmp_dir),
        "socket_path": str(socket_dir),
        "docker_root_on_nvme": True,
    }


def main() -> int:
    import p24_pg_recost_runner as p24
    p24.run = lane_run
    p24.CASES["job"]["name"] = CONTAINER_NAME
    p24.CASES["job"]["port"] = PORT
    _, points, validation = p24.configure_modules("job")
    import formal_pg_engine as engine
    engine.run = lane_run
    points.run = lane_run
    points.host_measurement_lock = lane_lock
    points.start_container = lambda kind, pgdata, profile_name="none", cost_parameters=None: start_lane_container(
        engine, kind, pgdata, profile_name, cost_parameters
    )
    points.cleanup_overlay = lambda kind, restore: cleanup_runtime(engine, kind, restore)
    output = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_7"
    costs = points.default_cost_parameters()
    result = points.run_cost_point(
        "pg_job_memory",
        "R0",
        1,
        output,
        costs,
        evidence_context={
            "purpose": "shared_common_outer_baseline_R0_attempt7_two_terminals",
            "attempt": 7,
            "attempt1_retroactive_acceptance": False,
            "attempt2_retroactive_acceptance": False,
            "attempt3_retroactive_acceptance": False,
            "attempt4_retroactive_acceptance": False,
            "attempt5_retroactive_acceptance": False,
            "attempt6_retroactive_acceptance": False,
            "job_terminals": 2,
            "raw_utilization_validation": "finite_and_non_negative_no_upper_bound",
            "raw_utilization_persistence": "original_value_without_clipping",
            "normal_throughput_stability": (
                "per_30s_window_mean_then_cv_across_latest_six_window_means_cv10"
            ),
            "resource_stability": (
                "per_30s_window_mean_then_cv_across_latest_six_window_means_cv10"
            ),
            "io_utilization_definition": (
                "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)"
            ),
            "read_cap_bps": 268435456,
            "write_cap_bps": 268435456,
            "io_total_cap_bps": 536870912,
            "campaign_id": CAMPAIGN_ID,
            "lane_id": "job_node02",
            "background_load_allowed": True,
            "host_idle_gate_waived_by_user": True,
            "external_tasks_stopped_or_overwritten": False,
            "optimization_candidate_budget_consumed": False,
            "source_validation": validation,
        },
        warmup_seconds=60,
        measurement_seconds=600,
    )
    protocol = json.loads(
        (LANE_ROOT / "bundle" / "attempt7_frozen" / "measurement_protocol.json")
        .read_text(encoding="utf-8")
    )
    scoring_path = LANE_ROOT / "bundle" / "attempt7_frozen" / "common_scoring_contract.json"
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    expected_scoring_sha256 = (
        "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588"
    )
    expected_scoring_block = {
        "overload_threshold": 0.8,
        "score_mode": "band_target",
        "resource_weights": {"cpu": 1, "memory": 1, "io": 3},
        "resource_target_ranges": {
            "cpu": {"min_pct": 65, "max_pct": 80, "hard_overload_pct": 90},
            "memory": {"min_pct": 70, "max_pct": 80, "hard_overload_pct": 90},
            "io": {"min_pct": 50, "max_pct": 75, "hard_overload_pct": 90},
        },
    }
    r0_gate_path = LANE_ROOT / "bundle" / "attempt7_frozen" / "r0_resource_gate_contract.json"
    r0_gate = json.loads(r0_gate_path.read_text(encoding="utf-8"))
    expected_r0_gate_sha256 = (
        "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e"
    )
    expected_r0_gate = {
        "schema_version": 1,
        "contract_id": "pg5_r0_single_bottleneck_gate_v1",
        "target_rule": "identify_bottleneck utilization > 0.8",
        "target_min_pct_exclusive": 80,
        "non_target_min_pct_inclusive": 30,
        "non_target_max_pct_inclusive": 50,
        "required_resources": ["cpu", "memory", "io"],
        "candidate_acceptance_affected": False,
    }
    stability_gate_path = (
        LANE_ROOT / "bundle" / "attempt7_frozen" / "stability_gate_contract.json"
    )
    expected_stability_gate_sha256 = (
        "33cf52a83f4a11ba2fd1bffad703aee9c0192141749320b1ece65919a482f6bd"
    )
    io_contract_path = (
        LANE_ROOT / "bundle" / "attempt7_frozen" / "io_utilization_contract.json"
    )
    expected_io_contract_sha256 = (
        "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064"
    )
    bands = protocol["baseline_gates"]
    measurement = result["measurement"]
    utilization = measurement["resource_mean_utilization"]
    io_contract = json.loads(io_contract_path.read_text(encoding="utf-8"))
    derived_path = Path(str(measurement["derived_resource_samples_path"]))
    derived_rows = points.parse_jsonl(derived_path)
    required_io_fields = tuple(io_contract["persist_required_fields"])
    io_fields_persisted = bool(derived_rows) and all(
        all(field in row for field in required_io_fields) for row in derived_rows
    )
    io_identity_verified = io_fields_persisted and all(
        math.isclose(
            float(row["io_utilization"]),
            (float(row["read_bps"]) + float(row["write_bps"]))
            / (float(row["read_cap_bps"]) + float(row["write_cap_bps"])),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        for row in derived_rows
    )
    checks = {
        "point_complete": result.get("status") == "complete",
        "correctness": measurement.get("correctness_passed") is True,
        "error_rate": float(measurement.get("error_rate", 1.0)) <= float(bands["error_rate_max"]),
        "sample_coverage": float(measurement.get("sample_coverage", 0.0))
        >= float(protocol["timing"]["sample_coverage_min"]),
        "stability": measurement.get("resource_stability", {}).get("passed") is True,
        "memory_strictly_above_80": float(utilization["memory"])
        > float(bands["target_resource_utilization_min_exclusive"]),
        "cpu_30_to_50": float(bands["non_target_resource_utilization_min"])
        <= float(utilization["cpu"])
        <= float(bands["non_target_resource_utilization_max"]),
        "io_30_to_50": float(bands["non_target_resource_utilization_min"])
        <= float(utilization["io"])
        <= float(bands["non_target_resource_utilization_max"]),
        "no_oom": result.get("oom_killed") is False,
        "no_database_restart": int(result.get("database_restart_count", -1)) == 0,
        "resource_readback": result.get("readback", {}).get("resource_profile_passed") is True,
        "fixed_settings_readback": result.get("readback", {}).get("fixed_settings_passed") is True,
        "common_scoring_contract_hash": points.sha256_file(scoring_path)
        == expected_scoring_sha256,
        "common_scoring_contract_semantics": (
            scoring == expected_scoring_block
        ),
        "r0_resource_gate_contract_hash": points.sha256_file(r0_gate_path)
        == expected_r0_gate_sha256,
        "r0_resource_gate_contract_semantics": r0_gate == expected_r0_gate,
        "stability_gate_contract_hash": points.sha256_file(stability_gate_path)
        == expected_stability_gate_sha256,
        "io_utilization_contract_hash": points.sha256_file(io_contract_path)
        == expected_io_contract_sha256,
        "io_required_fields_persisted": io_fields_persisted,
        "io_sum_total_identity_verified": io_identity_verified,
    }
    passed = all(checks.values())
    gate = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "outer_allowed": passed,
        "evaluated_at_utc": points.utc_now(),
        "freeze_id": result["freeze_id"],
        "point_id": "R0",
        "attempt": 7,
        "authoritative_r0_gate": {
            "memory": "strictly_greater_than_0.80_no_upper_bound",
            "cpu": [0.30, 0.50],
            "io": [0.30, 0.50],
        },
        "common_scoring_contract": {
            "path": str(scoring_path),
            "sha256": points.sha256_file(scoring_path),
            "expected_sha256": expected_scoring_sha256,
        },
        "r0_resource_gate_contract": {
            "path": str(r0_gate_path),
            "sha256": points.sha256_file(r0_gate_path),
            "expected_sha256": expected_r0_gate_sha256,
        },
        "stability_gate_contract": {
            "path": str(stability_gate_path),
            "sha256": points.sha256_file(stability_gate_path),
            "expected_sha256": expected_stability_gate_sha256,
        },
        "io_utilization_contract": {
            "path": str(io_contract_path),
            "sha256": points.sha256_file(io_contract_path),
            "expected_sha256": expected_io_contract_sha256,
        },
        "observed_resource_mean_utilization": utilization,
        "checks": checks,
        "accepted_shrink_count": 0,
        "point_result_path": str(output / "point_result.json"),
        "point_result_sha256": points.sha256_file(output / "point_result.json"),
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
        "attempt3_retroactive_acceptance": False,
        "attempt4_retroactive_acceptance": False,
        "attempt5_retroactive_acceptance": False,
        "attempt6_retroactive_acceptance": False,
    }
    gate_path = output / "baseline_gate_decision.json"
    points.atomic_json(gate_path, gate)
    state_path = output / "point_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["baseline_hard_gate"] = {
        "status": gate["status"],
        "outer_allowed": passed,
        "path": str(gate_path),
        "sha256": points.sha256_file(gate_path),
    }
    points.atomic_json(state_path, state, exclusive=False)
    print(
        json.dumps(
            {
                "status": result["status"],
                "point_result": str(output / "point_result.json"),
                "baseline_hard_gate": gate["status"],
                "outer_allowed": passed,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
