#!/usr/bin/env python3
"""Module."""


from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import itertools
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = (
    ROOT
    / "history/bonect_parallel_vm_replay"
    / "pg_bonect_p2_12h_four_scenario_20260714_0218"
)
DEFAULT_OUTPUT = RUN_ROOT / "four_scenario_feedback_closed_loop_20260715"

COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
TARGET_RESOURCE = {"cpu": "cpu", "memory": "memory", "io": "io", "none": ""}
FALLBACK_SCENARIO_CONFIG = {
    "io": ROOT
    / "config/generated/bonect_phase2_vm_io_stage_a_v87-3_20260714"
    / "io_stage_a_bonect-replay-v87-3_vm.json",
    "none": ROOT
    / "config/generated/bonect_phase2_vm_none_stage_a_v87-4_20260714"
    / "none_stage_a_bonect-replay-v87-4_vm.json",
}
PREFERRED_WORKER_POOL = {
    "io": ROOT
    / "config/generated/bonect_phase2_vm_io_stage_a_r5_restore_measure_verified_v87-3_20260714"
    / "io_v87-3_r5_single_pool.json",
    "none": ROOT
    / "config/generated/bonect_phase2_vm_none_stage_a_r4_conc_xtp6_xap4_v87-4_20260714"
    / "none_v87-4_r4_single_pool.json",
}


SCENARIO_INPUTS = {
    "cpu": {
        "ranking": RUN_ROOT / "cpu_scan_prep/full_grid_5e7/candidates.jsonl",
        "round0_candidates": RUN_ROOT
        / "cpu_stage_a/round0_blind_recalib_20260714/cpu_round0_prefix_k4_candidates_FROZEN.json",
        "round0_results": RUN_ROOT
        / "cpu_stage_a/round0_blind_recalib_20260714/preflight_k4_2m_20260714_112300"
        / "screen_2m_prefix_k4_READY_NOT_LAUNCHED/parallel_validation_results.jsonl",
    },
    "memory": {
        "ranking": RUN_ROOT / "memory_scan_prep/full_grid_5e7/candidates.jsonl",
        "round0_candidates": RUN_ROOT
        / "memory_stage_a/round0_blind_preflight_20260714/memory_round0_frozen_prefix_k4_candidates.json",
        "round0_results": RUN_ROOT
        / "memory_stage_a/round0_blind_screen_2m_prefix_k4_20260714_1122/parallel_validation_results.jsonl",
    },
    "io": {
        "ranking": RUN_ROOT / "io_scan_prep/full_grid_5e7/candidates.jsonl",
        "round0_candidates": RUN_ROOT
        / "io_stage_a/round0_blind_k4_pack_20260714_1201/io_round0_prefix_k4_candidates_FROZEN.json",
        "round0_results": RUN_ROOT
        / "io_stage_a/round0_blind_screen_2m_prefix_k4_20260714_1201/parallel_validation_results.jsonl",
    },
    "none": {
        "ranking": RUN_ROOT / "none_scan_prep/full_grid_5e7/candidates.jsonl",
        "round0_candidates": RUN_ROOT
        / "none_stage_a/resume_k4_preflight_20260714_1123/none_round0_prefix_k4_candidates.json",
        "round0_results": RUN_ROOT
        / "none_stage_a/screen_2m_prefix_k4_resume_20260714_1123/parallel_validation_results.jsonl",
    },
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def build_grid_ranking(scenario: str) -> List[Dict[str, Any]]:
    """build grid ranking."""
    path = RUN_ROOT / f"{scenario}_scan_prep/full_grid_5e7/ranges.json"
    grid_values = read_json(path).get("grid_values") or {}
    missing = [name for name in COST_PARAMS if not isinstance(grid_values.get(name), list)]
    if missing:
        raise ValueError(f"{scenario}: frozen grid values missing for {missing}")
    rows: List[Dict[str, Any]] = []
    value_lists = [grid_values[name] for name in COST_PARAMS]
    for candidate_index, values in enumerate(itertools.product(*value_lists)):
        rows.append(
            {
                "candidate_index": candidate_index,
                "params": {name: float(values[index]) for index, name in enumerate(COST_PARAMS)},
                "score": 0.0,
            }
        )
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def exact_params(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, Mapping):
        raise ValueError("params must be an object")
    missing = [name for name in COST_PARAMS if name not in raw]
    extra = [name for name in raw if name not in COST_PARAMS]
    if missing or extra:
        raise ValueError(f"params must contain exactly seven cost keys; missing={missing}, extra={extra}")
    return {name: float(raw[name]) for name in COST_PARAMS}


def signature(params: Mapping[str, Any]) -> Tuple[float, ...]:
    cleaned = exact_params(params)
    return tuple(round(cleaned[name], 12) for name in COST_PARAMS)


def load_candidate_manifest(path: Path) -> List[Dict[str, Any]]:
    value = read_json(path)
    rows = value.get("candidates") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError(f"invalid candidate manifest: {path}")
    return [row for row in rows if isinstance(row, dict)]


def initial_ranges(scenario: str) -> Dict[str, Dict[str, Any]]:
    path = RUN_ROOT / f"{scenario}_scan_prep/full_grid_5e7/ranges.json"
    value = read_json(path).get("ranges") or {}
    result: Dict[str, Dict[str, Any]] = {}
    for name in COST_PARAMS:
        row = value.get(name) or {}
        result[name] = {
            "low": float(row["low"]),
            "high": float(row["high"]),
            "scale": str(row.get("scale") or "linear"),
        }
    return result


def midpoint(row: Mapping[str, Any]) -> float:
    low = float(row["low"])
    high = float(row["high"])
    if str(row.get("scale")) == "log" and low > 0.0:
        return math.sqrt(low * high)
    return (low + high) / 2.0


def binary_shrink(
    ranges: Mapping[str, Mapping[str, Any]],
    anchor: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    params = exact_params(anchor)
    result: Dict[str, Dict[str, Any]] = {}
    for name in COST_PARAMS:
        row = ranges[name]
        low = float(row["low"])
        high = float(row["high"])
        mid = midpoint(row)
        value = min(high, max(low, params[name]))
        if value <= mid:
            new_low, new_high = low, mid
        else:
            new_low, new_high = mid, high
        result[name] = {"low": new_low, "high": new_high, "scale": row.get("scale", "linear")}
    return result


def in_ranges(params: Mapping[str, Any], ranges: Mapping[str, Mapping[str, Any]]) -> bool:
    cleaned = exact_params(params)
    return all(
        float(ranges[name]["low"]) - 1e-12 <= cleaned[name] <= float(ranges[name]["high"]) + 1e-12
        for name in COST_PARAMS
    )


def normalized_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    ranges: Mapping[str, Mapping[str, Any]],
) -> float:
    a = exact_params(left)
    b = exact_params(right)
    total = 0.0
    for name in COST_PARAMS:
        row = ranges[name]
        low = float(row["low"])
        high = float(row["high"])
        if str(row.get("scale")) == "log" and low > 0.0:
            width = max(1e-15, math.log(high) - math.log(low))
            delta = (math.log(max(a[name], low)) - math.log(max(b[name], low))) / width
        else:
            width = max(1e-15, high - low)
            delta = (a[name] - b[name]) / width
        total += delta * delta
    return math.sqrt(total)


def result_quality(row: Mapping[str, Any], scenario: str) -> Tuple[int, float, float, float]:
    qps = float(row.get("db_qps_delta_pct") or -1e9)
    deltas = row.get("resource_delta_pp") or {}
    actual = row.get("actual_utilization_pct") or {}
    target = TARGET_RESOURCE[scenario]
    if scenario == "none":
        all_sweet = all(float(actual.get(name, 1e9)) < 80.0 for name in ("cpu", "memory", "io"))
        strict = qps >= 15.0 and all_sweet
        loose = qps > 0.0 and all_sweet
        return (2 if strict else 1 if loose else 0, qps, 0.0, -max((float(actual.get(n, 0.0)) for n in ("cpu", "memory", "io")), default=0.0))
    target_drop = -float(deltas.get(target, 0.0))
    target_exit = float(actual.get(target, 1e9)) < 80.0
    non_target_up = any(float(deltas.get(name, 0.0)) > 0.0 for name in ("cpu", "memory", "io") if name != target)
    non_targets_sweet = all(float(actual.get(name, 1e9)) < 80.0 for name in ("cpu", "memory", "io") if name != target)
    strict = qps >= 15.0 and target_drop >= 5.0 and target_exit and non_target_up and non_targets_sweet
    loose = qps > 0.0 and target_drop > 0.0
    return (2 if strict else 1 if loose else 0, qps, target_drop, -max((float(actual.get(n, 0.0)) for n in ("cpu", "memory", "io")), default=0.0))


def classify_result(row: Mapping[str, Any], scenario: str) -> Dict[str, Any]:
    quality = result_quality(row, scenario)
    target = TARGET_RESOURCE[scenario]
    deltas = row.get("resource_delta_pp") or {}
    return {
        "strict_good": quality[0] == 2,
        "loose_good": quality[0] >= 1,
        "db_qps_delta_pct": float(row.get("db_qps_delta_pct") or 0.0),
        "target_resource": target or None,
        "target_delta_pp": float(deltas.get(target, 0.0)) if target else None,
        "quality_key": list(quality),
    }


def choose_real_anchor(rows: Sequence[Mapping[str, Any]], scenario: str) -> Dict[str, Any]:
    usable = [row for row in rows if isinstance(row.get("params"), Mapping) and row.get("status") == "ok"]
    if not usable:
        raise RuntimeError(f"no usable prior real results for {scenario}")
    return copy.deepcopy(max(usable, key=lambda row: result_quality(row, scenario)))


def ranking_value(row: Mapping[str, Any], scenario: str) -> float:
    keys = {
        "cpu": ("rank_score", "calibrated_score", "score", "old_score"),
        "memory": ("calibrated_score", "score", "old_score"),
        "io": ("calibrated_score", "score", "old_score"),
        "none": ("none_r4_score", "score", "old_score"),
    }[scenario]
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return -1e30


def select_recall(
    scenario: str,
    ranking_rows: Sequence[Mapping[str, Any]],
    ranges: Mapping[str, Mapping[str, Any]],
    distance_ranges: Mapping[str, Mapping[str, Any]],
    anchor_params: Mapping[str, Any],
    real_feedback: Sequence[Mapping[str, Any]],
    tested: Iterable[Tuple[float, ...]],
    top_k: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    tested_set = set(tested)
    eligible: List[Dict[str, Any]] = []
    for model_rank, raw in enumerate(ranking_rows, start=1):
        if not isinstance(raw.get("params"), Mapping):
            continue
        params = exact_params(raw["params"])
        if signature(params) in tested_set or not in_ranges(params, ranges):
            continue
        row = copy.deepcopy(dict(raw))
        row["params"] = params
        row["frozen_model_rank"] = model_rank
        row["frozen_model_score"] = ranking_value(row, scenario)
        row["distance_to_feedback_anchor"] = normalized_distance(params, anchor_params, ranges)
        weights: List[Tuple[float, Mapping[str, Any], float]] = []
        for feedback_row in real_feedback:
            if not isinstance(feedback_row.get("params"), Mapping) or feedback_row.get("status") != "ok":
                continue
            distance = normalized_distance(params, feedback_row["params"], distance_ranges)

            weight = math.exp(-4.0 * distance * distance)
            weights.append((weight, feedback_row, distance))
        weight_sum = sum(item[0] for item in weights)
        if weight_sum <= 1e-15:
            predicted_qps = -100.0
            predicted_target_drop = -100.0
            min_feedback_distance = 10.0
        else:
            predicted_qps = sum(
                weight * float(feedback_row.get("db_qps_delta_pct") or 0.0)
                for weight, feedback_row, _distance in weights
            ) / weight_sum
            target = TARGET_RESOURCE[scenario]
            predicted_target_drop = (
                sum(
                    weight * -float((feedback_row.get("resource_delta_pp") or {}).get(target, 0.0))
                    for weight, feedback_row, _distance in weights
                )
                / weight_sum
                if target
                else 0.0
            )
            min_feedback_distance = min(item[2] for item in weights)
        feedback_score = predicted_qps - 8.0 * min_feedback_distance
        if scenario != "none":
            feedback_score += 2.0 * predicted_target_drop
        row["feedback_predicted_qps_delta_pct"] = predicted_qps
        row["feedback_predicted_target_drop_pp"] = predicted_target_drop
        row["minimum_real_feedback_distance"] = min_feedback_distance
        row["feedback_rank_score"] = feedback_score
        eligible.append(row)
    if len(eligible) < top_k:
        raise RuntimeError(f"{scenario}: only {len(eligible)} untested candidates remain inside shrunk ranges")

    eligible.sort(
        key=lambda row: (
            float(row["feedback_rank_score"]),
            float(row["frozen_model_score"]),
            -int(row["frozen_model_rank"]),
        ),
        reverse=True,
    )
    selected: List[Dict[str, Any]] = []
    used_signatures = set()

    def add(row: Mapping[str, Any], role: str) -> None:
        sig = signature(row["params"])
        if sig in used_signatures or len(selected) >= top_k:
            return
        item = copy.deepcopy(dict(row))
        item["selection_role"] = role
        selected.append(item)
        used_signatures.add(sig)

    add(eligible[0], "feedback_predicted_top1")
    if scenario != "none":


        joint_direction = [
            row
            for row in eligible
            if float(row["feedback_predicted_qps_delta_pct"]) > 0.0
            and float(row["feedback_predicted_target_drop_pp"]) > 0.0
        ]
        if joint_direction:
            pareto = max(
                joint_direction,
                key=lambda row: (
                    min(
                        float(row["feedback_predicted_qps_delta_pct"]) / 15.0,
                        float(row["feedback_predicted_target_drop_pp"]) / 5.0,
                    ),
                    float(row["feedback_predicted_qps_delta_pct"]) / 15.0
                    + float(row["feedback_predicted_target_drop_pp"]) / 5.0,
                    float(row["feedback_rank_score"]),
                ),
            )
            add(pareto, "feedback_joint_direction_pareto")
        else:


            directional = max(
                eligible,
                key=lambda row: (
                    float(row["feedback_predicted_target_drop_pp"]),
                    float(row["feedback_predicted_qps_delta_pct"]),
                    -float(row["minimum_real_feedback_distance"]),
                ),
            )
            add(directional, "feedback_target_drop_boundary")
    top_plan = str(eligible[0].get("plan_hash") or "")
    diverse = next(
        (
            row
            for row in eligible[1:]
            if top_plan and str(row.get("plan_hash") or "") != top_plan
        ),
        None,
    )
    if diverse is None:
        diverse = max(
            eligible[1 : min(len(eligible), 257)],
            key=lambda row: normalized_distance(row["params"], eligible[0]["params"], ranges),
            default=None,
        )
    if diverse is not None:
        add(diverse, "feedback_plan_diverse" if top_plan else "feedback_parameter_diverse")
    for row in sorted(eligible, key=lambda item: float(item["distance_to_feedback_anchor"])):
        add(row, "feedback_nearest_anchor")
        if len(selected) >= min(3, top_k):
            break
    for row in eligible:
        add(row, "feedback_model_prefix")
        if len(selected) >= top_k:
            break

    candidates = []
    for index, row in enumerate(selected, start=1):
        candidate_id = f"{scenario}_feedback_r{{round_index}}_k{index}"
        candidates.append(
            {
                "candidate_id_template": candidate_id,
                "scenario": scenario,
                "params": row["params"],
                "selection_role": row["selection_role"],
                "frozen_model_rank": row["frozen_model_rank"],
                "frozen_model_score": row["frozen_model_score"],
                "distance_to_feedback_anchor": row["distance_to_feedback_anchor"],
                "feedback_predicted_qps_delta_pct": row["feedback_predicted_qps_delta_pct"],
                "feedback_predicted_target_drop_pp": row["feedback_predicted_target_drop_pp"],
                "minimum_real_feedback_distance": row["minimum_real_feedback_distance"],
                "feedback_rank_score": row["feedback_rank_score"],
                "plan_hash": row.get("plan_hash"),
                "teacher_injection": False,
                "source_candidate_index": row.get("candidate_index"),
            }
        )
    audit = {
        "eligible_count": len(eligible),
        "tested_signature_count": len(tested_set),
        "selection_roles": [row["selection_role"] for row in candidates],
        "feedback_label_count": len(real_feedback),
        "feedback_rule": (
            "rbf_on_prior_real_qps_and_target_drop_plus_distance_penalty"
            "+explicit_joint_direction_pareto_slot"
        ),
        "teacher_coordinates_loaded": False,
        "teacher_coordinates_affect_selection": False,
    }
    return candidates, audit


def inversion_rate(candidates: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]) -> Optional[float]:
    by_id = {str(row.get("candidate_id")): row for row in results}
    pairs = 0
    inversions = 0
    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            a = by_id.get(str(candidates[left].get("candidate_id")))
            b = by_id.get(str(candidates[right].get("candidate_id")))
            if a is None or b is None:
                continue
            pairs += 1
            if float(a.get("db_qps_delta_pct") or -1e9) < float(b.get("db_qps_delta_pct") or -1e9):
                inversions += 1
    return inversions / pairs if pairs else None


def scenario_worker_pool(scenario: str, output_dir: Path) -> Path:
    base = PREFERRED_WORKER_POOL.get(scenario) or ROOT / (
        f"config/generated/bonect_phase2_12h_freeze_20260714/{scenario}/worker_pool.json"
    )
    value = read_json(base)
    workers = value.get("workers") if isinstance(value, dict) else None
    if not isinstance(workers, list):
        raise ValueError(f"invalid worker pool: {base}")
    default_config = FALLBACK_SCENARIO_CONFIG.get(scenario) or ROOT / (
        f"config/generated/bonect_phase2_12h_freeze_20260714/{scenario}/{scenario}_provisional_vm.json"
    )
    changed = False
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        vm_resource = worker.get("vm_resource")
        if isinstance(vm_resource, dict) and not vm_resource.get("memory_size"):
            memory_gb = vm_resource.get("memory_gb")
            if isinstance(memory_gb, (int, float)) and not float(memory_gb).is_integer():
                vm_resource["memory_size"] = f"{round(float(memory_gb) * 1024)}M"
                changed = True
        scenario_configs = worker.setdefault("scenario_configs", {})
        if scenario not in scenario_configs:
            if not default_config.exists():
                raise FileNotFoundError(f"missing frozen scenario config: {default_config}")
            scenario_configs[scenario] = str(default_config)
            changed = True
    if not changed:
        return base
    materialized = output_dir / "materialized_worker_pool.json"
    value["materialized_from"] = str(base)
    value["materialization_reason"] = f"fill_missing_scenario_configs.{scenario}"
    write_json(materialized, value)
    return materialized


def prepare_round(
    scenario: str,
    scenario_dir: Path,
    round_index: int,
    ranges: Mapping[str, Mapping[str, Any]],
    distance_ranges: Mapping[str, Mapping[str, Any]],
    prior_results: Sequence[Mapping[str, Any]],
    ranking_rows: Sequence[Mapping[str, Any]],
    tested: Iterable[Tuple[float, ...]],
    top_k: int,
) -> Tuple[Path, Dict[str, Dict[str, Any]], Dict[str, Any]]:
    anchor = choose_real_anchor(prior_results, scenario)
    shrunk = binary_shrink(ranges, anchor["params"])
    candidates, recall_audit = select_recall(
        scenario,
        ranking_rows,
        shrunk,
        distance_ranges,
        anchor["params"],
        prior_results,
        tested,
        top_k,
    )
    for row in candidates:
        row["candidate_id"] = str(row.pop("candidate_id_template")).format(round_index=round_index)
    round_dir = scenario_dir / f"round_{round_index:02d}"
    manifest = {
        "event": "feedback_closed_loop_recall_frozen_before_real_replay",
        "scenario": scenario,
        "round": round_index,
        "exact_cost_param_count": 7,
        "feedback_anchor": {
            "candidate_id": anchor.get("candidate_id"),
            "params": exact_params(anchor["params"]),
            "classification": classify_result(anchor, scenario),
        },
        "ranges_before": ranges,
        "ranges_after_binary_shrink": shrunk,
        "recall_audit": recall_audit,
        "candidates": candidates,
    }
    manifest["freeze_sha256"] = canonical_sha256(manifest)
    manifest_path = round_dir / "candidates_frozen_before_replay.json"
    write_json(manifest_path, manifest)
    return manifest_path, shrunk, manifest


def run_replay(
    scenario: str,
    manifest_path: Path,
    output_dir: Path,
    *,
    run_mins: float,
    apply_vm_profile: bool,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(ROOT / "shell/bonect_parallel_vm_replay.py"),
        "--candidates-json",
        str(manifest_path),
        "--worker-pool-json",
        str(scenario_worker_pool(scenario, output_dir)),
        "--output-dir",
        str(output_dir),
        "--scenario",
        scenario,
        "--max-workers",
        "1",
        "--run-mins",
        str(run_mins),
        "--workload-timeout-s",
        "900",
    ]
    if apply_vm_profile:
        cmd.extend(["--apply-vm-profile", "--force-vm-destroy"])
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "controller_replay.log").open("a", encoding="utf-8") as log_handle:
        return subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            check=False,
        )


def summarize_round(
    scenario: str,
    manifest: Mapping[str, Any],
    replay_dir: Path,
) -> Dict[str, Any]:
    summary_path = replay_dir / "parallel_replay_summary.json"
    results_path = replay_dir / "parallel_validation_results.jsonl"
    replay_summary = read_json(summary_path) if summary_path.exists() else {}
    results = read_jsonl(results_path) if results_path.exists() else []
    candidates = manifest.get("candidates") or []
    classified = []
    for row in results:
        classified.append(
            {
                "candidate_id": row.get("candidate_id"),
                "params": row.get("params"),
                "actual_utilization_pct": row.get("actual_utilization_pct"),
                "resource_delta_pp": row.get("resource_delta_pp"),
                **classify_result(row, scenario),
            }
        )
    return {
        "scenario": scenario,
        "round": manifest.get("round"),
        "freeze_sha256": manifest.get("freeze_sha256"),
        "replay_status": replay_summary.get("status"),
        "replay_stop_reason": replay_summary.get("stop_reason"),
        "baseline_gate_passed": bool((replay_summary.get("baseline_gate") or {}).get("passed")),
        "anchor_gate_passed": bool((replay_summary.get("anchor_gate") or {}).get("passed")),
        "candidate_count": len(candidates),
        "result_count": len(results),
        "strict_good_count": sum(1 for row in classified if row["strict_good"]),
        "loose_good_count": sum(1 for row in classified if row["loose_good"]),
        "predicted_top1_recalled_strict_good": bool(classified and classified[0]["strict_good"]),
        "predicted_top1_recalled_loose_good": bool(classified and classified[0]["loose_good"]),
        "qps_pairwise_inversion_rate": inversion_rate(candidates, results),
        "results": classified,
        "results_jsonl": str(results_path),
    }


def run_scenario(args: argparse.Namespace, scenario: str, output_root: Path) -> Dict[str, Any]:
    inputs = SCENARIO_INPUTS[scenario]
    scenario_dir = output_root / scenario
    scenario_dir.mkdir(parents=True, exist_ok=True)
    ranking_rows = build_grid_ranking(scenario)
    prior_candidates = load_candidate_manifest(Path(inputs["round0_candidates"]))
    prior_results = read_jsonl(Path(inputs["round0_results"]))
    tested = {signature(row["params"]) for row in prior_candidates if isinstance(row.get("params"), Mapping)}
    ranges = initial_ranges(scenario)
    distance_ranges = copy.deepcopy(ranges)
    rounds: List[Dict[str, Any]] = []

    for round_index in range(1, int(args.feedback_rounds) + 1):
        manifest_path, ranges, manifest = prepare_round(
            scenario,
            scenario_dir,
            round_index,
            ranges,
            distance_ranges,
            prior_results,
            ranking_rows,
            tested,
            int(args.top_k),
        )
        state = {
            "event": "round_prepared",
            "scenario": scenario,
            "round": round_index,
            "manifest": str(manifest_path),
            "freeze_sha256": manifest["freeze_sha256"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        }
        write_json(scenario_dir / "state.json", state)
        if args.prepare_only:
            rounds.append({**state, "status": "prepared_not_run"})
            break

        replay_dir = scenario_dir / f"round_{round_index:02d}/real_replay_2m"
        completed = run_replay(
            scenario,
            manifest_path,
            replay_dir,
            run_mins=float(args.run_mins),
            apply_vm_profile=not args.skip_apply_vm_profile,
        )
        round_summary = summarize_round(scenario, manifest, replay_dir)
        round_summary["replay_returncode"] = completed.returncode
        write_json(scenario_dir / f"round_{round_index:02d}/round_summary.json", round_summary)
        rounds.append(round_summary)
        append_jsonl(output_root / "events.jsonl", {"event": "round_complete", **round_summary})
        if completed.returncode != 0 or round_summary["result_count"] == 0:
            break
        prior_results.extend(read_jsonl(Path(round_summary["results_jsonl"])))
        for candidate in manifest["candidates"]:
            tested.add(signature(candidate["params"]))

    result = {
        "scenario": scenario,
        "status": "prepared" if args.prepare_only else (
            "completed" if len(rounds) == int(args.feedback_rounds) and all(row.get("result_count", 0) > 0 for row in rounds) else "incomplete"
        ),
        "rounds": rounds,
        "teacher_coordinates_loaded": False,
        "teacher_coordinates_affect_selection": False,
    }
    write_json(scenario_dir / "scenario_summary.json", result)
    return result


def write_combined_report(output_root: Path, scenarios: Sequence[Mapping[str, Any]]) -> None:
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "output_root": str(output_root),
        "scenarios": list(scenarios),
    }
    write_json(output_root / "four_scenario_summary.json", payload)
    lines = [
        "# ",
        "",
        "|  |  |  |  |  |  Top1 / |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for scenario in scenarios:
        rounds = scenario.get("rounds") or []
        strict = sum(int(row.get("strict_good_count") or 0) for row in rounds)
        loose = sum(int(row.get("loose_good_count") or 0) for row in rounds)
        top1 = [
            f"{row.get('predicted_top1_recalled_strict_good')}/{row.get('predicted_top1_recalled_loose_good')}"
            for row in rounds
            if "predicted_top1_recalled_strict_good" in row
        ]
        lines.append(
            f"| {scenario.get('scenario')} | {scenario.get('status')} | {len(rounds)} | "
            f"{strict} | {loose} | {', '.join(top1) or '-'} |"
        )
    lines.extend(
        [
            "",
            "DB QPS >= +15% 5pp  80% "
            "None  QPS >= +15%  80%",
            "DB QPS None  QPS  80%",
            "",
            " 2  5  A/B/A ",
        ]
    )
    (output_root / "four_scenario_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", default="cpu,memory,io,none")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--feedback-rounds", type=int, default=2)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--run-mins", type=float, default=2.0)
    parser.add_argument("--max-parallel-scenarios", type=int, default=4)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--skip-apply-vm-profile", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    scenarios = [value.strip().lower() for value in args.scenarios.split(",") if value.strip()]
    invalid = [value for value in scenarios if value not in SCENARIO_INPUTS]
    if invalid:
        raise ValueError(f"unknown scenarios: {invalid}")
    if args.feedback_rounds < 1 or args.top_k < 1:
        raise ValueError("feedback-rounds and top-k must be positive")
    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(
        output_root / "run_meta.json",
        {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "controller": str(Path(__file__).resolve()),
            "scenarios": scenarios,
            "feedback_rounds": args.feedback_rounds,
            "top_k": args.top_k,
            "run_mins": args.run_mins,
            "answer_blind": True,
            "teacher_coordinates_loaded": False,
            "teacher_coordinates_affect_selection": False,
            "exact_cost_param_count": 7,
        },
    )
    results: List[Dict[str, Any]] = []
    workers = min(len(scenarios), max(1, int(args.max_parallel_scenarios)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(run_scenario, args, scenario, output_root): scenario
            for scenario in scenarios
        }
        for future in concurrent.futures.as_completed(future_map):
            scenario = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"scenario": scenario, "status": "failed", "error": str(exc), "rounds": []}
                write_json(output_root / scenario / "scenario_summary.json", result)
            results.append(result)
            print(json.dumps({"event": "scenario_done", **result}, ensure_ascii=False), flush=True)
    results.sort(key=lambda row: scenarios.index(str(row["scenario"])))
    write_combined_report(output_root, results)
    return 0 if all(row.get("status") in {"completed", "prepared"} for row in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
