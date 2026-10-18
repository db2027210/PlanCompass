#!/usr/bin/env python3
"""Resume Algorithm A while reusing an interrupted round's physical evidence.

This continuation is for a controller-only candidate-classification fix.  It
preserves the same-worker baseline and last-accepted reference, regenerates the
same factorial candidate document, reclassifies already attempted waves from
their immutable batch results, and starts new physical work only after the
last predecessor wave.  No baseline, incumbent, or previously attempted
candidate is physically replayed solely because the controller changed.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as full_resume


RESUME_CONTRACT = "mysql_algorithm_a_partial_round_codefix_resume_v1"
PARTIAL_RESUME_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_partial_round_resume_20260822_v1"
    / "partial_round_resume_contract.json"
)


def regular_file(value: str) -> Path:
    return full_resume.regular_file(value)


def load(path: Path) -> dict[str, Any]:
    return full_resume.load(path)


def sha(path: Path) -> str:
    return full_resume.sha(path)


def validate_partial_resume_contract() -> dict[str, Any]:
    if PARTIAL_RESUME_CONTRACT.is_symlink() or not PARTIAL_RESUME_CONTRACT.is_file():
        raise RuntimeError("partial-round recovery contract is absent")
    contract = load(PARTIAL_RESUME_CONTRACT)
    base_contract = base.A_CONTRACT.resolve()
    flow = contract.get("partial_round_recovery_flow") or {}
    if (
        contract.get("release_version") != "v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("base_algorithm_a_contract_path")
        != str(base_contract.relative_to(ROOT))
        or contract.get("base_algorithm_a_contract_sha256") != sha(base_contract)
        or flow.get("same_worker_cohort_required") is not True
        or flow.get("candidate_document_exact_equivalence_required") is not True
        or flow.get("cached_batch_sha256_required") is not True
        or flow.get("cached_attempted_prefix_physically_replayed") is not False
        or flow.get("cached_attempted_prefix_reclassified_by_v4") is not True
        or flow.get("new_physical_work_starts_after_cached_prefix") is not True
        or flow.get("baseline_reused_without_replay") is not True
        or flow.get("incumbent_reference_reused_without_replay") is not True
        or flow.get("technical_failures_count_as_semantic_rejections") is not False
    ):
        raise RuntimeError("partial-round recovery semantic contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = (ROOT / str(binding["path"])).resolve()
        if path.is_symlink() or not path.is_file() or sha(path) != binding["sha256"]:
            raise RuntimeError(f"partial-round recovery source differs: {path}")
    return contract


def _artifact_path(
    predecessor_root: Path, *, round_index: int, wave_index: int | None = None,
    attempt: int | None = None,
) -> Path:
    path = predecessor_root / f"round_{round_index:03d}"
    if wave_index is not None:
        path = path / f"wave_{wave_index:03d}"
    if attempt is not None:
        path = path / f"attempt_{attempt:02d}"
    return path


def validate_cached_round(
    predecessor: Mapping[str, Any], predecessor_path: Path,
    interrupted_round: int,
) -> tuple[Path, dict[int, dict[str, Any]], list[int]]:
    checkpoint = predecessor.get("round_checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise RuntimeError("predecessor round checkpoint is absent")
    waves = checkpoint.get("waves")
    attempted = [int(value) for value in checkpoint.get("attempted_ranks") or []]
    if not isinstance(waves, Sequence) or isinstance(waves, (str, bytes)):
        raise RuntimeError("predecessor cached waves are invalid")
    if not waves or not attempted:
        raise RuntimeError("partial-round evidence is empty")

    predecessor_root = predecessor_path.parent.resolve()
    candidate_path = _artifact_path(
        predecessor_root, round_index=interrupted_round,
    ) / "candidates.json"
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise RuntimeError("predecessor candidate document is absent")
    candidate_document = load(candidate_path)
    if (
        int(candidate_document.get("round_index") or 0) != interrupted_round
        or int(candidate_document.get("candidate_count") or 0)
        != int(checkpoint.get("candidate_count") or 0)
        or not isinstance(candidate_document.get("candidates"), Sequence)
    ):
        raise RuntimeError("predecessor candidate document differs")

    cached: dict[int, dict[str, Any]] = {}
    flattened: list[int] = []
    for expected_wave, raw in enumerate(waves, start=1):
        if not isinstance(raw, Mapping):
            raise RuntimeError("predecessor wave record is invalid")
        wave_index = int(raw.get("wave_index") or 0)
        attempt = int(raw.get("attempt") or 0)
        ranks = [int(value) for value in raw.get("ranks") or []]
        if wave_index != expected_wave or attempt < 1 or not ranks:
            raise RuntimeError("predecessor waves are not a contiguous prefix")
        batch_path = _artifact_path(
            predecessor_root, round_index=interrupted_round,
            wave_index=wave_index, attempt=attempt,
        ) / "batch_result.json"
        if batch_path.is_symlink() or not batch_path.is_file():
            raise RuntimeError(f"predecessor batch result is absent: {wave_index}")
        batch = load(batch_path)
        measurement_rows(batch, expected_workers=None)
        cached[wave_index] = {
            "wave_index": wave_index,
            "attempt": attempt,
            "ranks": ranks,
            "batch_path": batch_path,
            "batch_sha256": sha(batch_path),
        }
        flattened.extend(ranks)

    if flattened != attempted or attempted != list(range(1, max(attempted) + 1)):
        raise RuntimeError("predecessor attempted ranks are not a contiguous prefix")
    return candidate_path, cached, attempted


def measurement_rows(
    document: Mapping[str, Any], expected_workers: set[str] | None,
) -> dict[str, dict[str, Any]]:
    raw = document.get("measurements")
    rows: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        iterator = raw.items()
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        iterator = (
            (str(row.get("worker_id") or ""), row)
            for row in raw if isinstance(row, Mapping)
        )
    else:
        raise RuntimeError("cached batch measurements are invalid")
    for worker_id, row in iterator:
        if not worker_id or not isinstance(row, Mapping) or worker_id in rows:
            raise RuntimeError("cached batch worker evidence is invalid")
        rows[str(worker_id)] = copy.deepcopy(dict(row))
    if expected_workers is not None and set(rows) != expected_workers:
        raise RuntimeError("cached batch worker cohort differs")
    return rows


def candidate_documents_equivalent(
    predecessor_path: Path, regenerated_path: Path,
) -> bool:
    predecessor = load(predecessor_path)
    regenerated = load(regenerated_path)
    document_keys = (
        "algorithm", "method", "scene", "round_index", "accepted_index",
        "active_parameters", "candidate_count", "expected_candidate_count",
        "canonical_rank_order_preserved",
    )
    candidate_keys = (
        "rank", "active_subset", "algorithm_a_factor_levels",
        "algorithm_a_factor_mask", "candidate_origin", "optimized_params",
        "effective_optimizer_cost_readback",
        "optimizer_cost_readback_tolerance", "probe_kind",
        "prediction_used_for_ordering", "prediction_used_for_selection",
        "qps_used_for_inner_scoring",
    )
    if not all(
        predecessor.get(key) == regenerated.get(key) for key in document_keys
    ):
        return False
    predecessor_candidates = predecessor.get("candidates")
    regenerated_candidates = regenerated.get("candidates")
    if (
        not isinstance(predecessor_candidates, Sequence)
        or isinstance(predecessor_candidates, (str, bytes))
        or not isinstance(regenerated_candidates, Sequence)
        or isinstance(regenerated_candidates, (str, bytes))
        or len(predecessor_candidates) != len(regenerated_candidates)
    ):
        return False
    return all(
        isinstance(left, Mapping)
        and isinstance(right, Mapping)
        and all(left.get(key) == right.get(key) for key in candidate_keys)
        for left, right in zip(predecessor_candidates, regenerated_candidates)
    )


def build_resumed_state(
    *, fresh: Mapping[str, Any], predecessor: Mapping[str, Any],
    preserved: Mapping[str, Any], completed_round: int,
    interrupted_round: int, predecessor_path: Path,
    cached_waves: Mapping[int, Mapping[str, Any]], attempted_ranks: Sequence[int],
) -> dict[str, Any]:
    value = full_resume.build_resumed_state(
        fresh=fresh, predecessor=predecessor, preserved=preserved,
        completed_round=completed_round, interrupted_round=interrupted_round,
        predecessor_path=predecessor_path,
    )
    value.update({
        "same_cohort_codefix_resume_contract": RESUME_CONTRACT,
        "partial_interrupted_round_credited": True,
        "cached_physical_wave_evidence_reused": True,
        "cached_predecessor_wave_count": len(cached_waves),
        "cached_predecessor_attempted_ranks": list(attempted_ranks),
    })
    return value


def run(args: argparse.Namespace) -> int:
    scene = str(args.scene)
    validate_partial_resume_contract()
    predecessor = load(args.predecessor_state)
    preserved, completed_round, interrupted_round = (
        full_resume.validate_predecessor(predecessor, scene)
    )
    predecessor_candidate_path, cached_waves, attempted_ranks = (
        validate_cached_round(
            predecessor, args.predecessor_state, interrupted_round,
        )
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
    if set(workers) != set(preserved["baseline_by_worker"]):
        raise RuntimeError("worker cohort changed during partial-round continuation")

    original_fresh_state = base.fresh_state
    original_measure = base.measure
    original_aggregate = base.aggregate_baseline
    original_atomic = base.atomic
    state_path = args.output_dir.resolve() / "algorithm_a_state.json"
    baseline_skipped = False
    candidate_equivalence_checked = False
    reused_wave_indexes: list[int] = []

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
            completed_round=completed_round, interrupted_round=interrupted_round,
            predecessor_path=args.predecessor_state,
            cached_waves=cached_waves, attempted_ranks=attempted_ranks,
        )

    def preserved_aggregate(
        rows: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if rows != preserved["baseline_by_worker"]:
            raise RuntimeError("preserved same-cohort baseline differs")
        return copy.deepcopy(preserved["canonical_baseline"])

    def partial_round_measure(**kwargs: Any):
        nonlocal baseline_skipped, candidate_equivalence_checked
        output = Path(kwargs["output"]).resolve()
        if not baseline_skipped and output.name == "formal_baseline":
            baseline_skipped = True
            ranking_log_root = (
                args.output_dir.resolve() / "plan_ranking_client_processes"
            )
            ranking_log_root.mkdir(parents=True, mode=0o700)
            for worker_id, worker in kwargs["workers"].items():
                worker["formal_client_log"] = str(
                    ranking_log_root / f"{worker_id}.jsonl"
                )
            return (
                copy.deepcopy(preserved["baseline_by_worker"]), kwargs["state"],
            )

        attempt_match = re.fullmatch(r"attempt_(\d{2})", output.name)
        wave_match = re.fullmatch(r"wave_(\d{3})", output.parent.name)
        round_match = re.fullmatch(r"round_(\d{3})", output.parent.parent.name)
        if attempt_match and wave_match and round_match:
            round_index = int(round_match.group(1))
            wave_index = int(wave_match.group(1))
            cached = cached_waves.get(wave_index)
            if round_index == interrupted_round and cached is not None:
                if int(attempt_match.group(1)) != 1:
                    raise RuntimeError("cached wave must be consumed on attempt 1")
                if not candidate_equivalence_checked:
                    regenerated = (
                        args.output_dir.resolve()
                        / f"round_{interrupted_round:03d}" / "candidates.json"
                    )
                    if not candidate_documents_equivalent(
                        predecessor_candidate_path, regenerated,
                    ):
                        raise RuntimeError("regenerated candidate set differs")
                    # Plan-probe hashes and other audit-only annotations can
                    # legitimately differ when EXPLAIN is recaptured.  The
                    # complete ranked executable vectors were verified above;
                    # retain the immutable predecessor candidate document on
                    # disk so the credited physical evidence and its candidate
                    # authority remain byte-for-byte aligned.
                    original_atomic(regenerated, load(predecessor_candidate_path))
                    candidate_equivalence_checked = True
                assignments = kwargs["assignments"]
                expected_workers = set(assignments)
                expected_ranks = sorted(
                    int(row["rank"]) for row in assignments.values()
                )
                if expected_ranks != sorted(cached["ranks"]):
                    raise RuntimeError("cached wave ranks differ")
                document = load(cached["batch_path"])
                rows = measurement_rows(document, expected_workers)
                for worker_id, assignment in assignments.items():
                    if int(rows[worker_id].get("rank") or 0) != int(
                        assignment["rank"]
                    ):
                        raise RuntimeError("cached worker rank differs")
                reused_document = copy.deepcopy(document)
                reused_document.update({
                    "same_cohort_partial_round_evidence_reused": True,
                    "predecessor_batch_result_path": str(cached["batch_path"]),
                    "predecessor_batch_result_sha256": cached["batch_sha256"],
                    "new_physical_measurement_invoked": False,
                    "optimizer_or_measurement_state_mutated": False,
                })
                original_atomic(output / "batch_result.json", reused_document)
                reused_wave_indexes.append(wave_index)
                return rows, kwargs["state"]
        return original_measure(**kwargs)

    def partial_round_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path == state_path and document.get("formal_started") is True:
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
            document["same_cohort_partial_round_resume_completed"] = True
            document["same_cohort_partial_round_resume"] = {
                "contract": RESUME_CONTRACT,
                "same_worker_cohort": True,
                "baseline_reused_without_replay": True,
                "incumbent_reference_reused_without_replay": True,
                "candidate_document_regenerated_and_verified": (
                    candidate_equivalence_checked
                ),
                "cached_physical_wave_evidence_reused": True,
                "cached_predecessor_wave_count": len(cached_waves),
                "cached_predecessor_attempted_ranks": list(attempted_ranks),
                "reused_wave_indexes": sorted(set(reused_wave_indexes)),
                "new_physical_work_starts_after_rank": max(attempted_ranks),
                "reference_refresh_trigger_unchanged": (
                    "two_consecutive_semantic_rejections"
                ),
                "technical_failures_count_as_rejections": False,
                "interrupted_round_index": interrupted_round,
                "completed_round_index": completed_round,
            }
        original_atomic(path, document)

    base.fresh_state = resumed_fresh_state
    base.aggregate_baseline = preserved_aggregate
    base.measure = partial_round_measure
    base.atomic = partial_round_atomic
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
