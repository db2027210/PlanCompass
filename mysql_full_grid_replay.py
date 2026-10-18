"""Full-factorial MySQL grid replay with real workload validation for every point."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from candidate_search import anchored_log_grid, full_factorial_candidates
from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter, SQLClient
from mysql_parameter_optimizer import _build_ranges, load_config
from mysql_test_orchestrator import (
    MySQLTestOrchestrator,
    _average_validation,
    _build_task_estimator,
    _calculator,
    _contextual_estimator_config,
)
from ranking_metrics import evaluate_top15_gate


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


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _completed_signatures(rows: Sequence[Mapping[str, Any]], retry_failed: bool) -> set:
    latest: Dict[str, str] = {}
    for row in rows:
        key = str(row.get("param_signature") or "")
        if key:
            latest[key] = str(row.get("status") or "failed")
    return {
        key
        for key, status in latest.items()
        if status == "ok" or (status != "ok" and not retry_failed)
    }


def _signature_text(params: Mapping[str, float]) -> str:
    return "|".join(
        f"{name}={float(value):.17g}" for name, value in sorted(params.items())
    )


def _filter_tasks(
    tasks: Iterable[Tuple[str, str, Dict[str, Any]]],
    scenarios: Optional[Sequence[str]],
    benchmarks: Optional[Sequence[str]],
) -> List[Tuple[str, str, Dict[str, Any]]]:
    scenario_set = {item.strip().lower() for item in scenarios or [] if item.strip()}
    benchmark_set = {item.strip().lower() for item in benchmarks or [] if item.strip()}
    selected = [
        item
        for item in tasks
        if (not scenario_set or item[0] in scenario_set)
        and (not benchmark_set or item[1] in benchmark_set)
    ]
    if not selected:
        raise ValueError("task filters selected no scenario/benchmark pairs")
    return selected


class MySQLFullGridReplay:
    """Run every full-factorial point through the configured real benchmark."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self.config = copy.deepcopy(dict(config))
        self.client_factory = client_factory or (lambda mysql_cfg: MySQLCLIClient(mysql_cfg))
        self.matrix = MySQLTestOrchestrator(self.config, client_factory=self.client_factory)
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, endpoint: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(endpoint, threading.Lock())

    def _full_cfg(self) -> Dict[str, Any]:
        workflow = self.config.get("workflow", {})
        value = workflow.get("full_replay", {}) if isinstance(workflow, Mapping) else {}
        return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}

    def _cost_cfg(self, scenario_cfg: Mapping[str, Any]) -> Mapping[str, Any]:
        return scenario_cfg.get("cost_parameters", self.config.get("cost_parameters", {}))

    def _active_params(self, scenario_cfg: Mapping[str, Any]) -> List[str]:
        cost_cfg = self._cost_cfg(scenario_cfg)
        configured = self._full_cfg().get("active_params")
        active = configured if isinstance(configured, list) and configured else cost_cfg.get("active", [])
        names = [str(name) for name in active]
        unknown = sorted(set(names) - set(str(name) for name in cost_cfg.get("active", [])))
        if unknown:
            raise ValueError(f"full_replay.active_params are not enabled cost parameters: {unknown}")
        return names

    def _grid_count_without_database(self, scenario_cfg: Mapping[str, Any]) -> int:
        full_cfg = self._full_cfg()
        points = max(2, int(full_cfg.get("grid_points", 5)))
        explicit = full_cfg.get("parameter_values", {})
        total = 1
        for name in self._active_params(scenario_cfg):
            values = explicit.get(name) if isinstance(explicit, Mapping) else None
            total *= len({float(value) for value in values}) if isinstance(values, list) and values else points
        return total

    def prepare(
        self,
        output_root: Path,
        tasks: Sequence[Tuple[str, str, Dict[str, Any]]],
        *,
        shard_count: int,
        shard_index: int,
        limit: int,
    ) -> Dict[str, Any]:
        rows = []
        for scenario, benchmark, scenario_cfg in tasks:
            total = self._grid_count_without_database(scenario_cfg)
            shard_total = (total + shard_count - 1 - shard_index) // shard_count
            if limit > 0:
                shard_total = min(shard_total, limit)
            mysql_cfg = self.matrix._mysql_config(scenario, benchmark)
            rows.append(
                {
                    "scenario": scenario,
                    "benchmark": benchmark,
                    "endpoint": f"{mysql_cfg.get('host', '127.0.0.1')}:{int(mysql_cfg.get('port', 3306))}",
                    "active_params": self._active_params(scenario_cfg),
                    "full_grid_candidate_count": total,
                    "this_shard_candidate_count": shard_total,
                }
            )
        plan = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "mode": "mysql_full_factorial_real_replay",
            "shard_count": shard_count,
            "shard_index": shard_index,
            "limit_per_task": limit,
            "task_count": len(rows),
            "total_real_replays": sum(int(row["this_shard_candidate_count"]) for row in rows),
            "tasks": rows,
        }
        _write_json(output_root / "full_replay_plan.json", plan)
        return plan

    def _parameter_values(
        self,
        baseline: Mapping[str, float],
        ranges: Mapping[str, Any],
        active_params: Sequence[str],
    ) -> Dict[str, List[float]]:
        full_cfg = self._full_cfg()
        points = max(2, int(full_cfg.get("grid_points", 5)))
        explicit = full_cfg.get("parameter_values", {})
        values: Dict[str, List[float]] = {}
        for name in active_params:
            configured = explicit.get(name) if isinstance(explicit, Mapping) else None
            if isinstance(configured, list) and configured:
                parsed = sorted({float(value) for value in configured})
                if any(not math.isfinite(value) or value <= 0 for value in parsed):
                    raise ValueError(f"invalid explicit full-grid values for {name}")
                values[name] = parsed
            else:
                values[name] = anchored_log_grid(ranges[name], baseline[name], points)
        return values

    def run_task(
        self,
        scenario: str,
        benchmark: str,
        scenario_cfg: Mapping[str, Any],
        output_root: Path,
        *,
        resume: bool,
        retry_failed: bool,
        stop_on_failure: bool,
        shard_count: int,
        shard_index: int,
        limit: int,
    ) -> Dict[str, Any]:
        task_dir = output_root / scenario / benchmark
        summary_path = task_dir / "full_replay_summary.json"
        if resume and not retry_failed and summary_path.exists():
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
            if prior.get("status") == "completed":
                prior["resumed_from_completed"] = True
                prior["attempted_this_run"] = 0
                prior["successes_this_run"] = 0
                prior["failures_this_run"] = 0
                return prior
        mysql_cfg = self.matrix._mysql_config(scenario, benchmark)
        endpoint = f"{mysql_cfg.get('host', '127.0.0.1')}:{int(mysql_cfg.get('port', 3306))}"
        with self._lock_for(endpoint):
            client = self.client_factory(mysql_cfg)
            adapter = MySQLCostAdapter(
                client,
                engine_name=str(mysql_cfg.get("engine_name", "default")),
                device_type=int(mysql_cfg.get("device_type", 0)),
            )
            state_path = task_dir / "full_replay_state.json"
            if resume and state_path.exists():
                prior_state = json.loads(state_path.read_text(encoding="utf-8"))
                prior_snapshot = Path(str(prior_state.get("snapshot_path") or ""))
                if prior_state.get("status") != "completed" and prior_snapshot.is_file():
                    adapter.restore(adapter.load_snapshot(prior_snapshot))
            snapshot = adapter.snapshot()
            snapshot_path = task_dir / "mysql_cost_snapshot.json"
            adapter.save_snapshot(snapshot_path, snapshot)
            cost_cfg = self._cost_cfg(scenario_cfg)
            all_active = [str(name) for name in cost_cfg["active"]]
            active_params = self._active_params(scenario_cfg)
            baseline_all = {item.name: item.effective_value for item in snapshot}
            baseline = {name: baseline_all[name] for name in all_active}
            ranges = _build_ranges(cost_cfg, baseline, all_active)
            parameter_values = self._parameter_values(baseline, ranges, active_params)
            total = math.prod(len(parameter_values[name]) for name in active_params)
            benchmark_cfg = self.config["benchmarks"][benchmark]
            validation_cfg = _contextual_estimator_config(
                benchmark_cfg["validation_estimator"], scenario=scenario, benchmark=benchmark
            )
            validation_estimator = _build_task_estimator(validation_cfg, adapter)
            full_cfg = self._full_cfg()
            predict_before_replay = bool(full_cfg.get("predict_before_replay", True))
            search_estimator = None
            if predict_before_replay:
                search_cfg = _contextual_estimator_config(
                    benchmark_cfg["search_estimator"], scenario=scenario, benchmark=benchmark
                )
                search_estimator = _build_task_estimator(search_cfg, adapter)
            capability = {name: float(value) for name, value in scenario_cfg["capability"].items()}
            calculator = _calculator(scenario_cfg.get("scoring", {}))
            score_func = lambda resource: calculator.calculate_utility_score(dict(resource), capability)
            repeats = max(1, int(full_cfg.get("validation_repeats", 1)))
            checkpoint_every = max(1, int(full_cfg.get("checkpoint_every", 1)))
            results_path = task_dir / "full_replay_results.jsonl"
            existing_rows = _read_jsonl(results_path) if resume else []
            if not resume and results_path.exists():
                results_path.unlink()
            done = _completed_signatures(existing_rows, retry_failed)
            _write_json(
                task_dir / "grid_values.json",
                {
                    "active_params": active_params,
                    "parameter_values": parameter_values,
                    "full_grid_candidate_count": total,
                },
            )
            _write_json(
                state_path,
                {
                    "status": "running",
                    "stage": "real_replay",
                    "snapshot_path": str(snapshot_path.resolve()),
                    "full_grid_candidate_count": total,
                    "shard_count": shard_count,
                    "shard_index": shard_index,
                },
            )
            started = time.perf_counter()
            attempted = 0
            successes = 0
            failures = 0
            skipped = 0
            selected = 0
            try:
                baseline_resource = _average_validation(validation_estimator, baseline, repeats)
                _write_json(
                    task_dir / "baseline.json",
                    {"params": baseline, "resource": baseline_resource, "score": score_func(baseline_resource)},
                )
                for candidate_index, params in full_factorial_candidates(baseline, parameter_values):
                    if candidate_index % shard_count != shard_index:
                        continue
                    if limit > 0 and selected >= limit:
                        break
                    selected += 1
                    sig = _signature_text(params)
                    if sig in done:
                        skipped += 1
                        continue
                    candidate_started = time.perf_counter()
                    row: Dict[str, Any] = {
                        "candidate_id": f"{scenario}_{benchmark}_grid_{candidate_index}",
                        "candidate_index": candidate_index,
                        "scenario": scenario,
                        "benchmark": benchmark,
                        "source": "mysql_full_factorial_real_replay",
                        "param_signature": sig,
                        "params": params,
                        "shard_count": shard_count,
                        "shard_index": shard_index,
                    }
                    try:
                        if search_estimator is not None:
                            predicted = search_estimator(None, params)
                            row["predicted_resource"] = {
                                name: float(predicted[name]) for name in ("cpu", "memory", "io")
                            }
                            row["predicted_score"] = score_func(predicted)
                        actual = _average_validation(validation_estimator, params, repeats)
                        row["actual_resource"] = actual
                        row["actual_score"] = score_func(actual)
                        row["status"] = "ok"
                        successes += 1
                    except Exception as exc:
                        row["status"] = "failed"
                        row["error"] = str(exc)
                        failures += 1
                    row["elapsed_sec"] = time.perf_counter() - candidate_started
                    row["completed_at"] = datetime.now().isoformat(timespec="seconds")
                    _append_jsonl(results_path, row)
                    attempted += 1
                    if attempted % checkpoint_every == 0:
                        _write_json(
                            state_path,
                            {
                                "status": "running",
                                "stage": "real_replay",
                                "snapshot_path": str(snapshot_path.resolve()),
                                "last_candidate_index": candidate_index,
                                "attempted_this_run": attempted,
                                "successes_this_run": successes,
                                "failures_this_run": failures,
                                "skipped_by_resume": skipped,
                            },
                        )
                    if row["status"] == "failed" and stop_on_failure:
                        raise RuntimeError(f"full replay stopped at {row['candidate_id']}: {row['error']}")
                all_rows = _read_jsonl(results_path)
                latest: Dict[str, Dict[str, Any]] = {}
                for row in all_rows:
                    latest[str(row.get("param_signature") or "")] = row
                ok_rows = [row for row in latest.values() if row.get("status") == "ok"]
                best = max(ok_rows, key=lambda row: float(row["actual_score"])) if ok_rows else None
                gate = None
                predicted_rows = [
                    row
                    for row in ok_rows
                    if isinstance(row.get("predicted_resource"), dict)
                    and row.get("predicted_score") is not None
                ]
                if len(predicted_rows) >= 15:
                    predicted_rows.sort(key=lambda row: -float(row["predicted_score"]))
                    gate = evaluate_top15_gate(
                        predicted_rows[:15],
                        self.config.get("workflow", {}).get("quality_gate", {}),
                    )
                if bool(full_cfg.get("commit_best", False)) and best is not None:
                    adapter.apply(best["params"])
                    committed = True
                else:
                    adapter.restore(snapshot)
                    committed = False
                summary = {
                    "status": "completed",
                    "scenario": scenario,
                    "benchmark": benchmark,
                    "endpoint": endpoint,
                    "full_grid_candidate_count": total,
                    "selected_for_this_shard": selected,
                    "attempted_this_run": attempted,
                    "successes_this_run": successes,
                    "failures_this_run": failures,
                    "skipped_by_resume": skipped,
                    "unique_result_count": len(latest),
                    "unique_success_count": len(ok_rows),
                    "best": best,
                    "top15_quality_gate": gate,
                    "committed": committed,
                    "results_path": str(results_path.resolve()),
                    "elapsed_sec": time.perf_counter() - started,
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
        scenarios: Optional[Sequence[str]] = None,
        benchmarks: Optional[Sequence[str]] = None,
        max_parallel: int = 3,
        resume: bool = False,
        retry_failed: bool = False,
        stop_on_failure: bool = False,
        shard_count: int = 1,
        shard_index: int = 0,
        limit: int = 0,
        prepare_only: bool = False,
    ) -> Dict[str, Any]:
        if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
            raise ValueError("shard parameters must satisfy 0 <= shard_index < shard_count")
        tasks = _filter_tasks(self.matrix.tasks(), scenarios, benchmarks)
        plan = self.prepare(
            output_root,
            tasks,
            shard_count=shard_count,
            shard_index=shard_index,
            limit=max(0, limit),
        )
        if prepare_only:
            summary = {"status": "prepared", **plan}
            _write_json(output_root / "full_replay_matrix_summary.json", summary)
            return summary
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
                    retry_failed=retry_failed,
                    stop_on_failure=stop_on_failure,
                    shard_count=shard_count,
                    shard_index=shard_index,
                    limit=max(0, limit),
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
                            "event": "mysql_full_replay_task_done",
                            "scenario": scenario,
                            "benchmark": benchmark,
                            "status": result.get("status"),
                            "attempted": result.get("attempted_this_run"),
                            "error": result.get("error"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        results.sort(key=lambda row: (str(row.get("benchmark")), str(row.get("scenario"))))
        summary = {
            "status": "completed" if all(row.get("status") == "completed" for row in results) else "failed",
            "task_count": len(results),
            "total_attempted_this_run": sum(int(row.get("attempted_this_run") or 0) for row in results),
            "total_successes_this_run": sum(int(row.get("successes_this_run") or 0) for row in results),
            "total_failures_this_run": sum(int(row.get("failures_this_run") or 0) for row in results),
            "tasks": results,
        }
        _write_json(output_root / "full_replay_matrix_summary.json", summary)
        return summary


def _csv(value: str) -> Optional[List[str]]:
    parsed = [item.strip().lower() for item in value.split(",") if item.strip()]
    return parsed or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--benchmarks", default="")
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    full_cfg = config.get("workflow", {}).get("full_replay", {})
    output_dir = args.output_dir or Path(
        full_cfg.get("output_directory", "history/mysql_full_replay")
    ) / datetime.now().strftime("%Y%m%d_%H%M%S")
    result = MySQLFullGridReplay(config).run(
        output_dir.resolve(),
        scenarios=_csv(args.scenarios),
        benchmarks=_csv(args.benchmarks),
        max_parallel=args.max_parallel,
        resume=args.resume,
        retry_failed=args.retry_failed,
        stop_on_failure=args.stop_on_failure,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        limit=max(0, args.limit),
        prepare_only=args.prepare_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result["status"] in {"completed", "prepared"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
