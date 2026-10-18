from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from tpcc_typed_collector import (  # noqa: E402
    EvidenceError,
    TRACE_SCHEMA,
    TRANSACTION_TYPES,
    collect_and_validate,
    sha256_file,
)


MANIFEST = ROOT / "tpcc_offline_binding_manifest.json"


def manifest_value() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def write_trace(
    path: Path,
    *,
    drop_binding: str | None = None,
    qps: bool = False,
    bad_setter: bool = False,
    bad_sql_hash: bool = False,
    bad_sensitive_alias: bool = False,
) -> None:
    manifest = manifest_value()
    by_type = {name: [] for name in TRANSACTION_TYPES}
    for row in manifest["statements"]:
        transaction_type = (
            "StockLevel"
            if row["transaction_type"] == "SensitiveStockLevel"
            else row["transaction_type"]
        )
        by_type[transaction_type].append(row)
    records = [{
        "record_type": "header",
        "schema_version": TRACE_SCHEMA,
        "manifest_sha256": sha256_file(MANIFEST),
        "capture_only": True,
        "outer_execution_authorized": False,
        "correctness_verified": True,
        "qps_included": False,
    }]
    clock = 1_000_000_000
    sql_mutated = False
    alias_mutated = False
    for index, transaction_type in enumerate(TRANSACTION_TYPES):
        transaction_id = "tx-{}".format(index)
        records.append({
            "record_type": "transaction_begin",
            "transaction_id": transaction_id,
            "transaction_type": transaction_type,
            "started_monotonic_ns": clock,
        })
        for row in by_type[transaction_type]:
            if row["binding_id"] == drop_binding:
                continue
            clock += 1_000_000
            event_type = transaction_type
            if (
                bad_sensitive_alias
                and not alias_mutated
                and row["transaction_type"] == "SensitiveStockLevel"
            ):
                event_type = "SensitiveStockLevel"
                alias_mutated = True
            sql_hash = row["template_sql_sha256"]
            if bad_sql_hash and not sql_mutated:
                sql_hash = "f" * 64
                if sql_hash == row["template_sql_sha256"]:
                    sql_hash = "e" * 64
                sql_mutated = True
            records.append({
                "record_type": "statement",
                "transaction_id": transaction_id,
                "transaction_type": event_type,
                "binding_id": row["binding_id"],
                "statement_id": row["statement_id"],
                "statement_order": row["order"],
                "template_sql_sha256": sql_hash,
                "typed_bindings": [{
                    "position": position,
                    "jdbc_type": "INTEGER",
                    "setter": "setTimestamp" if bad_setter else "setInt",
                    "is_null": False,
                    "value_sha256": "0" * 64,
                } for position in row["parameter_positions"]],
                "started_monotonic_ns": clock,
                "ended_monotonic_ns": clock + 500_000,
                "cpu_time_ms": 1,
                "lock_wait_ms": 0,
                "io_read_bytes": 2,
                "io_write_bytes": 3,
                "io_wait_ms": 0,
            })
            clock += 1_000_000
        clock += 1_000_000
        records.append({
            "record_type": "transaction_end",
            "transaction_id": transaction_id,
            "ended_monotonic_ns": clock,
            "outcome": "COMMIT",
        })
        clock += 1_000_000
    if qps:
        records[0]["qps"] = 1.0
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in records) + "\n",
        encoding="utf-8",
    )


class PortableTypedCollectorTests(unittest.TestCase):
    def run_case(self, **kwargs):
        with tempfile.TemporaryDirectory(prefix="tpcc-typed-test-") as raw:
            trace = Path(raw) / "trace.jsonl"
            write_trace(trace, **kwargs)
            return collect_and_validate(MANIFEST, trace)

    def test_complete_34_bindings_and_five_transaction_vectors(self):
        result = self.run_case()
        self.assertEqual(result["binding_count"], 34)
        self.assertEqual(result["observed_binding_count"], 34)
        self.assertEqual(set(result["l3_vectors"]), set(TRANSACTION_TYPES))
        self.assertTrue(result["capture_workload_authorized"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["qps_included"])

    def test_missing_statement_fails_closed(self):
        binding = manifest_value()["statements"][0]["binding_id"]
        with self.assertRaisesRegex(EvidenceError, "typed binding evidence is missing"):
            self.run_case(drop_binding=binding)

    def test_qps_fails_closed(self):
        with self.assertRaisesRegex(EvidenceError, "QPS/throughput"):
            self.run_case(qps=True)

    def test_incompatible_setter_fails_closed(self):
        with self.assertRaisesRegex(EvidenceError, "incompatible JDBC setter/type"):
            self.run_case(bad_setter=True)

    def test_sql_template_hash_tamper_fails_closed(self):
        with self.assertRaisesRegex(EvidenceError, "template_sql_sha256"):
            self.run_case(bad_sql_hash=True)

    def test_sensitive_stock_level_must_use_normalized_type(self):
        self.assertIn(
            "SensitiveStockLevel",
            {row["transaction_type"] for row in manifest_value()["statements"]},
        )
        with self.assertRaisesRegex(EvidenceError, "transaction_type|transaction type"):
            self.run_case(bad_sensitive_alias=True)

    def test_correctness_flag_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="tpcc-typed-test-") as raw:
            trace = Path(raw) / "trace.jsonl"
            write_trace(trace)
            lines = trace.read_text(encoding="utf-8").splitlines()
            header = json.loads(lines[0])
            header["correctness_verified"] = False
            lines[0] = json.dumps(header)
            trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(EvidenceError, "correctness verification"):
                collect_and_validate(MANIFEST, trace)


if __name__ == "__main__":
    unittest.main()
