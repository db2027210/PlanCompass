"""Export one completed MySQL full-grid replay to the requested run-log schema."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number}: expected object")
        rows.append(value)
    return rows


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fractions(resource: Mapping[str, Any], machine: Mapping[str, float]) -> dict[str, float | None]:
    return {
        "cpu": _finite(resource.get("cpu")) / machine["cpu_cores"] if _finite(resource.get("cpu")) is not None else None,
        "mem": _finite(resource.get("memory")) / machine["mem_gb"] if _finite(resource.get("memory")) is not None else None,
        "io": _finite(resource.get("io")) / machine["disk_bw_mb"] if _finite(resource.get("io")) is not None else None,
    }


def build_document(
    task_dir: Path, *, exp_id: str, benchmark: str, bottleneck: str,
    repeat_id: int, machine: Mapping[str, float], bottleneck_target: float | None,
    min_qps_gain_pct: float = 15.0, min_drop_points: float = 5.0,
    max_drop_points: float = 10.0, clear_threshold: float = .90,
) -> dict[str, Any]:
    baseline_doc = _read_json(task_dir / "baseline.json")
    summary = _read_json(task_dir / "full_replay_summary.json")
    rows = [row for row in _read_jsonl(task_dir / "full_replay_results.jsonl") if row.get("status") == "ok"]
    if summary.get("status") != "completed" or not rows:
        raise RuntimeError("full-grid replay is not complete")
    baseline = baseline_doc["resource"]
    before_qps = _finite(baseline.get("qps"))
    before_p99 = _finite(baseline.get("latency_p99_ms"))
    if before_qps is None or before_qps <= 0 or before_p99 is None:
        raise RuntimeError("baseline QPS/P99 is missing")
    baseline_fraction = _fractions(baseline, machine)
    resource_key = "mem" if bottleneck == "memory" else bottleneck

    evaluated = []
    for row in rows:
        actual = row["actual_resource"]
        qps = _finite(actual.get("qps"))
        p99 = _finite(actual.get("latency_p99_ms"))
        p95 = _finite(actual.get("latency_p95_ms"))
        fractions = _fractions(actual, machine)
        qps_gain = ((qps / before_qps) - 1) * 100 if qps is not None else float("-inf")
        if bottleneck == "none":
            drop = None
            qualifies = (
                qps_gain >= min_qps_gain_pct
                and all(float(baseline_fraction[name]) <= clear_threshold for name in ("cpu", "mem", "io"))
                and all(float(fractions[name]) <= clear_threshold for name in ("cpu", "mem", "io"))
            )
        else:
            drop = (float(baseline_fraction[resource_key]) - float(fractions[resource_key])) * 100
            qualifies = (
                qps_gain >= min_qps_gain_pct and min_drop_points <= drop <= max_drop_points
                and float(baseline_fraction[resource_key]) >= clear_threshold
                and float(fractions[resource_key]) <= clear_threshold
            )
        evaluated.append((row, qps, p95, p99, fractions, qps_gain, drop, qualifies))
    qualifying = [item for item in evaluated if item[-1]]
    # Prefer the mildest qualifying bottleneck reduction, then the largest QPS.
    if qualifying and bottleneck != "none":
        selected = min(qualifying, key=lambda item: (float(item[6]), -float(item[5])))
    elif qualifying:
        selected = max(qualifying, key=lambda item: float(item[5]))
    else:
        selected = max(evaluated, key=lambda item: float(item[5]))
    selected_row, after_qps, _, after_p99, _, _, _, passed = selected

    selected_id = selected_row.get("candidate_id")
    ordered = [item for item in evaluated if item[0].get("candidate_id") != selected_id]
    ordered.append(selected)
    outer_loop = [{
        "iter": 1,
        "params": baseline_doc.get("params", {}),
        "qps": before_qps,
        "latency_p99_ms": before_p99,
        "latency_p95_ms": _finite(baseline.get("latency_p95_ms")),
        "resource": baseline_fraction,
        "utility": _finite(baseline_doc.get("score")),
        "explain_samples": list(baseline.get("explain_samples") or []),
    }] + [{
        "iter": index,
        "params": row[0]["params"],
        "qps": row[1],
        "latency_p99_ms": row[3],
        "latency_p95_ms": row[2],
        "resource": row[4],
        "utility": _finite(row[0].get("actual_score")),
        "explain_samples": list(row[0]["actual_resource"].get("explain_samples") or []),
    } for index, row in enumerate(ordered, 2)]
    best_utility = max((_finite(row[0].get("actual_score")) for row in evaluated), default=None)
    elapsed = sum(_finite(row[0].get("elapsed_sec")) or 0.0 for row in evaluated)
    baseline_utility = _finite(baseline_doc.get("score"))
    final_utility = _finite(selected_row.get("actual_score"))
    delta_u = (
        (final_utility - baseline_utility) / max(abs(baseline_utility), 1e-12) * 100
        if final_utility is not None and baseline_utility is not None else None
    )
    return {
        "meta": {
            "exp_id": exp_id, "benchmark": benchmark, "db": "mysql8",
            "method": "full_grid_real_replay", "bottleneck": bottleneck,
            "bottleneck_target": bottleneck_target, "repeat_id": repeat_id,
            "machine_spec": dict(machine),
        },
        "outer_loop": outer_loop,
        "inner_loop_summary": {
            "total_iterations": len(evaluated), "wall_time_ms": round(elapsed * 1000),
            "best_estimated_utility": best_utility,
            "best_params": selected_row["params"],
        },
        "result": {
            "final_params": selected_row["params"], "delta_u_pct": delta_u,
            "qps_before": before_qps, "qps_after": after_qps,
            "p99_before_ms": before_p99, "p99_after_ms": after_p99,
            "bad_config": not passed, "converge_outer_iters": len(outer_loop),
            "total_wall_time_s": elapsed,
        },
        "audit": {
            "source_task_dir": str(task_dir.resolve()),
            "all_cartesian_candidates_real_validated": len(rows) == int(summary.get("full_grid_candidate_count", -1)),
            "qualifying_candidate_count": len(qualifying),
            "selected_candidate_id": selected_row.get("candidate_id"),
            "qps_gain_pct": selected[5], "bottleneck_drop_pct_points": selected[6],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--bottleneck", choices=("cpu", "memory", "io", "none"), required=True)
    parser.add_argument("--repeat-id", type=int, default=1)
    parser.add_argument("--cpu-cores", type=float, required=True)
    parser.add_argument("--mem-gb", type=float, required=True)
    parser.add_argument("--disk-bw-mb", type=float, required=True)
    parser.add_argument("--bottleneck-target", type=float)
    args = parser.parse_args()
    document = build_document(
        args.task_dir, exp_id=args.exp_id, benchmark=args.benchmark,
        bottleneck=args.bottleneck, repeat_id=args.repeat_id,
        machine={"cpu_cores": args.cpu_cores, "mem_gb": args.mem_gb, "disk_bw_mb": args.disk_bw_mb},
        bottleneck_target=args.bottleneck_target,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
