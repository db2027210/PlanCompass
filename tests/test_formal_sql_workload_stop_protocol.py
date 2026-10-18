from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import pytest


SOURCE = Path("formal_clients_20260721/com/formal/FormalSQLWorkload.java")


@pytest.fixture(scope="module")
def compiled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    classes = tmp_path_factory.mktemp("formal_sql_classes")
    subprocess.run(
        ["javac", "-encoding", "UTF-8", "-d", str(classes), str(SOURCE)],
        check=True,
        capture_output=True,
        text=True,
    )
    return classes


def java(compiled: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["java", "-cp", str(compiled), "com.formal.FormalSQLWorkload", *args],
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("seconds", [180, 210, 240, 570, 600])
def test_frozen_stop_boundaries_are_accepted(compiled: Path, seconds: int) -> None:
    result = java(compiled, "validate-stop-request", str(seconds), "600")
    assert f'"measurement_seconds":{seconds}' in result.stdout


@pytest.mark.parametrize("seconds", [0, 179, 181, 599, 601])
def test_non_boundary_stop_requests_fail_closed(compiled: Path, seconds: int) -> None:
    result = java(
        compiled, "validate-stop-request", str(seconds), "600", check=False
    )
    assert result.returncode != 0
    assert "stop request must be one of 180,210,...,600" in result.stderr


def test_stop_file_is_observed_only_after_atomic_publish(
    compiled: Path, tmp_path: Path,
) -> None:
    stop_file = tmp_path / "formal-job.stop"
    process = subprocess.Popen(
        [
            "java", "-cp", str(compiled), "com.formal.FormalSQLWorkload",
            "await-stop-request", str(stop_file), "600", "3000",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.1)
    temporary = tmp_path / "formal-job.stop.tmp"
    temporary.write_text("240\n", encoding="utf-8")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, stop_file)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert '"measurement_seconds":240' in stdout


def test_stale_stop_file_fails_before_session(compiled: Path, tmp_path: Path) -> None:
    stop_file = tmp_path / "stale.stop"
    stop_file.write_text("180\n", encoding="utf-8")
    result = java(
        compiled, "await-stop-request", str(stop_file), "600", "100", check=False
    )
    assert result.returncode != 0
    assert "stale stop_file exists before await" in result.stderr

