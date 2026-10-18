#!/usr/bin/env python3
"""Build v14 with controller-library collector authority synchronized."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
V13=ROOT/"artifacts/mysql_tpcc_qualification_fresh_r0_v13/93768e5c760ed9817de90ae2a3011223c0368309e5d1176138ae7b74c1447756"
TYPED_ID="1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c";TYPED=ROOT/"artifacts/mysql_tpcc_typed_collector_portable_v2"/TYPED_ID
COLLECTOR_ID="ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec";COLLECTOR_SHA="a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127"
OUT=ROOT/"artifacts/mysql_tpcc_qualification_fresh_r0_v14"
PINNED="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
OUTPUT="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/v14-qualification-fresh-r0"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
TEST=r'''import ast,hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];M=B/"bundle_manifest.json";C=B/"control/mysql_tpcc_qualification_fresh_r0_v14.py";L=B/"lib/mysql_tpcc_inventory_controller_v1.py"
def can(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_identity_zero_credit():m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(can(u)).hexdigest()==m["bundle_id"]==B.name and m["accepted_rounds_credit"]==0
def test_controller_and_library_same_collector():
 c=C.read_text();l=L.read_text();ast.parse(c);ast.parse(l)
 for s in (c,l):assert 'COLLECTOR_ID = "ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec"' in s and 'COLLECTOR_MANIFEST_SHA = "a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127"' in s
def test_authority_emits_new_identity():
 q=importlib.util.spec_from_file_location("v14lib",L);m=importlib.util.module_from_spec(q);q.loader.exec_module(m);a=m.authority({"bundle_id":"x","mode":"qualification_solo","qualification_attempt_id":"a"},"c","t","m",0,10);assert a["collector_bundle_id"]=="ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec"
def test_new_attempt_and_preflight_order():s=C.read_text();assert 'tag = "v14-"' in s and s.index("run_collector_preflight(plan, run_root, collector)")<s.index('run_stage(plan, "qualification"')
def test_v13_failure_invalidated():f=json.loads((B/"authority/v13_failure.json").read_text());assert f["workload_pid"]==3218 and f["rerun_prohibited"] is True and f["accepted_rounds_credit"]==0
'''
def main():
 c=(V13/"control/mysql_tpcc_qualification_fresh_r0_v13.py").read_text().replace("v13","v14").replace("V13","V14")
 l=(V13/"lib/mysql_tpcc_inventory_controller_v1.py").read_text()
 oldid='COLLECTOR_ID = "f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23"';oldsha='COLLECTOR_MANIFEST_SHA = "842fbef539b8c2f42805ed301a41b3f75900b927074b4f9c625a406109dbe4bc"'
 if l.count(oldid)!=1 or l.count(oldsha)!=1:raise RuntimeError("v13 inherited library constants differ")
 l=l.replace(oldid,'COLLECTOR_ID = "'+COLLECTOR_ID+'"').replace(oldsha,'COLLECTOR_MANIFEST_SHA = "'+COLLECTOR_SHA+'"')
 failure={"schema_version":"mysql_tpcc_v13_signed_collector_authority_failure_v1","status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","output_root":"/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/v13-qualification-fresh-r0/v13-20260804T082319Z-68011af90ec6","fresh_error":"paired collector signed authority differs","root_cause":"controller library authority emitted f5a7/842f while executed collector was ba98/a389","collector_preflight_passed":True,"workload_pid":3218,"workload_pid_alive_after_failure":False,"fresh_r0_started":False,"rerun_prohibited":True,"reusable":False,"accepted_rounds_credit":0,"postgresql_mutation_performed":False}
 runbook={"schema_version":"mysql_tpcc_qualification_fresh_r0_one_click_runbook_v14","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"main_agent_execution_required":True,"build_executes_workload":False,"execution_order":["recreated_authority_preflight","qualification_180s_181","fresh_r0_180s_181"],"argv":[PINNED,"-B","[THIS_PUBLISHED_BUNDLE]/control/mysql_tpcc_qualification_fresh_r0_v14.py","one-click","--output-root",OUTPUT],"accepted_rounds_credit":0}
 contents={"control/mysql_tpcc_qualification_fresh_r0_v14.py":c.encode(),"lib/mysql_tpcc_inventory_controller_v1.py":l.encode(),"authority/v13_failure.json":canonical(failure),"runbook/one_click.json":canonical(runbook),"tests/test_mysql_tpcc_qualification_fresh_r0_v14.py":TEST.encode(),"builder/build_mysql_tpcc_qualification_fresh_r0_v14.py":Path(__file__).read_bytes()}
 for p in TYPED.iterdir():
  if p.is_file():contents["typed_collector/"+p.name]=p.read_bytes()
 files={r:sha(p) for r,p in sorted(contents.items())};unsigned={"schema_version":"mysql_tpcc_qualification_fresh_r0_bundle_v14","status":"SEALED_NOT_PUBLISHED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":V13.name,"predecessor_manifest_sha256":sha((V13/"bundle_manifest.json").read_bytes()),"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"controller_library_collector_authority_synchronized":True,"failed_v13_rerun_prohibited":True,"dynamic_window_seconds":180,"exact_sample_count":181,"qualification_before_fresh_r0":True,"formal_outer_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"data_plane_actions_performed":0,"files":files}
 identity=sha(canonical(unsigned));manifest=dict(unsigned,bundle_id=identity);raw=canonical(manifest);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/identity
 if dst.exists():
  if (dst/"bundle_manifest.json").read_bytes()!=raw:raise RuntimeError("existing manifest differs")
  for r,p in contents.items():
   if (dst/r).read_bytes()!=p:raise RuntimeError("existing closure differs: "+r)
 else:
  stage=Path(tempfile.mkdtemp(prefix=".tpcc-v14-",dir=OUT))
  try:
   for r,p in contents.items():q=stage/r;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(p)
   (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 print(identity);print(sha(raw));print(dst)
if __name__=="__main__":main()
