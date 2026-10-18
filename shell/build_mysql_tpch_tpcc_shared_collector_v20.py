#!/usr/bin/env python3
"""Build immutable v20 with contiguous, no-skip metric catch-up."""
from __future__ import annotations

import importlib.util
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
spec = importlib.util.spec_from_file_location(
    "v19_builder", ROOT / "shell/build_mysql_tpch_tpcc_shared_collector_v19.py"
)
v19 = importlib.util.module_from_spec(spec); spec.loader.exec_module(v19)
PARENT_ID = "12b2338cd095d54d797cc06f1a9b44430a2f3002fd49688ec10b3f226767285f"
PARENT_SHA = "3d11d9617fdcd3155ffb423e703c842849d5febee43b1988757695ebf875592f"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v19" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v20"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v20"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v20.py"
INVALIDATION = "authority/v19a_exact_next_backlog_invalidation.json"
TEST = "tests/test_shared_collector_contiguous_catchup_v20.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID
            or v19.digest(path) != PARENT_SHA
            or v19.sha_bytes(v19.canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_tpch_tpcc_shared_collector_bundle_v19"
            or manifest.get("paired_peer_epoch_policy")
            != "exact_sequence_zero_then_every_exact_successor"
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise v19.BuildError("v19 parent differs")
    for relative, expected in manifest["files"].items():
        item = PARENT / relative
        if item.is_symlink() or not item.is_file() or v19.digest(item) != expected:
            raise v19.BuildError("v19 closure differs: " + relative)
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_shared_v19a_exact_next_backlog_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V20",
        "failed_attempts": [
            "tpch-a-paired-v19a-20260801t220000p08-6c3809b8",
            "tpcc-b-paired-v19a-20260801t220000p08-7e2e13aa",
        ],
        "failure": "full process/resource snapshots can take longer than the one-second metric cadence, so exact-one-row consumption accumulates stale backlog",
        "repair": "verify every unconsumed sequence is unique and contiguous, then consume the freshest row",
        "missing_or_duplicate_middle_sequence_fails_closed": True,
        "freshest_sample_age_gate_unchanged": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import importlib.util,json,time
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
def load():
 p=ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"
 s=importlib.util.spec_from_file_location("v20core",str(p));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def row(i,attempt,now):
 return {"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":attempt,"sequence":i,"epoch_seconds":now,"completed_total":i,"query_duration_ms":1.0}
def test_v20_catches_up_only_over_complete_unique_contiguous_suffix(tmp_path):
 m=load();a="attempt-abcdefghijklmnopqrstuvwxyz0123456789";now=time.time();p=tmp_path/"m.jsonl"
 p.write_text("".join(json.dumps(row(i,a,now))+"\\n" for i in range(5)))
 assert m.workload_metric_sample(p,a,0,0.1)["sequence"]==4
 p.write_text("".join(json.dumps(row(i,a,now))+"\\n" for i in (0,1,3,4)))
 with pytest.raises(m.QualificationError,match="contiguous"):
  m.workload_metric_sample(p,a,0,0.03)
 p.write_text("".join(json.dumps(row(i,a,now))+"\\n" for i in (0,1,2,2,3)))
 with pytest.raises(m.QualificationError,match="contiguous"):
  m.workload_metric_sample(p,a,0,0.03)
'''


def main():
    parent = load_parent()
    payload = {r: (PARENT / r).read_bytes() for r in parent["files"]}
    modes = {r: stat.S_IMODE((PARENT / r).stat().st_mode) for r in parent["files"]}
    old = b'''            else:\n                expected_sequence = previous_sequence + 1\n                matches = [\n                    row for row in values\n                    if isinstance(row, dict)\n                    and row.get("sequence") == expected_sequence\n                ]\n                if len(matches) != 1:\n                    raise QualificationError(\n                        "workload metric exact next sequence is absent or ambiguous"\n                    )\n                value = matches[0]\n'''
    new = b'''            else:\n                expected_sequence = previous_sequence + 1\n                forward = []\n                for row in values:\n                    if not isinstance(row, dict):\n                        raise QualificationError(\n                            "workload metric row is not an object"\n                        )\n                    raw_sequence = row.get("sequence")\n                    if (isinstance(raw_sequence, bool)\n                            or not isinstance(raw_sequence, int)\n                            or raw_sequence < 0):\n                        raise QualificationError(\n                            "workload metric sequence closure is malformed"\n                        )\n                    if raw_sequence > previous_sequence:\n                        if (row.get("schema_version")\n                                != "mysql_algorithm_c_debug_workload_metric_sample_v1"\n                                or row.get("qualification_attempt_id")\n                                != attempt_id):\n                            raise QualificationError(\n                                "workload metric suffix authority differs"\n                            )\n                        forward.append(row)\n                if not forward:\n                    raise QualificationError(\n                        "workload metric contiguous suffix is absent"\n                    )\n                newest_sequence = max(row["sequence"] for row in forward)\n                counts = {}\n                for row in forward:\n                    sequence_key = row["sequence"]\n                    counts[sequence_key] = counts.get(sequence_key, 0) + 1\n                expected = list(range(expected_sequence, newest_sequence + 1))\n                if (sorted(counts) != expected\n                        or any(counts[key] != 1 for key in expected)):\n                    raise QualificationError(\n                        "workload metric suffix is not unique and contiguous"\n                    )\n                value = forward[-1]\n'''
    payload[CORE] = v19.replace_exact(payload[CORE], old, new, "contiguous catch-up")
    ordered = "tests/test_shared_collector_ordered_stream_v17.py"
    payload[ordered] = payload[ordered].replace(
        b'assert m["workload_stream_consumption_policy"]=="exact_next_sequence_no_latest_row_skip"',
        b'assert m["workload_stream_consumption_policy"]=="contiguous_unique_suffix_catchup_to_freshest"'
    ).replace(b'["sequence"]==1', b'["sequence"]==2')
    epoch = "tests/test_shared_collector_paired_epoch_barrier_v19.py"
    payload[epoch] = payload[epoch].replace(
        b'assert "workload metric exact next sequence is absent or ambiguous" in s',
        b'assert "workload metric suffix is not unique and contiguous" in s'
    ).replace(
        b'assert m["paired_peer_epoch_policy"]=="exact_sequence_zero_then_every_exact_successor"',
        b'assert m["paired_peer_epoch_policy"]=="sequence_zero_in_complete_contiguous_suffix"'
    )
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v19", SCHEMA.encode()
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = v19.canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_CONTIGUOUS_CATCHUP_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "paired_peer_epoch_policy": "sequence_zero_in_complete_contiguous_suffix",
        "workload_stream_consumption_policy": "contiguous_unique_suffix_catchup_to_freshest",
        "missing_or_duplicate_sequence_policy": "fail_closed",
        "maximum_sample_gap_seconds_unchanged": 3.5,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r: v19.sha_bytes(d) for r, d in sorted(payload.items())},
    })
    bundle_id = v19.sha_bytes(v19.canonical(unsigned)); manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    test_result = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative, data in payload.items():
                path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data); os.chmod(path, modes.get(relative, 0o444) & ~0o222)
            raw = v19.canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                v19.sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v20-test-")); scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run([str(PYTHON), "-m", "pytest", "-q", *tests],
                                        cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=180)
                if result.returncode:
                    raise v19.BuildError("v20 tests failed:\n" + result.stdout[-16000:])
                test_result = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            v19.clean(stage); v19.freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text()); check = dict(published); check.pop("bundle_id", None)
    if (published != manifest or v19.sha_bytes(v19.canonical(check)) != bundle_id
            or v19.file_map(destination) != manifest["files"]
            or any(stat.S_IMODE(p.stat().st_mode) & 0o222
                   for p in [destination, *destination.rglob("*")])):
        raise v19.BuildError("published v20 differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V20",
                      "bundle":str(destination),"bundle_id":bundle_id,
                      "bundle_manifest_sha256":v19.digest(destination/"bundle_manifest.json"),
                      "verified_files":len(manifest["files"]),"tests":test_result,
                      "formal_launch_authorized":False,"accepted_round_authorized":False},
                     indent=2,sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (v19.BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
