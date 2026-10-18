#!/usr/bin/env python3
"""Auditably repair one infrastructure-contaminated GPTuner search lineage."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.run_pg_none_external_baseline import (
    candidate_failure_classification,
    canonical_sha256,
    read_json,
    write_json,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def require_stopped(state: Mapping[str, Any], label: str) -> None:
    if state.get("inflight_wave") is not None:
        raise RuntimeError(f"{label}: inflight wave must be absent")
    if state.get("phase") not in {
        "complete",
        "clean_stopped",
        "failed_closed_clean",
    }:
        raise RuntimeError(f"{label}: controller is not cleanly stopped")


def valid_observation_count(rows: List[Mapping[str, Any]]) -> int:
    return sum(row.get("valid") is True for row in rows)


def invalid_observation_count(rows: List[Mapping[str, Any]]) -> int:
    return sum(row.get("valid") is not True for row in rows)


def valid_validation_count(rows: List[Mapping[str, Any]]) -> int:
    return sum(row.get("physical_valid") is True for row in rows)


def invalid_validation_count(rows: List[Mapping[str, Any]]) -> int:
    return sum(row.get("physical_valid") is not True for row in rows)


def backup_state(method_root: Path, action: str) -> tuple[Path, Dict[str, Any]]:
    state_path = method_root / "state.json"
    state = read_json(state_path)
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    backup_root = method_root / "audited_recovery" / f"{timestamp}_{action}"
    backup_root.mkdir(parents=True, exist_ok=False)
    state_backup = backup_root / "state.before.json"
    write_json(state_backup, state)
    authority_path = method_root / "authority_manifest.json"
    if authority_path.is_file():
        authority_backup = backup_root / "authority_manifest.before.json"
        write_json(authority_backup, read_json(authority_path))
    return backup_root, state


def recompute_best(state: Mapping[str, Any]) -> Dict[str, Any]:
    observations = [
        copy.deepcopy(row)
        for row in list(state.get("observations") or [])
        if row.get("valid") is True
    ]
    previous = state.get("best_observation") or {}
    if int(previous.get("trial") or -1) == 0:
        observations.append(copy.deepcopy(previous))
    if not observations:
        raise RuntimeError("no valid retained observation for incumbent")
    return max(
        observations,
        key=lambda row: float(
            row.get("objective_score")
            if row.get("objective_score") is not None
            else row.get("db_qps")
            or 0.0
        ),
    )


def rollback_job(method_root: Path, first_trial: int) -> Dict[str, Any]:
    backup_root, state = backup_state(method_root, f"rollback_trial_{first_trial}")
    require_stopped(state, "job rollback")
    observations = list(state.get("observations") or [])
    retained = [row for row in observations if int(row.get("trial") or 0) < first_trial]
    removed = [row for row in observations if int(row.get("trial") or 0) >= first_trial]
    if not removed or int(removed[0].get("trial") or 0) != first_trial:
        raise RuntimeError("rollback boundary is absent or non-contiguous")
    validation_path = method_root / "validations" / f"{removed[0]['label']}.json"
    if not validation_path.is_file():
        raise RuntimeError("rollback boundary validation is missing")
    boundary_validation = read_json(validation_path)
    if candidate_failure_classification(boundary_validation) != "infrastructure_failure":
        raise RuntimeError("rollback boundary is not an infrastructure failure")

    post_rows = list(state.get("post_baseline_rows") or [])
    champion_rows = list(state.get("champion_validation_rows") or [])
    evidence = {
        "contract": "pg_external_superseded_search_lineage_evidence_v1",
        "first_superseded_trial": first_trial,
        "observations": removed,
        "post_baseline_rows": post_rows,
        "champion_validation_rows": champion_rows,
        "post_search_baseline": state.get("post_search_baseline"),
        "comparison_baseline": state.get("comparison_baseline"),
        "frozen_search_champion": state.get("frozen_search_champion"),
        "validated_champion": state.get("validated_champion"),
    }
    evidence_path = backup_root / "superseded_lineage.json"
    write_json(evidence_path, evidence)

    removed_valid = (
        valid_observation_count(removed)
        + valid_validation_count(post_rows)
        + valid_validation_count(champion_rows)
    )
    removed_invalid = (
        invalid_observation_count(removed)
        + invalid_validation_count(post_rows)
        + invalid_validation_count(champion_rows)
    )
    removed_physical = len(removed) + len(post_rows) + len(champion_rows)
    receipt = {
        "contract": "pg_external_superseded_search_lineage_v1",
        "reason": "candidate_infrastructure_failure_contaminated_feedback_lineage",
        "first_superseded_trial": first_trial,
        "retained_candidate_count": len(retained),
        "superseded_candidate_count": len(removed),
        "physical_attempt_count": removed_physical,
        "valid_attempt_count": removed_valid,
        "technical_failure_count": removed_invalid,
        "excluded_from_candidate_budget": True,
        "excluded_from_tuner_feedback": True,
        "excluded_from_comparisons": True,
        "source_state_sha256": file_sha256(backup_root / "state.before.json"),
        "evidence_path": str(evidence_path),
        "evidence_sha256": file_sha256(evidence_path),
        "created_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)

    state["observations"] = retained
    state["candidate_physical_invocations"] = len(retained)
    state["physical_workload_invocations"] = int(
        state.get("physical_workload_invocations") or 0
    ) - removed_physical
    state["valid_replays"] = int(state.get("valid_replays") or 0) - removed_valid
    state["technical_failures"] = int(
        state.get("technical_failures") or 0
    ) - removed_invalid
    if min(
        int(state["physical_workload_invocations"]),
        int(state["valid_replays"]),
        int(state["technical_failures"]),
    ) < 0:
        raise RuntimeError("rollback would create a negative replay counter")
    state["post_baseline_rows"] = []
    state["post_search_baseline"] = None
    state["comparison_baseline"] = None
    state["frozen_search_champion"] = None
    state["champion_validation_rows"] = []
    state["validated_champion"] = None
    state["best_observation"] = recompute_best(state)
    state["phase"] = "search"
    state["inflight_wave"] = None
    state.setdefault("superseded_search_lineage", []).append(receipt)
    for key in (
        "completed_at_epoch_s",
        "method_metadata",
        "technical_failure_accounting",
        "final_execution_domain_receipts",
    ):
        state.pop(key, None)
    state["updated_at_epoch_s"] = time.time()
    write_json(method_root / "state.json", state)
    authority_path = method_root / "authority_manifest.json"
    if authority_path.is_file():
        os.replace(authority_path, backup_root / "authority_manifest.superseded.json")
    append_jsonl(
        method_root.parent.parent / "gptuner_job_memory.log",
        {
            "event": "external_search_lineage_rolled_back",
            "time": time.time(),
            "method": "gptuner",
            "receipt": receipt,
        },
    )
    return receipt


def reclassify_xp_cpu(method_root: Path, trial: int) -> Dict[str, Any]:
    backup_root, state = backup_state(method_root, f"reclassify_trial_{trial}")
    require_stopped(state, "xp_cpu reclassification")
    observations = list(state.get("observations") or [])
    matches = [row for row in observations if int(row.get("trial") or 0) == trial]
    if len(matches) != 1:
        raise RuntimeError("XP-CPU reclassification trial is not unique")
    observation = matches[0]
    validation_path = method_root / "validations" / f"{observation['label']}.json"
    validation = read_json(validation_path)
    classification = candidate_failure_classification(validation)
    if classification != "configuration_failure":
        raise RuntimeError("XP-CPU trial is not a configuration failure")
    observation["model_failure"] = True
    observation["failure_classification"] = classification
    receipt = {
        "contract": "pg_external_candidate_failure_reclassification_v1",
        "trial": trial,
        "label": observation.get("label"),
        "classification": classification,
        "counter_values_unchanged": True,
        "included_in_candidate_budget": True,
        "included_in_tuner_feedback": True,
        "source_state_sha256": file_sha256(backup_root / "state.before.json"),
        "validation_path": str(validation_path),
        "validation_sha256": file_sha256(validation_path),
        "created_at_epoch_s": time.time(),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    state.setdefault("candidate_failure_reclassifications", []).append(receipt)
    state.pop("technical_failure_accounting", None)
    state["phase"] = "search"
    state["updated_at_epoch_s"] = time.time()
    write_json(method_root / "state.json", state)
    append_jsonl(
        method_root.parent.parent / "gptuner_xp_cpu.log",
        {
            "event": "external_candidate_failure_reclassified",
            "time": time.time(),
            "method": "gptuner",
            "receipt": receipt,
        },
    )
    return receipt


def preflight_repair(campaign_root: Path, job_first_trial: int, xp_trial: int) -> None:
    """Validate both repair targets before either state file is changed."""
    root = campaign_root / "gptuner"
    job_root = root / "job_memory"
    job_state = read_json(job_root / "state.json")
    require_stopped(job_state, "job rollback")
    job_matches = [
        row
        for row in list(job_state.get("observations") or [])
        if int(row.get("trial") or 0) == job_first_trial
    ]
    if len(job_matches) != 1:
        raise RuntimeError("JOB rollback boundary is not unique")
    job_validation = read_json(
        job_root / "validations" / f"{job_matches[0]['label']}.json"
    )
    if candidate_failure_classification(job_validation) != "infrastructure_failure":
        raise RuntimeError("JOB rollback boundary is not an infrastructure failure")

    xp_root = root / "xp_cpu"
    xp_state = read_json(xp_root / "state.json")
    require_stopped(xp_state, "xp_cpu reclassification")
    xp_matches = [
        row
        for row in list(xp_state.get("observations") or [])
        if int(row.get("trial") or 0) == xp_trial
    ]
    if len(xp_matches) != 1:
        raise RuntimeError("XP-CPU reclassification trial is not unique")
    xp_validation = read_json(
        xp_root / "validations" / f"{xp_matches[0]['label']}.json"
    )
    if candidate_failure_classification(xp_validation) != "configuration_failure":
        raise RuntimeError("XP-CPU trial is not a configuration failure")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_root", type=Path)
    parser.add_argument("--job-first-trial", type=int, default=28)
    parser.add_argument("--xp-cpu-trial", type=int, default=18)
    args = parser.parse_args()
    campaign_root = args.campaign_root.resolve()
    preflight_repair(campaign_root, args.job_first_trial, args.xp_cpu_trial)
    root = campaign_root / "gptuner"
    job_receipt = rollback_job(root / "job_memory", args.job_first_trial)
    xp_receipt = reclassify_xp_cpu(root / "xp_cpu", args.xp_cpu_trial)
    print(
        json.dumps(
            {
                "contract": "pg_gptuner_failure_lineage_repair_v1",
                "job": job_receipt,
                "xp_cpu": xp_receipt,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
