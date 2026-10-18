#!/usr/bin/env python3
"""Validate Q1--Q22 EXPLAIN capture against an idle frozen TPC-H worker."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v1 as ranker
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v18
    as base,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-id", default="tpch_w2")
    parser.add_argument("--prepared-binding", type=Path)
    parser.add_argument("--accounts-binding", type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"tpch_w[12]", args.worker_id) is None:
        raise RuntimeError("TPC-H validation worker differs")
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh validation output required: {output}")
    if output.parent != (base.ROOT / "preflight/mysql_four_scene_20260809").resolve():
        raise RuntimeError("TPC-H plan validation output root differs")

    if (args.prepared_binding is None) != (args.accounts_binding is None):
        raise RuntimeError(
            "prepared and benchmark-account bindings must be supplied together"
        )
    if args.prepared_binding is not None:
        prepared = args.prepared_binding.resolve()
        accounts = args.accounts_binding.resolve()
        for label, path in (("prepared", prepared), ("accounts", accounts)):
            if not path.is_file() or path.is_symlink():
                raise RuntimeError(f"regular {label} binding required: {path}")
        base.PREPARED = prepared
        base.ACCOUNTS = accounts
    base.configure_runtime()
    freeze, workers, point_gate = base.fresh_authority()
    worker = base.worker_with_plan_log(
        workers[args.worker_id], output.with_suffix(".client_processes.jsonl"),
    )
    started = time.monotonic()
    plans = ranker.full_suite_capture_scene_plans(
        freeze, ranker.TPCH_SCENE, worker,
    )
    elapsed = time.monotonic() - started
    if list(plans) != [f"tpch_{query_id:03d}" for query_id in range(1, 23)]:
        raise RuntimeError("captured TPC-H plan registry differs")
    parsed = {name: json.loads(plan) for name, plan in plans.items()}
    document = {
        "schema_version": "mysql_tpch_q1_q22_plan_capture_validation_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "worker_id": args.worker_id,
        "container": worker["container"],
        "host_port": worker["host_port"],
        "database": worker["database"],
        "query_ids": list(range(1, 23)),
        "query_count": 22,
        "elapsed_seconds": elapsed,
        "plans": parsed,
        "plan_sha256": {
            name: hashlib.sha256(plan.encode()).hexdigest()
            for name, plan in plans.items()
        },
        "point_gate": point_gate,
        "freeze_path": str(base.FREEZE),
        "freeze_sha256": base.alg.sha(base.FREEZE),
        "ranker_source_path": str(Path(ranker.__file__).resolve()),
        "ranker_source_sha256": base.alg.sha(Path(ranker.__file__).resolve()),
        "qualification_sql_root": str(ranker.TPCH_QUALIFICATION_SQL_ROOT),
        "rendered_registry_sha256": hashlib.sha256(json.dumps(
            ranker.tpch_full_suite_explains(),
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
        "qps_used_for_resource_prediction_or_score": False,
        "workload_executed": False,
        "read_only_explain_only": True,
    }
    base.alg.atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "query_count": 22, "elapsed_seconds": elapsed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
