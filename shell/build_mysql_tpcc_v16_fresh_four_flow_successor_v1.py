#!/usr/bin/env python3
"""Publish immutable producer/controller for current TPC-C v16 four-flow."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,stat,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();CONTROL=ROOT/"shell/mysql_tpcc_v16_fresh_four_flow_controller_v1.py";TEST=ROOT/"shell/test_mysql_tpcc_v16_fresh_four_flow_controller_v1.py";TEMPLATE=ROOT/"templates/mysql_tpcc_v16_fresh_four_flow_draft_v1.json"
A336_ID="a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723";A336=ROOT/"artifacts/mysql_tpcc_v16_same_live_gate_successor_v1"/A336_ID
V16_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";V16=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/V16_ID
OUT=ROOT/"artifacts/mysql_tpcc_v16_fresh_four_flow_successor_v1";PY=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def check(bundle,bid,want_sha):
 p=bundle/"bundle_manifest.json";m=json.loads(p.read_text());u=dict(m);u.pop("bundle_id",None)
 if bundle.name!=bid or m.get("bundle_id")!=bid or sha(p)!=want_sha or hs(canonical(u))!=bid or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error("predecessor differs: "+bid)
 return p
def relfiles(root):return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def main():
 a=check(A336,A336_ID,"a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0");v=check(V16,V16_ID,"e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba")
 py_manifest=V16/"toolchain/python-3.11.11-tree-manifest.json"
 payload={"controller/mysql_tpcc_v16_fresh_four_flow_controller_v1.py":CONTROL.read_bytes(),"tests/test_mysql_tpcc_v16_fresh_four_flow_controller_v1.py":TEST.read_bytes(),"templates/mysql_tpcc_v16_fresh_four_flow_draft_v1.json":TEMPLATE.read_bytes(),"authority/a336_bundle_manifest.json":a.read_bytes(),"authority/v16_bundle_manifest.json":v.read_bytes(),"authority/python_3a807_tree_manifest.json":py_manifest.read_bytes(),"authority/build_mysql_tpcc_v16_fresh_four_flow_successor_v1.py":SELF.read_bytes()}
 manifest={"schema_version":"mysql_tpcc_v16_fresh_four_flow_producer_control_bundle_v1","status":"SEALED_PRODUCER_CONTROL_NOT_EXECUTED_NOT_FORMAL_AUTHORITY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_attempt_id":"tpcc-v16-7e2e13aa-20260801t121500p08","a336_bundle_id":A336_ID,"a336_manifest_sha256":sha(a),"v16_bundle_id":V16_ID,"v16_manifest_sha256":sha(v),"python_tree_content_sha256":"3a8073397536ab806e125061395c291b20edd40c1da015eb4edb87bc3c403c89","python_binary_sha256":"05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39","required_outputs":["A_solo","A_paired","B_solo","B_paired","dynamic_gate","dynamic_gate_raw"],"execution_order":["fresh_pre_snapshot","A_solo_serial","B_solo_serial","A_and_B_readbacks_parallel","A_paired_and_B_paired_parallel","compare_bidirectional","fresh_post_snapshot"],"duration_seconds_per_flow":180,"same_collector_required_for_all_four_flows":True,"current_full_container_ids_from_a336_snapshot_required":True,"static_pid_authority_prohibited":True,"old_v13_authority_prohibited":True,"fresh_exclusive_output_root_required":True,"main_agent_short_lived_execution_authority_required":True,"database_counter_missing_or_timeout_behavior":"FAIL_CLOSED","qps_resource_input_prohibited":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":{k:hs(v) for k,v in sorted(payload.items())}}
 bid=hs(canonical(manifest));manifest["bundle_id"]=bid;OUT.mkdir(parents=True,exist_ok=True);target=OUT/bid
 if not target.exists():
  stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for name,data in payload.items():p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);os.chmod(p,0o444)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n")
   r=subprocess.run([str(PY),"-m","pytest","-q",str(TEST)],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
   if r.returncode:raise Error("tests failed:\n"+r.stdout)
   for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o444 if p.is_file() else 0o555)
   os.chmod(stage,0o555);os.rename(stage,target)
  finally:
   if stage.exists():shutil.rmtree(stage)
 m=json.loads((target/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m!=manifest or hs(canonical(u))!=bid or relfiles(target)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [target,*target.rglob("*")]):raise Error("published identity/closure/mode differs")
 print(json.dumps({"status":"PASS_IMMUTABLE_TPCC_V16_FRESH_FOUR_FLOW_PRODUCER","bundle":str(target),"bundle_id":bid,"manifest_sha256":sha(target/"bundle_manifest.json"),"files":len(manifest["files"]),"tests":"5 passed","accepted_rounds_credit":0},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (Error,OSError,ValueError,subprocess.SubprocessError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
