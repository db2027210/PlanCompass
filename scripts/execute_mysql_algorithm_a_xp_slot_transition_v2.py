#!/usr/bin/env python3
"""Execute the Algorithm A memory-slot to XP 2-to-4 worker handoff."""

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

from scripts import mysql_algorithm_a_dynamic_slots_v2 as slots
from scripts import monitor_mysql_algorithm_a_dynamic_slots_v2 as monitor
from scripts import mysql_cross_project_wave_coordination_v2 as coordination
from scripts import provision_mysql_algorithm_a_xp_slot_workers_v2 as provision
from scripts import reset_mysql_xp_data_planes_v5 as reset


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_dynamic_resume_v3.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
FREEZE_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_dynamic_slots_20260822_v5"
    / "xp_four_worker_resource_successor.json"
)
TRANSITION_CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_dynamic_slots_20260822_v5"
    / "transition_contract.json"
)
A_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_async_20260822_v3"
    / "algorithm_a_contract.json"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
SOURCE_FREEZE_SHA256 = (
    "a19dff2a72963f147ac914345e4b188522d9dca23dd35b5aa85cba6f1f4801df"
)
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


def validate_transition_contract() -> dict[str, Any]:
    contract = load(TRANSITION_CONTRACT_PATH)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_dynamic_slot_contract_v4"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("contract")
        != slots.TRANSITION_CONTRACT
        or contract.get("initial_parallelism_per_scene") != 2
        or contract.get("xp_parallelism_after_transition") != {
            "hybench_xp_cpu": 4, "hybench_xp_io": 4,
        }
        or contract.get("memory_trigger")
        != {
            "job_memory": ["COMPLETE"],
            "tpch_sf4_memory": ["COMPLETE"],
        }
        or contract.get("round_boundary_required") is not True
        or contract.get("mixed_worker_cohort_round_prohibited") is not True
        or contract.get("fresh_baseline_repetitions_per_worker") != 2
        or contract.get("incumbent_replay_required") is not True
        or contract.get("native_xp_workload_unchanged") is not True
    ):
        raise RuntimeError("frozen dynamic-slot transition contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if (
            path.is_symlink() or not path.is_file()
            or sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"dynamic-slot source binding differs: {path}")
    return contract


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


def checkpoint(path: Path, document: dict[str, Any], stage: str, **fields: Any) -> None:
    document.update({"status": "TRANSITIONING", "stage": stage, "updated_at": now()})
    document.update(copy.deepcopy(fields))
    atomic(path, document)


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], cwd=ROOT,
        capture_output=True, text=True, check=check, timeout=120,
    )


def wait_for_round_boundary(
    state_path: Path, unit: str, scene: str, output: Path,
    *, timeout_seconds: float = 172800.0,
) -> Path | None:
    """Wait for the current formal round to finish, stop, and seal a snapshot."""
    initial = load(state_path)
    if monitor.strict_complete(initial, scene):
        return None
    initial_accepted = int(initial.get("accepted") or 0)
    initial_round = int(initial.get("round_index") or 0)
    initial_checkpoint = initial.get("round_checkpoint")
    # A state between rounds can be stopped immediately.  Otherwise the
    # current round must finish; a partial next round may start before the
    # stop reaches the process and is explicitly archived without credit.
    already_at_boundary = initial_checkpoint is None and (
        str(initial.get("stage") or "").endswith("_ACCEPTED")
        or initial.get("status") in {"NONCONVERGENCE_CLEAN", "COMPLETE"}
    )
    deadline = time.monotonic() + timeout_seconds
    while not already_at_boundary:
        state = load(state_path)
        if (
            int(state.get("accepted") or 0) > initial_accepted
            or monitor.strict_complete(state, scene)
            or state.get("status") == "NONCONVERGENCE_CLEAN"
        ):
            break
        if state.get("status") not in {"RUNNING"}:
            raise RuntimeError(
                f"{scene} cannot reach a transition boundary: "
                f"{state.get('status')} {state.get('stage')}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{scene} round-boundary wait timed out")
        time.sleep(5.0)
    systemctl("stop", unit, check=False)
    fields = systemctl(
        "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
        check=False,
    ).stdout
    if "MainPID=0" not in fields or "ActiveState=active" in fields:
        raise RuntimeError(f"{scene} predecessor controller did not stop: {fields}")
    stopped = load(state_path)
    if int(stopped.get("accepted") or 0) < initial_accepted:
        raise RuntimeError(f"{scene} accepted state regressed during stop")
    partial = copy.deepcopy(stopped.get("round_checkpoint"))
    snapshot = copy.deepcopy(stopped)
    snapshot.update({
        "status": "TRANSITION_BOUNDARY_SNAPSHOT",
        "transition_boundary_snapshot": True,
        "transition_boundary_snapshot_at": now(),
        "transition_boundary_initial_accepted": initial_accepted,
        "transition_boundary_initial_round": initial_round,
        "predecessor_state_path": str(state_path),
        "predecessor_state_sha256_after_stop": sha(state_path),
        "partial_next_round_archived": partial is not None,
        "partial_next_round_credited": False,
        "archived_partial_round_checkpoint": partial,
        "round_checkpoint": None,
    })
    snapshot_path = output / f"{scene}.predecessor_boundary_snapshot.json"
    atomic(snapshot_path, snapshot)
    return snapshot_path


def latest_live_worker_rows(
    predecessor_output: Path, scene: str,
    source_freeze: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    expected = {
        f"xp_cpu_w{index}" if scene == "hybench_xp_cpu" else f"xp_io_w{index}"
        for index in (1, 2)
    }
    candidates = sorted(
        predecessor_output.rglob("reset_binding.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        value = load(path)
        workers = {
            str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
            for row in value.get("workers") or []
            if row.get("worker_id") in expected
        }
        if set(workers) != expected:
            continue
        live = True
        for row in workers.values():
            inspected = subprocess.run(
                ["docker", "inspect", str(row["container"])],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if inspected.returncode != 0:
                live = False
                break
            parsed = json.loads(inspected.stdout)[0]
            if (
                parsed.get("Id") != row.get("container_id")
                or (parsed.get("State") or {}).get("Running") is not True
            ):
                live = False
                break
        if live:
            return workers
    if source_freeze is None:
        raise RuntimeError(f"no current live reset authority found for {scene}")
    # The predecessor may begin resetting the first wave of its next round in
    # the few seconds between persisting an accepted boundary and receiving
    # the stop.  That fresh container is authoritative even though its reset
    # binding may not yet have been published.  Reconstruct only from the
    # frozen identity, exact live Docker readback, private credential files,
    # and zero-session proof.
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source_freeze.get("workers") or []
        if row.get("worker_id") in expected
    }
    if set(frozen) != expected:
        raise RuntimeError(f"source XP worker identity differs for {scene}")
    reconstructed: dict[str, dict[str, Any]] = {}
    runtime_root = Path(str(source_freeze["storage"]["runtime_root"]))
    for worker_id in sorted(expected):
        row = frozen[worker_id]
        inspect = reset._docker_json("inspect", str(row["container"]))
        state = inspect.get("State") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspect.get("Mounts") or []
        }
        credentials = runtime_root / worker_id / "credentials"
        defaults = credentials / "selected.cnf"
        benchmark_defaults = credentials / "benchmark.cnf"
        datadir = runtime_root / worker_id / "datadir"
        if (
            state.get("Running") is not True
            or int(state.get("Pid") or 0) <= 1
            or mounts.get("/var/lib/mysql") != str(datadir)
            or not defaults.is_file()
            or defaults.is_symlink()
            or not benchmark_defaults.is_file()
            or benchmark_defaults.is_symlink()
        ):
            raise RuntimeError(f"live fallback authority differs: {worker_id}")
        active = reset._host_mysql(
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'",
            timeout=30,
        ).stdout.strip()
        if active != "0":
            raise RuntimeError(f"benchmark sessions remain after stop: {worker_id}")
        benchmark_identity = reset._host_mysql(
            row, benchmark_defaults, "SELECT CURRENT_USER();", timeout=30,
        ).stdout.strip()
        if not benchmark_identity.startswith(f"{reset.BENCHMARK_USER}@"):
            raise RuntimeError(f"benchmark credential fallback differs: {worker_id}")
        kernel = reset.prep._kernel_resource_readback(inspect, row)
        blkio = reset.prep._enforce_blkio(inspect, source_freeze, row)
        reconstructed[worker_id] = {
            **row,
            "ready": True,
            "container_id": inspect["Id"],
            "container_pid": int(state["Pid"]),
            "datadir": str(datadir),
            "credential_defaults": str(defaults),
            "root_defaults_rotated": str(defaults),
            "benchmark_defaults": str(benchmark_defaults),
            "benchmark_user": reset.BENCHMARK_USER,
            "external_current_user": benchmark_identity,
            "external_database": "hybench_sf10x",
            "database": "hybench_sf10x",
            "kernel_cgroup_resources": kernel,
            "blkio_authority": blkio,
            "passwords_emitted": False,
            "live_authority_reconstructed_after_boundary_stop": True,
        }
    return reconstructed


def stop_memory_containers(
    source_freeze: Mapping[str, Any], memory_evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if set(memory_evidence) != set(slots.MEMORY_SCENES) or not all(
        row.get("complete") is True for row in memory_evidence.values()
    ):
        raise RuntimeError("memory containers cannot stop before both scenes complete")
    rows = [
        row for row in source_freeze.get("workers") or []
        if row.get("scene") in slots.MEMORY_SCENES
    ]
    if len(rows) != 4:
        raise RuntimeError("source memory worker set differs")
    results = []
    for row in rows:
        container = str(row["container"])
        before = json.loads(subprocess.run(
            ["docker", "inspect", container], capture_output=True,
            text=True, check=True, timeout=30,
        ).stdout)[0]
        if (before.get("State") or {}).get("Running") is True:
            subprocess.run(
                ["docker", "stop", "--time", "60", container],
                capture_output=True, text=True, check=True, timeout=120,
            )
        after = json.loads(subprocess.run(
            ["docker", "inspect", container], capture_output=True,
            text=True, check=True, timeout=30,
        ).stdout)[0]
        if after.get("Id") != before.get("Id") or (
            after.get("State") or {}
        ).get("Running") is True:
            raise RuntimeError(f"released memory container did not stop: {container}")
        results.append({
            "worker_id": row["worker_id"], "container": container,
            "container_id": before["Id"], "stopped": True,
            "datadir_retained": True, "recoverable": True,
        })
    return results


def load_or_provision(
    freeze: Mapping[str, Any], worker: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    evidence = output / f"{worker['worker_id']}.provisioned.json"
    if not evidence.is_file():
        return provision.provision_worker(freeze, worker, output)
    row = load(evidence)
    inspected = json.loads(subprocess.run(
        ["docker", "inspect", str(row["container"])], capture_output=True,
        text=True, check=True, timeout=30,
    ).stdout)[0]
    if (
        row.get("worker_id") != worker.get("worker_id")
        or inspected.get("Id") != row.get("container_id")
        or (inspected.get("State") or {}).get("Running") is not True
    ):
        raise RuntimeError(f"existing slot provision evidence differs: {evidence}")
    row["worker_evidence_path"] = str(evidence)
    row["worker_evidence_sha256"] = sha(evidence)
    return row


def launch_resume(
    *, campaign_id: str, scene: str, snapshot: Path, authority: Path,
    output: Path,
) -> dict[str, Any]:
    suffix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
    run_id = f"{campaign_id}_dynamic_slots_attempt001_{suffix}"
    unit = f"mysql-alga-dynamic-{run_id}.service"
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A four-worker resume {scene}",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", run_id,
        "--scene", scene, "--predecessor-state", str(snapshot),
        "--transition-authority", str(authority),
        "--output-dir", str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        check=False, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} four-worker launch failed: {completed.stderr}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        fields = systemctl(
            "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
        ).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"{scene} successor exited during startup: {fields}")
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("status") == "RUNNING"
                and state.get("async_scene") == scene
                and int(state.get("physical_replay_parallelism") or 0) == 4
            ):
                return {
                    "scene": scene, "run_id": run_id, "unit": unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "stage": state.get("stage"),
                }
        time.sleep(1)
    raise TimeoutError(f"{scene} successor startup timed out")


def run(args: argparse.Namespace) -> int:
    handoff_path = args.handoff_result.resolve()
    if handoff_path.is_file():
        prior = load(handoff_path)
        if prior.get("status") in {"LAUNCHED", "COMPLETE"}:
            print(json.dumps(prior, ensure_ascii=False, sort_keys=True))
            return 0
    transition_contract = validate_transition_contract()
    manifest = load(args.launch_manifest)
    monitor_state = load(args.monitor_state)
    source_freeze = load(SOURCE_FREEZE)
    if (
        sha(SOURCE_FREEZE) != SOURCE_FREEZE_SHA256
        or manifest.get("contract_path") != str(A_CONTRACT)
        or manifest.get("contract_sha256") != sha(A_CONTRACT)
        or monitor_state.get("memory_scenes_complete") is not True
    ):
        raise RuntimeError("dynamic slot transition trigger authority differs")
    memory_states = {
        scene: load(Path(str(manifest["scenes"][scene]["state"])))
        for scene in slots.MEMORY_SCENES
    }
    if not all(
        slots.memory_scene_terminal(memory_states[scene], scene)
        for scene in slots.MEMORY_SCENES
    ):
        raise RuntimeError("both memory scenes have not reached terminal outcomes")
    memory_evidence = {
        scene: {
            "terminal": True,
            "terminal_outcome": memory_states[scene].get("status"),
            "converged": bool(memory_states[scene].get("converged")),
            "accepted": int(memory_states[scene].get("accepted") or 0),
            "state_path": manifest["scenes"][scene]["state"],
            "state_sha256": sha(Path(str(manifest["scenes"][scene]["state"]))),
        }
        for scene in slots.MEMORY_SCENES
    }
    document: dict[str, Any] = {
        "schema_version": "mysql_algorithm_a_dynamic_slot_handoff_v1",
        "status": "TRANSITIONING", "stage": "TRIGGER_VALIDATED",
        "created_at": now(), "updated_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "campaign_id": manifest["campaign_id"],
        "contract": slots.TRANSITION_CONTRACT,
        "transition_contract_path": str(TRANSITION_CONTRACT_PATH),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT_PATH),
        "memory_terminal_evidence": memory_evidence,
    }
    atomic(handoff_path, document)
    campaign_id = str(manifest["campaign_id"])
    transition_root = HISTORY / f"{campaign_id}_dynamic_slots_v2"
    transition_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshots: dict[str, Path] = {}
    for scene in slots.XP_SCENES:
        state_path = Path(str(manifest["scenes"][scene]["state"]))
        snapshot = wait_for_round_boundary(
            state_path, str(manifest["scenes"][scene]["unit"]), scene,
            transition_root,
        )
        if snapshot is not None:
            snapshots[scene] = snapshot
    checkpoint(
        handoff_path, document, "XP_PREDECESSORS_STOPPED_AT_BOUNDARIES",
        predecessor_snapshots={scene: str(path) for scene, path in snapshots.items()},
    )
    released = stop_memory_containers(source_freeze, memory_evidence)
    checkpoint(
        handoff_path, document, "MEMORY_POSITIONS_RELEASED",
        released_memory_workers=released,
    )
    additions = slots.build_released_xp_workers(source_freeze)
    successor_freeze = provision.build_successor_freeze(
        source_freeze, additions, memory_evidence,
    )
    atomic(FREEZE_PATH, successor_freeze)
    checkpoint(
        handoff_path, document, "SUCCESSOR_RESOURCE_FREEZE_SEALED",
        successor_freeze_path=str(FREEZE_PATH),
        successor_freeze_sha256=sha(FREEZE_PATH),
    )
    provision_root = transition_root / "provisioned_workers"
    new_live: dict[str, dict[str, Any]] = {}
    required_additions = [
        row for row in additions if row["scene"] in snapshots
    ]
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="alga-slot") as pool:
        futures = {
            pool.submit(
                load_or_provision, successor_freeze, row, provision_root,
            ): str(row["worker_id"])
            for row in required_additions
        }
        for future in as_completed(futures):
            new_live[futures[future]] = future.result()
    checkpoint(
        handoff_path, document, "NEW_XP_WORKERS_PROVISIONED",
        new_worker_ids=sorted(new_live),
    )
    original_live: dict[str, dict[str, Any]] = {}
    for scene in snapshots:
        predecessor_output = Path(str(manifest["scenes"][scene]["output"]))
        original_live.update(latest_live_worker_rows(
            predecessor_output, scene, successor_freeze,
        ))
    all_live = {**original_live, **new_live}
    target_cpus = {
        cpu for row in all_live.values()
        for cpu in (
            slots.parse_cpuset(row["database_cpuset"])
            | slots.parse_cpuset(row["client_cpuset"])
        )
    }
    gate = coordination.rolling_quiet_gate(
        run_id=f"{campaign_id}_dynamic_slots_v2",
        required_seconds=60.0, maximum_wait_seconds=300.0,
        target_cpus=target_cpus,
    )
    gate["target_worker_ids"] = sorted(all_live)
    gate_path = transition_root / "dynamic_noninterference_gate.json"
    atomic(gate_path, gate)
    if gate.get("status") != "PASS":
        raise RuntimeError("four-worker dynamic non-interference gate failed")
    checkpoint(
        handoff_path, document, "DYNAMIC_NONINTERFERENCE_GATE_PASSED",
        dynamic_noninterference_gate_path=str(gate_path),
        dynamic_noninterference_gate_sha256=sha(gate_path),
    )
    authorities: dict[str, Path] = {}
    for scene, snapshot in snapshots.items():
        prefix = "xp_cpu_" if scene == "hybench_xp_cpu" else "xp_io_"
        scene_workers = {
            worker_id: row for worker_id, row in all_live.items()
            if worker_id.startswith(prefix)
        }
        scene_gate = copy.deepcopy(gate)
        scene_gate["global_target_worker_ids"] = sorted(all_live)
        scene_gate["target_worker_ids"] = sorted(scene_workers)
        predecessor = load(snapshot)
        authority = {
            "schema_version": (
                "mysql_algorithm_a_xp_four_worker_transition_authority_v1"
            ),
            "status": "PASS", "created_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "contract": slots.TRANSITION_CONTRACT,
            "transition_contract_path": str(TRANSITION_CONTRACT_PATH),
            "transition_contract_sha256": sha(TRANSITION_CONTRACT_PATH),
            "scene": scene,
            "predecessor_snapshot_path": str(snapshot),
            "predecessor_snapshot_sha256": sha(snapshot),
            "preserved_search_state_sha256": slots.canonical_sha256(
                slots.search_state_payload(predecessor)
            ),
            "successor_freeze_path": str(FREEZE_PATH),
            "successor_freeze_sha256": sha(FREEZE_PATH),
            "memory_terminal_evidence": memory_evidence,
            "dynamic_noninterference_gate": scene_gate,
            "workers": [scene_workers[key] for key in sorted(scene_workers)],
            "native_xp_workload_unchanged": True,
            "mixed_worker_cohort_round_prohibited": True,
            "fresh_baseline_repetitions_per_worker": 2,
            "incumbent_replay_required": True,
            "canonical_baseline_preserved": True,
            "accepted_ranges_incumbent_history_preserved": True,
        }
        authority_path = transition_root / f"{scene}.transition_authority.json"
        atomic(authority_path, authority)
        authorities[scene] = authority_path
    launches = {}
    for scene, snapshot in snapshots.items():
        suffix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
        output = HISTORY / f"{campaign_id}_dynamic_slots_attempt001_{suffix}"
        launches[scene] = launch_resume(
            campaign_id=campaign_id, scene=scene, snapshot=snapshot,
            authority=authorities[scene],
            output=output,
        )
    document.update({
        "status": "LAUNCHED", "stage": "FOUR_WORKER_XP_RESUMES_LAUNCHED",
        "updated_at": now(), "launches": launches,
        "successor_state_paths": {
            scene: launches[scene]["state"] for scene in launches
        },
        "successor_freeze_path": str(FREEZE_PATH),
        "successor_freeze_sha256": sha(FREEZE_PATH),
        "dynamic_noninterference_gate_path": str(gate_path),
        "dynamic_noninterference_gate_sha256": sha(gate_path),
        "initial_parallelism_per_scene": 2,
        "xp_parallelism_after_transition": 4,
    })
    atomic(handoff_path, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-manifest", type=Path, required=True)
    parser.add_argument("--monitor-state", type=Path, required=True)
    parser.add_argument("--handoff-result", type=Path, required=True)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
