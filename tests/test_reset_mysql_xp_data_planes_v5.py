from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import reset_mysql_xp_data_planes_v5 as reset


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/reset_mysql_xp_data_planes_v5.py"


def test_reset_is_exactly_four_xp_workers_and_preserves_invalid_copy() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}' in source
    assert "os.rename(runtime_root, old_target)" in source
    assert '"--resume-stalled-copy"' in source
    assert '"taskset", "-c", first_cpu_segment, "sudo", "-n", "cp", "-a"' in source
    assert '"--reflink=never"' in source
    assert "IMAGE = prep.IMAGE" in source
    assert "IMAGE_ID = prep.IMAGE_ID" in source
    assert 'local_image.get("Id") != IMAGE_ID' in source
    assert '"staged_physical_copy_reused": copy_reused' in source
    assert '"chmod", "0750"' in source
    assert '"chmod", "0640"' in source
    assert '"quarantined_invalid_runtime_root"' in source
    assert 'invalid.get("fresh_r0_complete") is not False' in source
    assert 'invalid.get("formal_outer_launch_authorized") is not False' in source
    assert 'any((invalid.get("accepted_rounds") or {}).values())' in source


def test_reset_reapplies_fixed_cpu_numa_and_kernel_limits() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"--cpuset-cpus", str(worker["database_cpuset"])' in source
    assert '"--cpuset-mems", str(worker["database_cpuset_mems"])' in source
    assert '"--cpus", str(worker["cpu_quota_cores"])' in source
    assert "prep._kernel_resource_readback(inspect, worker)" in source
    assert "prep._enforce_blkio(inspect, freeze, worker)" in source
    assert '"workload_started": False' in source
    assert '"formal_outer_started": False' in source


def test_recovery_and_live_xp_use_single_start_init_file_and_frozen_io() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"docker", "run", "-d", "--init", "--name", container' in source
    assert 'f"{credential_init}:/run/ra-init/reset.sql:ro"' in source
    assert '"--init-file=/run/ra-init/reset.sql"' in source
    assert '"credential_initialization": "mysqld_init_file"' in source
    assert '"credential_init_file_sha256": credential_init_sha256' in source
    assert '"reset_bootstrap_attempts": []' in source
    assert '"docker", "run", "-d", "--init", "--name", bootstrap' not in source
    assert '"--skip-grant-tables=ON"' not in source
    assert '"--innodb-use-native-aio=OFF", "--innodb-flush-method=fsync"' in source
    assert '"@@global.innodb_use_native_aio,@@global.innodb_flush_method' in source
    assert '"Init": True' in source
    assert '"bootstrap_touched_staging_reused": bootstrap_touched_staging' in source
    assert "**prior,\n        **worker," in source
    assert "force_fresh_copy_on_resume: bool = False" in source
    assert "not force_fresh_copy_on_resume" in source
    assert "after_forced_fresh_copy_{output_root.name}" in source
    assert '["docker", "start", container]' in source
    assert '"container_creation_avoided": container_creation_avoided' in source
    assert '"restart_stopped_container_with_replaced_bind_sources"' in source
    assert '"recovery_after_docker_run_response_timeout": False' in source


def test_single_start_rotates_both_private_accounts_before_readiness() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'benchmark_password = secrets.token_hex(32)' in source
    assert '_write_private(defaults, prep._defaults(password))' in source
    assert '_benchmark_defaults(benchmark_password)' in source
    assert "CREATE USER IF NOT EXISTS 'ra_benchmark'@'%'" not in source
    assert "f\"CREATE USER IF NOT EXISTS '{BENCHMARK_USER}'@'%'" in source
    assert "GRANT ALL PRIVILEGES ON `hybench_sf10x`.*" in source
    assert '["sudo", "-n", "chown", "999:999", str(credential_init)]' in source
    assert '["sudo", "-n", "chmod", "0400", str(credential_init)]' in source


def test_docker_remove_timeout_is_confirmed_from_inventory(monkeypatch) -> None:
    inventory_calls = 0

    def fake_run(argv, *, timeout=600, capture=True):
        nonlocal inventory_calls
        if argv[:3] == ["docker", "ps", "-aq"]:
            inventory_calls += 1
            value = "container-id\n" if inventory_calls == 1 else ""
            return subprocess.CompletedProcess(argv, 0, value, "")
        if argv[:2] == ["docker", "rm"]:
            raise subprocess.TimeoutExpired(argv, timeout)
        raise AssertionError(argv)

    monkeypatch.setattr(reset.prep, "_run", fake_run)

    reset._remove_stopped_container("xp_io_w1", total_timeout_seconds=5)

    assert inventory_calls == 2


def test_docker_remove_non_progress_error_remains_fail_closed(monkeypatch) -> None:
    def fake_run(argv, *, timeout=600, capture=True):
        if argv[:3] == ["docker", "ps", "-aq"]:
            return subprocess.CompletedProcess(argv, 0, "container-id\n", "")
        if argv[:2] == ["docker", "rm"]:
            raise subprocess.CalledProcessError(
                1, argv, output="", stderr="container is running",
            )
        raise AssertionError(argv)

    monkeypatch.setattr(reset.prep, "_run", fake_run)

    try:
        reset._remove_stopped_container("xp_io_w1", total_timeout_seconds=5)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("unexpected running-container removal was accepted")


def test_discard_fast_shutdown_avoids_120_second_docker_stop(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_subprocess_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_prep_run(argv, *, timeout=600, capture=True):
        calls.append(list(argv))
        assert argv[:2] == ["docker", "wait"]
        return subprocess.CompletedProcess(argv, 0, "0\n", "")

    monkeypatch.setattr(reset.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(reset.prep, "_run", fake_prep_run)
    monkeypatch.setattr(
        reset, "_host_mysql",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "2\n", ""),
    )
    inspected = iter((
        {"Id": "container-id", "State": {"Running": True, "Pid": 123, "ExitCode": 0}},
        {"Id": "container-id", "State": {"Running": False, "Pid": 0, "ExitCode": 0}},
    ))
    monkeypatch.setattr(reset, "_docker_json", lambda *_args: next(inspected))
    monkeypatch.setattr(reset, "_container_id", lambda _container: "container-id")
    monkeypatch.setattr(
        reset, "_lift_discard_blkio", lambda *_args: {"status": "LIFTED_FOR_DISCARD_ONLY"},
    )

    row = reset._stop_mysql_container(
        "xp_cpu_w1", worker={"host_port": 65331},
        defaults=Path("/tmp/selected.cnf"),
    )

    assert row["method"] == "discard_fast_shutdown_authenticated"
    assert row["innodb_fast_shutdown_readback"] == 2
    assert row["forced_kill"] is False
    assert row["discarded_datadir_never_reused"] is True
    assert row["verified_stopped"] is True
    assert calls[0][0] == "mysqladmin"
    assert not any(call[:2] == ["docker", "stop"] for call in calls)


def test_discard_shutdown_failure_uses_bounded_audited_sigkill(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_subprocess_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "cannot connect")

    def fake_prep_run(argv, *, timeout=600, capture=True):
        calls.append(list(argv))
        assert argv[:2] in (["docker", "kill"], ["docker", "wait"])
        return subprocess.CompletedProcess(argv, 0, "xp_io_w1\n", "")

    monkeypatch.setattr(reset.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(reset.prep, "_run", fake_prep_run)
    monkeypatch.setattr(
        reset, "_host_mysql",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "2\n", ""),
    )
    monkeypatch.setattr(
        reset, "_container_running", lambda _container: True,
    )
    inspected = iter((
        {"Id": "container-id", "State": {"Running": True, "Pid": 123, "ExitCode": 0}},
        {"Id": "container-id", "State": {"Running": False, "Pid": 0, "ExitCode": 137}},
    ))
    monkeypatch.setattr(reset, "_docker_json", lambda *_args: next(inspected))
    monkeypatch.setattr(reset, "_container_id", lambda _container: "container-id")
    monkeypatch.setattr(
        reset, "_lift_discard_blkio", lambda *_args: {"status": "LIFTED_FOR_DISCARD_ONLY"},
    )

    row = reset._stop_mysql_container(
        "xp_io_w1", worker={"host_port": 65341},
        defaults=Path("/tmp/selected.cnf"),
    )

    assert row["method"] == "discard_fast_shutdown_sigkill_fallback"
    assert row["forced_kill"] is True
    assert row["container_exit_code"] == 137
    assert row["clean_shutdown_error"].startswith("RuntimeError:")
    assert calls == [
        ["docker", "kill", "--signal=KILL", "xp_io_w1"],
        ["docker", "wait", "xp_io_w1"],
    ]


def test_measurement_container_restores_default_fast_shutdown() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert '"SET GLOBAL innodb_fast_shutdown=2; "' in source
    assert '"LIFTED_FOR_DISCARD_ONLY"' in source
    assert '"@@global.innodb_fast_shutdown; "' in source
    assert 'if fast_shutdown != "1":' in source
    assert '"innodb_fast_shutdown": int(fast_shutdown)' in source
