#!/usr/bin/env python3
"""Independently verify local and host04 TPC-C E1 deployment evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / (
    "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/"
    "evidence/tpcc_e1_template_deployment_20260723.json"
)
HOST = "anonymous@192.0.2.7"
REMOTE_PROJECT = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc"
RUN_ID = "20260722T220924Z_1489393"
PG_ID = "5bfdab1bb8e8e6d382e70b15c252c9f3b2d74ae3e0ba0a1e05532e2291b479cc"
EXPECTED_COUNTS = {
    "warehouse": 10,
    "district": 100,
    "customer": 300000,
    "history": 300000,
    "oorder": 300000,
    "new_order": 90000,
    "item": 100000,
    "stock": 1000000,
    "order_line": 3000644,
}
EXPECTED_SCOPE = {
    "template_deployment_only": True,
    "r0": False,
    "sgd": False,
    "accepted_iteration": False,
    "readiness_changed": False,
    "ready": False,
    "launch_authorized": False,
}


class VerificationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VerificationError(f"{label} must be an object")
    return value


def repo_file(raw: Any, label: str) -> Path:
    path = (ROOT / str(raw or "")).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise VerificationError(f"{label} escapes repository") from exc
    if not path.is_file() or path.is_symlink():
        raise VerificationError(f"{label} is absent or a symlink")
    return path


def verify_checksum_bundle(path: Path) -> int:
    count = 0
    root = path.parent.resolve()
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        child = (root / name.removeprefix("./")).resolve()
        if not separator or len(digest) != 64 or child.parent != root:
            raise VerificationError(f"malformed or unsafe checksum member: {name!r}")
        if not child.is_file() or child.is_symlink() or sha256(child) != digest:
            raise VerificationError(f"local bundle member mismatch: {name}")
        count += 1
    if count == 0:
        raise VerificationError("local evidence checksum bundle is empty")
    return count


def verify_readiness_guard(document: Mapping[str, Any]) -> None:
    guard = mapping(document.get("readiness_guard"), "readiness_guard")
    path = repo_file(guard.get("path"), "readiness manifest")
    if sha256(path) != guard.get("sha256_unchanged"):
        raise VerificationError("readiness manifest changed")
    readiness = mapping(json.loads(path.read_text(encoding="utf-8")), "readiness")
    if readiness.get("status") != "NOT_READY" or readiness.get("launch_authorized") is not False:
        raise VerificationError("v2 draft readiness must remain NOT_READY and unauthorized")


REMOTE_SCRIPT = r'''set -euo pipefail
project="$1"; run_id="$2"; pg_id="$3"
evidence="$project/evidence/$run_id"; template="$project/template"
test -d "$evidence" && test -d "$template/datadir"
(cd "$evidence" && sha256sum -c checksums.sha256 >/dev/null)
checksums_sha=$(sha256sum "$evidence/checksums.sha256" | awk '{print $1}')
manifest_sha=$(sha256sum "$template/template_manifest.json" | awk '{print $1}')
(cd "$template" && sha256sum -c template_manifest.json.sha256 >/dev/null)
test -z "$(sudo -n find "$template" -xdev -perm /0222 -print -quit)"
test -z "$(sudo -n find "$template" -xdev ! -type f ! -type d -print -quit)"
test ! -e "$project/locks/host04_mysql_lane.owner.json"
exec 9>"$project/locks/host04_mysql_lane.lock"; flock -n 9
test -z "$(docker ps -a --format '{{.Names}}' | grep -E '^ra_mysql_tpcc04_e1_(template|loader)$' || true)"
tree=$(sudo -n python3 - "$template/datadir" <<'PY'
import hashlib,json,pathlib,stat,sys
root=pathlib.Path(sys.argv[1]); h=hashlib.sha256(); files=dirs=size=0
for p in sorted(root.rglob('*'),key=lambda x:x.relative_to(root).as_posix()):
 rel_text=p.relative_to(root).as_posix(); rel=rel_text.encode(); mode=p.lstat().st_mode
 if stat.S_ISDIR(mode): h.update(b'D\0'+rel+b'\0'); dirs+=1; continue
 if not stat.S_ISREG(mode): raise SystemExit('non-regular tree entry: '+rel_text)
 n=p.stat().st_size; files+=1; size+=n; h.update(b'F\0'+rel+b'\0'+str(n).encode()+b'\0')
 with p.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''): h.update(block)
print(json.dumps({'datadir_sha256_tree_v1':h.hexdigest(),'datadir_file_count':files,'datadir_directory_count':dirs,'datadir_bytes':size},sort_keys=True))
PY
)
docker inspect pg5_tpch_s04_20260722 > /tmp/tpcc_e1_verify_pg.$$.json
trap 'rm -f /tmp/tpcc_e1_verify_pg.$$.json' EXIT
python3 - "$template/template_manifest.json" /tmp/tpcc_e1_verify_pg.$$.json "$tree" "$checksums_sha" "$manifest_sha" "$pg_id" <<'PY'
import json,sys
manifest=json.load(open(sys.argv[1])); pg=json.load(open(sys.argv[2]))[0]; tree=json.loads(sys.argv[3])
assert pg['Id']==sys.argv[6] and pg['State']['Running'] is True
assert pg['HostConfig']['CpusetCpus']=='0-15,36-51' and pg['HostConfig']['CpusetMems']=='0'
assert any(m.get('Source','').startswith('/mnt/nvme2n1/') for m in pg['Mounts'])
print(json.dumps({'remote_checksums_sha256':sys.argv[4],'manifest_sha256':sys.argv[5],
 'manifest':manifest,'tree':tree,'readonly':True,'lease_free':True,'temporary_container_count':0,
 'pg':{'id':pg['Id'],'running':pg['State']['Running'],'cpuset_cpus':pg['HostConfig']['CpusetCpus'],
 'cpuset_mems':pg['HostConfig']['CpusetMems'],'nano_cpus':pg['HostConfig']['NanoCpus'],
 'memory':pg['HostConfig']['Memory'],'memory_swap':pg['HostConfig']['MemorySwap'],
 'pids_limit':pg['HostConfig']['PidsLimit']}},sort_keys=True))
PY
'''


def remote_verify() -> Mapping[str, Any]:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", HOST,
         "bash", "-s", "--", REMOTE_PROJECT, RUN_ID, PG_ID],
        input=REMOTE_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise VerificationError(f"remote verification failed: {result.stderr.strip()}")
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise VerificationError("remote verifier did not return exactly one JSON object")
    return mapping(json.loads(lines[0]), "remote result")


def verify(path: Path = DEFAULT_EVIDENCE) -> dict[str, Any]:
    evidence_path = path.resolve()
    document = mapping(json.loads(evidence_path.read_text(encoding="utf-8")), "evidence")
    sidecar = evidence_path.with_name(evidence_path.name + ".sha256")
    expected_sidecar = f"{sha256(evidence_path)}  {evidence_path.name}\n"
    if not sidecar.is_file() or sidecar.is_symlink() or sidecar.read_text(encoding="ascii") != expected_sidecar:
        raise VerificationError("canonical evidence sidecar is absent or mismatched")
    if document.get("schema_version") != "mysql_tpcc_e1_canonical_template_deployment_evidence_v1":
        raise VerificationError("canonical evidence schema mismatch")
    if document.get("status") != "TEMPLATE_DEPLOYMENT_VERIFIED_NOT_READY":
        raise VerificationError("canonical evidence status mismatch")
    if mapping(document.get("formal_scope"), "formal_scope") != EXPECTED_SCOPE:
        raise VerificationError("formal scope exceeds non-R0/non-SGD/non-READY deployment")
    verify_readiness_guard(document)

    source = mapping(document.get("source_evidence"), "source_evidence")
    bundle = mapping(source.get("retry6_local_bundle"), "retry6_local_bundle")
    checksums = repo_file(bundle.get("checksums_path"), "local retry6 checksums")
    if sha256(checksums) != bundle.get("checksums_sha256"):
        raise VerificationError("local checksum-manifest SHA mismatch")
    member_count = verify_checksum_bundle(checksums)
    row_counts_path = repo_file(bundle.get("row_counts_path"), "local row counts")
    final_path = repo_file(bundle.get("final_path"), "local final result")
    before_path = repo_file(bundle.get("pg_before_path"), "local PG before")
    after_path = repo_file(bundle.get("pg_after_path"), "local PG after")
    for child, field in ((row_counts_path, "row_counts_sha256"), (final_path, "final_sha256"),
                         (before_path, "pg_before_sha256"), (after_path, "pg_after_sha256")):
        if sha256(child) != bundle.get(field):
            raise VerificationError(f"source SHA mismatch: {field}")
    counts = mapping(json.loads(row_counts_path.read_text())["counts"], "row counts")
    if counts != EXPECTED_COUNTS or document.get("exact_row_counts") != EXPECTED_COUNTS:
        raise VerificationError("row counts differ from exact TPC-C scale-10 closure")
    final = mapping(json.loads(final_path.read_text()), "final")
    if final.get("status") != "PASS" or final.get("formal_r0_authorized") is not False:
        raise VerificationError("retry6 final result is not a non-formal PASS")
    if before_path.read_bytes() != after_path.read_bytes():
        raise VerificationError("PG before/after readback bytes differ")

    remote = remote_verify()
    identity = mapping(document.get("template_identity"), "template_identity")
    remote_source = mapping(source.get("retry6_remote_bundle"), "retry6_remote_bundle")
    manifest = mapping(remote.get("manifest"), "remote manifest")
    tree = mapping(remote.get("tree"), "remote tree")
    if remote.get("remote_checksums_sha256") != bundle.get("checksums_sha256") \
            or remote.get("remote_checksums_sha256") != remote_source.get("checksums_sha256"):
        raise VerificationError("local and remote evidence copies differ")
    if remote.get("manifest_sha256") != identity.get("template_manifest_sha256"):
        raise VerificationError("remote template manifest SHA mismatch")
    if any(manifest.get(key) != identity.get(key) for key in (
        "datadir_sha256_tree_v1", "datadir_file_count", "datadir_directory_count", "datadir_bytes"
    )):
        raise VerificationError("canonical identity differs from remote manifest")
    if any(tree.get(key) != manifest.get(key) for key in tree):
        raise VerificationError("live datadir tree differs from sealed manifest")
    if manifest.get("counts") != EXPECTED_COUNTS or manifest.get("clean_shutdown") is not True \
            or manifest.get("formal_r0_authorized") is not False:
        raise VerificationError("remote manifest contents are invalid")
    pg = mapping(remote.get("pg"), "remote PG")
    expected_pg = mapping(document.get("postgresql_unchanged"), "postgresql_unchanged")
    if any(pg.get(key) != expected_pg.get(key) for key in pg):
        raise VerificationError("live PG identity/resource readback changed")
    if remote.get("readonly") is not True or remote.get("lease_free") is not True \
            or remote.get("temporary_container_count") != 0:
        raise VerificationError("remote readonly/lease/container closure failed")
    return {
        "status": "PASS",
        "evidence": str(evidence_path),
        "evidence_sha256": sha256(evidence_path),
        "local_bundle_members": member_count,
        "local_remote_evidence_identical": True,
        "row_counts_verified": True,
        "manifest_tree_readonly_verified": True,
        "postgresql_unchanged": True,
        "lease_free": True,
        "temporary_container_count": 0,
        "r0": False,
        "sgd": False,
        "ready": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    try:
        result = verify(args.evidence)
    except (OSError, UnicodeError, json.JSONDecodeError, VerificationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
