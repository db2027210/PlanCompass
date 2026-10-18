#!/usr/bin/env python3
"""Build one sealed collector identity for JOB, TPC-H, and TPC-C gates."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "1f5d221658221f1a8b39862543d35684acc5f1c1cb0ce10273bd4d6ac1aeac05"
JOB_SHA = "5150d3cd0dd9625b591e60273a19e869e948e2fa0e44898fdec30cd024fa540a"
JOB = ROOT / "artifacts/mysql_job_v17_paired_gate_relocation_successor" / JOB_ID
SHARED_ID = "4369e9c9ba01a7784a45120c60e23e64bdc6be50bb2441b8ea2b1ae4f86d01cb"
SHARED_SHA = "c5d24877868cf10f060861f0b5dbed4be18c3076dc7ecd96a3da58a179fdce23"
SHARED = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v28" / SHARED_ID
OUT = ROOT / "artifacts/mysql_unified_job_tpch_tpcc_collector_v1"
SCHEMA = "mysql_unified_job_tpch_tpcc_collector_bundle_v1"
ENTRY = "qualification/mysql_unified_job_tpch_tpcc_collector_entry_v1.py"
JOB_CORE = "qualification/mysql_job_distributed_live_readback_qualification_v2.py"
JOB_ADAPTER = "qualification/mysql_job_distributed_workload_metric_adapter_v1.py"
SHARED_CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
SHARED_ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
PAIRED = "controller/mysql_unified_qualification_paired_authority_v2.py"
AGGREGATE = "qualification/mysql_unified_three_pair_gate_aggregate_v2.py"
IDENTITY = "authority/mysql_unified_collector_identity_v1.json"
BUILDER = "builder/build_mysql_unified_job_tpch_tpcc_collector_v1.py"
TEST = "tests/test_mysql_unified_job_tpch_tpcc_collector_v1.py"
BEHAVIOR_TEST = "tests/test_mysql_unified_collector_v1_behavior.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def verify(bundle, expected_id, expected_sha, schema):
    path = bundle / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (bundle.name != expected_id or manifest.get("bundle_id") != expected_id
            or digest(path) != expected_sha or sha(canonical(unsigned)) != expected_id
            or manifest.get("schema_version") != schema):
        raise BuildError("predecessor identity differs: " + str(bundle))
    for relative, expected in (manifest.get("files") or {}).items():
        source = bundle / relative
        if source.is_symlink() or not source.is_file() or digest(source) != expected:
            raise BuildError("predecessor closure differs: " + relative)
    return manifest


ENTRY_SOURCE = r'''#!/usr/bin/env python3
"""Dispatch one sealed collector version across JOB, TPC-H, and TPC-C."""
from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
JOB_SCHEMA="mysql_job_v17_paired_gate_relocation_successor_bundle_v1"
SHARED_SCHEMAS={"mysql_tpch_v23_recalibrated_successor_v3","mysql_tpcc_prefault_successor_bundle_v16"}
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,str(path))
 if spec is None or spec.loader is None:raise SystemExit("NOT_READY: unified collector module load failed")
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def option(name):
 rows=[i for i,value in enumerate(sys.argv) if value==name]
 if len(rows)!=1 or rows[0]+1>=len(sys.argv):raise SystemExit("NOT_READY: exact --bundle is required")
 return Path(sys.argv[rows[0]+1]).resolve(strict=True)
def main():
 if sys.argv.count("--compare-bidirectional")==1:
  evidence=option("--a-solo-evidence")
  scenario=json.loads(evidence.read_text(encoding="utf-8")).get("scenario")
  if scenario=="job_memory":
   return load("unified_job_core",HERE/"mysql_job_distributed_live_readback_qualification_v2.py").main()
  if scenario in {"tpch_memory","tpcc_cpu"}:
   entry=load("unified_shared_entry",HERE/"mysql_tpch_tpcc_shared_collector_entry_v1.py")
   return entry.core.main()
  raise SystemExit("NOT_READY: comparison scenario is outside unified closure")
 bundle=option("--bundle")
 manifest=json.loads((bundle/"bundle_manifest.json").read_text(encoding="utf-8"))
 schema=manifest.get("schema_version")
 if schema==JOB_SCHEMA:
  return load("unified_job_core",HERE/"mysql_job_distributed_live_readback_qualification_v2.py").main()
 if schema in SHARED_SCHEMAS:
  entry=load("unified_shared_entry",HERE/"mysql_tpch_tpcc_shared_collector_entry_v1.py")
  return entry.core.main()
 raise SystemExit("NOT_READY: target scenario bundle schema is outside unified closure")
if __name__=="__main__":
 raise SystemExit(main())
'''.encode("utf-8")


TEST_SOURCE = r'''from __future__ import annotations
import ast,hashlib,json,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_manifest_identity_scope_and_exact_closure():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_unified_job_tpch_tpcc_collector_bundle_v1"
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
 assert m["postgresql_mutation_prohibited"] is True and m["remote_actions"]==0
 for key in ("qualification_credit","r0_credit","candidate_credit","top5_replay_credit","accepted_rounds_credit"):assert m[key]==0
 actual={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*") if p.is_file() and p.relative_to(ROOT).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
 assert actual==m["files"]
 for p in (ROOT,*ROOT.rglob("*")):assert not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222==0
def test_one_manifest_sha_is_emitted_by_both_collector_cores():
 job=(ROOT/"qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_text()
 shared=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 needle='Path(__file__).resolve().parents[1] / "bundle_manifest.json"'
 assert needle in job and needle in shared
 assert 'sha256(Path(__file__).resolve())' not in job
 assert 'collector != sha256(' in job and 'collector != sha256(' in shared
 entry=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()
 assert 'mysql_unified_job_tpch_tpcc_collector_entry_v1.py' in entry
def test_dispatch_and_exact_separate_collector_closure():
 entry=(ROOT/"qualification/mysql_unified_job_tpch_tpcc_collector_entry_v1.py").read_text()
 assert "mysql_job_v17_paired_gate_relocation_successor_bundle_v1" in entry
 assert "mysql_tpch_v23_recalibrated_successor_v3" in entry
 assert "mysql_tpcc_prefault_successor_bundle_v16" in entry
 paired=(ROOT/"controller/mysql_unified_qualification_paired_authority_v2.py").read_text()
 for value in ("--collector-bundle","collector_bundle_id","collector_manifest_sha256","collector is outside sealed collector closure"):
  assert value in paired
 aggregate=(ROOT/"qualification/mysql_unified_three_pair_gate_aggregate_v2.py").read_text()
 assert 'len(collector_hashes) != 1' in aggregate
 assert 'Path(__file__).resolve().parents[1] / "bundle_manifest.json"' in aggregate
def test_all_python_ast_and_no_cache():
 paths=list(ROOT.rglob("*.py"));assert paths
 for path in paths:ast.parse(path.read_text())
 assert not list(ROOT.rglob("*.pyc")) and not list(ROOT.rglob("__pycache__"))
'''.encode("utf-8")


def patch_job_core(raw):
    old = b'import mysql_distributed_workload_metric_adapter_v1 as metric_adapter'
    new = b'import mysql_job_distributed_workload_metric_adapter_v1 as metric_adapter'
    old_collector = b'"collector_sha256": sha256(Path(__file__).resolve()),'
    new_collector = (b'"collector_sha256": sha256(\n'
                     b'            Path(__file__).resolve().parents[1] / "bundle_manifest.json"\n'
                     b'        ),')
    if raw.count(old) != 1 or raw.count(old_collector) != 1:
        raise BuildError("JOB collector patch anchor differs")
    return patch_collector_validation(
        raw.replace(old, new).replace(old_collector, new_collector))


def patch_collector_validation(raw):
    old = (b'        or not isinstance(collector, str)\n'
           b'        or len(collector) != 64\n')
    new = old + (b'        or collector != sha256(\n'
                 b'            Path(__file__).resolve().parents[1] / "bundle_manifest.json"\n'
                 b'        )\n')
    if raw.count(old) != 1:
        raise BuildError("collector evidence current-manifest anchor differs")
    return raw.replace(old, new)


def patch_shared_entry(raw):
    old = b'entry = str(Path(__file__).resolve())'
    new = b'entry = str(HERE / "mysql_unified_job_tpch_tpcc_collector_entry_v1.py")'
    if raw.count(old) != 1:
        raise BuildError("shared paired executable patch anchor differs")
    return raw.replace(old, new)


def patch_paired(raw):
    raw = raw.replace(b"mysql_job_v17_qualification_paired_authority_v1",
                      b"mysql_unified_qualification_paired_authority_v2")
    anchor = b"def produce(args):\n    bundle, manifest = verify_bundle(args.scenario_bundle)\n"
    replacement = (b"def produce(args):\n    bundle, manifest = verify_bundle(args.scenario_bundle)\n"
                   b"    collector_bundle, collector_manifest = verify_bundle(args.collector_bundle)\n"
                   b"    collector = args.collector.resolve(strict=True)\n"
                   b"    try: collector_relative = collector.relative_to(collector_bundle).as_posix()\n"
                   b"    except ValueError: raise ProducerError(\"collector is outside sealed collector bundle\")\n"
                   b"    if (collector_relative not in collector_manifest[\"files\"]\n"
                   b"            or collector_manifest[\"files\"][collector_relative] != sha256(collector)):\n"
                   b"        raise ProducerError(\"collector is outside sealed collector closure\")\n")
    if raw.count(anchor) != 1:
        raise BuildError("paired producer entry anchor differs")
    raw = raw.replace(anchor, replacement)
    command_anchor = b'        "peer_live_readback_sha256": sha256(args.peer_readback),\n'
    command_new = command_anchor + (b'        "collector_bundle_id": collector_manifest["bundle_id"],\n'
                                    b'        "collector_manifest_sha256": sha256(collector_bundle / "bundle_manifest.json"),\n'
                                    b'        "collector_entry_sha256": sha256(collector),\n')
    if raw.count(command_anchor) != 1:
        raise BuildError("paired command collector anchor differs")
    raw = raw.replace(command_anchor, command_new)
    authority_anchor = (b'        "producer": "mysql_unified_qualification_paired_authority_v2",\n'
                        b'        "source_solo_authority_sha256": sha256(args.solo_authority),\n')
    authority_new = authority_anchor + (b'        "collector_bundle_id": collector_manifest["bundle_id"],\n'
                                        b'        "collector_manifest_sha256": sha256(collector_bundle / "bundle_manifest.json"),\n')
    if raw.count(authority_anchor) != 1:
        raise BuildError("paired authority collector anchor differs")
    raw = raw.replace(authority_anchor, authority_new)
    parser_anchor = b'    parser.add_argument("--collector", type=Path, required=True)\n'
    if raw.count(parser_anchor) != 1:
        raise BuildError("paired parser collector anchor differs")
    raw = raw.replace(parser_anchor, parser_anchor + b'    parser.add_argument("--collector-bundle", type=Path, required=True)\n')
    old_main = (b'    bundle, manifest = verify_bundle(args.scenario_bundle)\n'
                b'    collector = args.collector.resolve(strict=True)\n'
                b'    try: relative = collector.relative_to(bundle).as_posix()\n'
                b'    except ValueError: raise ProducerError("collector is outside scenario bundle")\n'
                b'    if (relative not in manifest["files"] or manifest["files"][relative] != sha256(collector)):\n'
                b'        raise ProducerError("collector is outside sealed scenario closure")\n'
                b'    print(json.dumps(produce(args), sort_keys=True))\n')
    if raw.count(old_main) != 1:
        raise BuildError("paired main closure anchor differs")
    return raw.replace(old_main, b'    print(json.dumps(produce(args), sort_keys=True))\n')


def patch_aggregate(raw):
    raw = raw.replace(b"mysql_job_v17_three_pair_gate_aggregate_v1",
                      b"mysql_unified_three_pair_gate_aggregate_v2")
    anchor = (b'    if not all(isinstance(value, str) and HEX64.fullmatch(value)\n'
              b'               for value in collector_hashes):\n'
              b'        raise AggregateError("collector identity differs")\n')
    replacement = anchor + (b'    current_manifest = Path(__file__).resolve().parents[1] / "bundle_manifest.json"\n'
                            b'    if (len(collector_hashes) != 1\n'
                            b'            or collector_hashes[0] != sha256(current_manifest)):\n'
                            b'        raise AggregateError("all three gates require this one unified collector manifest SHA")\n')
    if raw.count(anchor) != 1:
        raise BuildError("three-gate unified collector anchor differs")
    return raw.replace(anchor, replacement)


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("symlink in unified collector")
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    job_manifest = verify(JOB, JOB_ID, JOB_SHA,
                          "mysql_job_v17_paired_gate_relocation_successor_bundle_v1")
    shared_manifest = verify(SHARED, SHARED_ID, SHARED_SHA,
                             "mysql_tpch_tpcc_shared_collector_bundle_v28")
    payload = {}
    modes = {}
    for relative in shared_manifest["files"]:
        if relative.startswith("tests/"):
            continue
        payload[relative] = (SHARED / relative).read_bytes()
        modes[relative] = stat.S_IMODE((SHARED / relative).stat().st_mode)
    payload[SHARED_CORE] = patch_collector_validation(payload[SHARED_CORE])
    payload[SHARED_ENTRY] = patch_shared_entry(payload[SHARED_ENTRY])
    payload[JOB_CORE] = patch_job_core((JOB / "qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_bytes())
    payload[JOB_ADAPTER] = (JOB / "qualification/mysql_distributed_workload_metric_adapter_v1.py").read_bytes()
    payload[ENTRY] = ENTRY_SOURCE
    payload[PAIRED] = patch_paired((JOB / "controller/mysql_job_v17_qualification_paired_authority_v1.py").read_bytes())
    payload[AGGREGATE] = patch_aggregate((JOB / "qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py").read_bytes())
    identity = {
        "schema_version": "mysql_unified_job_tpch_tpcc_collector_identity_v1",
        "status": "SEALED_SAME_VERSION_IDENTITY_NOT_LAUNCH_AUTHORITY",
        "scenarios": ["job_memory", "tpch_memory", "tpcc_cpu"],
        "collector_identity_at_runtime": "sha256_of_this_bundle_manifest_raw_bytes",
        "job_predecessor_bundle_id": JOB_ID,
        "job_predecessor_manifest_sha256": JOB_SHA,
        "tpch_tpcc_predecessor_bundle_id": SHARED_ID,
        "tpch_tpcc_predecessor_manifest_sha256": SHARED_SHA,
        "same_collector_sha_gate_preserved": True,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False, "qualification_credit": 0,
    }
    payload[IDENTITY] = canonical(identity)
    payload[BUILDER] = Path(__file__).read_bytes()
    payload[TEST] = TEST_SOURCE
    payload[BEHAVIOR_TEST] = (ROOT / "tests/test_mysql_unified_collector_v1_behavior.py").read_bytes()
    for relative in (JOB_CORE, JOB_ADAPTER, ENTRY, PAIRED, AGGREGATE): modes[relative] = 0o555
    for relative in (IDENTITY, TEST, BEHAVIOR_TEST): modes[relative] = 0o444
    modes[BUILDER] = 0o555
    manifest_identity = {
        "schema_version": SCHEMA,
        "status": "SEALED_UNIFIED_THREE_SCENARIO_COLLECTOR_NOT_LAUNCHED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "formal_launch_authorized": False, "formal10_authorized": False,
        "accepted_round_authorized": False, "qualification_credit": 0,
        "r0_credit": 0, "candidate_credit": 0, "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "supported_scenarios": ["job_memory", "tpch_memory", "tpcc_cpu"],
        "collector_entry": ENTRY,
        "paired_authority_producer": PAIRED,
        "three_pair_gate_aggregate_producer": AGGREGATE,
        "collector_identity": IDENTITY,
        "same_collector_sha_gate_preserved": True,
        "job_predecessor_bundle_id": JOB_ID,
        "job_predecessor_manifest_sha256": JOB_SHA,
        "tpch_tpcc_predecessor_bundle_id": SHARED_ID,
        "tpch_tpcc_predecessor_manifest_sha256": SHARED_SHA,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    }
    bundle_id = sha(canonical(manifest_identity))
    manifest = dict(manifest_identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing unified collector identity differs")
        print(json.dumps({"status": manifest["status"], "bundle": str(destination),
                          "bundle_id": bundle_id,
                          "manifest_sha256": digest(destination / "bundle_manifest.json")}, sort_keys=True))
        return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); path.chmod(modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.rename(stage, destination)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status": manifest["status"], "bundle": str(destination),
                      "bundle_id": bundle_id,
                      "manifest_sha256": digest(destination / "bundle_manifest.json")}, sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
