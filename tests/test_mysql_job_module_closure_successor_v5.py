#!/usr/bin/env python3
"""Self-contained offline contract checks for the JOB v5 successor bundle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v5"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _manifest():
    value = json.loads((ROOT / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert value["bundle_id"] == ROOT.name
    assert value["schema_version"] == SCHEMA
    identity = dict(value)
    identity.pop("bundle_id", None)
    assert hashlib.sha256(_canonical(identity)).hexdigest() == ROOT.name
    for relative, expected in value["files"].items():
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink()
        assert _digest(path) == expected
    return value


def test_manifest_declares_no_formal_credit_and_v5_estimator_gates():
    value = _manifest()
    assert value["formal_launch_authorized"] is False
    assert value["accepted_round_authorized"] is False
    assert value["algorithm_c_contract"]["candidate_estimator_identity_required"] is True
    assert value["algorithm_c_contract"]["canonical_plan_bundle_required_before_seal"] is True
    assert value["algorithm_c_contract"]["all_unresponsive_candidates_fail_closed"] is True
    assert {
        "qualification/gradient_descent_optimizer.py",
        "qualification/experience_table_subset_optimizer.py",
        "qualification/mysql_parameter_optimizer.py",
        "qualification/mysql_plan_clustering.py",
        "qualification/mysql_three_level_fingerprint.py",
        "qualification/mysql_job_dedicated_daemon_materializer_v5.py",
    } <= set(value["files"])


def test_raw_top5_requires_bound_candidate_identity_canonical_plan_and_response():
    _manifest()
    source = r'''
from mysql_plan_clustering import build_canonical_plan_bundle
from mysql_parameter_optimizer import _formal_raw_all_subset_rankings

bundle = build_canonical_plan_bundle([{
  "query_id":"q1", "query_sql_sha256":"a" * 64, "query_order":1,
  "query_weight":1.0, "dynamic":False, "binding_id":"static",
  "binding_values":[], "plan_mode":"custom",
  "raw_explain":{"query_block":{"table":{"table_name":"t","access_type":"ALL"}}},
}], registry_query_ids=["q1"], registry_sha256="b" * 64, registry_authority_verified=True)

def row(index, responsive=True):
  p = float(index)
  return {
    "subset_id": "subset_%04d" % index, "final_score": float(16-index),
    "optimized_params":{"p":p}, "initial_params":{"p":0.0},
    "initial_resource":{"cpu":10.0,"memory":10.0,"io":10.0},
    "final_resource":{"cpu":10.0+index if responsive else 10.0,"memory":10.0,"io":10.0},
    "canonical_plan_bundle":bundle,
    "candidate_estimation_identity":{
      "verified":True,
      "requested_parameter_signature":[["p",p.hex()]],
      "metadata_parameter_signature":[["p",p.hex()]],
      "candidate_plan_work_sha256":("plan-%d" % index) if responsive else "baseline",
      "baseline_plan_work_sha256":"baseline",
    },
  }

assert len(_formal_raw_all_subset_rankings([row(i) for i in range(1,16)], 4)) == 15
try:
  _formal_raw_all_subset_rankings([row(i, False) for i in range(1,16)], 4)
except RuntimeError as exc:
  assert "unresponsive" in str(exc)
else:
  raise AssertionError("stale all-baseline candidates were sealed")
'''
    result = subprocess.run(
        [str(Path(sys.executable).resolve()), "-B", "-c", source],
        cwd=str(ROOT / "qualification"),
        env={**os.environ, "PYTHONPATH": str(ROOT / "qualification"), "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
