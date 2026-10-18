#!/usr/bin/env python3
"""Continue CPU Top4 B after rank-13 native-XP drain timeout.

Ranks 1-12 and the independently attributable valid rank-14/rank-15 lanes
are reused bit-for-bit.  Only failed rank 13 is physically replayed.  The
native runner's already-requested 300-second graceful drain is preserved.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql_workload_adapter_v2 as workload_adapter_v2
from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as split_base
from scripts import mysql_algorithm_b_fresh_parallel_4x_scheduler_v26 as scheduler
from scripts import mysql_cross_project_wave_coordination_v8 as gate_v8
from scripts import mysql_xp_post_reset_scene_scoped_settle_v1 as scene_settle
from scripts import run_mysql_algorithm_b_xp_top4_async_v6 as successor

HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v234_to_b_top4_fresh_20260826_attempt021_xp_cpu"
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v25"
)
VALID_PARTIAL_RANKS = (14, 15)
_partial_documents: dict[int, dict[str, Any]] = {}


def _core():
    return successor.predecessor


def _load_verified(path: Path) -> dict[str, Any]:
    core = _core()
    core._impl["_verified_sidecar"](path)
    return core._impl["alg"].load(path)


def _sha(path: Path) -> str:
    return _core()._impl["alg"].sha(path)


def _valid_rank_batch(path: Path, rank: int) -> dict[str, dict[str, Any]]:
    batch = _load_verified(path)
    rows = copy.deepcopy(dict(batch.get("measurements") or {}))
    if (
        batch.get("status") != "PASS"
        or batch.get("failures") not in ([], None)
        or len(rows) != 1
        or any(
            int(row.get("rank") or 0) != rank
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in rows.values()
        )
    ):
        raise RuntimeError(f"CPU B verified calibration rank {rank} differs")
    return rows


def _derive_valid_partial_documents() -> dict[int, dict[str, Any]]:
    root = SOURCE / "fresh_calibration/wave_13"
    joint_path = root / "physical_joint_wave_batch_result.json"
    provenance_path = root / "parallel_wave_provenance.json"
    rank13_path = root / "batch_result.json"
    joint = _load_verified(joint_path)
    provenance = _load_verified(provenance_path)
    rank13 = _load_verified(rank13_path)
    slot_ranks = {
        "xp_cpu_w1": 13,
        "xp_cpu_w2": 14,
        "xp_cpu_w3": 15,
        "xp_cpu_w4": 0,
    }
    failures = joint.get("failures") or []
    if (
        joint.get("status") != "FAILED_CLOSED"
        or provenance.get("status") != "PASS"
        or provenance.get("candidate_ranks") != [13, 14, 15]
        or {
            worker: int(row.get("candidate_rank", -1))
            for worker, row in (provenance.get("lane_mapping") or {}).items()
        } != slot_ranks
        or rank13.get("status") != "FAILED_CLOSED"
        or rank13.get("candidate_rank") != 13
        or len(failures) != 2
        or {str(row.get("task") or "") for row in failures}
        != {"xp_cpu_w1", "xp_cpu_w1_plan_capture"}
    ):
        raise RuntimeError("CPU B rank-13 attributable drain failure differs")
    documents = {
        rank: split_base._derived_document(
            joint,
            rank=rank,
            ranks=(13, 14, 15),
            active_scenes={"hybench_xp_cpu"},
            slot_ranks=slot_ranks,
            joint_path=joint_path,
            joint_sha256=_sha(joint_path),
        )
        for rank in VALID_PARTIAL_RANKS
    }
    for rank, document in documents.items():
        rows = document.get("measurements") or {}
        if (
            document.get("status") != "PASS"
            or document.get("failures") not in ([], None)
            or len(rows) != 1
            or any(
                int(row.get("rank") or 0) != rank
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in rows.values()
            )
        ):
            raise RuntimeError(f"CPU B partial valid rank {rank} differs")
    return documents


def validate_resume_source(source: Path) -> dict[str, Any]:
    global _partial_documents
    source = source.resolve()
    if source != SOURCE.resolve():
        raise RuntimeError("CPU B rank-13 continuation source differs")
    state_path = source / "run_state.json"
    state = _load_verified(state_path)
    if (
        state.get("algorithm") != "B"
        or state.get("async_scene") != "hybench_xp_cpu"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: Algorithm B calibration wave 13 failed closed"
        or int(state.get("outer_attempt") or 0) != 0
        or int((state.get("accepted_rounds") or {}).get("hybench_xp_cpu") or 0) != 0
        or int((state.get("rejected_outer_attempts") or {}).get("hybench_xp_cpu") or 0) != 0
    ):
        raise RuntimeError("CPU B rank-13 continuation state differs")
    baseline_path = source / "formal_independent_baseline/batch_result.json"
    baseline = _load_verified(baseline_path)
    baseline_rows = copy.deepcopy(dict(baseline.get("measurements") or {}))
    if baseline.get("status") != "PASS" or len(baseline_rows) != 4:
        raise RuntimeError("CPU B continuation baseline differs")
    design_path = source / "calibration_design.json"
    design = _load_verified(design_path)
    wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
    wave_evidence: list[dict[str, Any]] = []
    for rank in range(1, 13):
        path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        wave_rows[rank] = _valid_rank_batch(path, rank)
        wave_evidence.append({
            "rank": rank, "path": str(path), "sha256": _sha(path),
            "role": "completed_calibration_rank_reused",
        })
    _partial_documents = _derive_valid_partial_documents()
    for rank in VALID_PARTIAL_RANKS:
        scheduler._rank_cache[("calibration", rank)] = {
            "document": copy.deepcopy(_partial_documents[rank])
        }
        wave_evidence.append({
            "rank": rank,
            "path": str(source / "fresh_calibration/wave_13/physical_joint_wave_batch_result.json"),
            "sha256": _sha(source / "fresh_calibration/wave_13/physical_joint_wave_batch_result.json"),
            "role": "independently_attributable_valid_lane_reused",
        })
    placeholder = state_path
    return {
        "source": source,
        "state": {
            "params_by_scene": copy.deepcopy(state["params_by_scene"]),
            "ranges_by_scene": copy.deepcopy(state["ranges_by_scene"]),
            "active_by_scene": copy.deepcopy(state["active_by_scene"]),
        },
        "state_path": state_path,
        "state_sha256": _sha(state_path),
        "baseline_path": baseline_path,
        "baseline_rows": baseline_rows,
        "baseline_sha256": _sha(baseline_path),
        "design_path": design_path,
        "design": design,
        "design_sha256": _sha(design_path),
        "wave_rows": wave_rows,
        "wave_evidence": wave_evidence,
        "companion_path": placeholder,
        "companion_sha256": _sha(placeholder),
        "failure_evidence_path": placeholder,
        "failure_evidence_sha256": _sha(placeholder),
        "quality_gate_evidence": [],
        "rankings_path": placeholder,
        "rankings_sha256": _sha(placeholder),
        "rankings": {},
        "valid_batch_path": placeholder,
        "valid_batch_sha256": _sha(placeholder),
        "valid_batch_rows": {},
        "invalid_batch_path": placeholder,
        "invalid_batch_sha256": _sha(placeholder),
        "invalid_batch": {},
    }


def _rank13_only_assignments(**kwargs: Any):
    rankings = kwargs["rankings"]
    rank = int(kwargs["rank"])
    calibration = bool(rankings) and all(
        rows and "calibration_id" in rows[0] for rows in rankings.values()
    )
    if calibration and rank == 13:
        assignments, companions, slot_ranks = scheduler._single_assignments(**kwargs)
        scheduler._pending = {
            "rank": rank,
            "ranks": (13,),
            "active_scenes": set(rankings),
            "assignments": copy.deepcopy(assignments),
            "companions": set(companions),
            "slot_ranks": slot_ranks,
            "calibration": True,
        }
        return assignments, companions
    return scheduler._build_assignments_221(**kwargs)


def _run_hybench_with_300s_drain(*args: Any, **kwargs: Any) -> dict[str, Any]:
    control = kwargs.get("formal_control")
    if control is not None and int(control.get("graceful_quiescence_seconds", 0)) != 300:
        raise RuntimeError("CPU B rank-13 continuation drain authority differs")
    return workload_adapter_v2.run_hybench(*args, **kwargs)


def main() -> int:
    core = _core()
    core.install()
    successor.CONTRACT_ROOT = CONTRACT_ROOT
    successor.gate_v7 = gate_v8
    successor.install()
    original_configure = successor.configure_runtime
    original_install_scene = core._impl["install_scene"]
    original_state_document = core._impl["state_document"]
    original_atomic = core._impl["alg"].atomic

    continuation_audit = {
        "kind": "cpu_b_rank13_native_drain_timeout_retry_v1",
        "source_run_root": str(SOURCE),
        "completed_calibration_points_reused": list(range(1, 13)) + [14, 15],
        "failed_calibration_points_replayed": [13],
        "partial_failed_wave_results_reused": True,
        "partial_failed_wave_reuse_limited_to_valid_attributable_lanes": True,
        "accepted_or_rejected_outer_credit_reused": False,
        "graceful_quiescence_seconds": 300,
    }

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        core._impl["alg"].reset_xp = scene_settle.reset_xp
        core._impl["alg"].run_hybench = _run_hybench_with_300s_drain
        return contract

    def install_scene(scene: str) -> None:
        original_install_scene(scene)
        core._impl["runtime"]._build_single_candidate_assignments = (
            _rank13_only_assignments
        )

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value.setdefault("technical_continuation", {}).update(
            copy.deepcopy(continuation_audit)
        )
        return value

    def atomic(path: Path, value: Mapping[str, Any]) -> None:
        target = Path(path)
        document = value
        if isinstance(value, dict) and target.name == "run_state.json":
            value.setdefault("technical_continuation", {}).update(
                copy.deepcopy(continuation_audit)
            )
        elif (
            isinstance(value, dict)
            and target.name == "technical_continuation_manifest.json"
        ):
            value.update(copy.deepcopy(continuation_audit))
        original_atomic(target, document)

    core._impl["configure_runtime"] = configure_runtime
    core._impl["install_scene"] = install_scene
    core._impl["state_document"] = state_document
    core._impl["alg"].atomic = atomic
    core._impl["validate_resume_source"] = validate_resume_source
    core._impl["RESUME_CALIBRATION_WAVES"] = 12
    core._impl["REUSE_VERIFIED_BASELINE"] = True
    core._impl["alg"].reset_xp = scene_settle.reset_xp
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
