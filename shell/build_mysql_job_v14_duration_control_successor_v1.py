#!/usr/bin/env python3
"""Build the immutable 6b55 control successor bound to JOB duration v14."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat

ROOT = Path(__file__).resolve().parents[1]
PRED_ID = "6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74"
PRED_SHA = "5c5e59098304942800ef44dc17c777ffa6df0a81261a275b526a9737474c77ba"
PRED = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v3" / PRED_ID
TARGET_ID = "659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef"
TARGET_SHA = "f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406"
OLD_TARGET = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
OLD_FAMILY = "artifacts/mysql_job_raw_all_subset_three_level_successor_v13"
NEW_FAMILY = "artifacts/mysql_job_native_duration_successor_v14"
OLD_BACKEND_SHA = "02ef703b51f923bdc3cbe0127830e929872eb212468e1b6dcbd1577ee00bb7ad"
NEW_BACKEND_SHA = "4010961af8ac559e0968993a6c9aaad127a9a55012d961fa9d477fb24c1dc7e8"
SCHEMA = "mysql_job_v14_duration_control_successor_v1"
OUT = ROOT / "artifacts/mysql_job_v14_duration_control_successor_v1"
OPERATIONS = (
    "controller/mysql_job_v13_qualification_authority_v2.py",
    "controller/mysql_job_xp_four_flow_controller_v1.py",
    "controller/mysql_xp_successor_binding_v1.py",
    "controller/mysql_xp_successor_qualification_launch_adapter_v2.py",
    "controller/render_mysql_job_xp_successor_short_ttl_v3.py",
    "publisher/mysql_content_addressed_bundle_publish_v1.py",
    "qualification/mysql_job_v13_fresh_r0_runner_v1.py",
    "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py",
    "qualification/mysql_xp_successor_binding_v1.py",
    "templates/mysql_job_v13_xp_successor_short_ttl_commands_v3.json",
)
BUILDER = "builder/build_mysql_job_v14_duration_control_successor_v1.py"
FRESH = "controller/mysql_job_v14_fresh_attempt_materializer_v1.py"
INVALIDATION = "authority/mysql_job_6b55_failed_attempt_invalidation_20260803.json"
TEST = "tests/test_mysql_job_v14_duration_control_successor_v1.py"


class BuildError(RuntimeError): pass
def canonical(value): return (json.dumps(value,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(data): return hashlib.sha256(data).hexdigest()
def digest(path): return sha(Path(path).read_bytes())


def verify_predecessor():
    path=PRED/"bundle_manifest.json";m=json.loads(path.read_text());u=dict(m);u.pop("bundle_id",None)
    if m.get("bundle_id")!=PRED_ID or digest(path)!=PRED_SHA or sha(canonical(u))!=PRED_ID:
        raise BuildError("6b55 predecessor identity differs")
    for r,h in m["files"].items():
        p=PRED/r
        if p.is_symlink() or not p.is_file() or digest(p)!=h: raise BuildError("6b55 closure differs: "+r)
    return m


def replace_all(data):
    return data.replace(OLD_TARGET.encode(),TARGET_ID.encode()).replace(OLD_FAMILY.encode(),NEW_FAMILY.encode())


def patch_authority(data):
    marker=b'TARGET_ID = "'+TARGET_ID.encode()+b'"\n'
    addition=marker+b'TARGET_MANIFEST_SHA256 = "'+TARGET_SHA.encode()+b'"\nPREDECESSOR_CONTROL_ID = "'+PRED_ID.encode()+b'"\nPREDECESSOR_CONTROL_MANIFEST_SHA256 = "'+PRED_SHA.encode()+b'"\nFAILED_ATTEMPT_MARKER = "6b55-20260803t0427"\n'
    if data.count(marker)!=1: raise BuildError("authority constant marker differs")
    data=data.replace(marker,addition)
    gate=b'''def materialize(args: argparse.Namespace) -> Mapping[str, Any]:
    if SAFE_ID.fullmatch(args.qualification_attempt_id or "") is None:
'''
    new=b'''def materialize(args: argparse.Namespace) -> Mapping[str, Any]:
    control_root = Path(__file__).resolve().parents[1]
    control_manifest_path = control_root / "bundle_manifest.json"
    control_manifest = json.loads(control_manifest_path.read_text(encoding="utf-8"))
    dispatch = args.shared_dispatch.resolve(strict=True)
    if (
        dispatch.parent.parent != control_root
        or control_manifest.get("bundle_id") != control_root.name
        or control_manifest.get("schema_version") != "mysql_job_v14_duration_control_successor_v1"
        or control_manifest.get("target_job_bundle_id") != TARGET_ID
        or control_manifest.get("target_job_manifest_sha256") != TARGET_MANIFEST_SHA256
        or control_manifest.get("predecessor_support_bundle_id") != PREDECESSOR_CONTROL_ID
        or control_manifest.get("predecessor_support_manifest_sha256") != PREDECESSOR_CONTROL_MANIFEST_SHA256
        or (control_manifest.get("files") or {}).get(
            "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py"
        ) != sha256(dispatch)
        or FAILED_ATTEMPT_MARKER in str(args.qualification_attempt_id or "")
    ):
        raise AuthorityError("v14 control identity or failed-attempt denylist differs")
    control_manifest_sha256 = sha256(control_manifest_path)
    if SAFE_ID.fullmatch(args.qualification_attempt_id or "") is None:
'''
    if data.count(gate)!=1: raise BuildError("authority materialize marker differs")
    data=data.replace(gate,new)
    identity=b'''        "bundle_id": TARGET_ID,
        "mode": args.mode,
'''
    identity_new=b'''        "bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_MANIFEST_SHA256,
        "qualification_control_bundle_id": control_root.name,
        "qualification_control_manifest_sha256": control_manifest_sha256,
        "qualification_control_predecessor_bundle_id": PREDECESSOR_CONTROL_ID,
        "qualification_control_predecessor_manifest_sha256": PREDECESSOR_CONTROL_MANIFEST_SHA256,
        "failed_attempt_reusable": False,
        "mode": args.mode,
'''
    if data.count(identity)!=1: raise BuildError("authority document marker differs")
    data=data.replace(identity,identity_new)
    launch=b'''        "shared_dispatch_sha256": sha256(args.shared_dispatch.resolve(strict=True)),
        "capture_authorized": False,
'''
    launch_new=b'''        "shared_dispatch_sha256": sha256(args.shared_dispatch.resolve(strict=True)),
        "qualification_control_bundle_id": control_root.name,
        "qualification_control_manifest_sha256": control_manifest_sha256,
        "qualification_control_predecessor_bundle_id": PREDECESSOR_CONTROL_ID,
        "target_manifest_sha256": TARGET_MANIFEST_SHA256,
        "failed_attempt_reusable": False,
        "capture_authorized": False,
'''
    if data.count(launch)!=1: raise BuildError("authority launch marker differs")
    return data.replace(launch,launch_new)


def patch_runner(data):
    marker=b'TARGET_ID = "'+TARGET_ID.encode()+b'"\n'
    addition=marker+b'TARGET_MANIFEST_SHA256 = "'+TARGET_SHA.encode()+b'"\nPREDECESSOR_CONTROL_ID = "'+PRED_ID.encode()+b'"\nPREDECESSOR_CONTROL_MANIFEST_SHA256 = "'+PRED_SHA.encode()+b'"\nFAILED_ATTEMPT_MARKER = "6b55-20260803t0427"\n'
    if data.count(marker)!=1: raise BuildError("runner constant marker differs")
    data=data.replace(marker,addition)
    check=b'''        or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
        or authority.get("main_agent_token_sha256")'''
    check_new=b'''        or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
        or FAILED_ATTEMPT_MARKER in str(command.get("qualification_attempt_id") or "")
        or authority.get("failed_attempt_reusable") is not False
        or authority.get("main_agent_token_sha256")'''
    if data.count(check)!=1: raise BuildError("runner failed-attempt marker differs")
    data=data.replace(check,check_new)
    main=b'''    dispatch = args.shared_dispatch.resolve(strict=True)
    if HEX64.fullmatch(args.shared_dispatch_sha256) is None or sha256(dispatch) != args.shared_dispatch_sha256:
        raise R0Error("shared dispatch identity differs")
'''
    main_new=b'''    dispatch = args.shared_dispatch.resolve(strict=True)
    control_root = Path(__file__).resolve().parents[1]
    control_manifest_path = control_root / "bundle_manifest.json"
    control_manifest = json.loads(control_manifest_path.read_text(encoding="utf-8"))
    if (
        HEX64.fullmatch(args.shared_dispatch_sha256) is None
        or sha256(dispatch) != args.shared_dispatch_sha256
        or dispatch.parent.parent != control_root
        or control_manifest.get("bundle_id") != control_root.name
        or control_manifest.get("schema_version") != "mysql_job_v14_duration_control_successor_v1"
        or control_manifest.get("target_job_bundle_id") != TARGET_ID
        or control_manifest.get("target_job_manifest_sha256") != TARGET_MANIFEST_SHA256
        or control_manifest.get("predecessor_support_bundle_id") != PREDECESSOR_CONTROL_ID
        or control_manifest.get("predecessor_support_manifest_sha256") != PREDECESSOR_CONTROL_MANIFEST_SHA256
        or (control_manifest.get("files") or {}).get(
            "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py"
        ) != args.shared_dispatch_sha256
    ):
        raise R0Error("shared dispatch/control identity differs")
'''
    if data.count(main)!=1: raise BuildError("runner dispatch marker differs")
    data=data.replace(main,main_new)
    result=b'''    result = build_evidence(bootstrap, gate, raw, sha256(raw_path))
    atomic_json(args.output, result)
'''
    result_new=b'''    result = build_evidence(bootstrap, gate, raw, sha256(raw_path))
    result.update({
        "target_manifest_sha256": TARGET_MANIFEST_SHA256,
        "qualification_control_bundle_id": control_root.name,
        "qualification_control_manifest_sha256": sha256(control_manifest_path),
        "qualification_control_predecessor_bundle_id": PREDECESSOR_CONTROL_ID,
        "qualification_control_predecessor_manifest_sha256": PREDECESSOR_CONTROL_MANIFEST_SHA256,
        "shared_dispatch_sha256": args.shared_dispatch_sha256,
        "failed_attempt_reusable": False,
    })
    atomic_json(args.output, result)
'''
    if data.count(result)!=1: raise BuildError("runner evidence marker differs")
    return data.replace(result,result_new)


def test_source():
    return b'''from __future__ import annotations
import argparse,hashlib,importlib.util,json,stat
from datetime import datetime,timezone
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT.parents[2]
TARGET=PROJECT/"artifacts/mysql_job_native_duration_successor_v14/659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef"
def load(name,relative):
 p=ROOT/relative;s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def test_manifest_exact_identity_and_zero_credit():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 assert m["bundle_id"]==ROOT.name==hashlib.sha256((json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()).hexdigest()
 assert m["target_job_bundle_id"]==TARGET.name and m["failed_attempt_reusable"] is False
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 actual={p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and p.name not in {"bundle_manifest.json","bundle_manifest.json.sha256"}}
 assert actual==set(m["files"])
 for r,h in m["files"].items():assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
def test_dispatcher_allows_only_v14_job_and_verifies_real_bundle():
 d=load("d","qualification/mysql_job_xp_shared_qualification_dispatch_v1.py")
 assert d.JOB_BUNDLE_ID==TARGET.name and set(d.SUPPORTED_BACKEND_HASHES["job"])=={TARGET.name}
 path=TARGET/"qualification/mysql_job_distributed_live_readback_qualification_v2.py"
 assert d.sha256(path)=="4010961af8ac559e0968993a6c9aaad127a9a55012d961fa9d477fb24c1dc7e8"
 backend=d.load_backend("job",path);manifest,_=backend.verify_bundle(TARGET);assert manifest["bundle_id"]==TARGET.name
def test_authority_and_fresh_r0_bind_control_and_deny_failed_attempt():
 a=load("a","controller/mysql_job_v13_qualification_authority_v2.py");r=load("r","qualification/mysql_job_v13_fresh_r0_runner_v1.py")
 assert a.TARGET_ID==r.TARGET_ID==TARGET.name
 assert a.TARGET_MANIFEST_SHA256==r.TARGET_MANIFEST_SHA256=="f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406"
 for relative in ("controller/mysql_job_v13_qualification_authority_v2.py","qualification/mysql_job_v13_fresh_r0_runner_v1.py"):
  s=(ROOT/relative).read_text();assert "failed_attempt_reusable" in s and "qualification_control_manifest_sha256" in s
def test_authority_mechanically_creates_private_short_lived_material(monkeypatch,tmp_path):
 a=load("authority_materialize","controller/mysql_job_v13_qualification_authority_v2.py")
 spec_doc={"bundle":{"path":str(TARGET)},"paths":{"host_measurement_tmpfs":str(tmp_path/"measurement")},"containers":{"database":"job_db","client":"job_client"},"mysql_defaults_file":str(tmp_path/"mysql.cnf")}
 bootstrap_spec=tmp_path/"spec.json";bootstrap_evidence=tmp_path/"evidence.json";bootstrap_spec.write_text(json.dumps(spec_doc));bootstrap_evidence.write_text("{}")
 class Base:
  @staticmethod
  def load(path,label):return spec_doc if path==bootstrap_spec else {}
  @staticmethod
  def extract_evidence(value):return value
  @staticmethod
  def validate_inputs(spec,evidence):return {"bundle_id":TARGET.name,"attempt":"job-v14-bootstrap-"+"a"*32}
  @staticmethod
  def build_command(spec,evidence,binding):return {"schema_version":"mysql_algorithm_c_debug_qualification_command_v1","bundle_id":TARGET.name,"argv":[binding["attempt"]+"-qualification-solo","/measurement/qualification_solo.native.jsonl"],"client_container":{"container_id":"b"*64}}
 monkeypatch.setattr(a,"load_base",lambda path:Base)
 qid="job-v14-fresh-qualification-"+"c"*32
 args=argparse.Namespace(bootstrap_spec=bootstrap_spec,bootstrap_evidence=bootstrap_evidence,shared_dispatch=ROOT/"qualification/mysql_job_xp_shared_qualification_dispatch_v1.py",mode="qualification_solo",qualification_attempt_id=qid,output_directory=tmp_path/"authority",ttl_seconds=120,peer_scenario=None,peer_qualification_attempt_id=None,peer_workload_metric_stream=None,peer_readback=None,peer_container=None)
 result=a.materialize(args);doc=json.loads(Path(result["authority"]).read_text());token=tmp_path/"authority/main_agent_token"
 assert doc["bundle_id"]==TARGET.name and doc["qualification_control_bundle_id"]==ROOT.name
 assert doc["qualification_control_predecessor_bundle_id"]=="6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74"
 assert doc["failed_attempt_reusable"] is False and 0 < doc["expires_epoch"]-doc["not_before_epoch"] <= 121
 assert stat.S_IMODE(token.stat().st_mode)==0o600
 args.qualification_attempt_id="job-6b55-20260803t0427-"+"d"*32;args.output_directory=tmp_path/"denied"
 with pytest.raises(a.AuthorityError):a.materialize(args)
def test_mechanical_fresh_id_never_reuses_failed_attempt():
 f=load("f","controller/mysql_job_v14_fresh_attempt_materializer_v1.py")
 value=f.fresh_qualification_attempt_id(datetime(2026,8,3,tzinfo=timezone.utc))
 assert value.startswith("job-v14-fresh-qual-20260803T000000Z-")
 assert f.FAILED_MARKER not in value and len(value)>=32
 root,manifest=f.verify_support(ROOT);assert root==ROOT and manifest["failed_attempt_reusable"] is False
'''


def freeze(root):
    for p in sorted(root.rglob("*"),reverse=True):
        if p.is_symlink(): raise BuildError("control successor symlink")
        os.chmod(p, (0o555 if p.is_dir() or p.suffix==".py" else 0o444))
    os.chmod(root,0o555)


def main():
    predecessor=verify_predecessor();payload={};modes={}
    for r in OPERATIONS:
        data=replace_all((PRED/r).read_bytes())
        if r.endswith("mysql_job_xp_shared_qualification_dispatch_v1.py"):
            if data.count(OLD_BACKEND_SHA.encode())!=1: raise BuildError("dispatcher backend hash marker differs")
            data=data.replace(OLD_BACKEND_SHA.encode(),NEW_BACKEND_SHA.encode())
        if r.endswith("mysql_job_v13_qualification_authority_v2.py"): data=patch_authority(data)
        if r.endswith("mysql_job_v13_fresh_r0_runner_v1.py"): data=patch_runner(data)
        payload[r]=data;modes[r]=0o555 if r.endswith(".py") else 0o444
    payload[BUILDER]=Path(__file__).read_bytes();modes[BUILDER]=0o555
    payload[FRESH]=(ROOT/"shell/mysql_job_v14_fresh_attempt_materializer_v1.py").read_bytes();modes[FRESH]=0o555
    payload[INVALIDATION]=canonical({
        "schema_version":"mysql_job_6b55_failed_attempt_invalidation_v1",
        "status":"INVALIDATED_ZERO_CREDIT_NEVER_REUSABLE",
        "failed_unit":"ra-mysql-job-qual-solo-6b55-20260803t0427.service",
        "failed_at_local":"2026-08-03T12:29:56+08:00",
        "failure":"native SQL workload requires query duration",
        "failed_attempt_marker":"6b55-20260803t0427",
        "replacement_target_bundle_id":TARGET_ID,
        "fresh_attempt_required":True,"failed_attempt_reusable":False,
        "qualification_credit":0,"r0_credit":0,"candidate_credit":0,
        "top5_replay_credit":0,"accepted_rounds_credit":0,"postgresql_mutation":False,
    });modes[INVALIDATION]=0o444
    payload[TEST]=test_source();modes[TEST]=0o444
    dispatch_sha=sha(payload["qualification/mysql_job_xp_shared_qualification_dispatch_v1.py"])
    identity={k:v for k,v in predecessor.items() if k not in {"bundle_id","files"}}
    identity.update({
        "schema_version":SCHEMA,
        "status":"SEALED_JOB_V14_DURATION_CONTROL_SUCCESSOR_NOT_AUTHORITY_NOT_EXECUTED",
        "predecessor_support_bundle_id":PRED_ID,
        "predecessor_support_manifest_sha256":PRED_SHA,
        "target_job_bundle_id":TARGET_ID,
        "target_job_manifest_sha256":TARGET_SHA,
        "shared_dispatch_sha256":dispatch_sha,
        "failed_unit":"ra-mysql-job-qual-solo-6b55-20260803t0427.service",
        "failed_attempt_marker":"6b55-20260803t0427",
        "failed_attempt_reusable":False,"fresh_attempt_required":True,
        "fresh_attempt_ttl_max_seconds":900,
        "entrypoints":{
            "publish":"publisher/mysql_content_addressed_bundle_publish_v1.py",
            "materialize_fresh_attempt":FRESH,
            "materialize_qualification_authority":"controller/mysql_job_v13_qualification_authority_v2.py",
            "dispatch":"qualification/mysql_job_xp_shared_qualification_dispatch_v1.py",
            "fresh_r0":"qualification/mysql_job_v13_fresh_r0_runner_v1.py",
        },
        "remote_actions_performed":0,"qualification_credit":0,"r0_credit":0,
        "candidate_credit":0,"top5_replay_credit":0,"accepted_rounds_credit":0,
        "formal_launch_authorized":False,"formal_r0_authorized":False,
        "accepted_round_authorized":False,
        "files":{r:sha(data) for r,data in sorted(payload.items())},
    })
    bundle_id=sha(canonical(identity));manifest={**identity,"bundle_id":bundle_id}
    OUT.mkdir(parents=True,exist_ok=True);target=OUT/bundle_id
    if target.exists() or target.is_symlink():
        if json.loads((target/"bundle_manifest.json").read_text())!=manifest: raise BuildError("existing control bundle differs")
    else:
        stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
        try:
            for r,data in payload.items():
                p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);os.chmod(p,modes[r])
            raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n")
            freeze(stage);os.rename(stage,target)
        except BaseException:
            if stage.exists():shutil.rmtree(stage)
            raise
    print(json.dumps({"status":manifest["status"],"bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":digest(target/"bundle_manifest.json"),"shared_dispatch_sha256":dispatch_sha,"target_job_bundle_id":TARGET_ID,"fresh_attempt_entrypoint":str(target/FRESH),"publish_entrypoint":str(target/"publisher/mysql_content_addressed_bundle_publish_v1.py")},sort_keys=True))


if __name__=="__main__":
    try:main()
    except (BuildError,OSError,ValueError,KeyError) as exc:raise SystemExit("NOT_READY: %s"%exc)
