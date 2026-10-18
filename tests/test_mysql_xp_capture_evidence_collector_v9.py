from pathlib import Path

from shell.mysql_xp_capture_evidence_collector_v9 import (
    _is_data_plane,
    _observer_violations,
    _proc_sample,
    _tree_device,
)
from shell.mysql_process_classification_v10 import classify_process


def test_dynamic_postgres_worker_inherits_snapshot_parent_device() -> None:
    entries = {
        2630045: {
            "pid": 2630045,
            "ppid": 1,
            "category": "postgresql",
            "command": "postgres -c port=59630",
        },
        2230634: {
            "pid": 2230634,
            "ppid": 2630045,
            "category": "postgresql",
            "command": "postgres: parallel worker for PID 1015165",
        },
    }

    assert _tree_device(
        2230634, entries, {}, {2630045: "259:5"}
    ) == "259:5"


def test_dynamic_grandchild_inherits_snapshot_ancestor_device() -> None:
    entries = {
        10: {"pid": 10, "ppid": 1, "category": "benchmark", "command": "java"},
        11: {"pid": 11, "ppid": 10, "category": "benchmark", "command": "bash"},
        12: {"pid": 12, "ppid": 11, "category": "benchmark", "command": "java"},
    }

    assert _tree_device(12, entries, {}, {10: "259:5"}) == "259:5"


def test_unknown_process_tree_remains_fail_closed() -> None:
    entries = {
        20: {
            "pid": 20,
            "ppid": 1,
            "category": "postgresql",
            "command": "postgres: parallel worker",
        }
    }

    assert _tree_device(20, entries, {}, {}) is None


def test_dynamic_resource_gate_counts_only_database_and_benchmark_processes() -> None:
    assert _is_data_plane({"category": "mysql"}) is True
    assert _is_data_plane({"category": "postgresql"}) is True
    assert _is_data_plane({"category": "benchmark"}) is True
    assert _is_data_plane({"category": "controller"}) is False
    assert _is_data_plane({"category": "observer"}) is False


def _write_proc(
    root: Path, *, pid: int, ppid: int, command: str
) -> None:
    directory = root / str(pid)
    directory.mkdir()
    (directory / "cmdline").write_bytes(command.replace(" ", "\0").encode() + b"\0")
    (directory / "status").write_text(
        "Cpus_allowed_list:\t0-511\nMems_allowed_list:\t0-1\n",
        encoding="utf-8",
    )
    fields = ["S", str(ppid)] + ["0"] * 50
    (directory / "stat").write_text(
        f"{pid} (test) {' '.join(fields)}\n", encoding="ascii"
    )
    (directory / "io").write_text(
        "read_bytes: 0\nwrite_bytes: 0\n", encoding="ascii"
    )
    (directory / "cgroup").write_text("0::/\n", encoding="utf-8")


def test_observer_descendant_with_benchmark_marker_stays_benchmark(
    tmp_path, monkeypatch
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_proc(
        proc_root,
        pid=100,
        ppid=1,
        command=(
            "python3 -u /sealed/observer/shell/"
            "monitor_pg5_five_experiments.py --config "
            "/sealed/observer/config/formal/"
            "pg5_five_experiments_live_20260723.json --watch --interval 300"
        ),
    )
    _write_proc(
        proc_root,
        pid=101,
        ppid=100,
        command="python3 -c print(1) /disk1 hybench_xp io",
    )
    monkeypatch.setattr(
        "shell.mysql_xp_capture_evidence_collector_v9.classify_process",
        classify_process,
    )
    entries = _proc_sample(proc_root)["entries"]
    by_pid = {entry["pid"]: entry for entry in entries}
    assert by_pid[100]["category"] == "observer"
    assert by_pid[101]["category"] == "benchmark"


def test_exact_observer_passes_only_off_target_cpu_device_without_children():
    sample = {
        "entries": [{
            "pid": 3243222,
            "ppid": 1,
            "category": "observer",
            "observer_identity": "frozen_benchmark_monitor_sha256",
            "cwd": "/home/anonymous/program/resource_adaptive",
            "processor": 324,
            "cwd_device_major_minor": "259:4",
            "executable_device_major_minor": "259:4",
            "open_regular_file_devices": ["259:4"],
        }],
        "process_tree_ppids": {"3243222": 1},
    }

    assert _observer_violations(
        sample,
        xp_cpus=set(range(184, 200)) | set(range(440, 456)),
        xp_device="8:0",
    ) == [{
        "pid": 3243222,
        "identity": "frozen_benchmark_monitor_sha256",
        "processor": 324,
        "path_devices": ["259:4"],
        "data_plane_descendant_pids": [],
        "passed": True,
        "reasons": [],
    }]


def test_exact_observer_fails_for_target_cpu_device_or_data_plane_child():
    sample = {
        "entries": [
            {
                "pid": 100,
                "ppid": 1,
                "category": "observer",
                "observer_identity": "frozen_benchmark_monitor_sha256",
                "cwd": "/home/anonymous/program/resource_adaptive",
                "processor": 184,
                "cwd_device_major_minor": "8:0",
                "executable_device_major_minor": "259:4",
                "open_regular_file_devices": ["8:0"],
            },
            {
                "pid": 102,
                "ppid": 101,
                "category": "benchmark",
                "command": "java com.hybench.HyBench",
            },
        ],
        "process_tree_ppids": {"100": 1, "101": 100, "102": 101},
    }

    [result] = _observer_violations(
        sample, xp_cpus={184}, xp_device="8:0"
    )
    assert result["passed"] is False
    assert result["data_plane_descendant_pids"] == [102]
    assert result["reasons"] == [
        "owns_data_plane_descendant",
        "executing_on_target_cpu",
        "open_path_on_target_device",
    ]
