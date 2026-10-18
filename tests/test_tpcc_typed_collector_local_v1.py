from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_PATH = ROOT / "collector/tpcc_typed_collector.py"
MANIFEST = ROOT / "config/tpcc_offline_binding_manifest.json"
SPEC = importlib.util.spec_from_file_location("tpcc_typed_collector_local", COLLECTOR_PATH)
assert SPEC is not None and SPEC.loader is not None
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)


def _write_trace(path: Path, *, include_qps: bool = False) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    records: list[dict[str, object]] = [
        {
            "record_type": "header",
            "schema_version": COLLECTOR.TRACE_SCHEMA,
            "manifest_sha256": COLLECTOR.sha256_file(MANIFEST),
            "capture_only": True,
            "outer_execution_authorized": False,
            "correctness_verified": True,
            "qps_included": False,
        }
    ]
    if include_qps:
        records[0]["qps"] = 1
    clock = 1_000_000_000
    grouped: dict[str, list[dict[str, object]]] = {
        name: [] for name in COLLECTOR.TRANSACTION_TYPES
    }
    for statement in manifest["statements"]:
        transaction = statement["transaction_type"]
        grouped[
            "StockLevel" if transaction == "SensitiveStockLevel" else transaction
        ].append(statement)
    for transaction_type, statements in grouped.items():
        transaction_id = "tx-" + transaction_type
        records.append(
            {
                "record_type": "transaction_begin",
                "transaction_id": transaction_id,
                "transaction_type": transaction_type,
                "started_monotonic_ns": clock,
            }
        )
        for statement in statements:
            clock += 1_000_000
            records.append(
                {
                    "record_type": "statement",
                    "transaction_id": transaction_id,
                    "transaction_type": transaction_type,
                    "binding_id": statement["binding_id"],
                    "statement_id": statement["statement_id"],
                    "statement_order": statement["order"],
                    "template_sql_sha256": statement["template_sql_sha256"],
                    "typed_bindings": [
                        {
                            "position": position,
                            "jdbc_type": "INTEGER",
                            "setter": "setInt",
                            "is_null": False,
                            "value_sha256": "0" * 64,
                        }
                        for position in statement["parameter_positions"]
                    ],
                    "started_monotonic_ns": clock,
                    "ended_monotonic_ns": clock + 500_000,
                    "cpu_time_ms": 1,
                    "lock_wait_ms": 0,
                    "io_read_bytes": 2,
                    "io_write_bytes": 3,
                    "io_wait_ms": 0,
                }
            )
        clock += 1_000_000
        records.append(
            {
                "record_type": "transaction_end",
                "transaction_id": transaction_id,
                "ended_monotonic_ns": clock,
                "outcome": "COMMIT",
            }
        )
        clock += 1_000_000
    path.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )


def test_all_34_bindings_and_sensitive_alias_compile(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace)
    result = COLLECTOR.collect_and_validate(MANIFEST, trace)
    assert result["observed_binding_count"] == 34
    assert result["sample_count_by_transaction"]["StockLevel"] == 1
    stock = result["l3_vectors"]["StockLevel"]
    assert stock["cpu_time_ms"] == 3
    assert result["qps_included"] is False
    assert result["execution_authorized"] is False


def test_qps_remains_rejected(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(trace, include_qps=True)
    with pytest.raises(COLLECTOR.EvidenceError, match="QPS/throughput"):
        COLLECTOR.collect_and_validate(MANIFEST, trace)
