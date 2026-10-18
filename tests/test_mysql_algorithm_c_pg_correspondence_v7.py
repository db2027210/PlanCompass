from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import mysql_algorithm_c_benchmark_probe_resilience_v1 as probe
from scripts import mysql_algorithm_c_reset_affinity_v1 as reset_affinity
from scripts import mysql_cross_project_wave_coordination_v27 as gate_v27
from scripts import mysql_cross_project_wave_coordination_v28 as gate_v28
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v106 as controller


def test_fresh_controller_installs_all_startup_bindings() -> None:
    names = controller.main.__code__.co_names
    assert "install" in names
    assert "prepare_fresh_xp" in names
    assert "wrap" in names
    assert "validate_contract" in names


def test_gate_install_replaces_and_can_restore_inventory() -> None:
    original_inventory = gate_v27._inventory
    original_gate = gate_v27.rolling_quiet_gate
    try:
        gate_v28.install()
        assert gate_v27._inventory is gate_v28._inventory
        assert gate_v27.rolling_quiet_gate is gate_v28.rolling_quiet_gate
    finally:
        gate_v27._inventory = original_inventory
        gate_v27.rolling_quiet_gate = original_gate


def test_reset_filesystem_helpers_are_taskset_pinned() -> None:
    names = reset_affinity.prepare_fresh_xp.__code__.co_names
    constants = reset_affinity.prepare_fresh_xp.__code__.co_consts
    code = reset_affinity.prepare_fresh_xp.__code__
    assert "management_cpuset" in code.co_varnames + code.co_cellvars
    assert "taskset" in constants
    assert "subprocess" in names


def test_probe_retry_is_bounded_and_read_only_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def timeout_then_pass(*args):
        calls.append(args)
        if len(calls) < 3:
            raise subprocess.TimeoutExpired(["mysql"], 30)
        return {"ready": True}

    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    wrapped = probe.wrap(timeout_then_pass)
    assert wrapped({}, Path("/tmp/test"), True, False) == {"ready": True}
    assert len(calls) == 3

    calls.clear()
    with pytest.raises(subprocess.TimeoutExpired):
        wrapped({}, Path("/tmp/test"), False, False)
    assert len(calls) == 1
