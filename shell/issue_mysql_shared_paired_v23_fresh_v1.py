#!/usr/bin/env python3
"""Issue fresh v23 paired qualification and extend only H guard to 600s."""
import argparse,hashlib,importlib.util,json
from pathlib import Path
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("base",str(HERE/"issue_mysql_shared_qualification_headroom_v1.py"))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ("--tpch-old-command","--tpch-old-authority","--tpcc-old-command","--tpcc-old-authority","--fresh-token-file","--tpch-output-dir","--tpcc-output-dir"):p.add_argument(n,type=Path,required=True)
 for n in ("--tpch-old-attempt","--tpcc-old-attempt","--tpch-new-attempt","--tpcc-new-attempt","--collector-bundle-id","--collector-manifest-sha256"):p.add_argument(n,required=True)
 p.add_argument("--lifetime-seconds",type=int,default=3600)
 p.add_argument("--old-guard-seconds",type=int,default=420)
 p.add_argument("--new-guard-seconds",type=int,default=600)
 a=p.parse_args()
 attempts=(a.tpch_old_attempt,a.tpcc_old_attempt,a.tpch_new_attempt,a.tpcc_new_attempt)
 if any(len(x)<32 or not x.replace("-","").isalnum() for x in attempts) or len(set(attempts))!=4 or any(len(x)!=64 for x in (a.collector_bundle_id,a.collector_manifest_sha256)) or not 600<=a.lifetime_seconds<=3600 or not 181<=a.old_guard_seconds<=600 or not 181<=a.new_guard_seconds<=600:raise base.IssueError("issuer identity or TTL differs")
 h=base.load_sealed(a.tpch_old_command,"TPC-H old command");ha=base.load_sealed(a.tpch_old_authority,"TPC-H old authority");c=base.load_sealed(a.tpcc_old_command,"TPC-C old command");ca=base.load_sealed(a.tpcc_old_authority,"TPC-C old authority")
 base.validate_predecessor(h,ha,a.tpch_old_command,a.tpch_old_attempt,a.tpcc_old_attempt);base.validate_predecessor(c,ca,a.tpcc_old_command,a.tpcc_old_attempt,a.tpch_old_attempt)
 binding=h.get("native_sql_adapter_binding") or {}
 old_guard="--duration-seconds=%d"%a.old_guard_seconds
 if binding.get("workload_duration_seconds")!=a.old_guard_seconds or (h.get("argv") or []).count(old_guard)!=1 or binding.get("collector_sample_window_seconds")!=180 or len(str(binding.get("bundle_id") or ""))!=64:raise base.IssueError("TPC-H adapter predecessor differs")
 mapping={a.tpch_old_attempt:a.tpch_new_attempt,a.tpcc_old_attempt:a.tpcc_new_attempt};h=base.replace(h,mapping);c=base.replace(c,mapping)
 i=h["argv"].index(old_guard);h["argv"][i]="--duration-seconds=%d"%a.new_guard_seconds;h["native_sql_adapter_binding"]["workload_duration_seconds"]=a.new_guard_seconds
 if h.get("qualification_attempt_id")!=a.tpch_new_attempt or c.get("qualification_attempt_id")!=a.tpcc_new_attempt or h.get("paired_peer_workload",{}).get("qualification_attempt_id")!=a.tpcc_new_attempt or c.get("paired_peer_workload",{}).get("qualification_attempt_id")!=a.tpch_new_attempt:raise base.IssueError("new reciprocal binding differs")
 fresh=base.token(a.fresh_token_file);now=time.time()
 def auth(old,cmd,own):
  v=base.replace(old,mapping);v.update({"qualification_attempt_id":own,"command_spec_sha256":base.sha_bytes(base.canonical(cmd)),"main_agent_token_sha256":hashlib.sha256(fresh.encode("ascii")).hexdigest(),"not_before_epoch":now-1.0,"expires_epoch":now+float(a.lifetime_seconds),"collector_bundle_id":a.collector_bundle_id,"collector_manifest_sha256":a.collector_manifest_sha256,"qualification_only":True,"debug_only":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False});return v
 base.write_one(a.tpch_output_dir,h,auth(ha,h,a.tpch_new_attempt));base.write_one(a.tpcc_output_dir,c,auth(ca,c,a.tpcc_new_attempt))
 print(json.dumps({"status":"ISSUED_PAIRED_DEBUG_QUALIFICATION_ONLY","tpch_attempt":a.tpch_new_attempt,"tpcc_attempt":a.tpcc_new_attempt,"tpch_guard_seconds":a.new_guard_seconds,"collector_sample_window_seconds":180,"collector_bundle_id":a.collector_bundle_id,"expires_epoch":now+float(a.lifetime_seconds),"formal_r0_authorized":False,"accepted_round_authorized":False},sort_keys=True))

if __name__=="__main__":
 try:main()
 except (base.IssueError,OSError,ValueError,KeyError,IndexError) as exc:
  print("NOT_READY: "+str(exc),file=__import__("sys").stderr);raise SystemExit(2)
