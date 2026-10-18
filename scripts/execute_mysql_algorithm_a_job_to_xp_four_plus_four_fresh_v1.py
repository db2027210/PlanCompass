#!/usr/bin/env python3
"""Release completed memory slots and launch fresh XP/CPU+XP/I/O at 4+4."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_job_to_xp_slots_v1 as slots
from scripts import mysql_cross_project_wave_coordination_v2 as coordination
from scripts import provision_mysql_algorithm_a_job_to_xp_workers_v1 as provision
from scripts import reset_mysql_xp_data_planes_v5 as reset
from scripts import run_mysql_algorithm_c_async_scene_v49 as c49


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_four_plus_four_fresh_v1.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_xp_four_plus_four_20260823_v1"
    / "transition_contract.json"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v6"
    / "job_four_worker_resource_successor.json"
)
SUCCESSOR_FREEZE = CONTRACT.parent / "xp_four_worker_resource_successor.json"
TPCH_STATE = (
    HISTORY / "v54_to_a_storage_compatible_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
JOB_STATES = (
    HISTORY / "v55_to_a_storage_compatible_job4_20260823_attempt001_job"
    / "algorithm_a_state.json",
    HISTORY / "v54_to_a_storage_compatible_20260823_attempt001_job"
    / "algorithm_a_state.json",
)
TRANSITION_ROOT = HISTORY / "v57_to_a_xp_four_plus_four_20260823_transition_v1"
MANAGEMENT_CPUSET = "240-241,496-497"
PREPARED = {
    "hybench_xp_cpu": (
        HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001"
        / "prepared_binding.json"
    ),
    "hybench_xp_io": (
        HISTORY / "v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery"
        / "prepared_binding.json"
    ),
}
R0 = {
    "hybench_xp_cpu": (
        HISTORY / "v36_to_c_per_scene_20260819_attempt037_xp_cpu_r0_recovered"
        / "r0_binding.json"
    ),
    "hybench_xp_io": (
        HISTORY / "v36_to_c_per_scene_20260819_attempt044_xp_io_r0_recovered"
        / "r0_binding.json"
    ),
}
RUNS = {
    "hybench_xp_cpu": "v57_to_a_xp_four_plus_four_fresh_20260823_attempt001_xp_cpu",
    "hybench_xp_io": "v57_to_a_xp_four_plus_four_fresh_20260823_attempt001_xp_io",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_four_plus_four_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("memory_scenes_must_complete_before_launch") is not True
        or contract.get("xp_parallelism")
        != {"hybench_xp_cpu": 4, "hybench_xp_io": 4, "total": 8}
        or contract.get("xp_scenes_measure_concurrently") is not True
        or contract.get("fresh_xp_runs_required") is not True
    ):
        raise RuntimeError("XP 4+4 transition contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"XP 4+4 source binding differs: {path}")
    return contract


def terminal(path: Path, scene: str) -> tuple[dict[str, Any], dict[str, Any]]:
    state = load(path)
    if not (
        state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
        and state.get("round_checkpoint") is None
    ):
        raise RuntimeError(f"memory donor is not complete: {scene}")
    return state, {
        "scene": scene, "complete": True, "accepted": 10,
        "run_id": state.get("run_id"), "state_path": str(path),
        "state_sha256": sha(path),
    }


def current_job() -> tuple[Path, dict[str, Any], dict[str, Any]]:
    for path in JOB_STATES:
        if path.is_file() and not path.is_symlink():
            try:
                state, evidence = terminal(path, "job_memory")
                return path, state, evidence
            except RuntimeError:
                continue
    raise RuntimeError("completed JOB Algorithm A state is absent")


def stop_job_donors(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    results = []
    donors = set(slots.DONOR_TO_RECIPIENT)
    rows = [row for row in source.get("workers") or [] if row.get("worker_id") in donors]
    if {row.get("worker_id") for row in rows} != donors:
        raise RuntimeError("JOB donor worker set differs")
    for row in rows:
        container = str(row["container"])
        before = reset._docker_json("inspect", container)
        if (before.get("State") or {}).get("Running") is True:
            reset._docker_run(["docker", "stop", "--time", "60", container], timeout=120)
        after = reset._docker_json("inspect", container)
        if after.get("Id") != before.get("Id") or (after.get("State") or {}).get("Running") is True:
            raise RuntimeError(f"JOB donor did not stop: {container}")
        results.append({
            "worker_id": row["worker_id"], "container": container,
            "container_id": before["Id"], "stopped": True,
            "datadir_retained": True, "recoverable": True,
        })
    return results


def original_live_workers(
    scene: str, successor: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    expected = {
        f"xp_cpu_w{index}" if scene == "hybench_xp_cpu" else f"xp_io_w{index}"
        for index in (1, 2)
    }
    prepared = load(PREPARED[scene])
    live = {
        str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or [] if row.get("worker_id") in expected
    }
    frozen = {
        str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
        for row in successor.get("workers") or [] if row.get("worker_id") in expected
    }
    if set(live) != expected or set(frozen) != expected:
        raise RuntimeError(f"original XP authority differs: {scene}")
    result: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(expected):
        row = {**frozen[worker_id], **{
            name: live[worker_id].get(name) for name in (
                "datadir", "credential_defaults", "root_defaults_rotated",
                "benchmark_defaults", "benchmark_user", "container_id",
                "container_pid", "database", "external_current_user",
                "external_database", "kernel_cgroup_resources", "blkio_authority",
            )
        }}
        inspect = reset._docker_json("inspect", str(row["container"]))
        state = inspect.get("State") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspect.get("Mounts") or []
        }
        defaults = Path(str(row.get("credential_defaults") or ""))
        benchmark = Path(str(row.get("benchmark_defaults") or ""))
        if (
            state.get("Running") is not True
            or inspect.get("Id") != row.get("container_id")
            or mounts.get("/var/lib/mysql") != row.get("datadir")
            or not defaults.is_file() or defaults.is_symlink()
            or not benchmark.is_file() or benchmark.is_symlink()
        ):
            raise RuntimeError(f"original XP worker is not live: {worker_id}")
        sessions = reset._host_mysql(
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'", timeout=30,
        ).stdout.strip()
        identity = reset._host_mysql(
            row, benchmark, "SELECT CURRENT_USER(),DATABASE();", timeout=30,
        ).stdout.strip().split("\t")
        if sessions != "0" or not identity or not identity[0].startswith(f"{reset.BENCHMARK_USER}@"):
            raise RuntimeError(f"original XP worker sessions/account differ: {worker_id}")
        row.update({
            "ready": True, "container_id": inspect["Id"],
            "container_pid": int(state.get("Pid") or 0),
            "external_current_user": identity[0], "external_database": "hybench_sf10x",
            "database": "hybench_sf10x",
            "kernel_cgroup_resources": reset.prep._kernel_resource_readback(inspect, row),
            "blkio_authority": reset.prep._enforce_blkio(inspect, successor, row),
            "passwords_emitted": False,
        })
        result[worker_id] = row
    return result


def gate(scene: str, workers: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    target_cpus = {
        cpu for row in workers.values()
        for cpu in slots.parse_cpuset(row["database_cpuset"])
        | slots.parse_cpuset(row["client_cpuset"])
    }
    value = coordination.rolling_quiet_gate(
        run_id=f"v57_to_a_xp_four_plus_four_20260823_{scene}",
        required_seconds=60.0, maximum_wait_seconds=300.0,
        target_cpus=target_cpus,
    )
    value["target_worker_ids"] = sorted(workers)
    value["concurrent_xp_companion_planned"] = True
    value["shared_device"] = "/dev/nvme2n1"
    return value


def launch(scene: str, authority: Path) -> dict[str, Any]:
    run_id = RUNS[scene]
    unit = f"mysql-alga-xp-four-plus-four-{run_id}.service"
    output = HISTORY / run_id
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink() or stdout.exists() or stderr.exists():
        raise RuntimeError(f"fresh XP 4+4 launch artifacts required: {run_id}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A fresh XP 4+4 {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", run_id,
        "--scene", scene, "--launch-authority", str(authority),
        "--output-dir", str(output),
    ]
    started = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if started.returncode != 0:
        raise RuntimeError(f"XP 4+4 launch failed: {scene}: {started.stderr[-2000:]}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        ).stdout
        if "ActiveState=failed" in shown or "ActiveState=inactive" in shown:
            raise RuntimeError(f"XP 4+4 controller exited during startup: {scene}: {shown}")
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("status") == "RUNNING"
                and state.get("async_scene") == scene
                and int(state.get("physical_replay_parallelism") or 0) == 4
                and int(state.get("accepted") or 0) == 0
            ):
                return {
                    "scene": scene, "run_id": run_id, "unit": unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "stage": state.get("stage"), "physical_replay_parallelism": 4,
                }
        time.sleep(1)
    raise TimeoutError(f"XP 4+4 startup timed out: {scene}")


def run(result: Path) -> int:
    validate_contract()
    if result.is_file():
        previous = load(result)
        if previous.get("status") == "LAUNCHED":
            print(json.dumps(previous, ensure_ascii=False, sort_keys=True))
            return 0
        raise RuntimeError("existing incomplete XP 4+4 transition requires audit")
    _, _, job_evidence = current_job()
    _, tpch_evidence = terminal(TPCH_STATE, "tpch_sf4_memory")
    memory = {"job_memory": job_evidence, "tpch_sf4_memory": tpch_evidence}
    source = load(SOURCE_FREEZE)
    if (
        source.get("schema_version") != "mysql_algorithm_a_job_four_worker_resource_successor_v1"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("completed JOB four-worker resource freeze differs")
    document: dict[str, Any] = {
        "schema_version": "mysql_algorithm_a_xp_four_plus_four_transition_v1",
        "status": "TRANSITIONING", "stage": "MEMORY_TERMINALS_VALIDATED",
        "created_at": now(), "updated_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "memory_terminal_evidence": memory,
    }
    atomic(result, document)
    released = stop_job_donors(source)
    document.update({"stage": "ALL_EIGHT_XP_SLOTS_RELEASED", "released_job_workers": released, "updated_at": now()})
    atomic(result, document)
    additions = slots.build_xp_workers(source)
    successor = slots.build_successor_freeze(
        source, additions, created_at=now(), job_evidence=memory,
    )
    if SUCCESSOR_FREEZE.exists() or SUCCESSOR_FREEZE.is_symlink():
        raise RuntimeError("fresh XP 4+4 successor freeze required")
    atomic(SUCCESSOR_FREEZE, successor)
    provision_root = TRANSITION_ROOT / "provisioned_workers"
    new_live: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="alga-xp-4p4-provision") as pool:
        futures = {
            pool.submit(provision.provision_worker, successor, row, provision_root): str(row["worker_id"])
            for row in additions
        }
        for future in as_completed(futures):
            new_live[futures[future]] = future.result()
    workers_by_scene: dict[str, dict[str, dict[str, Any]]] = {}
    authorities: dict[str, Path] = {}
    for scene in slots.XP_SCENES:
        workers = original_live_workers(scene, successor)
        workers.update({
            worker_id: row for worker_id, row in new_live.items()
            if row.get("scene") == scene
        })
        if len(workers) != 4:
            raise RuntimeError(f"XP 4+4 scene worker cardinality differs: {scene}")
        workers_by_scene[scene] = workers
        gate_value = gate(scene, workers)
        gate_path = TRANSITION_ROOT / f"{scene}.dynamic_noninterference_gate.json"
        atomic(gate_path, gate_value)
        if gate_value.get("status") != "PASS":
            raise RuntimeError(f"XP 4+4 non-interference gate failed: {scene}")
        companion = ({"hybench_xp_cpu", "hybench_xp_io"} - {scene}).pop()
        authority_value = {
            "schema_version": "mysql_algorithm_a_xp_four_plus_four_launch_authority_v1",
            "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
            "scene": scene, "successor_freeze_path": str(SUCCESSOR_FREEZE),
            "successor_freeze_sha256": sha(SUCCESSOR_FREEZE),
            "memory_terminal_evidence": memory,
            "dynamic_noninterference_gate": gate_value,
            "workers": [workers[key] for key in sorted(workers)],
            "r0_path": str(R0[scene]), "r0_sha256": sha(R0[scene]),
            "fresh_baseline_all_four_workers": True,
            "historical_accepted_rounds_reused": False,
            "native_xp_workload_unchanged": True,
            "concurrent_companion_scene": companion,
            "xp_measurement_concurrency": 4,
            "total_concurrent_xp_measurements": 8,
        }
        authority_path = TRANSITION_ROOT / f"{scene}.launch_authority.json"
        atomic(authority_path, authority_value)
        authorities[scene] = authority_path
    launches: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="alga-xp-4p4-launch") as pool:
        futures = {pool.submit(launch, scene, authorities[scene]): scene for scene in slots.XP_SCENES}
        for future in as_completed(futures):
            launches[futures[future]] = future.result()
    document.update({
        "status": "LAUNCHED", "stage": "XP_CPU_AND_IO_FRESH_4_PLUS_4_LAUNCHED",
        "updated_at": now(), "successor_freeze_path": str(SUCCESSOR_FREEZE),
        "successor_freeze_sha256": sha(SUCCESSOR_FREEZE),
        "launch_authorities": {
            scene: {"path": str(path), "sha256": sha(path)}
            for scene, path in authorities.items()
        },
        "launches": launches,
        "xp_parallelism": {"hybench_xp_cpu": 4, "hybench_xp_io": 4, "total": 8},
        "xp_scenes_measure_concurrently": True,
    })
    atomic(result, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result = args.result.expanduser().resolve()
    try:
        c49.validate_v65_wave_coordination_contract()
        c49.install()
        coordination.predecessor.CONTRACT_PATH = c49.COORDINATION_CONTRACT
        return run(result)
    except BaseException as error:
        previous = load(result) if result.is_file() else {}
        previous.update({
            "status": "FAILED_CLOSED", "stage": "FAILED_CLOSED",
            "updated_at": now(), "error": f"{type(error).__name__}: {error}",
        })
        atomic(result, previous)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
