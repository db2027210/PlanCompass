#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parameter_optimizer import (  # noqa: E402
    COST_PARAM_KEYS,
    _online_update_experience_table_and_rerank,
    load_explicit_experience_table_contract,
)


ARTIFACT_PATH = (
    PROJECT_ROOT
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1"
    / "experience_table_T_job_memory_v1.json"
)
ARTIFACT_SHA256 = "f5bfe293923e3c1122ca7363f3dc9b1b37d5f9c430540976535222188d8d7261"
TABLE_SHA256 = "a2fca76dd0ad04758ceebebb29a675ec43f1e52c5e820efca4b25a5fbaeaf07b"
V2_ARTIFACT_PATH = ARTIFACT_PATH.with_name("experience_table_T_job_memory_v2.json")
SEMANTIC_PRIOR_PATH = ARTIFACT_PATH.with_name(
    "experience_table_T_tpcc_cpu_v3_semantic_prior.json"
)


def make_config(artifact_path: Path = ARTIFACT_PATH) -> dict:
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return {
        "sensitivity_table": copy.deepcopy(artifact["table"]),
        "experience_table_contract": {
            "required": True,
            "disable_fallback": True,
            "fallback_enabled": False,
            "artifact_path": str(artifact_path),
            "artifact_sha256": ARTIFACT_SHA256,
            "table_id": "pg5_job_memory_experience_table_T_v1",
            "version": "2026-07-22.1",
            "table_sha256": TABLE_SHA256,
            "active_params": list(COST_PARAM_KEYS),
            "finite_difference_direction_enabled": False,
            "random_direction_enabled": False,
        },
        "optimizer": {
            "gradient": {
                "require_explicit_sensitivity_table": True,
            }
        },
    }


def assert_rejected(cfg: dict, expected_message: str) -> None:
    try:
        load_explicit_experience_table_contract(
            cfg,
            str(PROJECT_ROOT / "unused_config.json"),
            COST_PARAM_KEYS,
        )
    except ValueError as exc:
        assert expected_message in str(exc), str(exc)
    else:
        raise AssertionError("invalid formal experience-table contract must fail closed")


def test_exact_artifact_and_full_config_table_pass() -> None:
    table, binding = load_explicit_experience_table_contract(
        make_config(),
        str(PROJECT_ROOT / "unused_config.json"),
        COST_PARAM_KEYS,
    )
    assert table is not None
    assert list(table) == COST_PARAM_KEYS
    assert binding["artifact_sha256"] == ARTIFACT_SHA256
    assert binding["table_sha256"] == TABLE_SHA256
    assert binding["version"] == "2026-07-22.1"
    assert binding["fallback_disabled"] is True
    assert binding["finite_difference_direction_enabled"] is False
    assert binding["random_direction_enabled"] is False


def test_partial_runtime_table_is_rejected() -> None:
    cfg = make_config()
    del cfg["sensitivity_table"]["parallel_tuple_cost"]
    assert_rejected(cfg, "")


def test_optimizer_level_override_is_rejected() -> None:
    cfg = make_config()
    cfg["optimizer"]["sensitivity_table"] = copy.deepcopy(
        cfg["sensitivity_table"]
    )
    assert_rejected(cfg, " optimizer.sensitivity_table ")


def test_artifact_byte_tampering_is_rejected_before_table_use() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        tampered_path = Path(temp_dir) / "T.json"
        tampered_path.write_bytes(ARTIFACT_PATH.read_bytes() + b"\n")
        cfg = make_config(tampered_path)
        assert_rejected(cfg, "artifact SHA256 ")


def test_direction_fallback_switches_must_be_explicitly_false() -> None:
    cfg = make_config()
    cfg["experience_table_contract"]["random_direction_enabled"] = True
    assert_rejected(cfg, "random_direction_enabled=false")


def test_ordered_six_param_subset_with_fixed_parameter_passes() -> None:
    six_active = [name for name in COST_PARAM_KEYS if name != "parallel_setup_cost"]
    full = make_config()
    table = {
        name: copy.deepcopy(full["sensitivity_table"][name])
        for name in six_active
    }
    table_sha256 = hashlib.sha256(
        json.dumps(
            table,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    artifact = {
        "schema_version": 1,
        "table_id": "test_six_active",
        "version": "test-v1",
        "active_params": six_active,
        "fixed_params": {"parallel_setup_cost": 1000.0},
        "finite_difference_direction_enabled": False,
        "random_direction_enabled": False,
        "table_sha256": table_sha256,
        "table": table,
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        artifact_path = Path(temp_dir) / "T.json"
        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        cfg = {
            "initial_params": {"parallel_setup_cost": 1000.0},
            "sensitivity_table": table,
            "experience_table_contract": {
                "required": True,
                "disable_fallback": True,
                "fallback_enabled": False,
                "artifact_path": str(artifact_path),
                "artifact_sha256": artifact_sha256,
                "table_id": "test_six_active",
                "version": "test-v1",
                "table_sha256": table_sha256,
                "active_params": six_active,
                "fixed_params": {"parallel_setup_cost": 1000.0},
                "finite_difference_direction_enabled": False,
                "random_direction_enabled": False,
            },
            "optimizer": {
                "gradient": {"require_explicit_sensitivity_table": True}
            },
        }
        parsed, binding = load_explicit_experience_table_contract(
            cfg,
            str(PROJECT_ROOT / "unused_config.json"),
            six_active,
        )
        assert parsed == table
        assert binding["fixed_params"] == {"parallel_setup_cost": 1000.0}


def test_fixed_parameter_must_not_be_active() -> None:
    cfg = make_config()
    cfg["initial_params"] = {"parallel_setup_cost": 1000.0}
    fixed = {"parallel_setup_cost": 1000.0}
    cfg["experience_table_contract"]["fixed_params"] = fixed
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    artifact["fixed_params"] = fixed
    with tempfile.TemporaryDirectory() as temp_dir:
        artifact_path = Path(temp_dir) / "T.json"
        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        cfg["experience_table_contract"]["artifact_path"] = str(artifact_path)
        cfg["experience_table_contract"]["artifact_sha256"] = hashlib.sha256(
            artifact_path.read_bytes()
        ).hexdigest()
        assert_rejected(cfg, " active_params")


def test_v2_qps_confidence_and_evidence_contract_passes() -> None:
    artifact = json.loads(V2_ARTIFACT_PATH.read_text(encoding="utf-8"))
    artifact_sha256 = hashlib.sha256(V2_ARTIFACT_PATH.read_bytes()).hexdigest()
    cfg = {
        "sensitivity_table": copy.deepcopy(artifact["table"]),
        "experience_table_contract": {
            "required": True,
            "disable_fallback": True,
            "fallback_enabled": False,
            "artifact_path": str(V2_ARTIFACT_PATH),
            "artifact_sha256": artifact_sha256,
            "table_id": artifact["table_id"],
            "version": artifact["version"],
            "table_sha256": artifact["table_sha256"],
            "active_params": list(COST_PARAM_KEYS),
            "finite_difference_direction_enabled": False,
            "random_direction_enabled": False,
            "low_confidence_freeze_threshold": 0.5,
        },
        "optimizer": {
            "gradient": {"require_explicit_sensitivity_table": True}
        },
    }
    table, binding = load_explicit_experience_table_contract(
        cfg,
        str(PROJECT_ROOT / "unused_config.json"),
        COST_PARAM_KEYS,
    )
    assert table == artifact["table"]
    assert binding["schema_version"] == 2
    assert binding["low_confidence_freeze_threshold"] == 0.5


def test_first_batch_online_delta_is_frozen_without_group_holdout() -> None:
    table = json.loads(V2_ARTIFACT_PATH.read_text(encoding="utf-8"))["table"]
    baseline_params = {"parallel_setup_cost": 1000.0}
    evaluations = []
    for index, setup_cost in enumerate((800.0, 900.0, 1000.0, 1100.0, 1200.0)):
        evaluations.append(
            {
                "candidate_optimized_params": {
                    "parallel_setup_cost": setup_cost,
                },
                "candidate_final_validation": {
                    "formal_qps": 90.0 + index * 5.0,
                    "utilization_pct": {
                        "cpu": 39.0 + index,
                        "memory": 87.0 - index * 0.5,
                        "io": 36.0 + index,
                    },
                },
            }
        )
    remaining = [
        SimpleNamespace(
            optimized_params={"parallel_setup_cost": 850.0},
            gradient_subset_candidate_rank=6,
        ),
        SimpleNamespace(
            optimized_params={"parallel_setup_cost": 1300.0},
            gradient_subset_candidate_rank=7,
        ),
    ]
    updated, reranked, audit = _online_update_experience_table_and_rerank(
        table,
        baseline_params=baseline_params,
        baseline_validation={
            "formal_qps": 100.0,
            "utilization_pct": {"cpu": 40.0, "memory": 86.0, "io": 37.0},
        },
        evaluations=evaluations,
        remaining_candidates=remaining,
    )
    assert audit["sample_count"] == 5
    assert audit["action"] == "frozen_no_online_update"
    assert audit["experience_table_mutated"] is False
    assert audit["suffix_reranked"] is False
    assert updated == table
    assert [item.optimized_params for item in reranked] == [
        item.optimized_params for item in remaining
    ]


def test_online_update_is_wired_to_initial_and_feedback_batches() -> None:
    source = (PROJECT_ROOT / "parameter_optimizer.py").read_text(encoding="utf-8")
    # One definition plus one call in the initial batch and one in every
    # feedback-outer batch.  This prevents a regression to static suffix order.
    assert source.count("_online_update_experience_table_and_rerank(") >= 3
    assert "feedback_incumbent_validation = copy.deepcopy(final_validation)" in source
    assert source.count("static_blind_expansion\": False") >= 2


def test_semantic_prior_is_hashed_but_not_formally_admitted() -> None:
    artifact = json.loads(SEMANTIC_PRIOR_PATH.read_text(encoding="utf-8"))
    canonical_table = json.dumps(
        artifact["table"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(canonical_table).hexdigest() == artifact["table_sha256"]
    cfg = {
        "initial_params": {},
        "sensitivity_table": copy.deepcopy(artifact["table"]),
        "experience_table_contract": {
            "required": True,
            "disable_fallback": True,
            "fallback_enabled": False,
            "artifact_path": str(SEMANTIC_PRIOR_PATH),
            "artifact_sha256": hashlib.sha256(
                SEMANTIC_PRIOR_PATH.read_bytes()
            ).hexdigest(),
            "table_id": artifact["table_id"],
            "version": artifact["version"],
            "table_sha256": artifact["table_sha256"],
            "active_params": artifact["active_params"],
            "finite_difference_direction_enabled": False,
            "random_direction_enabled": False,
            "low_confidence_freeze_threshold": 0.5,
        },
        "optimizer": {"gradient": {"require_explicit_sensitivity_table": True}},
    }
    try:
        load_explicit_experience_table_contract(
            cfg,
            str(PROJECT_ROOT / "unused_config.json"),
            artifact["active_params"],
        )
    except ValueError as exc:
        assert "formal_admission.ready" in str(exc), str(exc)
    else:
        raise AssertionError("semantic prior must not be usable as a formal table")


def main() -> None:
    test_exact_artifact_and_full_config_table_pass()
    test_partial_runtime_table_is_rejected()
    test_optimizer_level_override_is_rejected()
    test_artifact_byte_tampering_is_rejected_before_table_use()
    test_direction_fallback_switches_must_be_explicitly_false()
    test_ordered_six_param_subset_with_fixed_parameter_passes()
    test_fixed_parameter_must_not_be_active()
    test_v2_qps_confidence_and_evidence_contract_passes()
    test_first_batch_online_delta_is_frozen_without_group_holdout()
    test_online_update_is_wired_to_initial_and_feedback_batches()
    test_semantic_prior_is_hashed_but_not_formally_admitted()
    print("experience-table formal contract tests passed", flush=True)


if __name__ == "__main__":
    main()
