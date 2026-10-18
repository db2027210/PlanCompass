from __future__ import annotations

import hashlib
import json

import pytest

from mysql_workload_adapter_v2 import MySQLEndpoint, run_hybench


def _authority(tmp_path):
    artifact = tmp_path / "artifact"
    runtime = artifact / "runtime"
    runtime.mkdir(parents=True)
    jar = runtime / "native.jar"
    jar.write_bytes(b"native-xp")
    (artifact / "manifest.json").write_text(json.dumps({
        "runtime_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
        "execution_authorized": True,
    }), encoding="utf-8")
    runner = tmp_path / "adaptive.jar"
    runner.write_bytes(b"adaptive")
    home = tmp_path / "home"
    home.mkdir()
    return jar, runner, home


def test_native_xp_accepts_frozen_300_second_quiescence(tmp_path) -> None:
    jar, runner, home = _authority(tmp_path)
    result = run_hybench(
        "xp", MySQLEndpoint("127.0.0.1", 3306, "hybench_sf10x", "u", "p"),
        tmp_path / "output", hybench_home=home, duration_s=900, clients=8,
        runtime_jar=jar,
        formal_control={
            "runner_jar": runner,
            "stop_file": tmp_path / "output" / "stop.txt",
            "minimum_measurement_seconds": 300,
            "maximum_measurement_seconds": 900,
            "graceful_quiescence_seconds": 300,
        },
        dry_run=True,
    )
    assert result["formal_control"]["graceful_quiescence_seconds"] == 300


def test_native_xp_rejects_quiescence_above_frozen_cap(tmp_path) -> None:
    jar, runner, home = _authority(tmp_path)
    with pytest.raises(ValueError, match="300..900 seconds"):
        run_hybench(
            "xp", MySQLEndpoint("127.0.0.1", 3306, "hybench_sf10x", "u", "p"),
            tmp_path / "output", hybench_home=home, duration_s=900, clients=8,
            runtime_jar=jar,
            formal_control={
                "runner_jar": runner,
                "stop_file": tmp_path / "output" / "stop.txt",
                "minimum_measurement_seconds": 300,
                "maximum_measurement_seconds": 900,
                "graceful_quiescence_seconds": 301,
            },
            dry_run=True,
        )
