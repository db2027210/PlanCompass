#!/usr/bin/env python3
"""Run only the shared JOB baseline/R0 in the isolated node02 lane."""

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
CONTAINER_NAME = "ra_pg5_job_node02_v1"
PORT = 64302


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
    tmp_dir = LANE_ROOT / "tmp" / "postgres"
    socket_dir = LANE_ROOT / "socket" / "postgresql"
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
    expected_rate = 1205862400
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
    _, points, validation = p24.configure_modules("job")
    import formal_pg_engine as engine
    engine.run = lane_run
    points.run = lane_run
    points.host_measurement_lock = lane_lock
    points.start_container = lambda kind, pgdata, profile_name="none", cost_parameters=None: start_lane_container(
        engine, kind, pgdata, profile_name, cost_parameters
    )
    points.cleanup_overlay = lambda kind, restore: cleanup_runtime(engine, kind, restore)
    output = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_1"
    costs = points.default_cost_parameters()
    result = points.run_cost_point(
        "pg_job_memory",
        "R0",
        1,
        output,
        costs,
        evidence_context={
            "purpose": "shared_common_outer_baseline_R0",
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
    print(json.dumps({"status": result["status"], "point_result": str(output / "point_result.json")}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
