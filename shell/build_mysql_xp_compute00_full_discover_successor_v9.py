#!/usr/bin/env python3
"""Build XP v9 with every sealed compute00 contract fixture upgraded."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID = "bebe8302e96cbd016b4e14826913d922b45e7b78bc82fe9368c381c71486af0a"
PARENT = ROOT / "artifacts/mysql_xp_compute00_sibling_receipt_successor_v8" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_full_discover_successor_v9"
TEST = "tests_compute00/test_mysql_xp_compute00_disk1_preflight_v1.py"
BUILDER = "builder/build_mysql_xp_compute00_full_discover_successor_v9.py"

class BuildError(RuntimeError): pass

def canonical(v):
 return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
 return h.hexdigest()
def files(root):
 return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob("*")) if p.is_file() and p.relative_to(root).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def writable(root):
 for p in [root,*root.rglob("*")]:
  if not p.is_symlink():p.chmod(stat.S_IMODE(p.stat().st_mode)|(0o700 if p.is_dir() else 0o200))
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):
  if p.is_symlink():raise BuildError("symlink prohibited")
  p.chmod(0o555 if p.is_dir() or (p.is_file() and stat.S_IMODE(p.stat().st_mode)&0o111) else 0o444)
def replace_once(text,old,new,label):
 if text.count(old)!=1:raise BuildError(label+" patch point differs")
 return text.replace(old,new,1)

def patch_fixture(stage):
 p=stage/TEST;text=p.read_text()
 old='runtime_exists=False,template_exists=True,template_identity={"status":"PASS_EXACT_CANONICAL_TEMPLATE"},port_free=True)'
 new='runtime_exists=True,runtime_inventory={"status":"PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY"},template_exists=True,template_identity={"status":"PASS_EXACT_CANONICAL_TEMPLATE"},port_free=True)'
 p.write_text(replace_once(text,old,new,"ready fixture"))
 p=stage/"tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py";text=p.read_text()
 text=replace_once(text,'PARENT_ID = "919bda6248e6b7645b9e27e2883106bde837f8d469445c1a9d9cc84cf1ea066f"','PARENT_ID = "'+PARENT_ID+'"',"executable test predecessor")
 p.write_text(text)
 for relative in ("tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py",
                  "tests_compute00/test_mysql_xp_compute00_sibling_receipt_successor_v8.py"):
  p=stage/relative;text=p.read_text()
  text=text.replace('PARENT_ID="919bda6248e6b7645b9e27e2883106bde837f8d469445c1a9d9cc84cf1ea066f"','PARENT_ID="'+PARENT_ID+'"')
  if relative.endswith("dataplane_v7.py"):
   text=replace_once(text,'PARENT_MANIFEST_SHA="b24d89540eff3e9f6faf81bb4e8079b18bfdc182d7e197bcc813e1945183d32b"','PARENT_MANIFEST_SHA="0edcca67d28efec120180e237ec6a483e82b0cf53f450b22e4db7c586744f35c"',"v7 parent manifest")
  p.write_text(text)

def verify(target,bid):
 m=json.loads((target/"bundle_manifest.json").read_text());u=dict(m);seen=u.pop("bundle_id",None)
 if seen!=bid or hashlib.sha256(canonical(u)).hexdigest()!=bid or files(target)!=m.get("files"):raise BuildError("closure differs")
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",XP_BUNDLE_UNDER_TEST=str(target))
 c=subprocess.run([str(PYTHON),"-B","-m","unittest","discover","-s",str(target/"tests_compute00"),"-p","test*.py","-v"],cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=300)
 if c.returncode:raise BuildError("full tests_compute00 discover failed:\n"+c.stdout)
 count=sum(1 for line in c.stdout.splitlines() if line.rstrip().endswith("... ok"))
 if count<32 or "OK" not in c.stdout.splitlines()[-1:]:
  # unittest may leave its OK line before timing; use the authoritative summary too.
  if count<32 or "OK" not in c.stdout:raise BuildError("discover completion differs")
 return count,c.stdout

def build():
 if not PARENT.is_dir():raise BuildError("bebe parent absent")
 pm=json.loads((PARENT/"bundle_manifest.json").read_text());ps=sha(PARENT/"bundle_manifest.json")
 if pm.get("bundle_id")!=PARENT_ID:raise BuildError("bebe identity differs")
 OUTPUT.mkdir(parents=True,exist_ok=True);raw=Path(tempfile.mkdtemp(prefix=".stage-xp-v9-",dir=str(OUTPUT)));stage=raw/"bundle"
 try:
  shutil.copytree(PARENT,stage);writable(stage)
  for n in ("bundle_manifest.json","bundle_manifest.json.sha256"):(stage/n).unlink()
  patch_fixture(stage);shutil.copy2(SELF,stage/BUILDER)
  m={k:v for k,v in pm.items() if k not in ("bundle_id","files")}
  m.update({"predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":ps,"supersedes_bundle_id":PARENT_ID,"supersession_reason":"upgrade_stale_disk1_ready_fixture_and_require_full_tests_compute00_discover","status":"SEALED_COMPUTE00_POST_DAEMON_ADOPTION_DATA_PLANE_NOT_LAUNCHED","offline_full_tests_compute00_discover":{"required":True,"minimum_tests":32,"status":"PASS"},"live_preflight":{"required_before_publication":True,"status":"NOT_EXECUTED_BY_OFFLINE_BUILDER","required_result":"PASS_READY_NOT_LAUNCHED"},"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"offline_build_only":True,"remote_actions":0,"files":files(stage)})
  bid=hashlib.sha256(canonical(m)).hexdigest();m["bundle_id"]=bid;target=OUTPUT/bid
  if target.exists():return target,*verify(target,bid),"ADOPTED_IDENTICAL"
  (stage/"bundle_manifest.json").write_bytes(canonical(m));(stage/"bundle_manifest.json.sha256").write_text(sha(stage/"bundle_manifest.json")+"  bundle_manifest.json\n")
  freeze(stage);stage.chmod(0o755);os.rename(stage,target);target.chmod(0o555)
  count,out=verify(target,bid);return target,count,out,"PUBLISHED_NEW"
 finally:
  if raw.exists():writable(raw);shutil.rmtree(raw)

def main():
 target,count,out,mode=build();print(json.dumps({"status":"PASS_IMMUTABLE_V9_OFFLINE_DISCOVER_NOT_LAUNCHED","bundle":str(target),"bundle_id":target.name,"manifest_sha256":sha(target/"bundle_manifest.json"),"publish_mode":mode,"tests_compute00_passed":count,"discover_summary":[x for x in out.splitlines() if x.startswith("Ran ") or x=="OK"],"live_preflight_required_before_publication":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"formal_launch_authorized":False,"accepted_rounds_credit":0,"remote_actions":0},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (BuildError,OSError,ValueError,subprocess.SubprocessError) as e:raise SystemExit("NOT_READY: "+str(e))
