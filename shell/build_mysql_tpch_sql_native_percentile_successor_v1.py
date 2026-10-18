#!/usr/bin/env python3
"""Build the immutable TPC-H SQL-native completed-latency successor.

This builder is strictly offline.  It copies the exact c4fd221 predecessor,
repairs the collector's SQL-native latency-shape validator, seals focused
regressions, and grants no qualification, R0, formal, or accepted-round credit.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "c4fd221bb397ad7077e2cc4463d8dd93514fc9d3ed89772d45c33b2332e401cd"
PARENT_MANIFEST_SHA256 = (
    "e4da2fe2b714177e10fdaab5da0d39686ba13f40727f122245a65b88166e761f"
)
PARENT = ROOT / "artifacts/mysql_tpch_workload_lifetime_successor_v3" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
FORMAL = "tpch_formal10_contract.json"
SELF_CONTAINED_VERIFIER = (
    "builder/build_mysql_tpch_socket_identity_successor_v1.py"
)
BUILDER = "builder/build_mysql_tpch_sql_native_percentile_successor_v1.py"
REMEDIATION = "tpch_sql_native_percentile_remediation_v1.json"
INVALIDATION = "invalidated_c4fd_sql_native_completed_latency_contract.json"
OFFLINE_TEST = "offline_tests/test_sql_native_completed_latency_contract_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(payload: bytes, label: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise BuildError(label + " is not an object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json",
            "bundle_manifest.json.sha256",
        ):
            result[relative] = sha_file(path)
    return result


def replace_exact(
    payload: bytes, old: str, new: str, count: int, label: str
) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != count:
        raise BuildError(label + " predecessor cardinality differs")
    return text.replace(old, new).encode("utf-8")


def patch_collector(payload: bytes) -> bytes:
    old = '''                if (
                    native_kind == "sql_query_native"
                    and set(latency) != {"query_duration_ms"}
                ):
                    raise QualificationError(
                        "native SQL workload requires query duration"
                    )
'''
    new = '''                # Native SQL exposes the elapsed duration of an in-flight
                # query before its first completion, then may expose the
                # complete p50/p95/p99 latency tuple once real completions
                # exist.  The shape validation above already rejects partial,
                # unordered, non-finite, or negative percentile evidence.
                if native_kind == "sql_query_native" and set(latency) not in (
                    {"query_duration_ms"},
                    set(percentile_keys),
                ):
                    raise QualificationError(
                        "native SQL latency shape differs"
                    )
'''
    return replace_exact(
        payload, old, new, 1, "SQL-native completed latency validator"
    )


def patch_self_contained_verifier(payload: bytes) -> bytes:
    """Bind the inherited read-only verifier to this bundle's 43 tests."""
    text = payload.decode("utf-8")
    old = "sealed_bundle_pytest_regression_count\") != 39"
    new = "sealed_bundle_pytest_regression_count\") != 43"
    if text.count(old) != 1:
        raise BuildError("self-contained verifier guard cardinality differs")
    text = text.replace(old, new)
    old = '"pytest_regression_count": 39,'
    new = '"pytest_regression_count": 43,'
    if text.count(old) != 1:
        raise BuildError("self-contained verifier evidence cardinality differs")
    return text.replace(old, new).encode("utf-8")


def offline_test_payload() -> bytes:
    return b'''from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import time
import pytest

BUNDLE = Path(__file__).resolve().parents[1]
QUALIFICATION = BUNDLE / "qualification"
sys.path.insert(0, str(QUALIFICATION))
SPEC = importlib.util.spec_from_file_location(
    "sealed_tpch_collector",
    QUALIFICATION / "mysql_distributed_live_readback_qualification_v1.py",
)
COLLECTOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(COLLECTOR)
ATTEMPT = "sealed-sql-native-" + "a" * 32


def write_row(path, **overrides):
    now = time.time()
    row = {
        "schema_version": "mysql_algorithm_c_debug_workload_metric_sample_v1",
        "qualification_attempt_id": ATTEMPT,
        "sequence": 7,
        "epoch_seconds": now,
        "completed_total": 1,
        "latency_p50_ms": 1.0,
        "latency_p95_ms": 2.0,
        "latency_p99_ms": 3.0,
        "native_adapter_kind": "sql_query_native",
        "native_marker_epoch_ms": int(round(now * 1000.0)),
    }
    row.update(overrides)
    path.write_text(json.dumps(row) + "\\n", encoding="utf-8")


def test_complete_percentiles_pass_after_real_completion(tmp_path):
    path = tmp_path / "metric.jsonl"
    write_row(path)
    row = COLLECTOR.workload_metric_sample(path, ATTEMPT, 6, timeout_seconds=0)
    assert row["completed_total"] == 1
    assert (row["latency_p50_ms"], row["latency_p95_ms"], row["latency_p99_ms"]) == (1.0, 2.0, 3.0)


def test_partial_percentiles_remain_fail_closed(tmp_path):
    path = tmp_path / "metric.jsonl"
    write_row(path)
    row = json.loads(path.read_text())
    row.pop("latency_p99_ms")
    path.write_text(json.dumps(row) + "\\n", encoding="utf-8")
    with pytest.raises(COLLECTOR.QualificationError, match="missing/stale/timed out"):
        COLLECTOR.workload_metric_sample(path, ATTEMPT, 6, timeout_seconds=0)


def test_stale_sequence_remains_fail_closed(tmp_path):
    path = tmp_path / "metric.jsonl"
    write_row(path, sequence=7)
    with pytest.raises(COLLECTOR.QualificationError, match="missing/stale/timed out"):
        COLLECTOR.workload_metric_sample(path, ATTEMPT, 7, timeout_seconds=0)


def test_stale_epoch_remains_fail_closed(tmp_path):
    path = tmp_path / "metric.jsonl"
    old = time.time() - 10.0
    write_row(path, epoch_seconds=old, native_marker_epoch_ms=int(round(old * 1000.0)))
    with pytest.raises(COLLECTOR.QualificationError, match="missing/stale/timed out"):
        COLLECTOR.workload_metric_sample(path, ATTEMPT, 6, timeout_seconds=0)
'''


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path.read_bytes(), "parent manifest")
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = verify_parent()
    content = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    parent_collector_sha = sha_bytes(content[COLLECTOR])
    if parent_collector_sha != parent["component_hashes"]["collector"]:
        raise BuildError("parent collector component identity differs")
    content[COLLECTOR] = patch_collector(content[COLLECTOR])
    content[SELF_CONTAINED_VERIFIER] = patch_self_contained_verifier(
        content[SELF_CONTAINED_VERIFIER]
    )
    content[BUILDER] = HERE.read_bytes()
    content[OFFLINE_TEST] = offline_test_payload()
    content[REMEDIATION] = canonical({
        "schema_version": "mysql_tpch_sql_native_percentile_remediation_v1",
        "status": "OFFLINE_REMEDIATED_FRESH_BOOTSTRAP_AND_QUALIFICATION_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_sql_native_latency_shapes": [
            ["query_duration_ms"],
            ["latency_p50_ms", "latency_p95_ms", "latency_p99_ms"],
        ],
        "partial_percentiles_result": "FAIL",
        "unordered_nonfinite_negative_percentiles_result": "FAIL",
        "missing_stale_or_timed_out_sample_result": "FAIL",
        "failed_parent_attempt_credit": 0,
        "fresh_bootstrap_required": True,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[INVALIDATION] = canonical({
        "schema_version": "mysql_tpch_sql_native_completed_latency_invalidation_v1",
        "status": "INVALIDATED_DO_NOT_RUN_OR_CREDIT",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_attempt_token":
            "tpch-v2-20260803t011702z-0432f938f0e2b7d1e6dbdabc",
        "failure_at_cst": "2026-08-03T09:40:46+08:00",
        "reason": (
            "the SQL adapter switched from in-flight query_duration_ms to a "
            "complete p50/p95/p99 tuple after the first real completion, but "
            "the downstream collector rejected that valid completed shape"
        ),
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })

    component_hashes = dict(parent["component_hashes"])
    component_hashes["collector"] = sha_bytes(content[COLLECTOR])
    component_hashes["self_contained_read_only_verifier"] = sha_bytes(
        content[SELF_CONTAINED_VERIFIER]
    )
    formal = load(content[FORMAL], FORMAL)
    qualification_hashes = dict(formal["qualification_hashes"])
    qualification_hashes["collector"] = component_hashes["collector"]
    formal["qualification_hashes"] = qualification_hashes
    content[FORMAL] = canonical(formal)

    files = {
        relative: sha_bytes(payload)
        for relative, payload in sorted(content.items())
    }
    identity = {
        key: value
        for key, value in parent.items()
        if key not in ("bundle_id", "files")
    }
    identity.update({
        "successor_generation": "sql_native_completed_latency_contract_v1",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(HERE),
        "component_hashes": component_hashes,
        "formal10_contract_sha256": files[FORMAL],
        "sql_native_percentile_remediation_sha256": files[REMEDIATION],
        "invalidated_parent_sql_native_latency_sha256": files[INVALIDATION],
        "sql_native_completed_latency_contract": {
            "in_flight_shape": ["query_duration_ms"],
            "completed_shape": [
                "latency_p50_ms",
                "latency_p95_ms",
                "latency_p99_ms",
            ],
            "complete_ordered_finite_nonnegative_required": True,
            "missing_stale_timeout_policy": "fail_closed",
        },
        "sealed_bundle_pytest_regression_count":
            int(parent["sealed_bundle_pytest_regression_count"]) + 4,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(
            prefix=".tpch-sql-native-percentile-v1.", dir=OUTPUT_ROOT
        ))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n",
                encoding="ascii",
            )
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    actual = relative_files(destination)
    unsigned = {
        key: value for key, value in manifest.items() if key != "bundle_id"
    }
    if (
        actual != files
        or destination.name != bundle_id
        or sha_bytes(canonical(unsigned)) != bundle_id
        or sha_file(destination / "bundle_manifest.json")
        != (destination / "bundle_manifest.json.sha256")
        .read_text(encoding="ascii")
        .split()[0]
    ):
        raise BuildError("published successor identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_TPCH_SQL_NATIVE_PERCENTILE_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "collector_sha256": component_hashes["collector"],
        "sealed_bundle_pytest_regression_count":
            identity["sealed_bundle_pytest_regression_count"],
        "fresh_bootstrap_required": True,
        "fresh_qualification_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, UnicodeError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
