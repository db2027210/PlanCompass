#!/usr/bin/env python3
"""Unit checks for the 18-run VM task scheduler."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path

import double_loop_vm_task_scheduler as scheduler


def main() -> int:
    config_path = scheduler.ROOT / "config/double_loop_vm_scheduler_20260718.json"
    config = scheduler.load_json(config_path)
    catalog = scheduler.build_task_catalog(config)
    assert len(catalog) == 18
    assert sorted(task_id for task_id in catalog if task_id.startswith("b")) == [
        "b4_r2",
        "b4_r3",
        "b5_r2",
        "b5_r3",
        "b6_r2",
        "b6_r3",
    ]
    assert "b4_r1" not in catalog
    assert "b5_r1" not in catalog
    assert "b6_r1" not in catalog
    assert catalog["b4_r2"]["dependencies"] == ["a16_r1"]
    assert catalog["b5_r3"]["dependencies"] == ["a13_r1"]
    assert catalog["b6_r2"]["dependencies"] == ["a18_r1"]
    override_config = copy.deepcopy(config)
    override_config["task_profile_overrides"] = {
        "a13_r1": {"launcher_args": ["--resume-candidate-dir", "/tmp/a13"]}
    }
    override_catalog = scheduler.build_task_catalog(override_config)
    assert override_catalog["a13_r1"]["profile"]["launcher_args"] == [
        "--resume-candidate-dir",
        "/tmp/a13",
    ]
    assert override_catalog["b5_r2"]["profile"] == catalog["b5_r2"]["profile"]
    assert override_catalog["a13_r1"]["profile"] is not catalog["a13_r1"]["profile"]

    only_task_config = copy.deepcopy(config)
    only_task_config["adopted_runs"] = {}
    only_task_config["holds"] = {
        identity: "excluded by --only-task"
        for identity in catalog
        if identity != "a14_r1"
    }
    only_task_state = scheduler.initial_state(only_task_config, catalog)
    assert only_task_state["tasks"]["a14_r1"]["status"] == "pending"
    assert sum(
        row["status"] == "held" for row in only_task_state["tasks"].values()
    ) == 17
    assert scheduler.parse_cpu_set("220-222,476-478") == {
        220,
        221,
        222,
        476,
        477,
        478,
    }
    try:
        scheduler.parse_cpu_set("4-2")
    except ValueError:
        pass
    else:
        raise AssertionError("descending cpuset range must be rejected")

    isolated = copy.deepcopy(config)
    isolated["adopted_runs"] = {}
    isolated["holds"] = {"a18_r1": "pilot"}
    state = scheduler.initial_state(isolated, catalog)
    assert state["tasks"]["a18_r1"]["status"] == "held"
    assert scheduler.dependencies_complete(state["tasks"]["b6_r2"], state["tasks"]) is False
    state["tasks"]["a18_r1"]["status"] = "completed"
    assert scheduler.dependencies_complete(state["tasks"]["b6_r2"], state["tasks"]) is True

    hold_config = copy.deepcopy(isolated)
    hold_config["holds"] = {"a16_r1": "external owner"}
    scheduler.apply_config_holds(state, hold_config)
    assert state["tasks"]["a16_r1"]["status"] == "held"
    assert state["tasks"]["a16_r1"]["hold_reason"] == "external owner"
    assert state["tasks"]["a18_r1"]["status"] == "completed"
    state["tasks"]["a16_r1"]["status"] = "pending"

    selected = scheduler.choose_next(state, isolated)
    assert selected is not None and selected["lane"] == "cpu"
    assert selected["task_id"] == "a16_r1"
    selected = scheduler.choose_next(
        state,
        isolated,
        excluded_task_ids={"a16_r1"},
    )
    assert selected is not None and selected["task_id"] != "a16_r1"

    snapshot = {
        "mem_available_gib": 200.0,
        "shm_free_gib": 100.0,
        "output_disk_free_gib": 1000.0,
        "root_disk_free_gib": 1000.0,
        "host_load_1m_per_cpu": 0.1,
        "cpu_pressure_some_avg10": 1.0,
        "io_pressure_full_avg10": 1.0,
    }
    safe, reason = scheduler.safe_for_task(isolated, catalog["e13_r1"], snapshot)
    assert safe is True and reason == "ok"
    requested_gib = float(catalog["e13_r1"]["profile"]["memory_mib"]) / 1024.0
    reserved_gib = float(
        isolated["safety"]["min_mem_available_gib_after_launch"]
    )
    unsafe = dict(
        snapshot,
        mem_available_gib=reserved_gib + requested_gib - 1.0,
    )
    safe, reason = scheduler.safe_for_task(isolated, catalog["e13_r1"], unsafe)
    assert safe is False and "mem_available_after" in reason

    unsafe_load = dict(
        snapshot,
        cpu_pressure_some_avg10=41.0,
    )
    isolated["safety"]["max_cpu_pressure_some_avg10"] = 40.0
    safe, reason = scheduler.safe_for_task(isolated, catalog["e13_r1"], unsafe_load)
    assert safe is False and "cpu_pressure_some_avg10" in reason

    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "state.json"
        scheduler.write_json(path, {"count": len(catalog)})
        assert json.loads(path.read_text(encoding="utf-8"))["count"] == 18

        task_root = Path(raw) / "attempt_1"
        scheduler.write_json(
            task_root / "guest_exit_diagnostics.json",
            {"output": "kernel: Out of memory: Killed process 42 (postgres)"},
        )
        assert scheduler.guest_exit_failure({"task_root": str(task_root)}) == (
            "guest OOM occurred during formal real replay"
        )
        scheduler.write_json(
            task_root / "guest_exit_diagnostics.json",
            {"output": "no guest kernel failures"},
        )
        assert scheduler.guest_exit_failure({"task_root": str(task_root)}) is None

        scheduler_root = Path(raw) / "current"
        external_root = Path(raw) / "external"
        external_root.mkdir(parents=True)
        control = external_root / "control" / "live" / "attempt_1"
        scheduler.write_json(
            control / "launch_spec.json",
            {"slot": {"slot_id": "vm-test", "cpuset_cpus": "220-228,476-484"}},
        )
        global_config = {
            "global_max_active": 4,
            "global_scheduler_state_glob": str(Path(raw) / "*" / "scheduler_state.json"),
        }
        expected_dispatch_lock = Path(raw) / ".double_loop_global_dispatch.lock"
        assert scheduler.global_dispatch_lock_path(global_config) == expected_dispatch_lock
        dispatch_lock = scheduler.acquire_global_dispatch_lock(global_config)
        try:
            assert dispatch_lock is not None
            assert expected_dispatch_lock.is_file()
        finally:
            scheduler.release_global_dispatch_lock(dispatch_lock)
        current_state = {"tasks": {}}
        scheduler.write_json(
            external_root / "scheduler_state.json",
            {
                "tasks": {
                    "live": {
                        "task_id": "live",
                        "status": "preparing",
                        "worker_pid": os.getpid(),
                        "worker_result": str(control / "worker_result.json"),
                    },
                    "stale": {
                        "task_id": "stale",
                        "status": "running",
                        "runner_pid": 999999999,
                    },
                }
            },
        )
        audit = scheduler.global_active_audit(
            global_config,
            current_state,
            scheduler_root,
        )
        assert audit["enabled"] is True
        assert audit["active_count"] == 1
        assert audit["available"] == 3
        assert audit["active_tasks"][0]["task_id"] == "live"
        assert audit["active_tasks"][0]["slot_cpuset_cpus"] == "220-228,476-484"
        occupied = scheduler.occupied_cpu_ids(audit)
        assert 220 in occupied and 484 in occupied
        assert scheduler.parse_cpu_set("229-237,485-493").isdisjoint(occupied)
        assert not scheduler.parse_cpu_set("228-237,484-493").isdisjoint(occupied)

    child_pid = os.fork()
    if child_pid == 0:
        os._exit(0)
    try:
        for _ in range(100):
            status_path = Path(f"/proc/{child_pid}/status")
            if status_path.is_file() and "State:\tZ" in status_path.read_text(encoding="utf-8"):
                break
            time.sleep(0.01)
        assert scheduler.process_alive(child_pid) is False
    finally:
        os.waitpid(child_pid, 0)

    child_pid = os.fork()
    if child_pid == 0:
        os._exit(0)
    for _ in range(100):
        status_path = Path(f"/proc/{child_pid}/status")
        if status_path.is_file() and "State:\tZ" in status_path.read_text(encoding="utf-8"):
            break
        time.sleep(0.01)
    assert child_pid in scheduler.reap_finished_children()
    assert not Path(f"/proc/{child_pid}").exists()
    print("double-loop VM task scheduler tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
