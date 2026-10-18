#!/usr/bin/env python3
"""Seal the shared collector with race-safe native-marker polling and readable stop requests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "933d01502e0c57b823edf6e8894d94349216eefb93ed57f4dd369271454d53e9"
PARENT_SHA = "a61155338635748616300610268725e02b1e8e8f1b67bd9d3e04a2bf1f123e7b"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v3" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v4"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v4"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
ADAPTER = "qualification/mysql_distributed_workload_metric_adapter_v1.py"
ARITY_TEST = "tests/test_shared_collector_tpch_client_readback_arity_v2.py"
IDENTITY_TEST = "tests/test_shared_collector_full_bundle_identity_v3.py"
NEW_TEST = "tests/test_shared_collector_native_poll_and_stop_v4.py"
INVALIDATION = "authority/paired_v6_v8_metric_poll_invalidation.json"


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
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(path) != PARENT_SHA
            or sha(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_tpch_tpcc_shared_collector_bundle_v3"):
        raise BuildError("shared collector v3 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("shared collector v3 closure differs: " + relative)
    return manifest


def patch_adapter(payload):
    source = payload.decode("utf-8")
    return replace_once(source, "    timeout_seconds=1.0,\n",
                        "    timeout_seconds=2.5,\n",
                        "native marker polling timeout").encode("utf-8")


def patch_core(payload):
    source = payload.decode("utf-8")
    return replace_once(
        source,
        "descriptor = os.open(str(stop_host), flags, 0o600)",
        "descriptor = os.open(str(stop_host), flags, 0o444)",
        "BenchBase stop request readability",
    ).encode("utf-8")


def patch_inherited_test(payload):
    source = payload.decode("utf-8")
    return source.replace(
        "mysql_tpch_tpcc_shared_collector_bundle_v3", SCHEMA
    ).encode("utf-8")


TEST_SOURCE = b'''from __future__ import annotations\nimport hashlib,json\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_v4_native_poll_and_stop_contract():\n m=json.loads((ROOT/"bundle_manifest.json").read_text()); assert m["bundle_id"]==ROOT.name\n assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v4"\n for r,h in m["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h\n adapter=(ROOT/"qualification/mysql_distributed_workload_metric_adapter_v1.py").read_text()\n core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()\n assert "timeout_seconds=2.5" in adapter and "timeout_seconds=1.0" not in adapter\n assert "1500.0" in adapter\n assert "descriptor = os.open(str(stop_host), flags, 0o444)" in core\n assert "descriptor = os.open(str(stop_host), flags, 0o600)" not in core\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v4")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in parent["files"]}
    payload[ADAPTER] = patch_adapter(payload[ADAPTER]); modes[ADAPTER] = 0o555
    payload[CORE] = patch_core(payload[CORE]); modes[CORE] = 0o555
    for relative in (ARITY_TEST, IDENTITY_TEST):
        payload[relative] = patch_inherited_test(payload[relative]); modes[relative] = 0o444
    payload[NEW_TEST] = TEST_SOURCE; modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_shared_collector_paired_metric_poll_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT_RAW_DIAGNOSTIC_RETAINED",
        "collector_bundle_id": PARENT_ID,
        "flows": ["A_paired_v6", "B_paired_v8"],
        "failures": [
            "NATIVE_ONE_HZ_MARKER_POLL_TIMEOUT_EQUAL_TO_MARKER_PERIOD",
            "BENCHBASE_STOP_REQUEST_ROOT_ONLY_MODE",
        ],
        "fresh_four_flow_inputs_required": True,
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "postgresql_mutation": False,
    }); modes[INVALIDATION] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_RACE_SAFE_NATIVE_POLL_READABLE_STOP_COLLECTOR_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "native_marker_period_seconds": 1.0,
        "native_marker_poll_timeout_seconds": 2.5,
        "native_marker_age_limit_seconds": 1.5,
        "benchbase_stop_request_mode": "0444",
        "same_collector_required_for_all_four_flows": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(identity)); manifest = {**identity, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True); target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        raise BuildError("immutable shared collector v4 already exists")
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); os.chmod(path, modes[relative] & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.rename(stage, target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status": manifest["status"], "bundle": str(target),
                      "bundle_id": bundle_id,
                      "manifest_sha256": digest(target / "bundle_manifest.json"),
                      "formal_launch_authorized": False,
                      "accepted_round_authorized": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
