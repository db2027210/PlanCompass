#!/usr/bin/env python3
"""MySQL all-subset experience-table SGD controller regression."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_parameter_optimizer import (  # noqa: E402
    _LaneLease,
    REQUIRED_REAL_REPLAY_EVIDENCE,
    ReplayRecoveryError,
    _gradient_replay_stage_action,
    _online_update_experience_table_and_rerank,
    _orchestrate_staged_real_replays,
    _formal_checkpoint_path,
    _build_replay_suppression_log,
    _enforce_strict_plan_replay_quota,
    run_optimization,
)
import mysql_parameter_optimizer as optimizer_module  # noqa: E402
from tests.test_mysql_tuner import FakeClient  # noqa: E402
from formal_mysql_sgd_checkpoint import (  # noqa: E402
    FormalOuterReplayLedger, FormalReplayCheckpointError,
)
from gradient_descent_optimizer import ParameterRange  # noqa: E402


def _formal_evidence():
    return {name: f"artifact://{name}" for name in REQUIRED_REAL_REPLAY_EVIDENCE}


class _ReplayDriver:
    def __init__(self, measure, *, recovery=None):
        self.measure = measure
        self.recovery = recovery

    def __call__(self, candidate, context):
        return self.measure(candidate, context)

    def restore(self, candidate, context):
        if self.recovery is not None:
            return self.recovery(candidate, context)
        return {
            "succeeded": True,
            "effective_params": context["baseline_params"],
            "fixed_settings_verified": True,
            "clean_template_verified": True,
            "evidence": "artifact://recovery",
        }

    def review_no_eligible_root_cause(self, diagnostic):
        return {"approved": True, "diagnostic_code": diagnostic.get("code")}


def test_mysql_entry_runs_every_nonempty_subset() -> None:
    active = [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        config = {
            "mysql": {"engine_name": "default", "device_type": 0},
            "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "target_resource": "memory",
            "cost_parameters": {
                "active": active,
                "ranges": {
                    name: {"min_multiplier": 0.5, "max_multiplier": 2.0}
                    for name in active
                },
            },
            "sensitivity_table": {
                "io_block_read_cost": {"cpu": 0.1, "memory": 0.1, "io": -0.3},
                "memory_block_read_cost": {"cpu": 0.1, "memory": -0.2, "io": 0.1},
                "row_evaluate_cost": {"cpu": -0.2, "memory": 0.0, "io": -0.1},
            },
            "optimizer": {
                "max_iter": 1,
                "patience": 1,
                "gamma": 0.5,
                "subset_max_workers": 1,
            },
            "formal_outer_protocol": {
                "formal_search_initial_replay_top_k": 5,
                "formal_search_max_replay_top_k": 5,
            },
            "estimator": {
                "type": "python",
                "callable": "tests.fake_estimator:estimate",
            },
            "output": {"directory": temp_dir, "verbose": False},
            "commit_best": False,
        }
        client = FakeClient()
        result = run_optimization(config, client=client)
        assert result.subset_count == 2**3 - 1 == 7
        assert result.successful_subset_count == 7
        assert result.failed_subset_count == 0
        assert len(result.subset_audit) == 7
        assert [row["active_subset"] for row in result.subset_audit] == [
            [active[0]],
            [active[1]],
            [active[2]],
            [active[0], active[1]],
            [active[0], active[2]],
            [active[1], active[2]],
            active,
        ]
        assert all(row["status"] == "success" for row in result.subset_audit)
        subset_iterations = [row["evaluated_sgd_iterations"] for row in result.subset_audit]
        assert all(value in {0, 1} for value in subset_iterations)
        assert result.total_inner_iterations == sum(subset_iterations)
        assert all(value is None for value in client.raw.values())

        payload = json.loads(Path(result.result_path).read_text(encoding="utf-8"))
        assert payload["subset_count"] == 7
        assert payload["optimistic_pruning_framework"] == "resource_utility_joint_box_v2"

        assert payload["estimate_cache"]["request_count"] == 7 + result.total_inner_iterations
        assert payload["estimate_cache"]["cache_hit_count"] > 0
        assert payload["estimate_cache"]["unique_parameter_count"] < payload[
            "estimate_cache"
        ]["request_count"]
        # The fake estimator provides no calibrated simultaneous error bounds,
        # therefore optimistic pruning must fail open.
        assert payload["optimistically_pruned_count"] == 0
        assert set(("meta", "outer_loop", "inner_loop_summary", "result")) <= set(payload)
        audit = payload["audit_artifacts"]
        inner_path = Path(audit["inner_jsonl_path"])
        assert inner_path.is_file()
        assert hashlib.sha256(inner_path.read_bytes()).hexdigest() == audit["inner_jsonl_sha256"]
        records = [json.loads(line) for line in inner_path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == result.total_inner_iterations
        assert all(
            row["acceptance_reference"]
            == "strict_estimated_resource_utility_improvement"
            for row in records
        )
        assert all("changed_param_set" in row and "experience_table" in row for row in records)


def test_formal_entry_parallel_subset_search_and_serial_real_replay() -> None:
    """The production optimizer boundary passes >1 workers, but stages replay serially."""
    active = ["io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost"]
    observed_workers = []
    original_optimize = optimizer_module.optimize_all_experience_table_subsets

    def observe_optimize(*args, **kwargs):
        observed_workers.append(kwargs["max_workers"])
        summary = original_optimize(*args, **kwargs)
        # The deterministic fake estimator can legitimately keep every SGD
        # result at R0.  Make one already-ranked result non-incumbent so this
        # boundary test exercises the subsequent real-replay stage as well.
        ranking = summary.candidate_rankings[0]
        ranking["optimized_params"] = dict(ranking["optimized_params"])
        ranking["optimized_params"][active[0]] *= 1.01
        return summary

    replay_lock = threading.Lock()
    replay_active = 0
    replay_peak = 0
    replay_order = []

    def measure(_candidate, context):
        nonlocal replay_active, replay_peak
        with replay_lock:
            replay_active += 1
            replay_peak = max(replay_peak, replay_active)
            replay_order.append(context["replay_ordinal"])
        time.sleep(0.005)
        with replay_lock:
            replay_active -= 1
        ordinal = context["replay_ordinal"]
        return {
            "qps": 100.0 + ordinal,
            "db_qps": 100.0 + ordinal,
            "db_qps_source": "database_status_counter",
            "p95_ms": 20.0,
            "p99_ms": 30.0,
            "correctness_passed": True,
            "error_rate": 0.0,
            "stable": True,
            "sample_coverage": 0.95,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
            "utilization_pct": {"cpu": 0.4, "memory": 0.89, "io": 0.3},
            "evidence": _formal_evidence(),
        }

    with tempfile.TemporaryDirectory() as temp_dir:
        config = {
            "exp_id": "parallel-subsets-serial-replays",
            "benchmark": "job",
            "mysql": {"engine_name": "default", "device_type": 0},
            "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "target_resource": "memory",
            "cost_parameters": {
                "active": active,
                "ranges": {
                    name: {"min_multiplier": 0.5, "max_multiplier": 2.0}
                    for name in active
                },
            },
            "sensitivity_table": {
                name: {"cpu": 0.1, "memory": -0.2, "io": 0.1}
                for name in active
            },
            "optimizer": {
                "max_iter": 1,
                "patience": 1,
                "gamma": 0.5,
                "subset_max_workers": 3,
            },
            "formal_outer_protocol": {
                "formal_search_initial_replay_top_k": 5,
                "formal_search_max_replay_top_k": 5,
                "max_outer_attempts": 1,
                "target_accepted_shrinks": 10,
                "lane_lease_path": str(Path(temp_dir) / "parallel-formal-entry.lane.lock"),
            },
            "estimator": {
                "type": "python",
                "callable": "tests.fake_estimator:estimate",
            },
            "output": {"directory": temp_dir, "verbose": False},
            "commit_best": False,
        }
        baseline = {
            "qps": 100.0,
            "db_qps": 100.0,
            "db_qps_source": "database_status_counter",
            "p95_ms": 20.0,
            "p99_ms": 30.0,
            "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
        }
        optimizer_module.optimize_all_experience_table_subsets = observe_optimize
        try:
            result = run_optimization(
                config,
                client=FakeClient(),
                real_replay_callback=_ReplayDriver(measure),
                baseline_validation=baseline,
            )
        finally:
            optimizer_module.optimize_all_experience_table_subsets = original_optimize

    assert observed_workers == [3]
    assert result.subset_count == 7
    assert replay_order, "formal entry did not stage any real replay"
    assert replay_order == list(range(1, len(replay_order) + 1))
    assert replay_peak == 1


def test_staged_top_k_policy_matches_source() -> None:
    assert _gradient_replay_stage_action(
        validated_count=4,
        eligible_count=0,
        available_count=20,
    ) == "continue_initial"
    assert _gradient_replay_stage_action(
        validated_count=5,
        eligible_count=1,
        available_count=20,
    ) == "accept_current_batch"
    assert _gradient_replay_stage_action(
        validated_count=5,
        eligible_count=0,
        available_count=20,
    ) == "reject_exploration"
    assert _gradient_replay_stage_action(
        validated_count=10,
        eligible_count=0,
        available_count=20,
    ) == "reject_exploration"


def test_online_t_delta_reranks_only_remaining_candidates() -> None:
    table = {
        "p": {
            "cpu": 0.0,
            "memory": 0.1,
            "io": 0.0,
            "qps": 0.1,
            "confidence": 1.0,
            "evidence_count": 0.0,
        }
    }
    evaluations = []
    for value, db_qps, memory in (
        (0.8, 96.0, 0.95),
        (1.2, 104.0, 0.85),
        (1.4, 108.0, 0.80),
    ):
        evaluations.append(
            {
                "candidate_optimized_params": {"p": value},
                "candidate_final_validation": {
                    "qps": db_qps,
                    "db_qps": db_qps,
                    "utilization_pct": {
                        "cpu": 0.5,
                        "memory": memory,
                        "io": 0.4,
                    },
                },
            }
        )
    updated, reranked, audit = _online_update_experience_table_and_rerank(
        table,
        baseline_params={"p": 1.0},
        baseline_validation={
            "qps": 100.0,
            "db_qps": 100.0,
            "utilization_pct": {"cpu": 0.5, "memory": 0.9, "io": 0.4},
        },
        evaluations=evaluations,
        remaining_candidates=[
            {"rank": 4, "optimized_params": {"p": 0.9}},
            {"rank": 5, "optimized_params": {"p": 1.3}},
        ],
        target_resource="memory",
    )
    assert audit["sample_count"] == 3
    assert audit["parameter_updates"][0]["supported"]
    assert updated["p"]["evidence_count"] == 3.0
    assert len(reranked) == 2
    assert all("gradient_online_rank" in item for item in reranked)
    assert all("gradient_online_delta_prediction" in item for item in reranked)


def test_single_plan_cluster_triggers_bounded_escape_augmentation() -> None:
    rankings = [
        {
            "rank": 1,
            "subset_id": "s1",
            "optimized_params": {"a": 2.0, "b": 1.0},
            "initial_resource": {"cpu": 0.5, "memory": 0.9, "io": 0.5},
            "initial_score": 0.0,
            "final_score": 1.0,
        }
    ]

    class Estimator:
        last_stats = {"qps": [100.0]}
        last_metadata = {}

        def __call__(self, _sql_set, _params):
            return {"cpu": 0.5, "memory": 0.8, "io": 0.5}

    original_build = optimizer_module._build_estimator
    original_cluster = optimizer_module.cluster_ranked_candidates
    optimizer_module._build_estimator = lambda _config, _adapter: Estimator()
    optimizer_module.cluster_ranked_candidates = lambda candidates: {
        "strict_plan_cluster_count": len(candidates),
        "clusters": [
            {
                "cluster_key": f"strict:{index}",
                "strict_fingerprint_present": True,
            }
            for index in range(
                2 if any(item.get("escape_batch_index") for item in candidates) else 1
            )
        ],
    }
    try:
        augmented, audit = optimizer_module._augment_rankings_for_plan_escape(
            rankings,
            config={
                "formal_outer_protocol": {
                    "plan_basin_escape": {
                        "enabled": True,
                        "maximum_batches": 5,
                        "required_plan_clusters": 2,
                    }
                },
                "sql_set": None,
            },
            adapter=object(),
            baseline={"a": 1.0, "b": 1.0},
            ranges={
                "a": ParameterRange(0.1, 10.0),
                "b": ParameterRange(0.1, 10.0),
            },
            sensitivity={"a": {"memory": 1.0}, "b": {"memory": -1.0}},
            capability={"cpu": 1.0, "memory": 1.0, "io": 1.0},
            target_resource="memory",
            optimizer_options={},
            estimators=[],
        )
    finally:
        optimizer_module._build_estimator = original_build
        optimizer_module.cluster_ranked_candidates = original_cluster

    assert len(augmented) == 2
    escape_rows = [row for row in augmented if row.get("escape_batch_index")]
    assert len(escape_rows) == 1
    assert escape_rows[0]["candidate_origin"] == "reverse_local_delta"
    assert audit["batches_used"] == 2
    assert audit["quota_satisfied"] is True


def test_missing_plan_fingerprints_do_not_satisfy_escape_quota() -> None:
    rankings = [
        {
            "rank": 1,
            "subset_id": "s1",
            "optimized_params": {"a": 2.0, "b": 1.0},
            "initial_resource": {"cpu": 0.5, "memory": 0.9, "io": 0.5},
            "initial_score": 0.0,
            "final_score": 1.0,
        }
    ]

    class Estimator:
        last_stats = {"qps": [100.0]}
        last_metadata = {}

        def __call__(self, _sql_set, _params):
            return {"cpu": 0.5, "memory": 0.8, "io": 0.5}

    original_build = optimizer_module._build_estimator
    original_cluster = optimizer_module.cluster_ranked_candidates
    optimizer_module._build_estimator = lambda _config, _adapter: Estimator()
    optimizer_module.cluster_ranked_candidates = lambda candidates: {
        "strict_plan_cluster_count": len(candidates),
        "clusters": [
            {
                "cluster_key": f"missing:{index}",
                "strict_fingerprint_present": False,
            }
            for index, _candidate in enumerate(candidates, 1)
        ],
    }
    try:
        _augmented, audit = optimizer_module._augment_rankings_for_plan_escape(
            rankings,
            config={
                "formal_outer_protocol": {
                    "plan_basin_escape": {
                        "enabled": True,
                        "maximum_batches": 5,
                        "required_plan_clusters": 2,
                    }
                },
                "sql_set": None,
            },
            adapter=object(),
            baseline={"a": 1.0, "b": 1.0},
            ranges={
                "a": ParameterRange(0.1, 10.0),
                "b": ParameterRange(0.1, 10.0),
            },
            sensitivity={"a": {"memory": 1.0}, "b": {"memory": -1.0}},
            capability={"cpu": 1.0, "memory": 1.0, "io": 1.0},
            target_resource="memory",
            optimizer_options={},
            estimators=[],
        )
    finally:
        optimizer_module._build_estimator = original_build
        optimizer_module.cluster_ranked_candidates = original_cluster

    assert audit["batches_used"] == 5
    assert audit["final_strict_plan_cluster_count"] == 0
    assert audit["missing_fingerprints_count_toward_quota"] is False
    assert audit["quota_satisfied"] is False


def test_seen_candidate_filter_happens_before_escape_plan_quota() -> None:
    rankings = [
        {
            "rank": 1,
            "subset_id": "s1",
            "optimized_params": {"a": 2.0, "b": 1.0},
            "initial_resource": {"cpu": 0.5, "memory": 0.9, "io": 0.5},
            "initial_score": 0.0,
            "final_score": 2.0,
        },
        {
            "rank": 2,
            "subset_id": "s2-seen",
            "optimized_params": {"a": 3.0, "b": 1.0},
            "initial_resource": {"cpu": 0.5, "memory": 0.9, "io": 0.5},
            "initial_score": 0.0,
            "final_score": 1.0,
        },
    ]

    class Estimator:
        last_stats = {"qps": [100.0]}
        last_metadata = {}

        def __call__(self, _sql_set, _params):
            return {"cpu": 0.5, "memory": 0.8, "io": 0.5}

    original_build = optimizer_module._build_estimator
    original_cluster = optimizer_module.cluster_ranked_candidates
    optimizer_module._build_estimator = lambda _config, _adapter: Estimator()
    optimizer_module.cluster_ranked_candidates = lambda candidates: {
        "strict_plan_cluster_count": len(candidates),
        "clusters": [
            {
                "cluster_key": f"strict:{index}",
                "strict_fingerprint_present": True,
            }
            for index, _candidate in enumerate(candidates, 1)
        ],
    }
    try:
        augmented, audit = optimizer_module._augment_rankings_for_plan_escape(
            rankings,
            config={
                "formal_outer_protocol": {
                    "plan_basin_escape": {
                        "enabled": True,
                        "maximum_batches": 5,
                        "required_plan_clusters": 2,
                    }
                },
                "sql_set": None,
            },
            adapter=object(),
            baseline={"a": 1.0, "b": 1.0},
            ranges={
                "a": ParameterRange(0.1, 10.0),
                "b": ParameterRange(0.1, 10.0),
            },
            sensitivity={"a": {"memory": 1.0}, "b": {"memory": -1.0}},
            capability={"cpu": 1.0, "memory": 1.0, "io": 1.0},
            target_resource="memory",
            optimizer_options={},
            estimators=[],
            excluded_signature_tokens=[
                optimizer_module._parameter_signature_token(
                    rankings[1]["optimized_params"]
                )
            ],
        )
    finally:
        optimizer_module._build_estimator = original_build
        optimizer_module.cluster_ranked_candidates = original_cluster

    assert all(item["subset_id"] != "s2-seen" for item in augmented)
    assert any(item.get("escape_batch_index") == 2 for item in augmented)
    assert audit["excluded_candidate_count_before_quota"] == 1
    assert audit["batches_used"] == 2
    assert audit["quota_satisfied"] is True


def _strict_quota_fixture(strict_count=2, missing_count=1):
    strict = [
        {
            "rank": 10 + index,
            "subset_id": f"strict-{index}",
            "strict_plan_cluster_key": f"strict:plan-{index}",
            "optimized_params": {"p": 1.0 + index / 10.0},
            "final_score": 10.0 - index,
        }
        for index in range(1, strict_count + 1)
    ]
    missing = [
        {
            "rank": index,
            "subset_id": f"missing-{index}",
            "strict_plan_cluster_key": f"missing:{index}",
            "optimized_params": {"p": 2.0 + index / 10.0},
            "final_score": 20.0 - index,
        }
        for index in range(1, missing_count + 1)
    ]
    clustering = {
        "representatives": strict + missing,
        "clusters": [
            {
                "cluster_key": item["strict_plan_cluster_key"],
                "strict_fingerprint_present": item in strict,
                "representative_subset_id": item["subset_id"],
            }
            for item in strict + missing
        ],
    }
    checks = [
        {
            "candidate_rank": item["rank"],
            "decision": {"pruned": True, "reason": "optimistic_bound"},
        }
        for item in strict
    ]
    return strict, missing, clustering, checks


def test_strict_plan_representatives_survive_optimistic_pruning() -> None:
    strict, missing, clustering, checks = _strict_quota_fixture()
    kept, pruned, updated_checks, audit = _enforce_strict_plan_replay_quota(
        plan_clustering=clustering,
        replay_kept=missing,
        replay_pruned=strict,
        pruning_checks=checks,
        required_plan_clusters=2,
        scenario_replay_cap=3,
    )

    assert [item["subset_id"] for item in kept[:2]] == ["strict-1", "strict-2"]
    assert not pruned
    assert all(item["strict_plan_replay_priority"] for item in kept[:2])
    assert all(check.get("policy_override", {}).get("applied") for check in updated_checks)
    assert audit["restored_after_optimistic_pruning_count"] == 2
    assert audit["final_queue_strict_plan_clusters"] == 2
    assert audit["strict_plan_coverage_conflict"] is False
    assert audit["quota_satisfied"] is True


def test_final_replay_queue_keeps_required_strict_clusters_after_feedback() -> None:
    strict, missing, clustering, checks = _strict_quota_fixture(
        strict_count=6, missing_count=2
    )
    kept, _pruned, _updated_checks, audit = _enforce_strict_plan_replay_quota(
        plan_clustering=clustering,
        replay_kept=missing,
        replay_pruned=strict,
        pruning_checks=checks,
        required_plan_clusters=2,
        scenario_replay_cap=5,
    )
    baseline = {
        "qps": 100.0,
        "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0,
        "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }
    table = {
        "p": {
            "cpu": 0.0,
            "memory": -0.1,
            "io": 0.0,
            "qps": 0.1,
            "confidence": 1.0,
        }
    }

    result = _orchestrate_staged_real_replays(
        kept,
        callback=_ReplayDriver(
            lambda _candidate, _context: {
                **baseline,
                "qps": 99.0,
                "db_qps": 99.0,
                "evidence": _formal_evidence(),
            }
        ),
        baseline_params={"p": 1.0},
        baseline_validation=baseline,
        sensitivity_table=table,
        target_resource="memory",
        benchmark="tpch",
        initial_top_k=5,
        max_top_k=5,
    )

    final_queue = result["final_replay_queue"]
    assert audit["quota_satisfied"] is True
    assert [item["subset_id"] for item in final_queue] == [
        f"strict-{index}" for index in range(1, 6)
    ]
    assert len({
        item["strict_plan_cluster_key"]
        for item in final_queue
        if item.get("strict_plan_replay_priority") is True
    }) == 5


def test_staged_real_replay_callback_and_diagnostic() -> None:
    candidates = [
        {"rank": index, "subset_id": f"s{index}", "optimized_params": {"p": 0.7 + index * 0.1}}
        for index in range(1, 13)
    ]
    baseline = {
        "qps": 100.0,
        "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0,
        "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }
    table = {"p": {"cpu": 0.0, "memory": -0.1, "io": 0.0, "qps": 0.1, "confidence": 1.0}}

    def callback(candidate, context):
        ordinal = context["replay_ordinal"]
        return {
            "qps": 100.0 + ordinal,
            "db_qps": 100.0 + ordinal,
            "db_qps_source": "database_status_counter",
            "p95_ms": 20.0,
            "p99_ms": 30.0,
            "correctness_passed": True,
            "error_rate": 0.0,
            "stable": True,
            "sample_coverage": 0.95,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
            "utilization_pct": {
                "cpu": 0.4,
                "memory": ({2: 0.89, 4: 0.88}[ordinal]) if ordinal in {2, 4} else 0.91,
                "io": 0.3,
            },
            "evidence": _formal_evidence(),
        }

    staged = _orchestrate_staged_real_replays(
        candidates,
        callback=_ReplayDriver(callback),
        baseline_params={"p": 1.0},
        baseline_validation=baseline,
        sensitivity_table=table,
        target_resource="memory",
    )
    assert staged["validated_count"] == 5
    assert staged["eligible_count"] == 2
    assert staged["online_t_update"]["enabled"] is False
    assert staged["online_t_update"]["reason"] == (
        "accepted_batch_needs_no_rejection_feedback"
    )
    assert staged["online_t_update"]["qps_role"] == "outer_acceptance_only"
    replayed_subset_ids = [
        row["candidate_subset_id"] for row in staged["evaluations"]
    ]
    assert replayed_subset_ids[:5] == [f"s{index}" for index in range(1, 6)]
    assert len(replayed_subset_ids) == len(set(replayed_subset_ids)) == 5
    assert len(staged["final_replay_queue"]) == 5
    assert staged["chosen"]["candidate_final_validation"]["db_qps"] == 104.0
    assert staged["diagnostic"] is None

    accepted_first_batch = _orchestrate_staged_real_replays(
        candidates,
        callback=_ReplayDriver(lambda candidate, context: {
            "qps": 101.0 + context["replay_ordinal"],
            "db_qps": 101.0 + context["replay_ordinal"],
            "db_qps_source": "database_status_counter",
            "p95_ms": 20.0,
            "p99_ms": 30.0,
            "correctness_passed": True,
            "error_rate": 0.0,
            "stable": True,
            "sample_coverage": 0.95,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
            "utilization_pct": {"cpu": 0.4, "memory": 0.89, "io": 0.3},
            "evidence": _formal_evidence(),
        }),
        baseline_params={"p": 1.0},
        baseline_validation=baseline,
        sensitivity_table=table,
        target_resource="memory",
    )
    assert accepted_first_batch["validated_count"] == 5
    assert accepted_first_batch["online_t_update"]["enabled"] is False
    assert accepted_first_batch["online_t_update"]["qps_role"] == "outer_acceptance_only"

    checkpoint_dir = tempfile.TemporaryDirectory()
    replay_ledger = FormalOuterReplayLedger.create_campaign(
        Path(checkpoint_dir.name) / "staged.json", outer_iter=1,
        campaign_identity={"campaign": "staged-audit"}, ordered_subset_ids=[],
    )
    replay_ledger.seal_replay(
        ranked_candidates=candidates,
        representative_ranks=[candidate["rank"] for candidate in candidates],
        pruning_log=[{"candidate_rank": 1, "decision": {"pruned": False}}],
        plan_cluster_log=[{"cluster_key": "c1"}],
        representative_log=candidates,
        suppression_log=[{"rank": 13, "suppression_reason": "scenario_cap"}],
    )
    rejected = _orchestrate_staged_real_replays(
        candidates,
        callback=_ReplayDriver(lambda candidate, context: {
            "qps": 101.0,
            "db_qps": 101.0,
            "db_qps_source": "database_status_counter",
            "p95_ms": 20.0,
            "p99_ms": 30.0,
            "correctness_passed": True,
            "error_rate": 0.0,
            "stable": True,
            "sample_coverage": 0.95,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
            "utilization_pct": {"cpu": 0.4, "memory": 0.91, "io": 0.3},
            "evidence": _formal_evidence(),
        }),
        baseline_params={"p": 1.0},
        baseline_validation=baseline,
        sensitivity_table=table,
        target_resource="memory",
        max_top_k=5,
        plan_clustering_log={"clusters": [{"cluster_key": "c1"}]},
        pruning_decision_log=[{"candidate_rank": 1, "decision": {"pruned": False}}],
        suppressed_candidate_log=[{"rank": 13, "subset_id": "suppressed"}],
        replay_ledger=replay_ledger,
    )
    assert rejected["validated_count"] == 5
    assert rejected["diagnostic"]["code"] == "no_outer_eligible_candidate_after_max_real_replays"
    assert rejected["diagnostic"]["reason_counts"] == {
        "target_utilization_strictly_decreased": 5
    }
    audit = rejected["staged_replay_audit"]
    assert [row["executed"] for row in audit["stage_transitions"]] == [True]
    stage_ranks = [row["candidate_ranks"] for row in audit["stage_transitions"]]
    assert stage_ranks[0] == [1, 2, 3, 4, 5]
    assert [len(ranks) for ranks in stage_ranks] == [5]
    assert sorted(rank for ranks in stage_ranks for rank in ranks) == list(range(1, 6))
    assert audit["plan_clustering"]["clusters"] == [{"cluster_key": "c1"}]
    assert audit["pruning_decisions"][0]["candidate_rank"] == 1
    assert audit["suppressed_candidates"][0]["rank"] == 13
    expansion = audit["root_cause_review_after_top_5"]
    assert expansion["diagnostic"]["code"] == (
        "no_outer_eligible_candidate_after_max_real_replays"
    )
    assert expansion["decision"] == "reject_outer_round_after_global_top_5"
    assert expansion["trigger"] == "no_outer_eligible_candidate_after_global_top_5"
    assert expansion["manual_approval_required"] is True
    assert replay_ledger.approved_root_cause_review() is None


def test_global_top_five_is_a_hard_replay_cap() -> None:
    candidates = [
        {"rank": index, "subset_id": f"s{index}", "optimized_params": {"p": float(index)}}
        for index in range(1, 13)
    ]
    baseline = {
        "qps": 100.0,
        "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0,
        "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }

    class NoReviewDriver:
        def __call__(self, _candidate, _context):
            return {
                "qps": 101.0,
                "db_qps": 101.0,
                "db_qps_source": "database_status_counter",
                "p95_ms": 20.0,
                "p99_ms": 30.0,
                "correctness_passed": True,
                "error_rate": 0.0,
                "stable": True,
                "sample_coverage": 0.95,
                "oom": False,
                "mysql_restart": False,
                "container_restart": False,
                "utilization_pct": {"cpu": 0.4, "memory": 0.91, "io": 0.3},
                "evidence": _formal_evidence(),
            }

        def restore(self, _candidate, context):
            return {
                "succeeded": True,
                "effective_params": context["baseline_params"],
                "fixed_settings_verified": True,
                "clean_template_verified": True,
                "evidence": "artifact://recovery",
            }

    try:
        _orchestrate_staged_real_replays(
            candidates,
            callback=NoReviewDriver(),
            baseline_params={"p": 0.0},
            baseline_validation=baseline,
            sensitivity_table={"p": {"memory": -0.1}},
            target_resource="memory",
            max_top_k=12,
        )
    except ValueError as exc:
        assert "one global fixed Top-5 batch" in str(exc)
    else:
        raise AssertionError("formal replay accepted max_top_k above five")


def test_v2_formal_config_requires_initial_top_five() -> None:
    config = json.loads(
        (ROOT / "config" / "mysql_optimizer_config.json").read_text(encoding="utf-8")
    )
    config["formal_outer_protocol"]["formal_search_initial_replay_top_k"] = 4
    try:
        optimizer_module.validate_config(config)
    except ValueError as exc:
        assert "formal_search_initial_replay_top_k must equal 5" in str(exc)
    else:
        raise AssertionError("v2 formal config accepted initial_top_k != 5")


def test_replay_resume_never_calls_an_already_marked_rank(tmp_path: Path) -> None:
    candidates = [
        {"rank": rank, "subset_id": f"s{rank}", "optimized_params": {"p": float(rank)}}
        for rank in range(1, 4)
    ]
    checkpoint = tmp_path / "campaign.json"
    ledger = FormalOuterReplayLedger.create_campaign(
        checkpoint, outer_iter=1, campaign_identity={"campaign": "resume-test"},
        ordered_subset_ids=[],
    )
    ledger.seal_replay(
        ranked_candidates=candidates, representative_ranks=[1, 2, 3],
        pruning_log=[{"rank": 1, "pruned": False}],
        plan_cluster_log=[{"cluster": "c1"}], representative_log=candidates,
        suppression_log=[],
    )
    baseline = {
        "qps": 100.0, "db_qps": 100.0, "db_qps_source": "database_status_counter", "p95_ms": 20.0, "p99_ms": 30.0,
        "db_qps_source": "database_status_counter",
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }
    first_calls = []

    def interrupted(candidate, context):
        first_calls.append(candidate["rank"])
        if candidate["rank"] == 2:
            raise KeyboardInterrupt("simulated abrupt interruption")
        return {
            "qps": 101.0, "db_qps": 101.0, "db_qps_source": "database_status_counter", "p95_ms": 20.0, "p99_ms": 30.0,
            "db_qps_source": "database_status_counter",
            "correctness_passed": True, "error_rate": 0.0, "stable": True,
            "sample_coverage": 0.95, "oom": False, "mysql_restart": False,
            "container_restart": False,
            "utilization_pct": {"cpu": 0.4, "memory": 0.91, "io": 0.3},
            "evidence": _formal_evidence(),
        }

    try:
        _orchestrate_staged_real_replays(
            candidates, callback=_ReplayDriver(interrupted),
            baseline_params={"p": 0.0}, baseline_validation=baseline,
            sensitivity_table={"p": {"memory": -0.1}}, target_resource="memory",
            replay_ledger=ledger,
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("simulated interruption did not propagate")
    assert first_calls == [1, 2]

    resumed = FormalOuterReplayLedger.load_campaign_verified(
        checkpoint, campaign_identity={"campaign": "resume-test"},
        ordered_subset_ids=[],
    )
    resumed._validate(candidates)
    resumed_calls = []

    def after_resume(candidate, context):
        resumed_calls.append(candidate["rank"])
        return {
            "qps": 101.0, "db_qps": 101.0, "db_qps_source": "database_status_counter", "p95_ms": 20.0, "p99_ms": 30.0,
            "db_qps_source": "database_status_counter",
            "correctness_passed": True, "error_rate": 0.0, "stable": True,
            "sample_coverage": 0.95, "oom": False, "mysql_restart": False,
            "container_restart": False,
            "utilization_pct": {"cpu": 0.4, "memory": 0.91, "io": 0.3},
            "evidence": _formal_evidence(),
        }

    try:
        _orchestrate_staged_real_replays(
            candidates, callback=_ReplayDriver(after_resume),
            baseline_params={"p": 0.0}, baseline_validation=baseline,
            sensitivity_table={"p": {"memory": -0.1}}, target_resource="memory",
            replay_ledger=resumed,
        )
    except ReplayRecoveryError as exc:
        assert "no reconciliation service" in str(exc)
    else:
        raise AssertionError("unresolved started replay was automatically rerun")
    assert resumed_calls == []


def test_crash_after_restore_before_outer_mark_reconciles_without_rank_rerun(
    tmp_path: Path,
) -> None:
    candidates = [
        {"rank": rank, "subset_id": f"s{rank}", "optimized_params": {"p": float(rank)}}
        for rank in range(1, 4)
    ]
    checkpoint = tmp_path / "two-phase.json"
    ledger = FormalOuterReplayLedger.create_campaign(
        checkpoint, outer_iter=1, campaign_identity={"campaign": "two-phase"},
        ordered_subset_ids=[],
    )
    ledger.seal_replay(
        ranked_candidates=candidates, representative_ranks=[1, 2, 3],
        representative_log=candidates,
    )
    baseline = {
        "qps": 100.0, "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0, "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }
    completed_attempts = {}
    first_calls = []

    class ReconcilingDriver(_ReplayDriver):
        def __call__(self, candidate, context):
            first_calls.append(candidate["rank"])
            measurement = {
                "qps": 101.0, "db_qps": 101.0,
                "db_qps_source": "database_status_counter",
                "p95_ms": 20.0, "p99_ms": 30.0,
                "correctness_passed": True, "error_rate": 0.0, "stable": True,
                "sample_coverage": 0.95, "oom": False, "mysql_restart": False,
                "container_restart": False,
                "utilization_pct": {"cpu": 0.4, "memory": 0.91, "io": 0.3},
                "evidence": _formal_evidence(),
            }
            completed_attempts[context["outer_replay_attempt_token"]] = {
                "measurement": measurement,
            }
            return measurement

        def restore(self, candidate, context):
            recovery = super().restore(candidate, context)
            completed_attempts[context["outer_replay_attempt_token"]]["recovery"] = recovery
            return recovery

        def reconcile_started_replay(self, started):
            proof = completed_attempts[started["attempt_token"]]
            return {"status": "completed", **proof}

    original_mark = ledger.mark_replayed

    def crash_before_mark(*args, **kwargs):
        raise KeyboardInterrupt("crash after restore, before outer commit")

    ledger.mark_replayed = crash_before_mark
    try:
        _orchestrate_staged_real_replays(
            candidates, callback=ReconcilingDriver(lambda *_: None),
            baseline_params={"p": 0.0}, baseline_validation=baseline,
            sensitivity_table={"p": {"memory": -0.1}}, target_resource="memory",
            replay_ledger=ledger,
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("post-restore crash was not injected")
    finally:
        ledger.mark_replayed = original_mark
    assert first_calls == [1]

    resumed = FormalOuterReplayLedger.load_campaign_verified(
        checkpoint, campaign_identity={"campaign": "two-phase"},
        ordered_subset_ids=[],
    )
    resumed._validate(candidates)
    resumed_calls = []
    driver = ReconcilingDriver(lambda *_: None)
    first_calls.clear()
    _orchestrate_staged_real_replays(
        candidates, callback=driver,
        baseline_params={"p": 0.0}, baseline_validation=baseline,
        sensitivity_table={"p": {"memory": -0.1}}, target_resource="memory",
        replay_ledger=resumed,
    )
    resumed_calls.extend(first_calls)
    assert resumed_calls == [2, 3]
    assert resumed.document["replayed_candidate_ranks"] == [1, 2, 3]


def test_production_campaign_resume_restores_r0_and_completed_subsets(tmp_path: Path) -> None:
    active = ["io_block_read_cost", "memory_block_read_cost"]
    checkpoint = tmp_path / "production-campaign.json"
    config = {
        "exp_id": "resume-production-entry", "benchmark": "job",
        "mysql": {"engine_name": "default", "device_type": 0},
        "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
        "target_resource": "memory",
        "cost_parameters": {
            "active": active,
            "ranges": {name: {"min_multiplier": 0.5, "max_multiplier": 2.0} for name in active},
        },
        "sensitivity_table": {
            name: {"cpu": 0.1, "memory": -0.2, "io": 0.1} for name in active
        },
        "optimizer": {"max_iter": 1, "patience": 1, "gamma": 0.5, "subset_max_workers": 1},
        "formal_outer_protocol": {
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 5,
            "formal_checkpoint_path": str(checkpoint),
            "max_outer_attempts": 1, "target_accepted_shrinks": 10,
        },
        "estimator": {"type": "python", "callable": "tests.fake_estimator:estimate"},
        "output": {"directory": str(tmp_path), "verbose": False},
        "commit_best": False,
    }
    baseline = {
        "qps": 100.0, "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0, "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }
    callback_calls = []

    def callback(candidate, context):
        callback_calls.append(candidate["rank"])
        raise AssertionError("fake estimator should produce no non-incumbent replay")

    run_optimization(
        config, client=FakeClient(), real_replay_callback=_ReplayDriver(callback),
        baseline_validation=baseline,
    )
    first = json.loads(checkpoint.read_text())
    assert first["r0_completed"] is True
    assert len(first["completed_subset_results"]) == 3
    identity = first["campaign_identity"]
    assert set((
        "parameter_ranges_float_hex", "sensitivity_table_float_hex",
        "optimizer_contract", "estimator_contract", "workload_plan_freeze",
    )) <= set(identity)
    run_optimization(
        config, client=FakeClient(), real_replay_callback=_ReplayDriver(callback),
        baseline_validation=baseline,
    )
    assert callback_calls == []

    changed = json.loads(json.dumps(config))
    changed["sensitivity_table"][active[0]]["memory"] = -0.20000000000000004
    try:
        run_optimization(
            changed, client=FakeClient(),
            real_replay_callback=_ReplayDriver(callback),
            baseline_validation=baseline,
        )
    except FormalReplayCheckpointError as exc:
        assert "campaign identity differs" in str(exc)
    else:
        raise AssertionError("changed experience table reused an old checkpoint")


def test_explicit_checkpoint_path_is_partitioned_by_outer_round(tmp_path: Path) -> None:
    configured = tmp_path / "formal.json"
    assert _formal_checkpoint_path(configured, tmp_path, 1) == configured
    assert _formal_checkpoint_path(configured, tmp_path, 2) == (
        tmp_path / "formal.round_2.json"
    )
    templated = tmp_path / "formal_{round_index}.json"
    assert _formal_checkpoint_path(templated, tmp_path, 3) == (
        tmp_path / "formal_3.json"
    )


def test_cap_and_cluster_suppression_reasons_are_complete() -> None:
    ranked = [{"rank": rank, "subset_id": f"s{rank}"} for rank in range(1, 5)]
    representatives = [ranked[0], ranked[2], ranked[3]]
    kept = [ranked[0], ranked[2], ranked[3]]
    log = _build_replay_suppression_log(
        non_incumbent_rankings=ranked, plan_representatives=representatives,
        replay_pruned=[], replay_kept=kept, scenario_replay_cap=2,
    )
    reasons = {row["subset_id"]: row["suppression_reason"] for row in log}
    assert reasons == {
        "s2": "non_representative_strict_plan_cluster_member",
        "s4": "beyond_frozen_scenario_replay_cap",
    }


def test_hard_gates_ignore_callback_claim_and_recovery_is_fail_closed() -> None:
    candidates = [{"rank": 1, "subset_id": "s1", "optimized_params": {"p": 1.1}}]
    baseline = {
        "qps": 100.0,
        "db_qps": 100.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 20.0,
        "p99_ms": 30.0,
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
    }
    bad_measurement = {
        "qps": 111.0,
        "db_qps": 110.0,
        "db_qps_source": "database_status_counter",
        "p95_ms": 23.0,
        "p99_ms": 30.0,
        "correctness_passed": True,
        "error_rate": 0.0,
        "stable": True,
        "sample_coverage": 0.95,
        "oom": False,
        "mysql_restart": False,
        "container_restart": False,
        "utilization_pct": {"cpu": 0.4, "memory": 0.8, "io": 0.3},
        "outer_eligible": True,
        "evidence": {},
    }
    rejected = _orchestrate_staged_real_replays(
        candidates,
        callback=_ReplayDriver(lambda candidate, context: bad_measurement),
        baseline_params={"p": 1.0},
        baseline_validation=baseline,
        sensitivity_table={"p": {"memory": -0.1, "qps": 0.1}},
        target_resource="memory",
    )
    gate = rejected["evaluations"][0]["candidate_final_validation"]["outer_gate"]
    assert not gate["eligible"]
    assert "qps_db_qps_mismatch" in gate["reasons"]
    assert "p95_lte_incumbent_1_10" in gate["reasons"]
    assert gate["missing_evidence_references"] == list(REQUIRED_REAL_REPLAY_EVIDENCE)

    broken_recovery = _ReplayDriver(
        lambda candidate, context: {**bad_measurement, "evidence": _formal_evidence()},
        recovery=lambda candidate, context: {
            "succeeded": False,
            "effective_params": {"p": 1.1},
            "fixed_settings_verified": False,
            "clean_template_verified": False,
        },
    )
    try:
        _orchestrate_staged_real_replays(
            candidates,
            callback=broken_recovery,
            baseline_params={"p": 1.0},
            baseline_validation=baseline,
            sensitivity_table={"p": {"memory": -0.1, "qps": 0.1}},
            target_resource="memory",
        )
        raise AssertionError("unverified recovery unexpectedly continued")
    except ReplayRecoveryError as exc:
        assert exc.evaluation["recovery"]["verified"] is False


def test_lane_lease_rejects_a_second_holder() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "lane.lock"
        with _LaneLease(path):
            try:
                with _LaneLease(path):
                    raise AssertionError("second lane lease unexpectedly acquired")
            except RuntimeError as exc:
                assert "lane lease is busy" in str(exc)


if __name__ == "__main__":
    test_mysql_entry_runs_every_nonempty_subset()
    test_formal_entry_parallel_subset_search_and_serial_real_replay()
    test_staged_top_k_policy_matches_source()
    test_online_t_delta_reranks_only_remaining_candidates()
    test_single_plan_cluster_triggers_bounded_escape_augmentation()
    test_missing_plan_fingerprints_do_not_satisfy_escape_quota()
    test_seen_candidate_filter_happens_before_escape_plan_quota()
    test_strict_plan_representatives_survive_optimistic_pruning()
    test_final_replay_queue_keeps_required_strict_clusters_after_feedback()
    test_staged_real_replay_callback_and_diagnostic()
    test_global_top_five_is_a_hard_replay_cap()
    test_v2_formal_config_requires_initial_top_five()
    test_hard_gates_ignore_callback_claim_and_recovery_is_fail_closed()
    test_lane_lease_rejects_a_second_holder()
    print("MySQL all-subset experience-table SGD integration tests passed", flush=True)
