from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL_UNDER_TEST = os.environ.get("TPCC_POSTCAPTURE_TOOL_UNDER_TEST")
MODULE_PATH = (
    Path(TOOL_UNDER_TEST).resolve() / "postcapture_promotion.py"
    if TOOL_UNDER_TEST
    else ROOT / "shell/mysql_tpcc_postcapture_promotion_v1.py"
)
CAPTURE = (
    ROOT / "artifacts/mysql_tpcc_typed_runtime_v1/"
    "fe7aaa24d15256f4e771af598ac672cb"
    "72ad9106a02c6dea5e30cc679e55d516"
)


def load_module():
    spec = importlib.util.spec_from_file_location("tpcc_postcapture", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: object) -> None:
    path.write_bytes(
        (
            json.dumps(value, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
    )


def sidecar(module, path: Path) -> None:
    path.with_name(path.name + ".sha256").write_text(
        f"{module.sha256(path)}  {path.name}\n", encoding="ascii"
    )


def synthetic_trace(module, root: Path) -> Path:
    manifest_path = CAPTURE / "config/tpcc_offline_binding_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    rows = [{
        "record_type": "header",
        "schema_version": "mysql_tpcc_jdbc_typed_trace_v1",
        "manifest_sha256": module.BINDING_MANIFEST_SHA256,
        "capture_token": "TPCC_SYNTHETIC_FIXTURE_0001",
        "capture_only": True,
        "outer_execution_authorized": False,
        "correctness_verified": True,
        "qps_included": False,
    }]
    by_type = {name: [] for name in module.TRANSACTIONS}
    for statement in manifest["statements"]:
        transaction = (
            "StockLevel"
            if statement["transaction_type"] == "SensitiveStockLevel"
            else statement["transaction_type"]
        )
        by_type[transaction].append(statement)
    clock = 1_000_000_000
    for transaction in module.TRANSACTIONS:
        transaction_id = f"fixture-{transaction}"
        rows.append({
            "record_type": "transaction_begin",
            "transaction_id": transaction_id,
            "transaction_type": transaction,
            "started_monotonic_ns": clock,
        })
        for statement in by_type[transaction]:
            started = clock + 1_000_000
            ended = started + 1_000_000
            rows.append({
                "record_type": "statement",
                "transaction_id": transaction_id,
                "transaction_type": transaction,
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
                        "value_sha256": f"{position:064x}",
                    }
                    for position in statement["parameter_positions"]
                ],
                "cpu_time_ms": 1.0,
                "lock_wait_ms": 0.0,
                "io_read_bytes": 1.0,
                "io_write_bytes": 1.0,
                "io_wait_ms": 0.1,
                "started_monotonic_ns": started,
                "ended_monotonic_ns": ended,
            })
            clock = ended
        clock += 1_000_000
        rows.append({
            "record_type": "transaction_end",
            "transaction_id": transaction_id,
            "transaction_type": transaction,
            "ended_monotonic_ns": clock,
            "outcome": "COMMIT",
        })
        clock += 1_000_000
    trace = root / "trace.jsonl"
    trace.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    sidecar(module, trace)
    return trace


def synthetic_completion(module, root: Path) -> Path:
    root.mkdir()
    trace = synthetic_trace(module, root)
    collector = module._load_collector(CAPTURE)
    validation = collector.collect_and_validate(
        CAPTURE / "config/tpcc_offline_binding_manifest.json", trace
    )
    validation_path = root / "validation.json"
    l2_path = root / "l2.json"
    l3_path = root / "l3.json"
    write_json(validation_path, validation)
    write_json(l2_path, validation["l2_transactions"])
    write_json(l3_path, {
        "vectors": validation["l3_vectors"],
        "qps_included": False,
    })
    miscellaneous = {}
    for name in (
        "coverage", "resource_readback", "effective_readback",
        "performance_schema",
    ):
        path = root / f"{name}.json"
        write_json(path, {"synthetic_fixture": True})
        miscellaneous[name] = path
    write_json(miscellaneous["effective_readback"], {
        "synthetic_fixture": True,
        "verified": True,
        "formal_scope": "mysql_only",
        "effective": {
            "cpu_quota_us": 400000,
            "cpu_period_us": 100000,
            "cpuset_cpus": "208-215,464-471",
            "cpuset_mems": "1",
            "memory_bytes": 8589934592,
            "memory_swap_bytes": 0,
            "pids_max": 512,
            "io_read_bps": 1205862400,
            "io_write_bps": 1205862400,
            "io_device_major_minor": "8:0",
            "buffer_pool_bytes": 4294967296,
        },
    })
    gates = {}
    for name in ("runtime_gate", "dynamic_gate"):
        path = root / f"{name}.json"
        write_json(path, {
            "status": "PASS", "passed": True, "synthetic_fixture": True,
        })
        gates[name] = path
    result = {
        "schema_version": "mysql_tpcc_typed_capture_result_v1",
        "status": "PASS_CAPTURE_ONLY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "capture_only": True,
        "r0": False,
        "accepted_round": False,
        "qps_resource_input_prohibited": True,
        "capture_token": "TPCC_SYNTHETIC_FIXTURE_0001",
        "immutable_runtime_bundle_id": module.CAPTURE_BUNDLE_ID,
        "immutable_runtime_bundle_manifest_sha256":
            module.CAPTURE_MANIFEST_SHA256,
        "bundle_bound_collector_sha256": module.COLLECTOR_SHA256,
        "bundle_bound_capture_config_sha256": module.sha256(
            CAPTURE / "capture/tpcc_typed_capture_config_v1.json"
        ),
        "typed_binding_manifest_sha256": module.BINDING_MANIFEST_SHA256,
        "trace_path": str(trace),
        "trace_sha256": module.sha256(trace),
        "collector_validation_path": str(validation_path),
        "collector_validation_sha256": module.sha256(validation_path),
        "collector_l2_path": str(l2_path),
        "collector_l2_sha256": module.sha256(l2_path),
        "collector_l3_path": str(l3_path),
        "collector_l3_sha256": module.sha256(l3_path),
        "coverage_path": str(miscellaneous["coverage"]),
        "coverage_sha256": module.sha256(miscellaneous["coverage"]),
        "resource_readback_path": str(miscellaneous["resource_readback"]),
        "resource_readback_sha256":
            module.sha256(miscellaneous["resource_readback"]),
        "effective_resource_readback_path":
            str(miscellaneous["effective_readback"]),
        "effective_resource_readback_sha256":
            module.sha256(miscellaneous["effective_readback"]),
        "performance_schema_readback_path":
            str(miscellaneous["performance_schema"]),
        "performance_schema_readback_sha256":
            module.sha256(miscellaneous["performance_schema"]),
        "correctness_evidence": {"passed": True},
        "correctness_invariant_passed": True,
        "observed_binding_count": 34,
        "transaction_types": [
            "NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel",
        ],
        "formal_r0_authorized": False,
        "algorithm_c_authorized": False,
        "outer_execution_authorized": False,
        "accepted_round_authorized": False,
        "execution_authorized": False,
    }
    result_path = root / "typed_capture_result.json"
    write_json(result_path, result)
    sidecar(module, result_path)
    completion = {
        "schema_version":
            "mysql_tpcc_typed_capture_completion_authority_v1",
        "status": "PASS_CAPTURE_ONLY_COMPLETE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "synthetic_fixture": True,
        "capture_token": "TPCC_SYNTHETIC_FIXTURE_0001",
        "immutable_runtime_bundle_id": module.CAPTURE_BUNDLE_ID,
        "immutable_runtime_bundle_manifest_sha256":
            module.CAPTURE_MANIFEST_SHA256,
        "typed_capture_result_path": str(result_path),
        "typed_capture_result_sha256": module.sha256(result_path),
        "runtime_process_interference_gate_path": str(gates["runtime_gate"]),
        "runtime_process_interference_gate_sha256":
            module.sha256(gates["runtime_gate"]),
        "dynamic_interference_gate_path": str(gates["dynamic_gate"]),
        "dynamic_interference_gate_sha256":
            module.sha256(gates["dynamic_gate"]),
        "trace_path": str(trace),
        "trace_sha256": module.sha256(trace),
        "collector_validation_sha256": module.sha256(validation_path),
        "collector_l2_sha256": module.sha256(l2_path),
        "collector_l3_sha256": module.sha256(l3_path),
        "coverage_sha256": module.sha256(miscellaneous["coverage"]),
        "resource_readback_sha256":
            module.sha256(miscellaneous["resource_readback"]),
        "effective_resource_readback_sha256":
            module.sha256(miscellaneous["effective_readback"]),
        "performance_schema_readback_sha256":
            module.sha256(miscellaneous["performance_schema"]),
        "capture_workload_authorized": True,
        "capture_result_is_r0": False,
        "capture_result_is_accepted_round": False,
        "formal_r0_authorized": False,
        "algorithm_c_authorized": False,
        "outer_execution_authorized": False,
        "accepted_round_authorized": False,
        "qps_resource_input_prohibited": True,
    }
    completion_path = root / "typed_capture_completion_authority.json"
    write_json(completion_path, completion)
    sidecar(module, completion_path)
    return completion_path


def test_shared_contract_is_exact_and_semantically_checked() -> None:
    module = load_module()
    assert module.verify_shared_contract()["formal10"][
        "target_accepted_rounds"
    ] == 10


def test_synthetic_positive_exercises_full_capture_semantics_but_cannot_publish(
    tmp_path: Path,
) -> None:
    module = load_module()
    completion = synthetic_completion(module, tmp_path / "capture")
    evidence = module.validate_capture_completion(
        completion, allow_synthetic_fixture=True
    )
    assert evidence["validation"]["observed_binding_count"] == 34
    assert set(evidence["validation"]["l3_vectors"]) == set(module.TRANSACTIONS)
    with pytest.raises(module.PromotionError, match="synthetic"):
        module.seal_capture_registry(completion, tmp_path / "registry")


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda rows: rows.__setitem__(0, {**rows[0], "capture_token": "WRONG_TOKEN_123456"}), "trace token"),
        (lambda rows: rows[-2].__setitem__("qps", 99), "QPS/throughput"),
        (lambda rows: rows[2]["typed_bindings"].__setitem__(
            0, {**rows[2]["typed_bindings"][0], "setter": "setString"}
        ), "incompatible JDBC"),
    ],
)
def test_trace_tampering_fails_closed(
    tmp_path: Path, mutator, message: str,
) -> None:
    module = load_module()
    completion = synthetic_completion(module, tmp_path / "capture")
    completion_value = json.loads(completion.read_text())
    result_path = Path(completion_value["typed_capture_result_path"])
    result = json.loads(result_path.read_text())
    trace = Path(result["trace_path"])
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    mutator(rows)
    trace.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    result["trace_sha256"] = module.sha256(trace)
    write_json(result_path, result)
    completion_value["trace_sha256"] = module.sha256(trace)
    completion_value["typed_capture_result_sha256"] = module.sha256(result_path)
    write_json(completion, completion_value)
    sidecar(module, completion)
    sidecar(module, result_path)
    sidecar(module, trace)
    with pytest.raises((module.PromotionError, ValueError), match=message):
        module.validate_capture_completion(
            completion, allow_synthetic_fixture=True
        )


def test_cleanup_gate_failure_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    completion = synthetic_completion(module, tmp_path / "capture")
    value = json.loads(completion.read_text())
    gate = Path(value["dynamic_interference_gate_path"])
    write_json(gate, {"status": "FAIL", "passed": False})
    value["dynamic_interference_gate_sha256"] = module.sha256(gate)
    write_json(completion, value)
    sidecar(module, completion)
    with pytest.raises(module.PromotionError, match="dynamic_gate did not pass"):
        module.validate_capture_completion(
            completion, allow_synthetic_fixture=True
        )


def test_strict_elapsed_cadence_rejects_known_pseudo_coverage() -> None:
    module = load_module()
    assert module.strict_elapsed_cadence(
        list(range(1_000, 181_000, 1_000)),
        start_ms=0, end_ms=180_000,
    )["real_elapsed_coverage"] == 1
    with pytest.raises(module.PromotionError, match="invalid strict"):
        module.strict_elapsed_cadence(
            [2_800, 2_810] + list(range(3_810, 180_811, 1_000)),
            start_ms=0, end_ms=180_810,
        )
    alternating = []
    now = 0
    for index in range(200):
        now += 100 if index % 2 == 0 else 900
        alternating.append(now)
    with pytest.raises(module.PromotionError, match="invalid strict"):
        module.strict_elapsed_cadence(
            alternating, start_ms=0, end_ms=100_000
        )


def test_sampling_gate_requires_real_elapsed_and_three_samplers() -> None:
    module = load_module()
    value = {
        "measurement_start_epoch_ms": 0,
        "measurement_end_epoch_ms": 180_000,
        "stability_window_seconds": 180,
        "stopped_at_first_stable_valid_boundary": True,
        "resource_epoch_ms": list(range(1_000, 181_000, 1_000)),
        "progress_epoch_ms": list(range(1_000, 181_000, 1_000)),
        "database_counter_epoch_ms": list(range(1_000, 181_000, 1_000)),
        "missing_or_timed_out_sample_count": 0,
    }
    module.verify_sampling_evidence(value, "positive")
    del value["database_counter_epoch_ms"][10:40]
    with pytest.raises(module.PromotionError):
        module.verify_sampling_evidence(value, "missing_db_counter")


def accepted_event(token: str) -> dict:
    return {
        "attempt_token": token,
        "kind": "outer_acceptance",
        "accepted_round": True,
        "fresh_runtime_gate_pass": True,
        "complete_fixed_top5_real_replay": True,
        "outer_qps_acceptance_pass": True,
        "complete_l1_l2_l3_evidence": True,
        "atomic_checkpoint_commit": True,
        "replay_status": "COMPLETED",
        "point_journals_complete": True,
        "fixed_top5_replay_count": 5,
        "capture": False,
        "r0": False,
        "debug_round": False,
    }


def test_strict_counter_counts_only_fully_committed_outer_acceptance() -> None:
    module = load_module()
    events = [
        {"attempt_token": "capture-1", "kind": "capture", "accepted_round": False},
        {"attempt_token": "r0-1", "kind": "r0", "accepted_round": False},
        {"attempt_token": "debug-1", "kind": "debug_round", "accepted_round": False},
        *[accepted_event(f"formal-{index}") for index in range(10)],
    ]
    assert module.strict_accepted_round_count(events) == 10
    bad = accepted_event("failed")
    bad["replay_status"] = "FAILED"
    with pytest.raises(module.PromotionError, match="increment gate"):
        module.strict_accepted_round_count([bad])
    with pytest.raises(module.PromotionError, match="cannot count"):
        module.strict_accepted_round_count([{
            "attempt_token": "capture-bad",
            "kind": "capture",
            "accepted_round": True,
        }])


def test_content_addressed_publish_is_idempotent_and_conflict_closed(
    tmp_path: Path,
) -> None:
    module = load_module()
    identity = {"schema_version": "fixture_v1", "synthetic_fixture": True}
    first = module._publish(tmp_path, identity, {"a.txt": b"a"})
    second = module._publish(tmp_path, identity, {"a.txt": b"a"})
    assert first == second
    (first / "a.txt").chmod(0o644)
    (first / "a.txt").write_bytes(b"tampered")
    with pytest.raises(module.PromotionError, match="differs"):
        module._publish(tmp_path, identity, {"a.txt": b"a"})


def test_debug_and_formal_launchers_bind_point_journals_and_strict_counter() -> None:
    debug_path = (
        Path(TOOL_UNDER_TEST).resolve() / "debug_launcher.py"
        if TOOL_UNDER_TEST
        else ROOT / "shell/mysql_tpcc_postcapture_debug_launcher_v1.py"
    )
    formal_path = (
        Path(TOOL_UNDER_TEST).resolve() / "formal10_launcher.py"
        if TOOL_UNDER_TEST
        else ROOT / "shell/mysql_tpcc_formal10_launcher_v1.py"
    )
    debug = debug_path.read_text()
    formal = formal_path.read_text()
    optimizer = (ROOT / "mysql_parameter_optimizer.py").read_text()
    assert "FormalPointJournal" in debug
    assert "formal_point_journal_root" in debug
    assert "FormalPointJournal" in formal
    assert "verify_formal_campaign_result" in formal
    assert "COMPLETE_10_ACCEPTED_ROUNDS" in formal
    assert "claim_accounting" in optimizer
    assert "atomic_accounting_committed" in optimizer


def test_tool_bundle_verifies_its_content_address_and_project_root(
    tmp_path: Path,
) -> None:
    module = load_module()
    bundle = (
        Path(TOOL_UNDER_TEST).resolve()
        if TOOL_UNDER_TEST
        else module.publish_tool_bundle(tmp_path / "tool")
    )
    manifest = module.verify_tool_bundle(bundle)
    assert manifest["project_root_authority"] == str(ROOT)
    assert manifest["runtime_authority_published"] is False
    assert manifest["synthetic_authority_published"] is False


def test_all_three_sealed_cli_entrypoints_bootstrap_from_clean_cwd(
    tmp_path: Path,
) -> None:
    if TOOL_UNDER_TEST:
        root = Path(TOOL_UNDER_TEST).resolve()
        scripts = (
            root / "postcapture_promotion.py",
            root / "debug_launcher.py",
            root / "formal10_launcher.py",
        )
    else:
        scripts = (
            ROOT / "shell/mysql_tpcc_postcapture_promotion_v1.py",
            ROOT / "shell/mysql_tpcc_postcapture_debug_launcher_v1.py",
            ROOT / "shell/mysql_tpcc_formal10_launcher_v1.py",
        )
    for script in scripts:
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
