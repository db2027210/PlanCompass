#!/usr/bin/env python3
"""Fail-closed JOB formal10 state, per-attempt authority, and commit controller."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Mapping

TARGET_ID = "659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef"
TARGET_SHA = "f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406"
CONTROL_ID = "ec061f7b186af215e14daaaac64333fa3e88c8f934f78fe055d1f3ef091ace0b"
CONTROL_SHA = "1117b36cc27c5dbb4d69b26420240d3fbaf8b0d20b8e97ef6ba5490a87bc0438"
POST_ID = "0aa28209af781a16bd576f81147298d40fe31f3f2c3c6975edbe18843d07e7ad"
POST_SHA = "8dc9c53891eb00adce7418709f581e5ce32a819916b895841910bc397b05531e"
PARAMETERS = ("io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost", "memory_temptable_row_cost")
FORMULA = "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"


class Formal10Error(RuntimeError): pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sealed(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file(): raise Formal10Error(label + " is absent or unsafe")
    value=json.loads(path.read_text(encoding="utf-8"));side=Path(str(path)+".sha256")
    if side.is_symlink() or not side.is_file() or side.read_text().split()[0] != hashlib.sha256(canonical(value)).hexdigest():
        raise Formal10Error(label + " sidecar differs")
    return value


def verify_bundle(path: Path, bundle_id: str, manifest_sha: str) -> Path:
    root=path.resolve(strict=True);manifest=root/"bundle_manifest.json"
    if path.is_symlink() or root.name!=bundle_id or digest(manifest)!=manifest_sha: raise Formal10Error("bundle identity differs")
    value=json.loads(manifest.read_text());files=value.get("files") or {}
    for relative, expected in files.items():
        expected_hash=expected["sha256"] if isinstance(expected,dict) else expected
        item=root/relative
        if item.is_symlink() or not item.is_file() or digest(item)!=expected_hash: raise Formal10Error("bundle closure differs: "+relative)
    return root


def _time(value: Any) -> datetime:
    result=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if result.tzinfo is None: raise Formal10Error("authority time is naive")
    return result.astimezone(timezone.utc)


def verify_handoff(value: Mapping[str, Any], fresh: Mapping[str, Any], debug: Mapping[str, Any], *, now: datetime | None=None) -> None:
    clock=now or datetime.now(timezone.utc)
    if (
        value.get("schema_version")!="mysql_job_distributed_v2_same_live_planner_handoff_v1"
        or value.get("status")!="READY_FORMAL10_SAME_LIVE_PLANNER" or value.get("stage")!="formal10"
        or value.get("bundle_id")!=TARGET_ID or value.get("attempt_token")!=fresh.get("attempt_token")
        or value.get("planner_identity")!=fresh.get("planner_identity")
        or debug.get("planner_identity")!=fresh.get("planner_identity")
        or value.get("formal10_authorized") is not True or value.get("target_accepted_rounds")!=10
        or value.get("outer_acceptance_authorized") is not True
        or value.get("accepted_round_authorized") is not False
        or value.get("capture_or_r0_or_debug_countable_as_accepted") is not False
        or clock >= _time(value.get("expires_at_utc"))
    ): raise Formal10Error("formal10 same-live handoff differs")


def write_sealed(path: Path, value: Mapping[str, Any], mode: int=0o400) -> None:
    side=Path(str(path)+".sha256")
    if any(p.exists() or p.is_symlink() for p in (path,side)): raise Formal10Error("refusing to overwrite "+str(path))
    path.parent.mkdir(parents=True,exist_ok=True);payload=canonical(value)
    for target,data,target_mode in ((path,payload,mode),(side,(hashlib.sha256(payload).hexdigest()+"  "+path.name+"\n").encode(),0o400)):
        fd=os.open(str(target),os.O_WRONLY|os.O_CREAT|os.O_EXCL,target_mode)
        with os.fdopen(fd,"wb") as f:f.write(data);f.flush();os.fsync(f.fileno())


def initial_state(fresh: Mapping[str,Any], debug: Mapping[str,Any], handoff: Mapping[str,Any], baseline: Mapping[str,Any], base_config: Mapping[str,Any]) -> dict[str,Any]:
    verify_handoff(handoff,fresh,debug)
    if (
        debug.get("status")!="PASS_DEBUG_ROUND" or debug.get("completed_nonempty_subsets")!=15
        or debug.get("fixed_top5_only") is not True or debug.get("fingerprint_l1_l2_l3_complete") is not True
        or debug.get("accepted_rounds_credit")!=0 or debug.get("algorithm_formula")!=FORMULA
        or base_config.get("target_bundle_id")!=TARGET_ID or base_config.get("support_bundle_id")!=CONTROL_ID
        or base_config.get("accepted_round_authorized") is not False
    ): raise Formal10Error("debug predecessor or base config differs")
    if baseline.get("db_qps_source")!="database_status_counter" or baseline.get("qps")!=baseline.get("db_qps"):
        raise Formal10Error("baseline must bind database-side QPS")
    return {
        "schema_version":"mysql_job_formal10_state_v1","status":"IN_PROGRESS",
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_SHA,
        "control_bundle_id":CONTROL_ID,"control_manifest_sha256":CONTROL_SHA,
        "post_r0_bundle_id":POST_ID,"post_r0_manifest_sha256":POST_SHA,
        "attempt_token":fresh["attempt_token"],"planner_identity":fresh["planner_identity"],
        "fresh_r0_sha256":hashlib.sha256(canonical(fresh)).hexdigest(),
        "debug_round_sha256":hashlib.sha256(canonical(debug)).hexdigest(),
        "accepted_rounds":0,"rejected_rounds":0,"technical_failures_counted":0,
        "attempts_committed":0,"target_accepted_rounds":10,"next_accepted_round":1,
        "current_validation":copy.deepcopy(dict(baseline)),
        "current_costs":copy.deepcopy((base_config.get("cost_parameters") or {}).get("baseline_values") or {}),
        "current_ranges":copy.deepcopy((base_config.get("cost_parameters") or {}).get("ranges") or {}),
        "current_sensitivity":copy.deepcopy(base_config.get("sensitivity_table") or {}),
        "seen_result_sha256":[],"previous_state_sha256":None,"formal_complete":False,
    }


def authorize(state: Mapping[str,Any], handoff: Mapping[str,Any], fresh: Mapping[str,Any], debug: Mapping[str,Any], base: Mapping[str,Any], output: Path, ttl: int, formal_root: Path) -> Mapping[str,Any]:
    if state.get("status")!="IN_PROGRESS" or state.get("formal_complete") is not False or int(state.get("accepted_rounds",-1))>=10:
        raise Formal10Error("formal10 state cannot authorize another attempt")
    verify_handoff(handoff,fresh,debug)
    if handoff.get("planner_identity")!=state.get("planner_identity") or handoff.get("attempt_token")!=state.get("attempt_token"):
        raise Formal10Error("same-live identity changed")
    if ttl<60 or ttl>900: raise Formal10Error("attempt TTL must be 60..900 seconds")
    if output.exists() or output.is_symlink(): raise Formal10Error("attempt output exists")
    output.mkdir(parents=True,mode=0o700)
    attempt_index=int(state["attempts_committed"])+1
    attempt_id="job-formal10-a%04d-r%02d-%s"%(attempt_index,int(state["next_accepted_round"]),secrets.token_hex(16))
    config=copy.deepcopy(dict(base));config.update({
        "schema_version":"mysql_job_formal10_current_config_v1","debug_only":False,
        "formal_launch_authorized":True,"accepted_round_authorized":True,"commit_best":True,
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "formal_attempt_id":attempt_id,"formal_state_sha256":hashlib.sha256(canonical(state)).hexdigest(),
    })
    config["same_live_planner_authority"]={"path":str(formal_root.resolve()),"sha256":digest(formal_root),"attempt_token":state["attempt_token"],"bundle_id":TARGET_ID}
    config["cost_parameters"]["baseline_values"]=copy.deepcopy(state["current_costs"])
    config["cost_parameters"]["ranges"]=copy.deepcopy(state["current_ranges"])
    config["sensitivity_table"]=copy.deepcopy(state["current_sensitivity"])
    config["measurement_seconds"]=600;config["minimum_measurement_seconds"]=180
    config["output"]={"directory":str((output/"campaign").resolve()),"verbose":True}
    config["formal_outer_protocol"].update({
        "accepted_shrink_count":int(state["accepted_rounds"]),"rejected_count":int(state["rejected_rounds"]),
        "target_accepted_shrinks":10,"max_outer_attempts":1,
        "formal_point_journal_root":str((output/"point_journal").resolve()),
        "formal_checkpoint_path":str((output/"formal_outer_checkpoint.json").resolve()),
        "formal_real_r0_checkpoint_path":str((output/"formal_real_r0.json").resolve()),
        "lane_lease_path":str((output/"lane.lock").resolve()),
    })
    token=secrets.token_urlsafe(48);now=datetime.now(timezone.utc);expires=min(now+timedelta(seconds=ttl),_time(handoff["expires_at_utc"]))
    if (expires-now).total_seconds()<60: raise Formal10Error("formal handoff has insufficient remaining TTL")
    authority={
        "schema_version":"mysql_job_formal10_attempt_authority_v1","authorized_by":"main_agent",
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "attempt_token":state["attempt_token"],"formal_attempt_id":attempt_id,
        "attempt_index":attempt_index,"authorized_accepted_round":int(state["next_accepted_round"]),
        "accepted_before":int(state["accepted_rounds"]),"accepted_after_maximum":int(state["accepted_rounds"])+1,
        "planner_identity":state["planner_identity"],"state_sha256":hashlib.sha256(canonical(state)).hexdigest(),
        "formal_handoff_sha256":hashlib.sha256(canonical(handoff)).hexdigest(),
        "current_config_sha256":hashlib.sha256(canonical(config)).hexdigest(),
        "main_agent_token_sha256":hashlib.sha256(token.encode()).hexdigest(),
        "not_before_utc":now.isoformat(),"expires_at_utc":expires.isoformat(),
        "one_outer_attempt_only":True,"formal10_authorized":True,
        "accepted_round_authorized":True,"formal_launch_authorized":True,
    }
    write_sealed(output/"current_config.json",config);write_sealed(output/"baseline_validation.json",state["current_validation"])
    write_sealed(output/"main_agent_authority.json",authority)
    token_path=output/"main_agent_token";token_path.write_text(token+"\n",encoding="ascii");token_path.chmod(0o600)
    controller_bundle=Path(__file__).resolve().parents[1]
    launch={"schema_version":"mysql_job_formal10_attempt_launch_v1","status":"MATERIALIZED_NOT_EXECUTED","argv":[sys.executable,str(controller_bundle/"campaign/mysql_job_formal10_campaign_v1.py"),"--config",str(output/"current_config.json"),"--baseline-validation",str(output/"baseline_validation.json"),"--main-agent-authority",str(output/"main_agent_authority.json"),"--main-agent-token-file",str(token_path),"--output",str(output/"formal_result.json")],"accepted_round_authorized":True,"one_outer_attempt_only":True}
    write_sealed(output/"launch.json",launch)
    return launch


def _finite(value: Any) -> bool: return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(float(value))


def validate_result(result: Mapping[str,Any], state: Mapping[str,Any], authority: Mapping[str,Any]) -> tuple[bool,Mapping[str,Any],Mapping[str,Any]]:
    outer=result.get("outer_loop");summary=result.get("result") or {}
    if result.get("schema_version")!="mysql_job_formal10_attempt_result_v1" or result.get("formal_attempt_id")!=authority.get("formal_attempt_id") or result.get("post_r0_bundle_id")!=POST_ID or result.get("qps_role")!="outer_acceptance_after_real_replay_only" or not isinstance(outer,list) or len(outer)!=1:
        raise Formal10Error("formal result identity differs")
    row=outer[0];record=row.get("campaign_round") or {};orchestration=row.get("real_replay_orchestration") or {};evaluations=orchestration.get("evaluations") or []
    if len(evaluations)!=5 or [e.get("candidate_rank") for e in evaluations]!=[1,2,3,4,5]: raise Formal10Error("fixed Top-5 replay is incomplete")
    for evaluation in evaluations:
        validation=evaluation.get("candidate_final_validation") or {};evidence=validation.get("evidence") or {};stable=evidence.get("stability_windows_and_coverage") or {}
        if (
            evaluation.get("callback_error") is not None or (evaluation.get("recovery") or {}).get("verified") is not True
            or validation.get("db_qps_source")!="database_status_counter" or validation.get("qps")!=validation.get("db_qps")
            or not _finite(validation.get("qps")) or int(stable.get("measurement_seconds",0))!=600
            or int(stable.get("sample_count",0))<600 or stable.get("status") not in {None,"PASS"}
            or not evidence.get("requested_effective_costs")
            or not evidence.get("fresh_connection_generation")
        ): raise Formal10Error("replay stability/exact recovery/database-QPS proof differs")
    accepted=record.get("accepted") is True;before=int(state["accepted_rounds"]);after=int(record.get("accepted_shrink_count",-1))
    if accepted:
        if after!=before+1 or record.get("accepted_round_authorized") is not True or record.get("atomic_accounting_committed") is not True or not record.get("accepted_accounting_token"):
            raise Formal10Error("accepted round lacks atomic authority/accounting")
    elif after!=before or record.get("atomic_accounting_committed") is not False:
        raise Formal10Error("rejected round changed accepted accounting")
    if int(summary.get("accepted_shrink_count",-1))!=after or int(summary.get("target_accepted_shrinks",-1))!=10:
        raise Formal10Error("campaign result accounting differs")
    return accepted,record,orchestration


def commit(state: Mapping[str,Any], authority: Mapping[str,Any], token: str, result: Mapping[str,Any]) -> dict[str,Any]:
    if authority.get("state_sha256")!=hashlib.sha256(canonical(state)).hexdigest() or authority.get("main_agent_token_sha256")!=hashlib.sha256(token.encode()).hexdigest() or datetime.now(timezone.utc)>=_time(authority.get("expires_at_utc")):
        raise Formal10Error("attempt authority is invalid or expired")
    result_sha=hashlib.sha256(canonical(result)).hexdigest()
    if result_sha in state.get("seen_result_sha256",[]): raise Formal10Error("formal result replay is prohibited")
    accepted,record,orchestration=validate_result(result,state,authority)
    updated=copy.deepcopy(dict(state));updated["previous_state_sha256"]=hashlib.sha256(canonical(state)).hexdigest();updated["attempts_committed"]+=1
    if accepted:
        updated["accepted_rounds"]+=1;updated["next_accepted_round"]=updated["accepted_rounds"]+1
        updated["current_costs"]=copy.deepcopy(record["incumbent_after"]);updated["current_validation"]=copy.deepcopy(record["incumbent_validation_after"])
        updated["current_ranges"]=copy.deepcopy(record.get("range_after") or updated["current_ranges"])
    else: updated["rejected_rounds"]+=1
    sensitivity=orchestration.get("updated_sensitivity_table")
    if isinstance(sensitivity,Mapping): updated["current_sensitivity"]=copy.deepcopy(dict(sensitivity))
    updated["seen_result_sha256"].append(result_sha);updated["technical_failures_counted"]=0
    updated["formal_complete"]=updated["accepted_rounds"]==10;updated["status"]="PASS_FORMAL10_COMPLETE" if updated["formal_complete"] else "IN_PROGRESS"
    if updated["accepted_rounds"]>10: raise Formal10Error("accepted count exceeded ten")
    return updated


def main() -> int:
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument("--init",action="store_true");mode.add_argument("--authorize",action="store_true");mode.add_argument("--commit",action="store_true")
    parser.add_argument("--state",type=Path);parser.add_argument("--fresh-r0",type=Path);parser.add_argument("--debug-round",type=Path);parser.add_argument("--formal-handoff",type=Path);parser.add_argument("--base-config",type=Path);parser.add_argument("--baseline-validation",type=Path);parser.add_argument("--authority",type=Path);parser.add_argument("--main-agent-token-file",type=Path);parser.add_argument("--result",type=Path);parser.add_argument("--output",type=Path,required=True);parser.add_argument("--ttl-seconds",type=int,default=900)
    args=parser.parse_args()
    if args.init:
        value=initial_state(load_sealed(args.fresh_r0,"fresh R0"),load_sealed(args.debug_round,"debug round"),load_sealed(args.formal_handoff,"formal handoff"),load_sealed(args.baseline_validation,"baseline"),load_sealed(args.base_config,"base config"));write_sealed(args.output,value)
    elif args.authorize:
        state=load_sealed(args.state,"state");fresh=load_sealed(args.fresh_r0,"fresh R0");debug=load_sealed(args.debug_round,"debug round");handoff=load_sealed(args.formal_handoff,"formal handoff");base=load_sealed(args.base_config,"base config");formal_root=Path(__file__).resolve().parents[1]/"authority/formal_handoff.json"
        # Use the supplied freshly sealed handoff path in the generated config.
        formal_root=args.formal_handoff
        authorize(state,handoff,fresh,debug,base,args.output,args.ttl_seconds,formal_root)
    else:
        state=load_sealed(args.state,"state");authority=load_sealed(args.authority,"authority");result=load_sealed(args.result,"result");token=args.main_agent_token_file.read_text().strip();write_sealed(args.output,commit(state,authority,token,result))
    return 0


if __name__=="__main__":
    try:raise SystemExit(main())
    except (Formal10Error,OSError,ValueError,KeyError,TypeError) as exc:raise SystemExit("NOT_READY: %s"%exc)
