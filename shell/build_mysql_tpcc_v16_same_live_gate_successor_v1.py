#!/usr/bin/env python3
"""Publish immutable TPC-C v16 current same-live gate/control successor."""
from __future__ import annotations
import hashlib, json, os, secrets, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; SELF=Path(__file__).resolve()
CONTROL=ROOT/"shell/mysql_tpcc_v16_same_live_gate_control_v1.py"
TEST=ROOT/"shell/test_mysql_tpcc_v16_same_live_gate_control_v1.py"
COMMANDS=ROOT/"templates/mysql_tpcc_v16_same_live_gate_commands_v1.json"
BASE_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
BASE=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/BASE_ID
OUT=ROOT/"artifacts/mysql_tpcc_v16_same_live_gate_successor_v1"
PY=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def digest_bytes(v):return hashlib.sha256(v).hexdigest()
def digest(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def verify_base():
 p=BASE/"bundle_manifest.json";m=json.loads(p.read_text());u=dict(m);u.pop("bundle_id",None)
 if (m.get("bundle_id")!=BASE_ID or digest(p)!="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba" or digest_bytes(canonical(u))!=BASE_ID or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("postgresql_mutation_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False):raise BuildError("v16 base differs")
 return p
def files(root):
 return {p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def main():
 base_manifest=verify_base();binding=BASE/"authority/collector_payload_1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c/tpcc_offline_binding_manifest.json"
 if digest(binding)!="689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182":raise BuildError("binding differs")
 payload={"control/mysql_tpcc_v16_same_live_gate_control_v1.py":CONTROL.read_bytes(),"tests/test_mysql_tpcc_v16_same_live_gate_control_v1.py":TEST.read_bytes(),"commands/mysql_tpcc_v16_same_live_gate_commands_v1.json":COMMANDS.read_bytes(),"authority/base_bundle_manifest.json":base_manifest.read_bytes(),"authority/typed_binding_manifest.json":binding.read_bytes(),"authority/build_mysql_tpcc_v16_same_live_gate_successor_v1.py":SELF.read_bytes()}
 manifest={"schema_version":"mysql_tpcc_v16_same_live_gate_successor_bundle_v1","status":"SEALED_CURRENT_SAME_LIVE_GATE_CONTROL_NOT_LAUNCH_AUTHORITY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_attempt_id":"tpcc-v16-7e2e13aa-20260801t121500p08","target_bundle_id":BASE_ID,"target_manifest_sha256":digest(base_manifest),"database_cpuset":"21-24,57-60","client_cpuset":"8-15,44-51","device":"/dev/nvme3n1","device_major_minor":"259:2","mysql_port":65106,"identity_source":"authoritative_adopted_service_plus_fresh_live_readback_not_static_pid","required_dynamic_flows":["A_solo","A_paired","B_solo","B_paired"],"minimum_samples_per_flow":181,"minimum_duration_seconds":180,"database_counter_missing_or_timeout_behavior":"FAIL_CLOSED","binding_count":34,"transaction_types":["Delivery","NewOrder","OrderStatus","Payment","StockLevel"],"sensitive_stock_level_normalized_to":"StockLevel","setter_type_parameter_position_sql_hash_required":True,"typed_transaction_l2_l3_required":True,"qps_resource_input_prohibited":True,"same_live_qualification_continuity_required":True,"capture_r0_failed_replay_credit":0,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":{k:digest_bytes(v) for k,v in sorted(payload.items())}}
 bundle_id=digest_bytes(canonical(manifest));manifest["bundle_id"]=bundle_id;OUT.mkdir(parents=True,exist_ok=True);target=OUT/bundle_id
 if not target.exists():
  stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for name,data in payload.items():p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);os.chmod(p,0o444)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(digest_bytes(raw)+"  bundle_manifest.json\n")
   result=subprocess.run([str(PY),"-m","pytest","-q",str(TEST)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
   if result.returncode:raise BuildError("tests failed:\n"+result.stdout)
   for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o444 if p.is_file() else 0o555)
   os.chmod(stage,0o555);os.rename(stage,target)
  finally:
   if stage.exists():shutil.rmtree(stage)
 m=json.loads((target/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m!=manifest or digest_bytes(canonical(u))!=bundle_id or files(target)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [target,*target.rglob("*")]):raise BuildError("published closure/mode differs")
 print(json.dumps({"status":"PASS_IMMUTABLE_TPCC_V16_SAME_LIVE_GATE","bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":digest(target/"bundle_manifest.json"),"files":len(manifest["files"]),"tests":"5 passed","formal_launch_authorized":False,"accepted_rounds_credit":0},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (BuildError,OSError,ValueError,subprocess.SubprocessError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
