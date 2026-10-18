#!/usr/bin/env python3
"""Fail-closed adapter that makes the a336 gate mandatory for v16 typed capture."""
from __future__ import annotations

import argparse, hashlib, json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

V16_ID = "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
V16_SHA = "e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
JOINT_ID = "765c4fa3343b3e78254c102e1824f206c94f70f94b6e94d2d70bcd1a167f7897"
JOINT_SHA = "1a592f08b55c3e8bc1edf07e42a7be5540abba6a94b5bbbc356cff0f19d2a0c2"
GATE_ID = "a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723"
GATE_SHA = "a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0"
ATTEMPT = "tpcc-v16-7e2e13aa-20260801t121500p08"
HEX = re.compile(r"^[0-9a-f]{64}$")

class AuthorityError(RuntimeError): pass

def canonical(v: Any) -> bytes:
    return (json.dumps(v, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()

def sha(p: Path) -> str:
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest()

def sealed(p: Path, label: str) -> tuple[Path,dict[str,Any]]:
    p=Path(p).resolve(strict=True); side=Path(str(p)+".sha256")
    if p.is_symlink() or not p.is_file() or not side.is_file() or side.read_text(encoding="ascii") != f"{sha(p)}  {p.name}\n":
        raise AuthorityError(label+" seal differs")
    value=json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise AuthorityError(label+" object required")
    return p,value

def bundle(root: Path, identity: str, digest: str, label: str) -> dict[str,Any]:
    root=Path(root).resolve(strict=True); path,value=sealed(root/"bundle_manifest.json",label)
    unsigned=dict(value); unsigned.pop("bundle_id",None)
    compact=json.dumps(unsigned,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
    if root.name!=identity or value.get("bundle_id")!=identity or sha(path)!=digest or identity not in {hashlib.sha256(canonical(unsigned)).hexdigest(),hashlib.sha256(compact).hexdigest()}:
        raise AuthorityError(label+" identity differs")
    if value.get("formal_scope")!="mysql_only" or value.get("cross_engine_comparison_prohibited") is not True or value.get("postgresql_mutation_prohibited") is not True:
        raise AuthorityError(label+" scope differs")
    return value

def reject_qps(v: Any, where="authority") -> None:
    if isinstance(v,Mapping):
        for k,x in v.items():
            if str(k).lower() in {"qps","throughput","transactions_per_second"}: raise AuthorityError("QPS prohibited: "+where)
            reject_qps(x,where+"."+str(k))
    elif isinstance(v,list):
        for i,x in enumerate(v): reject_qps(x,f"{where}[{i}]")

def adapt(*, v16_bundle:Path, joint_bundle:Path, gate_bundle:Path, joint_completion:Path,
          inner_completion:Path, snapshot:Path, capture_gate:Path, materialized_command:Path) -> dict[str,Any]:
    bundle(v16_bundle,V16_ID,V16_SHA,"v16 bundle"); bundle(joint_bundle,JOINT_ID,JOINT_SHA,"joint bundle"); bundle(gate_bundle,GATE_ID,GATE_SHA,"a336 bundle")
    jp,j=sealed(joint_completion,"joint completion"); ip,inner=sealed(inner_completion,"inner completion")
    sp,snap=sealed(snapshot,"fresh snapshot"); gp,gate=sealed(capture_gate,"capture gate"); cp,cmd=sealed(materialized_command,"materialized command")
    for label,value in (("joint",j),("inner",inner),("snapshot",snap),("gate",gate),("command",cmd)): reject_qps(value,label)
    if (j.get("schema_version")!="mysql_tpcc_joint_four_flow_completion_v1" or j.get("status")!="PASS_ALL_INPUTS_AND_FOUR_FLOWS_BOUND_ZERO_CREDIT"
        or j.get("inner_completion_sha256")!=sha(ip) or j.get("inner_evidence_sha256")!=inner.get("evidence_sha256")
        or j.get("inner_dynamic_gate_sha256")!=inner.get("dynamic_gate_sha256") or j.get("formal_scope")!="mysql_only"
        or j.get("postgresql_mutation_prohibited") is not True or j.get("accepted_round_authorized") is not False or j.get("accepted_rounds_credit")!=0
        or inner.get("status")!="PASS_FOUR_FLOWS_FRESH_GATE_ZERO_CREDIT"):
        raise AuthorityError("joint/inner completion closure differs")
    try: age=(datetime.now(timezone.utc)-datetime.fromisoformat(str(snap["collected_at_utc"]).replace("Z","+00:00"))).total_seconds()
    except (KeyError,ValueError) as e: raise AuthorityError("snapshot time differs") from e
    if age < -5 or age > 120: raise AuthorityError("snapshot is not fresh")
    inputs=gate.get("inputs") or {}
    if (snap.get("schema_version")!="mysql_tpcc_v16_same_live_readback_v1" or snap.get("status")!="PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY"
        or snap.get("bundle_id")!=V16_ID or snap.get("attempt_id")!=ATTEMPT or snap.get("postgresql_mutation_prohibited") is not True
        or gate.get("schema_version")!="mysql_tpcc_v16_same_live_typed_gate_authority_v1" or gate.get("status")!="PASS_SAME_LIVE_TYPED_CAPTURE_READY_ZERO_CREDIT"
        or gate.get("bundle_id")!=V16_ID or gate.get("attempt_id")!=ATTEMPT or gate.get("capture_authorized") is not True
        or gate.get("registry_ready") is not False or gate.get("collector_validation_sha256") is not None or gate.get("algorithm_c_unchanged") is not True
        or gate.get("accepted_round_authorized") is not False or gate.get("accepted_rounds_credit")!=0
        or inputs.get("same_live_snapshot_sha256")!=sha(sp) or gate.get("same_live_identity")!={"database":snap.get("database"),"client":snap.get("client")}
        or inputs.get("dynamic_gate_sha256")!=j.get("inner_dynamic_gate_sha256")
        or inputs.get("flows")!=j.get("inner_evidence_sha256")):
        raise AuthorityError("fresh a336 gate/joint closure differs")
    unsigned=dict(cmd); old=unsigned.pop("command_spec_sha256",None)
    if (old!=hashlib.sha256(canonical(unsigned)).hexdigest() or cmd.get("schema_version")!="mysql_tpcc_materialized_phase_command_v1"
        or cmd.get("status")!="SEALED_CAPTURE_COMMAND" or cmd.get("phase")!="typed_capture" or cmd.get("bundle_id")!=V16_ID
        or cmd.get("attempt_id")!=ATTEMPT or cmd.get("capture_only") is not True or cmd.get("formal_authorized") is not False
        or cmd.get("accepted_round_authorized") is not False or cmd.get("qps_resource_input_prohibited") is not True
        or cmd.get("db_identity")!={k:snap["database"].get(k) for k in ("container_id","pid","process_start_ticks","datadir","device_major_minor","cpuset_cpus","cpuset_mems","mysql_port")}
        or any(cmd.get("client_container",{}).get(k)!=snap["client"].get(k) for k in ("container_id","pid","process_start_ticks"))):
        raise AuthorityError("typed materialized command differs")
    out=dict(cmd); out.pop("command_spec_sha256",None)
    out["capture_authority_binding"]={"schema_version":"mysql_tpcc_v16_capture_authority_binding_v1","v16_manifest_sha256":V16_SHA,
      "joint_bundle_id":JOINT_ID,"joint_manifest_sha256":JOINT_SHA,"a336_bundle_id":GATE_ID,"a336_manifest_sha256":GATE_SHA,
      "joint_completion_sha256":sha(jp),"inner_completion_sha256":sha(ip),"same_live_snapshot_sha256":sha(sp),
      "capture_gate_authority_sha256":sha(gp),"source_command_spec_sha256":old,"algorithm_c_unchanged":True,
      "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,
      "capture_authorized":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
    reject_qps(out); out["command_spec_sha256"]=hashlib.sha256(canonical(out)).hexdigest(); return out

def cli() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    for n in ("v16_bundle","joint_bundle","gate_bundle","joint_completion","inner_completion","snapshot","capture_gate","materialized_command","output"):
        p.add_argument("--"+n.replace("_","-"),type=Path,required=True)
    a=p.parse_args(); out=adapt(v16_bundle=a.v16_bundle,joint_bundle=a.joint_bundle,gate_bundle=a.gate_bundle,joint_completion=a.joint_completion,
      inner_completion=a.inner_completion,snapshot=a.snapshot,capture_gate=a.capture_gate,materialized_command=a.materialized_command)
    if a.output.exists(): raise AuthorityError("refusing to overwrite output")
    a.output.parent.mkdir(parents=True,exist_ok=True); raw=canonical(out); a.output.write_bytes(raw); os.chmod(a.output,0o400)
    side=Path(str(a.output)+".sha256"); side.write_text(f"{hashlib.sha256(raw).hexdigest()}  {a.output.name}\n",encoding="ascii"); os.chmod(side,0o400)
    print(json.dumps({"status":"PASS_CAPTURE_COMMAND_AUTHORITY_BOUND_ZERO_CREDIT","output":str(a.output),"command_spec_sha256":out["command_spec_sha256"],"accepted_rounds_credit":0},sort_keys=True)); return 0

if __name__=="__main__":
    try: raise SystemExit(cli())
    except (AuthorityError,OSError,ValueError,KeyError) as e: sys.stderr.write("NOT_READY: %s\n"%e); raise SystemExit(2)
