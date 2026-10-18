#!/usr/bin/env python3
"""Content-address the re-signable local XP node02 v10 JIT issuer.

No Docker, SSH, systemd, database, or workload action is performed here.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
V10_ID = "e2b809f690375604bf9bc8b767009ed43dfd8baec2fc75c0ddfe7d72f8447b9b"
V10_SHA = "da3c4b6b0996ef4bc7ca98085ee0c0c661297b0d5b13c315075a9a26ce1d2b5c"
V10 = ROOT / "artifacts/mysql_xp_node02_successor_v10" / V10_ID
OUT = ROOT / "artifacts/mysql_xp_node02_v10_jit_issuer_v1"
ISSUER = "issuer/mysql_xp_node02_v10_jit_issuer_v1.py"
MATERIALIZER = "materializer/mysql_xp_node02_v10_qualification_materializer_v1.py"
TEST = "tests/test_mysql_xp_node02_v10_jit_issuer_v1.py"


class BuildError(RuntimeError): pass

def canonical(value): return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
def sha(value): return hashlib.sha256(value).hexdigest()
def digest(path): return sha(Path(path).read_bytes())

def verify_v10():
    manifest_path = V10 / "bundle_manifest.json"; sidecar = V10 / "bundle_manifest.json.sha256"
    if (not manifest_path.is_file() or manifest_path.is_symlink() or digest(manifest_path) != V10_SHA
            or not sidecar.is_file() or sidecar.read_text(encoding="ascii") != V10_SHA + "  bundle_manifest.json\n"):
        raise BuildError("v10 successor manifest differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); unsigned=dict(manifest); unsigned.pop("bundle_id",None)
    if (manifest.get("bundle_id") != V10_ID or sha(canonical(unsigned)) != V10_ID
            or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v10"
            or manifest.get("formal_launch_authorized") is not False or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("v10 successor identity/authority differs")
    for rel, expected in manifest.get("files",{}).items():
        path=V10/rel
        if path.is_symlink() or not path.is_file() or digest(path)!=expected: raise BuildError("v10 closure differs: "+rel)
    return manifest

def materializer_source():
    text=(ROOT/"shell/mysql_xp_qualification_bootstrap_materials_v5.py").read_text(encoding="utf-8")
    old_id="2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"; old_sha="8cb77864c55b5b51d846abc736c5ec872cb0cdcf7883550e86652e5e190c6412"
    if text.count(old_id) != 2 or text.count(old_sha) != 1: raise BuildError("v10 materializer source cardinality differs")
    text=text.replace("XP v8 successor", "XP node02 v10 successor").replace(old_id,V10_ID).replace(old_sha,V10_SHA)
    text=text.replace("mysql_xp_formal10_launch_v8", "mysql_xp_node02_successor_v10").replace("mysql_xp_qualification_bootstrap_v8", "mysql_xp_node02_v10_qualification_bootstrap")
    text=text.replace("mysql-xp-v8-", "mysql-xp-v10-")
    text=text.replace('ROOT = Path(__file__).resolve().parents[1]\n', 'ROOT = Path(__file__).resolve().parents[1]\nARTIFACTS_ROOT = ROOT.parents[1]\n', 1)
    text=text.replace('ROOT / "artifacts/mysql_xp_node02_successor_v10/"', 'ARTIFACTS_ROOT / "mysql_xp_node02_successor_v10/"')
    text=text.replace('ROOT / "artifacts/mysql_xp_node02_v10_qualification_bootstrap"', 'ARTIFACTS_ROOT / "mysql_xp_node02_v10_qualification_bootstrap"')
    old='ROOT / "artifacts/worker_systemd_run_help_20260731/"\n    "node02_node04_systemd_run_help.stdout"'
    if old not in text: raise BuildError("v10 materializer fixture source differs")
    text=text.replace(old, 'ROOT / "authority/node02_systemd_run_help.stdout"')
    return text.encode("utf-8")

def issuer_source():
    text=(ROOT/"shell/mysql_xp_v9_jit_issuer_v1.py").read_text(encoding="utf-8")
    old_id="168f1505fa89a80dee9fb39533300b7e1e1a4ecce6e9d60af2704072e4cb67ac"; old_sha="02c85d1bc350fbd8a95a0e5007581fd16721402c6faec59f48ee89dd2151ca26"
    if text.count(old_id) != 1 or text.count(old_sha) != 1: raise BuildError("v10 issuer source cardinality differs")
    text=text.replace(old_id,V10_ID).replace(old_sha,V10_SHA)
    text=text.replace("V9_ID", "V10_ID").replace("V9_MANIFEST_SHA256", "V10_MANIFEST_SHA256").replace("verify_v9", "verify_v10")
    text=text.replace("mysql_xp_v9", "mysql_xp_node02_v10").replace("XP v9", "XP node02 v10").replace("v9 bundle", "v10 bundle").replace("child v9", "child v10")
    text=text.replace('"mysql_xp_distributed_formal10_launch_v3"', '"mysql_xp_node02_successor_bundle_v10"')
    text=text.replace('materializer/mysql_xp_node02_v10_qualification_materializer_v1.py', MATERIALIZER)
    return text.encode("utf-8")

def test_source():
    return b'''from __future__ import annotations\nfrom datetime import datetime,timezone\nimport importlib.util,json,os,tempfile\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_v10_issuer_binds_successor_and_keeps_zero_authority():\n p=ROOT/"issuer/mysql_xp_node02_v10_jit_issuer_v1.py"; s=importlib.util.spec_from_file_location("i",p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n manifest=json.loads((ROOT/"bundle_manifest.json").read_text()); assert manifest["v10_bundle_id"]==m.V10_ID and manifest["formal_launch_authorized"] is False\n with tempfile.TemporaryDirectory() as raw:\n  d=Path(raw)/"private"; d.mkdir(mode=0o700); t=d/"token"; t.write_text("I"*64+"\\n"); t.chmod(0o600); lease=Path(raw)/"lease.json"\n  value=m.prepare_lease(ROOT.parents[1]/"mysql_xp_node02_successor_v10"/m.V10_ID,t,lease,datetime.now(timezone.utc).replace(microsecond=0)); assert value["accepted_round_authorized"] is False\n'''

def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("issuer symlink prohibited")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode)&~0o222)
    os.chmod(root, stat.S_IMODE(Path(root).stat().st_mode)&~0o222)

def verify_bundle(path):
    manifest=json.loads((path/"bundle_manifest.json").read_text(encoding="utf-8")); unsigned=dict(manifest); unsigned.pop("bundle_id",None); actual={}
    for item in path.rglob("*"):
        if item.is_symlink(): raise BuildError("issuer symlink prohibited")
        if item.is_file():
            rel=item.relative_to(path).as_posix()
            if rel not in {"bundle_manifest.json","bundle_manifest.json.sha256"}: actual[rel]=digest(item)
    if (manifest.get("bundle_id")!=path.name or sha(canonical(unsigned))!=path.name or actual!=manifest.get("files")
            or manifest.get("v10_bundle_id")!=V10_ID or manifest.get("v10_bundle_manifest_sha256")!=V10_SHA
            or manifest.get("formal_launch_authorized") is not False or manifest.get("accepted_round_authorized") is not False): raise BuildError("v10 issuer immutable identity differs")
    return manifest

def main():
    verify_v10(); payload={ISSUER:issuer_source(),MATERIALIZER:materializer_source(),TEST:test_source(),"authority/v10_bundle_manifest.json":(V10/"bundle_manifest.json").read_bytes(),"authority/v10_bundle_manifest.json.sha256":(V10/"bundle_manifest.json.sha256").read_bytes(),"authority/v10_xp_executable_launch_contract_v3.json":(V10/"xp_executable_launch_contract_v3.json").read_bytes(),"authority/node02_dedicated_lane_binding_v1.json":(V10/"authority/node02_dedicated_lane_binding_v1.json").read_bytes(),"authority/node02_systemd_run_help.stdout":(V10/"authority/node02_systemd_run_help.stdout").read_bytes()}
    gate={"formal_scope":"mysql_only","database_cpu_quota_cores":5.3,"database_io_read_bps":29360128,"database_io_write_bps":524288,"capture_only_io_bps_per_direction":1205862400,"capture_accepted_round_credit":0,"required_nonempty_subsets":127,"required_trace_transactions":["AT1","AT2","AT3","AT4","AT5","AT6"],"authorization_outside_attempt_required":True,"private_directory_mode":"0700","private_file_mode":"0600"}
    identity={"schema_version":"mysql_xp_node02_v10_jit_issuer_bundle_v1","status":"SEALED_LOCAL_ISSUER_NO_FORMAL_ATTEMPT_GENERATED","v10_bundle_id":V10_ID,"v10_bundle_manifest_sha256":V10_SHA,"issuer_valid_minutes":60,"child_runtime_valid_minutes_default":25,"child_runtime_valid_minutes_max":30,"runtime_60min_prohibited_by_sealed_service":True,"runtime_service_max_seconds":1800,"just_in_time_child_generation":True,"formal_attempt_generated_by_build":False,"remote_actions":0,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"gate_binding":gate,"files":{k:sha(v) for k,v in sorted(payload.items())}}
    bid=sha(canonical(identity)); manifest={**identity,"bundle_id":bid}; OUT.mkdir(parents=True,exist_ok=True); target=OUT/bid
    if target.exists() or target.is_symlink(): freeze(target); verify_bundle(target)
    else:
        stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for rel,value in payload.items(): p=stage/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(value); os.chmod(p,0o444)
            raw=canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw); (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii"); freeze(stage); os.rename(stage,target)
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            raise
        verify_bundle(target)
    print(json.dumps({"status":"SEALED_LOCAL_ISSUER_NO_FORMAL_ATTEMPT_GENERATED","bundle":str(target),"bundle_id":target.name,"bundle_manifest_sha256":digest(target/"bundle_manifest.json"),"formal_launch_authorized":False,"accepted_round_authorized":False,"remote_actions":0},sort_keys=True))

if __name__=="__main__":
    try: main()
    except (OSError,ValueError,KeyError,RuntimeError) as exc: sys.stderr.write("NOT_READY: "+str(exc)+"\n"); raise SystemExit(2)
