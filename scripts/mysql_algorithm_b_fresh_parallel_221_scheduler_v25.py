#!/usr/bin/env python3
"""Fresh v25 Algorithm B frozen two-lane Top-5 replay scheduler.

The predecessor established the independent baseline, 15-point calibration,
and outer-1 Top-5 identities.  Its outer-1 physical replays are complete but
the controller stopped before committing a decision because the shared
objective API added the required ``raw`` argument.  This successor:

* reuses outer-1 ranks 1/3/4/5 bit-for-bit and preserves rank 2 as an invalid
  formal timeout;
* supplies the resource-only score payload required by the current API; and
* executes later Top-5 candidates as deterministic ``[1,2], [3,4], [5]``
  waves on the frozen w1/w2 lanes.

The physical two-rank result is retained verbatim.  Rank-specific derived
batch documents keep failures isolated by lane; any failure that cannot be
attributed to exactly one lane remains attached to both ranks and therefore
fails closed.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_resume_outer_rank3_identity_v7
    as predecessor,
)


alg = predecessor.alg
runtime = predecessor.runtime
SCENES = tuple(predecessor.SCENES)
HISTORY = predecessor.HISTORY
OUTER1_REPLAY_SOURCE = (
    HISTORY
    / "v8_formal_independent_outer_rank3_identity_resume_v63_20260818_attempt001"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v9"
    / "algorithm_b_parallel_221_contract.json"
)
FORMAL_CANDIDATE_BATCHES = [[1, 2, 3, 4], [5]]
RESUME_ERROR = (
    "TypeError: FreshBaselineObjective.score() missing 1 required positional "
    "argument: 'raw'"
)
RESTORE_HISTORICAL_OUTER1_RANKS = False

_ORIGINAL_ASSIGNMENTS = runtime._build_single_candidate_assignments
_ORIGINAL_MEASURE = runtime._measure_batch_with_fair_coordination
_ORIGINAL_SCORE = predecessor.FreshBaselineObjective.score
_ORIGINAL_STATE_DOCUMENT = predecessor.state_document

_pending: dict[str, Any] = {}
_rank_cache: dict[tuple[int, int], dict[str, Any]] = {}


def _regular_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON evidence required: {path}")
    predecessor._verified_sidecar(path)
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_outer1_replay_source() -> dict[int, dict[str, Any]]:
    """Prove the predecessor stopped after complete replay and before decision."""
    state = _regular_json(OUTER1_REPLAY_SOURCE / "run_state.json")
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != RESUME_ERROR
        or int(state.get("outer_attempt") or 0) != 0
        or any(int(value) != 0 for value in
               (state.get("accepted_rounds") or {}).values())
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or any((OUTER1_REPLAY_SOURCE / "outer_001").glob("*.decision.json"))
    ):
        raise RuntimeError("outer-1 score-interface failure boundary differs")

    rankings = _regular_json(
        OUTER1_REPLAY_SOURCE / "outer_001/predicted_rankings.json"
    )
    selected = rankings.get("selected_top_k") or {}
    if (
        rankings.get("status") != "PASS"
        or rankings.get("outer_attempt") != 1
        or rankings.get("active_scenes") != list(SCENES)
        or set(selected) != set(SCENES)
        or any(len(rows) != 5 for rows in selected.values())
    ):
        raise RuntimeError("outer-1 Top-5 replay identity differs")

    batches: dict[int, dict[str, Any]] = {}
    for rank in range(1, 6):
        path = (
            OUTER1_REPLAY_SOURCE
            / f"outer_001/top5_batch_{rank}/batch_result.json"
        )
        document = _regular_json(path)
        if rank == 2:
            if not predecessor.formally_infeasible_candidate_batch(document):
                raise RuntimeError("outer-1 rank 2 is not the preserved timeout")
        else:
            rows = predecessor._batch_rows(path, expected_count=4)
            if any(int(row.get("rank") or 0) != rank for row in rows.values()):
                raise RuntimeError(f"outer-1 rank {rank} measurement identity differs")
        batches[rank] = {
            "path": path,
            "sha256": alg.sha(path),
            "document": document,
        }
    return batches


def _score_with_resource_payload(
    self: Any, resource: Any, raw: Any | None = None,
) -> float:
    """Bridge the API change without allowing QPS into Resource Score."""
    if raw is None:
        raw = {"db_qps": float(self.baseline.db_qps)}
    return float(_ORIGINAL_SCORE(self, resource, raw))


def _dual_assignments(
    *, rankings: Mapping[str, list[dict[str, Any]]],
    params_by_scene: Mapping[str, Mapping[str, float]],
    workers: Mapping[str, Mapping[str, Any]], ranks: tuple[int, ...],
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, int]]:
    assignments: dict[str, dict[str, Any]] = {}
    companions: set[str] = set()
    slot_ranks: dict[str, int] = {}
    for scene in SCENES:
        scene_workers = sorted(
            worker_id for worker_id, worker in workers.items()
            if worker["scene"] == scene
        )
        if len(scene_workers) != len(ranks):
            raise RuntimeError(f"{scene} frozen parallel-worker cardinality differs")
        for slot, rank in enumerate(ranks):
            worker_id = scene_workers[slot]
            rows = rankings.get(scene) or []
            if rank <= len(rows):
                slot_ranks[worker_id] = rank
                row = rows[rank - 1]
                assignment = {
                    "rank": rank,
                    "params": copy.deepcopy(row["optimized_params"]),
                    "candidate_physical_plan_sha256": (
                        row.get("resource_model", {}).get(
                            "candidate_plan_sha256"
                        )
                    ),
                }
            else:
                slot_ranks[worker_id] = 0
                assignment = {
                    "rank": 0,
                    "params": copy.deepcopy(dict(params_by_scene[scene])),
                }
                companions.add(worker_id)
            assignments[worker_id] = assignment
    return assignments, companions, slot_ranks


def _build_assignments_221(**kwargs: Any):
    """Build ranks 1-4 on four lanes, with rank 5 serialized."""
    global _pending
    rankings = kwargs["rankings"]
    rank = int(kwargs["rank"])
    calibration = bool(rankings) and all(
        rows and "calibration_id" in rows[0] for rows in rankings.values()
    )
    position_in_top5 = (rank - 1) % 5 + 1
    if calibration or position_in_top5 in {2, 3, 4, 5}:
        assignments, companions = _ORIGINAL_ASSIGNMENTS(**kwargs)
        _pending = {
            "rank": rank,
            "ranks": (rank,),
            "active_scenes": set(rankings),
            "assignments": copy.deepcopy(assignments),
            "companions": set(companions),
            "slot_ranks": {
                worker_id: rank for worker_id in assignments
            },
            "calibration": calibration,
        }
        return assignments, companions

    ranks = (rank, rank + 1, rank + 2, rank + 3)
    assignments, companions, slot_ranks = _dual_assignments(
        rankings=rankings,
        params_by_scene=kwargs["params_by_scene"],
        workers=kwargs["workers"],
        ranks=ranks,
    )
    _pending = {
        "rank": rank,
        "ranks": ranks,
        "active_scenes": set(rankings),
        "assignments": copy.deepcopy(assignments),
        "companions": set(companions),
        "slot_ranks": slot_ranks,
        "calibration": False,
    }
    return assignments, companions


def _failure_ranks(
    failure: Mapping[str, Any], slot_ranks: Mapping[str, int],
    ranks: tuple[int, ...],
) -> set[int]:
    task = str(failure.get("task") or "")
    error = str(failure.get("error") or "")
    matched: set[int] = set()
    for worker_id, rank in slot_ranks.items():
        pair = f"pair_{worker_id[-1]}" if worker_id.startswith(
            ("job_", "tpch_")
        ) else None
        if (
            task == worker_id
            or task.startswith(worker_id + "_")
            or (pair is not None and task == pair)
            or worker_id in error
            or (pair is not None and pair in error)
        ):
            matched.add(int(rank))
    return matched if matched else set(ranks)


def _derived_document(
    joint: Mapping[str, Any], *, rank: int, ranks: tuple[int, ...],
    active_scenes: set[str], slot_ranks: Mapping[str, int],
    joint_path: Path, joint_sha256: str,
) -> dict[str, Any]:
    all_measurements = dict(joint.get("measurements") or {})
    slot_rows = {
        worker_id: copy.deepcopy(row)
        for worker_id, row in all_measurements.items()
        if int(slot_ranks.get(worker_id, -1)) == rank
    }
    active_rows = {
        worker_id: row for worker_id, row in slot_rows.items()
        if str(row.get("scene") or "") in active_scenes
    }
    companion_rows = {
        worker_id: row for worker_id, row in slot_rows.items()
        if worker_id not in active_rows
    }
    failures = [
        copy.deepcopy(dict(failure))
        for failure in (joint.get("failures") or [])
        if rank in _failure_ranks(failure, slot_ranks, ranks)
    ]
    slot_hard_pass = bool(slot_rows) and all(
        row.get("hard_gate_passed") is True
        and row.get("coverage_complete") is True
        for row in slot_rows.values()
    )
    return {
        "schema_version": "mysql_four_scene_algorithm_b_parallel_rank_batch_v1",
        "status": "PASS" if not failures and slot_hard_pass else "FAILED_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "elapsed_seconds": float(joint.get("elapsed_seconds") or 0.0),
        "measurements": active_rows,
        "companion_measurements": companion_rows,
        "failures": failures,
        "candidate_rank": rank,
        "physical_candidate_wave": list(ranks),
        "physical_joint_batch_path": str(joint_path),
        "physical_joint_batch_sha256": joint_sha256,
        "derived_without_additional_workload": True,
        "full_slot_workload_hard_gate_passed": slot_hard_pass,
        "candidate_measurement_data_cross_rank_mixing": False,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"fresh derived artifact required: {path}")
    alg.atomic(path, value)


def _split_physical_wave(
    *, output: Path, measured: Mapping[str, Mapping[str, Any]],
    ranks: tuple[int, ...], active_scenes: set[str],
    slot_ranks: Mapping[str, int],
) -> dict[int, dict[str, Any]]:
    batch_path = output / "batch_result.json"
    joint = _regular_json(batch_path)
    if set(joint.get("measurements") or {}) != set(measured):
        raise RuntimeError("physical two-rank measurement cardinality differs")
    joint_path = output / "physical_joint_wave_batch_result.json"
    shutil.copy2(batch_path, joint_path)
    shutil.copy2(
        batch_path.with_name(batch_path.name + ".sha256"),
        joint_path.with_name(joint_path.name + ".sha256"),
    )
    joint_sha = alg.sha(joint_path)
    batch_path.unlink()
    batch_path.with_name(batch_path.name + ".sha256").unlink()
    documents = {
        rank: _derived_document(
            joint, rank=rank, ranks=ranks, active_scenes=active_scenes,
            slot_ranks=slot_ranks, joint_path=joint_path,
            joint_sha256=joint_sha,
        )
        for rank in ranks
    }
    alg.atomic(batch_path, documents[ranks[0]])
    _write_json(output / "parallel_wave_provenance.json", {
        "schema_version": "mysql_algorithm_b_parallel_221_wave_provenance_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "candidate_ranks": list(ranks),
        "lane_mapping": {
            worker_id: {
                "candidate_rank": int(slot_ranks[worker_id]),
                "scene": str(row.get("scene") or ""),
            }
            for worker_id, row in measured.items()
        },
        "physical_joint_batch_path": str(joint_path),
        "physical_joint_batch_sha256": joint_sha,
        "rank_batch_paths": {
            **{
                str(candidate_rank): (
                    str(batch_path) if candidate_rank == ranks[0] else None
                )
                for candidate_rank in ranks
            },
        },
        "fixed_candidate_batches": FORMAL_CANDIDATE_BATCHES,
        "failure_attribution_unknown_fails_both_ranks": True,
        "cross_rank_measurement_mixing": False,
    })
    return documents


def _materialize_cached_rank(
    *, output: Path, cached: Mapping[str, Any], state_path: Path,
    state: dict[str, Any], stage_prefix: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    document = copy.deepcopy(dict(cached["document"]))
    _write_json(output / "batch_result.json", document)
    _write_json(output / "parallel_wave_provenance.json", {
        "schema_version": "mysql_algorithm_b_parallel_221_derived_rank_v1",
        "status": "PASS",
        "candidate_rank": int(document["candidate_rank"]),
        "physical_candidate_wave": list(document["physical_candidate_wave"]),
        "physical_joint_batch_path": document["physical_joint_batch_path"],
        "physical_joint_batch_sha256": document["physical_joint_batch_sha256"],
        "derived_without_additional_workload": True,
        "cross_rank_measurement_mixing": False,
    })
    state["stage"] = f"{stage_prefix}_RESTORED_FROM_PARALLEL_WAVE"
    state["parallel_221_last_derived_rank"] = int(document["candidate_rank"])
    alg.atomic(state_path, state)
    return copy.deepcopy(dict(document["measurements"])), state


def _filter_single_rank(
    *, output: Path, measured: Mapping[str, Mapping[str, Any]], rank: int,
    active_scenes: set[str], slot_ranks: Mapping[str, int],
) -> dict[str, dict[str, Any]]:
    batch_path = output / "batch_result.json"
    joint = _regular_json(batch_path)
    active_rows = {
        worker_id: copy.deepcopy(dict(row))
        for worker_id, row in measured.items()
        if str(row.get("scene") or "") in active_scenes
    }
    companion_rows = {
        worker_id: copy.deepcopy(dict(row))
        for worker_id, row in measured.items()
        if worker_id not in active_rows
    }
    if companion_rows:
        preserved = output / "physical_single_wave_batch_result.json"
        shutil.copy2(batch_path, preserved)
        shutil.copy2(
            batch_path.with_name(batch_path.name + ".sha256"),
            preserved.with_name(preserved.name + ".sha256"),
        )
        batch_path.unlink()
        batch_path.with_name(batch_path.name + ".sha256").unlink()
        derived = copy.deepcopy(joint)
        derived["measurements"] = active_rows
        derived["companion_measurements"] = companion_rows
        derived["candidate_rank"] = rank
        derived["physical_single_wave_batch_path"] = str(preserved)
        derived["physical_single_wave_batch_sha256"] = alg.sha(preserved)
        alg.atomic(batch_path, derived)
    return active_rows


def _measure_221(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is None or _pending.get("calibration") is True:
        return _ORIGINAL_MEASURE(**kwargs)
    outer = int(match.group(1))
    rank = int(match.group(2))
    state = kwargs["state"]
    state_path = Path(kwargs["state_path"])
    stage_prefix = str(kwargs["stage_prefix"])

    if RESTORE_HISTORICAL_OUTER1_RANKS and outer == 1 and rank in {3, 4, 5}:
        source = _OUTER1_BATCHES[rank]["path"]
        predecessor._mirror_batch(source, output / "batch_result.json")
        document = alg.load(output / "batch_result.json")
        state["stage"] = f"{stage_prefix}_RESTORED_VERIFIED"
        state["outer1_replay_resume_source"] = str(OUTER1_REPLAY_SOURCE)
        alg.atomic(state_path, state)
        return copy.deepcopy(dict(document["measurements"])), state

    position_in_top5 = (rank - 1) % 5 + 1
    if position_in_top5 in {2, 3, 4}:
        key = (outer, rank)
        if key not in _rank_cache:
            raise RuntimeError(f"parallel wave cache absent for outer {outer} rank {rank}")
        return _materialize_cached_rank(
            output=output, cached=_rank_cache.pop(key), state_path=state_path,
            state=state, stage_prefix=stage_prefix,
        )

    measured, state = _ORIGINAL_MEASURE(**kwargs)
    active_scenes = set(_pending["active_scenes"])
    if position_in_top5 == 1:
        ranks = tuple(int(value) for value in _pending["ranks"])
        if ranks != (rank, rank + 1, rank + 2, rank + 3):
            raise RuntimeError("parallel candidate wave identity differs")
        documents = _split_physical_wave(
            output=output, measured=measured, ranks=ranks,
            active_scenes=active_scenes,
            slot_ranks=_pending["slot_ranks"],
        )
        for cached_rank in ranks[1:]:
            _rank_cache[(outer, cached_rank)] = {
                "document": documents[cached_rank],
            }
        state["formal_candidate_batches"] = copy.deepcopy(
            FORMAL_CANDIDATE_BATCHES
        )
        state["parallel_221_last_physical_wave"] = list(ranks)
        alg.atomic(state_path, state)
        return copy.deepcopy(dict(documents[rank]["measurements"])), state

    filtered = _filter_single_rank(
        output=output, measured=measured, rank=rank,
        active_scenes=active_scenes, slot_ranks=_pending["slot_ranks"],
    )
    return filtered, state


def _state_document_v9(run_id: str) -> dict[str, Any]:
    document = _ORIGINAL_STATE_DOCUMENT(run_id)
    document["technical_continuation"].update({
        "kind": "post_outer1_score_interface_parallel_221_resume_v9",
        "outer1_complete_replays_reused": [1, 3, 4, 5],
        "outer1_invalid_timeout_preserved": [2],
        "outer1_decision_state_reused": False,
        "score_api_raw_payload_supplied": True,
        "qps_used_for_real_resource_score": False,
        "parallel_scheduler_contract_path": str(SCHEDULER_CONTRACT),
        "parallel_scheduler_contract_sha256": alg.sha(SCHEDULER_CONTRACT),
    })
    document["formal_candidate_batches"] = copy.deepcopy(FORMAL_CANDIDATE_BATCHES)
    document["candidate_parallelism"] = 4
    document["parallel_lane_mapping"] = {
        "rank_1": "w1", "rank_2": "w2",
        "rank_3": "w3", "rank_4": "w4",
        "final_rank": "w1",
    }
    document["outer1_replay_resume_source"] = str(OUTER1_REPLAY_SOURCE)
    document["outer1_replay_resume_source_state_sha256"] = alg.sha(
        OUTER1_REPLAY_SOURCE / "run_state.json"
    )
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def install() -> None:
    predecessor.FreshBaselineObjective.score = _score_with_resource_payload
    runtime._build_single_candidate_assignments = _build_assignments_221
    runtime._measure_batch_with_fair_coordination = _measure_221
    predecessor.state_document = _state_document_v9


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    known, _remaining = parser.parse_known_args()
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed parallel scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != FORMAL_CANDIDATE_BATCHES
        or int(contract.get("candidate_parallelism") or 0) != 2
    ):
        raise RuntimeError("parallel 2+2+1 scheduler contract differs")
    # The predecessor parser and its source validation remain authoritative.
    # This pre-parse only makes the v9 run identity explicit in early errors.
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", known.run_id) is None:
        raise RuntimeError("unsafe run id")
    install()
    return predecessor.main()


_OUTER1_BATCHES = validate_outer1_replay_source()


if __name__ == "__main__":
    raise SystemExit(main())
