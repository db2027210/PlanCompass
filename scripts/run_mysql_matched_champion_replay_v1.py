#!/usr/bin/env python3
"""Replay all six matched champions after their matched baselines."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v24 as coordination
from scripts import mysql_single_scene_measurement_v2 as single_measurement
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as runtime_config


CONTRACT = ROOT / "config/formal/frozen_mysql_matched_champion_replay_20260829_v1/champion_replay_contract.json"
C49_ADAPTER = ROOT / "config/formal/frozen_mysql_c49_contaminated_champion_adapter_20260829_v4/algorithm_c_measurement_adapter_contract.json"
XP_SCENES = {"hybench_xp_cpu", "hybench_xp_io"}


def now() -> str:
    return datetime.now().astimezone().isoformat()


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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        if "-" in token:
            left, right = token.split("-", 1)
            result.update(range(int(left), int(right) + 1))
        elif token:
            result.add(int(token))
    return result


def validate_contract(scene: str, contract_path: Path) -> dict[str, Any]:
    contract = load(contract_path)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("baseline_replayed") is not False
        or contract.get("champion_replayed") is not True
        or contract.get("algorithm_search_reexecuted") is not False
        or contract.get("accepted_state_credit_increment") != 0
        or scene not in contract.get("assignments_by_scene", {})
    ):
        raise RuntimeError("contaminated champion replay contract differs")
    for binding in contract["source_bindings"].values():
        path = ROOT / binding["path"]
        if sha(path) != binding["sha256"]:
            raise RuntimeError(f"source binding differs: {path}")
    for binding in contract["source_result_evidence"].values():
        path = ROOT / binding["path"]
        if sha(path) != binding["sha256"]:
            raise RuntimeError(f"result source binding differs: {path}")
    return contract


def configure(scene: str, freeze_path: Path, workers: dict[str, dict[str, Any]]) -> Any:
    runtime_config.base.c49.ASYNC_CONTRACT = C49_ADAPTER
    runtime_config.base.c49.validate_contract()
    runtime_config.base.c49.install()
    base = runtime_config.base
    base.alg.run_hybench = base.workload_adapter_v2.run_hybench
    base.alg.HYBENCH_ADAPTIVE_RUNNER_JAR = base.HYBENCH_ADAPTIVE_RUNNER
    base.alg.reset.prep._kernel_resource_readback = base.cgroup_readback_v2.kernel_resource_readback
    original_prepare_json = base.alg.reset.prep._docker_json
    original_reset_json = base.alg.reset._docker_json

    def retrying_json(callback: Any, *argv: str) -> dict[str, Any]:
        deadline = time.monotonic() + 600.0
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            try:
                return callback(*argv)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, RuntimeError) as error:
                last_error = error
                time.sleep(1.0)
        raise RuntimeError(f"Docker JSON readback did not recover: {last_error}")

    base.alg.reset.prep._docker_json = (
        lambda *argv: retrying_json(original_prepare_json, *argv)
    )
    base.alg.reset._docker_json = (
        lambda *argv: retrying_json(original_reset_json, *argv)
    )
    if scene in {"job_memory", "tpch_sf4_memory"}:
        workers_by_container = {
            str(row["container"]): row for row in workers.values()
        }

        def host_cgroup_inspect(*argv: str) -> dict[str, Any]:
            if len(argv) == 2 and argv[0] == "inspect" and argv[1] in workers_by_container:
                worker = workers_by_container[argv[1]]
                _rows, active_ids = coordination._metadata_container_inventory()
                container_id = str(worker.get("container_id") or "")
                if not container_id or container_id not in active_ids:
                    raise RuntimeError(f"sealed relational container is absent: {argv[1]}")
                return {
                    "Id": container_id,
                    "RestartCount": 0,
                    "State": {"Running": True, "OOMKilled": False},
                    "host_cgroup_identity_readback": True,
                    "docker_control_plane_required": False,
                }
            return retrying_json(original_prepare_json, *argv)

        base.alg.reset.prep._docker_json = host_cgroup_inspect
    base.runtime.INDEPENDENT_SCENE_EXECUTION = True
    base.runtime.SCENES = (scene,)
    base.alg.FREEZE = freeze_path
    base.alg.r0mod.FREEZE = freeze_path
    base.alg.r0mod.FREEZE_SCHEMA = load(freeze_path)["schema_version"]
    worker_ids = tuple(sorted(workers))
    base.alg.WORKER_IDS = worker_ids
    xp_ids = worker_ids if scene in XP_SCENES else ()
    base.alg.XP_IDS = xp_ids
    base.alg.reset.XP_IDS = set(xp_ids)
    base.alg.reset.XP_CONTAINER_ALLOWLIST = {
        worker_id: str(workers[worker_id]["container"]) for worker_id in xp_ids
    }
    return base.alg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    args = parser.parse_args()
    scene = str(args.scene)
    output = args.output_dir.resolve()
    state_path = output / "run_state.json"
    if output.exists():
        raise FileExistsError(f"fresh output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    state: dict[str, Any] = {
        "schema_version": "mysql_matched_champion_scene_replay_state_v1",
        "run_id": args.run_id, "scene": scene, "status": "STARTING",
        "stage": "VALIDATING", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "baseline_replayed": False,
        "champion_replayed": True,
        "algorithm_search_reexecuted": False, "accepted_state_credit_increment": 0,
    }
    atomic(state_path, state)
    try:
        contract = validate_contract(scene, args.contract.resolve())
        prepared_path = Path(contract["authority"]["prepared_path"])
        preflight_path = Path(contract["authority"]["preflight_path"])
        accounts_path = Path(contract["authority"]["accounts_path"])
        for key, path in (("prepared_sha256", prepared_path), ("preflight_sha256", preflight_path), ("accounts_sha256", accounts_path)):
            if sha(path) != contract["authority"][key]:
                raise RuntimeError(f"authority binding differs: {path}")
        prepared = load(prepared_path)
        freeze_binding = contract["resource_freeze_by_scene"][scene]
        freeze_path = Path(freeze_binding["path"])
        if sha(freeze_path) != freeze_binding["sha256"]:
            raise RuntimeError("resource freeze binding differs")
        freeze = load(freeze_path)
        assignment_labels = contract["assignments_by_scene"][scene]
        all_workers = {str(row["worker_id"]): copy.deepcopy(dict(row)) for row in prepared["workers"]}
        workers = {worker_id: all_workers[worker_id] for worker_id in assignment_labels}
        if any(
            row.get("ready") is not True
            or not re.fullmatch(r"\d+:\d+", str((row.get("blkio_authority") or {}).get("device_major_minor") or ""))
            for row in workers.values()
        ):
            raise RuntimeError("selected worker authority is not ready with a concrete device")
        alg = configure(scene, freeze_path, workers)
        alg.r0mod.PREFLIGHT = preflight_path
        alg.r0mod.RUNTIME_PREFLIGHT = preflight_path
        alg.r0mod.PREPARED = prepared_path
        alg.r0mod.BENCHMARK_ACCOUNTS = accounts_path
        target_cpus = set().union(*(
            cpus(str(row["database_cpuset"])) | cpus(str(row["client_cpuset"]))
            for row in workers.values()
        ))
        state.update({"status": "RUNNING", "stage": "DYNAMIC_NONINTERFERENCE_GATE"})
        atomic(state_path, state)
        authority_path = Path(contract["authority"]["manifest_path"])
        if sha(authority_path) != contract["authority"]["manifest_sha256"]:
            raise RuntimeError("rolling authority manifest binding differs")
        authority = load(authority_path)
        sealed_gate = authority.get("rolling_dynamic_noninterference_gate") or {}
        policy = load(coordination.CONTRACT)["co_resident_isolated_pg_allowance"]
        snapshot_attempts: list[dict[str, Any]] = []
        snapshot_deadline = time.monotonic() + 30.0
        clean_snapshot_streak = 0
        current_boundary: dict[str, Any] = {}
        current_host_workloads: list[dict[str, Any]] = []
        absent_optional_containers: list[str] = []
        while time.monotonic() < snapshot_deadline:
            _container_rows, active_ids = coordination._metadata_container_inventory()
            current_policy = copy.deepcopy(policy)
            current_policy["containers"] = [
                row for row in policy.get("containers") or []
                if str(row.get("container_id") or "") in active_ids
            ]
            absent_optional_containers = sorted(
                str(row.get("name") or "") for row in policy.get("containers") or []
                if str(row.get("container_id") or "") not in active_ids
            )
            current_boundary = coordination._host_readback(current_policy, target_cpus)
            current_external = coordination._metadata_external_inventory(args.run_id, {0})
            current_host_workloads = list(current_external.get("workloads") or [])
            clean = bool(current_boundary.get("passed") is True and not current_host_workloads)
            clean_snapshot_streak = clean_snapshot_streak + 1 if clean else 0
            snapshot_attempts.append({
                "captured_at": now(),
                "container_boundary_passed": current_boundary.get("passed") is True,
                "host_workload_count": len(current_host_workloads),
                "clean_snapshot_streak": clean_snapshot_streak,
                "active_frozen_container_count": len(current_policy["containers"]),
                "absent_optional_containers": absent_optional_containers,
            })
            if clean_snapshot_streak >= 3:
                break
            time.sleep(1.0)
        gate = {
            "schema_version": "mysql_matched_replay_launch_gate_v1",
            "status": "PASS" if (
                authority.get("status") == "PASS"
                and sealed_gate.get("status") == "PASS"
                and float(sealed_gate.get("observed_seconds") or 0.0) >= 60.0
                and current_boundary.get("passed") is True
                and clean_snapshot_streak >= 3
            ) else "BLOCKED_FAIL_CLOSED",
            "sealed_rolling_gate_manifest": str(authority_path),
            "sealed_rolling_gate_manifest_sha256": sha(authority_path),
            "sealed_rolling_gate_status": sealed_gate.get("status"),
            "sealed_rolling_gate_observed_seconds": sealed_gate.get("observed_seconds"),
            "current_container_cgroup_boundary": current_boundary,
            "current_host_workloads_metadata_only": current_host_workloads,
            "current_snapshot_attempts": snapshot_attempts,
            "required_consecutive_clean_snapshots": 3,
            "maximum_snapshot_wait_seconds": 30,
            "absent_optional_containers": absent_optional_containers,
            "redundant_per_scene_rolling_window_started": False,
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "external_process_control_or_stop_performed": False,
        }
        atomic(output / "dynamic_noninterference_gate.json", gate)
        if gate.get("status") != "PASS":
            raise RuntimeError("dynamic non-interference gate failed closed")
        assignments = {
            worker_id: {
                "rank": index,
                "params": copy.deepcopy(contract["points"][label]["params"]),
            }
            for index, (worker_id, label) in enumerate(assignment_labels.items(), start=1)
        }
        state.update({"stage": "RESETTING_AND_MEASURING", "assignments": assignment_labels})
        atomic(state_path, state)
        measured = single_measurement.measure_batch(
            load(freeze_path), workers, assignments, output / "physical_replay",
            f"{args.run_id}_champions", state_path=state_path,
            stage_prefix="MATCHED_CHAMPION_REPLAY",
        )
        batch_path = output / "physical_replay/batch_result.json"
        batch = load(batch_path)
        if batch.get("status") != "PASS" or set(measured) != set(assignments):
            raise RuntimeError("champion replay batch failed closed")
        points: dict[str, Any] = {}
        for worker_id, row in measured.items():
            label = assignment_labels[worker_id]
            observed_device = str(row["resource_summary"]["device_major_minor"])
            expected_device = str(workers[worker_id]["blkio_authority"]["device_major_minor"])
            if (
                row.get("hard_gate_passed") is not True
                or float(row["resource_means"]["io"]) <= 0.0
                or observed_device != expected_device
            ):
                raise RuntimeError(f"{worker_id} corrected champion IO measurement is invalid")
            points[label] = {**copy.deepcopy(dict(row)), "device_major_minor": observed_device}
        result = {
            "schema_version": "mysql_matched_champion_scene_replay_result_v1",
            "status": "PASS", "completed_at": now(), "scene": scene,
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "baseline_replayed": False, "champion_replayed": True,
            "algorithm_search_reexecuted": False,
            "accepted_state_credit_increment": 0, "historical_artifacts_mutated": False,
            "point_results": points, "batch_result_path": str(batch_path),
            "batch_result_sha256": sha(batch_path),
        }
        result_path = output / "champion_replay_result.json"
        atomic(result_path, result)
        state.update({
            "status": "COMPLETE", "stage": "COMPLETE", "completed_at": now(),
            "result_path": str(result_path), "result_sha256": sha(result_path),
        })
        atomic(state_path, state)
        print(json.dumps({"status": "PASS", "result": str(result_path)}))
        return 0
    except BaseException as error:
        state.update({
            "status": "FAILED_CLOSED", "stage": "FAILED", "failed_at": now(),
            "error": f"{type(error).__name__}: {error}",
        })
        atomic(state_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
