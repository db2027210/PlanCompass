#!/usr/bin/env python3
"""Self-contained offline checks for the JOB Algorithm-C repair successor."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v14"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _canonical(value) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n").encode("utf-8")


def _manifest():
    value = json.loads((ROOT / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert value["bundle_id"] == ROOT.name
    assert value["schema_version"] == SCHEMA
    identity = dict(value)
    identity.pop("bundle_id")
    assert hashlib.sha256(_canonical(identity)).hexdigest() == ROOT.name
    assert (ROOT / "bundle_manifest.json.sha256").read_text(encoding="ascii") == (
        f"{_digest(ROOT / 'bundle_manifest.json')}  bundle_manifest.json\n"
    )
    for relative, expected in value["files"].items():
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink()
        assert _digest(path) == expected
    return value


def test_manifest_is_zero_credit_and_binds_all_repairs():
    value = _manifest()
    assert value["formal_launch_authorized"] is False
    assert value["accepted_round_authorized"] is False
    assert value["accepted_rounds_credit"] == 0
    contract = value["algorithm_c_contract"]
    assert contract["original_gradient_confidence_required"] is True
    assert contract["raw_gradient_event_revalidated_before_seal"] is True
    assert contract["all_baseline_winners_fail_closed"] is True
    assert contract["canonical_plan_sha_required_at_checkpoint"] is True
    assert contract["complete_l1_l2_l3_required_for_outer_acceptance"] is True
    assert contract["missing_l2_l3_independent"] is True
    assert contract["qps_candidate_input_prohibited"] is True


def test_algorithm_repairs_are_executable_and_fail_closed():
    _manifest()
    source = r'''
import copy
from mysql_parameter_optimizer import (
    _attach_replay_three_level_fingerprint,
    _evaluate_outer_eligibility,
    _formal_raw_all_subset_rankings,
)
from mysql_plan_clustering import build_canonical_plan_bundle

bundle = build_canonical_plan_bundle([{
  "query_id":"q1", "query_sql_sha256":"a"*64, "query_order":1,
  "query_weight":1.0, "dynamic":False, "binding_id":"static",
  "binding_values":[], "plan_mode":"custom",
  "raw_explain":{"query_block":{"table":{"table_name":"t","access_type":"ALL"}}},
}], registry_query_ids=["q1"], registry_sha256="b"*64,
registry_authority_verified=True)

def event(g=1.0):
  eta=.1; before=1.0; proposed=before*(1+eta*g)
  return {
    "gradient_source":"original_dScore_dU_times_T_times_confidence",
    "feature_ratio":{"rows":1.0},
    "predicted_resource":{"cpu":.5,"memory":.5,"io":.5},
    "utilization":{"cpu":.5,"memory":.5,"io":.5},
    "dScore/dU":{"p":{"cpu":2.0}}, "T":{"p":{"cpu":1.0}},
    "confidence":{"p":.5}, "g_j":{"p":g}, "eta":eta,
    "delta_param":{"p":{"before":before,"proposed_before_clip":proposed,
      "after_clip":proposed,"absolute":proposed-before,
      "relative":(proposed-before)/before,"clipped":False}},
    "candidate_score":1.0,
  }

def row(index, baseline=False):
  value=1.0 if baseline else 1.0+index/100.0
  return {
    "subset_id":f"subset_{index:04d}", "active_subset":["p"],
    "gradient_events":[event()], "final_score":float(16-index),
    "optimized_params":{"p":value}, "initial_params":{"p":1.0},
    "initial_resource":{"cpu":10.,"memory":10.,"io":10.},
    "final_resource":{"cpu":10.+index,"memory":10.,"io":10.},
    "canonical_plan_bundle":bundle,
    "candidate_estimation_identity":{"verified":True,
      "requested_parameter_signature":[["p",value.hex()]],
      "metadata_parameter_signature":[["p",value.hex()]],
      "candidate_plan_work_sha256":f"candidate-{index}",
      "baseline_plan_work_sha256":"baseline"},
  }

ranked=_formal_raw_all_subset_rankings([row(i) for i in range(1,16)],4)
assert len(ranked)==15 and [x["rank"] for x in ranked[:5]]==[1,2,3,4,5]
try:
  _formal_raw_all_subset_rankings([row(i,True) for i in range(1,16)],4)
except RuntimeError as exc:
  assert "only baseline winners" in str(exc)
else:
  raise AssertionError("all-baseline Top-5 sealed")
bad=[row(i) for i in range(1,16)]
bad[0]["gradient_events"][0]["g_j"]["p"]=9.0
try:
  _formal_raw_all_subset_rankings(bad,4)
except RuntimeError as exc:
  assert "not sum" in str(exc)
else:
  raise AssertionError("tampered raw gradient sealed")

candidate=row(1)
missing=_attach_replay_three_level_fingerprint({},candidate,"job")
assert missing["true_duplicate_eligible"] is False
assert missing["missing_fingerprint_kept_independent"] is True
assert "missing_l2_execution_state_by_query" in missing["three_level_fingerprint"]["true_duplicate_ineligible_reasons"]
evidence={
  "execution_state_by_query":{"q1":{"workers":0,"hash_batches":1,
    "sort_mode":"none","spill_mode":"none","spill_bytes":0,
    "bitmap_lossy_pages":0,"intermediate_rows":1}},
  "resource_vectors":{"q1":{"memory_peak_bytes":1024,
    "dynamic_execution_memory_bytes":512,"spill_bytes":0}},
}
validation={"three_level_fingerprint_evidence":evidence}
attached=_attach_replay_three_level_fingerprint(validation,candidate,"job")
assert attached["three_level_fingerprint"]["true_duplicate_usable"] is True
assert attached["three_level_fingerprint"]["qps_in_fingerprint"] is False
'''
    result = subprocess.run(
        [str(Path(sys.executable).resolve()), "-B", "-c", source],
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

