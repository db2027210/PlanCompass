#!/usr/bin/env python3
"""Seal the literal JOB-8 SQL contract for exact candidate plan fingerprints."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List


SOURCE_CONFIG_SHA256 = "f24aee5ea7d403ad5129002a37fc2f046f95451a105224b098940538fad5326d"
FIXED_SQL_CONTRACT_FILE_SHA256 = "ba217fe752d789a4ff9a02caa4913b5337dd40cdf09b32e79a832ae67e615de5"
SNAPSHOT_CONTRACT = "sealed_startup_baseline_sql_v1"
QUERY_SEQUENCE = [
    "1a.sql",
    "5a.sql",
    "9a.sql",
    "12a.sql",
    "16a.sql",
    "20a.sql",
    "26a.sql",
    "33a.sql",
]
QUERY_SHA256 = {
    "1a.sql": "53a90dac948641a2f9dd1ee12b68c66354d4c44016494d9e460b2b1879c5d7b2",
    "5a.sql": "b78eed60ed4341b93d663a99ef4268d2f0c589c159a3a56d32027aee85d8f17c",
    "9a.sql": "1e2fcf63ad6fa5286704eb78c9b1c998a12605412fb34ed6f0b92923adc4f543",
    "12a.sql": "871ec385dd6e28440d15b03cf0fdd9c9376b3453bb8ec552c4e5eb1e2afdbeb8",
    "16a.sql": "43cd687d2b4005c6b665762dbf7e5277135ebe738d82e672371571ca255902b9",
    "20a.sql": "eea79cc4c815789c64bf7f4306e8e30db85289a8938bbcd012cb22388264067f",
    "26a.sql": "e68db6c6477fcd41adde48510c3eaec1765ad13fa9e6b899348dc996f6a589cc",
    "33a.sql": "16ec76ddb7fed9155cf98d85c3a6741cc75c1f08e45da9ffda3206a0d8215c2b",
}


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--fixed-sql-contract", type=Path, required=True)
    parser.add_argument("--sql-dir", type=Path, required=True)
    parser.add_argument("--new-attempt", type=Path, required=True)
    parser.add_argument("--config-output", type=Path, required=True)
    args = parser.parse_args()
    source_config = args.source_config.resolve()
    fixed_contract_path = args.fixed_sql_contract.resolve()
    sql_dir = args.sql_dir.resolve()
    attempt = args.new_attempt.resolve()
    config_output = args.config_output.resolve()

    if sha256(source_config) != SOURCE_CONFIG_SHA256:
        raise RuntimeError("SQL-snapshot source config SHA mismatch")
    if sha256(fixed_contract_path) != FIXED_SQL_CONTRACT_FILE_SHA256:
        raise RuntimeError("fixed JOB SQL contract file SHA mismatch")
    fixed_contract = json.loads(fixed_contract_path.read_text(encoding="utf-8"))
    if fixed_contract.get("query_sequence") != QUERY_SEQUENCE:
        raise RuntimeError("fixed JOB SQL query sequence mismatch")
    if fixed_contract.get("query_sha256") != QUERY_SHA256:
        raise RuntimeError("fixed JOB SQL per-file SHA contract mismatch")

    queries: List[Dict[str, Any]] = []
    for filename in QUERY_SEQUENCE:
        sql_path = sql_dir / filename
        actual_sha = sha256(sql_path)
        if actual_sha != QUERY_SHA256[filename]:
            raise RuntimeError(
                f"literal JOB SQL SHA mismatch: {filename}: {actual_sha}"
            )
        sql_text = sql_path.read_text(encoding="utf-8").strip()
        if not re.match(r"^select\b", sql_text, flags=re.IGNORECASE):
            raise RuntimeError(f"literal JOB SQL is not SELECT: {filename}")
        if re.search(r"\$\d+\b", sql_text):
            raise RuntimeError(
                f"literal JOB SQL unexpectedly contains dynamic bindings: {filename}"
            )
        queries.append(
            {
                "queryid": f"fixed-job8:{filename}",
                "calls": 1.0,
                "total_time_ms": 0.0,
                "mean_time_ms": 0.0,
                "rows": 0.0,
                "query": sql_text,
                "source_file": str(sql_path),
                "source_file_sha256": actual_sha,
            }
        )

    snapshot_path = attempt / "sealed_startup_baseline_sql_job8_literal_v2.json"
    snapshot = {
        "schema_version": 1,
        "contract": SNAPSHOT_CONTRACT,
        "source_role": "fixed_job8_literal_sql_contract",
        "fixed_sql_contract_path": str(fixed_contract_path),
        "fixed_sql_contract_file_sha256": FIXED_SQL_CONTRACT_FILE_SHA256,
        "exact_cluster_semantics": fixed_contract.get("exact_cluster_semantics"),
        "query_sequence": copy.deepcopy(QUERY_SEQUENCE),
        "query_sha256": copy.deepcopy(QUERY_SHA256),
        "expected_workload_query_count": len(QUERY_SEQUENCE),
        "dynamic_binding_query_count": 0,
        "queries": queries,
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
    reuse["baseline_sql_expected_query_count"] = len(QUERY_SEQUENCE)
    generation = config.setdefault("_tsgd_generation", {})
    generation["baseline_sql_source"] = "fixed_job8_literal_sql_contract"
    generation["baseline_sql_snapshot_sha256"] = snapshot_sha
    generation["baseline_sql_dynamic_binding_query_count"] = 0
    write_json_new(config_output, config)

    audit = {
        "schema_version": 1,
        "status": "sealed_literal_job8_sql_ready",
        "source_config_path": str(source_config),
        "source_config_sha256": SOURCE_CONFIG_SHA256,
        "fixed_sql_contract_path": str(fixed_contract_path),
        "fixed_sql_contract_file_sha256": FIXED_SQL_CONTRACT_FILE_SHA256,
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": snapshot_sha,
        "workload_query_count": len(queries),
        "dynamic_binding_query_count": 0,
        "config_path": str(config_output),
        "config_sha256": sha256(config_output),
        "imports_grid_candidates_or_acceptance": False,
    }
    write_json_new(attempt / "tsgd_literal_sql_snapshot_seal.json", audit)
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
