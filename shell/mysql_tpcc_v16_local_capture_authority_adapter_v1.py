#!/usr/bin/env python3
"""Bind a fresh typed-capture command to TPC-C-only qualification evidence."""
from __future__ import annotations
import argparse,hashlib,json,os,re,sys
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Mapping

V16_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";V16_SHA="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
FLOW_ID="88332d998a2d23e835811833f93315be9c222e7f266763af89e66135a9442f92";FLOW_SHA="ab66070d4099fbe365b251b714259ee53574528880a8cf0285ad2f427f85f04e"
GATE_ID="a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723";GATE_SHA="a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0"
ATTEMPT="tpcc-v16-7e2e13aa-20260801t121500p08";HEX=re.compile(r"^[0-9a-f]{64}$")
class Error(RuntimeError):pass
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(p:Path)->str:
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def sealed(p:Path,label:str)->tuple[Path,dict[str,Any]]:
 p=Path(p).resolve(strict=True);s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text())
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def bundle(root:Path,bid:str,msha:str,label:str)->None:
 root=Path(root).resolve(strict=True);p,v=sealed(root/"bundle_manifest.json",label+" manifest")
 if root.name!=bid or v.get("bundle_id")!=bid or sha(p)!=msha or v.get("formal_scope")!="mysql_only" or v.get("cross_engine_comparison_prohibited") is not True or v.get("postgresql_mutation_prohibited") is not True:raise Error(label+" identity/scope differs")
def reject_qps(v:Any,where="authority"):
 if isinstance(v,Mapping):
  for k,x in v.items():
   if str(k).lower() in {"qps","throughput","transactions_per_second","tps"}:raise Error("QPS resource input prohibited: "+where)
   reject_qps(x,where+"."+str(k))
 elif isinstance(v,list):
  for i,x in enumerate(v):reject_qps(x,where+"["+str(i)+"]")
def adapt(a:argparse.Namespace)->dict[str,Any]:
 bundle(a.v16_bundle,V16_ID,V16_SHA,"v16");bundle(a.local_flow_bundle,FLOW_ID,FLOW_SHA,"local flow");bundle(a.a336_bundle,GATE_ID,GATE_SHA,"a336")
 cp,c=sealed(a.local_completion,"local completion");gp,g=sealed(a.local_gate,"local gate");ep,e=sealed(a.local_evidence,"local evidence");sp,s=sealed(a.fresh_snapshot,"fresh snapshot");mp,cmd=sealed(a.materialized_command,"materialized command")
 if (c.get("schema_version")!="mysql_tpcc_v16_local_parallel_completion_v1" or c.get("status")!="PASS_TPCC_LOCAL_QUALIFICATION_ZERO_CREDIT" or c.get("tpcc_workloads_started")!=1 or c.get("tpch_workloads_started")!=0 or c.get("tpch_mutation_performed") is not False or c.get("tpcc_evidence_sha256")!=sha(ep) or c.get("local_interference_gate_sha256")!=sha(gp) or c.get("accepted_rounds_credit")!=0):raise Error("local completion closure differs")
 if (g.get("schema_version")!="mysql_tpcc_v16_local_parallel_interference_gate_v1" or g.get("status")!="PASS_TPCC_ONLY_WITH_TPCH_READ_ONLY_STABLE" or g.get("capture_authorized") is not True or g.get("rolling_inventory_complete") is not True or g.get("foreign_writer_count")!=0 or g.get("tpch_mutation_performed") is not False or g.get("typed_transaction_l2_l3_required_before_registry") is not True or g.get("accepted_rounds_credit")!=0):raise Error("local interference gate differs")
 try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(str(s["collected_at_utc"]).replace("Z","+00:00"))).total_seconds()
 except (KeyError,ValueError) as x:raise Error("snapshot time differs") from x
 db=s.get("database") or {};client=s.get("client") or {};live=e.get("initial_live_readback") or {};live_db=live.get("container") or {}
 if (age < -5 or age > 120 or s.get("schema_version")!="mysql_tpcc_v16_same_live_readback_v1" or s.get("status")!="PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY" or s.get("bundle_id")!=V16_ID or s.get("attempt_id")!=ATTEMPT or not HEX.fullmatch(str(db.get("container_id") or "")) or not HEX.fullmatch(str(client.get("container_id") or "")) or live.get("bundle_id")!=V16_ID or live.get("scenario")!="tpcc_cpu" or live_db.get("container_id")!=db.get("container_id") or live_db.get("pid")!=db.get("pid")):raise Error("fresh same-live TPC-C continuity differs")
 unsigned=dict(cmd);old=unsigned.pop("command_spec_sha256",None)
 if (old!=hashlib.sha256(canonical(unsigned)).hexdigest() or cmd.get("schema_version")!="mysql_tpcc_materialized_phase_command_v1" or cmd.get("status")!="SEALED_CAPTURE_COMMAND" or cmd.get("phase")!="typed_capture" or cmd.get("bundle_id")!=V16_ID or cmd.get("attempt_id")!=ATTEMPT or cmd.get("capture_only") is not True or cmd.get("formal_authorized") is not False or cmd.get("accepted_round_authorized") is not False or cmd.get("qps_resource_input_prohibited") is not True or cmd.get("db_identity")!={k:db.get(k) for k in ("container_id","pid","process_start_ticks","datadir","device_major_minor","cpuset_cpus","cpuset_mems","mysql_port")} or any((cmd.get("client_container") or {}).get(k)!=client.get(k) for k in ("container_id","pid","process_start_ticks"))):raise Error("typed command/snapshot differs")
 for label,value in (("completion",c),("gate",g),("snapshot",s),("command",cmd)):reject_qps(value,label)
 out=dict(cmd);out.pop("command_spec_sha256",None);out["capture_authority_binding"]={"schema_version":"mysql_tpcc_v16_local_capture_authority_binding_v1","v16_manifest_sha256":V16_SHA,"local_flow_bundle_id":FLOW_ID,"local_flow_manifest_sha256":FLOW_SHA,"a336_bundle_id":GATE_ID,"a336_manifest_sha256":GATE_SHA,"local_completion_sha256":sha(cp),"local_gate_sha256":sha(gp),"local_evidence_sha256":sha(ep),"fresh_snapshot_sha256":sha(sp),"source_command_spec_sha256":old,"tpcc_workloads_started":1,"tpch_workloads_started":0,"tpch_mutation_performed":False,"rolling_interference_gate_passed":True,"typed_transaction_l2_l3_required_before_registry":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"capture_authorized":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};reject_qps(out);out["command_spec_sha256"]=hashlib.sha256(canonical(out)).hexdigest();return out
def main()->int:
 p=argparse.ArgumentParser()
 for n in ("v16_bundle","local_flow_bundle","a336_bundle","local_completion","local_gate","local_evidence","fresh_snapshot","materialized_command","output"):p.add_argument("--"+n.replace("_","-"),type=Path,required=True)
 a=p.parse_args();out=adapt(a)
 if a.output.exists():raise Error("refusing overwrite")
 a.output.parent.mkdir(parents=True,exist_ok=True);raw=canonical(out);a.output.write_bytes(raw);os.chmod(a.output,0o400);side=Path(str(a.output)+".sha256");side.write_text(hashlib.sha256(raw).hexdigest()+"  "+a.output.name+"\n");os.chmod(side,0o400);print(json.dumps({"status":"PASS_TPCC_LOCAL_CAPTURE_COMMAND_BOUND_ZERO_CREDIT","output":str(a.output),"accepted_rounds_credit":0},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
