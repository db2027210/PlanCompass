#!/usr/bin/env python3
"""Prepare an XP formal target-10 run using one immutable valid R0."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
SOURCE_FORMAL = ATTEMPT / "formal10_after_v3g_entry_gate_20260729T1040"
OUTPUT = ATTEMPT / "formal10_after_v3g_entry_gate_reuse_20260729T1425"
SOURCE_SUMMARY = (
    ATTEMPT
    / "candidate_measurements/20260726T041914+0800_2255900/summary.json"
)
SOURCE_HISTORY = (
    ATTEMPT / "output/hybench_xp_optimizer_history_2026-07-26_041755.jsonl"
)
EXPECTED_CONFIG_SHA = (
    "7a79a7fcb6c1d41c9ab1c968a9844becfe4227e3da990f25f34f72f9740a4e08"
)
EXPECTED_SUMMARY_SHA = (
    "6de811a6af4764b8c6ae2d210903edbba50e2922180fd7687c4371a21f587f73"
)
EXPECTED_HISTORY_SHA = (
    "05b2081e7c49e249572ffcad15f7112957cceabd79b53ef37f6afac439b8ec7b"
)
EXPECTED_SAMPLES_SHA = (
    "5e701a514954719c1ab3968459727d79f5edff21f44a023cf053f47671647822"
)
EXPECTED_DB_QPS = 88.11817831972111


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def baseline_event() -> dict[str, Any]:
    matches = []
    for line in SOURCE_HISTORY.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") != "baseline_top_sql":
            continue
        protocol = (
            (event.get("baseline_runtime_resource_stats") or {}).get(
                "candidate_measurement_protocol"
            )
            or {}
        )
        if protocol.get("summary") == str(SOURCE_SUMMARY):
            matches.append(event)
    if len(matches) != 1:
        raise RuntimeError("expected one R0-bound baseline_top_sql event")
    return matches[0]


def normalized_sql(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().rstrip(";").lower())


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if sha256(SOURCE_FORMAL / "config.json") != EXPECTED_CONFIG_SHA:
        raise RuntimeError("source formal config SHA mismatch")
    if sha256(SOURCE_SUMMARY) != EXPECTED_SUMMARY_SHA:
        raise RuntimeError("sealed R0 summary SHA mismatch")
    if sha256(SOURCE_HISTORY) != EXPECTED_HISTORY_SHA:
        raise RuntimeError("sealed R0 history SHA mismatch")

    summary = load_json(SOURCE_SUMMARY)
    sample_path = Path(
        str((summary.get("formal_pg_resource") or {}).get("samples_path") or "")
    )
    if sha256(sample_path) != EXPECTED_SAMPLES_SHA:
        raise RuntimeError("sealed R0 samples SHA mismatch")
    aggregate = (summary["formal_pg_resource"] or {})["aggregate"]
    if (
        summary.get("correctness_passed") is not True
        or summary.get("stable") is not True
        or summary.get("technical_failure") is not False
        or not (0.30 <= float(aggregate["cpu_utilization"]) <= 0.50)
        or not (0.30 <= float(aggregate["memory_utilization"]) <= 0.50)
        or not (0.85 <= float(aggregate["io_utilization"]) <= 0.90)
    ):
        raise RuntimeError("sealed R0 does not pass the exact profile gate")

    event = baseline_event()
    db_view = event.get("baseline_db_view_monitor") or {}
    if abs(float(db_view.get("db_qps")) - EXPECTED_DB_QPS) > 1e-12:
        raise RuntimeError("sealed baseline DB QPS mismatch")
    top = event.get("top")
    if not isinstance(top, list) or not top:
        raise RuntimeError("sealed baseline SQL is missing")
    queries = []
    seen = set()
    for record in top:
        query = normalized_sql(record.get("query"))
        if not re.match(r"^(select|with|insert|update|delete)\b", query):
            continue
        if query in seen:
            raise RuntimeError("sealed baseline SQL contains a duplicate")
        seen.add(query)
        queries.append(record)
    if not queries:
        raise RuntimeError("sealed baseline SQL has no workload queries")

    config = load_json(SOURCE_FORMAL / "config.json")
    protocol = config.get("formal_outer_protocol")
    if not isinstance(protocol, dict):
        raise RuntimeError("formal_outer_protocol is missing")
    state_dir = OUTPUT / "state"
    counter_paths = {
        "formal_search_accepted_counter_path":
            state_dir / "formal_search_accepted_counter.txt",
        "formal_search_replay_counter_path":
            state_dir / "formal_search_replay_counter.txt",
        "formal_startup_baseline_counter_path":
            state_dir / "formal_startup_baseline_counter.txt",
        "global_replay_counter_path":
            state_dir / "global_replay_counter.txt",
    }
    protocol.update({
        name: str(path) for name, path in counter_paths.items()
    })
    protocol.update({
        "formal_search_accepted_count_start": 0,
        "formal_search_replay_count_start": 0,
        "formal_startup_baseline_count_start": 0,
        "global_replay_count_start": 0,
        "global_replay_ordinal_at_binding": 0,
        "initial_real_replay_count": 0,
        "formal_search_accepted_target": 10,
        "accepted_shrink_target": 10,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "global_real_replay_top_k_hard_cap": 5,
        "second_replay_batch_enabled": False,
    })
    config["formal_outer_protocol"] = protocol
    config.setdefault("logging", {})["history_path"] = str(
        OUTPUT / "optimizer_history.jsonl"
    )
    config.setdefault("optimizer", {}).setdefault("gradient", {})[
        "subset_log_dir"
    ] = str(OUTPUT / "subset_logs")
    config.setdefault("output", {})["directory"] = str(OUTPUT)

    OUTPUT.mkdir(parents=True)
    state_dir.mkdir()
    for path in counter_paths.values():
        atomic_text(path, "0\n")

    snapshot_path = OUTPUT / "sealed_startup_baseline_sql.json"
    snapshot = {
        "schema_version": 1,
        "contract": "sealed_startup_baseline_sql_v1",
        "source_history_path": str(SOURCE_HISTORY),
        "source_history_sha256": EXPECTED_HISTORY_SHA,
        "queries": queries,
    }
    atomic_json(snapshot_path, snapshot)

    point_id = SOURCE_SUMMARY.parent.name
    caps = summary["formal_pg_resource"]["caps"]
    initial_params = {
        name: float(value)
        for name, value in (config.get("initial_params") or {}).items()
    }
    if set(initial_params) != {
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost",
    }:
        raise RuntimeError("formal initial parameter set is incomplete")
    query_count = sum(int(record.get("calls") or 0) for record in queries)
    point_result = {
        "status": "complete",
        "point_id": point_id,
        "cost_parameters": initial_params,
        "oom_killed": False,
        "database_restart_count": 0,
        "measurement": {
            "correctness_passed": True,
            "resource_stability": {"passed": True},
            "derived_resource_samples_path": str(sample_path),
            "derived_resource_samples_sha256": EXPECTED_SAMPLES_SHA,
            "resource_denominators": {
                "cpu_quota_cores": float(caps["cpu_quota_cores"]),
                "memory_bytes": float(caps["memory_bytes"]),
                "io_read_bps": float(caps["read_cap_bps"]),
                "io_write_bps": float(caps["write_cap_bps"]),
            },
            "db_qps": EXPECTED_DB_QPS,
            "qps": float((summary.get("benchmark") or {})["xp_qps"]),
            "query_count": query_count,
            "latency_ms": {"p90": None, "p95": None, "p99": None},
            "effective_measurement_seconds": float(
                summary["effective_measurement_seconds"]
            ),
            "error_rate": 0.0,
        },
    }
    point_result_path = OUTPUT / "startup_baseline_point_result.json"
    atomic_json(point_result_path, point_result)

    evidence_path = OUTPUT / "startup_baseline_reclassification.json"
    evidence = {
        "contract": "xp_immutable_startup_baseline_reuse_v1",
        "classification": "formal_startup_baseline",
        "point_id": point_id,
        "point_result_sha256": sha256(point_result_path),
        "source_summary_path": str(SOURCE_SUMMARY),
        "source_summary_sha256": EXPECTED_SUMMARY_SHA,
        "source_history_path": str(SOURCE_HISTORY),
        "source_history_sha256": EXPECTED_HISTORY_SHA,
        "candidate_replay_budget_after": 0,
        "accepted_budget_after": 0,
        "startup_baseline_count_after": 1,
        "new_workload_calls": 0,
        "qps_role": "outer_audit_only",
    }
    atomic_json(evidence_path, evidence)

    config["startup_baseline_reuse"] = {
        "enabled": True,
        "point_result_path": str(point_result_path),
        "point_result_sha256": sha256(point_result_path),
        "reclassification_evidence_path": str(evidence_path),
        "reclassification_evidence_sha256": sha256(evidence_path),
        "baseline_sql_snapshot_path": str(snapshot_path),
        "baseline_sql_snapshot_sha256": sha256(snapshot_path),
        "baseline_sql_expected_query_count": len(queries),
        "startup_baseline_count_before_restart": 0,
        "db_qps_source": "formal_benchmark_db_qps",
    }
    config["_xp_sealed_r0_reuse"] = {
        "contract": "xp_formal_r0_sha_pinned_reuse_v1",
        "source_summary": str(SOURCE_SUMMARY),
        "source_summary_sha256": EXPECTED_SUMMARY_SHA,
        "source_history": str(SOURCE_HISTORY),
        "source_history_sha256": EXPECTED_HISTORY_SHA,
        "new_workload_calls": 0,
        "profile_gate_passed": True,
    }
    output_config = OUTPUT / "config.json"
    atomic_json(output_config, config)

    source_manifest = load_json(SOURCE_FORMAL / "formal_manifest.json")
    manifest = {
        **source_manifest,
        "contract": "xp_formal10_after_v3g_entry_gate_v1",
        "source_failed_formal_dir": str(SOURCE_FORMAL),
        "source_failed_formal_config_sha256": EXPECTED_CONFIG_SHA,
        "output_config": str(output_config),
        "output_config_sha256": sha256(output_config),
        "sealed_startup_baseline_reuse": config["startup_baseline_reuse"],
        "sealed_r0_summary_sha256": EXPECTED_SUMMARY_SHA,
        "sealed_r0_history_sha256": EXPECTED_HISTORY_SHA,
        "sealed_r0_db_qps": EXPECTED_DB_QPS,
        "sealed_baseline_sql_query_count": len(queries),
    }
    atomic_json(OUTPUT / "formal_manifest.json", manifest)
    print(json.dumps({
        "status": "prepared",
        "output": str(OUTPUT),
        "config_sha256": sha256(output_config),
        "point_result_sha256": sha256(point_result_path),
        "evidence_sha256": sha256(evidence_path),
        "baseline_sql_sha256": sha256(snapshot_path),
        "baseline_sql_query_count": len(queries),
        "accepted_start": 0,
        "accepted_target": 10,
        "new_workload_calls": 0,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
