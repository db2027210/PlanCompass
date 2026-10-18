#!/usr/bin/env python3
"""Seal JOB v14 with an invariant native-SQL query-duration metric shape."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat

ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
PARENT_SHA = "3ee84d8e47827ff1a4221e3f8828d41e85f3d6aca2e15cf69dc610aa460f2ce6"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v13" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_native_duration_successor_v14"
SCHEMA13 = "mysql_job_raw_all_subset_three_level_successor_bundle_v13"
SCHEMA14 = "mysql_job_native_duration_successor_bundle_v14"
ADAPTER = "qualification/mysql_sql_debug_metric_adapter_v1.py"
BUILDER = "authority/build_mysql_job_native_duration_successor_v14.py"
INVALIDATION = "authority/mysql_job_6b55_native_duration_invalidation_20260803.json"
TEST = "tests/test_mysql_job_native_duration_successor_v14.py"


class BuildError(RuntimeError): pass
def canonical(value): return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
def sha(value): return hashlib.sha256(value).hexdigest()
def digest(path): return sha(Path(path).read_bytes())


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text()); unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(manifest_path) != PARENT_SHA
            or sha(canonical(unsigned)) != PARENT_ID or manifest.get("schema_version") != SCHEMA13):
        raise BuildError("JOB v13 parent identity differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("JOB v13 closure differs: " + relative)
    return manifest


def test_source():
    return b'''from __future__ import annotations
import hashlib,importlib.util,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load():
 p=ROOT/"qualification/mysql_sql_debug_metric_adapter_v1.py";s=importlib.util.spec_from_file_location("duration_adapter",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def test_v14_closure_and_policy():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 assert m["bundle_id"]==ROOT.name==hashlib.sha256((json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()).hexdigest()
 assert m["schema_version"]=="mysql_job_native_duration_successor_bundle_v14"
 assert m["native_sql_metric_shape"]=="invariant_empirical_query_duration_ms"
 assert m["qualification_gate_relaxed"] is False and m["accepted_round_authorized"] is False
 for r,h in m["files"].items():assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
def test_duration_is_proven_inflight_and_after_completion():
 a=load();m=a.NativeMetrics();m.begin(1,10.0)
 x=m.document("a"*32,0,100.0,10.25);assert set(x)&{"query_duration_ms"}=={"query_duration_ms"};assert x["query_duration_ms"]==250.0
 m.complete(1,321.5);y=m.document("a"*32,1,101.0,11.0)
 assert y["query_duration_ms"]==321.5
 assert not ({"latency_p50_ms","latency_p95_ms","latency_p99_ms"}&set(y))
def test_consumer_gate_remains_fail_closed():
 q=(ROOT/"qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_text()
 assert 'native SQL workload requires query duration' in q
 assert 'set(latency) != {"query_duration_ms"}' in q
 s=(ROOT/"qualification/mysql_sql_debug_metric_adapter_v1.py").read_text()
 assert 'if self.in_flight:' in s and 'elif self.latencies_ms:' in s
 assert 'result["query_duration_ms"] = float(self.latencies_ms[-1])' in s
'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("symlink in JOB v14")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {r:(PARENT/r).read_bytes() for r in parent["files"] if r != "closure.json"}
    modes = {r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in payload}
    old_adapter_sha = sha(payload[ADAPTER])
    fixed_adapter = (ROOT / "shell/mysql_sql_debug_metric_adapter_v1.py").read_bytes()
    if (b'result["query_duration_ms"] = float(self.latencies_ms[-1])' not in fixed_adapter
            or b'elif self.latencies_ms:' not in fixed_adapter):
        raise BuildError("stable duration source differs")
    payload[ADAPTER] = fixed_adapter; modes[ADAPTER] = 0o555
    for relative in tuple(payload):
        if relative.endswith(".py") and SCHEMA13.encode() in payload[relative]:
            payload[relative] = payload[relative].replace(SCHEMA13.encode(), SCHEMA14.encode())
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o555
    payload[INVALIDATION] = canonical({
        "schema_version":"mysql_job_native_duration_invalidation_v1",
        "status":"INVALIDATED_ZERO_CREDIT_REQUIRES_FRESH_QUALIFICATION",
        "failed_unit":"ra-mysql-job-qual-solo-6b55-20260803t0427.service",
        "failed_host":"node02", "failed_at_local":"2026-08-03T12:29:56+08:00",
        "failure":"native SQL workload requires query duration",
        "root_cause":"v13 adapter changed from query_duration_ms to percentile keys after first completion",
        "repair":"emit one empirically measured query_duration_ms shape for both in-flight and most-recent-completed states",
        "qualification_gate_relaxed":False, "fresh_qualification_required":True,
        "qualification_credit":0, "r0_credit":0, "candidate_credit":0,
        "top5_replay_credit":0, "accepted_rounds_credit":0,
        "postgresql_mutation":False,
    }); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    closure = json.loads((PARENT/"closure.json").read_text())
    closure.update({
        "schema_version":"mysql_job_module_closure_v14",
        "predecessor_bundle_id":PARENT_ID,
        "predecessor_bundle_manifest_sha256":PARENT_SHA,
        "native_sql_metric_shape":"invariant_empirical_query_duration_ms",
        "qualification_gate_relaxed":False,
    })
    closure["files"] = {r:sha(data) for r,data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent); identity.pop("bundle_id",None)
    identity.update({
        "schema_version":SCHEMA14,
        "status":"SEALED_NATIVE_DURATION_FIX_DEBUG_QUALIFICATION_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id":PARENT_ID,
        "predecessor_bundle_manifest_sha256":PARENT_SHA,
        "failed_6b55_qualification_credit":0,
        "fresh_qualification_required":True,
        "native_sql_metric_shape":"invariant_empirical_query_duration_ms",
        "native_sql_duration_source":"inflight_monotonic_elapsed_or_latest_successful_client_elapsed",
        "qualification_gate_relaxed":False,
        "sql_metric_adapter_sha256":sha(fixed_adapter),
        "builder_sha256":sha(payload[BUILDER]),
        "formal_launch_authorized":False,"formal10_authorized":False,
        "accepted_round_authorized":False,
        "files":{r:sha(data) for r,data in sorted(payload.items())},
    })
    bundle_id=sha(canonical(identity)); manifest={**identity,"bundle_id":bundle_id}
    OUT.mkdir(parents=True,exist_ok=True); target=OUT/bundle_id
    if target.exists() or target.is_symlink():
        if json.loads((target/"bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing v14 identity differs")
    else:
        stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
        try:
            for r,data in payload.items():
                path=stage/r;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);os.chmod(path,modes[r]&~0o222)
            raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n")
            freeze(stage);os.rename(stage,target)
        except BaseException:
            if stage.exists():shutil.rmtree(stage)
            raise
    print(json.dumps({"status":manifest["status"],"bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":digest(target/"bundle_manifest.json"),"old_adapter_sha256":old_adapter_sha,"new_adapter_sha256":sha(fixed_adapter),"qualification_gate_relaxed":False,"accepted_round_authorized":False},sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (BuildError,OSError,ValueError,KeyError) as exc: raise SystemExit("NOT_READY: %s"%exc)
