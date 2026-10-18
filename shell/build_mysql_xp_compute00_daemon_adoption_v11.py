#!/usr/bin/env python3
"""Build the socket-endpoint-correct v11 successor for the live XP daemon."""
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
PARENT_ID = "671cbb6f98c9b223f46ad7f5499311c6aebd4a87ee7f7ce1e9fcca5169780b96"
PARENT_MANIFEST_SHA256 = "2fa6a0a5839006619e51cce8babb78bd5f6fb7971dbdabb1aee6aa082d38de23"
PARENT = ROOT / "artifacts/mysql_xp_compute00_daemon_adoption_v10" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_daemon_adoption_v11"
SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v11"
STATUS = "SEALED_XP_EXISTING_DAEMON_ENDPOINT_ADOPTION_ONLY_NOT_EXECUTED"
TEST_SOURCE = ROOT / "tests/test_mysql_xp_compute00_daemon_adoption_v11.py"
ENTRYPOINT = "adoption/mysql_xp_compute00_daemon_adopt_v1.py"
TEST = "tests_v11/test_mysql_xp_compute00_daemon_adoption_v11.py"
FIXTURE = "authority/mysql_xp_v10_proc_4149472_net_unix.stdout"
FAILURE = "authority/mysql_xp_v10_socket_inode_adoption_failure_20260803.json"
SOCKET_CONTRACT = "authority/mysql_xp_compute00_socket_endpoint_contract_v2.json"
DAEMON_CONTRACT = "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
DAEMON_CONTRACT_SHA256 = "7fdb1c24e59743e81b70496dadcb1f5ea7f287b2a347ac2aa0f269fa5ce8afa3"
RUNBOOK = "operator/mysql_xp_compute00_daemon_adoption_v11.md"
MAIN_PID = 4149472
STARTTICKS = 37998304
CMDLINE_SHA256 = "eb036eba455e55f13d26da8355e7aa3f6c590d24fc2e95ba2e497ab8737ced27"
SOCKET_PATH = "/run/ra-docker-mysql-xp-v2.sock"
SOCKET_FS_DEVICE = 19
SOCKET_FS_INODE = 2915
SOCKET_ENDPOINT_INODE = 221659057
SOCKET_OWNER_FD = 3
PROC_NET_UNIX = (
    "Num       RefCount Protocol Flags    Type St Inode Path\n"
    "ffff9eadc4f7d500: 00000002 00000000 00010000 0001 01 221659057 "
    "/run/ra-docker-mysql-xp-v2.sock\n"
)


class BuildError(RuntimeError):
    pass


def load_v10_builder():
    path = ROOT / "shell/build_mysql_xp_compute00_daemon_adoption_v10.py"
    spec = importlib.util.spec_from_file_location("xp_v10_builder_for_v11", path)
    if spec is None or spec.loader is None:
        raise BuildError("v10 builder import differs")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V10 = load_v10_builder()
canonical, sha, sha_bytes = V10.canonical, V10.sha, V10.sha_bytes
writable, freeze, files = V10.writable, V10.freeze, V10.files


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(label + " patch point differs")
    return text.replace(old, new, 1)


OLD_SOCKET_OWNER = '''def socket_owner(pid):
    state = os.stat(SOCKET, follow_symlinks=False)
    if (not stat.S_ISSOCK(state.st_mode) or state.st_uid != 0
            or state.st_gid != 0 or stat.S_IMODE(state.st_mode) != 0o660):
        raise DeploymentError("dedicated socket metadata differs")
    identity = "socket:[%d]" % state.st_ino
    try:
        targets = {os.readlink(path) for path in
                   (Path("/proc") / str(pid) / "fd").iterdir()}
    except OSError as exc:
        raise DeploymentError("daemon FD inventory differs") from exc
    if identity not in targets:
        raise DeploymentError("dedicated socket is not owned by MainPID")
    return {"path": SOCKET, "inode": state.st_ino, "mode": "0660",
            "uid": 0, "gid": 0, "owned_by_main_pid": True}
'''


NEW_SOCKET_OWNER = '''def parse_proc_net_unix(raw):
    if "\\x00" in raw:
        raise DeploymentError("proc net unix inventory malformed")
    lines = raw.splitlines()
    header = ("Num", "RefCount", "Protocol", "Flags", "Type", "St",
              "Inode", "Path")
    if not lines or tuple(lines[0].split()) != header:
        raise DeploymentError("proc net unix inventory malformed header")
    matches = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(maxsplit=7)
        if len(fields) not in (7, 8):
            raise DeploymentError("proc net unix inventory malformed row")
        address = fields[0]
        if not address.endswith(":"):
            raise DeploymentError("proc net unix inventory malformed address")
        try:
            int(address[:-1], 16)
            ref_count = int(fields[1], 16)
            protocol = int(fields[2], 16)
            flags = int(fields[3], 16)
            socket_type = int(fields[4], 16)
            socket_state = int(fields[5], 16)
            inode = int(fields[6], 10)
        except ValueError as exc:
            raise DeploymentError("proc net unix inventory malformed numeric field") from exc
        path = fields[7] if len(fields) == 8 else None
        if path and path.startswith("/"):
            normalized = os.path.normpath(path)
            if normalized == SOCKET and path != SOCKET:
                raise DeploymentError("proc net unix socket path escape prohibited")
        if path == SOCKET:
            matches.append({
                "path": path, "kernel_endpoint_inode": inode,
                "ref_count": ref_count, "protocol": protocol,
                "flags": flags, "type": socket_type, "state": socket_state,
            })
    if len(matches) > 1:
        raise DeploymentError("duplicate dedicated socket path in proc net unix")
    if not matches:
        raise DeploymentError("dedicated socket path missing from proc net unix")
    endpoint = matches[0]
    if (endpoint["protocol"] != 0 or endpoint["flags"] != 0x10000
            or endpoint["type"] != 1 or endpoint["state"] != 1):
        raise DeploymentError("dedicated socket is not unique UNIX STREAM LISTEN endpoint")
    if endpoint["kernel_endpoint_inode"] != ADOPTED_SOCKET_ENDPOINT_INODE:
        raise DeploymentError("adopted socket endpoint inode differs")
    return endpoint


def endpoint_owner_fds(targets, endpoint_inode):
    owners = []
    for name, target in targets.items():
        if not str(name).isdigit():
            raise DeploymentError("daemon FD inventory malformed name")
        if target.startswith("socket:"):
            match = re.fullmatch(r"socket:\\[(\\d+)\\]", target)
            if match is None:
                raise DeploymentError("daemon socket FD inventory malformed")
            if int(match.group(1)) == endpoint_inode:
                owners.append(int(name))
    if not owners:
        raise DeploymentError("dedicated socket endpoint is not owned by MainPID")
    return sorted(owners)


def socket_owner(pid):
    # Filesystem socket inode identifies the pathname node, not the kernel endpoint.
    state = os.stat(SOCKET, follow_symlinks=False)
    if (not stat.S_ISSOCK(state.st_mode) or state.st_uid != 0
            or state.st_gid != 0 or stat.S_IMODE(state.st_mode) != 0o660):
        raise DeploymentError("dedicated socket metadata differs")
    if (state.st_dev != ADOPTED_SOCKET_FILESYSTEM_DEVICE
            or state.st_ino != ADOPTED_SOCKET_FILESYSTEM_INODE):
        raise DeploymentError("adopted filesystem socket identity differs")
    proc = Path("/proc") / str(pid)
    try:
        net_unix = (proc / "net/unix").read_text(encoding="ascii")
        targets = {entry.name: os.readlink(entry)
                   for entry in (proc / "fd").iterdir()}
    except OSError as exc:
        raise DeploymentError("daemon socket inventory differs") from exc
    endpoint = parse_proc_net_unix(net_unix)
    owners = endpoint_owner_fds(targets, endpoint["kernel_endpoint_inode"])
    return {
        "path": SOCKET,
        "filesystem": {"device": state.st_dev, "inode": state.st_ino,
                       "mode": "0660", "uid": 0, "gid": 0},
        "endpoint": endpoint,
        "owner_fds": owners,
        "owned_by_main_pid": True,
        "filesystem_inode_compared_to_endpoint_inode": False,
    }
'''


def patch_entrypoint(stage):
    path = stage / ENTRYPOINT
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text, 'SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v10"',
        'SCHEMA = "%s"' % SCHEMA, "entrypoint schema")
    marker = 'ADOPTED_CMDLINE_SHA256 = %r\n' % CMDLINE_SHA256
    addition = marker + (
        "ADOPTED_SOCKET_FILESYSTEM_DEVICE = %d\n"
        "ADOPTED_SOCKET_FILESYSTEM_INODE = %d\n"
        "ADOPTED_SOCKET_ENDPOINT_INODE = %d\n" % (
            SOCKET_FS_DEVICE, SOCKET_FS_INODE, SOCKET_ENDPOINT_INODE))
    text = replace_once(text, marker, addition, "socket identity constants")
    text = replace_once(text, OLD_SOCKET_OWNER, NEW_SOCKET_OWNER,
                        "socket endpoint ownership")
    text = replace_once(
        text, '"schema_version": "mysql_xp_compute00_daemon_adoption_receipt_v1",',
        '"schema_version": "mysql_xp_compute00_daemon_adoption_receipt_v2",',
        "receipt schema")
    path.write_text(text, encoding="utf-8")


def failure_payload(fixture_sha):
    return {
        "schema_version": "mysql_xp_v10_socket_inode_adoption_failure_v1",
        "status": "INVALID_ZERO_CREDIT_READBACK_FAILURE_NO_MUTATION_DO_NOT_RERUN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "observed_error": "dedicated socket is not owned by MainPID",
        "root_diagnosis": (
            "filesystem socket inode 2915 was incorrectly compared with "
            "kernel UNIX endpoint inode 221659057"),
        "adoption_attempted_once": True,
        "systemd_mutation": False,
        "daemon_process_mutation": False,
        "postgresql_mutation": False,
        "deployment_receipt_created": False,
        "main_pid": MAIN_PID,
        "process_startticks": STARTTICKS,
        "cmdline_raw_sha256": CMDLINE_SHA256,
        "socket_filesystem": {
            "path": SOCKET_PATH, "device": SOCKET_FS_DEVICE,
            "inode": SOCKET_FS_INODE, "mode": "0660", "uid": 0, "gid": 0,
            "type": "socket",
        },
        "proc_net_unix_fixture": FIXTURE,
        "proc_net_unix_fixture_sha256": fixture_sha,
        "socket_endpoint_inode": SOCKET_ENDPOINT_INODE,
        "socket_owner_fd": SOCKET_OWNER_FD,
        "other_observed_endpoint_inodes": [221659069, 221659073],
        "dedicated_docker_info_succeeded": True,
        "docker_name": "compute00",
        "docker_data_root": (
            "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/"
            "docker_ra_xp_v29"),
        "storage_driver": "overlay2",
        "cgroup_driver": "cgroupfs",
        "container_count": 0,
        "database_container_started": False,
        "client_container_started": False,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "authorization_reuse_prohibited": True,
    }


def socket_contract_payload(fixture_sha):
    return {
        "schema_version": "mysql_xp_compute00_socket_endpoint_contract_v2",
        "status": "SEALED_EXACT_SOCKET_ENDPOINT_READBACK_ONLY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "socket_path": SOCKET_PATH,
        "filesystem_socket_device": SOCKET_FS_DEVICE,
        "filesystem_socket_inode": SOCKET_FS_INODE,
        "filesystem_socket_mode": "0660",
        "filesystem_socket_uid": 0,
        "filesystem_socket_gid": 0,
        "kernel_endpoint_inode": SOCKET_ENDPOINT_INODE,
        "observed_owner_fd": SOCKET_OWNER_FD,
        "proc_net_unix_fixture": FIXTURE,
        "proc_net_unix_fixture_sha256": fixture_sha,
        "unique_stream_listen_path_required": True,
        "endpoint_inode_must_be_owned_by_main_pid_fd": True,
        "filesystem_inode_must_not_be_compared_to_endpoint_inode": True,
        "duplicate_path_prohibited": True,
        "lexical_path_escape_prohibited": True,
        "malformed_inventory_prohibited": True,
        "daemon_adoption_authorized": True,
        "daemon_deployment_authorized": False,
        "systemd_run_authorized": False,
        "workload_launch_authorized": False,
        "accepted_rounds_credit": 0,
    }


def runbook_text():
    return """# MySQL XP v11 exact existing-daemon endpoint adoption

This is a readback-only successor for the already-running exact daemon.
It validates filesystem socket metadata separately from the kernel UNIX
LISTEN endpoint, then proves that endpoint is held by MainPID. It cannot
create, start, stop, restart, remove, or reset any unit. Workload launch is prohibited.
PostgreSQL mutation and cross-engine comparison are prohibited.

```sh
BUNDLE=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803/bundles/<v11_bundle_id>
ID=$(basename "$BUNDLE")
OUT=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_adoption_receipts/$ID.json
sudo python3 -B "$BUNDLE/adoption/mysql_xp_compute00_daemon_adopt_v1.py" --bundle "$BUNDLE" --authorize-exact-bundle-id "$ID" --output "$OUT"
jq -e '.status == "PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY" and .daemon_deployment_authorized == false and .daemon_adoption_authorized == true and .systemd_run_authorized == false and .live_identity.socket.endpoint.kernel_endpoint_inode == 221659057 and .live_identity.socket.owner_fds == [3] and .live_identity.docker.container_count == 0 and .workload_started == false and .accepted_rounds_credit == 0' "$OUT"
```

Never invoke v10 `%s` again; its one-shot authorization is sealed as failed,
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
        raise BuildError("v11 content-addressed closure/envelope differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target),
               XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(target / TEST)], cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=180)
    if completed.returncode:
        raise BuildError("v11 sealed tests failed:\n" + completed.stdout)
    return completed.stdout


def prepublish_validation(stage, bundle_id):
    with tempfile.TemporaryDirectory(
            prefix="xp-v11-content-addressed-", dir="/tmp") as temporary:
        candidate = Path(temporary) / bundle_id
        shutil.copytree(stage, candidate)
        freeze(candidate)
        candidate.chmod(0o555)
        return validate_target(candidate, bundle_id)


def verify_parent():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("v10 predecessor manifest identity differs")
    parent = json.loads((PARENT / "bundle_manifest.json").read_text())
    if (parent.get("bundle_id") != PARENT_ID
            or parent.get("schema_version")
            != "mysql_xp_compute00_daemon_adoption_successor_v10"
            or parent.get("daemon_adoption_authorized") is not True
            or parent.get("daemon_deployment_authorized") is not False
            or parent.get("systemd_run_authorized") is not False
            or parent.get("workload_launch_authorized") is not False
            or parent.get("accepted_rounds_credit") != 0
            or files(PARENT) != parent.get("files")
            or sha(PARENT / DAEMON_CONTRACT) != DAEMON_CONTRACT_SHA256):
        raise BuildError("v10 predecessor closure/authority/envelope differs")
    return parent


def build():
    parent = verify_parent()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-adopt-v11-", dir=OUTPUT))
    stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage)
        writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch_entrypoint(stage)
        shutil.rmtree(stage / "tests_v10")
        (stage / "tests_v11").mkdir()
        shutil.copy2(TEST_SOURCE, stage / TEST)
        (stage / "operator/mysql_xp_compute00_daemon_adoption_v10.md").unlink()
        (stage / RUNBOOK).write_text(runbook_text(), encoding="utf-8")
        (stage / "builder/build_mysql_xp_compute00_daemon_adoption_v10.py").unlink()
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_daemon_adoption_v11.py")
        (stage / FIXTURE).write_text(PROC_NET_UNIX, encoding="ascii")
        fixture_sha = sha(stage / FIXTURE)
        (stage / FAILURE).write_bytes(canonical(failure_payload(fixture_sha)))
        (stage / SOCKET_CONTRACT).write_bytes(
            canonical(socket_contract_payload(fixture_sha)))
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
            "live_daemon_main_pid": MAIN_PID,
            "live_daemon_process_startticks": STARTTICKS,
            "live_daemon_cmdline_sha256": CMDLINE_SHA256,
            "live_socket_filesystem_inode": SOCKET_FS_INODE,
            "live_socket_endpoint_inode": SOCKET_ENDPOINT_INODE,
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
            "supersedes_bundle_id": PARENT_ID,
            "v10_adoption_reuse_prohibited": True,
            "v10_one_shot_failure_evidence": FAILURE,
            "socket_endpoint_contract": SOCKET_CONTRACT,
            "proc_net_unix_fixture": FIXTURE,
            "proc_net_unix_fixture_sha256": fixture_sha,
            "socket_filesystem_metadata_validated_separately": True,
            "socket_endpoint_fd_ownership_required": True,
            "socket_filesystem_inode_comparison_to_endpoint_prohibited": True,
            "daemon_contract_sha256": DAEMON_CONTRACT_SHA256,
            "daemon_envelope_unchanged": True,
            "daemon_deployment_authorized": False,
            "daemon_adoption_authorized": True,
            "systemd_run_authorized": False,
            "transactional_daemon_adoption": True,
            "daemon_adoption_entrypoint": ENTRYPOINT,
            "daemon_adoption_contract": SOCKET_CONTRACT,
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
        "schema_version": "mysql_xp_compute00_daemon_adoption_v11_build_result_v1",
        "status": "PASS_SOCKET_ENDPOINT_ADOPTION_ONLY_NOT_EXECUTED",
        "publish_mode": mode,
        "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "tests": "PASS",
        "test_output": output.strip().splitlines()[-1],
        "daemon_contract_sha256": DAEMON_CONTRACT_SHA256,
        "daemon_deployment_authorized": False,
        "daemon_adoption_authorized": True,
        "systemd_run_authorized": False,
        "workload_launch_authorized": False,
        "formal_launch_authorized": False,
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
