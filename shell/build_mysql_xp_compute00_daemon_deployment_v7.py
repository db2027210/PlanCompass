#!/usr/bin/env python3
"""Build the daemon-deployment-only XP v7 successor offline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID = "02b460d96dfd69972ca76a2b9620228fc2d239d46453d3c880d8ac001a7f12b4"
PARENT_MANIFEST_SHA256 = "1c5c02d5e6cf1a57e815de50797fda01339827a6e58caef5d0d1d1aea38811be"
PARENT = ROOT / "artifacts/mysql_xp_compute00_systemd219_hybrid_cgroup_v6" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_daemon_deployment_v7"
SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v7"
STATUS = "SEALED_XP_DAEMON_DEPLOYMENT_ONLY_NOT_EXECUTED"
DEPLOYER_SOURCE = ROOT / "shell/mysql_xp_compute00_daemon_deploy_v1.py"
TEST_SOURCE = ROOT / "tests/test_mysql_xp_compute00_daemon_deployment_v7.py"
DEPLOYER = "deployment/mysql_xp_compute00_daemon_deploy_v1.py"
TEST = "tests_v7/test_mysql_xp_compute00_daemon_deployment_v7.py"
CONTRACT = "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
FAILURE = "authority/mysql_xp_v6_missing_dedicated_daemon_20260803.json"
RUNBOOK = "operator/mysql_xp_compute00_daemon_deployment_v7.md"


class BuildError(RuntimeError):
    pass


def load_parent_builder():
    path = ROOT / "shell/build_mysql_xp_compute00_systemd219_hybrid_cgroup_v6.py"
    spec = importlib.util.spec_from_file_location("xp_v6_builder_for_v7", path)
    if spec is None or spec.loader is None:
        raise BuildError("v6 builder import differs")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V6 = load_parent_builder()
canonical, sha, sha_bytes = V6.canonical, V6.sha, V6.sha_bytes
writable, freeze, files = V6.writable, V6.freeze, V6.files


def contract_payload():
    dockerd = [
        "/usr/bin/dockerd",
        "--config-file=<sealed_bundle>/deployment/dockerd-empty-config.json",
        "--host=unix:///run/ra-docker-mysql-xp-v2.sock",
        "--data-root=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29",
        "--exec-root=/run/ra-docker-mysql-xp-v2",
        "--pidfile=/run/ra-docker-mysql-xp-v2.pid",
        "--storage-driver=overlay2",
        "--exec-opt=native.cgroupdriver=cgroupfs",
        "--bridge=none", "--iptables=false", "--ip-forward=false",
        "--ip-masq=false", "--userland-proxy=false", "--log-driver=none",
        "--group=root",
    ]
    properties = [
        "CPUShares=256", "MemoryLimit=2147483648", "TasksMax=512",
        "LimitNOFILE=1048576", "Delegate=yes", "Restart=no",
        "KillMode=process",
    ]
    return {
        "schema_version": "mysql_xp_compute00_daemon_deployment_contract_v1",
        "status": "SEALED_EXACT_DEPLOYMENT_CONTRACT_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "unit": "ra-docker-mysql-xp-v2.service",
        "unit_kind": "systemd_219_transient_notify_service",
        "fragment_path": "/run/systemd/transient/ra-docker-mysql-xp-v2.service",
        "unit_file_state": "transient",
        "transient": "yes",
        "control_group": "/system.slice/ra-docker-mysql-xp-v2.service",
        "systemd_run_prefix": [
            "/usr/bin/systemd-run",
            "--unit=ra-docker-mysql-xp-v2.service",
            "--description=Dedicated Docker daemon for MySQL XP only",
            "--service-type=notify",
        ],
        "systemd_properties": properties,
        "unit_exec_argv": [
            "/usr/bin/taskset", "-c", "192-193,448-449", *dockerd,
        ],
        "live_dockerd_argv": dockerd,
        "daemon_cpuset": "192-193,448-449",
        "socket": "/run/ra-docker-mysql-xp-v2.sock",
        "socket_uri": "unix:///run/ra-docker-mysql-xp-v2.sock",
        "socket_metadata": {"type": "socket", "mode": "0660", "uid": 0,
                            "gid": 0, "main_pid_fd_owner_required": True},
        "data_root": "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29",
        "data_mount": "/disk1", "data_device": "/dev/nvme1n1",
        "data_major_minor": "259:6", "storage_driver": "overlay2",
        "cgroup_driver": "cgroupfs", "empty_container_inventory_required": True,
        "hybrid_cgroup_projection": {
            "active_exact_unit": sorted([
                "name=systemd", "cpu", "cpuacct", "devices", "memory",
                "pids", "blkio",
            ]),
            "root": sorted([
                "cpuset", "rdma", "freezer", "perf_event", "hugetlb",
                "net_cls", "net_prio",
            ]),
            "unique_hierarchy_zero": {
                "hierarchy": "0", "controllers": "",
                "path": "/system.slice/ra-docker-mysql-xp-v2.service",
            },
            "rdma_projection": "root",
        },
        "idempotency": {
            "not_found": "create_and_start_exact_transient_unit_once",
            "exact_active": "readback_and_adopt_without_lifecycle_mutation",
            "anything_else": "fail_closed",
            "receipt": "atomic_no_overwrite_or_identical_adoption",
        },
        "transaction": {
            "prepare": "read_only_preflight_and_pg_service_root_snapshot",
            "single_manager_mutation": "exact_systemd_run_transient_unit_creation_and_start",
            "commit": "full_live_readback_then_atomic_receipt",
            "crash_after_exact_start": "rerun_adopts_same_pid_startticks_and_commits",
            "nonexact_or_failed_loaded_unit": "quarantine_fail_closed",
            "automatic_stop_remove_or_reset_failed_authorized": False,
            "workload_commit_coupled": False,
        },
        "authorized_mutation": "exact_xp_dedicated_daemon_transient_unit_creation_and_start_only",
        "postgresql_reserved_cpuset": "32-51,288-307",
        "postgresql_cpu_overlap": [],
        "postgresql_dependency_prohibited": True,
        "postgresql_path_prefix_prohibited": "/mnt/nvme2n1/",
        "postgresql_service_root_identity_must_remain_unchanged": True,
        "postgresql_mutation_prohibited": True,
        "default_docker_socket_prohibited": True,
        "database_container_launch_authorized": False,
        "client_container_launch_authorized": False,
        "workload_launch_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }


def failure_payload():
    return {
        "schema_version": "mysql_xp_v6_missing_dedicated_daemon_failure_v1",
        "status": "SEALED_PREREQUISITE_ABSENT_ZERO_CREDIT",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "unit": "ra-docker-mysql-xp-v2.service",
        "observed_load_state": "not-found",
        "journal_history_entries": 0,
        "existing_unit_authority_available": False,
        "failure": "required XP dedicated Docker daemon unit is absent",
        "invented_existing_authority_prohibited": True,
        "successor_action": "define_exact_transactional_daemon_deployment",
        "database_container_started": False,
        "client_container_started": False,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def runbook_text():
    return """# MySQL XP v7 daemon-deployment-only handoff

This successor may create and start only `ra-docker-mysql-xp-v2.service`.
Database/client containers and workload launch are prohibited. Formal and
accepted-round credit remain disabled. PostgreSQL lifecycle and data paths
must not be modified.

After publishing the complete immutable bundle on compute00, the main agent runs
exactly one command. The deployer derives and verifies the content-addressed
bundle ID, creates the transient unit only when `LoadState=not-found`, or
adopts an already active unit only after exact readback.

```sh
BUNDLE=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803/bundles/<v7_bundle_id>
ID=$(basename "$BUNDLE")
OUT=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_deployment_receipts/$ID.json
sudo python3 -B "$BUNDLE/deployment/mysql_xp_compute00_daemon_deploy_v1.py" --bundle "$BUNDLE" --authorize-exact-bundle-id "$ID" --output "$OUT"
jq -e '.status == "PASS_XP_DEDICATED_DAEMON_ONLY" and .formal_scope == "mysql_only" and .postgresql_mutation == false and .database_container_started == false and .client_container_started == false and .workload_started == false and .formal_launch_authorized == false and .accepted_round_authorized == false and .accepted_rounds_credit == 0' "$OUT"
```

Do not synthesize a unit file, use the default Docker socket, run a Docker
container, invoke another lifecycle verb, or retry a loaded but non-exact
unit. Such states require a separate audited remediation successor.
"""


def validate_target(target, bundle_id, require_content_addressed_name=True):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id", None)
    if (
        observed != bundle_id
        or (require_content_addressed_name and target.name != bundle_id)
        or sha_bytes(canonical(unsigned)) != bundle_id
        or files(target) != manifest.get("files")
    ):
        raise BuildError("v7 content-addressed closure differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target),
               XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", "-q", str(target / TEST)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=180,
    )
    if completed.returncode:
        raise BuildError("v7 sealed tests failed:\n" + completed.stdout)
    return completed.stdout


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("v6 predecessor manifest identity differs")
    parent_manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    if (
        parent_manifest.get("bundle_id") != PARENT_ID
        or parent_manifest.get("schema_version")
        != "mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6"
        or parent_manifest.get("formal_launch_authorized") is not False
        or parent_manifest.get("accepted_round_authorized") is not False
        or parent_manifest.get("accepted_rounds_credit") != 0
    ):
        raise BuildError("v6 predecessor authority differs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-daemon-v7-", dir=OUTPUT))
    stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage); writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        (stage / "deployment").mkdir(exist_ok=True)
        (stage / "tests_v7").mkdir(exist_ok=True)
        shutil.copy2(DEPLOYER_SOURCE, stage / DEPLOYER)
        (stage / DEPLOYER).chmod(0o755)
        shutil.copy2(TEST_SOURCE, stage / TEST)
        (stage / "deployment/dockerd-empty-config.json").write_text(
            "{}\n", encoding="ascii")
        (stage / CONTRACT).write_bytes(canonical(contract_payload()))
        (stage / FAILURE).write_bytes(canonical(failure_payload()))
        (stage / RUNBOOK).write_text(runbook_text(), encoding="utf-8")
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_daemon_deployment_v7.py")
        manifest = {key: value for key, value in parent_manifest.items()
                    if key not in ("bundle_id", "files")}
        manifest.update({
            "schema_version": SCHEMA, "status": STATUS,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "daemon_deployment_authorized": True,
            "transactional_daemon_deployment": True,
            "daemon_deployment_entrypoint": DEPLOYER,
            "daemon_deployment_contract": CONTRACT,
            "daemon_deployment_runbook": RUNBOOK,
            "authorized_mutations": [
                "create_and_start_exact_ra-docker-mysql-xp-v2_transient_unit_when_not_found",
                "dockerd_owned_runtime_socket_exec_root_pidfile_and_overlay2_metadata_on_disk1",
                "atomic_daemon_deployment_receipt",
            ],
            "workload_launch_authorized": False,
            "database_container_launch_authorized": False,
            "client_container_launch_authorized": False,
            "formal_launch_authorized": False,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
            "remote_actions": 0,
            "v6_failure_evidence": FAILURE,
            "files": files(stage),
        })
        bundle_id = sha_bytes(canonical(manifest)); manifest["bundle_id"] = bundle_id
        target = OUTPUT / bundle_id
        if target.exists():
            return target, validate_target(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(stage / "bundle_manifest.json") + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        freeze(stage); stage.chmod(0o755)
        output = validate_target(
            stage, bundle_id, require_content_addressed_name=False)
        os.rename(stage, target); target.chmod(0o555)
        return target, output, "PUBLISHED_NEW"
    finally:
        if raw.exists():
            writable(raw); shutil.rmtree(raw)


def main():
    target, output, mode = build()
    print(json.dumps({
        "schema_version": "mysql_xp_compute00_daemon_deployment_v7_build_result_v1",
        "status": "PASS_DAEMON_DEPLOYMENT_ONLY_NOT_EXECUTED",
        "publish_mode": mode, "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "tests": "PASS", "test_output": output.strip().splitlines()[-1],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "daemon_deployment_authorized": True,
        "workload_launch_authorized": False,
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError,
            subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))
