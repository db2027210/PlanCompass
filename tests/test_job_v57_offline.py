from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BASE_V11 = ROOT / "tools/run_job_round8_strict_serial_base_v11_explicit_selection.py"
ROBUST_V10 = ROOT / "tools/run_job_round8_strict_serial_robust_v10_resource_median.py"
CONTROLLER = ROOT / "tools/job_fresh10_target10_autonomous_v57.py"
BOOTSTRAP = ROOT / "tools/bootstrap_job_v57_fresh0.py"


def test_explicit_selection_binding_does_not_use_process_argv() -> None:
    source = BASE_V11.read_text(encoding="utf-8")
    assert "Path(args.selection).resolve()" in source
    assert "selection_path = Path(sys.argv[1])" not in source
    assert "_ACTIVE_SELECTION_SHA256" in source
    assert '"binding_implementation": "job_v57_explicit_args_selection_v1"' in source
    assert 'v10.decide_state_update = globals()["decide_state_update"]' in source


def _load_robust_with_fakes() -> ModuleType:
    parameter_names = ("p",)

    def state_subset(value):
        return {
            name: copy.deepcopy(value[name])
            for name in ("accepted_count", "incumbent_score", "incumbent_params", "range_by_parameter")
        }

    def base_decision(candidate, actual, state, **kwargs):
        del candidate, actual, kwargs
        before = state_subset(state)
        return {
            "actual_score": 0.0,
            "accepted": False,
            "gradient_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "incumbent_state_before": before,
            "incumbent_state_after": copy.deepcopy(before),
        }

    base = SimpleNamespace(
        PARAMETER_NAMES=parameter_names,
        finite=lambda value, label: float(value),
        actual_score=lambda utilization: float(
            utilization["cpu"] + utilization["io"] + utilization["memory"]
        ),
        _state_subset=state_subset,
        decide_state_update=base_decision,
    )
    robust_v7 = SimpleNamespace(
        POLICY_CONTRACT="job_three_independent_exact_point_median_acceptance_v2",
        _base_decide_state_update=base_decision,
        _params=lambda candidate: {"p": float(candidate["candidate_params"]["p"])},
        _validate_formal_selection=lambda path: None,
        _POLICY_CACHE={},
    )
    robust_v8 = SimpleNamespace(
        robust_v7=robust_v7,
        base=base,
        run=lambda args: {},
    )
    fake_v9 = ModuleType("run_job_round8_strict_serial_robust_v9_state_guard")
    fake_v9.robust_v8 = robust_v8
    fake_v9.validate_formal_state_transitions = lambda result, state: None
    previous = sys.modules.get(fake_v9.__name__)
    sys.modules[fake_v9.__name__] = fake_v9
    try:
        spec = importlib.util.spec_from_file_location("job_v57_robust_test", ROBUST_V10)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(fake_v9.__name__, None)
        else:
            sys.modules[fake_v9.__name__] = previous
    return module


def test_resource_component_median_is_rescored_before_acceptance() -> None:
    module = _load_robust_with_fakes()
    module._prior_evidence = lambda candidate: [
        {"actual_resource": {"cpu": 1.0, "io": 20.0, "memory": 1.0}},
        {"actual_resource": {"cpu": 2.0, "io": 30.0, "memory": 2.0}},
    ]
    candidate = {
        "candidate_params": {"p": 2.0},
        "selection_role": "trusted",
        "incumbent_update_eligible": True,
        "used_for_inner_incumbent_update": True,
        "used_for_gradient": True,
    }
    state = {
        "accepted_count": 0,
        "incumbent_score": 0.0,
        "incumbent_params": {"p": 1.0},
        "range_by_parameter": {"p": {"min": 1.0, "max": 1.0}},
        "gradient_observation_count": 0,
    }
    # Formal raw resources are cpu=3, io=10, memory=3.  Component medians are
    # cpu=2, io=20, memory=2 and are then converted to utilization and rescored.
    actual = {"cpu": 1.0, "io": 10.0 / 256.0, "memory": 3.0 / 2.91015625}
    decision = module.decide_state_update(candidate, actual, state)
    expected = 2.0 / 3.0 + 20.0 / 256.0 + 2.0 / 2.91015625
    assert abs(decision["actual_score"] - expected) < 1e-12
    assert decision["robust_actual_resource"] == {"cpu": 2.0, "io": 20.0, "memory": 2.0}
    assert decision["accepted"] is True
    assert state["accepted_count"] == 1
    assert state["incumbent_score"] == decision["actual_score"]


def test_probe_is_always_state_neutral() -> None:
    module = _load_robust_with_fakes()
    state = {
        "accepted_count": 0,
        "incumbent_score": 1.0,
        "incumbent_params": {"p": 1.0},
        "range_by_parameter": {"p": {"min": 1.0, "max": 1.0}},
    }
    before = copy.deepcopy(state)
    result = module.decide_state_update(
        {"selection_role": "probe"},
        {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        state,
    )
    assert result["accepted"] is False
    assert state == before


def test_controller_preserves_bounded_discovery_and_packages_dependencies() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    assert "import job_fresh10_target10_autonomous_v55 as v55" in source
    assert 'implementation.CONTROLLER_VERSION = "v57"' in source
    assert "implementation.v49.ROBUST_RUNNER = ROBUST_V10" in source
    assert "_sealed_copy(BASE_V10" in source
    assert "_sealed_copy(ROBUST_V9" in source
    assert "manual_candidate" not in source


def test_bootstrap_is_fresh0_and_quarantines_without_retry() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert '"accepted_count": 0' in source
    assert '"gradient_observation_count": 0' in source
    assert '"status": "quarantined_no_retry"' in source
    assert '"retry_allowed": False' in source
    assert '"historical_v55_diagnostics_training_only": True' in source
    assert "physical_invocation_count" in source
