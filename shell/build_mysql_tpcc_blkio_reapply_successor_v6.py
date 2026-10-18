#!/usr/bin/env python3
"""Build the immutable TPC-C v6 blkio reapply-and-adopt successor."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "62dc8c6ec76b9cbfb4aca8b00e39a5e5f362072babe9c688bbd1f8dfef94b7e8"
PARENT_SHA = "f0c738828d04c12333108c3ae21722ef82ca82cbb90dc12ad3ba02eedbda9ff2"
PARENT = ROOT / "artifacts/mysql_tpcc_fresh_blkio_authority_successor_v5" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpcc_blkio_reapply_successor_v6"
CONTROL_REL = "control/mysql_tpcc_zero_swap_recommission_blkio_reapply_v6.py"


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


ANCHOR = '''BLKIO_DEVICE="259:2";BLKIO_BPS=1205862400;BLKIO_ROOT=Path("/sys/fs/cgroup/blkio/docker")'''

HELPERS = r'''REAPPLY_RECEIPT=ATTEMPT/"blkio_throttle_reapply.zero_swap_restart_v1.json"
def proc_cgroup_rows(proc_root=Path("/proc")):
 rows=[]
 for entry in sorted(Path(proc_root).iterdir(),key=lambda p:p.name):
  if not entry.name.isdigit():continue
  try:
   comm=(entry/"comm").read_text(encoding="utf-8").strip()
   raw_cgroup=(entry/"cgroup").read_text(encoding="ascii")
   raw_cmd=(entry/"cmdline").read_bytes()
  except (FileNotFoundError,ProcessLookupError,PermissionError):continue
  cgroups=[]
  for line in raw_cgroup.splitlines():
   fields=line.split(":",2)
   if len(fields)!=3:raise E("process cgroup row differs: "+entry.name)
   cgroups.append((tuple(x for x in fields[1].split(",") if x),fields[2]))
  cmd=[x.decode("utf-8","surrogateescape") for x in raw_cmd.split(b"\0") if x]
  rows.append({"pid":int(entry.name),"comm":comm,"cmdline":cmd,"cgroups":cgroups})
 return rows
def client_idle_evidence(client,proc_root=Path("/proc")):
 if client.get("container_id")!=CLIENT_ID or client.get("container_name")!=CLIENT or int(client.get("pid") or 0)<=1:raise E("client live identity differs before blkio reapply")
 needle="/docker/"+CLIENT_ID;members=[]
 for row in proc_cgroup_rows(proc_root):
  if any(path==needle for controllers,path in row["cgroups"]):members.append(row)
 if len(members)!=1 or members[0]["pid"]!=client["pid"]:raise E("client cgroup workload absence differs")
 cmd=members[0]["cmdline"]
 if len(cmd)!=2 or Path(cmd[0]).name!="sleep" or cmd[1]!="infinity":raise E("client is not the sealed idle sleep process")
 return {"client_container_id":CLIENT_ID,"client_pid":client["pid"],"member_pids":[members[0]["pid"]],"idle_command":["sleep","infinity"],"workload_absent":True}
def postgres_blkio_cgroups(proc_root=Path("/proc")):
 paths=set()
 for row in proc_cgroup_rows(proc_root):
  if row["comm"]!="postgres":continue
  for controllers,path in row["cgroups"]:
   if "blkio" in controllers:paths.add(path)
 return sorted(paths)
def parse_throttle(path):
 rows=[];seen=set()
 for line in Path(path).read_text(encoding="ascii").splitlines():
  fields=line.split()
  if len(fields)!=2:raise E("blkio throttle row differs: "+str(path))
  device=fields[0]
  try:value=int(fields[1])
  except ValueError:raise E("blkio throttle value differs: "+str(path))
  if device in seen:raise E("duplicate blkio throttle device: "+str(path))
  seen.add(device);rows.append((device,value))
 return rows
def cgroup_write(path,payload):
 fd=os.open(str(path),os.O_WRONLY)
 try:
  raw=payload.encode("ascii");written=os.write(fd,raw)
  if written!=len(raw):raise E("short cgroup write: "+str(path))
 finally:os.close(fd)
def apply_throttle_file(path,writer=cgroup_write):
 path=Path(path)
 if path.is_symlink() or not path.is_file():raise E("blkio throttle file differs: "+str(path))
 before=parse_throttle(path);target=[value for device,value in before if device==BLKIO_DEVICE]
 if target not in ([],[BLKIO_BPS]):raise E("pre-reapply target blkio throttle differs: "+str(path))
 other_before=[list(row) for row in before if row[0]!=BLKIO_DEVICE]
 action="already_exact"
 if not target:
  writer(path,BLKIO_DEVICE+" "+str(BLKIO_BPS)+"\n");action="wrote_exact_target"
 after=parse_throttle(path);target_after=[value for device,value in after if device==BLKIO_DEVICE]
 other_after=[list(row) for row in after if row[0]!=BLKIO_DEVICE]
 if target_after!=[BLKIO_BPS]:raise E("post-reapply target blkio throttle differs: "+str(path))
 if other_after!=other_before:raise E("non-target blkio throttle rows changed: "+str(path))
 return {"path":str(path),"action":action,"target_before":target,"target_after":target_after,"non_target_rows_before":other_before,"non_target_rows_after":other_after}
def blkio_reapply_value(plan,database,client,read_root=BLKIO_ROOT,proc_root=Path("/proc"),writer=cgroup_write):
 db=plan.get("database") or {}
 if plan.get("attempt_id")!=ATTEMPT.name or not isinstance(plan.get("bundle_id"),str) or not plan["bundle_id"] or db.get("name")!=DB or database.get("container_id")!=DB_ID or database.get("container_name")!=DB or int(database.get("pid") or 0)<=1:raise E("blkio reapply plan/live identity differs")
 idle=client_idle_evidence(client,proc_root);root=Path(read_root);cgroup=root/DB_ID
 if root.is_symlink() or cgroup.is_symlink() or not cgroup.is_dir():raise E("dedicated database blkio cgroup differs")
 relative="/docker/"+DB_ID;pg_paths=postgres_blkio_cgroups(proc_root)
 if relative in pg_paths:raise E("database target is a PostgreSQL blkio cgroup")
 read=apply_throttle_file(cgroup/"blkio.throttle.read_bps_device",writer)
 write=apply_throttle_file(cgroup/"blkio.throttle.write_bps_device",writer)
 return {"schema_version":"mysql_tpcc_blkio_v1_throttle_reapply_receipt_v1","status":"PASS","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"postgresql_mutation_performed":False,"postgresql_cgroup_written":False,"bundle_id":plan["bundle_id"],"attempt_id":plan["attempt_id"],"database_container":DB,"database_container_id":DB_ID,"database_pid":database["pid"],"client_container":CLIENT,"client_container_id":CLIENT_ID,"client_pid":client["pid"],"private_docker_socket":SOCKET,"cgroup_version":"v1","cgroup_path":str(BLKIO_ROOT/DB_ID),"device_major_minor":BLKIO_DEVICE,"read_bps":BLKIO_BPS,"write_bps":BLKIO_BPS,"write_payload":BLKIO_DEVICE+" "+str(BLKIO_BPS),"client_idle_evidence":idle,"observed_postgresql_blkio_cgroups":pg_paths,"read_reapply":read,"write_reapply":write,"non_target_device_rows_preserved":True,"workload_started":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
def equivalent_reapply_receipt(left,right):
 left=json.loads(json.dumps(left));right=json.loads(json.dumps(right))
 for value in (left,right):
  for field in ("read_reapply","write_reapply"):
   value[field].pop("action",None);value[field].pop("target_before",None)
 return left==right
def seal_blkio_reapply(plan,database,client,path=REAPPLY_RECEIPT,read_root=BLKIO_ROOT,proc_root=Path("/proc"),writer=cgroup_write):
 path=Path(path);sidecar=Path(str(path)+".sha256")
 value=blkio_reapply_value(plan,database,client,read_root,proc_root,writer)
 if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
  if not path.is_file() or path.is_symlink() or not sidecar.is_file() or sidecar.is_symlink() or not equivalent_reapply_receipt(load_sealed(path),value):raise E("existing blkio reapply receipt differs")
  return path
 atomic(path,value)
 if load_sealed(path)!=value:raise E("blkio reapply receipt post-seal differs")
 return path
'''

OLD_CALL = '''spec=importlib.util.spec_from_file_location("tpcc_recommission_adopt",module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);plan=json.loads((ATTEMPT/"plan.json").read_text());fresh_blkio=seal_fresh_blkio_authority(plan,after["database"]);value=adopt_with_fresh_blkio(m,plan,fresh_blkio)'''
NEW_CALL = '''spec=importlib.util.spec_from_file_location("tpcc_recommission_adopt",module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);plan=json.loads((ATTEMPT/"plan.json").read_text());reapply_receipt=seal_blkio_reapply(plan,after["database"],after["client"]);fresh_blkio=seal_fresh_blkio_authority(plan,after["database"]);value=adopt_with_fresh_blkio(m,plan,fresh_blkio)'''

OLD_RECEIPT = '''"fresh_blkio_authority_path":str(fresh_blkio),"fresh_blkio_authority_sha256":sha(fresh_blkio),"legacy_blkio_authority_overwritten":False,"postgresql_mutation_performed":False}'''
NEW_RECEIPT = '''"blkio_reapply_receipt_path":str(reapply_receipt),"blkio_reapply_receipt_sha256":sha(reapply_receipt),"fresh_blkio_authority_path":str(fresh_blkio),"fresh_blkio_authority_sha256":sha(fresh_blkio),"legacy_blkio_authority_overwritten":False,"postgresql_mutation_performed":False}'''


TEST = r'''import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pytest

B = Path(__file__).resolve().parents[1]
C = B / "control/mysql_tpcc_zero_swap_recommission_blkio_reapply_v6.py"

def module():
    spec = importlib.util.spec_from_file_location("tpcc_blkio_v6", C)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value

def plan(m):
    return {"bundle_id": "7e2e13aa", "attempt_id": m.ATTEMPT.name,
            "database": {"name": m.DB}}

def live(m, role, pid):
    return {"container_id": m.DB_ID if role == "database" else m.CLIENT_ID,
            "container_name": m.DB if role == "database" else m.CLIENT, "pid": pid}

def proc(root, pid, comm, cmd, cgroup):
    d = root / str(pid); d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_bytes(b"\0".join(x.encode() for x in cmd) + b"\0")
    (d / "cgroup").write_text("5:blkio:" + cgroup + "\n4:memory:" + cgroup + "\n")

def fixture(m, tmp_path, read="", write="", extra_client=False, pg_target=False):
    cg = tmp_path / "blkio/docker" / m.DB_ID; cg.mkdir(parents=True)
    rp = cg / "blkio.throttle.read_bps_device"; wp = cg / "blkio.throttle.write_bps_device"
    rp.write_text(read); wp.write_text(write)
    pr = tmp_path / "proc"; pr.mkdir()
    proc(pr, 222, "sleep", ["/usr/bin/sleep", "infinity"], "/docker/" + m.CLIENT_ID)
    if extra_client: proc(pr, 223, "java", ["java", "DBWorkload"], "/docker/" + m.CLIENT_ID)
    proc(pr, 333, "postgres", ["postgres"], "/docker/" + (m.DB_ID if pg_target else "pg-safe"))
    calls=[]
    def writer(path, payload):
        calls.append((Path(path), payload)); rows=[]
        for line in Path(path).read_text().splitlines():
            if line.split()[0] != m.BLKIO_DEVICE: rows.append(line)
        rows.append(payload.strip()); Path(path).write_text("\n".join(rows) + "\n")
    return tmp_path / "blkio/docker", pr, calls, writer

def test_reapply_empty_files_exact_payload_and_zero_credit(tmp_path):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path)
    value=m.blkio_reapply_value(plan(m),live(m,"database",111),live(m,"client",222),root,pr,writer)
    assert [payload for _,payload in calls] == ["259:2 1205862400\n"] * 2
    assert value["read_reapply"]["target_after"] == [1205862400]
    assert value["write_reapply"]["target_after"] == [1205862400]
    assert value["postgresql_cgroup_written"] is False
    assert value["workload_started"] is False and value["accepted_rounds_credit"] == 0

def test_active_client_workload_fails_before_any_write(tmp_path):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path,extra_client=True)
    with pytest.raises(m.E,match="workload absence"):
        m.blkio_reapply_value(plan(m),live(m,"database",111),live(m,"client",222),root,pr,writer)
    assert calls == []

def test_postgres_target_collision_fails_before_any_write(tmp_path):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path,pg_target=True)
    with pytest.raises(m.E,match="PostgreSQL"):
        m.blkio_reapply_value(plan(m),live(m,"database",111),live(m,"client",222),root,pr,writer)
    assert calls == []

def test_non_target_rows_are_preserved(tmp_path):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path,"8:0 7\n","8:16 9\n")
    value=m.blkio_reapply_value(plan(m),live(m,"database",111),live(m,"client",222),root,pr,writer)
    assert value["read_reapply"]["non_target_rows_before"] == [["8:0",7]]
    assert value["read_reapply"]["non_target_rows_after"] == [["8:0",7]]
    assert value["write_reapply"]["non_target_rows_after"] == [["8:16",9]]

@pytest.mark.parametrize("bad", ["259:2 1\n", "259:2 1205862400\n259:2 1205862400\n"])
def test_wrong_or_duplicate_target_fails_before_write(tmp_path,bad):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path,bad,"")
    with pytest.raises(m.E):m.blkio_reapply_value(plan(m),live(m,"database",111),live(m,"client",222),root,pr,writer)
    assert calls == []

def test_partial_prior_application_is_idempotently_reconciled(tmp_path):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path,"259:2 1205862400\n","")
    value=m.blkio_reapply_value(plan(m),live(m,"database",111),live(m,"client",222),root,pr,writer)
    assert value["read_reapply"]["action"] == "already_exact"
    assert value["write_reapply"]["action"] == "wrote_exact_target"
    assert len(calls) == 1

def test_writer_failure_produces_no_receipt(tmp_path):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path); receipt=tmp_path/"receipt.json"
    def fail(path,payload):raise OSError("injected")
    with pytest.raises(OSError):m.seal_blkio_reapply(plan(m),live(m,"database",111),live(m,"client",222),receipt,root,pr,fail)
    assert not receipt.exists() and not Path(str(receipt)+".sha256").exists()

def test_receipt_is_non_overwriting_and_idempotent(tmp_path):
    m=module(); root,pr,calls,writer=fixture(m,tmp_path); receipt=tmp_path/"receipt.json"
    m.seal_blkio_reapply(plan(m),live(m,"database",111),live(m,"client",222),receipt,root,pr,writer)
    before=(receipt.stat().st_ino,receipt.stat().st_mtime_ns,receipt.read_bytes())
    m.seal_blkio_reapply(plan(m),live(m,"database",111),live(m,"client",222),receipt,root,pr,writer)
    assert before == (receipt.stat().st_ino,receipt.stat().st_mtime_ns,receipt.read_bytes())
    with pytest.raises(m.E,match="existing blkio reapply receipt differs"):
        m.seal_blkio_reapply(plan(m),live(m,"database",112),live(m,"client",222),receipt,root,pr,writer)

def test_order_and_v5_gates_preserved():
    source=C.read_text()
    call='reapply_receipt=seal_blkio_reapply(plan,after["database"],after["client"]);fresh_blkio=seal_fresh_blkio_authority(plan,after["database"]);value=adopt_with_fresh_blkio'
    assert call in source
    for required in ('after["total_swap_bytes"]!=0','os.environ["DOCKER_HOST"]=SOCKET',
                     'postgresql_mutation_performed":False','accepted_rounds_credit":0'):
        assert required in source

def test_content_address_scope_and_parent():
    manifest=json.loads((B/"bundle_manifest.json").read_text()); unsigned=dict(manifest); unsigned.pop("bundle_id")
    actual=hashlib.sha256((json.dumps(unsigned,sort_keys=True,indent=2)+"\n").encode()).hexdigest()
    assert actual == manifest["bundle_id"] == B.name
    assert manifest["parent_bundle_id"] == "62dc8c6ec76b9cbfb4aca8b00e39a5e5f362072babe9c688bbd1f8dfef94b7e8"
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
    source = (PARENT / "control/mysql_tpcc_zero_swap_recommission_fresh_blkio_v5.py").read_text()
    for old, new, label in (
        (ANCHOR, ANCHOR + "\n" + HELPERS, "blkio reapply helpers"),
        (OLD_CALL, NEW_CALL, "reapply before fresh authority/adopt"),
        (OLD_RECEIPT, NEW_RECEIPT, "reapply receipt binding"),
    ):
        if source.count(old) != 1:
            raise RuntimeError(label + " patch anchor differs")
        source = source.replace(old, new)
    runbook = json.loads((PARENT / "runbook/transaction.json").read_text())
    runbook["schema_version"] = "mysql_tpcc_blkio_reapply_transaction_v6"
    runbook["post_restart_blkio_reapply"] = {
        "receipt": "blkio_throttle_reapply.zero_swap_restart_v1.json",
        "device": "259:2", "read_bps": 1205862400, "write_bps": 1205862400,
        "client_workload_absent_required": True,
        "postgresql_blkio_cgroup_write_prohibited": True,
        "non_target_device_rows_preserved": True,
        "ordering": ["reapply_and_seal_receipt", "seal_fresh_readback_authority", "v5_private_socket_adopt"],
        "partial_reapply_recovery": "already-exact target is retained; missing target is written",
    }
    evidence = {
        "schema_version": "mysql_tpcc_v5_empty_post_restart_blkio_evidence_v1",
        "status": "OFFLINE_SUCCESSOR_ZERO_CREDIT",
        "parent_bundle_id": PARENT_ID,
        "observed_condition": "post-restart cgroup-v1 read and write throttle files contain no target row",
        "docker_hostconfig_read_bps": [], "docker_hostconfig_write_bps": [],
        "root_cause": "the old throttle was not persisted in Docker HostConfig and restart did not recreate it",
        "v6_fix": "write only the exact target-device command to the dedicated database cgroup, immediately read back, seal receipt and fresh authority, then adopt",
        "postgresql_mutation_prohibited": True,
        "workload_started": False, "accepted_rounds_credit": 0,
    }
    contents = {
        CONTROL_REL: source.encode(),
        "runbook/transaction.json": canonical(runbook),
        "evidence/mysql_tpcc_v5_empty_post_restart_blkio_evidence_v1.json": canonical(evidence),
        "tests/test_mysql_tpcc_blkio_reapply_successor_v6.py": TEST.encode(),
        "builder/build_mysql_tpcc_blkio_reapply_successor_v6.py": HERE.read_bytes(),
    }
    files = {relative: digest(raw) for relative, raw in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_blkio_reapply_successor_bundle_v6",
        "status": "SEALED_NOT_EXECUTED", "immutable": True,
        "parent_bundle_id": PARENT_ID, "parent_manifest_sha256": PARENT_SHA,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "dedicated_database_cgroup_only": True,
        "blkio_device_major_minor": "259:2", "blkio_read_bps": 1205862400,
        "blkio_write_bps": 1205862400, "client_workload_absent_required": True,
        "current_container_and_pid_required": True, "postgresql_cgroup_collision_rejected": True,
        "non_target_device_rows_preserved": True, "partial_reapply_idempotently_recoverable": True,
        "reapply_receipt_non_overwriting": True, "fresh_authority_non_overwriting": True,
        "reapply_then_fresh_authority_then_v5_adopt": True,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions_performed": 0,
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
        stage = Path(tempfile.mkdtemp(prefix=".blkio-reapply-", dir=OUT))
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
