#!/usr/bin/env python3
"""Content-address the re-signable local XP node02 v11 JIT issuer."""
from __future__ import annotations

import hashlib,json,os,secrets,shutil,stat,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V11_ID="f910d2246b38b9719136ea015ad843733a16bbdd6b9666133b997d73bfc71c78"
V11_SHA="67773059a5a5a11424ea22f3bcab82a3d39113913a61ab4e3181e91c629abfd6"
V11=ROOT/"artifacts/mysql_xp_node02_successor_v11"/V11_ID
PARENT_ISSUER=ROOT/"artifacts/mysql_xp_node02_v10_jit_issuer_v1/83782ec14b0edc267479ad204b7c3924d46bac284d439636813c27816c69cf74"
OUT=ROOT/"artifacts/mysql_xp_node02_v11_jit_issuer_v1"
ISSUER="issuer/mysql_xp_node02_v11_jit_issuer_v1.py"
MATERIALIZER="materializer/mysql_xp_node02_v11_qualification_materializer_v1.py"
TEST="tests/test_mysql_xp_node02_v11_jit_issuer_v1.py"

class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def digest(p):return sha(Path(p).read_bytes())

def verify_v11():
 p=V11/"bundle_manifest.json";s=V11/"bundle_manifest.json.sha256";m=json.loads(p.read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=V11_ID or digest(p)!=V11_SHA or s.read_text()!=V11_SHA+"  bundle_manifest.json\n" or sha(canonical(u))!=V11_ID or m.get("schema_version")!="mysql_xp_node02_successor_bundle_v11":raise BuildError("v11 successor identity differs")
 for r,h in m["files"].items():
  q=V11/r
  if q.is_symlink() or not q.is_file() or digest(q)!=h:raise BuildError("v11 closure differs: "+r)
 return m

def materializer_source():
 p=V11/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py";t=p.read_text()
 old_id="2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087";old_sha="8cb77864c55b5b51d846abc736c5ec872cb0cdcf7883550e86652e5e190c6412"
 if t.count(old_id)!=1 or t.count(old_sha)!=1 or "--property=CPUAffinity=" in t or t.count("ra-mysql-xp-v11-")!=3:raise BuildError("v11 materializer compatibility closure differs")
 t=t.replace(old_id,V11_ID).replace(old_sha,V11_SHA).replace("XP v8 successor","XP node02 v11 successor")
 return t.encode()

def issuer_source():
 p=PARENT_ISSUER/"issuer/mysql_xp_node02_v10_jit_issuer_v1.py";t=p.read_text()
 old_id="e2b809f690375604bf9bc8b767009ed43dfd8baec2fc75c0ddfe7d72f8447b9b";old_sha="da3c4b6b0996ef4bc7ca98085ee0c0c661297b0d5b13c315075a9a26ce1d2b5c"
 if t.count(old_id)!=1 or t.count(old_sha)!=1:raise BuildError("v10 issuer source identity differs")
 t=t.replace(old_id,V11_ID).replace(old_sha,V11_SHA).replace("V10_ID","V11_ID").replace("V10_MANIFEST_SHA256","V11_MANIFEST_SHA256").replace("verify_v10","verify_v11")
 t=t.replace("mysql_xp_node02_v10","mysql_xp_node02_v11").replace("XP node02 v10","XP node02 v11").replace("v10 bundle","v11 bundle").replace("child v10","child v11")
 t=t.replace('"mysql_xp_node02_successor_bundle_v10"','"mysql_xp_node02_successor_bundle_v11"').replace("materializer/mysql_xp_node02_v10_qualification_materializer_v1.py",MATERIALIZER)
 return t.encode()

TEST_SOURCE=b'''from __future__ import annotations\nfrom datetime import datetime,timezone\nimport importlib.util,json,tempfile\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_v11_issuer_and_systemd_plan():\n p=ROOT/"issuer/mysql_xp_node02_v11_jit_issuer_v1.py";s=importlib.util.spec_from_file_location("i",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n manifest=json.loads((ROOT/"bundle_manifest.json").read_text()); assert manifest["v11_bundle_id"]==m.V11_ID\n with tempfile.TemporaryDirectory() as raw:\n  d=Path(raw)/"private";d.mkdir(mode=0o700);t=d/"token";t.write_text("I"*64+"\\n");t.chmod(0o600);lease=Path(raw)/"lease.json"\n  v=m.prepare_lease(ROOT.parents[1]/"mysql_xp_node02_successor_v11"/m.V11_ID,t,lease,datetime.now(timezone.utc).replace(microsecond=0));assert v["accepted_round_authorized"] is False\n material=(ROOT/"materializer/mysql_xp_node02_v11_qualification_materializer_v1.py").read_text();assert "ra-mysql-xp-v11-" in material and "--property=CPUAffinity=" not in material\n'''

def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):
  if p.is_symlink():raise BuildError("issuer symlink prohibited")
  os.chmod(p,stat.S_IMODE(p.stat().st_mode)&~0o222)
 os.chmod(root,stat.S_IMODE(root.stat().st_mode)&~0o222)

def main():
 verify_v11();payload={ISSUER:issuer_source(),MATERIALIZER:materializer_source(),TEST:TEST_SOURCE,"authority/v11_bundle_manifest.json":(V11/"bundle_manifest.json").read_bytes(),"authority/v11_bundle_manifest.json.sha256":(V11/"bundle_manifest.json.sha256").read_bytes(),"authority/v11_xp_executable_launch_contract_v3.json":(V11/"xp_executable_launch_contract_v3.json").read_bytes(),"authority/node02_dedicated_lane_binding_v1.json":(V11/"authority/node02_dedicated_lane_binding_v1.json").read_bytes(),"authority/node02_systemd_run_help.stdout":(V11/"authority/node02_systemd_run_help.stdout").read_bytes()}
 gate={"formal_scope":"mysql_only","database_cpu_quota_cores":5.3,"database_io_read_bps":29360128,"database_io_write_bps":524288,"capture_only_io_bps_per_direction":1205862400,"capture_accepted_round_credit":0,"required_nonempty_subsets":127,"required_trace_transactions":["AT1","AT2","AT3","AT4","AT5","AT6"],"authorization_outside_attempt_required":True,"private_directory_mode":"0700","private_file_mode":"0600"}
 identity={"schema_version":"mysql_xp_node02_v11_jit_issuer_bundle_v1","status":"SEALED_LOCAL_ISSUER_NO_FORMAL_ATTEMPT_GENERATED","v11_bundle_id":V11_ID,"v11_bundle_manifest_sha256":V11_SHA,"issuer_valid_minutes":60,"child_runtime_valid_minutes_default":25,"child_runtime_valid_minutes_max":30,"runtime_60min_prohibited_by_sealed_service":True,"runtime_service_max_seconds":1800,"just_in_time_child_generation":True,"formal_attempt_generated_by_build":False,"remote_actions":0,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"gate_binding":gate,"files":{k:sha(v) for k,v in sorted(payload.items())}}
 bid=sha(canonical(identity));manifest={**identity,"bundle_id":bid};OUT.mkdir(parents=True,exist_ok=True);target=OUT/bid
 if target.exists() or target.is_symlink():raise BuildError("immutable v11 issuer already exists")
 stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  for r,v in payload.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v);os.chmod(p,0o444)
  raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,target)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 print(json.dumps({"status":identity["status"],"bundle":str(target),"bundle_id":bid,"bundle_manifest_sha256":digest(target/"bundle_manifest.json"),"formal_launch_authorized":False,"accepted_round_authorized":False,"remote_actions":0},sort_keys=True))

if __name__=="__main__":
 try:main()
 except (OSError,ValueError,KeyError,RuntimeError) as exc:sys.stderr.write("NOT_READY: "+str(exc)+"\n");raise SystemExit(2)
