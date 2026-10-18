from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


BUNDLE_ENV = "MYSQL_JOB_V29_BUNDLE"


def _bundle() -> Path:
    raw = os.environ.get(BUNDLE_ENV)
    if not raw:
        pytest.skip(f"{BUNDLE_ENV} is required for published-bundle tests")
    return Path(raw)


def test_real_launcher_clean_cwd_import_and_help(tmp_path: Path) -> None:
    launcher = _bundle() / "launch_job_v29_debug.py"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-I", str(launcher), "--help"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "Run or analyze one isolated MySQL Algorithm C" in combined
    assert "ModuleNotFoundError" not in combined


def test_real_launcher_has_no_workspace_import_dependency(
    tmp_path: Path,
) -> None:
    launcher = _bundle() / "launch_job_v29_debug.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = "/definitely/not/the/workspace"
    env["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-I", str(launcher), "-h"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
