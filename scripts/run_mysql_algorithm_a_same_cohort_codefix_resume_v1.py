#!/usr/bin/env python3
"""Resume Algorithm A after a controller-only fix on the same worker cohort.

The interrupted outer round is archived and repeated from rank 1.  The frozen
same-worker baseline and the saved last-accepted QPS reference are preserved;
this continuation performs no incumbent/reference replay merely because the
controller code changed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_dynamic_slots_v2 as slots
from scripts import run_mysql_algorithm_a_async_scene_v3 as base


RESUME_CONTRACT = "mysql_algorithm_a_same_cohort_codefix_resume_v1"


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_predecessor(
    predecessor: Mapping[str, Any], scene: str,
) -> tuple[dict[str, Any], int, int]:
    checkpoint = predecessor.get("round_checkpoint")
    if (
        predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != scene
        or predecessor.get("formal_scope") != "mysql_only"
        or predecessor.get("cross_engine_comparison_prohibited") is not True
        or int(predecessor.get("accepted") or 0) >= base.TARGET_ACCEPTED
        or not isinstance(checkpoint, Mapping)
    ):
        raise RuntimeError("invalid incomplete same-cohort Algorithm A state")
    interrupted_round = int(checkpoint.get("round_index") or 0)
    if (
        interrupted_round < 1
        or interrupted_round != int(predecessor.get("round_index") or 0)
    ):
        raise RuntimeError("interrupted round identity differs")
    completed_round = interrupted_round - 1
    preserved = slots.search_state_payload(predecessor)
    baseline_rows = predecessor.get("baseline_by_worker")
    if (
        not isinstance(baseline_rows, Mapping)
        or len(baseline_rows) != base.REPLAY_PARALLELISM
        or not all(
            base.pg_outer.physical_valid(row)
            for row in baseline_rows.values()
        )
    ):
        raise RuntimeError("same-cohort predecessor baseline is invalid")
    preserved["baseline_by_worker"] = copy.deepcopy(baseline_rows)
    preserved["rejected_history"] = copy.deepcopy(
        predecessor.get("rejected_history") or []
    )
    return preserved, completed_round, interrupted_round


def build_resumed_state(
    *, fresh: Mapping[str, Any], predecessor: Mapping[str, Any],
    preserved: Mapping[str, Any], completed_round: int,
    interrupted_round: int, predecessor_path: Path,
) -> dict[str, Any]:
    value = copy.deepcopy(dict(fresh))
    value.update(copy.deepcopy(dict(preserved)))
    value.update({
        "accepted": int(predecessor["accepted"]),
        "accepted_target": base.TARGET_ACCEPTED,
        "round_index": completed_round,
        "rejected_rounds": int(predecessor.get("rejected_rounds") or 0),
        "consecutive_rejected_rounds": int(
            predecessor.get("consecutive_rejected_rounds") or 0
        ),
        "incumbent_reference_refresh_count": int(
            predecessor.get("incumbent_reference_refresh_count") or 0
        ),
        "maximum_rejected_outer_attempts":
            base.MAX_REJECTED_OUTER_ATTEMPTS,
        "predecessor_run_id": predecessor.get("run_id"),
        "predecessor_snapshot_path": str(predecessor_path),
        "predecessor_snapshot_sha256": sha(predecessor_path),
        "same_cohort_codefix_resume_contract": RESUME_CONTRACT,
        "execution_evidence_epoch": int(
            predecessor.get("execution_evidence_epoch") or 1
        ) + 1,
        "interrupted_round_index": interrupted_round,
        "partial_interrupted_round_credited": False,
        "round_checkpoint": None,
        "status": "RUNNING",
        "stage": "SAME_COHORT_CODEFIX_AUTHORITY_VALIDATED",
        "converged": False,
    })
    return value


def run(args: argparse.Namespace) -> int:
    scene = str(args.scene)
    predecessor = load(args.predecessor_state)
    preserved, completed_round, interrupted_round = validate_predecessor(
        predecessor, scene,
    )
    active, _ranges = base.scene_parameter_space(scene)
    contract = base.validate_a_contract(scene, active)
    freeze, workers, authority = base.validate_fresh_authority(
        runtime_preflight=args.runtime_preflight,
        prepared_binding=args.prepared_binding,
        benchmark_accounts=args.benchmark_accounts,
        fresh_r0=args.fresh_r0,
        scene=scene,
    )
    predecessor_workers = set(preserved["baseline_by_worker"])
    if set(workers) != predecessor_workers:
        raise RuntimeError("worker cohort changed during code-fix continuation")

    original_fresh_state = base.fresh_state
    original_measure = base.measure
    original_aggregate = base.aggregate_baseline
    original_atomic = base.atomic
    state_path = args.output_dir.resolve() / "algorithm_a_state.json"
    baseline_skipped = False

    def resumed_fresh_state(
        *, run_id: str, scene: str, active: Sequence[str],
        ranges: Mapping[str, Any], authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        fresh = original_fresh_state(
            run_id=run_id, scene=scene, active=active, ranges=ranges,
            authority=authority,
        )
        return build_resumed_state(
            fresh=fresh, predecessor=predecessor, preserved=preserved,
            completed_round=completed_round,
            interrupted_round=interrupted_round,
            predecessor_path=args.predecessor_state,
        )

    def preserved_aggregate(
        rows: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if rows != preserved["baseline_by_worker"]:
            raise RuntimeError("preserved same-cohort baseline differs")
        return copy.deepcopy(preserved["canonical_baseline"])

    def codefix_measure(**kwargs: Any):
        nonlocal baseline_skipped
        if not baseline_skipped and Path(kwargs["output"]).name == "formal_baseline":
            baseline_skipped = True
            ranking_log_root = (
                args.output_dir.resolve() / "plan_ranking_client_processes"
            )
            ranking_log_root.mkdir(parents=True, mode=0o700)
            runtime_workers = kwargs["workers"]
            for worker_id, worker in runtime_workers.items():
                worker["formal_client_log"] = str(
                    ranking_log_root / f"{worker_id}.jsonl"
                )
            return (
                copy.deepcopy(preserved["baseline_by_worker"]),
                kwargs["state"],
            )
        return original_measure(**kwargs)

    def codefix_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if (
            path == state_path
            and document.get("formal_started") is True
            and document.get("same_cohort_codefix_resume_completed") is not True
        ):
            document["canonical_baseline"] = copy.deepcopy(
                preserved["canonical_baseline"]
            )
            document["baseline_by_worker"] = copy.deepcopy(
                preserved["baseline_by_worker"]
            )
            document["incumbent_validation"] = copy.deepcopy(
                preserved["incumbent_validation"]
            )
            document["incumbent_reference_source"] = preserved[
                "incumbent_reference_source"
            ]
            document["same_cohort_codefix_resume_completed"] = True
            document["same_cohort_codefix_resume"] = {
                "contract": RESUME_CONTRACT,
                "same_worker_cohort": True,
                "baseline_reused_without_replay": True,
                "incumbent_reference_reused_without_replay": True,
                "reference_refresh_trigger_unchanged": (
                    "two_consecutive_semantic_rejections"
                ),
                "technical_failures_count_as_rejections": False,
                "interrupted_round_repeated_from_start": True,
                "interrupted_round_index": interrupted_round,
                "completed_round_index": completed_round,
            }
        original_atomic(path, document)

    base.fresh_state = resumed_fresh_state
    base.aggregate_baseline = preserved_aggregate
    base.measure = codefix_measure
    base.atomic = codefix_atomic
    base.validate_a_contract = lambda _scene, _active: contract
    base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(authority)
    )
    try:
        return base.run(args)
    finally:
        base.fresh_state = original_fresh_state
        base.aggregate_baseline = original_aggregate
        base.measure = original_measure
        base.atomic = original_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=base.SCENES, required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
