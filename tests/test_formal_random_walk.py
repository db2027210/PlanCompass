import json
from copy import deepcopy
from pathlib import Path

import pytest

from formal_random_walk import RandomWalkState, axes_from_frozen_search_space


FROZEN = Path(__file__).parents[1] / "preflight" / "frozen_formal_config_20260721_v3"


def _payload(name: str):
    return json.loads((FROZEN / name).read_text(encoding="utf-8"))


def test_frozen_state_is_deterministic_and_resumable():
    search = _payload("search_spaces.json")
    methods = _payload("method_protocols.json")
    left = RandomWalkState.from_frozen(search, methods)
    right = RandomWalkState.from_frozen(search, methods)

    first = left.propose()
    assert first == right.propose()
    left.observe(accepted=False)
    right.observe(accepted=False)
    assert left.sigma == pytest.approx(0.175)

    restored = RandomWalkState.restore(
        json.loads(json.dumps(left.as_dict())),
        axes_from_frozen_search_space(search),
    )
    assert restored.propose() == left.propose()


def test_cannot_skip_a_durable_outer_decision():
    state = RandomWalkState.from_frozen(
        _payload("search_spaces.json"), _payload("method_protocols.json")
    )
    state.propose()
    with pytest.raises(RuntimeError, match="no outer decision"):
        state.propose()
    state.observe(accepted=False)
    with pytest.raises(RuntimeError, match="exactly one outstanding"):
        state.observe(accepted=False)


def test_duplicate_must_use_its_frozen_terminal_outcome():
    state = RandomWalkState.from_frozen(
        _payload("search_spaces.json"), _payload("method_protocols.json")
    )
    state.seen_signatures.clear()
    candidate = state.propose()
    from formal_random_walk import _signature

    state.pending_duplicate = True
    state.seen_signatures.add(_signature(state.axes, candidate))
    with pytest.raises(RuntimeError, match="recorded as duplicate"):
        state.observe(accepted=False)
    state.record_outcome("duplicate")
    assert state.consumed_budget == 1


def test_accept_updates_incumbent_and_sigma():
    state = RandomWalkState.from_frozen(
        _payload("search_spaces.json"), _payload("method_protocols.json")
    )
    state.propose()
    proposal = list(state.proposals[-1])
    state.observe(accepted=True)
    assert state.incumbent == proposal
    assert state.sigma == pytest.approx(0.275)
    assert state.consecutive_rejections == 0


def test_early_stop_after_frozen_minimum_and_rejection_count():
    state = RandomWalkState.from_frozen(
        _payload("search_spaces.json"), _payload("method_protocols.json")
    )
    for _ in range(6):
        state.propose()
        state.observe(accepted=False)
    assert state.should_stop
    assert state.consumed_budget == 6
    with pytest.raises(RuntimeError, match="stopping rule"):
        state.propose()


def test_every_proposal_stays_inside_frozen_bounds():
    search = _payload("search_spaces.json")
    state = RandomWalkState.from_frozen(search, _payload("method_protocols.json"))
    for _ in range(10):
        candidate = state.propose()
        for name, value in candidate.items():
            bounds = search["mysql"]["parameters"][name]
            assert bounds["min"] <= value <= bounds["max"]
        state.observe(accepted=(_ == 0))
        if state.should_stop:
            break


def test_frozen_method_formulas_are_not_silently_ignored():
    methods = deepcopy(_payload("method_protocols.json"))
    methods["random_walk"]["sigma_after_outer_reject"] = "max(0.01,sigma*0.9)"
    with pytest.raises(ValueError, match="protocol mismatch"):
        RandomWalkState.from_frozen(_payload("search_spaces.json"), methods)


def test_extra_random_walk_fields_fail_closed():
    methods = deepcopy(_payload("method_protocols.json"))
    methods["random_walk"]["unfrozen_override"] = True
    with pytest.raises(ValueError, match="fields differ"):
        RandomWalkState.from_frozen(_payload("search_spaces.json"), methods)


def test_durable_restore_rebinds_frozen_seed_and_budget():
    search = _payload("search_spaces.json")
    methods = _payload("method_protocols.json")
    state = RandomWalkState.from_frozen(search, methods)
    payload = json.loads(json.dumps(state.as_dict()))
    payload["seed"] = 99
    with pytest.raises(ValueError, match="differs from frozen method"):
        RandomWalkState.restore_frozen(payload, search, methods)
