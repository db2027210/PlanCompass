#!/usr/bin/env python3
"""Self-contained offline tests for the sealed JOB successor bundle.

These tests deliberately treat ``ROOT`` as the bundle under test.  They never
load a repository builder, publish another bundle, or depend on a sibling
``shell/`` tree.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v4"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def canonical(value) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def manifest():
    path = ROOT / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["bundle_id"] == ROOT.name
    assert value["schema_version"] == SCHEMA
    assert value["self_contained_bundle_tests"] is True
    assert value["bundle_tests_publish_prohibited"] is True
    identity = dict(value)
    identity.pop("bundle_id", None)
    assert hashlib.sha256(canonical(identity)).hexdigest() == ROOT.name
    for relative, expected in value["files"].items():
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink()
        assert digest(path) == expected
    return value


def frozen_python():
    path = Path(sys.executable).resolve(strict=True)
    return path, digest(path)


def remote_isomorphic_copy(root: Path):
    remote = (
        root
        / "mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job"
        / "runtime_algorithm_c_successor_20260729/bundles"
        / ROOT.name
    )
    remote.parent.mkdir(parents=True)
    shutil.copytree(ROOT, remote)
    return remote


def test_current_sealed_bundle_has_manifest_bound_sibling_module():
    value = manifest()
    module = "qualification/mysql_job_dedicated_daemon_contract_v1.py"
    authority = (
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"
    )
    test = "tests/test_mysql_job_module_closure_successor_v4.py"
    assert {module, authority, test}.issubset(value["files"])
    source = (ROOT / authority).read_text(encoding="utf-8")
    assert "except ModuleNotFoundError as exc:" in source
    assert "FAIL_CLOSED_BOOTSTRAP_AUTHORITY" in source
    assert '"--failure-output"' in source
    test_source = (ROOT / test).read_text(encoding="utf-8")
    assert "builder." + "publish" not in test_source
    assert "BUILDER" + "_PATH" not in test_source
    assert not (ROOT / "shell").exists()


def test_remote_isomorphic_frozen_python_fixture_and_missing_module_fail_closed(
    tmp_path,
):
    manifest()
    remote = remote_isomorphic_copy(tmp_path)
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
    environment = {
        **os.environ,
        "PYTHONPATH": "",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [
            str(python),
            "-B",
            str(authority),
            "--verify-dedicated-daemon-readback-fixture",
            str(fixture),
        ],
        cwd="/",
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["status"] == "PASS_DEDICATED_DAEMON_NON_PG_GRAPHDRIVER"
    assert evidence["accepted_round_authorized"] is False

    (remote / "qualification").chmod(0o700)
    (
        remote / "qualification/mysql_job_dedicated_daemon_contract_v1.py"
    ).unlink()
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
        env=environment,
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


def test_current_bundle_materializer_uses_external_authorization_root(tmp_path):
    manifest()
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
                ROOT
                / "qualification/mysql_job_dedicated_daemon_materializer_v4.py"
            ),
            "--bundle-manifest",
            str(ROOT / "bundle_manifest.json"),
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
            "--valid-minutes",
            "60",
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
    assert record["bundle_id"] == ROOT.name
    assert "/authorizations/" in record["authorization_root"]
    assert "/attempts/" not in record["authorization_root"]
    assert spec["paths"]["authorization_root"] == record["authorization_root"]
    assert install["attempt_root_must_be_absent_before_install"] is True
    assert install["destination_root_must_be_outside_attempts"] is True
    private = material / "private_authority/main_agent_token"
    assert private.stat().st_mode & 0o077 == 0


def test_algorithm_c_uses_raw_all_subsets_and_complete_original_gradient_event():
    value = manifest()
    assert value["algorithm_c_contract"] == {
        "formal_raw_all_nonempty_subsets_before_top5": True,
        "plan_clustering_diagnostic_only": True,
        "missing_l2_l3_independent": True,
        "original_gradient_confidence_required": True,
        "fixed_top5_only": True,
    }
    required = {
        "qualification/gradient_descent_optimizer.py",
        "qualification/experience_table_subset_optimizer.py",
        "qualification/mysql_parameter_optimizer.py",
        "qualification/mysql_plan_clustering.py",
        "qualification/mysql_three_level_fingerprint.py",
    }
    assert required <= set(value["files"])
    python, _python_sha = frozen_python()
    source = r'''
import math
from gradient_descent_optimizer import GradientDescentParameterOptimizer, ParameterRange
from mysql_parameter_optimizer import _formal_raw_all_subset_rankings
from mysql_plan_clustering import cluster_ranked_candidates

def estimator(_sql, params):
    return (
        {"cpu": 60.0 - params["p"], "memory": 20.0 + params["p"], "io": 10.0},
        {},
        {"feature_ratios": {"q1": params["p"] / 10.0}},
    )

optimizer = GradientDescentParameterOptimizer(
    estimator,
    {"cpu": 100.0, "memory": 100.0, "io": 100.0},
    score_mode="performance_io",
    verbose=False,
)
result = optimizer.optimize(
    p_real={"p": 10.0},
    sensitivity_table={
        "p": {"cpu": -2.0, "memory": 3.0, "io": 0.0, "confidence": 0.25}
    },
    param_ranges={"p": ParameterRange(1.0, 100.0)},
    active_params=["p"],
    sql_set=None,
    eta=0.1,
    max_iter=1,
    patience=1,
    gamma=0.5,
    original_gradient_only=True,
)
event = result.history[0]["gradient_debug"]
required = {
    "feature_ratio", "predicted_resource", "utilization", "dScore/dU", "T",
    "confidence", "g_j", "eta", "delta_param", "candidate_score",
}
assert required <= set(event)
rows = [
    {"subset_id": f"s{i:02d}", "final_score": float(16 - i)}
    for i in range(1, 16)
]
formal = _formal_raw_all_subset_rankings(rows, 4)
assert len(formal) == 15
assert [row["rank"] for row in formal[:5]] == [1, 2, 3, 4, 5]
diagnostic = cluster_ranked_candidates(formal, scenario="job")
assert diagnostic["missing_l2_or_l3_candidates_are_independent"] is True
assert len(formal) == 15
'''
    result = subprocess.run(
        [str(python), "-B", "-c", source],
        cwd=str(ROOT / "qualification"),
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "qualification"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
