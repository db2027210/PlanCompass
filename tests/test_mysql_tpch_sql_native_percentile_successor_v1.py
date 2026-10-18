from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "shell/build_mysql_tpch_sql_native_percentile_successor_v1.py"
SPEC = importlib.util.spec_from_file_location("tpch_sql_native_builder", BUILDER_PATH)
BUILDER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BUILDER)


def test_successor_is_content_addressed_read_only_and_fail_closed() -> None:
    bundle = BUILDER.build()
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id")
    assert bundle.name == manifest["bundle_id"]
    assert hashlib.sha256(BUILDER.canonical(unsigned)).hexdigest() == bundle.name
    assert manifest["predecessor_bundle_id"] == BUILDER.PARENT_ID
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["qualification_credit"] == 0
    assert manifest["r0_credit"] == 0
    assert manifest["accepted_rounds_credit"] == 0
    assert manifest["sql_native_completed_latency_contract"] == {
        "in_flight_shape": ["query_duration_ms"],
        "completed_shape": [
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_p99_ms",
        ],
        "complete_ordered_finite_nonnegative_required": True,
        "missing_stale_timeout_policy": "fail_closed",
    }
    assert BUILDER.relative_files(bundle) == manifest["files"]
    for path in (bundle, *bundle.rglob("*")):
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


def test_successor_updates_only_bound_collector_contract() -> None:
    bundle = BUILDER.build()
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    parent = json.loads((BUILDER.PARENT / "bundle_manifest.json").read_text())
    changed_parent_files = {
        relative
        for relative, digest in parent["files"].items()
        if manifest["files"].get(relative) != digest
    }
    assert changed_parent_files == {
        BUILDER.COLLECTOR,
        BUILDER.FORMAL,
        BUILDER.SELF_CONTAINED_VERIFIER,
    }
    collector = (bundle / BUILDER.COLLECTOR).read_text()
    assert "native SQL workload requires query duration" not in collector
    assert "native SQL latency shape differs" in collector
    formal = json.loads((bundle / BUILDER.FORMAL).read_text())
    assert formal["qualification_hashes"]["collector"] == manifest[
        "component_hashes"
    ]["collector"]
