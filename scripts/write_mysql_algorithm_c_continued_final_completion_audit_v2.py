#!/usr/bin/env python3
"""Seal a completed four-scene campaign with one checkpointed continuation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v12 as checkpoint_v12
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v15 as checkpoint_v15
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v16 as checkpoint_v16
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v17 as checkpoint_v17
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v18 as checkpoint_v18
from scripts import write_mysql_algorithm_c_final_completion_audit_v1 as base


HISTORY = ROOT / "history/mysql_four_scene_20260809"
TERMINAL_WIDTH = 0.01
EXPECTED_WAVES = (2, 2, 1)
PARAMETER_TABLE_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1"
)
PARAMETER_TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_io.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.operator_physics_v1.json",
}


def load(path: Path) -> dict[str, Any]:
    return base.load(path)


def require(condition: bool, message: str) -> None:
    base.require(condition, message)


def controller_process_snapshot(
    manifest: dict[str, Any], proc_root: Path = Path("/proc")
) -> dict[str, Any]:
    """Capture controller liveness once, without starting a polling monitor."""

    rows: dict[str, Any] = {}
    for scene, launch in sorted((manifest.get("launches") or {}).items()):
        pid = int(launch.get("pid") or 0)
        require(pid > 1, f"{scene}: controller PID is missing")
        proc_dir = proc_root / str(pid)
        command_path = proc_dir / "cmdline"
        try:
            raw_command = command_path.read_bytes()
        except FileNotFoundError:
            process_exists = False
            arguments: list[str] = []
        except OSError as exc:
            raise RuntimeError(f"{scene}: cannot read controller process identity: {exc}") from exc
        else:
            process_exists = True
            arguments = [
                value.decode("utf-8", errors="replace")
                for value in raw_command.split(b"\0")
                if value
            ]

        run_id = str(launch.get("run_id") or "")
        run_id_bound = any(
            arguments[index] == "--run-id"
            and index + 1 < len(arguments)
            and arguments[index + 1] == run_id
            for index in range(len(arguments))
        )
        expected_controller_alive = process_exists and run_id_bound
        rows[scene] = {
            "pid": pid,
            "run_id": run_id,
            "process_exists_at_pid": process_exists,
            "run_id_bound_controller_alive": expected_controller_alive,
            "observed_argv": arguments,
        }
        require(
            expected_controller_alive is False,
            f"{scene}: formal controller is still alive",
        )

    return {
        "schema_version": "mysql_algorithm_c_manual_terminal_snapshot_v1",
        "captured_at": datetime.now().astimezone().isoformat(),
        "observation_mode": "single_manual_read",
        "continuous_polling": False,
        "all_expected_controllers_exited": True,
        "scenes": rows,
    }


def write_terminal_snapshot_atomic(
    snapshot_path: Path,
    terminal_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Write a recoverable one-shot snapshot before the final audit is sealed."""

    require(
        not snapshot_path.is_symlink(),
        f"terminal snapshot must not be a symlink: {snapshot_path}",
    )
    require(
        not snapshot_path.exists() or snapshot_path.is_file(),
        f"terminal snapshot path is not a regular file: {snapshot_path}",
    )
    replaced_unsealed_snapshot = snapshot_path.exists()
    temporary = snapshot_path.with_name(f".{snapshot_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(terminal_snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, snapshot_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "path": str(snapshot_path),
        "sha256": base.sha(snapshot_path),
        "replaced_unsealed_prior_snapshot": replaced_unsealed_snapshot,
    }


def seal_final_audit_atomic(
    output: Path,
    document: dict[str, Any],
) -> dict[str, Any]:
    """Publish the checksum before the audit so interruption stays retryable."""

    require(
        not output.exists() and not output.is_symlink(),
        f"fresh audit required: {output}",
    )
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    require(
        not checksum_path.is_symlink(),
        f"audit checksum must not be a symlink: {checksum_path}",
    )
    require(
        not checksum_path.exists() or checksum_path.is_file(),
        f"audit checksum path is not a regular file: {checksum_path}",
    )
    output_temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    checksum_temporary = checksum_path.with_name(
        f".{checksum_path.name}.{os.getpid()}.tmp"
    )
    try:
        output_temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        digest = base.sha(output_temporary)
        checksum_temporary.write_text(
            f"{digest}  {output.name}\n",
            encoding="utf-8",
        )
        # If interrupted between these replaces, only an orphan checksum can
        # remain.  A retry safely overwrites it while the final audit is absent.
        os.replace(checksum_temporary, checksum_path)
        os.replace(output_temporary, output)
    finally:
        for temporary in (output_temporary, checksum_temporary):
            if temporary.exists():
                temporary.unlink()

    require(base.sha(output) == digest, "published final audit digest differs")
    _verify_sidecar_checksum(output, "published final audit")
    return {
        "path": str(output),
        "sha256": digest,
        "checksum_path": str(checksum_path),
    }


def _verified_artifact(receipt: dict[str, Any], label: str) -> Path:
    path = Path(str(receipt.get("path") or "")).resolve()
    require(path.is_file(), f"{label}: artifact is missing")
    require(receipt.get("sha256") == base.sha(path), f"{label}: SHA-256 differs")
    return path


def _verify_sidecar_checksum(path: Path, label: str) -> str:
    digest = base.sha(path)
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    require(checksum_path.is_file(), f"{label}: checksum sidecar is missing")
    fields = checksum_path.read_text(encoding="utf-8").strip().split()
    require(len(fields) >= 2, f"{label}: checksum sidecar is malformed")
    require(fields[0] == digest, f"{label}: checksum sidecar digest differs")
    require(Path(fields[-1]).name == path.name, f"{label}: checksum sidecar filename differs")
    return digest


def _validate_dynamic_gate(gate: dict[str, Any], scene: str, label: str) -> dict[str, Any]:
    require(gate.get("status") == "PASS", f"{label}: status is not PASS")
    require(gate.get("formal_scope") == "mysql_only", f"{label}: formal scope differs")
    require(gate.get("cross_engine_comparison_prohibited") is True, f"{label}: comparison flag differs")
    require(not gate.get("errors"), f"{label}: gate errors are present")
    require(gate.get("external_process_control_or_stop_performed") is False, f"{label}: external process control occurred")
    require(gate.get("postgresql_process_control_or_stop_performed") is False, f"{label}: PostgreSQL process control occurred")
    require(gate.get("cross_scene_completion_dependency") is False, f"{label}: cross-scene dependency is enabled")
    require(gate.get("shared_nvme_mutual_exclusion") is False, f"{label}: shared-NVMe lock is enabled")
    require(gate.get("numa_exclusive_scheduler_lock") is False, f"{label}: NUMA-exclusive lock is enabled")
    require(gate.get("whole_round_resource_lease") is False, f"{label}: whole-round lease is enabled")
    require(gate.get("lease_mode") == "none", f"{label}: lease mode differs")
    require(gate.get("kernel_proc_and_cgroup_readback_authoritative") is True, f"{label}: kernel/cgroup readback is not authoritative")
    observed = float(gate.get("observed_seconds") or 0)
    required_seconds = float(gate.get("required_clean_seconds") or 0)
    require(required_seconds > 0 and observed >= required_seconds, f"{label}: rolling clean window is incomplete")

    samples = gate.get("samples") or []
    require(samples, f"{label}: rolling-window samples are missing")
    worker_ids: set[str] = set()
    transient_reset_count = 0
    last_reset_index = -1
    allowed_transient_errors = {
        "external_database_or_benchmark_isolation_unproven",
        "persistent_target_overlapping_uninterruptible_sleep",
    }
    for sample_index, sample in enumerate(samples, 1):
        sample_label = f"{label} sample {sample_index}"
        sample_errors = set(sample.get("errors") or [])
        blocking_workloads = sample.get("blocking_external_workloads") or []
        if sample_errors or blocking_workloads:
            require(sample_errors, f"{sample_label}: blocking workload lacks a gate error")
            require(sample_errors.issubset(allowed_transient_errors), f"{sample_label}: unrecognized transient error")
            require(float(sample.get("clean_elapsed_seconds") or 0) == 0.0, f"{sample_label}: dirty sample did not reset the clean window")
            if "external_database_or_benchmark_isolation_unproven" in sample_errors:
                require(blocking_workloads, f"{sample_label}: external isolation error lacks a blocking process")
            if "persistent_target_overlapping_uninterruptible_sleep" in sample_errors:
                require(sample.get("persistent_blocking_d_state_pids"), f"{sample_label}: D-state error lacks persistent PIDs")
            transient_reset_count += 1
            last_reset_index = sample_index - 1
        proof = sample.get("target_local_resource_proof") or {}
        require(proof.get("passed") is True, f"{sample_label}: target resource proof failed")
        require(proof.get("controller_affinity_exact") is True, f"{sample_label}: controller affinity differs")
        require(proof.get("host_swap_state_is_audited_not_mutated") is True, f"{sample_label}: host swap policy differs")
        require(not proof.get("errors"), f"{sample_label}: target resource errors are present")
        workers = proof.get("workers") or []
        require(workers, f"{sample_label}: worker readbacks are missing")
        for worker in workers:
            require(worker.get("passed") is True, f"{sample_label}: worker resource proof failed")
            require(not worker.get("errors"), f"{sample_label}: worker resource errors are present")
            checks = worker.get("checks") or {}
            required_checks = {
                "authority_identity_present",
                "cgroup_directories_present",
                "cpu_quota",
                "cpuset",
                "effective_process_affinity",
                "memory",
                "numa",
                "pids",
                "prepared_swap_policy",
                "read_bps",
                "running_process_present",
                "swappiness_zero",
                "write_bps",
            }
            require(required_checks.issubset(checks), f"{sample_label}: worker resource checks are incomplete")
            require(all(checks[name] is True for name in required_checks), f"{sample_label}: worker resource check failed")
            worker_ids.add(str(worker.get("worker_id") or ""))
    require("" not in worker_ids, f"{label}: worker identity is missing")
    final_sample = samples[-1]
    require(not final_sample.get("errors"), f"{label}: final sample has errors")
    require(not final_sample.get("blocking_external_workloads"), f"{label}: final sample has a blocking workload")
    require(
        float(final_sample.get("clean_elapsed_seconds") or 0) >= required_seconds,
        f"{label}: final contiguous clean window is incomplete",
    )
    require(
        all(
            not sample.get("errors") and not sample.get("blocking_external_workloads")
            for sample in samples[last_reset_index + 1 :]
        ),
        f"{label}: final clean suffix contains a reset",
    )
    return {
        "scene": scene,
        "status": "PASS",
        "observed_seconds": observed,
        "required_clean_seconds": required_seconds,
        "sample_count": len(samples),
        "worker_ids": sorted(worker_ids),
        "transient_clean_window_resets": transient_reset_count,
        "final_contiguous_clean_seconds": float(final_sample["clean_elapsed_seconds"]),
        "cpu_numa_memory_swap_io_process_readbacks_passed": True,
        "blocking_external_workloads_absent": True,
        "process_control_performed": False,
        "prohibited_locks_and_barriers_absent": True,
    }


def dynamic_gate_receipt(path: Path, scene: str, outer: int, batch: int) -> dict[str, Any]:
    label = f"{scene}: outer {outer} batch {batch} dynamic gate"
    gate = load(path)
    receipt = _validate_dynamic_gate(gate, scene, label)
    receipt.update({"path": str(path), "sha256": _verify_sidecar_checksum(path, label)})
    return receipt


def _load_properties(path: Path) -> dict[str, str]:
    require(path.is_file(), f"XP properties are missing: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        require(key not in values, f"duplicate XP property: {key}")
        values[key] = value.strip()
    return values


def native_xp_contract_receipt(state: dict[str, Any], scene: str) -> dict[str, Any] | None:
    if scene not in {"hybench_xp_cpu", "hybench_xp_io"}:
        return None

    freeze_path = Path(str(state.get("freeze_path") or "")).resolve()
    require(freeze_path.is_file(), f"{scene}: resource freeze is missing")
    require(state.get("freeze_sha256") == base.sha(freeze_path), f"{scene}: resource freeze SHA-256 differs")
    resource_freeze = load(freeze_path)
    contract = resource_freeze.get("native_xp_contract") or {}
    require(contract.get("ap_selection") == "IQ1-IQ7_uniform_random", f"{scene}: IQ selection differs")
    require(contract.get("at1_at6_percent") == "35/25/15/15/7/3", f"{scene}: AT mix differs")
    require(contract.get("xap_xtp_ratio") == "1:1", f"{scene}: XAP:XTP ratio differs")
    require(contract.get("cpu_and_io_workload_identical") is True, f"{scene}: CPU/I/O XP workload differs")
    require(contract.get("workload_mix_override_prohibited") is True, f"{scene}: XP mix overrides are allowed")

    workload_binding = contract.get("workload_freeze") or {}
    workload_path = Path(str(workload_binding.get("path") or ""))
    if not workload_path.is_absolute():
        workload_path = (ROOT / workload_path).resolve()
    require(workload_path.is_file(), f"{scene}: native XP workload freeze is missing")
    require(workload_binding.get("sha256") == base.sha(workload_path), f"{scene}: native XP workload freeze SHA-256 differs")
    workload_freeze = load(workload_path)
    require(workload_freeze.get("status") == "FROZEN", f"{scene}: native XP workload is not frozen")
    native = workload_freeze.get("native_hybench_xp") or {}
    require(native.get("iq_selection") == "uniform_random_IQ1_IQ7", f"{scene}: native IQ policy differs")
    require(native.get("at_mix_percent") == [35, 25, 15, 15, 7, 3], f"{scene}: native AT policy differs")
    require(native.get("exclusive_random_range") == [1, 8], f"{scene}: native IQ range differs")
    require(native.get("xap_xtp_baseline") == [8, 8], f"{scene}: native client baseline differs")
    require(native.get("xap_xtp_reduced_ratio") == [1, 1], f"{scene}: native client ratio differs")
    require(native.get("cpu_and_io_workload_identical") is True, f"{scene}: native CPU/I/O workload differs")

    runtime_binding = native.get("runtime_jar") or {}
    runtime_path = Path(str(runtime_binding.get("path") or ""))
    if not runtime_path.is_absolute():
        runtime_path = (ROOT / runtime_path).resolve()
    require(runtime_path.is_file(), f"{scene}: native XP runtime JAR is missing")
    require(runtime_binding.get("sha256") == base.sha(runtime_path), f"{scene}: native XP runtime JAR SHA-256 differs")
    return {
        "resource_freeze": {"path": str(freeze_path), "sha256": base.sha(freeze_path)},
        "workload_freeze": {"path": str(workload_path), "sha256": base.sha(workload_path)},
        "runtime_jar": {"path": str(runtime_path), "sha256": base.sha(runtime_path)},
        "iq_selection": "uniform_random_IQ1_IQ7",
        "at_mix_percent": [35, 25, 15, 15, 7, 3],
        "xap_xtp_clients": [8, 8],
        "cpu_and_io_workload_identical": True,
        "workload_mix_override_prohibited": True,
    }


def tpch_sf4_contract_receipt(state: dict[str, Any], scene: str) -> dict[str, Any] | None:
    if scene != "tpch_sf4_memory":
        return None
    freeze_path = Path(str(state.get("freeze_path") or "")).resolve()
    require(freeze_path.is_file(), "TPC-H resource freeze is missing")
    require(state.get("freeze_sha256") == base.sha(freeze_path), "TPC-H resource freeze SHA-256 differs")
    resource_freeze = load(freeze_path)
    storage = resource_freeze.get("tpch_sf4_storage_manifest") or {}
    require(storage.get("database") == "tpch", "TPC-H frozen database differs")
    require(int(storage.get("scale_factor") or 0) == 4, "TPC-H scale factor is not SF4")
    storage_path = Path(str(storage.get("path") or "")).resolve()
    require(storage_path.is_file(), "TPC-H SF4 storage manifest is missing")
    require(storage.get("sha256") == base.sha(storage_path), "TPC-H SF4 storage manifest SHA-256 differs")
    return {
        "resource_freeze": {"path": str(freeze_path), "sha256": base.sha(freeze_path)},
        "storage_manifest": {"path": str(storage_path), "sha256": base.sha(storage_path)},
        "database": "tpch",
        "scale_factor": 4,
        "lane_count": 5,
        "required_queries": [f"Q{i}" for i in range(1, 23)],
        "maximum_wall_time_seconds": 900,
    }


def coverage_receipt(
    scene: str,
    measurement: dict[str, Any],
    native_xp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receipt = base.coverage_receipt(scene, measurement)
    workload = measurement.get("workload_result") or {}
    if scene == "job_memory":
        require(workload.get("adapter") == "job", "JOB adapter differs")
        require(workload.get("dry_run") is False, "JOB dry-run result is ineligible")
        require(int(workload.get("repetitions") or 0) == 1, "JOB suite repetition differs")
        require(int(workload.get("concurrency") or 0) == 16, "JOB concurrency differs")
        require(workload.get("dedicated_heavy_pool") is True, "JOB heavy-query pool is disabled")
        require(int(workload.get("maximum_concurrent_heavy_queries") or 0) <= 4, "JOB heavy-query admission exceeds four")
        require(float(workload.get("heavy_query_threshold_s") or 0) == 30.0, "JOB heavy-query threshold differs")
        require(float(workload.get("heavy_start_stagger_s") or 0) > 0.0, "JOB heavy queries were not staggered")
        require(workload.get("scene_independent_execution") is True, "JOB execution is not scene-independent")
        require(workload.get("four_scene_wave_barrier") is False, "JOB four-scene barrier is enabled")
        wall_time = float(workload.get("independent_wall_time_s") or workload.get("wall_time_s") or 0)
        require(0.0 < wall_time <= 900.0, "JOB wall time is outside the formal upper bound")
        executions = workload.get("query_executions") or []
        queries = [row.get("query") for row in executions]
        required = (workload.get("query_coverage") or {}).get("required_queries") or []
        require(len(executions) == 113, "JOB execution count differs")
        require(all(row.get("returncode") == 0 for row in executions), "JOB query execution failed")
        require(len(set(queries)) == 113 and set(queries) == set(required), "JOB did not execute each SQL exactly once")
        receipt.update({
            "wall_time_seconds": wall_time,
            "concurrency": 16,
            "maximum_concurrent_heavy_queries": int(workload["maximum_concurrent_heavy_queries"]),
            "heavy_queries_staggered": True,
            "each_sql_executed_exactly_once": True,
        })
        return receipt

    if scene == "tpch_sf4_memory":
        require(workload.get("scene_independent_execution") is True, "TPC-H execution is not scene-independent")
        require(workload.get("four_scene_wave_barrier") is False, "TPC-H four-scene barrier is enabled")
        wall_time = float(workload.get("independent_wall_time_s") or 0)
        require(0.0 < wall_time <= 900.0, "TPC-H wall time is outside the formal upper bound")
        lanes = workload.get("lanes") or []
        require(len(lanes) == 5, "TPC-H lane count is not five")
        completed: list[str] = []
        for lane_index, lane in enumerate(lanes, 1):
            require(lane.get("adapter") == "benchbase", f"TPC-H lane {lane_index} adapter differs")
            require(lane.get("dry_run") is False, f"TPC-H lane {lane_index} dry-run result is ineligible")
            coverage = lane.get("query_coverage") or {}
            require(coverage.get("coverage_complete") is True, f"TPC-H lane {lane_index} coverage failed")
            require(not coverage.get("missing_queries"), f"TPC-H lane {lane_index} has missing SQL")
            require(not coverage.get("unexpected_transaction_names"), f"TPC-H lane {lane_index} has unexpected SQL")
            lane_completed = coverage.get("completed_queries") or []
            require(lane_completed == (coverage.get("required_queries") or []), f"TPC-H lane {lane_index} schedule differs")
            completed.extend(lane_completed)
        required_queries = {f"Q{i}" for i in range(1, 23)}
        require(len(completed) == 22, "TPC-H did not execute exactly 22 queries")
        require(len(set(completed)) == 22 and set(completed) == required_queries, "TPC-H did not execute Q1-Q22 exactly once")
        receipt.update({
            "scale_factor": 4,
            "wall_time_seconds": wall_time,
            "lane_count": 5,
            "each_query_executed_exactly_once": True,
        })
        return receipt

    if scene not in {"hybench_xp_cpu", "hybench_xp_io"}:
        return receipt
    require(native_xp is not None, f"{scene}: native XP contract is missing")
    require(workload.get("adapter") == "hybench", f"{scene}: XP adapter differs")
    require(workload.get("mode") == "xp", f"{scene}: XP mode differs")
    require(workload.get("dry_run") is False, f"{scene}: XP dry-run result is ineligible")
    require(
        workload.get("runtime_jar_sha256") == native_xp["runtime_jar"]["sha256"],
        f"{scene}: measured XP runtime JAR differs from the freeze",
    )

    properties_path = Path(str(workload.get("properties") or "")).resolve()
    properties = _load_properties(properties_path)
    forbidden = {
        "ap_query_ids",
        "ap_query_weights",
        "ap_query_sequence",
        "ap_query_sequence_limit",
    }
    require(not forbidden.intersection(properties), f"{scene}: forbidden XP query override is present")
    require(properties.get("dist") == "uniform", f"{scene}: XP IQ distribution is not uniform")
    expected_at = [35, 25, 15, 15, 7, 3]
    observed_at = [int(properties.get(f"at{i}_percent", "-1")) for i in range(1, 7)]
    require(observed_at == expected_at, f"{scene}: XP AT1-AT6 mix differs")
    observed_clients = [
        int(properties.get("xapclient", "-1")),
        int(properties.get("xtpclient", "-1")),
    ]
    require(observed_clients == native_xp["xap_xtp_clients"], f"{scene}: XP XAP:XTP clients differ")
    require(
        int(properties.get("apclient", "-1")) == 8
        and int(properties.get("tpclient", "-1")) == 8,
        f"{scene}: XP AP/TP client baseline differs",
    )
    receipt["native_workload_contract"] = {
        "iq_selection": "uniform_random_IQ1_IQ7",
        "at_mix_percent": observed_at,
        "xap_xtp_clients": observed_clients,
        "runtime_jar_sha256": workload["runtime_jar_sha256"],
        "forbidden_overrides_absent": True,
    }
    return receipt


def _parse_cpuset(value: str, label: str) -> set[int]:
    cpus: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        require(bool(token), f"{label}: empty CPU-set token")
        if "-" in token:
            fields = token.split("-", 1)
            require(all(field.isdigit() for field in fields), f"{label}: malformed CPU range")
            first, last = (int(field) for field in fields)
            require(0 <= first <= last, f"{label}: invalid CPU range")
            cpus.update(range(first, last + 1))
        else:
            require(token.isdigit(), f"{label}: malformed CPU identifier")
            cpus.add(int(token))
    require(bool(cpus), f"{label}: CPU set is empty")
    return cpus


def original_full_parallel_launch_receipt(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    binding = manifest.get("original_full_parallel_launch") or {}
    launch_path = _verified_artifact(binding, "original full-parallel launch")
    _verify_sidecar_checksum(launch_path, "original full-parallel launch")
    launch = load(launch_path)
    scenes_expected = set(base.SCENES.values())
    require(
        launch.get("schema_version")
        == "mysql_algorithm_c_v211_pg_dynamic_gate_four_scene_launch_v1",
        "original launch schema differs",
    )
    require(launch.get("formal_scope") == "mysql_only", "original launch scope differs")
    require(
        launch.get("cross_engine_comparison_prohibited") is True,
        "original launch comparison flag differs",
    )
    require(launch.get("four_scene_round_barrier") is False, "original launch has a four-scene barrier")
    require(launch.get("sibling_scene_wait") is False, "original launch has sibling-scene waiting")
    launches = launch.get("launches") or {}
    require(set(launches) == scenes_expected, "original launch scene set differs")

    offsets: dict[str, float] = {}
    cpu_sets: dict[str, set[int]] = {}
    for scene, row in sorted(launches.items()):
        offset = float(row.get("started_offset_seconds"))
        require(offset >= 0, f"{scene}: original launch offset is negative")
        offsets[scene] = offset
        require(int(row.get("pid") or 0) > 1, f"{scene}: original controller PID is missing")
        lineage = (manifest.get("decision_lineage") or {}).get(scene) or []
        require(bool(lineage), f"{scene}: decision lineage is missing")
        require(
            row.get("run_id") == lineage[0].get("run_id"),
            f"{scene}: original run differs from first decision lineage",
        )
        cpu_sets[scene] = _parse_cpuset(
            str(row.get("controller_cpuset") or ""),
            f"{scene}: original controller CPU set",
        )

    maximum_skew = max(offsets.values())
    require(
        abs(float(launch.get("maximum_start_skew_seconds")) - maximum_skew) <= 1e-12,
        "original launch maximum start skew differs",
    )
    require(maximum_skew <= 1.0, "original four-scene launch was not concurrent")
    for index, left in enumerate(sorted(cpu_sets)):
        for right in sorted(cpu_sets)[index + 1 :]:
            require(
                cpu_sets[left].isdisjoint(cpu_sets[right]),
                f"original controller CPU sets overlap: {left}, {right}",
            )

    return {
        "path": str(launch_path),
        "sha256": base.sha(launch_path),
        "scene_count": len(launches),
        "started_offset_seconds": offsets,
        "maximum_start_skew_seconds": maximum_skew,
        "controller_cpuset": {
            scene: str(launches[scene]["controller_cpuset"])
            for scene in sorted(launches)
        },
        "controller_cpu_sets_disjoint": True,
        "four_scene_round_barrier": False,
        "sibling_scene_wait": False,
    }


def failed_recovery_attempts_receipt(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    authoritative: dict[str, set[tuple[str, int]]] = {}
    for scene, segments in (manifest.get("decision_lineage") or {}).items():
        for segment in segments:
            run_id = str(segment.get("run_id") or "")
            start = int(segment.get("from_outer") or 0)
            require(run_id and start >= 1, f"{scene}: malformed decision lineage")
            run = HISTORY / run_id
            if segment.get("through_outer") == "terminal":
                indexes = [
                    int(path.parent.name.split("_", 1)[1])
                    for path in run.glob(f"outer_*/{scene}.decision.json")
                ]
                end = max(indexes) if indexes else start - 1
            else:
                end = int(segment.get("through_outer") or 0)
            authoritative.setdefault(run_id, set()).update(
                (scene, outer) for outer in range(start, end + 1)
            )

    attempts = manifest.get("failed_recovery_attempts") or []
    require(isinstance(attempts, list) and attempts, "failed recovery inventory is missing")
    seen_run_ids: set[str] = set()
    receipts = []
    workload_started_count = 0
    preserved_authoritative_decisions = 0
    for row in attempts:
        require(isinstance(row, dict), "failed recovery entry is malformed")
        run_id = str(row.get("run_id") or "")
        require(run_id and run_id not in seen_run_ids, "failed recovery run ID is missing or duplicated")
        seen_run_ids.add(run_id)
        require(bool(row.get("reason")), f"{run_id}: failure reason is missing")
        for field in (
            "completed_outer_credit",
            "rejected_outer_attempt_increment",
            "incomplete_outer_credit",
        ):
            if field in row:
                require(int(row[field]) == 0, f"{run_id}: technical failure received {field}")

        workload_started = (
            row.get("workload_started") is True
            or row.get("workload_started_in_incomplete_outer") is True
        )
        workload_started_count += int(workload_started)
        run = HISTORY / run_id
        if workload_started:
            require(run.is_dir(), f"{run_id}: started recovery run directory is missing")

        decisions = []
        if run.is_dir():
            for decision_path in sorted(run.glob("outer_*/*.decision.json")):
                scene = decision_path.name.removesuffix(".decision.json")
                outer = int(decision_path.parent.name.split("_", 1)[1])
                require(
                    (scene, outer) in authoritative.get(run_id, set()),
                    f"{run_id}: failed recovery decision entered no authoritative lineage",
                )
                _verify_sidecar_checksum(
                    decision_path,
                    f"{run_id}: preserved outer {outer} decision",
                )
                decisions.append({
                    "scene": scene,
                    "outer_attempt": outer,
                    "path": str(decision_path),
                    "sha256": base.sha(decision_path),
                    "authoritative_lineage_preserved": True,
                })
        if "incomplete_outer_attempt" in row:
            incomplete_outer = int(row["incomplete_outer_attempt"])
            require(
                all(item["outer_attempt"] != incomplete_outer for item in decisions),
                f"{run_id}: incomplete outer produced a decision",
            )
        preserved_authoritative_decisions += len(decisions)
        receipts.append({
            "run_id": run_id,
            "reason": row["reason"],
            "workload_started": workload_started,
            "run_directory_exists": run.is_dir(),
            "accepted_credit": 0,
            "rejected_credit": 0,
            "non_authoritative_decision_count": 0,
            "preserved_authoritative_decisions": decisions,
        })

    return {
        "attempt_count": len(receipts),
        "workload_started_attempt_count": workload_started_count,
        "preserved_authoritative_decision_count": preserved_authoritative_decisions,
        "non_authoritative_decision_count": 0,
        "all_technical_failures_received_zero_semantic_credit": True,
        "attempts": receipts,
    }


def stable_round_receipt(manifest: dict[str, Any]) -> dict[str, Any]:
    """Verify the sealed four-scene outer-1 audit and its read-only observation."""

    binding = manifest.get("stable_complete_round_observation") or {}
    audit_path = _verified_artifact(binding.get("audit") or {}, "stable round audit")
    audit = load(audit_path)
    scenes_expected = set(base.SCENES.values())
    require(
        audit.get("schema_version") == "mysql_algorithm_c_first_complete_outer_round_audit_v1",
        "stable round audit schema differs",
    )
    require(
        audit.get("status") == "PASS_RUNNING_FIRST_COMPLETE_OUTER_OBSERVED",
        "stable round audit status differs",
    )
    require(audit.get("formal_scope") == "mysql_only", "stable round scope differs")
    require(
        audit.get("cross_engine_comparison_prohibited") is True,
        "stable round comparison flag differs",
    )
    require(
        audit.get("all_four_scenes_complete_outer_001") is True,
        "stable round does not cover all four scenes",
    )
    require(int(audit.get("completed_scene_count") or 0) == 4, "stable round scene count differs")
    require(audit.get("experiment_continues_independently") is True, "stable round did not continue")

    scene_rows = audit.get("completed_outer_001_scenes") or {}
    require(set(scene_rows) == scenes_expected, "stable round scene set differs")
    scene_receipts: dict[str, Any] = {}
    for scene, row in sorted(scene_rows.items()):
        decision = row.get("decision") or {}
        require(decision.get("formal_evidence_eligible") is True, f"{scene}: stable decision is ineligible")
        require(int(decision.get("physical_top5_measurement_count") or 0) == 5, f"{scene}: stable Top5 count differs")
        require(0 < int(decision.get("semantic_evaluation_count") or 0) <= 5, f"{scene}: stable semantic count differs")
        decision_path = _verified_artifact(decision, f"{scene}: stable decision")

        waves = row.get("physical_waves") or []
        require(len(waves) == len(EXPECTED_WAVES), f"{scene}: stable wave count differs")
        wave_receipts = []
        for index, (wave, expected_count) in enumerate(zip(waves, EXPECTED_WAVES), 1):
            require(int(wave.get("batch_index") or 0) == index, f"{scene}: stable batch index differs")
            require(wave.get("status") == "PASS", f"{scene}: stable batch failed")
            require(int(wave.get("measurement_count") or 0) == expected_count, f"{scene}: stable wave size differs")
            wave_path = _verified_artifact(wave, f"{scene}: stable batch {index}")
            wave_receipts.append({
                "batch_index": index,
                "measurement_count": expected_count,
                "path": str(wave_path),
                "sha256": base.sha(wave_path),
            })

        coverage = row.get("coverage") or []
        require(len(coverage) == 5, f"{scene}: stable coverage count differs")
        for coverage_row in coverage:
            if scene == "job_memory":
                require(
                    coverage_row == {"suite": "full_job", "completed": 113, "required": 113},
                    "JOB stable full-suite coverage differs",
                )
            elif scene == "tpch_sf4_memory":
                require(
                    coverage_row == {"suite": "tpch_sf4_q1_q22", "completed": 22, "required": 22},
                    "TPC-H stable full-suite coverage differs",
                )
            else:
                require(coverage_row.get("suite") == "native_hybench_xp", f"{scene}: stable XP suite differs")
                require(set(coverage_row.get("completed_iq") or []) == {f"IQ{i}" for i in range(1, 8)}, f"{scene}: stable IQ coverage differs")
                require(set(coverage_row.get("completed_at") or []) == {f"AT{i}" for i in range(1, 7)}, f"{scene}: stable AT coverage differs")

        scene_receipts[scene] = {
            "decision": {"path": str(decision_path), "sha256": base.sha(decision_path)},
            "waves": wave_receipts,
            "coverage_measurements": len(coverage),
        }

    baseline_rows = audit.get("paper_baseline_resource_gates") or {}
    require(set(baseline_rows) == scenes_expected, "stable round baseline scene set differs")
    for scene, row in baseline_rows.items():
        _verified_artifact(row, f"{scene}: stable baseline gate")
        deltas = row.get("delta_percentage_points") or {}
        require(set(deltas) == {"cpu", "memory", "io"}, f"{scene}: stable baseline resources differ")
        require(all(abs(float(value)) <= 1.0 for value in deltas.values()), f"{scene}: stable baseline exceeds 1 pp")

    monitor_path = _verified_artifact(
        binding.get("read_only_monitor_snapshot") or {},
        "stable round read-only monitor snapshot",
    )
    monitor = load(monitor_path)
    require(monitor.get("read_only") is True, "stable round monitor was not read-only")
    require(monitor.get("process_control_performed") is False, "stable round monitor controlled a process")
    require(
        monitor.get("postgresql_process_control_or_stop_performed") is False,
        "stable round monitor controlled PostgreSQL",
    )
    require(
        monitor.get("at_least_one_complete_outer_round") is True,
        "stable round monitor did not observe a complete outer round",
    )
    require(monitor.get("first_complete_outer_round_observed_at"), "stable round observation time is missing")

    return {
        "audit": {"path": str(audit_path), "sha256": base.sha(audit_path)},
        "audit_created_at": audit.get("created_at"),
        "all_four_scenes_complete_outer_001": True,
        "physical_wave_shape": list(EXPECTED_WAVES),
        "scenes": scene_receipts,
        "read_only_observation": {
            "path": str(monitor_path),
            "sha256": base.sha(monitor_path),
            "first_complete_outer_round_observed_at": monitor[
                "first_complete_outer_round_observed_at"
            ],
            "continuous_monitoring_now_enabled": False,
        },
    }


def pg_logic_correspondence_receipt(manifest: dict[str, Any]) -> dict[str, Any]:
    binding = manifest.get("pg_algorithm_c_logic_correspondence") or {}
    audit_path = _verified_artifact(binding, "PG/MySQL Algorithm C logic audit")
    audit = load(audit_path)
    require(audit.get("schema_version") == "mysql_algorithm_c_pg_full_logic_recheck_v6", "PG logic audit schema differs")
    require(audit.get("status") == "PASS_RUNNING_NOT_COMPLETE", "PG logic audit status differs")
    require(audit.get("formal_scope") == "mysql_only", "PG logic audit scope differs")
    require(audit.get("cross_engine_comparison_prohibited") is True, "PG logic audit comparison flag differs")
    require(audit.get("pg_source_hashes_unchanged_from_v3") is True, "PG source hashes changed")

    required_semantics = {
        "acceptance_tiers",
        "accepted_round_target",
        "accepted_update",
        "candidate_admission",
        "convergence",
        "empty_admission",
        "first_valid_miss",
        "full_parallelism",
        "generator_exhaustion",
        "physical_waves",
        "post_search_confirmation",
        "qps_role",
        "second_valid_miss",
        "technical_failure",
        "width_transform",
    }
    semantics = audit.get("semantic_correspondence") or {}
    require(set(semantics) == required_semantics, "PG logic semantic check set differs")
    require(all(row.get("status") == "PASS" for row in semantics.values()), "PG logic semantic check failed")
    require(semantics["candidate_admission"].get("mysql") == "same", "MySQL Top5 admission differs from PG")
    require(semantics["first_valid_miss"].get("mysql") == "same", "MySQL first-miss expansion differs from PG")
    require(semantics["second_valid_miss"].get("mysql") == "same", "MySQL second-miss shrink differs from PG")
    require(semantics["technical_failure"].get("mysql", "").startswith("same"), "MySQL technical-failure credit differs from PG")
    require(semantics["convergence"].get("mysql") == "same inclusive threshold", "MySQL convergence differs from PG")
    require(semantics["physical_waves"].get("mysql") == [[1, 2], [3, 4], [5]], "MySQL physical waves differ")

    completion = audit.get("completion_assessment") or {}
    require(completion.get("accepted_or_total_round_count_is_not_a_stop_condition") is True, "round count is a stop condition")
    require(float(completion.get("normal_stop_requires_width_at_most") or -1) == 0.01, "normal stop width differs")
    regression = audit.get("regression_receipt") or {}
    mysql_suite = regression.get("mysql_current_suite") or {}
    direct_suite = regression.get("mysql_direct_state_machine_scripts") or {}
    require(int(mysql_suite.get("failed") or 0) == 0 and int(mysql_suite.get("passed") or 0) >= 99, "MySQL logic regression receipt differs")
    require(int(direct_suite.get("failed") or 0) == 0 and int(direct_suite.get("passed") or 0) >= 2, "MySQL state-machine receipt differs")

    source_bindings = audit.get("source_bindings") or {}
    required_sources = {
        "mysql_effective_runtime_v58",
        "mysql_effective_runtime_v59",
        "mysql_interval_v3",
        "mysql_outer_policy_v1",
        "mysql_recovery_v2",
        "mysql_rejection_v3",
        "pg_budgeted_controller",
        "pg_policy",
        "pg_search",
    }
    source_receipts = {}
    for name in sorted(required_sources):
        source_path = _verified_artifact(source_bindings.get(name) or {}, f"PG logic source {name}")
        source_receipts[name] = {"path": str(source_path), "sha256": base.sha(source_path)}
    audited_runtime_hashes = sorted({
        source_receipts["mysql_effective_runtime_v58"]["sha256"],
        source_receipts["mysql_effective_runtime_v59"]["sha256"],
    })
    return {
        "path": str(audit_path),
        "sha256": base.sha(audit_path),
        "semantic_checks": sorted(required_semantics),
        "semantic_check_count": len(required_semantics),
        "all_semantic_checks_passed": True,
        "mysql_regression_tests_passed": int(mysql_suite["passed"]),
        "mysql_direct_state_machine_tests_passed": int(direct_suite["passed"]),
        "audited_sources": source_receipts,
        "audited_runtime_sha256": audited_runtime_hashes,
        "historical_progress_snapshot_used_for_completion": False,
    }


def tpch_confirmed_cleanup_recovery_receipt(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    launch = (manifest.get("launches") or {}).get("tpch_sf4_memory") or {}
    require(
        launch.get("tpch_cleanup_readback_after_error") is True,
        "TPC-H cleanup readback is not enabled",
    )
    controller_path = _verified_artifact(
        {
            "path": launch.get("controller"),
            "sha256": launch.get("controller_sha256"),
        },
        "TPC-H confirmed-cleanup controller",
    )
    contract_path = _verified_artifact(
        {
            "path": launch.get("contract"),
            "sha256": launch.get("contract_sha256"),
        },
        "TPC-H confirmed-cleanup contract",
    )
    _verify_sidecar_checksum(contract_path, "TPC-H confirmed-cleanup contract")
    contract = load(contract_path)
    require(
        contract.get("release_version") == "v243-tpch-confirmed-cleanup-recovery",
        "TPC-H confirmed-cleanup release differs",
    )
    require(
        contract.get("tpch_cleanup_readback_after_error") is True,
        "TPC-H contract cleanup readback differs",
    )
    require(
        int(contract.get("tpch_cleanup_attempt_limit") or 0) == 3,
        "TPC-H cleanup attempt limit differs",
    )
    require(
        int(contract.get("tpch_cleanup_lock_wait_timeout_seconds") or 0) == 5,
        "TPC-H cleanup lock-wait timeout differs",
    )
    source_binding = (contract.get("source_bindings") or {}).get(
        "resume_controller"
    ) or {}
    source_path = Path(str(source_binding.get("path") or ""))
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    require(
        source_path.resolve() == controller_path,
        "TPC-H contract controller path differs",
    )
    require(
        source_binding.get("sha256") == base.sha(controller_path),
        "TPC-H contract controller SHA-256 differs",
    )

    run_id = str(launch.get("run_id") or "")
    state_path = HISTORY / run_id / "run_state.json"
    state = load(state_path)
    release = state.get("actual_width_credit_release") or {}
    require(release.get("release_version") == "v243", "TPC-H state release differs")
    require(
        release.get("tpch_cleanup_readback_after_error") is True,
        "TPC-H state cleanup readback differs",
    )
    require(
        Path(str(release.get("source_path") or "")).resolve() == controller_path,
        "TPC-H state cleanup controller path differs",
    )
    require(
        release.get("source_sha256") == base.sha(controller_path),
        "TPC-H state cleanup controller SHA-256 differs",
    )
    return {
        "release_version": contract["release_version"],
        "controller": {"path": str(controller_path), "sha256": base.sha(controller_path)},
        "contract": {"path": str(contract_path), "sha256": base.sha(contract_path)},
        "attempt_limit": 3,
        "lock_wait_timeout_seconds": 5,
        "server_readback_after_client_error": True,
        "incomplete_outer_credit": 0,
    }


def decision_receipt(
    path: Path,
    scene: str,
    index: int,
    native_xp: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    decision = load(path)
    _verify_sidecar_checksum(path, f"{scene}: outer {index} decision")
    require(decision.get("outer_attempt") == index, f"{scene}: outer {index} index differs")
    require(decision.get("formal_evidence_eligible") is True, f"{scene}: outer {index} is ineligible")
    require(
        decision.get("pg_first_accepting_semantic_batch_preserved") is True,
        f"{scene}: outer {index} first-accepting-batch policy differs",
    )
    require(
        decision.get("later_semantic_batches_grant_no_state_credit") is True,
        f"{scene}: outer {index} later batches may grant state credit",
    )

    physical_measurements: list[dict[str, Any]] = []
    physical_waves = []
    for batch, expected_count in enumerate(EXPECTED_WAVES, 1):
        batch_path = path.parent / f"top5_batch_{batch}" / "batch_result.json"
        batch_result = load(batch_path)
        _verify_sidecar_checksum(
            batch_path,
            f"{scene}: outer {index} physical batch {batch}",
        )
        require(
            batch_result.get("status") == "PASS",
            f"{scene}: outer {index} physical batch {batch} failed",
        )
        require(
            not batch_result.get("failures"),
            f"{scene}: outer {index} physical batch {batch} has technical failures",
        )
        raw_measurements = batch_result.get("measurements") or {}
        measurements = (
            list(raw_measurements.values())
            if isinstance(raw_measurements, dict)
            else list(raw_measurements)
        )
        require(
            len(measurements) == expected_count,
            f"{scene}: outer {index} physical wave {batch} size differs",
        )
        for measurement in measurements:
            coverage_receipt(scene, measurement, native_xp)
        physical_measurements.extend(measurements)
        physical_waves.append({
            "batch_index": batch,
            "measurement_count": len(measurements),
            "status": "PASS",
            "failures": 0,
            "path": str(batch_path),
            "sha256": base.sha(batch_path),
        })

    coverage = []
    for row in decision.get("evaluations") or []:
        if row.get("semantic_state_eligible", True) is not True:
            continue
        coverage.append(coverage_receipt(scene, row, native_xp))
    require(coverage, f"{scene}: outer {index} has no semantic evaluation")

    def measurement_identity(row: dict[str, Any]) -> str:
        return json.dumps(
            {
                "worker_id": row.get("worker_id"),
                "params": row.get("params"),
                "rank": row.get("rank"),
                "outer_primary_qps": row.get("outer_primary_qps"),
                "resource_means": row.get("resource_means"),
                "three_level_fingerprint": row.get("three_level_fingerprint"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    physical_identities = Counter(
        measurement_identity(row) for row in physical_measurements
    )
    semantic_identities = Counter(
        measurement_identity(row)
        for row in decision.get("evaluations") or []
        if row.get("semantic_state_eligible", True) is True
    )
    require(
        all(semantic_identities[key] <= physical_identities[key] for key in semantic_identities),
        f"{scene}: outer {index} semantic evaluation lacks a physical measurement",
    )
    convergence = decision.get("interval_convergence") or {}
    gates = [
        dynamic_gate_receipt(
            path.parent / f"top5_batch_{batch}_dynamic_noninterference_gate.json",
            scene,
            index,
            batch,
        )
        for batch in range(1, 4)
    ]
    return ({
        "outer_attempt": index,
        "accepted": decision.get("accepted"),
        "accepted_rounds": decision.get("accepted_rounds"),
        "rejected_outer_attempts": decision.get("rejected_outer_attempts"),
        "accepted_state_credit_increment": decision.get("accepted_state_credit_increment"),
        "post_target_stability_confirmation": decision.get("post_target_stability_confirmation"),
        "rejection_cycle": decision.get("rejection_cycle"),
        "physical_wave_shape": list(EXPECTED_WAVES),
        "physical_measurement_count": len(physical_measurements),
        "physical_full_workload_coverage_verified": True,
        "physical_waves": physical_waves,
        "semantic_measurement_count": len(coverage),
        "semantic_evaluations_are_physical_measurements": True,
        "normalized_width": convergence.get("normalized_width"),
        "converged": convergence.get("converged"),
        "interval_convergence": convergence,
        "dynamic_noninterference_gates": gates,
        "path": str(path),
        "sha256": base.sha(path),
    }, len(coverage))


def validate_decision_trajectory(
    decisions: list[dict[str, Any]],
    expected_accepted: int,
    expected_rejected: int,
    scene: str,
    continuation_boundaries: set[int] | None = None,
) -> dict[str, Any]:
    continuation_boundaries = set(continuation_boundaries or set())
    accepted_count = 0
    state_accepted_count = 0
    rejected_count = 0
    consecutive_rejections = 0
    first_miss_expansions = 0
    second_miss_shrinks = 0
    zero_credit_legacy_confirmations = 0
    pending_legacy_confirmation_credits = 0
    restored_legacy_confirmation_credits = 0
    legacy_confirmation_credit_restorations = 0
    last_confirmation_ordinal = 0
    for expected_outer, decision in enumerate(decisions, 1):
        require(decision.get("outer_attempt") == expected_outer, f"{scene}: decision trajectory has an index gap")
        accepted = decision.get("accepted")
        require(isinstance(accepted, bool), f"{scene}: outer {expected_outer} accepted flag is not Boolean")
        cycle = decision.get("rejection_cycle") or {}
        require(cycle.get("policy_id") == "mysql_c_pg_half_interval_two_reject_shrink_v3", f"{scene}: outer {expected_outer} rejection policy differs")
        require(cycle.get("accepted") is accepted, f"{scene}: outer {expected_outer} cycle acceptance differs")
        require(int(cycle.get("consecutive_rejections_before") or 0) == consecutive_rejections, f"{scene}: outer {expected_outer} miss counter before differs")

        if accepted:
            accepted_count += 1
            credit_increment = int(decision.get("accepted_state_credit_increment") or 0)
            confirmation = decision.get("post_target_stability_confirmation")
            if credit_increment == 0:
                require(isinstance(confirmation, dict), f"{scene}: outer {expected_outer} zero-credit acceptance lacks legacy confirmation proof")
                require(confirmation.get("kind") == "post_target_stability_confirmation", f"{scene}: outer {expected_outer} legacy confirmation kind differs")
                require(int(confirmation.get("accepted_rounds_before") or 0) == state_accepted_count, f"{scene}: outer {expected_outer} legacy confirmation counter before differs")
                require(int(confirmation.get("accepted_rounds_after") or 0) == state_accepted_count, f"{scene}: outer {expected_outer} legacy confirmation counter after differs")
                require(int(confirmation.get("accepted_state_credit_increment") or 0) == 0, f"{scene}: outer {expected_outer} legacy confirmation received state credit")
                confirmation_ordinal = int(confirmation.get("confirmation_ordinal") or 0)
                require(confirmation_ordinal == last_confirmation_ordinal + 1, f"{scene}: outer {expected_outer} legacy confirmation ordinal differs")
                require(int(decision.get("accepted_rounds") or 0) == state_accepted_count, f"{scene}: outer {expected_outer} zero-credit confirmation changed accepted counter")
                # The retired controller recorded cycle credit as one while deliberately
                # withholding state credit.  The confirmation object above is the
                # authoritative proof of that legacy-only mismatch.
                require(int(cycle.get("accepted_round_credit") or 0) == 1, f"{scene}: outer {expected_outer} legacy cycle credit differs")
                last_confirmation_ordinal = confirmation_ordinal
                zero_credit_legacy_confirmations += 1
                pending_legacy_confirmation_credits += 1
            else:
                require(credit_increment == 1, f"{scene}: outer {expected_outer} accepted credit differs")
                require(confirmation is None, f"{scene}: outer {expected_outer} credited acceptance also claims legacy confirmation")
                require(int(cycle.get("accepted_round_credit") or 0) == 1, f"{scene}: outer {expected_outer} cycle credit differs")
                next_state_accepted = int(decision.get("accepted_rounds") or 0)
                ordinary_next = state_accepted_count + 1
                if next_state_accepted != ordinary_next:
                    require(pending_legacy_confirmation_credits > 0, f"{scene}: outer {expected_outer} unexplained accepted-counter jump")
                    require(expected_outer in continuation_boundaries, f"{scene}: outer {expected_outer} legacy confirmation credits restored outside a continuation boundary")
                    require(next_state_accepted == ordinary_next + pending_legacy_confirmation_credits, f"{scene}: outer {expected_outer} legacy confirmation credit restoration differs")
                    restored_legacy_confirmation_credits += pending_legacy_confirmation_credits
                    pending_legacy_confirmation_credits = 0
                    legacy_confirmation_credit_restorations += 1
                state_accepted_count = next_state_accepted
            require(cycle.get("forced_incumbent_shrink") is False, f"{scene}: outer {expected_outer} accepted round forced a shrink")
            require(int(cycle.get("consecutive_rejections_after") or 0) == 0, f"{scene}: outer {expected_outer} did not reset miss counter")
            consecutive_rejections = 0
        else:
            rejected_count += 1
            require(decision.get("post_target_stability_confirmation") is None, f"{scene}: outer {expected_outer} miss claims legacy confirmation")
            require(int(decision.get("accepted_state_credit_increment") or 0) == 0, f"{scene}: outer {expected_outer} miss received accepted credit")
            require(int(cycle.get("accepted_round_credit") or 0) == 0, f"{scene}: outer {expected_outer} miss cycle received credit")
            require(cycle.get("incumbent_changed_by_cycle") is False, f"{scene}: outer {expected_outer} miss changed incumbent")
            require(cycle.get("next_outer_expansion") == "predicted_points_and_plan_tree", f"{scene}: outer {expected_outer} expansion policy differs")
            if consecutive_rejections == 0:
                require(cycle.get("forced_incumbent_shrink") is False, f"{scene}: outer {expected_outer} first miss shrank intervals")
                require(int(cycle.get("consecutive_rejections_after") or 0) == 1, f"{scene}: outer {expected_outer} first miss counter differs")
                consecutive_rejections = 1
                first_miss_expansions += 1
            else:
                require(consecutive_rejections == 1, f"{scene}: outer {expected_outer} miss counter exceeded one")
                require(cycle.get("forced_incumbent_shrink") is True, f"{scene}: outer {expected_outer} second miss did not shrink")
                require(int(cycle.get("consecutive_rejections_after") or 0) == 0, f"{scene}: outer {expected_outer} second miss did not reset counter")
                changes = cycle.get("range_changes") or {}
                require(changes, f"{scene}: outer {expected_outer} second miss lacks range changes")
                require(
                    all(abs(float(row.get("log_width_ratio")) - 0.5) <= 1e-12 for row in changes.values()),
                    f"{scene}: outer {expected_outer} second-miss shrink is not one half",
                )
                consecutive_rejections = 0
                second_miss_shrinks += 1

        require(int(decision.get("accepted_rounds") or 0) == state_accepted_count, f"{scene}: outer {expected_outer} accepted counter differs")
        require(int(decision.get("rejected_outer_attempts") or 0) == rejected_count, f"{scene}: outer {expected_outer} rejected counter differs")
        convergence = decision.get("interval_convergence") or {}
        require(convergence.get("contract") == "mysql_c_pg_half_interval_two_reject_shrink_v3", f"{scene}: outer {expected_outer} convergence contract differs")
        require(convergence.get("accepted_rounds_are_not_inferred_from_forced_shrinks") is True, f"{scene}: outer {expected_outer} shrink inferred acceptance")
        require(convergence.get("post_search_stability_confirmation") is False, f"{scene}: outer {expected_outer} has a post-search confirmation phase")
        require(float(convergence.get("terminal_normalized_width") or -1) == TERMINAL_WIDTH, f"{scene}: outer {expected_outer} terminal width differs")
        if convergence.get("converged") is True:
            require(float(convergence.get("normalized_width")) <= TERMINAL_WIDTH, f"{scene}: outer {expected_outer} converged above threshold")

    require(accepted_count == expected_accepted, f"{scene}: terminal accepted counter differs from decisions")
    require(state_accepted_count == expected_accepted, f"{scene}: terminal accepted state counter differs from decisions")
    require(rejected_count == expected_rejected, f"{scene}: terminal rejected counter differs from decisions")
    require(accepted_count + rejected_count == len(decisions), f"{scene}: terminal decision accounting differs")
    require(pending_legacy_confirmation_credits == 0, f"{scene}: legacy confirmation credits remain unrestored")
    require(restored_legacy_confirmation_credits == zero_credit_legacy_confirmations, f"{scene}: legacy confirmation restoration accounting differs")
    return {
        "decision_count": len(decisions),
        "accepted_decisions": accepted_count,
        "rejected_decisions": rejected_count,
        "first_valid_miss_expansions": first_miss_expansions,
        "second_valid_miss_half_shrinks": second_miss_shrinks,
        "zero_credit_legacy_confirmations": zero_credit_legacy_confirmations,
        "legacy_confirmation_credit_restorations": legacy_confirmation_credit_restorations,
        "restored_legacy_confirmation_credits": restored_legacy_confirmation_credits,
        "terminal_consecutive_rejections": consecutive_rejections,
        "accepted_and_rejected_counters_match_run_state": True,
    }


def independent_terminal_convergence_receipt(
    state: dict[str, Any],
    final_decision: dict[str, Any],
    scene: str,
) -> dict[str, Any]:
    rule = state.get("convergence_rule") or {}
    require(
        rule.get("metric")
        == "maximum_log_domain_width_relative_to_frozen_initial",
        f"{scene}: convergence metric differs",
    )
    require(float(rule.get("maximum_interval_ratio") or -1) == TERMINAL_WIDTH, f"{scene}: state terminal interval differs")
    require(float(rule.get("maximum_relative_width") or -1) == TERMINAL_WIDTH, f"{scene}: state relative width differs")
    require(rule.get("all_active_parameters_must_satisfy_interval") is True, f"{scene}: convergence does not cover every active parameter")
    require(rule.get("post_search_stability_confirmation") is False, f"{scene}: convergence adds a stability-confirmation phase")
    require(int(rule.get("required_consecutive_stable_accepted_rounds") or 0) == 0, f"{scene}: convergence requires extra accepted rounds")

    table_path = PARAMETER_TABLE_ROOT / PARAMETER_TABLES[scene]
    _verify_sidecar_checksum(table_path, f"{scene}: frozen initial parameter table")
    table = load(table_path)
    parameter_space = table.get("parameter_space") or {}
    active_params = [str(name) for name in parameter_space.get("active_params") or []]
    initial_ranges = parameter_space.get("ranges") or {}
    current_ranges = (state.get("ranges_by_scene") or {}).get(scene) or {}
    require(active_params, f"{scene}: active parameter set is empty")
    require(set(active_params) == set(initial_ranges), f"{scene}: frozen initial ranges differ from active parameters")
    require(set(active_params) == set(current_ranges), f"{scene}: terminal ranges differ from active parameters")

    ratios: dict[str, float] = {}
    for name in active_params:
        initial = initial_ranges[name]
        current = current_ranges[name]
        initial_lower = float(initial["lower"])
        initial_upper = float(initial["upper"])
        current_lower = float(current["lower"])
        current_upper = float(current["upper"])
        require(0 < initial_lower <= initial_upper, f"{scene}.{name}: invalid frozen initial range")
        require(0 < current_lower <= current_upper, f"{scene}.{name}: invalid terminal range")
        initial_span = math.log(initial_upper) - math.log(initial_lower)
        current_span = math.log(current_upper) - math.log(current_lower)
        ratio = 0.0 if initial_span <= 0 and current_span <= 0 else current_span / initial_span
        require(math.isfinite(ratio) and ratio >= 0, f"{scene}.{name}: invalid normalized log width")
        require(ratio <= TERMINAL_WIDTH + 1e-12, f"{scene}.{name}: terminal normalized log width exceeds 0.01")
        ratios[name] = ratio

    recomputed = max(ratios.values(), default=0.0)
    recorded = float(final_decision.get("normalized_width"))
    require(
        abs(recomputed - recorded) <= 1e-12,
        f"{scene}: recorded terminal width differs from independent recomputation",
    )
    require(recomputed <= TERMINAL_WIDTH, f"{scene}: independently recomputed terminal width exceeds 0.01")
    return {
        "metric": rule["metric"],
        "frozen_initial_parameter_table": {
            "path": str(table_path),
            "sha256": base.sha(table_path),
        },
        "active_parameters": active_params,
        "normalized_log_width_by_parameter": ratios,
        "maximum_normalized_log_width": recomputed,
        "recorded_normalized_width": recorded,
        "terminal_normalized_width": TERMINAL_WIDTH,
        "all_active_parameters_at_most_0_01": True,
        "post_search_stability_confirmation": False,
    }


def terminal_baseline_binding_receipt(
    state: dict[str, Any],
    terminal_run: Path,
    scene: str,
) -> dict[str, Any]:
    expected_path = (terminal_run / "paper_baseline_resource_gate.json").resolve()
    binding = state.get("paper_baseline_resource_gate") or {}
    bound_path = _verified_artifact(binding, f"{scene}: terminal paper baseline gate")
    require(bound_path == expected_path, f"{scene}: terminal baseline path differs")
    _verify_sidecar_checksum(bound_path, f"{scene}: terminal paper baseline gate")
    receipt = base.baseline_receipt(bound_path, scene)
    require(binding.get("status") == "PASS", f"{scene}: terminal baseline binding status differs")
    return {
        **receipt,
        "terminal_state_binding_verified": True,
        "checksum_sidecar_verified": True,
    }


def scene_receipt(
    manifest: dict[str, Any],
    scene: str,
    audited_runtime_hashes: set[str] | None = None,
) -> dict[str, Any]:
    launch = manifest["launches"][scene]
    terminal_run = HISTORY / str(launch["run_id"])
    state_path = terminal_run / "run_state.json"
    state = load(state_path)
    require(state.get("formal_scope") == "mysql_only", f"{scene}: formal scope differs")
    require(state.get("cross_engine_comparison_prohibited") is True, f"{scene}: comparison flag differs")
    require(state.get("formal_evidence_eligible") is True, f"{scene}: formal evidence is ineligible")
    require(state.get("status") == "COMPLETE", f"{scene}: state is not COMPLETE")
    require(state.get("stage") == "FINISHED", f"{scene}: terminal stage differs")
    require(
        state.get("completion_reason") == "all_active_log_domain_widths_reached_1pct",
        f"{scene}: completion reason differs",
    )
    require((state.get("converged") or {}).get(scene) is True, f"{scene}: convergence flag is false")
    require(state.get("post_search_stability_confirmation") is False, f"{scene}: unexpected stability phase")
    require(not state.get("error"), f"{scene}: terminal error is present")
    baseline_binding = terminal_baseline_binding_receipt(
        state,
        terminal_run,
        scene,
    )
    controller_source = Path(str(state.get("controller_source_path") or "")).resolve()
    require(controller_source.is_file(), f"{scene}: controller source is missing")
    controller_sha = base.sha(controller_source)
    require(state.get("controller_source_sha256") == controller_sha, f"{scene}: controller source SHA-256 differs")
    if audited_runtime_hashes is not None:
        require(controller_sha in audited_runtime_hashes, f"{scene}: controller runtime was not covered by the PG logic audit")
    native_xp = native_xp_contract_receipt(state, scene)
    tpch_sf4 = tpch_sf4_contract_receipt(state, scene)

    outer_attempt = int(state.get("outer_attempt") or 0)
    lineage = (manifest.get("decision_lineage") or {}).get(scene) or []
    paths: dict[int, Path] = {}
    lineage_receipts = []
    for segment in lineage:
        start = int(segment["from_outer"])
        end = outer_attempt if segment["through_outer"] == "terminal" else int(segment["through_outer"])
        run = HISTORY / str(segment["run_id"])
        require(1 <= start <= end <= outer_attempt, f"{scene}: invalid decision lineage range")
        for index in range(start, end + 1):
            require(index not in paths, f"{scene}: duplicate outer {index} lineage")
            paths[index] = run / f"outer_{index:03d}" / f"{scene}.decision.json"
        lineage_receipts.append({
            "run_id": segment["run_id"], "from_outer": start,
            "through_outer": end,
        })
    require(set(paths) == set(range(1, outer_attempt + 1)), f"{scene}: decision lineage has gaps")

    decisions = []
    semantic_measurement_count = 0
    physical_measurement_count = 0
    for index in range(1, outer_attempt + 1):
        receipt, count = decision_receipt(paths[index], scene, index, native_xp)
        decisions.append(receipt)
        semantic_measurement_count += count
        physical_measurement_count += int(receipt["physical_measurement_count"])
    state_accepted = int((state.get("accepted_rounds") or {}).get(scene) or 0)
    state_rejected = int((state.get("rejected_outer_attempts") or {}).get(scene) or 0)
    trajectory = validate_decision_trajectory(
        decisions,
        state_accepted,
        state_rejected,
        scene,
        {int(segment["from_outer"]) for segment in lineage if int(segment["from_outer"]) > 1},
    )
    final = decisions[-1]
    require(final["converged"] is True, f"{scene}: final decision is not converged")
    require(final["normalized_width"] is not None, f"{scene}: final width missing")
    require(float(final["normalized_width"]) <= TERMINAL_WIDTH, f"{scene}: final width exceeds 0.01")
    independent_convergence = independent_terminal_convergence_receipt(
        state,
        final,
        scene,
    )
    require(semantic_measurement_count > 0, f"{scene}: no complete semantic measurement")
    require(
        physical_measurement_count == 5 * outer_attempt,
        f"{scene}: physical Top-5 measurement accounting differs",
    )
    return {
        "state": {
            "path": str(state_path), "sha256": base.sha(state_path),
            "status": state["status"], "stage": state["stage"],
            "completion_reason": state["completion_reason"],
            "outer_attempt": outer_attempt,
            "accepted_rounds": (state.get("accepted_rounds") or {}).get(scene),
            "rejected_outer_attempts": (state.get("rejected_outer_attempts") or {}).get(scene),
            "controller_source": {"path": str(controller_source), "sha256": controller_sha},
        },
        "decision_lineage": lineage_receipts,
        "paper_baseline_resource_gate": baseline_binding,
        "decision_count": len(decisions),
        "decision_trajectory": trajectory,
        "semantic_measurement_count": semantic_measurement_count,
        "physical_measurement_count": physical_measurement_count,
        "all_physical_wave_shapes": list(EXPECTED_WAVES),
        "all_physical_measurements_have_full_workload_coverage": True,
        "final_normalized_width": float(final["normalized_width"]),
        "terminal_normalized_width": TERMINAL_WIDTH,
        "independent_terminal_convergence": independent_convergence,
        "native_xp_contract": native_xp,
        "tpch_sf4_contract": tpch_sf4,
        "decisions": decisions,
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("usage: writer CONTINUED_CAMPAIGN_PREFIX")
    prefix = sys.argv[1]
    campaign = HISTORY / prefix
    output = campaign / "final_completion_audit.json"
    require(not output.exists() and not output.is_symlink(), f"fresh audit required: {output}")
    manifest_path = campaign / "launch_manifest.json"
    manifest = load(manifest_path)
    scenes_expected = set(base.SCENES.values())
    require(manifest.get("formal_scope") == "mysql_only", "launch scope differs")
    require(manifest.get("cross_engine_comparison_prohibited") is True, "comparison flag differs")
    require(manifest.get("original_all_four_scenes_started_together") is True, "original concurrent start differs")
    require(manifest.get("sibling_scene_wait") is False, "sibling-scene wait is enabled")
    require(set(manifest.get("launches") or {}) == scenes_expected, "scene set differs")
    monitoring_policy = manifest.get("monitoring_policy") or {}
    require(
        monitoring_policy.get("continuous_monitoring_enabled") is False,
        "continuous monitoring is enabled",
    )
    require(
        monitoring_policy.get("future_status_checks") == "manual_one_off_only",
        "manual one-off monitoring policy differs",
    )

    checkpoint_path = Path(str(manifest["continuation"]["checkpoint"])).resolve()
    checkpoint_schema = load(checkpoint_path).get("schema_version")
    if checkpoint_schema == checkpoint_v18.SCHEMA:
        checkpoint = checkpoint_v18.verify_checkpoint(checkpoint_path)
    else:
        checkpoint = checkpoint_v17.verify_checkpoint(checkpoint_path)
    require(checkpoint.get("scene") == "tpch_sf4_memory", "continuation checkpoint scene differs")
    require(int(checkpoint.get("completed_outer_attempts") or 0) == 10, "checkpoint boundary differs")
    require(
        int(checkpoint["reconstructed_state"]["accepted_rounds"]["tpch_sf4_memory"]) == 9,
        "TPC-H accepted decision count differs",
    )
    require(
        int(checkpoint["reconstructed_state"]["rejected_outer_attempts"]["tpch_sf4_memory"]) == 1,
        "TPC-H rejected decision count differs",
    )
    require(
        checkpoint["reconstructed_state"].get("post_target_stability_confirmation_counts") == {},
        "TPC-H legacy confirmation counter was not retired",
    )
    quarantine_path = Path(str(manifest["continuation"]["quarantine"])).resolve()
    quarantine = load(quarantine_path)
    require(quarantine.get("status") == "PASS", "TPC-H timeout quarantine failed")
    require(quarantine.get("formal_state_mutation") is False, "TPC-H timeout quarantine mutated state")
    require(quarantine.get("accepted_rounds_mutation") is False, "TPC-H timeout quarantine credited acceptance")
    require(quarantine.get("rejected_rounds_mutation") is False, "TPC-H timeout quarantine credited rejection")
    require(
        int((quarantine.get("permanent_invalid_counts_by_scene") or {}).get("tpch_sf4_memory", -1)) == 17,
        "TPC-H timeout quarantine candidate count differs",
    )
    require(
        int((quarantine.get("soft_replayed_counts_by_scene") or {}).get("tpch_sf4_memory", -1)) == 8,
        "TPC-H soft replay quarantine candidate count differs",
    )
    quarantine_checkpoint_path = Path(
        str(manifest["continuation"].get("quarantine_checkpoint") or checkpoint_path)
    ).resolve()
    require(
        base.sha(quarantine_checkpoint_path)
        == manifest["continuation"].get(
            "quarantine_checkpoint_sha256", base.sha(quarantine_checkpoint_path)
        ),
        "TPC-H timeout quarantine checkpoint manifest binding differs",
    )
    require(
        (quarantine.get("evidence") or {}).get("checkpoint", {}).get("sha256")
        == base.sha(quarantine_checkpoint_path),
        "TPC-H timeout quarantine checkpoint binding differs",
    )
    continuation_checkpoints = {
        "tpch_sf4_memory": {
            "path": str(checkpoint_path), "sha256": base.sha(checkpoint_path),
            "completed_outer_attempts": 10,
            "accepted_decisions_preserved": 9,
            "rejected_decisions_preserved": 1,
            "timeout_quarantine": {
                "path": str(quarantine_path),
                "sha256": base.sha(quarantine_path),
                "checkpoint": {
                    "path": str(quarantine_checkpoint_path),
                    "sha256": base.sha(quarantine_checkpoint_path),
                },
                "permanent_invalid_candidates": 17,
                "soft_replayed_candidates": 8,
                "formal_state_mutation": False,
            },
        }
    }
    for scene, continuation in dict(manifest.get("additional_continuations") or {}).items():
        path = Path(str(continuation["checkpoint"])).resolve()
        if scene == "hybench_xp_cpu":
            value = checkpoint_v18.verify_checkpoint(path)
        elif scene == "job_memory":
            value = checkpoint_v16.verify_checkpoint(path)
        else:
            value = checkpoint_v12.verify_checkpoint(path)
        require(value.get("scene") == scene, f"{scene}: continuation checkpoint scene differs")
        require(
            int(value.get("completed_outer_attempts") or 0)
            == int(continuation["completed_outer_attempts_preserved"]),
            f"{scene}: continuation checkpoint boundary differs",
        )
        if scene in {"job_memory", "hybench_xp_cpu"}:
            require(
                int(value["reconstructed_state"]["accepted_rounds"][scene])
                == int(continuation["actual_accepted_decisions_restored"]),
                f"{scene}: actual accepted decision count differs",
            )
            require(
                int(value["reconstructed_state"]["rejected_outer_attempts"][scene])
                == int(continuation["rejected_decisions_preserved"]),
                f"{scene}: rejected decision count differs",
            )
            require(
                value["reconstructed_state"].get(
                    "post_target_stability_confirmation_counts"
                ) == {},
                f"{scene}: legacy confirmation counter was not retired",
            )
        continuation_checkpoints[scene] = {
            "path": str(path), "sha256": base.sha(path),
            "completed_outer_attempts": int(value["completed_outer_attempts"]),
        }
        if continuation.get("quarantine"):
            continuation_quarantine_path = _verified_artifact(
                {
                    "path": continuation["quarantine"],
                    "sha256": continuation.get("quarantine_sha256"),
                },
                f"{scene}: continuation quarantine",
            )
            continuation_quarantine = load(continuation_quarantine_path)
            _verify_sidecar_checksum(
                continuation_quarantine_path,
                f"{scene}: continuation quarantine",
            )
            require(
                continuation_quarantine.get("status") == "PASS",
                f"{scene}: continuation quarantine failed",
            )
            require(
                continuation_quarantine.get("formal_state_mutation") is False,
                f"{scene}: continuation quarantine mutated formal state",
            )
            require(
                continuation_quarantine.get("accepted_rounds_mutation") is False,
                f"{scene}: continuation quarantine credited acceptance",
            )
            require(
                continuation_quarantine.get("rejected_rounds_mutation") is False,
                f"{scene}: continuation quarantine credited rejection",
            )
            require(
                (continuation_quarantine.get("evidence") or {}).get(
                    "checkpoint", {}
                ).get("sha256")
                == base.sha(path),
                f"{scene}: continuation quarantine checkpoint binding differs",
            )
            continuation_checkpoints[scene]["quarantine"] = {
                "path": str(continuation_quarantine_path),
                "sha256": base.sha(continuation_quarantine_path),
                "formal_state_mutation": False,
                "accepted_rounds_mutation": False,
                "rejected_rounds_mutation": False,
            }

    failed_recoveries = failed_recovery_attempts_receipt(manifest)
    original_launch = original_full_parallel_launch_receipt(manifest)
    stable_round = stable_round_receipt(manifest)
    pg_logic = pg_logic_correspondence_receipt(manifest)
    tpch_cleanup_recovery = tpch_confirmed_cleanup_recovery_receipt(manifest)

    baselines = {}
    scenes = {}
    for scene in sorted(scenes_expected):
        run = HISTORY / str(manifest["launches"][scene]["run_id"])
        baselines[scene] = base.baseline_receipt(run / "paper_baseline_resource_gate.json", scene)
        scenes[scene] = scene_receipt(
            manifest,
            scene,
            set(pg_logic["audited_runtime_sha256"]),
        )
        require(
            scenes[scene]["paper_baseline_resource_gate"]["sha256"]
            == baselines[scene]["sha256"],
            f"{scene}: campaign baseline receipt differs from terminal state binding",
        )

    terminal_snapshot = controller_process_snapshot(manifest)
    snapshot_path = campaign / "manual_terminal_snapshot.json"
    snapshot_receipt = write_terminal_snapshot_atomic(
        snapshot_path,
        terminal_snapshot,
    )

    document = {
        "schema_version": "mysql_algorithm_c_continued_final_completion_audit_v2",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "PASS_COMPLETE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_prefix": prefix,
        "launch_manifest": {"path": str(manifest_path), "sha256": base.sha(manifest_path)},
        "continuation_checkpoints": continuation_checkpoints,
        "failed_recovery_attempts": failed_recoveries,
        "original_full_parallel_launch": original_launch,
        "paper_baseline_resource_gates": baselines,
        "stable_complete_outer_round_observation": stable_round,
        "pg_algorithm_c_logic_correspondence": pg_logic,
        "tpch_confirmed_cleanup_recovery": tpch_cleanup_recovery,
        "scenes": scenes,
        "all_four_scenes_complete": True,
        "all_final_normalized_widths_at_most_0_01": True,
        "full_workload_coverage_verified_for_semantic_measurements": True,
        "full_workload_coverage_verified_for_all_physical_measurements": True,
        "all_outer_round_physical_wave_shapes": list(EXPECTED_WAVES),
        "terminal_observation": {
            "mode": "manual_one_off_only",
            "continuous_polling": False,
            "replaced_unsealed_prior_snapshot": snapshot_receipt[
                "replaced_unsealed_prior_snapshot"
            ],
            "snapshot": {
                "path": snapshot_receipt["path"],
                "sha256": snapshot_receipt["sha256"],
            },
        },
        "postgresql_process_control_or_stop_performed": False,
    }
    receipt = seal_final_audit_atomic(output, document)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
