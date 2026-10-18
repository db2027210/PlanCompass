from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from formal_acceptance_policy import LivePoint
from formal_mysql_recost_session import (
    _causal_state,
    _default_params,
    _point_parent,
    _record_candidate_artifacts,
    static_validation,
)
from formal_mysql_recost_spec import load_recost_schedule


def _point() -> LivePoint:
    return LivePoint(
        correctness_passed=True,
        stable=True,
        sample_coverage=0.99,
        error_rate=0.0,
        oom=False,
        database_restarted=False,
        qps=100.0,
        xap_qps=None,
        xtp_tps=None,
        latency_p95_ms=10.0,
        latency_p99_ms=20.0,
        cpu_utilization=0.4,
        memory_utilization=0.9,
        io_utilization=0.3,
    )


def test_static_validation_pins_exact_session_targets_and_estimator():
    result = static_validation()
    assert result["target_count"] == 14
    assert result["target_ordinals"] == [1, 9, 17, 19, 25, 36, 38, 46, 49, 59, 64, 72, 75, 81]
    assert result["recost_settings"]["estimator_implementation_sha256"] == (
        "3c0397fb60ea9d21c3f353a543c57bdab54d6efb8e5ca421510fb5dc82c43247"
    )


def test_round_layout_uses_registered_round_directories():
    root = Path("/tmp/not-created-recost-test")
    assert _point_parent(root, "baseline_search") == root / "round_000_baseline"
    assert _point_parent(root, "candidate_7") == root / "round_007"
    assert _point_parent(root, "final_validation_2") == root / "validation/final_validation_2"


def test_current_run_causal_rebuild_never_imports_another_run():
    params = _default_params()
    state = {
        "baseline_point": {
            "params": params,
            "live_point": asdict(_point()),
            "plan_fingerprint_sha256": "baseline",
            "path": "baseline",
        },
        "candidates": [
            {
                "point_status": "complete",
                "params_replayed": params,
                "live_point": asdict(_point()),
                "plan_fingerprint_sha256": "candidate",
                "path": "candidate",
                "failure_reason": None,
                "outer_candidate_accepted": False,
                "observed_utility_improvement": -0.2,
                "params_proposed": params,
            },
            {
                "point_status": "failed",
                "params_replayed": params,
                "live_point": None,
                "plan_fingerprint_sha256": None,
                "path": "failed",
                "failure_reason": "timeout",
                "outer_candidate_accepted": False,
                "observed_utility_improvement": -1.0,
                "params_proposed": params,
            },
        ],
    }
    engine, incumbent, estimator = _causal_state(state, load_recost_schedule()[0])
    assert engine.real_candidate_count == 2
    assert engine.consecutive_outer_rejections == 2
    assert incumbent.artifact_relative_path == "baseline"
    estimator_state = estimator.state()
    assert estimator_state["outer_observation_count"] == 2
    assert len(estimator_state["observations"]) == 3


def test_candidate_artifacts_are_complete_checksummed_and_idempotent():
    decision = {
        "schema_version": 1,
        "label": "outer_candidate_accepted",
        "candidate_index": 1,
        "outer_candidate_accepted": False,
        "budget_consumed": True,
        "inner_estimator_made_outer_decision": False,
        "acceptance_policy_path": "/frozen/acceptance_policy.json",
        "acceptance_policy_sha256": "a" * 64,
        "params_before": {"p": 1.0},
        "params_candidate": {"p": 2.0},
        "params_applied": {"p": 2.0},
        "plan_before_sha256": "b" * 64,
        "plan_candidate_sha256": "c" * 64,
        "point_status": "complete",
        "decision": None,
        "observed_utility_improvement": -1.0,
        "early_stop_after_candidate": False,
        "decided_at_utc": "2026-07-21T00:00:00Z",
    }
    with tempfile.TemporaryDirectory(prefix="recost-session-test-") as temporary:
        root = Path(temporary)
        point = root / "round_001/attempt_01"
        point.mkdir(parents=True)
        inner = root / "inner.jsonl"
        inner.write_text(json.dumps({"label": "inner_step_accepted"}) + "\n")
        _record_candidate_artifacts(point, inner, decision)
        _record_candidate_artifacts(point, inner, decision)
        required = {
            "inner_iterations.jsonl", "decision.json", "params_before.json",
            "params_candidate.json", "params_applied.json", "plan_fingerprint.json",
            "round_manifest.json", "round_summary.json", "checksums.sha256",
        }
        assert required <= {item.name for item in point.iterdir()}
