#!/usr/bin/env python3
"""Replay one fresh IO baseline plus a 15-point physical bridge calibration.

The bridge connects the measured low-IO/low-QPS point to the measured
high-QPS/high-IO region.  It changes all six differing cost parameters with one
shared coordinate, leaving random_page_cost=20.  This gives the resource model
dense evidence across the plan-switch path most likely to contain the desired
IO sweet point, while preserving the fixed Top15 replay budget.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import load_config, run_real_validation_safe, wait_for_postgres  # noqa: E402
from double_loop_scenario import measurement_from_payload  # noqa: E402
from shell import run_double_loop_grid_scenarios as runner  # noqa: E402


LOW_IO_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 20.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

HIGH_QPS_PARAMS = {
    "seq_page_cost": 100.0,
    "random_page_cost": 20.0,
    "cpu_tuple_cost": 1e-6,
    "cpu_index_tuple_cost": 0.428572,
    "cpu_operator_cost": 1e-6,
    "parallel_setup_cost": 10000.0,
    "parallel_tuple_cost": 10.0,
}


def bridge_top15() -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for index in range(15):
        fraction = index / 14.0
        params = {
            name: float(LOW_IO_PARAMS[name])
            + fraction * (float(HIGH_QPS_PARAMS[name]) - float(LOW_IO_PARAMS[name]))
            for name in LOW_IO_PARAMS
        }
        result.append(
            {
                "calibration_id": f"io_bridge_top15_{index + 1:03d}",
                "phase": "io_low_resource_to_high_qps_bridge",
                "bridge_fraction": fraction,
                "params": params,
            }
        )
    return result


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def usable_raw(raw: Mapping[str, Any]) -> Dict[str, float]:
    measurement = measurement_from_payload(
        raw.get("resources") or raw.get("resource") or {},
        raw,
    )
    if runner.measurement_invalid_reason(measurement, raw):
        raise RuntimeError(
            f"unusable IO recalibration measurement: {raw.get('label')} {raw.get('error')}"
        )
    return measurement.as_resource()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pruning-source-dir", required=True)
    parser.add_argument("--run-mins", type=int, default=10)
    parser.add_argument("--print-design", action="store_true")
    args = parser.parse_args()
    design = bridge_top15()
    if args.print_design:
        print(json.dumps(design, ensure_ascii=False, indent=2), flush=True)
        return 0

    output_dir = Path(args.output_dir).resolve()
    scenario_dir = output_dir / "io"
    real_dir = scenario_dir / "real_validations"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    real_dir.mkdir(parents=True, exist_ok=True)

    source_cfg = load_config(str(Path(args.config).resolve()))
    cfg, isolation = runner.isolated_fresh_runtime_config(source_cfg, source_cfg)
    validation_cfg = cfg.setdefault("binary_plan_tuning", {}).setdefault("validation", {})
    validation_cfg.setdefault("hybench_xp", {})["xp_run_mins"] = int(args.run_mins)
    replay_cfg = validation_cfg.get("vm_replay")
    if not isinstance(replay_cfg, dict) or not runner.config_flag(replay_cfg.get("enabled"), False):
        raise RuntimeError("IO bridge recalibration requires vm_replay.enabled")
    replay_cfg["run_mins"] = int(args.run_mins)
    replay_cfg.setdefault("run_mins_by_target", {})["io"] = int(args.run_mins)
    validation_cfg["vm_replay"] = replay_cfg
    worker, _ = runner.vm_replay_probe_worker(cfg, "io")
    worker_pg = runner.pg_conn_from_worker(cfg, worker)
    wait_for_postgres(runner.replace(worker_pg, dbname="postgres"), timeout_s=900)
    runner.write_json(scenario_dir / "config_isolation.json", isolation)

    baseline_path = scenario_dir / "fresh_baseline.json"
    if baseline_path.is_file():
        baseline_record = read_json(baseline_path)
        baseline_raw = baseline_record["raw"]
        baseline_measurement = measurement_from_payload(
            baseline_record["measurement"],
            baseline_raw,
        )
    else:
        baseline_raw = run_real_validation_safe(
            cfg,
            copy.deepcopy(runner.POSTGRESQL_DEFAULT_COST_PARAMS),
            output_dir=real_dir,
            label="io_model_recalibration_baseline",
            force_new_replay=True,
        )
        baseline_measurement = measurement_from_payload(usable_raw(baseline_raw), baseline_raw)
        sweet_upper, semantics = runner.physical_target_sweet_upper(
            "io",
            cfg,
            baseline_measurement,
            worker=worker,
            explicit_upper=None,
            min_target_drop_ratio=0.05,
            max_target_drop_ratio=0.10,
            simulated=False,
        )
        limits = runner.sigmoid_linear_resource_limits(
            "io",
            cfg,
            worker,
            explicit_target_upper=sweet_upper,
        )
        semantics["scoring_formula"] = "sigmoid_linear_sweet_band_weighted_mean_v2"
        semantics["scoring_resource_limits"] = limits
        baseline_record = {
            "event": "fresh_baseline",
            "scenario": "io",
            "params": copy.deepcopy(runner.POSTGRESQL_DEFAULT_COST_PARAMS),
            "measurement": baseline_measurement.as_resource(),
            "physical_semantics": semantics,
            "raw": baseline_raw,
        }
        runner.write_json(baseline_path, runner.json_value(baseline_record))

    pruning_source = Path(args.pruning_source_dir).resolve()
    pruning = read_json(pruning_source / "fresh_workload_pruning.json")
    pruning["event"] = "fresh_workload_pruning_resumed_for_io_bridge_top15"
    pruning["resumed_from"] = str(pruning_source)
    pruning["used_new_real_replay"] = False
    runner.write_json(scenario_dir / "fresh_workload_pruning.json", runner.json_value(pruning))

    calibration_path = scenario_dir / "fresh_model_calibration.jsonl"
    completed = set()
    if calibration_path.is_file():
        with calibration_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if row.get("status") == "usable":
                        completed.add(str(row.get("calibration_id")))
    for row in design:
        if row["calibration_id"] in completed:
            continue
        raw = run_real_validation_safe(
            cfg,
            copy.deepcopy(row["params"]),
            output_dir=real_dir,
            label=f"io_model_recalibration_{row['calibration_id'][-3:]}",
            force_new_replay=True,
        )
        record = {
            "event": "fresh_model_calibration_replay",
            **copy.deepcopy(row),
            "status": "usable",
            "measurement": usable_raw(raw),
            "raw_result": runner.json_value(raw),
            "current_run_only": True,
            "used_for_optimizer_candidate_selection": False,
            "used_new_real_replay": True,
        }
        runner.append_jsonl(calibration_path, runner.json_value(record))
        print(
            f"[io-bridge] {row['calibration_id']} complete "
            f"measurement={json.dumps(record['measurement'], sort_keys=True)}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
