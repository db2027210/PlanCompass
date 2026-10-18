#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = (
    ROOT
    / "shell/build_mysql_job_dedicated_daemon_module_closure_successor_v2.py"
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_python():
    path = Path(sys.executable).resolve(strict=True)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def remote_isomorphic_copy(bundle, root):
    remote = (
        root
        / "mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job"
        / "runtime_algorithm_c_successor_20260729/bundles"
        / bundle.name
    )
    remote.parent.mkdir(parents=True)
    shutil.copytree(bundle, remote)
    return remote


def test_module_is_sibling_manifest_bound_and_failure_is_fail_closed(tmp_path):
    builder = load(BUILDER_PATH, "job_module_builder_manifest")
    bundle = builder.publish()
    manifest = json.loads(
        (bundle / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    module = "qualification/mysql_job_dedicated_daemon_contract_v1.py"
    authority = (
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"
    )
    assert module in manifest["files"]
    assert authority in manifest["files"]
    assert manifest["dedicated_daemon_module_same_directory_as_runtime"] is True
    source = (bundle / authority).read_text(encoding="utf-8")
    assert "except ModuleNotFoundError as exc:" in source
    assert "FAIL_CLOSED_BOOTSTRAP_AUTHORITY" in source
    assert '"--failure-output"' in source


def test_remote_isomorphic_frozen_python_executes_import_and_fixture(tmp_path):
    builder = load(BUILDER_PATH, "job_module_builder_e2e")
    bundle = builder.publish()
    remote = remote_isomorphic_copy(bundle, tmp_path)
    python, python_sha = frozen_python()
    assert len(python_sha) == 64
    authority = (
        remote
        / "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"
    )
    fixture = (
        remote
        / "qualification/fixtures/"
        "mysql_job_dedicated_daemon_readback_fixture_v1.json"
    )
    result = subprocess.run(
        [
            str(python),
            "-B",
            str(authority),
            "--verify-dedicated-daemon-readback-fixture",
            str(fixture),
        ],
        cwd="/",
        env={
            **os.environ,
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["status"] == (
        "PASS_DEDICATED_DAEMON_NON_PG_GRAPHDRIVER"
    )
    assert evidence["accepted_round_authorized"] is False

    # A remote-layout regression must become AuthorityError/NOT_READY, never
    # escape as an unhandled ModuleNotFoundError traceback.
    (remote / "qualification").chmod(0o700)
    (remote / "qualification/mysql_job_dedicated_daemon_contract_v1.py").unlink()
    failure_output = tmp_path / "module_failure.json"
    failed = subprocess.run(
        [
            str(python),
            "-B",
            str(authority),
            "--verify-dedicated-daemon-readback-fixture",
            str(fixture),
            "--failure-output",
            str(failure_output),
        ],
        cwd="/",
        env={
            **os.environ,
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode == 2
    assert "NOT_READY: sealed dedicated daemon contract module" in failed.stderr
    assert "Traceback" not in failed.stderr
    failure = json.loads(failure_output.read_text(encoding="utf-8"))
    assert failure["status"] == "FAIL_CLOSED_MODULE_OR_READBACK"
    assert failure["failure_type"] == "AuthorityError"
    assert failure["qualification_credit"] == 0
    assert failure["accepted_rounds_credit"] == 0


def test_materializer_places_authorization_outside_absent_attempt(tmp_path):
    builder = load(BUILDER_PATH, "job_module_builder_materializer")
    bundle = builder.publish()
    token = tmp_path / "main_agent_token"
    token.write_text("job-main-agent-fixture-token\n", encoding="utf-8")
    token.chmod(0o600)
    output = tmp_path / "materials"
    python, _python_sha = frozen_python()
    result = subprocess.run(
        [
            str(python),
            "-B",
            str(
                bundle
                / "qualification/mysql_job_dedicated_daemon_materializer_v3.py"
            ),
            "--bundle-manifest",
            str(bundle / "bundle_manifest.json"),
            "--mysql-image-reference",
            "mysql:8.0",
            "--mysql-image-id",
            "sha256:" + "a" * 64,
            "--client-image-reference",
            "mysql:8.0",
            "--client-image-id",
            "sha256:" + "b" * 64,
            "--main-agent-token-file",
            str(token),
            "--output-root",
            str(output),
        ],
        env={
            **os.environ,
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    material = Path(record["materials"])
    spec = json.loads(
        (material / "bootstrap_spec.json").read_text(encoding="utf-8")
    )
    install = json.loads(
        (material / "authorization_install_plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert "/authorizations/" in record["authorization_root"]
    assert "/attempts/" not in record["authorization_root"]
    assert spec["paths"]["authorization_root"] == record["authorization_root"]
    assert install["attempt_root_must_be_absent_before_install"] is True
    assert install["destination_root_must_be_outside_attempts"] is True
    assert (material / "private_authority/main_agent_token").stat().st_mode & 0o077 == 0
