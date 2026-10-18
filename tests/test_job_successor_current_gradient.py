from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SUCCESSOR = ROOT / "tools/job_exact_regime_successor_expand.py"


def load_successor():
    spec = importlib.util.spec_from_file_location(
        "job_successor_current_gradient_test",
        SUCCESSOR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs():
    incumbent_params = {"p": 1.0, "q": 2.0}
    incumbent_resource = {"cpu": 0.9, "io": 100.0, "memory": 2.45}
    capability = {"cpu": 3.0, "io": 256.0, "memory": 2.91015625}
    return incumbent_params, incumbent_resource, capability


def test_refresh_replaces_inherited_gradient_fields_from_current_round() -> None:
    module = load_successor()
    incumbent_params, incumbent_resource, capability = inputs()
    candidate = {
        "candidate_params": {"p": 1.1, "q": 2.0},
        "predicted_resource": {"cpu": 1.05, "io": 108.0, "memory": 2.50},
        "dScore/dU": {"stale": {"cpu": 999.0}},
        "T": {"stale": {"cpu": 999.0}},
        "g_j": {"stale": 999.0},
        "gradient_components": {
            "stale": {"cpu": {"phase": "original_gradient"}}
        },
    }

    module.refresh_current_round_gradient_fields(
        candidate,
        incumbent_params=incumbent_params,
        incumbent_model_resource=incumbent_resource,
        capability=capability,
        model_confidence=0.35,
        generation_eta=17.0,
    )

    assert candidate["gradient_contract"] == module.CURRENT_ROUND_GRADIENT_CONTRACT
    assert candidate["gradient_source"] == "current_incumbent_and_current_candidate_model"
    assert set(candidate["g_j"]) == {"p", "q"}
    assert "stale" not in candidate["T"]
    assert {
        values["phase"]
        for resources in candidate["gradient_components"].values()
        for values in resources.values()
    } == {"current_round_directional_secant"}
    assert candidate["generation_eta"] == 17.0
    assert math.isclose(candidate["eta"], math.log(1.1), abs_tol=1e-15)
    assert candidate["delta_param"]["p"]["absolute"] == pytest.approx(0.1)
    assert any(abs(value) > module.ZERO_GRADIENT_EPSILON for value in candidate["g_j"].values())


def test_nonzero_delta_with_all_zero_current_gradient_fails_closed() -> None:
    module = load_successor()
    incumbent_params, incumbent_resource, capability = inputs()
    candidate = {
        "candidate_params": {"p": 1.1, "q": 2.0},
        "predicted_resource": dict(incumbent_resource),
    }

    with pytest.raises(ValueError, match="g_j is all zero"):
        module.refresh_current_round_gradient_fields(
            candidate,
            incumbent_params=incumbent_params,
            incumbent_model_resource=incumbent_resource,
            capability=capability,
            model_confidence=1.0,
            generation_eta=1.1,
        )


def test_zero_delta_may_publish_an_all_zero_current_gradient() -> None:
    module = load_successor()
    incumbent_params, incumbent_resource, capability = inputs()
    candidate = {
        "candidate_params": dict(incumbent_params),
        "predicted_resource": dict(incumbent_resource),
    }

    module.refresh_current_round_gradient_fields(
        candidate,
        incumbent_params=incumbent_params,
        incumbent_model_resource=incumbent_resource,
        capability=capability,
        model_confidence=1.0,
        generation_eta=0.0,
    )

    assert candidate["eta"] == 0.0
    assert all(value == 0.0 for value in candidate["g_j"].values())
    assert all(
        values["absolute"] == 0.0
        for values in candidate["delta_param"].values()
    )


def test_directional_secant_obeys_gradient_chain_rule() -> None:
    module = load_successor()
    incumbent_params, incumbent_resource, capability = inputs()
    candidate = {
        "candidate_params": {"p": 1.1, "q": 1.8},
        "predicted_resource": {"cpu": 1.05, "io": 108.0, "memory": 2.50},
    }
    confidence = 0.6

    module.refresh_current_round_gradient_fields(
        candidate,
        incumbent_params=incumbent_params,
        incumbent_model_resource=incumbent_resource,
        capability=capability,
        model_confidence=confidence,
        generation_eta=1.0,
    )

    coordinates = {
        name: math.log(candidate["candidate_params"][name] / value)
        for name, value in incumbent_params.items()
    }
    observed = sum(
        candidate["g_j"][name] * coordinates[name]
        for name in coordinates
    )
    predicted_utilization = {
        name: candidate["predicted_resource"][name] / capability[name]
        for name in module.RESOURCES
    }
    incumbent_utilization = {
        name: incumbent_resource[name] / capability[name]
        for name in module.RESOURCES
    }
    expected = confidence * sum(
        module.resource_score_slope(name, predicted_utilization[name])
        * (predicted_utilization[name] - incumbent_utilization[name])
        for name in module.RESOURCES
    )
    assert observed == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_controller_rejects_inherited_or_zero_gradient_movement() -> None:
    successor = load_successor()
    incumbent_params, incumbent_resource, capability = inputs()
    candidate = {
        "candidate_params": {"p": 1.1, "q": 2.0},
        "predicted_resource": {"cpu": 1.05, "io": 108.0, "memory": 2.50},
    }
    successor.refresh_current_round_gradient_fields(
        candidate,
        incumbent_params=incumbent_params,
        incumbent_model_resource=incumbent_resource,
        capability=capability,
        model_confidence=1.0,
        generation_eta=1.0,
    )
    assert successor.current_round_gradient_fields_valid(candidate) is True

    candidate["gradient_components"]["p"]["cpu"]["phase"] = "original_gradient"
    assert successor.current_round_gradient_fields_valid(candidate) is False
    candidate["gradient_components"]["p"]["cpu"]["phase"] = (
        "current_round_directional_secant"
    )
    candidate["g_j"] = {"p": 0.0, "q": 0.0}
    assert successor.current_round_gradient_fields_valid(candidate) is False
