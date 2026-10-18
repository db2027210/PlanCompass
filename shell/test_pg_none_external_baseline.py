#!/usr/bin/env python3
"""Fast semantic tests for the PostgreSQL external scene baselines."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import binary_plan_resource_tuner as resource_tuner

from shell.run_pg_none_external_baseline import (
    ACTIVE_PARAMS,
    AQETUNER_WARM_SAMPLES,
    AQETunerPolicy,
    BASELINE_MAX_WAVES,
    BASELINE_REPETITIONS,
    CHAMPION_MAX_WAVES,
    CHAMPION_REPETITIONS,
    DEFAULT_CANDIDATE_BUDGET,
    GPTUNER_COARSE_TRIALS,
    GPTunerPolicy,
    POST_BASELINE_REPETITIONS,
    ReplayHarness,
    RESTORE_WAVE_ATTEMPTS,
    RESTORE_WAVE_RETRY_DELAY_S,
    aggregate_postgres_plans,
    candidate_failure_classification,
    denormalized_params,
    exact_signature,
    normalized_vector,
    slim_validation,
    technical_failure_accounting,
    trial_from_replay_label,
)


DEFAULTS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def test_round_trip() -> None:
    vector = normalized_vector(DEFAULTS)
    restored = denormalized_params(vector)
    for name in ACTIVE_PARAMS:
        assert abs(restored[name] - DEFAULTS[name]) < 1e-10
    assert exact_signature(restored) == exact_signature(DEFAULTS)


def test_trial_label_parser() -> None:
    assert trial_from_replay_label("none_aqetuner_dual_task_eic_t010_w1") == 10
    assert trial_from_replay_label("none_aqetuner_pso_warm_start_t009_w2") == 9
    assert trial_from_replay_label("none_gptuner_baseline_rep01_t-102_w1") == -102
    assert trial_from_replay_label("missing_trial") == -1


def test_slim_validation_preserves_two_worker_receipt() -> None:
    row = {
        "status": "ok",
        "technical_failure": False,
        "parallel_worker_aggregate": {
            "method": "mean_resources_min_samples",
            "worker_count": 2,
            "worker_keys": ["w1", "w2"],
        },
    }
    result = slim_validation(row)
    assert result["parallel_worker_aggregate"]["worker_count"] == 2
    assert result["parallel_worker_aggregate"]["worker_keys"] == ["w1", "w2"]


def test_technical_failure_accounting_separates_retries_from_search() -> None:
    state = {
        "baseline_rows": [
            {"physical_valid": False},
            {"physical_valid": True},
            {"physical_valid": True},
        ],
        "observations": [{"valid": True}, {"valid": True}],
        "post_baseline_rows": [{"physical_valid": True}],
        "champion_validation_rows": [{"physical_valid": True}],
        "physical_workload_invocations": 7,
        "technical_failures": 1,
        "excluded_infrastructure_attempts": [
            {"physical_attempt_count": 2},
        ],
    }
    result = technical_failure_accounting(state)
    assert result["candidate_technical_failures"] == 0
    assert result["retryable_validation_technical_failures"] == 1
    assert result["accounted_technical_failures"] == 1
    assert result["excluded_infrastructure_physical_attempts"] == 2
    assert result["total_physical_attempts_including_excluded_infrastructure"] == 9
    assert result["all_technical_failures_accounted"] is True


def test_technical_failure_accounting_rejects_candidate_failure() -> None:
    state = {
        "baseline_rows": [{"physical_valid": True}],
        "observations": [{"valid": False}],
        "post_baseline_rows": [],
        "champion_validation_rows": [],
        "physical_workload_invocations": 2,
        "technical_failures": 1,
    }
    result = technical_failure_accounting(state)
    assert result["candidate_technical_failures"] == 1
    assert result["candidate_infrastructure_failures"] == 1
    assert result["candidate_configuration_failures"] == 0
    assert result["retryable_validation_technical_failures"] == 0
    assert result["all_technical_failures_accounted"] is True


def test_technical_failure_accounting_accepts_configuration_failure() -> None:
    state = {
        "baseline_rows": [{"physical_valid": True}],
        "observations": [{"valid": False, "model_failure": True}],
        "post_baseline_rows": [],
        "champion_validation_rows": [],
        "physical_workload_invocations": 2,
        "technical_failures": 1,
    }
    result = technical_failure_accounting(state)
    assert result["candidate_technical_failures"] == 1
    assert result["candidate_infrastructure_failures"] == 0
    assert result["candidate_configuration_failures"] == 1


def test_candidate_failure_classification() -> None:
    timeout_row = {
        "status": "failed",
        "technical_failure": True,
        "process_error": "HyBench XP timed out after 900.0s",
        "monitor_error": "",
        "resource_stats": {"resource_monitor_ok": True},
        "ssd_qualification": {
            "passed": False,
            "errors": ["w2:base_physical_validation_failed"],
        },
    }
    assert candidate_failure_classification(timeout_row) == "configuration_failure"
    observer_row = {
        "status": "failed",
        "technical_failure": True,
        "resource_stats": {"resource_monitor_ok": True},
        "ssd_qualification": {
            "passed": False,
            "errors": ["w2:unexpected_ssd_observer_devices"],
        },
    }
    assert candidate_failure_classification(observer_row) == "infrastructure_failure"


def test_publication_inflight_candidate_is_excluded_without_consuming_budget() -> None:
    with tempfile.TemporaryDirectory(prefix="pg_external_inflight_") as directory:
        root = Path(directory)
        harness = ReplayHarness.__new__(ReplayHarness)
        harness.publication_mode = True
        harness.log_path = root / "events.jsonl"
        harness.method = "gptuner"
        harness.state = {
            "candidate_physical_invocations": 3,
            "physical_workload_invocations": 7,
            "valid_replays": 7,
            "technical_failures": 0,
            "observations": [{"valid": True}] * 3,
            "inflight_wave": {
                "journal_id": "candidate-journal",
                "phase": "coarse",
                "entries": [
                    {
                        "label": "job_memory_gptuner_coarse_t004_w2",
                        "params": copy.deepcopy(DEFAULTS),
                        "worker_key": "w2",
                    }
                ],
                "suggestions": [],
            },
        }
        harness.save = lambda: None
        harness.log = lambda *args, **kwargs: None

        harness.recover_inflight_wave()

        assert harness.state["inflight_wave"] is None
        assert harness.state["candidate_physical_invocations"] == 3
        assert harness.state["physical_workload_invocations"] == 7
        receipts = harness.state["excluded_infrastructure_attempts"]
        assert len(receipts) == 1
        assert receipts[0]["excluded_from_candidate_budget"] is True
        assert receipts[0]["excluded_from_tuner_feedback"] is True


def test_plan_adapter() -> None:
    explained = [
        {
            "plan_root": {
                "Node Type": "Hash Join",
                "Plan Rows": 12,
                "Plans": [
                    {"Node Type": "Seq Scan", "Plan Rows": 100},
                    {
                        "Node Type": "Aggregate",
                        "Plan Rows": 5,
                        "Plans": [
                            {"Node Type": "Index Scan", "Plan Rows": 5}
                        ],
                    },
                ],
            }
        }
    ]
    plan = aggregate_postgres_plans(explained)
    assert plan["NodeType"] == "Projection"
    assert plan["Children"][0]["NodeType"] == "Join"
    assert plan["Children"][0]["Children"][0]["NodeType"] == "TableScan"


def test_gptuner_policy_stages() -> None:
    state = {"observations": []}
    harness = SimpleNamespace(
        seed=20260825,
        scene="job_memory",
        defaults=copy.deepcopy(DEFAULTS),
        candidate_budget=DEFAULT_CANDIDATE_BUDGET,
        state=state,
    )
    official = Path(__file__).resolve().parents[1] / "baselines/GPTuner_official"
    policy = GPTunerPolicy(harness, official)
    seen = set()
    for _ in range(GPTUNER_COARSE_TRIALS):
        suggestion = policy.suggest(1)[0]
        assert suggestion["stage"] == "coarse"
        signature = exact_signature(suggestion["params"])
        assert signature not in seen
        seen.add(signature)
        observation = {
            **suggestion,
            "param_signature": signature,
            "valid": True,
            "db_qps": 40.0 + len(state["observations"]),
            "cost": -(40.0 + len(state["observations"])),
            "elapsed_s": 1.0,
        }
        state["observations"].append(observation)
        policy.observe(suggestion, observation)
    fine = policy.suggest(2)
    assert len(fine) == 2
    assert all(item["stage"] == "fine" for item in fine)
    assert len({exact_signature(item["params"]) for item in fine}) == 2


def test_gptuner_policy_resume_does_not_exhaust_internal_trial_budget() -> None:
    state = {"observations": []}
    harness = SimpleNamespace(
        seed=20260834,
        scene="xp_io",
        defaults=copy.deepcopy(DEFAULTS),
        candidate_budget=DEFAULT_CANDIDATE_BUDGET,
        state=state,
    )
    official = Path(__file__).resolve().parents[1] / "baselines/GPTuner_official"
    policy = GPTunerPolicy(harness, official)
    for index in range(26):
        suggestion = policy.suggest(1)[0]
        signature = exact_signature(suggestion["params"])
        observation = {
            **suggestion,
            "param_signature": signature,
            "valid": index not in (12, 21),
            "db_qps": 50.0 + index,
            "cost": -(50.0 + index),
            "elapsed_s": 1.0,
        }
        state["observations"].append(observation)
        if observation["valid"] is True:
            policy.observe(suggestion, observation)

    resumed = GPTunerPolicy(harness, official)
    suggestions = resumed.suggest(2)
    assert len(suggestions) == 2
    assert resumed.resume_rehydrated_valid_observations == 24
    assert all(item["stage"] == "fine" for item in suggestions)
    assert all(
        exact_signature(item["params"])
        not in {row["param_signature"] for row in state["observations"]}
        for item in suggestions
    )


def test_publication_gptuner_requires_complete_gpt4_snapshot() -> None:
    state = {"observations": []}
    harness = SimpleNamespace(
        seed=20260904,
        scene="job_memory",
        defaults=copy.deepcopy(DEFAULTS),
        candidate_budget=DEFAULT_CANDIDATE_BUDGET,
        state=state,
        publication_mode=True,
    )
    official = Path(__file__).resolve().parents[1] / "baselines/GPTuner_official"
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary)
        normal = snapshot / "structured_knowledge/normal"
        normal.mkdir(parents=True)
        manifest = {
            "contract": "gptuner_online_knowledge_snapshot_v1",
            "status": "complete",
            "model": "gpt-4",
            "provider": "openai-api",
            "dbms": "postgres",
            "scene": "job_memory",
            "active_params": list(ACTIVE_PARAMS),
            "stages": {
                "llm_source_extraction": True,
                "manual_source_summarization": True,
                "official_constraint_filter": True,
                "cross_source_contradiction_resolution": True,
                "default_range_debias": True,
                "factual_consistency_check": True,
                "prompt_ensemble_votes": 5,
                "hardware_aware_transformation": True,
                "special_value_classification": True,
            },
        }
        (snapshot / "manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        for name in ACTIVE_PARAMS:
            low = DEFAULTS[name] * 0.8
            high = DEFAULTS[name] * 1.2
            (normal / f"{name}.json").write_text(
                json.dumps(
                    {
                        "suggested_values": [DEFAULTS[name]],
                        "min_value": low,
                        "max_value": high,
                    }
                ),
                encoding="utf-8",
            )
        policy = GPTunerPolicy(harness, official, snapshot)
        for name in ACTIVE_PARAMS:
            summary = policy.knowledge_summary[name]
            assert summary["refined_bounds"] == [
                DEFAULTS[name] * 0.8,
                DEFAULTS[name] * 1.2,
            ]
            assert DEFAULTS[name] in summary["coarse_values"]
        assert policy.knowledge_manifest["model"] == "gpt-4"


def test_aqetuner_pso_uses_observation_feedback() -> None:
    policy = AQETunerPolicy.__new__(AQETunerPolicy)
    policy.harness = SimpleNamespace(seed=17)
    policy.rng = __import__("numpy").random.default_rng(17)
    policy._initialize_pso()
    original = policy.pso_positions[0].copy()
    observation = {
        "valid": True,
        "objective_score": 1.2,
        "db_qps": 12.0,
    }
    policy._update_pso(0, original.copy(), observation)
    assert policy.pso_global_best_score < float("inf")
    assert policy.pso_local_best_scores[0] < float("inf")
    assert not __import__("numpy").array_equal(
        policy.pso_positions[0],
        original,
    )


def test_aqetuner_public_model_forward() -> None:
    official = Path(__file__).resolve().parents[1] / "baselines/AQETuner_official"
    if str(official) not in sys.path:
        sys.path.insert(0, str(official))
    os.environ.setdefault("DB", "pg_none_test")
    import torch
    from network import LatentModel
    from pair_encoder import TPair

    plan = {
        "NodeId": 0,
        "NodeType": "Projection",
        "Statistic": {"RowCount": 10},
        "Children": [
            {
                "NodeId": 1,
                "NodeType": "TableScan",
                "Statistic": {"RowCount": 10},
                "Children": [],
            }
        ],
    }
    pairs = [
        TPair(copy.deepcopy(plan), [0.1 * (index + 1)] * len(ACTIVE_PARAMS))
        for index in range(6)
    ]
    context_y = torch.tensor(
        [[0.025, 0.0], [0.024, 0.0], [0.026, 0.0], [0.023, 0.0]],
        dtype=torch.float32,
    )
    model = LatentModel(32).to("cpu")
    model.train(False)
    with torch.no_grad():
        latency, failure, loss = model(pairs[:4], context_y, pairs[4:])
    assert latency.mean.shape[0] == 2
    assert failure.mean.shape[0] == 2
    assert loss is None


def test_constants() -> None:
    assert AQETUNER_WARM_SAMPLES == 9
    assert BASELINE_MAX_WAVES >= 3
    assert GPTUNER_COARSE_TRIALS == 10
    assert DEFAULT_CANDIDATE_BUDGET == 35
    assert BASELINE_REPETITIONS == 2
    assert POST_BASELINE_REPETITIONS == 2
    assert CHAMPION_REPETITIONS == 2
    assert CHAMPION_MAX_WAVES >= CHAMPION_REPETITIONS
    assert RESTORE_WAVE_ATTEMPTS == 3
    assert RESTORE_WAVE_RETRY_DELAY_S >= 20.0


def test_container_cache_helper_retries_empty_success() -> None:
    container = "unit_test_semantic_retry"
    responses = [
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout='{"ok":true,"files":7,"bytes":4096,"errors":0,"rc":1}\n',
            stderr="",
        ),
    ]
    calls = []
    original_run = resource_tuner.run_docker_cmd
    original_helper = resource_tuner.ensure_fadvise_helper
    resource_tuner._FADVISE_HELPER_INSTALLED.add(container)

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0)

    try:
        resource_tuner.run_docker_cmd = fake_run
        resource_tuner.ensure_fadvise_helper = lambda: (Path("/tmp/fake"), "")
        result = resource_tuner.evict_file_cache_in_container(
            container,
            container_user="postgres",
        )
    finally:
        resource_tuner.run_docker_cmd = original_run
        resource_tuner.ensure_fadvise_helper = original_helper
        resource_tuner._FADVISE_HELPER_INSTALLED.discard(container)

    assert result["ok"] is True
    assert result["files"] == 7
    assert len(calls) == 2
    assert result["semantic_retry_attempts"][0]["errors"] == [
        "helper_stdout_empty"
    ]


def main() -> None:
    _ = tempfile.gettempdir()
    test_round_trip()
    test_trial_label_parser()
    test_slim_validation_preserves_two_worker_receipt()
    test_technical_failure_accounting_separates_retries_from_search()
    test_technical_failure_accounting_rejects_candidate_failure()
    test_technical_failure_accounting_accepts_configuration_failure()
    test_candidate_failure_classification()
    test_publication_inflight_candidate_is_excluded_without_consuming_budget()
    test_plan_adapter()
    test_gptuner_policy_stages()
    test_gptuner_policy_resume_does_not_exhaust_internal_trial_budget()
    test_publication_gptuner_requires_complete_gpt4_snapshot()
    test_aqetuner_pso_uses_observation_feedback()
    test_aqetuner_public_model_forward()
    test_constants()
    test_container_cache_helper_retries_empty_success()
    print("PASS: PostgreSQL external scene baseline semantic tests", flush=True)


if __name__ == "__main__":
    main()
