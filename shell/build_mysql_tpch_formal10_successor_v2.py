#!/usr/bin/env python3
"""Build the immutable TPC-H distributed formal-10 successor v2.

The builder is strictly local/offline.  It performs no SSH, Docker, database,
or workload action.  Publication is frozen before visibility, atomic,
non-overwriting, and idempotent for identical content.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
AUTHORITY = ROOT / "shell/mysql_tpch_distributed_v2_authority.py"
BLKIO_HELPER = ROOT / "shell/mysql_tpch_blkio_v1.py"
QUALIFICATION_AUTHORITY_RENDERER = (
    ROOT / "shell/mysql_tpch_v2_qualification_authority_v1.py"
)
FRESH_R0_RUNNER = ROOT / "shell/mysql_tpch_v2_fresh_r0_runner_v1.py"
READINESS = ROOT / "shell/mysql_tpch_formal10_readiness_v2.py"
COLLECTOR = ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
METRIC_ADAPTER = ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py"
SQL_ADAPTER = ROOT / "shell/mysql_sql_debug_metric_adapter_v1.py"
RUNTIME_HELPER = ROOT / "shell/runtime_tree_content_identity_v3.py"
CLIENT_IDENTITY_HELPER = (
    ROOT / "shell/mysql_tpch_client_immutable_identity_v1.py"
)
PYTHON_RUNTIME_MANIFEST = (
    ROOT
    / "artifacts/mysql_tpch_v37_distributed_staged/"
    "9da749f28670c7ac95e9ba930c7ca8bb855930fd2622f4f1f9862bb1c60b1ffd/"
    "runtime/python_runtime_tree_manifest_v3.json"
)
PYTHON_RUNTIME_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
PYTHON_RUNTIME_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_RUNTIME_TARGET = (
    "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
    "runtime_algorithm_c_successor_20260729/runtime/python311-"
    + PYTHON_RUNTIME_TREE_CONTENT_SHA256
)
INVALIDATED_BOOTSTRAP_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "948cf38ff8d5c6757143c543fbec6002f1ea638a43d9f935286a256f29620d6c"
)
INVALIDATED_CLOSURE_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "764a5b03627b7a776c59114cc17687ef5bf01cf937c2ca1e971cd9b70a3cb40a"
)
INVALIDATED_LSOF_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "795ee2e905dc680fa593cc56892b42a3d3cb43928d09a5aae7c04a597d3875dc"
)
INVALIDATED_FALLBACK_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "ff1769e482e9e384c038271326e802d515b7aa8408ae895b77cc02b4cbfa04c5"
)
INVALIDATED_NONROOT_FALLBACK_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "2b777b5b66aa03a7fb190a4f1d654ae89f1f277bf16a4cc759acc32d955cdbe1"
)
INVALIDATED_SOCKET_IDENTITY_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "cee512b778291f8cdb335a4dc07bba56646a45d1f5e4b91a82c8e03c57442e0c"
)
INVALIDATED_PERMISSION_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "716544c9dba781b0cea9548225aa61daf4ae04ce141300f76df762b779c061ac"
)
INVALIDATED_PERMISSION_READBACK_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "c659ae9f5d3743c341095c976b03ea6c6b7469c213ede618f7eaf7fa7f597d46"
)
INVALIDATED_TREE_DIGEST_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "6417f98e939aa67ed8042559e716916abb1b798034e4116294190d5e41b12fc4"
)
INVALIDATED_TREE_DIGEST_BYTES_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "703b1382f0833463c42f02566f75bac490034cf6ed915c21bd1b87efaee23728"
)
INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "9372c125b38343841032592a9b8ce7d0fc8cc7096ced2149ff24de2e1cb98550"
)
INVALIDATED_COLLECTOR_SCHEMA_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "b4c2ab8701855eaa87c4ae59c5c204c2a1a1330df29bd683588ad2de3b080390"
)
INVALIDATED_COLLECTOR_SCHEMA_EVIDENCE = (
    ROOT
    / "preflight/mysql_tpch_v2_b4c2_collector_schema_invalidation_20260731.json"
)
INVALIDATED_EFFECTIVE_BLKIO_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "d8ea437cfdc33b197113c5f15ebedb8173032a08b0cd18d19395513787dd4c4b"
)
INVALIDATED_EFFECTIVE_BLKIO_EVIDENCE = (
    ROOT
    / "preflight/mysql_tpch_v2_d8ea_effective_blkio_invalidation_20260731.json"
)
INVALIDATED_EFFECTIVE_BLKIO_MANIFEST_SHA256 = (
    "93e133c7c777119880cbc597b15cd87998dd23cc2bb56d8be07cce20a2383a7e"
)
INVALIDATED_CLIENT_VOLUME_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "04326c3bca149835bf00f9a4f34e31eac225b4c237421bdda4034987fe8119f0"
)
INVALIDATED_CLIENT_VOLUME_EVIDENCE = (
    ROOT
    / "preflight/mysql_tpch_v2_0432_client_anonymous_volume_invalidation_20260731.json"
)
INVALIDATED_CLIENT_VOLUME_MANIFEST_SHA256 = (
    "deee1481969a6ad303909ee432dff0e78350c0365cb378abedc895a295df44ad"
)
INVALIDATED_QUALIFICATION_CWD_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "de80c37c45106b5aedbcddc7d53de252e5292358002b6d31222e911e74908862"
)
INVALIDATED_QUALIFICATION_CWD_EVIDENCE = (
    ROOT
    / "preflight/mysql_tpch_v2_de80_qualification_cwd_invalidation_20260731.json"
)
INVALIDATED_QUALIFICATION_CWD_MANIFEST_SHA256 = (
    "754ceab637bd8b1500d02f410d737989ba11e7713f1f664eaa20a6437db1df82"
)
INVALIDATED_CLIENT_IDENTITY_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "7d73a1b8a9b74f1418313117b79c4d6eedfb1e7db9a4eb22876cb16a36c8b1ac"
)
INVALIDATED_CLIENT_IDENTITY_EVIDENCE = (
    ROOT
    / "preflight/"
    "mysql_tpch_v2_7d73_client_identity_projection_invalidation_20260731.json"
)
INVALIDATED_CLIENT_IDENTITY_MANIFEST_SHA256 = (
    "8da3cf1513b5edd8f0ac4af79f9b20a22cda8e848bf8868c866c4660eb88f3e4"
)
INVALIDATED_IMPORT_BYTECODE_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "0ddb9a19be2e408fb215a69fa64bfb3d6864ed16de447f22d024eccc4f8a0793"
)
INVALIDATED_IMPORT_BYTECODE_EVIDENCE = (
    ROOT
    / "preflight/mysql_tpch_v2_0ddb_import_bytecode_invalidation_20260731.json"
)
INVALIDATED_IMPORT_BYTECODE_MANIFEST_SHA256 = (
    "fc55d1c5c3878d082dcdbf86f8fc8de6efad03af7cab3de8737d75468cd27c37"
)
INVALIDATED_CLIENT_PERMISSION_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "87fc669e87ae4db5368e129e7cebe2b9d4fccefcbf189dae3387163bdbce84be"
)
INVALIDATED_CLIENT_PERMISSION_EVIDENCE = (
    ROOT / "preflight/mysql_tpch_v2_87fc_client_permission_invalidation_20260731.json"
)
INVALIDATED_CLIENT_PERMISSION_MANIFEST_SHA256 = (
    "0060d4cc45f8630e1bc80653adbb434de68907a0c9f3d516e4ee3baeb514335c"
)
INVALIDATED_SHARED_CONTRACT_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "2d4b793d1c68fe1634495629c5207356d96193bdc2fbba3dabe3a201dc4e4aa6"
)
INVALIDATED_SHARED_CONTRACT_EVIDENCE = (
    ROOT / "preflight/mysql_tpch_v2_2d4b_shared_contract_mutation_invalidation_20260731.json"
)
INVALIDATED_SHARED_CONTRACT_MANIFEST_SHA256 = (
    "31d6ddf7e64a8ff0d456cc98dfe9f151a3bdd0580db9b8d320f37a508faacf39"
)
INVALIDATED_HOST_TMPFS_BUNDLE = (
    ROOT
    / "artifacts/mysql_tpch_formal10_successor_v2/"
    "2943d88c35c3ceb9e110bc5df7ae3829851e24e75628e2be159ab07debf8bc59"
)
INVALIDATED_HOST_TMPFS_EVIDENCE = (
    ROOT / "preflight/mysql_tpch_v2_2943_host_tmpfs_invalidation_20260731.json"
)
INVALIDATED_HOST_TMPFS_MANIFEST_SHA256 = (
    "5c40fa6a773475d512d9dd8b83e32b4e637a6c4b58ac7154d5b496f37a63b51e"
)
CLIENT_MYSQL_DATADIR_MASK_RELATIVE = (
    "qualification/client_volume_masks/mysql_datadir"
)
CONTRACT = (
    ROOT
    / "preflight/"
    "mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
)
CONTRACT_SHA256 = (
    "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
)
TPCH_CLIENT_IDENTITY_AMENDMENT = (
    ROOT / "preflight/mysql_tpch_formal10_client_identity_amendment_v3_20260731.json"
)
TPCH_CLIENT_IDENTITY_AMENDMENT_SHA256 = (
    "ea0f3a13d44f1f143e63fcd19003f23e90486cfa297df4b07fea99314af82d7b"
)
PREDECESSOR = (
    ROOT
    / "artifacts/mysql_tpch_v36_debug/"
    "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4"
)
ALGORITHM_SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/"
    "readiness_mysql_tpch_sampling_v2_successor/"
    "source-127bd0e521d494c3ef9520b89b8c9e1af53a9aafb4d797af61b7f83b71d4329c"
)
SQL_REGISTRY = (
    ROOT
    / "history/mysql_gradient_debug/tpch_binding_registry_q1_q22_v10/"
    "tpch-registry-v10-20260728T112123Z"
)
V40_POINT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_local_r0_v40_cpu750_io832_parallel8_terminal_propagation/"
    "production_points/alg_c_tpch_r0_20260730T210012_414be152"
)
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_formal10_successor_v2"
PARAMETERS = [
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "disk_temptable_row_cost",
]
GRADIENT_FIELDS = [
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
]


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"{label} must be a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BuildError(f"{label} must be a JSON object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"symlink prohibited in bundle: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def copy_file(source: Path, destination: Path, mode: int | None = None) -> str:
    if source.is_symlink() or not source.is_file():
        raise BuildError(f"regular source file required: {source}")
    before = sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    after = sha256(source)
    copied = sha256(destination)
    if before != after or after != copied:
        raise BuildError(f"source changed while sealing: {source}")
    os.chmod(
        destination,
        mode if mode is not None else (stat.S_IMODE(source.stat().st_mode) | 0o600),
    )
    return copied


def tpch_collector_payload() -> bytes:
    """Bind the shared collector to the exact known TPC-H successor schema."""
    before = sha256(COLLECTOR)
    payload = COLLECTOR.read_bytes()
    after = sha256(COLLECTOR)
    anchor = b'        "mysql_xp_distributed_formal10_launch_v3",\n'
    binding = b'        "mysql_tpch_distributed_formal10_successor_v2",\n'
    if before != after or payload.count(anchor) != 1 or payload.count(binding) > 1:
        raise BuildError("shared collector exact-schema patch point differs")
    # Other engines may add their own accepted schemas after the XP entry.  Do
    # not make the TPC-H builder depend on XP being the final tuple member: bind
    # the TPC-H schema at the unique frozen anchor, and accept an already-bound
    # shared collector without duplicating the entry.
    if binding not in payload:
        payload = payload.replace(anchor, anchor + binding, 1)
    rate_marker = b"""    data_device = storage["device"]
    throttle_device = storage.get("io_throttle_parent_device", data_device)
    throttle_major_minor = storage.get(
        "io_throttle_parent_major_minor", storage.get("expected_live_major_minor")
    )
"""
    rate_replacement = rate_marker
    function_marker = b"\ndef validate_container_readback(container_row, runtime):\n"
    function_payload = b'''
def validate_tpch_effective_blkio_authority(
    container_row, runtime, defaults_file
):
    defaults_file = Path(defaults_file)
    if (
        defaults_file.name != "mysql.cnf"
        or defaults_file.parent.name != "credentials"
    ):
        raise QualificationError("TPC-H defaults/attempt path binding differs")
    authority_path = (
        defaults_file.parent.parent / "blkio_throttle_authority.json"
    )
    if not valid_sidecar(authority_path):
        raise QualificationError("effective blkio authority sidecar differs")
    value = load_object(authority_path, "effective blkio authority")
    unsigned = dict(value)
    recorded_sha = unsigned.pop("authority_sha256", None)
    container_id = container_row.get("Id")
    pid = int((container_row.get("State") or {}).get("Pid") or 0)
    expected_cgroup = "/sys/fs/cgroup/blkio/docker/" + str(container_id)
    envelope = runtime["resource_envelope"]
    storage = runtime["storage"]
    if (
        recorded_sha != sha_bytes(canonical(unsigned))
        or value.get("schema_version")
        != "mysql_tpch_blkio_v1_throttle_authority_v1"
        or value.get("status") != "PASS_EFFECTIVE_CGROUP_V1_THROTTLE"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("database_container_id") != container_id
        or value.get("database_pid") != pid
        or value.get("host_effective_uid_gid") != [0, 0]
        or value.get("cgroup_version") != "v1"
        or value.get("cgroup_path") != expected_cgroup
        or value.get("device_major_minor")
        != storage["expected_live_major_minor"]
        or value.get("read_bps") != envelope["io_read_bps"]
        or value.get("write_bps") != envelope["io_write_bps"]
        or value.get("read_throttle_file")
        != expected_cgroup + "/blkio.throttle.read_bps_device"
        or value.get("write_throttle_file")
        != expected_cgroup + "/blkio.throttle.write_bps_device"
        or value.get(
            "docker_hostconfig_is_declaration_not_effective_authority"
        )
        is not True
        or value.get("database_or_client_started") is not True
        or value.get("workload_started") is not False
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise QualificationError("effective cgroup-v1 blkio authority differs")
    return {
        "schema_version": value["schema_version"],
        "authority_sha256": recorded_sha,
        "cgroup_path": expected_cgroup,
        "device_major_minor": value["device_major_minor"],
        "read_bps": value["read_bps"],
        "write_bps": value["write_bps"],
        "docker_hostconfig_is_declaration_not_effective_authority": True,
    }

'''
    call_marker = (
        b"    container_readback = validate_container_readback(database_row, runtime)\n"
    )
    call_replacement = call_marker + (
        b"    container_readback[\"effective_blkio_authority\"] = (\n"
        b"        validate_tpch_effective_blkio_authority(\n"
        b"            database_row, runtime, defaults_file\n"
        b"        )\n"
        b"    )\n"
    )
    if (
        payload.count(rate_marker) != 1
        or payload.count(function_marker) != 1
        or payload.count(call_marker) != 1
    ):
        raise BuildError("shared collector blkio patch point differs")
    payload = payload.replace(rate_marker, rate_replacement, 1)
    payload = payload.replace(
        function_marker, b"\n" + function_payload + function_marker.lstrip(), 1
    )
    payload = payload.replace(call_marker, call_replacement, 1)
    volume_marker = (
        b"    observed_mounts = {}\n"
        b"    for mount in container_row.get(\"Mounts\") or []:\n"
    )
    volume_replacement = (
        b"    raw_client_mounts = container_row.get(\"Mounts\") or []\n"
        b"    if any(mount.get(\"Type\") == \"volume\" "
        b"for mount in raw_client_mounts):\n"
        b"        raise QualificationError(\n"
        b"            \"client anonymous or named volume is prohibited\"\n"
        b"        )\n"
        b"    observed_mounts = {}\n"
        b"    for mount in raw_client_mounts:\n"
        b"        if (\n"
        b"            mount.get(\"Type\") in (\"bind\", \"volume\")\n"
        b"            and mount.get(\"RW\") is not False\n"
        b"            and mount.get(\"Destination\") == \"/measurement\"\n"
        b"            and expected.get(\"measurement_output_medium\")\n"
        b"            == \"host_tmpfs_bind\"\n"
        b"        ):\n"
        b"            continue\n"
    )
    required_marker = b'''            {
                "source": str(defaults_file.parent),
                "destination": str(defaults_file.parent),
            },
        ]
'''
    required_replacement = b'''            {
                "source": str(defaults_file.parent),
                "destination": str(defaults_file.parent),
            },
            {
                "source": str(
                    bundle
                    / "qualification/client_volume_masks/mysql_datadir"
                ),
                "destination": "/var/lib/mysql",
            },
        ]
'''
    if (
        payload.count(volume_marker) != 1
        or payload.count(required_marker) != 1
    ):
        raise BuildError("shared collector client-volume patch point differs")
    payload = payload.replace(volume_marker, volume_replacement, 1)
    payload = payload.replace(required_marker, required_replacement, 1)
    hardening_marker = b'''        or _tmpfs_size_bytes(host_config, tmpfs_destination)
        != int(expected["tmpfs_bytes"])
'''
    hardening_replacement = b'''        or (
            expected.get("measurement_output_medium") != "host_tmpfs_bind"
            and _tmpfs_size_bytes(host_config, tmpfs_destination)
            != int(expected["tmpfs_bytes"])
        )
'''
    secure_helper_marker = b"\ndef validate_client_container_readback(\n"
    secure_helper_payload = b'''
def validate_tpch_secure_measurement_bind(container_row, expected, destination):
    if expected.get("measurement_output_medium") != "host_tmpfs_bind":
        return
    config = container_row.get("Config") or {}
    host = container_row.get("HostConfig") or {}
    if (
        config.get("User") != expected.get("container_user")
        or host.get("Tmpfs") not in ({}, None)
    ):
        raise QualificationError("TPC-H client user/tmpfs contract differs")
    mounts = [
        row for row in (container_row.get("Mounts") or [])
        if row.get("Destination") == destination
    ]
    if len(mounts) != 1:
        raise QualificationError("TPC-H measurement bind cardinality differs")
    mount = mounts[0]
    source = mount.get("Source")
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or not isinstance(source, str)
        or not Path(source).is_absolute()
    ):
        raise QualificationError("TPC-H measurement bind differs")
    source_path = Path(source)
    try:
        state = source_path.stat()
    except OSError as exc:
        raise QualificationError("TPC-H measurement bind source is unreadable") from exc
    if (
        state.st_uid != int(expected["measurement_directory_uid"])
        or state.st_gid != int(expected["measurement_directory_gid"])
        or stat.S_IMODE(state.st_mode)
        != int(str(expected["measurement_directory_mode"]), 8)
    ):
        raise QualificationError("TPC-H measurement bind ownership differs")
    raw = run(
        ["findmnt", "-n", "-o", "FSTYPE,OPTIONS", "--target", str(source_path)],
        timeout=5,
        label="TPC-H measurement bind tmpfs readback",
    ).strip().split(None, 1)
    if len(raw) != 2 or raw[0] != "tmpfs":
        raise QualificationError("TPC-H measurement bind is not tmpfs")
    sizes = [
        _parse_size_option(value.split("=", 1)[1])
        for value in raw[1].split(",")
        if value.startswith("size=")
    ]
    if len(sizes) != 1 or sizes[0] < int(expected["tmpfs_bytes"]):
        raise QualificationError("TPC-H measurement bind capacity differs")

'''
    bind_call_marker = b"    observed_mounts = {}\n"
    bind_call_replacement = (
        b"    validate_tpch_secure_measurement_bind(\n"
        b"        container_row, expected, tmpfs_destination\n"
        b"    )\n"
        + bind_call_marker
    )
    command_client_marker = b'''            "inspect_config_sha256",
            "required_read_only_mounts",
        }
'''
    command_client_replacement = b'''            "inspect_config_sha256",
            "execution_user",
            "required_read_only_mounts",
        }
'''
    command_measurement_marker = b'''            "host_tmpfs_directory",
            "container_tmpfs_destination",
        }
'''
    command_measurement_replacement = b'''            "host_tmpfs_directory",
            "host_bind_source",
            "container_tmpfs_destination",
        }
'''
    command_user_marker = b'''    mounts = client_container.get("required_read_only_mounts")
'''
    command_user_replacement = b'''    if (
        expected_client_envelope.get("measurement_output_medium")
        != "host_tmpfs_bind"
        or client_container.get("execution_user")
        != expected_client_envelope.get("container_user")
    ):
        raise QualificationError("TPC-H client execution user differs")
    mounts = client_container.get("required_read_only_mounts")
'''
    command_bind_marker = b'''    metric_path = Path(str(metric_stream.get("path") or ""))
'''
    command_bind_replacement = b'''    host_bind_source = Path(measurement_output["host_bind_source"])
    if (
        host_bind_source.parent != Path(measurement_output["host_tmpfs_directory"])
        or host_bind_source.name != "client_measurement"
    ):
        raise QualificationError("TPC-H command measurement bind differs")
    metric_path = Path(str(metric_stream.get("path") or ""))
'''
    if (
        payload.count(hardening_marker) != 1
        or payload.count(secure_helper_marker) != 1
        or payload.count(bind_call_marker) != 1
        or payload.count(command_client_marker) != 1
        or payload.count(command_measurement_marker) != 1
        or payload.count(command_user_marker) != 1
        or payload.count(command_bind_marker) != 1
    ):
        raise BuildError("shared collector secure-measurement patch point differs")
    payload = payload.replace(hardening_marker, hardening_replacement, 1)
    payload = payload.replace(
        secure_helper_marker,
        b"\n" + secure_helper_payload + secure_helper_marker.lstrip(),
        1,
    )
    payload = payload.replace(bind_call_marker, bind_call_replacement, 1)
    payload = payload.replace(
        command_client_marker, command_client_replacement, 1
    )
    payload = payload.replace(
        command_measurement_marker, command_measurement_replacement, 1
    )
    payload = payload.replace(command_user_marker, command_user_replacement, 1)
    return payload.replace(command_bind_marker, command_bind_replacement, 1)


def runtime_contract(inputs: dict[str, Any]) -> dict[str, Any]:
    tpch = inputs["tpch"]
    return {
        "schema_version": "mysql_algorithm_c_distributed_runtime_contract_v2",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "tpch_memory",
        "host": tpch["host"],
        "storage": tpch["storage"],
        "resource_envelope": tpch["resource_envelope"],
        "client_resource_envelope": tpch["client_resource_envelope"],
        "host_measurement_authority": {
            "implementation":
                "host_root_attempt_private_exact_tmpfs_mount_v1",
            "capacity_bytes": 2147483648,
            "mount_options_required": [
                "rw", "nosuid", "nodev", "noexec"
            ],
            "uid": 1008,
            "gid": 1008,
            "mode": "0700",
            "client_subdirectory": "client_measurement",
            "failure_cleanup_and_readback_evidence_required": True,
        },
        "toolchain": {
            "java_required": False,
            "python_required": True,
            "python_runtime_root": PYTHON_RUNTIME_TARGET,
            "python_runtime": PYTHON_RUNTIME_TARGET + "/bin/python3.11",
            "python_binary_relative": "bin/python3.11",
            "python_binary_sha256":
                "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39",
            "python_tree_manifest":
                "runtime/python_runtime_tree_manifest_v3.json",
            "python_tree_content_sha256":
                PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "python_tree_content_identity_algorithm":
                "sha256_canonical_relative_tree_entries_v3",
            "python_version_expected": "Python 3.11.11",
        },
        "database_counter_transport": {
            "schema_version":
                "mysql_algorithm_c_database_counter_transport_v1",
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "mysql_binary_in_client": "/usr/bin/mysql",
            "stdout_only": True,
            "host_mysql_cli_required": False,
            "docker_socket_mount_prohibited": True,
            "target_datadir_mount_prohibited": True,
        },
        "effective_blkio_authority": {
            "implementation":
                "sealed_host_root_cgroup_v1_start_enforce_readback_adopt_v1",
            "device_major_minor": "259:1",
            "read_bps": 100663296,
            "write_bps": 100663296,
            "docker_hostconfig_role": "declaration_only",
            "host_effective_uid_gid_required": [0, 0],
            "same_live_container_id_pid_required": True,
            "phase_order": [
                "start_database_and_client",
                "seal_start_authority",
                "enforce_and_read_back_effective_cgroup_v1_blkio_as_root",
                "adopt_same_live_database_and_client",
                "collect_only",
            ],
        },
        "client_mysql_datadir_volume_mask": {
            "source_relative": CLIENT_MYSQL_DATADIR_MASK_RELATIVE,
            "destination": "/var/lib/mysql",
            "mount_type": "bind",
            "read_only_required": True,
            "manifest_covered_marker_required": True,
            "anonymous_or_named_volume_prohibited": True,
            "mutable_application_mount_prohibited": True,
            "docker_managed_resolver_metadata_only_exception": True,
        },
        "qualification_authority_renderer": {
            "path":
                "qualification/mysql_tpch_v2_qualification_authority_v1.py",
            "short_token_private_mode": "0600",
            "authority_validity_seconds_min": 240,
            "authority_validity_seconds_max": 3600,
            "json_and_sha256_sidecars_required": True,
            "launches_data_plane": False,
        },
        "fresh_r0_runner": {
            "path": "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py",
            "real_workload_required": True,
            "duration_seconds": 180,
            "sample_count": 181,
            "resource_profile": {
                "cpu": [0.30, 0.50],
                "memory": [0.85, 0.95],
                "io": [0.30, 0.50],
            },
            "database_counter_missing_or_timeout_result": "FAIL",
            "same_live_identity_required": True,
            "dynamic_gate_file_sha256_required": True,
            "l1_l2_l3_required": True,
            "accepted_rounds_credit": 0,
        },
        "data_plane_launch_authority": "main_agent_only",
        "collector_and_qualification_entrypoint":
            "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "workload_metric_adapter":
            "qualification/mysql_sql_debug_metric_adapter_v1.py",
        "qualification_policy": {
            "duration_seconds": 180,
            "sample_period_seconds": 1,
            "minimum_samples_per_stream": 181,
            "database_counter_missing_or_timeout_result": "FAIL",
            "capture_r0_formal_accounting_prohibited": True,
        },
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def copy_manifest_closure(
    source: Path, manifest: dict[str, Any], destination: Path
) -> None:
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise BuildError("source manifest file closure is absent")
    for relative, expected in sorted(files.items()):
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or ".." in PurePosixPath(relative).parts
            or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
        ):
            raise BuildError("source manifest path/hash differs")
        source_path = source / relative
        if sha256(source_path) != expected:
            raise BuildError(f"source manifest hash differs: {relative}")
        copy_file(source_path, destination / relative)


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError(f"symlink prohibited in bundle: {path}")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def thaw_directories(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o700)


def fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for path in sorted(
        [entry for entry in root.rglob("*") if entry.is_dir()],
        key=lambda entry: len(entry.parts),
        reverse=True,
    ):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_inputs() -> dict[str, Any]:
    if sha256(CONTRACT) != CONTRACT_SHA256:
        raise BuildError("distributed-v2 contract identity differs")
    amendment = load_object(
        TPCH_CLIENT_IDENTITY_AMENDMENT, "TPC-H client identity amendment"
    )
    runtime_manifest = load_object(
        PYTHON_RUNTIME_MANIFEST, "Python runtime portable manifest"
    )
    invalidated_manifest = load_object(
        INVALIDATED_BOOTSTRAP_BUNDLE / "bundle_manifest.json",
        "incompatible TPC-H bootstrap bundle",
    )
    invalidated_closure_manifest = load_object(
        INVALIDATED_CLOSURE_BUNDLE / "bundle_manifest.json",
        "incompatible TPC-H closure bundle",
    )
    invalidated_lsof_manifest = load_object(
        INVALIDATED_LSOF_BUNDLE / "bundle_manifest.json",
        "lsof-dependent TPC-H bundle",
    )
    invalidated_fallback_manifest = load_object(
        INVALIDATED_FALLBACK_BUNDLE / "bundle_manifest.json",
        "root-squash-incompatible TPC-H fallback bundle",
    )
    invalidated_nonroot_fallback_manifest = load_object(
        INVALIDATED_NONROOT_FALLBACK_BUNDLE / "bundle_manifest.json",
        "non-root host-PID scanner bundle",
    )
    invalidated_socket_identity_manifest = load_object(
        INVALIDATED_SOCKET_IDENTITY_BUNDLE / "bundle_manifest.json",
        "logical-socket identity TPC-H bundle",
    )
    invalidated_permission_manifest = load_object(
        INVALIDATED_PERMISSION_BUNDLE / "bundle_manifest.json",
        "datadir-permission TPC-H bundle",
    )
    invalidated_permission_readback_manifest = load_object(
        INVALIDATED_PERMISSION_READBACK_BUNDLE / "bundle_manifest.json",
        "datadir-permission readback TPC-H bundle",
    )
    invalidated_tree_digest_manifest = load_object(
        INVALIDATED_TREE_DIGEST_BUNDLE / "bundle_manifest.json",
        "unprivileged tree-digest TPC-H bundle",
    )
    invalidated_tree_digest_bytes_manifest = load_object(
        INVALIDATED_TREE_DIGEST_BYTES_BUNDLE / "bundle_manifest.json",
        "stale tree-digest byte-count TPC-H bundle",
    )
    invalidated_metadata_sensitive_tree_identity_manifest = load_object(
        INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE
        / "bundle_manifest.json",
        "metadata-sensitive tree-identity TPC-H bundle",
    )
    invalidated_collector_schema_manifest = load_object(
        INVALIDATED_COLLECTOR_SCHEMA_BUNDLE / "bundle_manifest.json",
        "collector-schema TPC-H bundle",
    )
    invalidated_collector_schema_evidence = load_object(
        INVALIDATED_COLLECTOR_SCHEMA_EVIDENCE,
        "collector-schema TPC-H invalidation",
    )
    invalidated_effective_blkio_manifest = load_object(
        INVALIDATED_EFFECTIVE_BLKIO_BUNDLE / "bundle_manifest.json",
        "missing-effective-blkio TPC-H bundle",
    )
    invalidated_effective_blkio_evidence = load_object(
        INVALIDATED_EFFECTIVE_BLKIO_EVIDENCE,
        "missing-effective-blkio TPC-H invalidation",
    )
    invalidated_client_volume_manifest = load_object(
        INVALIDATED_CLIENT_VOLUME_BUNDLE / "bundle_manifest.json",
        "anonymous-client-volume TPC-H bundle",
    )
    invalidated_client_volume_evidence = load_object(
        INVALIDATED_CLIENT_VOLUME_EVIDENCE,
        "anonymous-client-volume TPC-H invalidation",
    )
    invalidated_qualification_cwd_manifest = load_object(
        INVALIDATED_QUALIFICATION_CWD_BUNDLE / "bundle_manifest.json",
        "qualification-cwd TPC-H bundle",
    )
    invalidated_qualification_cwd_evidence = load_object(
        INVALIDATED_QUALIFICATION_CWD_EVIDENCE,
        "qualification-cwd TPC-H invalidation",
    )
    invalidated_client_identity_manifest = load_object(
        INVALIDATED_CLIENT_IDENTITY_BUNDLE / "bundle_manifest.json",
        "client-identity-projection TPC-H bundle",
    )
    invalidated_client_identity_evidence = load_object(
        INVALIDATED_CLIENT_IDENTITY_EVIDENCE,
        "client-identity-projection TPC-H invalidation",
    )
    invalidated_import_bytecode_manifest = load_object(
        INVALIDATED_IMPORT_BYTECODE_BUNDLE / "bundle_manifest.json",
        "import-bytecode TPC-H bundle",
    )
    invalidated_import_bytecode_evidence = load_object(
        INVALIDATED_IMPORT_BYTECODE_EVIDENCE,
        "import-bytecode TPC-H invalidation",
    )
    invalidated_client_permission_manifest = load_object(
        INVALIDATED_CLIENT_PERMISSION_BUNDLE / "bundle_manifest.json",
        "client-permission TPC-H bundle",
    )
    invalidated_client_permission_evidence = load_object(
        INVALIDATED_CLIENT_PERMISSION_EVIDENCE,
        "client-permission TPC-H invalidation",
    )
    invalidated_shared_contract_manifest = load_object(
        INVALIDATED_SHARED_CONTRACT_BUNDLE / "bundle_manifest.json",
        "shared-contract-mutated TPC-H bundle",
    )
    invalidated_shared_contract_evidence = load_object(
        INVALIDATED_SHARED_CONTRACT_EVIDENCE,
        "shared-contract-mutated TPC-H invalidation",
    )
    invalidated_host_tmpfs_manifest = load_object(
        INVALIDATED_HOST_TMPFS_BUNDLE / "bundle_manifest.json",
        "shared-dev-shm TPC-H bundle",
    )
    invalidated_host_tmpfs_evidence = load_object(
        INVALIDATED_HOST_TMPFS_EVIDENCE,
        "shared-dev-shm TPC-H invalidation",
    )
    if (
        sha256(PYTHON_RUNTIME_MANIFEST)
        != PYTHON_RUNTIME_MANIFEST_SHA256
        or runtime_manifest.get("schema_version")
        != "mysql_algorithm_c_runtime_tree_manifest_v3"
        or runtime_manifest.get("tree_content_sha256")
        != PYTHON_RUNTIME_TREE_CONTENT_SHA256
        or invalidated_manifest.get("bundle_id")
        != INVALIDATED_BOOTSTRAP_BUNDLE.name
        or sha256(
            INVALIDATED_BOOTSTRAP_BUNDLE / "bundle_manifest.json"
        )
        != "f80cc0fa1ef6e15e7dbc59140d45753cea1aa69b42b6015236d4a078fae4896e"
        or invalidated_closure_manifest.get("bundle_id")
        != INVALIDATED_CLOSURE_BUNDLE.name
        or sha256(
            INVALIDATED_CLOSURE_BUNDLE / "bundle_manifest.json"
        )
        != "c02c17f8a82732df2a13db824a8bb066b6ff04f0f3ebdd8bbf02e1e0f98dd471"
        or invalidated_lsof_manifest.get("bundle_id")
        != INVALIDATED_LSOF_BUNDLE.name
        or sha256(
            INVALIDATED_LSOF_BUNDLE / "bundle_manifest.json"
        )
        != "c8ca4ee51d1596c627173321ba8a332f41fd5438c7f9b45a281b64cc52774373"
        or invalidated_fallback_manifest.get("bundle_id")
        != INVALIDATED_FALLBACK_BUNDLE.name
        or sha256(
            INVALIDATED_FALLBACK_BUNDLE / "bundle_manifest.json"
        )
        != "85d1f88d042f43b1ff7a74541b97e6d2237bb5c51bd6168cb4329b6e652f393d"
        or invalidated_nonroot_fallback_manifest.get("bundle_id")
        != INVALIDATED_NONROOT_FALLBACK_BUNDLE.name
        or sha256(
            INVALIDATED_NONROOT_FALLBACK_BUNDLE / "bundle_manifest.json"
        )
        != "d186cd70a95f21b67c2680ab3c19a0f3268280bdb05830d49fe47d734ba2ba58"
        or invalidated_socket_identity_manifest.get("bundle_id")
        != INVALIDATED_SOCKET_IDENTITY_BUNDLE.name
        or sha256(
            INVALIDATED_SOCKET_IDENTITY_BUNDLE / "bundle_manifest.json"
        )
        != "a2bd5505af32cc7bcbef76d00ba43c19aceeaa4131c727128808c58263b8d3d2"
        or invalidated_permission_manifest.get("bundle_id")
        != INVALIDATED_PERMISSION_BUNDLE.name
        or sha256(INVALIDATED_PERMISSION_BUNDLE / "bundle_manifest.json")
        != "afb10db2f96890a7154ecbc5693d0695731ff93572f41bacb846212f9b9c23e4"
        or invalidated_permission_readback_manifest.get("bundle_id")
        != INVALIDATED_PERMISSION_READBACK_BUNDLE.name
        or sha256(
            INVALIDATED_PERMISSION_READBACK_BUNDLE / "bundle_manifest.json"
        )
        != "e388f018b43811425f30a46d5b0a4796251c0ac105f0e08f0fb1c19e4568e82f"
        or invalidated_tree_digest_manifest.get("bundle_id")
        != INVALIDATED_TREE_DIGEST_BUNDLE.name
        or sha256(
            INVALIDATED_TREE_DIGEST_BUNDLE / "bundle_manifest.json"
        )
        != "b545b4e7ae47987a54082bf1a2e5b8ccbec6320f3bd6733f9bcd5d9edb11bb56"
        or invalidated_tree_digest_bytes_manifest.get("bundle_id")
        != INVALIDATED_TREE_DIGEST_BYTES_BUNDLE.name
        or sha256(
            INVALIDATED_TREE_DIGEST_BYTES_BUNDLE / "bundle_manifest.json"
        )
        != "9fd391a829dbb9067685bf390a2373fc283c782b289463f7654bee6980f744e4"
        or invalidated_metadata_sensitive_tree_identity_manifest.get(
            "bundle_id"
        )
        != INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE.name
        or sha256(
            INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE
            / "bundle_manifest.json"
        )
        != "6ef0fb1dbf7d36b9b63ce6210534cd0c748976fb6d6297a660486a04916d8a94"
        or invalidated_collector_schema_manifest.get("bundle_id")
        != INVALIDATED_COLLECTOR_SCHEMA_BUNDLE.name
        or sha256(
            INVALIDATED_COLLECTOR_SCHEMA_BUNDLE / "bundle_manifest.json"
        )
        != "fe4329f2bc5937cdc1c4eedfbfd98917daf212602d35291d9c1bcd770c01afef"
        or invalidated_collector_schema_evidence.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_collector_schema_evidence.get("bundle_id")
        != INVALIDATED_COLLECTOR_SCHEMA_BUNDLE.name
        or invalidated_collector_schema_evidence.get(
            "accepted_rounds_credit"
        )
        != 0
        or invalidated_effective_blkio_manifest.get("bundle_id")
        != INVALIDATED_EFFECTIVE_BLKIO_BUNDLE.name
        or sha256(
            INVALIDATED_EFFECTIVE_BLKIO_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_EFFECTIVE_BLKIO_MANIFEST_SHA256
        or invalidated_effective_blkio_evidence.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_effective_blkio_evidence.get("bundle_id")
        != INVALIDATED_EFFECTIVE_BLKIO_BUNDLE.name
        or invalidated_effective_blkio_evidence.get(
            "bundle_manifest_sha256"
        )
        != INVALIDATED_EFFECTIVE_BLKIO_MANIFEST_SHA256
        or invalidated_effective_blkio_evidence.get("failure_stage")
        != "collect_only_before_fresh_r0"
        or invalidated_effective_blkio_evidence.get(
            "accepted_rounds_credit"
        )
        != 0
        or invalidated_effective_blkio_evidence.get("reusable") is not False
        or invalidated_client_volume_manifest.get("bundle_id")
        != INVALIDATED_CLIENT_VOLUME_BUNDLE.name
        or sha256(
            INVALIDATED_CLIENT_VOLUME_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_CLIENT_VOLUME_MANIFEST_SHA256
        or invalidated_client_volume_evidence.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_client_volume_evidence.get("bundle_id")
        != INVALIDATED_CLIENT_VOLUME_BUNDLE.name
        or invalidated_client_volume_evidence.get("bundle_manifest_sha256")
        != INVALIDATED_CLIENT_VOLUME_MANIFEST_SHA256
        or invalidated_client_volume_evidence.get("database_pid") != 87352
        or invalidated_client_volume_evidence.get("client_pid") != 87558
        or invalidated_client_volume_evidence.get("reason")
        != "mysql_image_implicit_var_lib_mysql_anonymous_writable_volume"
        or invalidated_client_volume_evidence.get("accepted_rounds_credit")
        != 0
        or invalidated_client_volume_evidence.get("r0_credit") != 0
        or invalidated_client_volume_evidence.get("reusable") is not False
        or invalidated_qualification_cwd_manifest.get("bundle_id")
        != INVALIDATED_QUALIFICATION_CWD_BUNDLE.name
        or sha256(
            INVALIDATED_QUALIFICATION_CWD_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_QUALIFICATION_CWD_MANIFEST_SHA256
        or invalidated_qualification_cwd_evidence.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_qualification_cwd_evidence.get("bundle_id")
        != INVALIDATED_QUALIFICATION_CWD_BUNDLE.name
        or invalidated_qualification_cwd_evidence.get(
            "bundle_manifest_sha256"
        )
        != INVALIDATED_QUALIFICATION_CWD_MANIFEST_SHA256
        or invalidated_qualification_cwd_evidence.get("reason")
        != "sealed_command_spec_cwd_outside_client_measurement_tmpfs"
        or invalidated_qualification_cwd_evidence.get(
            "accepted_rounds_credit"
        )
        != 0
        or invalidated_qualification_cwd_evidence.get("r0_credit") != 0
        or invalidated_qualification_cwd_evidence.get("reusable") is not False
        or invalidated_client_identity_manifest.get("bundle_id")
        != INVALIDATED_CLIENT_IDENTITY_BUNDLE.name
        or sha256(
            INVALIDATED_CLIENT_IDENTITY_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_CLIENT_IDENTITY_MANIFEST_SHA256
        or invalidated_client_identity_evidence.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_client_identity_evidence.get("bundle_id")
        != INVALIDATED_CLIENT_IDENTITY_BUNDLE.name
        or invalidated_client_identity_evidence.get(
            "bundle_manifest_sha256"
        )
        != INVALIDATED_CLIENT_IDENTITY_MANIFEST_SHA256
        or invalidated_client_identity_evidence.get(
            "same_live_client_inspect_config_sha256"
        )
        != "fbc8a5b5f9b025fe807793160d4645652a5ce9cb3f874bbce4e988b440a57fb5"
        or invalidated_client_identity_evidence.get(
            "collect_only_client_inspect_config_sha256"
        )
        != "440a1093e2584491583a8c0eda5d36055f056c75b0cba9ce3456a8eaeafb688b"
        or invalidated_client_identity_evidence.get(
            "accepted_rounds_credit"
        )
        != 0
        or invalidated_client_identity_evidence.get("r0_credit") != 0
        or invalidated_client_identity_evidence.get("reusable") is not False
        or invalidated_import_bytecode_manifest.get("bundle_id")
        != INVALIDATED_IMPORT_BYTECODE_BUNDLE.name
        or sha256(
            INVALIDATED_IMPORT_BYTECODE_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_IMPORT_BYTECODE_MANIFEST_SHA256
        or invalidated_import_bytecode_evidence.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_import_bytecode_evidence.get("bundle_id")
        != INVALIDATED_IMPORT_BYTECODE_BUNDLE.name
        or invalidated_import_bytecode_evidence.get(
            "bundle_manifest_sha256"
        )
        != INVALIDATED_IMPORT_BYTECODE_MANIFEST_SHA256
        or invalidated_import_bytecode_evidence.get(
            "published_exact_payload_file_count"
        )
        != 1027
        or invalidated_import_bytecode_evidence.get(
            "bootstrap_reached_container_or_workload_start"
        )
        is not False
        or invalidated_import_bytecode_evidence.get(
            "accepted_rounds_credit"
        )
        != 0
        or invalidated_import_bytecode_evidence.get("r0_credit") != 0
        or invalidated_import_bytecode_evidence.get("reusable") is not False
        or invalidated_client_permission_manifest.get("bundle_id")
        != INVALIDATED_CLIENT_PERMISSION_BUNDLE.name
        or sha256(
            INVALIDATED_CLIENT_PERMISSION_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_CLIENT_PERMISSION_MANIFEST_SHA256
        or invalidated_client_permission_evidence.get("status")
        != "INVALIDATED_DO_NOT_REUSE"
        or invalidated_client_permission_evidence.get("bundle_id")
        != INVALIDATED_CLIENT_PERMISSION_BUNDLE.name
        or invalidated_client_permission_evidence.get("bundle_manifest_sha256")
        != INVALIDATED_CLIENT_PERMISSION_MANIFEST_SHA256
        or invalidated_client_permission_evidence.get("attempt_token")
        != "tpch-v2-20260731t020337z-c02b2656a8868d38b0dcab8a"
        or invalidated_client_permission_evidence.get("workload_started") is not False
        or invalidated_client_permission_evidence.get("accepted_rounds_credit")
        != 0
        or invalidated_client_permission_evidence.get("r0_credit") != 0
        or invalidated_client_permission_evidence.get("reusable") is not False
        or invalidated_shared_contract_manifest.get("bundle_id")
        != INVALIDATED_SHARED_CONTRACT_BUNDLE.name
        or sha256(
            INVALIDATED_SHARED_CONTRACT_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_SHARED_CONTRACT_MANIFEST_SHA256
        or invalidated_shared_contract_evidence.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_shared_contract_evidence.get("bundle_id")
        != INVALIDATED_SHARED_CONTRACT_BUNDLE.name
        or invalidated_shared_contract_evidence.get("bundle_manifest_sha256")
        != INVALIDATED_SHARED_CONTRACT_MANIFEST_SHA256
        or invalidated_shared_contract_evidence.get("workload_started") is not False
        or invalidated_shared_contract_evidence.get("r0_credit") != 0
        or invalidated_shared_contract_evidence.get("accepted_rounds_credit")
        != 0
        or invalidated_shared_contract_evidence.get("reusable") is not False
        or invalidated_host_tmpfs_manifest.get("bundle_id")
        != INVALIDATED_HOST_TMPFS_BUNDLE.name
        or sha256(
            INVALIDATED_HOST_TMPFS_BUNDLE / "bundle_manifest.json"
        )
        != INVALIDATED_HOST_TMPFS_MANIFEST_SHA256
        or invalidated_host_tmpfs_evidence.get("status")
        != "INVALIDATED_DO_NOT_LAUNCH"
        or invalidated_host_tmpfs_evidence.get("bundle_id")
        != INVALIDATED_HOST_TMPFS_BUNDLE.name
        or invalidated_host_tmpfs_evidence.get("bundle_manifest_sha256")
        != INVALIDATED_HOST_TMPFS_MANIFEST_SHA256
        or invalidated_host_tmpfs_evidence.get(
            "bootstrap_reached_container_or_workload_start"
        )
        is not False
        or invalidated_host_tmpfs_evidence.get("workload_started") is not False
        or invalidated_host_tmpfs_evidence.get("r0_credit") != 0
        or invalidated_host_tmpfs_evidence.get("accepted_rounds_credit") != 0
        or invalidated_host_tmpfs_evidence.get("reusable") is not False
    ):
        raise BuildError("runtime/incompatible bootstrap authority differs")
    contract = load_object(CONTRACT, "distributed-v2 contract")
    tpch_base = (contract.get("scenarios") or {}).get("tpch") or {}
    tpch = dict(tpch_base)
    tpch["client_resource_envelope"] = amendment.get(
        "client_resource_envelope_override"
    )
    expected_db = {
        "cpu_quota_cores": 4,
        "cpuset_cpus": "18-20,54-56",
        "cpuset_mems": "1",
        "memory_bytes": 5583454208,
        "memory_swap_bytes": 5583454208,
        "pids_max": 512,
        "io_read_bps": 100663296,
        "io_write_bps": 100663296,
        "innodb_buffer_pool_bytes": 4294967296,
        "mysql_port": 65103,
    }
    expected_storage = {
        "device": "/dev/nvme0n1",
        "mount": "/mnt/nvme0n1",
        "expected_live_major_minor": "259:1",
        "template_path":
            "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template",
        "runtime_root":
            "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
            "runtime_algorithm_c_successor_20260729",
        "full_physical_copy_required": True,
        "overlay_or_reflink_prohibited": True,
    }
    if (
        contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("postgresql_mutation_prohibited") is not True
        or sha256(TPCH_CLIENT_IDENTITY_AMENDMENT)
        != TPCH_CLIENT_IDENTITY_AMENDMENT_SHA256
        or amendment.get("schema_version")
        != "mysql_tpch_formal10_client_identity_amendment_v3"
        or amendment.get("formal_scope") != "mysql_only"
        or amendment.get("cross_engine_comparison_prohibited") is not True
        or amendment.get("postgresql_mutation_prohibited") is not True
        or amendment.get("base_four_scene_contract_sha256") != CONTRACT_SHA256
        or amendment.get("scenario") != "tpch_memory"
        or amendment.get("accepted_round_authorized") is not False
        or tpch.get("scenario") != "tpch_memory"
        or (tpch.get("host") or {}).get("hostname") != "node04"
        or tpch.get("storage") != expected_storage
        or tpch.get("resource_envelope") != expected_db
        or tpch.get("client_resource_envelope")
        != {
            "execution_mode": "dedicated_read_only_docker_client_cgroup",
            "cpuset_cpus": "0-7,36-43",
            "cpuset_mems": "0",
            "cpu_quota_cores": 16,
            "memory_bytes": 4294967296,
            "memory_swap_bytes": 4294967296,
            "pids_max": 512,
            "network_mode": "host",
            "root_filesystem_read_only": True,
            "linux_capabilities_drop": "ALL",
            "no_new_privileges": True,
            "docker_log_driver": "none",
            "runtime_and_bundle_mounts_read_only": True,
            "measurement_output_medium": "host_tmpfs_bind",
            "container_user": "1008:1008",
            "measurement_directory_uid": 1008,
            "measurement_directory_gid": 1008,
            "measurement_directory_mode": "0700",
            "tmpfs_bytes": 1073741824,
            "target_mysql_device_write_prohibited": True,
            "pid_and_all_descendant_affinity_readback_required": True,
        }
    ):
        raise BuildError("TPC-H distributed-v2 frozen fields differ")
    predecessor = load_object(
        PREDECESSOR / "bundle_manifest.json", "TPC-H v36 predecessor"
    )
    predecessor_files = relative_files(PREDECESSOR)
    predecessor_files.pop("bundle_manifest.json", None)
    if (
        predecessor.get("bundle_id") != PREDECESSOR.name
        or predecessor.get("formal_scope") != "mysql_only"
        or predecessor.get("cross_engine_comparison_prohibited") is not True
        or predecessor.get("parameter_count") != 4
        or predecessor.get("candidate_count") != 15
        or predecessor.get("fixed_top_k_real_replay") != 5
        or predecessor.get("original_gradient_only") is not True
        or predecessor.get("files") != predecessor_files
    ):
        raise BuildError("TPC-H v36 predecessor closure/invariants differ")
    source_manifest = load_object(
        ALGORITHM_SOURCE / "source_manifest.json",
        "TPC-H algorithm source manifest",
    )
    identity = dict(source_manifest)
    identity.pop("bundle_id", None)
    if (
        source_manifest.get("bundle_id") != ALGORITHM_SOURCE.name.removeprefix("source-")
        or hashlib.sha256(canonical(identity)).hexdigest()
        != source_manifest["bundle_id"]
    ):
        raise BuildError("TPC-H algorithm source content address differs")
    binding = load_object(
        SQL_REGISTRY / "production_bindings/binding_manifest.json",
        "TPC-H binding manifest",
    )
    seal = load_object(SQL_REGISTRY / "registry_seal.json", "TPC-H registry seal")
    if (
        binding.get("coverage_complete") is not True
        or binding.get("covered_queries")
        != [f"Q{index}" for index in range(1, 23)]
        or seal.get("status") != "SEALED"
        or seal.get("coverage_complete") is not True
        or seal.get("missing_queries") != []
        or seal.get("outer_algorithm_c_authorized") is not False
        or seal.get("binding_manifest_sha256")
        != sha256(SQL_REGISTRY / "production_bindings/binding_manifest.json")
    ):
        raise BuildError("TPC-H Q1-Q22 production registry differs")
    runtime = load_object(V40_POINT / "campaign_runtime.json", "TPC-H v40 runtime")
    if (
        runtime.get("run_id") != "alg_c_tpch_r0_20260730T210012_414be152"
        or runtime.get("phase") != "INVALID_R0_UNSTABLE"
        or runtime.get("host") != "local://compute00"
        or runtime.get("device") != "/dev/nvme2n1"
        or runtime.get("r0_complete") is not False
        or runtime.get("r0_gate_valid") is not False
        or runtime.get("algorithm_c_authorized") is not False
    ):
        raise BuildError("TPC-H v40 invalid-R0 evidence differs")
    return {
        "contract": contract,
        "tpch_client_identity_amendment": amendment,
        "tpch": tpch,
        "predecessor": predecessor,
        "source_manifest": source_manifest,
        "binding": binding,
        "seal": seal,
        "v40_runtime": runtime,
        "runtime_manifest": runtime_manifest,
        "invalidated_bootstrap_manifest": invalidated_manifest,
        "invalidated_closure_manifest": invalidated_closure_manifest,
        "invalidated_lsof_manifest": invalidated_lsof_manifest,
        "invalidated_fallback_manifest": invalidated_fallback_manifest,
        "invalidated_nonroot_fallback_manifest": invalidated_nonroot_fallback_manifest,
        "invalidated_socket_identity_manifest": invalidated_socket_identity_manifest,
        "invalidated_permission_manifest": invalidated_permission_manifest,
        "invalidated_permission_readback_manifest": invalidated_permission_readback_manifest,
        "invalidated_tree_digest_manifest": invalidated_tree_digest_manifest,
        "invalidated_tree_digest_bytes_manifest": invalidated_tree_digest_bytes_manifest,
        "invalidated_metadata_sensitive_tree_identity_manifest":
            invalidated_metadata_sensitive_tree_identity_manifest,
        "invalidated_collector_schema_manifest":
            invalidated_collector_schema_manifest,
        "invalidated_collector_schema_evidence":
            invalidated_collector_schema_evidence,
        "invalidated_effective_blkio_manifest":
            invalidated_effective_blkio_manifest,
        "invalidated_effective_blkio_evidence":
            invalidated_effective_blkio_evidence,
        "invalidated_client_volume_manifest":
            invalidated_client_volume_manifest,
        "invalidated_client_volume_evidence":
            invalidated_client_volume_evidence,
        "invalidated_qualification_cwd_manifest":
            invalidated_qualification_cwd_manifest,
        "invalidated_qualification_cwd_evidence":
            invalidated_qualification_cwd_evidence,
        "invalidated_client_identity_manifest":
            invalidated_client_identity_manifest,
        "invalidated_client_identity_evidence":
            invalidated_client_identity_evidence,
        "invalidated_import_bytecode_manifest":
            invalidated_import_bytecode_manifest,
        "invalidated_import_bytecode_evidence":
            invalidated_import_bytecode_evidence,
        "invalidated_client_permission_manifest":
            invalidated_client_permission_manifest,
        "invalidated_client_permission_evidence":
            invalidated_client_permission_evidence,
        "invalidated_shared_contract_manifest":
            invalidated_shared_contract_manifest,
        "invalidated_shared_contract_evidence":
            invalidated_shared_contract_evidence,
        "invalidated_host_tmpfs_manifest":
            invalidated_host_tmpfs_manifest,
        "invalidated_host_tmpfs_evidence":
            invalidated_host_tmpfs_evidence,
    }


def formal10_contract(
    inputs: dict[str, Any], hashes: dict[str, str]
) -> dict[str, Any]:
    tpch = inputs["tpch"]
    return {
        "schema_version": "mysql_tpch_algorithm_c_formal10_contract_v2",
        "scenario": "tpch_memory",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "database_resource_envelope": tpch["resource_envelope"],
        "client_resource_envelope": tpch["client_resource_envelope"],
        "storage": tpch["storage"],
        "physical_copy_authority": {
            "implementation":
                "tar_regular_file_byte_stream_target_permission_normalization_plus_fiemap_no_shared_extents",
            "cp_reflink_never_dependency": False,
            "clone_api_prohibited": True,
            "shared_physical_extent_bytes_required": 0,
            "source_and_destination_tree_identity_required": True,
            "tree_digest_scanner": {
                "sealed_image_required": True,
                "user": "0:0",
                "capabilities_drop": "ALL",
                "capabilities_add_exact": ["DAC_READ_SEARCH"],
                "network_mode": "none",
                "root_filesystem_read_only": True,
                "source_and_destination_bind_read_only": True,
                "traversal_or_hash_error_policy": "FAIL_CLOSED",
                "expected_regular_file_count": 173,
                "expected_regular_file_bytes": 27_110_986_878,
            },
            "atomic_rename_required": True,
            "target_only_permission_normalization_required": True,
            "target_mysql_image_identity": {
                "uid": 999,
                "gid": 999,
                "directory_mode": "0700",
                "regular_file_mode": "0600",
            },
            "source_metadata_stable_before_after_required": True,
        },
        "template_owner_inventory_authority": {
            "host_lsof_or_fuser_dependency": False,
            "primary":
                "python_stdlib_proc_fd_cwd_root_inventory",
            "permission_fallback":
                "readonly_host_pid_docker_root_scanner_sys_ptrace_"
                "dac_read_search",
            "fallback_runtime_identity_readback_required": True,
            "fallback_root_identity": {"uid": 0, "gid": 0},
            "fallback_capabilities_exact": ["SYS_PTRACE", "DAC_READ_SEARCH"],
            "fallback_network_none_required": True,
            "fallback_read_only_rootfs_required": True,
            "fallback_docker_socket_mount_prohibited": True,
            "fallback_stdout_failure_evidence_required": True,
            "open_owner_result": "FAIL_CLOSED",
            "unreadable_pid_after_fallback_result": "FAIL_CLOSED",
            "must_pass_before_runtime_or_datadir_mutation": True,
        },
        "python_runtime_authority": {
            "source_root": "/home/anonymous/recost_runtime_py311_24_11_1",
            "target_root":
                "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
                "runtime_algorithm_c_successor_20260729/runtime/python311-"
                + PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "source_device": "/dev/sda3",
            "target_device": "/dev/nvme0n1",
            "source_and_target_different_device_required": True,
            "portable_manifest_sha256":
                PYTHON_RUNTIME_MANIFEST_SHA256,
            "tree_content_sha256":
                PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "copy_mode": "tar_regular_file_byte_stream_no_clone_api",
            "source_stage_target_exact_readback_required": True,
            "atomic_content_addressed_publish_required": True,
            "preexisting_target_reuse_requires_exact_readback": True,
        },
        "client_mount_isolation": {
            "mysql_image_volume_destination": "/var/lib/mysql",
            "manifest_covered_read_only_mask":
                CLIENT_MYSQL_DATADIR_MASK_RELATIVE,
            "exact_bind_source_and_destination_required": True,
            "anonymous_or_named_volume_prohibited": True,
            "mutable_application_mount_prohibited": True,
            "database_datadir_source_mount_prohibited": True,
            "secure_measurement_bind": {
                "source_relative_to_attempt_tmpfs": "client_measurement",
                "destination": "/measurement",
                "mount_type": "bind",
                "read_write_required": True,
                "host_filesystem": "tmpfs",
                "attempt_path_is_exact_mountpoint": True,
                "host_root_mount_required": True,
                "mount_capacity_bytes": 2147483648,
                "mount_options_required": [
                    "rw", "nosuid", "nodev", "noexec"
                ],
                "uid": 1008,
                "gid": 1008,
                "mode": "0700",
                "container_user": "1008:1008",
                "container_created_tmpfs_prohibited": True,
                "failure_cleanup_and_readback_evidence_required": True,
            },
            "docker_managed_resolver_metadata_exception_exact": [
                "/etc/hosts",
                "/etc/hostname",
                "/etc/resolv.conf",
            ],
        },
        "client_immutable_identity": {
            "projection":
                "canonical_client_immutable_projection_v1",
            "shared_helper_root":
                "mysql_tpch_client_immutable_identity_v1.py",
            "shared_helper_qualification":
                "qualification/mysql_tpch_client_immutable_identity_v1.py",
            "shared_helper_sha256":
                hashes["client_identity_helper_root"],
            "bootstrap_same_live_uses_projection": True,
            "command_materializer_uses_projection": True,
            "database_counter_transport_uses_projection": True,
            "qualification_collector_uses_projection": True,
            "docker_exec_state_execids_excluded": True,
            "lifecycle_identity_checked_separately": True,
            "authority_import_bytecode_disabled_before_helper_import": True,
            "readiness_import_bytecode_disabled_before_helper_import": True,
        },
        "qualification_execution": {
            "working_directory": "/measurement",
            "main_agent_authority_renderer":
                "qualification/mysql_tpch_v2_qualification_authority_v1.py",
            "short_token_and_sidecar_required": True,
            "qualification_seconds": 180,
            "minimum_samples": 181,
        },
        "fresh_r0_execution": {
            "runner": "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py",
            "real_workload_and_live_counters_required": True,
            "dynamic_gate_sha256_binding_required": True,
            "same_live_database_and_client_required": True,
            "database_counter_missing_or_timeout_result": "FAIL",
            "l1_l2_l3_fingerprint_required": True,
            "accepted_rounds_credit": 0,
        },
        "formal_graceful_quiescence_seconds": 120,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "algorithm": {
            "method": "experience_table_sgd",
            "active_parameters": PARAMETERS,
            "active_parameter_count": 4,
            "required_nonempty_subsets": 15,
            "subset_enumeration": "all_2^4_minus_1_nonempty_subspaces",
            "candidate_order": "global_descending_final_candidate_score",
            "fixed_real_replay_top_k": 5,
            "target_accepted_rounds": 10,
            "original_gradient_only": True,
            "gradient_formula":
                "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
            "parameter_update_source": "g_j_and_eta_only",
            "required_gradient_event_fields": GRADIENT_FIELDS,
            "qps_role": "real_replay_outer_acceptance_only",
            "replacement_search_algorithm_prohibited": True,
        },
        "fingerprint": {
            "l1_required": True,
            "l2_required": True,
            "l3_required": True,
            "job_tpch_l3": [
                "memory_peak", "dynamic_execution_memory", "spill"
            ],
            "duplicate_rule":
                "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3",
            "missing_any_level_policy": "independent_candidate",
        },
        "same_live_identity_handoff_required": [
            "database_container_id",
            "database_pid",
            "database_starttime_ticks",
            "database_inspect_config_sha256",
            "database_datadir",
            "client_container_id",
            "client_pid",
            "client_starttime_ticks",
            "client_inspect_config_sha256",
            "effective_blkio_authority_sha256",
            "attempt_token",
            "bundle_id",
        ],
        "sql_stdout_host_bridge": {
            "workload": "sealed_Q1_Q22_real_mysql_client_completions",
            "q18_weight": 16,
            "other_query_weight": 4,
            "producer": "docker_exec_stdout",
            "host_sink": "host_tmpfs",
            "parser":
                "qualification/mysql_distributed_workload_metric_adapter_v1.py",
            "native_adapter":
                "qualification/mysql_sql_debug_metric_adapter_v1.py",
            "docker_log_driver_dependency": False,
            "synthetic_metric_prohibited": True,
        },
        "promotion_chain": [
            "immutable_static_pass",
            "python_runtime_atomic_publish_and_exact_readback_pass",
            "executable_debug_bootstrap_pass",
            "database_and_client_exact_live_readback_pass",
            "180s_bidirectional_dynamic_noninterference_pass",
            "fresh_R0_pass",
            "15_candidates_fixed_Top5_debug_pass",
            "same_live_formal10_handoff_pass",
            "main_agent_formal10_launch_decision",
        ],
        "accounting_exclusions": [
            "capture",
            "R0",
            "qualification",
            "failed_replay",
            "debug_round",
            "gate_failure",
            "historical_v40_INVALID_R0_UNSTABLE",
        ],
        "qualification_hashes": hashes,
        "predecessor_bundle_id": PREDECESSOR.name,
        "algorithm_source_bundle_id": inputs["source_manifest"]["bundle_id"],
        "sql_registry_binding_manifest_sha256":
            sha256(SQL_REGISTRY / "production_bindings/binding_manifest.json"),
        "sql_registry_seal_sha256": sha256(SQL_REGISTRY / "registry_seal.json"),
    }


def verify_bundle(bundle: Path, expected_id: str | None = None) -> dict[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path, "TPC-H v2 bundle manifest")
    bundle_id = manifest.get("bundle_id")
    if expected_id is not None and bundle_id != expected_id:
        raise BuildError("expected TPC-H bundle ID differs")
    if expected_id is None and bundle.name != bundle_id:
        raise BuildError("TPC-H directory/content address differs")
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if hashlib.sha256(canonical(identity)).hexdigest() != bundle_id:
        raise BuildError("TPC-H manifest content address differs")
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(manifest_path)}  bundle_manifest.json\n"
    ):
        raise BuildError("TPC-H manifest sidecar differs")
    actual = relative_files(bundle)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    if actual != manifest.get("files"):
        raise BuildError("TPC-H exact file closure differs")
    for path in [bundle, *sorted(bundle.rglob("*"))]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError(f"TPC-H immutable mode differs: {path}")
    return manifest


def publish(output_root: Path) -> Path:
    inputs = verify_inputs()
    for label, source in {
        "authority": AUTHORITY,
        "blkio helper": BLKIO_HELPER,
        "qualification authority renderer": QUALIFICATION_AUTHORITY_RENDERER,
        "fresh R0 runner": FRESH_R0_RUNNER,
        "readiness": READINESS,
        "collector": COLLECTOR,
        "metric adapter": METRIC_ADAPTER,
        "SQL adapter": SQL_ADAPTER,
        "runtime helper": RUNTIME_HELPER,
        "client immutable identity helper": CLIENT_IDENTITY_HELPER,
    }.items():
        if source.is_symlink() or not source.is_file():
            raise BuildError(f"{label} source is absent")
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpch-formal10-v2.", dir=output_root))
    try:
        # Include only each predecessor's sealed file map, never incidental
        # pycache or mutable files beside the manifest.
        predecessor_manifest = inputs["predecessor"]
        for relative, expected in sorted(predecessor_manifest["files"].items()):
            source = PREDECESSOR / relative
            if sha256(source) != expected:
                raise BuildError(f"TPC-H predecessor differs: {relative}")
            copy_file(source, stage / "predecessor" / relative)
        copy_file(
            PREDECESSOR / "bundle_manifest.json",
            stage / "predecessor/bundle_manifest.json",
        )
        source_manifest = inputs["source_manifest"]
        copy_manifest_closure(
            ALGORITHM_SOURCE, source_manifest, stage / "algorithm_source"
        )
        copy_file(
            ALGORITHM_SOURCE / "source_manifest.json",
            stage / "algorithm_source/source_manifest.json",
        )
        hashes: dict[str, str] = {}
        collector_destination = (
            stage
            / "qualification/mysql_distributed_live_readback_qualification_v1.py"
        )
        collector_destination.parent.mkdir(parents=True, exist_ok=True)
        collector_destination.write_bytes(tpch_collector_payload())
        os.chmod(collector_destination, 0o755)
        hashes["collector"] = sha256(collector_destination)
        for key, source, relative in (
            (
                "qualification_authority_renderer",
                QUALIFICATION_AUTHORITY_RENDERER,
                "qualification/mysql_tpch_v2_qualification_authority_v1.py",
            ),
            (
                "fresh_r0_runner",
                FRESH_R0_RUNNER,
                "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py",
            ),
            (
                "blkio_v1_helper",
                BLKIO_HELPER,
                "qualification/mysql_tpch_blkio_v1.py",
            ),
            (
                "metric_adapter",
                METRIC_ADAPTER,
                "qualification/mysql_distributed_workload_metric_adapter_v1.py",
            ),
            (
                "sql_adapter",
                SQL_ADAPTER,
                "qualification/mysql_sql_debug_metric_adapter_v1.py",
            ),
            (
                "runtime_helper",
                RUNTIME_HELPER,
                "qualification/runtime_tree_content_identity_v3.py",
            ),
            (
                "client_identity_helper_qualification",
                CLIENT_IDENTITY_HELPER,
                "qualification/mysql_tpch_client_immutable_identity_v1.py",
            ),
            (
                "client_identity_helper_root",
                CLIENT_IDENTITY_HELPER,
                "mysql_tpch_client_immutable_identity_v1.py",
            ),
            (
                "bootstrap_authority",
                AUTHORITY,
                "mysql_tpch_distributed_v2_authority.py",
            ),
            (
                "readiness_verifier",
                READINESS,
                "tpch_formal10_readiness_v2.py",
            ),
        ):
            hashes[key] = copy_file(source, stage / relative, 0o755)
        client_mask_marker = {
            "schema_version": "mysql_tpch_client_volume_readonly_mask_v1",
            "bundle_content_address_bound_by_manifest": True,
            "destination": "/var/lib/mysql",
            "mount_type": "bind",
            "read_only_required": True,
            "anonymous_or_named_volume_prohibited": True,
            "mutable_application_mount_prohibited": True,
            "accepted_round_authorized": False,
        }
        client_mask_path = (
            stage
            / CLIENT_MYSQL_DATADIR_MASK_RELATIVE
            / "mask_identity.json"
        )
        client_mask_path.parent.mkdir(parents=True, exist_ok=True)
        client_mask_path.write_bytes(canonical(client_mask_marker))
        os.chmod(client_mask_path, 0o444)
        hashes["client_mysql_datadir_readonly_mask"] = sha256(
            client_mask_path
        )
        copy_file(CONTRACT, stage / "contracts/four_scene_v2.json", 0o444)
        copy_file(
            TPCH_CLIENT_IDENTITY_AMENDMENT,
            stage / "contracts/tpch_client_identity_amendment_v3.json",
            0o444,
        )
        runtime_payload = canonical(runtime_contract(inputs))
        (stage / "distributed_runtime_contract.json").write_bytes(
            runtime_payload
        )
        os.chmod(stage / "distributed_runtime_contract.json", 0o444)
        copy_file(
            INVALIDATED_COLLECTOR_SCHEMA_EVIDENCE,
            stage
            / "invalidated_collector_schema_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_EFFECTIVE_BLKIO_EVIDENCE,
            stage / "invalidated_effective_blkio_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_CLIENT_VOLUME_EVIDENCE,
            stage / "invalidated_client_volume_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_QUALIFICATION_CWD_EVIDENCE,
            stage / "invalidated_qualification_cwd_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_CLIENT_IDENTITY_EVIDENCE,
            stage / "invalidated_client_identity_projection_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_IMPORT_BYTECODE_EVIDENCE,
            stage / "invalidated_import_bytecode_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_CLIENT_PERMISSION_EVIDENCE,
            stage / "invalidated_client_permission_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_SHARED_CONTRACT_EVIDENCE,
            stage / "invalidated_shared_contract_bundle.json",
            0o444,
        )
        copy_file(
            INVALIDATED_HOST_TMPFS_EVIDENCE,
            stage / "invalidated_host_tmpfs_bundle.json",
            0o444,
        )
        copy_file(
            PYTHON_RUNTIME_MANIFEST,
            stage / "runtime/python_runtime_tree_manifest_v3.json",
            0o444,
        )
        binding_dir = SQL_REGISTRY / "production_bindings"
        output_map = inputs["binding"]["outputs"]
        for index in range(1, 23):
            name = f"Q{index}.sql"
            source = binding_dir / name
            if sha256(source) != output_map[name]["sha256"]:
                raise BuildError(f"TPC-H sealed SQL differs: {name}")
            copy_file(source, stage / "qualification/sql/tpch" / name, 0o444)
        for name in ("binding_manifest.json",):
            copy_file(
                binding_dir / name,
                stage / "qualification/sql_registry" / name,
                0o444,
            )
        copy_file(
            SQL_REGISTRY / "registry_seal.json",
            stage / "qualification/sql_registry/registry_seal.json",
            0o444,
        )
        v40_runtime = V40_POINT / "campaign_runtime.json"
        v40_result = V40_POINT / "r0_result.json"
        invalidation = {
            "schema_version": "mysql_tpch_v40_invalid_r0_invalidation_v1",
            "status": "INVALIDATED_NOT_REUSABLE",
            "run_id": "alg_c_tpch_r0_20260730T210012_414be152",
            "phase": "INVALID_R0_UNSTABLE",
            "host": "local://compute00",
            "device": "/dev/nvme2n1",
            "campaign_runtime_sha256": sha256(v40_runtime),
            "r0_result_sha256": sha256(v40_result),
            "r0_complete": False,
            "r0_gate_valid": False,
            "algorithm_c_authorized": False,
            "reusable": False,
            "candidate_seed_authorized": False,
            "formal10_authorized": False,
            "accepted_rounds_credit": 0,
            "superseded_by": "distributed_node04_nvme0n1_fresh_R0_only",
        }
        invalidation_payload = canonical(invalidation)
        (stage / "historical_v40_invalidation.json").write_bytes(
            invalidation_payload
        )
        invalidated_bootstrap = {
            "schema_version":
                "mysql_tpch_incompatible_bootstrap_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_BOOTSTRAP_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_BOOTSTRAP_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_bootstrap_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason":
                "node04_coreutils_8.22_cp_reflink_never_unsupported",
            "failed_primitive": "cp -a --reflink=never",
            "replacement_primitive":
                "tar_regular_file_byte_stream_plus_fiemap_no_shared_extents",
            "runtime_target_was_unpublished": True,
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_bootstrap_payload = canonical(invalidated_bootstrap)
        (stage / "invalidated_bootstrap_bundle.json").write_bytes(
            invalidated_bootstrap_payload
        )
        invalidated_closure = {
            "schema_version":
                "mysql_tpch_exact_closure_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_CLOSURE_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_CLOSURE_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_closure_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason":
                "root_manifest_filter_excluded_nested_predecessor_manifest",
            "excluded_valid_relative_path":
                "predecessor/bundle_manifest.json",
            "failure_stage": "live_bundle_closure_pre_mutation",
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_closure_payload = canonical(invalidated_closure)
        (stage / "invalidated_closure_bundle.json").write_bytes(
            invalidated_closure_payload
        )
        invalidated_lsof = {
            "schema_version":
                "mysql_tpch_host_tool_dependency_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_LSOF_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_LSOF_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_lsof_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason": "node04_host_lsof_absent",
            "failed_dependency": "lsof +D TEMPLATE",
            "replacement":
                "stdlib_proc_fd_cwd_root_with_docker_host_pid_fallback",
            "failure_stage": "template_owner_inventory_pre_mutation",
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_lsof_payload = canonical(invalidated_lsof)
        (stage / "invalidated_lsof_bundle.json").write_bytes(
            invalidated_lsof_payload
        )
        invalidated_fallback = {
            "schema_version": "mysql_tpch_root_squash_fallback_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_FALLBACK_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_FALLBACK_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_fallback_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason": "docker_fallback_default_root_cannot_read_root_squashed_0700_bundle",
            "failed_primitive": "docker run default-root immutable-bundle script",
            "replacement": "docker run --user host_uid:host_gid with scanner identity readback",
            "failure_stage": "template_owner_inventory_pre_runtime_or_datadir_mutation",
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_fallback_payload = canonical(invalidated_fallback)
        (stage / "invalidated_fallback_bundle.json").write_bytes(
            invalidated_fallback_payload
        )
        invalidated_nonroot_fallback = {
            "schema_version": "mysql_tpch_nonroot_host_pid_fallback_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_NONROOT_FALLBACK_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_NONROOT_FALLBACK_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_nonroot_fallback_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason": "nonroot_host_pid_scanner_has_unreadable_pids",
            "failed_primitive": "docker run --user host_uid:host_gid --cap-add SYS_PTRACE",
            "replacement": "docker run --user 0:0 --cap-drop ALL --cap-add SYS_PTRACE --cap-add DAC_READ_SEARCH",
            "verified_node04_scanned_pid_count": 715,
            "verified_node04_unreadable_pids": [],
            "verified_node04_open_owners": [],
            "failure_stage": "template_owner_inventory_pre_runtime_or_datadir_mutation",
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_nonroot_fallback_payload = canonical(invalidated_nonroot_fallback)
        (stage / "invalidated_nonroot_fallback_bundle.json").write_bytes(
            invalidated_nonroot_fallback_payload
        )
        invalidated_socket_identity = {
            "schema_version": "mysql_tpch_logical_socket_identity_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_SOCKET_IDENTITY_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_SOCKET_IDENTITY_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_socket_identity_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason": "tree_digest_included_root_logical_mysql_socket_removed_by_copy",
            "failed_primitive": "source_tree_digest_tar_including_mysql_sock",
            "replacement": "validate_exact_root_logical_mysql_socket_then_normalized_exclusion",
            "failure_stage": "post_copy_pre_database_or_client",
            "database_or_client_started": False,
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_socket_identity_payload = canonical(
            invalidated_socket_identity
        )
        (stage / "invalidated_socket_identity_bundle.json").write_bytes(
            invalidated_socket_identity_payload
        )
        invalidated_permission = {
            "schema_version": "mysql_tpch_datadir_permission_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_PERMISSION_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_PERMISSION_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_permission_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason": "mysql_bootstrap_failed_mysql_socket_ibdata1_read_only_datadir",
            "failure_stage": "bootstrap_database_before_database_or_client",
            "database_or_client_started": False,
            "pid": 0,
            "replacement": (
                "target_only_uid999_gid999_0700_0600_normalization_with_"
                "source_metadata_before_after_readback"
            ),
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_permission_payload = canonical(invalidated_permission)
        (stage / "invalidated_permission_bundle.json").write_bytes(
            invalidated_permission_payload
        )
        invalidated_permission_readback = {
            "schema_version": "mysql_tpch_datadir_permission_readback_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_PERMISSION_READBACK_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_PERMISSION_READBACK_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_permission_readback_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason": "permission_normalization_readback_used_find_user_group_names_against_numeric_uid_gid",
            "failed_primitive": "find_printf_percent_u_percent_g_compared_to_999_999",
            "replacement": "find_printf_percent_U_percent_G_numeric_uid_gid_readback",
            "failure_stage": "post_copy_pre_database_or_client",
            "database_or_client_started": False,
            "database_pid": 0,
            "client_pid": 0,
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_permission_readback_payload = canonical(
            invalidated_permission_readback
        )
        (stage / "invalidated_permission_readback_bundle.json").write_bytes(
            invalidated_permission_readback_payload
        )
        invalidated_tree_digest = {
            "schema_version":
                "mysql_tpch_tree_digest_privilege_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_TREE_DIGEST_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_TREE_DIGEST_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_tree_digest_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason":
                "tree_digest_scanner_lacked_root_dac_read_search_and_could_not_traverse_0700_datadir",
            "failed_primitive":
                "docker_readonly_tree_digest_cap_drop_all_without_explicit_user_or_dac_read_search",
            "replacement":
                "sealed_image_root_0_0_cap_drop_all_cap_add_dac_read_search_network_none_readonly_bind",
            "expected_regular_file_count": 173,
            "expected_regular_file_bytes": 27_110_986_878,
            "complete_source_destination_content_identity_required": True,
            "traversal_or_hash_error_policy": "FAIL_CLOSED",
            "failure_stage":
                "source_tree_digest_pre_copy_pre_database_or_client",
            "database_or_client_started": False,
            "database_pid": 0,
            "client_pid": 0,
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
            "accepted_rounds_credit": 0,
        }
        invalidated_tree_digest_payload = canonical(invalidated_tree_digest)
        (stage / "invalidated_tree_digest_bundle.json").write_bytes(
            invalidated_tree_digest_payload
        )
        invalidated_tree_digest_bytes = {
            "schema_version": "mysql_tpch_tree_digest_byte_count_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id": INVALIDATED_TREE_DIGEST_BYTES_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_TREE_DIGEST_BYTES_BUNDLE / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs["invalidated_tree_digest_bytes_manifest"]["component_hashes"][
                    "bootstrap_authority"
                ]
            ),
            "reason": "sealed_tree_digest_expected_regular_file_bytes_stale_by_293762",
            "expected_regular_file_count": 173,
            "stale_expected_regular_file_bytes": 27_111_280_640,
            "read_only_scanner_regular_file_bytes": 27_110_986_878,
            "prior_authority_summary_regular_file_bytes": 27_110_986_878,
            "root_dac_read_search_scanner_preserved": True,
            "traversal_or_hash_error_policy": "FAIL_CLOSED",
            "failure_stage": "source_tree_digest_pre_attempt_pre_database_or_client",
            "attempt_created": False,
            "database_or_client_started": False,
            "database_pid": 0,
            "client_pid": 0,
            "accepted_rounds_credit": 0,
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
        }
        invalidated_tree_digest_bytes_payload = canonical(
            invalidated_tree_digest_bytes
        )
        (stage / "invalidated_tree_digest_bytes_bundle.json").write_bytes(
            invalidated_tree_digest_bytes_payload
        )
        invalidated_metadata_sensitive_tree_identity = {
            "schema_version":
                "mysql_tpch_metadata_sensitive_tree_identity_bundle_invalidation_v1",
            "status": "INVALIDATED_DO_NOT_LAUNCH",
            "bundle_id":
                INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE.name,
            "bundle_manifest_sha256": sha256(
                INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE
                / "bundle_manifest.json"
            ),
            "bootstrap_authority_sha256": (
                inputs[
                    "invalidated_metadata_sensitive_tree_identity_manifest"
                ]["component_hashes"]["bootstrap_authority"]
            ),
            "reason":
                "tar_tree_identity_included_owner_and_mode_after_target_permission_normalization",
            "failed_primitive":
                "tar_archive_identity_retained_mode_bits_while_target_was_normalized",
            "replacement":
                "canonical_path_type_size_content_sha256_identity_excluding_owner_and_mode",
            "source_regular_file_count": 173,
            "destination_regular_file_count": 173,
            "source_regular_file_bytes": 27_110_986_878,
            "destination_regular_file_bytes": 27_110_986_878,
            "source_tree_sha256":
                "50f4bab0c5ffa5180a26ef0d1c16f1a1c2320512399d6c540b72e88d28dde43b",
            "destination_tree_sha256":
                "35181f0ff87bd622d348c2c4058b1903ab0ca352f3542a34cfa4acd24ac5b310",
            "content_identity_fields": [
                "canonical_relative_path",
                "entry_type",
                "regular_file_size",
                "regular_file_sha256",
            ],
            "ownership_and_mode_excluded_from_content_identity": True,
            "target_permission_readback_remains_separate_and_strict": True,
            "root_dac_read_search_scanner_preserved": True,
            "traversal_or_hash_error_policy": "FAIL_CLOSED",
            "failure_stage": "post_copy_pre_database_or_client",
            "database_or_client_started": False,
            "database_pid": 0,
            "client_pid": 0,
            "accepted_rounds_credit": 0,
            "launch_authorized": False,
            "formal_r0_authorized": False,
            "formal10_authorized": False,
            "reusable": False,
        }
        invalidated_metadata_sensitive_tree_identity_payload = canonical(
            invalidated_metadata_sensitive_tree_identity
        )
        (
            stage
            / "invalidated_metadata_sensitive_tree_identity_bundle.json"
        ).write_bytes(invalidated_metadata_sensitive_tree_identity_payload)
        contract = formal10_contract(inputs, hashes)
        contract_payload = canonical(contract)
        (stage / "tpch_formal10_contract.json").write_bytes(contract_payload)
        files = relative_files(stage)
        identity = {
            "schema_version": "mysql_tpch_distributed_formal10_successor_v2",
            "scenario": "tpch_memory",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "immutable": True,
            "offline_build_only": True,
            "remote_actions": 0,
            "four_scene_contract_sha256": CONTRACT_SHA256,
            "tpch_client_identity_amendment_sha256":
                TPCH_CLIENT_IDENTITY_AMENDMENT_SHA256,
            "predecessor_bundle_id": PREDECESSOR.name,
            "predecessor_manifest_sha256":
                sha256(PREDECESSOR / "bundle_manifest.json"),
            "algorithm_source_bundle_id": source_manifest["bundle_id"],
            "algorithm_source_manifest_sha256":
                sha256(ALGORITHM_SOURCE / "source_manifest.json"),
            "formal10_contract_sha256":
                hashlib.sha256(contract_payload).hexdigest(),
            "historical_v40_invalidation_sha256":
                hashlib.sha256(invalidation_payload).hexdigest(),
            "invalidated_bootstrap_bundle_sha256":
                hashlib.sha256(invalidated_bootstrap_payload).hexdigest(),
            "invalidated_closure_bundle_sha256":
                hashlib.sha256(invalidated_closure_payload).hexdigest(),
            "invalidated_lsof_bundle_sha256":
                hashlib.sha256(invalidated_lsof_payload).hexdigest(),
            "invalidated_fallback_bundle_sha256":
                hashlib.sha256(invalidated_fallback_payload).hexdigest(),
            "invalidated_nonroot_fallback_bundle_sha256":
                hashlib.sha256(invalidated_nonroot_fallback_payload).hexdigest(),
            "invalidated_socket_identity_bundle_sha256":
                hashlib.sha256(invalidated_socket_identity_payload).hexdigest(),
            "invalidated_permission_bundle_sha256":
                hashlib.sha256(invalidated_permission_payload).hexdigest(),
            "invalidated_permission_readback_bundle_sha256":
                hashlib.sha256(invalidated_permission_readback_payload).hexdigest(),
            "invalidated_tree_digest_bundle_sha256":
                hashlib.sha256(invalidated_tree_digest_payload).hexdigest(),
            "invalidated_tree_digest_bytes_bundle_sha256":
                hashlib.sha256(invalidated_tree_digest_bytes_payload).hexdigest(),
            "invalidated_metadata_sensitive_tree_identity_bundle_sha256":
                hashlib.sha256(
                    invalidated_metadata_sensitive_tree_identity_payload
                ).hexdigest(),
            "invalidated_collector_schema_bundle_sha256":
                sha256(INVALIDATED_COLLECTOR_SCHEMA_EVIDENCE),
            "invalidated_effective_blkio_bundle_sha256":
                sha256(INVALIDATED_EFFECTIVE_BLKIO_EVIDENCE),
            "invalidated_client_volume_bundle_sha256":
                sha256(INVALIDATED_CLIENT_VOLUME_EVIDENCE),
            "invalidated_qualification_cwd_bundle_sha256":
                sha256(INVALIDATED_QUALIFICATION_CWD_EVIDENCE),
            "invalidated_client_identity_projection_bundle_sha256":
                sha256(INVALIDATED_CLIENT_IDENTITY_EVIDENCE),
            "invalidated_import_bytecode_bundle_sha256":
                sha256(INVALIDATED_IMPORT_BYTECODE_EVIDENCE),
            "invalidated_client_permission_bundle_sha256":
                sha256(INVALIDATED_CLIENT_PERMISSION_EVIDENCE),
            "invalidated_shared_contract_bundle_sha256":
                sha256(INVALIDATED_SHARED_CONTRACT_EVIDENCE),
            "invalidated_host_tmpfs_bundle_sha256":
                sha256(INVALIDATED_HOST_TMPFS_EVIDENCE),
            "distributed_runtime_contract_sha256":
                hashlib.sha256(runtime_payload).hexdigest(),
            "shared_collector_source_sha256": sha256(COLLECTOR),
            "python_runtime_manifest_sha256":
                PYTHON_RUNTIME_MANIFEST_SHA256,
            "python_runtime_tree_content_sha256":
                PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "builder_sha256": sha256(BUILDER),
            "component_hashes": hashes,
            "invalidated_predecessor_bundle_ids": [
                "e29cafa825e8ed9134e1467a3eafffcaa8572fc5a6d9a9ba0dd676a091248d27",
                INVALIDATED_BOOTSTRAP_BUNDLE.name,
                INVALIDATED_CLOSURE_BUNDLE.name,
                INVALIDATED_LSOF_BUNDLE.name,
                INVALIDATED_FALLBACK_BUNDLE.name,
                INVALIDATED_NONROOT_FALLBACK_BUNDLE.name,
                INVALIDATED_SOCKET_IDENTITY_BUNDLE.name,
                INVALIDATED_PERMISSION_BUNDLE.name,
                INVALIDATED_PERMISSION_READBACK_BUNDLE.name,
                INVALIDATED_TREE_DIGEST_BUNDLE.name,
                INVALIDATED_TREE_DIGEST_BYTES_BUNDLE.name,
                INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE.name,
                INVALIDATED_COLLECTOR_SCHEMA_BUNDLE.name,
                INVALIDATED_EFFECTIVE_BLKIO_BUNDLE.name,
                INVALIDATED_CLIENT_VOLUME_BUNDLE.name,
                INVALIDATED_QUALIFICATION_CWD_BUNDLE.name,
                INVALIDATED_CLIENT_IDENTITY_BUNDLE.name,
                INVALIDATED_IMPORT_BYTECODE_BUNDLE.name,
                INVALIDATED_CLIENT_PERMISSION_BUNDLE.name,
                INVALIDATED_SHARED_CONTRACT_BUNDLE.name,
                INVALIDATED_HOST_TMPFS_BUNDLE.name,
            ],
            "invalidated_collector_sha256":
                "27240549d82200661615ac129bf081c0a7e01a2598faa5887be32c6fb821ac9b",
            "e29_collector_inherited": False,
            "fresh_r0_required": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "publication": {
                "content_addressed": True,
                "same_filesystem": True,
                "frozen_before_visibility": True,
                "atomic_directory_rename": True,
                "non_overwriting": True,
                "concurrent_identical_publish_idempotent": True,
                "fsync_before_and_after_rename": True,
            },
            "files": dict(sorted(files.items())),
        }
        bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
        manifest = {**identity, "bundle_id": bundle_id}
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            f"{hashlib.sha256(manifest_payload).hexdigest()}  bundle_manifest.json\n",
            encoding="ascii",
        )
        freeze(stage)
        verify_bundle(stage, bundle_id)
        fsync_tree(stage)
        final = output_root / bundle_id
        if final.exists() or final.is_symlink():
            verify_bundle(final, bundle_id)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        try:
            os.rename(stage, final)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            verify_bundle(final, bundle_id)
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        verify_bundle(final, bundle_id)
        return final
    except BaseException:
        if stage.exists():
            thaw_directories(stage)
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify-bundle", type=Path)
    args = parser.parse_args()
    if args.verify_bundle is not None:
        result = verify_bundle(args.verify_bundle.resolve())
        print(json.dumps(result, sort_keys=True))
        return 0
    bundle = publish(args.output_root)
    print(
        json.dumps(
            {
                "schema_version":
                    "mysql_tpch_formal10_successor_build_result_v2",
                "status": "BUILT_STATIC_PASS_RUNTIME_EVIDENCE_PENDING",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "remote_actions": 0,
                "bundle": str(bundle),
                "bundle_id": bundle.name,
                "formal10_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
