#!/usr/bin/env python3
"""Build the immutable TPC-C distributed successor v4, locally and offline.

The v4 successor adds a TPC-C-only client-container SQL counter transport.
It forbids host ``mysql`` fallback and maps the read-only host defaults file
to the sealed config mount inside the frozen client container.
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


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
BASE_ID = "233397042055cd093725ea328400116c9a09b7fc990acd751aa09b849d14b248"
BASE = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v3" / BASE_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v4"
SHARED_COLLECTOR = (
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
)
EXPECTED_SHARED_COLLECTOR_SHA256 = (
    "f3b367e839e365edf3169e77070c3fd0072d3ccdcdcd8c9798077573a1fcf781"
)
SHARED_METRIC_ADAPTER = (
    ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py"
)
READINESS_VERIFIER = (
    ROOT / "shell/verify_mysql_tpcc_v3_formal10_readiness.py"
)
ADOPTED_SERVICE = (
    ROOT / "shell/mysql_tpcc_node04_adopted_service_v1.py"
)
PHASE_MATERIALIZER = (
    ROOT / "shell/mysql_tpcc_materialize_phase_command_v2.py"
)
PHASE_AUTHORITY_BUILDER = ROOT / "shell/build_mysql_tpcc_v3_phase_authority.py"
TPCC_CLIENT_SQL_TRANSPORT = ROOT / "shell/mysql_tpcc_distributed_live_readback_qualification_v2.py"
RUNTIME_TREE_HELPER = (
    ROOT / "shell/runtime_tree_content_identity_v3.py"
)
INVALIDATED_BUNDLE_IDS = [
    BASE_ID,
    "ebbdfd33e9e98f45c689e0b72524ea3961428a3de252a24653e269ba093e488d",
    "85a8273c6507e5b3abe17110f29bbf0097db08ecf80b23795434d2e2a2a73dee",
    "5e5cde94355225712d7567d594d3393f4cb43ec0f366b4fccd1dfc2cf77f580b",
    "9ead1b6ade955d42baaedcddd3a51f3781730ffcf25e8f8e8223c0c28afe03b5",
    "ba0d47be2751b0a9c52d3e405ba84f296a8d79216b53cd1d6129d1dd858a31af",
]
COLLECTOR_PAYLOAD_ID = (
    "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
)
COLLECTOR_PAYLOAD = (
    ROOT / "artifacts/mysql_tpcc_typed_collector" / COLLECTOR_PAYLOAD_ID
)
FOUR_SCENE_CONTRACT_SHA256 = (
    "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
)


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


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"regular JSON file required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON object required: {path}")
    return value


def files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"symlink prohibited: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def verify_base() -> dict[str, Any]:
    manifest = load(BASE / "bundle_manifest.json")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != BASE_ID
        or sha_bytes(canonical(unsigned)) != BASE_ID
        or manifest.get("schema_version")
        != "mysql_tpcc_typed_distributed_successor_bundle_v3"
        or manifest.get("scenario") != "tpcc"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("four_scene_contract_sha256")
        != FOUR_SCENE_CONTRACT_SHA256
    ):
        raise BuildError("base TPC-C successor identity differs")
    for relative, expected in (manifest.get("files") or {}).items():
        candidate = BASE / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or sha256(candidate) != expected
        ):
            raise BuildError(f"base TPC-C closure differs: {relative}")
    return manifest


def verify_inputs() -> None:
    verify_base()
    for path in (
        SHARED_COLLECTOR,
        SHARED_METRIC_ADAPTER,
        READINESS_VERIFIER,
        ADOPTED_SERVICE,
        PHASE_MATERIALIZER,
        RUNTIME_TREE_HELPER,
        PHASE_AUTHORITY_BUILDER,
        TPCC_CLIENT_SQL_TRANSPORT,
        BUILDER,
    ):
        if path.is_symlink() or not path.is_file():
            raise BuildError(f"v4 input absent or symlinked: {path}")
    if sha256(SHARED_COLLECTOR) != EXPECTED_SHARED_COLLECTOR_SHA256:
        raise BuildError("shared collector differs from frozen f3b367 authority")
    expected_collector_files = {
        "README.md":
            "03792e4ff6724d032f07897bd33d9b8f0caccc23197fbe523b462c333963ce5d",
        "test_tpcc_typed_collector.py":
            "6c23587a3f05dc801e6a6dfcb7cc0dd3afadde5fd32f6646b8e64c2d8c285102",
        "tpcc_typed_collector.py":
            "f95c21304314474eaca1930fc3fd14931e3034f2c0ecd875813fa53b7d227994",
    }
    actual = {
        path.name
        for path in COLLECTOR_PAYLOAD.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    if actual != set(expected_collector_files):
        raise BuildError("032eb2 collector payload file set differs")
    for relative, expected in expected_collector_files.items():
        if sha256(COLLECTOR_PAYLOAD / relative) != expected:
            raise BuildError(f"032eb2 collector differs: {relative}")


def copy_mutable(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise BuildError(f"regular source directory required: {source}")
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative.as_posix() in {
            "bundle_manifest.json",
            "bundle_manifest.json.sha256",
        }:
            continue
        target = destination / relative
        if path.is_symlink():
            raise BuildError(f"symlink prohibited: {path}")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, 0o755)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            mode = stat.S_IMODE(path.stat().st_mode)
            os.chmod(target, mode | 0o200)


def write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.chmod(path, mode)


def readiness_contract() -> dict[str, Any]:
    return {
        "schema_version":
            "mysql_tpcc_distributed_formal10_readiness_contract_v3",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "collector_payload_id": COLLECTOR_PAYLOAD_ID,
        "typed_capture_bundle_id":
            "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516",
        "binding_count": 34,
        "transaction_types": [
            "Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"
        ],
        "sensitive_stock_level_normalizes_to": "StockLevel",
        "setter_type_position_sql_hash_required": True,
        "l2_l3_required": True,
        "qps_resource_input_prohibited": True,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "real_production_trace_registry_schema":
            "mysql_tpcc_typed_trace_registry_bundle_v2",
        "same_live_container_handoff_schema":
            "mysql_tpcc_same_live_container_formal10_handoff_v2",
        "adopted_service_schema":
            "mysql_tpcc_node04_adopted_service_authority_v1",
        "phase_command_schema":
            "mysql_tpcc_materialized_phase_command_v1",
        "same_db_identity_fields": [
            "container_id",
            "pid",
            "process_start_ticks",
            "datadir",
            "device_major_minor",
        ],
        "handoff_phases": [
            "qualification",
            "typed_capture",
            "fresh_r0",
            "debug",
            "formal10_launch",
        ],
        "client_envelope": {
            "cpuset_cpus": "8-15,44-51",
            "cpuset_mems": "0",
            "cpu_quota_cores": 16,
            "memory_bytes": 8589934592,
            "memory_swap_bytes": 8589934592,
            "pids_max": 1024,
            "read_only_root": True,
            "target_mysql_device_write_prohibited": True,
        },
        "database_envelope": {
            "cpuset_cpus": "21-24,57-60",
            "cpuset_mems": "1",
            "cpu_quota_cores": 4,
            "mysql_port": 65106,
            "device": "/dev/nvme3n1",
            "device_major_minor": "259:2",
        },
        "qualification_gate": {
            "minimum_duration_seconds": 180,
            "minimum_samples": 181,
            "maximum_sample_gap_seconds": 1.5,
            "foreign_device_io_attribution_bytes_max": 0,
            "missing_or_timeout_action": "FAIL_CLOSED",
        },
        "current_bundle_authority": {
            "qualification": True,
            "typed_capture_via_materialized_composite_command_after_gate": True,
            "production_trace_registry_seal": True,
            "formal_r0": False,
            "debug": False,
            "formal_outer": False,
            "accepted_round": False,
        },
        "supersession": {
            "supersedes_bundle_id": BASE_ID,
            "invalidated_bundle_ids": INVALIDATED_BUNDLE_IDS,
            "source_base_bundle_id": BASE_ID,
            "superseded_bundle_launch_authorized": False,
        },
    }


def invalidation_authority() -> dict[str, Any]:
    return {
        "schema_version":
            "mysql_tpcc_distributed_successor_invalidation_v1",
        "status": "INVALIDATED_DO_NOT_LAUNCH",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "invalidated_bundle_id": BASE_ID,
        "invalidated_bundle_manifest_sha256": sha256(BASE / "bundle_manifest.json"),
        "replacement_shared_collector_sha256":
            sha256(SHARED_COLLECTOR),
        "reason": (
            "v4 forbids host MySQL counter fallback and seals the exact "
            "client-container defaults-file SQL transport"
        ),
        "launch_authorized": False,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def typed_capture_command_template(
    qualification_template: Mapping[str, Any],
) -> dict[str, Any]:
    java_recipe = qualification_template["native_java_launch_recipe"]
    java_argv = java_recipe["materialized_argv"]
    return {
        "schema_version":
            "mysql_tpcc_distributed_typed_capture_command_template_v2",
        "status": "TEMPLATE_NOT_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "debug_only": True,
        "capture_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "qualification_attempt_id":
            "REPLACE_WITH_UNIQUE_AT_LEAST_32_CHAR_ATTEMPT_ID",
        "actual_main_class": "com.oltpbenchmark.DBWorkload",
        "native_java_launch_recipe": java_recipe,
        "actual_java_argv": java_argv,
        "argv": [
            "${PYTHON_BINARY}",
            "${BUNDLE_ROOT}/predecessor/orchestration/"
            "formal_tpcc_composite_workload.py",
            "--defaults-extra-file",
            "${MYSQL_DEFAULTS_FILE}",
            "--host",
            "127.0.0.1",
            "--port",
            "65106",
            "--database",
            "tpcc",
            "--sensitive-sql",
            "${BUNDLE_ROOT}/predecessor/config/"
            "mysql_tpcc_sensitive_item_semijoin.sql",
            "--event-path",
            "${MEASUREMENT_TMPFS}/sensitive_events.jsonl",
            "--interval-seconds",
            "15",
            "--",
            *java_argv,
        ],
        "environment": {
            **qualification_template["environment"],
            "MYSQL_TPCC_TYPED_TRACE_PATH":
                "${MEASUREMENT_TMPFS}/tpcc_typed_trace.jsonl",
            "MYSQL_TPCC_TYPED_MANIFEST_PATH":
                "${BUNDLE_ROOT}/predecessor/config/"
                "tpcc_offline_binding_manifest.json",
            "MYSQL_TPCC_TYPED_MANIFEST_SHA256":
                "689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182",
            "MYSQL_TPCC_TYPED_CAPTURE_TOKEN":
                "${QUALIFICATION_ATTEMPT_ID}",
            "MYSQL_TPCC_TYPED_METRICS_JDBC_URL":
                "${MYSQL_JDBC_URL}",
            "MYSQL_TPCC_TYPED_METRICS_USER": "${MYSQL_USER}",
            "MYSQL_TPCC_TYPED_METRICS_PASSWORD":
                "${MYSQL_PASSWORD_SECRET}",
            "MYSQL_TPCC_TYPED_TARGET_JDBC_URL":
                "${MYSQL_JDBC_URL}",
            "MYSQL_TPCC_TYPED_TARGET_USER": "${MYSQL_USER}",
            "MYSQL_TPCC_TYPED_TARGET_PASSWORD":
                "${MYSQL_PASSWORD_SECRET}",
            "MYSQL_TPCC_TYPED_CORRECTNESS_VERIFIED": "1",
            "MYSQL_TPCC_TYPED_CAPTURE_ONLY": "1",
            "MYSQL_TPCC_TYPED_OUTER_AUTHORIZED": "0",
            "MYSQL_TPCC_TYPED_TRACE_ROLE": "OWNER",
            "MYSQL_TPCC_TYPED_BENCHBASE_CLASSPATH":
                java_argv[2],
            "MYSQL_TPCC_TYPED_SENSITIVE_SQL_PATH":
                "${BUNDLE_ROOT}/predecessor/config/"
                "mysql_tpcc_sensitive_item_semijoin.sql",
            "MYSQL_TPCC_TYPED_JAVA": "${JAVA_BINARY}",
        },
        "required_observed_binding_count": 34,
        "required_transaction_types": [
            "Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"
        ],
        "client_container":
            qualification_template["client_container"],
        "workload_process_isolation":
            qualification_template["workload_process_isolation"],
        "measurement_output":
            qualification_template["measurement_output"],
        "qps_resource_input_prohibited": True,
    }


def patch_stage(stage: Path) -> None:
    write(
        stage / "qualification/mysql_distributed_live_readback_qualification_v1.py",
        SHARED_COLLECTOR.read_bytes(),
        0o755,
    )
    write(
        stage / "qualification/mysql_distributed_workload_metric_adapter_v1.py",
        SHARED_METRIC_ADAPTER.read_bytes(),
        0o755,
    )
    write(
        stage / "qualification/mysql_tpcc_distributed_live_readback_qualification_v2.py",
        TPCC_CLIENT_SQL_TRANSPORT.read_bytes(),
        0o755,
    )
    write(
        stage / "authority/tpcc_v3_formal10_readiness_verifier.py",
        READINESS_VERIFIER.read_bytes(),
        0o755,
    )
    write(
        stage / "authority/mysql_tpcc_node04_adopted_service_v1.py",
        ADOPTED_SERVICE.read_bytes(),
        0o755,
    )
    write(
        stage / "authority/mysql_tpcc_materialize_phase_command_v2.py",
        PHASE_MATERIALIZER.read_bytes(),
        0o755,
    )
    write(
        stage / "authority/runtime_tree_content_identity_v3.py",
        RUNTIME_TREE_HELPER.read_bytes(),
        0o755,
    )
    write(
        stage / "authority/build_mysql_tpcc_distributed_successor_v3.py",
        BUILDER.read_bytes(),
        0o755,
    )
    write(
        stage / "authority/build_mysql_tpcc_v3_phase_authority.py",
        PHASE_AUTHORITY_BUILDER.read_bytes(),
        0o755,
    )
    payload_destination = (
        stage / "authority" / f"collector_payload_{COLLECTOR_PAYLOAD_ID}"
    )
    for source in sorted(COLLECTOR_PAYLOAD.iterdir()):
        if source.is_file() and not source.is_symlink():
            write(payload_destination / source.name, source.read_bytes())

    runtime_path = stage / "distributed_runtime_contract.json"
    runtime = load(runtime_path)
    runtime["schema_version"] = (
        "mysql_algorithm_c_tpcc_distributed_runtime_contract_v4"
    )
    runtime["supersedes_bundle_id"] = BASE_ID
    runtime["source_base_bundle_id"] = BASE_ID
    runtime["runtime_capabilities"] = {
        "qualification": True,
        "typed_capture_after_gate": True,
        "typed_capture_mode": "materialized_composite_client_command_only",
        "legacy_local_capture_entry_launch_authorized": False,
        "production_trace_registry_v2_seal": True,
        "same_live_container_handoff_authority": True,
        "node04_adopted_service_bootstrap": True,
        "private_tmpfs_metric_bridge": True,
        "ordered_phase_command_materializer": True,
        "manifest_bound_mounted_jdk_python": True,
        "docker_image_id_live_readback": True,
        "client_target_device_prefault_zero_io_gate": True,
        "fresh_r0": False,
        "debug": False,
        "formal_outer": False,
    }
    runtime["readiness_promotion"] = {
        "verifier":
            "authority/tpcc_v3_formal10_readiness_verifier.py",
        "contract":
            "authority/tpcc_v3_formal10_readiness_contract.json",
        "real_production_trace_required": True,
        "same_live_database_container_required": True,
        "adopted_service":
            "authority/mysql_tpcc_node04_adopted_service_v1.py",
        "phase_materializer":
            "authority/mysql_tpcc_materialize_phase_command_v2.py",
        "detached_phase_authority_builder":
            "authority/build_mysql_tpcc_v3_phase_authority.py",
        "formal_authority_created_by_this_bundle": False,
    }
    runtime["client_runtime_mounts"] = {
        "jdk": {
            "source":
                "/home/anonymous/recost_deploy_20260721_v1/runtime/"
                "jdk-23.0.2-oracle-x64",
            "destination": "/opt/mysql-tpcc-jdk",
            "tree_content_sha256":
                "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c",
            "binary": "/opt/mysql-tpcc-jdk/bin/java",
            "read_only": True,
        },
        "python": {
            "source": "/home/anonymous/recost_runtime_py311_24_11_1",
            "destination": "/opt/mysql-tpcc-python",
            "tree_content_sha256":
                "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
            "binary": "/opt/mysql-tpcc-python/bin/python3.11",
            "read_only": True,
        },
    }
    runtime["shared_qualification_inputs"] = {
        "collector_sha256": sha256(SHARED_COLLECTOR),
        "metric_adapter_sha256": sha256(SHARED_METRIC_ADAPTER),
        "collector_features": [
            "docker_root_owned_coresident_fallback",
            "full_host_rolling_inventory",
            "foreign_target_device_writer_attribution",
        ],
        "metric_adapter_features": [
            "native_benchbase_latency",
            "same_attempt_fresh_stream",
            "sql_stdout_host_bridge",
        ],
    }
    runtime["database_counter_transport"] = {
        "schema_version": "mysql_algorithm_c_database_counter_transport_v2",
        "kind": "exact_client_container_docker_exec_stdout_v2",
        "mysql_binary_in_client": "/usr/bin/mysql",
        "stdout_only": True,
        "host_mysql_cli_required": False,
        "host_mysql_cli_prohibited": True,
        "docker_socket_mount_prohibited": True,
        "target_datadir_mount_prohibited": True,
        "client_paths": {
            "bundle": "/opt/mysql-tpcc-bundle",
            "python": "/opt/mysql-tpcc-python",
            "config": "/opt/mysql-tpcc-config",
        },
    }
    write(runtime_path, canonical(runtime))

    qualification_contract_path = (
        stage / "qualification/qualification_execution_contract.json"
    )
    qualification_contract = load(qualification_contract_path)
    qualification_contract["successor_generation"] = "tpcc_v4"
    qualification_contract["entrypoint"] = (
        "qualification/mysql_tpcc_distributed_live_readback_qualification_v2.py"
    )
    qualification_contract["tpcc_client_sql_transport"] = {
        "host_mysql_cli_prohibited": True,
        "client_mysql_binary": "/usr/bin/mysql",
        "host_defaults_to_client_config_mount_mapping_required": True,
    }
    qualification_contract["shared_collector_sha256"] = sha256(
        SHARED_COLLECTOR
    )
    qualification_contract["shared_metric_adapter_sha256"] = sha256(
        SHARED_METRIC_ADAPTER
    )
    qualification_contract["tpcc_readiness_verifier"] = (
        "authority/tpcc_v3_formal10_readiness_verifier.py"
    )
    qualification_contract["typed_capture_command_template"] = (
        "qualification/typed_capture_command_template_v2.json"
    )
    write(qualification_contract_path, canonical(qualification_contract))

    qualification_template = load(
        stage / "qualification/qualification_command_template.json"
    )
    write(
        stage / "qualification/typed_capture_command_template_v2.json",
        canonical(typed_capture_command_template(qualification_template)),
    )
    write(
        stage / "authority/tpcc_v3_formal10_readiness_contract.json",
        canonical(readiness_contract()),
    )
    write(
        stage
        / f"authority/invalidation_{BASE_ID}.json",
        canonical(invalidation_authority()),
    )


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError(f"symlink prohibited: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, mode & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def thaw_directories(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o700)


def verify_published(bundle: Path) -> dict[str, Any]:
    manifest = load(bundle / "bundle_manifest.json")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or sha_bytes(canonical(unsigned)) != bundle.name
        or manifest.get("schema_version")
        != "mysql_tpcc_typed_distributed_successor_bundle_v4"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("immutable") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("supersedes_bundle_id") != BASE_ID
        or manifest.get("source_base_bundle_id") != BASE_ID
        or manifest.get("shared_collector_sha256") != sha256(SHARED_COLLECTOR)
        or manifest.get("shared_metric_adapter_sha256")
        != sha256(SHARED_METRIC_ADAPTER)
        or manifest.get("readiness_verifier_sha256")
        != sha256(READINESS_VERIFIER)
        or manifest.get("adopted_service_sha256")
        != sha256(ADOPTED_SERVICE)
        or manifest.get("phase_materializer_sha256")
        != sha256(PHASE_MATERIALIZER)
        or manifest.get("runtime_tree_helper_sha256")
        != sha256(RUNTIME_TREE_HELPER)
        or manifest.get("shared_collector_sha256")
        != EXPECTED_SHARED_COLLECTOR_SHA256
        or manifest.get("invalidated_bundle_ids") != INVALIDATED_BUNDLE_IDS
        or manifest.get("phase_authority_builder_sha256")
        != sha256(PHASE_AUTHORITY_BUILDER)
        or manifest.get("tpcc_client_sql_transport_sha256")
        != sha256(TPCC_CLIENT_SQL_TRANSPORT)
    ):
        raise BuildError("published TPC-C v4 manifest differs")
    for relative, expected in (manifest.get("files") or {}).items():
        candidate = bundle / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or sha256(candidate) != expected
        ):
            raise BuildError(f"published TPC-C v4 closure differs: {relative}")
    sidecar = bundle / "bundle_manifest.json.sha256"
    expected_sidecar = (
        f"{sha256(bundle / 'bundle_manifest.json')}  bundle_manifest.json\n"
    )
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii") != expected_sidecar
    ):
        raise BuildError("published TPC-C v4 manifest sidecar differs")
    for path in [bundle, *sorted(bundle.rglob("*"))]:
        if stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError(f"published TPC-C v4 entry writable: {path}")
    return manifest


def build(output_root: Path = OUTPUT_ROOT) -> Path:
    verify_inputs()
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=".tpcc-v4.", dir=str(output_root))
    )
    try:
        copy_mutable(BASE, stage)
        patch_stage(stage)
        closure = files(stage)
        runtime_sha = closure["distributed_runtime_contract.json"]
        readiness_contract_sha = closure[
            "authority/tpcc_v3_formal10_readiness_contract.json"
        ]
        identity = {
            "schema_version":
                "mysql_tpcc_typed_distributed_successor_bundle_v4",
            "scenario": "tpcc",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "immutable": True,
            "offline_build_only": True,
            "remote_actions": 0,
            "supersedes_bundle_id": BASE_ID,
            "source_base_bundle_id": BASE_ID,
            "superseded_bundle_launch_authorized": False,
            "invalidated_bundle_ids": INVALIDATED_BUNDLE_IDS,
            "base_manifest_sha256": sha256(BASE / "bundle_manifest.json"),
            "four_scene_contract_sha256": FOUR_SCENE_CONTRACT_SHA256,
            "runtime_contract_sha256": runtime_sha,
            "readiness_contract_sha256": readiness_contract_sha,
            "builder_sha256": sha256(BUILDER),
            "shared_collector_sha256": sha256(SHARED_COLLECTOR),
            "shared_metric_adapter_sha256": sha256(SHARED_METRIC_ADAPTER),
            "readiness_verifier_sha256": sha256(READINESS_VERIFIER),
            "adopted_service_sha256": sha256(ADOPTED_SERVICE),
            "phase_materializer_sha256": sha256(PHASE_MATERIALIZER),
            "phase_authority_builder_sha256": sha256(PHASE_AUTHORITY_BUILDER),
            "tpcc_client_sql_transport_sha256": sha256(TPCC_CLIENT_SQL_TRANSPORT),
            "runtime_tree_helper_sha256": sha256(RUNTIME_TREE_HELPER),
            "collector_payload_id": COLLECTOR_PAYLOAD_ID,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "fresh_r0_required": True,
            "real_production_trace_required": True,
            "same_live_container_handoff_required": True,
            "files": closure,
        }
        bundle_id = sha_bytes(canonical(identity))
        manifest = {**identity, "bundle_id": bundle_id}
        write(stage / "bundle_manifest.json", canonical(manifest))
        write(
            stage / "bundle_manifest.json.sha256",
            (
                f"{sha256(stage / 'bundle_manifest.json')}  "
                "bundle_manifest.json\n"
            ).encode("ascii"),
        )
        freeze(stage)
        destination = output_root / bundle_id
        if destination.exists():
            verify_published(destination)
            return destination
        os.rename(stage, destination)
        directory_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination
    finally:
        if stage.exists():
            thaw_directories(stage)
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        bundle = args.verify.resolve(strict=True)
        manifest = verify_published(bundle)
    else:
        bundle = build(args.output_root.resolve())
        manifest = verify_published(bundle)
    print(json.dumps({
        "status": "PASS",
        "bundle_id": bundle.name,
        "bundle": str(bundle),
        "formal_launch_authorized":
            manifest["formal_launch_authorized"],
        "accepted_round_authorized":
            manifest["accepted_round_authorized"],
        "shared_collector_sha256":
            manifest["shared_collector_sha256"],
        "shared_metric_adapter_sha256":
            manifest["shared_metric_adapter_sha256"],
        "readiness_verifier_sha256":
            manifest["readiness_verifier_sha256"],
        "adopted_service_sha256":
            manifest["adopted_service_sha256"],
        "phase_materializer_sha256":
            manifest["phase_materializer_sha256"],
        "runtime_tree_helper_sha256":
            manifest["runtime_tree_helper_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
