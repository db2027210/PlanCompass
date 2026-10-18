#!/usr/bin/env python3
"""Regression checks for measured workload and JDBC timeout guards."""

from __future__ import annotations

import subprocess
import shlex
import sys
import tempfile
import time
import os
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_pg_benchmark_bottleneck_matrix as runner
import prepare_double_loop_matrix_runtime as runtime_preparer
import system_resource_monitor as monitor


def _fake_vm_snapshot(**_: object) -> dict:
    return {
        "pg_pid_count": 1.0,
        "pg_pss_pid_count": 1.0,
        "pg_io_pid_count": 1.0,
        "pg_cpu_ticks": 100.0,
        "clock_ticks": 100.0,
        "pg_pss_kb": 1024.0,
        "pg_rss_kb": 1024.0,
        "pg_io_read_bytes": 0.0,
        "pg_io_write_bytes": 0.0,
        "io_read_sectors": 0.0,
        "io_write_sectors": 0.0,
        "cpu_total": 100.0,
        "cpu_idle": 90.0,
        "cpu_count": 1.0,
        "mem_total_kb": 1024.0,
        "mem_available_kb": 512.0,
    }


def test_vm_runtime_workload_deadline_terminates_measurement() -> None:
    original = monitor._read_vm_runtime_snapshot
    monitor._read_vm_runtime_snapshot = _fake_vm_snapshot
    started = time.monotonic()
    try:
        try:
            monitor.monitor_resources_from_vm_runtime_workload(
                workload_cmd="sleep 60",
                vm_host="unused",
                interval_s=0.05,
                max_runtime_s=0.25,
            )
        except subprocess.TimeoutExpired as exc:
            assert exc.timeout == 0.25, exc
        else:
            raise AssertionError("expected measured workload timeout")
    finally:
        monitor._read_vm_runtime_snapshot = original
    assert time.monotonic() - started < 5.0


def test_vm_runtime_fixed_horizon_returns_samples() -> None:
    original = monitor._read_vm_runtime_snapshot
    monitor._read_vm_runtime_snapshot = _fake_vm_snapshot
    try:
        resource, stats = monitor.monitor_resources_from_vm_runtime_workload(
            workload_cmd="sleep 60",
            vm_host="unused",
            interval_s=0.05,
            max_runtime_s=0.25,
            fixed_horizon_success=True,
        )
    finally:
        monitor._read_vm_runtime_snapshot = original
    assert resource["memory"] > 0
    assert stats["workload_exit"]["fixed_horizon_reached"] is True
    assert stats["workload_exit"]["sigterm_treated_as_success"] is True


def test_terminate_process_group_cleans_descendant_after_leader_exit() -> None:
    proc = subprocess.Popen(
        ["bash", "-c", "sleep 60 & echo $!"],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    child_pid = int(proc.stdout.readline().strip())
    proc.wait(timeout=2.0)
    try:
        monitor._terminate_process_group(proc, timeout_s=0.2)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            stat_path = Path(f"/proc/{child_pid}/stat")
            if not stat_path.exists():
                break
            state = stat_path.read_text(encoding="utf-8").split()[2]
            if state == "Z":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"descendant process survived cleanup: pid={child_pid}")
    finally:
        try:
            os.kill(child_pid, 9)
        except ProcessLookupError:
            pass


def test_hybench_jdbc_url_contains_statement_timeout() -> None:
    conn = runner.PgConn("127.0.0.1", 5432, "postgres", "pw", "hybench")
    url = conn.jdbc_url(statement_timeout_ms=120000)
    assert "ApplicationName=resource-adaptive-matrix" in url, url
    assert "options=-c%20statement_timeout%3D120000" in url, url
    assert "statement_timeout" not in conn.jdbc_url(), conn.jdbc_url()
    serial_url = conn.jdbc_url(max_parallel_workers_per_gather=0)
    assert "max_parallel_workers_per_gather%3D0" in serial_url, serial_url


def test_benchbase_xml_can_disable_query_parallelism() -> None:
    conn = runner.PgConn("127.0.0.1", 5432, "postgres", "pw", "tpch")
    template = """<?xml version='1.0' encoding='UTF-8'?>
<parameters>
  <url></url><username></username><password></password>
  <type></type><driver></driver><isolation></isolation>
  <scalefactor>1</scalefactor><terminals>1</terminals>
  <works><work><time>1</time><rate>unlimited</rate><serial>true</serial></work></works>
</parameters>
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        template_path = temp_root / "template.xml"
        output_path = temp_root / "tpch.xml"
        template_path.write_text(template, encoding="utf-8")
        runner.update_benchbase_xml(
            template_path=template_path,
            output_path=output_path,
            benchmark="tpch",
            conn=conn,
            time_sec=60,
            terminals=1,
            scalefactor=3.0,
            work_mem_mb=4,
            max_parallel_workers_per_gather=0,
            serial=False,
        )
        output = output_path.read_text(encoding="utf-8")
    assert "work_mem%3D4MB" in output, output
    assert "max_parallel_workers_per_gather%3D0" in output, output


def test_non_memory_hybench_does_not_force_serial_parallel_workers() -> None:
    captured = []
    captured_profiles = []
    original_update = runtime_preparer.update_hybench_props
    original_profile = runtime_preparer.build_hybench_statement_profile

    def fake_update(**kwargs: object) -> None:
        captured.append(kwargs["max_parallel_workers_per_gather"])
        captured_profiles.append(
            (
                kwargs["ap_query_ids"],
                kwargs["ap_query_weights"],
                kwargs["ap_query_sequence"],
            )
        )

    def fake_profile(**kwargs: object) -> Path:
        return Path(str(kwargs["output_path"]))

    runtime_preparer.update_hybench_props = fake_update
    runtime_preparer.build_hybench_statement_profile = fake_profile
    common = {
        "hybench_root": Path("/tmp/fake-hybench-root"),
        "run_mins": 1,
        "tp_clients": 1,
        "ap_clients": 1,
        "random_seed": 1,
        "statement_timeout_ms": 1000,
        "hybench_ap_work_mem_mb": 48,
        "hybench_ap_query_ids": "6",
        "hybench_ap_query_weights": "100",
        "hybench_ap_query_sequence": "6",
        "hybench_tp_percent_mix": "",
        "hybench_ap_max_parallel_workers_per_gather": 0,
    }
    conn = runner.PgConn("127.0.0.1", 5432, "postgres", "pw", "hybench")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                runtime_preparer.benchmark_command(
                    Namespace(benchmark="hybench_xp", target="io", **common),
                    Path(temp_dir),
                    conn,
                )
            except ValueError as exc:
                assert "native HyBench XP forbids AP query" in str(exc)
            else:
                raise AssertionError("XP accepted non-native AP query controls")
            runtime_preparer.benchmark_command(
                Namespace(benchmark="hybench_ap", target="memory", **common),
                Path(temp_dir),
                conn,
            )
    finally:
        runtime_preparer.update_hybench_props = original_update
        runtime_preparer.build_hybench_statement_profile = original_profile
    assert captured == [0], captured
    assert captured_profiles == [("6", "100", "6")], captured_profiles


def test_hybench_tp_percent_mix_is_materialized() -> None:
    conn = runner.PgConn("127.0.0.1", 5432, "postgres", "pw", "hybench")
    mix = "20,15,20,15,0,0,10,10,0,0,10,0,0,0,0,0,0,0"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        args = Namespace(
            benchmark="hybench_tp",
            target="cpu",
            hybench_root=runtime_preparer.HYBENCH_ROOT,
            run_mins=1,
            tp_clients=16,
            ap_clients=0,
            random_seed=1,
            statement_timeout_ms=1000,
            hybench_ap_work_mem_mb=0,
            hybench_ap_query_ids="",
            hybench_ap_query_weights="",
            hybench_ap_query_sequence="",
            hybench_tp_percent_mix=mix,
            hybench_ap_max_parallel_workers_per_gather=-1,
        )
        runtime_preparer.benchmark_command(args, temp_root, conn)
        props = (temp_root / "hybench_tp.props").read_text(encoding="utf-8")
    for index, value in enumerate(mix.split(","), start=1):
        assert f"tp{index}_percent={value}\n" in props


def test_hybench_completed_progress_allows_tail_timeout() -> None:
    stable_counts = {"cpu": 200, "memory": 200, "io": 200}
    assert monitor._is_hybench_sigterm_with_samples(
        returncode=2,
        workload_cmd="bash hybench -t runxp",
        workload_output_lines=["Current 10/10 time AP QPS is 0.05"],
        stable_sample_counts=stable_counts,
    )
    assert not monitor._is_hybench_sigterm_with_samples(
        returncode=2,
        workload_cmd="bash hybench -t runxp",
        workload_output_lines=["Current 9/10 time AP QPS is 0.05"],
        stable_sample_counts=stable_counts,
    )
    assert monitor._is_hybench_sigterm_with_samples(
        returncode=2,
        workload_cmd="bash hybench -t runxp",
        workload_output_lines=[
            "Current 10/10 time AP QPS is 0.05",
            "Caused by: org.postgresql.util.PSQLException: statement timeout",
        ],
        stable_sample_counts=stable_counts,
    )
    assert not monitor._is_hybench_sigterm_with_samples(
        returncode=2,
        workload_cmd="bash hybench -t runap",
        workload_output_lines=[
            'ERROR: could not resize shared memory segment: No space left on device',
            "Current 10/10 time AP QPS is 0.05",
        ],
        stable_sample_counts=stable_counts,
    )


def test_vm_snapshot_skips_unused_expensive_procfs_metrics() -> None:
    original = monitor._run_ssh_command
    captured = {}

    def fake_ssh(**kwargs: object) -> str:
        captured.update(kwargs)
        return "cpu_total=100\ncpu_idle=10\ncpu_count=4\n"

    monitor._run_ssh_command = fake_ssh
    try:
        monitor._read_vm_runtime_snapshot(
            host="unused",
            disk_devices=["vda"],
            cpu_mode="pg_proc",
            io_mode="disk_device",
            memory_mode="pg_proc_rss",
        )
    finally:
        monitor._run_ssh_command = original
    command = str(captured["remote_cmd"])
    assert "collect_proc_cpu=1" in command
    assert "collect_proc_io=0" in command
    assert "collect_proc_memory=1" in command
    assert "collect_proc_pss=0" in command
    assert "ps -C postgres -o rss=" in command
    assert "sed 's/^.*) //'" not in command
    shell_parts = shlex.split(command)
    assert shell_parts[:2] == ["bash", "-lc"]
    syntax = subprocess.run(
        ["bash", "-n"],
        input=shell_parts[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def main() -> None:
    test_vm_runtime_workload_deadline_terminates_measurement()
    test_vm_runtime_fixed_horizon_returns_samples()
    test_terminate_process_group_cleans_descendant_after_leader_exit()
    test_hybench_jdbc_url_contains_statement_timeout()
    test_benchbase_xml_can_disable_query_parallelism()
    test_non_memory_hybench_does_not_force_serial_parallel_workers()
    test_hybench_tp_percent_mix_is_materialized()
    test_hybench_completed_progress_allows_tail_timeout()
    test_vm_snapshot_skips_unused_expensive_procfs_metrics()
    print("PASS measured workload timeout guard tests", flush=True)


if __name__ == "__main__":
    main()
