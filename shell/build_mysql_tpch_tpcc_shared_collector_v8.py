#!/usr/bin/env python3
"""Build v8 shared collector preserving the primary qualification failure.

V7's cleanup ``finally`` could replace an earlier sampling exception with a
75-second graceful-exit timeout.  V8 keeps the exact cleanup behavior but, if
an exception is already active, immediately performs the sealed exact-process
cleanup and lets the primary exception propagate.  No resource, workload,
four-flow, or Algorithm C semantics change.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "6c8806546e5da07089993ce3740fe188f668602a96fefc1af2538640842ea085"
PARENT_SHA = "e7b223ba1cb121a964aeba754f513e659c487f568572751b394f31d60d968e05"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v7" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v8"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v8"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v8.py"
INVALIDATION = "authority/v7_primary_failure_masking_invalidation.json"
TEST = "tests/test_shared_collector_primary_failure_v8.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def parent_manifest():
    path = PARENT / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(value)
    unsigned.pop("bundle_id", None)
    if (
        value.get("bundle_id") != PARENT_ID
        or value.get("schema_version") != "mysql_tpch_tpcc_shared_collector_bundle_v7"
        or sha(canonical(unsigned)) != PARENT_ID
        or digest(path) != PARENT_SHA
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v7 parent identity/scope differs")
    for relative, expected in value["files"].items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("v7 parent closure differs: " + relative)
    return value


def patch_core(data):
    source = data.decode("utf-8")
    old = '''        finally:
            if process is not None and process.poll() is None:
                stop_error = None'''
    new = '''        finally:
            # Preserve the primary sampling/readback failure.  The v7 cleanup
            # waited 75 seconds and then replaced it with a generic graceful-
            # exit timeout, hiding the actionable cause.
            primary_exception_active = sys.exc_info()[1] is not None
            if process is not None and process.poll() is None:
                stop_error = None'''
    if source.count(old) != 1:
        raise BuildError("primary-exception capture patch cardinality differs")
    source = source.replace(old, new)
    old = '''                    if stop_error is not None:
                        raise subprocess.TimeoutExpired(process.args, 0)
                    process.wait(timeout=75)'''
    new = '''                    if stop_error is not None or primary_exception_active:
                        raise subprocess.TimeoutExpired(process.args, 0)
                    process.wait(timeout=75)'''
    if source.count(old) != 1:
        raise BuildError("early exact-cleanup patch cardinality differs")
    source = source.replace(old, new)
    old = '''                if timed_out:
                    raise QualificationError(
                        "debug qualification workload did not exit gracefully "
                        "within 75s"
                    )'''
    new = '''                if timed_out and not primary_exception_active:
                    raise QualificationError(
                        "debug qualification workload did not exit gracefully "
                        "within 75s"
                    )'''
    if source.count(old) != 1:
        raise BuildError("timeout masking patch cardinality differs")
    return source.replace(old, new).encode("utf-8")


def test_source():
    return b'''from __future__ import annotations\nimport hashlib,json,py_compile\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_v8_identity_and_primary_failure_preservation():\n m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")\n raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()\n assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()\n assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v8"\n for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected\n core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()\n assert "primary_exception_active = sys.exc_info()[1] is not None" in core\n assert "stop_error is not None or primary_exception_active" in core\n assert "if timed_out and not primary_exception_active:" in core\n py_compile.compile(str(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-core.pyc")))\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v8")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = parent_manifest()
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode) for relative in parent["files"]}
    payload[CORE] = patch_core(payload[CORE])
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v7", SCHEMA.encode("ascii")
            )
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_shared_collector_primary_failure_masking_invalidation_v1",
        "invalidated_collector_bundle_id": PARENT_ID,
        "failed_attempt": "tpch-a-solo-v7a-20260801t074500p08-6c3809b8",
        "observed_terminal_error": "debug qualification workload did not exit gracefully within 75s",
        "reason": "cleanup finally masked the earlier primary sampling/readback exception",
        "replacement_behavior": "immediate exact-process cleanup on active primary exception and propagation of that primary exception",
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "resource_workload_four_flow_algorithm_c_unchanged": True,
        "postgresql_mutation": False,
    }); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_PRIMARY_FAILURE_PRESERVATION_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "primary_failure_preserved": True,
        "resource_workload_four_flow_algorithm_c_unchanged": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(unsigned)); manifest = {**unsigned, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing shared v8 differs")
        print(destination); return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); os.chmod(path, modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii")
        freeze(stage); os.rename(stage, destination)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    print(destination)


if __name__ == "__main__":
    main()
