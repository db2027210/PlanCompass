#!/usr/bin/env python3
"""Build the TPC-C v8 controller bound to the post-recommission epoch."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
PARENT_ID="a8c105e854302f82b8752a3f076bbfa8a8538f8f885d9487ee1bed8161e81dfa";PARENT_SHA="b9c2db44cf60f172a51be05923b720c1e9c3c4495a5bc5db5f2db562f1e5bf16";PARENT=ROOT/"artifacts/mysql_tpcc_post_recommission_controller_v1"/PARENT_ID
SEALER_ID="a75e23ef75351a03266b38790ec2c6d745ea40e4eab3742dd281b0a55c543c7b";SEALER_SHA="bfdc060610122778896f8debcdbe031395f02e38b8ba012a0b5484a40e782d65";SEALER=ROOT/"artifacts/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1"/SEALER_ID
OUT=ROOT/"artifacts/mysql_tpcc_post_recommission_controller_v8";CTL="controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py";LIVE="control/mysql_tpcc_v16_fresh_blkio_live_sealer_v2.py"
ADOPTED_SHA="8f73143b1ded2d344e7b6a577d0c438ab8705eec64a76605f214b99b93d8246e";FRESH_SHA="0c4af692177b9857ecb95b219fcbb62d0e5db5b4ce3451fc8b2fbac798508b01";REAPPLY_SHA="10192d1effad48dc9d4b06b257f9a33669a291d054273bdbf84d410a9610f1c3"
def can(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hs(Path(p).read_bytes())
def verify(root,identity,manifest_sha):
 raw=(root/"bundle_manifest.json").read_bytes();m=json.loads(raw);u=dict(m);bid=u.pop("bundle_id",None)
 if sha(root/"bundle_manifest.json")!=manifest_sha or bid!=identity or hs(can(u))!=identity:raise RuntimeError("bundle identity differs: "+identity)
 for r,w in m["files"].items():
  if sha(root/r)!=w:raise RuntimeError("bundle closure differs: "+r)
 return m
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)

EPOCH_HELPER=r'''def validate_recommission_epoch(adopted_path:Path,adopted:dict[str,Any],authority:Path)->dict[str,Any]:
 if sha_bytes(regular(adopted_path,"adopted service").read_bytes())!=ADOPTED_SHA:raise Error("adopted service exact post-recommission SHA differs")
 db=adopted.get("database") or {};client=adopted.get("client") or {};epoch=adopted.get("pre_authority_zero_swap_recommission") or {};attempt_root=Path(str(db.get("datadir") or "")).parent.parent
 fresh_expected=attempt_root/"blkio_throttle_authority.zero_swap_restart_v1.json";reapply_expected=attempt_root/"blkio_throttle_reapply.zero_swap_restart_v1.json"
 if regular(authority,"fresh blkio authority")!=fresh_expected.resolve(strict=True) or Path(str(epoch.get("fresh_blkio_authority_path") or ""))!=fresh_expected or epoch.get("fresh_blkio_authority_sha256")!=AUTH_SHA or Path(str(epoch.get("blkio_reapply_receipt_path") or ""))!=reapply_expected or epoch.get("blkio_reapply_receipt_sha256")!=REAPPLY_SHA or epoch.get("legacy_blkio_authority_overwritten") is not False or epoch.get("postgresql_mutation_performed") is not False:raise Error("adopted recommission epoch binding differs")
 reapply_path,reapply=sealed(reapply_expected,"blkio reapply receipt")
 if sha_bytes(reapply_path.read_bytes())!=REAPPLY_SHA or reapply.get("schema_version")!="mysql_tpcc_blkio_v1_throttle_reapply_receipt_v2" or reapply.get("status")!="PASS" or reapply.get("bundle_id")!=V16_ID or reapply.get("attempt_id")!=ATTEMPT or reapply.get("database_container_id")!=db.get("container_id") or int(reapply.get("database_pid") or 0)!=int(db.get("pid") or 0) or reapply.get("client_container_id")!=client.get("container_id") or int(reapply.get("client_pid") or 0)!=int(client.get("pid") or 0) or reapply.get("device_major_minor")!=MM or int(reapply.get("read_bps") or 0)!=BPS or int(reapply.get("write_bps") or 0)!=BPS or reapply.get("non_target_device_rows_preserved") is not True or reapply.get("postgresql_mutation_performed") is not False or reapply.get("postgresql_cgroup_written") is not False or reapply.get("workload_started") is not False or reapply.get("accepted_rounds_credit")!=0 or ((reapply.get("client_idle_evidence") or {}).get("workload_absent")) is not True:raise Error("sealed blkio reapply receipt differs")
 return {"adopted_service_sha256":ADOPTED_SHA,"fresh_blkio_authority_sha256":AUTH_SHA,"reapply_receipt_sha256":REAPPLY_SHA,"fresh_blkio_path":str(fresh_expected),"reapply_receipt_path":str(reapply_expected),"database_container_id":db.get("container_id"),"database_pid":db.get("pid"),"client_container_id":client.get("container_id"),"client_pid":client.get("pid"),"postgresql_mutation_performed":False}
'''

TEST=r'''import hashlib,importlib.util,json
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];C=B/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py";S=B/"control/mysql_tpcc_v16_fresh_blkio_live_sealer_v2.py"
def test_content_address_scope_and_exact_epoch_hashes():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["postgresql_mutation_prohibited"] is True and m["accepted_rounds_credit"]==0
 assert m["adopted_service_sha256"]=="8f73143b1ded2d344e7b6a577d0c438ab8705eec64a76605f214b99b93d8246e" and m["fresh_blkio_authority_sha256"]=="0c4af692177b9857ecb95b219fcbb62d0e5db5b4ce3451fc8b2fbac798508b01" and m["blkio_reapply_receipt_sha256"]=="10192d1effad48dc9d4b06b257f9a33669a291d054273bdbf84d410a9610f1c3"
def test_controller_uses_fresh_epoch_and_in_bundle_live_sealer():
 s=C.read_text();assert 'ADOPTED=CUR/"adopted_service.zero_swap_restart_v1.json"' in s and 'FRESH_BLKIO=CUR/"blkio_throttle_authority.zero_swap_restart_v1.json"' in s and 'REAPPLY_RECEIPT=CUR/"blkio_throttle_reapply.zero_swap_restart_v1.json"' in s
 assert 'str(root/"control/mysql_tpcc_v16_fresh_blkio_live_sealer_v2.py")' in s and '--authority",str(FRESH_BLKIO)' in s and 'resolve(*BLKIO' not in s and 'str(CUR/"blkio_throttle_authority.json")' not in s
 assert 'sha256(adopted_path)!=ADOPTED_SERVICE_SHA' in s and 'bv.get("authority_sha256")!=BLKIO_AUTH_SHA' in s and 'bv.get("reapply_receipt_sha256")!=REAPPLY_RECEIPT_SHA' in s
def test_live_sealer_binds_fresh_path_sidecars_epoch_and_live_readback():
 s=S.read_text();assert '"blkio_throttle_authority.zero_swap_restart_v1.json"' in s and 'validate_recommission_epoch' in s and 'sealed(reapply_expected,"blkio reapply receipt")' in s and 'rate(read)==BPS and rate(write)==BPS' in s
 assert 'sha_bytes(regular(adopted_path,"adopted service").read_bytes())!=ADOPTED_SHA' in s and 'epoch.get("fresh_blkio_authority_sha256")!=AUTH_SHA' in s and 'epoch.get("blkio_reapply_receipt_sha256")!=REAPPLY_SHA' in s
def test_typed_five_transaction_l2_l3_qps_dynamic_pg_gates_preserved():
 s=C.read_text();m=json.loads((B/"bundle_manifest.json").read_text());assert m["typed_binding_count"]==34 and m["transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"] and m["sensitive_stock_level_normalized"] is True and m["typed_l2_l3_retained"] is True and m["qps_resource_input_prohibited"] is True
 for required in ('mysql_only_pg_artifact_http_dynamic_noninterference_v1','postgresql_mutation_performed") is not False','inv.get("conflicts")!=[]','inv.get("foreign_writer_count")!=0','zero_swap_readback(','accepted_rounds_credit":0'):
  assert required in s
def test_only_authority_binding_changed_from_parent():
 parent=B.parents[1]/"mysql_tpcc_post_recommission_controller_v1"/"a8c105e854302f82b8752a3f076bbfa8a8538f8f885d9487ee1bed8161e81dfa"/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py";old=parent.read_text();new=C.read_text()
 for invariant in ('def validate_evidence(v):','def zero_swap_readback(','def host_inventory(','def snapshot_bound_client_identity(','_FORBIDDEN_METRICS'):
  assert old.count(invariant)==new.count(invariant)
'''

def main():
 pm=verify(PARENT,PARENT_ID,PARENT_SHA);verify(SEALER,SEALER_ID,SEALER_SHA);contents={}
 for r,w in sorted(pm["files"].items()):
  if not r.startswith("tests/") and not r.startswith("builder/"):contents[r]=(PARENT/r).read_bytes()
 controller=contents[CTL].decode()
 patches=(
 ('ADOPTED=CUR/"adopted_service.zero_swap_restart_v1.json";LEDGER=CUR/"phase_ledger_v2.json";TPCC_SOCKET="unix:///run/ra-docker-mysql-tpcc-v1.sock"','ADOPTED=CUR/"adopted_service.zero_swap_restart_v1.json";FRESH_BLKIO=CUR/"blkio_throttle_authority.zero_swap_restart_v1.json";REAPPLY_RECEIPT=CUR/"blkio_throttle_reapply.zero_swap_restart_v1.json";LEDGER=CUR/"phase_ledger_v2.json";TPCC_SOCKET="unix:///run/ra-docker-mysql-tpcc-v1.sock"'),
 ('BLKIO=("a75e23ef75351a03266b38790ec2c6d745ea40e4eab3742dd281b0a55c543c7b","bfdc060610122778896f8debcdbe031395f02e38b8ba012a0b5484a40e782d65")\nBLKIO_AUTH_SHA="6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d"','ADOPTED_SERVICE_SHA="'+ADOPTED_SHA+'"\nBLKIO_AUTH_SHA="'+FRESH_SHA+'";REAPPLY_RECEIPT_SHA="'+REAPPLY_SHA+'"'),
 ('a336,_,_=resolve(*A336,"a336");blkio,_,_=resolve(*BLKIO,"blkio sealer");tpcc,tpcc_mp,tpcc_m=resolve','a336,_,_=resolve(*A336,"a336");tpcc,tpcc_mp,tpcc_m=resolve'),
 ('adopted_path,adopted=load_sealed(ADOPTED,"adopted service");ledger_path,ledger=load_sealed(LEDGER,"phase ledger")','adopted_path,adopted=load_sealed(ADOPTED,"adopted service");ledger_path,ledger=load_sealed(LEDGER,"phase ledger")\n if sha256(adopted_path)!=ADOPTED_SERVICE_SHA:raise MaterializationError("adopted service exact post-recommission SHA differs")'),
 ('blkio_receipt=control/"blkio.receipt.json";br=subprocess.run([str(PINNED),"-B",str(blkio/"control/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py"),"--v16-bundle",str(tpcc),"--adopted-service",str(adopted_path),"--authority",str(CUR/"blkio_throttle_authority.json"),"--output",str(blkio_receipt)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=120)','blkio_receipt=control/"blkio.receipt.json";br=subprocess.run([str(PINNED),"-B",str(root/"control/mysql_tpcc_v16_fresh_blkio_live_sealer_v2.py"),"--v16-bundle",str(tpcc),"--adopted-service",str(adopted_path),"--authority",str(FRESH_BLKIO),"--output",str(blkio_receipt)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=120)'),
 ('if bv.get("status")!="PASS_VALIDATED_LIVE_BLKIO_AUTHORITY_SIDECAR" or bv.get("authority_sha256")!=BLKIO_AUTH_SHA:raise MaterializationError("blkio authority differs")','if bv.get("status")!="PASS_VALIDATED_LIVE_BLKIO_AUTHORITY_SIDECAR" or bv.get("authority_sha256")!=BLKIO_AUTH_SHA or bv.get("adopted_service_sha256")!=ADOPTED_SERVICE_SHA or bv.get("reapply_receipt_sha256")!=REAPPLY_RECEIPT_SHA:raise MaterializationError("post-recommission blkio authority differs")'))
 for old,new in patches:
  if controller.count(old)!=1:raise RuntimeError("controller patch anchor differs: "+old[:80])
  controller=controller.replace(old,new)
 contents[CTL]=controller.encode()
 sealer=(SEALER/"control/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py").read_text()
 spatches=((f'AUTH_SHA="6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d"',f'AUTH_SHA="{FRESH_SHA}";ADOPTED_SHA="{ADOPTED_SHA}";REAPPLY_SHA="{REAPPLY_SHA}"'),('parent.parent/"blkio_throttle_authority.json"','parent.parent/"blkio_throttle_authority.zero_swap_restart_v1.json"'),('def write_sidecar(authority:Path,raw:bytes)->tuple[Path,bool]:',EPOCH_HELPER+'def write_sidecar(authority:Path,raw:bytes)->tuple[Path,bool]:'),('p=argparse.ArgumentParser();p.add_argument("--v16-bundle",type=Path,required=True);p.add_argument("--adopted-service",type=Path,required=True);p.add_argument("--authority",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();bundle(a.v16_bundle);_,adopted=sealed(a.adopted_service,"adopted service")','p=argparse.ArgumentParser();p.add_argument("--v16-bundle",type=Path,required=True);p.add_argument("--adopted-service",type=Path,required=True);p.add_argument("--authority",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();bundle(a.v16_bundle);adopted_path,adopted=sealed(a.adopted_service,"adopted service")'),('raw,live=validate_live(a.authority,adopted);side,created=write_sidecar(a.authority,raw);receipt={','epoch=validate_recommission_epoch(adopted_path,adopted,a.authority);raw,live=validate_live(a.authority,adopted);side,created=write_sidecar(a.authority,raw);receipt={**epoch,'))
 for old,new in spatches:
  if sealer.count(old)!=1:raise RuntimeError("sealer patch anchor differs: "+old[:80])
  sealer=sealer.replace(old,new)
 contents[LIVE]=sealer.encode();contents["tests/test_mysql_tpcc_post_recommission_controller_v8.py"]=TEST.encode();contents["builder/build_mysql_tpcc_post_recommission_controller_v8.py"]=HERE.read_bytes();files={r:hs(v) for r,v in sorted(contents.items())}
 unsigned={"schema_version":"mysql_tpcc_post_recommission_controller_bundle_v8","status":"SEALED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"adopted_service_relative":"adopted_service.zero_swap_restart_v1.json","adopted_service_sha256":ADOPTED_SHA,"fresh_blkio_authority_relative":"blkio_throttle_authority.zero_swap_restart_v1.json","fresh_blkio_authority_sha256":FRESH_SHA,"fresh_blkio_sidecar_exact_content_required":True,"blkio_reapply_receipt_relative":"blkio_throttle_reapply.zero_swap_restart_v1.json","blkio_reapply_receipt_sha256":REAPPLY_SHA,"live_blkio_readback_required":True,"same_post_recommission_database_pid_required":True,"typed_binding_count":34,"transaction_types":["Delivery","NewOrder","OrderStatus","Payment","StockLevel"],"sensitive_stock_level_normalized":True,"typed_l2_l3_retained":True,"qps_resource_input_prohibited":True,"dynamic_noninterference_gate_preserved":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"files":files};bid=hs(can(unsigned));manifest=dict(unsigned,bundle_id=bid);raw=can(manifest);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid
 if dest.exists():
  if (dest/"bundle_manifest.json").read_bytes()!=raw:raise RuntimeError("existing manifest differs")
  for r,v in contents.items():
   if (dest/r).read_bytes()!=v:raise RuntimeError("existing closure differs: "+r)
 else:
  stage=Path(tempfile.mkdtemp(prefix=".tpcc-v8-",dir=OUT))
  try:
   for r,v in contents.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
   (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 print(bid);print(sha(dest/"bundle_manifest.json"));print(dest)
if __name__=="__main__":main()
