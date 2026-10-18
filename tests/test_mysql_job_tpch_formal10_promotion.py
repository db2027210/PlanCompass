from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "shell/promote_mysql_job_tpch_formal10.py"
)
SPEC = importlib.util.spec_from_file_location("formal10_promotion", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
promotion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)


def write_authority(path: Path, document: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(promotion.canonical(document))
    digest = promotion.sha(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def write_runtime_journal(
    root: Path,
    token: str,
    rank: int,
    subset_id: str,
    candidate_params: dict[str, float],
    measurement: dict[str, Any],
) -> tuple[Path, str]:
    attempt = root / token
    attempt.mkdir(parents=True, exist_ok=True)
    initial = {
        "schema_version": 1,
        "resume_token": token,
        "accounting_token": token,
        "pending": {
            "stage": "initial_top_k",
            "replay_ordinal": rank,
            "subset_id": subset_id,
            "candidate_params": candidate_params,
        },
        "measurement": None,
        "measurement_complete": False,
        "recovery_verified": False,
        "lease_released": False,
        "sequence": 1,
        "stage": "initialized",
        "previous_sha256": None,
    }
    initial_path = attempt / "0001_initialized.json"
    initial_path.write_bytes(promotion.compact_canonical(initial))
    initial_sha = promotion.sha(initial_path)
    initial_path.with_name(initial_path.name + ".sha256").write_text(
        initial_sha + "\n", encoding="ascii"
    )
    current = {
        "schema_version": 1,
        "resume_token": token,
        "accounting_token": token,
        "pending": None,
        "measurement": measurement,
        "measurement_complete": True,
        "recovery_verified": True,
        "lease_released": True,
        "sequence": 2,
        "stage": "point_complete",
        "previous_sha256": initial_sha,
    }
    history = attempt / "0002_point_complete.json"
    history.write_bytes(promotion.compact_canonical(current))
    current_sha = promotion.sha(history)
    history.with_name(history.name + ".sha256").write_text(
        current_sha + "\n", encoding="ascii"
    )
    current_path = attempt / "current.json"
    current_path.write_bytes(history.read_bytes())
    current_path.with_name("current.json.sha256").write_text(
        current_sha + "\n", encoding="ascii"
    )
    return current_path, current_sha


def fingerprint(rank: int) -> dict[str, Any]:
    structure = {
        "operator_types": ["scan"],
        "join_order": [],
        "scan_types": ["table_scan"],
        "parallel_structure": {"workers": 1},
    }
    l1 = {
        "version": "mysql_plan_structure_l1_v2",
        "sha256": promotion.sha_bytes(
            promotion.compact_canonical(structure)
        ),
        "structure": structure,
    }
    queries = {
        "q1": {
            "workers": 1.0,
            "hash_batches": 1.0,
            "sort_mode": "none",
            "spill_mode": "none",
            "spill_bytes": 0.0,
            "bitmap_lossy_pages": 0.0,
            "intermediate_rows": 1.0,
        }
    }
    l2_body = {
        "version": "mysql_execution_state_l2_v1",
        "queries": queries,
    }
    l2 = {
        **l2_body,
        "sha256": promotion.sha_bytes(
            promotion.compact_canonical(l2_body)
        ),
    }
    vectors = {
        "q1": {
            "memory_peak_bytes": 1024.0,
            "dynamic_execution_memory_bytes": 512.0,
            "spill_bytes": 0.0,
        }
    }
    l3_body = {
        "version": "mysql_scenario_resource_l3_v1",
        "scenario": "job",
        "unit_kind": "query",
        "primary_metrics": [
            "memory_peak_bytes",
            "dynamic_execution_memory_bytes",
            "spill_bytes",
        ],
        "vectors": vectors,
        "qps_included": False,
    }
    l3 = {
        **l3_body,
        "sha256": promotion.sha_bytes(
            promotion.compact_canonical(l3_body)
        ),
    }
    parameter_signature = [["p", float(rank).hex()]]
    identity = {
        "parameter_signature": parameter_signature,
        "l1_sha256": l1["sha256"],
        "l2_sha256": l2["sha256"],
        "l3_sha256": l3["sha256"],
    }
    return {
        "schema_version": "mysql_candidate_three_level_fingerprint_v1",
        "scenario": "job",
        "parameter_signature": parameter_signature,
        "true_duplicate_usable": True,
        "true_duplicate_ineligible_reasons": [],
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "qps_in_fingerprint": False,
        "qps_role": "outer_acceptance_only",
        "exact_identity_sha256": promotion.sha_bytes(
            promotion.compact_canonical(identity)
        ),
    }


def build_clean_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    name = "job"
    original = copy.deepcopy(promotion.SCENARIOS[name])
    original["formal_root"] = tmp_path / "formal"
    monkeypatch.setitem(promotion.SCENARIOS, name, original)
    real_manifest, real_config, readiness, source = (
        promotion.validate_debug_bundle(name)
    )
    config = copy.deepcopy(real_config)
    output = tmp_path / "debug-output"
    config["output"]["directory"] = str(output)
    config["formal_outer_protocol"]["formal_real_r0_checkpoint_path"] = str(
        output / "formal_real_r0.json"
    )
    config["formal_outer_protocol"]["formal_checkpoint_path"] = str(
        output / "formal_outer_checkpoint.json"
    )
    debug_config_path = output / "debug_config.json"
    debug_config_sha = write_authority(debug_config_path, config)

    def frozen_debug(_name: str):
        return real_manifest, config, readiness, source

    monkeypatch.setattr(promotion, "validate_debug_bundle", frozen_debug)
    monkeypatch.setattr(
        promotion, "validate_debug_run_config", lambda *_args: None
    )
    synthetic_seal = tmp_path / "synthetic-promotion-seal"
    synthetic_seal.mkdir()
    synthetic_closure = synthetic_seal / "seal_closure.json"
    synthetic_closure.write_bytes(promotion.canonical({
        "schema_version":
            "mysql_algorithm_c_promotion_seal_closure_v1",
        "seal_id": synthetic_seal.name,
        "files": {},
    }))
    monkeypatch.setattr(
        promotion,
        "verify_promotion_seal",
        lambda _path: (
            synthetic_seal,
            promotion.sha(synthetic_closure),
        ),
    )

    r0_measurement = {
        "stable": True,
        "correctness_passed": True,
        "oom": False,
        "mysql_restart": False,
        "container_restart": False,
        "sample_coverage": 1.0,
        "db_qps_source": "database_status_counter",
        "db_qps": 1000.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.4},
    }
    r0 = {
        "schema_version": "mysql_sgd_production_real_r0_checkpoint_v1",
        "measurement_kind": "production_real_database_r0",
        "measurement": r0_measurement,
        "measurement_sha256": promotion.sha_bytes(
            promotion.compact_canonical(r0_measurement, newline=True)
        ),
        "campaign_identity": {
            "benchmark": "job",
            "config_sha256": promotion.sha_bytes(
                promotion.compact_canonical(config)
            ),
            "readiness_manifest_sha256": promotion.sha(readiness),
            "service_factory_spec": original["service_factory"],
        },
    }
    r0_path = output / "formal_real_r0.json"
    r0_sha = write_authority(r0_path, r0)

    ordered = [f"subset-{index:02d}" for index in range(1, 16)]
    sealed: list[dict[str, Any]] = []
    audit_log: list[dict[str, Any]] = []
    rank_authority: list[dict[str, Any]] = []
    measurement_hashes: dict[int, str] = {}
    rank_fingerprints: dict[int, dict[str, Any]] = {}
    for rank, subset_id in enumerate(ordered, 1):
        sealed.append({
            "rank": rank,
            "subset_id": subset_id,
            "strict_plan_cluster_key": f"cluster-{rank}",
            "canonical_plan_bundle_sha256": f"{rank:064x}",
            "optimized_params_float_hex": {"p": float(rank).hex()},
        })
    for rank in range(1, 6):
        rank_fingerprint = fingerprint(rank)
        rank_fingerprints[rank] = rank_fingerprint
        token = f"token-{rank}"
        identity = sealed[rank - 1]
        identity_sha = promotion.sha_bytes(
            promotion.compact_canonical(identity)
        )
        measurement = {
            "rank": rank,
            "attempt_token": token,
            "status": "COMPLETE",
            "real_replay": True,
            "failed_replay": False,
            "accepted_round_credit": 0,
            "correctness_passed": True,
            "stable": True,
            "resource_sample_coverage": 1.0,
            "database_counter_coverage": 1.0,
            "three_level_fingerprint": rank_fingerprint,
        }
        measurement_path = output / "point-journal" / token / "measurement.json"
        measurement_sha = write_authority(measurement_path, measurement)
        measurement_hashes[rank] = measurement_sha
        source_measurement = {
            "correctness_passed": True,
            "stable": True,
            "sample_coverage": 1.0,
            "db_qps_source": "database_status_counter",
            "db_qps": 1000.0 - rank,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
        }
        current_path, current_sha = write_runtime_journal(
            output / "point-journal",
            token,
            rank,
            identity["subset_id"],
            {"p": float(rank)},
            source_measurement,
        )
        candidate = {
            "rank": rank,
            "subset_id": identity["subset_id"],
            "strict_plan_cluster_key": identity[
                "strict_plan_cluster_key"
            ],
            "canonical_plan_bundle": {
                "canonical_plan_bundle_sha256":
                    identity["canonical_plan_bundle_sha256"]
            },
            "optimized_params": {"p": float(rank)},
        }
        audit_log.extend([
            {
                "event": "candidate_replay_started",
                "rank": rank,
                "stage": "top_5",
                "attempt_token": token,
                "candidate": candidate,
            },
            {
                "event": "candidate_replayed",
                "rank": rank,
                "stage": "top_5",
                "attempt_token": token,
                "evidence": {},
            },
        ])
        rank_authority.append({
            "rank": rank,
            "attempt_token": token,
            "journal_current": {
                "path": str(current_path),
                "sha256": current_sha,
            },
            "measurement": {
                "path": str(measurement_path),
                "sha256": measurement_sha,
            },
            "promotion_authority": {
                "schema_version":
                    "mysql_algorithm_c_rank_promotion_authority_v1",
                "rank": rank,
                "attempt_token": token,
                "candidate_identity_sha256": identity_sha,
                "journal_current_sha256": current_sha,
                "source_measurement_sha256": promotion.sha_bytes(
                    promotion.compact_canonical(source_measurement)
                ),
                "measurement_sha256": measurement_sha,
                "real_replay_completed": True,
                "accepted_round_credit": 0,
            },
        })
    gradient = {
        "feature_ratio": {"x": 1.0},
        "predicted_resource": {"memory": 0.9},
        "utilization": {"memory": 0.9},
        "dScore/dU": {"memory": 1.0},
        "T": {"p": {"memory": 1.0}},
        "confidence": 1.0,
        "g_j": {"p": 1.0},
        "eta": 0.01,
        "candidate_params": {"p": 1.01},
        "delta_param": {
            "p": {
                "before": 1.0,
                "proposed_before_clip": 1.01,
                "after_clip": 1.01,
                "relative": 0.01,
                "absolute": 0.01,
                "clipped": False,
            }
        },
        "candidate_score": 1.0,
        "gradient_source":
            "original_dScore_dU_times_T_times_confidence",
    }
    checkpoint = {
        "schema_version": "mysql_sgd_outer_replay_checkpoint_v1",
        "r0_completed": True,
        "ordered_subset_ids": ordered,
        "completed_subset_ids": ordered,
        "completed_subset_results": {
            subset: {
                "optimization_result": {
                    "history": [{"gradient_debug": gradient}],
                    "final_score": 100.0 - index,
                    "optimized_params": {"p": float(index)},
                }
            }
            for index, subset in enumerate(ordered, 1)
        },
        "sealed_candidate_order": sealed,
        "sealed_representative_ranks": [1, 2, 3, 4, 5],
        "replayed_candidate_ranks": [1, 2, 3, 4, 5],
        "inflight_replays": {},
        "audit_log": audit_log,
    }
    checkpoint_path = output / "formal_outer_checkpoint.json"
    checkpoint_sha = write_authority(checkpoint_path, checkpoint)
    ranking = {
        "schema_version":
            "mysql_algorithm_c_prediction_real_ranking_audit_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": "job_memory",
        "debug_bundle_id": original["debug_id"],
        "checkpoint_sha256": checkpoint_sha,
        "prediction_vs_real_comparison_complete": True,
        "predicted_order": ordered,
        "fixed_top5_candidate_ids": ordered[:5],
        "real_order": ordered[:5],
        "unresolved_issues": [],
        "unresolved_issue_categories": {
            "sampling": [],
            "gradient": [],
            "estimation": [],
            "fingerprint": [],
            "technical": [],
        },
        "candidate_score_qps_input_used": False,
        "per_rank": [
            {
                "predicted_rank": rank,
                "real_rank": rank,
                "candidate_id": ordered[rank - 1],
                "measurement_sha256": measurement_hashes[rank],
                "candidate_score": 100.0 - rank,
                "real_qps": 1000.0 - rank,
                "three_level_fingerprint": rank_fingerprints[rank],
            }
            for rank in range(1, 6)
        ],
        "dedup_audit": {
            "status": "PASS",
            "identical_parameter_signature_required": True,
            "identical_l1_required": True,
            "l2_within_frozen_tolerance_required": True,
            "l3_within_frozen_tolerance_required": True,
            "missing_level_kept_independent": True,
        },
    }
    ranking_path = output / "prediction_real_ranking_audit.json"
    ranking_sha = write_authority(ranking_path, ranking)
    evidence = {
        "schema_version":
            "mysql_algorithm_c_formal10_promotion_evidence_v1",
        "scenario": "job_memory",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "debug_bundle_id": original["debug_id"],
        "debug_run_kind": "fresh_in_band_debug",
        "capture_or_historical_evidence_used": False,
        "accepted_round_credit": 0,
        "unresolved_issues": [],
        "point_journal_root": str(output / "point-journal"),
        "pointers": {
            "debug_config": {
                "path": str(debug_config_path),
                "sha256": debug_config_sha,
            },
            "r0": {"path": str(r0_path), "sha256": r0_sha},
            "checkpoint": {
                "path": str(checkpoint_path),
                "sha256": checkpoint_sha,
            },
            "ranking_audit": {
                "path": str(ranking_path),
                "sha256": ranking_sha,
            },
        },
        "rank_authority": rank_authority,
    }
    evidence_path = output / "promotion_evidence_manifest.json"
    evidence_path.write_bytes(promotion.canonical(evidence))
    return evidence_path, checkpoint_path


def test_clean_complete_evidence_promotes_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, _ = build_clean_fixture(tmp_path, monkeypatch)
    seal, manifest, config, source = promotion.audit_evidence(
        "job", evidence
    )
    assert seal["promotion_authorized"] is True
    assert seal["fixed_top5_real_replays_completed"] == 5
    first = promotion.publish(
        "job", evidence, seal, manifest, config, source
    )
    second = promotion.publish(
        "job", evidence, seal, manifest, config, source
    )
    assert first == second
    assert first.name == promotion.sha(first / "closure.json")
    formal = json.loads((first / "config.json").read_text())
    assert formal["formal_outer_protocol"]["accepted_shrink_count"] == 0
    assert formal["formal_outer_protocol"]["target_accepted_shrinks"] == 10
    assert formal["minimum_measurement_seconds"] == 180
    assert formal["measurement_seconds"] == 600
    runtime = (
        first / "runtime/mysql_parameter_optimizer.py"
    ).read_text(encoding="utf-8")
    compile(runtime, "<formal10-runtime>", "exec")


def test_incomplete_top5_evidence_fails_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, checkpoint_path = build_clean_fixture(tmp_path, monkeypatch)
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["replayed_candidate_ranks"] = [1, 2, 3, 4]
    checkpoint_sha = write_authority(checkpoint_path, checkpoint)
    authority = json.loads(evidence.read_text())
    authority["pointers"]["checkpoint"]["sha256"] = checkpoint_sha
    evidence.write_bytes(promotion.canonical(authority))
    with pytest.raises(
        promotion.PromotionError,
        match=r"checkpoint is not complete",
    ):
        promotion.audit_evidence("job", evidence)
    assert not (tmp_path / "formal").exists()


def test_rejected_predecessor_identity_cannot_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, _ = build_clean_fixture(tmp_path, monkeypatch)
    authority = json.loads(evidence.read_text())
    authority["debug_bundle_id"] = (
        "d812b811e0fdbed0c8bbb966b907134946ade1ce63600eb46619db8b23cad454"
    )
    evidence.write_bytes(promotion.canonical(authority))
    with pytest.raises(
        promotion.PromotionError,
        match=r"promotion evidence authority differs",
    ):
        promotion.audit_evidence("job", evidence)


def test_gradient_g_j_must_recompute_from_original_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, checkpoint_path = build_clean_fixture(tmp_path, monkeypatch)
    checkpoint = json.loads(checkpoint_path.read_text())
    subset_id = checkpoint["completed_subset_ids"][0]
    gradient = checkpoint["completed_subset_results"][subset_id][
        "optimization_result"
    ]["history"][0]["gradient_debug"]
    gradient["g_j"]["p"] = 0.5
    checkpoint_sha = write_authority(checkpoint_path, checkpoint)
    authority = json.loads(evidence.read_text())
    authority["pointers"]["checkpoint"]["sha256"] = checkpoint_sha
    evidence.write_bytes(promotion.canonical(authority))
    with pytest.raises(
        promotion.PromotionError,
        match=r"is not sum\(dScore/dU\*T\*confidence\)",
    ):
        promotion.audit_evidence("job", evidence)


def test_gradient_delta_must_derive_from_eta_and_g_j(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, checkpoint_path = build_clean_fixture(tmp_path, monkeypatch)
    checkpoint = json.loads(checkpoint_path.read_text())
    subset_id = checkpoint["completed_subset_ids"][0]
    gradient = checkpoint["completed_subset_results"][subset_id][
        "optimization_result"
    ]["history"][0]["gradient_debug"]
    gradient["delta_param"]["p"]["proposed_before_clip"] = 1.02
    checkpoint_sha = write_authority(checkpoint_path, checkpoint)
    authority = json.loads(evidence.read_text())
    authority["pointers"]["checkpoint"]["sha256"] = checkpoint_sha
    evidence.write_bytes(promotion.canonical(authority))
    with pytest.raises(
        promotion.PromotionError,
        match=r"is not derived from eta and g_j",
    ):
        promotion.audit_evidence("job", evidence)
