#!/usr/bin/env python3
"""Build offline distributed successors for MySQL XP v8 and TPC-C typed runtime.

The builder performs no SSH and launches no workload.  It copies each complete
runtime closure, adds an exact distributed storage/resource/toolchain contract,
and publishes by a same-filesystem atomic rename.  Existing content-addressed
outputs are verified and reused; divergent outputs fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Mapping

try:
    from . import runtime_tree_content_identity_v3 as runtime_tree
except ImportError:
    import runtime_tree_content_identity_v3 as runtime_tree

ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
QUALIFICATION_LAUNCHER = (
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
)
QUALIFICATION_METRIC_ADAPTER = (
    ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py"
)
HYBENCH_QUALIFICATION_SOURCE = (
    ROOT / "formal_clients_20260721/com/hybench/HyBenchFormalMain.java"
)
HYBENCH_QUALIFICATION_CLASSES = (
    ROOT / "qualification_producers/hybench/classes"
)
BENCHBASE_QUALIFICATION_SOURCES = (
    ROOT
    / "formal_benchbase_src_20260723/src/main/java/com/oltpbenchmark/FormalControl.java",
    ROOT
    / "formal_benchbase_src_20260723/src/main/java/com/oltpbenchmark/api/Worker.java",
    ROOT
    / "formal_benchbase_src_20260723/src/main/java/com/oltpbenchmark/ThreadBench.java",
)
BENCHBASE_QUALIFICATION_CLASSES = (
    ROOT / "qualification_producers/benchbase/classes"
)
QUALIFICATION_TREE_HELPER = (
    ROOT / "shell/runtime_tree_content_identity_v3.py"
)
FOUR_SCENE_CONTRACT = (
    ROOT
    / "preflight/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
)
JDK_SOURCE = Path("/usr/lib/jvm/jdk-23.0.2-oracle-x64")
JDK_PORTABLE_MANIFEST = (
    ROOT / "preflight/runtime_portable_manifests_v3_20260729/"
    "node04_jdk23_v3-"
    "9b00b2613e0fe5669c614c608510db0d0239ce8f1904e99c0877a1e894652bee.json"
)
JDK_PORTABLE_MANIFEST_SHA256 = (
    "9b00b2613e0fe5669c614c608510db0d0239ce8f1904e99c0877a1e894652bee"
)
JDK_RUNTIME_TREE_CONTENT_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
PYTHON_PORTABLE_MANIFEST = (
    ROOT / "preflight/runtime_portable_manifests_v3_20260729/"
    "node04_python311_v3-"
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4.json"
)
PYTHON_PORTABLE_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
PYTHON_RUNTIME_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_RUNTIME_EXECUTABLE_SHA256 = (
    "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
)
XP_PREDECESSOR = (
    ROOT
    / "artifacts/mysql_xp_v8"
    / "a7d2386cd2a703b6bb73e5269cb8cace807b5fb0eb5fb3d5ccb6e71c2b9caeed"
)
TPCC_PREDECESSOR = (
    ROOT
    / "artifacts/mysql_tpcc_typed_runtime_v1"
    / "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516"
)
TPCC_SOURCE_CLOSURE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
    / "source-v28-b4a7a8bec9b033ae280cf4ad23459ed0a4d0abd81e8e25d2da0c3c6f7412dfb8"
)
XP_OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_distributed_successor_v1"
TPCC_OUTPUT_ROOT = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v1"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise BuildError(f"{label} absent or symlinked: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"{label} must be a JSON object")
    return value


def relative_regular_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"symlink is prohibited in bundle closure: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def copy_regular_tree(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise BuildError(f"regular source directory required: {source}")
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise BuildError(f"symlink is prohibited in source closure: {path}")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            os.chmod(target, stat.S_IMODE(path.stat().st_mode))


def freeze_tree_modes(root: Path) -> None:
    """Remove every write bit after the closure has been fully materialized."""
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError(f"symlink is prohibited in bundle closure: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, mode & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def verify_immutable_modes(root: Path) -> None:
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_symlink():
            raise BuildError(f"symlink is prohibited in bundle closure: {path}")
        if stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError(f"writable immutable bundle entry: {path}")


def make_directories_owner_writable(root: Path) -> None:
    """Permit cleanup of an unpublished, partially frozen staging tree."""
    if not root.exists():
        return
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o700)


def verify_xp_predecessor() -> dict[str, Any]:
    manifest = load_object(XP_PREDECESSOR / "manifest.json", "XP v8 manifest")
    if (
        manifest.get("bundle_id") != XP_PREDECESSOR.name
        or manifest.get("schema_version") != "mysql_xp_v8_immutable_runtime_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise BuildError("XP v8 predecessor identity/scope differs")
    for relative, expected in (manifest.get("payload_files") or {}).items():
        path = XP_PREDECESSOR / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256(path) != expected
        ):
            raise BuildError(f"XP v8 predecessor closure differs: {relative}")
    return manifest


def verify_tpcc_predecessor() -> dict[str, Any]:
    manifest = load_object(
        TPCC_PREDECESSOR / "bundle_manifest.json", "TPC-C typed manifest"
    )
    if (
        manifest.get("bundle_id") != TPCC_PREDECESSOR.name
        or manifest.get("schema_version") != "mysql_tpcc_typed_runtime_identity_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("capture_only") is not True
    ):
        raise BuildError("TPC-C typed predecessor identity/scope differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = TPCC_PREDECESSOR / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256(path) != expected
        ):
            raise BuildError(f"TPC-C typed predecessor closure differs: {relative}")
    source = manifest.get("contract", {}).get("python_source_closure", {})
    source_manifest = TPCC_SOURCE_CLOSURE / "source_manifest.json"
    if (
        source.get("directory") != str(TPCC_SOURCE_CLOSURE)
        or source.get("source_manifest_sha256") != sha256(source_manifest)
    ):
        raise BuildError("TPC-C external Python source closure authority differs")
    frozen = load_object(source_manifest, "TPC-C source manifest")
    for relative, expected in (frozen.get("files") or {}).items():
        path = TPCC_SOURCE_CLOSURE / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256(path) != expected
        ):
            raise BuildError(f"TPC-C Python source closure differs: {relative}")
    return manifest


def jdk_tree_manifest() -> dict[str, Any]:
    if not JDK_SOURCE.is_dir() or JDK_SOURCE.is_symlink():
        raise BuildError(f"frozen JDK 23 source is absent: {JDK_SOURCE}")
    if sha256(JDK_PORTABLE_MANIFEST) != JDK_PORTABLE_MANIFEST_SHA256:
        raise BuildError("fresh JDK v3 portable manifest differs")
    identity = load_object(
        JDK_PORTABLE_MANIFEST, "fresh JDK v3 portable manifest"
    )
    try:
        runtime_tree.validate_portable_manifest(identity)
        runtime_tree.verify_tree_manifest(JDK_SOURCE, identity)
    except runtime_tree.RuntimeTreeError as exc:
        raise BuildError(f"frozen JDK tree invalid: {exc}") from exc
    if identity["tree_content_sha256"] != JDK_RUNTIME_TREE_CONTENT_SHA256:
        raise BuildError("frozen JDK portable tree identity differs")
    if identity["artifacts"]["bin/java"] != (
        "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
    ):
        raise BuildError("frozen JDK 23 java binary differs")
    if identity["artifacts"]["release"] != (
        "c0cb3f9c13a88cac28beb94aa6139473af9ce3ecdb6f3fb17589d8cdb470648d"
    ):
        raise BuildError("frozen JDK 23 release file differs")
    return identity


def python_tree_manifest() -> dict[str, Any]:
    if sha256(PYTHON_PORTABLE_MANIFEST) != (
        PYTHON_PORTABLE_MANIFEST_SHA256
    ):
        raise BuildError("fresh Python v3 portable manifest differs")
    identity = load_object(
        PYTHON_PORTABLE_MANIFEST, "fresh Python v3 portable manifest"
    )
    try:
        runtime_tree.validate_portable_manifest(identity)
    except runtime_tree.RuntimeTreeError as exc:
        raise BuildError(f"canonical Python runtime tree invalid: {exc}") from exc
    if (
        identity["tree_content_sha256"]
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or identity["artifacts"]["bin/python3.11"]
        != PYTHON_RUNTIME_EXECUTABLE_SHA256
    ):
        raise BuildError("canonical Python runtime portable identity differs")
    return identity


def runtime_contract(
    scenario: str,
    global_contract: Mapping[str, Any],
    jdk: Mapping[str, Any],
    python_runtime: Mapping[str, Any],
) -> dict[str, Any]:
    scene = global_contract["scenarios"][scenario]
    storage = scene["storage"]
    envelope = scene["resource_envelope"]
    common = {
        "schema_version": "mysql_algorithm_c_distributed_runtime_contract_v1",
        "scenario": scene["scenario"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "data_plane_launch_authority": "main_agent_only",
        "host": scene["host"],
        "storage": {
            "device": storage["device"],
            "mount": storage["mount"],
            "expected_live_major_minor": storage["expected_live_major_minor"],
            "template_path": storage["template_path"],
            "runtime_root": storage["runtime_root"],
            "full_physical_copy_required": True,
            "overlay_or_reflink_prohibited": True,
        },
        "resource_envelope": envelope,
        "client_resource_envelope": scene["client_resource_envelope"],
        "fresh_r0_required": True,
        "accepted_round_authorized": False,
        "formal_launch_authorized": False,
        "qualification_policy": {
            "main_agent_authorization_required": True,
            "allowed_before_bidirectional_gate": [
                "debug_only_solo_qualification",
                "debug_only_paired_qualification",
            ],
            "capture_r0_formal_accounting_prohibited_during_qualification": True,
            "concurrent_capture_r0_or_debug_requires_180s_bidirectional_pass": True,
            "concurrent_formal_authorized": False,
            "serial_fallback_required_on_missing_failed_or_stale_gate": True,
            "dynamic_gate_schema":
                "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
            "all_required_streams_missing_or_timeout_result": "FAIL",
            "solo_vs_paired_tolerance_comparator_required": True,
        },
        "live_readback": {
            "same_attempt_required": True,
            "hostname_device_mount_major_minor_cpuset_mems_port_required": True,
            "cpu_memory_swap_pids_io_and_buffer_pool_required": True,
            "toolchain_hash_readback_required": True,
            "missing_or_timeout_result": "FAIL",
            "collector_and_qualification_entrypoint":
                "qualification/mysql_distributed_live_readback_qualification_v1.py",
            "collect_only_is_read_only": True,
            "qualification_main_agent_token_and_sidecars_required": True,
        },
    }
    if scenario == "xp":
        tree_id = str(jdk["tree_content_sha256"])
        common["toolchain"] = {
            "java_required": True,
            "python_required": False,
            "java_version": "23.0.2",
            "deployment_mode": "content_addressed_full_tree_copy",
            "deployment_source": str(JDK_SOURCE),
            "deployment_target": (
                storage["mount"]
                + "/anonymous/resource_adaptive_mysql/runtime/"
                + "jdk-23.0.2-oracle-x64-"
                + tree_id
            ),
            "tree_content_sha256": tree_id,
            "tree_content_identity_algorithm": jdk["algorithm"],
            "tree_manifest": "toolchain/jdk-23.0.2-tree-manifest.json",
            "java_binary_relative": "bin/java",
            "java_binary_sha256": jdk["artifacts"]["bin/java"],
            "release_sha256": jdk["artifacts"]["release"],
            "usr_bin_java_or_java8_prohibited": True,
            "live_full_tree_hash_readback_required": True,
        }
        common["runtime_capabilities"] = {
            "qualification": True,
            "capture": True,
            "fresh_r0_after_gate": True,
            "debug_after_gate": True,
            "formal_outer": False,
        }
    else:
        common["toolchain"] = {
            "java_required": True,
            "python_required": True,
            "java_version": "23.0.2",
            "deployment_mode": "preexisting_runtime_exact_hash_readback",
            "deployment_target": (
                "/home/anonymous/recost_deploy_20260721_v1/runtime/"
                "jdk-23.0.2-oracle-x64"
            ),
            "tree_content_sha256": jdk["tree_content_sha256"],
            "tree_content_identity_algorithm": jdk["algorithm"],
            "tree_manifest": "toolchain/jdk-23.0.2-tree-manifest.json",
            "java_binary_relative": "bin/java",
            "java_binary_sha256": jdk["artifacts"]["bin/java"],
            "release_sha256": jdk["artifacts"]["release"],
            "live_full_tree_hash_readback_required": True,
            "python_runtime_root":
                "/home/anonymous/recost_runtime_py311_24_11_1",
            "python_runtime":
                "/home/anonymous/recost_runtime_py311_24_11_1/bin/python3.11",
            "python_tree_content_sha256":
                python_runtime["tree_content_sha256"],
            "python_tree_content_identity_algorithm":
                python_runtime["algorithm"],
            "python_tree_manifest":
                "toolchain/python-3.11.11-tree-manifest.json",
            "python_binary_sha256":
                python_runtime["artifacts"]["bin/python3.11"],
            "python_version_expected": "3.11.11",
            "python_live_full_tree_hash_readback_required": True,
            "python_binary_sha256_live_readback_required": True,
        }
        common["runtime_capabilities"] = {
            "qualification": True,
            "typed_capture_after_gate": True,
            "fresh_r0": False,
            "debug": False,
            "formal_outer": False,
        }
        common["typed_runtime_invariants"] = {
            "binding_count": 34,
            "jdbc_binding_count": 34,
            "sensitive_binding_count": 1,
            "transactions": [
                "Delivery",
                "NewOrder",
                "OrderStatus",
                "Payment",
                "StockLevel",
            ],
            "sensitive_stock_level_normalizes_to": "StockLevel",
            "qps_resource_input_prohibited": True,
            "capture_result_is_not_r0_or_accepted_round": True,
        }
    return common


def native_java_launch_recipe(scenario: str) -> dict[str, Any]:
    overlay = "qualification/native_latency_producer/classes"
    if scenario == "xp":
        classpath = [
            overlay,
            (
                "predecessor/"
                "HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-"
                "candidate-jar-with-dependencies.jar"
            ),
        ]
        main_class = "com.hybench.HyBenchFormalMain"
        arguments = [
            "xp",
            "${CONFIG_ABSOLUTE}",
            "${STATEMENTS_ABSOLUTE}",
        ]
        overlay_classes = [
            (
                "qualification/native_latency_producer/classes/"
                "com/hybench/HyBenchFormalMain.class"
            ),
        ]
    else:
        dependencies = sorted(
            "predecessor/runtime/lib/" + path.name
            for path in (TPCC_PREDECESSOR / "runtime/lib").iterdir()
            if path.is_file() and not path.is_symlink()
            and path.suffix == ".jar"
        )
        classpath = [
            overlay,
            "predecessor/runtime/benchbase-formal.jar",
            *dependencies,
        ]
        main_class = "com.oltpbenchmark.DBWorkload"
        arguments = [
            "-b",
            "tpcc",
            "-c",
            "${CONFIG_ABSOLUTE}",
            "--execute=true",
        ]
        overlay_classes = [
            (
                "qualification/native_latency_producer/classes/"
                "com/oltpbenchmark/FormalControl.class"
            ),
            (
                "qualification/native_latency_producer/classes/"
                "com/oltpbenchmark/api/Worker.class"
            ),
            (
                "qualification/native_latency_producer/classes/"
                "com/oltpbenchmark/api/Worker$1.class"
            ),
            (
                "qualification/native_latency_producer/classes/"
                "com/oltpbenchmark/ThreadBench.class"
            ),
        ]
    return {
        "schema_version":
            "mysql_algorithm_c_native_java_launch_recipe_v1",
        "java_binary": "${JAVA_BINARY}",
        "java_binary_sha256":
            "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c",
        "classpath_relative_entries": classpath,
        "classpath_first_entry": overlay,
        "main_class": main_class,
        "scenario_arguments": arguments,
        "java_jar_mode_prohibited": True,
        "all_classpath_regular_files_must_be_hash_bound": True,
        "overlay_class_files": overlay_classes,
        "materialized_argv": [
            "${JAVA_BINARY}",
            "-cp",
            ":".join("${BUNDLE_ROOT}/" + value for value in classpath),
            main_class,
            *arguments,
        ],
    }


def qualification_support_documents(scenario: str) -> dict[str, dict[str, Any]]:
    launch_recipe = native_java_launch_recipe(scenario)
    return {
        "qualification_execution_contract.json": {
            "schema_version":
                "mysql_algorithm_c_distributed_qualification_execution_contract_v1",
            "scenario": scenario,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "entrypoint":
                "qualification/mysql_distributed_live_readback_qualification_v1.py",
            "collect_only": {
                "arguments": [
                    "--collect-only",
                    "--bundle",
                    "--container",
                    "--mysql-defaults-file",
                    "--output",
                ],
                "data_plane_launch": False,
                "main_agent_token_required": False,
                "atomic_json_and_sha256_sidecar": True,
            },
            "qualification": {
                "modes": [
                    "--qualification-solo",
                    "--qualification-paired",
                ],
                "main_agent_token_required": True,
                "main_agent_authority_sidecar_required": True,
                "command_spec_sidecar_required": True,
                "duration_seconds": 180,
                "minimum_samples": 181,
                "maximum_sample_gap_seconds": 1.5,
                "paired_requires_peer_readback_and_container": True,
                "debug_only": True,
                "capture_r0_formal_accepted_authority": False,
                "required_streams": [
                    "workload_throughput_or_completion_rate",
                    "p50_p95_p99_or_query_duration",
                    "cpu_usage_and_throttle_ratio",
                    "cpu_pressure",
                    "memory_current_swap_and_pressure",
                    "per_declared_device_bytes_iops_latency_queue_and_utilization",
                    "database_counters",
                    "process_cpu_affinity_and_device_inventory",
                ],
                "missing_or_timeout_result": "FAIL",
                "workload_metric_stream_same_attempt_required": True,
                "paired_peer_workload_fresh_sequence_and_progress_required":
                    True,
                "native_latency_producer": {
                    "kind": (
                        "hybench_xp" if scenario == "xp"
                        else "benchbase_tpcc"
                    ),
                    "classpath_prepend":
                        "qualification/native_latency_producer/classes",
                    "classpath_prepend_before_production_jar_required": True,
                    "native_marker_must_emit_each_second": [
                        "completed_total_or_xap_and_xtp_totals",
                        "latency_p50_ms",
                        "latency_p95_ms",
                        "latency_p99_ms",
                    ],
                    "latency_source":
                        "native_transaction_start_end_or_native_histogram",
                    "estimated_or_synthetic_latency_prohibited": True,
                    "sealed_java_launch_recipe":
                        "qualification/qualification_command_template.json",
                },
            },
            "bidirectional_comparator": {
                "mode": "--compare-bidirectional",
                "directions": [
                    "A_solo_vs_A_while_B_runs",
                    "B_solo_vs_B_while_A_runs",
                ],
                "four_distinct_attempt_tokens_required": True,
                "same_collector_version_required": True,
                "schema_version":
                    "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
                "tolerances": {
                    "throughput_degradation_fraction_max": 0.05,
                    "latency_inflation_fraction_max": 0.10,
                    "resource_utilization_absolute_drift_max": 0.05,
                    "cpu_throttle_ratio_absolute_drift_max": 0.05,
                    "foreign_device_io_attribution_bytes_max": 0,
                    "new_oom_kill_events_max": 0,
                    "new_unexpected_swap_events_max": 0,
                },
            },
            "output_authority": (
                "debug_evidence_and_dynamic_gate_only_main_agent_launch_decision_required"
            ),
        },
        "qualification_command_template.json": {
            "schema_version":
                "mysql_algorithm_c_debug_qualification_command_v1",
            "status": "TEMPLATE_NOT_AUTHORITY",
            "bundle_id": "REPLACE_WITH_SUCCESSOR_BUNDLE_ID",
            "mode": "qualification_solo_or_qualification_paired",
            "debug_only": True,
            "capture_authorized": False,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
            "qualification_attempt_id":
                "REPLACE_WITH_UNIQUE_AT_LEAST_32_CHAR_ATTEMPT_ID",
            "workload_metric_stream": {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_stream_v1",
                "path": "REPLACE_WITH_ABSOLUTE_SAME_ATTEMPT_JSONL_PATH",
            },
            "workload_metric_adapter": {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_adapter_v1",
                "kind": (
                    "hybench_xp" if scenario == "xp"
                    else "benchbase_tpcc"
                ),
            },
            "workload_process_isolation": {
                "cpuset_cpus": (
                    "3-11,39-47" if scenario == "xp"
                    else "8-15,44-51"
                ),
                "cpuset_mems": "0",
                "root_and_descendants_exact": True,
            },
            "client_container": {
                "name": "REPLACE_WITH_PRECREATED_FROZEN_CLIENT_CONTAINER",
                "container_id": "REPLACE_WITH_64_HEX_CONTAINER_ID",
                "image_id": "REPLACE_WITH_IMAGE_CONTENT_DIGEST",
                "created": "REPLACE_WITH_DOCKER_CREATED_TIMESTAMP",
                "inspect_config_sha256":
                    "REPLACE_WITH_CLIENT_CONFIG_IDENTITY_SHA256",
                "required_read_only_mounts": [
                    {
                        "source": "REPLACE_WITH_ABSOLUTE_BUNDLE_SOURCE",
                        "destination":
                            "REPLACE_WITH_ABSOLUTE_BUNDLE_DESTINATION",
                    },
                    {
                        "source": "REPLACE_WITH_ABSOLUTE_RUNTIME_SOURCE",
                        "destination":
                            "REPLACE_WITH_ABSOLUTE_RUNTIME_DESTINATION",
                    },
                    {
                        "source": "REPLACE_WITH_ABSOLUTE_CONFIG_SOURCE",
                        "destination":
                            "REPLACE_WITH_ABSOLUTE_CONFIG_DESTINATION",
                    },
                ],
            },
            "measurement_output": {
                "host_tmpfs_directory":
                    "REPLACE_WITH_ABSOLUTE_HOST_TMPFS_ATTEMPT_DIRECTORY",
                "container_tmpfs_destination":
                    "REPLACE_WITH_ABSOLUTE_CONTAINER_TMPFS_DESTINATION",
            },
            "native_java_launch_recipe": launch_recipe,
            "paired_peer_workload": (
                "OMIT_FOR_SOLO; FOR_PAIRED_BIND_PEER_SCENARIO_ATTEMPT_ID_"
                "AND_ABSOLUTE_METRIC_STREAM_PATH"
            ),
            "argv": launch_recipe["materialized_argv"],
            "executable_sha256": "REPLACE_WITH_SHA256",
            "code_files": {
                "REPLACE_WITH_ABSOLUTE_CODE_FILE": "REPLACE_WITH_SHA256"
            },
            "environment": (
                {}
                if scenario == "xp"
                else {
                    "BENCHBASE_FORMAL_STOP_FILE":
                        "/measurement/benchbase-stop-request",
                    "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
                    "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
                }
            ),
            "cwd": "REPLACE_WITH_ABSOLUTE_EXISTING_DIRECTORY",
        },
        "main_agent_authority_template.json": {
            "schema_version":
                "mysql_algorithm_c_main_agent_qualification_authority_v1",
            "status": "TEMPLATE_NOT_AUTHORITY",
            "authorized_by": "main_agent",
            "bundle_id": "REPLACE_WITH_SUCCESSOR_BUNDLE_ID",
            "mode": "qualification_solo_or_qualification_paired",
            "qualification_attempt_id":
                "COPY_EXACTLY_FROM_QUALIFICATION_COMMAND",
            "main_agent_token_sha256": "REPLACE_WITH_SHA256",
            "command_spec_sha256": "REPLACE_WITH_SHA256",
            "debug_only": True,
            "capture_authorized": False,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
            "not_before_epoch": 0,
            "expires_epoch": 0,
        },
    }


def _manifest_identity(
    scenario: str,
    predecessor_id: str,
    predecessor_manifest_sha: str,
    contract_sha: str,
    runtime_contract_sha: str,
    files: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": "mysql_algorithm_c_distributed_successor_bundle_v1",
        "scenario": scenario,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "offline_build_only": True,
        "remote_actions": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "fresh_r0_required": True,
        "supersedes_bundle_id": predecessor_id,
        "predecessor_manifest_sha256": predecessor_manifest_sha,
        "four_scene_contract_sha256": contract_sha,
        "runtime_contract_sha256": runtime_contract_sha,
        "builder_sha256": sha256(BUILDER),
        "publication": {
            "content_addressed": True,
            "transactional_same_filesystem_atomic_rename": True,
            "idempotent_existing_verified": True,
            "recursive_write_bits_removed_before_publish": True,
            "bytecode_cache_creation_prohibited_by_immutable_modes": True,
        },
        "files": dict(sorted(files.items())),
    }


def verify_successor(
    bundle: Path,
    expected_id: str | None = None,
    require_immutable_modes: bool = True,
) -> dict[str, Any]:
    manifest = load_object(bundle / "bundle_manifest.json", "successor manifest")
    bundle_id = manifest.get("bundle_id")
    if expected_id is not None and bundle_id != expected_id:
        raise BuildError("successor expected/content address differs")
    if expected_id is None and bundle_id != bundle.name:
        raise BuildError("successor directory/content address differs")
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if sha_bytes(canonical(identity)) != bundle_id:
        raise BuildError("successor manifest content address differs")
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(bundle / 'bundle_manifest.json')}  bundle_manifest.json\n"
    ):
        raise BuildError("successor manifest sidecar differs")
    actual = relative_regular_files(bundle)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    if actual != manifest.get("files"):
        raise BuildError("successor full closure differs")
    if require_immutable_modes:
        verify_immutable_modes(bundle)
    runtime = load_object(
        bundle / "distributed_runtime_contract.json", "runtime contract"
    )
    if (
        runtime.get("formal_scope") != "mysql_only"
        or runtime.get("cross_engine_comparison_prohibited") is not True
        or runtime.get("data_plane_launch_authority") != "main_agent_only"
        or runtime.get("formal_launch_authorized") is not False
    ):
        raise BuildError("successor runtime authority is unsafe")
    return manifest


def _publish(
    *,
    scenario: str,
    predecessor: Path,
    predecessor_manifest_name: str,
    output_root: Path,
    global_contract: Mapping[str, Any],
    jdk: Mapping[str, Any],
    python_runtime: Mapping[str, Any],
    extra_source: Path | None,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(
        tempfile.mkdtemp(prefix=f".{scenario}-distributed.", dir=output_root)
    )
    stage = stage_parent / "payload"
    stage.mkdir()
    try:
        copy_regular_tree(predecessor, stage / "predecessor")
        if extra_source is not None:
            copy_regular_tree(extra_source, stage / "python_source_closure")
        (stage / "authority").mkdir()
        shutil.copyfile(
            FOUR_SCENE_CONTRACT,
            stage / "authority/four_scene_successor_contract.json",
        )
        (stage / "toolchain").mkdir()
        (stage / "toolchain/jdk-23.0.2-tree-manifest.json").write_bytes(
            canonical(jdk)
        )
        if scenario == "tpcc":
            (
                stage / "toolchain/python-3.11.11-tree-manifest.json"
            ).write_bytes(canonical(python_runtime))
        runtime = runtime_contract(
            scenario, global_contract, jdk, python_runtime
        )
        runtime_payload = canonical(runtime)
        (stage / "distributed_runtime_contract.json").write_bytes(runtime_payload)
        (stage / "qualification").mkdir()
        shutil.copyfile(
            QUALIFICATION_LAUNCHER,
            stage
            / "qualification/mysql_distributed_live_readback_qualification_v1.py",
        )
        shutil.copyfile(
            QUALIFICATION_TREE_HELPER,
            stage / "qualification/runtime_tree_content_identity_v3.py",
        )
        shutil.copyfile(
            QUALIFICATION_METRIC_ADAPTER,
            stage
            / "qualification/mysql_distributed_workload_metric_adapter_v1.py",
        )
        producer = stage / "qualification/native_latency_producer"
        (producer / "source").mkdir(parents=True)
        if scenario == "xp":
            shutil.copyfile(
                HYBENCH_QUALIFICATION_SOURCE,
                producer / "source/HyBenchFormalMain.java",
            )
            copy_regular_tree(
                HYBENCH_QUALIFICATION_CLASSES, producer / "classes"
            )
        else:
            shutil.copyfile(
                BENCHBASE_QUALIFICATION_SOURCES[0],
                producer / "source/FormalControl.java",
            )
            shutil.copyfile(
                BENCHBASE_QUALIFICATION_SOURCES[1],
                producer / "source/Worker.java",
            )
            shutil.copyfile(
                BENCHBASE_QUALIFICATION_SOURCES[2],
                producer / "source/ThreadBench.java",
            )
            copy_regular_tree(
                BENCHBASE_QUALIFICATION_CLASSES, producer / "classes"
            )
        for name, document in qualification_support_documents(scenario).items():
            (stage / "qualification" / name).write_bytes(canonical(document))
        files = relative_regular_files(stage)
        predecessor_manifest = predecessor / predecessor_manifest_name
        identity = _manifest_identity(
            scenario,
            predecessor.name,
            sha256(predecessor_manifest),
            sha256(FOUR_SCENE_CONTRACT),
            sha_bytes(runtime_payload),
            files,
        )
        bundle_id = sha_bytes(canonical(identity))
        manifest = {**identity, "bundle_id": bundle_id}
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            f"{sha_bytes(manifest_payload)}  bundle_manifest.json\n",
            encoding="ascii",
        )
        final = output_root / bundle_id
        if final.exists():
            verify_successor(final)
            shutil.rmtree(stage_parent)
            return final
        verify_successor(
            stage, bundle_id, require_immutable_modes=False
        )
        os.replace(stage, final)
        stage_parent.rmdir()
        freeze_tree_modes(final)
        verify_successor(final)
        return final
    except BaseException:
        if stage_parent.exists():
            make_directories_owner_writable(stage_parent)
            shutil.rmtree(stage_parent)
        raise


def build_all() -> dict[str, Any]:
    global_contract = load_object(FOUR_SCENE_CONTRACT, "four-scene contract")
    if (
        global_contract.get("formal_scope") != "mysql_only"
        or global_contract.get("cross_engine_comparison_prohibited") is not True
        or global_contract.get("formal_launch_authorized") is not False
    ):
        raise BuildError("four-scene contract scope/authority differs")
    verify_xp_predecessor()
    verify_tpcc_predecessor()
    jdk = jdk_tree_manifest()
    python_runtime = python_tree_manifest()
    xp = _publish(
        scenario="xp",
        predecessor=XP_PREDECESSOR,
        predecessor_manifest_name="manifest.json",
        output_root=XP_OUTPUT_ROOT,
        global_contract=global_contract,
        jdk=jdk,
        python_runtime=python_runtime,
        extra_source=None,
    )
    tpcc = _publish(
        scenario="tpcc",
        predecessor=TPCC_PREDECESSOR,
        predecessor_manifest_name="bundle_manifest.json",
        output_root=TPCC_OUTPUT_ROOT,
        global_contract=global_contract,
        jdk=jdk,
        python_runtime=python_runtime,
        extra_source=TPCC_SOURCE_CLOSURE,
    )
    return {
        "schema_version": "mysql_xp_tpcc_distributed_successor_build_result_v1",
        "status": "BUILT_OFFLINE_NOT_LAUNCH_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "remote_actions": 0,
        "jdk_tree_content_sha256": jdk["tree_content_sha256"],
        "python_tree_content_sha256":
            python_runtime["tree_content_sha256"],
        "xp_bundle": str(xp),
        "xp_bundle_id": xp.name,
        "tpcc_bundle": str(tpcc),
        "tpcc_bundle_id": tpcc.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-bundle",
        type=Path,
        help="verify one already-published successor instead of building",
    )
    args = parser.parse_args()
    if args.verify_bundle:
        result: Mapping[str, Any] = verify_successor(
            args.verify_bundle.resolve(strict=True)
        )
    else:
        result = build_all()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
