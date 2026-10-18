import json
from pathlib import Path

import pytest

from shell.build_tpcc_production_trace_registry import (
    build_trace,
    normalize_sql,
    parse_general_log,
)
from shell.run_tpcc_production_raw_explain import explain_sql, statement_class


def test_normalize_sql_masks_values_but_preserves_shape():
    assert normalize_sql("SELECT * FROM t WHERE a = 7 AND b = 'x'") == "SELECT * FROM T WHERE A = ? AND B = ?"


def test_general_log_parser_keeps_connection_and_line_provenance(tmp_path: Path):
    path = tmp_path / "general.log"
    path.write_text(
        "Time Id Command Argument\n"
        "2026-01-01T00:00:00Z\t7\tQuery\tSELECT * FROM t\n"
        " WHERE a = 7\n",
        encoding="utf-8",
    )
    rows = parse_general_log(path)
    assert rows[0]["connection_id"] == 7
    assert rows[0]["line_number"] == 2
    assert rows[0]["command"] == "Query"
    assert "WHERE a = 7" in rows[0]["argument"]


def test_trace_fails_closed_for_untyped_and_ambiguous_general_log(tmp_path: Path):
    contract = {
        "freeze_id": "freeze",
        "capture_authority": {"server_log_limit": "no JDBC types"},
        "production_benchbase_jar": {"sha256": "frozen"},
        "complete_sql_inventory": {
            "native_statement_count": 2,
            "total_statement_templates": 2,
            "procedures": [{
                "transaction": "NewOrder",
                "weight_percent": 45,
                "statements": [
                    {"statement_id": "q1", "statement_kind": "read", "production_sql_template": "SELECT * FROM t WHERE a = ?", "production_sql_template_sha256": "a", "dynamic": True},
                    {"statement_id": "q2", "statement_kind": "read", "production_sql_template": "SELECT * FROM t WHERE a = ?", "production_sql_template_sha256": "b", "dynamic": True},
                ],
            }],
            "sensitive_statement": {"logical_name": "s", "statement_kind": "read", "production_sql_template": "SELECT 1", "production_sql_template_sha256": "c", "dynamic": False},
        },
    }
    capture = {"production_jar_sha256": "candidate"}
    log = tmp_path / "general.log"
    log.write_text("2026-01-01T00:00:00Z 1 Query SELECT * FROM t WHERE a = 7\n", encoding="utf-8")
    registry, corpus, feature = build_trace(contract, capture, log)
    assert registry["execution_authorized"] is False
    assert registry["binding_authority"]["jdbc_types_proven"] is False
    assert any("ambiguous_statement_shape_events" in reason for reason in registry["failure_reasons"])
    assert corpus["complete_workload"] is False
    assert feature["coverage_scope"] == "read_only_representative_subset_only"


def test_raw_explain_is_non_executing_and_classifies_writes():
    assert statement_class("UPDATE customer SET c_balance = 1") == "UPDATE"
    assert explain_sql("UPDATE customer SET c_balance = 1;") == "EXPLAIN FORMAT=JSON UPDATE customer SET c_balance = 1"
