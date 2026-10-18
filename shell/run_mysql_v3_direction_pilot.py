#!/usr/bin/env python3
"""Generate one v3 all-subset candidate and measure it without committing."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from experience_table_subset_optimizer import optimize_all_experience_table_subsets
from gradient_descent_optimizer import ParameterRange


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
POINT_RUNNER = ROOT / "shell/manual_mysql_sgd_r0_campaign.py"
HISTORY = ROOT / "history/mysql_sgd_env_agents_20260723"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def latest_checkpoint(lane_root: Path) -> Path:
    paths = sorted(
        lane_root.glob("direct_*/checkpoint.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not paths:
        raise RuntimeError(f"no direct checkpoint under {lane_root}")
    return paths[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("tpcc_cpu",), default="tpcc_cpu")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    scenario = args.scenario
    lane = "tpcc"
    target = "cpu"
    direct_config = load(ROOT / f"config/formal/mysql_sgd_production/{scenario}.json")
    table_path = ROOT / str(direct_config["experience_table"])
    table_doc = load(table_path)
    checkpoint_path = latest_checkpoint(HISTORY / lane / "formal_sgd_direct")
    checkpoint = load(checkpoint_path)
    incumbent = {
        name: float(value)
        for name, value in checkpoint["incumbent_costs"].items()
    }
    baseline_metrics = checkpoint["incumbent_metrics"]
    baseline_resources = {
        name: float(value)
        for name, value in baseline_metrics["resource_means"].items()
    }
    active = [str(name) for name in table_doc["parameter_space"]["active_params"]]
    sensitivity = {
        name: {resource: float(value) for resource, value in effects.items()}
        for name, effects in table_doc["table"].items()
    }
    ranges = {
        name: ParameterRange(float(bounds["lower"]), float(bounds["upper"]))
        for name, bounds in table_doc["parameter_space"]["ranges"].items()
    }

    def estimator_factory(_subset: tuple[str, ...], _subset_id: str):
        def estimate(_sql_set: Any, params: dict[str, float]) -> dict[str, float]:
            predicted = dict(baseline_resources)
            for name in active:
                delta = math.log(float(params[name]) / float(incumbent[name]))
                for resource in predicted:
                    predicted[resource] += sensitivity[name][resource] * delta
            return {
                resource: max(0.0, min(1.5, float(value)))
                for resource, value in predicted.items()
            }

        return estimate

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output = HISTORY / lane / "v3_direction_pilot" / f"pilot_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    summary = optimize_all_experience_table_subsets(
        resource_estimation_factory=estimator_factory,
        capability={"cpu": 1.0, "memory": 1.0, "io": 1.0},
        p_real=incumbent,
        sensitivity_table=sensitivity,
        param_ranges=ranges,
        sql_set=None,
        active_params=active,
        max_workers=4,
        round_index="v3_direction_pilot_1",
        log_dir=str(output / "all_subset_search"),
        optimizer_options={
            "eta": 0.0051,
            "max_iter": 1,
            "patience": 1,
            "gamma": 0.5,
            "score_mode": "threshold",
            "overload_threshold": 0.8,
            "resource_weights": {"cpu": 3.0, "memory": 1.0, "io": 1.0},
            "verbose": False,
        },
        ranking_target_resource=target,
    )
    candidates = [
        row for row in summary.candidate_rankings
        if any(
            float(row["optimized_params"][name]).hex()
            != float(incumbent[name]).hex()
            for name in active
        )
    ]
    if not candidates:
        raise RuntimeError("all-subset pilot produced no non-incumbent candidate")
    selected = candidates[0]
    cost_path = output / "candidate.costs.json"
    atomic_json(cost_path, selected["optimized_params"])
    result_document: dict[str, Any] = {
        "schema_version": "mysql_v3_direction_pilot_v1",
        "formal_result": False,
        "purpose": "one_candidate_direction_check_without_checkpoint_mutation",
        "scenario": scenario,
        "target_resource": target,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_checkpoint": str(checkpoint_path),
        "source_experience_table": str(table_path),
        "subset_count": summary.subset_count,
        "unique_candidate_count": summary.unique_candidate_count,
        "incumbent_costs": incumbent,
        "incumbent_metrics": baseline_metrics,
        "selected_candidate": selected,
        "top_candidates": candidates[:10],
        "candidate_cost_path": str(cost_path),
        "executed": False,
    }
    atomic_json(output / "pilot_result.json", result_document)
    if not args.execute:
        print(json.dumps(result_document, ensure_ascii=False), flush=True)
        return 0

    run_id = f"v3pilot_{stamp}_candidate_001"
    command = [
        str(PYTHON), str(POINT_RUNNER),
        "--scenario", lane,
        "--run-id", run_id,
        "--profile-json", str(ROOT / str(direct_config["profile"])),
        "--cost-json", str(cost_path),
        "--measurement-seconds", str(int(direct_config.get("measurement_seconds", 600))),
        "--minimum-measurement-seconds", str(
            int(direct_config.get("minimum_measurement_seconds", 180))
        ),
        "--minimum-client-completions", str(
            int(direct_config.get("minimum_client_completions", 0))
        ),
    ]
    result_document["point_command"] = command
    result_document["point_run_id"] = run_id
    result_document["executed"] = True
    atomic_json(output / "pilot_result.json", result_document)
    returncode = subprocess.run(command, cwd=ROOT, check=False).returncode
    point_dir = HISTORY / lane / "formal_sgd_campaigns" / run_id
    result_path = point_dir / "r0_result.json"
    result_document["point_returncode"] = returncode
    result_document["point_result_path"] = str(result_path)
    if returncode == 0 and result_path.is_file():
        measurement = load(result_path)
        selected_observation = measurement["rolling_measurement_control"][
            "selected_observation"
        ]
        candidate_metrics = {
            "db_qps": float(measurement["database_throughput"]["db_qps"]),
            "resource_means": {
                name: float(value)
                for name, value in selected_observation["resource_means"].items()
            },
        }
        checks = {
            "correctness_passed": measurement.get("correctness", {}).get("passed") is True,
            "stable": selected_observation.get("stability", {}).get("stable") is True,
            "db_qps_strictly_higher": (
                candidate_metrics["db_qps"] > float(baseline_metrics["db_qps"])
            ),
            "target_utilization_strictly_lower": (
                candidate_metrics["resource_means"][target]
                < float(baseline_resources[target])
            ),
        }
        result_document["candidate_metrics"] = candidate_metrics
        result_document["direction_checks"] = checks
        result_document["direction_effective"] = all(checks.values())
    else:
        failure_path = point_dir / "failure.txt"
        result_document["technical_failure"] = (
            failure_path.read_text(encoding="utf-8").strip()
            if failure_path.is_file() else "point runner failed without failure.txt"
        )
        result_document["direction_effective"] = None
    atomic_json(output / "pilot_result.json", result_document)
    print(json.dumps(result_document, ensure_ascii=False), flush=True)
    return 0 if returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
