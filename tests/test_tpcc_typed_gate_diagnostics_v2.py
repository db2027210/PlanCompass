from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "capture/mysql_tpcc_typed_interference_gate_v1.py"


def load_gate():
    sys.path.insert(0, str(ROOT / "capture"))
    spec = importlib.util.spec_from_file_location("tpcc_gate_v2", GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def sample(monotonic: float, duration: float = 0.1):
    return {
        "monotonic": monotonic,
        "epoch_ns": int(monotonic * 1_000_000_000),
        "proc_sample_duration_seconds": duration,
        "entries": [],
    }


def identity(pid: int, category: str, cpuset: str, devices=()):
    return {
        "pid": pid,
        "ppid": 1,
        "start_ticks": pid * 10,
        "category": category,
        "command": f"{category}-{pid}",
        "cwd": "/frozen",
        "cpuset_cpus": cpuset,
        "cpuset_mems": "1",
        "cgroup": "/frozen",
        "device_authority": list(devices),
        "device_authority_complete": True,
        "vm_data_plane_candidate": False,
    }


def test_gap_reset_and_deadline_preserve_complete_diagnostics() -> None:
    gate = load_gate()
    before = sample(10.0, 0.2)
    current = sample(12.0, 0.3)
    old = {(10, 100): identity(10, "postgresql", "32-51")}
    new = {(10, 100): dict(old[(10, 100)])}
    record = gate.reset_record(
        reason="sample_gap",
        previous_sample=before,
        current_sample=current,
        stable_samples=[sample(8.0), before],
        previous_identities=old,
        current_identities=new,
    )
    record["reset_index"] = 1
    record["triggers"] = ["sample_gap"]
    assert record["sample_gap_seconds"] == 2.0
    assert record["stable_interval_count"] == 1
    assert record["previous_proc_sample_duration_seconds"] == 0.2
    assert record["current_proc_sample_duration_seconds"] == 0.3
    summary = gate.deadline_summary(
        samples=[current],
        resets=[record],
        sample_count=60,
        deadline_seconds=150.0,
    )
    assert summary["required_interval_count"] == 60
    assert summary["maximum_sample_gap_seconds"] == 1.5
    assert summary["reset_count_by_reason"]["sample_gap"] == 1
    assert summary["reset_journal"][0]["reset_index"] == 1


def test_identity_churn_records_added_and_removed_authority() -> None:
    gate = load_gate()
    removed = identity(20, "postgresql", "32-51", ("259:5",))
    added = identity(21, "postgresql", "32-51", ("259:5",))
    record = gate.reset_record(
        reason="relevant_identity_churn",
        previous_sample=sample(20.0),
        current_sample=sample(21.0),
        stable_samples=[sample(19.0), sample(20.0)],
        previous_identities={(20, 200): removed},
        current_identities={(21, 210): added},
    )
    assert record["removed_identities"] == [removed]
    assert record["added_identities"] == [added]
    assert record["removed_identities"][0]["cwd"] == "/frozen"
    assert record["added_identities"][0]["device_authority"] == ["259:5"]


def test_qemu_is_never_invisible_to_the_data_plane_gate() -> None:
    gate = load_gate()
    argv = [
        "qemu-system-x86_64",
        "-drive",
        "file=/disk3/vm/work-overlay.qcow2",
    ]
    assert gate.is_vm_data_plane(" ".join(argv), argv)
    source = GATE.read_text(encoding="utf-8")
    assert 'category = "benchmark"' in source
    assert '"vm_data_plane_candidate": vm_data_plane' in source


def process_row(
    pid: int,
    cpuset: str,
    *,
    cpu_ticks: int,
    io_bytes: int,
    category: str = "benchmark",
):
    return {
        "pid": pid,
        "ppid": 1,
        "start_ticks": pid * 10,
        "category": category,
        "cpuset_cpus": cpuset,
        "cpu_ticks": cpu_ticks,
        "io_bytes": io_bytes,
    }


def test_interval_detects_cpu_and_target_device_overlap() -> None:
    gate = load_gate()
    before_row = process_row(
        30, "208-215,464-471", cpu_ticks=100, io_bytes=1000
    )
    after_row = process_row(
        30, "208-215,464-471", cpu_ticks=110, io_bytes=5096
    )
    key = gate.identity(after_row)
    observed = gate.interval_interference(
        before={30: before_row},
        after={30: after_row},
        owned=set(),
        target_pid=99,
        initial_relevant={key},
        target_cpus=gate.parse_cpu_list(gate.EXPECTED_CPUSET),
        target_device="8:0",
        device_authority={key: ["8:0"]},
        elapsed=1.0,
        ticks_per_second=100.0,
    )
    assert observed["cpu_overlap_pids"] == [30]
    assert observed["active_shared_device_pids"] == [30]
    assert observed["co_resident_shared_device_io_bps"] == 4096.0


def test_41_disjoint_postgres_restore_backend_worker_churn_is_observe_only() -> None:
    gate = load_gate()
    previous = {}
    journal = []
    commands = (
        "bash /disk1/pg5_common/lane/safe_restore_hybench.sh",
        "postgres: postgres hybench_sf10x SELECT",
        "postgres: parallel worker for PID 1065363",
    )
    for index in range(41):
        pid = 4000 + index
        row = {
            **identity(
                pid,
                "postgresql" if index % 3 == 2 else "benchmark",
                "32-51,288-307",
                ("259:5",),
            ),
            "command": commands[index % len(commands)],
        }
        current = {(pid, pid * 10): row}
        event = gate.churn_record(
            previous_sample=sample(float(index)),
            current_sample=sample(float(index + 1)),
            previous_identities=previous,
            current_identities=current,
            target_cpus=gate.parse_cpu_list(gate.EXPECTED_CPUSET),
            target_device="8:0",
        )
        journal.append(event)
        previous = current
    assert len(journal) == 41
    assert {row["action"] for row in journal} == {"observe_only"}
    assert {
        row["reason"] for row in journal
    } == {"isolated_postgresql_identity_churn"}
    assert all(
        event["decision"]["reason"] == "isolated_postgresql_identity"
        for row in journal
        for event in row["events"]
    )


def test_cpu_shared_device_and_unknown_authority_fail_closed() -> None:
    gate = load_gate()
    base = {
        "pid": 40, "ppid": 0, "start_ticks": 400,
        "category": "postgresql", "command": "postgres: worker",
        "cpuset_cpus": "32-51,288-307",
        "device_authority": ["259:5"],
    }
    target_cpus = gate.parse_cpu_list(gate.EXPECTED_CPUSET)
    assert gate.isolation_decision(
        base, target_cpus=target_cpus, target_device="8:0"
    )["action"] == "observe_only"
    assert gate.isolation_decision(
        {**base, "cpuset_cpus": gate.EXPECTED_CPUSET},
        target_cpus=target_cpus,
        target_device="8:0",
    )["reason"] == "cpu_overlap"
    assert gate.isolation_decision(
        {**base, "device_authority": ["8:0"]},
        target_cpus=target_cpus,
        target_device="8:0",
    )["reason"] == "shared_target_device"
    assert gate.isolation_decision(
        {**base, "device_authority": []},
        target_cpus=target_cpus,
        target_device="8:0",
    )["reason"] == "device_authority_unknown"


def test_non_postgres_churn_stays_in_blocking_identity_set() -> None:
    gate = load_gate()
    benchmark = {
        **identity(
            50, "benchmark", "32-51,288-307", ("259:5",)
        ),
        "command": "unrelated benchmark",
    }
    decision = gate.isolation_decision(
        benchmark,
        target_cpus=gate.parse_cpu_list(gate.EXPECTED_CPUSET),
        target_device="8:0",
    )
    assert decision["action"] == "retain_relevant"
