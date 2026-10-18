import ast
import hashlib
from pathlib import Path

import pytest

from shell import mysql_process_classification_v10 as classification
from shell.mysql_process_classification_v10 import classify_process


def test_pg5_monitor_is_exact_observer():
    command = (
        "python3 -u "
        "/home/anonymous/program/resource_adaptive/shell/"
        "monitor_pg5_five_experiments.py "
        "--config /home/anonymous/program/resource_adaptive/config/formal/"
        "pg5_five_experiments_live_20260723.json --watch --interval 300"
    )
    assert classify_process(command) == "observer"


def test_similar_monitor_basename_requires_absolute_script():
    command = (
        "python3 monitor_pg5_five_experiments.py "
        "--config /tmp/hybench_xp.json"
    )
    assert classify_process(command) == "benchmark"


def test_exact_pg5_inline_probe_requires_original_argv(monkeypatch):
    source = "print('read-only probe fixture')"
    monkeypatch.setattr(
        classification,
        "_PG5_INLINE_PROBE_SHA256",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
    argv = [
        "/home/anonymous/miniconda3/bin/python3",
        "-c",
        source,
        (
            "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
            "hybench_xp_compute00_59630/experience_sgd/attempt37"
        ),
        "attempt37",
        "io",
    ]
    command = " ".join(argv)

    assert classify_process(command, argv=argv) == "observer"
    assert classify_process(command) != "observer"


@pytest.mark.parametrize(
    "index,replacement",
    [
        (0, "/usr/bin/python3"),
        (1, "-m"),
        (3, "/tmp/pg5"),
        (4, ""),
        (5, "qps"),
    ],
)
def test_pg5_inline_probe_near_misses_fail_closed(
    monkeypatch, index, replacement
):
    source = "print('read-only probe fixture')"
    monkeypatch.setattr(
        classification,
        "_PG5_INLINE_PROBE_SHA256",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
    argv = [
        "/home/anonymous/miniconda3/bin/python3",
        "-c",
        source,
        (
            "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
            "job_compute00/attempt"
        ),
        "job_attempt",
        "memory",
    ]
    argv[index] = replacement

    assert classify_process(" ".join(argv), argv=argv) != "observer"


def test_deployed_pg5_probe_matches_frozen_source_hash():
    monitor = Path(
        "/home/anonymous/program/resource_adaptive/shell/"
        "monitor_pg5_five_experiments.py"
    )
    if not monitor.is_file():
        pytest.skip("co-resident PG watcher source is not installed")
    tree = ast.parse(monitor.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "REMOTE_PROBE"
            for target in node.targets
        )
    )
    source = ast.literal_eval(assignment.value)

    assert (
        hashlib.sha256(source.encode("utf-8")).hexdigest()
        == classification._PG5_INLINE_PROBE_SHA256
    )
