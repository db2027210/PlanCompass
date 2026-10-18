#!/usr/bin/env python3
"""Build immutable v16 with signed peer-client container attribution."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,stat,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; SOURCE=Path(__file__).resolve()
PARENT_ID="8f327b8fb378bc8ca4f8f0659e018027e987e4dad9026644006dcc1ef7728e09"
PARENT_SHA="937d19acfde63301d63483609acc982d5635dd5ec3dca095b00b35832cd23392"
PARENT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v15"/PARENT_ID
OUT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v16"; SCHEMA="mysql_tpch_tpcc_shared_collector_bundle_v16"
ENTRY="qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
BUILDER="authority/build_mysql_tpch_tpcc_shared_collector_v16.py"
INVALIDATION="authority/v15b_peer_client_counter_child_invalidation.json"
TEST="tests/test_shared_collector_peer_client_container_v16.py"
PYTHON=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
class BuildError(RuntimeError): pass
def canonical(v): return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(v): return hashlib.sha256(v).hexdigest()
def digest(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as s:
  for b in iter(lambda:s.read(1024*1024),b""): d.update(b)
 return d.hexdigest()
def replace_exact(data,old,new,label):
 if data.count(old)!=1: raise BuildError(label+" patch cardinality differs")
 return data.replace(old,new)
def parent_manifest():
 p=PARENT/"bundle_manifest.json";m=json.loads(p.read_text());u=dict(m);u.pop("bundle_id",None)
 if (m.get("bundle_id")!=PARENT_ID or digest(p)!=PARENT_SHA or sha(canonical(u))!=PARENT_ID
  or m.get("schema_version")!="mysql_tpch_tpcc_shared_collector_bundle_v15"
  or m.get("paired_peer_control_tree_policy")!="reciprocal_attempt_authority_hash_cpu_and_parentage_exact"
  or m.get("unknown_process_change_remains_fail_closed") is not True
  or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True
  or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False): raise BuildError("v15 parent differs")
 for r,h in m["files"].items():
  q=PARENT/r
  if q.is_symlink() or not q.is_file() or digest(q)!=h: raise BuildError("v15 closure differs: "+r)
 return m
def test_source(): return b'''from __future__ import annotations
import hashlib,json,py_compile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v16_binds_peer_client_container_children_only():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v16"
 assert m["paired_peer_client_policy"]=="signed_container_id_exact_cpu_zero_target_writer"
 assert m["unknown_process_change_remains_fail_closed"] is True
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for r,h in m["files"].items():assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
 e=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()
 for x in ("paired peer client identity differs","paired peer client process isolation differs","paired_peer_client_container_id","peer_client_rows"):assert x in e
 assert 'conflicts.append("process_fd_or_io_permission_failure")' in (ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 py_compile.compile(str(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-v16-entry.pyc")))
'''
def invalidation(): return {"schema_version":"mysql_shared_peer_client_counter_child_invalidation_v1","status":"INVALIDATED_ZERO_CREDIT_REPLACED_BY_V16","invalidated_collector_bundle_id":PARENT_ID,"failed_attempts":["tpch-a-paired-v15b-20260801t200500p08-6c3809b8","tpcc-b-paired-v15b-20260801t200500p08-7e2e13aa"],"observed_complete_samples":{"tpch":181,"tpcc":181},"failure":"signed_peer_client_mysql_counter_child_misclassified_as_unknown_co_resident","repair":"signed_peer_client_container_id_exact_cpu_zero_target_writer_attribution","unknown_process_change_remains_fail_closed":True,"qualification_credit":0,"r0_credit":0,"accepted_rounds_credit":0,"postgresql_mutation":False}
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):
  if p.is_symlink(): raise BuildError("v16 symlink")
  os.chmod(p,stat.S_IMODE(p.stat().st_mode)&~0o222)
 os.chmod(root,stat.S_IMODE(root.stat().st_mode)&~0o222)
def clean(root):
 for p in sorted(root.rglob("*"),reverse=True):
  if p.is_dir() and p.name in {"__pycache__",".pytest_cache"}:shutil.rmtree(p)
  elif p.is_file() and p.suffix in {".pyc",".pyo"}:p.unlink()
def fmap(root):
 out={}
 for p in sorted(root.rglob("*")):
  if p.is_symlink():raise BuildError("v16 closure symlink")
  r=p.relative_to(root).as_posix()
  if p.is_file() and r not in {"bundle_manifest.json","bundle_manifest.json.sha256"}:out[r]=digest(p)
 return out
def main():
 parent=parent_manifest();payload={r:(PARENT/r).read_bytes() for r in parent["files"]};modes={r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in parent["files"]}
 old=b'''    def descendants(root_pid):\n'''
 new=b'''    peer_client = peer_command.get("client_container") or {}\n    peer_client_id = peer_client.get("container_id")\n    peer_client_rows = [row for row in rows if row.get("container_identity") == peer_client_id]\n    if (not isinstance(peer_client_id, str) or re.fullmatch(r"[0-9a-f]{64}", peer_client_id) is None\n            or not peer_client_rows):\n        raise core.QualificationError("paired peer client identity differs")\n    for row in peer_client_rows:\n        if (core.expand_cpuset(row.get("cpus_allowed_list", "")) != peer_cpus\n                or int(row.get("declared_device_writable_fds") or 0) != 0):\n            raise core.QualificationError("paired peer client process isolation differs")\n        row["role"] = "qualification_workload"\n    result["paired_peer_client_container_id"] = peer_client_id\n    result["paired_peer_client_processes_exact_authority_verified"] = True\n    def descendants(root_pid):\n'''
 payload[ENTRY]=replace_exact(payload[ENTRY],old,new,"peer client attribution")
 for r,d in list(payload.items()):
  if r.startswith("tests/") and r.endswith(".py"):payload[r]=d.replace(b"mysql_tpch_tpcc_shared_collector_bundle_v15",SCHEMA.encode())
 payload[BUILDER]=SOURCE.read_bytes();modes[BUILDER]=0o444;payload[INVALIDATION]=canonical(invalidation());modes[INVALIDATION]=0o444;payload[TEST]=test_source();modes[TEST]=0o444
 u={k:v for k,v in parent.items() if k not in {"bundle_id","files"}};u.update({"schema_version":SCHEMA,"status":"SEALED_SIGNED_PEER_CLIENT_ATTRIBUTION_NOT_LAUNCH_AUTHORITY","predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,"paired_peer_client_policy":"signed_container_id_exact_cpu_zero_target_writer","unknown_process_change_remains_fail_closed":True,"resource_workload_sample_gates_algorithm_c_unchanged":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":{r:sha(d) for r,d in sorted(payload.items())}})
 bid=sha(canonical(u));manifest=dict(u,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid;test="existing immutable bundle reverified"
 if not dest.exists():
  stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for r,d in payload.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(d);os.chmod(p,modes.get(r,0o444)&~0o222)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n")
   tmp=Path(tempfile.mkdtemp(prefix="mysql-shared-v16-test-"));scratch=tmp/bid
   try:
    shutil.copytree(stage,scratch);tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py"));res=subprocess.run([str(PYTHON),"-m","pytest","-q",*tests],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    if res.returncode:raise BuildError("v16 tests failed:\n"+res.stdout[-16000:])
    test=res.stdout.strip()
   finally:shutil.rmtree(tmp)
   clean(stage);freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 pub=json.loads((dest/"bundle_manifest.json").read_text());chk=dict(pub);chk.pop("bundle_id",None)
 if pub!=manifest or sha(canonical(chk))!=bid or fmap(dest)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [dest,*dest.rglob("*")]):raise BuildError("published v16 differs")
 print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V16","bundle":str(dest),"bundle_id":bid,"bundle_manifest_sha256":digest(dest/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":test,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (BuildError,OSError,ValueError,subprocess.SubprocessError) as e:print("NOT_READY: "+str(e),file=__import__("sys").stderr);raise SystemExit(2)
