#!/usr/bin/env python3
"""Prepare an isolated TPC-C attempt using the corrected experience-table T-SGD."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path("/home/anonymous/program/resource_adaptive")
SOURCE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt7/config/tpcc_experience_table_all_subset_sgd.json"
)
ATTEMPT_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt8_tsgd_v1"
)
TARGET = ATTEMPT_ROOT / "config/tpcc_experience_table_all_subset_sgd.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if TARGET.exists():
        raise SystemExit(f"refuse to overwrite existing config: {TARGET}")
    config = json.loads(SOURCE.read_text(encoding="utf-8"))
    old_root = str(SOURCE.parent.parent)
    new_root = str(ATTEMPT_ROOT)
    old_run_id = "pg5v1_tpcc_exp_sgd_attempt7"
    new_run_id = "pg5v1_tpcc_exp_sgd_attempt8_tsgd_v1"

    def replace_owned_paths(value: object) -> object:
        if isinstance(value, dict):
            return {key: replace_owned_paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_owned_paths(item) for item in value]
        if isinstance(value, str):
            return value.replace(old_root, new_root).replace(old_run_id, new_run_id)
        return value

    config = replace_owned_paths(config)
    gradient = config["optimizer"]["gradient"]
    gradient.update(
        {
            "eta": 0.02,
            "max_eta": 0.02,
            "max_iter": 10,
            "patience": 3,
            "gamma": 0.5,
            "min_eta": 0.0001,
            "subset_max_workers": 16,
        }
    )
    config["formal_outer_protocol"] = {
        "formal_search_initial_replay_top_k": 5,
        "formal_search_second_replay_top_k": 10,
        "formal_search_max_replay_top_k": 20,
        "formal_search_accepted_target": 10,
        "formal_search_accepted_count_start": 0,
    }
    config["benchmark"]["workload_cmd"] += " --stability-target-resource cpu"
    config["_campaign"]["attempt_tag"] = "attempt8_tsgd_v1"
    config["_campaign"]["candidate_protocol"]["stability_fields"] = [
        "cpu_utilization"
    ]
    config["_campaign"]["candidate_protocol"]["initial_probe_seconds"] = 180
    config["_candidate_measurement_protocol"]["wrapper_sha256"] = sha256(
        ROOT / "shell/run_tpcc_sensitive_stable_point.py"
    )
    stability_contract = (
        ROOT
        / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
        "stability_gate_contract_v5.json"
    )
    config["_candidate_measurement_protocol"].update(
        {
            "initial_probe_seconds": 180,
            "stability": json.loads(stability_contract.read_text(encoding="utf-8")),
        }
    )
    config["stability_gate_contract"] = {
        "path": str(stability_contract),
        "sha256": sha256(stability_contract),
    }
    config["_tpcc_attempt8_tsgd_contract"] = {
        "algorithm": "experience_table_subset_sgd_v1",
        "old_grid_results_are_audit_only": True,
        "accepted_shrink_count_start": 0,
        "accepted_shrink_target": 10,
        "subset_count": 127,
        "subset_parallelism": 16,
        "inner": {
            "eta": 0.02,
            "max_iter": 10,
            "patience": 3,
            "gamma": 0.5,
            "min_eta": 0.0001,
        },
        "real_replay": "complete_first5_then_expand_to10_then_at_most20",
        "stability": "cpu_only_after_warmup; first decision 180s; +30s to 600s",
        "core_sha256": {
            name: sha256(ROOT / name)
            for name in (
                "gradient_descent_optimizer.py",
                "experience_table_subset_optimizer.py",
                "parameter_optimizer.py",
                "common_outer_loop.py",
                "plan_resource_estimator.py",
            )
        },
    }
    TARGET.parent.mkdir(parents=True, exist_ok=False)
    TARGET.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(TARGET, flush=True)
    print(sha256(TARGET), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
