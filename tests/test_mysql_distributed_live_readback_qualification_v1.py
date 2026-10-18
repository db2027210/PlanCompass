from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = (
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
)
BUILDER_PATH = ROOT / "shell/build_mysql_xp_tpcc_distributed_successors.py"
ADAPTER_PATH = (
    ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py"
)


def module_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAUNCHER = module_from(LAUNCHER_PATH, "qualification_launcher")
BUILDER = module_from(BUILDER_PATH, "distributed_builder_with_launcher")
ADAPTER = module_from(ADAPTER_PATH, "native_workload_metric_adapter")


def write_json_sidecar(path: Path, value: dict) -> None:
    payload = LAUNCHER.canonical(value)
    path.write_bytes(payload)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )


def frozen_client_envelope(cpus: str = "3-11,39-47") -> dict:
    return {
        "execution_mode": "dedicated_read_only_docker_client_cgroup",
        "cpuset_cpus": cpus,
        "cpuset_mems": "0",
        "cpu_quota_cores": 18,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592,
        "pids_max": 1024,
        "network_mode": "host",
        "root_filesystem_read_only": True,
        "linux_capabilities_drop": "ALL",
        "no_new_privileges": True,
        "docker_log_driver": "none",
        "runtime_and_bundle_mounts_read_only": True,
        "measurement_output_medium": "tmpfs",
        "tmpfs_bytes": 2147483648,
        "target_mysql_device_write_prohibited": True,
        "pid_and_all_descendant_affinity_readback_required": True,
    }


def frozen_client_binding(root: Path, name: str) -> dict:
    return {
        "name": name,
        "container_id": "a" * 64,
        "image_id": "sha256:" + ("b" * 64),
        "created": "2026-07-29T00:00:00Z",
        "inspect_config_sha256": "c" * 64,
        "required_read_only_mounts": [
            {"source": str(root), "destination": str(root)}
        ],
    }


def test_launcher_source_parses_with_python36_grammar() -> None:
    ast.parse(
        LAUNCHER_PATH.read_text(encoding="utf-8"),
        filename=str(LAUNCHER_PATH),
        feature_version=(3, 6),
    )
    ast.parse(
        ADAPTER_PATH.read_text(encoding="utf-8"),
        filename=str(ADAPTER_PATH),
        feature_version=(3, 6),
    )


def test_v1_oom_control_uses_failcnt_for_the_actual_kernel_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """v1 memory.oom_control has no oom_kill counter (unlike v2 events)."""
    memory_root = tmp_path / "memory"

    def read_required(path, unused_label):
        if path == memory_root / "memory.oom_control":
            return "oom_kill_disable 0\nunder_oom 0\n"
        if path == memory_root / "memory.failcnt":
            return "17\n"
        raise AssertionError(path)

    monkeypatch.setattr(LAUNCHER, "_read_required", read_required)
    sample = LAUNCHER._v1_memory_oom_sample(memory_root)
    assert sample == {
        "events": 17,
        "source": "cgroup_v1_memory.failcnt_fail_closed_proxy",
        "under_oom": 0,
        "oom_kill_disable": 0,
    }

    def under_oom(path, unused_label):
        if path == memory_root / "memory.oom_control":
            return "oom_kill_disable 0\nunder_oom 1\n"
        if path == memory_root / "memory.failcnt":
            return "17\n"
        raise AssertionError(path)

    monkeypatch.setattr(LAUNCHER, "_read_required", under_oom)
    with pytest.raises(LAUNCHER.QualificationError, match="under OOM"):
        LAUNCHER._v1_memory_oom_sample(memory_root)


def test_no_psi_cpu_fallback_is_versioned_complete_and_psi_is_preferred(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("cpu  10 2 3 40 4 1 2 5 0 0\n", encoding="ascii")
    (proc / "loadavg").write_text("0.50 0.40 0.30 2/100 999\n", encoding="ascii")
    monkeypatch.setattr(LAUNCHER, "PROC_ROOT", proc)
    fallback = LAUNCHER._cpu_contention_sample()
    assert fallback["mode"] == "proc_stat_loadavg_fallback_v1"
    assert fallback["policy_version"] == LAUNCHER.CPU_CONTENTION_POLICY_VERSION
    assert fallback["proc_stat"]["steal_ticks"] == 5
    assert fallback["loadavg"]["runnable_tasks"] == 2

    pressure = proc / "pressure"
    pressure.mkdir()
    (pressure / "cpu").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=12\n", encoding="ascii")
    psi = LAUNCHER._cpu_contention_sample()
    assert psi["mode"] == "psi_cpu_v1"
    assert psi["some_total_usec"] == 12

    (pressure / "cpu").unlink()
    with pytest.raises(LAUNCHER.QualificationError, match="partial"):
        LAUNCHER._cpu_contention_sample()


def test_no_psi_cpu_summary_rejects_counter_regression() -> None:
    def row(total, busy, iowait, steal):
        return {
            "policy_version": LAUNCHER.CPU_CONTENTION_POLICY_VERSION,
            "mode": "proc_stat_loadavg_fallback_v1",
            "psi_preferred_when_available": True,
            "proc_stat": {"total_ticks": total, "busy_ticks": busy,
                          "iowait_ticks": iowait, "steal_ticks": steal},
            "loadavg": {"load_1": 0.1, "load_5": 0.1, "load_15": 0.1,
                        "runnable_tasks": 1, "total_tasks": 10},
        }
    good = [
        {"required_streams": {"cpu_pressure": row(100, 20, 2, 1)}},
        {"required_streams": {"cpu_pressure": row(200, 50, 3, 2)}},
    ]
    summary = LAUNCHER._cpu_contention_summary(good, 1.0)
    assert summary["cpu_pressure_ratio"] == 0.3
    bad = copy.deepcopy(good)
    bad[1]["required_streams"]["cpu_pressure"]["proc_stat"]["steal_ticks"] = 0
    with pytest.raises(LAUNCHER.QualificationError, match="counter regressed"):
        LAUNCHER._cpu_contention_summary(bad, 1.0)


@pytest.mark.parametrize(
    ("kind", "prefix", "native"),
    [
        (
            "hybench_xp",
            "HYBENCH_FORMAL_SAMPLE_JSON=",
            {
                "xap_or_ap_total": 19,
                "xtp_total": 23,
                "measurement_phase": True,
                "latency_p50_ms": 1.25,
                "latency_p95_ms": 4.5,
                "latency_p99_ms": 8.75,
            },
        ),
        (
            "benchbase_tpcc",
            "BENCHBASE_FORMAL_SAMPLE_JSON=",
            {
                "completed_total": 42,
                "measurement_phase": True,
                "latency_p50_ms": 1.25,
                "latency_p95_ms": 4.5,
                "latency_p99_ms": 8.75,
            },
        ),
    ],
)
def test_native_producer_schema_round_trips_through_adapter_and_launcher(
    tmp_path: Path,
    kind: str,
    prefix: str,
    native: dict,
) -> None:
    native["epoch_ms"] = int(time.time() * 1000)
    stdout = tmp_path / "native.stdout"
    stdout.write_text(
        prefix + json.dumps(native, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stream = tmp_path / "metric.jsonl"
    attempt = "attempt-" + ("9" * 32)
    epoch = ADAPTER.emit_fresh_sample(
        kind, stdout, stream, attempt, 0, None, timeout_seconds=0
    )
    parsed = LAUNCHER.workload_metric_sample(
        stream, attempt, None, timeout_seconds=0
    )
    emitted = json.loads(stream.read_text(encoding="utf-8"))
    assert emitted["native_marker_epoch_ms"] == epoch
    assert parsed["completed_total"] == 42
    assert parsed["latency_p50_ms"] == 1.25
    assert parsed["latency_p95_ms"] == 4.5
    assert parsed["latency_p99_ms"] == 8.75


def test_sql_query_native_round_trips_only_with_query_duration_contract(
    tmp_path: Path,
) -> None:
    attempt = "attempt-" + ("s" * 32)
    epoch_ms = int(time.time() * 1000)
    stdout = tmp_path / "native.stdout"
    stdout.write_text(
        "DEBUG_SQL_METRIC_SAMPLE_JSON="
        + json.dumps(
            {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_sample_v1",
                "qualification_attempt_id": attempt,
                "sequence": 0,
                "epoch_seconds": epoch_ms / 1000.0,
                "completed_total": 1,
                "query_duration_ms": 250.0,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    stream = tmp_path / "metric.jsonl"
    ADAPTER.emit_fresh_sample(
        "sql_query_native",
        stdout,
        stream,
        attempt,
        0,
        None,
        timeout_seconds=0,
    )
    parsed = LAUNCHER.workload_metric_sample(
        stream, attempt, None, timeout_seconds=0
    )
    assert parsed["native_adapter_kind"] == "sql_query_native"
    assert parsed["query_duration_ms"] == 250.0
    assert "latency_p95_ms" not in parsed


def test_sql_query_native_accepts_complete_percentile_latency_after_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = "attempt-" + ("s" * 32)
    monkeypatch.setattr(LAUNCHER.time, "time", lambda: 1000.0)
    stream = tmp_path / "metric.jsonl"
    stream.write_text(
        json.dumps(
            {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_sample_v1",
                "qualification_attempt_id": attempt,
                "sequence": 0,
                "epoch_seconds": 1000.0,
                "completed_total": 1,
                "latency_p50_ms": 1.0,
                "latency_p95_ms": 2.0,
                "latency_p99_ms": 3.0,
                "native_adapter_kind": "sql_query_native",
                "native_marker_epoch_ms": 1000000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    parsed = LAUNCHER.workload_metric_sample(
        stream, attempt, None, timeout_seconds=0
    )
    assert parsed["native_adapter_kind"] == "sql_query_native"
    assert parsed["completed_total"] == 1
    assert parsed["latency_p50_ms"] == 1.0
    assert parsed["latency_p95_ms"] == 2.0
    assert parsed["latency_p99_ms"] == 3.0
    assert "query_duration_ms" not in parsed


def test_sql_query_native_still_rejects_partial_percentile_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = "attempt-" + ("s" * 32)
    monkeypatch.setattr(LAUNCHER.time, "time", lambda: 1000.0)
    stream = tmp_path / "metric.jsonl"
    stream.write_text(
        json.dumps(
            {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_sample_v1",
                "qualification_attempt_id": attempt,
                "sequence": 0,
                "epoch_seconds": 1000.0,
                "completed_total": 1,
                "latency_p50_ms": 1.0,
                "latency_p95_ms": 2.0,
                "native_adapter_kind": "sql_query_native",
                "native_marker_epoch_ms": 1000000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        LAUNCHER.QualificationError, match="missing/stale/timed out"
    ):
        LAUNCHER.workload_metric_sample(
            stream, attempt, None, timeout_seconds=0
        )


@pytest.mark.parametrize(
    ("kind", "prefix", "reset_prefix", "reset_counts"),
    [
        (
            "hybench_xp",
            "HYBENCH_FORMAL_SESSION_JSON=",
            "HYBENCH_FORMAL_WARMUP_RESET=",
            {"xap_or_ap_total": 0, "xtp_total": 0},
        ),
        (
            "benchbase_tpcc",
            "BENCHBASE_FORMAL_SESSION_JSON=",
            "BENCHBASE_FORMAL_WARMUP_RESET=",
            {"completed_total": 0},
        ),
    ],
)
def test_native_warmup_transition_is_exact_and_reset(
    tmp_path: Path,
    kind: str,
    prefix: str,
    reset_prefix: str,
    reset_counts: dict,
) -> None:
    started = 1_000_000
    session = {
        "started_epoch_ms": started,
        "measurement_start_epoch_ms": started + 60_000,
        "warmup_seconds": 60,
        "maximum_measurement_seconds": 600,
    }
    reset = {
        "epoch_ms": started + 60_000,
        "warmup_seconds": 60,
        **reset_counts,
    }
    stdout = tmp_path / "native.stdout"
    stdout.write_text(
        prefix + json.dumps(session) + "\n"
        + reset_prefix + json.dumps(reset) + "\n",
        encoding="utf-8",
    )
    assert ADAPTER.validate_measurement_transition(
        kind, stdout
    )["reset_epoch_ms"] == started + 60_000
    reset.update(reset_counts | {next(iter(reset_counts)): 1})
    stdout.write_text(
        prefix + json.dumps(session) + "\n"
        + reset_prefix + json.dumps(reset) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ADAPTER.WorkloadMetricAdapterError, match="not reset"
    ):
        ADAPTER.validate_measurement_transition(kind, stdout)


def test_actual_compiled_producers_expose_latency_and_worker_jj_linkage() -> None:
    hybench = (
        ROOT
        / "qualification_producers/hybench/classes/com/hybench/"
        "HyBenchFormalMain.class"
    )
    benchbase_root = ROOT / "qualification_producers/benchbase/classes"
    formal = (
        benchbase_root / "com/oltpbenchmark/FormalControl.class"
    )
    thread_bench = benchbase_root / "com/oltpbenchmark/ThreadBench.class"
    worker = benchbase_root / "com/oltpbenchmark/api/Worker.class"
    worker_inner = benchbase_root / "com/oltpbenchmark/api/Worker$1.class"
    for path in (hybench, formal, thread_bench, worker, worker_inner):
        assert path.is_file() and path.stat().st_size > 0
    assert all(
        marker in hybench.read_bytes()
        for marker in (
            b"latency_p50_ms",
            b"latency_p95_ms",
            b"latency_p99_ms",
        )
    )
    formal_api = subprocess.run(
        [
            "javap", "-classpath", str(benchbase_root), "-p",
            "com.oltpbenchmark.FormalControl",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    worker_code = subprocess.run(
        [
            "javap", "-classpath", str(benchbase_root), "-c", "-p",
            "com.oltpbenchmark.api.Worker",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    production_jar = (
        ROOT
        / "artifacts/mysql_tpcc_typed_runtime_v1/"
        "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516/"
        "runtime/benchbase-formal.jar"
    )
    db_workload_code = subprocess.run(
        [
            "javap", "-classpath",
            f"{benchbase_root}:{production_jar}", "-c", "-p",
            "com.oltpbenchmark.DBWorkload",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    thread_bench_code = subprocess.run(
        [
            "javap", "-classpath", str(benchbase_root), "-c", "-p",
            "com.oltpbenchmark.ThreadBench",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "completion(long, long)" in formal_api
    assert "FormalControl.completion:(JJ)V" in worker_code
    assert "FormalControl.rightCensored:()V" in worker_code
    assert "FormalControl.initialize:()V" in thread_bench_code
    assert "FormalControl.measurementStarted:()V" in thread_bench_code
    assert "FormalControl.stopDue:()Z" in thread_bench_code
    assert "FormalControl.finish:" in db_workload_code


def test_command_and_main_agent_token_authority_are_exact(tmp_path: Path) -> None:
    executable = tmp_path / "debug_workload"
    executable.write_text("#!/bin/sh\nsleep 180\n", encoding="utf-8")
    executable.chmod(0o755)
    overlay = tmp_path / "classes"
    hybench_class = overlay / "com/hybench/HyBenchFormalMain.class"
    hybench_class.parent.mkdir(parents=True)
    hybench_class.write_bytes(b"hybench-class")
    bundle_id = "a" * 64
    mode = "qualification_solo"
    command_path = tmp_path / "command.json"
    command = {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": bundle_id,
        "mode": mode,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "qualification_attempt_id": "attempt-" + ("1" * 32),
        "workload_metric_stream": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(tmp_path / "metrics.jsonl"),
        },
        "workload_metric_adapter": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "hybench_xp",
        },
        "workload_process_isolation": {
            "cpuset_cpus": "3-11,39-47",
            "cpuset_mems": "0",
            "root_and_descendants_exact": True,
        },
        "client_container": frozen_client_binding(
            tmp_path, "xp-client-attempt"
        ),
        "measurement_output": {
            "host_tmpfs_directory": str(tmp_path),
            "container_tmpfs_destination": "/measurement",
        },
        "argv": [
            str(executable), "-cp", str(overlay),
            "com.hybench.HyBenchFormalMain", "--debug-only"
        ],
        "executable_sha256": LAUNCHER.sha256(executable),
        "code_files": {
            str(executable): LAUNCHER.sha256(executable),
            str(hybench_class): LAUNCHER.sha256(hybench_class),
        },
        "environment": {"QUALIFICATION_ROLE": "solo"},
        "cwd": "/measurement",
    }
    write_json_sidecar(command_path, command)
    assert LAUNCHER.validate_command_spec(
        command_path, bundle_id, mode, frozen_client_envelope()
    ) == command

    token = "main-agent-token-" + ("x" * 32)
    authority_path = tmp_path / "authority.json"
    now = time.time()
    authority = {
        "schema_version":
            "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "authorized_by": "main_agent",
        "bundle_id": bundle_id,
        "mode": mode,
        "qualification_attempt_id": command["qualification_attempt_id"],
        "main_agent_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "command_spec_sha256": LAUNCHER.sha256(command_path),
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "not_before_epoch": now - 1,
        "expires_epoch": now + 300,
    }
    write_json_sidecar(authority_path, authority)
    assert (
        LAUNCHER.validate_main_agent_authority(
            authority_path, token, bundle_id, mode, command_path
        )
        == authority
    )
    with pytest.raises(LAUNCHER.QualificationError):
        LAUNCHER.validate_main_agent_authority(
            authority_path, token + "wrong", bundle_id, mode, command_path
        )


def test_capture_r0_or_formal_tokens_are_rejected_from_debug_command(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "worker"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    overlay = tmp_path / "classes"
    benchbase_classes = [
        overlay / "com/oltpbenchmark/FormalControl.class",
        overlay / "com/oltpbenchmark/ThreadBench.class",
        overlay / "com/oltpbenchmark/api/Worker.class",
        overlay / "com/oltpbenchmark/api/Worker$1.class",
    ]
    for class_file in benchbase_classes:
        class_file.parent.mkdir(parents=True, exist_ok=True)
        class_file.write_bytes(class_file.name.encode())
    command_path = tmp_path / "command.json"
    command = {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": "b" * 64,
        "mode": "qualification_paired",
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "qualification_attempt_id": "attempt-" + ("2" * 32),
        "workload_metric_stream": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(tmp_path / "metrics.jsonl"),
        },
        "workload_metric_adapter": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "benchbase_tpcc",
        },
        "workload_process_isolation": {
            "cpuset_cpus": "8-15,44-51",
            "cpuset_mems": "0",
            "root_and_descendants_exact": True,
        },
        "client_container": frozen_client_binding(
            tmp_path, "tpcc-client-attempt"
        ),
        "measurement_output": {
            "host_tmpfs_directory": str(tmp_path),
            "container_tmpfs_destination": "/measurement",
        },
        "paired_peer_workload": {
            "schema_version":
                "mysql_algorithm_c_paired_peer_workload_binding_v1",
            "scenario": "peer",
            "qualification_attempt_id": "peer-attempt-" + ("3" * 32),
            "workload_metric_stream_path":
                str(tmp_path / "peer-metrics.jsonl"),
        },
        "argv": [
            str(executable), "-cp", str(overlay),
            "com.oltpbenchmark.DBWorkload", "--formal",
        ],
        "executable_sha256": LAUNCHER.sha256(executable),
        "code_files": {
            str(executable): LAUNCHER.sha256(executable),
            **{
                str(class_file): LAUNCHER.sha256(class_file)
                for class_file in benchbase_classes
            },
        },
        "environment": {
            "BENCHBASE_FORMAL_STOP_FILE":
                "/measurement/benchbase-stop-request",
            "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
            "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
        },
        "cwd": "/measurement",
    }
    write_json_sidecar(command_path, command)
    with pytest.raises(
        LAUNCHER.QualificationError, match="capture/R0/formal token"
    ):
        LAUNCHER.validate_command_spec(
            command_path,
            "b" * 64,
            "qualification_paired",
            frozen_client_envelope("8-15,44-51"),
        )
    production_jar = tmp_path / "benchbase.jar"
    production_jar.write_bytes(b"production-jar")
    command["argv"] = [
        str(executable), "-cp", str(overlay),
        "com.oltpbenchmark.DBWorkload", "-jar", str(production_jar),
    ]
    command["code_files"][str(production_jar)] = LAUNCHER.sha256(
        production_jar
    )
    write_json_sidecar(command_path, command)
    with pytest.raises(
        LAUNCHER.QualificationError,
        match="native Java classpath/main launch shape differs",
    ):
        LAUNCHER.validate_command_spec(
            command_path,
            "b" * 64,
            "qualification_paired",
            frozen_client_envelope("8-15,44-51"),
        )


def test_database_counter_sampling_is_complete_and_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mysql = tmp_path / "mysql"
    defaults = tmp_path / "client.cnf"
    mysql.write_text("", encoding="utf-8")
    defaults.write_text("", encoding="utf-8")
    output = "\n".join(
        f"{name.upper()}\t{index}"
        for index, name in enumerate(LAUNCHER.DB_COUNTERS)
    )
    monkeypatch.setattr(LAUNCHER, "run", lambda *args, **kwargs: output)
    result = LAUNCHER.db_counter_sample(mysql, defaults, 65106)
    assert set(result) == set(LAUNCHER.DB_COUNTERS)

    monkeypatch.setattr(
        LAUNCHER,
        "run",
        lambda *args, **kwargs: "QUESTIONS\t1\n",
    )
    with pytest.raises(
        LAUNCHER.QualificationError, match="coverage incomplete"
    ):
        LAUNCHER.db_counter_sample(mysql, defaults, 65106)


def test_database_counters_use_mysql8_show_global_status_not_filtered_pfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mysql = tmp_path / "mysql"
    defaults = tmp_path / "client.cnf"
    mysql.write_text("", encoding="utf-8")
    defaults.write_text("", encoding="utf-8")
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        return "\n".join("%s\t7" % name for name in LAUNCHER.DB_COUNTERS)

    monkeypatch.setattr(LAUNCHER, "run", fake_run)
    assert set(LAUNCHER.db_counter_sample(mysql, defaults, 65106)) == set(LAUNCHER.DB_COUNTERS)
    sql = observed["argv"][observed["argv"].index("--execute") + 1]
    assert sql == "SHOW GLOBAL STATUS"
    assert "performance_schema.global_status" not in sql
    assert "--raw" in observed["argv"]


def test_database_counters_ignore_mysql8_nonrequired_text_but_reject_duplicate_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mysql, defaults = tmp_path / "mysql", tmp_path / "client.cnf"
    mysql.write_text("", encoding="utf-8")
    defaults.write_text("", encoding="utf-8")
    full_stream = ["Caching_sha2_password_rsa_public_key\t-----BEGIN PUBLIC KEY-----"]
    full_stream.extend("%s\t3" % name for name in LAUNCHER.DB_COUNTERS)
    monkeypatch.setattr(LAUNCHER, "run", lambda *args, **kwargs: "\n".join(full_stream))
    assert set(LAUNCHER.db_counter_sample(mysql, defaults, 65106)) == set(LAUNCHER.DB_COUNTERS)
    full_stream.append("Com_select\t4")
    with pytest.raises(LAUNCHER.QualificationError, match="duplicate database counter"):
        LAUNCHER.db_counter_sample(mysql, defaults, 65106)


def client_container_fixture(tmp_path: Path) -> tuple[dict, dict, dict]:
    envelope = frozen_client_envelope()
    row = {
        "Id": "a" * 64,
        "Name": "/xp-client-attempt",
        "Image": "sha256:" + ("b" * 64),
        "Created": "2026-07-29T00:00:00Z",
        "State": {"Running": True, "Pid": 4321},
        "Config": {"Image": "client@sha256:" + ("d" * 64)},
        "HostConfig": {
            "CpuQuota": 1800000,
            "CpuPeriod": 100000,
            "CpusetCpus": "3-11,39-47",
            "CpusetMems": "0",
            "Memory": 8589934592,
            "MemorySwap": 8589934592,
            "PidsLimit": 1024,
            "NetworkMode": "host",
            "ReadonlyRootfs": True,
            "LogConfig": {"Type": "none", "Config": {}},
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/measurement": "rw,size=2147483648"},
        },
        "Mounts": [{
            "Type": "bind",
            "Source": str(tmp_path),
            "Destination": "/sealed",
            "RW": False,
        }],
    }
    binding = {
        "name": "xp-client-attempt",
        "container_id": row["Id"],
        "image_id": row["Image"],
        "created": row["Created"],
        "required_read_only_mounts": [{
            "source": str(tmp_path),
            "destination": "/sealed",
        }],
    }
    binding["inspect_config_sha256"] = (
        LAUNCHER._client_container_config_identity(row)
    )
    return envelope, row, binding


def test_client_container_exact_readback_and_identity_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    envelope, row, binding = client_container_fixture(tmp_path)
    monkeypatch.setattr(
        LAUNCHER,
        "process_status",
        lambda pid: {
            "Cpus_allowed_list": envelope["cpuset_cpus"],
            "Mems_allowed_list": envelope["cpuset_mems"],
        },
    )
    monkeypatch.setattr(LAUNCHER, "process_start_ticks", lambda pid: 123456)
    result = LAUNCHER.validate_client_container_readback(
        row, envelope, binding, "/measurement"
    )
    assert result["container_id"] == row["Id"]
    docker_managed = copy.deepcopy(row)
    docker_managed["Mounts"].extend(
        {
            "Type": "bind",
            "Source": "/var/lib/docker/containers/%s/%s"
            % (docker_managed["Id"], filename),
            "Destination": destination,
            "RW": True,
        }
        for destination, filename in (
            ("/etc/hosts", "hosts"),
            ("/etc/hostname", "hostname"),
            ("/etc/resolv.conf", "resolv.conf"),
        )
    )
    docker_managed_binding = copy.deepcopy(binding)
    docker_managed_binding["inspect_config_sha256"] = (
        LAUNCHER._client_container_config_identity(docker_managed)
    )
    assert LAUNCHER.validate_client_container_readback(
        docker_managed, envelope, docker_managed_binding, "/measurement"
    )["container_id"] == row["Id"]
    forged_managed_target = copy.deepcopy(docker_managed)
    forged_managed_target["Mounts"][-1]["Source"] = "/tmp/resolv.conf"
    forged_binding = copy.deepcopy(binding)
    forged_binding["inspect_config_sha256"] = (
        LAUNCHER._client_container_config_identity(forged_managed_target)
    )
    with pytest.raises(LAUNCHER.QualificationError, match="mutable mount"):
        LAUNCHER.validate_client_container_readback(
            forged_managed_target, envelope, forged_binding, "/measurement"
        )
    for key, value in (
        ("Id", "e" * 64),
        ("Image", "sha256:" + ("f" * 64)),
        ("Created", "2026-07-29T00:00:01Z"),
    ):
        changed = copy.deepcopy(row)
        changed[key] = value
        with pytest.raises(LAUNCHER.QualificationError):
            LAUNCHER.validate_client_container_readback(
                changed, envelope, binding, "/measurement"
            )
    for mutate in (
        lambda value: value["HostConfig"].__setitem__(
            "Memory", envelope["memory_bytes"] - 1
        ),
        lambda value: value["HostConfig"].__setitem__(
            "ReadonlyRootfs", False
        ),
        lambda value: value["HostConfig"].__setitem__(
            "Tmpfs", {"/wrong": "rw,size=2147483648"}
        ),
        lambda value: value["Mounts"][0].__setitem__("RW", True),
        lambda value: value.__setitem__("Mounts", []),
    ):
        changed = copy.deepcopy(row)
        mutate(changed)
        changed_binding = copy.deepcopy(binding)
        changed_binding["inspect_config_sha256"] = (
            LAUNCHER._client_container_config_identity(changed)
        )
        with pytest.raises(LAUNCHER.QualificationError):
            LAUNCHER.validate_client_container_readback(
                changed, envelope, changed_binding, "/measurement"
            )


def test_client_immutable_identity_excludes_execids_but_binds_fixed_fields(
    tmp_path: Path,
) -> None:
    _envelope, row, _binding = client_container_fixture(tmp_path)
    baseline = LAUNCHER._client_container_config_identity(row)
    transient = copy.deepcopy(row)
    transient["State"]["ExecIDs"] = ["exec-ephemeral"]
    transient["State"]["Health"] = {"Status": "healthy"}
    assert LAUNCHER._client_container_config_identity(transient) == baseline
    changed_quota = copy.deepcopy(row)
    changed_quota["HostConfig"]["CpuQuota"] += 1
    assert LAUNCHER._client_container_config_identity(changed_quota) != baseline
    changed_mount = copy.deepcopy(row)
    changed_mount["Mounts"][0]["Source"] = "/unexpected"
    assert LAUNCHER._client_container_config_identity(changed_mount) != baseline


def test_host_measurement_output_must_be_exact_sized_tmpfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        LAUNCHER,
        "run",
        lambda *args, **kwargs:
            "tmpfs %s rw,size=2147483648" % tmp_path,
    )
    assert LAUNCHER.validate_host_tmpfs_output(
        tmp_path, tmp_path, 2147483648
    )["filesystem"] == "tmpfs"
    monkeypatch.setattr(
        LAUNCHER,
        "run",
        lambda *args, **kwargs:
            "/dev/nvme3n1 %s rw" % tmp_path,
    )
    with pytest.raises(
        LAUNCHER.QualificationError, match="not tmpfs"
    ):
        LAUNCHER.validate_host_tmpfs_output(
            tmp_path, tmp_path, 2147483648
        )


def test_target_device_fd_attribution_reads_real_fd_and_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-device-file"
    target.write_bytes(b"x")
    proc = tmp_path / "proc"
    (proc / "77/fd").mkdir(parents=True)
    (proc / "77/fdinfo").mkdir()
    (proc / "77/fd/3").symlink_to(target)
    (proc / "77/fdinfo/3").write_text(
        "pos:\t0\nflags:\t0100001\n", encoding="ascii"
    )
    device = target.stat().st_dev
    monkeypatch.setattr(LAUNCHER, "PROC_ROOT", proc)
    assert LAUNCHER._process_target_device_write_fds(
        77, f"{os.major(device)}:{os.minor(device)}"
    ) == (1, 1)


def _write_fake_process(
    proc: Path,
    pid: int,
    ppid: int,
    comm: str,
    command: str,
    cgroup: str,
    cpus: str,
    mems: str,
) -> None:
    directory = proc / str(pid)
    (directory / "fd").mkdir(parents=True)
    (directory / "fdinfo").mkdir()
    (directory / "status").write_text(
        "\n".join([
            f"Name:\t{comm}",
            f"Pid:\t{pid}",
            f"PPid:\t{ppid}",
            f"Cpus_allowed_list:\t{cpus}",
            f"Mems_allowed_list:\t{mems}",
        ]) + "\n",
        encoding="utf-8",
    )
    trailing = ["S", str(ppid)] + (["0"] * 17) + [str(pid * 100)]
    (directory / "stat").write_text(
        f"{pid} ({comm}) " + " ".join(trailing) + "\n",
        encoding="ascii",
    )
    (directory / "cmdline").write_bytes(
        command.replace(" ", "\0").encode() + b"\0"
    )
    (directory / "cgroup").write_text(
        f"0::/{cgroup}\n", encoding="utf-8"
    )
    (directory / "io").write_text(
        "read_bytes: 0\nwrite_bytes: 0\n", encoding="ascii"
    )


def test_rolling_inventory_closes_container_and_generic_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "proc"
    proc.mkdir()
    target_id = "a" * 64
    client_id = "b" * 64
    _write_fake_process(
        proc, 100, 1, "container init", "init",
        f"docker/{target_id}", "21-24,57-60", "1"
    )
    _write_fake_process(
        proc, 101, 100, "my sql child", "mysqld",
        f"docker/{target_id}", "21-24,57-60", "1"
    )
    _write_fake_process(
        proc, 200, 1, "client init", "init",
        f"docker/{client_id}", "8-15,44-51", "0"
    )
    _write_fake_process(
        proc, 201, 200, "generic child", "java",
        f"docker/{client_id}", "8-15,44-51", "0"
    )
    _write_fake_process(
        proc, 300, 1, "postgres", "postgres",
        "docker/" + ("c" * 64), "16-17,52-53", "1"
    )
    _write_fake_process(
        proc, 400, 1, "collector", "collector",
        "observer", "8-15,44-51", "0"
    )
    monkeypatch.setattr(LAUNCHER, "PROC_ROOT", proc)
    monkeypatch.setattr(LAUNCHER.os, "getpid", lambda: 400)
    monkeypatch.setattr(
        LAUNCHER,
        "_process_target_device_write_fds",
        lambda pid, device: (0, 0),
    )
    result = LAUNCHER.rolling_process_inventory(
        100,
        200,
        400,
        "259:2",
        "21-24,57-60",
        "8-15,44-51",
        "0",
    )
    assert result["inventory_complete"] is True
    assert result["conflicts"] == []
    roles = {row["pid"]: row["role"] for row in result["processes"]}
    assert roles[101] == "target_mysql"
    assert roles[201] == "qualification_workload"
    assert roles[300] == "co_resident"
    assert roles[400] == "qualification_control_plane"

    monkeypatch.setattr(
        LAUNCHER,
        "_process_target_device_write_fds",
        lambda pid, device: (1, 1) if pid == 300 else (0, 0),
    )
    conflict = LAUNCHER.rolling_process_inventory(
        100, 200, 400, "259:2",
        "21-24,57-60", "8-15,44-51", "0",
    )
    assert conflict["foreign_writer_count"] == 1
    assert "co_resident_cpu_or_device" in conflict["conflicts"]

    def permission_failure(pid: int, device: str) -> tuple[int, int]:
        if pid == 300:
            raise LAUNCHER.QualificationError("permission denied")
        return (0, 0)

    monkeypatch.setattr(
        LAUNCHER,
        "_process_target_device_write_fds",
        permission_failure,
    )
    incomplete = LAUNCHER.rolling_process_inventory(
        100, 200, 400, "259:2",
        "21-24,57-60", "8-15,44-51", "0",
    )
    assert incomplete["inventory_complete"] is False
    assert incomplete["permission_failure_count"] == 1


def test_rolling_inventory_uses_exact_docker_fallback_for_root_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "proc"
    proc.mkdir()
    target_id = "a" * 64
    client_id = "b" * 64
    pg_id = "c" * 64
    _write_fake_process(
        proc, 100, 1, "container init", "init",
        f"docker/{target_id}", "21-24,57-60", "1"
    )
    _write_fake_process(
        proc, 101, 100, "mysqld", "mysqld",
        f"docker/{target_id}", "21-24,57-60", "1"
    )
    _write_fake_process(
        proc, 200, 1, "client init", "init",
        f"docker/{client_id}", "8-15,44-51", "0"
    )
    _write_fake_process(
        proc, 201, 200, "java", "java",
        f"docker/{client_id}", "8-15,44-51", "0"
    )
    _write_fake_process(
        proc, 300, 1, "postgres", "postgres",
        f"docker/{pg_id}", "16-17,52-53", "0"
    )
    _write_fake_process(
        proc, 400, 1, "collector", "collector",
        "observer", "0-71", "0-1"
    )
    monkeypatch.setattr(LAUNCHER, "PROC_ROOT", proc)
    monkeypatch.setattr(LAUNCHER.os, "getpid", lambda: 400)

    def fd_readback(pid: int, device: str) -> tuple[int, int]:
        if pid == 300:
            raise LAUNCHER.QualificationError("root-owned /proc fd denied")
        return (0, 0)

    monkeypatch.setattr(
        LAUNCHER, "_process_target_device_write_fds", fd_readback
    )

    def docker_row(
        container_id: str, pid: int, name: str, mounts: list[dict],
    ) -> dict:
        return {
            "container_id": container_id,
            "name": name,
            "init_pid": pid,
            "mounts": mounts,
            "mounts_sha256": LAUNCHER.sha_bytes(
                LAUNCHER.canonical(mounts)
            ),
            "cgroup_version": 1,
            "cgroup_devices": {
                "259:2": {
                    "read_bytes": 0,
                    "write_bytes": 0,
                    "read_ios": 0,
                    "write_ios": 0,
                }
            },
        }

    docker = {
        target_id: docker_row(target_id, 100, "mysql", []),
        client_id: docker_row(client_id, 200, "client", []),
        pg_id: docker_row(
            pg_id,
            300,
            "postgres",
            [
                {
                    "source": "/mnt/pg",
                    "destination": "/var/lib/postgresql/data",
                    "major_minor": "259:6",
                    "rw": True,
                }
            ],
        ),
    }
    result = LAUNCHER.rolling_process_inventory(
        100,
        200,
        400,
        "259:2",
        "21-24,57-60",
        "8-15,44-51",
        "0",
        docker_containers=docker,
    )
    assert result["inventory_complete"] is True
    assert result["conflicts"] == []
    pg = next(row for row in result["processes"] if row["pid"] == 300)
    assert pg["fd_attribution"] == "docker_mount_and_cgroup_fallback"
    assert pg["container_mounts_sha256"] == docker[pg_id]["mounts_sha256"]

    docker[pg_id] = docker_row(
        pg_id,
        300,
        "postgres",
        [
            {
                "source": "/mnt/mysql-target",
                "destination": "/var/lib/postgresql/data",
                "major_minor": "259:2",
                "rw": True,
            }
        ],
    )
    conflict = LAUNCHER.rolling_process_inventory(
        100,
        200,
        400,
        "259:2",
        "21-24,57-60",
        "8-15,44-51",
        "0",
        docker_containers=docker,
    )
    assert conflict["foreign_writer_count"] == 1
    assert "co_resident_cpu_or_device" in conflict["conflicts"]


def test_paired_readback_requires_cpu_device_and_port_disjoint(
    tmp_path: Path,
) -> None:
    own = {
        "hostname": "node04",
        "resource_envelope": {"cpuset_cpus": "21-24,57-60"},
        "storage": {"major_minor": "259:2"},
        "mysql_port": 65106,
    }
    peer_path = tmp_path / "peer.json"
    peer = {
        "schema_version": "mysql_algorithm_c_distributed_live_readback_v1",
        "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
        "hostname": "node04",
        "resource_envelope": {"cpuset_cpus": "18-20,54-56"},
        "storage": {"major_minor": "259:1"},
        "mysql_port": 65103,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
        "container": {"pid": 123},
    }
    write_json_sidecar(peer_path, peer)
    assert LAUNCHER.validate_peer_readback(peer_path, own) == peer
    peer["resource_envelope"]["cpuset_cpus"] = "20-24,56-60"
    write_json_sidecar(peer_path, peer)
    with pytest.raises(LAUNCHER.QualificationError, match="CPU sets overlap"):
        LAUNCHER.validate_peer_readback(peer_path, own)


def test_collect_only_document_is_readback_not_launch_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / ("c" * 64)
    (bundle / "toolchain").mkdir(parents=True)
    (bundle / "bundle_manifest.json").write_text("{}", encoding="utf-8")
    (bundle / "toolchain/tree.json").write_text("{}", encoding="utf-8")
    python_root = tmp_path / "python-root"
    (python_root / "bin").mkdir(parents=True)
    python_binary = python_root / "bin/python3.11"
    python_binary.write_bytes(b"python")
    python_sha = LAUNCHER.sha256(python_binary)
    (bundle / "toolchain/python-tree.json").write_text(
        json.dumps(
            {
                "schema_version":
                    "mysql_algorithm_c_runtime_tree_manifest_v3",
                "artifacts": {"bin/python3.11": python_sha},
            }
        ),
        encoding="utf-8",
    )
    runtime = {
        "scenario": "tpcc_cpu",
        "host": {"hostname": "node04"},
        "storage": {
            "device": "/dev/nvme3n1",
            "mount": "/mnt/mysql_tpcc_nvme3n1",
            "expected_live_major_minor": "259:2",
        },
            "resource_envelope": {
            "mysql_port": 65106,
                "cpuset_cpus": "21-24,57-60",
            },
            "client_resource_envelope": frozen_client_envelope(
                "8-15,44-51"
            ),
        "toolchain": {
            "java_required": True,
            "python_required": True,
            "tree_manifest": "toolchain/tree.json",
            "deployment_target": "/runtime/jdk23",
            "tree_content_sha256": "jdk-tree-id",
            "tree_content_identity_algorithm": "portable-v2",
            "java_binary_relative": "bin/java",
            "java_binary_sha256": "java-sha",
            "release_sha256": "release-sha",
            "python_runtime_root": str(python_root),
            "python_runtime": str(python_binary),
            "python_binary_relative": "bin/python3.11",
            "python_tree_manifest": "toolchain/python-tree.json",
            "python_tree_content_sha256": "python-tree-id",
            "python_tree_content_identity_algorithm": "portable-v2",
            "python_binary_sha256": python_sha,
            "python_version_expected": "3.11.11",
        },
    }
    monkeypatch.setattr(
        LAUNCHER,
        "verify_bundle",
        lambda path: (
            {"bundle_id": bundle.name},
            runtime,
        ),
    )
    monkeypatch.setattr(LAUNCHER.socket, "gethostname", lambda: "node04")
    monkeypatch.setattr(
        LAUNCHER, "resolve_device", lambda path: "259:2"
    )
    monkeypatch.setattr(
        LAUNCHER,
        "mount_readback",
        lambda path: {
            "source": "/dev/nvme3n1",
            "filesystem": "ext4",
            "target": "/mnt/mysql_tpcc_nvme3n1",
        },
    )
    monkeypatch.setattr(LAUNCHER, "inspect_container", lambda name: {})
    monkeypatch.setattr(
        LAUNCHER,
        "validate_container_readback",
        lambda row, contract: {"pid": 123},
    )
    monkeypatch.setattr(LAUNCHER, "port_probe", lambda port: None)
    monkeypatch.setattr(
        LAUNCHER,
        "db_counter_sample",
        lambda *args: {name: 1 for name in LAUNCHER.DB_COUNTERS},
    )
    tree_roots = []

    def fake_tree_readback(root: Path, manifest: dict) -> dict:
        tree_roots.append(root)
        return {
            "root": str(root),
            "tree_content_sha256": (
                "python-tree-id"
                if root == python_root
                else "jdk-tree-id"
            ),
            "algorithm": "portable-v2",
        }

    monkeypatch.setattr(LAUNCHER, "tree_readback", fake_tree_readback)
    original_sha256 = LAUNCHER.sha256

    def fake_sha256(path: Path) -> str:
        known = {
            str(python_binary): python_sha,
            "/runtime/jdk23/bin/java": "java-sha",
            "/runtime/jdk23/release": "release-sha",
            str(bundle / "bundle_manifest.json"):
                hashlib.sha256(b"{}").hexdigest(),
        }
        if str(path) in known:
            return known[str(path)]
        return original_sha256(path)

    monkeypatch.setattr(LAUNCHER, "sha256", fake_sha256)
    monkeypatch.setattr(
        LAUNCHER, "run", lambda *args, **kwargs: "Python 3.11.11"
    )
    readback = LAUNCHER.collect_readback(
        bundle,
        "tpcc",
        tmp_path / "mysql",
        tmp_path / "client.cnf",
    )
    assert readback["status"] == "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
    assert readback["storage"]["major_minor"] == "259:2"
    assert (
        readback["toolchain"]["python"]["tree_content_sha256"]
        == "python-tree-id"
    )
    assert readback["toolchain"]["python"]["python_binary_sha256"] == python_sha
    assert tree_roots == [Path("/runtime/jdk23"), python_root]
    assert readback["capture_authorized"] is False
    assert readback["formal_r0_authorized"] is False
    assert readback["formal_launch_authorized"] is False
    assert readback["accepted_round_authorized"] is False


def test_database_counters_use_exact_client_docker_exec_stdout_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = tmp_path / "mysql.cnf"
    defaults.write_text("[client]\n", encoding="utf-8")
    client = {
        "Id": "a" * 64,
        "Name": "/ra_mysql_job_v2_test_client",
        "State": {"Running": True, "Pid": 321},
        "Config": {},
        "HostConfig": {},
        "Mounts": [],
        "Image": "sha256:" + ("b" * 64),
        "Created": "2026-07-31T00:00:00Z",
    }
    transport = {
        "client_container": "ra_mysql_job_v2_test_client",
        "client_container_name": "ra_mysql_job_v2_test_client",
        "client_container_id": client["Id"],
        "client_container_short_id": client["Id"][:12],
        "client_pid": 321,
        "client_process_start_ticks": 123456,
        "client_inspect_config_sha256":
            LAUNCHER._client_container_config_identity(client),
        "mysql_binary_in_client": "/usr/bin/mysql",
    }
    monkeypatch.setattr(
        LAUNCHER, "inspect_container", lambda name: client
    )
    monkeypatch.setattr(LAUNCHER, "process_start_ticks", lambda pid: 123456)
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        return "\n".join(
            "%s\t1" % name for name in LAUNCHER.DB_COUNTERS
        )

    monkeypatch.setattr(LAUNCHER, "run", fake_run)
    counters = LAUNCHER.db_counter_sample(
        tmp_path / "host-mysql-is-absent",
        defaults,
        65102,
        transport,
    )
    assert set(counters) == set(LAUNCHER.DB_COUNTERS)
    assert observed["argv"][:4] == [
        "docker",
        "exec",
        client["Id"],
        "/usr/bin/mysql",
    ]
    monkeypatch.setattr(LAUNCHER, "process_start_ticks", lambda pid: 123457)
    with pytest.raises(
        LAUNCHER.QualificationError, match="expected_start_ticks=CHANGE_ME"
    ):
        LAUNCHER.db_counter_sample(
            tmp_path / "host-mysql-is-absent",
            defaults,
            65102,
            transport,
        )
    assert str(tmp_path / "host-mysql-is-absent") not in observed["argv"]
    changed = dict(client)
    changed["Id"] = "c" * 64
    monkeypatch.setattr(
        LAUNCHER, "inspect_container", lambda name: changed
    )
    with pytest.raises(
        LAUNCHER.QualificationError, match="identity changed"
    ):
        LAUNCHER.db_counter_sample(
            tmp_path / "host-mysql-is-absent",
            defaults,
            65102,
            transport,
        )


def test_qualification_duration_cannot_be_shortened() -> None:
    with pytest.raises(LAUNCHER.QualificationError, match="exactly 180s"):
        LAUNCHER.run_qualification(
            Path("/not-used"),
            "qualification_solo",
            "not-used",
            Path("/not-used"),
            Path("/not-used"),
            Path("/not-used"),
            Path("/not-used"),
            Path("/not-used"),
            "x" * 32,
            duration_seconds=1,
        )


def test_runtime_tree_identity_is_path_independent_and_rejects_extras(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "relocated-jdk"
    (tree / "bin").mkdir(parents=True)
    (tree / "lib").mkdir()
    (tree / "bin/java").write_bytes(b"java")
    (tree / "release").write_bytes(b"release")
    (tree / "lib/java-link").symlink_to("../bin/java")
    entries = LAUNCHER.runtime_tree.inventory_tree(tree)
    manifest = LAUNCHER.runtime_tree.portable_manifest(
        entries, {"test_source": "/not/in/identity"}
    )
    readback = LAUNCHER.tree_readback(tree, manifest)
    assert (
        readback["tree_content_sha256"]
        == manifest["tree_content_sha256"]
    )

    (tree / "unexpected").write_text("extra", encoding="utf-8")
    with pytest.raises(
        LAUNCHER.QualificationError, match="full tree inventory"
    ):
        LAUNCHER.tree_readback(tree, manifest)


def test_launchers_are_sealed_in_new_successors() -> None:
    result = BUILDER.build_all()
    assert result["remote_actions"] == 0
    for key in ("xp_bundle", "tpcc_bundle"):
        bundle = Path(result[key])
        manifest = BUILDER.verify_successor(bundle)
        runtime = json.loads(
            (bundle / "distributed_runtime_contract.json").read_text(
                encoding="utf-8"
            )
        )
        relative = (
            "qualification/"
            "mysql_distributed_live_readback_qualification_v1.py"
        )
        assert manifest["files"][relative] == BUILDER.sha256(bundle / relative)
        helper = "qualification/runtime_tree_content_identity_v3.py"
        assert manifest["files"][helper] == BUILDER.sha256(bundle / helper)
        assert (
            runtime["live_readback"]["collector_and_qualification_entrypoint"]
            == relative
        )
        assert runtime["live_readback"]["collect_only_is_read_only"] is True
        assert (
            runtime["live_readback"][
                "qualification_main_agent_token_and_sidecars_required"
            ]
            is True
        )


def synthetic_dynamic_samples(
    *,
    completion_rate: int = 100,
    latency_ms: float = 10.0,
    cpu_ratio: float = 0.40,
    throttle_ratio: float = 0.01,
    foreign_bytes_per_second: int = 0,
    oom_kills: int = 0,
    swap_step_bytes: int = 0,
) -> list[dict]:
    rows = []
    quota = 4.0
    declared = "259:2"
    for index in range(181):
        devices = {
            declared: {
                "read_bytes": index * 4096,
                "write_bytes": index * 8192,
                "read_ios": index * 2,
                "write_ios": index * 4,
            }
        }
        if foreign_bytes_per_second:
            devices["259:99"] = {
                "read_bytes": index * foreign_bytes_per_second,
                "write_bytes": 0,
                "read_ios": index,
                "write_ios": 0,
            }
        streams = {
            "workload_throughput_or_completion_rate": {
                "sequence": index,
                "epoch_seconds": 1000.0 + index,
                "completed_total": index * completion_rate,
            },
            "p50_p95_p99_or_query_duration": {
                "latency_p50_ms": latency_ms,
                "latency_p95_ms": latency_ms * 1.5,
                "latency_p99_ms": latency_ms * 2.0,
            },
            "cpu_usage_and_throttle_ratio": {
                "usage_usec": int(index * 1_000_000 * quota * cpu_ratio),
                "throttled_usec": int(index * 1_000_000 * throttle_ratio),
                "nr_throttled": index,
                "cpu_quota_cores": quota,
            },
            "cpu_pressure": {
                "source": "/proc/pressure/cpu",
                "some_total_usec": index * 100_000,
                "full_total_usec": None,
                "policy_version": LAUNCHER.CPU_CONTENTION_POLICY_VERSION,
                "mode": "psi_cpu_v1",
                "psi_preferred_when_available": True,
            },
            "memory_current_swap_and_pressure": {
                "memory_current_bytes": 4_000_000_000,
                "swap_current_bytes": index * swap_step_bytes,
                "memory_limit_bytes": 8_000_000_000,
                "memory_swap_limit_bytes": 12_000_000_000,
                "oom_kill_events": oom_kills if index == 180 else 0,
                "pressure": {
                    "source": "/proc/pressure/memory",
                    "some_total_usec": index * 20_000,
                    "full_total_usec": index * 10_000,
                    "policy_version": LAUNCHER.CPU_CONTENTION_POLICY_VERSION,
                    "mode": "psi_memory_v1",
                },
            },
            "per_declared_device_bytes_iops_latency_queue_and_utilization": {
                "declared_major_minor": declared,
                "cgroup_devices": devices,
                "block_device": {
                    "read_ios": index * 2,
                    "read_bytes": index * 4096,
                    "read_ticks_ms": index * 2,
                    "write_ios": index * 4,
                    "write_bytes": index * 8192,
                    "write_ticks_ms": index * 4,
                    "in_flight": 1,
                    "io_ticks_ms": index * 100,
                    "weighted_io_ticks_ms": index * 120,
                },
            },
            "database_counters": {
                name: (2 if name == "Threads_running" else index * 10)
                for name in LAUNCHER.DB_COUNTERS
            },
            "process_cpu_affinity_and_device_inventory": {
                "pid": 123,
                "cpus_allowed_list": "21-24,57-60",
                "mems_allowed_list": "1",
                "expected_cpuset_cpus": "21-24,57-60",
                "expected_cpuset_mems": "1",
                "affinity_exact": True,
                "declared_major_minor": declared,
                "observed_cgroup_devices": sorted(devices),
                "cgroup_version": 1,
                "client_cgroup_devices": {
                    declared: {
                        "read_bytes": 0,
                        "write_bytes": 0,
                        "read_ios": 0,
                        "write_ios": 0,
                    }
                },
                "rolling_full_host_inventory": {
                    "inventory_complete": True,
                    "conflicts": [],
                    "permission_failure_count": 0,
                    "race_count": 0,
                    "foreign_writer_count": 0,
                    "target_cgroup_paths_sha256": "target-cgroup",
                    "workload_cgroup_paths_sha256": "client-cgroup",
                    "docker_inventory_sha256": "docker-inventory",
                    "processes": [
                        {
                            "pid": 123,
                            "ppid": 1,
                            "starttime_ticks": 100,
                            "comm": "mysqld",
                            "cmdline_sha256": "a" * 64,
                            "cgroup_sha256": "b" * 64,
                            "cgroup_paths_sha256": "target-cgroup",
                            "container_identity": "c" * 64,
                            "cpus_allowed_list": "21-24,57-60",
                            "mems_allowed_list": "1",
                            "role": "target_mysql",
                            "process_io": {
                                "read_bytes": index * 4096,
                                "write_bytes": index * 8192,
                            },
                        }
                    ],
                },
            },
        }
        rows.append(
            {
                "index": index,
                "monotonic": float(index),
                "elapsed_seconds": float(index),
                "required_streams": streams,
            }
        )
    return rows


def write_qualification_evidence(
    path: Path,
    *,
    scenario: str,
    mode: str,
    token: str,
    peer_scenario: str | None,
    paired_peer_attempt_id: str | None,
    samples: list[dict],
) -> None:
    attempt_id = "attempt-" + token[:32]
    value = {
        "schema_version": "mysql_algorithm_c_debug_qualification_evidence_v2",
        "status":
            "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY",
        "mode": mode,
        "qualification_attempt_id": attempt_id,
        "bundle_id": "d" * 64,
        "scenario": scenario,
        "hostname": "node04",
        "peer_scenario": peer_scenario,
        "initial_live_readback": {
            "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
            "scenario": scenario,
            "hostname": "node04",
        },
        "peer_live_readback": (
            {
                "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
                "scenario": peer_scenario,
                "hostname": "node04",
            }
            if peer_scenario is not None
            else None
        ),
        "paired_peer_workload_proof": (
            {
                "schema_version":
                    "mysql_algorithm_c_paired_peer_workload_proof_v1",
                "scenario": peer_scenario,
                "qualification_attempt_id": paired_peer_attempt_id,
                "workload_metric_stream_path": "/tmp/peer-metrics.jsonl",
                "sample_count": 181,
                "first_sequence": 0,
                "last_sequence": 180,
                "completion_delta": 18000,
                "fresh_sequence_at_every_sample": True,
            }
            if peer_scenario is not None
            else None
        ),
        "duration_seconds": 180,
        "minimum_samples": 181,
        "maximum_sample_gap_seconds": 1.5,
        "required_streams": list(LAUNCHER.REQUIRED_STREAMS),
        "samples": samples,
        "summary": LAUNCHER.summarize_dynamic_samples(samples),
        "attempt_token_sha256": token,
        "collector_sha256": "c" * 64,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "concurrent_capture_r0_or_debug_authorized": False,
    }
    write_json_sidecar(path, value)


def bidirectional_fixture(
    tmp_path: Path,
    *,
    a_paired_samples: list[dict] | None = None,
    b_paired_samples: list[dict] | None = None,
) -> tuple[Path, Path, Path, Path]:
    paths = tuple(tmp_path / f"evidence-{index}.json" for index in range(4))
    write_qualification_evidence(
        paths[0],
        scenario="tpch_memory",
        mode="qualification_solo",
        token="1" * 64,
        peer_scenario=None,
        paired_peer_attempt_id=None,
        samples=synthetic_dynamic_samples(),
    )
    write_qualification_evidence(
        paths[1],
        scenario="tpch_memory",
        mode="qualification_paired",
        token="2" * 64,
        peer_scenario="tpcc_cpu",
        paired_peer_attempt_id="attempt-" + ("4" * 32),
        samples=a_paired_samples or synthetic_dynamic_samples(),
    )
    write_qualification_evidence(
        paths[2],
        scenario="tpcc_cpu",
        mode="qualification_solo",
        token="3" * 64,
        peer_scenario=None,
        paired_peer_attempt_id=None,
        samples=synthetic_dynamic_samples(),
    )
    write_qualification_evidence(
        paths[3],
        scenario="tpcc_cpu",
        mode="qualification_paired",
        token="4" * 64,
        peer_scenario="tpch_memory",
        paired_peer_attempt_id="attempt-" + ("2" * 32),
        samples=b_paired_samples or synthetic_dynamic_samples(),
    )
    return paths


def test_workload_metric_timeout_and_incomplete_latency_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(
        LAUNCHER.QualificationError, match="missing/stale/timed out"
    ):
        LAUNCHER.workload_metric_sample(
            missing, "attempt-" + ("x" * 32), None, timeout_seconds=0
        )
    stream = tmp_path / "metrics.jsonl"
    monkeypatch.setattr(LAUNCHER.time, "time", lambda: 1000.0)
    stream.write_text(
        json.dumps(
            {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_sample_v1",
                "qualification_attempt_id": "attempt-" + ("x" * 32),
                "sequence": 0,
                "epoch_seconds": 1000.0,
                "completed_total": 0,
                "latency_p50_ms": 1.0,
                "latency_p95_ms": 2.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        LAUNCHER.QualificationError, match="missing/stale/timed out"
    ):
        LAUNCHER.workload_metric_sample(
            stream, "attempt-" + ("x" * 32), None, timeout_seconds=0
        )


@pytest.mark.parametrize("missing_stream", LAUNCHER.REQUIRED_STREAMS)
def test_each_missing_required_stream_fails_closed(
    missing_stream: str,
) -> None:
    samples = synthetic_dynamic_samples()
    del samples[90]["required_streams"][missing_stream]
    with pytest.raises(
        LAUNCHER.QualificationError, match="required stream is missing"
    ):
        LAUNCHER.summarize_dynamic_samples(samples)


def test_sample_gap_over_contract_limit_fails_closed() -> None:
    samples = synthetic_dynamic_samples()
    for index in range(90, len(samples)):
        samples[index]["monotonic"] += 1.0
    with pytest.raises(
        LAUNCHER.QualificationError, match="sample gap exceeded"
    ):
        LAUNCHER.summarize_dynamic_samples(samples)


def test_bidirectional_comparator_passes_complete_in_tolerance_evidence(
    tmp_path: Path,
) -> None:
    result = LAUNCHER.compare_bidirectional_gate(
        *bidirectional_fixture(tmp_path)
    )
    assert result["schema_version"] == (
        "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
    )
    assert result["status"] == "PASS"
    assert result[
        "concurrent_capture_r0_or_debug_eligible_for_main_agent_decision"
    ] is True
    assert result["formal_authorized"] is False
    assert set(result["required_streams"]) == set(LAUNCHER.REQUIRED_STREAMS)


@pytest.mark.parametrize(
    ("paired_samples", "violation"),
    [
        (
            synthetic_dynamic_samples(completion_rate=90),
            "throughput_degradation_exceeded",
        ),
        (
            synthetic_dynamic_samples(latency_ms=12.0),
            "latency_p99_ms_inflation_exceeded",
        ),
        (
            synthetic_dynamic_samples(cpu_ratio=0.50),
            "cpu_usage_ratio_absolute_drift_exceeded",
        ),
        (
            synthetic_dynamic_samples(throttle_ratio=0.10),
            "cpu_throttle_ratio_absolute_drift_exceeded",
        ),
        (
            synthetic_dynamic_samples(foreign_bytes_per_second=1),
            "foreign_device_io_attribution_detected",
        ),
        (
            synthetic_dynamic_samples(oom_kills=1),
            "new_oom_kill_events_detected",
        ),
        (
            synthetic_dynamic_samples(swap_step_bytes=1),
            "new_unexpected_swap_events_detected",
        ),
    ],
)
def test_each_dynamic_tolerance_breach_requires_serial_fallback(
    tmp_path: Path,
    paired_samples: list[dict],
    violation: str,
) -> None:
    result = LAUNCHER.compare_bidirectional_gate(
        *bidirectional_fixture(tmp_path, a_paired_samples=paired_samples)
    )
    assert result["status"] == "FAIL"
    assert result["serial_fallback_required"] is True
    assert violation in result["directions"][
        "A_solo_vs_A_while_B_runs"
    ]["violations"]


def test_attempt_authority_cannot_be_reused_across_four_runs(
    tmp_path: Path,
) -> None:
    paths = bidirectional_fixture(tmp_path)
    reused = json.loads(paths[3].read_text(encoding="utf-8"))
    reused["attempt_token_sha256"] = "1" * 64
    write_json_sidecar(paths[3], reused)
    with pytest.raises(
        LAUNCHER.QualificationError, match="reused across attempts"
    ):
        LAUNCHER.compare_bidirectional_gate(*paths)
