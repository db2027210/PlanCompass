#!/usr/bin/env python3
"""Seal the shared collector with a paired-workload startup barrier."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "c9ecfcae7d06e454aade61381fef8bd8605c48363bf9ee70d3547a5e8de2be6c"
PARENT_SHA = "3616c1b8b80a18d7dddfac416003c94e2efff51fc2c3b412448bdb4fb3910a8f"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v4" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v5"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v5"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v5.py"
TEST = "tests/test_shared_collector_paired_startup_barrier_v5.py"
INVALIDATION = "authority/paired_v4e_v4d_startup_barrier_invalidation.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or digest(path) != PARENT_SHA
        or sha(canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v4"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("shared collector v4 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("shared collector v4 closure differs: " + relative)
    return manifest


def patch_core(payload):
    source = payload.decode("utf-8")
    old_barrier = """            started = time.monotonic()
            for index in range(MINIMUM_SAMPLES):
"""
    new_barrier = """            # Paired workloads can have intentionally different warmup
            # durations (TPC-C is 60 seconds while TPC-H starts immediately).
            # Establish presence/freshness before starting the common 180-second
            # window.  Do not consume the sequence: index zero must bind the
            # same current marker and later indexes still require strict growth.
            if peer_metric_binding is not None:
                workload_metric_sample(
                    Path(
                        peer_metric_binding[
                            \"workload_metric_stream_path\"
                        ]
                    ),
                    peer_metric_binding[\"qualification_attempt_id\"],
                    None,
                    timeout_seconds=90.0,
                )
            started = time.monotonic()
            for index in range(MINIMUM_SAMPLES):
"""
    source = replace_once(
        source, old_barrier, new_barrier, "paired startup barrier"
    )
    old_poll = """                        peer_metric_binding[\"qualification_attempt_id\"],
                        peer_previous_sequence,
                    )
"""
    new_poll = """                        peer_metric_binding[\"qualification_attempt_id\"],
                        peer_previous_sequence,
                        timeout_seconds=2.5,
                    )
"""
    return replace_once(
        source, old_poll, new_poll, "paired steady-state metric poll"
    ).encode("utf-8")


def patch_inherited_tests(relative, payload):
    if relative.startswith("tests/"):
        return payload.replace(
            b"mysql_tpch_tpcc_shared_collector_bundle_v4",
            SCHEMA.encode("ascii"),
        )
    return payload


TEST_SOURCE = b'''from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v5_paired_startup_barrier_and_strict_poll():
 m=json.loads((ROOT/"bundle_manifest.json").read_text()); assert m["bundle_id"]==ROOT.name
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v5"
 for r,h in m["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "timeout_seconds=90.0" in core
 assert "timeout_seconds=2.5" in core
 assert "peer_previous_sequence = None" in core
 assert 'if previous_sequence is not None and sequence <= previous_sequence:' in core
 assert "MAXIMUM_SAMPLE_GAP_SECONDS = 1.5" in core
'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v5")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {
        relative: patch_inherited_tests(relative, (PARENT / relative).read_bytes())
        for relative in parent["files"]
    }
    modes = {
        relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
        for relative in parent["files"]
    }
    payload[CORE] = patch_core((PARENT / CORE).read_bytes())
    modes[CORE] = 0o555
    payload[BUILDER] = Path(__file__).read_bytes()
    modes[BUILDER] = 0o444
    payload[TEST] = TEST_SOURCE
    modes[TEST] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_shared_collector_paired_startup_barrier_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT_RAW_DIAGNOSTIC_RETAINED",
        "collector_bundle_id": PARENT_ID,
        "flows": ["A_paired_v4e", "B_paired_v4d"],
        "failure": "ASYMMETRIC_WARMUP_WITHOUT_PAIRED_FIRST_MARKER_BARRIER",
        "masked_shutdown_error":
            "debug qualification workload did not exit gracefully within 75s",
        "fresh_four_flow_inputs_required": True,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "postgresql_mutation": False,
    })
    modes[INVALIDATION] = 0o444
    identity = dict(parent)
    identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_PAIRED_STARTUP_BARRIER_COLLECTOR_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "paired_first_marker_barrier_seconds": 90.0,
        "paired_steady_state_poll_seconds": 2.5,
        "native_marker_age_limit_seconds": 1.5,
        "strict_peer_sequence_growth_required": True,
        "same_collector_required_for_all_four_flows": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(identity))
    manifest = {**identity, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        raise BuildError("immutable shared collector v5 already exists")
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, modes[relative] & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(raw) + "  bundle_manifest.json\n", encoding="ascii"
        )
        freeze(stage)
        os.rename(stage, target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({
        "status": manifest["status"],
        "bundle": str(target),
        "bundle_id": bundle_id,
        "manifest_sha256": digest(target / "bundle_manifest.json"),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
