from __future__ import annotations

from tools import build_tpcc_v49_empirical_interval_successor as v49


PARAMS = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
RANGES = {
    name: {"min_value": value * 0.5, "max_value": value * 1.5}
    for name, value in PARAMS.items()
}


def _candidate(rank: int, scale: float = 1.0) -> dict:
    params = {name: value * scale for name, value in PARAMS.items()}
    return {
        "rank": rank,
        "selection_role": "trusted",
        "workload_plan_fingerprint": "ratio-plan-sha256:test",
        "exact_point_key": f"point-{rank}-{scale}",
        "candidate_params": params,
        "predicted_resource": {"cpu": 2.0, "io": 20.0, "memory": 2.5},
        "predicted_score": v49.v44.v6._score(
            {"cpu": 2.0, "io": 20.0, "memory": 2.5}
        ),
    }


def _observation(cpu: float, io: float, memory: float) -> dict:
    resource = {"cpu": cpu, "io": io, "memory": memory}
    return {"actual_resource": resource, "actual_score": v49.v44.v6._score(resource)}


def test_exact_repeat_does_not_apply_distance_zero_residual() -> None:
    candidate = _candidate(1)
    historical = {"cpu": 2.04, "io": 26.0, "memory": 2.55}
    interval = {
        name: {
            "lower": value * 0.99,
            "median": value,
            "upper": value * 1.01,
            "relative_span": 0.02,
        }
        for name, value in historical.items()
    }
    previous = v49._EXACT_INTERVALS
    v49._EXACT_INTERVALS = {candidate["exact_point_key"]: interval}
    try:
        model = v49._local_model(
            candidate,
            historical,
            v49.v44.v6._score(historical),
            [(candidate, _observation(2.20, 32.0, 2.80))],
            RANGES,
        )
    finally:
        v49._EXACT_INTERVALS = previous
    assert model["predicted_resource"] == historical
    assert model["all_resource_reconciliation_alpha"] == 0.0
    assert model["distance_zero_residual_applied"] is False
    assert model["prediction_inside_calibration_interval"] is True


def test_nonexact_model_bounds_alpha_persists_loo_and_interval() -> None:
    candidate = _candidate(9, 1.10)
    anchors = [
        (_candidate(1, 0.90), _observation(2.05, 25.0, 2.55)),
        (_candidate(2, 1.20), _observation(2.08, 27.0, 2.58)),
    ]
    historical = {"cpu": 2.01, "io": 24.0, "memory": 2.52}
    previous = v49._EXACT_INTERVALS
    v49._EXACT_INTERVALS = {}
    try:
        model = v49._local_model(
            candidate,
            historical,
            v49.v44.v6._score(historical),
            anchors,
            RANGES,
        )
    finally:
        v49._EXACT_INTERVALS = previous
    assert 0.0 <= model["all_resource_reconciliation_alpha"] <= 1.0
    assert model["nonexact_loo"]["defined"] is True
    assert model["distance_zero_residual_applied"] is False
    for name, value in model["predicted_resource"].items():
        bounds = model["predicted_resource_interval"][name]
        assert bounds["lower"] <= value <= bounds["upper"]


def test_nonexact_model_rejects_only_distance_zero_anchor() -> None:
    candidate = _candidate(1)
    previous = v49._EXACT_INTERVALS
    v49._EXACT_INTERVALS = {}
    try:
        try:
            v49._local_model(
                candidate,
                {"cpu": 2.0, "io": 20.0, "memory": 2.5},
                candidate["predicted_score"],
                [(candidate, _observation(2.1, 24.0, 2.6))],
                RANGES,
            )
        except RuntimeError as exc:
            assert "non-zero-distance anchors" in str(exc)
        else:
            raise AssertionError("distance-zero residual was unexpectedly accepted")
    finally:
        v49._EXACT_INTERVALS = previous
