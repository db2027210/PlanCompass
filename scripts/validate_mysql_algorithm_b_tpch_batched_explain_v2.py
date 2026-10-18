#!/usr/bin/env python3
"""Prove batched Q1-Q22 EXPLAIN is byte-identical to v1 capture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v1 as v1
from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v2 as v2


def _sha_payload(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-binding", type=Path, required=True)
    parser.add_argument("--worker-id", default="tpch_w1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepared_path = args.prepared_binding.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh validation output required: {output}")
    prepared = v1.base.alg.load(prepared_path)
    workers = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
    }
    worker = workers[args.worker_id]
    if worker.get("scene") != v1.TPCH_SCENE:
        raise RuntimeError("TPC-H validation worker required")
    freeze = v1.base.alg.load(v1.base.FREEZE)

    legacy_worker = dict(worker)
    legacy_log = output.parent / f".{output.stem}.legacy_clients.jsonl"
    legacy_worker["formal_client_log"] = str(legacy_log)
    legacy_started = time.monotonic()
    legacy = v1.full_suite_capture_scene_plans(
        freeze, v1.TPCH_SCENE, legacy_worker,
    )
    legacy_seconds = time.monotonic() - legacy_started

    batched_worker = dict(worker)
    batched_log = output.parent / f".{output.stem}.batched_clients.jsonl"
    batched_worker["formal_client_log"] = str(batched_log)
    batched_started = time.monotonic()
    batched = v2.full_suite_capture_scene_plans(
        freeze, v1.TPCH_SCENE, batched_worker,
    )
    batched_seconds = time.monotonic() - batched_started

    if legacy != batched or len(legacy) != 22:
        raise RuntimeError("batched Q1-Q22 plans differ from v1 byte-for-byte")
    document = {
        "schema_version": "mysql_algorithm_b_tpch_batched_explain_validation_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "worker_id": args.worker_id,
        "query_ids": list(range(1, 23)),
        "query_count": 22,
        "read_only_explain_only": True,
        "workload_executed": False,
        "byte_identical_plan_payloads": True,
        "plan_payload_sha256": _sha_payload(legacy),
        "legacy_client_process_count": sum(1 for _ in legacy_log.open()),
        "batched_client_process_count": sum(1 for _ in batched_log.open()),
        "legacy_elapsed_seconds": legacy_seconds,
        "batched_elapsed_seconds": batched_seconds,
        "speedup": legacy_seconds / batched_seconds,
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": v1.base.alg.sha(prepared_path),
        "legacy_ranker_path": str(Path(v1.__file__).resolve()),
        "legacy_ranker_sha256": v1.base.alg.sha(Path(v1.__file__).resolve()),
        "batched_ranker_path": str(Path(v2.__file__).resolve()),
        "batched_ranker_sha256": v1.base.alg.sha(Path(v2.__file__).resolve()),
    }
    v1.base.alg.atomic(output, document)
    print(json.dumps({
        "status": "PASS",
        "output": str(output),
        "legacy_seconds": legacy_seconds,
        "batched_seconds": batched_seconds,
        "speedup": document["speedup"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
