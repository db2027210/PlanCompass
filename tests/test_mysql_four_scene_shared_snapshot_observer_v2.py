import hashlib
from pathlib import Path

from shell.mysql_four_scene_shared_snapshot import proc_entries
from shell import mysql_process_classification_v10 as classification
from shell.mysql_process_classification_v10 import classify_process


def _frozen_observer_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "sealed_observer"
    monitor = root / "shell/benchmark_monitor.py"
    monitor.parent.mkdir(parents=True)
    monitor.write_text("print('sealed observer')\n", encoding="utf-8")
    monkeypatch.setattr(
        classification, "_BENCHMARK_MONITOR_SCRIPT", str(monitor)
    )
    monkeypatch.setattr(
        classification,
        "_BENCHMARK_MONITOR_SHA256",
        hashlib.sha256(monitor.read_bytes()).hexdigest(),
    )
    return root


def _write_proc(
    proc_root: Path,
    *,
    pid: int,
    ppid: int,
    argv: list[str],
    observer_root: Path,
    processor: int = 324,
) -> None:
    directory = proc_root / str(pid)
    directory.mkdir()
    (directory / "cmdline").write_bytes(
        b"\0".join(value.encode() for value in argv) + b"\0"
    )
    fields = ["S", str(ppid)] + ["0"] * 50
    fields[36] = str(processor)
    (directory / "stat").write_text(
        f"{pid} (test) {' '.join(fields)}\n", encoding="ascii"
    )
    (directory / "cgroup").write_text("0::/\n", encoding="utf-8")
    (directory / "cwd").symlink_to(observer_root)
    (directory / "exe").symlink_to(
        observer_root / "shell/benchmark_monitor.py"
    )
    (directory / "fd").mkdir()
    (directory / "fd/0").symlink_to(
        observer_root / "shell/benchmark_monitor.py"
    )


def test_exact_orphan_monitor_is_fully_inventoried_as_observer(
    tmp_path, monkeypatch
):
    observer_root = _frozen_observer_root(tmp_path, monkeypatch)
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_proc(
        proc_root,
        pid=3243222,
        ppid=1,
        argv=["python", "shell/benchmark_monitor.py"],
        observer_root=observer_root,
    )

    entries, observers = proc_entries(
        classify_process,
        target_cpus=set(range(184, 200)) | set(range(440, 456)),
        target_device="8:0",
        proc_root=proc_root,
    )

    assert len(entries) == 1
    assert entries[0]["category"] == "observer"
    assert entries[0]["observer_identity"] == (
        "frozen_benchmark_monitor_sha256"
    )
    assert observers["3243222"]["accepted_as_observer"] is True
    assert observers["3243222"]["data_plane_descendant_pids"] == []
    assert observers["3243222"]["target_cpu_hit"] is False
    assert observers["3243222"]["target_device_hit"] is False
    assert observers["3243222"]["path_devices"]


def test_monitor_on_target_cpu_is_not_exempted_from_device_authority(
    tmp_path, monkeypatch
):
    observer_root = _frozen_observer_root(tmp_path, monkeypatch)
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_proc(
        proc_root,
        pid=3243222,
        ppid=1,
        argv=["python", "shell/benchmark_monitor.py"],
        observer_root=observer_root,
        processor=184,
    )

    entries, observers = proc_entries(
        classify_process,
        target_cpus={184},
        target_device="8:0",
        proc_root=proc_root,
    )

    assert observers == {}
    assert entries[0]["category"] == "benchmark"
    assert entries[0]["observer_rejection_reasons"] == [
        "executing_on_target_cpu"
    ]


def test_monitor_with_hybench_descendant_is_not_observer(
    tmp_path, monkeypatch
):
    observer_root = _frozen_observer_root(tmp_path, monkeypatch)
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_proc(
        proc_root,
        pid=100,
        ppid=1,
        argv=["python", "shell/benchmark_monitor.py"],
        observer_root=observer_root,
    )
    _write_proc(
        proc_root,
        pid=101,
        ppid=100,
        argv=["bash"],
        observer_root=observer_root,
    )
    _write_proc(
        proc_root,
        pid=102,
        ppid=101,
        argv=["java", "com.hybench.HyBench"],
        observer_root=observer_root,
    )

    entries, observers = proc_entries(
        classify_process,
        target_cpus={184},
        target_device="8:0",
        proc_root=proc_root,
    )
    by_pid = {entry["pid"]: entry for entry in entries}

    assert observers == {}
    assert by_pid[100]["category"] == "benchmark"
    assert by_pid[100]["observer_rejection_reasons"] == [
        "owns_data_plane_descendant"
    ]
    assert by_pid[102]["category"] == "benchmark"
