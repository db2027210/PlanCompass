#!/usr/bin/env python3
"""Publish the local TPC-C typed capture runtime as an immutable bundle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BENCHBASE = ROOT / "formal_benchbase_src_20260723"
JAR = BENCHBASE / "target/benchbase-2023-SNAPSHOT-formal-binding-trace-candidate.jar"
LIB = Path("/home/anonymous/benchbase-mysql/lib")
COLLECTOR = (
    ROOT
    / "artifacts/mysql_tpcc_typed_collector"
    / "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
    / "tpcc_typed_collector.py"
)
TYPED_MANIFEST = (
    ROOT
    / "artifacts/mysql_tpcc_typed_manifest"
    / "689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182"
    / "tpcc_offline_binding_manifest.json"
)
SENSITIVE_SQL = Path(
    "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/"
    "config/workloads/mysql_tpcc_sensitive_item_semijoin.sql"
)
SENSITIVE_SQL_SHA256 = (
    "cd8fed7df71c5a681b001083397181804b185816225e68ae8449f8cc384d507e"
)
DESTINATION = ROOT / "artifacts/mysql_tpcc_typed_runtime_v1"
SOURCE_CLOSURE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
    / "source-v28-b4a7a8bec9b033ae280cf4ad23459ed0a4d0abd81e8e25d2da0c3c6f7412dfb8"
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def inputs() -> dict[str, Path]:
    result = {
        "runtime/benchbase-formal.jar": JAR,
        "build/pom.xml": BENCHBASE / "pom.xml",
        "source/TpccTypedTrace.java": (
            BENCHBASE
            / "src/main/java/com/oltpbenchmark/TpccTypedTrace.java"
        ),
        "source/TpccSensitiveTypedMain.java": (
            BENCHBASE
            / "src/main/java/com/oltpbenchmark/TpccSensitiveTypedMain.java"
        ),
        "source/TpccTypedTracePreflightMain.java": (
            BENCHBASE
            / "src/main/java/com/oltpbenchmark/TpccTypedTracePreflightMain.java"
        ),
        "source/Procedure.java": (
            BENCHBASE / "src/main/java/com/oltpbenchmark/api/Procedure.java"
        ),
        "source/Worker.java": (
            BENCHBASE / "src/main/java/com/oltpbenchmark/api/Worker.java"
        ),
        "orchestration/formal_tpcc_composite_workload.py": (
            ROOT / "shell/formal_tpcc_composite_workload.py"
        ),
        "capture/mysql_tpcc_typed_capture_entry_v1.py": (
            ROOT / "shell/mysql_tpcc_typed_capture_entry_v1.py"
        ),
        "capture/mysql_tpcc_typed_interference_gate_v1.py": (
            ROOT / "shell/mysql_tpcc_typed_interference_gate_v1.py"
        ),
        "capture/mysql_process_classification.py": (
            ROOT / "shell/mysql_process_classification_v10.py"
        ),
        "capture/mysql_numa_bind_exec_v1.py": (
            ROOT / "shell/mysql_numa_bind_exec_v1.py"
        ),
        "capture/tpcc_typed_capture_config_v1.json": (
            ROOT
            / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
            / "tpcc_typed_capture_config_v1.json"
        ),
        "capture/tpcc_typed_capture_profile_v1.json": (
            ROOT
            / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
            / "tpcc_typed_capture_profile_v1.json"
        ),
        "collector/tpcc_typed_collector.py": COLLECTOR,
        "config/tpcc_offline_binding_manifest.json": TYPED_MANIFEST,
        "config/mysql_tpcc_sensitive_item_semijoin.sql": SENSITIVE_SQL,
        "tests/test_tpcc_typed_trace_env_v1.py": (
            ROOT / "tests/test_tpcc_typed_trace_env_v1.py"
        ),
        "tests/test_tpcc_typed_collector_local_v1.py": (
            ROOT / "tests/test_tpcc_typed_collector_local_v1.py"
        ),
        "tests/test_tpcc_typed_numa_gate_v1.py": (
            ROOT / "tests/test_tpcc_typed_numa_gate_v1.py"
        ),
        "tests/test_tpcc_typed_capture_coverage_v1.py": (
            ROOT / "tests/test_tpcc_typed_capture_coverage_v1.py"
        ),
        "tests/test_tpcc_typed_disjoint_client_gate_v1.py": (
            ROOT / "tests/test_tpcc_typed_disjoint_client_gate_v1.py"
        ),
        "tests/test_tpcc_typed_trace_concurrency_guard_v1.py": (
            ROOT / "tests/test_tpcc_typed_trace_concurrency_guard_v1.py"
        ),
        "tests/test_tpcc_typed_trace_history_visibility_retry_v1.py": (
            ROOT / "tests/test_tpcc_typed_trace_history_visibility_retry_v1.py"
        ),
        "tests/test_tpcc_typed_runtime_process_gate_v1.py": (
            ROOT / "tests/test_tpcc_typed_runtime_process_gate_v1.py"
        ),
        "tests/test_tpcc_typed_interference_gate_observer_cwd_v1.py": (
            ROOT
            / "tests/test_tpcc_typed_interference_gate_observer_cwd_v1.py"
        ),
        "tests/test_tpcc_typed_gate_diagnostics_v2.py": (
            ROOT / "tests/test_tpcc_typed_gate_diagnostics_v2.py"
        ),
        "tests/run_sealed_bundle_tests.py": (
            ROOT / "shell/run_mysql_tpcc_typed_bundle_tests_v1.py"
        ),
    }
    for dependency in sorted(LIB.glob("*.jar")):
        result[f"runtime/lib/{dependency.name}"] = dependency
    return result


def validate(source_files: dict[str, Path]) -> dict[str, object]:
    for relative, source in source_files.items():
        if (
            source.is_symlink()
            or not source.is_file()
            or not os.access(source, os.R_OK)
        ):
            raise RuntimeError(f"bundle input is absent, unreadable, or symlinked: {relative}")
    manifest = json.loads(TYPED_MANIFEST.read_text(encoding="utf-8"))
    statements = manifest.get("statements")
    if (
        manifest.get("binding_count") != 34
        or not isinstance(statements, list)
        or len(statements) != 34
    ):
        raise RuntimeError("TPC-C typed manifest is not the exact 34-statement authority")
    sensitive = [
        row
        for row in statements
        if row.get("binding_id")
        == "34:SensitiveStockLevel:SensitiveStockLevel/item-in-orderline-w1"
    ]
    if (
        len(sensitive) != 1
        or sensitive[0].get("parameter_count") != 0
        or sensitive[0].get("template_sql_sha256")
        != "d727716a2be8da74ec08cf8cf87cbcc342fa3518ef40281fcc86ca9233c60c6c"
    ):
        raise RuntimeError("SensitiveStockLevel typed authority differs")
    if sha256(SENSITIVE_SQL) != SENSITIVE_SQL_SHA256:
        raise RuntimeError("SensitiveStockLevel SQL source differs")
    required_classes = {
        "com/oltpbenchmark/TpccTypedTrace.class",
        "com/oltpbenchmark/TpccSensitiveTypedMain.class",
        "com/oltpbenchmark/TpccTypedTracePreflightMain.class",
    }
    with zipfile.ZipFile(JAR) as archive:
        missing_classes = required_classes - set(archive.namelist())
    if missing_classes:
        raise RuntimeError(f"BenchBase jar lacks typed classes: {sorted(missing_classes)}")
    connector = source_files.get("runtime/lib/mysql-connector-java-8.0.30.jar")
    if connector is None:
        raise RuntimeError("MySQL Connector/J is absent from the dependency closure")
    source_manifest = json.loads(
        (SOURCE_CLOSURE / "source_manifest.json").read_text(encoding="utf-8")
    )
    if (
        source_manifest.get("bundle_id") != SOURCE_CLOSURE.name.removeprefix("source-v28-")
        or source_manifest.get("formal_scope") != "mysql_only"
        or source_manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("TPC-C capture Python source closure identity differs")
    for relative, expected in source_manifest["files"].items():
        candidate = SOURCE_CLOSURE / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or sha256(candidate) != expected
        ):
            raise RuntimeError(f"TPC-C capture Python source closure differs: {relative}")
    capture_source = source_files[
        "capture/mysql_tpcc_typed_capture_entry_v1.py"
    ].read_text(encoding="utf-8")
    required_capture_timing = (
        "CAPTURE_MEASUREMENT_SECONDS = 180",
        "SESSION_MAXIMUM_MEASUREMENT_SECONDS = 600",
        "CAPTURE_COVERAGE_RECHECK_SECONDS = 30",
        "maximum_measurement_seconds=SESSION_MAXIMUM_MEASUREMENT_SECONDS",
        "self.session.wait_until_measurement_end(measurement_seconds)",
        "trace_binding_coverage(",
        "self.session.stop_after_measurement(",
        "measurement_seconds=SESSION_MAXIMUM_MEASUREMENT_SECONDS",
        "minimum_measurement_seconds=CAPTURE_MEASUREMENT_SECONDS",
        "graceful_drain_seconds=120",
    )
    missing_capture_timing = [
        marker for marker in required_capture_timing if marker not in capture_source
    ]
    if missing_capture_timing:
        raise RuntimeError(
            "TPC-C typed capture timing contract differs: "
            f"{missing_capture_timing}"
        )
    required_capture_authority = (
        '"--output-dir",',
        '"immutable_runtime_bundle_id": self.bundle.name',
        '"immutable_runtime_bundle_manifest_sha256": sha256(',
        '"bundle_bound_capture_config_sha256": sha256(',
        '"bundle_bound_collector_sha256": sha256(',
        '"trace_sha256_sidecar_path": str(',
        '"effective_resource_readback_sha256": sha256(',
        '"performance_schema_readback_sha256": sha256(',
        '"correctness_evidence": dict(correctness)',
        '"mysql_tpcc_typed_capture_completion_authority_v1"',
        '"accepted_round_authorized": False',
        '"fresh TPC-C typed capture token/output authority already exists"',
    )
    missing_capture_authority = [
        marker for marker in required_capture_authority
        if marker not in capture_source
    ]
    if missing_capture_authority:
        raise RuntimeError(
            "TPC-C typed capture downstream authority differs: "
            f"{missing_capture_authority}"
        )
    if (
        '"MYSQL_TPCC_TYPED_DEFAULTS_FILE": "/opt/mysql-tpcc-config/mysql.cnf"'
        not in capture_source
        or "MYSQL_TPCC_TYPED_METRICS_USER" in capture_source
        or "MYSQL_TPCC_TYPED_METRICS_PASSWORD" in capture_source
        or "MYSQL_TPCC_TYPED_TARGET_USER" in capture_source
        or "MYSQL_TPCC_TYPED_TARGET_PASSWORD" in capture_source
    ):
        raise RuntimeError("TPC-C typed capture credential transport differs")
    interference_source = source_files[
        "capture/mysql_tpcc_typed_interference_gate_v1.py"
    ].read_text(encoding="utf-8")
    required_observer_cwd = (
        'cwd = (directory / "cwd").resolve(strict=True)',
        "classify_process(command, argv=argv, cwd=cwd)",
        '"cwd": cwd,',
    )
    missing_observer_cwd = [
        marker for marker in required_observer_cwd
        if marker not in interference_source
    ]
    if missing_observer_cwd:
        raise RuntimeError(
            "TPC-C typed dynamic observer cwd authority differs: "
            f"{missing_observer_cwd}"
        )
    required_gate_diagnostics = (
        "class DiagnosticGateError(RuntimeError):",
        '"proc_sample_duration_seconds": sample_finished - sample_started',
        '"relevant_identity_churn"',
        '"sample_gap"',
        '"added_identities"',
        '"removed_identities"',
        '"diagnostic_reset_journal"',
        '"deadline_diagnostics"',
        "is_vm_data_plane(command, argv)",
        'category = "benchmark"',
        '"device_authority_complete"',
        '"observed_churn_journal"',
        '"fail_closed_events"',
        '"isolated_postgresql_identity_churn"',
        '"cpu_overlap"',
        '"shared_target_device"',
        '"device_authority_unknown"',
        '"target_resource_readback_drift"',
    )
    missing_gate_diagnostics = [
        marker for marker in required_gate_diagnostics
        if marker not in interference_source
    ]
    if missing_gate_diagnostics:
        raise RuntimeError(
            "TPC-C typed diagnostic gate contract differs: "
            f"{missing_gate_diagnostics}"
        )
    sealed_tests = {
        relative: source_files[relative].read_text(encoding="utf-8")
        for relative in source_files
        if relative.startswith("tests/test_")
    }
    if len(sealed_tests) != 10:
        raise RuntimeError(
            f"TPC-C sealed test module count differs: {len(sealed_tests)}"
        )
    prohibited_test_markers = (
        "/home/anonymous/program/resource_adaptive_mysql",
        "artifacts/mysql_tpcc_",
        "formal_benchbase_src_",
        'ROOT / "shell',
        "shell.mysql_",
    )
    leaked_test_paths = {
        relative: [
            marker
            for marker in prohibited_test_markers
            if marker in source
        ]
        for relative, source in sealed_tests.items()
        if any(marker in source for marker in prohibited_test_markers)
    }
    if leaked_test_paths:
        raise RuntimeError(
            "TPC-C sealed tests retain external workspace paths: "
            f"{leaked_test_paths}"
        )
    runner_source = source_files[
        "tests/run_sealed_bundle_tests.py"
    ].read_text(encoding="utf-8")
    required_runner_markers = (
        "SEALED_TPCC_BUNDLE_TEST_FAIL:",
        '"PYTHONPATH"',
        '"PYTHONDONTWRITEBYTECODE"',
        '"PYTEST_DISABLE_PLUGIN_AUTOLOAD"',
        '"external_workspace_paths_used": []',
        '"tests/run_sealed_bundle_tests.py"',
    )
    missing_runner_markers = [
        marker for marker in required_runner_markers
        if marker not in runner_source
    ]
    if missing_runner_markers:
        raise RuntimeError(
            "TPC-C sealed test runner contract differs: "
            f"{missing_runner_markers}"
        )
    trace_source = source_files[
        "source/TpccTypedTrace.java"
    ].read_text(encoding="utf-8")
    sensitive_source = source_files[
        "source/TpccSensitiveTypedMain.java"
    ].read_text(encoding="utf-8")
    orchestration_source = source_files[
        "orchestration/formal_tpcc_composite_workload.py"
    ].read_text(encoding="utf-8")
    defaults_markers = (
        'MYSQL_TPCC_TYPED_DEFAULTS_FILE',
        'Path.of("/opt/mysql-tpcc-config/mysql.cnf")',
        "connectionFromSealedDefaults(METRICS_URL)",
        "connectionFromSealedDefaults",
    )
    if (
        any(marker not in trace_source for marker in defaults_markers[:3])
        or defaults_markers[3] not in sensitive_source
        or "MYSQL_TPCC_TYPED_METRICS_USER" in trace_source
        or "MYSQL_TPCC_TYPED_METRICS_PASSWORD" in trace_source
        or "MYSQL_TPCC_TYPED_TARGET_USER" in sensitive_source
        or "MYSQL_TPCC_TYPED_TARGET_PASSWORD" in sensitive_source
    ):
        raise RuntimeError("TPC-C typed JDBC defaults-file authority differs")
    prelaunch_consumer_markers = (
        "enable_typed_consumers_before_workload",
        "PASS_ENABLED_BEFORE_WORKLOAD_START",
        "events_statements_history_long",
        "events_waits_history_long",
        "performance_schema_events_statements_history_long_size",
        "performance_schema_events_waits_history_long_size",
        "qps_resource_input_prohibited",
    )
    real_join_markers = (
        "wait_for_real_typed_join_authority",
        "PASS_REAL_OWNER_TRACE_JOIN",
        "SensitiveStockLevel/item-in-orderline-w1",
        "synthetic_or_fabricated_join_prohibited",
        "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_PATH",
        "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_SHA256",
    )
    if (
        any(marker not in orchestration_source for marker in prelaunch_consumer_markers)
        or any(marker not in orchestration_source for marker in real_join_markers)
        or any(marker not in trace_source for marker in real_join_markers[-2:])
    ):
        raise RuntimeError(
            "TPC-C prelaunch consumer or real typed-join authority differs"
        )
    concurrency_markers = (
        "private static synchronized void appendCommitted(",
        "FileLock fileLock = lockChannel.lock()",
        "if (!fileLock.isValid())",
    )
    missing_concurrency_markers = [
        marker for marker in concurrency_markers
        if marker not in trace_source
    ]
    if missing_concurrency_markers:
        raise RuntimeError(
            "TPC-C typed trace JVM/cross-process append lock differs: "
            f"{missing_concurrency_markers}"
        )
    return {
        "binding_count": 34,
        "jdbc_binding_count": 33,
        "sensitive_binding_count": 1,
        "transaction_types": [
            "Delivery",
            "NewOrder",
            "OrderStatus",
            "Payment",
            "StockLevel",
        ],
        "sensitive_alias": {"SensitiveStockLevel": "StockLevel"},
        "dependency_count": len(list(LIB.glob("*.jar"))),
        "python_source_closure": {
            "directory": str(SOURCE_CLOSURE),
            "source_manifest_sha256": sha256(
                SOURCE_CLOSURE / "source_manifest.json"
            ),
            "file_count": len(source_manifest["files"]),
        },
        "required_performance_schema_consumers": [
            "events_statements_history_long",
            "events_waits_history_long",
        ],
        "performance_schema_prelaunch_authority": {
            "enable_before_benchbase_process_start": True,
            "exact_consumer_readback_required": True,
            "positive_history_long_size_readback_required": True,
            "sealed_defaults_file_only": True,
            "qps_resource_input_prohibited": True,
        },
        "sensitive_stock_level_real_join_authority": {
            "owner_created_trace_and_lock_required": True,
            "exact_trace_header_required": True,
            "sensitive_sql_sha256": SENSITIVE_SQL_SHA256,
            "join_authority_file_and_sha256_required_by_java": True,
            "synthetic_or_fabricated_join_prohibited": True,
            "normalized_transaction_type": "StockLevel",
            "binding_transaction_type": "SensitiveStockLevel",
            "binding_id":
                "34:SensitiveStockLevel:"
                "SensitiveStockLevel/item-in-orderline-w1",
            "qps_resource_input_prohibited": True,
        },
        "required_statement_columns": ["EVENT_ID", "CPU_TIME", "LOCK_TIME"],
        "capture_timing": {
            "warmup_seconds": 60,
            "capture_measurement_seconds": 180,
            "session_maximum_measurement_seconds": 600,
            "coverage_recheck_seconds": 30,
            "stop_policy": "first_exact_34_of_34_coverage_boundary_after_180_else_fail_closed_at_600",
            "graceful_drain_seconds": 120,
            "capture_result_is_r0": False,
            "capture_result_is_accepted_round": False,
        },
        "trace_append_locking": {
            "same_jvm": "static_synchronized_append_critical_section",
            "cross_process": "blocking_file_channel_lock",
            "overlapping_file_lock_exception_prohibited": True,
        },
        "jdbc_credentials": {
            "defaults_file": "/opt/mysql-tpcc-config/mysql.cnf",
            "client_section_only": True,
            "regular_non_symlink_required": True,
            "group_or_other_permissions_prohibited": True,
            "environment_user_password_prohibited": True,
            "command_trace_manifest_credential_prohibited": True,
            "metrics_and_sensitive_jdbc_use_same_sealed_defaults": True,
        },
        "downstream_authority": {
            "collector_cli": "--output-dir",
            "trace_header_capture_token_required": True,
            "bundle_manifest_collector_config_hashes_recorded": True,
            "trace_result_l2_l3_hashes_recorded": True,
            "coverage_resource_performance_schema_hashes_recorded": True,
            "correctness_evidence_recorded": True,
            "trace_and_result_sha256_sidecars_required": True,
            "post_cleanup_completion_authority_required": True,
            "fresh_capture_token_output_required": True,
            "formal_r0_authorized": False,
            "outer_execution_authorized": False,
            "accepted_round_authorized": False,
        },
        "dynamic_observer_authority": {
            "proc_cwd_resolved": True,
            "observer_script_regular_non_symlink_required": True,
            "observer_script_sha256_required": True,
            "observer_argv_exact_required": True,
            "wrong_cwd_hash_or_argv_policy": "benchmark_fail_closed",
        },
        "dynamic_gate_diagnostics": {
            "window_seconds": 60,
            "minimum_intervals": 60,
            "maximum_sample_gap_seconds": 1.5,
            "fail_closed": True,
            "reset_reason_journal_required": True,
            "proc_sample_duration_required": True,
            "stable_run_length_required": True,
            "added_removed_identity_authority_required": True,
            "deadline_summary_required": True,
            "qemu_vm_data_plane_classification_required": True,
            "unknown_device_authority_policy": "fail_closed",
            "cpu_overlap_policy": "immediate_fail_closed",
            "shared_target_device_policy": "immediate_fail_closed",
            "target_resource_readback_drift_policy":
                "immediate_fail_closed",
            "isolated_postgresql_churn_journal_required": True,
            "disjoint_postgresql_identity_churn_policy":
                "inventory_only_no_window_reset",
        },
        "sealed_test_closure": {
            "runner": "tests/run_sealed_bundle_tests.py",
            "test_module_count": 10,
            "arbitrary_cwd_supported": True,
            "workspace_paths_prohibited": True,
            "old_artifact_paths_prohibited": True,
            "manifest_hash_verification_required": True,
            "pythonpath_inheritance_prohibited": True,
        },
    }


def readonly_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o444)
    root.chmod(0o555)


def verify_existing(directory: Path, expected: dict[str, str]) -> None:
    manifest_path = directory / "bundle_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(f"existing TPC-C typed bundle is incomplete: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("files") != expected:
        raise RuntimeError(f"existing TPC-C typed bundle file map differs: {directory}")
    for relative, digest in expected.items():
        target = directory / relative
        if target.is_symlink() or not target.is_file() or sha256(target) != digest:
            raise RuntimeError(f"existing TPC-C typed bundle content differs: {relative}")


def main() -> int:
    source_files = inputs()
    contract = validate(source_files)
    file_hashes = {
        relative: sha256(source) for relative, source in sorted(source_files.items())
    }
    identity = {
        "schema_version": "mysql_tpcc_typed_runtime_identity_v1",
        "supersedes_bundle_ids": [
            "ecc83ef6d296a80a2ff154c33bca5e279a0fe3f6c040d685b051e4cd120db6be",
            "8054eca49a60e837b00a38d099d8a158"
            "5a46f86f8b206d9d3ea9b3cc4095c142",
            "eb1b515a7371fb1c4cae26062dbf5279"
            "aba2da776959eb5c03db186454cc153c",
            "66648e639339932babfc08f13f34430c"
            "9f6f36792bc0fed56dcda9987b3c8496",
        ],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "capture_only": True,
        "outer_execution_authorized": False,
        "files": file_hashes,
        "contract": contract,
    }
    bundle_id = sha256_bytes(canonical(identity))
    final = DESTINATION / bundle_id
    if final.exists():
        verify_existing(final, file_hashes)
        print(
            json.dumps(
                {
                    "status": "EXISTS_VERIFIED",
                    "bundle_id": bundle_id,
                    "directory": str(final),
                    "file_count": len(file_hashes),
                },
                sort_keys=True,
            )
        )
        return 0

    DESTINATION.mkdir(parents=True, exist_ok=True)
    stage = DESTINATION / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        for relative, source in sorted(source_files.items()):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if sha256(target) != file_hashes[relative]:
                raise RuntimeError(f"staged TPC-C typed file hash differs: {relative}")
        manifest = {
            **identity,
            "bundle_id": bundle_id,
            "immutable": True,
            "publication": {
                "transactional": True,
                "idempotent": True,
                "temporary_directory": stage.name,
                "final_directory": str(final),
            },
            "runtime": {
                "java_main": "com.oltpbenchmark.DBWorkload",
                "sensitive_java_main": "com.oltpbenchmark.TpccSensitiveTypedMain",
                "preflight_java_main": "com.oltpbenchmark.TpccTypedTracePreflightMain",
                "classpath": "runtime/benchbase-formal.jar:runtime/lib/*",
                "trace_role_owner": "OWNER",
                "trace_role_sensitive_helper": "JOINER",
                "sensitive_interval_seconds": 15,
                "qps_resource_input_prohibited": True,
            },
            "authorization": {
                "collector_published": True,
                "production_trace_collected": False,
                "capture_workload_authorized": False,
                "formal_r0_authorized": False,
                "outer_execution_authorized": False,
                "accepted_round_authorized": False,
            },
        }
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            f"{sha256(stage / 'bundle_manifest.json')}  bundle_manifest.json\n",
            encoding="ascii",
        )
        readonly_tree(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    verify_existing(final, file_hashes)
    print(
        json.dumps(
            {
                "status": "PUBLISHED_VERIFIED",
                "bundle_id": bundle_id,
                "directory": str(final),
                "file_count": len(file_hashes),
                "dependency_count": contract["dependency_count"],
                "outer_execution_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
