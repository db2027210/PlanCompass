#!/usr/bin/env python3
"""Build the minimal st_dev-convention correction for XP adoption v11."""
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
PARENT_ID = "59b782a4bcdba646635b36b9b5b1e9d349340ea62cbe2f28a9b8d8af68b4ae07"
PARENT_MANIFEST_SHA256 = "d0f05763f0e373f7641890eab05b66fc43b7f9f47ac5890dfea305cf88e16baa"
PARENT_ENTRYPOINT_SHA256 = "d521f870f3c379c86a98655d09540ef3beb4c6319ae1c149c5195ce4b412d288"
PARENT = ROOT / "artifacts/mysql_xp_compute00_daemon_adoption_v11" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_daemon_adoption_v12"
SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v12"
STATUS = "SEALED_XP_EXISTING_DAEMON_ST_DEV_CORRECTED_ADOPTION_ONLY_NOT_EXECUTED"
ENTRYPOINT = "adoption/mysql_xp_compute00_daemon_adopt_v1.py"
TEST_SOURCE = ROOT / "tests/test_mysql_xp_compute00_daemon_adoption_v12.py"
TEST = "tests_v12/test_mysql_xp_compute00_daemon_adoption_v12.py"
STAT_FIXTURE = "authority/mysql_xp_v11_socket_stat.stdout"
FAILURE = "authority/mysql_xp_v11_socket_device_adoption_failure_20260803.json"
CONTRACT = "authority/mysql_xp_compute00_socket_endpoint_contract_v3.json"
DAEMON_CONTRACT = "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
DAEMON_CONTRACT_SHA256 = "7fdb1c24e59743e81b70496dadcb1f5ea7f287b2a347ac2aa0f269fa5ce8afa3"
RUNBOOK = "operator/mysql_xp_compute00_daemon_adoption_v12.md"
STAT_STDOUT = "25 19 2915 660 0 0 socket\n"
MAIN_PID = 4149472
STARTTICKS = 37998304
CMDLINE_SHA256 = "eb036eba455e55f13d26da8355e7aa3f6c590d24fc2e95ba2e497ab8737ced27"


class BuildError(RuntimeError):
    pass


def load_v11_builder():
    path = ROOT / "shell/build_mysql_xp_compute00_daemon_adoption_v11.py"
    spec = importlib.util.spec_from_file_location("xp_v11_builder_for_v12", path)
    if spec is None or spec.loader is None:
        raise BuildError("v11 builder import differs")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V11 = load_v11_builder()
canonical, sha, sha_bytes = V11.canonical, V11.sha, V11.sha_bytes
writable, freeze, files = V11.writable, V11.freeze, V11.files


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(label + " patch point differs")
    return text.replace(old, new, 1)


def patch_entrypoint(stage):
    path = stage / ENTRYPOINT
    if sha(path) != PARENT_ENTRYPOINT_SHA256:
        raise BuildError("v11 entrypoint identity differs")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text, 'SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v11"',
        'SCHEMA = "%s"' % SCHEMA, "entrypoint schema")
    text = replace_once(
        text,
        "ADOPTED_SOCKET_FILESYSTEM_DEVICE = 19\n"
        "ADOPTED_SOCKET_FILESYSTEM_INODE = 2915",
        "ADOPTED_SOCKET_FILESYSTEM_DEVICE = 25\n"
        "ADOPTED_SOCKET_FILESYSTEM_DEVICE_HEX = \"19\"\n"
        "ADOPTED_SOCKET_FILESYSTEM_INODE = 2915",
        "Python decimal and GNU hex device constants")
    text = replace_once(
        text,
        '''if (state.st_dev != ADOPTED_SOCKET_FILESYSTEM_DEVICE
            or state.st_ino != ADOPTED_SOCKET_FILESYSTEM_INODE):''',
        '''if (state.st_dev != ADOPTED_SOCKET_FILESYSTEM_DEVICE
            or format(state.st_dev, "x") != ADOPTED_SOCKET_FILESYSTEM_DEVICE_HEX
            or state.st_ino != ADOPTED_SOCKET_FILESYSTEM_INODE):''',
        "separate decimal and hexadecimal device verification")
    text = replace_once(
        text,
        '''"filesystem": {"device": state.st_dev, "inode": state.st_ino,
                       "mode": "0660", "uid": 0, "gid": 0},''',
        '''"filesystem": {"device": state.st_dev,
                       "device_decimal": state.st_dev,
                       "device_hex": format(state.st_dev, "x"),
                       "inode": state.st_ino, "mode": "0660",
                       "uid": 0, "gid": 0},''',
        "separate receipt device representations")
    text = replace_once(
        text, '"schema_version": "mysql_xp_compute00_daemon_adoption_receipt_v2",',
        '"schema_version": "mysql_xp_compute00_daemon_adoption_receipt_v3",',
        "receipt schema")
    path.write_text(text, encoding="utf-8")


def failure_payload(stat_sha):
    return {
        "schema_version": "mysql_xp_v11_socket_device_adoption_failure_v1",
        "status": "INVALID_ZERO_CREDIT_READBACK_FAILURE_NO_MUTATION_DO_NOT_RERUN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "predecessor_entrypoint_sha256": PARENT_ENTRYPOINT_SHA256,
        "observed_error": "adopted filesystem socket identity differs",
        "root_diagnosis": (
            "GNU stat %D emitted hexadecimal 19, which v11 incorrectly used "
            "as decimal Python st_dev; GNU %d and Python st_dev are decimal 25"),
        "adoption_attempted_once": True,
        "systemd_mutation": False,
        "daemon_process_mutation": False,
        "postgresql_mutation": False,
        "deployment_receipt_created": False,
        "main_pid": MAIN_PID,
        "process_startticks": STARTTICKS,
        "cmdline_raw_sha256": CMDLINE_SHA256,
        "socket_stat_command_format": "%d %D %i %a %u %g %F",
        "socket_stat_stdout_fixture": STAT_FIXTURE,
        "socket_stat_stdout_sha256": stat_sha,
        "python_st_dev_decimal": 25,
        "gnu_stat_percent_d_decimal": 25,
        "gnu_stat_percent_D_hex": "19",
        "filesystem_socket_inode": 2915,
        "filesystem_socket_mode": "0660",
        "filesystem_socket_uid": 0,
        "filesystem_socket_gid": 0,
        "filesystem_socket_type": "socket",
        "kernel_endpoint_inode": 221659057,
        "observed_owner_fd": 3,
        "container_count": 0,
        "database_container_started": False,
        "client_container_started": False,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "authorization_reuse_prohibited": True,
    }


def contract_payload(stat_sha):
    return {
        "schema_version": "mysql_xp_compute00_socket_endpoint_contract_v3",
        "status": "SEALED_EXACT_ST_DEV_CONVENTION_AND_SOCKET_ENDPOINT_READBACK_ONLY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "socket_path": "/run/ra-docker-mysql-xp-v2.sock",
        "python_st_dev_decimal": 25,
        "gnu_stat_percent_d_decimal": 25,
        "gnu_stat_percent_D_hex": "19",
        "device_conversion_required": "int(percent_D,16)==st_dev==percent_d",
        "socket_stat_stdout_fixture": STAT_FIXTURE,
        "socket_stat_stdout_sha256": stat_sha,
        "filesystem_socket_inode": 2915,
        "filesystem_socket_mode": "0660",
        "filesystem_socket_uid": 0,
        "filesystem_socket_gid": 0,
        "kernel_endpoint_inode": 221659057,
        "observed_owner_fd": 3,
        "v11_socket_endpoint_logic_unchanged": True,
        "unique_stream_listen_path_required": True,
        "endpoint_inode_must_be_owned_by_main_pid_fd": True,
        "filesystem_inode_must_not_be_compared_to_endpoint_inode": True,
        "daemon_adoption_authorized": True,
        "daemon_deployment_authorized": False,
        "systemd_run_authorized": False,
        "workload_launch_authorized": False,
        "accepted_rounds_credit": 0,
    }


def runbook_text():
    return """# MySQL XP v12 exact existing-daemon adoption

This minimal successor uses Python `st_dev` decimal 25 and records GNU
`stat %%D` hexadecimal `19` separately. All v11 UNIX LISTEN endpoint and PID
FD ownership gates remain unchanged. It cannot mutate any unit or process.
Workload launch is prohibited. PostgreSQL mutation and cross-engine comparison are prohibited.

```sh
BUNDLE=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803/bundles/<v12_bundle_id>
ID=$(basename "$BUNDLE")
OUT=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_adoption_receipts/$ID.json
sudo python3 -B "$BUNDLE/adoption/mysql_xp_compute00_daemon_adopt_v1.py" --bundle "$BUNDLE" --authorize-exact-bundle-id "$ID" --output "$OUT"
jq -e '.status == "PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY" and .daemon_deployment_authorized == false and .systemd_run_authorized == false and .live_identity.socket.filesystem.device_decimal == 25 and .live_identity.socket.filesystem.device_hex == "19" and .live_identity.socket.endpoint.kernel_endpoint_inode == 221659057 and .live_identity.socket.owner_fds == [3] and .live_identity.docker.container_count == 0 and .workload_started == false and .accepted_rounds_credit == 0' "$OUT"
```

Never invoke v11 `%s` again; its one-shot authorization is sealed as failed,
zero-credit, no-mutation, and no-receipt.
""" % PARENT_ID


def validate_target(target, bundle_id, require_name=True):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    observed = unsigned.pop("bundle_id", None)
    if (observed != bundle_id or (require_name and target.name != bundle_id)
            or sha_bytes(canonical(unsigned)) != bundle_id
            or files(target) != manifest.get("files")
            or sha(target / DAEMON_CONTRACT) != DAEMON_CONTRACT_SHA256):
        raise BuildError("v12 content-addressed closure/envelope differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target), XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(target / TEST)], cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    if completed.returncode:
        raise BuildError("v12 sealed tests failed:\n" + completed.stdout)
    return completed.stdout


def prepublish_validation(stage, bundle_id):
    with tempfile.TemporaryDirectory(
            prefix="xp-v12-content-addressed-", dir="/tmp") as temporary:
        candidate = Path(temporary) / bundle_id
        shutil.copytree(stage, candidate)
        freeze(candidate)
        candidate.chmod(0o555)
        return validate_target(candidate, bundle_id)


def verify_parent():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("v11 predecessor manifest identity differs")
    parent = json.loads((PARENT / "bundle_manifest.json").read_text())
    if (parent.get("bundle_id") != PARENT_ID
            or parent.get("schema_version")
            != "mysql_xp_compute00_daemon_adoption_successor_v11"
            or parent.get("daemon_adoption_authorized") is not True
            or parent.get("daemon_deployment_authorized") is not False
            or parent.get("systemd_run_authorized") is not False
            or parent.get("workload_launch_authorized") is not False
            or parent.get("accepted_rounds_credit") != 0
            or files(PARENT) != parent.get("files")
            or sha(PARENT / ENTRYPOINT) != PARENT_ENTRYPOINT_SHA256
            or sha(PARENT / DAEMON_CONTRACT) != DAEMON_CONTRACT_SHA256):
        raise BuildError("v11 predecessor closure/authority/envelope differs")
    return parent


def build():
    parent = verify_parent()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-adopt-v12-", dir=OUTPUT))
    stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage)
        writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch_entrypoint(stage)
        shutil.rmtree(stage / "tests_v11")
        (stage / "tests_v12").mkdir()
        shutil.copy2(TEST_SOURCE, stage / TEST)
        (stage / "operator/mysql_xp_compute00_daemon_adoption_v11.md").unlink()
        (stage / RUNBOOK).write_text(runbook_text(), encoding="utf-8")
        (stage / "builder/build_mysql_xp_compute00_daemon_adoption_v11.py").unlink()
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_daemon_adoption_v12.py")
        (stage / STAT_FIXTURE).write_text(STAT_STDOUT, encoding="ascii")
        stat_sha = sha(stage / STAT_FIXTURE)
        (stage / FAILURE).write_bytes(canonical(failure_payload(stat_sha)))
        (stage / CONTRACT).write_bytes(canonical(contract_payload(stat_sha)))
        inventory = dict(parent.get("controlled_existing_runtime_inventory") or {})
        required = list(inventory.get("required_bundle_ids") or [])
        if PARENT_ID not in required:
            required.append(PARENT_ID)
        invalid = list(inventory.get("invalidated_daemon_adoption_bundle_ids") or [])
        if PARENT_ID not in invalid:
            invalid.append(PARENT_ID)
        inventory.update({
            "required_bundle_ids": required,
            "invalidated_daemon_adoption_bundle_ids": invalid,
            "live_socket_st_dev_decimal": 25,
            "live_socket_stat_percent_D_hex": "19",
            "live_socket_filesystem_inode": 2915,
            "live_socket_endpoint_inode": 221659057,
            "accepted_rounds_credit": 0,
            "reuse_prohibited": True,
            "new_bundle_no_overwrite_atomic_publication_required": True,
        })
        manifest = {key: value for key, value in parent.items()
                    if key not in ("bundle_id", "files")}
        manifest.update({
            "schema_version": SCHEMA,
            "status": STATUS,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "predecessor_entrypoint_sha256": PARENT_ENTRYPOINT_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "v11_adoption_reuse_prohibited": True,
            "v11_one_shot_failure_evidence": FAILURE,
            "socket_endpoint_contract": CONTRACT,
            "socket_stat_stdout_fixture": STAT_FIXTURE,
            "socket_stat_stdout_sha256": stat_sha,
            "socket_device_python_st_dev_decimal": 25,
            "socket_device_gnu_stat_percent_D_hex": "19",
            "v12_minimal_change": "correct_st_dev_decimal_and_record_percent_D_hex",
            "v11_socket_endpoint_logic_unchanged": True,
            "daemon_contract_sha256": DAEMON_CONTRACT_SHA256,
            "daemon_envelope_unchanged": True,
            "daemon_deployment_authorized": False,
            "daemon_adoption_authorized": True,
            "systemd_run_authorized": False,
            "transactional_daemon_adoption": True,
            "daemon_adoption_entrypoint": ENTRYPOINT,
            "daemon_adoption_contract": CONTRACT,
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
            "accepted_rounds_credit": 0,
            "remote_actions": 0,
            "files": files(stage),
        })
        bundle_id = sha_bytes(canonical(manifest))
        manifest["bundle_id"] = bundle_id
        target = OUTPUT / bundle_id
        if target.exists():
            return target, validate_target(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(stage / "bundle_manifest.json") + "  bundle_manifest.json\n",
            encoding="ascii")
        prepublish_validation(stage, bundle_id)
        freeze(stage)
        stage.chmod(0o755)
        os.rename(stage, target)
        target.chmod(0o555)
        return target, validate_target(target, bundle_id), "PUBLISHED_NEW"
    finally:
        if raw.exists():
            writable(raw)
            shutil.rmtree(raw)


def main():
    target, output, mode = build()
    print(json.dumps({
        "schema_version": "mysql_xp_compute00_daemon_adoption_v12_build_result_v1",
        "status": "PASS_ST_DEV_CORRECTED_ADOPTION_ONLY_NOT_EXECUTED",
        "publish_mode": mode,
        "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "tests": "PASS",
        "test_output": output.strip().splitlines()[-1],
        "daemon_contract_sha256": DAEMON_CONTRACT_SHA256,
        "python_st_dev_decimal": 25,
        "gnu_stat_percent_D_hex": "19",
        "daemon_deployment_authorized": False,
        "daemon_adoption_authorized": True,
        "systemd_run_authorized": False,
        "workload_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))
