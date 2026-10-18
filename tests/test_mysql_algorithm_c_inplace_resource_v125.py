from __future__ import annotations

from scripts import prepare_mysql_algorithm_c_v22_cold_buffer_pools_v1 as cold
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v35 as controller


def test_cold_pool_waits_for_background_resize_completion(monkeypatch):
    target = 1_073_741_824
    observations = iter([
        {"size": target, "resize_status_code": 3, "pages_total": 65_536},
        {"size": target, "resize_status_code": 0, "pages_total": 65_536},
    ])
    calls = []

    def observe(_container):
        calls.append(1)
        return next(observations)

    monkeypatch.setattr(cold, "state", observe)
    monkeypatch.setattr(cold.time, "sleep", lambda _seconds: None)
    result = cold.wait_size("worker", target, maximum_seconds=10)
    assert result["resize_status_code"] == 0
    assert len(calls) == 2


def test_v125_contract_binds_hot_update_and_cold_pool_preparation():
    contract = controller.validate_contract()
    assert contract["container_stop_restart_or_replacement_performed"] is False
    assert {"resource_application", "cold_pool_preparation", "authority_builder"} <= set(contract["source_bindings"])
