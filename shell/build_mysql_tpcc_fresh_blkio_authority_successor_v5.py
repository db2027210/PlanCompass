#!/usr/bin/env python3
"""Build v5 with a fresh, non-overwriting post-restart blkio authority."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "394b7192d1a1558e9979b9d7a8e513dfe07e7423fa464a1ec83be8d89569a58a"
PARENT_SHA = "2e67a35ed84b176d8731d1a8d1e8199ad5ea9e74a6f1aaefb9d4c8c1788641e7"
PARENT = ROOT / "artifacts/mysql_tpcc_private_socket_adopt_successor_v4" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_fresh_blkio_authority_successor_v5"
CONTROL_REL = "control/mysql_tpcc_zero_swap_recommission_fresh_blkio_v5.py"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_parent():
    raw = (PARENT / "bundle_manifest.json").read_bytes()
    if digest(raw) != PARENT_SHA:
        raise RuntimeError("parent manifest sha256 differs")
    manifest = json.loads(raw)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id")
    if bundle_id != PARENT_ID or digest(canonical(unsigned)) != PARENT_ID:
        raise RuntimeError("parent content address differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise RuntimeError("parent closure differs: " + relative)
    return manifest


ANCHOR = '''def adopt_on_private_socket(module,plan):'''

HELPERS = '''BLKIO_DEVICE="259:2";BLKIO_BPS=1205862400;BLKIO_ROOT=Path("/sys/fs/cgroup/blkio/docker")
FRESH_BLKIO=ATTEMPT/"blkio_throttle_authority.zero_swap_restart_v1.json"
def exact_blkio_rate(path):
 rows=[]
 for line in Path(path).read_text(encoding="ascii").splitlines():
  fields=line.split()
  if len(fields)!=2:raise E("blkio throttle row differs: "+str(path))
  if fields[0]==BLKIO_DEVICE:
   try:value=int(fields[1])
   except ValueError:raise E("blkio throttle value differs: "+str(path))
   rows.append(value)
 if rows!=[BLKIO_BPS]:raise E("exact target-device blkio throttle differs: "+str(path))
 return rows[0]
def fresh_blkio_value(plan,database,read_root=BLKIO_ROOT):
 db=plan.get("database") or {};bundle_id=plan.get("bundle_id");attempt_id=plan.get("attempt_id")
 if not isinstance(bundle_id,str) or not bundle_id or attempt_id!=ATTEMPT.name or db.get("name")!=DB or database.get("container_id")!=DB_ID or database.get("container_name")!=DB or int(database.get("pid") or 0)<=1:raise E("fresh blkio plan/live identity differs")
 cgroup=Path(read_root)/DB_ID;read_file=cgroup/"blkio.throttle.read_bps_device";write_file=cgroup/"blkio.throttle.write_bps_device"
 read_bps=exact_blkio_rate(read_file);write_bps=exact_blkio_rate(write_file);authority_cgroup=BLKIO_ROOT/DB_ID
 return {"schema_version":"mysql_tpcc_blkio_v1_throttle_authority_v1","status":"PASS","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"bundle_id":bundle_id,"attempt_id":attempt_id,"database_container":DB,"database_container_id":DB_ID,"database_pid":database["pid"],"host_effective_uid_gid":[0,0],"cgroup_version":"v1","cgroup_path":str(authority_cgroup),"device_major_minor":BLKIO_DEVICE,"read_bps":read_bps,"write_bps":write_bps,"read_throttle_file":str(authority_cgroup/"blkio.throttle.read_bps_device"),"write_throttle_file":str(authority_cgroup/"blkio.throttle.write_bps_device"),"docker_hostconfig_readback_not_authoritative":True,"private_docker_socket":SOCKET,"read_only_cgroup_readback":True,"database_or_client_started":True,"workload_started":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
def seal_fresh_blkio_authority(plan,database,path=FRESH_BLKIO,read_root=BLKIO_ROOT):
 path=Path(path);sidecar=Path(str(path)+".sha256");value=fresh_blkio_value(plan,database,read_root)
 if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
  if not path.is_file() or path.is_symlink() or not sidecar.is_file() or sidecar.is_symlink() or load_sealed(path)!=value:raise E("existing fresh blkio authority differs")
  return path
 atomic(path,value)
 if load_sealed(path)!=value:raise E("fresh blkio authority post-seal differs")
 return path
def adopt_with_fresh_blkio(module,plan,fresh_path):
 fresh_path=Path(fresh_path);legacy=Path(plan["database"]["datadir"]).parent.parent/"blkio_throttle_authority.json";original=module.base.load
 if fresh_path!=FRESH_BLKIO or not fresh_path.is_file() or fresh_path.is_symlink() or load_sealed(fresh_path).get("database_pid")<=1:raise E("fresh blkio binding differs")
 def bound_load(path):
  candidate=Path(path)
  if candidate==legacy:return original(fresh_path)
  return original(path)
 module.base.load=bound_load
 try:return adopt_on_private_socket(module,plan)
 finally:module.base.load=original
'''

OLD_CALL = '''spec=importlib.util.spec_from_file_location("tpcc_recommission_adopt",module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);plan=json.loads((ATTEMPT/"plan.json").read_text());value=adopt_on_private_socket(m,plan)'''
NEW_CALL = '''spec=importlib.util.spec_from_file_location("tpcc_recommission_adopt",module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);plan=json.loads((ATTEMPT/"plan.json").read_text());fresh_blkio=seal_fresh_blkio_authority(plan,after["database"]);value=adopt_with_fresh_blkio(m,plan,fresh_blkio)'''

OLD_RECEIPT = '''"recomputed_before_mount_hashes":recomputed_before_mount_hashes,"postgresql_mutation_performed":False}'''
NEW_RECEIPT = '''"recomputed_before_mount_hashes":recomputed_before_mount_hashes,"fresh_blkio_authority_path":str(fresh_blkio),"fresh_blkio_authority_sha256":sha(fresh_blkio),"legacy_blkio_authority_overwritten":False,"postgresql_mutation_performed":False}'''


TEST = r'''import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

B = Path(__file__).resolve().parents[1]
C = B / "control/mysql_tpcc_zero_swap_recommission_fresh_blkio_v5.py"


def module():
    spec = importlib.util.spec_from_file_location("tpcc_blkio_v5", C)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def plan(m, root):
    return {"bundle_id": "7e2e13aa", "attempt_id": m.ATTEMPT.name,
            "database": {"name": m.DB, "datadir": str(root / "datadir/datadir")}}


def database(m, pid=4242):
    return {"container_id": m.DB_ID, "container_name": m.DB, "pid": pid}


def cgroup_files(m, tmp_path, read=None, write=None):
    root = tmp_path / "blkio/docker"
    cgroup = root / m.DB_ID
    cgroup.mkdir(parents=True)
    (cgroup / "blkio.throttle.read_bps_device").write_text(
        (read or "8:0 1\n259:2 1205862400\n"))
    (cgroup / "blkio.throttle.write_bps_device").write_text(
        (write or "259:2 1205862400\n8:0 1\n"))
    return root


def test_fresh_authority_binds_current_identity_and_exact_read_only_throttle(tmp_path):
    m = module(); read_root = cgroup_files(m, tmp_path); p = plan(m, tmp_path)
    value = m.fresh_blkio_value(p, database(m, 9876), read_root)
    assert value["bundle_id"] == p["bundle_id"]
    assert value["attempt_id"] == p["attempt_id"]
    assert value["database_container"] == m.DB
    assert value["database_container_id"] == m.DB_ID
    assert value["database_pid"] == 9876
    assert value["read_bps"] == value["write_bps"] == 1205862400
    assert value["device_major_minor"] == "259:2"
    assert value["read_only_cgroup_readback"] is True
    assert value["workload_started"] is False
    assert value["accepted_rounds_credit"] == 0


@pytest.mark.parametrize("which,text", [
    ("read", "259:2 1205862399\n"),
    ("write", "259:2 1205862400\n259:2 1205862400\n"),
    ("read", "8:0 1205862400\n"),
])
def test_wrong_missing_or_duplicate_target_throttle_fails_closed(tmp_path, which, text):
    m = module(); kwargs = {which: text}; read_root = cgroup_files(m, tmp_path, **kwargs)
    with pytest.raises(m.E, match="exact target-device blkio throttle differs"):
        m.fresh_blkio_value(plan(m, tmp_path), database(m), read_root)


def test_new_authority_is_exclusive_sealed_and_idempotent_without_overwrite(tmp_path):
    m = module(); read_root = cgroup_files(m, tmp_path); target = tmp_path / "blkio_throttle_authority.zero_swap_restart_v1.json"
    first = m.seal_fresh_blkio_authority(plan(m, tmp_path), database(m), target, read_root)
    before = (target.stat().st_ino, target.stat().st_mtime_ns, target.read_bytes())
    second = m.seal_fresh_blkio_authority(plan(m, tmp_path), database(m), target, read_root)
    after = (target.stat().st_ino, target.stat().st_mtime_ns, target.read_bytes())
    assert first == second == target and before == after
    assert Path(str(target) + ".sha256").is_file()


def test_changed_pid_refuses_existing_fresh_authority(tmp_path):
    m = module(); read_root = cgroup_files(m, tmp_path); target = tmp_path / "blkio_throttle_authority.zero_swap_restart_v1.json"
    m.seal_fresh_blkio_authority(plan(m, tmp_path), database(m, 111), target, read_root)
    with pytest.raises(m.E, match="existing fresh blkio authority differs"):
        m.seal_fresh_blkio_authority(plan(m, tmp_path), database(m, 222), target, read_root)


def test_legacy_path_is_temporarily_read_only_redirected_and_restored(tmp_path, monkeypatch):
    m = module(); monkeypatch.setattr(m, "FRESH_BLKIO", tmp_path / "blkio_throttle_authority.zero_swap_restart_v1.json")
    read_root = cgroup_files(m, tmp_path); p = plan(m, tmp_path); fresh = m.seal_fresh_blkio_authority(p, database(m), m.FRESH_BLKIO, read_root)
    legacy = Path(p["database"]["datadir"]).parent.parent / "blkio_throttle_authority.json"
    seen = []
    def original(path):
        seen.append(Path(path)); return json.loads(Path(path).read_text())
    base = SimpleNamespace(load=original)
    class Fake:
        def adopt(self, incoming):
            assert os.environ["DOCKER_HOST"] == m.SOCKET
            value = base.load(legacy)
            assert value["database_pid"] == 4242
            return {"status": "PASS"}
    fake = Fake(); fake.base = base; old = base.load
    assert m.adopt_with_fresh_blkio(fake, p, fresh)["status"] == "PASS"
    assert seen == [fresh]
    assert base.load is old
    assert not legacy.exists()


def test_v4_mount_socket_zero_swap_and_pg_gates_preserved():
    source = C.read_text()
    for required in (
        'canonical_mount_projection(before.get("mounts"),role)',
        'after["container_id"]!=before["container_id"]',
        'after["pid"]==before["pid"]',
        'after["total_swap_bytes"]!=0',
        'os.environ["DOCKER_HOST"]=SOCKET',
        'postgresql_mutation_performed":False',
        'legacy_blkio_authority_overwritten":False',
    ):
        assert required in source


def test_content_address_scope_and_parent():
    manifest = json.loads((B / "bundle_manifest.json").read_text()); unsigned = dict(manifest); unsigned.pop("bundle_id")
    actual = hashlib.sha256((json.dumps(unsigned, sort_keys=True, indent=2) + "\n").encode()).hexdigest()
    assert actual == manifest["bundle_id"] == B.name
    assert manifest["parent_bundle_id"] == "394b7192d1a1558e9979b9d7a8e513dfe07e7423fa464a1ec83be8d89569a58a"
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def main():
    verify_parent()
    source = (PARENT / "control/mysql_tpcc_zero_swap_recommission_private_socket_v4.py").read_text()
    for old, new, label in (
        (ANCHOR, HELPERS + ANCHOR, "fresh blkio helpers"),
        (OLD_CALL, NEW_CALL, "fresh blkio adopt call"),
        (OLD_RECEIPT, NEW_RECEIPT, "fresh blkio receipt"),
    ):
        if source.count(old) != 1:
            raise RuntimeError(label + " patch anchor differs")
        source = source.replace(old, new)
    runbook = json.loads((PARENT / "runbook/transaction.json").read_text())
    runbook["schema_version"] = "mysql_tpcc_fresh_blkio_authority_transaction_v5"
    runbook["post_restart_blkio_authority"] = {
        "new_path": "blkio_throttle_authority.zero_swap_restart_v1.json",
        "legacy_path_overwrite_or_delete_prohibited": True,
        "private_socket_live_identity_required": True,
        "cgroup_v1_read_only_exact_readback": {"device": "259:2", "read_bps": 1205862400, "write_bps": 1205862400},
        "legacy_module_binding": "exact-path in-process read-only redirect under try/finally",
    }
    evidence = json.loads((PARENT / "evidence/mysql_tpcc_v3_default_socket_adopt_failure_v1.json").read_text())
    evidence.update(
        schema_version="mysql_tpcc_v4_stale_blkio_pid_failure_evidence_v1",
        status="OFFLINE_DIAGNOSIS_V4_EXECUTED_ZERO_CREDIT",
        predecessor_bundle_id=PARENT_ID,
        additional_root_cause="legacy blkio authority is sealed to the pre-restart database PID",
        fix="seal a new post-restart exact cgroup-v1 readback authority and redirect only the legacy verifier read",
        legacy_authority_overwrite_or_delete_prohibited=True,
        accepted_rounds_credit=0,
    )
    contents = {
        CONTROL_REL: source.encode(),
        "runbook/transaction.json": canonical(runbook),
        "evidence/mysql_tpcc_v4_stale_blkio_pid_failure_v1.json": canonical(evidence),
        "tests/test_mysql_tpcc_fresh_blkio_authority_successor_v5.py": TEST.encode(),
        "builder/build_mysql_tpcc_fresh_blkio_authority_successor_v5.py": HERE.read_bytes(),
    }
    files = {relative: digest(raw) for relative, raw in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_fresh_blkio_authority_successor_bundle_v5",
        "status": "SEALED_NOT_EXECUTED",
        "immutable": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_SHA,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "fresh_blkio_authority_filename": "blkio_throttle_authority.zero_swap_restart_v1.json",
        "legacy_blkio_authority_overwrite_or_delete_prohibited": True,
        "fresh_blkio_private_socket_identity_required": True,
        "fresh_blkio_current_pid_required": True,
        "fresh_blkio_cgroup_version": "v1",
        "fresh_blkio_device_major_minor": "259:2",
        "fresh_blkio_read_bps": 1205862400,
        "fresh_blkio_write_bps": 1205862400,
        "fresh_blkio_readback_mutation_performed": False,
        "legacy_verifier_exact_path_read_only_redirected": True,
        "legacy_verifier_binding_restored_try_finally": True,
        "private_socket_adopt_preserved": True,
        "legacy_before_mount_projection_canonically_rehashed": True,
        "same_container_image_created_and_datadir_required": True,
        "database_and_client_pid_start_change_required": True,
        "database_and_client_zero_swap_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_performed": 0,
        "files": files,
    }
    bundle_id = digest(canonical(unsigned)); manifest_raw = canonical(dict(unsigned, bundle_id=bundle_id))
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    if destination.exists():
        if (destination / "bundle_manifest.json").read_bytes() != manifest_raw:
            raise RuntimeError("existing manifest differs")
        for relative, raw in contents.items():
            if (destination / relative).read_bytes() != raw:
                raise RuntimeError("existing closure differs: " + relative)
    else:
        stage = Path(tempfile.mkdtemp(prefix=".fresh-blkio-", dir=OUT))
        try:
            for relative, raw in contents.items():
                path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            (stage / "bundle_manifest.json").write_bytes(manifest_raw)
            (stage / "bundle_manifest.json.sha256").write_text(digest(manifest_raw) + "  bundle_manifest.json\n")
            freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    print(bundle_id); print(digest((destination / "bundle_manifest.json").read_bytes())); print(destination)


if __name__ == "__main__":
    main()
