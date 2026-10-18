#!/usr/bin/env python3
"""Bind the independent TPC-C controller to the dynamic inventory support."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PARENT_ID="30664c823bc8335e68abd80dfc67cfd89e8374cd1477d5f7532f8efd71bb3a21"
PARENT_SHA="98292857d96695868b23cd1e57607949e5ae844bad21a5487003fe2592b1961a"
PARENT=ROOT/"artifacts/mysql_tpcc_v16_independent_parallel_one_click_v1"/PARENT_ID
SUPPORT_ID="f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23"
SUPPORT_SHA="842fbef539b8c2f42805ed301a41b3f75900b927074b4f9c625a406109dbe4bc"
OUT=ROOT/"artifacts/mysql_tpcc_inventory_controller_successor_v1";HERE=Path(__file__).resolve()
CONTROLLER="controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py"
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hs(Path(p).read_bytes())
def once(t,o,n,l):
 if t.count(o)!=1:raise BuildError(l+" anchor count="+str(t.count(o)))
 return t.replace(o,n,1)

VALIDATE=r'''
  pg=inv.get("mysql_only_pg_http_dynamic_noninterference")
  if (not isinstance(pg,dict)
      or pg.get("schema_version")!="mysql_only_pg_artifact_http_dynamic_noninterference_v1"
      or pg.get("status")!="PASS_SAMPLE"
      or pg.get("not_database_or_benchmark_process") is not True
      or pg.get("static_process_identity_trusted") is not False
      or pg.get("postgresql_mutation_performed") is not False
      or pg.get("target_device_major_minor")!="259:2"
      or pg.get("thresholds")!={"maximum_adjacent_cpu_ticks":2,"maximum_adjacent_read_bytes":0,"maximum_adjacent_write_bytes":0}):
   raise MaterializationError("PG HTTP dynamic non-interference sample differs")
  rows=pg.get("processes")
  if not isinstance(rows,list) or sorted(r.get("subrole") for r in rows)!=["root","server"]:
   raise MaterializationError("PG HTTP dynamic process coverage differs")
  projection=[]
  for row in sorted(rows,key=lambda r:r["subrole"]):
   delta=row.get("adjacent_delta") or {}
   if (int(row.get("target_device_writable_fds") or 0)!=0
       or int(delta.get("cpu_ticks") or 0)<0 or int(delta.get("cpu_ticks") or 0)>2
       or int(delta.get("read_bytes") or 0)!=0 or int(delta.get("write_bytes") or 0)!=0
       or row.get("source_major_minor")=="259:2"):
    raise MaterializationError("PG HTTP dynamic interference observed")
   identity=tuple(json.dumps(row.get(k),sort_keys=True) for k in ("pid","starttime_ticks","cmdline_sha256","exe_path","exe_stat_identity","cwd","source_major_minor","cpus_allowed_list","mems_allowed_list"))
   projection.append((row["subrole"],identity))
   pg_totals[row["subrole"]]["cpu_ticks"].append(int(row["cpu_ticks"]))
   pg_totals[row["subrole"]]["read_bytes"].append(int(row["read_bytes"]))
   pg_totals[row["subrole"]]["write_bytes"].append(int(row["write_bytes"]))
  if pg_identity is None:pg_identity=projection
  elif projection!=pg_identity:raise MaterializationError("PG HTTP identity changed across rolling window")
'''

TEST=r'''import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py"
def test_content_address_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True and m["accepted_round_authorized"] is False
def test_exact_support_and_dynamic_window_validation():
 s=S.read_text();assert 'COLLECTOR_ID = "f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23"' in s and 'COLLECTOR_MANIFEST_SHA = "842fbef539b8c2f42805ed301a41b3f75900b927074b4f9c625a406109dbe4bc"' in s
 assert "PG HTTP identity changed across rolling window" in s and "whole-window CPU threshold exceeded" in s and "pg_http_window_sample_count" in s
def test_typed_l2_l3_and_metric_contract_retained():
 m=json.loads((B/"bundle_manifest.json").read_text());assert m["typed_binding_count"]==34 and m["transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"] and m["transaction_aliases"]=={"SensitiveStockLevel":"StockLevel"};assert m["native_metric_sequences"]=={"consumer":"0..180","phase_prime":1,"producer_start":2}
'''

def verify_parent():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA or hs(canonical(u))!=PARENT_ID or m.get("bundle_id")!=PARENT_ID:raise BuildError("parent identity differs")
 for rel,want in m["files"].items():
  if sha(PARENT/rel)!=want:raise BuildError("parent closure differs "+rel)
 return m
def patch_controller(raw):
 t=raw.decode()
 t=once(t,'COLLECTOR_ID = "6953a554fc57508d6685e95581935ce7eb7f0969c144265f2d1da034b9c7bf41"','COLLECTOR_ID = "'+SUPPORT_ID+'"',"support ID")
 t=once(t,'COLLECTOR_MANIFEST_SHA = "f2dba3ad086bf60ba5a930c46410558adde46e472a0c3143424e1a70eb8c025f"','COLLECTOR_MANIFEST_SHA = "'+SUPPORT_SHA+'"',"support SHA")
 t=once(t,' counts=[]\n for sample in samples:',' counts=[]\n pg_identity=None\n pg_totals={role:{name:[] for name in ("cpu_ticks","read_bytes","write_bytes")} for role in ("root","server")}\n for sample in samples:',"dynamic init")
 t=once(t,'  counts.append(len(inv.get("processes") or []))\n return {"sample_count":len(samples),','  counts.append(len(inv.get("processes") or []))\n'+VALIDATE+'\n for role,totals in pg_totals.items():\n  if totals["cpu_ticks"][-1]-totals["cpu_ticks"][0]>30 or totals["read_bytes"][-1]!=totals["read_bytes"][0] or totals["write_bytes"][-1]!=totals["write_bytes"][0]:raise MaterializationError("PG HTTP whole-window CPU threshold exceeded or IO changed")\n return {"pg_http_policy_version":"mysql_only_pg_artifact_http_dynamic_noninterference_v1","pg_http_window_sample_count":len(samples),"pg_http_identity":pg_identity,"sample_count":len(samples),',"dynamic validation")
 return t.encode()
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)
def main():
 pm=verify_parent();content={}
 for rel in sorted(pm["files"]):
  if not rel.startswith("tests/") and not rel.startswith("builder/"):content[rel]=(PARENT/rel).read_bytes()
 content[CONTROLLER]=patch_controller(content[CONTROLLER]);content["tests/test_mysql_tpcc_inventory_controller_successor_v1.py"]=TEST.encode();content["builder/build_mysql_tpcc_inventory_controller_successor_v1.py"]=HERE.read_bytes()
 audit={"schema_version":"mysql_tpcc_30664_failure_successor_authority_v1","failed_bundle_id":PARENT_ID,"failed_credit":0,"failed_attempt_rerun_prohibited":True,"successor_support_id":SUPPORT_ID,"postgresql_mutation_prohibited":True};content["authority/mysql_tpcc_30664_failure_successor_authority_v1.json"]=canonical(audit)
 files={rel:hs(payload) for rel,payload in sorted(content.items())};u={"schema_version":"mysql_tpcc_inventory_controller_successor_bundle_v1","status":"SEALED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"support_bundle_id":SUPPORT_ID,"support_manifest_sha256":SUPPORT_SHA,"failed_attempt_credit":0,"failed_attempt_rerun_prohibited":True,"typed_binding_count":34,"transaction_types":["Delivery","NewOrder","OrderStatus","Payment","StockLevel"],"transaction_aliases":{"SensitiveStockLevel":"StockLevel"},"qps_resource_input_prohibited":True,"native_metric_sequences":{"consumer":"0..180","phase_prime":1,"producer_start":2},"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":files};bid=hs(canonical(u));m=dict(u,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/bid
 if dst.exists():print(bid);print(sha(dst/"bundle_manifest.json"));print(dst);return
 st=Path(tempfile.mkdtemp(prefix=".tpcc-inventory-controller-",dir=OUT))
 try:
  for rel,payload in content.items():p=st/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payload)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(st);os.rename(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 print(bid);print(sha(dst/"bundle_manifest.json"));print(dst)
if __name__=="__main__":main()
