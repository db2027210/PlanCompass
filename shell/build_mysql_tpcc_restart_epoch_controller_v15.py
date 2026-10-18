#!/usr/bin/env python3
"""Build explicit TPC-C v15 controller for the restarted DB/collector epoch."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE_ID="48e6dc9ab33a379f5d0b8a797136cdfcd9c080850ba0a976dac89dfd06b7fd99";BASE_SHA="134ce2afc8625b5a32b52108d37374da46ad79b66a8cdfc793fa43c9f8de011b";BASE=ROOT/"artifacts/mysql_tpcc_v14_explicit_authority_successor_v1"/BASE_ID
COLLECTOR_ID="aabee55a007931f703e1d7eca22b0b23dc347750aa65f61d8e629282ddf2f7e2";COLLECTOR_SHA="54b20500c937a09692b2592bd22c2451d3a5c4746ba5d6e76493fd0903568d17"
OLD_COLLECTOR="ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec";OLD_COLLECTOR_SHA="a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127"
ADOPTED_SHA="06d546e9bcde677f6b5f784e38d756af1238a858dbd2db4737cb5a884bd2f848";BLKIO_SHA="d553c3670a81a2794a7f34ba43986828b2fe377c1bdaf98b0e865ee06b511632";COMPLETION_SHA="45d753710e82a4f6220f0dc73a4757b0e275e75a50566b048f8a47c430d7bb59"
COLLECTOR=ROOT/"artifacts/mysql_tpcc_inventory_restart_epoch_v15"/COLLECTOR_ID;EVIDENCE=Path("/tmp/mysql_tpcc_v15_recovery_evidence_local")
OUT=ROOT/"artifacts/mysql_tpcc_restart_epoch_controller_v15"
OUTPUT="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/v15-restart-epoch-qualification-fresh-r0"

def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)

TEST=r'''import ast,hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];M=B/"bundle_manifest.json";C=B/"control/mysql_tpcc_qualification_fresh_r0_v15.py";L=B/"lib/mysql_tpcc_inventory_controller_v1.py"
CID="aabee55a007931f703e1d7eca22b0b23dc347750aa65f61d8e629282ddf2f7e2";CSHA="54b20500c937a09692b2592bd22c2451d3a5c4746ba5d6e76493fd0903568d17"
def can(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_identity_scope_zero_credit():m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(can(u)).hexdigest()==m["bundle_id"]==B.name and m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_new_live_epoch_exact():
 s=C.read_text();ast.parse(s)
 for x in ('ADOPTED = ATTEMPT / "recovery/v15-restart-zero-swap/adopted_service.restart_zero_swap_v1.json"','ADOPTED_SHA = "06d546e9bcde677f6b5f784e38d756af1238a858dbd2db4737cb5a884bd2f848"',"DB_PID = 19638","DB_START = 269050791","mysql_tpcc_restart_zero_swap_adopted_service_v1","PASS_DURABLE_ZERO_SWAP_RESTARTED_IDLE_READY"):assert x in s
def test_controller_library_and_authority_all_bind_new_collector():
 c=C.read_text();l=L.read_text();assert CID in c and CSHA in c and CID in l and CSHA in l and "L.authority(command, command_sha, hashlib.sha256(token.encode()).hexdigest(), COLLECTOR_ID, COLLECTOR_MANIFEST_SHA" in c
def test_dual_binding_result_exact():
 q=importlib.util.spec_from_file_location("v15lib",L);m=importlib.util.module_from_spec(q);q.loader.exec_module(m);a=m.authority({"bundle_id":"b","mode":"qualification_solo","qualification_attempt_id":"a"},"c","t",CID,CSHA,1,1800);assert a["collector_bundle_id"]==CID and a["collector_manifest_sha256"]==CSHA
def test_preflight_new_entry_and_epoch_hashes():s=C.read_text();assert "mysql_tpcc_restart_epoch_preflight_v15.py" in s and 'evidence.get("status") != "PASS_READ_ONLY_RESTART_EPOCH_PREFLIGHT"' in s and "d553c3670a81a2794a7f34ba43986828b2fe377c1bdaf98b0e865ee06b511632" in s and s.index("run_collector_preflight(plan, run_root, collector)")<s.index('run_stage(plan, "qualification"')
def test_v15_new_attempt_root_and_no_old_reuse():
 s=C.read_text();r=json.loads((B/"runbook/one_click.json").read_text());assert 'tag = "v15-"' in s and "v15-restart-epoch-qualification-fresh-r0" in r["argv"][-1] and r["build_executes_workload"] is False and r["main_agent_execution_required"] is True
def test_recovery_evidence_and_old_failure_zero_credit():m=json.loads(M.read_text());assert m["recovery_completion_sha256"]=="45d753710e82a4f6220f0dc73a4757b0e275e75a50566b048f8a47c430d7bb59" and m["failed_v14_event_rerun_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_authority_pre_and_post_seal_readback():s=C.read_text();assert "assert_collector_authority(authority)" in s and "assert_collector_authority(sealed_authority)" in s
'''

def verify(path,identity,manifest_sha):
 mp=path/"bundle_manifest.json";m=json.loads(mp.read_text());u=dict(m)
 if sha(mp.read_bytes())!=manifest_sha or u.pop("bundle_id",None)!=identity or sha(canonical(u))!=identity:raise RuntimeError("bundle identity differs: "+identity)
 for rel,want in m["files"].items():
  if sha((path/rel).read_bytes())!=want:raise RuntimeError("bundle closure differs: "+rel)
 return m
def evidence(name,want):
 p=EVIDENCE/name
 if sha(p.read_bytes())!=want or (EVIDENCE/(name+".sha256")).read_text()!=want+"  "+name+"\n":raise RuntimeError("recovery evidence differs: "+name)
 return p.read_bytes()
def patch_controller(raw):
 s=raw.decode().replace("v14","v15").replace("V14","V15")
 changes={
  'ADOPTED = ATTEMPT / "adopted_service.recreated_swappiness0_v3.json"':'ADOPTED = ATTEMPT / "recovery/v15-restart-zero-swap/adopted_service.restart_zero_swap_v1.json"',
  'ADOPTED_SHA = "b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001"':'ADOPTED_SHA = "'+ADOPTED_SHA+'"',
  'DB_PID = 100769':'DB_PID = 19638','DB_START = 268728753':'DB_START = 269050791',
  'COLLECTOR_ID = "'+OLD_COLLECTOR+'"':'COLLECTOR_ID = "'+COLLECTOR_ID+'"','COLLECTOR_MANIFEST_SHA = "'+OLD_COLLECTOR_SHA+'"':'COLLECTOR_MANIFEST_SHA = "'+COLLECTOR_SHA+'"',
  'adopted.get("schema_version") != "mysql_tpcc_recreated_service_authority_v3"':'adopted.get("schema_version") != "mysql_tpcc_restart_zero_swap_adopted_service_v1"',
  'adopted.get("status") != "PASS_DURABLE_ZERO_SWAP_RECREATED_IDLE_READY"':'adopted.get("status") != "PASS_DURABLE_ZERO_SWAP_RESTARTED_IDLE_READY"',
  'collector / "qualification/mysql_tpcc_recreated_authority_preflight_v15.py"':'collector / "qualification/mysql_tpcc_restart_epoch_preflight_v15.py"',
  'evidence.get("status") != "PASS_READ_ONLY_RECREATED_AUTHORITY_PREFLIGHT"':'evidence.get("status") != "PASS_READ_ONLY_RESTART_EPOCH_PREFLIGHT"',
  'evidence.get("blkio_authority_sha256") != "b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8"':'evidence.get("blkio_authority_sha256") != "'+BLKIO_SHA+'"',
 }
 for old,new in changes.items():
  if s.count(old)!=1:raise RuntimeError("controller patch marker differs: "+old)
  s=s.replace(old,new)
 return s.encode()
def patch_lib(raw):
 s=raw.decode();changes={'COLLECTOR_ID = "'+OLD_COLLECTOR+'"':'COLLECTOR_ID = "'+COLLECTOR_ID+'"','COLLECTOR_MANIFEST_SHA = "'+OLD_COLLECTOR_SHA+'"':'COLLECTOR_MANIFEST_SHA = "'+COLLECTOR_SHA+'"'}
 for old,new in changes.items():
  if s.count(old)!=1:raise RuntimeError("library collector marker differs")
  s=s.replace(old,new)
 return s.encode()
def main():
 bm=verify(BASE,BASE_ID,BASE_SHA);verify(COLLECTOR,COLLECTOR_ID,COLLECTOR_SHA);contents={}
 for rel in sorted(bm["files"]):
  if rel in ("control/mysql_tpcc_qualification_fresh_r0_v14.py","lib/mysql_tpcc_inventory_controller_v1.py","runbook/one_click.json") or rel.startswith(("builder/","tests/")):continue
  contents[rel]=(BASE/rel).read_bytes()
 contents["control/mysql_tpcc_qualification_fresh_r0_v15.py"]=patch_controller((BASE/"control/mysql_tpcc_qualification_fresh_r0_v14.py").read_bytes());contents["lib/mysql_tpcc_inventory_controller_v1.py"]=patch_lib((BASE/"lib/mysql_tpcc_inventory_controller_v1.py").read_bytes());contents["tests/test_mysql_tpcc_restart_epoch_controller_v15.py"]=TEST.encode();contents["builder/build_mysql_tpcc_restart_epoch_controller_v15.py"]=Path(__file__).read_bytes()
 for name,want in (("completion.json",COMPLETION_SHA),("adopted_service.restart_zero_swap_v1.json",ADOPTED_SHA),("blkio_throttle_authority.restart_zero_swap_v1.json",BLKIO_SHA)):
  contents["authority/recovery/"+name]=evidence(name,want)
 runbook={"schema_version":"mysql_tpcc_restart_epoch_controller_runbook_v15","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"main_agent_execution_required":True,"build_executes_workload":False,"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"recovery_completion_sha256":COMPLETION_SHA,"failed_v14_event_rerun_prohibited":True,"execution_order":["restart_epoch_read_only_preflight","qualification_exact_180s_181_samples","fresh_r0_exact_180s_181_samples"],"argv":["/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11","-B","[THIS_PUBLISHED_BUNDLE]/control/mysql_tpcc_qualification_fresh_r0_v15.py","one-click","--output-root",OUTPUT],"accepted_rounds_credit":0};contents["runbook/one_click.json"]=canonical(runbook)
 files={rel:sha(raw) for rel,raw in sorted(contents.items())};unsigned={"schema_version":"mysql_tpcc_restart_epoch_controller_bundle_v15","status":"SEALED_NOT_PUBLISHED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_controller_bundle_id":BASE_ID,"predecessor_controller_manifest_sha256":BASE_SHA,"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"recovery_completion_sha256":COMPLETION_SHA,"recovery_adopted_authority_sha256":ADOPTED_SHA,"recovery_blkio_authority_sha256":BLKIO_SHA,"database_container_id":"fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948","database_pid":19638,"database_process_start_ticks":269050791,"database_process_cgroup_sha256":"0ac085e5b6b435d7610695e5fcb2c5e3b97f2e720b845d38565d6cdfde3bae3a","explicit_dual_authority_binding_required":True,"sealed_authority_readback_required":True,"failed_v14_event_rerun_prohibited":True,"formal_outer_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"data_plane_actions_performed":0,"files":files}
 identity=sha(canonical(unsigned));manifest=dict(unsigned,bundle_id=identity);raw=canonical(manifest);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/identity
 if dst.exists():
  if (dst/"bundle_manifest.json").read_bytes()!=raw:raise RuntimeError("existing manifest differs")
  for rel,payload in contents.items():
   if (dst/rel).read_bytes()!=payload:raise RuntimeError("existing closure differs: "+rel)
 else:
  stage=Path(tempfile.mkdtemp(prefix=".tpcc-controller-v15-",dir=OUT))
  try:
   for rel,payload in contents.items():p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payload)
   (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 print(identity);print(sha(raw));print(dst)
if __name__=="__main__":main()
