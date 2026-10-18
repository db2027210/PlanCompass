#!/usr/bin/env python3
"""Issue a short-lived, non-executing plan for TPC-C v13 dynamic four-flow evidence.

The material never starts a workload and never substitutes qualification data
for a trace.  Its only effect is to bind the required four raw evidence files
to the same-live dual-projection authority before a later gate evaluation.
"""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, shutil, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
class Error(RuntimeError):pass
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def obj(p,what):
 p=Path(p).resolve(strict=True)
 if p.is_symlink() or not p.is_file():raise Error(what+" must be regular")
 try:v=json.loads(p.read_text())
 except (UnicodeError,json.JSONDecodeError) as e:raise Error(what+" JSON differs") from e
 if not isinstance(v,dict):raise Error(what+" object required")
 return p,v
def main():
 a=argparse.ArgumentParser(description=__doc__);a.add_argument("--dual-projection-authority",type=Path,required=True);a.add_argument("--output-root",type=Path,required=True);a.add_argument("--ttl-seconds",type=int,default=900);x=a.parse_args()
 if not 60<=x.ttl_seconds<=1800:raise Error("TTL must be 60..1800 seconds")
 p,authority=obj(x.dual_projection_authority,"dual projection authority")
 if authority.get("schema_version")!="mysql_tpcc_v13_dual_projection_translation_authority_v1" or authority.get("status")!="PASS_SAME_INSPECT_DUAL_PROJECTION" or authority.get("accepted_round_authorized") is not False:raise Error("dual projection authority differs")
 now=datetime.now(timezone.utc);expires=now+timedelta(seconds=x.ttl_seconds)
 required={label:{"required":True,"freshness":"captured_after_material_issued_before_expiry","same_live_attempt_bound":True,"minimum_samples":181,"minimum_duration_seconds":180,"maximum_sample_gap_seconds":1.5,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"foreign_device_io_attribution_bytes":0} for label in ("A_solo","A_paired","B_solo","B_paired")}
 material={"schema_version":"mysql_tpcc_v13_dynamic_four_flow_materials_v1","status":"ISSUED_NOT_EXECUTED_NOT_GATE","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"issued_at_utc":now.isoformat().replace("+00:00","Z"),"expires_at_utc":expires.isoformat().replace("+00:00","Z"),"ttl_seconds":x.ttl_seconds,"dual_projection_authority_sha256":sha(p),"attempt_id":authority["attempt_id"],"same_live_identity":authority["same_live_identity"],"required_raw_inputs":required,"execution_sequence":["revalidate_same_live_dual_projection_authority","collect_A_solo_qualification_only","collect_A_paired_qualification_only","collect_B_solo_qualification_only","collect_B_paired_qualification_only","write_raw_closure_with_four_file_sha256","evaluate_dynamic_gate_from_closure_fail_closed"],"prohibitions":["no_typed_trace_replay","no_capture_result_synthesis","no_QPS_resource_input","no_accepted_round_credit","no_formal_launch_from_materials_alone"],"missing_evidence_behavior":"FAIL_CLOSED; do not create dynamic gate or registry seal if any of four raw streams is absent, stale, nonzero-timeout, or fails identity binding","formal_launch_authorized":False,"accepted_round_authorized":False}
 ident=hashlib.sha256(canon(material)).hexdigest();material["materials_id"]=ident;root=x.output_root.resolve();root.mkdir(parents=True,exist_ok=True);target=root/ident
 if target.exists():raise Error("materials ID collision")
 stage=root/(".stage-"+ident+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  out=stage/"dynamic_four_flow_materials.json";out.write_bytes(canon(material));os.chmod(out,0o400);(stage/"dynamic_four_flow_materials.json.sha256").write_text(sha(out)+"  dynamic_four_flow_materials.json\n");os.chmod(stage/"dynamic_four_flow_materials.json.sha256",0o444);os.rename(stage,target)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 print(json.dumps({"status":material["status"],"materials":str(target),"materials_id":ident,"expires_at_utc":material["expires_at_utc"],"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (Error,OSError,ValueError,KeyError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
