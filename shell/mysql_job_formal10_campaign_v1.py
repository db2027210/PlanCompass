#!/usr/bin/env python3
"""Execute exactly one authorized JOB formal outer attempt."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping

POST_ID="0aa28209af781a16bd576f81147298d40fe31f3f2c3c6975edbe18843d07e7ad"
POST_SHA="8dc9c53891eb00adce7418709f581e5ce32a819916b895841910bc397b05531e"


class CampaignError(RuntimeError):pass
def canonical(v:Any)->bytes:return (json.dumps(v,ensure_ascii=True,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
def digest(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def load(path:Path,label:str)->dict[str,Any]:
    if path.is_symlink() or not path.is_file():raise CampaignError(label+" is unsafe")
    v=json.loads(path.read_text());s=Path(str(path)+".sha256")
    if s.is_symlink() or not s.is_file() or s.read_text().split()[0]!=hashlib.sha256(canonical(v)).hexdigest():raise CampaignError(label+" sidecar differs")
    return v
def instant(v:Any):
    d=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    if d.tzinfo is None:raise CampaignError("authority time differs")
    return d.astimezone(timezone.utc)
def validate(config:Mapping[str,Any],authority:Mapping[str,Any],token_path:Path)->str:
    if token_path.is_symlink() or not token_path.is_file() or stat.S_IMODE(token_path.stat().st_mode)!=0o600:raise CampaignError("token must be a mode-0600 regular file")
    token=token_path.read_text().strip();now=datetime.now(timezone.utc)
    if (
        authority.get("schema_version")!="mysql_job_formal10_attempt_authority_v1"
        or authority.get("formal_attempt_id")!=config.get("formal_attempt_id")
        or authority.get("state_sha256")!=config.get("formal_state_sha256")
        or authority.get("current_config_sha256")!=hashlib.sha256(canonical(config)).hexdigest()
        or authority.get("main_agent_token_sha256")!=hashlib.sha256(token.encode()).hexdigest()
        or authority.get("one_outer_attempt_only") is not True
        or authority.get("formal10_authorized") is not True
        or authority.get("accepted_round_authorized") is not True
        or config.get("accepted_round_authorized") is not True
        or int((config.get("formal_outer_protocol") or {}).get("max_outer_attempts",0))!=1
        or int((config.get("formal_outer_protocol") or {}).get("target_accepted_shrinks",0))!=10
        or int(config.get("measurement_seconds",0))!=600
        or int(config.get("minimum_measurement_seconds",0))!=180
        or now<instant(authority.get("not_before_utc")) or now>=instant(authority.get("expires_at_utc"))
    ):raise CampaignError("formal attempt authority/config differs")
    return token
def write(path:Path,value:Mapping[str,Any]):
    if path.exists() or path.is_symlink() or Path(str(path)+".sha256").exists():raise CampaignError("refusing to overwrite result")
    payload=canonical(value);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload);Path(str(path)+".sha256").write_text(hashlib.sha256(payload).hexdigest()+"  "+path.name+"\n");os.chmod(path,0o400);os.chmod(Path(str(path)+".sha256"),0o400)
def execute(config:Mapping[str,Any],baseline:Mapping[str,Any],authority:Mapping[str,Any])->dict[str,Any]:
    service=Path(str(config.get("exact_service_module") or "")).resolve();post=service.parents[1];manifest=post/"bundle_manifest.json"
    if post.name!=POST_ID or digest(manifest)!=POST_SHA:raise CampaignError("post-R0 executable bundle differs")
    campaign_path=post/"campaign/mysql_job_post_r0_debug_campaign_v1.py"
    spec=importlib.util.spec_from_file_location("_job_formal_post_executor",campaign_path)
    if spec is None or spec.loader is None:raise CampaignError("cannot load sealed post-R0 executor")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=dict(module.execute(config,baseline,campaign_argv=tuple(sys.argv)))
    result.update({
        "schema_version":"mysql_job_formal10_attempt_result_v1",
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "formal_attempt_id":authority["formal_attempt_id"],"attempt_index":authority["attempt_index"],
        "authorized_accepted_round":authority["authorized_accepted_round"],
        "authority_sha256":hashlib.sha256(canonical(authority)).hexdigest(),
        "post_r0_bundle_id":POST_ID,"post_r0_manifest_sha256":POST_SHA,
        "accepted_round_authorized":True,"qps_role":"outer_acceptance_after_real_replay_only",
        "one_outer_attempt_only":True,
    })
    return result
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--config",type=Path,required=True);p.add_argument("--baseline-validation",type=Path,required=True);p.add_argument("--main-agent-authority",type=Path,required=True);p.add_argument("--main-agent-token-file",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    config=load(a.config,"config");baseline=load(a.baseline_validation,"baseline");authority=load(a.main_agent_authority,"authority");validate(config,authority,a.main_agent_token_file);write(a.output,execute(config,baseline,authority));return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except (CampaignError,OSError,ValueError,KeyError,TypeError) as exc:raise SystemExit("NOT_READY: %s"%exc)
