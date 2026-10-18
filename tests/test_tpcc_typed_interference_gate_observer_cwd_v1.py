from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "capture"
CLASSIFICATION_PATH = CAPTURE / "mysql_process_classification.py"
GATE_PATH = CAPTURE / "mysql_tpcc_typed_interference_gate_v1.py"
MONITOR_ARGV = ["python", "shell/benchmark_monitor.py"]
MONITOR_BYTES = b"#!/usr/bin/env python3\n# sealed observer fixture\n"


def load_modules():
    classification_spec = importlib.util.spec_from_file_location(
        "mysql_process_classification", CLASSIFICATION_PATH
    )
    assert (
        classification_spec is not None
        and classification_spec.loader is not None
    )
    classification = importlib.util.module_from_spec(classification_spec)
    classification_spec.loader.exec_module(classification)
    previous = sys.modules.get("mysql_process_classification")
    sys.modules["mysql_process_classification"] = classification
    try:
        gate_spec = importlib.util.spec_from_file_location(
            "tpcc_bundle_gate_observer_test", GATE_PATH
        )
        assert gate_spec is not None and gate_spec.loader is not None
        gate = importlib.util.module_from_spec(gate_spec)
        gate_spec.loader.exec_module(gate)
    finally:
        if previous is None:
            sys.modules.pop("mysql_process_classification", None)
        else:
            sys.modules["mysql_process_classification"] = previous
    return classification, gate


@pytest.fixture
def observer(tmp_path: Path, monkeypatch):
    classification, gate = load_modules()
    monitor_root = tmp_path / "sealed-monitor-root"
    script = monitor_root / "shell/benchmark_monitor.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(MONITOR_BYTES)
    monkeypatch.setattr(
        classification, "_BENCHMARK_MONITOR_SCRIPT", str(script)
    )
    monkeypatch.setattr(
        classification,
        "_BENCHMARK_MONITOR_SHA256",
        hashlib.sha256(MONITOR_BYTES).hexdigest(),
    )
    return classification, gate, monitor_root


def proc_directory(tmp_path: Path, cwd: Path) -> Path:
    directory = tmp_path / "4242"
    directory.mkdir()
    (directory / "cwd").symlink_to(cwd, target_is_directory=True)
    return directory


def test_relative_monitor_uses_the_real_proc_cwd(
    tmp_path: Path, observer
) -> None:
    _, gate, monitor_root = observer
    directory = proc_directory(tmp_path, monitor_root)
    category, cwd = gate.classify_proc_command(
        directory, " ".join(MONITOR_ARGV), MONITOR_ARGV
    )
    assert category == "observer"
    assert cwd == str(monitor_root)


def test_relative_monitor_with_wrong_proc_cwd_fails_closed(
    tmp_path: Path, observer
) -> None:
    _, gate, _ = observer
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    directory = proc_directory(tmp_path, wrong)
    category, cwd = gate.classify_proc_command(
        directory, " ".join(MONITOR_ARGV), MONITOR_ARGV
    )
    assert category == "benchmark"
    assert cwd == str(wrong)


def test_relative_monitor_with_extra_argv_fails_closed(
    tmp_path: Path, observer
) -> None:
    _, gate, monitor_root = observer
    directory = proc_directory(tmp_path, monitor_root)
    argv = [*MONITOR_ARGV, "--watch"]
    category, _ = gate.classify_proc_command(
        directory, " ".join(argv), argv
    )
    assert category == "benchmark"


def test_relative_monitor_with_wrong_hash_fails_closed(
    tmp_path: Path, monkeypatch, observer
) -> None:
    classification, gate, monitor_root = observer
    directory = proc_directory(tmp_path, monitor_root)
    monkeypatch.setattr(
        classification, "_BENCHMARK_MONITOR_SHA256", "0" * 64
    )
    category, _ = gate.classify_proc_command(
        directory, " ".join(MONITOR_ARGV), MONITOR_ARGV
    )
    assert category == "benchmark"
