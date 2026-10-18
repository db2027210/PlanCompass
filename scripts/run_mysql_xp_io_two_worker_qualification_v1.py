#!/usr/bin/env python3
"""Counterbalanced two-worker qualification for the corrected XP/I/O plane."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import fmean
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v24 as coordination
from scripts import mysql_single_scene_measurement_v2 as single_measurement
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v7 as reset8
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as runtime_config


DEFAULT_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_xp_io_two_worker_qualification_20260903_v6/"
    "qualification_contract.json"
)
C49_ADAPTER = ROOT / (
    "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260829_v3/"
    "algorithm_c_measurement_adapter_contract.json"
)


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
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
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


def host_swap_inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) >= 5:
            rows.append({
                "filename": fields[0], "type": fields[1],
                "size_kib": int(fields[2]), "used_kib": int(fields[3]),
                "priority": int(fields[4]),
            })
    return rows


def validate_contract(path: Path) -> dict[str, Any]:
    contract = load(path)
    if (
        contract.get("schema_version")
        != "mysql_xp_io_two_worker_qualification_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "hybench_xp_io"
        or contract.get("purpose") != "environment_qualification_only"
        or contract.get("algorithm_search_reexecuted") is not False
        or contract.get("accepted_state_credit_increment") != 0
        or contract.get("worker_ids") != ["xp_io_w1", "xp_io_w2"]
        or contract.get("replicas_per_point") != 2
        or len(contract.get("counterbalanced_wave_assignments") or []) != 4
    ):
        raise RuntimeError("XP/I/O qualification contract differs")
    for group in ("source_bindings", "source_result_evidence"):
        for binding in contract[group].values():
            target = ROOT / binding["path"]
            if sha(target) != binding["sha256"]:
                raise RuntimeError(f"bound source differs: {target}")
    return contract


def metric_row(row: Mapping[str, Any]) -> dict[str, Any]:
    workload = dict(row.get("workload_result") or {})
    metrics = dict(workload.get("formal_metrics") or {})
    coverage = dict(workload.get("workload_coverage") or {})
    resource = dict(row.get("resource_summary") or {})
    measurement_seconds = float(metrics.get("measurement_seconds") or math.inf)
    xap_qps = float(metrics.get("xap_qps") or 0.0)
    xtp_tps = float(metrics.get("xtp_tps") or 0.0)
    checks = {
        "hard_gate_passed": row.get("hard_gate_passed") is True,
        "coverage_complete": coverage.get("coverage_complete") is True,
        "all_iq_present": coverage.get("completed_iq") == [f"IQ{i}" for i in range(1, 8)],
        "all_at_present": coverage.get("completed_at") == [f"AT{i}" for i in range(1, 7)],
        "xap_qps_positive": xap_qps > 0.0,
        "xtp_tps_positive": xtp_tps > 0.0,
        "measurement_within_900_seconds": 0.0 < measurement_seconds <= 900.0,
        "io_positive": float(row["resource_means"]["io"]) > 0.0,
        "device_correct": resource.get("device_major_minor") == "259:6",
        "resource_stable": (row.get("resource_stability") or {}).get("stable") is True,
    }
    if not all(checks.values()):
        raise RuntimeError(
            f"{row.get('worker_id')} qualification measurement failed: "
            f"{[name for name, passed in checks.items() if not passed]}"
        )
    return {
        **copy.deepcopy(dict(row)),
        "qualification_checks": checks,
        "primary_client_xap_qps": xap_qps,
        "client_xtp_tps": xtp_tps,
        "server_questions_qps_audit": float(row.get("db_qps") or 0.0),
        "measurement_seconds": measurement_seconds,
        "right_censored": {
            "xap": int(metrics.get("primary_right_censored_count") or 0),
            "xtp": int(metrics.get("xtp_right_censored_count") or 0),
        },
    }


def validate_live_worker_resources(
    worker: Mapping[str, Any], expected_device: str,
) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", str(worker["container"])],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=60,
    )
    inspected = json.loads(completed.stdout)[0]
    host = dict(inspected.get("HostConfig") or {})
    state = dict(inspected.get("State") or {})
    blkio = dict(worker.get("blkio_authority") or {})
    kernel = dict(worker.get("kernel_cgroup_resources") or {})
    memory = int(host.get("Memory") or 0)
    memory_swap = int(host.get("MemorySwap") or 0)
    memory_swappiness = host.get("MemorySwappiness")
    checks = {
        "running": state.get("Running") is True,
        "docker_memory_matches_freeze": memory == int(worker["memory_bytes"]),
        "docker_memory_swap_request_recorded": memory_swap in (-1, memory),
        "container_anonymous_swap_disabled": memory_swappiness == 0,
        "kernel_cgroup_readback_present": bool(kernel),
        "blkio_device_correct": blkio.get("device_major_minor") == expected_device,
        "blkio_read_cap_correct": int(blkio.get("read_bps") or 0) == int(worker["io_read_bps"]),
        "blkio_write_cap_correct": int(blkio.get("write_bps") or 0) == int(worker["io_write_bps"]),
    }
    if not all(checks.values()):
        raise RuntimeError(
            f"{worker.get('worker_id')} live resource readback failed: "
            f"{[name for name, passed in checks.items() if not passed]}"
        )
    return {
        "checks": checks,
        "container_id": inspected.get("Id"),
        "docker_memory_bytes": memory,
        "docker_memory_swap_bytes": memory_swap,
        "docker_memory_swappiness": memory_swappiness,
        "effective_container_swap_policy": "anonymous_swap_disabled_swappiness_0",
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"fresh output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    state_path = output / "run_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_xp_io_two_worker_qualification_state_v1",
        "run_id": args.run_id,
        "status": "STARTING",
        "stage": "VALIDATING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "completed_waves": 0,
    }
    atomic(state_path, state)
    try:
        contract = validate_contract(args.contract.resolve())
        freeze_path = ROOT / contract["resource_freeze"]["path"]
        if sha(freeze_path) != contract["resource_freeze"]["sha256"]:
            raise RuntimeError("resource freeze differs")
        freeze = load(freeze_path)
        authority = contract["authority"]
        prepared_path = Path(authority["prepared_path"])
        preflight_path = Path(authority["preflight_path"])
        accounts_path = Path(authority["accounts_path"])
        for name, path in (
            ("prepared", prepared_path), ("preflight", preflight_path),
            ("accounts", accounts_path),
        ):
            if sha(path) != authority[f"{name}_sha256"]:
                raise RuntimeError(f"authority binding differs: {path}")
        prepared = load(prepared_path)
        workers = {
            str(row["worker_id"]): copy.deepcopy(dict(row))
            for row in prepared["workers"]
            if row.get("scene") == "hybench_xp_io" and row.get("ready") is True
        }
        expected_all = {f"xp_io_w{i}" for i in range(1, 9)}
        if set(workers) != expected_all:
            raise RuntimeError("eight-worker reset authority cohort differs")
        runtime_config.base.c49.ASYNC_CONTRACT = C49_ADAPTER
        runtime_config.configure_runtime(
            freeze_path=freeze_path, workers=workers, scene="hybench_xp_io"
        )
        alg = runtime_config.base.alg
        alg.r0mod.PREFLIGHT = preflight_path
        alg.r0mod.RUNTIME_PREFLIGHT = preflight_path
        alg.r0mod.PREPARED = prepared_path
        alg.r0mod.BENCHMARK_ACCOUNTS = accounts_path
        reset8.install_for_scene(workers)
        reset8.configure(freeze=freeze_path, prepared=prepared_path)
        alg.reset = reset8
        # This host's cgroup-v1 memory controller has no memsw files.  Record
        # host swap and use the strongest verifiable container control:
        # MemorySwappiness == 0.  Never mutate global host swap state.
        observed_host_swap = host_swap_inventory()
        reset8.prep._host_swap_devices = lambda: []
        selected = {worker_id: workers[worker_id] for worker_id in contract["worker_ids"]}
        target_cpus = set().union(*(
            cpus(str(row["database_cpuset"])) | cpus(str(row["client_cpuset"]))
            for row in selected.values()
        ))
        state.update({"status": "RUNNING", "stage": "DYNAMIC_NONINTERFERENCE_GATE"})
        atomic(state_path, state)
        gate = coordination.rolling_quiet_gate(
            run_id=args.run_id,
            required_seconds=60.0,
            maximum_wait_seconds=600.0,
            maximum_load1=512.0,
            target_cpus=target_cpus,
        )
        gate.update({
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "host_swap_inventory": observed_host_swap,
            "container_effective_swap_policy_required": "anonymous_swap_disabled_swappiness_0",
        })
        atomic(output / "dynamic_noninterference_gate.json", gate)
        if gate.get("status") != "PASS":
            raise RuntimeError("dynamic non-interference gate failed closed")
        grouped: dict[str, list[dict[str, Any]]] = {
            label: [] for label in contract["point_labels"]
        }
        for wave_index, labels in enumerate(
            contract["counterbalanced_wave_assignments"], start=1
        ):
            assignments = {
                worker_id: {
                    "rank": index,
                    "params": copy.deepcopy(contract["points"][label]),
                }
                for index, (worker_id, label) in enumerate(labels.items(), start=1)
            }
            state.update({
                "stage": f"WAVE_{wave_index}_RESETTING_AND_MEASURING",
                "active_wave": wave_index,
                "active_assignments": labels,
            })
            atomic(state_path, state)
            wave_root = output / f"wave_{wave_index:02d}"
            measured = single_measurement.measure_batch(
                freeze, selected, assignments, wave_root,
                f"{args.run_id}_qualification_wave_{wave_index:02d}",
                state_path=state_path,
                stage_prefix=f"XP_IO_QUALIFICATION_WAVE_{wave_index:02d}",
            )
            batch_path = wave_root / "batch_result.json"
            batch = load(batch_path)
            if batch.get("status") != "PASS" or set(measured) != set(labels):
                raise RuntimeError(f"qualification wave {wave_index} failed closed")
            for worker_id, row in measured.items():
                point = str(labels[worker_id])
                enriched = metric_row(row)
                enriched["live_resource_readback"] = validate_live_worker_resources(
                    selected[worker_id], contract["required_device_major_minor"]
                )
                enriched.update({
                    "point_label": point,
                    "wave_index": wave_index,
                    "batch_result_path": str(batch_path),
                    "batch_result_sha256": sha(batch_path),
                })
                grouped[point].append(enriched)
            state.update({"completed_waves": wave_index, "last_completed_wave": wave_index})
            atomic(state_path, state)
        point_results: dict[str, Any] = {}
        for label, rows in grouped.items():
            if len(rows) != 2 or {row["worker_id"] for row in rows} != set(contract["worker_ids"]):
                raise RuntimeError(f"{label} is not counterbalanced across both workers")
            point_results[label] = {
                "replica_count": 2,
                "workers": sorted(row["worker_id"] for row in rows),
                "device_major_minor": contract["required_device_major_minor"],
                "mean_client_xap_qps": fmean(row["primary_client_xap_qps"] for row in rows),
                "mean_client_xtp_tps": fmean(row["client_xtp_tps"] for row in rows),
                "mean_server_questions_qps_audit": fmean(row["server_questions_qps_audit"] for row in rows),
                "mean_p95_ms": fmean(float(row["p95_ms"]) for row in rows),
                "mean_p99_ms": fmean(float(row["p99_ms"]) for row in rows),
                "mean_io_fraction_of_cap": fmean(float(row["resource_means"]["io"]) for row in rows),
                "mean_cpu_fraction_of_quota": fmean(float(row["resource_means"]["cpu"]) for row in rows),
                "mean_memory_fraction": fmean(float(row["resource_means"]["memory"]) for row in rows),
                "replicas": rows,
            }
        result = {
            "schema_version": "mysql_xp_io_two_worker_qualification_result_v1",
            "status": "PASS",
            "completed_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "purpose": "environment_qualification_only",
            "algorithm_search_reexecuted": False,
            "accepted_state_credit_increment": 0,
            "historical_artifacts_mutated": False,
            "host_swap_inventory": observed_host_swap,
            "container_effective_swap_policy": "anonymous_swap_disabled_swappiness_0",
            "counterbalanced": True,
            "qualification_passed": True,
            "point_results": point_results,
        }
        result_path = output / "qualification_result.json"
        atomic(result_path, result)
        state.update({
            "status": "COMPLETE", "stage": "COMPLETE", "completed_at": now(),
            "result_path": str(result_path), "result_sha256": sha(result_path),
        })
        atomic(state_path, state)
        print(json.dumps({"status": "PASS", "result": str(result_path)}), flush=True)
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
