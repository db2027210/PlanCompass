#!/usr/bin/env python3
"""Seal the JOB raw-findmnt/overlay-aware GraphDriver successor."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "52dbd4b4ba8a57896c2388c5cf6824bab105333207b9a0c8232dc3980b132200"
PARENT_SHA256 = "490cc1c15de91907fcb2a9536ec00c875afd194383b4f510a59405c1fc8548c0"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v8" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v9"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v9"
AUTHORITY = "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"
CONTRACTS = (
    "qualification/mysql_job_dedicated_daemon_contract_v1.py",
    "authority/mysql_job_dedicated_daemon_contract_v1.py",
)
FIXTURE = "qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json"
MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v5.py"
TEST = "tests/test_mysql_job_module_closure_successor_v5.py"
NEW_TEST = "tests/test_mysql_job_graphdriver_readback_v9.py"
BUILDER = "authority/build_mysql_job_graphdriver_readback_successor_v9.py"
INVALIDATION = "authority/mysql_job_v8_findmnt_padding_invalidation_20260801.json"
PROBE = "authority/mysql_job_graphdriver_raw_probe_20260801.json"
FAILED_ATTEMPT = "job-v2-20260731t172502z-911a3a1ea09e8d116ded0833"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


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
    if (
        manifest.get("bundle_id") != PARENT_ID
        or digest(path) != PARENT_SHA256
        or sha(canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_job_raw_all_subset_three_level_successor_bundle_v8"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("JOB v8 parent identity differs")
    for relative, expected in manifest["files"].items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("JOB v8 parent closure differs: " + relative)
    return manifest


OLD_HELPER = r'''def _dedicated_daemon_root_findmnt_major_minor(image_id, path):
    """Read one root-owned daemon path device without exposing host root."""
    nsenter_stat = NSENTER_PATH.stat(follow_symlinks=False)
    if (NSENTER_PATH.is_symlink() or not NSENTER_PATH.is_file()
            or sha256(NSENTER_PATH) != NSENTER_SHA256
            or nsenter_stat.st_uid != 0 or nsenter_stat.st_gid != 0
            or stat.S_IMODE(nsenter_stat.st_mode) != 0o755):
        raise AuthorityError("manifest-bound host nsenter identity differs")
    helper_name = "ra_mysql_job_v2_findmnt_" + hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()[:20]
    script = (
        'set -eu; paths="$1"; oldifs="$IFS"; IFS=:; set -- $paths; '
        'IFS="$oldifs"; device=""; '
        'for target do value="$(/usr/local/bin/nsenter -t 1 -m '
        '--root=/proc/1/root --wd=/proc/1/root /usr/bin/findmnt -n '
        '-o MAJ:MIN --target "$target")"; '
        'case "$value" in ""|*[!0-9:]*) exit 42;; esac; '
        'if [ -n "$device" ] && [ "$device" != "$value" ]; then exit 43; fi; '
        'device="$value"; done; printf "%s\\n" "$device"'
    )
    result = _run([
        "docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock",
        "run", "--rm", "--name", helper_name,
        "--label", "ra.owner=mysql_algorithm_c_job_v2",
        "--label", "ra.role=host_graphdriver_findmnt_helper",
        "--network", "none", "--read-only", "--pid", "host", "--privileged",
        "--mount", "type=bind,src=/usr/bin/nsenter,dst=/usr/local/bin/nsenter,readonly",
        "--entrypoint", "/bin/sh", image_id,
        "-ceu", script, "findmnt-helper", str(path),
    ], timeout=60, check=False)
    value = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9]+:[0-9]+", value) is None:
        raise AuthorityError(
            "dedicated JOB graphdriver findmnt helper failed: stderr=%r stdout=%r"
            % (result.stderr, result.stdout)
        )
    return value
'''


NEW_HELPER = r'''def _dedicated_daemon_root_findmnt_major_minor(image_id, path):
    """Read exact device and filesystem for one or more daemon paths."""
    nsenter_stat = NSENTER_PATH.stat(follow_symlinks=False)
    if (NSENTER_PATH.is_symlink() or not NSENTER_PATH.is_file()
            or sha256(NSENTER_PATH) != NSENTER_SHA256
            or nsenter_stat.st_uid != 0 or nsenter_stat.st_gid != 0
            or stat.S_IMODE(nsenter_stat.st_mode) != 0o755):
        raise AuthorityError("manifest-bound host nsenter identity differs")
    helper_name = "ra_mysql_job_v2_findmnt_" + hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()[:20]
    script = (
        'set -eu; paths="$1"; oldifs="$IFS"; IFS=:; set -- $paths; '
        'IFS="$oldifs"; signature=""; '
        'for target do value="$(/usr/local/bin/nsenter -t 1 -m '
        '--root=/proc/1/root --wd=/proc/1/root /usr/bin/findmnt -rn '
        '-o MAJ:MIN,FSTYPE --target "$target")"; '
        'device="${value%% *}"; fstype="${value#* }"; '
        'case "$device" in ""|*[!0-9:]*) exit 42;; esac; '
        'case "$fstype" in ""|*[!A-Za-z0-9._-]*) exit 42;; esac; '
        'value="$device $fstype"; '
        'if [ -n "$signature" ] && [ "$signature" != "$value" ]; then exit 43; fi; '
        'signature="$value"; done; printf "%s\\n" "$signature"'
    )
    result = _run([
        "docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock",
        "run", "--rm", "--name", helper_name,
        "--label", "ra.owner=mysql_algorithm_c_job_v2",
        "--label", "ra.role=host_graphdriver_findmnt_helper",
        "--network", "none", "--read-only", "--pid", "host", "--privileged",
        "--mount", "type=bind,src=/usr/bin/nsenter,dst=/usr/local/bin/nsenter,readonly",
        "--entrypoint", "/bin/sh", image_id,
        "-ceu", script, "findmnt-helper", str(path),
    ], timeout=60, check=False)
    pieces = result.stdout.split()
    if (
        result.returncode != 0
        or len(pieces) != 2
        or re.fullmatch(r"[0-9]+:[0-9]+", pieces[0]) is None
        or re.fullmatch(r"[A-Za-z0-9._-]+", pieces[1]) is None
    ):
        raise AuthorityError(
            "dedicated JOB graphdriver findmnt helper failed: rc=%d path=%r stderr=%r stdout=%r"
            % (result.returncode, str(path), result.stderr, result.stdout)
        )
    return {"major_minor": pieces[0], "fstype": pieces[1]}
'''


def patch_authority(payload):
    source = payload.decode("utf-8")
    source = replace_once(source, OLD_HELPER, NEW_HELPER, "raw findmnt helper")
    source = replace_once(
        source,
        '"mysql_job_dedicated_daemon_readback_fixture_v1"',
        '"mysql_job_dedicated_daemon_readback_fixture_v2"',
        "fixture schema",
    )
    return source.encode("utf-8")


CONTRACT_SOURCE = r'''#!/usr/bin/env python3
"""Fail-closed JOB dedicated-Docker-daemon contract and readback validator."""
from __future__ import annotations
import json,re
SOCKET="/run/ra-docker-mysql-job-v1.sock"
ROOT="/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/docker_ra_job_v1"
DEVICE="259:3";FSTYPE="xfs";UNIT="ra-docker-mysql-job-v1.service"
CPUS="34-35,70-71";PORT=65102
class Error(RuntimeError):pass
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def unit_text():return "\n".join(("[Unit]","Description=Dedicated Docker daemon for MySQL JOB","[Service]","ExecStart=/usr/bin/dockerd --host=unix:%s --data-root=%s --iptables=false --bridge=none"%(SOCKET,ROOT),"CPUAffinity=%s"%CPUS,"Delegate=yes","[Install]","WantedBy=multi-user.target",""))
def validate(info,daemon,containers,mount_devices):
 if info.get("DockerRootDir")!=ROOT or info.get("Driver")!="overlay2":raise Error("DockerRootDir/driver differs")
 if daemon.get("unit")!=UNIT or daemon.get("socket")!=SOCKET or daemon.get("cpuset_cpus")!=CPUS or daemon.get("data_root_device")!=DEVICE or not daemon.get("pid") or not daemon.get("startticks") or not daemon.get("cmdline_sha256"):raise Error("daemon identity differs")
 if daemon.get("ppid")!=1 or daemon.get("docker_ancestry") is not False or UNIT not in str(daemon.get("cgroup") or ""):raise Error("daemon is not a direct host systemd process")
 namespaces=daemon.get("namespaces") or {}
 if set(namespaces)!={"mnt","pid","net","user"} or not all(isinstance(v,str) and v for v in namespaces.values()):raise Error("daemon namespace identity differs")
 if set(containers)!={"database","client"}:raise Error("DB/client evidence required")
 for role,row in containers.items():
  if row.get("network_mode")!="host" or row.get("daemon_socket")!=SOCKET or row.get("mysql_server_port")!=PORT or not row.get("container_id") or not row.get("pid") or not row.get("startticks"):raise Error(role+" dedicated daemon identity differs")
  data=row.get("graphdriver_data") or {}
  if set(data)!={"UpperDir","WorkDir","MergedDir","LowerDir"}:raise Error(role+" GraphDriver fields differ")
  for key,path in data.items():
   readback=mount_devices.get(path) or {}
   if not isinstance(path,str) or not path.startswith(ROOT+"/") or set(readback)!={"major_minor","fstype"}:raise Error(role+" GraphDriver readback shape differs")
   if key=="MergedDir":
    if readback.get("fstype")!="overlay" or re.fullmatch(r"0:[0-9]+",str(readback.get("major_minor") or "")) is None:raise Error(role+" merged overlay identity differs")
   elif readback!={"major_minor":DEVICE,"fstype":FSTYPE}:raise Error(role+" GraphDriver backing path is on PG/unknown device")
 return {"schema_version":"mysql_job_dedicated_daemon_readback_v2","status":"PASS_DEDICATED_DAEMON_NON_PG_GRAPHDRIVER","socket":SOCKET,"data_root":ROOT,"device_major_minor":DEVICE,"backing_fstype":FSTYPE,"unit":UNIT,"daemon":daemon,"containers":containers,"mount_readbacks":mount_devices,"accepted_round_authorized":False}
'''.encode("utf-8")


def patch_fixture(payload):
    value = json.loads(payload)
    value["schema_version"] = "mysql_job_dedicated_daemon_readback_fixture_v2"
    result = {}
    for path in value["mount_devices"]:
        if path.endswith("/merged"):
            result[path] = {
                "major_minor": "0:93" if "/client/" in path else "0:64",
                "fstype": "overlay",
            }
        else:
            result[path] = {"major_minor": "259:3", "fstype": "xfs"}
    value["mount_devices"] = result
    return canonical(value)


def patch_materializer(payload):
    source = payload.decode("utf-8")
    source = replace_once(
        source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v8"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v9"',
        "materializer schema",
    )
    source = replace_once(
        source,
        '"mysql_job_dedicated_daemon_materials_v8"',
        '"mysql_job_dedicated_daemon_materials_v9"',
        "materials schema",
    )
    marker = '                "job-v2-20260731t170729z-d56fa1b078949a64f24f0def",\n'
    return replace_once(
        source,
        marker,
        marker + '                "' + FAILED_ATTEMPT + '",\n',
        "failed attempt denylist",
    ).encode("utf-8")


def patch_existing_test(payload):
    source = payload.decode("utf-8")
    source = replace_once(source, "JOB v8 successor", "JOB v9 successor", "test title")
    source = replace_once(
        source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v8"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v9"',
        "test schema",
    )
    anchor = '    assert "host_graphdriver_findmnt_helper" in authority\n'
    return replace_once(
        source,
        anchor,
        anchor + '    assert "/usr/bin/findmnt -rn" in authority\n'
        + '    assert "MAJ:MIN,FSTYPE" in authority\n',
        "v9 helper assertions",
    ).encode("utf-8")


NEW_TEST_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations
import copy,hashlib,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def main():
 manifest=json.loads((ROOT/"bundle_manifest.json").read_text());unsigned=dict(manifest);unsigned.pop("bundle_id")
 assert manifest["bundle_id"]==ROOT.name
 assert hashlib.sha256(canonical(unsigned)).hexdigest()==ROOT.name
 assert manifest["schema_version"]=="mysql_job_raw_all_subset_three_level_successor_bundle_v9"
 for relative,expected in manifest["files"].items():
  path=ROOT/relative;assert path.is_file() and not path.is_symlink();assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
 fixture=json.loads((ROOT/"qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json").read_text())
 assert fixture["schema_version"]=="mysql_job_dedicated_daemon_readback_fixture_v2"
 contract=load(ROOT/"qualification/mysql_job_dedicated_daemon_contract_v1.py","job_contract_v9")
 passed=contract.validate(fixture["info"],fixture["daemon"],fixture["containers"],fixture["mount_devices"])
 assert passed["schema_version"]=="mysql_job_dedicated_daemon_readback_v2"
 assert passed["status"]=="PASS_DEDICATED_DAEMON_NON_PG_GRAPHDRIVER"
 bad=copy.deepcopy(fixture);merged=next(p for p in bad["mount_devices"] if p.endswith("/merged"));bad["mount_devices"][merged]["fstype"]="xfs"
 try:contract.validate(bad["info"],bad["daemon"],bad["containers"],bad["mount_devices"])
 except contract.Error:pass
 else:raise AssertionError("non-overlay MergedDir passed")
 bad=copy.deepcopy(fixture);upper=next(p for p in bad["mount_devices"] if p.endswith("/diff"));bad["mount_devices"][upper]["major_minor"]="0:99"
 try:contract.validate(bad["info"],bad["daemon"],bad["containers"],bad["mount_devices"])
 except contract.Error:pass
 else:raise AssertionError("unknown backing device passed")
 authority=(ROOT/"qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py").read_text()
 assert "/usr/bin/findmnt -rn" in authority and "MAJ:MIN,FSTYPE" in authority
if __name__=="__main__":main()
'''.encode("utf-8")


def invalidation():
    return {
        "schema_version": "mysql_job_graphdriver_helper_invalidation_v2",
        "status": "INVALID_ZERO_CREDIT",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_SHA256,
        "attempt_token": FAILED_ATTEMPT,
        "failure": "FINDMNT_COLUMN_PADDING_REJECTED_ALL_GRAPHDRIVER_PATHS_BEFORE_WORKLOAD",
        "secondary_contract_correction":
            "MERGEDDIR_IS_OVERLAY_0_COLON_N_WHILE_UPPER_WORK_LOWER_MUST_BE_XFS_259_COLON_3",
        "workload_started": False,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "fresh_attempt_required": True,
        "postgresql_mutation": False,
    }


def probe():
    return {
        "schema_version": "mysql_job_graphdriver_raw_findmnt_probe_v1",
        "status": "PASS_8_OF_8_KEYS",
        "probe_mode": "READ_ONLY_DEDICATED_JOB_DAEMON_HELPERS",
        "attempt_token": FAILED_ATTEMPT,
        "database": {
            "LowerDir": {"components": 12, "major_minor": "259:3", "fstype": "xfs"},
            "MergedDir": {"components": 1, "major_minor": "0:64", "fstype": "overlay"},
            "UpperDir": {"components": 1, "major_minor": "259:3", "fstype": "xfs"},
            "WorkDir": {"components": 1, "major_minor": "259:3", "fstype": "xfs"},
        },
        "client": {
            "LowerDir": {"components": 12, "major_minor": "259:3", "fstype": "xfs"},
            "MergedDir": {"components": 1, "major_minor": "0:93", "fstype": "overlay"},
            "UpperDir": {"components": 1, "major_minor": "259:3", "fstype": "xfs"},
            "WorkDir": {"components": 1, "major_minor": "259:3", "fstype": "xfs"},
        },
        "postgresql_mutation": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def freeze(path):
    for candidate in sorted(Path(path).rglob("*"), reverse=True):
        if candidate.is_symlink():
            raise BuildError("symlink in JOB v9")
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
        payload[relative] = CONTRACT_SOURCE
        modes[relative] = 0o555
    payload[FIXTURE] = patch_fixture(payload[FIXTURE])
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER])
    payload[TEST] = patch_existing_test(payload[TEST])
    payload[NEW_TEST] = NEW_TEST_SOURCE
    payload[BUILDER] = Path(__file__).read_bytes()
    payload[INVALIDATION] = canonical(invalidation())
    payload[PROBE] = canonical(probe())
    modes.update({AUTHORITY:0o555,FIXTURE:0o444,MATERIALIZER:0o555,TEST:0o444,
                  NEW_TEST:0o444,BUILDER:0o444,INVALIDATION:0o444,PROBE:0o444})
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v9",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA256,
        "graphdriver_readback": {
            "raw_findmnt": True,
            "lowerdir_component_equality_required": True,
            "backing_device": "259:3",
            "backing_fstype": "xfs",
            "merged_fstype": "overlay",
            "merged_major_minor_pattern": "0:[0-9]+",
            "postgresql_mutation_prohibited": True,
        },
        "invalidated_v8_attempt": INVALIDATION,
        "live_probe": PROBE,
    })
    closure["files"] = {name:sha(data) for name,data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure);modes["closure.json"] = 0o444
    manifest = dict(parent);manifest.pop("bundle_id",None)
    manifest.update({
        "schema_version": SCHEMA,
        "status": "SEALED_RAW_FINDMNT_OVERLAY_AWARE_DEBUG_BOOTSTRAP_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA256,
        "graphdriver_readback_schema": "mysql_job_dedicated_daemon_readback_v2",
        "invalidated_v8_attempt": FAILED_ATTEMPT,
        "invalidated_v8_credit": 0,
        "formal_launch_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "files": {name:sha(data) for name,data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(manifest));manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True,exist_ok=True);target=OUTPUT/bundle_id
    if target.exists():raise BuildError("immutable JOB v9 already exists")
    stage=OUTPUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
    for relative,data in payload.items():
        destination=stage/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data);os.chmod(destination,modes[relative]&~0o222)
    raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw)
    (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii")
    freeze(stage);os.rename(stage,target)
    print(json.dumps({"status":manifest["status"],"bundle":str(target),"bundle_id":bundle_id,
                      "manifest_sha256":digest(target/"bundle_manifest.json"),
                      "formal_launch_authorized":False,"accepted_round_authorized":False},sort_keys=True))


if __name__ == "__main__":
    try:main()
    except (BuildError,OSError,ValueError) as exc:raise SystemExit("NOT_READY: %s"%exc)
