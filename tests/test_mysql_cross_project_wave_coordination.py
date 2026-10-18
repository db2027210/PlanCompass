from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as boundary


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_external_controller_and_physical_workload_are_distinct() -> None:
    assert coordination.command_kind(
        "python pg_algorithm_c_lane_entry.py --target cpu"
    ) == "controller"
    assert coordination.command_kind(
        "java com.hybench.HyBench -f /tmp/stmt_postgres.toml"
    ) == "workload"
    assert coordination.command_kind(
        "postgres: postgres tpch 127.0.0.1 SELECT"
    ) == "workload"
    assert coordination.command_kind(
        "python run_pg_algorithm_b_scene.py --target memory"
    ) == "controller"
    assert coordination.command_kind(
        "psql -h 127.0.0.1 -p 59521 -c EXPLAIN SELECT 1"
    ) == "workload"
    assert coordination.command_kind(
        "postgres: postgres postgres 127.0.0.1 CREATE DATABASE fullgrid_job"
    ) == "workload"
    assert coordination.command_kind("postgres: checkpointer") is None


def test_exclusive_lease_is_kernel_visible_and_blocks_another_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "wave.lock"
    lease = coordination.acquire_exclusive(
        timeout_seconds=1.0, lock_path=lock_path,
    )
    try:
        assert coordination.lock_held_exclusively_by(lease.holder_pid, lock_path)
        code = (
            "from pathlib import Path; "
            "from scripts import mysql_cross_project_wave_coordination_v1 as c; "
            f"raise SystemExit(0 if c.try_acquire_exclusive(Path({str(lock_path)!r})) "
            "is None else 9)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, check=False,
            capture_output=True, text=True, timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
    finally:
        assert coordination.release(lease) >= 0.0
    reacquired = coordination.try_acquire_exclusive(lock_path)
    assert reacquired is not None
    coordination.release(reacquired)


def test_blocking_waiter_joins_kernel_queue_and_acquires_after_release(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "blocking.lock"
    holder_code = (
        "import fcntl,time; from pathlib import Path; "
        f"f=Path({str(lock_path)!r}).open('a+'); "
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX); "
        "print('ready', flush=True); time.sleep(0.25)"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        lease = coordination.acquire_exclusive_blocking(
            timeout_seconds=2.0, lock_path=lock_path,
        )
        try:
            assert lease.wait_seconds >= 0.1
            assert coordination.lock_held_exclusively_by(
                lease.holder_pid, lock_path,
            )
        finally:
            coordination.release(lease)
    finally:
        holder.wait(timeout=5)
        assert holder.returncode == 0


def test_frozen_coordination_contract_binds_runtime_sources() -> None:
    value = json.loads(coordination.CONTRACT_PATH.read_text(encoding="utf-8"))
    assert value["status"] == "FROZEN_RUNTIME_VALIDATED"
    assert value["formal_scope"] == "mysql_only"
    assert value["cross_engine_comparison_prohibited"] is True
    assert value["candidate_batches"] == [[1], [2], [3], [4], [5]]
    assert value["job_tpch_concurrent_full_suite_preserved"] is True
    assert value["native_xp_workload_unchanged"] is True
    assert value["per_wave_quiet_seconds"] == 15
    assert value["maximum_quiet_wait_while_held_seconds"] == 20
    assert value["lease_acquisition_timeout_seconds"] == 3
    assert value["maximum_gate_attempts_per_wave"] == 1
    assert value["persistent_d_state_required_consecutive_samples"] == 3
    allowance = value["co_resident_isolated_pg_allowance"]
    assert allowance["allowed_external_cpu_union"] == (
        "48-91,96-99,128-131,136-183"
    )
    assert allowance["frozen_controller_client_cpu_sets"] == {
        "xp_cpu": "128-131",
        "xp_io": "144-159",
        "job_memory": "176-179",
        "tpch_memory": "180-183",
    }
    assert allowance["allowed_postgres_ports"] == [
        59513, 59514, 59515, 59516, 59521, 59522, 59523, 59524,
    ]
    assert len(allowance["containers"]) == 8
    assert value["fair_release_yield_seconds"] >= 0.5
    for label in (
        "coordination_source", "boundary_resume_controller",
        "runtime_watchdog", "live_preflight", "auto_resume_waiter",
    ):
        path = ROOT / value["source_bindings"][f"{label}_path"]
        assert path.is_file() and not path.is_symlink()
        assert _sha(path) == value["source_bindings"][f"{label}_sha256"]


def test_rolling_quiet_gate_ignores_one_sample_d_state_but_records_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pressures = iter([
        {"blocking_d_state_pids": [42], "load1": 1.0},
        {"blocking_d_state_pids": [], "load1": 1.0},
        {"blocking_d_state_pids": [], "load1": 1.0},
    ])
    monkeypatch.setattr(
        coordination, "external_process_inventory",
        lambda run_id, ignored: {"workloads": [], "controllers": []},
    )
    monkeypatch.setattr(coordination, "same_run_process_pids", lambda run_id: set())
    monkeypatch.setattr(coordination, "sanitized_rows", lambda rows: rows)
    monkeypatch.setattr(coordination.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(coordination.time, "monotonic", iter((0.0, 0.0, 20.0, 20.0)).__next__)
    from scripts import preflight_mysql_four_scene_live_v8 as preflight
    monkeypatch.setattr(preflight, "host_pressure", lambda: next(pressures))
    result = coordination.rolling_quiet_gate(
        run_id="test_run", required_seconds=15.0,
        maximum_wait_seconds=60.0, interval_seconds=1.0,
    )
    assert result["status"] == "PASS"
    assert result["samples"][0]["transient_d_state_pids"] == [42]
    assert result["samples"][0]["persistent_d_state_pids"] == []
    assert result["persistent_d_state_required_consecutive_samples"] == 3


def test_rolling_quiet_gate_records_but_exempts_same_run_watchdog_d_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pressures = iter([
        {"blocking_d_state_pids": [42], "load1": 1.0},
        {"blocking_d_state_pids": [42], "load1": 1.0},
        {"blocking_d_state_pids": [42], "load1": 1.0},
    ])
    monkeypatch.setattr(
        coordination, "external_process_inventory",
        lambda run_id, ignored: {"workloads": [], "controllers": []},
    )
    monkeypatch.setattr(coordination, "same_run_process_pids", lambda run_id: {42})
    monkeypatch.setattr(coordination, "sanitized_rows", lambda rows: rows)
    monkeypatch.setattr(coordination.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        coordination.time, "monotonic",
        iter((0.0, 0.0, 8.0, 16.0, 16.0)).__next__,
    )
    from scripts import preflight_mysql_four_scene_live_v8 as preflight
    monkeypatch.setattr(preflight, "host_pressure", lambda: next(pressures))
    result = coordination.rolling_quiet_gate(
        run_id="formal_unique_run", required_seconds=15.0,
        maximum_wait_seconds=20.0, interval_seconds=1.0,
    )
    assert result["status"] == "PASS"
    assert result["samples"][0]["host_pressure"]["raw_blocking_d_state_pids"] == [42]
    assert result["samples"][0]["host_pressure"]["same_run_exempted_d_state_pids"] == [42]
    assert result["samples"][0]["host_pressure"]["blocking_d_state_pids"] == []
    assert result["samples"][0]["persistent_d_state_pids"] == []


def test_rolling_gate_records_host_load_but_uses_direct_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pressures = iter([
        {"blocking_d_state_pids": [], "load1": 200.0},
        {"blocking_d_state_pids": [], "load1": 200.0},
    ])
    monkeypatch.setattr(
        coordination, "external_process_inventory",
        lambda run_id, ignored: {"workloads": [], "controllers": []},
    )
    monkeypatch.setattr(coordination, "same_run_process_pids", lambda run_id: set())
    monkeypatch.setattr(coordination, "sanitized_rows", lambda rows: rows)
    monkeypatch.setattr(coordination.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        coordination.time, "monotonic", iter((0.0, 0.0, 16.0, 16.0)).__next__,
    )
    from scripts import preflight_mysql_four_scene_live_v8 as preflight
    monkeypatch.setattr(preflight, "host_pressure", lambda: next(pressures))
    result = coordination.rolling_quiet_gate(
        run_id="isolated_high_host_load", required_seconds=15.0,
        maximum_wait_seconds=20.0, interval_seconds=1.0,
    )
    assert result["status"] == "PASS"
    assert result["global_load1_policy"] == (
        "diagnostic_only_direct_isolation_is_authoritative"
    )
    assert result["samples"][0]["load1_diagnostic_only"] is True
    assert result["samples"][0]["normalized_load1"] > 0.10
    assert result["samples"][0]["errors"] == []


def test_frozen_disk1_pg_cohort_can_pass_dynamic_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    contract = tmp_path / "coordination.json"
    contract.write_text(json.dumps({
        "co_resident_isolated_pg_allowance": {
            "allowed_external_cpu_union": "80-99,160-167",
            "allowed_postgres_ports": [59513, 59514, 59515, 59516],
            "maximum_external_workload_processes": 32,
            "maximum_external_client_rss_bytes": 17179869184,
            "minimum_host_mem_available_bytes": 549755813888,
            "maximum_memory_full_psi_avg10": 0.10,
            "containers": [],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(coordination, "CONTRACT_PATH", contract)
    monkeypatch.setattr(coordination, "_target_cpu_union", lambda: {0, 1, 104, 105})
    monkeypatch.setattr(
        coordination, "_live_frozen_pg_container_readback",
        lambda policy: {"passed": True, "errors": [], "containers": []},
    )
    monkeypatch.setattr(
        coordination, "_meminfo",
        lambda: {"MemAvailable": 700 * 1024**3, "SwapTotal": 0},
    )
    monkeypatch.setattr(coordination, "_memory_full_psi_avg10", lambda: 0.0)
    result = coordination.evaluate_co_resident_pg_isolation([
        {
            "pid": 99999999,
            "command": "/disk1/resource_adaptive/run java stmt_postgres.toml",
            "command_sha256": "a" * 64,
            "cpus_allowed_list": "160-163",
        },
        {
            "pid": 99999998,
            "command": "psql --port=59513 -f canonical_job.sql",
            "command_sha256": "c" * 64,
            "cpus_allowed_list": "160-163",
        },
        {
            "pid": 99999997,
            "command": (
                "psql postgresql://postgres:CHANGE_ME@127.0.0.1:59514/"
                "hybench_sf10x -f canonical_job.sql"
            ),
            "command_sha256": "d" * 64,
            "cpus_allowed_list": "160-163",
        },
    ])
    assert result["status"] == "PASS_ISOLATED_CO_RESIDENT_PG"
    assert result["passed"] is True
    assert result["workload_count"] == 3
    assert result["workloads"][0]["overlap_target_cpus"] == []


def test_co_resident_pg_cpu_overlap_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    contract = tmp_path / "coordination.json"
    contract.write_text(json.dumps({
        "co_resident_isolated_pg_allowance": {
            "allowed_external_cpu_union": "80-99,160-167",
            "allowed_postgres_ports": [59513],
            "maximum_external_workload_processes": 32,
            "maximum_external_client_rss_bytes": 17179869184,
            "minimum_host_mem_available_bytes": 1,
            "maximum_memory_full_psi_avg10": 1.0,
            "containers": [],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(coordination, "CONTRACT_PATH", contract)
    monkeypatch.setattr(coordination, "_target_cpu_union", lambda: {160})
    monkeypatch.setattr(
        coordination, "_live_frozen_pg_container_readback",
        lambda policy: {"passed": True, "errors": [], "containers": []},
    )
    monkeypatch.setattr(
        coordination, "_meminfo", lambda: {"MemAvailable": 100, "SwapTotal": 0},
    )
    monkeypatch.setattr(coordination, "_memory_full_psi_avg10", lambda: 0.0)
    result = coordination.evaluate_co_resident_pg_isolation([{
        "pid": 99999999,
        "command": "/disk1/resource_adaptive/run java stmt_postgres.toml",
        "command_sha256": "b" * 64,
        "cpus_allowed_list": "160",
    }])
    assert result["passed"] is False
    assert any("external_cpu_overlaps_mysql" in error for error in result["errors"])


def test_formal_runner_wraps_each_unchanged_candidate_batch() -> None:
    source = (
        ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
    ).read_text(encoding="utf-8")
    assert "candidate_batches = [(rank, (rank,)) for rank in range(1, 6)]" in source
    assert "if args.interference_override_pilot" in source
    assert '"formal_partial_replay_source"' in source
    assert "_measure_batch_with_fair_coordination(" in source
    assert "measured = alg.measure_batch(" in source
    assert 'phase="waiting_for_lease"' in source
    assert 'phase="measuring"' in source
    assert 'phase="released"' in source


def test_single_candidate_uses_xp_reset_target_and_live_non_xp_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers = {
        "job_w1": {"worker_id": "job_w1", "scene": "job_memory"},
        "job_w2": {"worker_id": "job_w2", "scene": "job_memory"},
        "tpch_w1": {"worker_id": "tpch_w1", "scene": "tpch_sf4_memory"},
        "tpch_w2": {"worker_id": "tpch_w2", "scene": "tpch_sf4_memory"},
        "xp_cpu_w1": {"worker_id": "xp_cpu_w1", "scene": "hybench_xp_cpu"},
        "xp_cpu_w2": {"worker_id": "xp_cpu_w2", "scene": "hybench_xp_cpu"},
        "xp_io_w1": {"worker_id": "xp_io_w1", "scene": "hybench_xp_io"},
        "xp_io_w2": {"worker_id": "xp_io_w2", "scene": "hybench_xp_io"},
    }
    monkeypatch.setattr(
        boundary.alg, "mysql_server_liveness",
        lambda worker: worker["worker_id"] != "xp_cpu_w1",
    )
    rankings = {
        scene: [{"optimized_params": {"io_block_read_cost": 0.5}}]
        for scene in boundary.SCENES
    }
    assignments, companions = boundary._build_single_candidate_assignments(
        rankings=rankings,
        params_by_scene={scene: {} for scene in boundary.SCENES},
        workers=workers,
        rank=1,
    )
    assert "xp_cpu_w1" in assignments
    assert "xp_cpu_w2" not in assignments
    assert len(assignments) == 4
    assert companions == set()


def test_formal_authority_remains_fail_closed() -> None:
    source = (
        ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
    ).read_text(encoding="utf-8")
    assert "if args.interference_override_pilot" in source
    assert "freeze, workers, point_gate = alg.r0mod._validate_authority()" in source
    assert "interference_override_pilot=args.interference_override_pilot" in source
    assert '"accepted_state_credit_prohibited": True' in source


def test_coordination_gate_does_not_precreate_measurement_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "outer_005" / "top5_batch_1"
    state_path = tmp_path / "run_state.json"
    state_path.write_text(
        json.dumps({"cross_project_wave_coordination": {}}), encoding="utf-8"
    )

    class Lease:
        inode = 123
        acquired_at = "now"
        wait_seconds = 0.0

    monkeypatch.setattr(
        boundary.coordination, "acquire_exclusive_blocking", lambda **_: Lease()
    )
    monkeypatch.setattr(
        boundary.coordination, "rolling_quiet_gate",
        lambda **_: {"status": "PASS", "errors": []},
    )
    monkeypatch.setattr(boundary.coordination, "release", lambda _: 1.0)
    monkeypatch.setattr(boundary.time, "sleep", lambda _: None)

    def measure(*_args, **_kwargs):
        assert output.exists() is False
        output.mkdir(parents=True, mode=0o700)
        return {"job_w1": {"status": "PASS"}}

    monkeypatch.setattr(boundary.alg, "measure_batch", measure)
    measured, _ = boundary._measure_batch_with_fair_coordination(
        freeze={}, workers={}, assignments={}, output=output,
        reset_token="token", state_path=state_path,
        state={"cross_project_wave_coordination": {}}, stage_prefix="OUTER_005",
        run_id="formal-test", contract={
            "lease_acquisition_timeout_seconds": 1200,
            "per_wave_quiet_seconds": 30,
            "maximum_quiet_wait_while_held_seconds": 180,
            "maximum_idle_load1": 16,
            "fair_release_yield_seconds": 0.5,
        },
    )
    assert measured["job_w1"]["status"] == "PASS"
    gate = output.parent / (
        "top5_batch_1_cross_project_wave_quiet_gate_attempt_001.json"
    )
    assert gate.is_file()
    assert output.is_dir()
