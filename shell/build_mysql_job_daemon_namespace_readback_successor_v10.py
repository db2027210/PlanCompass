#!/usr/bin/env python3
"""Seal a JOB successor that reads root daemon namespaces through its own daemon."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "a35cca9d75b23eece066a62e1fe29adcf2d835bd967a12ed9a83ee54636c8bfc"
PARENT_SHA256 = "a76cb60803e9ec5413a69e4fd3b9d8066054edb50c801a85b7d56b2c670e963f"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v9" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v10"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v10"
AUTHORITY = "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"
CONTRACTS = (
    "qualification/mysql_job_dedicated_daemon_contract_v1.py",
    "authority/mysql_job_dedicated_daemon_contract_v1.py",
)
FIXTURE = "qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json"
MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v5.py"
MODULE_TEST = "tests/test_mysql_job_module_closure_successor_v5.py"
GRAPH_TEST = "tests/test_mysql_job_graphdriver_readback_v9.py"
NEW_TEST = "tests/test_mysql_job_daemon_namespace_readback_v10.py"
BUILDER = "authority/build_mysql_job_daemon_namespace_readback_successor_v10.py"
INVALIDATION = "authority/mysql_job_v9_proc_namespace_permission_invalidation_20260801.json"
FAILED_ATTEMPT = "job-v2-20260731t174850z-52dbba18e66699890069216f"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID
            or digest(path) != PARENT_SHA256
            or sha(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_job_raw_all_subset_three_level_successor_bundle_v9"
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("JOB v9 parent identity differs")
    for relative, expected in manifest["files"].items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("JOB v9 parent closure differs: " + relative)
    return manifest


NAMESPACE_HELPER = r'''
def _dedicated_daemon_root_namespaces(image_id, daemon_pid):
    """Read root dockerd namespace links via its sealed dedicated daemon."""
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise AuthorityError("dedicated daemon namespace helper image differs")
    if not isinstance(daemon_pid, int) or daemon_pid <= 0:
        raise AuthorityError("dedicated daemon namespace helper PID differs")
    helper_name = "ra_mysql_job_v2_daemon_ns_" + hashlib.sha256(
        (image_id + ":" + str(daemon_pid)).encode("utf-8")
    ).hexdigest()[:20]
    script = (
        'set -eu; pid="$1"; '
        'for key in mnt pid net user; do '
        'value="$(/usr/bin/readlink "/proc/$pid/ns/$key")"; '
        'case "$value" in "$key:["*[0-9]"]") :;; *) exit 44;; esac; '
        'printf "%s=%s\\n" "$key" "$value"; done'
    )
    result = _run([
        "docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock",
        "run", "--rm", "--name", helper_name,
        "--label", "ra.owner=mysql_algorithm_c_job_v2",
        "--label", "ra.role=daemon_namespace_readback_helper",
        "--network", "none", "--read-only", "--pid", "host", "--privileged",
        "--entrypoint", "/bin/sh", image_id,
        "-ceu", script, "namespace-helper", str(daemon_pid),
    ], timeout=60, check=False)
    namespaces = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or key not in ("mnt", "pid", "net", "user"):
            raise AuthorityError("dedicated daemon namespace helper output differs")
        if re.fullmatch(re.escape(key) + r":\[[0-9]+\]", value) is None:
            raise AuthorityError("dedicated daemon namespace identity differs")
        if key in namespaces:
            raise AuthorityError("dedicated daemon namespace helper duplicated key")
        namespaces[key] = value
    if result.returncode != 0 or set(namespaces) != {"mnt", "pid", "net", "user"}:
        raise AuthorityError(
            "dedicated daemon namespace helper failed: rc=%d stderr=%r stdout=%r"
            % (result.returncode, result.stderr, result.stdout)
        )
    return namespaces
'''


def patch_authority(payload):
    source = payload.decode("utf-8")
    source = replace_once(
        source,
        "\ndef _dedicated_daemon_readback(database_inspect, client_inspect):\n",
        NAMESPACE_HELPER + "\n\ndef _dedicated_daemon_readback(database_inspect, client_inspect):\n",
        "namespace helper insertion",
    )
    old = '    mount_devices = {}\n    containers = {}\n'
    new = (
        '    daemon_namespaces = _dedicated_daemon_root_namespaces(\n'
        '        database_inspect.get("Image"), daemon_pid\n'
        '    )\n'
        '    mount_devices = {}\n    containers = {}\n'
    )
    source = replace_once(source, old, new, "namespace helper call")
    old_dict = '"namespaces": {key: os.readlink(str(proc / "ns" / key)) for key in ("mnt", "pid", "net", "user")}, "docker_ancestry": False}'
    new_dict = '"namespaces": daemon_namespaces, "namespace_readback_transport": "dedicated_job_daemon_privileged_pid_host_helper", "docker_ancestry": False}'
    source = replace_once(source, old_dict, new_dict, "unsafe proc namespace readback")
    source = replace_once(
        source,
        '"mysql_job_dedicated_daemon_readback_fixture_v2"',
        '"mysql_job_dedicated_daemon_readback_fixture_v3"',
        "fixture schema",
    )
    return source.encode("utf-8")


def patch_contract(payload):
    source = payload.decode("utf-8")
    anchor = ' namespaces=daemon.get("namespaces") or {}\n'
    inserted = (
        ' if daemon.get("namespace_readback_transport")!='
        '"dedicated_job_daemon_privileged_pid_host_helper":raise Error('
        '"daemon namespace readback transport differs")\n'
        + anchor
    )
    source = replace_once(source, anchor, inserted, "namespace transport contract")
    source = replace_once(
        source,
        '"schema_version":"mysql_job_dedicated_daemon_readback_v2"',
        '"schema_version":"mysql_job_dedicated_daemon_readback_v3"',
        "readback schema",
    )
    return source.encode("utf-8")


def patch_fixture(payload):
    value = json.loads(payload)
    value["schema_version"] = "mysql_job_dedicated_daemon_readback_fixture_v3"
    value["daemon"]["namespace_readback_transport"] = (
        "dedicated_job_daemon_privileged_pid_host_helper"
    )
    return canonical(value)


def patch_materializer(payload):
    source = payload.decode("utf-8")
    source = replace_once(source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v9"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v10"',
        "materializer schema")
    source = replace_once(source,
        '"mysql_job_dedicated_daemon_materials_v9"',
        '"mysql_job_dedicated_daemon_materials_v10"',
        "materials schema")
    marker = '                "job-v2-20260731t172502z-911a3a1ea09e8d116ded0833",\n'
    return replace_once(source, marker, marker + '                "' + FAILED_ATTEMPT + '",\n',
                        "failed attempt denylist").encode("utf-8")


def patch_module_test(payload):
    source = payload.decode("utf-8")
    source = replace_once(source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v9"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v10"',
        "module test schema")
    source = replace_once(source, "JOB v9 successor", "JOB v10 successor",
                          "module test title")
    return source.encode("utf-8")


def patch_graph_test(payload):
    source = payload.decode("utf-8")
    source = replace_once(source,
        'mysql_job_raw_all_subset_three_level_successor_bundle_v9',
        'mysql_job_raw_all_subset_three_level_successor_bundle_v10',
        "graph test schema")
    source = replace_once(source,
        'mysql_job_dedicated_daemon_readback_v2',
        'mysql_job_dedicated_daemon_readback_v3',
        "graph readback schema")
    source = replace_once(source,
        'mysql_job_dedicated_daemon_readback_fixture_v2',
        'mysql_job_dedicated_daemon_readback_fixture_v3',
        "graph fixture schema")
    return source.encode("utf-8")


NEW_TEST_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def main():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 assert m["bundle_id"]==ROOT.name and hashlib.sha256(canon(u)).hexdigest()==ROOT.name
 assert m["schema_version"]=="mysql_job_raw_all_subset_three_level_successor_bundle_v10"
 for relative,expected in m["files"].items():
  p=ROOT/relative;assert p.is_file() and not p.is_symlink();assert hashlib.sha256(p.read_bytes()).hexdigest()==expected
 authority=(ROOT/"qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py").read_text()
 assert "_dedicated_daemon_root_namespaces" in authority
 assert "daemon_namespace_readback_helper" in authority
 assert "os.readlink(str(proc / \"ns\" / key))" not in authority
 assert "dedicated_job_daemon_privileged_pid_host_helper" in authority
 contract=(ROOT/"qualification/mysql_job_dedicated_daemon_contract_v1.py").read_text()
 assert "daemon namespace readback transport differs" in contract
if __name__=="__main__":main()
'''.encode("utf-8")


def invalidation():
    return {
        "schema_version": "mysql_job_proc_namespace_permission_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_SHA256,
        "attempt_token": FAILED_ATTEMPT,
        "failure": "UNPRIVILEGED_PROC_DOCKERD_NAMESPACE_READLINK_PERMISSION_DENIED",
        "workload_started": False,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "fresh_attempt_required": True,
        "postgresql_mutation": False,
    }


def freeze(path):
    for candidate in sorted(Path(path).rglob("*"), reverse=True):
        if candidate.is_symlink():
            raise BuildError("symlink in JOB v10")
        os.chmod(candidate, 0o444 if candidate.is_file() else 0o555)
    os.chmod(path, 0o555)


def main():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        if relative == "closure.json":
            continue
        path = PARENT / relative
        payload[relative] = path.read_bytes()
        modes[relative] = stat.S_IMODE(path.stat().st_mode)
    payload[AUTHORITY] = patch_authority(payload[AUTHORITY])
    for relative in CONTRACTS:
        payload[relative] = patch_contract(payload[relative])
        modes[relative] = 0o555
    payload[FIXTURE] = patch_fixture(payload[FIXTURE])
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER])
    payload[MODULE_TEST] = patch_module_test(payload[MODULE_TEST])
    payload[GRAPH_TEST] = patch_graph_test(payload[GRAPH_TEST])
    payload[NEW_TEST] = NEW_TEST_SOURCE
    payload[BUILDER] = Path(__file__).read_bytes()
    payload[INVALIDATION] = canonical(invalidation())
    modes.update({AUTHORITY:0o555,FIXTURE:0o444,MATERIALIZER:0o555,
                  MODULE_TEST:0o444,GRAPH_TEST:0o444,NEW_TEST:0o444,
                  BUILDER:0o444,INVALIDATION:0o444})
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v10",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA256,
        "daemon_namespace_readback": {
            "transport": "dedicated_job_daemon_privileged_pid_host_helper",
            "pid_namespace": "host",
            "network": "none",
            "read_only_rootfs": True,
            "helper_auto_remove": True,
            "postgresql_mutation_prohibited": True,
        },
        "invalidated_v9_attempt": INVALIDATION,
    })
    closure["files"] = {name:sha(data) for name,data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure)
    modes["closure.json"] = 0o444
    manifest = dict(parent)
    manifest.pop("bundle_id", None)
    manifest.update({
        "schema_version": SCHEMA,
        "status": "SEALED_ROOT_NAMESPACE_HELPER_DEBUG_BOOTSTRAP_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA256,
        "graphdriver_readback_schema": "mysql_job_dedicated_daemon_readback_v3",
        "invalidated_v9_attempt": FAILED_ATTEMPT,
        "invalidated_v9_credit": 0,
        "formal_launch_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "files": {name:sha(data) for name,data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / bundle_id
    if target.exists():
        raise BuildError("immutable JOB v10 already exists")
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    for relative, data in payload.items():
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        os.chmod(destination, modes[relative] & ~0o222)
    raw = canonical(manifest)
    (stage / "bundle_manifest.json").write_bytes(raw)
    (stage / "bundle_manifest.json.sha256").write_text(
        sha(raw) + "  bundle_manifest.json\n", encoding="ascii")
    freeze(stage)
    os.rename(stage, target)
    print(json.dumps({"status":manifest["status"],"bundle":str(target),
                      "bundle_id":bundle_id,
                      "manifest_sha256":digest(target/"bundle_manifest.json"),
                      "formal_launch_authorized":False,
                      "accepted_round_authorized":False},sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
