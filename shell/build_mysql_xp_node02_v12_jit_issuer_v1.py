#!/usr/bin/env python3
"""Content-address the re-signable local XP node02 v12 JIT issuer."""
from __future__ import annotations

import hashlib, json, os, secrets, shutil, stat, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V12_ID = "c78c6f8bbaf9af51be185c435a3cc72af32d3d7a903cbcc542ecb72fc4e00aab"
V12_SHA = "f91ecd949113397748ea40fde6057b4e8d456406dea72eee4d9d41674433f4ab"
V12 = ROOT / "artifacts/mysql_xp_node02_successor_v12" / V12_ID
PARENT = ROOT / "artifacts/mysql_xp_node02_v11_jit_issuer_v1/8f0802b3a045defeaaabdc1aa8f440c076df9422e39a9451131c7171179eccf4"
OUT = ROOT / "artifacts/mysql_xp_node02_v12_jit_issuer_v1"
ISSUER = "issuer/mysql_xp_node02_v12_jit_issuer_v1.py"
MATERIALIZER = "materializer/mysql_xp_node02_v12_qualification_materializer_v1.py"
TEST = "tests/test_mysql_xp_node02_v12_jit_issuer_v1.py"


class BuildError(RuntimeError): pass
def canonical(v): return (json.dumps(v, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)+"\n").encode()
def sha(v): return hashlib.sha256(v).hexdigest()
def digest(p): return sha(Path(p).read_bytes())


def verify_v12():
    p=V12/"bundle_manifest.json"; s=V12/"bundle_manifest.json.sha256"; m=json.loads(p.read_text()); u=dict(m); u.pop("bundle_id",None)
    if m.get("bundle_id")!=V12_ID or digest(p)!=V12_SHA or s.read_text()!=V12_SHA+"  bundle_manifest.json\n" or sha(canonical(u))!=V12_ID or m.get("schema_version")!="mysql_xp_node02_successor_bundle_v12": raise BuildError("v12 successor identity differs")
    for r,h in m["files"].items():
        q=V12/r
        if q.is_symlink() or not q.is_file() or digest(q)!=h: raise BuildError("v12 closure differs: "+r)
    return m


def materializer_source():
    p=V12/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py"; t=p.read_text()
    old_id="2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"; old_sha="8cb77864c55b5b51d846abc736c5ec872cb0cdcf7883550e86652e5e190c6412"
    if t.count(old_id)!=1 or t.count(old_sha)!=1 or "--property=CPUAffinity=" in t or t.count("ra-mysql-xp-v12-")!=3: raise BuildError("v12 materializer compatibility closure differs")
    return t.replace(old_id,V12_ID).replace(old_sha,V12_SHA).replace("XP v8 successor","XP node02 v12 successor").encode()


def issuer_source():
    p=PARENT/"issuer/mysql_xp_node02_v11_jit_issuer_v1.py"; t=p.read_text()
    old_id="f910d2246b38b9719136ea015ad843733a16bbdd6b9666133b997d73bfc71c78"; old_sha="67773059a5a5a11424ea22f3bcab82a3d39113913a61ab4e3181e91c629abfd6"
    if t.count(old_id)!=1 or t.count(old_sha)!=1: raise BuildError("v11 issuer source identity differs")
    t=t.replace(old_id,V12_ID).replace(old_sha,V12_SHA).replace("V11_ID","V12_ID").replace("V11_MANIFEST_SHA256","V12_MANIFEST_SHA256").replace("verify_v11","verify_v12")
    t=t.replace("mysql_xp_node02_v11","mysql_xp_node02_v12").replace("XP node02 v11","XP node02 v12").replace("v11 bundle","v12 bundle").replace("child v11","child v12")
    t=t.replace('"mysql_xp_node02_successor_bundle_v11"','"mysql_xp_node02_successor_bundle_v12"').replace("materializer/mysql_xp_node02_v11_qualification_materializer_v1.py",MATERIALIZER)
    return t.encode()


TEST_SOURCE=b'''from __future__ import annotations
from datetime import datetime,timezone
import importlib.util,json,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v12_issuer_and_systemd_plan():
 p=ROOT/"issuer/mysql_xp_node02_v12_jit_issuer_v1.py";s=importlib.util.spec_from_file_location("i",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
 manifest=json.loads((ROOT/"bundle_manifest.json").read_text()); assert manifest["v12_bundle_id"]==m.V12_ID
 with tempfile.TemporaryDirectory() as raw:
  d=Path(raw)/"private";d.mkdir(mode=0o700);t=d/"token";t.write_text("I"*64+"\\n");t.chmod(0o600);lease=Path(raw)/"lease.json"
  v=m.prepare_lease(ROOT.parents[1]/"mysql_xp_node02_successor_v12"/m.V12_ID,t,lease,datetime.now(timezone.utc).replace(microsecond=0));assert v["accepted_round_authorized"] is False
 material=(ROOT/"materializer/mysql_xp_node02_v12_qualification_materializer_v1.py").read_text();assert "ra-mysql-xp-v12-" in material and "--property=CPUAffinity=" not in material
'''


def freeze(root):
    for p in sorted(root.rglob("*"),reverse=True):
        if p.is_symlink(): raise BuildError("issuer symlink prohibited")
        os.chmod(p,stat.S_IMODE(p.stat().st_mode)&~0o222)
    os.chmod(root,stat.S_IMODE(root.stat().st_mode)&~0o222)


def main():
    verify_v12()
    payload={ISSUER:issuer_source(),MATERIALIZER:materializer_source(),TEST:TEST_SOURCE,
             "authority/v12_bundle_manifest.json":(V12/"bundle_manifest.json").read_bytes(),
             "authority/v12_bundle_manifest.json.sha256":(V12/"bundle_manifest.json.sha256").read_bytes(),
             "authority/v12_xp_executable_launch_contract_v3.json":(V12/"xp_executable_launch_contract_v3.json").read_bytes(),
             "authority/node02_dedicated_lane_binding_v1.json":(V12/"authority/node02_dedicated_lane_binding_v1.json").read_bytes(),
             "authority/node02_systemd_run_help.stdout":(V12/"authority/node02_systemd_run_help.stdout").read_bytes()}
    gate={"formal_scope":"mysql_only","database_cpu_quota_cores":5.3,"database_io_read_bps":29360128,"database_io_write_bps":524288,"capture_only_io_bps_per_direction":1205862400,"capture_accepted_round_credit":0,"required_nonempty_subsets":127,"required_trace_transactions":["AT1","AT2","AT3","AT4","AT5","AT6"],"authorization_outside_attempt_required":True,"private_directory_mode":"0700","private_file_mode":"0600"}
    identity={"schema_version":"mysql_xp_node02_v12_jit_issuer_bundle_v1","status":"SEALED_LOCAL_ISSUER_NO_FORMAL_ATTEMPT_GENERATED","v12_bundle_id":V12_ID,"v12_bundle_manifest_sha256":V12_SHA,"issuer_valid_minutes":60,"child_runtime_valid_minutes_default":25,"child_runtime_valid_minutes_max":30,"runtime_60min_prohibited_by_sealed_service":True,"runtime_service_max_seconds":1800,"just_in_time_child_generation":True,"formal_attempt_generated_by_build":False,"remote_actions":0,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"gate_binding":gate,"files":{k:sha(v) for k,v in sorted(payload.items())}}
    bid=sha(canonical(identity)); manifest={**identity,"bundle_id":bid}; OUT.mkdir(parents=True,exist_ok=True); target=OUT/bid
    if target.exists() or target.is_symlink(): raise BuildError("immutable v12 issuer already exists")
    stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for r,v in payload.items(): p=stage/r; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(v); os.chmod(p,0o444)
        raw=canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw); (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n"); freeze(stage); os.rename(stage,target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status":identity["status"],"bundle":str(target),"bundle_id":bid,"bundle_manifest_sha256":digest(target/"bundle_manifest.json"),"formal_launch_authorized":False,"accepted_round_authorized":False,"remote_actions":0},sort_keys=True))


if __name__=="__main__":
    try: main()
    except (OSError,ValueError,KeyError,RuntimeError) as exc: sys.stderr.write("NOT_READY: "+str(exc)+"\n"); raise SystemExit(2)
