from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/verify_mysql_tpcc_v2_formal10_readiness.py"
CURRENT_SUCCESSOR = (
    ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v1/"
    "b9ff6702d3876a0a0f9abeb22ad44c96b116dfca46d1cf81688c49b3f8af3857"
)


def module():
    spec = importlib.util.spec_from_file_location("tpcc_v2_readiness", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def production_registry(mod, root: Path, attempt: str) -> tuple[Path, dict]:
    root.mkdir()
    manifest_path = (
        mod.TPCC_CAPTURE_BUNDLE
        / "config/tpcc_offline_binding_manifest.json"
    )
    collector_path = (
        mod.TPCC_CAPTURE_BUNDLE / "collector/tpcc_typed_collector.py"
    )
    binding = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_type = {name: [] for name in (
        "NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"
    )}
    for statement in binding["statements"]:
        transaction = (
            "StockLevel"
            if statement["transaction_type"] == "SensitiveStockLevel"
            else statement["transaction_type"]
        )
        by_type[transaction].append(statement)
    rows = [{
        "record_type": "header",
        "schema_version": "mysql_tpcc_jdbc_typed_trace_v1",
        "manifest_sha256": mod.sha256(manifest_path),
        "capture_token": attempt,
        "capture_only": True,
        "outer_execution_authorized": False,
        "correctness_verified": True,
        "qps_included": False,
    }]
    clock = 1_000_000_000
    for transaction, statements in by_type.items():
        transaction_id = f"{attempt}:{transaction}"
        rows.append({
            "record_type": "transaction_begin",
            "transaction_id": transaction_id,
            "transaction_type": transaction,
            "started_monotonic_ns": clock,
        })
        for statement in statements:
            started = clock + 1_000_000
            ended = started + 1_000_000
            rows.append({
                "record_type": "statement",
                "transaction_id": transaction_id,
                "transaction_type": transaction,
                "binding_id": statement["binding_id"],
                "statement_id": statement["statement_id"],
                "statement_order": statement["order"],
                "template_sql_sha256":
                    statement["template_sql_sha256"],
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
    trace_payload = "".join(
        json.dumps(row, sort_keys=True) + "\n" for row in rows
    ).encode()
    trace_sha = hashlib.sha256(trace_payload).hexdigest()
    db_container = "d" * 64
    client_container = "c" * 64
    datadir = mod.EXPECTED_DB_DATADIR_PREFIX + "attempt/datadir"
    command = {
        "actual_main_class": "com.oltpbenchmark.DBWorkload",
        "classpath_overlay_first": True,
        "attempt_id": attempt,
        "db_container_id": db_container,
        "client_container_id": client_container,
        "synthetic_fixture": False,
    }
    gate = {
        "schema_version":
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "status": "PASS",
        "passed": True,
        "attempt_id": attempt,
        "duration_seconds": 180,
        "sample_count": 181,
        "maximum_sample_gap_seconds": 1.0,
        "foreign_device_io_attribution_bytes": 0,
    }
    db_readback = {
        "container_id": db_container,
        "pid": 4321,
        "process_start_ticks": 987654,
        "datadir": datadir,
        "device_major_minor": mod.EXPECTED_MAJOR_MINOR,
    }
    client_readback = {
        "container_id": client_container,
        "pid": 5432,
        "cpuset_cpus": mod.EXPECTED_CLIENT_CPUSET,
        "cpuset_mems": mod.EXPECTED_CLIENT_MEMS,
    }
    stage = root / "stage"
    stage.mkdir()
    (stage / "trace.jsonl").write_bytes(trace_payload)
    (stage / "typed_binding_manifest.json").write_bytes(
        manifest_path.read_bytes()
    )
    (stage / "typed_collector.py").write_bytes(collector_path.read_bytes())
    collector = mod._load_registry_collector(stage / "typed_collector.py")
    validation = collector.collect_and_validate(
        stage / "typed_binding_manifest.json", stage / "trace.jsonl"
    )
    payloads = {
        "trace.jsonl": trace_payload,
        "typed_binding_manifest.json": manifest_path.read_bytes(),
        "typed_collector.py": collector_path.read_bytes(),
        "collector_validation.json": (
            json.dumps(validation, indent=2, sort_keys=True) + "\n"
        ).encode(),
        "l2_transactions.json": (
            json.dumps(
                validation["l2_transactions"], indent=2, sort_keys=True
            )
            + "\n"
        ).encode(),
        "l3_vectors.json": (
            json.dumps(validation["l3_vectors"], indent=2, sort_keys=True)
            + "\n"
        ).encode(),
        "production_command.json": (
            json.dumps(command, indent=2, sort_keys=True) + "\n"
        ).encode(),
        "dynamic_gate.json": (
            json.dumps(gate, indent=2, sort_keys=True) + "\n"
        ).encode(),
        "db_live_readback.json": (
            json.dumps(db_readback, indent=2, sort_keys=True) + "\n"
        ).encode(),
        "client_live_readback.json": (
            json.dumps(client_readback, indent=2, sort_keys=True) + "\n"
        ).encode(),
    }
    files = {
        relative: hashlib.sha256(payload).hexdigest()
        for relative, payload in payloads.items()
    }
    unsigned = {
        "schema_version": "mysql_tpcc_typed_trace_registry_bundle_v2",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "synthetic_fixture": False,
        "real_production_trace": True,
        "capture_bundle_id": mod.TPCC_CAPTURE_BUNDLE_ID,
        "binding_count": 34,
        "observed_binding_count": 34,
        "transaction_types": list(mod.TRANSACTIONS),
        "sensitive_stock_level_normalized_to": "StockLevel",
        "setter_type_position_sql_hash_verified": True,
        "l2_l3_complete": True,
        "qps_resource_input_prohibited": True,
        "production_trace_provenance": {
            "schema_version": "mysql_tpcc_production_trace_provenance_v1",
            "attempt_id": attempt,
            "real_data_plane": True,
            "production_client": True,
            "synthetic_fixture": False,
            "actual_main_class": "com.oltpbenchmark.DBWorkload",
            "classpath_overlay_first": True,
            "native_latency_initialized": True,
            "measurement_started_observed": True,
            "started_epoch_ms": 1000,
            "ended_epoch_ms": 2000,
            "argv_sha256": "1" * 64,
            "environment_sha256": "2" * 64,
            "command_spec_sha256": "3" * 64,
            "dynamic_gate_sha256": "4" * 64,
            "trace_sha256": trace_sha,
            "db_container_id": db_container,
            "db_pid": 4321,
            "db_process_start_ticks": 987654,
            "db_datadir": datadir,
            "db_device_major_minor": mod.EXPECTED_MAJOR_MINOR,
            "db_cpuset_cpus": mod.EXPECTED_DB_CPUSET,
            "db_cpuset_mems": mod.EXPECTED_DB_MEMS,
            "mysql_port": mod.EXPECTED_PORT,
            "client_container_id": client_container,
            "client_pid": 5432,
            "client_cpuset_cpus": mod.EXPECTED_CLIENT_CPUSET,
            "client_cpuset_mems": mod.EXPECTED_CLIENT_MEMS,
        },
        "files": files,
    }
    bundle_id = hashlib.sha256(
        mod._canonical_registry_unsigned(unsigned)
    ).hexdigest()
    root = root / bundle_id
    root.mkdir()
    for relative, payload in payloads.items():
        (root / relative).write_bytes(payload)
    value = {**unsigned, "bundle_id": bundle_id}
    write_json(root / "bundle_manifest.json", value)
    return root, value


def materialized_command(mod, runtime: dict) -> dict:
    return {
        "schema_version": "mysql_tpcc_formal10_materialized_command_v1",
        "status": "SEALED_FORMAL10_COMMAND",
        "formal_authorized": True,
        "accepted_round_authorized": False,
        "argv": [
            "/runtime/bin/java",
            "-cp",
            "/bundle/qualification/native_latency_producer/classes:/bundle/runtime.jar",
            "com.oltpbenchmark.DBWorkload",
            "-b",
            "tpcc",
            "-c",
            "/config/tpcc.xml",
            "--execute=true",
        ],
        "native_java_launch_recipe": {
            "main_class": "com.oltpbenchmark.DBWorkload",
            "classpath_first_entry":
                "qualification/native_latency_producer/classes",
            "java_jar_mode_prohibited": True,
        },
        "environment": {
            "BENCHBASE_FORMAL_STOP_FILE": "/measurement/stop",
            "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
            "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
        },
        "client_container": {
            "container_id": "c" * 64,
            "cpuset_cpus": mod.EXPECTED_CLIENT_CPUSET,
            "cpuset_mems": mod.EXPECTED_CLIENT_MEMS,
            "cpu_quota_cores":
                runtime["client_resource_envelope"]["cpu_quota_cores"],
            "memory_bytes":
                runtime["client_resource_envelope"]["memory_bytes"],
            "memory_swap_bytes":
                runtime["client_resource_envelope"]["memory_swap_bytes"],
            "pids_max": runtime["client_resource_envelope"]["pids_max"],
            "root_filesystem_read_only": True,
            "network_mode": "host",
            "target_mysql_device_write_prohibited": True,
            "image_id": "sha256:" + "a" * 64,
        },
        "manifest_bound_java_binary": "/opt/mysql-tpcc-jdk/bin/java",
        "manifest_bound_python_binary":
            "/opt/mysql-tpcc-python/bin/python3.11",
        "jdk_tree_content_sha256":
            "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c",
        "python_tree_content_sha256":
            "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
        "prefault_authority_sha256": "f" * 64,
        "target_device_real_window_io_required_zero": True,
    }


def handoff(mod, bundle_id: str, attempt: str) -> dict:
    container = "d" * 64
    datadir = mod.EXPECTED_DB_DATADIR_PREFIX + "attempt/datadir"
    phases = {}
    for index, name in enumerate(mod.PHASES):
        phases[name] = {
            "status": "PASS",
            "attempt_id": attempt,
            "bundle_id": bundle_id,
            "db_container_id": container,
            "db_pid": 4321,
            "db_process_start_ticks": 987654,
            "db_datadir": datadir,
            "db_device_major_minor": mod.EXPECTED_MAJOR_MINOR,
            "db_cpuset_cpus": mod.EXPECTED_DB_CPUSET,
            "db_cpuset_mems": mod.EXPECTED_DB_MEMS,
            "mysql_port": mod.EXPECTED_PORT,
            "client_container_id": hashlib.sha256(name.encode()).hexdigest(),
            "client_pid": 5000 + index,
            "client_cpuset_cpus": mod.EXPECTED_CLIENT_CPUSET,
            "client_cpuset_mems": mod.EXPECTED_CLIENT_MEMS,
            "live_readback_sha256": hashlib.sha256(
                f"readback-{name}".encode()
            ).hexdigest(),
            "observed_epoch_ms": 3000 + index,
            "client_target_device_io_delta": {
                "rbytes": 0, "wbytes": 0, "rios": 0,
                "wios": 0, "dbytes": 0, "dios": 0,
            },
            "client_target_device_io_zero_verified": True,
        }
    return {
        "schema_version":
            "mysql_tpcc_same_live_container_formal10_handoff_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "attempt_id": attempt,
        "bundle_id": bundle_id,
        "db_container_id": container,
        "db_pid": 4321,
        "db_process_start_ticks": 987654,
        "db_datadir": datadir,
        "container_recreated": False,
        "datadir_replaced": False,
        "database_restarted": False,
        "phases": phases,
    }


def test_frozen_collector_and_typed_closure_are_exact() -> None:
    mod = module()
    assert mod.verify_collector_payload()["payload_id"] == (
        "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
    )
    typed = mod.verify_typed_capture_closure()
    assert typed["binding_count"] == 34
    assert typed["transactions"] == list(mod.TRANSACTIONS)
    assert typed["setter_type_position_sql_hash_verified_by_collector"] is True
    assert typed["l2_l3_contract_present"] is True
    assert typed["qps_resource_input_prohibited"] is True


def test_current_successor_is_qualification_only_and_not_formal10_ready() -> None:
    mod = module()
    blockers = mod.readiness_blockers(CURRENT_SUCCESSOR)
    assert "successor manifest does not authorize formal launch" in blockers
    assert (
        "successor runtime capabilities are qualification/capture-only"
        in blockers
    )
    assert "real production typed trace registry v2 is absent" in blockers
    assert "sealed materialized formal10 client command is absent" in blockers
    assert "same-live-container formal10 handoff is absent" in blockers


def test_not_ready_cli_publishes_hashed_audit(tmp_path: Path) -> None:
    mod = module()
    output = tmp_path / "audit.json"
    assert mod.main([
        "--bundle", str(CURRENT_SUCCESSOR),
        "--output", str(output),
    ]) == 1
    assert json.loads(output.read_text())["status"] == "NOT_READY"
    mod.verify_sidecar(output)


def test_qualification_template_uses_actual_main_but_is_not_authority() -> None:
    mod = module()
    _, runtime = mod.verify_successor_content(CURRENT_SUCCESSOR)
    template = mod.load(
        CURRENT_SUCCESSOR
        / "qualification/qualification_command_template.json"
    )
    assert "com.oltpbenchmark.DBWorkload" in template["argv"]
    assert (
        template["native_java_launch_recipe"]["classpath_first_entry"]
        == "qualification/native_latency_producer/classes"
    )
    with pytest.raises(mod.ReadinessError, match="template"):
        mod.validate_materialized_command(template, runtime)


def test_native_latency_overlay_is_wired_into_actual_benchbase_lifecycle() -> None:
    mod = module()
    overlay = CURRENT_SUCCESSOR / "qualification/native_latency_producer"
    thread_source = (overlay / "source/ThreadBench.java").read_text(
        encoding="utf-8"
    )
    worker_source = (overlay / "source/Worker.java").read_text(
        encoding="utf-8"
    )
    control_source = (overlay / "source/FormalControl.java").read_text(
        encoding="utf-8"
    )
    assert "FormalControl.initialize();" in thread_source
    assert "FormalControl.measurementStarted();" in thread_source
    assert "FormalControl.completion(start, end);" in worker_source
    assert "LATENCY_MICROS" in control_source
    assert "latency_p50_ms" in control_source
    assert "latency_p95_ms" in control_source
    assert "latency_p99_ms" in control_source

    disassembly = subprocess.run(
        [
            "javap",
            "-classpath",
            str(overlay / "classes"),
            "-c",
            "-p",
            "com.oltpbenchmark.ThreadBench",
            "com.oltpbenchmark.api.Worker",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    assert "com/oltpbenchmark/FormalControl.initialize:()V" in disassembly
    assert "com/oltpbenchmark/FormalControl.measurementStarted:()V" in disassembly
    assert "com/oltpbenchmark/FormalControl.completion:(JJ)V" in disassembly


def test_registry_requires_explicit_real_production_provenance(
    tmp_path: Path,
) -> None:
    mod = module()
    registry, value = production_registry(
        mod, tmp_path / "registry", "tpcc-prod-attempt-00000001"
    )
    assert mod.validate_production_registry(registry)[
        "real_production_trace"
    ] is True
    missing = copy.deepcopy(value)
    missing.pop("production_trace_provenance")
    unsigned = dict(missing)
    unsigned.pop("bundle_id")
    missing["bundle_id"] = hashlib.sha256(
        mod._canonical_registry_unsigned(unsigned)
    ).hexdigest()
    write_json(registry / "bundle_manifest.json", missing)
    with pytest.raises(
        mod.ReadinessError,
        match="content identity differs|provenance absent",
    ):
        mod.validate_production_registry(registry)


@pytest.mark.parametrize(
    ("phase_name", "field", "replacement", "message"),
    [
        ("fresh_r0", "db_container_id", "e" * 64, "database identity changed"),
        ("debug", "db_pid", 9999, "database identity changed"),
        ("formal10_launch", "db_datadir", "/tmp/other", "database identity changed"),
        ("typed_capture", "db_cpuset_cpus", "0-7", "database envelope changed"),
        ("qualification", "client_cpuset_cpus", "0-7", "client envelope differs"),
    ],
)
def test_same_live_container_handoff_fails_on_any_identity_drift(
    tmp_path: Path,
    phase_name: str,
    field: str,
    replacement: object,
    message: str,
) -> None:
    mod = module()
    attempt = "tpcc-prod-attempt-00000002"
    registry, registry_value = production_registry(
        mod, tmp_path / "registry", attempt
    )
    registry_manifest = mod.validate_production_registry(registry)
    value = handoff(mod, CURRENT_SUCCESSOR.name, attempt)
    mod.validate_same_live_container_handoff(
        value,
        bundle_id=CURRENT_SUCCESSOR.name,
        registry_manifest=registry_manifest,
    )
    value["phases"][phase_name][field] = replacement
    with pytest.raises(mod.ReadinessError, match=message):
        mod.validate_same_live_container_handoff(
            value,
            bundle_id=CURRENT_SUCCESSOR.name,
            registry_manifest=registry_value,
        )


def test_materialized_command_binds_actual_main_native_latency_and_client() -> None:
    mod = module()
    _, runtime = mod.verify_successor_content(CURRENT_SUCCESSOR)
    value = materialized_command(mod, runtime)
    assert mod.validate_materialized_command(value, runtime)[
        "formal_authorized"
    ] is True
    value["resource_inputs"] = {"qps": 1}
    with pytest.raises(mod.ReadinessError, match="QPS resource input"):
        mod.validate_materialized_command(value, runtime)


def test_prelaunch_handoff_accepts_exact_first_four_phases(
    tmp_path: Path,
) -> None:
    mod = module()
    attempt = "tpcc-prod-attempt-prelaunch-0001"
    registry, _ = production_registry(
        mod, tmp_path / "registry", attempt
    )
    registry_manifest = mod.validate_production_registry(registry)
    value = handoff(mod, CURRENT_SUCCESSOR.name, attempt)
    value["status"] = "PRELAUNCH_READY"
    value["phases"].pop("formal10_launch")
    validated = mod.validate_same_live_container_handoff(
        value,
        bundle_id=CURRENT_SUCCESSOR.name,
        registry_manifest=registry_manifest,
    )
    assert list(validated["phases"]) == list(mod.PHASES[:-1])


def test_all_positive_v2_authorities_validate_together(tmp_path: Path) -> None:
    mod = module()
    attempt = "tpcc-prod-attempt-00000003"
    registry, registry_value = production_registry(
        mod, tmp_path / "registry", attempt
    )
    _, runtime = mod.verify_successor_content(CURRENT_SUCCESSOR)
    registry_manifest = mod.validate_production_registry(registry)
    mod.validate_materialized_command(materialized_command(mod, runtime), runtime)
    mod.validate_same_live_container_handoff(
        handoff(mod, CURRENT_SUCCESSOR.name, attempt),
        bundle_id=CURRENT_SUCCESSOR.name,
        registry_manifest=registry_manifest,
    )
    assert registry_value["observed_binding_count"] == 34
