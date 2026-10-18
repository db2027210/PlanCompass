#!/usr/bin/env python3
"""JOB distributed-v2 bootstrap and same-live-planner authority.

This module is intentionally host-local.  It never opens SSH and it never
discovers a placement from ambient state.  A main-agent-signed specification
must bind the immutable bundle, images, attempt, paths, and private defaults
file before ``execute-bootstrap`` is allowed.

The bootstrap is qualification-only.  It creates one full physical datadir,
one exact-envelope MySQL container, and one hardened client container.  It
does not run capture, R0, replay, formal optimization, or accepted-round
accounting.  Later transitions are evidence-only and retain the exact same
live planner identity.
"""

from __future__ import print_function

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import socket
import stat
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
# A published content-addressed bundle has ``qualification/`` and
# ``authority/`` directly below its root.  The development checkout instead
# has the source authorities under preflight/history.  The runtime must never
# fall back to the mutable checkout when the sealed authority payload exists.
BUNDLE_AUTHORITY_ROOT = ROOT / "authority"
CONTRACT_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
)
if not CONTRACT_PATH.is_file():
    CONTRACT_PATH = (
        ROOT
        / "preflight/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
    )
CONTRACT_SHA256 = (
    "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
)
RECONCILIATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "formal_replay_authority_reconciliation_v1.json"
)
if not RECONCILIATION_PATH.is_file():
    RECONCILIATION_PATH = (
        ROOT
        / "history/mysql_gradient_debug/job_v6/20260728T085336Z/"
        "formal_replay_authority_reconciliation_v1.json"
    )
RECONCILIATION_SHA256 = (
    "96beed0c59d0f86ac8c4b517ce94a5af460be2b14089a3d40583a3ef6813df82"
)
F058_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json"
)
if not F058_INVALIDATION_PATH.is_file():
    F058_INVALIDATION_PATH = (
        ROOT
        / "preflight/mysql_job_v32_f0583a_sql_percent_invalidation_20260731.json"
    )
F058_INVALIDATION_SHA256 = (
    "172b0f74f9878251c3cb5b22b40bcc687151ca89c1967c901dc64b741dde9a86"
)
INVALIDATED_F058_BUNDLE_ID = (
    "f0583a934806dc2125ea402bf0e85e30b0e637a55e424d3d1ea1580875c3774c"
)
INVALIDATED_F058_ATTEMPT_TOKEN = (
    "job-v2-20260730t195053z-07e47cc55f09d6a807e4eb07"
)
EIGHTF_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_8f55631_client_credentials_invalidation_20260731.json"
)
if not EIGHTF_INVALIDATION_PATH.is_file():
    EIGHTF_INVALIDATION_PATH = (
        ROOT
        / "preflight/mysql_job_v32_8f55631_client_credentials_invalidation_20260731.json"
    )
EIGHTF_INVALIDATION_SHA256 = "64db09b05c29f458fd95491949f66b1c274396c91e0281df3695b1393566d1da"
INVALIDATED_EIGHTF_BUNDLE_ID = (
    "8f55631aaea99b6851c460822fe5099b8762f04183f71f0fdc37aae7b0d173f6"
)
INVALIDATED_EIGHTF_ATTEMPT_TOKEN = (
    "job-v2-20260730t201329z-6425f9a8eb0ec2662cf111ba"
)
FOUREE_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json"
)
if not FOUREE_INVALIDATION_PATH.is_file():
    FOUREE_INVALIDATION_PATH = (
        ROOT
        / "preflight/mysql_job_v32_4ee58d_client_managed_mount_invalidation_20260731.json"
    )
FOUREE_INVALIDATION_SHA256 = "277c5ef91043385f0a46aa15e98b834f45c8767b0ec6dda74c22401a5b628b97"
INVALIDATED_FOUREE_BUNDLE_ID = (
    "4ee58d435e830333b56f5a91876303a9f65d176a6ce0a760dbb51cd078112e7d"
)
INVALIDATED_FOUREE_ATTEMPT_TOKEN = (
    "job-v2-20260731t100000z-4ee58d435e830333b56f5a"
)
F1C705_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json"
)
if not F1C705_INVALIDATION_PATH.is_file():
    F1C705_INVALIDATION_PATH = (
        ROOT
        / "preflight/mysql_job_v32_f1c705_client_anonymous_volume_invalidation_20260731.json"
    )
F1C705_INVALIDATION_SHA256 = "b245701447e4d1ddf71b3e05937431c33c2df79bd91b2c7f405597fde0bdd962"
INVALIDATED_F1C705_BUNDLE_ID = (
    "f1c7051a70019ca6534a6687754349c6633e8f9ef1a43ac0a23564372207ccd6"
)
INVALIDATED_F1C705_ATTEMPT_TOKEN = (
    "job-v2-20260730t205603z-f1c7051a70019ca6534a668"
)
SEVENEO8_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_7e08bd_client_bridge_identity_invalidation_20260731.json"
)
if not SEVENEO8_INVALIDATION_PATH.is_file():
    SEVENEO8_INVALIDATION_PATH = (
        ROOT
        / "preflight/mysql_job_v32_7e08bd_client_bridge_identity_invalidation_20260731.json"
    )
SEVENEO8_INVALIDATION_SHA256 = "4a742c0fbdcc91e7eb62f311a52d8f7e3c353c06766db17140da2a3acba11586"
INVALIDATED_SEVENEO8_BUNDLE_ID = (
    "7e08bd2220b9f2c196163b71ba2d114d191b5c80377c2cd35e4778b7860a9d2f"
)
INVALIDATED_SEVENEO8_ATTEMPT_TOKEN = (
    "job-v2-20260730t211048z-7e08bd2220b9f2c196163b71"
)
TWOFEE_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_2fe2ae_transient_inspect_hash_invalidation_20260731.json"
)
if not TWOFEE_INVALIDATION_PATH.is_file():
    TWOFEE_INVALIDATION_PATH = (
        ROOT
        / "preflight/mysql_job_v32_2fe2ae_transient_inspect_hash_invalidation_20260731.json"
    )
TWOFEE_INVALIDATION_SHA256 = "262cd5a38297e228d209d4fe39f4f5c3beb7a98b5c5ae428d5914649c2072ab5"
INVALIDATED_TWOFEE_BUNDLE_ID = (
    "2fe2ae0c618eb022109cefc4c95d0d424e29a7062cd47f14241c975f1f828042"
)
INVALIDATED_TWOFEE_ATTEMPT_TOKEN = (
    "job-v2-20260730t212752z-2fe2ae0c618eb022109cefc4"
)
A0F3E7_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_a0f3e7_client_unknown_user_invalidation_20260731.json"
)
if not A0F3E7_INVALIDATION_PATH.is_file():
    A0F3E7_INVALIDATION_PATH = (
        ROOT / "preflight/mysql_job_v32_a0f3e7_client_unknown_user_invalidation_20260731.json"
    )
A0F3E7_INVALIDATION_SHA256 = "41dbac39cbce511fb1f774c2c9cd5403f7e1472842b77d72de520e80d0ee14d8"
INVALIDATED_A0F3E7_BUNDLE_ID = (
    "a0f3e7d93df3d8c2a596a231c21ff69a2361ef0c2ffd3f98e705452432329a61"
)
COUNTER_COVERAGE_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_1093df_collect_only_counter_coverage_invalidation_20260731.json"
)
if not COUNTER_COVERAGE_INVALIDATION_PATH.is_file():
    COUNTER_COVERAGE_INVALIDATION_PATH = (
        ROOT / "preflight/mysql_job_v32_1093df_collect_only_counter_coverage_invalidation_20260731.json"
    )
COUNTER_COVERAGE_INVALIDATION_SHA256 = "8b2029d56a3718b381d856a5d548b2da268bc208a215be21e2f01cc4895eb39f"
INVALIDATED_COUNTER_COVERAGE_BUNDLE_ID = (
    "1093dfde50bff6aef6ee30559d290a7aa59a54a462313c89a3763546d10ddb1e"
)
SHOW_STATUS_NONINTEGER_INVALIDATION_PATH = BUNDLE_AUTHORITY_ROOT / (
    "mysql_job_v32_0626b1_show_status_noninteger_invalidation_20260731.json"
)
if not SHOW_STATUS_NONINTEGER_INVALIDATION_PATH.is_file():
    SHOW_STATUS_NONINTEGER_INVALIDATION_PATH = (
        ROOT / "preflight/mysql_job_v32_0626b1_show_status_noninteger_invalidation_20260731.json"
    )
SHOW_STATUS_NONINTEGER_INVALIDATION_SHA256 = "7f91a9960ac8f5406a878b42ed56a8b3c3cc576e942125552ef9162b45e2d6de"
INVALIDATED_SHOW_STATUS_NONINTEGER_BUNDLE_ID = (
    "0626b1d59f5bbf18dbf86aa354e27cdaba901054b32d7158e545c16189e6dc95"
)
PYTHON_MANIFEST_RELATIVE = (
    "runtime/python_runtime_tree_manifest_v3.json"
)
PYTHON_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
PYTHON_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_EXECUTABLE_RELATIVE = "bin/python3.11"
PYTHON_EXECUTABLE_SHA256 = (
    "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
)
MYSQL_BINARY_IN_CLIENT = "/usr/bin/mysql"

SCENARIO = "job_memory"
HOSTNAME = "node02"
DEVICE = "/dev/nvme1n1"
MAJOR_MINOR = "259:3"
MOUNT = "/mnt/mysql_nvme1n1"
TEMPLATE = (
    "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template"
)
RUNTIME_ROOT = (
    "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
    "runtime_algorithm_c_successor_20260729"
)
MYSQL_PORT = 65102
DB_ENVELOPE = {
    "cpu_quota_cores": 1.15,
    "cpu_period_us": 100000,
    "cpu_quota_us": 115000,
    "cpuset_cpus": "18-20,54-56",
    "cpuset_mems": "1",
    "memory_bytes": 4294967296,
    "memory_swap_bytes": 4294967296,
    "pids_max": 512,
    "io_read_bps": 7340032,
    "io_write_bps": 7340032,
    "innodb_buffer_pool_bytes": 3221225472,
}
CLIENT_ENVELOPE = {
    "cpu_quota_cores": 6,
    "cpu_period_us": 100000,
    "cpu_quota_us": 600000,
    "cpuset_cpus": "0-2,36-38",
    "cpuset_mems": "0",
    "memory_bytes": 2147483648,
    "memory_swap_bytes": 2147483648,
    "pids_max": 256,
    "tmpfs_bytes": 1073741824,
}
HOST_MEASUREMENT_TMPFS_ROOT = "/dev/shm/ra_mysql_job_v2"
CLIENT_RUNTIME_UID = 1008
CLIENT_RUNTIME_GID = 1008
CLIENT_RUNTIME_USER = "%d:%d" % (CLIENT_RUNTIME_UID, CLIENT_RUNTIME_GID)
CLIENT_MYSQL_DATADIR_MASK_RELATIVE = "qualification/client_mysql_datadir_mask"
ATTEMPT_RE = re.compile(r"job-v2-[a-z0-9][a-z0-9_.-]{24,95}$")
HEX64_RE = re.compile(r"[0-9a-f]{64}$")
CONTAINER_RE = re.compile(r"ra_mysql_job_v2_[a-z0-9_]{8,80}_(?:db|client)$")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}$")
ROOT_LOGICAL_SOCKET_NAME = "mysql.sock"
ROOT_LOGICAL_SOCKET_TARGET = "/var/run/mysqld/mysqld.sock"
TREE_IDENTITY_NORMALIZATION = {
    "schema_version": "mysql_job_tree_identity_normalization_v1",
    "excluded_root_logical_socket": {
        "path": ROOT_LOGICAL_SOCKET_NAME,
        "target": ROOT_LOGICAL_SOCKET_TARGET,
    },
    "other_transient_entries_rejected": True,
    "other_symlinks_rejected": True,
    "path_type_size_sha256_content_identity": True,
    "ownership_and_mode_excluded_from_content_identity": True,
}
MYSQL_DATADIR_UID = 999
MYSQL_DATADIR_GID = 999
MYSQL_DATADIR_DIRECTORY_MODE = "0700"
MYSQL_DATADIR_FILE_MODE = "0600"
BOOTSTRAP_PASSWORD_RE = re.compile(r"[A-Za-z0-9_-]{40,80}$")


class AuthorityError(RuntimeError):
    pass


def canonical(value):
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


def _target_permission_normalization_valid(value, file_count):
    if not isinstance(value, dict):
        return False
    return (
        value.get("schema_version")
        == "mysql_job_target_datadir_permission_normalization_v1"
        and value.get("target_only") is True
        and value.get("frozen_image_uid") == MYSQL_DATADIR_UID
        and value.get("frozen_image_gid") == MYSQL_DATADIR_GID
        and value.get("directory_mode") == MYSQL_DATADIR_DIRECTORY_MODE
        and value.get("regular_file_mode") == MYSQL_DATADIR_FILE_MODE
        and isinstance(value.get("directory_count"), int)
        and value["directory_count"] >= 1
        and value.get("regular_file_count") == file_count
        and value.get("socket_excluded_from_target") is True
        and HEX64_RE.fullmatch(
            str(value.get("source_metadata_sha256_before") or "")
        ) is not None
        and value.get("source_metadata_sha256_before")
        == value.get("source_metadata_sha256_after")
    )


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def load_object(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AuthorityError("%s is absent or symlinked: %s" % (label, path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AuthorityError("%s is invalid JSON: %s" % (label, exc))
    if not isinstance(value, dict):
        raise AuthorityError("%s must be a JSON object" % label)
    return value


def load_with_sidecar(path, label):
    path = Path(path)
    value = load_object(path, label)
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.is_symlink() or not sidecar.is_file():
        raise AuthorityError("%s SHA-256 sidecar is absent" % label)
    pieces = sidecar.read_text(encoding="ascii").split()
    if (
        len(pieces) != 2
        or pieces[0] != sha256(path)
        or pieces[1] != path.name
    ):
        raise AuthorityError("%s SHA-256 sidecar differs" % label)
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(
        ".%s.%d.%s.tmp" % (path.name, os.getpid(), secrets.token_hex(8))
    )
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(
        "%s  %s\n" % (sha_bytes(payload), path.name), encoding="ascii"
    )


def _require_exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise AuthorityError(
            "%s key set differs: %s"
            % (label, sorted(set(value or {}) ^ set(expected)))
        )


def _safe_absolute(raw, label):
    if not isinstance(raw, str) or not raw.startswith("/"):
        raise AuthorityError("%s must be absolute" % label)
    value = PurePosixPath(raw)
    if ".." in value.parts or "." in value.parts:
        raise AuthorityError("%s contains a path alias" % label)
    return value


def _under(path, parent, label):
    path_value = _safe_absolute(path, label)
    parent_value = _safe_absolute(parent, label + " parent")
    if path_value == parent_value or parent_value not in path_value.parents:
        raise AuthorityError("%s escapes its frozen parent" % label)
    return path_value


def _validate_contract():
    if sha256(CONTRACT_PATH) != CONTRACT_SHA256:
        raise AuthorityError("distributed-v2 contract identity differs")
    contract = load_object(CONTRACT_PATH, "distributed-v2 contract")
    job = (contract.get("scenarios") or {}).get("job") or {}
    if (
        contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("postgresql_mutation_prohibited") is not True
        or job.get("scenario") != SCENARIO
        or (job.get("host") or {}).get("hostname") != HOSTNAME
        or (job.get("storage") or {}).get("device") != DEVICE
        or (job.get("storage") or {}).get("expected_live_major_minor")
        != MAJOR_MINOR
        or (job.get("storage") or {}).get("template_path") != TEMPLATE
        or (job.get("storage") or {}).get("runtime_root") != RUNTIME_ROOT
        or job.get("resource_envelope") != {
            key: DB_ENVELOPE[key]
            for key in (
                "cpu_quota_cores",
                "cpuset_cpus",
                "cpuset_mems",
                "memory_bytes",
                "memory_swap_bytes",
                "pids_max",
                "io_read_bps",
                "io_write_bps",
                "innodb_buffer_pool_bytes",
            )
        }
        | {"mysql_port": MYSQL_PORT}
    ):
        raise AuthorityError("JOB fields differ from distributed-v2 contract")
    expected_client = job.get("client_resource_envelope") or {}
    for key, expected in (
        ("cpuset_cpus", CLIENT_ENVELOPE["cpuset_cpus"]),
        ("cpuset_mems", CLIENT_ENVELOPE["cpuset_mems"]),
        ("cpu_quota_cores", CLIENT_ENVELOPE["cpu_quota_cores"]),
        ("memory_bytes", CLIENT_ENVELOPE["memory_bytes"]),
        ("memory_swap_bytes", CLIENT_ENVELOPE["memory_swap_bytes"]),
        ("pids_max", CLIENT_ENVELOPE["pids_max"]),
        ("tmpfs_bytes", CLIENT_ENVELOPE["tmpfs_bytes"]),
    ):
        if expected_client.get(key) != expected:
            raise AuthorityError("JOB client envelope differs at %s" % key)
    return contract


def _validate_reconciliation():
    # The sidecar is authoritative if the file changes later.  The constant
    # additionally prevents a different reconciliation from being substituted
    # by a bundle builder.
    actual = sha256(RECONCILIATION_PATH)
    sidecar = RECONCILIATION_PATH.with_name(
        RECONCILIATION_PATH.name + ".sha256"
    )
    # The published bundle closes the authority JSON in bundle_manifest.json;
    # it intentionally does not carry a second mutable sidecar.  In a source
    # checkout retain the historical sidecar requirement.
    if not sidecar.is_file() and RECONCILIATION_PATH.parent != BUNDLE_AUTHORITY_ROOT:
        raise AuthorityError("JOB replay reconciliation sidecar is absent")
    sidecar_sha = (
        actual
        if RECONCILIATION_PATH.parent == BUNDLE_AUTHORITY_ROOT
        else sidecar.read_text(encoding="ascii").split()[0]
    )
    if actual != sidecar_sha or actual != RECONCILIATION_SHA256:
        raise AuthorityError("JOB replay reconciliation sidecar differs")
    value = load_object(RECONCILIATION_PATH, "JOB replay reconciliation")
    if (
        value.get("status") != "RECOVERY_AUTHORITY_RECONCILED_NO_RERUN"
        or value.get("replay_authority_closed") is not True
        or value.get("rerun_authorized") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("completed_nonempty_subsets") != 15
        or value.get("sealed_representative_ranks") != [1, 2, 3, 4, 5]
        or value.get("replayed_candidate_ranks") != [1, 2, 3, 4, 5]
    ):
        raise AuthorityError("JOB replay reconciliation semantics differ")
    ranks = value.get("rank_records")
    if (
        not isinstance(ranks, list)
        or len(ranks) != 5
        or [row.get("rank") for row in ranks] != [1, 2, 3, 4, 5]
        or any(
            row.get("measurement_complete") is not True
            or row.get("recovery_verified") is not True
            or row.get("sealed_candidate_identity_verified") is not True
            or not isinstance(row.get("attempt_token"), str)
            for row in ranks
        )
    ):
        raise AuthorityError("JOB reconciliation rank closure differs")
    return value, actual


def _validate_f058_invalidation():
    if sha256(F058_INVALIDATION_PATH) != F058_INVALIDATION_SHA256:
        raise AuthorityError("f058 invalidation identity differs")
    value = load_object(F058_INVALIDATION_PATH, "f058 invalidation")
    if (
        value.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or value.get("bundle_id") != INVALIDATED_F058_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "2ca38c0e241cb22c5c0ec7deb3484ef0d0f18cb39473a41846a664a1a78c3846"
        or value.get("failed_attempt_token")
        != INVALIDATED_F058_ATTEMPT_TOKEN
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("f058 invalidation semantics differ")
    return value


def _validate_8f_client_credentials_invalidation():
    if sha256(EIGHTF_INVALIDATION_PATH) != EIGHTF_INVALIDATION_SHA256:
        raise AuthorityError("8f client-credentials invalidation identity differs")
    value = load_object(EIGHTF_INVALIDATION_PATH, "8f client-credentials invalidation")
    if (
        value.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or value.get("bundle_id") != INVALIDATED_EIGHTF_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "057b56c3b830ccba0c63d8f7558806e9eca42eb6e62ca69d9786cb406c5dde1e"
        or value.get("failed_attempt_token") != INVALIDATED_EIGHTF_ATTEMPT_TOKEN
        or value.get("database_pid") != 6088
        or value.get("client_credentials_readable") is not False
        or value.get("workload_started") is not False
        or value.get("formal_r0_started") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("8f client-credentials invalidation semantics differ")
    return value


def _validate_4ee_client_managed_mount_invalidation():
    if sha256(FOUREE_INVALIDATION_PATH) != FOUREE_INVALIDATION_SHA256:
        raise AuthorityError("4ee client-managed-mount invalidation identity differs")
    value = load_object(FOUREE_INVALIDATION_PATH, "4ee client-managed-mount invalidation")
    if (
        value.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or value.get("bundle_id") != INVALIDATED_FOUREE_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "275e7a8e4af4663ffe110738953cf040b9d4ebf165d55e6532aa1db50ff597ab"
        or value.get("failed_attempt_token") != INVALIDATED_FOUREE_ATTEMPT_TOKEN
        or value.get("failure_stage")
        != "bootstrap_live_readback_client_mount_validation"
        or value.get("workload_started") is not False
        or value.get("formal_r0_started") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("4ee client-managed-mount invalidation semantics differ")
    return value


def _validate_f1c705_client_anonymous_volume_invalidation():
    if sha256(F1C705_INVALIDATION_PATH) != F1C705_INVALIDATION_SHA256:
        raise AuthorityError("f1c705 anonymous-volume invalidation identity differs")
    value = load_object(
        F1C705_INVALIDATION_PATH, "f1c705 anonymous-volume invalidation"
    )
    if (
        value.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or value.get("bundle_id") != INVALIDATED_F1C705_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "b4eaab902f18aed7b78df61a9c375391640314e2448d2c9d577841f4d6c22d27"
        or value.get("failed_attempt_token") != INVALIDATED_F1C705_ATTEMPT_TOKEN
        or value.get("failure_stage")
        != "bootstrap_live_readback_client_mount_validation"
        or value.get("workload_started") is not False
        or value.get("formal_r0_started") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("f1c705 anonymous-volume invalidation semantics differ")
    return value


def _validate_7e08bd_client_bridge_identity_invalidation():
    if sha256(SEVENEO8_INVALIDATION_PATH) != SEVENEO8_INVALIDATION_SHA256:
        raise AuthorityError("7e08bd bridge-identity invalidation identity differs")
    value = load_object(
        SEVENEO8_INVALIDATION_PATH, "7e08bd bridge-identity invalidation"
    )
    if (
        value.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or value.get("bundle_id") != INVALIDATED_SEVENEO8_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "b34b06d71ac479e73891fe353ef467a1d7d0eaa155f1a02e985f5e0590c31afb"
        or value.get("failed_attempt_token") != INVALIDATED_SEVENEO8_ATTEMPT_TOKEN
        or value.get("failure_stage")
        != "bootstrap_collect_only_database_counter_bridge_identity"
        or value.get("workload_started") is not False
        or value.get("formal_r0_started") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("7e08bd bridge-identity invalidation semantics differ")
    return value


def _validate_2fe2ae_transient_inspect_hash_invalidation():
    if sha256(TWOFEE_INVALIDATION_PATH) != TWOFEE_INVALIDATION_SHA256:
        raise AuthorityError("2fe2ae inspect-hash invalidation identity differs")
    value = load_object(
        TWOFEE_INVALIDATION_PATH, "2fe2ae inspect-hash invalidation"
    )
    if (
        value.get("status") != "INVALIDATED_DO_NOT_LAUNCH"
        or value.get("bundle_id") != INVALIDATED_TWOFEE_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "e3b5c4b7af1181479fb6357e904332cf3f82c8a3f70d0098e6ff6131171c7458"
        or value.get("failed_attempt_token") != INVALIDATED_TWOFEE_ATTEMPT_TOKEN
        or value.get("failure_stage")
        != "bootstrap_collect_only_database_counter_bridge_identity"
        or value.get("workload_started") is not False
        or value.get("formal_r0_started") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("2fe2ae inspect-hash invalidation semantics differ")
    return value


def _validate_a0f3e7_client_unknown_user_invalidation():
    if sha256(A0F3E7_INVALIDATION_PATH) != A0F3E7_INVALIDATION_SHA256:
        raise AuthorityError("a0f3e7 UNKNOWN_USER invalidation identity differs")
    value = load_object(A0F3E7_INVALIDATION_PATH, "a0f3e7 UNKNOWN_USER invalidation")
    if (
        value.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or value.get("bundle_id") != INVALIDATED_A0F3E7_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "ddf294f015aa130f6ced8367e707bbe01acc8a82fa475427ed6059f53e965f42"
        or value.get("observed", {}).get("accepted_rounds") != 0
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_launch_authorized") is not False
        or value.get("formal_r0_authorized") is not False
    ):
        raise AuthorityError("a0f3e7 UNKNOWN_USER invalidation semantics differ")
    return value


def _validate_counter_coverage_invalidation():
    if sha256(COUNTER_COVERAGE_INVALIDATION_PATH) != COUNTER_COVERAGE_INVALIDATION_SHA256:
        raise AuthorityError("counter-coverage invalidation identity differs")
    value = load_object(COUNTER_COVERAGE_INVALIDATION_PATH, "counter-coverage invalidation")
    if (
        value.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or value.get("bundle_id") != INVALIDATED_COUNTER_COVERAGE_BUNDLE_ID
        or value.get("bundle_manifest_sha256")
        != "bd13ab21ce0420056fbfdb421f1683a4b686486fbd38d0fa87b899c299a86e9f"
        or value.get("observed", {}).get("accepted_rounds") != 0
        or value.get("observed", {}).get("r0_credit") != 0
        or value.get("accepted_rounds_credit") != 0
    ):
        raise AuthorityError("counter-coverage invalidation semantics differ")
    return value


def _validate_show_status_noninteger_invalidation():
    if sha256(SHOW_STATUS_NONINTEGER_INVALIDATION_PATH) != SHOW_STATUS_NONINTEGER_INVALIDATION_SHA256:
        raise AuthorityError("show-status noninteger invalidation identity differs")
    value = load_object(SHOW_STATUS_NONINTEGER_INVALIDATION_PATH, "show-status noninteger invalidation")
    if (
        value.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or value.get("bundle_id") != INVALIDATED_SHOW_STATUS_NONINTEGER_BUNDLE_ID
        or value.get("bundle_manifest_sha256") != "bfca72338327d6394a9c6bbbd1b4cb875ddca172cf63878f78cf296326899b83"
        or value.get("observed", {}).get("accepted_rounds") != 0
        or value.get("observed", {}).get("r0_credit") != 0
        or value.get("accepted_rounds_credit") != 0
    ):
        raise AuthorityError("show-status noninteger invalidation semantics differ")
    return value


def validate_spec(spec):
    _validate_contract()
    _, reconciliation_sha = _validate_reconciliation()
    _validate_f058_invalidation()
    _validate_8f_client_credentials_invalidation()
    _validate_4ee_client_managed_mount_invalidation()
    _validate_f1c705_client_anonymous_volume_invalidation()
    _validate_7e08bd_client_bridge_identity_invalidation()
    _validate_2fe2ae_transient_inspect_hash_invalidation()
    _validate_a0f3e7_client_unknown_user_invalidation()
    _validate_counter_coverage_invalidation()
    _validate_show_status_noninteger_invalidation()
    _require_exact_keys(
        spec,
        {
            "schema_version",
            "formal_scope",
            "cross_engine_comparison_prohibited",
            "postgresql_mutation_prohibited",
            "scenario",
            "host_id",
            "attempt_token",
            "bundle",
            "contract_sha256",
            "reconciliation_sha256",
            "mysql_image",
            "client_image",
            "paths",
            "containers",
            "database_counter_bridge",
            "python_runtime",
            "mysql_defaults_file",
            "collector",
            "created_at_utc",
            "expires_at_utc",
            "main_agent_token_sha256",
            "qualification_only",
            "capture_authorized",
            "formal_r0_authorized",
            "formal_authorized",
            "accepted_round_authorized",
        },
        "bootstrap spec",
    )
    if (
        spec["schema_version"] != "mysql_job_distributed_v2_bootstrap_spec_v1"
        or spec["formal_scope"] != "mysql_only"
        or spec["cross_engine_comparison_prohibited"] is not True
        or spec["postgresql_mutation_prohibited"] is not True
        or spec["scenario"] != SCENARIO
        or spec["host_id"] != HOSTNAME
        or spec["contract_sha256"] != CONTRACT_SHA256
        or spec["reconciliation_sha256"] != reconciliation_sha
        or spec["qualification_only"] is not True
        or spec["capture_authorized"] is not False
        or spec["formal_r0_authorized"] is not False
        or spec["formal_authorized"] is not False
        or spec["accepted_round_authorized"] is not False
    ):
        raise AuthorityError("bootstrap scope or authority boundary differs")
    attempt = spec["attempt_token"]
    if not isinstance(attempt, str) or ATTEMPT_RE.fullmatch(attempt) is None:
        raise AuthorityError("bootstrap attempt token is invalid")
    bundle = spec["bundle"]
    _require_exact_keys(
        bundle, {"path", "bundle_id", "manifest_sha256"}, "bundle binding"
    )
    _under(bundle["path"], RUNTIME_ROOT + "/bundles", "bundle path")
    if (
        not isinstance(bundle["bundle_id"], str)
        or HEX64_RE.fullmatch(bundle["bundle_id"]) is None
        or PurePosixPath(bundle["path"]).name != bundle["bundle_id"]
        or HEX64_RE.fullmatch(str(bundle["manifest_sha256"])) is None
    ):
        raise AuthorityError("bundle content address is invalid")
    if (
        bundle["bundle_id"] == INVALIDATED_F058_BUNDLE_ID
        or attempt == INVALIDATED_F058_ATTEMPT_TOKEN
    ):
        raise AuthorityError("f058 bundle or failed attempt is invalidated")
    if (
        bundle["bundle_id"] == INVALIDATED_EIGHTF_BUNDLE_ID
        or attempt == INVALIDATED_EIGHTF_ATTEMPT_TOKEN
    ):
        raise AuthorityError("8f client-credentials bundle or failed attempt is invalidated")
    if (
        bundle["bundle_id"] == INVALIDATED_FOUREE_BUNDLE_ID
        or attempt == INVALIDATED_FOUREE_ATTEMPT_TOKEN
    ):
        raise AuthorityError("4ee client-managed-mount bundle or failed attempt is invalidated")
    if (
        bundle["bundle_id"] == INVALIDATED_F1C705_BUNDLE_ID
        or attempt == INVALIDATED_F1C705_ATTEMPT_TOKEN
    ):
        raise AuthorityError("f1c705 anonymous-volume bundle or failed attempt is invalidated")
    if (
        bundle["bundle_id"] == INVALIDATED_SEVENEO8_BUNDLE_ID
        or attempt == INVALIDATED_SEVENEO8_ATTEMPT_TOKEN
    ):
        raise AuthorityError("7e08bd bridge-identity bundle or failed attempt is invalidated")
    if (
        bundle["bundle_id"] == INVALIDATED_TWOFEE_BUNDLE_ID
        or attempt == INVALIDATED_TWOFEE_ATTEMPT_TOKEN
    ):
        raise AuthorityError("2fe2ae inspect-hash bundle or failed attempt is invalidated")
    if bundle["bundle_id"] == INVALIDATED_A0F3E7_BUNDLE_ID:
        raise AuthorityError("a0f3e7 UNKNOWN_USER bundle is invalidated")
    if bundle["bundle_id"] == INVALIDATED_COUNTER_COVERAGE_BUNDLE_ID:
        raise AuthorityError("1093df counter-coverage bundle is invalidated")
    if bundle["bundle_id"] == INVALIDATED_SHOW_STATUS_NONINTEGER_BUNDLE_ID:
        raise AuthorityError("0626b1 SHOW STATUS parser bundle is invalidated")
    for label in ("mysql_image", "client_image"):
        image = spec[label]
        _require_exact_keys(image, {"reference", "image_id"}, label)
        if (
            not isinstance(image["reference"], str)
            or not image["reference"]
            or IMAGE_ID_RE.fullmatch(str(image["image_id"])) is None
        ):
            raise AuthorityError("%s is not digest-bound" % label)
    paths = spec["paths"]
    _require_exact_keys(
        paths,
        {
            "template",
            "runtime_root",
            "attempt_root",
            "datadir",
            "credentials_dir",
            "host_measurement_tmpfs",
            "python_runtime_root",
        },
        "path binding",
    )
    expected_attempt_root = RUNTIME_ROOT + "/attempts/" + attempt
    if (
        paths["template"] != TEMPLATE
        or paths["runtime_root"] != RUNTIME_ROOT
        or paths["attempt_root"] != expected_attempt_root
        or paths["datadir"] != expected_attempt_root + "/datadir"
        or paths["credentials_dir"] != expected_attempt_root + "/credentials"
        or paths["host_measurement_tmpfs"]
        != HOST_MEASUREMENT_TMPFS_ROOT + "/" + attempt
    ):
        raise AuthorityError("bootstrap path identity differs")
    _under(paths["python_runtime_root"], RUNTIME_ROOT + "/runtime", "Python root")
    containers = spec["containers"]
    _require_exact_keys(containers, {"database", "client"}, "container binding")
    suffix = re.sub(r"[^a-z0-9]", "_", attempt.lower())[:48]
    expected_names = {
        "database": "ra_mysql_job_v2_%s_db" % suffix,
        "client": "ra_mysql_job_v2_%s_client" % suffix,
    }
    if containers != expected_names or any(
        CONTAINER_RE.fullmatch(name) is None for name in containers.values()
    ):
        raise AuthorityError("container names are not attempt-derived")
    bridge = spec["database_counter_bridge"]
    _require_exact_keys(
        bridge,
        {
            "kind",
            "client_container",
            "mysql_binary_in_client",
            "stdout_only",
            "docker_socket_mount_prohibited",
            "target_device_write_prohibited",
        },
        "database counter bridge",
    )
    python_runtime = spec["python_runtime"]
    _require_exact_keys(
        python_runtime,
        {
            "manifest_relative",
            "manifest_sha256",
            "tree_content_sha256",
            "executable_relative",
            "executable_path",
            "executable_sha256",
        },
        "Python runtime binding",
    )
    if (
        bridge
        != {
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "client_container": containers["client"],
            "mysql_binary_in_client": MYSQL_BINARY_IN_CLIENT,
            "stdout_only": True,
            "docker_socket_mount_prohibited": True,
            "target_device_write_prohibited": True,
        }
        or python_runtime
        != {
            "manifest_relative": PYTHON_MANIFEST_RELATIVE,
            "manifest_sha256": PYTHON_MANIFEST_SHA256,
            "tree_content_sha256": PYTHON_TREE_CONTENT_SHA256,
            "executable_relative": PYTHON_EXECUTABLE_RELATIVE,
            "executable_path": (
                paths["python_runtime_root"]
                + "/"
                + PYTHON_EXECUTABLE_RELATIVE
            ),
            "executable_sha256": PYTHON_EXECUTABLE_SHA256,
        }
        or spec["mysql_defaults_file"]
        != expected_attempt_root + "/credentials/mysql.cnf"
        or spec["collector"]
        != bundle["path"]
        + "/qualification/mysql_job_distributed_live_readback_qualification_v2.py"
        or HEX64_RE.fullmatch(str(spec["main_agent_token_sha256"])) is None
    ):
        raise AuthorityError("tool or main-agent authority binding differs")
    try:
        created = datetime.fromisoformat(
            str(spec["created_at_utc"]).replace("Z", "+00:00")
        )
        expires = datetime.fromisoformat(
            str(spec["expires_at_utc"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise AuthorityError("bootstrap validity window is invalid: %s" % exc)
    if (
        created.tzinfo is None
        or expires.tzinfo is None
        or expires <= created
        or (expires - created).total_seconds() > 3600
        or datetime.now(timezone.utc) >= expires.astimezone(timezone.utc)
    ):
        raise AuthorityError("bootstrap validity window is stale or too broad")
    return spec


def command_plan(spec):
    """Return the exact mutation plan without executing it."""
    spec = validate_spec(spec)
    paths = spec["paths"]
    bundle = spec["bundle"]["path"]
    attempt = spec["attempt_token"]
    labels = [
        "--label",
        "ra.owner=mysql_algorithm_c_job_v2",
        "--label",
        "ra.scenario=%s" % SCENARIO,
        "--label",
        "ra.attempt=%s" % attempt,
        "--label",
        "ra.bundle_id=%s" % spec["bundle"]["bundle_id"],
    ]
    copy_stage = paths["datadir"] + ".stage"
    bootstrap_name = spec["containers"]["database"] + "_bootstrap"
    client_mysql_datadir_mask = (
        bundle + "/" + CLIENT_MYSQL_DATADIR_MASK_RELATIVE
    )
    copy_argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        "0.5",
        "--cpuset-cpus",
        "18",
        "--cpuset-mems",
        "1",
        "--memory",
        "536870912",
        "--memory-swap",
        "536870912",
        "--pids-limit",
        "128",
        "--mount",
        "type=bind,src=%s,dst=/template,readonly" % TEMPLATE,
        "--mount",
        "type=bind,src=%s,dst=%s"
        % (paths["attempt_root"], paths["attempt_root"]),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly" % (bundle, bundle),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly"
        % (paths["python_runtime_root"], paths["python_runtime_root"]),
        "--entrypoint",
        spec["python_runtime"]["executable_path"],
        spec["client_image"]["image_id"],
        bundle + "/qualification/mysql_job_physical_copy_v1.py",
        "--source",
        "/template",
        "--destination",
        copy_stage,
    ]
    bootstrap_argv = [
        "docker",
        "run",
        "-d",
        "--name",
        bootstrap_name,
        "--network",
        "none",
        "--cpus",
        "1",
        "--cpuset-cpus",
        "18",
        "--cpuset-mems",
        "1",
        "--memory",
        "2147483648",
        "--memory-swap",
        "2147483648",
        "--pids-limit",
        "128",
        "--mount",
        "type=bind,src=%s,dst=/var/lib/mysql" % paths["datadir"],
        spec["mysql_image"]["image_id"],
        "mysqld",
        "--skip-grant-tables=ON",
        "--skip-networking=ON",
        "--innodb-buffer-pool-size=536870912",
        "--innodb-flush-method=O_DIRECT",
        "--skip-log-bin",
        "--max-connections=32",
    ]
    db_argv = [
        "docker",
        "run",
        "-d",
        "--name",
        spec["containers"]["database"],
        *labels,
        "--cpu-period",
        str(DB_ENVELOPE["cpu_period_us"]),
        "--cpu-quota",
        str(DB_ENVELOPE["cpu_quota_us"]),
        "--cpuset-cpus",
        DB_ENVELOPE["cpuset_cpus"],
        "--cpuset-mems",
        DB_ENVELOPE["cpuset_mems"],
        "--memory",
        str(DB_ENVELOPE["memory_bytes"]),
        "--memory-swap",
        str(DB_ENVELOPE["memory_swap_bytes"]),
        "--pids-limit",
        str(DB_ENVELOPE["pids_max"]),
        "--device-read-bps",
        "%s:%d" % (DEVICE, DB_ENVELOPE["io_read_bps"]),
        "--device-write-bps",
        "%s:%d" % (DEVICE, DB_ENVELOPE["io_write_bps"]),
        "--publish",
        "127.0.0.1:%d:3306" % MYSQL_PORT,
        "--mount",
        "type=bind,src=%s,dst=/var/lib/mysql" % paths["datadir"],
        "--mount",
        "type=bind,src=%s,dst=/run/ra-credentials,readonly"
        % paths["credentials_dir"],
        spec["mysql_image"]["image_id"],
        "mysqld",
        "--bind-address=0.0.0.0",
        "--innodb-buffer-pool-size=%d"
        % DB_ENVELOPE["innodb_buffer_pool_bytes"],
        "--innodb-flush-method=O_DIRECT",
        "--innodb-flush-log-at-trx-commit=2",
        "--skip-log-bin",
        "--max-connections=96",
        "--performance-schema=ON",
    ]
    client_argv = [
        "docker",
        "run",
        "-d",
        "--name",
        spec["containers"]["client"],
        *labels,
        "--network",
        "host",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--log-driver",
        "none",
        "--cpu-period",
        str(CLIENT_ENVELOPE["cpu_period_us"]),
        "--cpu-quota",
        str(CLIENT_ENVELOPE["cpu_quota_us"]),
        "--cpuset-cpus",
        CLIENT_ENVELOPE["cpuset_cpus"],
        "--cpuset-mems",
        CLIENT_ENVELOPE["cpuset_mems"],
        "--memory",
        str(CLIENT_ENVELOPE["memory_bytes"]),
        "--memory-swap",
        str(CLIENT_ENVELOPE["memory_swap_bytes"]),
        "--pids-limit",
        str(CLIENT_ENVELOPE["pids_max"]),
        "--user",
        CLIENT_RUNTIME_USER,
        "--mount",
        "type=bind,src=%s,dst=/measurement" % paths["host_measurement_tmpfs"],
        "--mount",
        "type=bind,src=%s,dst=%s,readonly" % (bundle, bundle),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly"
        % (paths["python_runtime_root"], paths["python_runtime_root"]),
        "--mount",
        "type=bind,src=%s,dst=%s,readonly"
        % (paths["credentials_dir"], paths["credentials_dir"]),
        "--mount",
        "type=bind,src=%s,dst=/var/lib/mysql,readonly"
        % client_mysql_datadir_mask,
        "--entrypoint",
        "/bin/sh",
        spec["client_image"]["image_id"],
        "-ceu",
        "exec sleep 2147483647",
    ]
    collect_argv = [
        spec["python_runtime"]["executable_path"],
        spec["collector"],
        "--collect-only",
        "--bundle",
        bundle,
        "--container",
        spec["containers"]["database"],
        "--mysql-client-container",
        spec["database_counter_bridge"]["client_container"],
        "--mysql-binary-in-client",
        spec["database_counter_bridge"]["mysql_binary_in_client"],
        "--mysql-defaults-file",
        spec["mysql_defaults_file"],
        "--output",
        paths["host_measurement_tmpfs"] + "/live_readback.json",
    ]
    return {
        "schema_version": "mysql_job_distributed_v2_bootstrap_plan_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_token": attempt,
        "bundle_id": spec["bundle"]["bundle_id"],
        "qualification_only": True,
        "accepted_round_authorized": False,
        "copy": {
            "argv": copy_argv,
            "source": TEMPLATE,
            "staging_destination": copy_stage,
            "final_destination": paths["datadir"],
            "copy_mode": "userspace_read_write_plus_fiemap_nonsharing_v1",
            "atomic_rename_required": True,
            "cp_used": False,
            "reflink_api_used": False,
            "fiemap_nonsharing_required": True,
        },
        "bootstrap_database": {"name": bootstrap_name, "argv": bootstrap_argv},
        "database": {"name": spec["containers"]["database"], "argv": db_argv},
        "client": {"name": spec["containers"]["client"], "argv": client_argv},
        "collect_only": {"argv": collect_argv},
        "preflight": {
            "python_executable": spec["python_runtime"]["executable_path"],
            "python_manifest":
                bundle + "/" + spec["python_runtime"]["manifest_relative"],
            "python_tree_helper":
                bundle + "/qualification/runtime_tree_content_identity_v3.py",
            "client_sql_bridge_container":
                spec["database_counter_bridge"]["client_container"],
            "client_sql_bridge_binary":
                spec["database_counter_bridge"]["mysql_binary_in_client"],
            "host_mysql_cli_required": False,
            "docker_socket_mount_authorized": False,
        },
    }


def verify_bundle_closure(bundle_path, expected_bundle_id, expected_manifest_sha):
    bundle_path = Path(bundle_path)
    manifest_path = bundle_path / "bundle_manifest.json"
    if (
        bundle_path.is_symlink()
        or not bundle_path.is_dir()
        or bundle_path.name != expected_bundle_id
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or sha256(manifest_path) != expected_manifest_sha
    ):
        raise AuthorityError("bundle live identity differs")
    manifest = load_object(manifest_path, "bundle manifest")
    files = manifest.get("files")
    if (
        manifest.get("bundle_id") != expected_bundle_id
        or not isinstance(files, dict)
        or not files
    ):
        raise AuthorityError("bundle manifest identity/closure differs")
    expected_paths = set()
    for relative, expected_sha in files.items():
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or ".." in PurePosixPath(relative).parts
            or HEX64_RE.fullmatch(str(expected_sha)) is None
        ):
            raise AuthorityError("bundle closure path/hash is invalid")
        path = bundle_path / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected_sha:
            raise AuthorityError("bundle closure differs at %s" % relative)
        expected_paths.add(relative)
    actual_paths = {
        str(path.relative_to(bundle_path))
        for path in bundle_path.rglob("*")
        if path.is_file()
        and str(path.relative_to(bundle_path)) != "bundle_manifest.json"
    }
    actual_paths.discard("bundle_manifest.json.sha256")
    if actual_paths != expected_paths:
        raise AuthorityError(
            "bundle exact entry set differs: %s"
            % sorted(actual_paths ^ expected_paths)
        )
    return manifest


def _inspect_config_identity(row):
    return sha_bytes(
        canonical(
            {
                "Config": row.get("Config"),
                "HostConfig": row.get("HostConfig"),
                "Mounts": row.get("Mounts"),
                "Image": row.get("Image"),
                "Created": row.get("Created"),
            }
        )
    )


def _expand_cpuset(value):
    result = set()
    for token in str(value).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            low, high = [int(item) for item in token.split("-", 1)]
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    return result


def _rate(rows, device):
    for row in rows or []:
        if isinstance(row, dict) and row.get("Path") == device:
            return int(row.get("Rate") or 0)
    return None


def _tmpfs_exact(host, destination, size):
    tmpfs = host.get("Tmpfs")
    if not isinstance(tmpfs, dict) or set(tmpfs) != {destination}:
        return False
    options = tmpfs.get(destination)
    if not isinstance(options, str):
        return False
    values = set(options.split(","))
    expected = {
        "rw", "nosuid", "nodev", "noexec", "size=%d" % size,
        "uid=%d" % CLIENT_RUNTIME_UID,
        "gid=%d" % CLIENT_RUNTIME_GID,
    }
    mode = {value for value in values if value.startswith("mode=")}
    return values - mode == expected and mode in ({"mode=700"}, {"mode=0700"})


def _host_measurement_tmpfs_readback(path):
    """Return the exact attempt-private host tmpfs identity or fail closed."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise AuthorityError("measurement host tmpfs path is absent or unsafe")
    raw = _run(
        ["findmnt", "-n", "-o", "FSTYPE,TARGET,OPTIONS", "--target", str(path)],
        timeout=30,
    ).stdout.strip().split(None, 2)
    if len(raw) != 3 or raw[0] != "tmpfs" or Path(raw[1]).resolve() != path.resolve():
        raise AuthorityError("measurement host tmpfs mount target differs")
    values = set(raw[2].split(","))
    sizes = [value.split("=", 1)[1] for value in values if value.startswith("size=")]
    if len(sizes) != 1:
        raise AuthorityError("measurement host tmpfs capacity is absent")
    match = re.fullmatch(r"([0-9]+)([kKmMgG]?)", sizes[0])
    if match is None:
        raise AuthorityError("measurement host tmpfs capacity syntax differs")
    scale = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[match.group(2).lower()]
    size_bytes = int(match.group(1)) * scale
    current = path.stat(follow_symlinks=False)
    if (
        size_bytes != CLIENT_ENVELOPE["tmpfs_bytes"]
        or not {"rw", "nosuid", "nodev", "noexec"}.issubset(values)
        or current.st_uid != CLIENT_RUNTIME_UID
        or current.st_gid != CLIENT_RUNTIME_GID
        or stat.S_IMODE(current.st_mode) != 0o700
    ):
        raise AuthorityError("measurement host tmpfs envelope differs")
    return {
        "source": str(path.resolve()), "destination": "/measurement",
        "filesystem": "tmpfs", "size_bytes": size_bytes,
        "uid": current.st_uid, "gid": current.st_gid, "mode": "0700",
        "mount_options": ["rw", "nosuid", "nodev", "noexec"],
    }


def validate_bootstrap_evidence(evidence, spec):
    """Verify exact DB/client identity, physical copy, and collect-only output."""
    spec = validate_spec(spec)
    _require_exact_keys(
        evidence,
        {
            "schema_version",
            "status",
            "formal_scope",
            "cross_engine_comparison_prohibited",
            "postgresql_mutation_prohibited",
            "scenario",
            "host_id",
            "attempt_token",
            "bundle_id",
            "contract_sha256",
            "reconciliation_sha256",
            "spec_sha256",
            "copy",
            "database",
            "client",
            "measurement_bind",
            "credential_binding",
            "collect_only",
            "preflight",
            "qualification_only",
            "capture_authorized",
            "formal_r0_authorized",
            "formal_authorized",
            "accepted_round_authorized",
        },
        "bootstrap evidence",
    )
    if (
        evidence["schema_version"]
        != "mysql_job_distributed_v2_bootstrap_evidence_v1"
        or evidence["status"]
        != "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY"
        or evidence["formal_scope"] != "mysql_only"
        or evidence["cross_engine_comparison_prohibited"] is not True
        or evidence["postgresql_mutation_prohibited"] is not True
        or evidence["scenario"] != SCENARIO
        or evidence["host_id"] != HOSTNAME
        or evidence["attempt_token"] != spec["attempt_token"]
        or evidence["bundle_id"] != spec["bundle"]["bundle_id"]
        or evidence["contract_sha256"] != CONTRACT_SHA256
        or evidence["reconciliation_sha256"] != spec["reconciliation_sha256"]
        or evidence["spec_sha256"] != sha_bytes(canonical(spec))
        or evidence["qualification_only"] is not True
        or evidence["capture_authorized"] is not False
        or evidence["formal_r0_authorized"] is not False
        or evidence["formal_authorized"] is not False
        or evidence["accepted_round_authorized"] is not False
    ):
        raise AuthorityError("bootstrap evidence identity/boundary differs")
    credential_binding = evidence["credential_binding"]
    measurement_bind = evidence["measurement_bind"]
    if measurement_bind != _host_measurement_tmpfs_readback(
        spec["paths"]["host_measurement_tmpfs"]
    ):
        raise AuthorityError("bootstrap host measurement bind readback differs")
    if (
        not isinstance(credential_binding, dict)
        or credential_binding != {
            "user": "root",
            "placeholder_free": True,
            "password_source": "random_attempt_secret_not_logged_or_hashed",
        }
    ):
        raise AuthorityError("bootstrap credential binding differs")
    preflight = evidence["preflight"]
    runtime_preflight = preflight.get("python_runtime") or {}
    sql_preflight = preflight.get("database_counter_bridge") or {}
    if (
        preflight.get("all_required_components_present") is not True
        or preflight.get("host_mysql_cli_required") is not False
        or preflight.get("accepted_round_authorized") is not False
        or runtime_preflight.get("status")
        != "PASS_MANIFEST_BOUND_EXACT_RUNTIME"
        or runtime_preflight.get("manifest_sha256")
        != spec["python_runtime"]["manifest_sha256"]
        or runtime_preflight.get("tree_content_sha256")
        != spec["python_runtime"]["tree_content_sha256"]
        or runtime_preflight.get("executable_path")
        != spec["python_runtime"]["executable_path"]
        or runtime_preflight.get("executable_sha256")
        != spec["python_runtime"]["executable_sha256"]
        or runtime_preflight.get("exact_entry_set_verified") is not True
        or sql_preflight.get("status")
        != "PASS_EXACT_CLIENT_DOCKER_EXEC_SQL_STDOUT_BRIDGE"
        or sql_preflight.get("kind")
        != spec["database_counter_bridge"]["kind"]
        or sql_preflight.get("client_container")
        != spec["containers"]["client"]
        or sql_preflight.get("mysql_binary_in_client")
        != MYSQL_BINARY_IN_CLIENT
        or sql_preflight.get("host_mysql_cli_required") is not False
        or sql_preflight.get("docker_socket_mounted") is not False
        or sql_preflight.get("target_datadir_mounted") is not False
        or sql_preflight.get("stdout_only") is not True
        or sql_preflight.get("execution_uid_gid")
        != [CLIENT_RUNTIME_UID, CLIENT_RUNTIME_GID]
        or sql_preflight.get("credentials_defaults_file")
        != spec["mysql_defaults_file"]
        or sql_preflight.get("credentials_readable") is not True
        or sql_preflight.get("measurement_writable") is not True
        or sql_preflight.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("bootstrap executable/SQL bridge preflight differs")
    copy = evidence["copy"]
    physical_copy = copy.get("physical_copy_evidence") or {}
    fiemap = physical_copy.get("fiemap") or {}
    if (
        copy.get("command")
        != "userspace_read_write_plus_fiemap_nonsharing_v1"
        or copy.get("overlay") is not False
        or copy.get("reflink") is not False
        or copy.get("full_physical_copy") is not True
        or copy.get("atomic_rename_complete") is not True
        or copy.get("source") != TEMPLATE
        or copy.get("destination") != spec["paths"]["datadir"]
        or copy.get("device_major_minor") != MAJOR_MINOR
        or copy.get("tree_identity_normalization")
        != TREE_IDENTITY_NORMALIZATION
        or copy.get("source_tree_sha256")
        != copy.get("destination_tree_sha256")
        or not isinstance(copy.get("file_count"), int)
        or copy["file_count"] <= 0
        or not isinstance(copy.get("byte_count"), int)
        or copy["byte_count"] <= 0
        or copy.get("source_root_inode") == copy.get("destination_root_inode")
        or physical_copy.get("status")
        != "PASS_BYTE_COPY_AND_FIEMAP_NONSHARING"
        or physical_copy.get("implementation")
        != "userspace_read_write_no_clone_no_copy_file_range_v1"
        or physical_copy.get("copy_api") != "os.read/os.write"
        or physical_copy.get("cp_used") is not False
        or physical_copy.get("reflink_api_used") is not False
        or physical_copy.get("copy_file_range_used") is not False
        or physical_copy.get("sendfile_used") is not False
        or physical_copy.get("tree_identity_normalization")
        != TREE_IDENTITY_NORMALIZATION
        or not _target_permission_normalization_valid(
            physical_copy.get("target_permission_normalization"),
            copy.get("file_count"),
        )
        or fiemap.get("fiemap_extent_shared_absent") is not True
        or fiemap.get(
            "source_destination_physical_extent_overlap_absent"
        )
        is not True
        or physical_copy.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("full physical copy evidence differs")
    database = evidence["database"]
    row = database.get("inspect")
    if not isinstance(row, dict):
        raise AuthorityError("database inspect is absent")
    host = row.get("HostConfig") or {}
    state = row.get("State") or {}
    mounts = row.get("Mounts") or []
    port_rows = ((host.get("PortBindings") or {}).get("3306/tcp") or [])
    if (
        row.get("Id") != database.get("container_id")
        or row.get("Image") != spec["mysql_image"]["image_id"]
        or state.get("Running") is not True
        or int(state.get("Pid") or 0) != int(database.get("pid") or 0)
        or int(database.get("pid") or 0) <= 0
        or int(row.get("RestartCount") or 0) != 0
        or int(host.get("CpuPeriod") or 0) != DB_ENVELOPE["cpu_period_us"]
        or int(host.get("CpuQuota") or 0) != DB_ENVELOPE["cpu_quota_us"]
        or _expand_cpuset(host.get("CpusetCpus"))
        != _expand_cpuset(DB_ENVELOPE["cpuset_cpus"])
        or _expand_cpuset(host.get("CpusetMems"))
        != _expand_cpuset(DB_ENVELOPE["cpuset_mems"])
        or int(host.get("Memory") or 0) != DB_ENVELOPE["memory_bytes"]
        or int(host.get("MemorySwap") or 0)
        != DB_ENVELOPE["memory_swap_bytes"]
        or int(host.get("PidsLimit") or 0) != DB_ENVELOPE["pids_max"]
        or _rate(host.get("BlkioDeviceReadBps"), DEVICE)
        != DB_ENVELOPE["io_read_bps"]
        or _rate(host.get("BlkioDeviceWriteBps"), DEVICE)
        != DB_ENVELOPE["io_write_bps"]
        or {
            (item.get("HostIp"), int(item.get("HostPort") or 0))
            for item in port_rows
        }
        != {("127.0.0.1", MYSQL_PORT)}
        or not any(
            item.get("Source") == spec["paths"]["datadir"]
            and item.get("Destination") == "/var/lib/mysql"
            and item.get("RW") is True
            for item in mounts
        )
    ):
        raise AuthorityError("database container exact envelope differs")
    client = evidence["client"]
    row = client.get("inspect")
    if not isinstance(row, dict):
        raise AuthorityError("client inspect is absent")
    host = row.get("HostConfig") or {}
    state = row.get("State") or {}
    observed_mounts = {
        (item.get("Source"), item.get("Destination"), item.get("RW"))
        for item in row.get("Mounts") or []
    }
    expected_mounts = {
        (spec["bundle"]["path"], spec["bundle"]["path"], False),
        (
            spec["paths"]["python_runtime_root"],
            spec["paths"]["python_runtime_root"],
            False,
        ),
        (
            spec["paths"]["credentials_dir"],
            spec["paths"]["credentials_dir"],
            False,
        ),
        (
            spec["bundle"]["path"]
            + "/"
            + CLIENT_MYSQL_DATADIR_MASK_RELATIVE,
            "/var/lib/mysql",
            False,
        ),
        (
            spec["paths"]["host_measurement_tmpfs"],
            "/measurement",
            True,
        ),
    }
    if (
        row.get("Id") != client.get("container_id")
        or row.get("Image") != spec["client_image"]["image_id"]
        or state.get("Running") is not True
        or int(state.get("Pid") or 0) != int(client.get("pid") or 0)
        or int(client.get("pid") or 0) <= 0
        or int(row.get("RestartCount") or 0) != 0
        or int(host.get("CpuPeriod") or 0) != CLIENT_ENVELOPE["cpu_period_us"]
        or int(host.get("CpuQuota") or 0) != CLIENT_ENVELOPE["cpu_quota_us"]
        or _expand_cpuset(host.get("CpusetCpus"))
        != _expand_cpuset(CLIENT_ENVELOPE["cpuset_cpus"])
        or _expand_cpuset(host.get("CpusetMems"))
        != _expand_cpuset(CLIENT_ENVELOPE["cpuset_mems"])
        or int(host.get("Memory") or 0) != CLIENT_ENVELOPE["memory_bytes"]
        or int(host.get("MemorySwap") or 0)
        != CLIENT_ENVELOPE["memory_swap_bytes"]
        or int(host.get("PidsLimit") or 0) != CLIENT_ENVELOPE["pids_max"]
        or (row.get("Config") or {}).get("User") != CLIENT_RUNTIME_USER
        or host.get("NetworkMode") != "host"
        or host.get("ReadonlyRootfs") is not True
        or (host.get("LogConfig") or {}).get("Type") != "none"
        or "ALL" not in (host.get("CapDrop") or [])
        or not any(
            str(item).replace("=", ":").lower()
            in ("no-new-privileges", "no-new-privileges:true")
            for item in (host.get("SecurityOpt") or [])
        )
        or host.get("Tmpfs") not in ({}, None)
        or not expected_mounts.issubset(observed_mounts)
        or sum(
            item.get("Destination") == "/var/lib/mysql"
            for item in row.get("Mounts") or []
        ) != 1
        or any(
            item.get("Source") == spec["paths"]["datadir"]
            for item in row.get("Mounts") or []
        )
    ):
        raise AuthorityError("client container exact envelope differs")
    collect = evidence["collect_only"]
    live = collect.get("document")
    if (
        collect.get("sha256") != sha_bytes(canonical(live))
        or not isinstance(live, dict)
        or live.get("status") != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
        or live.get("bundle_id") != spec["bundle"]["bundle_id"]
        or live.get("scenario") != SCENARIO
        or (live.get("container") or {}).get("container_id")
        != database["container_id"]
        or int((live.get("container") or {}).get("pid") or 0)
        != database["pid"]
        or live.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("collect-only evidence differs")
    return {
        "planner_container_name": spec["containers"]["database"],
        "planner_container_id": database["container_id"],
        "planner_pid": database["pid"],
        "planner_datadir": spec["paths"]["datadir"],
        "client_container_id": client["container_id"],
        "client_pid": client["pid"],
        "database_inspect_config_sha256": _inspect_config_identity(
            database["inspect"]
        ),
        "client_inspect_config_sha256": _inspect_config_identity(
            client["inspect"]
        ),
    }


def _same_planner(document, identity, label):
    planner = document.get("planner_identity")
    if not isinstance(planner, dict):
        raise AuthorityError("%s planner identity is absent" % label)
    expected = {
        "container_name": identity["planner_container_name"],
        "container_id": identity["planner_container_id"],
        "pid": identity["planner_pid"],
        "datadir": identity["planner_datadir"],
        "database_inspect_config_sha256":
            identity["database_inspect_config_sha256"],
    }
    if planner != expected:
        raise AuthorityError("%s changed the live planner identity" % label)


def _validate_dynamic_gate(dynamic_gate, spec):
    if (
        dynamic_gate.get("schema_version")
        != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or dynamic_gate.get("policy_version")
        != "mysql_algorithm_c_same_host_bidirectional_interference_gate_v2"
        or dynamic_gate.get("cpu_contention_policy_version")
        != "mysql_algorithm_c_cpu_contention_signal_v2"
        or dynamic_gate.get("cpu_contention_mode") not in (
            "psi_cpu_v1", "proc_stat_loadavg_fallback_v1"
        )
        or dynamic_gate.get("memory_pressure_mode") not in (
            "psi_memory_v1", "cgroup_memory_occupancy_fallback_v1"
        )
        or dynamic_gate.get("status") != "PASS"
        or dynamic_gate.get("scenario") != SCENARIO
        or dynamic_gate.get("host_id") != HOSTNAME
        or dynamic_gate.get("attempt_token") != spec["attempt_token"]
        or dynamic_gate.get("all_tolerances_passed") is not True
        or dynamic_gate.get("ongoing_monitor_required") is not True
        or dynamic_gate.get("database_counter_missing_samples") != 0
        or dynamic_gate.get("database_counter_timeout_count") != 0
        or dynamic_gate.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("JOB dynamic gate is incomplete")


def build_fresh_r0_launch_authority(spec, bootstrap, dynamic_gate):
    """Authorize only fresh R0 on the already-qualified live planner."""
    identity = validate_bootstrap_evidence(bootstrap, spec)
    _validate_dynamic_gate(dynamic_gate, spec)
    reconciliation, reconciliation_sha = _validate_reconciliation()
    now = datetime.now(timezone.utc)
    return {
        "schema_version":
            "mysql_job_distributed_v2_same_live_planner_handoff_v1",
        "status": "READY_FRESH_R0_SAME_LIVE_PLANNER",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "stage": "fresh_r0",
        "planner_identity": {
            "container_name": identity["planner_container_name"],
            "container_id": identity["planner_container_id"],
            "pid": identity["planner_pid"],
            "datadir": identity["planner_datadir"],
            "database_inspect_config_sha256":
                identity["database_inspect_config_sha256"],
        },
        "client_identity": {
            "container_name": spec["containers"]["client"],
            "container_id": identity["client_container_id"],
            "pid": identity["client_pid"],
            "inspect_config_sha256":
                identity["client_inspect_config_sha256"],
        },
        "new_campaign_or_planner_container_prohibited": True,
        "qualification_container_adoption_required": True,
        "ongoing_rolling_gate_required": True,
        "bootstrap_evidence_sha256": sha_bytes(canonical(bootstrap)),
        "dynamic_gate_sha256": sha_bytes(canonical(dynamic_gate)),
        "old_reconciliation_sha256": reconciliation_sha,
        "old_top5_rerun_authorized": False,
        "old_top5_attempt_tokens_denylist": sorted(
            row["attempt_token"] for row in reconciliation["rank_records"]
        ),
        "historical_accepted_round_credit": 0,
        "fresh_r0_authorized": True,
        "formal10_authorized": False,
        "target_accepted_rounds": 0,
        "next_accepted_round": None,
        "resume_old_outer_checkpoint": False,
        "capture_or_r0_or_debug_countable_as_accepted": False,
        "outer_acceptance_authorized": False,
        "accepted_round_authorized": False,
        "created_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=15)).isoformat(),
    }


def build_handoff_authority(
    spec,
    bootstrap,
    dynamic_gate,
    fresh_r0,
    debug_round=None,
    formal10=False,
):
    """Build a fail-closed stage authority without touching the data plane."""
    identity = validate_bootstrap_evidence(bootstrap, spec)
    _validate_dynamic_gate(dynamic_gate, spec)
    _same_planner(fresh_r0, identity, "fresh R0")
    if (
        fresh_r0.get("schema_version")
        != "mysql_job_distributed_v2_fresh_r0_evidence_v1"
        or fresh_r0.get("status") != "PASS_FRESH_R0"
        or fresh_r0.get("attempt_token") != spec["attempt_token"]
        or fresh_r0.get("bundle_id") != spec["bundle"]["bundle_id"]
        or fresh_r0.get("dynamic_gate_sha256")
        != sha_bytes(canonical(dynamic_gate))
        or fresh_r0.get("r0_profile_gate_passed") is not True
        or fresh_r0.get("database_counter_gate_passed") is not True
        or fresh_r0.get("stable_seconds") < 180
        or fresh_r0.get("sample_count") < 181
        or fresh_r0.get("accepted_rounds_credit") != 0
        or fresh_r0.get("accepted_round_authorized") is not False
    ):
        raise AuthorityError("fresh R0 evidence is incomplete")
    stage = "debug"
    if formal10:
        if not isinstance(debug_round, dict):
            raise AuthorityError("formal10 requires one complete debug round")
        _same_planner(debug_round, identity, "debug round")
        required_gradient_fields = {
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
        }
        events = debug_round.get("gradient_events")
        ranking = debug_round.get("global_candidate_ranking")
        if (
            debug_round.get("schema_version")
            != "mysql_job_distributed_v2_debug_round_evidence_v1"
            or debug_round.get("status") != "PASS_DEBUG_ROUND"
            or debug_round.get("attempt_token") != spec["attempt_token"]
            or debug_round.get("completed_nonempty_subsets") != 15
            or debug_round.get("sealed_top5_ranks") != [1, 2, 3, 4, 5]
            or debug_round.get("replayed_top5_ranks") != [1, 2, 3, 4, 5]
            or debug_round.get("fixed_top5_only") is not True
            or debug_round.get("gradient_event_schema_complete") is not True
            or debug_round.get("fingerprint_l1_l2_l3_complete") is not True
            or debug_round.get("accepted_rounds_credit") != 0
            or debug_round.get("algorithm_formula")
            != "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
            or debug_round.get("parameter_count") != 4
            or debug_round.get("candidate_count") != 15
            or debug_round.get("qps_role")
            != "outer_acceptance_after_real_replay_only"
            or not isinstance(events, list)
            or len(events) != 15
            or any(
                not isinstance(event, dict)
                or not required_gradient_fields.issubset(event)
                or not isinstance(event.get("subset_id"), str)
                or not event["subset_id"]
                or not isinstance(event.get("candidate_score"), (int, float))
                or isinstance(event.get("candidate_score"), bool)
                or not math.isfinite(float(event["candidate_score"]))
                for event in events
            )
            or len({event["subset_id"] for event in events}) != 15
            or not isinstance(ranking, list)
            or len(ranking) != 15
            or any(
                not isinstance(row, dict)
                or set(row) != {"rank", "subset_id", "candidate_score"}
                or row.get("rank") != index
                or not isinstance(row.get("candidate_score"), (int, float))
                or isinstance(row.get("candidate_score"), bool)
                or not math.isfinite(float(row["candidate_score"]))
                for index, row in enumerate(ranking, 1)
            )
            or any(
                float(left["candidate_score"])
                < float(right["candidate_score"])
                for left, right in zip(ranking, ranking[1:])
            )
            or [row["rank"] for row in ranking[:5]] != [1, 2, 3, 4, 5]
        ):
            raise AuthorityError("debug round is incomplete")
        stage = "formal10"
    reconciliation, reconciliation_sha = _validate_reconciliation()
    deny_tokens = sorted(
        row["attempt_token"] for row in reconciliation["rank_records"]
    )
    now = datetime.now(timezone.utc)
    return {
        "schema_version":
            "mysql_job_distributed_v2_same_live_planner_handoff_v1",
        "status": (
            "READY_FORMAL10_SAME_LIVE_PLANNER"
            if formal10
            else "READY_DEBUG_SAME_LIVE_PLANNER"
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "stage": stage,
        "planner_identity": {
            "container_name": identity["planner_container_name"],
            "container_id": identity["planner_container_id"],
            "pid": identity["planner_pid"],
            "datadir": identity["planner_datadir"],
            "database_inspect_config_sha256":
                identity["database_inspect_config_sha256"],
        },
        "client_identity": {
            "container_name": spec["containers"]["client"],
            "container_id": identity["client_container_id"],
            "pid": identity["client_pid"],
            "inspect_config_sha256":
                identity["client_inspect_config_sha256"],
        },
        "new_campaign_or_planner_container_prohibited": True,
        "qualification_container_adoption_required": True,
        "ongoing_rolling_gate_required": True,
        "fresh_r0_sha256": sha_bytes(canonical(fresh_r0)),
        "dynamic_gate_sha256": sha_bytes(canonical(dynamic_gate)),
        "debug_round_sha256": (
            sha_bytes(canonical(debug_round)) if debug_round is not None else None
        ),
        "old_reconciliation_sha256": reconciliation_sha,
        "old_top5_rerun_authorized": False,
        "old_top5_attempt_tokens_denylist": deny_tokens,
        "historical_accepted_round_credit": 0,
        "formal10_authorized": formal10,
        "target_accepted_rounds": 10 if formal10 else 0,
        "next_accepted_round": 1 if formal10 else None,
        "resume_old_outer_checkpoint": False,
        "capture_or_r0_or_debug_countable_as_accepted": False,
        "outer_acceptance_authorized": formal10,
        "accepted_round_authorized": False,
        "created_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=15)).isoformat(),
    }


def _redact_sensitive(value, sensitive_values):
    text = str(value)
    for secret in sensitive_values:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "<redacted>")
    return text


def _run(
    argv,
    timeout=300,
    check=True,
    stdin_text=None,
    sensitive_values=(),
):
    completed = subprocess.run(
        list(argv),
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        safe_argv = [
            _redact_sensitive(value, sensitive_values) for value in argv
        ]
        safe_stderr = _redact_sensitive(
            completed.stderr[-2000:], sensitive_values
        )
        raise AuthorityError(
            "command failed rc=%d: %r; stderr=%s"
            % (completed.returncode, safe_argv, safe_stderr)
        )
    return completed


def _bootstrap_account_sql(password):
    """Build account SQL without percent formatting or argv secret exposure."""
    if (
        not isinstance(password, str)
        or BOOTSTRAP_PASSWORD_RE.fullmatch(password) is None
    ):
        raise AuthorityError("generated bootstrap password shape differs")
    literal = "'" + password + "'"
    return (
        "FLUSH PRIVILEGES; "
        "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH "
        "caching_sha2_password BY " + literal + "; "
        "ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY "
        + literal + "; "
        "ALTER USER 'root'@'localhost' IDENTIFIED WITH "
        "caching_sha2_password BY " + literal + ";"
    )


def _apply_bootstrap_account_sql(bootstrap_name, password):
    sql = _bootstrap_account_sql(password)
    return _run(
        [
            "docker", "exec", "-i", bootstrap_name,
            "mysql", "-uroot", "-NB",
        ],
        stdin_text=sql,
        sensitive_values=(password,),
    )


def _write_client_defaults(defaults, password):
    """Materialize the one attempt-local client identity.

    The workload collector sometimes invokes mysql only with the defaults
    file; leaving ``user`` implicit therefore lets a host/image supplied
    default (including the historical ``UNKNOWN_USER`` placeholder) become
    the wire identity.  Keep the user, reset SQL target, and all later client
    invocations pinned to root for this isolated attempt.  The password is
    deliberately returned nowhere and is never written to evidence.
    """
    if (
        not isinstance(password, str)
        or BOOTSTRAP_PASSWORD_RE.fullmatch(password) is None
    ):
        raise AuthorityError("generated client password shape differs")
    payload = (
        "[client]\n"
        "user=root\n"
        "password=%s\n"
        "protocol=TCP\n"
        "ssl-mode=DISABLED\n"
        "get-server-public-key\n" % password
    )
    if "UNKNOWN_USER" in payload or "${" in payload or "{{" in payload:
        raise AuthorityError("client defaults contain an unresolved user placeholder")
    Path(defaults).write_text(payload, encoding="utf-8")


def _verify_client_defaults_binding(defaults):
    """Read back identity fields without returning or logging the secret."""
    path = Path(defaults)
    if path.is_symlink() or not path.is_file():
        raise AuthorityError("client defaults file is absent")
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip().lower()] = value.strip()
    if (
        values.get("user") != "root"
        or not values.get("password")
        or "UNKNOWN_USER" in path.read_text(encoding="utf-8")
        or any("${" in value or "{{" in value for value in values.values())
    ):
        raise AuthorityError("client defaults user binding differs")
    return {"user": "root", "placeholder_free": True}


def _inspect(name):
    result = _run(["docker", "inspect", name], timeout=30)
    try:
        rows = json.loads(result.stdout)
    except ValueError as exc:
        raise AuthorityError("docker inspect is invalid JSON: %s" % exc)
    if not isinstance(rows, list) or len(rows) != 1:
        raise AuthorityError("docker inspect cardinality differs")
    return rows[0]


def _wait_mysql(
    container, defaults_file=None, client_container=None, seconds=180
):
    for _ in range(seconds):
        if defaults_file is None:
            argv = [
                "docker",
                "exec",
                container,
                "mysqladmin",
                "-uroot",
                "ping",
                "--silent",
            ]
        else:
            if not client_container:
                raise AuthorityError(
                    "authenticated MySQL wait requires exact client container"
                )
            argv = [
                "docker",
                "exec",
                "--user",
                CLIENT_RUNTIME_USER,
                client_container,
                MYSQL_BINARY_IN_CLIENT,
                "--defaults-file=%s" % defaults_file,
                "--protocol=TCP",
                "--host=127.0.0.1",
                "--port=%d" % MYSQL_PORT,
                "--user=root",
                "--connect-timeout=3",
                "--ssl-mode=DISABLED",
                "--get-server-public-key",
                "-NBe",
                "SELECT 1",
            ]
        if _run(argv, timeout=10, check=False).returncode == 0:
            return
        time.sleep(1)
    raise AuthorityError("MySQL did not become ready")


def _runtime_preflight(spec):
    """Verify the exact runtime before creating any attempt state."""
    bundle = Path(spec["bundle"]["path"])
    binding = spec["python_runtime"]
    manifest_path = bundle / binding["manifest_relative"]
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or sha256(manifest_path) != binding["manifest_sha256"]
    ):
        raise AuthorityError("Python runtime manifest live identity differs")
    manifest = load_object(manifest_path, "Python runtime manifest")
    if (
        manifest.get("schema_version")
        != "mysql_algorithm_c_runtime_tree_manifest_v3"
        or manifest.get("algorithm")
        != "sha256_canonical_relative_tree_entries_v3"
        or manifest.get("tree_content_sha256")
        != binding["tree_content_sha256"]
        or (manifest.get("artifacts") or {}).get(
            binding["executable_relative"]
        )
        != binding["executable_sha256"]
    ):
        raise AuthorityError("Python runtime manifest semantics differ")
    executable = Path(binding["executable_path"])
    if (
        executable.is_symlink()
        or not executable.is_file()
        or sha256(executable) != binding["executable_sha256"]
    ):
        raise AuthorityError("manifest-bound Python executable differs")
    helper = (
        bundle / "qualification/runtime_tree_content_identity_v3.py"
    )
    if helper.is_symlink() or not helper.is_file():
        raise AuthorityError("Python runtime exact-tree helper is absent")
    completed = _run(
        [
            str(executable),
            str(helper),
            "--authority-manifest",
            str(manifest_path),
            "--runtime-root",
            spec["paths"]["python_runtime_root"],
        ],
        timeout=1800,
    )
    try:
        readback = json.loads(completed.stdout)
    except ValueError as exc:
        raise AuthorityError(
            "Python runtime exact-tree readback is invalid: %s" % exc
        )
    if (
        readback.get("status")
        != "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK"
        or readback.get("tree_content_sha256")
        != binding["tree_content_sha256"]
        or readback.get("exact_entry_set_verified") is not True
    ):
        raise AuthorityError("Python runtime exact-tree preflight failed")
    return {
        "status": "PASS_MANIFEST_BOUND_EXACT_RUNTIME",
        "manifest_path": str(manifest_path),
        "manifest_sha256": binding["manifest_sha256"],
        "tree_content_sha256": binding["tree_content_sha256"],
        "executable_path": str(executable),
        "executable_relative": binding["executable_relative"],
        "executable_sha256": binding["executable_sha256"],
        "exact_entry_set_verified": True,
        "accepted_round_authorized": False,
    }


def _client_sql_bridge_preflight(spec, client_inspect):
    bridge = spec["database_counter_bridge"]
    mounts = client_inspect.get("Mounts") or []
    if (
        client_inspect.get("Id") is None
        or str(client_inspect.get("Name") or "").lstrip("/")
        != bridge["client_container"]
        or any(
            "/var/run/docker.sock"
            in (str(row.get("Source")) + " " + str(row.get("Destination")))
            for row in mounts
        )
        or any(
            row.get("Source") == spec["paths"]["datadir"] for row in mounts
        )
        or (client_inspect.get("Config") or {}).get("User")
        != CLIENT_RUNTIME_USER
    ):
        raise AuthorityError("client SQL bridge immutable isolation differs")
    defaults_file = spec["mysql_defaults_file"]
    preflight_script = (
        "test \"$(id -u)\" = \"%d\"; "
        "test \"$(id -g)\" = \"%d\"; "
        "test -f %s; test -r %s; "
        "grep -qx 'user=root' %s; "
        "! grep -Eq 'UNKNOWN_USER|\\$\\{|\\{\\{' %s; "
        "probe=/measurement/.ra-client-preflight.$$; "
        "(umask 077; : > \"$probe\"); test -f \"$probe\"; "
        "test -w \"$probe\"; rm -f \"$probe\""
        % (
            CLIENT_RUNTIME_UID,
            CLIENT_RUNTIME_GID,
            shlex.quote(defaults_file),
            shlex.quote(defaults_file),
            shlex.quote(defaults_file),
            shlex.quote(defaults_file),
        )
    )
    _run(
        [
            "docker", "exec", "--user", CLIENT_RUNTIME_USER,
            bridge["client_container"], "/bin/sh", "-ceu", preflight_script,
        ],
        timeout=30,
    )
    version = _run(
        [
            "docker",
            "exec",
            "--user",
            CLIENT_RUNTIME_USER,
            bridge["client_container"],
            bridge["mysql_binary_in_client"],
            "--version",
        ],
        timeout=30,
    ).stdout.strip()
    if not version or "mysql" not in version.lower():
        raise AuthorityError("client-container native MySQL CLI is absent")
    return {
        "status": "PASS_EXACT_CLIENT_DOCKER_EXEC_SQL_STDOUT_BRIDGE",
        "kind": bridge["kind"],
        "client_container": bridge["client_container"],
        "client_container_id": client_inspect["Id"],
        "client_pid": int((client_inspect.get("State") or {}).get("Pid") or 0),
        "mysql_binary_in_client": bridge["mysql_binary_in_client"],
        "mysql_version_stdout": version,
        "host_mysql_cli_required": False,
        "docker_socket_mounted": False,
        "target_datadir_mounted": False,
        "stdout_only": True,
        "execution_uid_gid": [CLIENT_RUNTIME_UID, CLIENT_RUNTIME_GID],
        "credentials_defaults_file": defaults_file,
        "credentials_readable": True,
        "measurement_writable": True,
        "accepted_round_authorized": False,
    }


def _tree_digest(image, path, source_tree):
    """Digest the copy domain, excluding only the known logical socket.

    The template retains one root-level socket symlink as a runtime marker.
    The physical copier deliberately omits it, so both sides must use the
    same normalized domain.  Every other transient entry or symlink is an
    authority failure rather than an implicit exclusion.
    """
    if source_tree:
        socket_checks = (
            "test -L {name}; "
            "test \"$(readlink {name})\" = \"{target}\"; "
            "test -z \"$(find . -xdev \\( -name mysql.sock -o -name mysqld.sock -o -name mysqld.pid \\) ! -path ./{name} -print -quit)\"; "
            "test -z \"$(find . -xdev -type l ! -path ./{name} -print -quit)\"; "
        ).format(
            name=ROOT_LOGICAL_SOCKET_NAME,
            target=ROOT_LOGICAL_SOCKET_TARGET,
        )
    else:
        socket_checks = (
            "test ! -e {name}; test ! -L {name}; "
            "test -z \"$(find . -xdev \\( -name mysql.sock -o -name mysqld.sock -o -name mysqld.pid \\) -print -quit)\"; "
            "test -z \"$(find . -xdev -type l -print -quit)\"; "
        ).format(name=ROOT_LOGICAL_SOCKET_NAME)
    script = (
        "cd /tree; "
        "test -z \"$(find . -xdev \\( -type s -o -type b -o -type c -o -type p \\) -print -quit)\"; "
        + socket_checks
        + "count=$(find . -xdev -type f | wc -l); "
        + "bytes=$(find . -xdev -type f -printf '%s\\n' | awk '{s+=$1} END {print s+0}'); "
        + "digest=$(tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner --mode=0600 "
        + "--format=posix --pax-option=delete=atime,delete=ctime "
        + ("--exclude=./%s -cf - . | sha256sum | awk '{print $1}'); "
           % ROOT_LOGICAL_SOCKET_NAME)
        + "printf '%s %s %s\\n' \"$digest\" \"$count\" \"$bytes\""
    )
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "DAC_READ_SEARCH",
            "--mount",
            "type=bind,src=%s,dst=/tree,readonly" % path,
            "--entrypoint",
            "/bin/sh",
            image,
            "-ceu",
            script,
        ],
        timeout=3600,
    )
    pieces = result.stdout.strip().split()
    if (
        len(pieces) != 3
        or HEX64_RE.fullmatch(pieces[0]) is None
        or not pieces[1].isdigit()
        or not pieces[2].isdigit()
    ):
        raise AuthorityError("tree digest output differs")
    return {
        "tree_sha256": pieces[0],
        "file_count": int(pieces[1]),
        "byte_count": int(pieces[2]),
        "tree_identity_normalization": TREE_IDENTITY_NORMALIZATION,
    }


def _copy_failure_evidence(spec, failure, source_identity, destination_identity,
                           copy_stdout):
    """Evidence for a post-copy, pre-container authority refusal."""
    return {
        "schema_version": "mysql_job_distributed_v2_bootstrap_failure_evidence_v1",
        "status": "FAIL_PHYSICAL_COPY_IDENTITY_BEFORE_DB_CLIENT",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "spec_sha256": sha_bytes(canonical(spec)),
        "failure": str(failure),
        "source_identity": source_identity,
        "destination_identity": destination_identity,
        "physical_copy_stdout": copy_stdout,
        "database_or_client_started": False,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def execute_bootstrap(spec, output, main_agent_token):
    """Execute the host-local qualification bootstrap.

    A partially written journal is intentionally not resumable.  The next
    attempt must use a new attempt token so a stale container/datadir can never
    be mistaken for a completed bootstrap.
    """
    spec = validate_spec(spec)
    if (
        not isinstance(main_agent_token, str)
        or not main_agent_token
        or sha_bytes(main_agent_token.encode("utf-8"))
        != spec["main_agent_token_sha256"]
    ):
        raise AuthorityError("main-agent bootstrap token differs")
    plan = command_plan(spec)
    output = Path(output)
    if output.exists() or output.with_name(output.name + ".sha256").exists():
        raise AuthorityError("bootstrap evidence already exists")
    if socket.gethostname() != HOSTNAME:
        raise AuthorityError("bootstrap must execute on node02")
    bundle_path = Path(spec["bundle"]["path"])
    verify_bundle_closure(
        bundle_path,
        spec["bundle"]["bundle_id"],
        spec["bundle"]["manifest_sha256"],
    )
    runtime_preflight = _runtime_preflight(spec)
    for image_key in ("mysql_image", "client_image"):
        result = _run(
            ["docker", "image", "inspect", spec[image_key]["image_id"]],
            timeout=30,
        )
        try:
            rows = json.loads(result.stdout)
        except ValueError as exc:
            raise AuthorityError("image inspect JSON invalid: %s" % exc)
        if not isinstance(rows, list) or len(rows) != 1:
            raise AuthorityError(
                "%s image inspect cardinality differs" % image_key
            )
        row = rows[0]
        if row.get("Id") != spec[image_key]["image_id"]:
            raise AuthorityError("%s live image ID differs" % image_key)
    for name in spec["containers"].values():
        if _run(["docker", "inspect", name], check=False).returncode == 0:
            raise AuthorityError("attempt container already exists: %s" % name)
    paths = spec["paths"]
    device_stat = os.stat(DEVICE)
    device_major_minor = "%d:%d" % (
        os.major(device_stat.st_rdev),
        os.minor(device_stat.st_rdev),
    )
    mount_stat = os.stat(MOUNT)
    mount_major_minor = "%d:%d" % (
        os.major(mount_stat.st_dev),
        os.minor(mount_stat.st_dev),
    )
    if device_major_minor != MAJOR_MINOR or mount_major_minor != MAJOR_MINOR:
        raise AuthorityError("live JOB device or mount major:minor differs")
    findmnt = _run(
        ["findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "--target", MOUNT],
        timeout=30,
    ).stdout.strip().split()
    if len(findmnt) != 3 or findmnt[0] != DEVICE or findmnt[2] != MOUNT:
        raise AuthorityError("live JOB mount source/target differs")
    if not Path(TEMPLATE).is_dir():
        raise AuthorityError("JOB template is absent")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", MYSQL_PORT))
        except OSError as exc:
            raise AuthorityError("JOB MySQL port is occupied: %s" % exc)
    attempt_root = Path(paths["attempt_root"])
    datadir = Path(paths["datadir"])
    stage = Path(plan["copy"]["staging_destination"])
    if attempt_root.exists() or datadir.exists() or stage.exists():
        raise AuthorityError("attempt path already exists")
    attempt_root.mkdir(parents=True, exist_ok=False)
    credentials = Path(paths["credentials_dir"])
    measurement = Path(paths["host_measurement_tmpfs"])
    credentials.mkdir(mode=0o700)
    if measurement.exists() or measurement.is_symlink():
        raise AuthorityError("attempt-private measurement mountpoint already exists")
    measurement.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    measurement.mkdir(mode=0o700)
    _run([
        "mount", "-t", "tmpfs", "-o",
        "rw,nosuid,nodev,noexec,size=%d,uid=%d,gid=%d,mode=0700"
        % (CLIENT_ENVELOPE["tmpfs_bytes"], CLIENT_RUNTIME_UID, CLIENT_RUNTIME_GID),
        "tmpfs", str(measurement),
    ])
    measurement_readback = _host_measurement_tmpfs_readback(measurement)
    os.chmod(credentials, 0o700)
    os.chown(credentials, CLIENT_RUNTIME_UID, CLIENT_RUNTIME_GID)
    journal = attempt_root / "bootstrap_inflight.json"
    atomic_json(
        journal,
        {
            "schema_version": "mysql_job_distributed_v2_bootstrap_journal_v1",
            "status": "STARTED_NOT_RESUMABLE",
            "attempt_token": spec["attempt_token"],
            "spec_sha256": sha_bytes(canonical(spec)),
            "accepted_round_authorized": False,
        },
    )
    source_identity = None
    destination_identity = None
    copy_completed = None
    try:
        source_identity = _tree_digest(
            spec["client_image"]["image_id"], TEMPLATE, source_tree=True
        )
        copy_completed = _run(plan["copy"]["argv"], timeout=7200)
        try:
            physical_copy = json.loads(copy_completed.stdout)
        except ValueError as exc:
            raise AuthorityError(
                "physical copy nonsharing evidence is invalid: %s" % exc
            )
        fiemap = physical_copy.get("fiemap") or {}
        if (
            physical_copy.get("status")
            != "PASS_BYTE_COPY_AND_FIEMAP_NONSHARING"
            or physical_copy.get("cp_used") is not False
            or physical_copy.get("reflink_api_used") is not False
            or physical_copy.get("copy_file_range_used") is not False
            or physical_copy.get("sendfile_used") is not False
            or physical_copy.get("tree_identity_normalization")
            != TREE_IDENTITY_NORMALIZATION
            or not _target_permission_normalization_valid(
                physical_copy.get("target_permission_normalization"),
                physical_copy.get("file_count"),
            )
            or fiemap.get("fiemap_extent_shared_absent") is not True
            or fiemap.get(
                "source_destination_physical_extent_overlap_absent"
            )
            is not True
            or physical_copy.get("accepted_round_authorized") is not False
        ):
            raise AuthorityError("physical copy nonsharing audit failed")
        destination_identity = _tree_digest(
            spec["client_image"]["image_id"], str(stage), source_tree=False
        )
        if source_identity != destination_identity:
            raise AuthorityError("physical copy tree identity differs")
    except (AuthorityError, subprocess.CalledProcessError) as exc:
        atomic_json(
            output,
            _copy_failure_evidence(
                spec,
                exc,
                source_identity,
                destination_identity,
                copy_completed.stdout if copy_completed is not None else None,
            ),
        )
        raise
    source_inode = os.stat(TEMPLATE).st_ino
    destination_inode = os.stat(stage).st_ino
    if source_inode == destination_inode:
        raise AuthorityError("copy root inode aliases the template")
    os.rename(str(stage), str(datadir))
    datadir_stat = os.stat(datadir)
    datadir_major_minor = "%d:%d" % (
        os.major(datadir_stat.st_dev),
        os.minor(datadir_stat.st_dev),
    )
    if datadir_major_minor != MAJOR_MINOR:
        raise AuthorityError("fresh JOB datadir is not on the frozen device")
    if (
        datadir_stat.st_uid != MYSQL_DATADIR_UID
        or datadir_stat.st_gid != MYSQL_DATADIR_GID
        or stat.S_IMODE(datadir_stat.st_mode)
        != int(MYSQL_DATADIR_DIRECTORY_MODE, 8)
    ):
        raise AuthorityError("fresh JOB datadir target permission normalization differs")
    password = secrets.token_urlsafe(36)
    defaults = Path(spec["mysql_defaults_file"])
    _write_client_defaults(defaults, password)
    defaults.chmod(0o600)
    os.chown(defaults, CLIENT_RUNTIME_UID, CLIENT_RUNTIME_GID)
    defaults_stat = defaults.stat()
    if (
        defaults_stat.st_uid != CLIENT_RUNTIME_UID
        or defaults_stat.st_gid != CLIENT_RUNTIME_GID
        or stat.S_IMODE(defaults_stat.st_mode) != 0o600
    ):
        raise AuthorityError("client defaults ownership/mode differs")
    defaults_binding = _verify_client_defaults_binding(defaults)
    bootstrap_name = plan["bootstrap_database"]["name"]
    _run(plan["bootstrap_database"]["argv"])
    _wait_mysql(bootstrap_name)
    _apply_bootstrap_account_sql(bootstrap_name, password)
    _run(["docker", "stop", "--time", "120", bootstrap_name], timeout=150)
    _run(["docker", "rm", bootstrap_name])
    _run(plan["database"]["argv"])
    _run(plan["client"]["argv"])
    client_inspect = _inspect(spec["containers"]["client"])
    sql_bridge_preflight = _client_sql_bridge_preflight(
        spec, client_inspect
    )
    _wait_mysql(
        spec["containers"]["database"],
        str(defaults),
        spec["containers"]["client"],
    )
    _run(
        [
            "docker",
            "exec",
            "--user",
            CLIENT_RUNTIME_USER,
            spec["containers"]["client"],
            spec["database_counter_bridge"]["mysql_binary_in_client"],
            "--defaults-file=%s" % defaults,
            "--protocol=TCP",
            "--host=127.0.0.1",
            "--port=%d" % MYSQL_PORT,
            "--user=root",
            "-NBe",
            (
                "UPDATE mysql.server_cost SET cost_value=NULL; "
                "UPDATE mysql.engine_cost SET cost_value=NULL; "
                "FLUSH OPTIMIZER_COSTS;"
            ),
        ]
    )
    database_inspect = _inspect(spec["containers"]["database"])
    client_inspect = _inspect(spec["containers"]["client"])
    live_path = measurement / "live_readback.json"
    _run(plan["collect_only"]["argv"], timeout=900)
    live = load_object(live_path, "collect-only readback")
    evidence = {
        "schema_version": "mysql_job_distributed_v2_bootstrap_evidence_v1",
        "status": "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": SCENARIO,
        "host_id": HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "contract_sha256": CONTRACT_SHA256,
        "reconciliation_sha256": spec["reconciliation_sha256"],
        "spec_sha256": sha_bytes(canonical(spec)),
        "copy": {
            "command":
                "userspace_read_write_plus_fiemap_nonsharing_v1",
            "overlay": False,
            "reflink": False,
            "full_physical_copy": True,
            "atomic_rename_complete": True,
            "source": TEMPLATE,
            "destination": str(datadir),
            "device_major_minor": MAJOR_MINOR,
            "source_tree_sha256": source_identity["tree_sha256"],
            "destination_tree_sha256":
                destination_identity["tree_sha256"],
            "file_count": source_identity["file_count"],
            "byte_count": source_identity["byte_count"],
            "tree_identity_normalization": source_identity[
                "tree_identity_normalization"
            ],
            "source_root_inode": source_inode,
            "destination_root_inode": destination_inode,
            "physical_copy_evidence": physical_copy,
        },
        "database": {
            "container_id": database_inspect.get("Id"),
            "pid": int((database_inspect.get("State") or {}).get("Pid") or 0),
            "inspect": database_inspect,
        },
        "client": {
            "container_id": client_inspect.get("Id"),
            "pid": int((client_inspect.get("State") or {}).get("Pid") or 0),
            "inspect": client_inspect,
        },
        "measurement_bind": measurement_readback,
        "credential_binding": {
            "user": defaults_binding["user"],
            "placeholder_free": defaults_binding["placeholder_free"],
            "password_source": "random_attempt_secret_not_logged_or_hashed",
        },
        "collect_only": {
            "sha256": sha_bytes(canonical(live)),
            "document": live,
        },
        "preflight": {
            "python_runtime": runtime_preflight,
            "database_counter_bridge": sql_bridge_preflight,
            "all_required_components_present": True,
            "host_mysql_cli_required": False,
            "accepted_round_authorized": False,
        },
        "qualification_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    validate_bootstrap_evidence(evidence, spec)
    atomic_json(output, evidence)
    atomic_json(
        journal,
        {
            "schema_version": "mysql_job_distributed_v2_bootstrap_journal_v1",
            "status": "COMPLETE",
            "attempt_token": spec["attempt_token"],
            "spec_sha256": sha_bytes(canonical(spec)),
            "evidence_sha256": sha_bytes(canonical(evidence)),
            "accepted_round_authorized": False,
        },
    )
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--emit-plan", action="store_true")
    modes.add_argument("--execute-bootstrap", action="store_true")
    modes.add_argument("--verify-bootstrap", type=Path)
    modes.add_argument("--build-handoff", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dynamic-gate", type=Path)
    parser.add_argument("--fresh-r0", type=Path)
    parser.add_argument("--debug-round", type=Path)
    parser.add_argument("--formal10", action="store_true")
    parser.add_argument("--fresh-r0-launch", action="store_true")
    parser.add_argument("--main-agent-token")
    args = parser.parse_args()
    spec = load_with_sidecar(args.spec, "bootstrap spec")
    if args.emit_plan:
        value = command_plan(spec)
    elif args.execute_bootstrap:
        if args.output is None:
            raise AuthorityError("execute-bootstrap requires --output")
        value = execute_bootstrap(spec, args.output, args.main_agent_token)
    elif args.verify_bootstrap is not None:
        value = validate_bootstrap_evidence(
            load_object(args.verify_bootstrap, "bootstrap evidence"), spec
        )
    else:
        if args.output is None or args.dynamic_gate is None:
            raise AuthorityError(
                "build-handoff requires output and dynamic gate"
            )
        bootstrap_path = Path(spec["paths"]["attempt_root"]) / (
            "bootstrap_evidence.json"
        )
        bootstrap = load_object(bootstrap_path, "bootstrap evidence")
        gate = load_object(args.dynamic_gate, "dynamic gate")
        if args.fresh_r0_launch:
            if (
                args.fresh_r0 is not None
                or args.debug_round is not None
                or args.formal10
            ):
                raise AuthorityError(
                    "fresh-R0 launch cannot bind later-stage evidence"
                )
            value = build_fresh_r0_launch_authority(
                spec, bootstrap, gate
            )
        else:
            if args.fresh_r0 is None:
                raise AuthorityError(
                    "debug/formal handoff requires fresh R0 evidence"
                )
            value = build_handoff_authority(
                spec,
                bootstrap,
                gate,
                load_object(args.fresh_r0, "fresh R0"),
                (
                    load_object(args.debug_round, "debug round")
                    if args.debug_round is not None
                    else None
                ),
                formal10=args.formal10,
            )
        atomic_json(args.output, value)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuthorityError as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
