import json
from pathlib import Path
import threading

import pytest

from shell import mysql_algorithm_c_local_services_v25 as services
from shell.mysql_algorithm_c_local_services_v25 import LocalCampaign


def campaign(tmp_path: Path) -> LocalCampaign:
    value = object.__new__(LocalCampaign)
    value.output = tmp_path
    value.lane = type(
        "Lane",
        (),
        {
            "key": "job",
            "scenario": "job_memory",
            "cpuset": "176-183,432-439",
            "device": "/dev/nvme1n1",
        },
    )()
    value._runtime_interference_stop = threading.Event()
    value._runtime_interference_thread = None
    value._runtime_interference_lock = threading.Lock()
    value._runtime_interference_started_epoch_ms = 1
    value._runtime_interference_sample_count = 0
    value._runtime_interference_error = None
    value._runtime_interference_ready = threading.Event()
    value._runtime_interference_ready_epoch_ms = None
    value._runtime_interference_ready_timeout_seconds = 0.2
    return value


def test_preparation_gate_fails_before_data_prepare(tmp_path, monkeypatch):
    value = campaign(tmp_path)
    monkeypatch.setattr(
        value,
        "_preparation_interference_rows",
        lambda: [
            {
                "pid": 42,
                "category": "benchmark",
                "overlap_cpus": "176-183",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="failed closed"):
        value._preparation_interference_gate(
            window_seconds=1, interval_seconds=0.001
        )

    artifact = tmp_path / "preparation_interference_gate_v1.json"
    assert artifact.is_file()
    assert '"passed": false' in artifact.read_text(encoding="utf-8")


def test_preparation_gate_passes_only_after_clear_window(tmp_path, monkeypatch):
    value = campaign(tmp_path)
    monkeypatch.setattr(value, "_preparation_interference_rows", lambda: [])

    result = value._preparation_interference_gate(
        window_seconds=0.002, interval_seconds=0.001
    )

    assert result["passed"] is True
    assert result["sample_count"] >= 2


def test_runtime_gate_detects_late_overlapping_benchmark_and_fails_closed(
    tmp_path, monkeypatch,
):
    value = campaign(tmp_path)
    monkeypatch.setattr(
        value,
        "_preparation_interference_rows",
        lambda: [
            {
                "pid": 73,
                "category": "benchmark",
                "overlap_cpus": "432-439",
            }
        ],
    )

    value._runtime_interference_monitor()

    with pytest.raises(RuntimeError, match="runtime interference gate failed closed"):
        value._assert_runtime_interference_clear()
    artifact = tmp_path / "runtime_interference_gate_v1.json"
    result = json.loads(artifact.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["first_violation"]["violations"][0]["pid"] == 73


def test_start_sampler_waits_for_first_clean_runtime_sample(
    tmp_path, monkeypatch,
):
    value = campaign(tmp_path)
    order = []
    monkeypatch.setattr(
        services.Campaign,
        "start_sampler",
        lambda self: order.append("base_sampler_started"),
    )
    monkeypatch.setattr(value, "_preparation_interference_rows", lambda: [])

    value.start_sampler()

    assert order == ["base_sampler_started"]
    result = json.loads(
        (tmp_path / "runtime_interference_gate_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["status"] == "READY"
    assert result["sample_count"] >= 1
    assert result["ready_epoch_ms"] is not None
    value._runtime_interference_stop.set()
    value._runtime_interference_thread.join(timeout=2)


def test_start_sampler_fails_before_caller_can_start_workload_on_violation(
    tmp_path, monkeypatch,
):
    value = campaign(tmp_path)
    monkeypatch.setattr(
        services.Campaign, "start_sampler", lambda self: None
    )
    monkeypatch.setattr(
        value,
        "_preparation_interference_rows",
        lambda: [{
            "pid": 74,
            "category": "benchmark",
            "overlap_cpus": "",
            "device_relation": "TARGET_DEVICE_CONFLICT",
            "interference_reasons": ["target_device_conflict"],
        }],
    )
    workload_started = False

    with pytest.raises(RuntimeError, match="failed closed"):
        value.start_sampler()
        workload_started = True

    assert workload_started is False
    result = json.loads(
        (tmp_path / "runtime_interference_gate_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["status"] == "INTERFERENCE_DETECTED"
    assert result["sample_count"] == 1


def test_start_sampler_timeout_fails_closed_before_workload(
    tmp_path, monkeypatch,
):
    value = campaign(tmp_path)
    value._runtime_interference_ready_timeout_seconds = 0.01
    monkeypatch.setattr(
        services.Campaign, "start_sampler", lambda self: None
    )
    monkeypatch.setattr(
        value, "_runtime_interference_monitor", lambda: None
    )

    with pytest.raises(RuntimeError, match="did not produce a first sample"):
        value.start_sampler()

    result = json.loads(
        (tmp_path / "runtime_interference_gate_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["passed"] is False
    assert result["status"] == "INTERFERENCE_DETECTED"


def test_device_conflict_propagates_across_controller_workload_tree():
    processes = {
        100: {
            "pid": 100,
            "ppid": 1,
            "category": "controller",
            "command": "python controller.py",
            "cpuset_cpus": "0-3",
            "direct_device_evidence": [{
                "authority": "argv_flag:--output-root",
                "path": "/disk2/point",
                "device_major_minor": "259:6",
            }],
        },
        101: {
            "pid": 101,
            "ppid": 100,
            "category": "benchmark",
            "command": "pgbench -T 600",
            "cpuset_cpus": "4-7",
            "direct_device_evidence": [],
        },
    }

    violations = services._interference_violations(
        processes,
        owned=set(),
        target_cpus={200},
        target_device_major_minor="259:6",
    )

    assert [row["pid"] for row in violations] == [100, 101]
    assert all(
        row["device_relation"] == "TARGET_DEVICE_CONFLICT"
        for row in violations
    )
    assert all(row["overlap_cpus"] == "" for row in violations)


def test_unknown_process_device_fails_closed_even_with_disjoint_cpu():
    processes = {
        102: {
            "pid": 102,
            "ppid": 1,
            "category": "benchmark",
            "command": "pgbench -T 600",
            "cpuset_cpus": "4-7",
            "direct_device_evidence": [],
        },
    }

    violations = services._interference_violations(
        processes,
        owned=set(),
        target_cpus={200},
        target_device_major_minor="259:6",
    )

    assert violations[0]["device_relation"] == "UNKNOWN_PROCESS_DEVICE"
    assert violations[0]["interference_reasons"] == [
        "process_device_authority_unknown"
    ]


def test_known_disjoint_device_and_cpu_passes():
    processes = {
        103: {
            "pid": 103,
            "ppid": 1,
            "category": "benchmark",
            "command": "pgbench -T 600",
            "cpuset_cpus": "4-7",
            "direct_device_evidence": [{
                "authority": "proc_cwd",
                "path": "/other",
                "device_major_minor": "8:0",
            }],
        },
    }

    assert services._interference_violations(
        processes,
        owned=set(),
        target_cpus={200},
        target_device_major_minor="259:6",
    ) == []


def test_pgbench_cwd_and_container_bind_are_device_authorities(tmp_path):
    major_minor = (
        f"{services.os.major(tmp_path.stat().st_dev)}:"
        f"{services.os.minor(tmp_path.stat().st_dev)}"
    )
    pgbench = services._direct_process_device_evidence(
        ["pgbench", "-T", "600"], str(tmp_path)
    )
    container = services._direct_process_device_evidence(
        ["docker", "run", "-v", f"{tmp_path}:/var/lib/postgresql:rw"],
        None,
    )
    mount_equals = services._direct_process_device_evidence(
        [
            "docker",
            "run",
            f"--mount=type=bind,source={tmp_path},target=/var/lib/mysql",
        ],
        None,
    )
    output_equals = services._direct_process_device_evidence(
        ["python", "controller.py", f"--output-root={tmp_path}"],
        None,
    )

    assert {row["device_major_minor"] for row in pgbench} == {major_minor}
    assert {row["device_major_minor"] for row in container} == {major_minor}
    assert {row["device_major_minor"] for row in mount_equals} == {major_minor}
    assert {row["device_major_minor"] for row in output_equals} == {major_minor}
