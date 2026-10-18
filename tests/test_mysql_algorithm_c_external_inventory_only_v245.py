from __future__ import annotations

from scripts import mysql_external_process_inventory_only_gate_v1 as gate
from scripts import preflight_mysql_four_scene_external_inventory_only_v1 as preflight
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v136 as controller
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v60 as runtime
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v61 as runtime_v61


def test_external_processes_are_retained_as_nonblocking_inventory(monkeypatch):
    monkeypatch.setattr(
        gate,
        "target_local_resource_proof",
        lambda _cpus: {"passed": True, "errors": [], "selected_workers": ["tpch_w1", "tpch_w2"]},
    )
    external = {"pid": 42, "comm": "postgres", "dynamically_idle": False}
    monkeypatch.setattr(
        gate.strict_gate,
        "_inventory",
        lambda _cpus, _workers: ([external], [], set()),
    )
    monkeypatch.setattr(
        gate.resource_readback,
        "_host_memory",
        lambda: (10, 0.0, {"MemAvailable": 10}),
    )
    result = gate.rolling_quiet_gate(
        run_id="test", required_seconds=15, maximum_wait_seconds=120,
        target_cpus={1, 2},
    )
    assert result["status"] == "PASS"
    assert result["errors"] == []
    assert result["external_processes_inventory_only"] is True
    assert result["strict_external_noninterference_evidence_eligible"] is False
    assert result["samples"][0]["external_workloads"] == [external]
    assert result["samples"][0]["blocking_external_workloads"] == []


def test_target_mysql_resource_failure_still_blocks(monkeypatch):
    monkeypatch.setattr(
        gate,
        "target_local_resource_proof",
        lambda _cpus: {"passed": False, "errors": ["cpu_quota"], "selected_workers": []},
    )
    monkeypatch.setattr(gate.strict_gate, "_inventory", lambda *_args: ([], [], set()))
    monkeypatch.setattr(gate.resource_readback, "_host_memory", lambda: (10, 0.0, {}))
    result = gate.rolling_quiet_gate(
        run_id="test", required_seconds=15, maximum_wait_seconds=120,
        target_cpus={1},
    )
    assert result["status"] == "BLOCKED_FAIL_CLOSED"
    assert result["errors"] == ["target_mysql_local_resource_isolation_unproven"]


def test_prestart_inventory_filters_only_blocking_semantics(monkeypatch):
    process = {"pid": 7, "comm": "postgres"}
    container = {"name": "other_db"}
    monkeypatch.setattr(
        preflight.predecessor,
        "_process_inventory",
        lambda _cpus: ([process], [process]),
    )
    monkeypatch.setattr(
        preflight.predecessor,
        "_docker_inventory",
        lambda _cpus, _names: ([container], [container]),
    )
    processes, process_blockers = preflight._process_inventory({1})
    containers, container_blockers = preflight._docker_inventory({1}, {"target"})
    assert process_blockers == []
    assert container_blockers == []
    assert processes[0]["would_block_under_strict_external_noninterference"] is True
    assert containers[0]["would_block_under_strict_external_noninterference"] is True


def test_state_is_classified_separately_but_keeps_algorithm_credit(monkeypatch):
    monkeypatch.setattr(
        controller,
        "BASE_STATE_DOCUMENT",
        lambda **_kwargs: {"accepted_state_credit_prohibited": True},
    )
    state = controller.state_document()
    assert state["formal_evidence_eligible"] is False
    assert state["strict_external_noninterference_evidence_eligible"] is False
    assert state["accepted_state_credit_prohibited"] is False
    assert state["external_processes_inventory_only"] is True


def test_runtime_replaces_both_point_and_wave_gate_boundaries():
    assert runtime._source.count("external_process_inventory_only_validate_authority(") == 2
    assert runtime._source.count("external_gate.rolling_quiet_gate(") == 2


def test_v246_runtime_restores_interval_control_binding():
    assert runtime_v61.interval_control.__name__.endswith(
        "mysql_algorithm_c_interval_control_v3"
    )
