import pytest

from formal_mysql_sgd_campaign import (
    FormalSGDNotReady,
    _bind_runtime_estimator_baseline,
)


def test_plan_estimator_is_bound_to_verified_fresh_r0_resources():
    config = {
        "estimator": {
            "type": "plan_physics",
            "baseline_real": {"cpu": 0.1, "memory": 0.2, "io": 0.3},
        }
    }
    measurement = {
        "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.5},
        "db_qps": 99.0,
    }

    bound = _bind_runtime_estimator_baseline(config, measurement)

    assert bound["estimator"]["baseline_real"] == {
        "cpu": 0.4,
        "memory": 0.9,
        "io": 0.5,
    }
    assert bound["estimator"]["baseline_real_authority"]["qps_used"] is False
    assert config["estimator"]["baseline_real"]["cpu"] == 0.1


def test_non_plan_estimator_is_left_unchanged():
    config = {"estimator": {"type": "command", "command": ["true"]}}

    assert _bind_runtime_estimator_baseline(config, {}) == config


def test_plan_estimator_fails_closed_on_missing_resource():
    with pytest.raises(FormalSGDNotReady, match="incomplete"):
        _bind_runtime_estimator_baseline(
            {"estimator": {"type": "ratio_scale"}},
            {"utilization_pct": {"cpu": 0.4, "memory": 0.9}},
        )
