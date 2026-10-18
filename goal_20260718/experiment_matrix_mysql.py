"""Safe manifest/history audit and scheduling preview for MySQL replay runs.

This module deliberately does not contain a benchmark launcher.  Its CLI is a
dry-run: it validates prior result JSON files and prints (or writes) a plan for
the still-missing logical runs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


DEFAULT_MANIFEST = Path(__file__).resolve().parent / "config" / "mysql_experiment_manifest.json"
BOTTLENECK_ALIASES = {"mem": "memory", "memory": "memory", "cpu": "cpu", "io": "io", "none": "none", "": "none"}
RESOURCE_NAMES = ("cpu", "mem", "io")
SCENARIO_ORDER = ("memory", "cpu", "io", "none")


@dataclass(frozen=True)
class LogicalRun:
    exp_id: str
    benchmark: str
    bottleneck: str
    repeat_id: int
    resource_key: str

    @property
    def run_id(self) -> str:
        return f"{self.exp_id}-r{self.repeat_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "exp_id": self.exp_id,
            "benchmark": self.benchmark,
            "bottleneck": self.bottleneck,
            "repeat_id": self.repeat_id,
            "resource_key": self.resource_key,
        }


def _normal_bottleneck(value: Any) -> str:
    normalized = str(value if value is not None else "none").strip().lower()
    if normalized not in BOTTLENECK_ALIASES:
        raise ValueError(f"unknown bottleneck {value!r}")
    return BOTTLENECK_ALIASES[normalized]


def load_manifest(path: Path | str = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    experiments = payload.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("manifest.experiments must be a non-empty list")
    if int(payload.get("max_parallel", 0)) < 1:
        raise ValueError("manifest.max_parallel must be positive")
    resource_gates = payload.get("runtime_resource_gates", {})
    if resource_gates:
        required_gates = {
            "host_disk_util_high_pct", "host_disk_util_resume_pct",
            "max_write_intensive_parallel", "write_intensive_benchmarks",
        }
        if required_gates - set(resource_gates):
            raise ValueError("manifest.runtime_resource_gates is incomplete")
        high = float(resource_gates["host_disk_util_high_pct"])
        resume = float(resource_gates["host_disk_util_resume_pct"])
        if not 0 <= resume < high <= 100:
            raise ValueError("runtime disk-util gates must satisfy 0 <= resume < high <= 100")
        if int(resource_gates["max_write_intensive_parallel"]) < 1:
            raise ValueError("max_write_intensive_parallel must be positive")
        if int(resource_gates.get(
            "max_write_intensive_parallel_per_storage_domain",
            resource_gates["max_write_intensive_parallel"],
        )) < 1:
            raise ValueError("max_write_intensive_parallel_per_storage_domain must be positive")
        if not isinstance(resource_gates["write_intensive_benchmarks"], list):
            raise ValueError("write_intensive_benchmarks must be a list")
    for item in experiments:
        missing = {"exp_id", "benchmark", "bottleneck", "runs", "resource_key"} - set(item)
        if missing:
            raise ValueError(f"manifest experiment is missing {sorted(missing)}")
        if int(item["runs"]) < 1:
            raise ValueError(f"{item['exp_id']}.runs must be positive")
        if int(item.get("repeat_start", 1)) < 1:
            raise ValueError(f"{item['exp_id']}.repeat_start must be positive")
        _normal_bottleneck(item["bottleneck"])
    workers = payload.get("workers")
    if not isinstance(workers, list) or not workers:
        raise ValueError("manifest.workers must be a non-empty list")
    worker_ids: set[str] = set()
    for worker in workers:
        if not isinstance(worker, Mapping) or not str(worker.get("worker_id", "")).strip():
            raise ValueError("each worker needs a worker_id")
        worker_id = str(worker["worker_id"])
        if worker_id in worker_ids:
            raise ValueError(f"duplicate worker_id {worker_id}")
        worker_ids.add(worker_id)
        capabilities = worker.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            raise ValueError(f"worker {worker_id} needs capabilities")
        if "storage_domain" in worker and not str(worker["storage_domain"]).strip():
            raise ValueError(f"worker {worker_id} has an empty storage_domain")
    required_acceptance = {
        "min_bottleneck_drop_pct_points", "max_bottleneck_drop_pct_points",
        "bottleneck_clear_threshold", "min_qps_gain_pct",
    }
    if required_acceptance - set(payload.get("acceptance", {})):
        raise ValueError("manifest.acceptance is incomplete")
    return payload


def expand_runs(manifest: Mapping[str, Any]) -> list[LogicalRun]:
    runs: list[LogicalRun] = []
    for item in manifest["experiments"]:
        repeat_start = int(item.get("repeat_start", 1))
        for repeat_id in range(repeat_start, repeat_start + int(item["runs"])):
            runs.append(LogicalRun(
                exp_id=str(item["exp_id"]),
                benchmark=str(item["benchmark"]).strip().lower(),
                bottleneck=_normal_bottleneck(item["bottleneck"]),
                repeat_id=repeat_id,
                resource_key=str(item["resource_key"]),
            ))
    run_ids = [item.run_id for item in runs]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("manifest expands to duplicate run ids")
    semantic_ids = [(item.benchmark, item.bottleneck, item.repeat_id) for item in runs]
    if len(semantic_ids) != len(set(semantic_ids)):
        raise ValueError("manifest repeats a benchmark/bottleneck/repeat_id tuple")
    return runs


def _number(value: Any, path: str, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path} must be numeric")
        return None
    return float(value)


def validate_result_schema(payload: Any) -> list[str]:
    """Return all standard-log schema errors; an empty result means complete."""
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["root must be an object"]
    for section in ("meta", "outer_loop", "inner_loop_summary", "result"):
        if section not in payload:
            errors.append(f"missing {section}")
    if errors:
        return errors
    meta, outer, inner, result = payload["meta"], payload["outer_loop"], payload["inner_loop_summary"], payload["result"]
    if not isinstance(meta, Mapping): errors.append("meta must be an object")
    if not isinstance(outer, list) or not outer: errors.append("outer_loop must be a non-empty list")
    if not isinstance(inner, Mapping): errors.append("inner_loop_summary must be an object")
    if not isinstance(result, Mapping): errors.append("result must be an object")
    if errors:
        return errors
    for key in ("exp_id", "benchmark", "db", "method", "bottleneck", "bottleneck_target", "repeat_id", "machine_spec"):
        if key not in meta: errors.append(f"missing meta.{key}")
    for key in ("exp_id", "benchmark", "db", "method", "bottleneck"):
        if key in meta and (not isinstance(meta[key], str) or not meta[key].strip()):
            errors.append(f"meta.{key} must be a non-empty string")
    if "bottleneck_target" in meta and meta["bottleneck_target"] is not None:
        _number(meta["bottleneck_target"], "meta.bottleneck_target", errors)
    if "repeat_id" in meta and (isinstance(meta["repeat_id"], bool) or not isinstance(meta["repeat_id"], int) or meta["repeat_id"] < 1):
        errors.append("meta.repeat_id must be a positive integer")
    if isinstance(meta.get("machine_spec"), Mapping):
        for key in ("cpu_cores", "mem_gb", "disk_bw_mb"):
            if key not in meta["machine_spec"]: errors.append(f"missing meta.machine_spec.{key}")
    elif "machine_spec" in meta: errors.append("meta.machine_spec must be an object")
    for index, row in enumerate(outer if isinstance(outer, list) else []):
        prefix = f"outer_loop[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{prefix} must be an object"); continue
        for key in ("iter", "params", "qps", "latency_p99_ms", "latency_p95_ms", "resource", "utility", "explain_samples"):
            if key not in row: errors.append(f"missing {prefix}.{key}")
        if isinstance(row.get("resource"), Mapping):
            for name in RESOURCE_NAMES:
                if name not in row["resource"]: errors.append(f"missing {prefix}.resource.{name}")
                else: _number(row["resource"][name], f"{prefix}.resource.{name}", errors)
        elif "resource" in row: errors.append(f"{prefix}.resource must be an object")
        if "params" in row and not isinstance(row["params"], Mapping): errors.append(f"{prefix}.params must be an object")
        if "explain_samples" in row and not isinstance(row["explain_samples"], list): errors.append(f"{prefix}.explain_samples must be a list")
        for key in ("qps", "latency_p99_ms", "latency_p95_ms", "utility"):
            if key in row: _number(row[key], f"{prefix}.{key}", errors)
    for key in ("total_iterations", "wall_time_ms", "best_estimated_utility", "best_params"):
        if key not in inner: errors.append(f"missing inner_loop_summary.{key}")
    for key in ("total_iterations", "wall_time_ms", "best_estimated_utility"):
        if key in inner: _number(inner[key], f"inner_loop_summary.{key}", errors)
    if "best_params" in inner and not isinstance(inner["best_params"], Mapping): errors.append("inner_loop_summary.best_params must be an object")
    for key in ("final_params", "delta_u_pct", "qps_before", "qps_after", "p99_before_ms", "p99_after_ms", "bad_config", "converge_outer_iters", "total_wall_time_s"):
        if key not in result: errors.append(f"missing result.{key}")
    for key in ("qps_before", "qps_after", "p99_before_ms", "p99_after_ms", "delta_u_pct", "total_wall_time_s"):
        if key in result: _number(result[key], f"result.{key}", errors)
    if "final_params" in result and not isinstance(result["final_params"], Mapping): errors.append("result.final_params must be an object")
    if "bad_config" in result and not isinstance(result["bad_config"], bool): errors.append("result.bad_config must be boolean")
    return errors


def evaluate_acceptance(payload: Mapping[str, Any], acceptance: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the requested QPS and, for bottleneck runs, resource gates."""
    schema_errors = validate_result_schema(payload)
    if schema_errors:
        return {"passed": False, "schema_errors": schema_errors, "gates": {}}
    result = payload["result"]
    before, after = float(result["qps_before"]), float(result["qps_after"])
    qps_gain = ((after / before) - 1.0) * 100.0 if before > 0 else float("-inf")
    gates: dict[str, bool] = {
        "not_bad_config": not result["bad_config"],
        "qps_gain": qps_gain >= float(acceptance["min_qps_gain_pct"]),
        # A tuning run must actually change the database configuration.  This
        # prevents cache-warm replay drift from being counted as an optimizer
        # success when final_params is identical to the authoritative baseline.
        "config_changed": dict(result["final_params"]) != dict(payload["outer_loop"][0]["params"]),
    }
    bottleneck = _normal_bottleneck(payload["meta"]["bottleneck"])
    detail: dict[str, Any] = {"qps_gain_pct": qps_gain}
    if bottleneck != "none":
        resource_name = "mem" if bottleneck == "memory" else bottleneck
        initial = float(payload["outer_loop"][0]["resource"][resource_name])
        final = float(payload["outer_loop"][-1]["resource"][resource_name])
        drop_points = (initial - final) * 100.0
        gates.update({
            "bottleneck_drop_5_to_10_points": (
                float(acceptance["min_bottleneck_drop_pct_points"]) <= drop_points
                <= float(acceptance["max_bottleneck_drop_pct_points"])
            ),
            "bottleneck_cleared": final <= float(acceptance["bottleneck_clear_threshold"]),
        })
        detail.update({"bottleneck_initial": initial, "bottleneck_final": final, "bottleneck_drop_pct_points": drop_points})
    else:
        initial_resource = payload["outer_loop"][0]["resource"]
        final_resource = payload["outer_loop"][-1]["resource"]
        resource_changes = {
            name: float(final_resource[name]) - float(initial_resource[name])
            for name in RESOURCE_NAMES
        }
        gates["at_least_one_resource_increased"] = any(
            change > 0.0 for change in resource_changes.values()
        )
        detail["resource_changes"] = resource_changes
    return {"passed": all(gates.values()), "schema_errors": [], "gates": gates, **detail}


def _candidate_json_files(roots: Iterable[Path | str]) -> list[Path]:
    files: set[Path] = set()
    for value in roots:
        root = Path(value)
        if root.is_file() and root.suffix == ".json": files.add(root.resolve())
        elif root.is_dir(): files.update(path.resolve() for path in root.rglob("*.json"))
    return sorted(files)


def audit_history(manifest: Mapping[str, Any], roots: Iterable[Path | str]) -> dict[str, Any]:
    runs = expand_runs(manifest)
    valid: list[tuple[Path, Mapping[str, Any], dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for path in _candidate_json_files(roots):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rejected.append({"path": str(path), "reason": f"invalid_json: {exc}"}); continue
        gate = evaluate_acceptance(payload, manifest["acceptance"])
        if gate["passed"]:
            valid.append((path, payload, gate))
        else:
            rejected.append({"path": str(path), "reason": "schema_or_acceptance", "evaluation": gate})
    used: set[Path] = set()
    reused: list[dict[str, Any]] = []
    missing: list[LogicalRun] = []
    for run in runs:
        match = next(((path, payload, gate) for path, payload, gate in valid if path not in used
            and str(payload["meta"]["benchmark"]).strip().lower() == run.benchmark
            and str(payload["meta"]["exp_id"]).strip().upper() == run.exp_id.upper()
            and _normal_bottleneck(payload["meta"]["bottleneck"]) == run.bottleneck
            and int(payload["meta"]["repeat_id"]) == run.repeat_id
            and str(payload["meta"]["db"]).strip().lower() == str(manifest["db"]).strip().lower()
            and str(payload["meta"]["method"]).strip().lower() == str(manifest["method"]).strip().lower()), None)
        if match is None:
            missing.append(run)
        else:
            path, _, gate = match; used.add(path)
            reused.append({**run.as_dict(), "history_path": str(path), "acceptance": gate})
    return {"total_runs": len(runs), "reused_count": len(reused), "missing_count": len(missing), "reused": reused, "missing": [r.as_dict() for r in missing], "rejected_history": rejected}


def schedule_preview(
    missing: Sequence[Mapping[str, Any]], max_parallel: int,
    workers: Sequence[Mapping[str, Any]] | None = None,
    resource_gates: Mapping[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    """Preview dynamic global-queue dispatch in completion waves.

    ``max_parallel`` limits concurrent assignments, not which workers may be
    selected.  Every wave considers the full inventory, so a compatible idle
    worker is never excluded merely because it occurs later in the manifest.
    The waves are only a deterministic preview; the live dispatcher should call
    :func:`dispatch_to_idle_workers` whenever any worker becomes idle.
    """
    if max_parallel < 1: raise ValueError("max_parallel must be positive")
    remaining = [dict(item) for item in missing]
    waves: list[list[dict[str, Any]]] = []
    if workers is None:
        while remaining:
            keys: set[str] = set(); wave: list[dict[str, Any]] = []; deferred: list[dict[str, Any]] = []
            for item in remaining:
                key = str(item["resource_key"])
                if len(wave) < max_parallel and key not in keys:
                    wave.append(item); keys.add(key)
                else: deferred.append(item)
            waves.append(wave); remaining = deferred
        return waves
    inventory = [dict(worker) for worker in workers]
    while remaining:
        assignment_allowed = None
        assignment_committed = None
        if resource_gates:
            write_benchmarks = {
                str(value) for value in resource_gates.get("write_intensive_benchmarks", [])
            }
            per_domain_limit = int(resource_gates.get(
                "max_write_intensive_parallel_per_storage_domain",
                resource_gates.get("max_write_intensive_parallel", max_parallel),
            ))
            write_by_domain: dict[str, int] = {}

            def allow(item: Mapping[str, Any], worker: Mapping[str, Any]) -> bool:
                if str(item.get("benchmark")) not in write_benchmarks:
                    return True
                domain = str(worker.get("storage_domain", "__host__"))
                return write_by_domain.get(domain, 0) < per_domain_limit

            def commit(item: Mapping[str, Any], worker: Mapping[str, Any]) -> None:
                if str(item.get("benchmark")) in write_benchmarks:
                    domain = str(worker.get("storage_domain", "__host__"))
                    write_by_domain[domain] = write_by_domain.get(domain, 0) + 1

            assignment_allowed = allow
            assignment_committed = commit
        wave, deferred = _assign_scenario_diverse(
            remaining, inventory, max_parallel, assignment_allowed, assignment_committed,
        )
        if not wave:
            raise ValueError(f"no compatible worker for: {[item['run_id'] for item in deferred]}")
        waves.append(wave); remaining = deferred
    return waves


def _assign_scenario_diverse(
    pending: Sequence[Mapping[str, Any]],
    workers: Sequence[Mapping[str, Any]],
    slots: int,
    assignment_allowed: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
    assignment_committed: Callable[[Mapping[str, Any], Mapping[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fill slots round-robin by scenario using any compatible idle worker."""
    items = [dict(item) for item in pending]
    available = [dict(worker) for worker in workers]
    assigned_indices: set[int] = set()
    assignments: list[dict[str, Any]] = []
    while len(assignments) < slots and available:
        made_progress = False
        for scenario in SCENARIO_ORDER:
            if len(assignments) >= slots:
                break
            candidate: tuple[int, int] | None = None
            for item_index, item in enumerate(items):
                if item_index in assigned_indices:
                    continue
                if _normal_bottleneck(item.get("bottleneck", "none")) != scenario:
                    continue
                benchmark = str(item["benchmark"])
                worker_index = next((index for index, worker in enumerate(available)
                    if benchmark in {str(value) for value in worker["capabilities"]}
                    and (assignment_allowed is None or assignment_allowed(item, worker))), None)
                if worker_index is not None:
                    candidate = item_index, worker_index
                    break
            if candidate is None:
                continue
            item_index, worker_index = candidate
            worker = available.pop(worker_index)
            if assignment_committed is not None:
                assignment_committed(items[item_index], worker)
            assignment = {**items[item_index], "worker_id": str(worker["worker_id"])}
            if worker.get("storage_domain") is not None:
                assignment["storage_domain"] = str(worker["storage_domain"])
            assignments.append(assignment)
            assigned_indices.add(item_index)
            made_progress = True
        if not made_progress:
            break
    remaining = [item for index, item in enumerate(items) if index not in assigned_indices]
    return assignments, remaining


def dispatch_to_idle_workers(
    pending: Sequence[Mapping[str, Any]],
    idle_workers: Sequence[Mapping[str, Any]],
    active_count: int,
    max_parallel: int,
    *,
    active_benchmarks: Sequence[str] = (),
    active_assignments: Sequence[Mapping[str, Any]] = (),
    resource_gates: Mapping[str, Any] | None = None,
    host_disk_util_pct: float | None = None,
    storage_domain_util_pct: Mapping[str, float] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Assign jobs from one global queue to currently idle compatible workers.

    This is one non-blocking scheduling tick.  Call it again when a worker
    finishes.  It round-robins Memory/CPU/IO/none to maximize scenario-level
    parallelism and preserves FIFO order inside each scenario.  A task may be
    skipped temporarily when no currently idle worker can run its benchmark.
    """
    if max_parallel < 1:
        raise ValueError("max_parallel must be positive")
    if active_count < 0 or active_count > max_parallel:
        raise ValueError("active_count must be between zero and max_parallel")
    slots = max_parallel - active_count
    eligible = [dict(item) for item in pending]
    assignment_allowed = None
    assignment_committed = None
    if resource_gates:
        write_benchmarks = {
            str(value) for value in resource_gates.get("write_intensive_benchmarks", [])
        }
        per_domain_limit = int(resource_gates.get(
            "max_write_intensive_parallel_per_storage_domain",
            resource_gates.get("max_write_intensive_parallel", max_parallel),
        ))
        active_write_by_domain: dict[str, int] = {}
        for active in active_assignments:
            if str(active.get("benchmark")) not in write_benchmarks:
                continue
            domain = str(active.get("storage_domain", "__host__"))
            active_write_by_domain[domain] = active_write_by_domain.get(domain, 0) + 1
        # Backward compatibility for callers which only supplied benchmark
        # names: those jobs conservatively share one host-wide domain.
        for benchmark in active_benchmarks:
            if str(benchmark) in write_benchmarks:
                active_write_by_domain["__host__"] = active_write_by_domain.get("__host__", 0) + 1
        disk_high = (
            host_disk_util_pct is not None
            and float(host_disk_util_pct) >= float(resource_gates.get("host_disk_util_high_pct", 101))
        )
        domain_util = {str(key): float(value) for key, value in (storage_domain_util_pct or {}).items()}
        high_threshold = float(resource_gates.get("host_disk_util_high_pct", 101))

        def allow(item: Mapping[str, Any], worker: Mapping[str, Any]) -> bool:
            if str(item.get("benchmark")) not in write_benchmarks:
                return True
            if disk_high:
                return False
            domain = str(worker.get("storage_domain", "__host__"))
            if domain_util.get(domain, 0.0) >= high_threshold:
                return False
            return active_write_by_domain.get(domain, 0) < per_domain_limit

        def commit(item: Mapping[str, Any], worker: Mapping[str, Any]) -> None:
            if str(item.get("benchmark")) in write_benchmarks:
                domain = str(worker.get("storage_domain", "__host__"))
                active_write_by_domain[domain] = active_write_by_domain.get(domain, 0) + 1

        assignment_allowed = allow
        assignment_committed = commit
    assignments, _ = _assign_scenario_diverse(
        eligible, idle_workers, slots, assignment_allowed, assignment_committed,
    )
    assigned_run_ids = {str(item["run_id"]) for item in assignments}
    remaining = [dict(item) for item in pending if str(item["run_id"]) not in assigned_run_ids]
    return {"assignments": assignments, "pending": remaining}


def build_dry_run_plan(manifest: Mapping[str, Any], history_roots: Iterable[Path | str]) -> dict[str, Any]:
    audit = audit_history(manifest, history_roots)
    waves = schedule_preview(
        audit["missing"], int(manifest["max_parallel"]), manifest["workers"],
        manifest.get("runtime_resource_gates"),
    )
    return {
        "mode": "dry_run_only_no_benchmark_launched",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_total_runs": len(expand_runs(manifest)),
        "history_audit": audit,
        "schedule": {
            "policy": str(manifest.get("dispatch_policy", "global_queue_idle_worker_first")),
            "priority": list(manifest.get("dispatch_priority", [])),
            "max_parallel": int(manifest["max_parallel"]),
            "runtime_resource_gates": dict(manifest.get("runtime_resource_gates", {})),
            "wave_count": len(waves),
            "waves": waves,
            "binding": False,
            "note": "live dispatch re-evaluates the global queue whenever any worker becomes idle",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--history", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, help="optional JSON path for the dry-run plan")
    args = parser.parse_args(argv)
    plan = build_dry_run_plan(load_manifest(args.manifest), args.history)
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
