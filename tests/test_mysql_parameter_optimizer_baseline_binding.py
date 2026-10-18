from unittest.mock import Mock

import pytest

import mysql_parameter_optimizer as optimizer


def test_build_estimator_binds_explicit_runtime_incumbent(monkeypatch):
    observed = {}

    def fake_builder(adapter, config):
        observed.update(config)
        return object()

    monkeypatch.setattr(optimizer, "build_resource_estimator", fake_builder)
    runtime_incumbent = {
        "io_block_read_cost": 1.0,
        "memory_block_read_cost": 0.25,
    }
    config = {
        "estimator": {"type": "plan_physics"},
        "cost_parameters": {
            "baseline_values": {
                "io_block_read_cost": 9.0,
                "memory_block_read_cost": 9.0,
            }
        },
    }

    optimizer._build_estimator_for_incumbent(
        config,
        Mock(),
        runtime_incumbent,
    )

    assert observed["baseline_params"] == runtime_incumbent
    assert observed["baseline_params"] is not runtime_incumbent


def test_build_estimator_supports_frozen_cost_baseline_without_legacy_key(
    monkeypatch,
):
    observed = {}

    def fake_builder(adapter, config):
        observed.update(config)
        return object()

    monkeypatch.setattr(optimizer, "build_resource_estimator", fake_builder)
    frozen = {"io_block_read_cost": 1.0}
    config = {
        "estimator": {"type": "ratio_scale"},
        "cost_parameters": {"baseline_values": frozen},
    }

    optimizer._build_estimator(config, Mock())

    assert observed["baseline_params"] == frozen


def test_build_estimator_fails_closed_without_any_baseline():
    with pytest.raises(ValueError, match="explicit non-empty incumbent"):
        optimizer._build_estimator(
            {"estimator": {"type": "plan_physics"}},
            Mock(),
        )
