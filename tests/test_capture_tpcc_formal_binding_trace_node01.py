from __future__ import annotations

import json
from pathlib import Path

import pytest

from shell import capture_tpcc_formal_binding_trace_node01 as launcher


RUN_ID = "formal_bindcap_tpcc_node01_test"


class LiveProcess:
    returncode = None

    def poll(self):
        return None


def write_trace(path: Path, transactions: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for sequence, query_id in enumerate(transactions, 1):
            handle.write(json.dumps({
                "benchmark": "tpcc",
                "query_id": query_id,
                "run_id": RUN_ID,
                "sequence": sequence,
                "actual_bindings": [],
                "seed_derivation": {"source": "TPCCWorker.rng.nextInt"},
            }) + "\n")


def test_normal_finalization_writes_result_and_proves_five_transactions(tmp_path: Path) -> None:
    trace = tmp_path / "formal_binding_trace.jsonl"
    write_trace(trace, launcher.TPCC_TRANSACTIONS)

    result = launcher.write_capture_result(
        tmp_path, trace, RUN_ID, "candidate-sha", {"completed_total": 5}
    )

    saved = json.loads((tmp_path / "capture_result.json").read_text(encoding="utf-8"))
    assert result == saved
    assert saved["status"] == "FORMAL_BINDING_TRACE_CAPTURED"
    assert saved["transaction_coverage"]["observed_transactions"] == list(
        launcher.TPCC_TRANSACTIONS
    )
    assert saved["trace_rows"] == 5


def test_finalization_rejects_missing_transaction_without_result(tmp_path: Path) -> None:
    trace = tmp_path / "formal_binding_trace.jsonl"
    write_trace(trace, launcher.TPCC_TRANSACTIONS[:-1])

    with pytest.raises(RuntimeError, match="missing TPCC transactions: StockLevel"):
        launcher.write_capture_result(
            tmp_path, trace, RUN_ID, "candidate-sha", {"completed_total": 4}
        )

    assert not (tmp_path / "capture_result.json").exists()


def test_launch_spec_is_typed_and_disallows_general_log() -> None:
    spec = launcher.build_launch_spec("candidate-sha")
    assert spec["benchmark"] == "tpcc"
    assert spec["transactions"] == list(launcher.TPCC_TRANSACTIONS)
    assert spec["general_log_binding_authority"] is False
    assert set(spec["trace_environment"]) == {
        "FORMAL_MYSQL_BINDING_TRACE_PATH",
        "FORMAL_MYSQL_BINDING_TRACE_RUN_ID",
        "FORMAL_MYSQL_BINDING_TRACE_SCHEMA",
        "FORMAL_MYSQL_BINDING_TRACE_BENCHMARK",
        "FORMAL_MYSQL_BINDING_TRACE_SEED_SOURCE",
    }


def test_trace_coverage_rejects_tpch_metadata(tmp_path: Path) -> None:
    trace = tmp_path / "formal_binding_trace.jsonl"
    write_trace(trace, launcher.TPCC_TRANSACTIONS)
    lines = trace.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["benchmark"] = "tpch"
    lines[0] = json.dumps(event)
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="benchmark mismatch"):
        launcher.validate_trace_coverage(trace, RUN_ID)


def test_trace_coverage_rejects_tpch_seed_source(tmp_path: Path) -> None:
    trace = tmp_path / "formal_binding_trace.jsonl"
    write_trace(trace, launcher.TPCC_TRANSACTIONS)
    lines = trace.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["seed_derivation"]["source"] = "TPCHWorker.rng.nextInt"
    lines[0] = json.dumps(event)
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="seed source mismatch"):
        launcher.validate_trace_coverage(trace, RUN_ID)


def test_post_start_gate_requires_runtime_growth_but_not_trace(tmp_path: Path) -> None:
    workload = tmp_path / "workload.stdout.log"
    resources = tmp_path / "resource_samples.jsonl"
    workload.write_text("workload\n", encoding="utf-8")
    resources.write_text("sample\n", encoding="utf-8")

    result = launcher.check_post_start_gate(LiveProcess(), workload, resources, 0, 0)

    assert result["workload_bytes_after"] > 0
    assert result["resource_bytes_after"] > 0


def test_post_start_gate_rejects_missing_runtime_growth(tmp_path: Path) -> None:
    workload = tmp_path / "workload.stdout.log"
    resources = tmp_path / "resource_samples.jsonl"
    workload.write_text("workload\n", encoding="utf-8")
    resources.write_text("sample\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="workload log did not grow"):
        launcher.check_post_start_gate(
            LiveProcess(), workload, resources,
            workload.stat().st_size, resources.stat().st_size,
        )


def test_trace_growth_and_five_transaction_coverage_are_post_measurement(tmp_path: Path) -> None:
    trace = tmp_path / "formal_binding_trace.jsonl"
    write_trace(trace, launcher.TPCC_TRANSACTIONS)

    coverage = launcher.check_post_measurement_trace(trace, 0, RUN_ID)

    assert coverage["observed_transactions"] == list(launcher.TPCC_TRANSACTIONS)
