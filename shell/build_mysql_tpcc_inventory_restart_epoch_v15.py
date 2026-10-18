#!/usr/bin/env python3
"""Build the TPC-C collector bound to the successful v15 restart epoch."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE_ID="ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec"
BASE_SHA="a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127"
BASE=ROOT/"artifacts/mysql_tpcc_inventory_dynamic_recreated_v13"/BASE_ID
EVIDENCE=Path("/tmp/mysql_tpcc_v15_recovery_evidence_local")
OUT=ROOT/"artifacts/mysql_tpcc_inventory_restart_epoch_v15"
ENTRY="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
OLD_ADOPTED="b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001";NEW_ADOPTED="06d546e9bcde677f6b5f784e38d756af1238a858dbd2db4737cb5a884bd2f848"
OLD_BLKIO="b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8";NEW_BLKIO="d553c3670a81a2794a7f34ba43986828b2fe377c1bdaf98b0e865ee06b511632"

def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)

TEST=r'''import ast,hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];M=B/"bundle_manifest.json";E=B/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";P=B/"qualification/mysql_tpcc_restart_epoch_preflight_v15.py"
def can(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_identity_scope_zero_credit():m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(can(u)).hexdigest()==m["bundle_id"]==B.name and m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_new_epoch_exact():
 s=E.read_text();ast.parse(s)
 for x in ("recovery/v15-restart-zero-swap/adopted_service.restart_zero_swap_v1.json","06d546e9bcde677f6b5f784e38d756af1238a858dbd2db4737cb5a884bd2f848","recovery/v15-restart-zero-swap/blkio_throttle_authority.restart_zero_swap_v1.json","d553c3670a81a2794a7f34ba43986828b2fe377c1bdaf98b0e865ee06b511632","TPCC_RECREATED_DB_PID = 19638","TPCC_RECREATED_DB_START = 269050791"):assert x in s
def test_restart_schemas_and_zero_swap_samples():s=E.read_text();assert "mysql_tpcc_restart_zero_swap_adopted_service_v1" in s and "PASS_DURABLE_ZERO_SWAP_RESTARTED_IDLE_READY" in s and "mysql_tpcc_restart_zero_swap_blkio_authority_v1" in s and "PASS_FRESH_RESTART_EPOCH" in s and 'len(adopted.get("zero_swap_samples") or []) != 3' in s
def test_old_epoch_cannot_pass():
 s=E.read_text()
 for x in ("100769","268728753","b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001","b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8"):assert x not in s
def test_real_read_only_preflight():s=P.read_text();ast.parse(s);assert "validate_effective_blkio_authority" in s and "validate_container_readback" in s and '"workload_started":False' in s and '"postgresql_mutation_performed":False' in s
def test_recovery_evidence_closure():
 m=json.loads(M.read_text());assert m["recovery_completion_sha256"]=="45d753710e82a4f6220f0dc73a4757b0e275e75a50566b048f8a47c430d7bb59" and m["recovery_adopted_authority_sha256"]=="06d546e9bcde677f6b5f784e38d756af1238a858dbd2db4737cb5a884bd2f848" and m["recovery_blkio_authority_sha256"]=="d553c3670a81a2794a7f34ba43986828b2fe377c1bdaf98b0e865ee06b511632"
'''

PREFLIGHT=r'''#!/usr/bin/env python3
"""Read-only real preflight for the restarted TPC-C authority epoch."""
import argparse,hashlib,importlib.util,json
from pathlib import Path
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True);p.add_argument("--container",required=True);p.add_argument("--defaults-file",type=Path,required=True);a=p.parse_args();root=Path(__file__).resolve().parents[1];mp=root/"bundle_manifest.json";m=json.loads(mp.read_text());u=dict(m);identity=u.pop("bundle_id",None)
 if root.name!=identity or hashlib.sha256(canonical(u)).hexdigest()!=identity:raise RuntimeError("collector self identity differs")
 for rel,want in m["files"].items():
  q=root/rel
  if q.is_symlink() or not q.is_file() or sha(q)!=want:raise RuntimeError("collector closure differs: "+rel)
 ep=root/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";s=importlib.util.spec_from_file_location("tpcc_v15_collector",ep);mod=importlib.util.module_from_spec(s);s.loader.exec_module(mod);mod._activate_profile(mod.PROFILES["tpcc_cpu"]);manifest,runtime=mod.verify_current_target_seal(a.bundle,"tpcc_cpu");row=mod.core.inspect_container(a.container);blkio=mod.validate_effective_blkio_authority(row,runtime,a.defaults_file,manifest["bundle_id"]);readback=mod.validate_container_readback(row,runtime,blkio,manifest["bundle_id"],a.defaults_file.parent.parent.name)
 print(json.dumps({"status":"PASS_READ_ONLY_RESTART_EPOCH_PREFLIGHT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_performed":False,"workload_started":False,"accepted_rounds_credit":0,"collector_bundle_id":identity,"database_container_id":readback["container_id"],"database_pid":readback["pid"],"database_process_start_ticks":blkio["database_process_start_ticks"],"adopted_authority_sha256":blkio["adopted_authority_sha256"],"blkio_authority_sha256":blkio["authority_sha256"],"live_effective_cgroup_v1_throttle":blkio["live_effective_cgroup_v1_throttle"]},sort_keys=True))
if __name__=="__main__":main()
'''

def verify_base():
 mp=BASE/"bundle_manifest.json";m=json.loads(mp.read_text());u=dict(m)
 if sha(mp.read_bytes())!=BASE_SHA or u.pop("bundle_id",None)!=BASE_ID or sha(canonical(u))!=BASE_ID:raise RuntimeError("base identity differs")
 for rel,want in m["files"].items():
  if sha((BASE/rel).read_bytes())!=want:raise RuntimeError("base closure differs: "+rel)
 return m
def evidence(name,want):
 p=EVIDENCE/name;raw=p.read_bytes()
 if sha(raw)!=want or (EVIDENCE/(name+".sha256")).read_text()!=want+"  "+name+"\n":raise RuntimeError("recovery evidence differs: "+name)
 return raw
def patch_entry(raw):
 s=raw.decode()
 changes={
  'TPCC_RECREATED_ADOPTED_NAME = "adopted_service.recreated_swappiness0_v3.json"':'TPCC_RECREATED_ADOPTED_NAME = "recovery/v15-restart-zero-swap/adopted_service.restart_zero_swap_v1.json"',
  'TPCC_RECREATED_ADOPTED_SHA256 = "'+OLD_ADOPTED+'"':'TPCC_RECREATED_ADOPTED_SHA256 = "'+NEW_ADOPTED+'"',
  'TPCC_RECREATED_BLKIO_NAME = "blkio_throttle_authority.recreated_swappiness0_v3.json"':'TPCC_RECREATED_BLKIO_NAME = "recovery/v15-restart-zero-swap/blkio_throttle_authority.restart_zero_swap_v1.json"',
  'TPCC_RECREATED_BLKIO_SHA256 = "'+OLD_BLKIO+'"':'TPCC_RECREATED_BLKIO_SHA256 = "'+NEW_BLKIO+'"',
  'TPCC_RECREATED_DB_PID = 100769':'TPCC_RECREATED_DB_PID = 19638',
  'TPCC_RECREATED_DB_START = 268728753':'TPCC_RECREATED_DB_START = 269050791',
  'adopted.get("schema_version") != "mysql_tpcc_recreated_service_authority_v3"':'adopted.get("schema_version") != "mysql_tpcc_restart_zero_swap_adopted_service_v1"',
  'adopted.get("status") != "PASS_DURABLE_ZERO_SWAP_RECREATED_IDLE_READY"':'adopted.get("status") != "PASS_DURABLE_ZERO_SWAP_RESTARTED_IDLE_READY"',
  'value.get("schema_version") != "mysql_tpcc_recreated_blkio_authority_v3"':'value.get("schema_version") != "mysql_tpcc_restart_zero_swap_blkio_authority_v1"',
  'value.get("status") != "PASS_FRESH_RECREATED_CONTAINER_EPOCH"':'value.get("status") != "PASS_FRESH_RESTART_EPOCH"',
  'int(value.get("process_start_ticks") or 0) != start':'int(value.get("database_process_start_ticks") or 0) != start',
 }
 for old,new in changes.items():
  if s.count(old)!=1:raise RuntimeError("entry patch marker differs: "+old)
  s=s.replace(old,new)
 marker='            or int(client.get("total_swap_bytes", -1)) != 0):'
 new='            or int(client.get("total_swap_bytes", -1)) != 0\n            or adopted.get("same_container_id") is not True\n            or adopted.get("database_restarted") is not True\n            or adopted.get("container_recreated") is not False\n            or len(adopted.get("zero_swap_samples") or []) != 3\n            or any(int(row.get("total_swap_bytes", -1)) != 0 for row in adopted.get("zero_swap_samples") or [])):'
 if s.count(marker)!=1:raise RuntimeError("zero swap sample marker differs")
 return s.replace(marker,new).encode()
def main():
 m=verify_base();contents={}
 for rel in sorted(m["files"]):
  if rel==ENTRY or rel.startswith("tests/") or rel=="qualification/mysql_tpcc_recreated_authority_preflight_v13.py":continue
  contents[rel]=(BASE/rel).read_bytes()
 contents[ENTRY]=patch_entry((BASE/ENTRY).read_bytes());contents["qualification/mysql_tpcc_restart_epoch_preflight_v15.py"]=PREFLIGHT.encode();contents["tests/test_mysql_tpcc_inventory_restart_epoch_v15.py"]=TEST.encode();contents["builder/build_mysql_tpcc_inventory_restart_epoch_v15.py"]=Path(__file__).read_bytes()
 for name,want in (("preflight.json","df8dfa3f68571717d515bad09619f6a648d2883c4de037e84ce5a4bb29d5a07c"),("completion.json","45d753710e82a4f6220f0dc73a4757b0e275e75a50566b048f8a47c430d7bb59"),("adopted_service.restart_zero_swap_v1.json",NEW_ADOPTED),("blkio_throttle_authority.restart_zero_swap_v1.json",NEW_BLKIO)):
  contents["authority/recovery/"+name]=evidence(name,want)
 files={rel:sha(raw) for rel,raw in sorted(contents.items())};unsigned={"schema_version":"mysql_tpcc_inventory_restart_epoch_bundle_v15","status":"SEALED_NOT_PUBLISHED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_collector_bundle_id":BASE_ID,"predecessor_collector_manifest_sha256":BASE_SHA,"recovery_completion_sha256":"45d753710e82a4f6220f0dc73a4757b0e275e75a50566b048f8a47c430d7bb59","recovery_adopted_authority_sha256":NEW_ADOPTED,"recovery_blkio_authority_sha256":NEW_BLKIO,"database_container_id":"fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948","database_pid":19638,"database_process_start_ticks":269050791,"database_process_cgroup_sha256":"0ac085e5b6b435d7610695e5fcb2c5e3b97f2e720b845d38565d6cdfde3bae3a","zero_swap_sample_count":3,"read_bps":1205862400,"write_bps":1205862400,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"files":files}
 identity=sha(canonical(unsigned));manifest=dict(unsigned,bundle_id=identity);raw=canonical(manifest);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/identity
 if dst.exists():
  if (dst/"bundle_manifest.json").read_bytes()!=raw:raise RuntimeError("existing manifest differs")
  for rel,payload in contents.items():
   if (dst/rel).read_bytes()!=payload:raise RuntimeError("existing closure differs: "+rel)
 else:
  stage=Path(tempfile.mkdtemp(prefix=".tpcc-collector-v15-",dir=OUT))
  try:
   for rel,payload in contents.items():p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payload)
   (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 print(identity);print(sha(raw));print(dst)
if __name__=="__main__":main()
