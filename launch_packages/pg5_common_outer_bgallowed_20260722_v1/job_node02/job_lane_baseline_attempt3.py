#!/usr/bin/env python3
"""Run JOB R0 attempt3 and enforce the corrected lane hard gate."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence


CAMPAIGN_ID = "pg5_common_outer_bgallowed_20260722_v1"
LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
CONTAINER_NAME = "ra_pg5_job_node02_v1_a3"
PORT = 64303


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
    root.relative_to((LANE_ROOT / "work" / "runtime").resolve())
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
    tmp_dir = LANE_ROOT / "tmp" / "postgres_attempt3"
    socket_dir = LANE_ROOT / "socket" / "postgresql_attempt3"
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
    output = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_3"
    costs = points.default_cost_parameters()
    result = points.run_cost_point(
        "pg_job_memory",
        "R0",
        1,
        output,
        costs,
        evidence_context={
            "purpose": "shared_common_outer_baseline_R0_attempt3_corrected_bands",
            "attempt": 3,
            "attempt1_retroactive_acceptance": False,
            "attempt2_retroactive_acceptance": False,
            "normal_throughput_stability": (
                "per_30s_window_mean_then_cv_across_latest_six_window_means"
            ),
            "resource_stability": "per_30s_window_from_raw_one_second_samples",
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
        (LANE_ROOT / "bundle" / "attempt3_frozen" / "measurement_protocol.json")
        .read_text(encoding="utf-8")
    )
    bands = protocol["baseline_gates"]
    measurement = result["measurement"]
    utilization = measurement["resource_mean_utilization"]
    checks = {
        "point_complete": result.get("status") == "complete",
        "correctness": measurement.get("correctness_passed") is True,
        "error_rate": float(measurement.get("error_rate", 1.0)) <= float(bands["error_rate_max"]),
        "sample_coverage": float(measurement.get("sample_coverage", 0.0))
        >= float(protocol["timing"]["sample_coverage_min"]),
        "stability": measurement.get("resource_stability", {}).get("passed") is True,
        "memory_85_to_90": float(bands["target_resource_utilization_min"])
        <= float(utilization["memory"])
        <= float(bands["target_resource_utilization_max"]),
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
    }
    passed = all(checks.values())
    gate = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "outer_allowed": passed,
        "evaluated_at_utc": points.utc_now(),
        "freeze_id": result["freeze_id"],
        "point_id": "R0",
        "attempt": 3,
        "corrected_formal_bands": {
            "memory": [0.85, 0.90],
            "cpu": [0.30, 0.50],
            "io": [0.30, 0.50],
        },
        "observed_resource_mean_utilization": utilization,
        "checks": checks,
        "accepted_shrink_count": 0,
        "point_result_path": str(output / "point_result.json"),
        "point_result_sha256": points.sha256_file(output / "point_result.json"),
        "attempt1_retroactive_acceptance": False,
        "attempt2_retroactive_acceptance": False,
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
