"""Run one manifest cell with real measurements and export the formal JSON."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Any, Dict, Mapping

from experiment_matrix_mysql import validate_result_schema
from mysql_parameter_optimizer import load_config, run_optimization


def _avg(stats: Any, name: str) -> float | None:
    if not isinstance(stats, Mapping):
        return None
    value = stats.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("avg"), (int, float)):
        return float(value["avg"])
    return None


def _bench(stats: Any, name: str) -> float | None:
    if not isinstance(stats, Mapping) or not isinstance(stats.get("benchmark"), Mapping):
        return None
    value = stats["benchmark"].get(name)
    return float(value) if isinstance(value, (int, float)) else None


def _same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return set(left) == set(right) and all(math.isclose(float(left[k]), float(right[k]), rel_tol=1e-10, abs_tol=1e-12) for k in left)


def _is_bad_config(p99_before_ms: float, p99_after_ms: float, legacy_degraded: Any) -> bool:
    """Flag a configuration only when per-request tail latency regresses materially.

    The optimizer's legacy ``degraded`` value is based on aggregate query time. For
    throughput benchmarks that aggregate grows with the number of completed
    requests, so a faster configuration can otherwise be mislabeled as degraded.
    """
    if p99_before_ms > 0 and p99_after_ms > 0:
        return p99_after_ms > p99_before_ms * 1.10
    return bool(legacy_degraded)


def build_document(args: argparse.Namespace, config: Mapping[str, Any], result: Any, elapsed: float) -> Dict[str, Any]:
    capability = {key: float(value) for key, value in config["capability"].items()}
    machine = config.get("machine_spec") if isinstance(config.get("machine_spec"), Mapping) else capability
    measurements = list(getattr(result, "measurements", []))
    if not measurements:
        raise RuntimeError("optimizer produced no real measurements")
    baseline = measurements[0]
    final = next((row for row in reversed(measurements) if _same_params(row["params"], result.optimized_params)), measurements[-1])
    scores_by_iteration = {int(row["iteration"]): float(row["candidate_score"]) for row in result.history}
    outer = []
    for index, row in enumerate(measurements, 1):
        label = str(row.get("label") or "")
        iteration = None
        if label.startswith("iter_"):
            try:
                iteration = int(label.split("_", 1)[1].split()[0])
            except ValueError:
                iteration = None
        stats = row.get("stats")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        raw_resource = row["resource"]
        outer.append(
            {
                "iter": index,
                "label": row.get("label"),
                "params": row["params"],
                "qps": float(row.get("qps") or _avg(stats, "qps") or 0.0),
                "latency_p99_ms": float(_bench(stats, "latency_p99") or metadata.get("latency_p99_ms") or 0.0),
                "latency_p95_ms": float(_bench(stats, "latency_p95") or metadata.get("latency_p95_ms") or 0.0),
                "resource": {
                    "cpu": float(raw_resource["cpu"]) / capability["cpu"],
                    "mem": float(raw_resource["memory"]) / capability["memory"],
                    "io": float(raw_resource["io"]) / capability["io"],
                },
                "resource_raw": raw_resource,
                "utility": scores_by_iteration.get(iteration, float(result.initial_score) if index == 1 else float(result.final_score)),
                "explain_samples": list(metadata.get("explain_samples") or []),
                "measurement_artifact": metadata.get("summary_path"),
                "measurement_replicates": list(metadata.get("measurement_replicates") or []),
            }
        )
    qps_before = float(baseline.get("qps") or _avg(baseline.get("stats"), "qps") or 0.0)
    qps_after = float(final.get("qps") or _avg(final.get("stats"), "qps") or 0.0)
    p99_before = float(_bench(baseline.get("stats"), "latency_p99") or 0.0)
    p99_after = float(_bench(final.get("stats"), "latency_p99") or 0.0)
    return {
        "meta": {
            "exp_id": args.exp_id,
            "benchmark": args.benchmark,
            "db": "mysql8",
            "method": "gradient_descent",
            "bottleneck": args.bottleneck,
            "bottleneck_target": None if args.bottleneck == "none" else args.bottleneck_target,
            "repeat_id": args.repeat_id,
            "worker_id": args.worker_id,
            "machine_spec": {
                "cpu_cores": float(machine.get("cpu_cores", capability["cpu"])),
                "mem_gb": float(machine.get("mem_gb", capability["memory"])),
                "disk_bw_mb": float(machine.get("disk_bw_mb", capability["io"])),
            },
        },
        "outer_loop": outer,
        "inner_loop_summary": {
            "total_iterations": int(result.iterations),
            "wall_time_ms": elapsed * 1000.0,
            "best_estimated_utility": float(result.final_score),
            "best_params": result.optimized_params,
        },
        "result": {
            "final_params": result.optimized_params,
            "delta_u_pct": float(result.score_improvement_pct or 0.0),
            "qps_before": qps_before,
            "qps_after": qps_after,
            "p99_before_ms": p99_before,
            "p99_after_ms": p99_after,
            "bad_config": _is_bad_config(p99_before, p99_after, result.degraded),
            "converge_outer_iters": int(result.iterations),
            "total_wall_time_s": elapsed,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--bottleneck", choices=("cpu", "memory", "io", "none"), required=True)
    parser.add_argument("--bottleneck-target", type=float, default=0.90)
    parser.add_argument("--repeat-id", type=int, default=1)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    started = time.time()
    result = run_optimization(config)
    document = build_document(args, config, result, time.time() - started)
    errors = validate_result_schema(document)
    if errors:
        raise RuntimeError("formal result schema errors: " + "; ".join(errors))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "qps_gain_pct": (document["result"]["qps_after"] / document["result"]["qps_before"] - 1.0) * 100.0 if document["result"]["qps_before"] else None}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
