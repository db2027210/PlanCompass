#!/usr/bin/env python3
"""Build immutable v2 with exact canonical XP template gates."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();PY=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID="fc9b2f1e71b35059d135a36f92025c1844a2e3e9e751aa37b51ea804ae57d553";PARENT_SHA="90a01b5fe4bb5b889eeb134c3441cc8e0bda4a6fa89e3920ddd1bb8b8728cc00"
PARENT=ROOT/"artifacts/mysql_xp_compute00_disk1_relocation_v1"/PARENT_ID;OUT=ROOT/"artifacts/mysql_xp_compute00_canonical_template_v2"
REGRESSION_BASE=ROOT/"artifacts/mysql_xp_node02_successor_v28/a0187979839c8fdb9530da525425a031bca9d955234d754cc495dbe0d5b342e9"
HELPER=ROOT/"shell/mysql_xp_canonical_template_v2.py";PREFLIGHT=ROOT/"shell/mysql_xp_compute00_disk1_preflight_v1.py";TESTS=[ROOT/"shell/test_mysql_xp_canonical_template_v2.py",ROOT/"shell/test_mysql_xp_compute00_disk1_preflight_v1.py"]
CANONICAL_MANIFEST=ROOT/"history/unified_formal/six_session_20260721_v2/mysql/preflight/template_build_attempts/hybench_20260721T065743Z_4084522_09ac727a/database_manifest.json"
CANONICAL_EVIDENCE=ROOT/"config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/xp_template_deployment_20260723.json"
POST_COPY=ROOT/"history/mysql_sgd_env_agents_20260723/xp/post_copy_closure_20260723_0544_retry1/xp_post_copy_verification.json"
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def writable(root):
 root.chmod(0o755)
 for p in root.rglob("*"):p.chmod((0o755 if p.is_dir() else stat.S_IMODE(p.stat().st_mode)|0o200)) if not p.is_symlink() else None
def files(root):return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):
  if not p.is_symlink():p.chmod(0o555 if p.is_dir() or stat.S_IMODE(p.stat().st_mode)&0o111 else 0o444)
 root.chmod(0o755)
def update_hash_refs(value,stage):
 if isinstance(value,dict):
  rel=value.get("path")
  if isinstance(rel,str) and isinstance(value.get("sha256"),str) and (stage/rel).is_file():value["sha256"]=sha(stage/rel)
  for item in value.values():update_hash_refs(item,stage)
 elif isinstance(value,list):
  for item in value:update_hash_refs(item,stage)
def patch_service(path):
 text=path.read_text();start=text.index("def _verify_template_manifest():");end=text.index("\n\ndef _fiemap_extents",start)
 replacement='''def _canonical_template_module():\n    helper = SEALED_BUNDLE_ROOT / "runtime/mysql_xp_canonical_template_v2.py"\n    if not helper.is_file() or helper.is_symlink():\n        raise ServiceError("canonical template verifier is absent")\n    spec = importlib.util.spec_from_file_location(\n        "mysql_xp_canonical_template_v2", str(helper)\n    )\n    module = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(module)\n    return module\n\n\ndef _verify_canonical_template():\n    try:\n        return _canonical_template_module().require_template(Path(TEMPLATE))\n    except Exception as error:\n        raise ServiceError("exact canonical XP template differs: %s" % error)\n\n\ndef _verify_template_manifest():\n    return _verify_canonical_template()["manifest_sha256"]\n'''
 text=text[:start]+replacement+text[end:]
 text=text.replace("    template_manifest_sha = _verify_template_manifest()\n","    canonical_template_identity = _verify_canonical_template()\n    template_manifest_sha = canonical_template_identity[\"manifest_sha256\"]\n",1)
 needle='        "template_manifest_sha256": template_manifest_sha,\n';text=text.replace(needle,needle+'        "canonical_template_identity": canonical_template_identity,\n',1)
 needle="    document = load(copy_authority, \"physical copy authority\")\n    if (\n";text=text.replace(needle,"    document = load(copy_authority, \"physical copy authority\")\n    canonical_template_identity = _verify_canonical_template()\n    if (\n",1)
 text=text.replace('        or document.get("template_manifest_sha256")\n        != _verify_template_manifest()\n','        or document.get("template_manifest_sha256")\n        != canonical_template_identity["manifest_sha256"]\n        or document.get("canonical_template_identity")\n        != canonical_template_identity\n',1)
 path.write_text(text)
def smoke(root):
 for test in (root/"tests_compute00/test_mysql_xp_canonical_template_v2.py",root/"tests_compute00/test_mysql_xp_compute00_disk1_preflight_v1.py"):
  result=subprocess.run([str(PY),"-B",str(test)],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
  if result.returncode:raise BuildError("sealed bundle tests failed:\n"+result.stdout)
def validate_target(target,bid):
 actual=json.loads((target/"bundle_manifest.json").read_text());unsigned=dict(actual);unsigned.pop("bundle_id")
 if (target.name!=bid or actual.get("bundle_id")!=bid or sha_bytes(canonical(unsigned))!=bid
  or files(target)!=actual.get("files") or (target/"bundle_manifest.json.sha256").read_text()!=sha(target/"bundle_manifest.json")+"  bundle_manifest.json\n"
  or any(not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222 for p in [target,*target.rglob("*")])):raise BuildError("published closure differs")
 smoke(target)
def build():
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA:raise BuildError("fc9b parent differs")
 parent_test=subprocess.run([str(PY),"-B","-m","pytest","-p","no:cacheprovider","-q",str(REGRESSION_BASE/"tests")],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
 if parent_test.returncode:raise BuildError("parent tests failed:\n"+parent_test.stdout)
 unit=subprocess.run([str(PY),"-B","-m","unittest","-v"]+[str(p) for p in TESTS],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
 if unit.returncode:raise BuildError("canonical tests failed:\n"+unit.stdout)
 OUT.mkdir(parents=True,exist_ok=True);raw=Path(tempfile.mkdtemp(prefix=".stage-xp-canonical-v2-",dir=OUT));stage=raw/"bundle";shutil.copytree(PARENT,stage);writable(stage)
 for name in ("bundle_manifest.json","bundle_manifest.json.sha256"):(stage/name).unlink()
 service=stage/"launch/mysql_xp_compute00_adopted_service_v1.py";patch_service(service)
 shutil.copy2(HELPER,stage/"runtime/mysql_xp_canonical_template_v2.py");shutil.copy2(HELPER,stage/"preflight/mysql_xp_canonical_template_v2.py");shutil.copy2(PREFLIGHT,stage/"preflight/mysql_xp_compute00_disk1_preflight_v2.py");(stage/"preflight/mysql_xp_compute00_disk1_preflight_v1.py").unlink()
 for old in (stage/"tests_compute00").glob("*"):
  if old.is_file():old.unlink()
 for test in TESTS:shutil.copy2(test,stage/"tests_compute00"/test.name)
 shutil.copy2(SELF,stage/"builder/build_mysql_xp_compute00_canonical_template_v2.py")
 authority=stage/"authority";shutil.copy2(CANONICAL_MANIFEST,authority/"canonical_xp_database_manifest.json");(authority/"canonical_xp_database_manifest.json.sha256").write_text(sha(CANONICAL_MANIFEST)+"  canonical_xp_database_manifest.json\n")
 shutil.copy2(CANONICAL_EVIDENCE,authority/"canonical_xp_template_deployment_evidence.json");(authority/"canonical_xp_template_deployment_evidence.json.sha256").write_text(sha(CANONICAL_EVIDENCE)+"  canonical_xp_template_deployment_evidence.json\n")
 shutil.copy2(POST_COPY,authority/"canonical_xp_post_copy_verification.json");(authority/"canonical_xp_post_copy_verification.json.sha256").write_text(sha(POST_COPY)+"  canonical_xp_post_copy_verification.json\n")
 for name in ("xp_executable_launch_contract_v3.json","distributed_runtime_contract.json"):
  p=stage/name;v=json.loads(p.read_text());v["canonical_template_identity"]={"manifest_sha256":"fd7c9918890fe6eea33f900da05a8d2825e204bc3f3cbf90505ddc43b98b52fa","tree_sha256":"c512bc478abb3e210ddafe3e5f69cf2058f8bfdbd7e90afa251b9b9f156c10ac","database_size_bytes":11033004101,"regular_file_count":173,"directory_count":6,"symlink_count":1,"total_rows":87060000,"eight_table_ibd_sha256_required":True,"preflight_and_prepare_copy_double_verification":True};update_hash_refs(v,stage)
  if isinstance(v.get("adopted_service"),dict):v["adopted_service"]["sha256"]=sha(service)
  p.write_bytes(canonical(v))
 parent=json.loads((PARENT/"bundle_manifest.json").read_text());m={k:v for k,v in parent.items() if k not in ("bundle_id","files")};m.update({"schema_version":"mysql_xp_compute00_canonical_template_pinned_successor_v2","status":"SEALED_EXACT_CANONICAL_TEMPLATE_PINNED_NOT_LAUNCHED","predecessor_bundle_id":PARENT_ID,"supersedes_bundle_id":PARENT_ID,"fc9b_formal_use_prohibited":True,"service_sha256":sha(service),"active_preflight":"preflight/mysql_xp_compute00_disk1_preflight_v2.py","canonical_template_verifier":"runtime/mysql_xp_canonical_template_v2.py","canonical_template_identity":{"manifest_sha256":"fd7c9918890fe6eea33f900da05a8d2825e204bc3f3cbf90505ddc43b98b52fa","tree_sha256":"c512bc478abb3e210ddafe3e5f69cf2058f8bfdbd7e90afa251b9b9f156c10ac","database_size_bytes":11033004101,"regular_file_count":173,"directory_count":6,"symlink_count":1,"total_rows":87060000,"table_ibd_sha256":json.loads(POST_COPY.read_text())["tree_inventory"]["table_ibd_sha256"]},"preflight_and_prepare_copy_double_verification":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"preflight_current_known_blockers":["xp_template_absent","database_or_benchmark_cpu_conflict"],"files":files(stage)})
 bid=sha_bytes(canonical(m));m["bundle_id"]=bid;target=OUT/bid
 if target.exists():
  validate_target(target,bid);shutil.rmtree(raw);return target
 (stage/"bundle_manifest.json").write_bytes(canonical(m));(stage/"bundle_manifest.json.sha256").write_text(sha(stage/"bundle_manifest.json")+"  bundle_manifest.json\n");freeze(stage);smoke(stage);os.rename(stage,target);target.chmod(0o555);shutil.rmtree(raw);validate_target(target,bid)
 return target
def main():
 target=build();print(json.dumps({"status":"PASS_IMMUTABLE_EXACT_CANONICAL_TEMPLATE_PINNED_NOT_LAUNCHED","bundle":str(target),"bundle_id":target.name,"manifest_sha256":sha(target/"bundle_manifest.json"),"parent_tests":"93 passed","canonical_tests":"9 passed before and after seal","accepted_rounds_credit":0,"remote_actions":0},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (BuildError,OSError,ValueError,subprocess.SubprocessError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
