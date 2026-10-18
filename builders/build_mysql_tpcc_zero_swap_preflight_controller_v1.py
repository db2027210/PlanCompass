#!/usr/bin/env python3
"""Build a fail-fast zero-swap controller successor after the seq180 failure."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PARENT_ID="59c1bb29f17bc6b1745c6ee0b11258f2a2d18589047d36f192bf111acb37d890";PARENT_SHA="c28fc6ff1caee275f925f99f88c807ce056fdfba0602e489c1031d3f7189b731"
PARENT=ROOT/"artifacts/mysql_tpcc_inventory_controller_successor_v1"/PARENT_ID;OUT=ROOT/"artifacts/mysql_tpcc_zero_swap_preflight_controller_v1";HERE=Path(__file__).resolve();CTL="controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py"
class E(RuntimeError):pass
def can(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hs(Path(p).read_bytes())
def once(t,o,n,l):
 if t.count(o)!=1:raise E(l+" anchor="+str(t.count(o)))
 return t.replace(o,n,1)
HELPER=r'''

def zero_swap_readback(pid,label,expected_memory_bytes):
 d=Path("/proc")/str(pid);raw=(d/"stat").read_text(encoding="ascii");end=raw.rfind(") ")
 if end<0:raise MaterializationError(label+" process stat differs")
 start=int(raw[end+2:].split()[19]);rows=[]
 for line in (d/"cgroup").read_text(encoding="utf-8").splitlines():
  parts=line.split(":",2)
  if len(parts)==3 and "memory" in parts[1].split(","):rows.append(parts[2])
 if len(rows)!=1:raise MaterializationError(label+" memory cgroup identity differs")
 root=Path("/sys/fs/cgroup/memory")/rows[0].lstrip("/")
 def integer(name):return int((root/name).read_text(encoding="ascii").strip())
 stat_rows={}
 for line in (root/"memory.stat").read_text(encoding="ascii").splitlines():
  parts=line.split()
  if len(parts)==2:stat_rows[parts[0]]=int(parts[1])
 swap=stat_rows.get("total_swap")
 memory_limit=integer("memory.limit_in_bytes");memsw_limit=integer("memory.memsw.limit_in_bytes")
 if (swap is None or swap!=0 or memory_limit!=int(expected_memory_bytes)
     or memsw_limit!=int(expected_memory_bytes)):
  raise MaterializationError(label+" zero-swap preflight failed: total_swap="+str(swap)+" memory_limit="+str(memory_limit)+" memsw_limit="+str(memsw_limit))
 return {"schema_version":"mysql_tpcc_zero_swap_live_readback_v1","role":label,"pid":pid,"process_start_ticks":start,"memory_cgroup_path":rows[0],"memory_limit_bytes":memory_limit,"memory_swap_limit_bytes":memsw_limit,"swap_capacity_bytes":0,"total_swap_bytes":swap,"status":"PASS_ZERO_SWAP_BEFORE_WORKLOAD","read_only":True}
'''
TEST=r'''import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py"
def test_id_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_fail_fast_both_cgroups_and_double_check():
 s=S.read_text();assert s.count("zero_swap_readback(")>=5 and "zero-swap preflight failed: total_swap=" in s and 'zero_swap_readback(dbpid,"database"' in s and 'zero_swap_readback(cpid,"client"' in s and '"zero_swap_preflight":swap_preflight' in s and 'p.get("zero_swap_preflight")!={"database":dbswap,"client":cswap}' in s
def test_typed_and_dynamic_contracts_retained():
 m=json.loads((B/"bundle_manifest.json").read_text());assert m["typed_binding_count"]==34 and m["transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"] and m["transaction_aliases"]=={"SensitiveStockLevel":"StockLevel"};s=S.read_text();assert "mysql_only_pg_artifact_http_dynamic_noninterference_v1" in s and "PG HTTP whole-window CPU threshold exceeded" in s
'''
def verify():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA or hs(can(u))!=PARENT_ID or m.get("bundle_id")!=PARENT_ID:raise E("parent differs")
 for r,w in m["files"].items():
  if sha(PARENT/r)!=w:raise E("closure "+r)
 return m
def patch(raw):
 t=raw.decode();t=once(t,"\ndef execute(a):\n",HELPER+"\n\ndef execute(a):\n","helper")
 t=once(t,' dbpid,_=docker_live(db["container_id"],"database",DB_CPUS);cpid,client_row=docker_live(client["container_id"],"client",CLIENT_CPUS);actual_name=', ' dbpid,_=docker_live(db["container_id"],"database",DB_CPUS);cpid,client_row=docker_live(client["container_id"],"client",CLIENT_CPUS);dbswap=zero_swap_readback(dbpid,"database",34359738368);cswap=zero_swap_readback(cpid,"client",8589934592)\n if p.get("zero_swap_preflight")!={"database":dbswap,"client":cswap}:raise MaterializationError("zero-swap plan/live readback differs")\n actual_name=',"execute preflight")
 t=once(t,' docker_live(db.get("container_id"),"database",DB_CPUS);_,client_row=docker_live(client.get("container_id"),"client",CLIENT_CPUS);actual_client_name=', ' dbpid,_=docker_live(db.get("container_id"),"database",DB_CPUS);cpid,client_row=docker_live(client.get("container_id"),"client",CLIENT_CPUS);swap_preflight={"database":zero_swap_readback(dbpid,"database",34359738368),"client":zero_swap_readback(cpid,"client",8589934592)};actual_client_name=',"run preflight")
 t=once(t,'"dynamic_noninterference_gate_required":True,"tpch_success_dependency_used":False', '"dynamic_noninterference_gate_required":True,"zero_swap_preflight":swap_preflight,"tpch_success_dependency_used":False',"plan bind")
 return t.encode()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def main():
 pm=verify();c={}
 for r in sorted(pm["files"]):
  if not r.startswith("tests/") and not r.startswith("builder/"):c[r]=(PARENT/r).read_bytes()
 c[CTL]=patch(c[CTL]);c["tests/test_mysql_tpcc_zero_swap_preflight_controller_v1.py"]=TEST.encode();c["builder/build_mysql_tpcc_zero_swap_preflight_controller_v1.py"]=HERE.read_bytes();c["authority/seq180_zero_swap_failure_v1.json"]=can({"schema_version":"mysql_tpcc_seq180_zero_swap_failure_v1","failed_attempt":"tpcc-v16-7e2e13aa-20260801t121500p08-qualification_solo-independent-2c4e9417c4ca426c922bac96","failed_credit":0,"rerun_prohibited":True,"target_database_swap_observed_bytes":1196032,"client_swap_observed_bytes":13922304,"simple_relaxation_prohibited":True})
 files={r:hs(v) for r,v in sorted(c.items())};u={"schema_version":"mysql_tpcc_zero_swap_preflight_controller_bundle_v1","status":"SEALED_FAIL_FAST_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"zero_swap_preflight_roles":["database","client"],"database_memory_bytes":34359738368,"client_memory_bytes":8589934592,"memory_swap_equals_memory_required":True,"typed_binding_count":34,"transaction_types":["Delivery","NewOrder","OrderStatus","Payment","StockLevel"],"transaction_aliases":{"SensitiveStockLevel":"StockLevel"},"qps_resource_input_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":files};bid=hs(can(u));m=dict(u,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);d=OUT/bid
 if d.exists():print(bid);print(sha(d/"bundle_manifest.json"));print(d);return
 st=Path(tempfile.mkdtemp(prefix=".zero-swap-",dir=OUT))
 try:
  for r,v in c.items():p=st/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=can(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(st);os.rename(st,d)
 finally:
  if st.exists():shutil.rmtree(st)
 print(bid);print(sha(d/"bundle_manifest.json"));print(d)
if __name__=="__main__":main()
