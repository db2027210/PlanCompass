#!/usr/bin/env python3
"""Replay one sealed PG scene without other Algorithm C lanes for diagnosis.

This is not a formal Algorithm C result. It preserves the scene's workload,
worker allocation, template database, and seven PostgreSQL planner costs while
isolating cross-scene interference as an experimental factor.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    COST_PARAMS,
    apply_pg_params,
    pg_conn_from_worker,
    restore_worker_from_hybench_template,
    run_real_validation_safe,
    worker_with_prepared_restore,
)
from shell.run_pg_algorithm_c_scene import (  # noqa: E402
    baseline_parameter_readback_audit,
    physical_valid,
)


def run_wave(
    cfg: Dict[str, Any],
    workers: List[Dict[str, Any]],
    params: Dict[str, float],
    output_dir: Path,
    repetition: int,
) -> List[Dict[str, Any]]:
    """Restore, verify, and replay both workers concurrently once."""

    def prepare(worker: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        key = str(worker.get("name") or worker.get("container"))
        restore = restore_worker_from_hybench_template(
            cfg, worker, label=f"isolated_baseline_rep{repetition:02d}_{key}",
        )
        if restore.get("status") != "ok":
            raise RuntimeError(f"restore failed for {key}: {restore}")
        readback = apply_pg_params(pg_conn_from_worker(cfg, worker), params)
        audit = baseline_parameter_readback_audit(params, readback)
        if audit.get("passed") is not True:
            raise RuntimeError(f"parameter readback failed for {key}: {audit}")
        return worker, restore

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        prepared = list(pool.map(prepare, workers))

    def replay(item: Tuple[Dict[str, Any], Dict[str, Any]]) -> Dict[str, Any]:
        worker, restore = item
        key = str(worker.get("name") or worker.get("container"))
        row = run_real_validation_safe(
            cfg,
            params,
            output_dir=output_dir / key,
            label=f"isolated_baseline_rep{repetition:02d}_{key}",
            worker=worker_with_prepared_restore(worker),
            force_new_replay=True,
        )
        row["worker_key"] = key
        row["baseline_repetition"] = repetition
        row["restore"] = restore
        row["diagnostic_only_not_formal_algorithm_c_result"] = True
        return row

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        return list(pool.map(replay, prepared))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 2:
        raise ValueError("at least two repetitions are required")
    if args.output_dir.exists():
        raise FileExistsError(f"diagnostic output already exists: {args.output_dir}")
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    params = dict(cfg["initial_params"])
    if set(params) != set(COST_PARAMS):
        raise ValueError("diagnostic may only apply the seven planner costs")
    workers = list(cfg["binary_plan_tuning"]["validation"]["docker_workers"]["workers"])
    if len(workers) != 2 or len({worker["container"] for worker in workers}) != 2:
        raise ValueError("diagnostic requires exactly two distinct scene workers")
    args.output_dir.mkdir(parents=True)
    manifest = {
        "contract": "pg_algorithm_c_single_scene_baseline_isolation_diagnostic_v1",
        "config": str(args.config.resolve()),
        "workers": [worker["container"] for worker in workers],
        "repetitions": args.repetitions,
        "diagnostic_only_not_formal_algorithm_c_result": True,
        "started_at_epoch_s": time.time(),
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    for repetition in range(1, args.repetitions + 1):
        rows = run_wave(cfg, workers, params, args.output_dir, repetition)
        (args.output_dir / f"wave_{repetition:02d}.json").write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        summary = [
            {
                "worker": row["worker_key"],
                "qps": row.get("db_qps"),
                "valid": physical_valid(row),
                "error": row.get("error"),
            }
            for row in rows
        ]
        print(json.dumps({"repetition": repetition, "workers": summary}), flush=True)
        if not all(item["valid"] for item in summary):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
