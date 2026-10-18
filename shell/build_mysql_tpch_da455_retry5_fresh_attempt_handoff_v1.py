#!/usr/bin/env python3
"""Rebind retry5 PASS handoff to the fresh-attempt-compatible R0 runner."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="e674446f92a8d50c4348eb05b6fa47402e4783af14ed83e2f3d47c5500bc4fdc";OLD_SHA="7d90839b5932208b99e2deaa09e2029c444bf669b89276566facdc3687b7a08c";OLD=ROOT/"artifacts/mysql_tpch_da455_retry5_pass_r0_handoff_v1"/OLD_ID
OLD_POST="2b9e632feb676bf10bf4524b6e704cdab95be6822c6cfe9a9317902e45148a94";OLD_POST_SHA="f8d0bc5fe5a8a0460ddff21ddd7936236a1e337f81f7fe0fa6d7ce3377468d5c";OLD_RUNNER="51980a5e0e23a21fa35f39b6dfaa369899ef333f7571996583a9098f6fb26e63"
POST="9ec68380ecce6eaf3b48f07bbf0ae9a165ecba1da567ca43cdd869d18c25a549";POST_SHA="d1447641d73f7b5518c1e66f16b70834ddbbb784b1ea2d9d74270797c523fc65";RUNNER="c2512a589d341d2949cc5fdacc241c13640b4f8b1bcd3090df6ec238cce03bad";POSTDIR=ROOT/"artifacts/mysql_tpch_da455_fresh_attempt_postqualification_v1"/POST
OUT=ROOT/"artifacts/mysql_tpch_da455_retry5_fresh_attempt_handoff_v1";BUILDER="builder/build_mysql_tpch_da455_retry5_fresh_attempt_handoff_v1.py";AUDIT="authority/fresh_attempt_handoff_audit.json";TEST="tests/test_mysql_tpch_da455_retry5_fresh_attempt_handoff_v1.py"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def replace(b):
 try:s=b.decode()
 except UnicodeDecodeError:return b
 return s.replace(OLD_POST,POST).replace(OLD_POST_SHA,POST_SHA).replace(OLD_RUNNER,RUNNER).encode()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=json.loads((OLD/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=m.get("files"):raise RuntimeError("predecessor handoff differs")
 if sf(POSTDIR/"bundle_manifest.json")!=POST_SHA or sf(POSTDIR/"qualification/mysql_tpch_v2_fresh_r0_runner_v2.py")!=RUNNER:raise RuntimeError("fresh-attempt postqualification differs")
 content={n:replace((OLD/n).read_bytes()) for n in sorted(m["files"]) if n!="builder/build_mysql_tpch_da455_retry5_pass_r0_handoff_v1.py"};content[BUILDER]=HERE.read_bytes();content[AUDIT]=canonical({"schema_version":"mysql_tpch_da455_retry5_fresh_attempt_handoff_audit_v1","status":"PASS_RETRY5_HANDOFF_BOUND_TO_FRESH_ATTEMPT_RUNNER_ZERO_CREDIT","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"postqualification_bundle_id":POST,"postqualification_manifest_sha256":POST_SHA,"fresh_r0_runner_sha256":RUNNER,"fixed_bootstrap_attempt_as_command_attempt_accepted":False,"fresh_attempt_and_paths_required":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0});content[TEST]=('''from pathlib import Path\nB=Path(__file__).resolve().parents[1];S=(B/"materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py").read_text()\ndef test_handoff_uses_fresh_attempt_runner():\n assert "POST_ID=\\\"'''+POST+'''\\\"" in S and "RUNNER_SHA=\\\"'''+RUNNER+'''\\\"" in S\n assert 'attempt=fresh_attempt()' in S and 'qualification_attempt_id":attempt' in S\ndef test_retry5_completion_and_gate_remain_required():\n assert "validate_native_metric_producer_completion" in S and "gate_remaining(gate" in S\n''').encode()
 files={n:sb(v) for n,v in sorted(content.items())};ident={k:v for k,v in m.items() if k not in ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256","postqualification_bundle_id","postqualification_manifest_sha256","fresh_r0_runner_sha256")};ident.update({"schema_version":"mysql_tpch_da455_retry5_fresh_attempt_handoff_bundle_v1","status":"PASS_IMMUTABLE_FRESH_ATTEMPT_HANDOFF_NOT_LAUNCHED","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"postqualification_bundle_id":POST,"postqualification_manifest_sha256":POST_SHA,"fresh_r0_runner_sha256":RUNNER,"fixed_bootstrap_attempt_as_command_attempt_accepted":False,"fresh_attempt_required":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files});bid=sb(canonical(ident));manifest=dict(ident,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(manifest)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".fresh-attempt-handoff-",dir=OUT))
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
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"materializer_sha256":sf(b/"materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py"),"remote_actions":0},sort_keys=True))
