#!/usr/bin/env python3
"""Reusable completed-boundary continuation for single-scene Top4-B."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v12 as gate_v12
from scripts import run_mysql_algorithm_b_xp_top4_two_worker_v1 as two_worker_base


SOURCE: Path | None = None
SCENE = ""
EXPECTED_ERROR = ""
EXPECTED_ACCEPTED = 0
EXPECTED_REJECTED = 0
CALIBRATION_COMPLETED = 0
COMMITTED_OUTER = 0
PARTIAL_OUTER = 0
PARTIAL_RANKS: tuple[int, ...] = ()
RUNTIME_PREDECESSOR: Any = None
DYNAMIC_GATE: Any = gate_v12
CONTRACT_ROOT: Path | None = None
CONTRACT: Path | None = None
COORDINATION: Path | None = None


def install_runtime() -> Any:
    if (
        SOURCE is None
        or RUNTIME_PREDECESSOR is None
        or CONTRACT_ROOT is None
        or CONTRACT is None
        or COORDINATION is None
        or not SCENE
    ):
        raise RuntimeError("boundary continuation configuration is incomplete")
    RUNTIME_PREDECESSOR.CONTRACT_ROOT = CONTRACT_ROOT
    RUNTIME_PREDECESSOR.CONTRACT = CONTRACT
    RUNTIME_PREDECESSOR.COORDINATION = COORDINATION
    original_legacy_validate = two_worker_base.predecessor.validate_contract
    two_worker_base.predecessor.validate_contract = lambda: {
        "status": "SUPERSEDED_BY_COMPLETE_BOUNDARY_SUCCESSOR_BINDINGS",
        "runtime_or_candidate_validation_bypassed": False,
    }
    try:
        core = RUNTIME_PREDECESSOR.install_runtime()
    finally:
        two_worker_base.predecessor.validate_contract = original_legacy_validate
    impl = core._impl
    alg = impl["alg"]
    original_configure = impl["configure_runtime"]
    original_state_document = impl["state_document"]
    original_install_scene = impl["install_scene"]
    verified_partial: dict[int, tuple[Path, dict[str, Any], dict[str, dict[str, Any]]]] = {}

    def verified(path: Path) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular continuation evidence required: {path}")
        sidecar = path.with_name(path.name + ".sha256")
        if (
            not sidecar.is_file()
            or sidecar.is_symlink()
            or sidecar.read_text(encoding="ascii").split()[0] != alg.sha(path)
        ):
            raise RuntimeError(f"continuation sidecar differs: {path}")
        value = alg.load(path)
        if not isinstance(value, Mapping):
            raise RuntimeError(f"JSON object required: {path}")
        return dict(value)

    def reusable_batch(path: Path, rank: int) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        batch = verified(path)
        rows = copy.deepcopy(dict(batch.get("measurements") or {}))
        if (
            batch.get("status") != "PASS"
            or batch.get("failures") not in ([], None)
            or len(rows) != 1
            or any(
                row.get("scene") != SCENE
                or int(row.get("rank") or 0) != rank
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in rows.values()
            )
        ):
            raise RuntimeError(f"completed rank {rank} differs: {path}")
        return batch, rows

    def validate_resume_source(source: Path) -> dict[str, Any]:
        source = source.resolve()
        if source != SOURCE.resolve():
            raise RuntimeError(f"{SCENE} boundary continuation source differs")
        state_path = source / "run_state.json"
        source_state = verified(state_path)
        if not (
            source_state.get("algorithm") == "B"
            and source_state.get("async_scene") == SCENE
            and source_state.get("status") == "FAILED_CLOSED"
            and source_state.get("stage") == "FAILED"
            and source_state.get("error") == EXPECTED_ERROR
            and int(source_state.get("outer_attempt") or 0) == COMMITTED_OUTER
            and int((source_state.get("accepted_rounds") or {}).get(SCENE) or 0)
            == EXPECTED_ACCEPTED
            and int(
                (source_state.get("rejected_outer_attempts") or {}).get(SCENE) or 0
            ) == EXPECTED_REJECTED
        ):
            raise RuntimeError(f"{SCENE} committed state differs")

        baseline_path = source / "formal_independent_baseline/batch_result.json"
        baseline = verified(baseline_path)
        baseline_rows = copy.deepcopy(dict(baseline.get("measurements") or {}))
        if (
            baseline.get("status") != "PASS"
            or baseline.get("failures") not in ([], None)
            or len(baseline_rows) != 2
            or any(
                row.get("scene") != SCENE
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in baseline_rows.values()
            )
        ):
            raise RuntimeError(f"{SCENE} continuation baseline differs")
        design_path = source / "calibration_design.json"
        design = verified(design_path)
        wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
        wave_evidence: list[dict[str, Any]] = []
        for rank in range(1, CALIBRATION_COMPLETED + 1):
            path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
            _batch, rows = reusable_batch(path, rank)
            wave_rows[rank] = rows
            wave_evidence.append({
                "rank": rank,
                "path": str(path),
                "sha256": alg.sha(path),
                "role": "completed_calibration_measurement_reused",
            })

        decisions: dict[int, dict[str, Any]] = {}
        if COMMITTED_OUTER and CALIBRATION_COMPLETED != 15:
            raise RuntimeError("committed outer requires complete calibration")
        for outer in range(1, COMMITTED_OUTER + 1):
            path = source / f"outer_{outer:03d}/{SCENE}.decision.json"
            decision = verified(path)
            evaluations = list(decision.get("evaluations") or [])
            if (
                int(decision.get("outer_attempt") or 0) != outer
                or decision.get("scene") != SCENE
                or not evaluations
                or len(evaluations)
                != int(decision.get("valid_real_evaluation_count") or 0)
            ):
                raise RuntimeError(f"{SCENE} committed Outer {outer} differs")
            update = verified(
                source / f"outer_{outer:03d}/online_resource_model_update.json"
            )
            scene_update = (update.get("scene_updates") or {}).get(SCENE) or {}
            if int(scene_update.get("added_count") or 0) != len(evaluations):
                raise RuntimeError(f"{SCENE} model update Outer {outer} differs")
            decisions[outer] = decision

        rankings_path = state_path
        rankings: dict[str, Any] = {}
        if PARTIAL_RANKS:
            if (
                CALIBRATION_COMPLETED != 15
                or PARTIAL_OUTER != COMMITTED_OUTER + 1
                or PARTIAL_RANKS != tuple(range(1, max(PARTIAL_RANKS) + 1))
                or len(PARTIAL_RANKS) < 2
            ):
                raise RuntimeError("partial outer continuation shape differs")
            rankings_path = source / f"outer_{PARTIAL_OUTER:03d}/predicted_rankings.json"
            rankings = verified(rankings_path)
            if (
                int(rankings.get("outer_attempt") or 0) != PARTIAL_OUTER
                or len((rankings.get("selected_top_k") or {}).get(SCENE) or []) != 5
            ):
                raise RuntimeError(f"{SCENE} partial outer rankings differ")
            for rank in PARTIAL_RANKS:
                path = source / f"outer_{PARTIAL_OUTER:03d}/top5_batch_{rank}/batch_result.json"
                batch, rows = reusable_batch(path, rank)
                verified_partial[rank] = (path, batch, rows)

        initial_params, initial_ranges, initial_active = impl["parameter_state"]()
        placeholder = state_path
        rank1 = verified_partial.get(1)
        rank2 = verified_partial.get(2)
        return {
            "source": source,
            "state": {
                "params_by_scene": initial_params,
                "ranges_by_scene": initial_ranges,
                "active_by_scene": initial_active,
            },
            "committed_state": source_state,
            "committed_decisions": decisions,
            "state_path": state_path,
            "state_sha256": alg.sha(state_path),
            "baseline_path": baseline_path,
            "baseline_rows": baseline_rows,
            "baseline_sha256": alg.sha(baseline_path),
            "design_path": design_path,
            "design": design,
            "design_sha256": alg.sha(design_path),
            "wave_rows": wave_rows,
            "wave_evidence": wave_evidence,
            "quality_gate_evidence": [],
            "rankings_path": rankings_path,
            "rankings_sha256": alg.sha(rankings_path),
            "rankings": rankings,
            "valid_batch_path": rank1[0] if rank1 else placeholder,
            "valid_batch_sha256": alg.sha(rank1[0]) if rank1 else alg.sha(placeholder),
            "valid_batch_rows": rank1[2] if rank1 else {},
            "invalid_batch_path": rank2[0] if rank2 else placeholder,
            "invalid_batch_sha256": alg.sha(rank2[0]) if rank2 else alg.sha(placeholder),
            "invalid_batch": rank2[1] if rank2 else {},
            "failure_evidence_path": state_path,
            "failure_evidence_sha256": alg.sha(state_path),
            "companion_path": state_path,
            "companion_sha256": alg.sha(state_path),
        }

    def restore_committed_state(**kwargs: Any) -> None:
        if CALIBRATION_COMPLETED != 15:
            return
        source_resume = kwargs["source_resume"]
        source_state = copy.deepcopy(source_resume["committed_state"])
        state = kwargs["state"]
        params = kwargs["params_by_scene"]
        ranges = kwargs["ranges_by_scene"]
        active = kwargs["active_by_scene"]
        incumbents = kwargs["incumbents"]
        models = kwargs["models"]
        replayed = kwargs["replayed"]
        negatives = kwargs["soft_negative_counts"]
        params[SCENE] = copy.deepcopy(source_state["params_by_scene"][SCENE])
        ranges[SCENE] = copy.deepcopy(source_state["ranges_by_scene"][SCENE])
        active[SCENE] = copy.deepcopy(source_state["active_by_scene"][SCENE])
        incumbents[SCENE] = copy.deepcopy(source_state["incumbents"][SCENE])
        for key in (
            "accepted_rounds", "rejected_outer_attempts",
            "post_target_stability_confirmation_counts", "acceptance_tier",
            "consecutive_failed_rounds", "interval_stable_rounds",
            "stable_intervals", "converged", "incumbent_centered_forced_shrinks",
        ):
            state[key][SCENE] = copy.deepcopy(source_state[key][SCENE])
        replayed[SCENE].update(
            source_state.get("replayed_param_keys_by_scene", {}).get(SCENE, [])
        )
        negatives[SCENE].update(copy.deepcopy(
            source_state.get("soft_negative_candidate_counts_by_scene", {}).get(
                SCENE, {}
            )
        ))
        for outer in range(1, COMMITTED_OUTER + 1):
            evaluations = sorted(
                source_resume["committed_decisions"][outer]["evaluations"],
                key=lambda row: int(row["rank"]),
            )
            for row in evaluations:
                payload = impl["measurement_payload"](row)
                models[SCENE].add(row["params"], payload["resource"], payload)
            impl["update_resource_model_from_formal_evaluations"](
                scene=SCENE, model=models[SCENE], evaluations=evaluations,
                outer_attempt=outer,
            )
        state["outer_attempt"] = COMMITTED_OUTER
        state["params_by_scene"] = copy.deepcopy(params)
        state["ranges_by_scene"] = copy.deepcopy(ranges)
        state["active_by_scene"] = copy.deepcopy(active)
        state["incumbents"] = copy.deepcopy(incumbents)
        state["replayed_param_keys_by_scene"] = {SCENE: sorted(replayed[SCENE])}
        state["soft_negative_candidate_counts_by_scene"] = copy.deepcopy(negatives)
        if COMMITTED_OUTER:
            state["last_online_resource_model_update"] = copy.deepcopy(
                source_state["last_online_resource_model_update"]
            )
        state["completed_boundary_continuation"] = {
            "source_run_root": str(SOURCE),
            "committed_outer_state_reused_through": COMMITTED_OUTER,
            "completed_partial_outer_ranks_reused": list(PARTIAL_RANKS),
            "failed_measurement_reused": False,
            "accepted_or_rejected_credit_for_partial_outer": 0,
        }
        alg.atomic(kwargs["state_path"], state)

    def install_scene(scene: str) -> None:
        original_install_scene(scene)
        if not PARTIAL_RANKS or max(PARTIAL_RANKS) <= 2:
            return
        runtime = impl["runtime"]
        original_measure = runtime._measure_batch_with_fair_coordination

        def measure_with_partial_reuse(**kwargs: Any):
            output = Path(kwargs["output"])
            match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
            if match and int(match.group(1)) == PARTIAL_OUTER:
                rank = int(match.group(2))
                cached = verified_partial.get(rank)
                if cached is not None and rank >= 3:
                    impl["_mirror_batch"](cached[0], output / "batch_result.json")
                    state = kwargs["state"]
                    state["stage"] = f"{kwargs['stage_prefix']}_RESTORED_VERIFIED"
                    alg.atomic(Path(kwargs["state_path"]), state)
                    return copy.deepcopy(cached[2]), state
            return original_measure(**kwargs)

        runtime._measure_batch_with_fair_coordination = measure_with_partial_reuse

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        alg.r0mod.dynamic_gate = DYNAMIC_GATE
        impl["no_lease_runtime"].coordination = DYNAMIC_GATE
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["completed_boundary_continuation_release"] = {
            "source_run_root": str(SOURCE),
            "scene": SCENE,
            "completed_calibration_waves_reused": CALIBRATION_COMPLETED,
            "committed_outer_state_reused_through": COMMITTED_OUTER,
            "completed_partial_outer_ranks_reused": list(PARTIAL_RANKS),
            "failed_measurement_reused": False,
            "candidate_replayed_due_to_controller_failure": False,
            "dynamic_gate_source_path": str(Path(DYNAMIC_GATE.__file__).resolve()),
            "dynamic_gate_source_sha256": alg.sha(
                Path(DYNAMIC_GATE.__file__).resolve()
            ),
        }
        return value

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["validate_resume_source"] = validate_resume_source
    impl["validate_intermediate_failure"] = lambda: {
        "state_path": SOURCE / "run_state.json",
        "state_sha256": alg.sha(SOURCE / "run_state.json"),
        "state": {},
    }
    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    impl["install_scene"] = install_scene
    impl["REUSE_VERIFIED_BASELINE"] = True
    impl["RESUME_CALIBRATION_WAVES"] = CALIBRATION_COMPLETED
    impl["REUSE_HISTORICAL_OUTER_EVIDENCE"] = bool(PARTIAL_RANKS)
    impl["RESUME_OUTER_ATTEMPT"] = PARTIAL_OUTER if PARTIAL_RANKS else 0
    impl["RESUME_VALID_RANKS"] = PARTIAL_RANKS
    impl["RESUME_INVALID_RANKS"] = ()
    impl["INITIAL_OUTER_ATTEMPT"] = COMMITTED_OUTER
    impl["POST_CALIBRATION_STATE_TRANSFORM"] = restore_committed_state
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())
