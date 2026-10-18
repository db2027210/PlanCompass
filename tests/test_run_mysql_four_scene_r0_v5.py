from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/run_mysql_four_scene_r0_v5.py"


def test_r0_runs_full_active_workloads_and_pins_every_client() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "run_job_tpch_parallel(" in source
    assert 'duration_s=600' in source
    assert 'clients=8' in source
    assert '"/usr/bin/hwloc-bind", "--physical", "--cpubind"' in source
    assert '"--membind", f"node:{self.worker[\'client_cpuset_mems\']}"' in source
    assert '"--mempolicy", "bind", "--strict"' in source
    assert 'candidate["numa_policy"]' in source
    assert 'f"bind:{self.worker[\'client_cpuset_mems\']}"' in source
    assert 'clients=16' in source
    assert 'tpch_clients=5' in source
    assert 'job_heavy_clients=4' in source
    assert 'scale_factor=4' in source
    assert "deadline = time.monotonic() + 20.0" in source
    assert 'BENCHMARK_ACCOUNTS = (' in source
    assert 'users != ["ra_benchmark"]' in source
    assert '"benchmark_accounts_sha256": _sha(BENCHMARK_ACCOUNTS)' in source
    assert "_preparation_chain_reaches_preflight(prepared, PREPARED)" in source
    assert 'document.get("predecessor_prepared_sha256")' in source
    assert '"hybench_sf10x", user,' in source
    assert '"imdb_job", job_user,' in source
    assert '"tpch", tpch_user,' in source
    assert '"hybench_sf10x", "root"' not in source
    assert 'native_xp_authority.get("execution_authorized") is not True' in source
    assert 'native_xp_authority.get("runtime_jar_sha256") != _sha(NATIVE_XP_JAR)' in source
    assert "_apply_and_readback_default_optimizer_costs" in source
    assert '"baseline_optimizer_cost_readback"' in source
    assert '"applied_before_measurement": True' in source
    assert '"flushed_before_measurement": True' in source
    assert 'RUNTIME_PREFLIGHT' in source
    assert 'active_windows: Mapping[str, tuple[float, float]] | None = None' in source
    assert 'resources = sampler.finish(worker_windows)' in source
    assert 'class ExternalWorkloadWatchdog:' in source
    assert 'coordination.external_process_inventory(' in source
    assert 'runtime_non_interference = interference_watchdog.finish()' in source
    assert 'and runtime_non_interference["passed"]' in source
    assert '"runtime_non_interference_gate": runtime_non_interference' in source
    assert '_workload_freeze_chain_reaches(' in source


def test_r0_is_fail_closed_and_zero_credit_before_outer() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"fresh_r0_complete": passed' in source
    assert '"formal_outer_launch_authorized": passed' in source
    assert '"formal_outer_started": False' in source
    assert '"accepted_rounds": {scene: 0' in source
    assert "bottleneck[\"passed\"]" in source
    assert "client_isolation_failures" in source
