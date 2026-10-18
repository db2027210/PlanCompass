#!/usr/bin/env python3
"""Authorize restart after fixing JOB model selection and reusing point 15."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
CONFIG_PATH = OUTER_ROOT / "generated_config/formal_job_outer_attempt1/memory/job.json"
BASE_AUTH_PATH = OUTER_ROOT / "job_quality_calibration/v3_outer_authorization.json"
RESTART_AUTH_PATH = (
    OUTER_ROOT
    / "job_quality_calibration/v3_outer_restart_authorization_after_startup.json"
)
RESULT_PATH = OUTER_ROOT / "formal_points/point_0015/point_result.json"
RECLASS_EVIDENCE_PATH = (
    OUTER_ROOT / "startup_baseline_budget_reclassification_evidence.json"
)
FAILURE_LOG_PATH = OUTER_ROOT / "launcher_v3_role_aware.log"


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Dict[str, Any], *, immutable: bool = False) -> None:
    if immutable and (path.exists() or path.is_symlink()):
        raise RuntimeError(f"refusing to overwrite immutable restart authorization: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> int:
    base_auth = read_json(BASE_AUTH_PATH)
    if base_auth.get("config_sha256") != sha256(CONFIG_PATH):
        raise RuntimeError("base authorized config changed before restart amendment")
    log_text = FAILURE_LOG_PATH.read_text(encoding="utf-8")
    expected_failure = "missing_selected_train_test_metrics"
    if expected_failure not in log_text or "'resource': 'cpu'" not in log_text:
        raise RuntimeError("outer did not stop at the exact CPU selected-model gate")
    result = read_json(RESULT_PATH)
    evidence = read_json(RECLASS_EVIDENCE_PATH)
    measurement = result.get("measurement") or {}
    if (
        result.get("status") != "complete"
        or evidence.get("classification") != "formal_startup_baseline"
        or evidence.get("point_result_sha256") != sha256(RESULT_PATH)
        or measurement.get("correctness_passed") is not True
        or (measurement.get("resource_stability") or {}).get("passed") is not True
    ):
        raise RuntimeError("point 15 is not reusable formal startup evidence")
    counters = {
        "global": int((OUTER_ROOT / "replay_counter.txt").read_text().strip()),
        "candidate_replay": int(
            (OUTER_ROOT / "formal_search_replay_counter.txt").read_text().strip()
        ),
        "accepted": int(
            (OUTER_ROOT / "formal_search_accepted_counter.txt").read_text().strip()
        ),
        "startup_baseline": int(
            (OUTER_ROOT / "formal_startup_baseline_counter.txt").read_text().strip()
        ),
    }
    if counters != {
        "global": 15,
        "candidate_replay": 0,
        "accepted": 0,
        "startup_baseline": 1,
    }:
        raise RuntimeError(f"unexpected restart accounting: {counters}")

    config = read_json(CONFIG_PATH)
    quality_gate = (
        (((config.get("metrics") or {}).get("plan_estimator") or {}).get(
            "model_retraining"
        ) or {}).get("quality_gate")
        or {}
    )
    if (
        float(quality_gate.get("min_test_r2")) != 0.0
        or quality_gate.get("target_resource") != "memory"
        or (quality_gate.get("r2_fallback") or {}).get("policy_id")
        != "pg5_job_role_aware_r2_fallback_v1"
    ):
        raise RuntimeError("restart would change the approved quality policy")
    quality_gate["runtime_selected_model_sets"] = {
        "cpu": "JOB",
        "io": "JOB",
        "memory": "JOB",
    }
    protocol = config.setdefault("formal_outer_protocol", {})
    protocol.update(
        {
            "formal_startup_baseline_counter_path": str(
                OUTER_ROOT / "formal_startup_baseline_counter.txt"
            ),
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_maximum_count": 1,
            "formal_startup_baseline_excluded_from_candidate_replay_budget": True,
        }
    )
    config["startup_baseline_reuse"] = {
        "enabled": True,
        "point_result_path": str(RESULT_PATH),
        "point_result_sha256": sha256(RESULT_PATH),
        "reclassification_evidence_path": str(RECLASS_EVIDENCE_PATH),
        "reclassification_evidence_sha256": sha256(RECLASS_EVIDENCE_PATH),
        "candidate_replay_budget_before_restart": 0,
        "accepted_budget_before_restart": 0,
        "startup_baseline_count_before_restart": 1,
        "reuse_is_measurement_only_no_direction_claim": True,
    }
    write_json(CONFIG_PATH, config)
    authorization = {
        "schema_version": 1,
        "authorization_id": (
            "pg5_job_v3_outer_restart_after_startup_model_selection_fix_v1"
        ),
        "authorized": True,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "workload": "job",
        "target_resource": "memory",
        "min_test_r2": 0.0,
        "base_authorization_path": str(BASE_AUTH_PATH),
        "base_authorization_sha256": sha256(BASE_AUTH_PATH),
        "exact_failure": "cpu_missing_selected_JOB_metrics_due_to_absent_runtime_selection_metadata",
        "failure_log_path": str(FAILURE_LOG_PATH),
        "failure_log_sha256": sha256(FAILURE_LOG_PATH),
        "point_result_sha256": sha256(RESULT_PATH),
        "reclassification_evidence_sha256": sha256(RECLASS_EVIDENCE_PATH),
        "config_sha256": sha256(CONFIG_PATH),
        "replay_accounting": counters,
        "candidate_replay_budget_preserved": True,
        "startup_baseline_reused_without_replay": True,
        "runtime_selected_model_sets": quality_gate["runtime_selected_model_sets"],
    }
    write_json(RESTART_AUTH_PATH, authorization, immutable=True)
    print(json.dumps(authorization, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
