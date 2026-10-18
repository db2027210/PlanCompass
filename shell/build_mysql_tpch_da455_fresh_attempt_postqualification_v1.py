#!/usr/bin/env python3
"""Repair fresh-R0 runner attempt binding while preserving the da455 closure."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="2b9e632feb676bf10bf4524b6e704cdab95be6822c6cfe9a9317902e45148a94";OLD_SHA="f8d0bc5fe5a8a0460ddff21ddd7936236a1e337f81f7fe0fa6d7ce3377468d5c";OLD=ROOT/"artifacts/mysql_tpch_da455_postqualification_successor_v1"/OLD_ID
RUNNER="qualification/mysql_tpch_v2_fresh_r0_runner_v2.py";BUILDER="builder/build_mysql_tpch_da455_fresh_attempt_postqualification_v1.py";AUDIT="authority/fresh_attempt_binding_audit.json";OUT=ROOT/"artifacts/mysql_tpch_da455_fresh_attempt_postqualification_v1"
TEST="tests/test_mysql_tpch_da455_fresh_attempt_binding_v1.py"
TEST_BODY=r'''from pathlib import Path
B=Path(__file__).resolve().parents[1];S=(B/"qualification/mysql_tpch_v2_fresh_r0_runner_v2.py").read_text()
def test_fresh_attempt_is_required_and_bound():
 assert 're.fullmatch(r"tpch-fresh-r0-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}", qualification_attempt_id)' in S
 assert 'raw.get("qualification_attempt_id") != qualification_attempt_id' in S
 assert 'command.get("qualification_attempt_id") != bootstrap.get("attempt_token")' not in S
def test_gate_and_collector_are_in_fresh_binding():
 assert 'handoff_binding.get("dynamic_gate_sha256") != sha256(args.dynamic_gate)' in S
 assert 'handoff_binding.get("support_collector_manifest_sha256") != SUPPORT_MANIFEST_SHA256' in S
'''
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def patch_runner(b):
 s=b.decode()
 edits=[
  ("    bootstrap: Mapping[str, Any],\n    gate: Mapping[str, Any],","    bootstrap: Mapping[str, Any],\n    qualification_attempt_id: str,\n    gate: Mapping[str, Any],"),
  ('or raw.get("qualification_attempt_id") != bootstrap.get("attempt_token")','or raw.get("qualification_attempt_id") != qualification_attempt_id'),
  ('        "attempt_token": bootstrap["attempt_token"],','        "attempt_token": bootstrap["attempt_token"],\n        "qualification_attempt_id": qualification_attempt_id,'),
  ('    if (\n        command.get("bundle_id") != bundle.name\n        or command.get("qualification_attempt_id") != bootstrap.get("attempt_token")','    qualification_attempt_id = command.get("qualification_attempt_id")\n    handoff_binding = command.get("fresh_r0_handoff_binding") or {}\n    if (\n        command.get("bundle_id") != bundle.name\n        or not isinstance(qualification_attempt_id, str)\n        or re.fullmatch(r"tpch-fresh-r0-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}", qualification_attempt_id) is None\n        or handoff_binding.get("qualification_attempt_id") != qualification_attempt_id\n        or handoff_binding.get("support_collector_bundle_id") != SUPPORT_BUNDLE_ID\n        or handoff_binding.get("support_collector_manifest_sha256") != SUPPORT_MANIFEST_SHA256\n        or handoff_binding.get("dynamic_gate_sha256") != sha256(args.dynamic_gate)'),
  ('        bundle,\n        bootstrap,\n        gate,','        bundle,\n        bootstrap,\n        qualification_attempt_id,\n        gate,'),
 ]
 for old,new in edits:
  if s.count(old)!=1:raise RuntimeError("runner patch anchor differs: "+old[:70])
  s=s.replace(old,new)
 return s.encode()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=json.loads((OLD/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=m.get("files"):raise RuntimeError("predecessor differs")
 content={n:(patch_runner((OLD/n).read_bytes()) if n==RUNNER else (OLD/n).read_bytes()) for n in sorted(m["files"]) if n!="builder/build_mysql_tpch_da455_postqualification_successor_v1.py"};content[BUILDER]=HERE.read_bytes();content[TEST]=TEST_BODY.encode();content[AUDIT]=canonical({"schema_version":"mysql_tpch_da455_fresh_attempt_binding_audit_v1","status":"PASS_FRESH_ATTEMPT_BOUND_TO_COMMAND_RAW_AND_HANDOFF_ZERO_CREDIT","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"fixed_old_bootstrap_attempt_requirement":True,"fresh_attempt_required":True,"fresh_output_reuse_prohibited":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0})
 files={n:sb(v) for n,v in sorted(content.items())};ident={k:v for k,v in m.items() if k not in ("bundle_id","files","schema_version","status","builder_sha256")};ident.update({"schema_version":"mysql_tpch_da455_fresh_attempt_postqualification_bundle_v1","status":"SEALED_LOCAL_FRESH_ATTEMPT_POSTQUALIFICATION_NOT_EXECUTED","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"builder_sha256":sf(HERE),"fresh_attempt_required":True,"old_bootstrap_attempt_as_command_attempt_accepted":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files});bid=sb(canonical(ident));manifest=dict(ident,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(manifest)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".fresh-attempt-post-",dir=OUT))
 try:
  for n,v in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
 finally:
  if stage.exists():shutil.rmtree(stage)
 if fmap(dst)!=files:raise RuntimeError("published files differ")
 for p in (dst,*dst.rglob("*")):
  if p.is_symlink() or stat.S_IMODE(p.stat().st_mode)&0o222:raise RuntimeError("mutable successor")
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"runner_sha256":sf(b/RUNNER),"remote_actions":0},sort_keys=True))
