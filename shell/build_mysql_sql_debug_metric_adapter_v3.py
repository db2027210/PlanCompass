#!/usr/bin/env python3
"""Build immutable SQL debug adapter v3 with per-attempt Q15 view names."""
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
PARENT_ID = "cdd51d297e7ba8a2f0d26f57a161d2ed94959ce1ce3b0644bd7a8a1ce3cd4243"
PARENT = ROOT / "artifacts/mysql_sql_debug_metric_adapter_v2" / PARENT_ID
OUT = ROOT / "artifacts/mysql_sql_debug_metric_adapter_v3"
ADAPTER = "qualification/mysql_sql_debug_metric_adapter_v3.py"
BUILDER = "authority/build_mysql_sql_debug_metric_adapter_v3.py"
INVALIDATION = "authority/v16a_q15_shared_view_invalidation.json"
TEST = "tests/test_mysql_sql_debug_metric_adapter_v3.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def replace_exact(data, old, new, label):
    if data.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return data.replace(old, new)


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version") != "mysql_sql_debug_metric_adapter_bundle_v2"
        or manifest.get("debug_qualification_only") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v2 adapter parent differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v2 adapter closure differs")
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_sql_debug_q15_shared_view_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V3",
        "invalidated_adapter_bundle_id": PARENT_ID,
        "failed_attempt": "tpch-a-paired-v16a-20260801t202300p08-6c3809b8",
        "failure": "Q15 shared revenue0 view remained after an interrupted prior invocation",
        "repair": "derive a deterministic per-attempt-per-worker view identifier",
        "query_semantics_changed": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import hashlib,importlib.util,json,py_compile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v3_q15_identity_is_attempt_and_worker_scoped(tmp_path):
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_sql_debug_metric_adapter_bundle_v3"
 assert m["q15_view_policy"]=="deterministic_attempt_worker_unique_identifier"
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 p=ROOT/"qualification/mysql_sql_debug_metric_adapter_v3.py";text=p.read_text()
 assert 'def _q15_statement(' in text and 'statement.count("revenue0") != 4' in text
 s=importlib.util.spec_from_file_location("a",str(p));x=importlib.util.module_from_spec(s);s.loader.exec_module(x)
 q="CREATE VIEW revenue0 AS SELECT 1; SELECT * FROM revenue0 WHERE 1=(SELECT 1 FROM revenue0); DROP VIEW revenue0;"
 a=x._q15_statement(q,"attempt-abcdefghijklmnopqrstuvwxyz0123456789",3)
 b=x._q15_statement(q,"attempt-abcdefghijklmnopqrstuvwxyz0123456789",4)
 assert "revenue0" not in a.replace("revenue0_ra_","") and a!=b
 assert len(a)>len(q)-1
 py_compile.compile(str(p),doraise=True,cfile=str(tmp_path/"adapter.pyc"))
'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("adapter v3 symlink")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def file_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("adapter v3 closure symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
            result[relative] = digest(path)
    return result


def main():
    parent = load_parent()
    old_relative = next(iter(parent["files"]))
    data = (PARENT / old_relative).read_bytes()
    data = replace_exact(
        data,
        b"import argparse\nfrom concurrent.futures import ThreadPoolExecutor\n",
        b"import argparse\nfrom concurrent.futures import ThreadPoolExecutor\nimport hashlib\n",
        "hashlib import",
    )
    marker = b'''def run_adapter(args):\n'''
    helper = b'''def _q15_statement(statement, attempt_id, worker_id):\n    if statement.count("revenue0") != 4:\n        raise AdapterError("sealed Q15 revenue0 identity cardinality differs")\n    suffix = hashlib.sha256(\n        (str(attempt_id) + ":" + str(worker_id)).encode("utf-8")\n    ).hexdigest()[:20]\n    identifier = "revenue0_ra_" + suffix\n    if len(identifier) > 64:\n        raise AdapterError("Q15 derived view identifier is too long")\n    return statement.replace("revenue0", identifier)\n\n\ndef run_adapter(args):\n'''
    data = replace_exact(data, marker, helper, "Q15 helper")
    old = b'''                    statements[path],\n                ]\n'''
    new = b'''                    (\n                        _q15_statement(\n                            statements[path], attempt, worker_id\n                        )\n                        if path.name == "Q15.sql"\n                        else statements[path]\n                    ),\n                ]\n'''
    data = replace_exact(data, old, new, "Q15 statement binding")
    payload = {
        ADAPTER: data,
        BUILDER: SOURCE.read_bytes(),
        INVALIDATION: canonical(invalidation()),
        TEST: test_source(),
    }
    unsigned = {
        "schema_version": "mysql_sql_debug_metric_adapter_bundle_v3",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "debug_qualification_only": True,
        "native_sql_metric_shape": "stable_query_duration_ms",
        "transactional_percentile_shape_prohibited": True,
        "q15_view_policy": "deterministic_attempt_worker_unique_identifier",
        "q15_query_semantics_changed": False,
        "predecessor_bundle_id": PARENT_ID,
        "workload_launch_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r: sha_bytes(d) for r, d in sorted(payload.items())},
    }
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    test_result = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, content in payload.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                os.chmod(path, 0o444)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            temp = Path(tempfile.mkdtemp(prefix="sql-adapter-v3-test-"))
            scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                result = subprocess.run(
                    [str(PYTHON), "-m", "pytest", "-q", str(scratch / TEST)],
                    cwd=ROOT, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=120,
                )
                if result.returncode:
                    raise BuildError("adapter v3 tests failed:\n" + result.stdout[-12000:])
                test_result = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text())
    check = dict(published); check.pop("bundle_id", None)
    if (
        published != manifest
        or sha_bytes(canonical(check)) != bundle_id
        or file_map(destination) != manifest["files"]
        or any(stat.S_IMODE(p.stat().st_mode) & 0o222 for p in [destination, *destination.rglob("*")])
    ):
        raise BuildError("published adapter v3 differs")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_SQL_DEBUG_ADAPTER_V3",
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": digest(destination / "bundle_manifest.json"),
        "adapter_sha256": digest(destination / ADAPTER),
        "tests": test_result,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
