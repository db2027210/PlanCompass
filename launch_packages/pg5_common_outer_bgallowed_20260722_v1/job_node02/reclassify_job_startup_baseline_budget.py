#!/usr/bin/env python3
"""Auditably exclude JOB global point 15 from the candidate replay budget."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
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
GLOBAL_PATH = OUTER_ROOT / "replay_counter.txt"
SEARCH_PATH = OUTER_ROOT / "formal_search_replay_counter.txt"
ACCEPTED_PATH = OUTER_ROOT / "formal_search_accepted_counter.txt"
STARTUP_PATH = OUTER_ROOT / "formal_startup_baseline_counter.txt"
LOCK_PATH = OUTER_ROOT / "replay_accounting.lock"
POINT_ROOT = OUTER_ROOT / "formal_points/point_0015"
CLAIM_PATH = OUTER_ROOT / "startup_baseline_budget_reclassification_claim.json"
EVIDENCE_PATH = OUTER_ROOT / "startup_baseline_budget_reclassification_evidence.json"
FRESH_POINTER = OUTER_ROOT / "fresh_restart_r0/latest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_immutable(path: Path, value: Dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite immutable audit: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def write_counter(path: Path, value: int) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"{value}\n")
        handle.flush()
        os.fsync(handle.fileno())


def claim() -> Dict[str, Any]:
    state_path = POINT_ROOT / "point_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("point_id") != "experience_table_outer_0015":
        raise RuntimeError("global point 15 is not the optimizer startup baseline")
    fresh = json.loads(FRESH_POINTER.read_text(encoding="utf-8"))
    if fresh.get("global_replay_ordinal") != 14:
        raise RuntimeError("startup baseline is not directly chained from fresh R0")
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        before = {
            "global": int(GLOBAL_PATH.read_text(encoding="utf-8").strip()),
            "candidate_replay": int(SEARCH_PATH.read_text(encoding="utf-8").strip()),
            "accepted": int(ACCEPTED_PATH.read_text(encoding="utf-8").strip()),
            "startup_baseline": (
                int(STARTUP_PATH.read_text(encoding="utf-8").strip())
                if STARTUP_PATH.is_file()
                else 0
            ),
        }
        if before != {
            "global": 15,
            "candidate_replay": 1,
            "accepted": 0,
            "startup_baseline": 0,
        }:
            raise RuntimeError(f"unexpected accounting state before reclassification: {before}")
        write_counter(SEARCH_PATH, 0)
        write_counter(STARTUP_PATH, 1)
        after = {
            "global": 15,
            "candidate_replay": 0,
            "accepted": 0,
            "startup_baseline": 1,
        }
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    audit = {
        "schema_version": 1,
        "audit_id": "pg5_job_startup_baseline_budget_reclassification_v1",
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "point_id": "experience_table_outer_0015",
        "global_ordinal": 15,
        "classification": "formal_startup_baseline",
        "candidate_replay_budget_consumed": False,
        "accepted_budget_consumed": False,
        "reason": (
            "parameter_optimizer startup baseline is a formal measurement ordinal "
            "but precedes 127-subset candidate ranking and is not a candidate replay"
        ),
        "before": before,
        "after": after,
        "fresh_r0_pointer_path": str(FRESH_POINTER),
        "fresh_r0_pointer_sha256": sha256(FRESH_POINTER),
        "point_state_path": str(state_path),
        "point_state_sha256_at_claim": sha256(state_path),
        "claimed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_json_immutable(CLAIM_PATH, audit)
    return audit


def seal() -> Dict[str, Any]:
    claim_audit = json.loads(CLAIM_PATH.read_text(encoding="utf-8"))
    result_path = POINT_ROOT / "point_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    measurement = result.get("measurement") or {}
    if (
        result.get("point_id") != "experience_table_outer_0015"
        or result.get("status") != "complete"
        or measurement.get("correctness_passed") is not True
        or (measurement.get("resource_stability") or {}).get("passed") is not True
    ):
        raise RuntimeError("startup baseline result is not complete and valid")
    if (
        int(SEARCH_PATH.read_text(encoding="utf-8").strip()) != 0
        or int(ACCEPTED_PATH.read_text(encoding="utf-8").strip()) != 0
        or int(STARTUP_PATH.read_text(encoding="utf-8").strip()) != 1
    ):
        raise RuntimeError("candidate or accepted budget moved before startup seal")
    evidence = {
        "schema_version": 1,
        "audit_id": "pg5_job_startup_baseline_budget_reclassification_evidence_v1",
        "claim_path": str(CLAIM_PATH),
        "claim_sha256": sha256(CLAIM_PATH),
        "point_id": result["point_id"],
        "point_result_path": str(result_path),
        "point_result_sha256": sha256(result_path),
        "classification": claim_audit["classification"],
        "candidate_replay_budget_after": 0,
        "accepted_budget_after": 0,
        "startup_baseline_count_after": 1,
        "sealed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_json_immutable(EVIDENCE_PATH, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seal", action="store_true")
    args = parser.parse_args()
    print(json.dumps(seal() if args.seal else claim(), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
