#!/usr/bin/env python3
"""Build an immutable TPC-H successor calibrated from failed fresh-R0 v23."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "b1ffe6e8dd6396d6da1ee6cb7e2a3a6548a71cac9388b098873b56ed9cb0ff59"
PARENT_MANIFEST_SHA256 = (
    "73f75e94533b9626f32cf39d678f6a4d94a8797eb8818c9ede55afc9d95437a6"
)
INTERMEDIATE_ID = (
    "a0cebba1482d70728cb0fcfefb474696f11b4896a53007d8db92858b3145cd0a"
)
INTERMEDIATE_MANIFEST_SHA256 = (
    "6bd445aa2fafdc1365fa67b624006f748cefa64262b5881a837716d05bdd1641"
)
PARENT = ROOT / "artifacts/mysql_tpch_self_contained_successor_v1" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_v23_recalibrated_successor_v3"

CPU_QUOTA_CORES = 3.0
CPU_PERIOD_US = 100_000
CPU_QUOTA_US = 300_000
IO_BPS = 335_544_320
MYSQL_PORT = 65_103

V23_CPU_QUOTA_CORES = 1.5
V23_IO_BPS = 288_358_400
V23_CPU_UTILIZATION = 0.7818950800663341
V23_MEMORY_UTILIZATION = 0.9115112225525035
V23_IO_UTILIZATION = 0.5002418484279849
MEASURED_CPU_DEMAND_CORES = V23_CPU_QUOTA_CORES * V23_CPU_UTILIZATION
MEASURED_IO_DEMAND_BPS = V23_IO_BPS * V23_IO_UTILIZATION
PROJECTED_CPU_UTILIZATION = MEASURED_CPU_DEMAND_CORES / CPU_QUOTA_CORES
PROJECTED_IO_UTILIZATION = MEASURED_IO_DEMAND_BPS / IO_BPS

FORMAL_CONTRACT = "tpch_formal10_contract.json"
RUNTIME_CONTRACT = "distributed_runtime_contract.json"
FOUR_SCENE_CONTRACT = "contracts/four_scene_v2.json"
AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
READINESS = "tpch_formal10_readiness_v2.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
QUALIFICATION_AUTHORITY = (
    "qualification/mysql_tpch_v2_qualification_authority_v1.py"
)
FRESH_R0_RUNNER = "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py"
BLKIO_HELPER = "qualification/mysql_tpch_blkio_v1.py"
READ_ONLY_VERIFIER = "builder/build_mysql_tpch_socket_identity_successor_v1.py"
SEALED_TEST = "offline_tests/test_sealed_bundle_self_contained_v1.py"
PROJECTION_TEST = "offline_tests/test_effective_authority_projection_v1.py"
PROFILE = "algorithm_source/profiles/tpch_v35_debug_profile.json"
LOCAL_SERVICE = "algorithm_source/shell/mysql_tpch_algorithm_c_local_services_v27.py"
LOCAL_READINESS = "algorithm_source/shell/verify_mysql_tpch_v35_readiness.py"
LOCAL_AUTHORITY_TEST = "algorithm_source/tests/test_mysql_tpch_v35_authority.py"
INVALIDATION = "invalidated_b1ff_stale_resource_envelope_bundle.json"
INTERMEDIATE_INVALIDATION = (
    "invalidated_a0c_stale_four_scene_hash_binding_bundle.json"
)
RECALIBRATION = "tpch_v23_fresh_r0_recalibration_evidence.json"
COLLECTOR_SCHEMA_INVALIDATION = (
    "invalidated_78e_v23_internal_schema_projection_bundle.json"
)
CREDENTIAL_EXEC_INVALIDATION = (
    "invalidated_039_client_credentials_exec_user_bundle.json"
)
AUTHORITY_EMPTY_PROJECTION_INVALIDATION = (
    "invalidated_a662_authority_empty_blkio_projection_bundle.json"
)

COMPONENT_PATHS = {
    "blkio_v1_helper": BLKIO_HELPER,
    "bootstrap_authority": AUTHORITY,
    "client_identity_helper_qualification":
        "qualification/mysql_tpch_client_immutable_identity_v1.py",
    "client_identity_helper_root": "mysql_tpch_client_immutable_identity_v1.py",
    "client_mysql_datadir_readonly_mask":
        "qualification/client_volume_masks/mysql_datadir/mask_identity.json",
    "collector": COLLECTOR,
    "fresh_r0_runner": FRESH_R0_RUNNER,
    "metric_adapter":
        "qualification/mysql_distributed_workload_metric_adapter_v1.py",
    "qualification_authority_renderer":
        "qualification/mysql_tpch_v2_qualification_authority_v1.py",
    "readiness_verifier": READINESS,
    "runtime_helper": "qualification/runtime_tree_content_identity_v3.py",
    "self_contained_read_only_verifier": READ_ONLY_VERIFIER,
    "sql_adapter": "qualification/mysql_sql_debug_metric_adapter_v1.py",
    "systemd_oneshot_materializer":
        "launch/mysql_tpch_systemd_oneshot_materializer_v1.py",
}


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
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


def load_object_payload(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError("invalid JSON payload: " + label) from exc
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + label)
    return value


def load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError("absent or symlinked: " + str(path))
    return load_object_payload(path.read_bytes(), str(path))


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise BuildError("symlink in bundle closure: " + relative)
        if path.is_file():
            if path in (
                root / "bundle_manifest.json",
                root / "bundle_manifest.json.sha256",
            ):
                continue
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                raise BuildError("bytecode/cache in bundle closure: " + relative)
            result[relative] = sha256(path)
        elif not path.is_dir():
            raise BuildError("special file in bundle closure: " + relative)
    return result


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load_object(manifest_path)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    sidecar = PARENT / "bundle_manifest.json.sha256"
    if (
        PARENT.name != PARENT_ID
        or bundle_id != PARENT_ID
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or sha256(manifest_path) != PARENT_MANIFEST_SHA256
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != PARENT_MANIFEST_SHA256 + "  bundle_manifest.json\n"
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("b1ff parent identity, closure, or authority differs")
    return manifest


def replace_exact(
    payload: bytes, old: str, new: str, expected_count: int, label: str
) -> bytes:
    text = payload.decode("utf-8")
    count = text.count(old)
    if count != expected_count:
        raise BuildError(
            f"{label} patch point differs: expected {expected_count}, got {count}"
        )
    return text.replace(old, new).encode("utf-8")


def json_payload(value: dict[str, Any]) -> bytes:
    return canonical(value)


def recalibration_evidence() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_v23_resource_recalibration_v1",
        "scenario": "tpch_memory",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_attempt": {
            "version": "v23",
            "terminal_status": "R0_FAILED",
            "valid_r0": False,
            "accepted_rounds_credit": 0,
            "cpu_quota_cores": V23_CPU_QUOTA_CORES,
            "io_read_bps": V23_IO_BPS,
            "io_write_bps": V23_IO_BPS,
            "observed_utilization": {
                "cpu": V23_CPU_UTILIZATION,
                "memory": V23_MEMORY_UTILIZATION,
                "io": V23_IO_UTILIZATION,
            },
        },
        "demand_derivation": {
            "cpu_formula": "v23_cpu_quota_cores * v23_cpu_utilization",
            "measured_cpu_demand_cores": MEASURED_CPU_DEMAND_CORES,
            "io_formula": "v23_io_bps_per_direction * v23_io_utilization",
            "measured_io_demand_bps_per_direction": MEASURED_IO_DEMAND_BPS,
        },
        "successor_envelope": {
            "cpu_quota_cores": CPU_QUOTA_CORES,
            "cpu_period_us": CPU_PERIOD_US,
            "cpu_quota_us": CPU_QUOTA_US,
            "io_read_bps": IO_BPS,
            "io_write_bps": IO_BPS,
            "projected_utilization": {
                "cpu": PROJECTED_CPU_UTILIZATION,
                "io": PROJECTED_IO_UTILIZATION,
            },
            "target_intervals": {
                "cpu": [0.30, 0.50],
                "memory": [0.85, 0.95],
                "io": [0.30, 0.50],
            },
            "io_measurement_margin": (
                "320_MiB_per_direction_projects_v23_demand_to_about_0.43"
            ),
        },
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def invalidation_evidence() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_stale_resource_envelope_invalidation_v1",
        "status": "INVALIDATED_DO_NOT_RUN_OR_CREDIT",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "reasons": [
            "formal_cpu_quota_4_0_projects_v23_demand_below_cpu_target_interval",
            "formal_io_cap_96_MiB_per_direction_conflicts_with_v23_measured_demand",
            "component_and_qualification_hash_references_require_successor_rebinding",
        ],
        "capture_credit": 0,
        "r0_credit": 0,
        "replay_credit": 0,
        "accepted_rounds_credit": 0,
        "container_started": False,
        "database_started": False,
        "workload_started": False,
        "successor_fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def intermediate_invalidation_evidence() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_stale_contract_hash_invalidation_v1",
        "status": "INVALIDATED_DO_NOT_RUN_OR_CREDIT",
        "bundle_id": INTERMEDIATE_ID,
        "bundle_manifest_sha256": INTERMEDIATE_MANIFEST_SHA256,
        "reason": (
            "intermediate_successor_retained_stale_four_scene_contract_hash_"
            "constants_in_authority_and_readiness"
        ),
        "capture_credit": 0,
        "r0_credit": 0,
        "replay_credit": 0,
        "accepted_rounds_credit": 0,
        "published_remotely": False,
        "container_started": False,
        "database_started": False,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def patched_payloads(parent: dict[str, Any]) -> dict[str, bytes]:
    content: dict[str, bytes] = {}
    for relative, expected in sorted(parent["files"].items()):
        source = PARENT / relative
        payload = source.read_bytes()
        if sha_bytes(payload) != expected:
            raise BuildError("parent file differs: " + relative)
        content[relative] = payload

    authority = content[AUTHORITY]
    authority = replace_exact(
        authority, '"cpu_quota_cores": 4,', '"cpu_quota_cores": 3.0,',
        1, AUTHORITY,
    )
    authority = replace_exact(
        authority, '"cpu_quota_us": 400000,', '"cpu_quota_us": 300000,',
        1, AUTHORITY,
    )
    authority = replace_exact(
        authority, '"io_read_bps": 100663296,',
        '"io_read_bps": 335544320,', 1, AUTHORITY,
    )
    authority = replace_exact(
        authority, '"io_write_bps": 100663296,',
        '"io_write_bps": 335544320,', 1, AUTHORITY,
    )
    authority = replace_exact(
        authority,
        "        or declared_read is not True\n"
        "        or declared_write is not True\n",
        "        or (declared_read is not True\n"
        '            and db_host.get("BlkioDeviceReadBps") != [])\n'
        "        or (declared_write is not True\n"
        '            and db_host.get("BlkioDeviceWriteBps") != [])\n',
        1,
        AUTHORITY,
    )
    content[AUTHORITY] = authority

    readiness = content[READINESS]
    readiness = replace_exact(
        readiness, '"cpu_quota_cores": 4,', '"cpu_quota_cores": 3.0,',
        1, READINESS,
    )
    readiness = replace_exact(
        readiness, '"io_read_bps": 100663296,',
        '"io_read_bps": 335544320,', 1, READINESS,
    )
    readiness = replace_exact(
        readiness, '"io_write_bps": 100663296,',
        '"io_write_bps": 335544320,', 1, READINESS,
    )
    readiness = replace_exact(
        readiness, 'effective_blkio.get("read_bps") != 100663296',
        'effective_blkio.get("read_bps") != 335544320', 1, READINESS,
    )
    readiness = replace_exact(
        readiness, 'effective_blkio.get("write_bps") != 100663296',
        'effective_blkio.get("write_bps") != 335544320', 1, READINESS,
    )
    content[READINESS] = readiness

    # The v23 envelope was raised to 320 MiB/s, but the sealed root-only
    # cgroup-v1 helper retained the predecessor's 96 MiB/s constant.  That
    # makes the helper's truthful read-back disagree with the authority
    # verifier after the containers start.  Bind the effective writer to the
    # same immutable envelope as every verifier/contract.
    blkio_helper = content[BLKIO_HELPER]
    blkio_helper = replace_exact(
        blkio_helper,
        "IO_BPS = 100_663_296",
        "IO_BPS = 335_544_320",
        1,
        BLKIO_HELPER,
    )
    content[BLKIO_HELPER] = blkio_helper

    collector = content[COLLECTOR]
    collector = replace_exact(
        collector,
        '        "mysql_tpch_distributed_formal10_successor_v2",\n'
        "    ):\n",
        '        "mysql_tpch_distributed_formal10_successor_v2",\n'
        '        "mysql_tpch_v23_recalibrated_successor_v3",\n'
        "    ):\n",
        1,
        COLLECTOR,
    )
    collector = replace_exact(
        collector,
        '            "kind": "exact_client_container_docker_exec_stdout_v1",\n'
        '            "mysql_binary_in_client": "/usr/bin/mysql",\n',
        '            "kind": "exact_client_container_docker_exec_stdout_v1",\n'
        '            "mysql_binary_in_client": "/usr/bin/mysql",\n'
        '            "docker_exec_user": "0",\n',
        1,
        COLLECTOR,
    )
    collector = replace_exact(
        collector,
        '            *DOCKER_CLI,\n'
        '            "exec",\n'
        '            transport["client_container_id"],\n',
        '            *DOCKER_CLI,\n'
        '            "exec", "--user", transport["docker_exec_user"],\n'
        '            transport["client_container_id"],\n',
        1,
        COLLECTOR,
    )
    collector = replace_exact(
        collector,
        '            *DOCKER_CLI,\n'
        '            "exec",\n'
        '            client_id,\n'
        '            mysql_binary_in_client,\n'
        '            "--version",\n',
        '            *DOCKER_CLI,\n'
        '            "exec", "--user", contract["docker_exec_user"],\n'
        '            client_id,\n'
        '            mysql_binary_in_client,\n'
        '            "--version",\n',
        1,
        COLLECTOR,
    )
    collector = replace_exact(
        collector,
        '        "mysql_binary_in_client": mysql_binary_in_client,\n'
        '        "mysql_defaults_file_in_client": defaults_file_in_client,\n',
        '        "mysql_binary_in_client": mysql_binary_in_client,\n'
        '        "docker_exec_user": contract["docker_exec_user"],\n'
        '        "mysql_defaults_file_in_client": defaults_file_in_client,\n',
        1,
        COLLECTOR,
    )
    content[COLLECTOR] = collector

    for relative in (READINESS, QUALIFICATION_AUTHORITY):
        payload = content[relative]
        payload = replace_exact(
            payload,
            '        != "mysql_tpch_distributed_formal10_successor_v2"\n',
            '        != "mysql_tpch_v23_recalibrated_successor_v3"\n',
            1,
            relative,
        )
        content[relative] = payload

    for relative, cpu_count, read_count, write_count in (
        (LOCAL_SERVICE, 3, 3, 3),
        (LOCAL_READINESS, 2, 2, 2),
        (LOCAL_AUTHORITY_TEST, 1, 1, 1),
    ):
        payload = content[relative]
        payload = replace_exact(
            payload, '"cpu_cores": 4,', '"cpu_cores": 3.0,',
            1 if relative != LOCAL_AUTHORITY_TEST else 0, relative,
        ) if relative != LOCAL_AUTHORITY_TEST else payload
        payload = replace_exact(
            payload, '"cpu_quota_cores": 4,', '"cpu_quota_cores": 3.0,',
            cpu_count - (1 if relative != LOCAL_AUTHORITY_TEST else 0),
            relative,
        )
        payload = replace_exact(
            payload, '"read_bps": 100663296,', '"read_bps": 335544320,',
            1 if relative != LOCAL_AUTHORITY_TEST else 0, relative,
        ) if relative != LOCAL_AUTHORITY_TEST else payload
        payload = replace_exact(
            payload, '"write_bps": 100663296,', '"write_bps": 335544320,',
            1 if relative != LOCAL_AUTHORITY_TEST else 0, relative,
        ) if relative != LOCAL_AUTHORITY_TEST else payload
        payload = replace_exact(
            payload, '"io_read_bps": 100663296,',
            '"io_read_bps": 335544320,', read_count - (
                1 if relative != LOCAL_AUTHORITY_TEST else 0
            ), relative,
        )
        payload = replace_exact(
            payload, '"io_write_bps": 100663296,',
            '"io_write_bps": 335544320,', write_count - (
                1 if relative != LOCAL_AUTHORITY_TEST else 0
            ), relative,
        )
        content[relative] = payload

    projection = content[PROJECTION_TEST]
    projection = replace_exact(
        projection, "RATE = 100663296", "RATE = 335544320",
        1, PROJECTION_TEST,
    )
    content[PROJECTION_TEST] = projection

    sealed_test = content[SEALED_TEST]
    sealed_test = replace_exact(
        sealed_test,
        '(4, 100663296, 100663296, 65103)',
        '(3.0, 335544320, 335544320, 65103)',
        1,
        SEALED_TEST,
    )
    sealed_test = replace_exact(
        sealed_test,
        'INVALIDATION = json.loads(\n'
        '    (BUNDLE / "invalidated_313_nonselfcontained_bundle.json").read_text()\n'
        ')\n',
        'INVALIDATION = json.loads(\n'
        '    (BUNDLE / "invalidated_313_nonselfcontained_bundle.json").read_text()\n'
        ')\n'
        'STALE_ENVELOPE_INVALIDATION = json.loads(\n'
        '    (BUNDLE / "invalidated_b1ff_stale_resource_envelope_bundle.json").read_text()\n'
        ')\n'
        'INTERMEDIATE_INVALIDATION = json.loads(\n'
        '    (BUNDLE / "invalidated_a0c_stale_four_scene_hash_binding_bundle.json").read_text()\n'
        ')\n',
        1,
        SEALED_TEST,
    )
    sealed_test = replace_exact(
        sealed_test,
        '("invalidation_zero_credit", INVALIDATION["status"] == '
        '"INVALIDATED_DO_NOT_RUN_OR_CREDIT" and '
        'INVALIDATION["accepted_rounds_credit"] == 0),',
        '("invalidation_zero_credit", INVALIDATION["status"] == '
        '"INVALIDATED_DO_NOT_RUN_OR_CREDIT" and '
        'INVALIDATION["accepted_rounds_credit"] == 0 and '
        'STALE_ENVELOPE_INVALIDATION["status"] == '
        '"INVALIDATED_DO_NOT_RUN_OR_CREDIT" and '
        'STALE_ENVELOPE_INVALIDATION["accepted_rounds_credit"] == 0 and '
        'INTERMEDIATE_INVALIDATION["status"] == '
        '"INVALIDATED_DO_NOT_RUN_OR_CREDIT" and '
        'INTERMEDIATE_INVALIDATION["accepted_rounds_credit"] == 0),',
        1,
        SEALED_TEST,
    )
    content[SEALED_TEST] = sealed_test

    verifier = content[READ_ONLY_VERIFIER]
    verifier = replace_exact(
        verifier,
        'INVALIDATION = BUNDLE / "invalidated_313_nonselfcontained_bundle.json"\n',
        'INVALIDATION = BUNDLE / "invalidated_313_nonselfcontained_bundle.json"\n'
        'STALE_ENVELOPE_INVALIDATION = (\n'
        '    BUNDLE / "invalidated_b1ff_stale_resource_envelope_bundle.json"\n'
        ')\n'
        'INTERMEDIATE_INVALIDATION = (\n'
        '    BUNDLE / "invalidated_a0c_stale_four_scene_hash_binding_bundle.json"\n'
        ')\n',
        1,
        READ_ONLY_VERIFIER,
    )
    verifier = replace_exact(
        verifier,
        "    invalidation = load(INVALIDATION)\n"
        "    runtime = load(RUNTIME)\n",
        "    invalidation = load(INVALIDATION)\n"
        "    stale_envelope_invalidation = load(STALE_ENVELOPE_INVALIDATION)\n"
        "    intermediate_invalidation = load(INTERMEDIATE_INVALIDATION)\n"
        "    runtime = load(RUNTIME)\n",
        1,
        READ_ONLY_VERIFIER,
    )
    verifier = replace_exact(
        verifier,
        '        or invalidation.get("bundle_id")\n'
        '        != "313637860bee5f333c11d8ae92689ef674585d8e7311c82b75c1cb7cc1fd9555"\n',
        '        or invalidation.get("bundle_id")\n'
        '        != "313637860bee5f333c11d8ae92689ef674585d8e7311c82b75c1cb7cc1fd9555"\n'
        '        or stale_envelope_invalidation.get("status")\n'
        '        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"\n'
        '        or stale_envelope_invalidation.get("bundle_id")\n'
        f'        != "{PARENT_ID}"\n'
        '        or stale_envelope_invalidation.get("accepted_rounds_credit") != 0\n'
        '        or intermediate_invalidation.get("status")\n'
        '        != "INVALIDATED_DO_NOT_RUN_OR_CREDIT"\n'
        '        or intermediate_invalidation.get("bundle_id")\n'
        f'        != "{INTERMEDIATE_ID}"\n'
        '        or intermediate_invalidation.get("accepted_rounds_credit") != 0\n',
        1,
        READ_ONLY_VERIFIER,
    )
    verifier = replace_exact(
        verifier,
        '    authority = AUTHORITY.read_text(encoding="utf-8")\n',
        '    envelope = runtime.get("resource_envelope") or {}\n'
        '    effective_blkio = runtime.get("effective_blkio_authority") or {}\n'
        '    if (\n'
        '        envelope.get("cpu_quota_cores") != 3.0\n'
        '        or envelope.get("io_read_bps") != 335544320\n'
        '        or envelope.get("io_write_bps") != 335544320\n'
        '        or effective_blkio.get("read_bps") != 335544320\n'
        '        or effective_blkio.get("write_bps") != 335544320\n'
        '    ):\n'
        '        raise VerifyError("v23 recalibrated resource envelope differs")\n'
        '    authority = AUTHORITY.read_text(encoding="utf-8")\n',
        1,
        READ_ONLY_VERIFIER,
    )
    content[READ_ONLY_VERIFIER] = verifier

    formal = load_object_payload(content[FORMAL_CONTRACT], FORMAL_CONTRACT)
    envelope = formal["database_resource_envelope"]
    envelope["cpu_quota_cores"] = CPU_QUOTA_CORES
    envelope["io_read_bps"] = IO_BPS
    envelope["io_write_bps"] = IO_BPS

    runtime = load_object_payload(content[RUNTIME_CONTRACT], RUNTIME_CONTRACT)
    runtime["resource_envelope"]["cpu_quota_cores"] = CPU_QUOTA_CORES
    runtime["resource_envelope"]["io_read_bps"] = IO_BPS
    runtime["resource_envelope"]["io_write_bps"] = IO_BPS
    runtime["effective_blkio_authority"]["read_bps"] = IO_BPS
    runtime["effective_blkio_authority"]["write_bps"] = IO_BPS
    runtime["database_counter_transport"]["docker_exec_user"] = "0"
    content[RUNTIME_CONTRACT] = json_payload(runtime)

    four_scene = load_object_payload(
        content[FOUR_SCENE_CONTRACT], FOUR_SCENE_CONTRACT
    )
    tpch_envelope = four_scene["scenarios"]["tpch"]["resource_envelope"]
    tpch_envelope["cpu_quota_cores"] = CPU_QUOTA_CORES
    tpch_envelope["io_read_bps"] = IO_BPS
    tpch_envelope["io_write_bps"] = IO_BPS
    content[FOUR_SCENE_CONTRACT] = json_payload(four_scene)
    four_scene_sha256 = sha_bytes(content[FOUR_SCENE_CONTRACT])
    old_four_scene_sha256 = (
        "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
    )
    content[AUTHORITY] = replace_exact(
        content[AUTHORITY],
        old_four_scene_sha256,
        four_scene_sha256,
        1,
        AUTHORITY,
    )
    content[READINESS] = replace_exact(
        content[READINESS],
        old_four_scene_sha256,
        four_scene_sha256,
        1,
        READINESS,
    )

    profile = load_object_payload(content[PROFILE], PROFILE)
    profile["cpu_cores"] = CPU_QUOTA_CORES
    profile["read_bps"] = IO_BPS
    profile["write_bps"] = IO_BPS
    content[PROFILE] = json_payload(profile)

    content[INVALIDATION] = json_payload(invalidation_evidence())
    content[INTERMEDIATE_INVALIDATION] = json_payload(
        intermediate_invalidation_evidence()
    )
    content[RECALIBRATION] = json_payload(recalibration_evidence())
    content[COLLECTOR_SCHEMA_INVALIDATION] = json_payload(
        {
            "schema_version":
                "mysql_tpch_v23_internal_schema_projection_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_RUN_OR_CREDIT",
            "bundle_id":
                "78e6498a82f88f54b962d2bdabb2fb1d68a18f40ad4e34a322c51375b986fd4c",
            "affected_attempts": [
                "tpch-v2-20260731t093427z-351f129723f14d30f09053f9",
            ],
            "reason": (
                "collector, qualification renderer, and readiness verifier "
                "retained the predecessor schema allowlist and rejected the "
                "bundle's own v23 schema after effective blkio passed"
            ),
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_rounds_credit": 0,
            "replay_or_rerun_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    content[CREDENTIAL_EXEC_INVALIDATION] = json_payload(
        {
            "schema_version":
                "mysql_tpch_v23_client_credentials_exec_user_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_RUN_OR_CREDIT",
            "bundle_id":
                "039622e6064f68f95e6a7559cab5d24c46f2d3bf99c2a44364a510823799f939",
            "affected_attempts": [
                "tpch-v2-20260731t095342z-f24742e50865a9a3796ab06c",
            ],
            "reason": (
                "the client container correctly runs as uid 1008 and the "
                "ephemeral credentials remain root:root 0600, but the sealed "
                "database-counter Docker exec omitted its explicit root user"
            ),
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_rounds_credit": 0,
            "replay_or_rerun_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    content[AUTHORITY_EMPTY_PROJECTION_INVALIDATION] = json_payload(
        {
            "schema_version":
                "mysql_tpch_v23_authority_empty_blkio_projection_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_RUN_OR_CREDIT",
            "bundle_id":
                "a66296bb73559df417216092c24bb4b6da9c2957a7313a8acd00b24088324ce3",
            "affected_attempts": [
                "tpch-v2-20260731t102441z-1ff0f5d5a5f37bdf6738ea48",
            ],
            "reason": (
                "the collector correctly accepted Docker's empty blkio "
                "declaration arrays after the sealed cgroup-v1 helper read "
                "back 335544320 B/s in both directions, but the bootstrap "
                "authority's later same-live envelope check still required "
                "nonempty Docker projection arrays"
            ),
            "observed_effective_cgroup_v1_read_bps": IO_BPS,
            "observed_effective_cgroup_v1_write_bps": IO_BPS,
            "r0_credit": 0,
            "candidate_credit": 0,
            "replay_credit": 0,
            "accepted_rounds_credit": 0,
            "replay_or_rerun_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )

    component_hashes = {
        name: sha_bytes(content[path])
        for name, path in sorted(COMPONENT_PATHS.items())
    }
    formal["qualification_hashes"] = {
        name: value
        for name, value in component_hashes.items()
        if name != "self_contained_read_only_verifier"
        and name != "systemd_oneshot_materializer"
    }
    content[FORMAL_CONTRACT] = json_payload(formal)
    return content


def chmod_sealed(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            os.chmod(path, 0o444)
        elif path.is_dir():
            os.chmod(path, 0o555)
    os.chmod(root, 0o555)


def verify_published(bundle: Path, manifest: dict[str, Any]) -> None:
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        bundle.name != manifest["bundle_id"]
        or relative_files(bundle) != manifest["files"]
        or sha_bytes(canonical(unsigned)) != manifest["bundle_id"]
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != sha256(bundle / "bundle_manifest.json") + "  bundle_manifest.json\n"
    ):
        raise BuildError("published TPC-H successor identity or closure differs")
    for path in [bundle, *bundle.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published TPC-H successor is mutable: " + str(path))


def publish(content: dict[str, bytes], manifest: dict[str, Any]) -> Path:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / manifest["bundle_id"]
    if destination.exists():
        verify_published(destination, manifest)
        return destination
    stage = Path(tempfile.mkdtemp(prefix=".tpch-v23-recalibrated.", dir=OUTPUT_ROOT))
    try:
        for relative, payload in sorted(content.items()):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(manifest_payload) + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        chmod_sealed(stage)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    verify_published(destination, manifest)
    return destination


def build() -> dict[str, Any]:
    parent = verify_parent()
    content = patched_payloads(parent)
    files = {
        relative: sha_bytes(payload)
        for relative, payload in sorted(content.items())
    }
    component_hashes = {
        name: files[path] for name, path in sorted(COMPONENT_PATHS.items())
    }
    identity = {
        key: value
        for key, value in parent.items()
        if key not in ("bundle_id", "files")
    }
    identity.update(
        {
            "schema_version": "mysql_tpch_v23_recalibrated_successor_v3",
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "builder_sha256": sha256(HERE),
            "component_hashes": component_hashes,
            "formal10_contract_sha256": files[FORMAL_CONTRACT],
            "distributed_runtime_contract_sha256": files[RUNTIME_CONTRACT],
            "four_scene_v2_contract_sha256": files[FOUR_SCENE_CONTRACT],
            "four_scene_contract_sha256": files[FOUR_SCENE_CONTRACT],
            "invalidated_b1ff_stale_resource_envelope_sha256":
                files[INVALIDATION],
            "invalidated_a0c_stale_four_scene_hash_binding_sha256":
                files[INTERMEDIATE_INVALIDATION],
            "v23_resource_recalibration_evidence_sha256":
                files[RECALIBRATION],
            "invalidated_039_client_credentials_exec_user_sha256":
                files[CREDENTIAL_EXEC_INVALIDATION],
            "invalidated_a662_authority_empty_blkio_projection_sha256":
                files[AUTHORITY_EMPTY_PROJECTION_INVALIDATION],
            "docker_empty_projection_policy": {
                **dict(identity.get("docker_empty_projection_policy") or {}),
                "requires_exact_read_bps": IO_BPS,
                "requires_exact_write_bps": IO_BPS,
            },
            "fresh_r0_envelope_preserved": {
                "cpu_quota_cores": CPU_QUOTA_CORES,
                "io_read_bps": IO_BPS,
                "io_write_bps": IO_BPS,
                "profile_intervals": {
                    "cpu": [0.30, 0.50],
                    "memory": [0.85, 0.95],
                    "io": [0.30, 0.50],
                },
            },
            "v23_failed_r0_credit": 0,
            "parent_bundle_credit": 0,
            "fresh_r0_required": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "offline_build_only": True,
            "remote_actions": 0,
            "files": files,
        }
    )
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    bundle = publish(content, manifest)
    return {
        "schema_version": "mysql_tpch_v23_recalibrated_successor_build_v3",
        "status": "PASS_IMMUTABLE_RECALIBRATED_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(bundle),
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "verified_files": len(files),
        "resource_envelope": {
            "cpu_quota_cores": CPU_QUOTA_CORES,
            "io_read_bps": IO_BPS,
            "io_write_bps": IO_BPS,
        },
        "projected_utilization": {
            "cpu": PROJECTED_CPU_UTILIZATION,
            "io": PROJECTED_IO_UTILIZATION,
        },
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def main() -> int:
    print(json.dumps(build(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
