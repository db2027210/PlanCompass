#!/usr/bin/env python3
"""Focused tests for audited Algorithm A worker-domain rollback."""

from __future__ import annotations

import copy

import rollback_pg_algorithm_a_worker_cohort_transition as rollback


def config(worker_names: list[str]) -> dict:
    return {
        "binary_plan_tuning": {
            "validation": {
                "docker_workers": {
                    "workers": [{"name": name} for name in worker_names]
                }
            }
        }
    }


def fixture() -> tuple[dict, dict]:
    protected = {
        "accepted": 5,
        "round_index": 5,
        "ranges": {"seq_page_cost": {"min_value": 0.5, "max_value": 1.0}},
        "incumbent_params": {"seq_page_cost": 0.75},
        "best_feasible_champion": {"label": "champion"},
        "accepted_candidate_archive": [{"accepted_index": 5}],
    }
    prior_domain = {
        "baseline_replay_rows": [{"worker_key": "w1"}, {"worker_key": "w2"}],
        "baseline_by_worker": {"w1": {}, "w2": {}},
        "canonical_baseline": {"db_qps": 1.0},
        "incumbent_validation": {"db_qps": 1.1},
        "qps_noise_model": {"model_id": "old"},
        "executor_telemetry_baseline_model": {"contract": "old"},
        "baseline_execution_domain": {"worker_keys": ["w1", "w2"]},
        "worker_execution_cohort": {"worker_keys": ["w1", "w2"]},
    }
    prior_identity = {"contract": "identity", "identities": {"old": {}}}
    prior_pruning = {"contract": "pruning", "rules": {"old": {}}}
    transition_id = "unit_4_to_2"
    evidence_archive = {
        "contract": rollback.TRANSITION_CONTRACT,
        "transition_id": transition_id,
        "prior_valid_replay_identity_memory": prior_identity,
        "prior_valid_replay_identity_memory_sha256": rollback.canonical_sha256(
            prior_identity
        ),
        "prior_outer_accept_reject_pruning": prior_pruning,
        "prior_outer_accept_reject_pruning_sha256": rollback.canonical_sha256(
            prior_pruning
        ),
    }
    state = {
        **copy.deepcopy(protected),
        "phase": "execution_domain_transition_technical_retry",
        "algorithm_a_round_checkpoint": None,
        "inflight_physical_wave": None,
        "physical_workload_invocations": 683,
        "valid_replays": 615,
        "technical_failures": 68,
        "restore_technical_failures": 3,
        "out_of_band_invalid_replays": 0,
        "formal_candidate_wave_sequence": 328,
        "execution_domain_transition": {
            "contract": rollback.TRANSITION_CONTRACT,
            "transition_id": transition_id,
            "status": "incumbent_replay_technical_retry_required",
            "from_worker_keys": ["w1", "w2"],
            "to_worker_keys": ["w1", "w2", "w3", "w4"],
            "protected_semantic_state_sha256": rollback.canonical_sha256(protected),
        },
        "execution_domain_evidence_epoch_archives": [evidence_archive],
        "pending_execution_domain_evidence_epoch_transition": {"active": True},
        "valid_replay_identity_epoch_start_s": 100.0,
        "outer_pruning_epoch_start_s": 100.0,
        "valid_replay_identity_memory": {"contract": "identity", "identities": {}},
        "outer_accept_reject_pruning": {"contract": "pruning", "rules": {}},
    }
    archive = {
        "contract": rollback.TRANSITION_CONTRACT,
        "transition_id": transition_id,
        "protected_semantic_state": protected,
        "prior_execution_domain": prior_domain,
        "prior_execution_domain_sha256": rollback.canonical_sha256(prior_domain),
    }
    return state, archive


def test_restores_prior_domain_without_counter_rollback() -> None:
    state, archive = fixture()
    restored, manifest = rollback.build_rollback(
        state=state,
        current_config=config(["w1", "w2", "w3", "w4"]),
        prior_config=config(["w1", "w2"]),
        transition_archive=archive,
        transition_id="unit_4_to_2",
        now=123.0,
    )
    assert restored["accepted"] == 5
    assert restored["physical_workload_invocations"] == 683
    assert restored["valid_replays"] == 615
    assert restored["technical_failures"] == 68
    assert restored["baseline_execution_domain"]["worker_keys"] == ["w1", "w2"]
    assert "execution_domain_transition" not in restored
    assert "pending_execution_domain_evidence_epoch_transition" not in restored
    assert "valid_replay_identity_epoch_start_s" not in restored
    assert restored["valid_replay_identity_memory"]["identities"] == {"old": {}}
    assert manifest["accepted_unchanged"] is True


def test_rejects_inflight_wave() -> None:
    state, archive = fixture()
    state["inflight_physical_wave"] = {"journal_id": "active"}
    try:
        rollback.build_rollback(
            state=state,
            current_config=config(["w1", "w2", "w3", "w4"]),
            prior_config=config(["w1", "w2"]),
            transition_archive=archive,
            transition_id="unit_4_to_2",
            now=123.0,
        )
    except RuntimeError as exc:
        assert "in flight" in str(exc)
    else:
        raise AssertionError("in-flight rollback must fail closed")


def test_failed_baseline_requires_all_checkpointed_slots() -> None:
    state, archive = fixture()
    state["phase"] = rollback.FAILED_BASELINE_PHASE
    state["execution_domain_transition"]["status"] = "fresh_baseline_pending"
    state["baseline_pending_context"] = {
        "worker_keys": ["w1", "w2", "w3", "w4"],
        "repetitions_per_worker": 2,
    }
    state["baseline_pending_rows"] = [
        {
            "worker_key": worker,
            "baseline_repetition": repetition,
            "physical_workload_invocations": 1,
            "technical_failure": worker == "w4",
        }
        for repetition in (1, 2)
        for worker in ("w1", "w2", "w3", "w4")
    ]
    restored, _ = rollback.build_rollback(
        state=state,
        current_config=config(["w1", "w2", "w3", "w4"]),
        prior_config=config(["w1", "w2"]),
        transition_archive=archive,
        transition_id="unit_4_to_2",
        now=123.0,
    )
    assert "baseline_pending_rows" not in restored
    assert "baseline_pending_context" not in restored

    state["baseline_pending_rows"].pop()
    try:
        rollback.build_rollback(
            state=state,
            current_config=config(["w1", "w2", "w3", "w4"]),
            prior_config=config(["w1", "w2"]),
            transition_archive=archive,
            transition_id="unit_4_to_2",
            now=123.0,
        )
    except RuntimeError as exc:
        assert "fully checkpointed" in str(exc)
    else:
        raise AssertionError("partial failed baseline rollback must fail closed")


if __name__ == "__main__":
    test_restores_prior_domain_without_counter_rollback()
    test_rejects_inflight_wave()
    test_failed_baseline_requires_all_checkpointed_slots()
    print("rollback worker cohort transition tests passed", flush=True)
