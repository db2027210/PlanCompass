#!/usr/bin/env python3
"""Build v25 with a fail-closed paired sample-completion barrier."""
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
    "v24", ROOT / "shell/build_mysql_tpch_tpcc_shared_collector_v24.py"
)
v24 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v24)
u = v24.u

PARENT_ID = "d72dec70cd62df6cce2dc6f60293740321bc808bf4bd22572a1b58180bc274ff"
PARENT_SHA = "14e1b92e5d0b57b569842962d283174966248bc45d89381185c9122b59f49a11"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v24" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v25"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v25.py"
INVALIDATION = "authority/v24a_paired_completion_invalidation.json"
TEST = "tests/test_shared_collector_paired_completion_v25.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or u.digest(path) != PARENT_SHA
        or u.sha_bytes(u.canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v24"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise u.BuildError("v24 parent differs")
    for relative, expected in manifest["files"].items():
        item = PARENT / relative
        if item.is_symlink() or not item.is_file() or u.digest(item) != expected:
            raise u.BuildError("v24 closure differs: " + relative)
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_shared_v24a_paired_completion_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V25",
        "failed_attempts": [
            "tpch-a-paired-v24a-20260801t231000p08-6c3809b8",
            "tpcc-b-paired-v24a-20260801t231000p08-7e2e13aa",
        ],
        "primary_failure": (
            "TPC-H stopped its native metric producer after its own sample 180 "
            "while the slower TPC-C collector still required fresh peer samples"
        ),
        "cascade_failure": (
            "TPC-C failed closed on the now-stale TPC-H peer metric; TPC-H then "
            "exceeded graceful quiescence because its debug SQL guard was 600s"
        ),
        "repair": (
            "atomically publish an own completion marker after 181 complete samples "
            "and keep the native producer alive until the reciprocal marker is "
            "validated; issue the next debug SQL guard at 360s"
        ),
        "paired_completion_timeout_seconds": 60,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v25_paired_completion_barrier_is_fail_closed():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v25"
 assert m["paired_completion_barrier"] is True
 assert m["paired_completion_timeout_seconds"]==60
 assert m["minimum_samples"]==181 and m["minimum_duration_seconds"]==180
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "mysql_algorithm_c_paired_collector_completion_v25" in core
 assert "paired collector completion marker timed out" in core
 barrier=core.index("own_completion_path = Path")
 producer_stop=core.index("native_metric_producer_stop.set()")
 assert barrier < producer_stop
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
'''


BARRIER = b'''                samples.append(dynamic_sample)\n            if peer_metric_binding is not None:\n                own_completion_path = Path(\n                    str(metric_path) + ".collector-complete-v25.json"\n                )\n                peer_metric_path = Path(\n                    peer_metric_binding["workload_metric_stream_path"]\n                )\n                peer_completion_path = Path(\n                    str(peer_metric_path) + ".collector-complete-v25.json"\n                )\n                own_completion = {\n                    "schema_version":\n                        "mysql_algorithm_c_paired_collector_completion_v25",\n                    "qualification_attempt_id":\n                        spec["qualification_attempt_id"],\n                    "sample_count": len(samples),\n                    "last_index": MINIMUM_SAMPLES - 1,\n                }\n                completion_payload = canonical(own_completion)\n                completion_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL\n                if hasattr(os, "O_NOFOLLOW"):\n                    completion_flags |= os.O_NOFOLLOW\n                try:\n                    completion_descriptor = os.open(\n                        str(own_completion_path), completion_flags, 0o600\n                    )\n                    try:\n                        os.write(completion_descriptor, completion_payload)\n                        os.fsync(completion_descriptor)\n                    finally:\n                        os.close(completion_descriptor)\n                except OSError as exc:\n                    raise QualificationError(\n                        "own paired collector completion marker failed: %s" % exc\n                    )\n                completion_deadline = time.monotonic() + 60.0\n                while True:\n                    if native_metric_producer_state["error"] is not None:\n                        raise QualificationError(\n                            "native workload metric producer failed during paired "\n                            "completion: %s"\n                            % native_metric_producer_state["error"]\n                        )\n                    if process.poll() is not None:\n                        raise QualificationError(\n                            "debug qualification workload exited during paired "\n                            "completion barrier"\n                        )\n                    if peer_completion_path.is_symlink():\n                        raise QualificationError(\n                            "peer paired collector completion marker is unsafe"\n                        )\n                    if peer_completion_path.is_file():\n                        try:\n                            peer_completion = json.loads(\n                                peer_completion_path.read_text(encoding="utf-8")\n                            )\n                        except (OSError, UnicodeError, ValueError) as exc:\n                            raise QualificationError(\n                                "peer paired collector completion marker is invalid: "\n                                "%s" % exc\n                            )\n                        expected_completion = {\n                            "schema_version":\n+                                "mysql_algorithm_c_paired_collector_completion_v25",\n                            "qualification_attempt_id":\n                                peer_metric_binding["qualification_attempt_id"],\n                            "sample_count": MINIMUM_SAMPLES,\n                            "last_index": MINIMUM_SAMPLES - 1,\n                        }\n                        if peer_completion != expected_completion:\n                            raise QualificationError(\n                                "peer paired collector completion authority differs"\n                            )\n                        workload_metric_sample(\n                            peer_metric_path,\n                            peer_metric_binding["qualification_attempt_id"],\n                            peer_previous_sequence,\n                            timeout_seconds=2.5,\n                            allow_fresh_repeat=True,\n                        )\n                        break\n                    if time.monotonic() >= completion_deadline:\n                        raise QualificationError(\n                            "paired collector completion marker timed out"\n                        )\n                    time.sleep(0.05)\n'''.replace(b"\n+", b"\n")


def main():
    parent = load_parent()
    payload = {r: (PARENT / r).read_bytes() for r in parent["files"]}
    modes = {r: stat.S_IMODE((PARENT / r).stat().st_mode) for r in parent["files"]}
    payload[CORE] = u.replace_exact(
        payload[CORE],
        b"                samples.append(dynamic_sample)\n        finally:\n",
        BARRIER + b"        finally:\n",
        "paired completion barrier",
    )
    for relative, content in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = content.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v24",
                b"mysql_tpch_tpcc_shared_collector_bundle_v25",
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = u.canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": "mysql_tpch_tpcc_shared_collector_bundle_v25",
        "status": "SEALED_PAIRED_COMPLETION_BARRIER_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "paired_completion_barrier": True,
        "paired_completion_timeout_seconds": 60,
        "minimum_samples": 181,
        "minimum_duration_seconds": 180,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r: u.sha_bytes(d) for r, d in sorted(payload.items())},
    })
    bundle_id = u.sha_bytes(u.canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    result_text = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, content in payload.items():
                item = stage / relative
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_bytes(content)
                os.chmod(item, modes.get(relative, 0o444) & ~0o222)
            raw = u.canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                u.sha_bytes(raw) + "  bundle_manifest.json\n"
            )
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v25-test-"))
            scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run(
                    [str(PYTHON), "-m", "pytest", "-q", *tests],
                    cwd=ROOT, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=180,
                )
                if result.returncode:
                    raise u.BuildError("v25 tests failed:\n" + result.stdout[-16000:])
                result_text = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            u.clean(stage); u.freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text())
    unsigned_check = dict(published); unsigned_check.pop("bundle_id", None)
    if (
        published != manifest
        or u.sha_bytes(u.canonical(unsigned_check)) != bundle_id
        or u.file_map(destination) != manifest["files"]
        or any(stat.S_IMODE(p.stat().st_mode) & 0o222 for p in [destination, *destination.rglob("*")])
    ):
        raise u.BuildError("published v25 differs")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_SHARED_COLLECTOR_V25",
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": u.digest(destination / "bundle_manifest.json"),
        "verified_files": len(manifest["files"]),
        "tests": result_text,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (u.BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
