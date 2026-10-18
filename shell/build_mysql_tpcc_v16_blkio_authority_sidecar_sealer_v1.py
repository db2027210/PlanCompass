#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,secrets,shutil,stat,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();SCRIPT=ROOT/"shell/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py";TEST=ROOT/"tests/test_mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py";OUT=ROOT/"artifacts/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1";PY=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def build():
 r=subprocess.run([str(PY),"-B","-m","pytest","-q",str(TEST)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
 if r.returncode:raise RuntimeError(r.stdout)
 content={"control/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py":SCRIPT.read_bytes(),"tests/test_mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py":TEST.read_bytes(),"builder/build_mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py":SELF.read_bytes()};files={k:sha(v) for k,v in sorted(content.items())};ident={"schema_version":"mysql_tpcc_v16_blkio_authority_sidecar_sealer_bundle_v1","status":"SEALED_LIVE_VALIDATOR_SIDECAR_REPAIR_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"immutable":True,"source_v16_bundle_id":"7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77","authority_content_modification_prohibited":True,"only_missing_exact_sidecar_may_be_created":True,"live_container_pid_cgroup_device_rate_validation_required":True,"database_restart_prohibited":True,"client_restart_prohibited":True,"workload_start_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"tests":"4 passed","files":files};bid=sha(canonical(ident));manifest=dict(ident,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);target=OUT/bid
 ident.update({"schema_version":"mysql_tpcc_v16_blkio_authority_sidecar_sealer_bundle_v2","status":"SEALED_EXACT_AUTHORITY_SHA_LIVE_VALIDATOR_SIDECAR_REPAIR_NOT_EXECUTED","exact_observed_authority_sha256":"6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d","expected_sidecar_content":"6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d  blkio_throttle_authority.json\n"});bid=sha(canonical(ident));manifest=dict(ident,bundle_id=bid);target=OUT/bid
 if not target.exists():
  stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(5));stage.mkdir(mode=0o700)
  try:
   for name,data in content.items():p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n")
   for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o444 if p.is_file() else 0o555)
   os.chmod(stage,0o555);os.rename(stage,target)
  finally:
   if stage.exists():shutil.rmtree(stage)
 if hashlib.sha256((target/"bundle_manifest.json").read_bytes()).hexdigest()!=sha(canonical(manifest)) or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [target,*target.rglob("*")]):raise RuntimeError("closure differs")
 print(json.dumps({"bundle":str(target),"bundle_id":bid,"manifest_sha256":hashlib.sha256((target/"bundle_manifest.json").read_bytes()).hexdigest(),"tests":"4 passed","remote_actions":0},sort_keys=True));return target
if __name__=="__main__":build()
