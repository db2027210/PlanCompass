import hashlib
from pathlib import Path

import pytest

from shell import mysql_process_classification_v10 as classification
from shell.mysql_process_classification_v10 import classify_process


EXACT_PROBE = (
    "/bin/sh -c \"ps aux | grep -E "
    "'bash.*launch_hybench_ap_attempt' | grep -v grep\""
)


def test_exact_read_only_shell_probe_is_observer():
    argv = [
        "/bin/sh",
        "-c",
        "ps aux | grep -E 'bash.*launch_hybench_ap_attempt' | grep -v grep",
    ]

    assert classify_process(EXACT_PROBE, argv=argv) == "observer"
    assert classify_process(EXACT_PROBE) == "observer"


@pytest.mark.parametrize(
    "command",
    [
        (
            "/bin/sh -c \"ps aux | grep -E "
            "'bash.*launch_hybench_ap_attempt' | grep -v grep; true\""
        ),
        (
            "/bin/sh -c \"ps aux | grep -E "
            "'bash.*launch_hybench_ap_attempt.*' | grep -v grep\""
        ),
        (
            "/bin/sh -c \"ps aux | grep -E "
            "'bash.*launch_hybench_ap_attempt' | grep -v grep | head\""
        ),
        (
            "/usr/bin/sh -c \"ps aux | grep -E "
            "'bash.*launch_hybench_ap_attempt' | grep -v grep\""
        ),
    ],
)
def test_near_or_extended_shell_probe_remains_benchmark(command):
    assert classify_process(command) == "benchmark"


def test_relative_parent_monitor_path_is_not_trusted():
    command = "python shell/benchmark_monitor.py"

    assert classify_process(command) == "benchmark"


def _frozen_monitor(tmp_path, monkeypatch):
    root = tmp_path / "sealed_observer"
    monitor = root / "shell/benchmark_monitor.py"
    monitor.parent.mkdir(parents=True)
    monitor.write_text("print('frozen observer fixture')\n", encoding="utf-8")
    monkeypatch.setattr(
        classification, "_BENCHMARK_MONITOR_SCRIPT", str(monitor)
    )
    monkeypatch.setattr(
        classification,
        "_BENCHMARK_MONITOR_SHA256",
        hashlib.sha256(monitor.read_bytes()).hexdigest(),
    )
    return root, monitor


def test_relative_parent_monitor_requires_matching_cwd_evidence(
    tmp_path, monkeypatch
):
    root, _monitor = _frozen_monitor(tmp_path, monkeypatch)
    assert classify_process(
        "python shell/benchmark_monitor.py",
        cwd=root,
    ) == "observer"
    assert classify_process(
        "python -u shell/benchmark_monitor.py",
        cwd=root,
    ) == "observer"
    assert classify_process(
        "python shell/benchmark_monitor.py",
        cwd=root / "shell",
    ) == "benchmark"


def test_absolute_parent_monitor_requires_frozen_deployed_source(
    tmp_path, monkeypatch
):
    _root, monitor = _frozen_monitor(tmp_path, monkeypatch)

    assert classify_process(f"python {monitor}") == "observer"
    assert classify_process(f"python -u {monitor}") == "observer"
    assert classify_process(f"python {monitor} --watch") == "benchmark"


def test_relative_parent_rejects_symlink_even_when_target_and_hash_match(
    tmp_path, monkeypatch,
):
    real = tmp_path / "real_monitor.py"
    real.write_text("print('read only')\n", encoding="utf-8")
    link = tmp_path / "benchmark_monitor.py"
    link.symlink_to(real)
    monkeypatch.setattr(
        classification, "_BENCHMARK_MONITOR_SCRIPT", str(real)
    )
    monkeypatch.setattr(
        classification,
        "_BENCHMARK_MONITOR_SHA256",
        hashlib.sha256(real.read_bytes()).hexdigest(),
    )

    assert classify_process(
        "python benchmark_monitor.py",
        cwd=tmp_path,
    ) == "benchmark"


def test_relative_parent_rejects_wrong_hash(tmp_path, monkeypatch):
    monitor = tmp_path / "benchmark_monitor.py"
    monitor.write_text("print('changed')\n", encoding="utf-8")
    monkeypatch.setattr(
        classification, "_BENCHMARK_MONITOR_SCRIPT", str(monitor)
    )
    monkeypatch.setattr(
        classification, "_BENCHMARK_MONITOR_SHA256", "0" * 64
    )

    assert classify_process(
        "python benchmark_monitor.py",
        cwd=tmp_path,
    ) == "benchmark"


def test_relative_parent_rejects_extra_args_with_matching_cwd(
    tmp_path, monkeypatch
):
    root, _monitor = _frozen_monitor(tmp_path, monkeypatch)

    assert classify_process(
        "python shell/benchmark_monitor.py --watch",
        cwd=root,
    ) == "benchmark"
