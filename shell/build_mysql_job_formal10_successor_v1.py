#!/usr/bin/env python3
"""Build immutable same-live JOB formal10 controller successor."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"artifacts/mysql_job_formal10_successor_v1"
SOURCES={
 "controller/mysql_job_formal10_controller_v1.py":ROOT/"shell/mysql_job_formal10_controller_v1.py",
 "campaign/mysql_job_formal10_campaign_v1.py":ROOT/"shell/mysql_job_formal10_campaign_v1.py",
 "publisher/mysql_content_addressed_bundle_publish_v1.py":ROOT/"shell/mysql_content_addressed_bundle_publish_v1.py",
 "builder/build_mysql_job_formal10_successor_v1.py":Path(__file__).resolve(),
 "tests/test_mysql_job_formal10_successor_v1.py":ROOT/"tests/test_mysql_job_formal10_successor_v1.py",
}
def canonical(v):return (json.dumps(v,ensure_ascii=True,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def main():
 payload={r:p.read_bytes() for r,p in SOURCES.items()};files={r:sha(b) for r,b in sorted(payload.items())}
 identity={
  "schema_version":"mysql_job_formal10_successor_bundle_v1","status":"SEALED_SAME_LIVE_FORMAL10_CONTROLLER_NOT_EXECUTED",
  "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,
  "target_bundle_id":"659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef","target_manifest_sha256":"f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406",
  "control_bundle_id":"ec061f7b186af215e14daaaac64333fa3e88c8f934f78fe055d1f3ef091ace0b","control_manifest_sha256":"1117b36cc27c5dbb4d69b26420240d3fbaf8b0d20b8e97ef6ba5490a87bc0438",
  "post_r0_bundle_id":"0aa28209af781a16bd576f81147298d40fe31f3f2c3c6975edbe18843d07e7ad","post_r0_manifest_sha256":"8dc9c53891eb00adce7418709f581e5ce32a819916b895841910bc397b05531e",
  "audit":{"post_r0_to_formal10_predecessor_closed":False,"missing_predecessor_capabilities":["formal10 current-config materializer","per-attempt accepted-round authority","formal attempt executor","atomic ten-accept controller"],"repaired_by_this_bundle":True},
  "formal10_contract":{"same_live_planner_required_every_attempt":True,"fresh_formal_handoff_required_every_attempt":True,"target_accepted_rounds":10,"one_outer_attempt_per_authority":True,"authority_ttl_max_seconds":900,"measurement_seconds":600,"minimum_stable_seconds":180,"fixed_top5_only":True,"exact_client_transport":"exact_client_docker_exec_stdout_v1","qps_role":"outer_acceptance_after_real_replay_only","technical_failure_counted":False,"rejected_attempt_increments_accepted":False,"atomic_accounting_token_required_for_accept":True,"state_hash_chain_required":True},
  "entrypoints":{"controller":"controller/mysql_job_formal10_controller_v1.py","campaign":"campaign/mysql_job_formal10_campaign_v1.py","publisher":"publisher/mysql_content_addressed_bundle_publish_v1.py"},
  "remote_actions_performed":0,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":files,
 }
 bundle_id=sha(canonical(identity));manifest={**identity,"bundle_id":bundle_id};OUT.mkdir(parents=True,exist_ok=True);target=OUT/bundle_id
 if target.exists() or target.is_symlink():
  if json.loads((target/"bundle_manifest.json").read_text())!=manifest:raise RuntimeError("existing formal10 bundle differs")
 else:
  stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for r,b in payload.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n")
   for p in sorted(stage.rglob("*"),reverse=True):
    if p.is_symlink():raise RuntimeError("formal10 symlink")
    os.chmod(p,0o555 if p.is_dir() or p.suffix==".py" else 0o444)
   os.chmod(stage,0o555);os.rename(stage,target)
  except BaseException:
   if stage.exists():shutil.rmtree(stage)
   raise
 print(json.dumps({"status":manifest["status"],"bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":sha((target/"bundle_manifest.json").read_bytes()),"controller":str(target/identity["entrypoints"]["controller"]),"campaign":str(target/identity["entrypoints"]["campaign"]),"publisher":str(target/identity["entrypoints"]["publisher"])},sort_keys=True))
if __name__=="__main__":main()
