from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from formal_acceptance_policy import LivePoint
from formal_random_walk import RandomWalkState
from formal_random_walk_controller import FrozenRandomWalkController


ROOT = Path(__file__).parents[1]
FROZEN = ROOT / "preflight/frozen_formal_config_20260721_v3"
POLICY = FROZEN / "acceptance_policy.json"


def document(name):
    return json.loads((FROZEN / name).read_text())


def point(qps=100.0, memory=0.90):
    return LivePoint(
        True, True, 0.99, 0.0, False, False,
        qps, None, None, 10.0, 20.0, 0.5, memory, 0.4,
    )


def controller():
    search = document("search_spaces.json")
    state = RandomWalkState.from_frozen(search, document("method_protocols.json"))
    return FrozenRandomWalkController(
        state,
        policy_path=POLICY,
        scenario="memory",
        xp=False,
        default_params=search["mysql"]["default"],
        baseline=point(),
    )


def test_accept_reject_and_final_selection_are_causal():
    run = controller()
    first = run.propose()
    record, decision = run.record_complete(
        params_applied=first,
        point=point(qps=102.0, memory=0.90),
        plan_changed=False,
    )
    assert decision is not None and decision.accepted and record.accepted
    second = run.propose()
    _, rejected = run.record_complete(
        params_applied=second,
        point=point(qps=90.0, memory=0.90),
        plan_changed=True,
    )
    assert rejected is not None and not rejected.accepted
    selection, params = run.select_final()
    assert selection.candidate_index == 1
    assert params == first


def test_failure_and_applied_mismatch_consume_without_acceptance():
    run = controller()
    candidate = run.propose()
    mismatched = dict(candidate)
    first_name = next(iter(mismatched))
    mismatched[first_name] *= 1.0001
    record, decision = run.record_complete(
        params_applied=mismatched,
        point=point(qps=200.0),
        plan_changed=True,
    )
    assert decision is None
    assert record.reason == "params_applied_mismatch"
    assert record.bad_configuration
    assert run.state.consumed_budget == 1


def test_resume_preserves_pending_proposal_exactly():
    run = controller()
    pending = run.propose()
    restored = FrozenRandomWalkController.restore_frozen(
        json.loads(json.dumps(run.as_dict())),
        policy_path=POLICY,
        search_space=document("search_spaces.json"),
        method_protocols=document("method_protocols.json"),
    )
    assert restored._pending_params == pending
    restored.record_failed(
        "timeout",
        params_applied=pending,
        reason="maximum_measurement_timeout",
        bad_configuration=True,
    )
    assert restored.state.consumed_budget == 1
    assert restored.records[0].outcome == "timeout"
