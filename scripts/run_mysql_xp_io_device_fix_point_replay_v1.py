#!/usr/bin/env python3
"""Replay the XP/IO baseline and existing A/B/C champions on both SSDs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v17 as coordination
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as runtime_config


CONTRACT = ROOT / "config/formal/frozen_mysql_xp_io_device_fix_point_replay_20260829_v2/point_replay_contract.json"
C49_ADAPTER = ROOT / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260829_v3/algorithm_c_measurement_adapter_contract.json"


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


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "hybench_xp_io"
        or contract.get("algorithm_search_reexecuted") is not False
        or contract.get("accepted_state_credit_increment") != 0
        or contract.get("physical_worker_count") != 8
        or contract.get("replicas_per_point") != 2
    ):
        raise RuntimeError("point replay contract differs")
    for binding in contract["source_bindings"].values():
        path = ROOT / binding["path"]
        if sha(path) != binding["sha256"]:
            raise RuntimeError(f"source binding differs: {path}")
    for binding in contract["source_result_evidence"].values():
        path = ROOT / binding["path"]
        if sha(path) != binding["sha256"]:
            raise RuntimeError(f"result source binding differs: {path}")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    state_path = output / "run_state.json"
    if output.exists():
        raise FileExistsError(f"fresh output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    state = {
        "schema_version": "mysql_xp_io_mixed_device_point_replay_state_v1",
        "run_id": args.run_id,
        "status": "STARTING",
        "stage": "VALIDATING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
    }
    atomic(state_path, state)
    try:
        contract = validate_contract()
        authority_path = Path(contract["authority"]["path"])
        if sha(authority_path) != contract["authority"]["sha256"]:
            raise RuntimeError("live authority hash differs")
        authority = load(authority_path)
        artifacts = authority["artifacts"]
        prepared_path = Path(artifacts["prepared"])
        preflight_path = Path(artifacts["preflight"])
        accounts_path = Path(artifacts["accounts"])
        prepared = load(prepared_path)
        freeze_path = Path(contract["resource_freeze"]["path"])
        if sha(freeze_path) != contract["resource_freeze"]["sha256"]:
            raise RuntimeError("resource freeze hash differs")
        freeze = load(freeze_path)
        workers = {
            str(row["worker_id"]): copy.deepcopy(dict(row))
            for row in prepared["workers"]
            if row.get("scene") == "hybench_xp_io" and row.get("ready") is True
        }
        if set(workers) != set(contract["assignments"]):
            raise RuntimeError("eight-worker assignment cohort differs")
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
        devices_by_point: dict[str, set[str]] = {}
        for worker_id, label in contract["assignments"].items():
            devices_by_point.setdefault(label, set()).add(
                str(workers[worker_id]["blkio_authority"]["device_major_minor"])
            )
        expected_devices = set(contract["required_devices_per_point"])
        if any(devices != expected_devices for devices in devices_by_point.values()):
            raise RuntimeError("each point must span disk1 and disk2")
        target_cpus = set().union(*(
            cpus(str(row["database_cpuset"])) | cpus(str(row["client_cpuset"]))
            for row in workers.values()
        ))
        state.update({"status": "RUNNING", "stage": "DYNAMIC_NONINTERFERENCE_GATE"})
        atomic(state_path, state)
        gate = coordination.rolling_quiet_gate(
            run_id=args.run_id,
            required_seconds=15.0,
            maximum_wait_seconds=600.0,
            maximum_load1=512.0,
            target_cpus=target_cpus,
        )
        gate.update({
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "external_process_control_or_stop_performed": False,
        })
        gate_path = output / "dynamic_noninterference_gate.json"
        atomic(gate_path, gate)
        if gate.get("status") != "PASS":
            raise RuntimeError("dynamic non-interference gate failed closed")
        assignments = {
            worker_id: {
                "rank": index,
                "params": copy.deepcopy(contract["points"][label]),
            }
            for index, (worker_id, label) in enumerate(
                contract["assignments"].items(), start=1
            )
        }
        state.update({"stage": "RESETTING_AND_MEASURING", "assignments": contract["assignments"]})
        atomic(state_path, state)
        measured = alg.measure_batch(
            freeze,
            workers,
            assignments,
            output / "physical_replay",
            f"{args.run_id}_single_balanced_wave",
            state_path=state_path,
            stage_prefix="XP_IO_DEVICE_FIX_POINT_REPLAY",
        )
        batch = load(output / "physical_replay/batch_result.json")
        if batch.get("status") != "PASS" or len(measured) != 8:
            raise RuntimeError("point replay batch failed closed")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for worker_id, row in measured.items():
            label = contract["assignments"][worker_id]
            enriched = copy.deepcopy(dict(row))
            enriched["point_label"] = label
            enriched["device_major_minor"] = row["resource_summary"]["device_major_minor"]
            if (
                row.get("hard_gate_passed") is not True
                or float(row["resource_means"]["io"]) <= 0.0
                or enriched["device_major_minor"]
                != workers[worker_id]["blkio_authority"]["device_major_minor"]
            ):
                raise RuntimeError(f"{worker_id} corrected IO measurement is invalid")
            grouped.setdefault(label, []).append(enriched)
        summary_points: dict[str, Any] = {}
        for label, rows in grouped.items():
            if len(rows) != 2 or {row["device_major_minor"] for row in rows} != expected_devices:
                raise RuntimeError(f"{label} balanced replicas differ")
            summary_points[label] = {
                "replica_count": 2,
                "devices": sorted(row["device_major_minor"] for row in rows),
                "mean_db_qps": fmean(float(row["db_qps"]) for row in rows),
                "mean_io_fraction_of_cap": fmean(float(row["resource_means"]["io"]) for row in rows),
                "mean_cpu_fraction_of_quota": fmean(float(row["resource_means"]["cpu"]) for row in rows),
                "mean_memory_fraction": fmean(float(row["resource_means"]["memory"]) for row in rows),
                "replicas": rows,
            }
        baseline = summary_points["baseline"]
        comparisons = {}
        for label in ("algorithm_a_champion", "algorithm_b_champion", "algorithm_c_champion"):
            row = summary_points[label]
            comparisons[label] = {
                "qps_gain_ratio_vs_baseline": row["mean_db_qps"] / baseline["mean_db_qps"] - 1.0,
                "io_drop_ratio_vs_baseline": 1.0 - row["mean_io_fraction_of_cap"] / baseline["mean_io_fraction_of_cap"],
            }
        result = {
            "schema_version": "mysql_xp_io_mixed_device_point_replay_result_v1",
            "status": "PASS",
            "completed_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "algorithm_search_reexecuted": False,
            "accepted_state_credit_increment": 0,
            "historical_artifacts_mutated": False,
            "point_results": summary_points,
            "comparisons": comparisons,
            "batch_result_path": str(output / "physical_replay/batch_result.json"),
            "batch_result_sha256": sha(output / "physical_replay/batch_result.json"),
        }
        result_path = output / "point_replay_result.json"
        atomic(result_path, result)
        state.update({
            "status": "COMPLETE",
            "stage": "COMPLETE",
            "completed_at": now(),
            "result_path": str(result_path),
            "result_sha256": sha(result_path),
        })
        atomic(state_path, state)
        print(json.dumps({"status": "PASS", "result": str(result_path)}))
        return 0
    except BaseException as error:
        state.update({
            "status": "FAILED_CLOSED",
            "stage": "FAILED",
            "failed_at": now(),
            "error": f"{type(error).__name__}: {error}",
        })
        atomic(state_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
