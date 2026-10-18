#!/usr/bin/env python3
"""Build the adoption-only successor for the exact live v9 XP daemon."""
from __future__ import annotations

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
PARENT_ID = "24d1324d264688fc8d4a737126b6a729b499688063ecf4cc22e24431833362ee"
PARENT_MANIFEST_SHA256 = "26fc54fb0bb856703280ae666902473d2621bb27cad72b7497e98affff9dd484"
PARENT = ROOT / "artifacts/mysql_xp_compute00_daemon_deployment_v9" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_daemon_adoption_v10"
SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v10"
STATUS = "SEALED_XP_EXISTING_DAEMON_ADOPTION_ONLY_NOT_EXECUTED"
DEPLOYER_SOURCE = ROOT / "shell/mysql_xp_compute00_daemon_deploy_v1.py"
TEST_SOURCE = ROOT / "tests/test_mysql_xp_compute00_daemon_adoption_v10.py"
ENTRYPOINT = "adoption/mysql_xp_compute00_daemon_adopt_v1.py"
CONFIG = "adoption/dockerd-empty-config.json"
TEST = "tests_v10/test_mysql_xp_compute00_daemon_adoption_v10.py"
ACTIVE_FIXTURE = "authority/mysql_xp_v9_systemd219_active.stdout"
FAILURE = "authority/mysql_xp_v9_partial_daemon_deployment_20260803.json"
ADOPTION_CONTRACT = "authority/mysql_xp_compute00_daemon_adoption_contract_v1.json"
DAEMON_CONTRACT = "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
DAEMON_CONTRACT_SHA256 = "7fdb1c24e59743e81b70496dadcb1f5ea7f287b2a347ac2aa0f269fa5ce8afa3"
RUNBOOK = "operator/mysql_xp_compute00_daemon_adoption_v10.md"
MAIN_PID = 4149472
STARTTICKS = 37998304
CMDLINE_SHA256 = "eb036eba455e55f13d26da8355e7aa3f6c590d24fc2e95ba2e497ab8737ced27"
ADOPTED_CONFIG = (
    "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/"
    "runtime_algorithm_c_successor_20260803/bundles/" + PARENT_ID
    + "/deployment/dockerd-empty-config.json"
)
ACTIVE_STDOUT = """Restart=no
MainPID=4149472
ExecStart={ path=/usr/bin/taskset ; argv[]=/usr/bin/taskset -c 192-193,448-449 /usr/bin/dockerd --config-file=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803/bundles/24d1324d264688fc8d4a737126b6a729b499688063ecf4cc22e24431833362ee/deployment/dockerd-empty-config.json --host=unix:///run/ra-docker-mysql-xp-v2.sock --data-root=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29 --exec-root=/run/ra-docker-mysql-xp-v2 --pidfile=/run/ra-docker-mysql-xp-v2.pid --storage-driver=overlay2 --exec-opt=native.cgroupdriver=cgroupfs --bridge=none --iptables=false --ip-forward=false --ip-masq=false --userland-proxy=false --log-driver=none --group=root ; ignore_errors=no ; start_time=[Mon 2026-08-03 18:48:17 CST] ; stop_time=[n/a] ; pid=4149472 ; code=(null) ; status=0/0 }
ControlGroup=/system.slice/ra-docker-mysql-xp-v2.service
Delegate=yes
CPUShares=256
MemoryLimit=2147483648
TasksMax=512
LimitNOFILE=1048576
KillMode=process
Id=ra-docker-mysql-xp-v2.service
Requires=system.slice sysinit.target
Wants=
BindsTo=
PartOf=
Conflicts=shutdown.target
Before=shutdown.target
After=system.slice systemd-journald.socket basic.target sysinit.target
LoadState=loaded
ActiveState=active
SubState=running
FragmentPath=/run/systemd/transient/ra-docker-mysql-xp-v2.service
SourcePath=
UnitFileState=transient
Transient=yes
"""
ACTIVE_MISSING = (
    "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecReload",
)


class BuildError(RuntimeError):
    pass


def load_v9_builder():
    path = ROOT / "shell/build_mysql_xp_compute00_daemon_deployment_v9.py"
    spec = importlib.util.spec_from_file_location("xp_v9_builder_for_v10", path)
    if spec is None or spec.loader is None:
        raise BuildError("v9 builder import differs")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


V9 = load_v9_builder()
canonical, sha, sha_bytes = V9.canonical, V9.sha, V9.sha_bytes
writable, freeze, files = V9.writable, V9.freeze, V9.files


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(label + " patch point differs")
    return text.replace(old, new, 1)


def active_expected():
    values = {}
    for line in ACTIVE_STDOUT.splitlines():
        key, value = line.split("=", 1)
        if key in values:
            raise BuildError("duplicate active fixture property")
        values[key] = value
    for key in ACTIVE_MISSING:
        values[key] = ""
    return values


def live_dockerd_argv():
    return [
        "/usr/bin/dockerd", "--config-file=" + ADOPTED_CONFIG,
        "--host=unix:///run/ra-docker-mysql-xp-v2.sock",
        "--data-root=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29",
        "--exec-root=/run/ra-docker-mysql-xp-v2",
        "--pidfile=/run/ra-docker-mysql-xp-v2.pid",
        "--storage-driver=overlay2",
        "--exec-opt=native.cgroupdriver=cgroupfs", "--bridge=none",
        "--iptables=false", "--ip-forward=false", "--ip-masq=false",
        "--userland-proxy=false", "--log-driver=none", "--group=root",
    ]


def failure_payload(fixture_sha):
    return {
        "schema_version": "mysql_xp_v9_partial_daemon_deployment_failure_v1",
        "status": "INVALID_ZERO_CREDIT_DAEMON_LIVE_NO_WORKLOAD_DO_NOT_RERUN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "observed_error": "daemon exact-live readback timeout: systemd property inventory differs",
        "root_diagnosis": "active systemd 219 omits five empty Exec hook properties while retaining exact ExecStart",
        "active_stdout_fixture": ACTIVE_FIXTURE,
        "active_stdout_sha256": fixture_sha,
        "systemd_mutation": True,
        "unit_created": True,
        "unit_active": True,
        "unit_substate": "running",
        "daemon_started": True,
        "main_pid": MAIN_PID,
        "process_startticks": STARTTICKS,
        "cmdline_raw_sha256": CMDLINE_SHA256,
        "live_dockerd_argv": live_dockerd_argv(),
        "dedicated_socket_created": True,
        "docker_ps_a_empty": True,
        "container_count": 0,
        "database_container_started": False,
        "client_container_started": False,
        "workload_started": False,
        "deployment_receipt_created": False,
        "persistent_change": "exact_dedicated_daemon_unit_socket_and_runtime_metadata_only",
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "authorization_reuse_prohibited": True,
        "postgresql_mutation": False,
    }


def adoption_payload(fixture_sha):
    return {
        "schema_version": "mysql_xp_compute00_exact_daemon_adoption_contract_v1",
        "status": "SEALED_ADOPTION_ONLY_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "unit": "ra-docker-mysql-xp-v2.service",
        "adopted_bundle_id": PARENT_ID,
        "adopted_bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "adopted_config_path": ADOPTED_CONFIG,
        "adopted_config_sha256": sha_bytes(b"{}\n"),
        "main_pid": MAIN_PID,
        "process_startticks": STARTTICKS,
        "cmdline_raw_sha256": CMDLINE_SHA256,
        "live_dockerd_argv": live_dockerd_argv(),
        "active_stdout_fixture": ACTIVE_FIXTURE,
        "active_stdout_sha256": fixture_sha,
        "active_required_present_property": "ExecStart",
        "active_exact_missing_empty_properties": list(ACTIVE_MISSING),
        "active_other_property_omission_prohibited": True,
        "systemd_run_prohibited": True,
        "unit_create_start_stop_restart_remove_reset_failed_prohibited": True,
        "exact_pid_startticks_cmdline_required": True,
        "exact_ppid_cpu_unit_cgroup_fragment_socket_pidfile_required": True,
        "exact_overlay2_cgroupfs_disk1_259_6_required": True,
        "empty_container_inventory_required": True,
        "postgresql_service_root_identity_unchanged_required": True,
        "postgresql_mutation_prohibited": True,
        "receipt_publication": "atomic_no_overwrite_or_identical_adoption",
        "database_container_launch_authorized": False,
        "client_container_launch_authorized": False,
        "workload_launch_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }


def patch_entrypoint(stage):
    shutil.rmtree(stage / "deployment")
    (stage / "adoption").mkdir()
    path = stage / ENTRYPOINT
    shutil.copy2(DEPLOYER_SOURCE, path); path.chmod(0o755)
    text = path.read_text(encoding="utf-8")
    text = replace_once(text,
        '''"""Deploy or adopt the exact XP-only Docker daemon on compute00.

This entrypoint has one narrowly scoped mutation: when the exact transient
unit does not exist, it may create and start that unit with systemd-run.
It never starts a database/container/workload and has no PostgreSQL mutation.
"""''',
        '''"""Adopt the exact already-running XP-only Docker daemon on compute00.

This content-addressed successor is readback-only except for its atomic
receipt. It cannot create, start, stop, restart, or remove a systemd unit and
never starts a database, container, client, or workload.
"""''', "adoption-only docstring")
    text = replace_once(text,
        'SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v7"',
        'SCHEMA = "%s"' % SCHEMA, "entrypoint schema")
    text = replace_once(text,
        'CONFIG_RELATIVE = "deployment/dockerd-empty-config.json"',
        'CONFIG_RELATIVE = "%s"' % CONFIG, "entrypoint config")
    old = '''ADOPT_EXISTING_ONLY = False
ADOPTED_CONFIG_PATH = ""
ADOPTED_BUNDLE_ID = ""
ADOPTED_MANIFEST_SHA256 = ""
ADOPTED_MAIN_PID = 0
ADOPTED_STARTTICKS = 0
ADOPTED_CMDLINE_SHA256 = ""
ADOPTED_ACTIVE_SYSTEMD_EXPECTED = None'''
    new = '''ADOPT_EXISTING_ONLY = True
ADOPTED_CONFIG_PATH = %r
ADOPTED_BUNDLE_ID = %r
ADOPTED_MANIFEST_SHA256 = %r
ADOPTED_MAIN_PID = %d
ADOPTED_STARTTICKS = %d
ADOPTED_CMDLINE_SHA256 = %r
ADOPTED_ACTIVE_SYSTEMD_EXPECTED = %r''' % (
        ADOPTED_CONFIG, PARENT_ID, PARENT_MANIFEST_SHA256, MAIN_PID,
        STARTTICKS, CMDLINE_SHA256, active_expected())
    text = replace_once(text, old, new, "adoption constants")
    text = replace_once(text,
        '"schema_version": "mysql_xp_compute00_daemon_deployment_receipt_v1",',
        '"schema_version": "mysql_xp_compute00_daemon_adoption_receipt_v1",',
        "receipt schema")
    text = replace_once(text,
        '"status": "PASS_XP_DEDICATED_DAEMON_ONLY",',
        '"status": "PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY",',
        "receipt status")
    path.write_text(text, encoding="utf-8")
    (stage / CONFIG).write_text("{}\n", encoding="ascii")


def runbook_text():
    return """# MySQL XP v10 exact existing-daemon adoption

This successor is readback/adoption-only. It cannot create, start, stop,
restart, remove, or reset a unit. Database/client containers are prohibited.
Workload launch is prohibited. PostgreSQL mutation and cross-engine comparison are
prohibited. The only persistent output is the atomic adoption receipt.

```sh
BUNDLE=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803/bundles/<v10_bundle_id>
ID=$(basename "$BUNDLE")
OUT=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_adoption_receipts/$ID.json
sudo python3 -B "$BUNDLE/adoption/mysql_xp_compute00_daemon_adopt_v1.py" --bundle "$BUNDLE" --authorize-exact-bundle-id "$ID" --output "$OUT"
jq -e '.status == "PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY" and .postgresql_mutation == false and .live_identity.docker.container_count == 0 and .workload_started == false and .formal_launch_authorized == false and .accepted_round_authorized == false and .accepted_rounds_credit == 0' "$OUT"
```

Bundle v9 `%s` must remain immutable because the live daemon's exact config
path is content-addressed into that bundle. Never invoke v9 again.
""" % PARENT_ID


def validate_target(target, bundle_id, require_name=True):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id", None)
    if (observed != bundle_id or (require_name and target.name != bundle_id)
            or sha_bytes(canonical(unsigned)) != bundle_id
            or files(target) != manifest.get("files")
            or sha(target / DAEMON_CONTRACT) != DAEMON_CONTRACT_SHA256):
        raise BuildError("v10 closure/daemon envelope differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target), XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", "-q", str(target / TEST)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=180)
    if completed.returncode:
        raise BuildError("v10 sealed tests failed:\n" + completed.stdout)
    return completed.stdout


def prepublish_validation(stage, bundle_id):
    with tempfile.TemporaryDirectory(prefix="xp-v10-content-addressed-", dir="/tmp") as temporary:
        candidate = Path(temporary) / bundle_id
        shutil.copytree(stage, candidate); freeze(candidate); candidate.chmod(0o555)
        return validate_target(candidate, bundle_id)


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("v9 predecessor manifest identity differs")
    parent = json.loads((PARENT / "bundle_manifest.json").read_text())
    if (parent.get("bundle_id") != PARENT_ID
            or parent.get("schema_version") != "mysql_xp_compute00_daemon_deployment_successor_v9"
            or parent.get("daemon_deployment_authorized") is not True
            or parent.get("workload_launch_authorized") is not False
            or parent.get("accepted_rounds_credit") != 0
            or sha(PARENT / DAEMON_CONTRACT) != DAEMON_CONTRACT_SHA256):
        raise BuildError("v9 predecessor authority/envelope differs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-adopt-v10-", dir=OUTPUT)); stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage); writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch_entrypoint(stage)
        shutil.rmtree(stage / "tests_v9"); (stage / "tests_v10").mkdir()
        shutil.copy2(TEST_SOURCE, stage / TEST)
        old_runbook = stage / "operator/mysql_xp_compute00_daemon_deployment_v9.md"
        old_runbook.unlink(); (stage / RUNBOOK).write_text(runbook_text(), encoding="utf-8")
        (stage / ACTIVE_FIXTURE).write_text(ACTIVE_STDOUT, encoding="utf-8")
        fixture_sha = sha(stage / ACTIVE_FIXTURE)
        (stage / FAILURE).write_bytes(canonical(failure_payload(fixture_sha)))
        (stage / ADOPTION_CONTRACT).write_bytes(canonical(adoption_payload(fixture_sha)))
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_daemon_adoption_v10.py")
        inventory = dict(parent.get("controlled_existing_runtime_inventory") or {})
        required = list(inventory.get("required_bundle_ids") or [])
        if PARENT_ID not in required: required.append(PARENT_ID)
        invalid = list(inventory.get("invalidated_daemon_deployment_bundle_ids") or [])
        if PARENT_ID not in invalid: invalid.append(PARENT_ID)
        inventory.update({"required_bundle_ids": required,
                          "invalidated_daemon_deployment_bundle_ids": invalid,
                          "live_daemon_config_dependency_bundle_id": PARENT_ID,
                          "accepted_rounds_credit": 0, "reuse_prohibited": True,
                          "new_bundle_no_overwrite_atomic_publication_required": True})
        manifest = {key: value for key, value in parent.items() if key not in ("bundle_id", "files")}
        manifest.update({
            "schema_version": SCHEMA, "status": STATUS,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "v9_deployment_reuse_prohibited": True,
            "v9_partial_failure_evidence": FAILURE,
            "active_systemd219_stdout_fixture": ACTIVE_FIXTURE,
            "active_systemd219_stdout_sha256": fixture_sha,
            "active_exact_missing_empty_properties": list(ACTIVE_MISSING),
            "active_execstart_presence_and_exact_argv_required": True,
            "daemon_contract_sha256": DAEMON_CONTRACT_SHA256,
            "daemon_envelope_unchanged": True,
            "daemon_deployment_authorized": False,
            "daemon_adoption_authorized": True,
            "systemd_run_authorized": False,
            "transactional_daemon_adoption": True,
            "daemon_adoption_entrypoint": ENTRYPOINT,
            "daemon_adoption_contract": ADOPTION_CONTRACT,
            "daemon_adoption_runbook": RUNBOOK,
            "controlled_existing_runtime_inventory": inventory,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "workload_launch_authorized": False,
            "database_container_launch_authorized": False,
            "client_container_launch_authorized": False,
            "formal_launch_authorized": False,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0, "remote_actions": 0,
            "files": files(stage),
        })
        bundle_id = sha_bytes(canonical(manifest)); manifest["bundle_id"] = bundle_id
        target = OUTPUT / bundle_id
        if target.exists(): return target, validate_target(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(stage / "bundle_manifest.json") + "  bundle_manifest.json\n", encoding="ascii")
        prepublish_validation(stage, bundle_id)
        freeze(stage); stage.chmod(0o755); os.rename(stage, target); target.chmod(0o555)
        return target, validate_target(target, bundle_id), "PUBLISHED_NEW"
    finally:
        if raw.exists(): writable(raw); shutil.rmtree(raw)


def main():
    target, output, mode = build()
    print(json.dumps({
        "schema_version": "mysql_xp_compute00_daemon_adoption_v10_build_result_v1",
        "status": "PASS_EXISTING_DAEMON_ADOPTION_ONLY_NOT_EXECUTED",
        "publish_mode": mode, "bundle": str(target), "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "tests": "PASS", "test_output": output.strip().splitlines()[-1],
        "daemon_contract_sha256": DAEMON_CONTRACT_SHA256,
        "daemon_deployment_authorized": False,
        "daemon_adoption_authorized": True, "systemd_run_authorized": False,
        "workload_launch_authorized": False, "formal_launch_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))
