#!/usr/bin/env python3
"""Build immutable adapter v4 with sealed non-root traversal permissions."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
PARENT_ID = "bb0a61815d3d0785c4be75cd16ee87c2692e281d846914e78aa4c65129de1ff2"
PARENT_SHA = "df9369c26c3b521dba41e4aa9d45aed737fab3f2ca0b80a9292fc69782b7461e"
PARENT = ROOT / "artifacts/mysql_sql_debug_metric_adapter_v3" / PARENT_ID
OUT = ROOT / "artifacts/mysql_sql_debug_metric_adapter_v4"
ADAPTER = "qualification/mysql_sql_debug_metric_adapter_v3.py"
BUILDER = "authority/build_mysql_sql_debug_metric_adapter_v4.py"
INVALIDATION = "authority/v18a_nonroot_traversal_invalidation.json"
TEST = "tests/test_mysql_sql_debug_metric_adapter_v4.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


class BuildError(RuntimeError): pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value): return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): result.update(block)
    return result.hexdigest()


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text()); unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(path) != PARENT_SHA
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_sql_debug_metric_adapter_bundle_v3"
            or manifest.get("q15_view_policy") != "deterministic_attempt_worker_unique_identifier"
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("adapter v3 parent differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("adapter v3 closure differs: " + relative)
    return manifest


def invalidation():
    return {"schema_version":"mysql_sql_debug_adapter_v18a_permission_invalidation_v1",
            "status":"INVALIDATED_ZERO_CREDIT_REPLACED_BY_V4",
            "invalidated_adapter_bundle_id":PARENT_ID,
            "failed_attempt":"tpch-a-solo-v18a-20260801t211932p08-6c3809b8",
            "failure":"content-addressed directories were root-only and client uid 1008 could not traverse",
            "repair":"world-readable files and world-traversable immutable directories",
            "adapter_bytes_changed":False,"qualification_credit":0,"r0_credit":0,
            "accepted_rounds_credit":0,"postgresql_mutation":False}


def test_source():
    return b'''from __future__ import annotations
import hashlib,json,os,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v4_is_content_addressed_and_nonroot_traversable():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_sql_debug_metric_adapter_bundle_v4"
 assert m["immutable_directory_mode"]=="0555" and m["immutable_file_mode"]=="0444"
 assert m["client_execution_uid"]==1008 and m["adapter_bytes_changed"] is False
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for p in [ROOT,*ROOT.rglob("*")]:
  mode=stat.S_IMODE(p.stat().st_mode)
  assert not mode&0o222
  if p.is_dir(): assert mode&0o005==0o005
  elif p.is_file(): assert mode&0o004==0o004
 for r,h in m["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
'''


def file_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink(): raise BuildError("adapter v4 closure symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json","bundle_manifest.json.sha256"}:
            result[relative] = digest(path)
    return result


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("adapter v4 symlink")
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def main():
    parent = load_parent()
    payload = {r:(PARENT/r).read_bytes() for r in parent["files"]}
    for relative, content in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = content.replace(
                b"mysql_sql_debug_metric_adapter_bundle_v3",
                b"mysql_sql_debug_metric_adapter_bundle_v4",
            )
    payload[BUILDER] = SOURCE.read_bytes(); payload[INVALIDATION] = canonical(invalidation())
    payload[TEST] = test_source()
    unsigned = {k:v for k,v in parent.items() if k not in {"bundle_id","files","schema_version"}}
    unsigned.update({"schema_version":"mysql_sql_debug_metric_adapter_bundle_v4",
                     "status":"SEALED_NONROOT_TRAVERSABLE_NOT_LAUNCH_AUTHORITY",
                     "predecessor_bundle_id":PARENT_ID,
                     "predecessor_manifest_sha256":PARENT_SHA,
                     "adapter_bytes_changed":False,"client_execution_uid":1008,
                     "immutable_directory_mode":"0555","immutable_file_mode":"0444",
                     "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
                     "workload_launch_authorized":False,"formal_launch_authorized":False,
                     "accepted_round_authorized":False,
                     "files":{r:sha_bytes(d) for r,d in sorted(payload.items())}})
    bundle_id=sha_bytes(canonical(unsigned)); manifest=dict(unsigned,bundle_id=bundle_id)
    OUT.mkdir(parents=True,exist_ok=True); destination=OUT/bundle_id
    test_result="existing immutable bundle reverified"
    if not destination.exists():
        stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative,content in payload.items():
                path=stage/relative; path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(content); os.chmod(path,0o444)
            raw=canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw)
            (stage/"bundle_manifest.json.sha256").write_text(sha_bytes(raw)+"  bundle_manifest.json\n")
            temp=Path(tempfile.mkdtemp(prefix="sql-adapter-v4-test-")); scratch=temp/bundle_id
            try:
                shutil.copytree(stage,scratch); freeze(scratch)
                tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py"))
                test_env=dict(os.environ); test_env["PYTHONDONTWRITEBYTECODE"]="1"
                result=subprocess.run([str(PYTHON),"-m","pytest","-q","-p","no:cacheprovider",
                                      "--basetemp",str(temp/"pytest"),*tests],cwd=ROOT,
                                      env=test_env,text=True,stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,timeout=120)
                if result.returncode: raise BuildError("adapter v4 tests failed:\n"+result.stdout[-12000:])
                test_result=result.stdout.strip()
            finally:
                if scratch.exists():
                    for path in [scratch, *scratch.rglob("*")]:
                        os.chmod(path, 0o700 if path.is_dir() else 0o600)
                shutil.rmtree(temp)
            freeze(stage); os.rename(stage,destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    published=json.loads((destination/"bundle_manifest.json").read_text()); check=dict(published); check.pop("bundle_id",None)
    if (published!=manifest or sha_bytes(canonical(check))!=bundle_id
            or file_map(destination)!=manifest["files"]
            or any(stat.S_IMODE(p.stat().st_mode)!=(0o555 if p.is_dir() else 0o444)
                   for p in [destination,*destination.rglob("*")])):
        raise BuildError("published adapter v4 differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SQL_DEBUG_ADAPTER_V4",
                      "bundle":str(destination),"bundle_id":bundle_id,
                      "bundle_manifest_sha256":digest(destination/"bundle_manifest.json"),
                      "adapter_sha256":digest(destination/ADAPTER),"tests":test_result,
                      "formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))


if __name__=="__main__":
    try: main()
    except (BuildError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print("NOT_READY: "+str(exc),file=__import__("sys").stderr); raise SystemExit(2)
