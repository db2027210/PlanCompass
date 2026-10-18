"""Orchestrate MySQL CPU/memory/IO tests across H/C/CH benchmarks."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from benchmark_estimator import build_resource_estimator
from candidate_search import (
    coordinate_grid_candidates,
    evaluate_candidates,
    gradient_trace_candidates,
)
from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter, SQLClient
from mysql_parameter_optimizer import _build_ranges, load_config, run_optimization, validate_config
from ranking_metrics import evaluate_top15_gate
from resource_utility import ResourceUtilityCalculator


DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "mysql_test_matrix.json"
ClientFactory = Callable[[Mapping[str, Any]], SQLClient]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _endpoint_key(mysql_cfg: Mapping[str, Any]) -> str:
    return f"{mysql_cfg.get('host', '127.0.0.1')}:{int(mysql_cfg.get('port', 3306))}"


def _contextual_estimator_config(
    estimator_cfg: Mapping[str, Any],
    *,
    scenario: str,
    benchmark: str,
) -> Dict[str, Any]:
    context = {"scenario": scenario, "benchmark": benchmark}
    result = copy.deepcopy(dict(estimator_cfg))
    command = result.get("command")
    if isinstance(command, list):
        result["command"] = [str(item).format(**context) for item in command]
    if result.get("cwd"):
        result["cwd"] = str(result["cwd"]).format(**context)
    environment = dict(result.get("environment") or {})
    environment.update({"MYSQL_TEST_SCENARIO": scenario, "MYSQL_TEST_BENCHMARK": benchmark})
    result["environment"] = environment
    if str(result.get("type", "command")) == "trained_models":
        result.setdefault("benchmark", benchmark)
        result["scenario"] = scenario
    return result


def _build_task_estimator(
    estimator_cfg: Mapping[str, Any],
    adapter: MySQLCostAdapter,
) -> Any:
    return build_resource_estimator(adapter, estimator_cfg)


def _calculator(scoring: Mapping[str, Any]) -> ResourceUtilityCalculator:
    return ResourceUtilityCalculator(
        reward_type=str(scoring.get("reward_type", "log")),
        penalty_type=str(scoring.get("penalty_type", "power")),
        overload_threshold=float(scoring.get("overload_threshold", 0.8)),
        score_mode=str(scoring.get("score_mode", "band_target")),
        resource_weights=scoring.get("resource_weights"),
        resource_reward_weights=scoring.get("resource_reward_weights"),
        resource_penalty_weights=scoring.get("resource_penalty_weights"),
        resource_target_ranges=scoring.get("resource_target_ranges"),
    )


def _average_validation(
    estimator: Any,
    params: Dict[str, float],
    repeats: int,
) -> Dict[str, float]:
    samples = []
    details = []
    for _ in range(max(1, repeats)):
        samples.append(estimator(None, params))
        measurements = getattr(estimator, "measurements", None)
        if measurements:
            details.append(measurements[-1])
    result = {
        resource: sum(float(sample[resource]) for sample in samples) / len(samples)
        for resource in ("cpu", "memory", "io")
    }
    for key in ("qps", "latency_p95_ms", "latency_p99_ms"):
        values = [float(row[key]) for row in details if row.get(key) is not None]
        if values:
            result[key] = sum(values) / len(values)
    explains = [sample for row in details for sample in (row.get("explain_samples") or [])]
    if explains:
        result["explain_samples"] = explains
    return result


class MySQLTestOrchestrator:
    """Matrix controller with endpoint isolation, rollback, Top-15 gates, and resume."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self.config = copy.deepcopy(dict(config))
        self.client_factory = client_factory or (lambda mysql_cfg: MySQLCLIClient(mysql_cfg))
        self.endpoint_locks: Dict[str, threading.Lock] = {}
        self.endpoint_locks_guard = threading.Lock()

    def _lock_for(self, endpoint: str) -> threading.Lock:
        with self.endpoint_locks_guard:
            return self.endpoint_locks.setdefault(endpoint, threading.Lock())

    def tasks(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        scenarios = self.config.get("scenarios")
        benchmarks = self.config.get("benchmarks")
        if not isinstance(scenarios, Mapping) or not scenarios:
            raise ValueError("matrix config requires scenarios")
        if not isinstance(benchmarks, Mapping) or not benchmarks:
            raise ValueError("matrix config requires benchmarks")
        tasks: List[Tuple[str, str, Dict[str, Any]]] = []
        # Benchmark-major ordering lets CPU/memory/IO for the same family enter
        # the pool together when three independent scenario workers are configured.
        for benchmark in benchmarks:
            for scenario, scenario_cfg in scenarios.items():
                if not isinstance(scenario_cfg, Mapping):
                    raise ValueError(f"scenario {scenario} must be an object")
                selected = scenario_cfg.get("benchmarks", list(benchmarks))
                if benchmark in selected:
                    tasks.append((str(scenario), str(benchmark), dict(scenario_cfg)))
        return tasks

    def _mysql_config(self, scenario: str, benchmark: str) -> Dict[str, Any]:
        benchmark_cfg = self.config["benchmarks"][benchmark]
        base = benchmark_cfg.get("mysql", {})
        workers = self.config.get("mysql_workers", {})
        override = workers.get(scenario, {}) if isinstance(workers, Mapping) else {}
        return _deep_merge(base, override)

    def prepare(self, output_root: Path) -> Dict[str, Any]:
        workflow = self.config.get("workflow", {})
        algorithms = list(workflow.get("algorithms", ["gradient", "bruteforce"]))
        invalid = sorted(set(algorithms) - {"gradient", "bruteforce"})
        if invalid:
            raise ValueError(f"unknown algorithms: {invalid}")
        plan = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "algorithms": algorithms,
            "top_k": int(workflow.get("top_k", 15)),
            "tasks": [
                {
                    "scenario": scenario,
                    "benchmark": benchmark,
                    "endpoint": _endpoint_key(self._mysql_config(scenario, benchmark)),
                }
                for scenario, benchmark, _ in self.tasks()
            ],
        }
        if plan["top_k"] < 15:
            raise ValueError("workflow.top_k must be at least 15")
        _write_json(output_root / "run_plan.json", plan)
        return plan

    def _task_config(
        self,
        scenario: str,
        benchmark: str,
        scenario_cfg: Mapping[str, Any],
        output_dir: Path,
        estimator_cfg: Mapping[str, Any],
    ) -> Dict[str, Any]:
        benchmark_cfg = self.config["benchmarks"][benchmark]
        merged: Dict[str, Any] = {
            "mysql": self._mysql_config(scenario, benchmark),
            "capability": copy.deepcopy(scenario_cfg["capability"]),
            "cost_parameters": copy.deepcopy(
                scenario_cfg.get("cost_parameters", self.config.get("cost_parameters", {}))
            ),
            "sensitivity_table": copy.deepcopy(
                scenario_cfg.get("sensitivity_table", self.config.get("sensitivity_table", {}))
            ),
            "scoring": copy.deepcopy(scenario_cfg.get("scoring", {})),
            "optimizer": copy.deepcopy(scenario_cfg.get("optimizer", self.config.get("optimizer", {}))),
            "estimator": _contextual_estimator_config(
                estimator_cfg,
                scenario=scenario,
                benchmark=benchmark,
            ),
            "output": {"directory": str(output_dir), "verbose": False},
            "commit_best": False,
        }
        validate_config(merged)
        return merged

    def run_task(
        self,
        scenario: str,
        benchmark: str,
        scenario_cfg: Mapping[str, Any],
        output_root: Path,
        *,
        resume: bool = False,
    ) -> Dict[str, Any]:
        task_dir = output_root / scenario / benchmark
        summary_path = task_dir / "task_summary.json"
        if resume and summary_path.exists():
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
            if prior.get("status") == "completed":
                return prior
        benchmark_cfg = self.config["benchmarks"][benchmark]
        mysql_cfg = self._mysql_config(scenario, benchmark)
        endpoint = _endpoint_key(mysql_cfg)
        with self._lock_for(endpoint):
            client = self.client_factory(mysql_cfg)
            adapter = MySQLCostAdapter(
                client,
                engine_name=str(mysql_cfg.get("engine_name", "default")),
                device_type=int(mysql_cfg.get("device_type", 0)),
            )
            state_path = task_dir / "run_state.json"
            if resume and state_path.exists():
                prior_state = json.loads(state_path.read_text(encoding="utf-8"))
                prior_snapshot = prior_state.get("snapshot_path")
                if prior_state.get("status") != "completed" and prior_snapshot and Path(prior_snapshot).exists():
                    adapter.restore(adapter.load_snapshot(Path(prior_snapshot)))
            snapshot = adapter.snapshot()
            snapshot_path = task_dir / "mysql_cost_snapshot.json"
            adapter.save_snapshot(snapshot_path, snapshot)
            _write_json(
                state_path,
                {
                    "status": "running",
                    "stage": "baseline",
                    "scenario": scenario,
                    "benchmark": benchmark,
                    "endpoint": endpoint,
                    "snapshot_path": str(snapshot_path.resolve()),
                },
            )
            workflow = self.config.get("workflow", {})
            algorithms = list(workflow.get("algorithms", ["gradient", "bruteforce"]))
            top_k = max(15, int(workflow.get("top_k", 15)))
            validation_repeats = max(1, int(workflow.get("validation_repeats", 1)))
            cost_cfg = scenario_cfg.get("cost_parameters", self.config.get("cost_parameters", {}))
            active = [str(name) for name in cost_cfg["active"]]
            baseline_all = {item.name: item.effective_value for item in snapshot}
            baseline = {name: baseline_all[name] for name in active}
            ranges = _build_ranges(cost_cfg, baseline, active)
            capability = {name: float(value) for name, value in scenario_cfg["capability"].items()}
            calculator = _calculator(scenario_cfg.get("scoring", {}))
            score_func = lambda resource: calculator.calculate_utility_score(dict(resource), capability)
            search_cfg = benchmark_cfg.get("search_estimator")
            validation_cfg = benchmark_cfg.get("validation_estimator")
            if not isinstance(search_cfg, Mapping) or not isinstance(validation_cfg, Mapping):
                raise ValueError(f"benchmark {benchmark} requires search_estimator and validation_estimator")
            contextual_validation = _contextual_estimator_config(
                validation_cfg, scenario=scenario, benchmark=benchmark
            )
            validation_estimator = _build_task_estimator(contextual_validation, adapter)
            algorithm_summaries: List[Dict[str, Any]] = []
            best_rows: List[Dict[str, Any]] = []
            try:
                baseline_resource = _average_validation(validation_estimator, baseline, validation_repeats)
                _write_json(
                    task_dir / "baseline.json",
                    {"params": baseline, "resource": baseline_resource, "score": score_func(baseline_resource)},
                )
                for algorithm in algorithms:
                    adapter.restore(snapshot)
                    algorithm_dir = task_dir / algorithm
                    search_task_cfg = self._task_config(
                        scenario,
                        benchmark,
                        scenario_cfg,
                        algorithm_dir / "search",
                        search_cfg,
                    )
                    search_estimator = _build_task_estimator(search_task_cfg["estimator"], adapter)
                    max_candidates = max(
                        top_k,
                        int(workflow.get("max_search_candidates", 256)),
                    )
                    if algorithm == "gradient":
                        result = run_optimization(search_task_cfg, client=client)
                        raw_candidates = gradient_trace_candidates(
                            result,
                            baseline,
                            ranges,
                            minimum_candidates=top_k,
                        )
                        predicted = evaluate_candidates(
                            raw_candidates,
                            search_estimator,
                            score_func,
                            source="gradient",
                            max_candidates=max_candidates,
                        )
                    elif algorithm == "bruteforce":
                        grid_cfg = workflow.get("bruteforce", {})
                        raw_candidates = coordinate_grid_candidates(
                            baseline,
                            ranges,
                            grid_points=max(2, int(grid_cfg.get("grid_points", 15))),
                            combination_max_size=max(2, int(grid_cfg.get("combination_max_size", 4))),
                            combination_values_per_param=max(
                                2, int(grid_cfg.get("combination_values_per_param", 3))
                            ),
                        )
                        predicted = evaluate_candidates(
                            raw_candidates,
                            search_estimator,
                            score_func,
                            source="bruteforce",
                            max_candidates=max_candidates,
                        )
                    else:
                        raise ValueError(f"unknown algorithm {algorithm}")
                    predicted_top = predicted[:top_k]
                    if len(predicted_top) < top_k:
                        raise RuntimeError(
                            f"{scenario}/{benchmark}/{algorithm}: only {len(predicted_top)} candidates"
                        )
                    validation_path = algorithm_dir / "top15_validation.jsonl"
                    if validation_path.exists():
                        validation_path.unlink()
                    validated: List[Dict[str, Any]] = []
                    for rank, row in enumerate(predicted_top, start=1):
                        actual = _average_validation(
                            validation_estimator,
                            dict(row["params"]),
                            validation_repeats,
                        )
                        record = dict(row)
                        record.update(
                            {
                                "predicted_rank": rank,
                                "actual_resource": actual,
                                "actual_score": score_func(actual),
                            }
                        )
                        validated.append(record)
                        _append_jsonl(validation_path, record)
                    gate = evaluate_top15_gate(validated, workflow.get("quality_gate", {}))
                    actual_sorted = sorted(
                        validated,
                        key=lambda row: (-float(row["actual_score"]), str(row["candidate_id"])),
                    )
                    summary = {
                        "algorithm": algorithm,
                        "status": "passed" if gate["passed"] else "quality_gate_failed",
                        "predicted_candidate_count": len(predicted),
                        "validated_candidate_count": len(validated),
                        "quality_gate": gate,
                        "best": actual_sorted[0],
                        "validation_path": str(validation_path.resolve()),
                    }
                    _write_json(algorithm_dir / "algorithm_summary.json", summary)
                    algorithm_summaries.append(summary)
                    best_rows.append(actual_sorted[0])
                best = max(best_rows, key=lambda row: float(row["actual_score"]))
                commit_best = bool(workflow.get("commit_best", False))
                if commit_best:
                    adapter.apply(best["params"])
                else:
                    adapter.restore(snapshot)
                summary = {
                    "status": "completed",
                    "scenario": scenario,
                    "benchmark": benchmark,
                    "endpoint": endpoint,
                    "baseline": {"params": baseline, "resource": baseline_resource},
                    "algorithms": algorithm_summaries,
                    "best": best,
                    "committed": commit_best,
                    "all_quality_gates_passed": all(
                        item["quality_gate"]["passed"] for item in algorithm_summaries
                    ),
                }
                _write_json(summary_path, summary)
                _write_json(
                    state_path,
                    {
                        "status": "completed",
                        "stage": "done",
                        "snapshot_path": str(snapshot_path.resolve()),
                    },
                )
                return summary
            except BaseException as exc:
                adapter.restore(snapshot)
                _write_json(
                    state_path,
                    {
                        "status": "failed",
                        "stage": "restored_after_failure",
                        "error": str(exc),
                        "snapshot_path": str(snapshot_path.resolve()),
                    },
                )
                raise

    def run(
        self,
        output_root: Path,
        *,
        max_parallel: int = 3,
        resume: bool = False,
        prepare_only: bool = False,
    ) -> Dict[str, Any]:
        plan = self.prepare(output_root)
        if prepare_only:
            summary = {"status": "prepared", **plan}
            _write_json(output_root / "matrix_summary.json", summary)
            return summary
        tasks = self.tasks()
        results: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(tasks), max(1, int(max_parallel)))
        ) as executor:
            futures = {
                executor.submit(
                    self.run_task,
                    scenario,
                    benchmark,
                    scenario_cfg,
                    output_root,
                    resume=resume,
                ): (scenario, benchmark)
                for scenario, benchmark, scenario_cfg in tasks
            }
            for future in concurrent.futures.as_completed(futures):
                scenario, benchmark = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "scenario": scenario,
                        "benchmark": benchmark,
                        "error": str(exc),
                    }
                results.append(result)
                print(
                    json.dumps(
                        {
                            "event": "mysql_task_done",
                            "scenario": scenario,
                            "benchmark": benchmark,
                            "status": result.get("status"),
                            "all_quality_gates_passed": result.get("all_quality_gates_passed"),
                            "error": result.get("error"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        results.sort(key=lambda row: (str(row.get("scenario")), str(row.get("benchmark"))))
        summary = {
            "status": "completed" if all(row.get("status") == "completed" for row in results) else "failed",
            "task_count": len(results),
            "passed_task_count": sum(row.get("status") == "completed" for row in results),
            "all_quality_gates_passed": all(
                bool(row.get("all_quality_gates_passed")) for row in results if row.get("status") == "completed"
            ) and all(row.get("status") == "completed" for row in results),
            "tasks": results,
        }
        _write_json(output_root / "matrix_summary.json", summary)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-parallel", type=int)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    workflow = config.get("workflow", {})
    output_dir = args.output_dir or Path(
        workflow.get("output_directory", "history/mysql_matrix")
    ) / datetime.now().strftime("%Y%m%d_%H%M%S")
    orchestrator = MySQLTestOrchestrator(config)
    result = orchestrator.run(
        output_dir.resolve(),
        max_parallel=args.max_parallel or int(workflow.get("max_parallel", 3)),
        resume=args.resume,
        prepare_only=args.prepare_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result["status"] in {"completed", "prepared"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
