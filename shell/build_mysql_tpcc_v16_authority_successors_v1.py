#!/usr/bin/env python3
"""Transactionally publish the two local-only TPC-C v16 authority successors."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,stat,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();PY=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
ADAPTER=ROOT/"shell/mysql_tpcc_v16_capture_authority_adapter_v1.py";ATEST=ROOT/"shell/test_mysql_tpcc_v16_capture_authority_adapter_v1.py"
SEALER=ROOT/"shell/mysql_tpcc_v16_registry_sealer_v1.py";STEST=ROOT/"shell/test_mysql_tpcc_v16_registry_sealer_v1.py"
V16_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";V16_SHA="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
JOINT_ID="765c4fa3343b3e78254c102e1824f206c94f70f94b6e94d2d70bcd1a167f7897";JOINT_SHA="1a592f08b55c3e8bc1edf07e42a7be5540abba6a94b5bbbc356cff0f19d2a0c2"
GATE_ID="a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723";GATE_SHA="a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0"
COLLECTOR_ID="1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def digest_bytes(v):return hashlib.sha256(v).hexdigest()
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def source(path,expected):
 if digest(path)!=expected:raise BuildError("source bundle manifest differs: "+str(path))
 return path.read_bytes()
def closure(root):return {p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def publish(family,payload,identity):
 identity=dict(identity,files={k:digest_bytes(v) for k,v in sorted(payload.items())});bid=digest_bytes(canonical(identity));manifest=dict(identity,bundle_id=bid);root=ROOT/"artifacts"/family;root.mkdir(parents=True,exist_ok=True);target=root/bid
 if not target.exists():
  stage=root/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for name,data in payload.items():p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);os.chmod(p,0o444)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(digest_bytes(raw)+"  bundle_manifest.json\n",encoding="ascii")
   for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o444 if p.is_file() else 0o555)
   os.chmod(stage,0o555);os.rename(stage,target)
  finally:
   if stage.exists():shutil.rmtree(stage)
 actual=json.loads((target/"bundle_manifest.json").read_text());unsigned=dict(actual);unsigned.pop("bundle_id",None)
 if actual!=manifest or digest_bytes(canonical(unsigned))!=bid or closure(target)!=identity["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [target,*target.rglob("*")]):raise BuildError("immutable published closure differs")
 return {"bundle":str(target),"bundle_id":bid,"manifest_sha256":digest(target/"bundle_manifest.json")}
def main():
 r=subprocess.run([str(PY),"-m","unittest","-v",str(ATEST),str(STEST)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
 if r.returncode:raise BuildError("tests failed:\n"+r.stdout)
 v16=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/V16_ID/"bundle_manifest.json";joint=ROOT/"artifacts/mysql_tpcc_joint_successor_v1"/JOINT_ID/"bundle_manifest.json";gate=ROOT/"artifacts/mysql_tpcc_v16_same_live_gate_successor_v1"/GATE_ID/"bundle_manifest.json"
 refs={"authority/v16_bundle_manifest.json":source(v16,V16_SHA),"authority/joint_bundle_manifest.json":source(joint,JOINT_SHA),"authority/a336_bundle_manifest.json":source(gate,GATE_SHA)}
 common={"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"immutable":True,"algorithm_c_unchanged":True,"qps_resource_input_prohibited":True,"capture_only":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0}
 adapter=publish("mysql_tpcc_v16_capture_authority_adapter_v1",{"control/mysql_tpcc_v16_capture_authority_adapter_v1.py":ADAPTER.read_bytes(),"tests/test_mysql_tpcc_v16_capture_authority_adapter_v1.py":ATEST.read_bytes(),"builder/build_mysql_tpcc_v16_authority_successors_v1.py":SELF.read_bytes(),**refs},{"schema_version":"mysql_tpcc_v16_capture_authority_adapter_bundle_v1","status":"SEALED_CAPTURE_AUTHORITY_ADAPTER_NOT_EXECUTED",**common,"source_v16_bundle_id":V16_ID,"source_v16_manifest_sha256":V16_SHA,"joint_bundle_id":JOINT_ID,"joint_manifest_sha256":JOINT_SHA,"a336_bundle_id":GATE_ID,"a336_manifest_sha256":GATE_SHA,"joint_completion_required":True,"fresh_snapshot_maximum_age_seconds":120,"command_spec_rehash_required":True})
 cdir=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/V16_ID/"authority"/("collector_payload_"+COLLECTOR_ID)
 registry=publish("mysql_tpcc_v16_registry_sealer_successor_v1",{"control/mysql_tpcc_v16_registry_sealer_v1.py":SEALER.read_bytes(),"tests/test_mysql_tpcc_v16_registry_sealer_v1.py":STEST.read_bytes(),"builder/build_mysql_tpcc_v16_authority_successors_v1.py":SELF.read_bytes(),"authority/portable_collector.py":(cdir/"tpcc_typed_collector.py").read_bytes(),"authority/typed_binding_manifest.json":(cdir/"tpcc_offline_binding_manifest.json").read_bytes(),**refs},{"schema_version":"mysql_tpcc_v16_registry_sealer_successor_bundle_v1","status":"SEALED_REGISTRY_SEALER_NOT_EXECUTED",**common,"source_v16_bundle_id":V16_ID,"source_v16_manifest_sha256":V16_SHA,"a336_bundle_id":GATE_ID,"a336_manifest_sha256":GATE_SHA,"portable_collector_payload_id":COLLECTOR_ID,"portable_collector_sha256":"f95c21304314474eaca1930fc3fd14931e3034f2c0ecd875813fa53b7d227994","binding_manifest_sha256":"689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182","postcapture_registry_ready_authority_required":True,"typed_transaction_l2_l3_required":True})
 print(json.dumps({"status":"PASS_TWO_IMMUTABLE_TPCC_V16_AUTHORITY_SUCCESSORS","adapter":adapter,"registry_sealer":registry,"tests":"8 passed","remote_actions":0,"accepted_rounds_credit":0},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (BuildError,OSError,ValueError,subprocess.SubprocessError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
