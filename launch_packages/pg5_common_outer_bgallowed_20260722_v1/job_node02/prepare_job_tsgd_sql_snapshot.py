#!/usr/bin/env python3
"""Seal the p31 JOB SQL snapshot for startup-baseline reuse."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List


SOURCE_CONFIG_SHA256 = "40fa75d7bbfb4b5627e3e7ce615d492d9c9e914402aa277226cdfddec25b03c2"
SOURCE_HISTORY_SHA256 = "8aa274ea9613b5effedd793f65b06825676803db386bcb194bffc25400c43560"
SNAPSHOT_CONTRACT = "sealed_startup_baseline_sql_v1"
EXPECTED_JOB_QUERY_COUNT = 8


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_new(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def normalize_sql(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().rstrip(";").lower())


def is_job_workload_sql(value: Any) -> bool:
    sql = normalize_sql(value)
    if not sql or "pg_reload_conf(" in sql or "pg_stat_statements_reset(" in sql:
        return False
    if sql.startswith(
        (
            "alter system",
            "begin",
            "commit",
            "rollback",
            "start transaction",
            "set ",
            "reset ",
            "show ",
            "discard ",
        )
    ):
        return False
    return bool(re.match(r"^(select|with|insert|update|delete)\b", sql))


def read_baseline_top_sql(history_path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with history_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("event") == "baseline_top_sql":
                events.append(record)
    if len(events) != 1:
        raise RuntimeError(
            f"expected exactly one baseline_top_sql event, found {len(events)}"
        )
    queries = events[0].get("top")
    if not isinstance(queries, list) or not queries:
        raise RuntimeError("sealed baseline_top_sql event has no top query list")
    if any(not isinstance(item, dict) for item in queries):
        raise RuntimeError("sealed baseline_top_sql contains a non-object query")
    return copy.deepcopy(queries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--source-history", type=Path, required=True)
    parser.add_argument("--new-attempt", type=Path, required=True)
    parser.add_argument("--config-output", type=Path, required=True)
    args = parser.parse_args()
    source_config = args.source_config.resolve()
    source_history = args.source_history.resolve()
    attempt = args.new_attempt.resolve()
    config_output = args.config_output.resolve()

    if sha256(source_config) != SOURCE_CONFIG_SHA256:
        raise RuntimeError("T-SGD model-reuse source config SHA mismatch")
    if sha256(source_history) != SOURCE_HISTORY_SHA256:
        raise RuntimeError("sealed p31 optimizer history SHA mismatch")

    raw_queries = read_baseline_top_sql(source_history)
    workload_queries = [
        item for item in raw_queries if is_job_workload_sql(item.get("query"))
    ]
    if len(workload_queries) != EXPECTED_JOB_QUERY_COUNT:
        raise RuntimeError(
            "sealed p31 baseline does not contain exactly eight JOB workload queries: "
            f"{len(workload_queries)}"
        )
    if len({normalize_sql(item.get("query")) for item in workload_queries}) != len(
        workload_queries
    ):
        raise RuntimeError("sealed p31 JOB workload contains duplicate SQL")

    snapshot_path = attempt / "sealed_startup_baseline_sql_p31_v1.json"
    snapshot = {
        "schema_version": 1,
        "contract": SNAPSHOT_CONTRACT,
        "source_role": "p31_immutable_formal_startup_baseline",
        "source_history_path": str(source_history),
        "source_history_sha256": SOURCE_HISTORY_SHA256,
        "source_event": "baseline_top_sql",
        "source_query_count": len(raw_queries),
        "expected_workload_query_count": EXPECTED_JOB_QUERY_COUNT,
        "queries": raw_queries,
    }
    write_json_new(snapshot_path, snapshot)
    snapshot_path.chmod(0o444)
    snapshot_sha = sha256(snapshot_path)

    config = json.loads(source_config.read_text(encoding="utf-8"))
    reuse = config.get("startup_baseline_reuse")
    if not isinstance(reuse, dict) or reuse.get("enabled") is not True:
        raise RuntimeError("source config does not enable startup_baseline_reuse")
    reuse["baseline_sql_snapshot_path"] = str(snapshot_path)
    reuse["baseline_sql_snapshot_sha256"] = snapshot_sha
    reuse["baseline_sql_expected_query_count"] = EXPECTED_JOB_QUERY_COUNT
    generation = config.setdefault("_tsgd_generation", {})
    generation["baseline_sql_source"] = "sealed_p31_baseline_top_sql"
    generation["baseline_sql_snapshot_sha256"] = snapshot_sha
    generation["baseline_sql_expected_query_count"] = EXPECTED_JOB_QUERY_COUNT
    generation["baseline_sql_control_records_are_filtered"] = True
    write_json_new(config_output, config)

    audit = {
        "schema_version": 1,
        "status": "sealed_startup_baseline_sql_ready",
        "source_config_path": str(source_config),
        "source_config_sha256": SOURCE_CONFIG_SHA256,
        "source_history_path": str(source_history),
        "source_history_sha256": SOURCE_HISTORY_SHA256,
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": snapshot_sha,
        "source_query_count": len(raw_queries),
        "expected_workload_query_count": EXPECTED_JOB_QUERY_COUNT,
        "filtered_control_or_nonworkload_query_count": (
            len(raw_queries) - EXPECTED_JOB_QUERY_COUNT
        ),
        "config_path": str(config_output),
        "config_sha256": sha256(config_output),
        "imports_grid_candidates_or_acceptance": False,
    }
    write_json_new(attempt / "tsgd_sql_snapshot_seal.json", audit)
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
