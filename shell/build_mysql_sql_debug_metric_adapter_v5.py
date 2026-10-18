#!/usr/bin/env python3
"""Build immutable adapter v5 with deadline-safe Q15 serialization."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location(
    "adapter_v4_builder", ROOT / "shell/build_mysql_sql_debug_metric_adapter_v4.py"
)
v4 = importlib.util.module_from_spec(spec); spec.loader.exec_module(v4)
PARENT_ID = "ed12285d3d396b84f2b53f6a62abec4392e8c009852a20731e2e1acfa634907b"
PARENT_SHA = "190666bcb02e05c473f80c6814488e69b26b092c271992d8957dd0f7f3c90777"
PARENT = ROOT / "artifacts/mysql_sql_debug_metric_adapter_v4" / PARENT_ID
OUT = ROOT / "artifacts/mysql_sql_debug_metric_adapter_v5"
ADAPTER = "qualification/mysql_sql_debug_metric_adapter_v3.py"
BUILDER = "authority/build_mysql_sql_debug_metric_adapter_v5.py"
INVALIDATION = "authority/v21a_q15_deadline_invalidation.json"
TEST = "tests/test_mysql_sql_debug_metric_adapter_v5.py"


def replace_exact(data, old, new, label):
    if data.count(old) != 1:
        raise v4.BuildError(label + " replacement cardinality differs")
    return data.replace(old, new)


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text()); unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or v4.digest(path) != PARENT_SHA
            or v4.sha_bytes(v4.canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_sql_debug_metric_adapter_bundle_v4"
            or manifest.get("q15_view_policy") != "deterministic_attempt_worker_unique_identifier"
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise v4.BuildError("adapter v4 parent differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or v4.digest(path) != expected:
            raise v4.BuildError("adapter v4 closure differs: " + relative)
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_sql_debug_adapter_v21a_q15_deadline_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V5",
        "invalidated_adapter_bundle_id": PARENT_ID,
        "failed_attempt": "tpch-a-paired-v21a-20260801t222000p08-6c3809b8",
        "failure": "Q15 waiters computed query deadlines before acquiring the serialized-view lock",
        "repair": "acquire the Q15 lock before begin/deadline/timeout calculation and recheck the stop deadline",
        "query_semantics_changed": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v5_content_and_q15_deadline_order():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_sql_debug_metric_adapter_bundle_v5"
 assert m["q15_lock_before_deadline"] is True and m["query_semantics_changed"] is False
 source=(ROOT/"qualification/mysql_sql_debug_metric_adapter_v3.py").read_text()
 lock=source.index("lock.acquire()")
 begun=source.index("begun = time.monotonic()",source.index("def worker"))
 bounds=source.index("_deadline_bounded_query_timeouts",source.index("def worker")+1)
 assert lock < begun < bounds
 assert "if stop.is_set() or time.monotonic() >= deadline:" in source[lock:begun]
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
'''


def main():
    parent = load_parent()
    payload = {r:(PARENT/r).read_bytes() for r in parent["files"]}
    old = b'''            begun = time.monotonic()\n            timeout_bounds = _deadline_bounded_query_timeouts(\n                deadline, args.query_timeout_seconds, begun\n            )\n            if timeout_bounds is None:\n                return\n            mysql_timeout_ms, client_timeout_seconds = timeout_bounds\n            metrics.begin(worker_id, begun)\n            try:\n                command = [\n'''
    new = b'''            lock = q15_lock if path.name == "Q15.sql" else None\n            if lock is not None:\n                lock.acquire()\n            if stop.is_set() or time.monotonic() >= deadline:\n                if lock is not None:\n                    lock.release()\n                return\n            begun = time.monotonic()\n            timeout_bounds = _deadline_bounded_query_timeouts(\n                deadline, args.query_timeout_seconds, begun\n            )\n            if timeout_bounds is None:\n                if lock is not None:\n                    lock.release()\n                return\n            mysql_timeout_ms, client_timeout_seconds = timeout_bounds\n            metrics.begin(worker_id, begun)\n            try:\n                command = [\n'''
    payload[ADAPTER] = replace_exact(payload[ADAPTER], old, new, "Q15 deadline order")
    old = b'''                lock = q15_lock if path.name == "Q15.sql" else None\n                if lock is not None:\n                    lock.acquire()\n                try:\n'''
    new = b'''                try:\n'''
    payload[ADAPTER] = replace_exact(payload[ADAPTER], old, new, "old Q15 lock acquisition")
    for relative, content in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = content.replace(
                b"mysql_sql_debug_metric_adapter_bundle_v4",
                b"mysql_sql_debug_metric_adapter_bundle_v5",
            )
    payload["tests/test_mysql_sql_debug_metric_adapter_v4.py"] = replace_exact(
        payload["tests/test_mysql_sql_debug_metric_adapter_v4.py"],
        b'm["adapter_bytes_changed"] is False',
        b'm["adapter_bytes_changed"] is True',
        "v4 inherited byte-change assertion",
    )
    payload[BUILDER] = SOURCE.read_bytes()
    payload[INVALIDATION] = v4.canonical(invalidation())
    payload[TEST] = test_source()
    unsigned = {k:v for k,v in parent.items() if k not in {"bundle_id","files","schema_version"}}
    unsigned.update({
        "schema_version": "mysql_sql_debug_metric_adapter_bundle_v5",
        "status": "SEALED_Q15_DEADLINE_SAFE_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "adapter_bytes_changed": True,
        "q15_lock_before_deadline": True,
        "query_semantics_changed": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workload_launch_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r:v4.sha_bytes(d) for r,d in sorted(payload.items())},
    })
    bundle_id=v4.sha_bytes(v4.canonical(unsigned)); manifest=dict(unsigned,bundle_id=bundle_id)
    OUT.mkdir(parents=True,exist_ok=True); destination=OUT/bundle_id
    test_result="existing immutable bundle reverified"
    if not destination.exists():
        stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative,content in payload.items():
                path=stage/relative; path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(content); os.chmod(path,0o444)
            raw=v4.canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw)
            (stage/"bundle_manifest.json.sha256").write_text(v4.sha_bytes(raw)+"  bundle_manifest.json\n")
            temp=Path(tempfile.mkdtemp(prefix="sql-adapter-v5-test-")); scratch=temp/bundle_id
            try:
                shutil.copytree(stage,scratch); v4.freeze(scratch)
                tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py"))
                env=dict(os.environ); env["PYTHONDONTWRITEBYTECODE"]="1"
                result=subprocess.run([str(v4.PYTHON),"-m","pytest","-q","-p","no:cacheprovider","--basetemp",str(temp/"pytest"),*tests],cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
                if result.returncode: raise v4.BuildError("adapter v5 tests failed:\n"+result.stdout[-12000:])
                test_result=result.stdout.strip()
            finally:
                if scratch.exists():
                    for path in [scratch,*scratch.rglob("*")]: os.chmod(path,0o700 if path.is_dir() else 0o600)
                shutil.rmtree(temp)
            v4.freeze(stage); os.rename(stage,destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    published=json.loads((destination/"bundle_manifest.json").read_text()); check=dict(published); check.pop("bundle_id",None)
    if (published!=manifest or v4.sha_bytes(v4.canonical(check))!=bundle_id
            or v4.file_map(destination)!=manifest["files"]
            or any(stat.S_IMODE(p.stat().st_mode)!=(0o555 if p.is_dir() else 0o444) for p in [destination,*destination.rglob("*")])):
        raise v4.BuildError("published adapter v5 differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SQL_DEBUG_ADAPTER_V5","bundle":str(destination),"bundle_id":bundle_id,"bundle_manifest_sha256":v4.digest(destination/"bundle_manifest.json"),"adapter_sha256":v4.digest(destination/ADAPTER),"tests":test_result,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))


if __name__=="__main__":
    try: main()
    except (v4.BuildError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print("NOT_READY: "+str(exc),file=__import__("sys").stderr); raise SystemExit(2)
