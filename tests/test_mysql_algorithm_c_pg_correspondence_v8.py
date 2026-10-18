from __future__ import annotations

from scripts import mysql_cross_project_wave_coordination_v28 as gate_v28
from scripts import mysql_cross_project_wave_coordination_v31 as gate_v31
from scripts import mysql_cross_project_wave_coordination_v27 as gate_v27
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v107 as controller


def journal_row(**overrides):
    row = {
        "comm": "jbd2/nvme2n1-8",
        "ppid": 2,
        "state": "D",
        "command_class": "other",
        "frozen_mysql_worker": None,
        "same_gate_process_tree": False,
        "target_device_mounted": True,
        "target_device_open": False,
    }
    row.update(overrides)
    return row


def test_only_exact_kernel_journal_is_classified() -> None:
    assert gate_v31._exact_target_journal(journal_row()) is True
    assert gate_v31._exact_target_journal(journal_row(ppid=100)) is False
    assert gate_v31._exact_target_journal(journal_row(target_device_open=True)) is False
    assert gate_v31._exact_target_journal(journal_row(comm="mysqld")) is False
    assert gate_v31._exact_target_journal(journal_row(comm="3")) is False


def test_effective_wait_extends_clean_window_without_sibling_completion(monkeypatch) -> None:
    captured = {}

    def fake_gate(**kwargs):
        captured.update(kwargs)
        return {"required_clean_seconds": kwargs["required_seconds"], "status": "PASS"}

    monkeypatch.setattr(gate_v31, "_ORIGINAL_V28_GATE", fake_gate)
    result = gate_v31.rolling_quiet_gate(
        run_id="test", required_seconds=15.0, maximum_wait_seconds=60.0,
        target_cpus={1},
    )
    assert captured["maximum_wait_seconds"] == 120.0
    release = result["target_journal_classification_release"]
    assert release["clean_window_seconds_unchanged"] == 15.0
    assert release["sibling_scene_completion_checked"] is False
    assert release["cross_scene_completion_dependency"] is False


def test_controller_preserves_installer_identity_and_rebinds_targets() -> None:
    assert "coordination_v28" in controller.main.__code__.co_names
    assert "_inventory" in controller.main.__code__.co_names
    assert "rolling_quiet_gate" in controller.main.__code__.co_names
    expected = str(__import__("pathlib").Path(gate_v27.__file__).resolve())
    assert controller._inventory_trampoline.__code__.co_filename == expected
    assert controller._gate_trampoline.__code__.co_filename == expected
    assert (
        controller._inventory_trampoline.__globals__["successor_inventory"]
        is gate_v31._inventory
    )
    assert (
        controller._gate_trampoline.__globals__["successor_gate"]
        is gate_v31.rolling_quiet_gate
    )


def test_v31_calls_captured_v28_targets_after_install(monkeypatch) -> None:
    monkeypatch.setattr(
        gate_v31,
        "_ORIGINAL_V28_INVENTORY",
        lambda target_cpus, target_workers: ([], [], {123}),
    )
    monkeypatch.setattr(gate_v28, "_inventory", controller._inventory_trampoline)
    assert gate_v31._inventory({1}, {"job_w1"}) == ([], [], {123})

    monkeypatch.setattr(
        gate_v31,
        "_ORIGINAL_V28_GATE",
        lambda **kwargs: {
            "required_clean_seconds": kwargs["required_seconds"],
            "status": "PASS",
        },
    )
    monkeypatch.setattr(
        gate_v28, "rolling_quiet_gate", controller._gate_trampoline,
    )
    result = gate_v31.rolling_quiet_gate(
        run_id="test", required_seconds=15.0,
        maximum_wait_seconds=60.0, target_cpus={1},
    )
    assert result["status"] == "PASS"
