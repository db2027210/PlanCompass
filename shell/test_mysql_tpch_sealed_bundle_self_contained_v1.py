#!/usr/bin/env python3
"""Thirty-one direct regressions for the sealed self-contained TPC-H bundle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


sys.dont_write_bytecode = True
BUNDLE = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((BUNDLE / "bundle_manifest.json").read_text())
RUNTIME = json.loads((BUNDLE / "distributed_runtime_contract.json").read_text())
SOCKET = json.loads(
    (
        BUNDLE / "fixtures/node04_tpch_daemon_socket_identity_fixture_v1.json"
    ).read_text()
)
SYSTEMD = json.loads(
    (
        BUNDLE / "fixtures/node04_tpch_live_daemon_systemctl_fixture_v1.json"
    ).read_text()
)
PROC = json.loads(
    (
        BUNDLE / "fixtures/node04_tpch_live_daemon_proc_fixture_v1.json"
    ).read_text()
)
NAMESPACE = json.loads(
    (
        BUNDLE / "fixtures/node04_kernel_namespace_capability_fixture_v1.json"
    ).read_text()
)
AUTHORITY = (BUNDLE / "mysql_tpch_distributed_v2_authority.py").read_text()
COLLECTOR = (
    BUNDLE / "qualification/mysql_distributed_live_readback_qualification_v1.py"
).read_text()
MATERIALIZER = (
    BUNDLE / "launch/mysql_tpch_systemd_oneshot_materializer_v1.py"
).read_text()
INVALIDATION = json.loads(
    (BUNDLE / "invalidated_313_nonselfcontained_bundle.json").read_text()
)


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode()


def actual_files():
    result = {}
    for path in sorted(BUNDLE.rglob("*")):
        if path.is_file() and path not in (
            BUNDLE / "bundle_manifest.json",
            BUNDLE / "bundle_manifest.json.sha256",
        ):
            result[path.relative_to(BUNDLE).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


UNSIGNED = {key: value for key, value in MANIFEST.items() if key != "bundle_id"}
ENVELOPE = RUNTIME["resource_envelope"]
PROJECTION = SYSTEMD["exact_projection"]
SOCKET_CONTRACT = MANIFEST["socket_listener_authority"]


CASES = [
    ("manifest_identity", hashlib.sha256(canonical(UNSIGNED)).hexdigest() == MANIFEST["bundle_id"]),
    ("directory_identity", BUNDLE.name == MANIFEST["bundle_id"]),
    ("exact_file_closure", actual_files() == MANIFEST["files"]),
    ("mysql_only", MANIFEST["formal_scope"] == "mysql_only"),
    ("cross_engine_prohibited", MANIFEST["cross_engine_comparison_prohibited"] is True),
    ("formal_launch_closed", MANIFEST["formal_launch_authorized"] is False),
    ("accepted_closed", MANIFEST["accepted_round_authorized"] is False),
    ("socket_path", SOCKET["socket_path"] == "/run/ra-docker-mysql-tpch-v1.sock"),
    ("socket_listener_count", SOCKET["listener_count"] == 1),
    ("socket_kernel_inode", SOCKET["listener_kernel_inode"] == 571336812),
    ("filesystem_inode_distinct", SOCKET["filesystem_inode"] == 571336813),
    ("socket_fd_target", SOCKET["listener_fd_target"] == "socket:[571336812]"),
    ("selector_flags", SOCKET["listener_selector"]["flags"] == "00010000"),
    ("selector_type", SOCKET["listener_selector"]["type"] == "0001"),
    ("selector_state", SOCKET["listener_selector"]["state"] == "01"),
    ("authority_unique_listener", "if len(listeners) != 1:" in AUTHORITY),
    ("collector_unique_listener", "if len(listeners) != 1:" in COLLECTOR),
    ("authority_kernel_fd", '"socket:[%d]" % listener["inode"]' in AUTHORITY),
    ("collector_kernel_fd", '"socket:[%d]" % listener["inode"]' in COLLECTOR),
    ("transient_fragment", PROJECTION["FragmentPath"].startswith("/run/systemd/system/")),
    ("transient_yes", PROJECTION["Transient"] == "yes"),
    ("daemon_main_pid", PROJECTION["MainPID"] == "97636"),
    ("daemon_cpuset", PROC["cpus_allowed_list"] == "34-35,70-71"),
    ("daemon_limits", (PROJECTION["CPUShares"], PROJECTION["MemoryLimit"], PROJECTION["TasksMax"]) == ("256", "2147483648", "512")),
    ("optional_cgroup_namespace", PROC["optional_cgroup_namespace"] == {"supported": False, "path_absent": True}),
    ("oneshot", "--service-type=oneshot" in MATERIALIZER),
    ("remain_after_exit", "--remain-after-exit" in MATERIALIZER),
    ("no_wait_collect", 'PROHIBITED_OPTIONS = ("--wait", "--collect")' in MATERIALIZER),
    ("fresh_envelope", (ENVELOPE["cpu_quota_cores"], ENVELOPE["io_read_bps"], ENVELOPE["io_write_bps"], ENVELOPE["mysql_port"]) == (4, 100663296, 100663296, 65103)),
    ("invalidation_zero_credit", INVALIDATION["status"] == "INVALIDATED_DO_NOT_RUN_OR_CREDIT" and INVALIDATION["accepted_rounds_credit"] == 0),
]


@pytest.mark.parametrize("label,passed", CASES, ids=[item[0] for item in CASES])
def test_sealed_fact(label, passed):
    assert passed, label


def test_sealed_read_only_verifier_passes_without_external_repository():
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(BUNDLE / "builder/build_mysql_tpch_socket_identity_successor_v1.py"),
        ],
        cwd=BUNDLE,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["status"] == "PASS_SELF_CONTAINED_SEALED_BUNDLE"
    assert evidence["bundle_id"] == MANIFEST["bundle_id"]
