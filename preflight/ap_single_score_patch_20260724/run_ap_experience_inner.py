#!/usr/bin/env python3
"""Rank all 63 formal AP experience-table subsets on host threads."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Dict, Tuple

from experience_table_subset_optimizer import optimize_all_experience_table_subsets
from formal_contracts import resolve_target_relative_scoring
from gradient_descent_optimizer import ParameterRange


ACTIVE_PARAMS = (
    "seq_page_cost", "random_page_cost", "cpu_tuple_cost",
    "cpu_operator_cost", "parallel_setup_cost", "parallel_tuple_cost",
)
INITIAL_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
RANGES = {
    "seq_page_cost": ParameterRange(0.1, 100.0),
    "random_page_cost": ParameterRange(0.1, 100.0),
    "cpu_tuple_cost": ParameterRange(0.001, 0.1),
    "cpu_operator_cost": ParameterRange(0.00025, 0.025),
    "parallel_setup_cost": ParameterRange(1.0, 10000.0),
    "parallel_tuple_cost": ParameterRange(0.001, 1.0),
}
CAPABILITY = {"cpu": 5.5, "memory": 223155000.0, "io": 103809024.0}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--max-workers", type=int, default=32)
    parser.add_argument("--round-index", type=int, default=1)
    parser.add_argument("--state-json", type=Path)
    args = parser.parse_args()
    lane = args.lane_root.resolve()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    progress_path = output / "subset_progress.json"
    table_path = lane / "experience_table_T_ap6_v3.json"
    table_artifact = json.loads(table_path.read_text(encoding="utf-8"))
    table = table_artifact["table"]
    canonical = hashlib.sha256(
        json.dumps(table, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if canonical != table_artifact["table_sha256"]:
        raise RuntimeError("experience table canonical SHA mismatch")
    if tuple(table_artifact["active_params"]) != ACTIVE_PARAMS:
        raise RuntimeError("experience table active parameter order mismatch")
    expected_subsets = (1 << len(ACTIVE_PARAMS)) - 1
    if expected_subsets != 63:
        raise RuntimeError("formal AP all-nonempty-subset contract is not 63")

    state_path = args.state_json.resolve() if args.state_json else None
    if state_path is None:
        baseline = lane / "results/shared_baseline/P0_repeat_1_attempt_34_retry3/measurement_summary.json"
        guard = lane / "results/shared_baseline/P0_repeat_1_attempt_34_retry3/measurement_outer_entry_guard.json"
        baseline_value = json.loads(baseline.read_text(encoding="utf-8"))
        guard_value = json.loads(guard.read_text(encoding="utf-8"))
        if guard_value.get("passed") is not True:
            raise RuntimeError("R0 outer-entry guard is not passing")
        initial_params = copy.deepcopy(INITIAL_PARAMS)
        ranges = copy.deepcopy(RANGES)
        base_util = {
            name: float(baseline_value["resource_mean_utilization"][name])
            for name in ("cpu", "memory", "io")
        }
        baseline_qps = float(baseline_value["db_qps"])
        incumbent_source = str(baseline)
        state_sha256 = None
    else:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") != "accepted_incumbent_state":
            raise RuntimeError("state-json is not an accepted incumbent state")
        incumbent = state.get("incumbent") or {}
        raw_params = incumbent.get("params") or {}
        raw_ranges = state.get("ranges") or {}
        if set(raw_params).issuperset(ACTIVE_PARAMS) is False:
            raise RuntimeError("accepted incumbent params are incomplete")
        if set(raw_ranges) != set(ACTIVE_PARAMS):
            raise RuntimeError("accepted incumbent ranges are incomplete")
        initial_params = {name: float(raw_params[name]) for name in ACTIVE_PARAMS}
        ranges = {
            name: ParameterRange(
                float(raw_ranges[name]["min_value"]),
                float(raw_ranges[name]["max_value"]),
            )
            for name in ACTIVE_PARAMS
        }
        base_util_pct = incumbent.get("utilization_pct") or {}
        base_util = {
            name: float(base_util_pct[name]) / 100.0
            for name in ("cpu", "memory", "io")
        }
        baseline_qps = float(incumbent["db_qps"])
        incumbent_source = str(incumbent.get("source") or state_path)
        baseline = state_path
        state_sha256 = sha256(state_path)
    base_resource = {
        name: base_util[name] * CAPABILITY[name] for name in base_util
    }

    write_json(progress_path, {
        "schema_version": 1,
        "status": "running",
        "pid": os.getpid(),
        "round_index": args.round_index,
        "subset_count": expected_subsets,
        "completed_count": 0,
        "max_workers": min(args.max_workers, expected_subsets),
        "table_path": str(table_path),
        "table_artifact_sha256": sha256(table_path),
        "table_canonical_sha256": canonical,
        "baseline_path": str(baseline),
        "baseline_sha256": sha256(baseline),
        "state_path": str(state_path) if state_path else None,
        "state_sha256": state_sha256,
        "incumbent_source": incumbent_source,
        "initial_params": initial_params,
        "ranges": {
            name: {
                "min_value": ranges[name].min_value,
                "max_value": ranges[name].max_value,
            }
            for name in ACTIVE_PARAMS
        },
        "started_at_epoch": time.time(),
    })

    def estimator_factory(
        subset: Tuple[str, ...], subset_id: str,
    ):
        def estimate(_sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
            effects = {name: 0.0 for name in ("cpu", "memory", "io")}
            for param_name in ACTIVE_PARAMS:
                ratio = float(params[param_name]) / float(initial_params[param_name])
                if not math.isfinite(ratio) or ratio <= 0.0:
                    raise RuntimeError(f"invalid parameter ratio: {param_name}={ratio}")
                delta = math.log(ratio)
                for resource_name in effects:
                    effects[resource_name] += float(table[param_name][resource_name]) * delta
            resource = {
                name: base_resource[name] * math.exp(effects[name]) for name in effects
            }
            estimate.last_stats = {
                "qps": {"avg": baseline_qps},
                "query_count": {"avg": baseline_qps},
                "query_time": {"avg": 1.0 / baseline_qps},
            }
            estimate.last_metadata = {
                "source": "experience_table_log_response_model_v1",
                "subset_id": subset_id,
                "active_subset": list(subset),
                "finite_difference_used": False,
                "random_direction_used": False,
            }
            return resource
        return estimate

    scoring = resolve_target_relative_scoring(
        json.loads(
            (lane / "common_scoring_contract.json").read_text(encoding="utf-8")
        ),
        "memory",
    )
    try:
        result = optimize_all_experience_table_subsets(
            resource_estimation_factory=estimator_factory,
            capability=CAPABILITY,
            p_real=initial_params,
            sensitivity_table=table,
            param_ranges=ranges,
            sql_set={"workload": "hybench_ap", "sequence_contract": "v1"},
            active_params=ACTIVE_PARAMS,
            max_workers=min(args.max_workers, expected_subsets),
            round_index=args.round_index,
            log_dir=str(output / "subset_logs"),
            optimizer_options={
                "eta": 0.02,
                "max_iter": 10,
                "patience": 3,
                "gamma": 0.5,
                "reward_type": "log",
                "penalty_type": "power",
                "use_continuous": False,
                "overload_threshold": scoring["overload_threshold"],
                "score_mode": scoring["score_mode"],
                "resource_weights": scoring["resource_weights"],
                "resource_target_ranges": scoring["resource_target_ranges"],
                "verbose": False,
            },
            ranking_target_resource="memory",
        )
    except BaseException as exc:
        write_json(progress_path, {
            "schema_version": 1,
            "status": "failed",
            "pid": os.getpid(),
            "round_index": args.round_index,
            "subset_count": expected_subsets,
            "completed_count": sum(1 for _ in (output / "subset_logs").rglob("*.jsonl")),
            "error": f"{type(exc).__name__}: {exc}",
            "completed_at_epoch": time.time(),
        })
        raise

    summary = {
        "schema_version": 1,
        "status": "complete",
        "pid": os.getpid(),
        "round_index": args.round_index,
        "subset_count": result.subset_count,
        "completed_count": len(result.audit_records),
        "successful_subset_count": result.successful_subset_count,
        "failed_subset_count": result.failed_subset_count,
        "unique_candidate_count": result.unique_candidate_count,
        "max_workers": min(args.max_workers, expected_subsets),
        "state_path": str(state_path) if state_path else None,
        "state_sha256": state_sha256,
        "incumbent_source": incumbent_source,
        "subset_log_dir": result.log_dir,
        "candidate_rankings": result.candidate_rankings,
        "audit_records": result.audit_records,
        "completed_at_epoch": time.time(),
    }
    write_json(output / "subset_ranking.json", summary)
    write_json(progress_path, {
        key: value for key, value in summary.items()
        if key not in {"candidate_rankings", "audit_records"}
    })
    print(json.dumps({
        "status": "complete",
        "pid": os.getpid(),
        "subset_count": result.subset_count,
        "completed_count": len(result.audit_records),
        "unique_candidate_count": result.unique_candidate_count,
        "ranking_path": str(output / "subset_ranking.json"),
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
