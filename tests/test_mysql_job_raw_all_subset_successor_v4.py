from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = (
    ROOT / "shell/build_mysql_job_raw_all_subset_three_level_successor_v4.py"
)


def load_builder():
    spec = importlib.util.spec_from_file_location("job_v4_builder", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_is_idempotent_strict_and_invalidates_e126():
    builder = load_builder()
    first = builder.publish()
    second = builder.publish()
    assert first == second
    manifest = builder.verify_bundle(first)
    assert manifest["bundle_id"] == first.name
    assert manifest["algorithm_c_contract"] == builder.ALGORITHM_C_CONTRACT
    assert manifest["invalidated_e126_bundle_id"] == builder.PARENT_ID
    assert manifest["invalidated_e126_bundle_credit"] == 0
    assert manifest["invalidated_e126_material_attempt"] == (
        builder.INVALID_ATTEMPT
    )
    assert manifest["fresh_r0_required"] is True
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert {
        "qualification/gradient_descent_optimizer.py",
        "qualification/experience_table_subset_optimizer.py",
        "qualification/mysql_parameter_optimizer.py",
        "qualification/mysql_plan_clustering.py",
        "qualification/mysql_three_level_fingerprint.py",
        "tests/test_mysql_job_module_closure_successor_v4.py",
    } <= set(manifest["files"])
    invalidation = json.loads(
        (
            first
            / "authority/mysql_job_e126_algorithm_and_materials_"
            "invalidation_20260731.json"
        ).read_text(encoding="utf-8")
    )
    assert invalidation["bundle_id"] == builder.PARENT_ID
    assert invalidation["accepted_round_credit"] == 0
    assert invalidation["reuse_prohibited"] is True
