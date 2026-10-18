#!/usr/bin/env python3
"""Expand Algorithm A JOB from two to four workers at the current round boundary."""

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

from scripts import mysql_algorithm_a_job_dynamic_slots_v1 as slots
from scripts import mysql_cross_project_wave_coordination_v2 as coordination
from scripts import provision_mysql_algorithm_a_job_slot_workers_v1 as provision
from scripts import reset_mysql_xp_data_planes_v5 as reset


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_dynamic_resume_v1.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
TRANSITION_ROOT = HISTORY / "v39_to_a_policy10_20260821_job_dynamic_slots_v1"
FREEZE_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260821_v1"
    / "job_four_worker_resource_successor.json"
)
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260821_v1"
    / "transition_contract.json"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
SOURCE_FREEZE_SHA256 = "a19dff2a72963f147ac914345e4b188522d9dca23dd35b5aa85cba6f1f4801df"
PREPARED_BINDING = (
    HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001"
    / "prepared_binding.json"
)
JOB_STATE = (
    HISTORY / "v39_to_a_policy10_20260821_attempt009_job"
    / "algorithm_a_state.json"
)
JOB_UNIT = "mysql-alga-policy10-attempt009-job.service"
TPCH_STATE = (
    HISTORY / "v39_to_a_policy10_20260821_attempt011_tpch"
    / "algorithm_a_state.json"
)
ORIGINAL_MONITOR_UNIT = "mysql-alga-policy10-dynamic-slots-v5.service"
MANAGEMENT_CPUSET = "240-241,496-497"


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


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], cwd=ROOT, capture_output=True,
        text=True, check=check, timeout=120,
    )


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT_PATH)
    if (
        contract.get("schema_version") != "mysql_algorithm_a_job_dynamic_slot_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("contract") != slots.TRANSITION_CONTRACT
        or contract.get("job_parallelism") != {"before": 2, "after": 4}
        or contract.get("round_boundary_required") is not True
        or contract.get("mixed_worker_cohort_round_prohibited") is not True
        or contract.get("candidate_count_per_round") != 16
        or contract.get("full_job_sql_count") != 113
        or contract.get("tpch_must_be_complete") is not True
        or contract.get("resource_recalibration_required") is not False
        or contract.get("fresh_baseline_repetitions_per_worker") != 2
        or contract.get("incumbent_replay_required") is not True
    ):
        raise RuntimeError("frozen JOB slot transition contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"JOB slot source binding differs: {path}")
    return contract


def tpch_completion() -> dict[str, Any]:
    state = load(TPCH_STATE)
    if not (
        state.get("algorithm") == "A"
        and state.get("async_scene") == "tpch_sf4_memory"
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
    ):
        raise RuntimeError("TPC-H is not a completed Algorithm A donor")
    return {
        "complete": True,
        "accepted": 10,
        "state_path": str(TPCH_STATE),
        "state_sha256": sha(TPCH_STATE),
    }


def wait_and_stop_job(output: Path, timeout_seconds: float) -> Path:
    initial = load(JOB_STATE)
    if initial.get("status") != "RUNNING" or initial.get("async_scene") != slots.SCENE:
        raise RuntimeError("JOB predecessor is not running")
    initial_round = int(initial.get("round_index") or 0)
    initial_accepted = int(initial.get("accepted") or 0)
    initial_rejected = int(initial.get("rejected_rounds") or 0)
    if initial.get("round_checkpoint") is None:
        raise RuntimeError("JOB expansion expected an active complete-factorial round")
    deadline = time.monotonic() + timeout_seconds
    boundary: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        state = load(JOB_STATE)
        if (
            int(state.get("accepted") or 0) > initial_accepted
            or int(state.get("rejected_rounds") or 0) > initial_rejected
            or int(state.get("round_index") or 0) > initial_round
            or state.get("status") in {"COMPLETE", "NONCONVERGENCE_CLEAN"}
        ):
            boundary = state
            break
        if state.get("status") != "RUNNING":
            raise RuntimeError(f"JOB failed before boundary: {state.get('status')} {state.get('stage')}")
        time.sleep(5)
    if boundary is None:
        raise TimeoutError("JOB round-boundary wait timed out")
    systemctl("stop", JOB_UNIT, check=False)
    fields = systemctl(
        "show", JOB_UNIT, "-p", "ActiveState", "-p", "MainPID", check=False,
    ).stdout
    if "MainPID=0" not in fields or "ActiveState=active" in fields:
        raise RuntimeError(f"JOB controller did not stop: {fields}")
    stopped = load(JOB_STATE)
    if (
        int(stopped.get("accepted") or 0) < initial_accepted
        or int(stopped.get("rejected_rounds") or 0) < initial_rejected
    ):
        raise RuntimeError("JOB state regressed during boundary stop")
    partial = copy.deepcopy(stopped.get("round_checkpoint"))
    snapshot = copy.deepcopy(stopped)
    snapshot.update({
        "status": "TRANSITION_BOUNDARY_SNAPSHOT",
        "stage": "ROUND_BOUNDARY_SEALED_FOR_JOB_2_TO_4",
        "transition_boundary_snapshot": True,
        "transition_boundary_snapshot_at": now(),
        "transition_boundary_completed_round": initial_round,
        "predecessor_state_path": str(JOB_STATE),
        "predecessor_state_sha256_after_stop": sha(JOB_STATE),
        "partial_next_round_archived": partial is not None,
        "partial_next_round_credited": False,
        "archived_partial_round_checkpoint": partial,
        "round_index": initial_round,
        "round_checkpoint": None,
    })
    snapshot_path = output / "job_memory.predecessor_boundary_snapshot.json"
    atomic(snapshot_path, snapshot)
    return snapshot_path


def stop_tpch_containers(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    results = []
    for row in source.get("workers") or []:
        if row.get("worker_id") not in slots.DONOR_TO_RECIPIENT:
            continue
        container = str(row["container"])
        before = reset._docker_json("inspect", container)
        if (before.get("State") or {}).get("Running") is True:
            reset._docker_run(["docker", "stop", "--time", "60", container], timeout=120)
        after = reset._docker_json("inspect", container)
        if (after.get("State") or {}).get("Running") is True or after.get("Id") != before.get("Id"):
            raise RuntimeError(f"TPC-H donor did not stop: {container}")
        results.append({
            "worker_id": row["worker_id"], "container": container,
            "container_id": before["Id"], "stopped": True,
            "datadir_retained": True, "recoverable": True,
        })
    if len(results) != 2:
        raise RuntimeError("TPC-H donor cardinality differs")
    return results


def original_job_authority(
    source: Mapping[str, Any], successor: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    prepared = load(PREPARED_BINDING)
    prepared_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or [] if row.get("worker_id") in {"job_w1", "job_w2"}
    }
    frozen_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in successor.get("workers") or [] if row.get("worker_id") in {"job_w1", "job_w2"}
    }
    if set(prepared_rows) != set(frozen_rows) or len(frozen_rows) != 2:
        raise RuntimeError("original JOB authority cardinality differs")
    result: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(frozen_rows):
        row = frozen_rows[worker_id]
        adopted = prepared_rows[worker_id]
        inspect = reset._docker_json("inspect", str(row["container"]))
        state = inspect.get("State") or {}
        defaults = Path(str(adopted["credential_defaults"]))
        benchmark_defaults = Path(str(adopted["benchmark_defaults"]))
        if state.get("Running") is not True or not defaults.is_file() or not benchmark_defaults.is_file():
            raise RuntimeError(f"original JOB worker is not live: {worker_id}")
        sessions = reset._host_mysql(
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' AND USER<>'event_scheduler'",
            timeout=30,
        ).stdout.strip()
        readback = reset._host_mysql(
            row, defaults,
            "SELECT @@global.innodb_buffer_pool_size; SELECT COUNT(*) "
            "FROM information_schema.tables WHERE table_schema='imdb_job';",
            timeout=30,
        ).stdout.splitlines()
        identity = reset._host_mysql(
            row, benchmark_defaults, "SELECT CURRENT_USER(),DATABASE();", timeout=30,
        ).stdout.strip().split("\t")
        if sessions != "0" or len(readback) != 2 or int(readback[0]) != int(row["innodb_buffer_pool_bytes"]) or int(readback[1]) <= 0:
            raise RuntimeError(f"original JOB worker readback differs: {worker_id}")
        if not identity or not identity[0].startswith(f"{reset.BENCHMARK_USER}@"):
            raise RuntimeError(f"original JOB benchmark account differs: {worker_id}")
        kernel = reset.prep._kernel_resource_readback(inspect, row)
        blkio = reset.prep._enforce_blkio(inspect, source, row)
        result[worker_id] = {
            **row, **{key: adopted.get(key) for key in (
                "datadir", "credential_defaults", "root_defaults_rotated",
                "benchmark_defaults", "benchmark_user",
            )},
            "ready": True, "container_id": inspect["Id"],
            "container_pid": int(state.get("Pid") or 0),
            "external_current_user": identity[0], "external_database": "imdb_job",
            "database": "imdb_job", "kernel_cgroup_resources": kernel,
            "blkio_authority": blkio, "passwords_emitted": False,
        }
    return result


def launch(snapshot: Path, authority: Path) -> dict[str, Any]:
    run_id = "v39_to_a_policy10_job_dynamic_slots_20260821_attempt001_job"
    unit = f"mysql-alga-dynamic-{run_id}.service"
    output = HISTORY / run_id
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL Algorithm A four-worker JOB resume",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", run_id,
        "--scene", slots.SCENE, "--predecessor-state", str(snapshot),
        "--transition-authority", str(authority), "--output-dir", str(output),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(f"four-worker JOB launch failed: {completed.stderr}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        fields = systemctl("show", unit, "-p", "ActiveState", "-p", "MainPID", check=False).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"JOB successor exited during startup: {fields}")
        if state_path.is_file():
            state = load(state_path)
            if state.get("status") == "RUNNING" and int(state.get("physical_replay_parallelism") or 0) == 4:
                return {
                    "run_id": run_id, "unit": unit, "output": str(output),
                    "state": str(state_path), "stdout": str(stdout),
                    "stderr": str(stderr), "stage": state.get("stage"),
                }
        time.sleep(1)
    raise TimeoutError("four-worker JOB successor startup timed out")


def run(args: argparse.Namespace) -> int:
    result_path = args.result.resolve()
    if result_path.is_file():
        prior = load(result_path)
        if prior.get("status") == "LAUNCHED":
            print(json.dumps(prior, ensure_ascii=False, sort_keys=True))
            return 0
    contract = validate_contract()
    tpch = tpch_completion()
    source = load(SOURCE_FREEZE)
    if sha(SOURCE_FREEZE) != SOURCE_FREEZE_SHA256:
        raise RuntimeError("source resource freeze differs")
    document: dict[str, Any] = {
        "schema_version": "mysql_algorithm_a_job_dynamic_slot_handoff_v1",
        "status": "WAITING_ROUND_BOUNDARY", "stage": "TRIGGER_VALIDATED",
        "created_at": now(), "updated_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": slots.TRANSITION_CONTRACT,
        "transition_contract_path": str(CONTRACT_PATH),
        "transition_contract_sha256": sha(CONTRACT_PATH),
        "tpch_terminal_evidence": tpch,
    }
    atomic(result_path, document)
    TRANSITION_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot = wait_and_stop_job(TRANSITION_ROOT, args.timeout_seconds)
    document.update({"status": "TRANSITIONING", "stage": "JOB_PREDECESSOR_STOPPED_AT_BOUNDARY", "snapshot": str(snapshot), "updated_at": now()})
    atomic(result_path, document)
    systemctl("stop", ORIGINAL_MONITOR_UNIT, check=False)
    released = stop_tpch_containers(source)
    additions = slots.build_job_workers(source)
    successor = slots.build_successor_freeze(
        source, additions, created_at=now(),
        tpch_evidence={"tpch_sf4_memory": tpch},
    )
    atomic(FREEZE_PATH, successor)
    document.update({"stage": "SUCCESSOR_FREEZE_SEALED", "released_tpch_workers": released, "successor_freeze_path": str(FREEZE_PATH), "successor_freeze_sha256": sha(FREEZE_PATH), "updated_at": now()})
    atomic(result_path, document)
    provision_root = TRANSITION_ROOT / "provisioned_workers"
    new_live: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="alga-job-slot") as pool:
        futures = {pool.submit(provision.provision_worker, successor, row, provision_root): str(row["worker_id"]) for row in additions}
        for future in as_completed(futures):
            new_live[futures[future]] = future.result()
    original_live = original_job_authority(source, successor)
    all_live = {**original_live, **new_live}
    document.update({"stage": "FOUR_JOB_WORKERS_PROVISIONED", "worker_ids": sorted(all_live), "updated_at": now()})
    atomic(result_path, document)
    target_cpus = {
        cpu for row in all_live.values()
        for cpu in slots.parse_cpuset(row["database_cpuset"]) | slots.parse_cpuset(row["client_cpuset"])
    }
    gate = coordination.rolling_quiet_gate(
        run_id="v39_to_a_policy10_20260821_job_dynamic_slots_v1",
        required_seconds=60.0, maximum_wait_seconds=300.0, target_cpus=target_cpus,
    )
    gate["target_worker_ids"] = sorted(all_live)
    gate_path = TRANSITION_ROOT / "dynamic_noninterference_gate.json"
    atomic(gate_path, gate)
    if gate.get("status") != "PASS":
        raise RuntimeError("four-worker JOB non-interference gate failed")
    predecessor = load(snapshot)
    authority = {
        "schema_version": "mysql_algorithm_a_job_four_worker_transition_authority_v1",
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": slots.TRANSITION_CONTRACT,
        "transition_contract_path": str(CONTRACT_PATH),
        "transition_contract_sha256": sha(CONTRACT_PATH),
        "scene": slots.SCENE, "predecessor_snapshot_path": str(snapshot),
        "predecessor_snapshot_sha256": sha(snapshot),
        "preserved_search_state_sha256": slots.canonical_sha256(slots.search_state_payload(predecessor)),
        "successor_freeze_path": str(FREEZE_PATH),
        "successor_freeze_sha256": sha(FREEZE_PATH),
        "memory_terminal_evidence": {"tpch_sf4_memory": tpch},
        "dynamic_noninterference_gate": gate,
        "workers": [all_live[key] for key in sorted(all_live)],
        "full_job_113_once_per_measurement": True,
        "candidate_count_per_round_unchanged": 16,
        "mixed_worker_cohort_round_prohibited": True,
        "fresh_baseline_repetitions_per_worker": 2,
        "incumbent_replay_required": True,
        "resource_recalibration_performed": False,
        "canonical_baseline_preserved": True,
        "accepted_ranges_incumbent_history_preserved": True,
    }
    authority_path = TRANSITION_ROOT / "job_memory.transition_authority.json"
    atomic(authority_path, authority)
    launched = launch(snapshot, authority_path)
    document.update({
        "status": "LAUNCHED", "stage": "FOUR_WORKER_JOB_RESUME_LAUNCHED",
        "updated_at": now(), "launch": launched,
        "authority_path": str(authority_path), "authority_sha256": sha(authority_path),
        "dynamic_noninterference_gate_path": str(gate_path),
        "dynamic_noninterference_gate_sha256": sha(gate_path),
        "job_parallelism_before": 2, "job_parallelism_after": 4,
    })
    atomic(result_path, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=86400.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
