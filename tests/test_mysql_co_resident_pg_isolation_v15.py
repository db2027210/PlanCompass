from __future__ import annotations

import copy

from scripts import mysql_co_resident_pg_isolation_v15 as subject
from scripts import preflight_mysql_four_scene_v3_pg_wrapper_v2 as point_preflight


def _contract() -> dict[str, object]:
    return {
        "maximum_recognized_processes": 4,
        "maximum_total_lifetime_cpu_cores": 0.1,
    }


def _proof(pid: int) -> dict[str, object]:
    return {
        "pid": pid,
        "launcher_pid": 900,
        "launcher_command_sha256": "a" * 64,
        "wrapper_rss_bytes": 4 * 1024 * 1024,
        "launcher_rss_bytes": 20 * 1024 * 1024,
        "wrapper_lifetime_cpu_cores": 0.001,
        "launcher_lifetime_cpu_cores": 0.002,
        "combined_lifetime_cpu_cores": 0.003,
        "capture_script_sha256": "b" * 64,
        "disk1_lineage": True,
        "disk2_lineage": False,
        "external_mutation": False,
    }


def _blocked(pid: int) -> dict[str, object]:
    names = sorted(subject.REMOVABLE)
    return {
        "passed": False,
        "status": "BLOCKED_FAIL_CLOSED",
        "errors": [f"pid_{pid}:{name}" for name in names],
        "workloads": [{"pid": pid, "errors": names}],
    }


def test_inventory_snapshot_survives_live_process_disappearance(monkeypatch):
    pid = 123
    workloads = [{
        "pid": pid,
        "pg_capture_wrapper_inventory_proof": _proof(pid),
    }]

    def must_not_reopen_proc(*_args, **_kwargs):
        raise AssertionError("inventory proof should avoid a second /proc read")

    monkeypatch.setattr(subject.predecessor, "wrapper_evidence", must_not_reopen_proc)
    result = subject.apply_allowance(workloads, _blocked(pid), _contract())
    assert result["passed"] is True
    assert result["errors"] == []
    assert result["pg_capture_wrapper_allowance"]["inventory_time_proof_reused"] is True
    assert result["workloads"][0]["inventory_time_proof_reused"] is True


def test_unproved_full_host_process_remains_blocked(monkeypatch):
    pid = 124
    monkeypatch.setattr(
        subject.predecessor, "wrapper_evidence", lambda *_args, **_kwargs: None,
    )
    original = _blocked(pid)
    result = subject.apply_allowance([{"pid": pid}], original, _contract())
    assert result["passed"] is False
    assert result["errors"] == original["errors"]
    assert result["pg_capture_wrapper_allowance"]["recognized_pid_count"] == 0


def test_snapshot_evidence_still_obeys_total_cpu_bound():
    pid = 125
    proof = _proof(pid)
    proof["combined_lifetime_cpu_cores"] = 0.2
    original = _blocked(pid)
    result = subject.apply_allowance(
        [{"pid": pid, "pg_capture_wrapper_inventory_proof": proof}],
        copy.deepcopy(original), _contract(),
    )
    assert result["passed"] is False
    assert result["errors"] == original["errors"]
    assert result["pg_capture_wrapper_allowance"]["passed"] is False


def test_inventory_enrichment_tolerates_wrapper_exit(monkeypatch):
    monkeypatch.setattr(
        subject,
        "_BASE_EXTERNAL_PROCESS_INVENTORY",
        lambda *_args, **_kwargs: {
            "workloads": [{"pid": 126, "cpus_allowed_list": "0"}],
        },
    )
    monkeypatch.setattr(subject, "load_contract", _contract)
    monkeypatch.setattr(
        subject.predecessor,
        "_proc_rss",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    inventory = subject.snapshot_external_process_inventory("run", {0})
    assert inventory == {
        "workloads": [{"pid": 126, "cpus_allowed_list": "0"}],
    }


def test_point_preflight_drops_only_a_vanished_inventory_row(monkeypatch):
    row = {
        "pid": 127,
        "command": "capture shim",
        "reason": "active_benchmark_or_controller_cpu_overlap",
    }
    monkeypatch.setattr(
        point_preflight.predecessor,
        "_process_inventory",
        lambda *_args, **_kwargs: ([dict(row)], [dict(row)]),
    )
    monkeypatch.setattr(point_preflight.allowance, "load_contract", _contract)
    monkeypatch.setattr(
        point_preflight.allowance.predecessor,
        "wrapper_evidence",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(point_preflight, "_process_gone", lambda *_args: True)
    inventory, blockers = point_preflight._process_inventory({0})
    assert blockers == []
    assert inventory[0]["process_exited_before_point_evaluation"] is True


def test_point_preflight_keeps_an_unproved_live_row_blocked(monkeypatch):
    row = {
        "pid": 128,
        "command": "unknown live process",
        "reason": "active_benchmark_or_controller_cpu_overlap",
    }
    monkeypatch.setattr(
        point_preflight.predecessor,
        "_process_inventory",
        lambda *_args, **_kwargs: ([dict(row)], [dict(row)]),
    )
    monkeypatch.setattr(point_preflight.allowance, "load_contract", _contract)
    monkeypatch.setattr(
        point_preflight.allowance.predecessor,
        "wrapper_evidence",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(point_preflight, "_process_gone", lambda *_args: False)
    _inventory, blockers = point_preflight._process_inventory({0})
    assert blockers == [row]
