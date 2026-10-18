#!/usr/bin/env python3
"""Regression checks for formal pg_stat_statements DB-QPS accounting."""

from __future__ import annotations

import ast
import hashlib
import math
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02/attempt8_workspace"
)
BRIDGE = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02/formal_job_outer_replay.py"
)
RUNNER_PATH = RUNNER_DIR / "formal_pg_point_runner.py"
RUNNER_SOURCE = RUNNER_PATH.read_text(encoding="utf-8")
RUNNER_TREE = ast.parse(RUNNER_SOURCE)


class FormalPgError(RuntimeError):
    pass


namespace = {
    "FormalPgError": FormalPgError,
    "FORMAL_DB_QPS_SOURCE": (
        "pg_stat_statements.sum(calls)_delta/effective_measurement_seconds"
    ),
    "hashlib": hashlib,
    "math": math,
    "re": re,
}
selected_names = {
    "_classify_pg_stat_statement",
    "_formal_db_qps_from_snapshots",
}
selected_source = ["from __future__ import annotations"]
selected_source.extend(
    ast.get_source_segment(RUNNER_SOURCE, node) or ""
    for node in RUNNER_TREE.body
    if isinstance(node, ast.FunctionDef) and node.name in selected_names
)
exec("\n\n".join(selected_source), namespace)
class RunnerNamespace:
    FormalPgError = FormalPgError
    _formal_db_qps_from_snapshots = staticmethod(
        namespace["_formal_db_qps_from_snapshots"]
    )


runner = RunnerNamespace()


def row(query_id: str, query: str, calls: int) -> dict:
    return {"query_id": query_id, "query": query, "calls": calls}


def test_calls_delta_and_monitor_exclusion() -> None:
    start = {
        "rows": [
            row("1", "SELECT min(t.title) FROM title t", 10),
            row("2", "select xact_commit from pg_stat_database", 5),
            row("3", "SELECT query, calls FROM pg_stat_statements", 0),
            row("4", "SET application_name = 'formal'", 1),
        ]
    }
    end = {
        "rows": [
            row("1", "SELECT min(t.title) FROM title t", 190),
            row("2", "select xact_commit from pg_stat_database", 185),
            row("3", "SELECT query, calls FROM pg_stat_statements", 1),
            row("4", "SET application_name = 'formal'", 2),
            row("5", "WITH q AS (SELECT 1) SELECT * FROM q", 18),
        ]
    }
    result = runner._formal_db_qps_from_snapshots(
        start,
        end,
        workload="job",
        effective_measurement_seconds=180,
    )
    assert result["included_calls_delta"] == 198
    assert result["excluded_monitor_calls_delta"] == 181
    assert result["excluded_non_monitor_calls_delta"] == 1
    assert result["db_qps"] == 1.1
    sampler = next(
        item
        for item in result["statements"]
        if item["classification"] == "formal_resource_sampler_sql"
    )
    assert sampler["included_in_db_qps"] is False


def test_counter_regression_and_zero_fail_closed() -> None:
    for start, end in (
        ({"rows": [row("1", "SELECT 1 FROM title", 10)]}, {"rows": [row("1", "SELECT 1 FROM title", 9)]}),
        ({"rows": [row("1", "SELECT 1 FROM title", 10)]}, {"rows": []}),
        ({"rows": []}, {"rows": [row("2", "select xact_commit from pg_stat_database", 180)]}),
    ):
        try:
            runner._formal_db_qps_from_snapshots(
                start,
                end,
                workload="job",
                effective_measurement_seconds=180,
            )
        except runner.FormalPgError:
            pass
        else:
            raise AssertionError("invalid formal DB-QPS evidence was accepted")


def test_bridge_and_baseline_have_no_client_qps_fallback() -> None:
    bridge_source = BRIDGE.read_text(encoding="utf-8")
    optimizer_source = (ROOT / "parameter_optimizer.py").read_text(encoding="utf-8")
    assert 'db_qps_raw = measurement.get("db_qps")' in bridge_source
    assert "must not be used as a DB-QPS fallback" in bridge_source
    assert 'db_qps_source = "formal_benchmark_db_qps"' in bridge_source
    assert "reusable startup baseline lacks positive formal measurement.db_qps" in optimizer_source
    assert 'expected_db_qps_source != "formal_benchmark_db_qps"' in optimizer_source


def main() -> None:
    test_calls_delta_and_monitor_exclusion()
    test_counter_regression_and_zero_fail_closed()
    test_bridge_and_baseline_have_no_client_qps_fallback()
    print("formal DB-QPS contract tests passed", flush=True)


if __name__ == "__main__":
    main()
