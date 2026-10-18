#!/usr/bin/env python3
"""Replay baseline/A/B/C on one scene with two-worker counterbalancing."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import fmean, stdev
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v24 as coordination
from scripts import mysql_tpch_client_qps_measurement_v2 as tpch_metric
from scripts import run_mysql_algorithm_b_four_scene_client_primary_v5 as b_runtime


SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
POINTS = ("baseline", "algorithm_a", "algorithm_b", "algorithm_c")
INITIAL_ORDERS = {
    "w1": (
        "baseline", "algorithm_a", "algorithm_b", "algorithm_c",
        "baseline", "algorithm_a", "algorithm_b", "algorithm_c",
        "baseline", "algorithm_a", "algorithm_b", "algorithm_c",
    ),
    "w2": (
        "algorithm_b", "algorithm_c", "baseline", "algorithm_a",
        "algorithm_c", "baseline", "algorithm_a", "algorithm_b",
        "algorithm_a", "algorithm_b", "algorithm_c", "baseline",
    ),
}
T95_N6 = 2.570581835636305


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
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
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        if "-" in token:
            left, right = token.split("-", 1)
            result.update(range(int(left), int(right) + 1))
        elif token:
            result.add(int(token))
    return result


def compact_metric(row: Mapping[str, Any], *, scene: str) -> dict[str, Any]:
    workload = dict(row.get("workload_result") or {})
    formal = dict(workload.get("formal_metrics") or {})
    coverage = dict(workload.get("workload_coverage") or {})
    resource = dict(row.get("resource_summary") or {})
    if scene.startswith("hybench_xp"):
        client_qps = float(formal.get("xap_qps") or 0.0)
        xtp_tps: float | None = float(formal.get("xtp_tps") or 0.0)
        measurement_seconds = float(formal.get("measurement_seconds") or 0.0)
        coverage_specific = (
            coverage.get("completed_iq") == [f"IQ{i}" for i in range(1, 8)]
            and coverage.get("completed_at") == [f"AT{i}" for i in range(1, 7)]
        )
        right_censored = {
            "xap": int(formal.get("primary_right_censored_count") or 0),
            "xtp": int(formal.get("xtp_right_censored_count") or 0),
        }
        server_qps = float(
            row.get("server_questions_qps_audit")
            or row.get("db_qps") or 0.0
        )
    else:
        client_qps = float(row.get("client_qps_audit") or 0.0)
        xtp_tps = None
        measurement_seconds = float(
            workload.get("independent_wall_time_s")
            or workload.get("wall_time_s")
            or (row.get("resource_stability") or {}).get("elapsed_seconds")
            or 0.0
        )
        coverage_specific = True
        right_censored = {"xap": 0, "xtp": 0}
        server_qps = float(
            row.get("server_db_qps_audit")
            or row.get("db_qps") or 0.0
        )
    checks = {
        "hard_gate_passed": row.get("hard_gate_passed") is True,
        "coverage_complete": row.get("coverage_complete") is True,
        "scene_coverage_specific": coverage_specific,
        "client_qps_positive": math.isfinite(client_qps) and client_qps > 0.0,
        "measurement_within_900_seconds": 0.0 < measurement_seconds <= 900.0,
        "resources_finite": all(
            math.isfinite(float((row.get("resource_means") or {}).get(name)))
            for name in ("cpu", "memory", "io")
        ),
    }
    if scene == "hybench_xp_io":
        checks.update({
            "device_correct": resource.get("device_major_minor") == "259:6",
            "io_positive": float(row["resource_means"]["io"]) > 0.0,
        })
    valid = all(checks.values())
    return {
        "valid": valid,
        "checks": checks,
        "worker_id": str(row.get("worker_id") or ""),
        "client_qps": client_qps,
        "xtp_tps": xtp_tps,
        "server_questions_qps_audit": server_qps,
        "p95_ms": float(row.get("p95_ms") or 0.0),
        "p99_ms": float(row.get("p99_ms") or 0.0),
        "resources": {
            name: float(row["resource_means"][name])
            for name in ("cpu", "memory", "io")
        },
        "measurement_seconds": measurement_seconds,
        "right_censored": right_censored,
        "device_major_minor": resource.get("device_major_minor"),
    }


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "valid_n": len(rows),
        "workers": sorted({str(row["worker_id"]) for row in rows}),
        "mean_client_qps": fmean(float(row["client_qps"]) for row in rows),
        "mean_xtp_tps": (
            fmean(float(row["xtp_tps"]) for row in rows)
            if all(row["xtp_tps"] is not None for row in rows) else None
        ),
        "mean_server_questions_qps_audit": fmean(
            float(row["server_questions_qps_audit"]) for row in rows
        ),
        "mean_p95_ms": fmean(float(row["p95_ms"]) for row in rows),
        "mean_p99_ms": fmean(float(row["p99_ms"]) for row in rows),
        "mean_resources": {
            name: fmean(float(row["resources"][name]) for row in rows)
            for name in ("cpu", "memory", "io")
        },
        "right_censored_totals": {
            key: sum(int(row["right_censored"][key]) for row in rows)
            for key in ("xap", "xtp")
        },
        "lane_effect_client_qps": {
            worker: fmean(
                float(row["client_qps"])
                for row in rows if row["worker_id"] == worker
            )
            for worker in sorted({str(row["worker_id"]) for row in rows})
        },
    }


def paired_delta(
    candidate: list[dict[str, Any]], baseline: list[dict[str, Any]],
) -> dict[str, Any]:
    deltas: list[float] = []
    for worker in sorted({str(row["worker_id"]) for row in baseline}):
        base_rows = [row for row in baseline if row["worker_id"] == worker]
        candidate_rows = [row for row in candidate if row["worker_id"] == worker]
        if len(base_rows) != len(candidate_rows):
            raise RuntimeError("paired replay worker cardinality differs")
        for base, cand in zip(base_rows, candidate_rows, strict=True):
            deltas.append(
                (float(cand["client_qps"]) / float(base["client_qps"]) - 1.0)
                * 100.0
            )
    mean = fmean(deltas)
    half = T95_N6 * stdev(deltas) / math.sqrt(len(deltas))
    return {
        "paired_n": len(deltas),
        "mean_delta_pct": mean,
        "ci95_low_pct": mean - half,
        "ci95_high_pct": mean + half,
        "paired_deltas_pct": deltas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.run_id) is None:
        parser.error("unsafe run id")
    output = args.output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh replay output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    state_path = output / "run_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_abc_final_counterbalanced_scene_state_v1",
        "run_id": args.run_id,
        "scene": args.scene,
        "status": "STARTING",
        "stage": "VALIDATING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "completed_waves": 0,
    }
    atomic(state_path, state)
    try:
        contract_path = args.contract.resolve()
        contract = load(contract_path)
        scene_contract = dict((contract.get("scenes") or {}).get(args.scene) or {})
        if (
            contract.get("schema_version")
            != "mysql_abc_final_counterbalanced_replay_contract_v1"
            or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or contract.get("formal_scope") != "mysql_only"
            or contract.get("cross_engine_comparison_prohibited") is not True
            or scene_contract.get("points") is None
            or set(scene_contract["points"]) != set(POINTS)
            or contract.get("target_valid_repeats_per_point") != 6
            or contract.get("target_valid_repeats_per_point_per_worker") != 3
        ):
            raise RuntimeError("final replay contract differs")
        for binding in (contract.get("source_bindings") or {}).values():
            path = ROOT / str(binding["path"])
            if sha(path) != binding["sha256"]:
                raise RuntimeError(f"final replay source binding differs: {path}")
        authority = Path(contract["authority_root"]).resolve()
        os.environ["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
        core = b_runtime.install_runtime()
        impl = core._impl
        impl["install_scene"](args.scene)
        impl["validate_release"](args.scene)
        coordination_contract = impl["configure_runtime"]()
        freeze, workers, point_gate = impl["fresh_authority"]()
        expected_workers = {
            f"{SCENES[args.scene]}_w1", f"{SCENES[args.scene]}_w2"
        }
        if set(workers) != expected_workers:
            raise RuntimeError("final replay two-worker cohort differs")
        base_measure = impl["single_measurement"].measure_batch
        measure = (
            tpch_metric.wrap_measure_batch(
                base_measure, load=impl["alg"].load, atomic=impl["alg"].atomic,
            )
            if args.scene == "tpch_sf4_memory" else base_measure
        )
        target_cpus = set().union(*(
            cpus(str(row["database_cpuset"]))
            | cpus(str(row["client_cpuset"]))
            for row in workers.values()
        ))
        state.update({
            "status": "RUNNING",
            "stage": "COUNTERBALANCED_REPLAY",
            "contract_path": str(contract_path),
            "contract_sha256": sha(contract_path),
            "point_gate": point_gate,
            "coordination_contract": coordination_contract,
        })
        atomic(state_path, state)
        short_workers = {
            "w1": f"{SCENES[args.scene]}_w1",
            "w2": f"{SCENES[args.scene]}_w2",
        }
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
            point: {worker: [] for worker in expected_workers}
            for point in POINTS
        }
        invalid: list[dict[str, Any]] = []
        schedule = [
            {
                short_workers["w1"]: INITIAL_ORDERS["w1"][index],
                short_workers["w2"]: INITIAL_ORDERS["w2"][index],
            }
            for index in range(12)
        ]
        wave_index = 0
        while True:
            missing = [
                (point, worker)
                for point in POINTS for worker in sorted(expected_workers)
                if len(grouped[point][worker]) < 3
            ]
            if wave_index < len(schedule):
                labels = schedule[wave_index]
            elif missing and wave_index < 24:
                labels = {}
                for worker in sorted(expected_workers):
                    choices = [point for point, lane in missing if lane == worker]
                    if choices:
                        labels[worker] = choices[0]
                    else:
                        labels[worker] = min(
                            POINTS, key=lambda point: len(grouped[point][worker])
                        )
            else:
                break
            wave_index += 1
            state.update({
                "stage": f"WAVE_{wave_index:02d}_DYNAMIC_GATE",
                "active_wave": wave_index,
                "active_assignments": labels,
            })
            atomic(state_path, state)
            gate = coordination.rolling_quiet_gate(
                run_id=f"{args.run_id}_wave_{wave_index:02d}",
                required_seconds=15.0,
                maximum_wait_seconds=180.0,
                maximum_load1=512.0,
                target_cpus=target_cpus,
            )
            atomic(output / f"wave_{wave_index:02d}_dynamic_gate.json", gate)
            if gate.get("status") != "PASS":
                invalid.append({
                    "wave": wave_index,
                    "labels": labels,
                    "reason": "dynamic_noninterference_gate_failed",
                })
                continue
            assignments = {
                worker: {
                    "rank": index,
                    "params": copy.deepcopy(scene_contract["points"][label]),
                }
                for index, (worker, label) in enumerate(labels.items(), 1)
            }
            state["stage"] = f"WAVE_{wave_index:02d}_MEASURING"
            atomic(state_path, state)
            wave_root = output / f"wave_{wave_index:02d}"
            try:
                measured = measure(
                    freeze, workers, assignments, wave_root,
                    f"{args.run_id}_wave_{wave_index:02d}",
                    state_path=state_path,
                    stage_prefix=f"ABC_FINAL_{args.scene}_WAVE_{wave_index:02d}",
                )
                batch_path = wave_root / "batch_result.json"
                batch = load(batch_path)
                if batch.get("status") != "PASS" or set(measured) != set(labels):
                    raise RuntimeError("final replay batch failed")
                for worker, row in measured.items():
                    compact = compact_metric(row, scene=args.scene)
                    compact.update({
                        "point": labels[worker],
                        "wave": wave_index,
                        "batch_path": str(batch_path),
                        "batch_sha256": sha(batch_path),
                    })
                    if compact["valid"]:
                        grouped[labels[worker]][worker].append(compact)
                    else:
                        invalid.append(compact)
            except Exception as error:
                invalid.append({
                    "wave": wave_index,
                    "labels": labels,
                    "reason": f"{type(error).__name__}: {error}",
                })
            state.update({
                "completed_waves": wave_index,
                "valid_counts": {
                    point: {
                        worker: len(rows)
                        for worker, rows in per_worker.items()
                    }
                    for point, per_worker in grouped.items()
                },
                "invalid_count": len(invalid),
            })
            atomic(state_path, state)
        if any(
            len(grouped[point][worker]) < 3
            for point in POINTS for worker in expected_workers
        ):
            raise RuntimeError("final replay valid repetition target not reached")
        rows_by_point = {
            point: [
                row
                for worker in sorted(expected_workers)
                for row in grouped[point][worker][:3]
            ]
            for point in POINTS
        }
        summaries = {
            point: summary(rows) for point, rows in rows_by_point.items()
        }
        comparisons = {
            point: paired_delta(rows_by_point[point], rows_by_point["baseline"])
            for point in POINTS if point != "baseline"
        }
        result = {
            "schema_version": "mysql_abc_final_counterbalanced_scene_result_v1",
            "status": "PASS",
            "completed_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "scene": args.scene,
            "counterbalanced": True,
            "target_valid_repeats_per_point": 6,
            "target_valid_repeats_per_point_per_worker": 3,
            "initial_wave_count": 12,
            "actual_wave_count": wave_index,
            "point_summaries": summaries,
            "paired_comparisons_vs_baseline": comparisons,
            "replicas": rows_by_point,
            "excluded_measurements": invalid,
        }
        result_path = output / "final_replay_result.json"
        atomic(result_path, result)
        state.update({
            "status": "COMPLETE",
            "stage": "COMPLETE",
            "completed_at": now(),
            "result_path": str(result_path),
            "result_sha256": sha(result_path),
        })
        atomic(state_path, state)
        print(json.dumps({"status": "PASS", "result": str(result_path)}), flush=True)
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
