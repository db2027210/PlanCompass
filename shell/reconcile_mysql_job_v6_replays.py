#!/usr/bin/env python3
"""Reconcile JOB v6 replay authority without launching or rerunning a rank."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "history/mysql_gradient_debug/job_v6/20260728T085336Z"
CHECKPOINT = RUN / "formal_outer_checkpoint.json"
JOURNALS = RUN / "formal_point_journals"
OUTPUT = RUN / "formal_replay_authority_reconciliation_v1.json"
BASELINE = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "memory_temptable_row_cost": 0.1,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = Path(str(path) + ".sha256")
    expected = sidecar.read_text(encoding="utf-8").split()[0].strip()
    actual = sha256(path)
    if expected != actual:
        raise RuntimeError(f"SHA-256 sidecar mismatch: {path}")
    return actual


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify JOB v6 replay reconciliation; writing the authority "
            "artifact requires an explicit --write."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="atomically replace the reconciliation artifact after verification",
    )
    args = parser.parse_args()
    checkpoint_sha = verify_sidecar(CHECKPOINT)
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    expected_ranks = [1, 2, 3, 4, 5]
    if checkpoint.get("completed_subset_ids") is None or len(
        checkpoint["completed_subset_ids"]
    ) != 15:
        raise RuntimeError("JOB checkpoint does not contain all 15 subsets")
    if checkpoint.get("sealed_representative_ranks") != expected_ranks:
        raise RuntimeError("JOB sealed representative ranks differ")
    if checkpoint.get("replayed_candidate_ranks") != expected_ranks:
        raise RuntimeError("JOB replayed ranks differ")
    if checkpoint.get("inflight_replays") != {}:
        raise RuntimeError("JOB checkpoint still has an inflight replay")

    audit = checkpoint.get("audit_log", [])
    started = {
        int(event["rank"]): event
        for event in audit if event.get("event") == "candidate_replay_started"
    }
    replayed = {
        int(event["rank"]): event
        for event in audit if event.get("event") == "candidate_replayed"
    }
    if sorted(started) != expected_ranks or sorted(replayed) != expected_ranks:
        raise RuntimeError("JOB replay audit is incomplete")

    sealed = {
        int(candidate["rank"]): candidate
        for candidate in checkpoint["sealed_candidate_order"]
    }
    rank_records = []
    all_baseline = True
    missing_fingerprint_ranks = []
    for rank in expected_ranks:
        start = started[rank]
        completion = replayed[rank]
        attempt_token = str(start.get("attempt_token") or "")
        if completion.get("attempt_token") != attempt_token:
            raise RuntimeError(f"rank {rank} attempt token changed")
        journal_dir = JOURNALS / attempt_token
        if not journal_dir.is_dir():
            raise RuntimeError(f"rank {rank} point journal is absent")
        journal_files = sorted(
            path for path in journal_dir.glob("*.json")
            if path.name != "current.json"
        )
        journal_hashes = {
            path.name: verify_sidecar(path) for path in journal_files
        }
        current_path = journal_dir / "current.json"
        current_sha = verify_sidecar(current_path)
        current = json.loads(current_path.read_text(encoding="utf-8"))
        if (
            current.get("accounting_token") != attempt_token
            or current.get("resume_token") != attempt_token
            or current.get("measurement_complete") is not True
            or current.get("recovery_verified") is not True
            or current.get("lease_released") is not True
        ):
            raise RuntimeError(f"rank {rank} terminal journal gates are incomplete")
        if rank == 1:
            if (
                current.get("stage") != "resume_decision"
                or not current.get("orphan_import_manifest")
                or current.get("restart_outer_attempt_required") is True
            ):
                raise RuntimeError("rank 1 orphan reconciliation is not terminal")
        elif current.get("stage") != "point_complete":
            raise RuntimeError(f"rank {rank} journal is not point_complete")

        candidate = start["candidate"]
        sealed_candidate = sealed[rank]
        optimized = {
            str(name): float(value)
            for name, value in candidate["optimized_params"].items()
        }
        optimized_hex = {
            name: value.hex() for name, value in optimized.items()
        }
        if (
            candidate.get("subset_id") != sealed_candidate.get("subset_id")
            or optimized_hex != sealed_candidate.get("optimized_params_float_hex")
        ):
            raise RuntimeError(f"rank {rank} sealed candidate identity differs")
        all_baseline = all_baseline and optimized == BASELINE
        if sealed_candidate.get("canonical_plan_bundle_sha256") is None:
            missing_fingerprint_ranks.append(rank)
        rank_records.append({
            "rank": rank,
            "subset_id": candidate["subset_id"],
            "attempt_token": attempt_token,
            "optimized_params_float_hex": optimized_hex,
            "sealed_candidate_identity_verified": True,
            "candidate_replayed_audit_verified": True,
            "journal_stage": current["stage"],
            "journal_sequence": current["sequence"],
            "journal_current_sha256": current_sha,
            "journal_event_sha256": journal_hashes,
            "measurement_complete": True,
            "recovery_verified": True,
            "lease_released": True,
            "orphan_measurement_reconciled": rank == 1,
        })

    result = {
        "schema_version": "mysql_job_replay_authority_reconciliation_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_checkpoint": str(CHECKPOINT),
        "source_checkpoint_sha256": checkpoint_sha,
        "outer_iter": checkpoint.get("outer_iter"),
        "completed_nonempty_subsets": 15,
        "sealed_representative_ranks": expected_ranks,
        "replayed_candidate_ranks": expected_ranks,
        "inflight_replays": {},
        "rank_records": rank_records,
        "replay_authority_closed": True,
        "rerun_authorized": False,
        "accepted_rounds_credit": 0,
        "debug_round_valid_for_exploration": False,
        "technical_findings": {
            "all_15_candidates_equal_baseline": all_baseline,
            "sealed_candidates_missing_canonical_plan_bundle_ranks":
                missing_fingerprint_ranks,
            "ranking_error_proven": False,
            "gradient_or_parameter_update_requires_repair": all_baseline,
            "l2_l3_fingerprint_requires_repair":
                bool(missing_fingerprint_ranks),
        },
        "status": "RECOVERY_AUTHORITY_RECONCILED_NO_RERUN",
    }
    payload = canonical(result)
    if not args.write:
        if not OUTPUT.is_file():
            raise RuntimeError(
                "reconciliation artifact is absent; refusing to create it "
                "without explicit --write"
            )
        output_sha = verify_sidecar(OUTPUT)
        if OUTPUT.read_bytes() != payload:
            raise RuntimeError(
                "reconciliation artifact differs from verified source evidence; "
                "refusing to overwrite without explicit --write"
            )
        print(f"JOB_REPLAY_AUTHORITY_VERIFIED {OUTPUT} {output_sha}")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=OUTPUT.parent, prefix=f".{OUTPUT.name}.", delete=False
    ) as stream:
        stream.write(payload)
        staging = Path(stream.name)
    os.replace(staging, OUTPUT)
    sidecar = Path(str(OUTPUT) + ".sha256")
    sidecar.write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="utf-8")
    print(f"JOB_REPLAY_AUTHORITY_RECONCILED {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
