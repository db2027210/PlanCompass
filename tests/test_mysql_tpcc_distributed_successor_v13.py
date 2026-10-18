from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "shell/build_mysql_tpcc_distributed_successor_v13.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("tpcc_v13_builder", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v13_is_idempotent_hash_closed_and_zero_credit(tmp_path: Path) -> None:
    builder = load_builder()
    first = builder.build(tmp_path)
    second = builder.build(tmp_path)
    assert first == second

    manifest = builder.verify_successor(first)
    assert first.name == manifest["bundle_id"]
    assert manifest["schema_version"] == (
        "mysql_tpcc_typed_distributed_successor_bundle_v13"
    )
    assert manifest["supersedes_bundle_id"] == builder.BASE_ID
    assert manifest["typed_runtime_bundle_id"] == builder.RUNTIME_ID
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
    assert manifest["remote_actions"] == 0
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["real_production_trace_required"] is True
    assert manifest["qps_resource_input_prohibited"] is True

    invalidation = load(
        first
        / "authority"
        / "mysql_tpcc_v12_d377_typed_capture_join_invalidation_20260731.json"
    )
    assert invalidation["bundle_id"] == builder.BASE_ID
    assert invalidation["capture_credit"] == 0
    assert invalidation["fresh_r0_credit"] == 0
    assert invalidation["accepted_rounds_credit"] == 0
    assert invalidation["typed_capture_authorized"] is False
    assert invalidation["workload_result_reusable"] is False

    recorded = (first / "bundle_manifest.json.sha256").read_text(
        encoding="ascii"
    ).split()[0]
    actual = hashlib.sha256(
        (first / "bundle_manifest.json").read_bytes()
    ).hexdigest()
    assert recorded == actual
    assert not list(first.rglob("*.tmp"))
    assert not any(path.is_symlink() for path in first.rglob("*"))


def test_v13_requires_prelaunch_consumers_and_real_sensitive_join(
    tmp_path: Path,
) -> None:
    builder = load_builder()
    bundle = builder.build(tmp_path)
    runtime = load(bundle / "distributed_runtime_contract.json")
    invariants = runtime["typed_runtime_invariants"]
    assert invariants["performance_schema_consumers_enabled_before_workload"]
    assert invariants["real_sensitive_join_authority_hash_bound"]
    assert invariants["synthetic_or_fabricated_join_prohibited"]
    assert invariants["qps_resource_input_prohibited"]

    template = load(
        bundle / "qualification/typed_capture_command_template_v2.json"
    )
    environment = template["environment"]
    assert template["qps_resource_input_prohibited"] is True
    assert environment["MYSQL_TPCC_TYPED_DEFAULTS_FILE"] == (
        "${MYSQL_DEFAULTS_FILE}"
    )
    assert not any(
        name in environment
        for name in (
            "MYSQL_TPCC_TYPED_METRICS_USER",
            "MYSQL_TPCC_TYPED_METRICS_PASSWORD",
            "MYSQL_TPCC_TYPED_TARGET_USER",
            "MYSQL_TPCC_TYPED_TARGET_PASSWORD",
        )
    )
    join = template["sensitive_stock_level_real_join_authority"]
    assert join["owner_trace_header_and_lock_required"] is True
    assert join["capture_token_and_manifest_hash_bound"] is True
    assert join["synthetic_or_fabricated_join_prohibited"] is True

    orchestration = (
        bundle
        / "predecessor/orchestration/formal_tpcc_composite_workload.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "enable_typed_consumers_before_workload",
        "PASS_ENABLED_BEFORE_WORKLOAD_START",
        "args.defaults_extra_file.is_symlink()",
        "wait_for_real_typed_join_authority",
        "PASS_REAL_OWNER_TRACE_JOIN",
        "SensitiveStockLevel/item-in-orderline-w1",
        "synthetic_or_fabricated_join_prohibited",
    ):
        assert marker in orchestration

    java = (
        bundle / "predecessor/source/TpccTypedTrace.java"
    ).read_text(encoding="utf-8")
    assert "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_PATH" in java
    assert "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_SHA256" in java
