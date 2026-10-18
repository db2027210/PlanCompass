#!/usr/bin/env python3
"""Focused checks for fail-closed structured double-loop export validation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from export_double_loop_experiment_log import COST_PARAMS, completion_errors


def document(raw_path: Path) -> dict:
    params = {name: float(index + 1) for index, name in enumerate(sorted(COST_PARAMS))}
    return {
        "meta": {
            "bottleneck_target": 0.9,
            "machine_spec": {"cpu_cores": 2, "mem_gb": 8, "disk_bw_mb": 100},
        },
        "outer_loop": [
            {
                "iter": 1,
                "params": params,
                "qps": 120,
                "latency_p99_ms": 10,
                "latency_p95_ms": 8,
                "resource": {"cpu": 0.85, "mem": 0.2, "io": 0.1},
                "utility": 0.8,
                "explain_samples": ["Plan"],
            }
        ],
        "inner_loop_summary": {
            "total_iterations": 10,
            "wall_time_ms": 5,
            "best_estimated_utility": 0.8,
            "best_params": params,
        },
        "result": {
            "final_params": params,
            "delta_u_pct": 5,
            "qps_before": 100,
            "qps_after": 120,
            "p99_before_ms": 12,
            "p99_after_ms": 10,
            "bad_config": False,
            "converge_outer_iters": 1,
            "total_wall_time_s": 30,
        },
        "audit": {
            "independent_final_real_replay": True,
            "final_raw_validation_path": str(raw_path),
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        raw_path = Path(raw) / "final.json"
        raw_path.write_text("{}\n", encoding="utf-8")
        valid = document(raw_path)
        assert completion_errors(valid, successful=True, bottleneck="cpu") == []

        invalid = document(raw_path)
        invalid["outer_loop"][0]["latency_p99_ms"] = None
        invalid["inner_loop_summary"]["best_params"] = {}
        errors = completion_errors(invalid, successful=True, bottleneck="cpu")
        assert "outer_loop[1].latency_p99_ms is missing" in errors
        assert "inner_loop_summary.best_params is not exactly seven cost parameters" in errors
        assert "run is not independently confirmed successful" in completion_errors(
            valid,
            successful=False,
            bottleneck="cpu",
        )
    print("structured double-loop export tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
