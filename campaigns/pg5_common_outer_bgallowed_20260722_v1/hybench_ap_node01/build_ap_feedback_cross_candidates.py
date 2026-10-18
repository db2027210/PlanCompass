#!/usr/bin/env python3
"""Build unseen AP candidates by interpolating T-SGD points with real feedback."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Tuple


ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON top level is not an object: {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def signature(params: Dict[str, float]) -> str:
    payload = json.dumps(
        {name: float(params[name]).hex() for name in sorted(params)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parameter_key(params: Dict[str, float]) -> Tuple[float, ...]:
    return tuple(float(params[name]) for name in ACTIVE_PARAMS)


def interpolate(
    left: Dict[str, float],
    right: Dict[str, float],
    fraction: float,
) -> Dict[str, float]:
    """Geometric interpolation matches the experience table's log-response space."""
    return {
        name: math.exp(
            (1.0 - fraction) * math.log(float(left[name]))
            + fraction * math.log(float(right[name]))
        )
        for name in ACTIVE_PARAMS
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ranking", type=Path, required=True)
    parser.add_argument("--state-json", type=Path, required=True)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--output-ranking", type=Path, required=True)
    parser.add_argument("--analysis-output", type=Path, required=True)
    args = parser.parse_args()

    source = read_json(args.source_ranking)
    state = read_json(args.state_json)
    incumbent = state["incumbent"]
    incumbent_params = {
        name: float(incumbent["params"][name]) for name in ACTIVE_PARAMS
    }
    incumbent_qps = float(incumbent["db_qps"])
    incumbent_memory = float(incumbent["utilization_pct"]["memory"])
    ranges = state["ranges"]

    validations: List[Dict[str, Any]] = []
    historical_keys = set()
    for generation in (5, 6, 7):
        replay = args.validation_root / f"generation_{generation:02d}" / "replay"
        for path in sorted(replay.glob("rank_*_validation.json")):
            if "_rep_" in path.name:
                continue
            row = read_json(path)
            params = {name: float(row["params"][name]) for name in ACTIVE_PARAMS}
            validation = row["validation"]
            record = {
                "generation": generation,
                "rank": int(row["rank"]),
                "subset_id": row["subset_id"],
                "params": params,
                "db_qps": float(validation["db_qps"]),
                "memory_pct": float(validation["utilization_pct"]["memory"]),
                "qps_delta": float(validation["db_qps"]) - incumbent_qps,
                "memory_delta": (
                    float(validation["utilization_pct"]["memory"]) - incumbent_memory
                ),
                "hard_gate_passed": validation["hard_gate_passed"] is True,
            }
            validations.append(record)
            historical_keys.add(parameter_key(params))

    grouped: Dict[Tuple[float, ...], List[Dict[str, Any]]] = {}
    for row in validations:
        grouped.setdefault(parameter_key(row["params"]), []).append(row)
    aggregates: List[Dict[str, Any]] = []
    for rows in grouped.values():
        qps_values = [row["db_qps"] for row in rows]
        memory_values = [row["memory_pct"] for row in rows]
        aggregates.append(
            {
                "subset_ids": sorted({row["subset_id"] for row in rows}),
                "params": rows[0]["params"],
                "replicate_count": len(rows),
                "mean_db_qps": mean(qps_values),
                "std_db_qps": pstdev(qps_values),
                "mean_memory_pct": mean(memory_values),
                "std_memory_pct": pstdev(memory_values),
                "best_db_qps": max(qps_values),
                "best_memory_pct": min(memory_values),
                "qps_side_observation_count": sum(
                    row["qps_delta"] > 0.0 for row in rows
                ),
                "memory_side_observation_count": sum(
                    row["memory_delta"] < 0.0 for row in rows
                ),
            }
        )
    aggregates.sort(key=lambda row: (-row["mean_db_qps"], row["mean_memory_pct"]))

    pareto: List[Dict[str, Any]] = []
    for candidate in validations:
        dominated = any(
            other["db_qps"] >= candidate["db_qps"]
            and other["memory_pct"] <= candidate["memory_pct"]
            and (
                other["db_qps"] > candidate["db_qps"]
                or other["memory_pct"] < candidate["memory_pct"]
            )
            for other in validations
        )
        if not dominated:
            pareto.append(candidate)
    pareto.sort(key=lambda row: (-row["db_qps"], row["memory_pct"]))

    source_candidates = source["candidate_rankings"]
    parents = {
        "memory_r3": {
            name: float(source_candidates[2]["optimized_params"][name])
            for name in ACTIVE_PARAMS
        },
        "qps_r4": {
            name: float(source_candidates[3]["optimized_params"][name])
            for name in ACTIVE_PARAMS
        },
        "qps_r8": {
            name: float(source_candidates[7]["optimized_params"][name])
            for name in ACTIVE_PARAMS
        },
        "qps_r9": {
            name: float(source_candidates[8]["optimized_params"][name])
            for name in ACTIVE_PARAMS
        },
        "incumbent": incumbent_params,
    }
    specifications = [
        ("incumbent", "memory_r3", 0.25),
        ("incumbent", "memory_r3", 0.50),
        ("incumbent", "memory_r3", 0.75),
        ("qps_r4", "memory_r3", 0.25),
        ("qps_r4", "memory_r3", 0.50),
        ("qps_r4", "memory_r3", 0.75),
        ("qps_r8", "memory_r3", 0.25),
        ("qps_r8", "memory_r3", 0.50),
        ("qps_r8", "memory_r3", 0.75),
        ("qps_r9", "memory_r3", 0.25),
        ("qps_r9", "memory_r3", 0.50),
        ("qps_r9", "memory_r3", 0.75),
    ]

    derived: List[Dict[str, Any]] = []
    derived_keys = set()
    for left_name, right_name, fraction in specifications:
        params = interpolate(parents[left_name], parents[right_name], fraction)
        for name, value in params.items():
            lower = float(ranges[name]["min_value"])
            upper = float(ranges[name]["max_value"])
            if not lower <= value <= upper:
                raise RuntimeError(f"derived {name}={value} outside [{lower}, {upper}]")
        key = parameter_key(params)
        if key in historical_keys or key in derived_keys:
            continue
        derived_keys.add(key)
        derived.append(
            {
                "left_parent": left_name,
                "right_parent": right_name,
                "fraction": fraction,
                "interpolation_space": "experience_table_log_response_parameter_space",
                "params": params,
                "continuous_signature_sha256": signature(params),
            }
        )

    if len(derived) < 10:
        raise RuntimeError(f"only {len(derived)} unseen candidates survived deduplication")

    output = copy.deepcopy(source)
    by_subset = {row["subset_id"]: row for row in output["audit_records"]}
    for index, derivation in enumerate(derived):
        candidate = output["candidate_rankings"][index]
        candidate["optimized_params"] = copy.deepcopy(derivation["params"])
        candidate["feedback_calibrated_derivation"] = copy.deepcopy(derivation)
        candidate["final_estimation_metadata"] = {
            **(candidate.get("final_estimation_metadata") or {}),
            "source": "experience_table_T_real_feedback_cross_interpolation_v1",
            "feedback_generations": [5, 6, 7],
            "strict_acceptance_unchanged": True,
        }
        audit = by_subset[candidate["subset_id"]]
        audit["optimized_params"] = copy.deepcopy(derivation["params"])
        audit["feedback_calibrated_derivation"] = copy.deepcopy(derivation)

    output["algorithm"] = "experience_table_T_real_feedback_cross_interpolation_v1"
    output["feedback_calibration"] = {
        "validation_generations": [5, 6, 7],
        "real_validation_count": len(validations),
        "unique_real_parameter_count": len(grouped),
        "generated_unseen_continuous_candidates": len(derived),
        "historical_exact_parameter_deduplication": True,
        "strict_acceptance": "db_qps > incumbent AND memory_pct < incumbent",
        "plan_pruning_enabled": False,
    }
    write_json(args.output_ranking, output)
    write_json(
        args.analysis_output,
        {
            "schema_version": 1,
            "status": "offline_feedback_candidates_ready",
            "incumbent": {
                "db_qps": incumbent_qps,
                "memory_pct": incumbent_memory,
                "params": incumbent_params,
            },
            "real_validation_count": len(validations),
            "unique_real_parameter_count": len(grouped),
            "qps_side_observations": [
                row for row in validations if row["qps_delta"] > 0.0
            ],
            "memory_side_observations": [
                row for row in validations if row["memory_delta"] < 0.0
            ],
            "observed_pareto_front": pareto,
            "per_parameter_point_aggregates": aggregates,
            "parent_points": parents,
            "derived_candidates": derived,
            "derived_candidate_count": len(derived),
            "historical_parameter_duplicates_removed": (
                len(specifications) - len(derived)
            ),
            "strict_acceptance_unchanged": True,
            "plan_pruning_enabled": False,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
