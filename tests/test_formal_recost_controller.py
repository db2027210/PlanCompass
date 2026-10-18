from dataclasses import replace
import json
from pathlib import Path

from formal_acceptance_policy import LivePoint
from formal_recost_controller import (
    FrozenRecostOuterController,
    ReplayResult,
    build_frozen_estimator_factory,
)
from formal_recost_core import FrozenRecostProposalEngine, MYSQL_COST_NAMES, load_frozen_recost_contract


ROOT = Path(__file__).parents[1]
FROZEN = ROOT / "preflight/frozen_formal_config_20260721_v3"


def engine():
    settings, specs = load_frozen_recost_contract(
        FROZEN / "method_protocols.json", FROZEN / "search_spaces.json"
    )
    return FrozenRecostProposalEngine(settings, specs, estimator_version=settings.estimator_version)


class _StubEstimator:
    def __init__(self, proposal_engine, *, flat=False):
        self.proposal_engine = proposal_engine
        self.flat = flat

    def __call__(self, params):
        if self.flat:
            return 1.0
        normalized = self.proposal_engine.normalize(params)
        return -sum((normalized[name] - 0.75) ** 2 for name in MYSQL_COST_NAMES)

    def observe_outer(self, **_observation):
        return None


def estimator_factory(proposal_engine, _incumbent, scenario):
    return _StubEstimator(proposal_engine, flat=scenario == "none")


def defaults():
    document = json.loads((FROZEN / "search_spaces.json").read_text())
    return {name: float(document["mysql"]["default"][name]) for name in MYSQL_COST_NAMES}


def measurement(**changes):
    base = LivePoint(
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
        cpu_utilization=0.5,
        memory_utilization=0.9,
        io_utilization=0.4,
    )
    return replace(base, **changes)


def replay(params, point, fingerprint):
    return ReplayResult(
        status="complete",
        params=dict(params),
        measurement=point,
        plan_fingerprints={"query": fingerprint},
    )


def test_one_accept_then_four_rejections_early_stops_at_six_and_selects_accepted():
    baseline = replay(defaults(), measurement(), "default")

    def execute(request):
        assert request.inner_iterations
        assert all(row["label"] == "inner_step_accepted" for row in request.inner_iterations)
        if request.candidate_index == 1:
            point = measurement(qps=102.0, memory_utilization=0.88)
        else:
            point = measurement(qps=80.0, memory_utilization=0.89)
        return replay(request.proposal.params, point, f"plan-{request.candidate_index}")

    trace = FrozenRecostOuterController(
        engine(),
        acceptance_policy_path=FROZEN / "acceptance_policy.json",
        scenario="memory",
        xp=False,
        estimator_factory=estimator_factory,
    ).optimize(baseline, execute)
    assert trace.real_candidate_count == 6
    assert trace.accepted_candidate_count == 1
    assert trace.final_candidate_index == 1
    assert trace.stop_reason == "consecutive_outer_rejections_after_minimum_candidates"


def test_failed_candidate_consumes_budget_and_is_retained():
    baseline = replay(defaults(), measurement(), "default")

    def execute(request):
        return ReplayResult(
            status="failed",
            params=dict(request.proposal.params),
            measurement=None,
            plan_fingerprints={},
            failure_reason="timeout",
        )

    trace = FrozenRecostOuterController(
        engine(),
        acceptance_policy_path=FROZEN / "acceptance_policy.json",
        scenario="memory",
        xp=False,
        estimator_factory=estimator_factory,
    ).optimize(baseline, execute)
    assert trace.real_candidate_count == 6
    assert all(item.budget_consumed for item in trace.candidates)
    assert all(item.replay.failure_reason == "timeout" for item in trace.candidates)
    assert trace.final_source == "default"


def test_none_flat_estimator_replays_duplicates_then_keeps_default():
    baseline = replay(defaults(), measurement(memory_utilization=0.5), "default")

    def execute(request):
        assert request.proposal.duplicate_of_incumbent
        return replay(request.proposal.params, measurement(memory_utilization=0.5), "default")

    trace = FrozenRecostOuterController(
        engine(),
        acceptance_policy_path=FROZEN / "acceptance_policy.json",
        scenario="none",
        xp=False,
        estimator_factory=estimator_factory,
    ).optimize(baseline, execute)
    assert trace.real_candidate_count == 6
    assert trace.accepted_candidate_count == 0
    assert trace.final_source == "default"


def test_executor_exception_is_a_retained_budgeted_failure():
    baseline = replay(defaults(), measurement(), "default")

    def execute(request):
        raise TimeoutError("frozen hard timeout")

    trace = FrozenRecostOuterController(
        engine(),
        acceptance_policy_path=FROZEN / "acceptance_policy.json",
        scenario="memory",
        xp=False,
        estimator_factory=estimator_factory,
    ).optimize(baseline, execute)
    assert trace.real_candidate_count == 6
    assert all(item.replay.status == "failed" for item in trace.candidates)
    assert all("TimeoutError" in item.replay.failure_reason for item in trace.candidates)


def test_applied_parameter_mismatch_can_never_be_accepted():
    baseline = replay(defaults(), measurement(), "default")

    def execute(request):
        wrong = dict(request.proposal.params)
        wrong["key_compare_cost"] = defaults()["key_compare_cost"]
        return replay(wrong, measurement(qps=150.0, memory_utilization=0.7), "better")

    trace = FrozenRecostOuterController(
        engine(),
        acceptance_policy_path=FROZEN / "acceptance_policy.json",
        scenario="memory",
        xp=False,
        estimator_factory=estimator_factory,
    ).optimize(baseline, execute)
    assert trace.accepted_candidate_count == 0
    assert all(item.replay.failure_reason == "params_applied_mismatch" for item in trace.candidates)


def test_production_factory_builds_frozen_current_run_estimator():
    proposal_engine = engine()
    baseline = replay(defaults(), measurement(), "default")
    estimator = build_frozen_estimator_factory("mysql_hybench_ap")(proposal_engine, baseline, "memory")
    value = estimator(defaults())
    assert isinstance(value, float)
    assert estimator.version == proposal_engine.settings.estimator_version
    assert estimator.uses_live_database is False
