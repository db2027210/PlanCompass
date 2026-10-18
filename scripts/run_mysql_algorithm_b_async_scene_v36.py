#!/usr/bin/env python3
"""Run one MySQL Algorithm B scene with asynchronous outer progression."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_b_pg_outer_fresh_v35 as v35
from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as scheduler
from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import mysql_async_shared_prestart_authority_v1 as shared_prestart


base = v35.base
alg = base.alg
runtime = base.runtime
CLEAN_BOUNDARY = (
    base.HISTORY / "v35_formal_pg_outer_adaptive_20260819_attempt034"
)
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260819_v36"
    / "algorithm_b_async_contract.json"
)
COORDINATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260819_v64"
    / "coordination_contract.json"
)


def validate_clean_boundary(source: Path) -> dict[str, Any]:
    source = source.resolve()
    if source != CLEAN_BOUNDARY.resolve() or not source.is_dir():
        raise RuntimeError("v36 clean asynchronous boundary source differs")
    state_path = source / "run_state.json"
    state_sha = base._verified_sidecar(state_path)
    state = alg.load(state_path)
    marker = source / "STOPPED_NO_CREDIT_POLICY_SUPERSEDED.json"
    if (
        state.get("algorithm") != "B"
        or state.get("status") != "RUNNING"
        or state.get("stage") != "ALGORITHM_B_OUTER_001_ESTIMATING"
        or state.get("active_scenes") != list(base.FULL_SCENES)
        or any(int(value) != 0 for value in state["accepted_rounds"].values())
        or any(int(value) != 0 for value in state["rejected_outer_attempts"].values())
        or any(bool(value) for value in state["converged"].values())
        or not marker.is_file()
        or alg.load(marker).get("status") != "STOPPED_NO_CREDIT"
        or (source / "outer_001/predicted_rankings.json").exists()
        or any((source / "outer_001").glob("top5_batch_*"))
        or any((source / "outer_001").glob("*.decision.json"))
    ):
        raise RuntimeError("v36 clean asynchronous boundary state differs")

    baseline = source / "formal_independent_baseline/batch_result.json"
    baseline_rows = base._batch_rows(baseline, expected_count=8)
    baseline_doc = alg.load(baseline)
    if (
        baseline_doc.get("status") != "PASS"
        or baseline_doc.get("failures") not in ([], None)
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in baseline_rows.values()
        )
    ):
        raise RuntimeError("v36 reused fresh baseline differs")

    design_path = source / "calibration_design.json"
    design_sha = base._verified_sidecar(design_path)
    design = alg.load(design_path)
    if (
        design.get("status") != "PASS"
        or design.get("replay_budget_per_scene") != 15
        or set(design.get("scene_designs") or {}) != set(base.FULL_SCENES)
    ):
        raise RuntimeError("v36 reused calibration design differs")

    wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
    wave_evidence: list[dict[str, Any]] = []
    for rank in range(1, 16):
        path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        wave_rows[rank] = base._batch_rows(path, expected_count=4)
        wave_evidence.append({
            "wave": rank, "path": str(path), "sha256": alg.sha(path),
        })

    companion = source / "fresh_calibration/companion_audit.json"
    companion_sha = base._verified_sidecar(companion)
    if alg.load(companion).get("status") != "PASS":
        raise RuntimeError("v36 calibration companion audit differs")

    quality_gate_evidence = []
    for scene in base.FULL_SCENES:
        path = source / f"fresh_calibration/{scene}/fresh_model_quality_gate.json"
        digest = base._verified_sidecar(path)
        gate = alg.load(path)
        state_gate = (state.get("fresh_model_quality_gates") or {}).get(scene) or {}
        if (
            gate.get("preflight_soft_start") is not True
            or gate.get("optimizer_started") is not True
            or gate.get("calibration_replay_attempt_count") != 15
            or gate.get("usable_sample_count_including_baseline") != 16
            or state_gate.get("sha256") != digest
            or set(state_gate.get("resource_error_bounds") or {})
            != {"cpu", "memory", "io"}
        ):
            raise RuntimeError(f"{scene} v36 reused model gate differs")
        quality_gate_evidence.append({
            "scene": scene, "path": str(path), "sha256": digest,
        })

    return {
        "source": source,
        "state": state,
        "state_path": state_path,
        "state_sha256": state_sha,
        "baseline_path": baseline,
        "baseline_rows": baseline_rows,
        "baseline_sha256": alg.sha(baseline),
        "design_path": design_path,
        "design": design,
        "design_sha256": design_sha,
        "wave_rows": wave_rows,
        "wave_evidence": wave_evidence,
        "companion_path": companion,
        "companion_sha256": companion_sha,
        "failure_evidence_path": marker,
        "failure_evidence_sha256": alg.sha(marker),
        "quality_gate_evidence": quality_gate_evidence,
        "rankings_path": state_path,
        "rankings_sha256": state_sha,
        "rankings": {"active_scenes": list(base.FULL_SCENES)},
        "valid_batch_path": state_path,
        "valid_batch_sha256": state_sha,
        "valid_batch_rows": {},
        "invalid_batch_path": state_path,
        "invalid_batch_sha256": state_sha,
        "invalid_batch": {},
    }


def validate_completed_scene_technical_resume(source: Path) -> dict[str, Any]:
    """Validate an immutable single-scene boundary after a technical failure.

    Only completed outer decisions are inherited.  The interrupted outer's
    rankings and failure are provenance; none of its measurements receive
    model, acceptance, rejection, replay-history, or pruning credit.
    """
    source = source.expanduser().resolve()
    if source.parent != base.HISTORY.resolve() or not source.is_dir():
        raise RuntimeError("v36 technical-resume source root differs")
    state_path = source / "run_state.json"
    state_sha = base._verified_sidecar(state_path)
    state = alg.load(state_path)
    scenes = list(state.get("active_scenes") or [])
    completed_outer = int(state.get("outer_attempt") or 0)
    interrupted_outer = completed_outer + 1
    failure_match = re.fullmatch(
        rf"RuntimeError: Algorithm B outer {interrupted_outer} rank (\d+) "
        r"failed closed",
        str(state.get("error") or ""),
    )
    reset_failure_match = re.fullmatch(
        r"CalledProcessError: Command '\['docker', 'start', '([^']+)'\]' "
        r"returned non-zero exit status 1\.",
        str(state.get("error") or ""),
    )
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or len(scenes) != 1
        or scenes[0] not in base.FULL_SCENES
        or completed_outer < 1
        or (failure_match is None and reset_failure_match is None)
        or int((state.get("accepted_rounds") or {}).get(scenes[0], -1))
        + int((state.get("rejected_outer_attempts") or {}).get(scenes[0], -1))
        != completed_outer
        or bool((state.get("converged") or {}).get(scenes[0]))
    ):
        raise RuntimeError("v36 technical-resume state differs")
    scene = scenes[0]
    if reset_failure_match is not None:
        frozen_workers = {
            str(row["container"]): str(row["scene"])
            for row in alg.load(base.FREEZE)["workers"]
        }
        if (
            not scene.startswith("hybench_xp_")
            or frozen_workers.get(reset_failure_match.group(1)) != scene
        ):
            raise RuntimeError("v36 technical-resume reset target differs")

    baseline = source / "formal_independent_baseline/batch_result.json"
    baseline_rows = base._batch_rows(baseline, expected_count=2)
    if (
        {str(row.get("scene") or "") for row in baseline_rows.values()}
        != {scene}
        or (state.get("fresh_baseline") or {}).get("sha256")
        != alg.sha(baseline)
    ):
        raise RuntimeError("v36 technical-resume baseline differs")

    design_path = source / "calibration_design.json"
    design_sha = base._verified_sidecar(design_path)
    design = alg.load(design_path)
    if (
        design.get("status") != "PASS"
        or int(design.get("replay_budget_per_scene") or 0) != 15
        or scene not in (design.get("scene_designs") or {})
    ):
        raise RuntimeError("v36 technical-resume calibration design differs")
    wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
    wave_evidence: list[dict[str, Any]] = []
    for rank in range(1, 16):
        path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        wave_rows[rank] = base._batch_rows(path, expected_count=1)
        if {
            str(row.get("scene") or "") for row in wave_rows[rank].values()
        } != {scene}:
            raise RuntimeError(
                f"v36 technical-resume calibration wave {rank} differs"
            )
        wave_evidence.append({
            "wave": rank, "path": str(path), "sha256": alg.sha(path),
        })
    companion = source / "fresh_calibration/companion_audit.json"
    companion_sha = base._verified_sidecar(companion)
    if alg.load(companion).get("status") != "PASS":
        raise RuntimeError("v36 technical-resume companion audit differs")
    gate_path = source / f"fresh_calibration/{scene}/fresh_model_quality_gate.json"
    gate_sha = base._verified_sidecar(gate_path)
    gate = alg.load(gate_path)
    state_gate = (state.get("fresh_model_quality_gates") or {}).get(scene) or {}
    if (
        gate.get("preflight_soft_start") is not True
        or gate.get("optimizer_started") is not True
        or int(gate.get("calibration_replay_attempt_count") or 0) != 15
        or int(gate.get("usable_sample_count_including_baseline") or 0) != 16
        or state_gate.get("sha256") != gate_sha
    ):
        raise RuntimeError("v36 technical-resume model gate differs")

    completed_evaluations: dict[int, dict[str, list[dict[str, Any]]]] = {}
    completed_evidence: list[dict[str, Any]] = []
    restored = state.get("completed_outer_dynamic_support_restore") or {}
    restored_completed = int(restored.get("completed_outer_attempt") or 0)
    restored_root = Path(str(restored.get("source_run_root") or "")).resolve()
    if restored_completed:
        restored_state = restored_root / "run_state.json"
        if (
            restored_completed >= completed_outer
            or restored_root.parent != base.HISTORY.resolve()
            or base._verified_sidecar(restored_state)
            != str(restored.get("source_state_sha256") or "")
        ):
            raise RuntimeError("v36 chained technical-resume prefix differs")
    accepted_count = 0
    rejected_count = 0
    for outer in range(1, completed_outer + 1):
        decision_source = (
            restored_root if outer <= restored_completed else source
        )
        decision_path = (
            decision_source / f"outer_{outer:03d}/{scene}.decision.json"
        )
        decision_sha = base._verified_sidecar(decision_path)
        decision = alg.load(decision_path)
        evaluations = [
            copy.deepcopy(dict(row))
            for row in (decision.get("evaluations") or [])
            if isinstance(row, Mapping)
        ]
        if (
            decision.get("scene") != scene
            or int(decision.get("outer_attempt") or 0) != outer
            or not evaluations
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in evaluations
            )
        ):
            raise RuntimeError(
                f"v36 technical-resume completed outer {outer} differs"
            )
        accepted = decision.get("accepted") is True
        accepted_count += int(accepted)
        rejected_count += int(not accepted)
        completed_evaluations[outer] = {scene: evaluations}
        completed_evidence.append({
            "outer_attempt": outer,
            "scene": scene,
            "path": str(decision_path),
            "sha256": decision_sha,
            "valid_evaluation_count": len(evaluations),
            "accepted": accepted,
        })
    if (
        accepted_count
        != int((state.get("accepted_rounds") or {})[scene])
        or rejected_count
        != int((state.get("rejected_outer_attempts") or {})[scene])
    ):
        raise RuntimeError("v36 technical-resume decision accounting differs")

    interrupted_root = source / f"outer_{interrupted_outer:03d}"
    rankings_path = interrupted_root / "predicted_rankings.json"
    rankings_sha = base._verified_sidecar(rankings_path)
    rankings = alg.load(rankings_path)
    if (
        int(rankings.get("outer_attempt") or 0) != interrupted_outer
        or rankings.get("active_scenes") != [scene]
        or (interrupted_root / f"{scene}.decision.json").exists()
    ):
        raise RuntimeError("v36 interrupted outer boundary differs")
    if failure_match is not None:
        failed_rank = int(failure_match.group(1))
        failed_batch = interrupted_root / f"top5_batch_{failed_rank}/batch_result.json"
        base._verified_sidecar(failed_batch)
        technical = base.technical_candidate_failure_batch(
            alg.load(failed_batch), batch_path=failed_batch,
        )
        if technical is None:
            raise RuntimeError("v36 interrupted failure is not candidate-technical")
        failure_evidence_path = failed_batch
        invalid_batch = alg.load(failed_batch)
    else:
        failed_rank = 5
        completed_batches = [
            interrupted_root / f"top5_batch_{batch}/batch_result.json"
            for batch in (1, 2)
        ]
        for batch_path in completed_batches:
            rows = base._batch_rows(batch_path, expected_count=1)
            if any(
                row.get("hard_gate_passed") is not True
                for row in rows.values()
            ):
                raise RuntimeError("v36 pre-reset completed batch differs")
        failed_batch = interrupted_root / "top5_batch_3/batch_result.json"
        if failed_batch.exists() or failed_batch.with_name(
            failed_batch.name + ".sha256"
        ).exists():
            raise RuntimeError("v36 reset failure unexpectedly published a batch")
        failure_evidence_path = state_path
        invalid_batch = {
            "classification": "TECHNICAL_OUTER_RESET_FAILURE_DISCARDED",
            "container": reset_failure_match.group(1),
            "completed_outer_boundary_inherited": completed_outer,
            "interrupted_outer_attempt_discarded": interrupted_outer,
        }
        technical = {
            **invalid_batch,
            "accepted_state_credit_increment": 0,
            "candidate_state_mutation": False,
            "replay_history_mutation": False,
            "pruning_evidence_eligible": False,
            "rejected_outer_attempt_increment": 0,
            "convergence_mutation": False,
        }

    return {
        "source": source,
        "state": state,
        "state_path": state_path,
        "state_sha256": state_sha,
        "baseline_path": baseline,
        "baseline_rows": baseline_rows,
        "baseline_sha256": alg.sha(baseline),
        "design_path": design_path,
        "design": design,
        "design_sha256": design_sha,
        "wave_rows": wave_rows,
        "wave_evidence": wave_evidence,
        "companion_path": companion,
        "companion_sha256": companion_sha,
        "failure_evidence_path": failure_evidence_path,
        "failure_evidence_sha256": alg.sha(failure_evidence_path),
        "quality_gate_evidence": [{
            "scene": scene, "path": str(gate_path), "sha256": gate_sha,
        }],
        "rankings_path": rankings_path,
        "rankings_sha256": rankings_sha,
        "rankings": rankings,
        "valid_batch_path": rankings_path,
        "valid_batch_sha256": rankings_sha,
        "valid_batch_rows": {},
        "invalid_batch_path": failure_evidence_path,
        "invalid_batch_sha256": alg.sha(failure_evidence_path),
        "invalid_batch": invalid_batch,
        "completed_outer_attempt": completed_outer,
        "completed_outer_evaluations": completed_evaluations,
        "completed_outer_evidence": completed_evidence,
        "interrupted_outer_attempt": interrupted_outer,
        "interrupted_outer_rankings_path": rankings_path,
        "interrupted_outer_rankings_sha256": rankings_sha,
        "interrupted_outer_measurement_credit": False,
        "technical_failure": technical,
    }


def install_scene(scene: str) -> None:
    scheduler.SCENES = (scene,)
    runtime.SCENES = (scene,)
    runtime.predecessor.SCENES = (scene,)
    runtime.INDEPENDENT_SCENE_EXECUTION = True


def fresh_authority_shared():
    if len(base.SCENES) != 1:
        raise RuntimeError("asynchronous B authority requires exactly one scene")
    return shared_prestart.validate_under_shared_lease(
        coordination=runtime.coordination,
        r0mod=alg.r0mod,
        contract_path=base.isolation.CONTRACT_PATH,
        timeout_seconds=base.PRESTART_AUTHORITY_TIMEOUT_SECONDS,
        scene=base.SCENES[0],
    )


def state_document_v36(run_id: str) -> dict[str, Any]:
    document = v35.state_document_v35(run_id)
    document.update({
        "schema_version": "mysql_algorithm_b_async_scene_state_v1",
        "method": "mysql_algorithm_b_grid_inner_independent_async_outer_v36",
        "async_scene": base.SCENES[0] if len(base.SCENES) == 1 else None,
        "four_scene_wave_barrier": False,
        "other_scene_completion_required": False,
        "per_scene_outer_attempt_counter": True,
        "scene_may_advance_immediately_after_own_round": True,
        "completed_scene_companion_workload": False,
        "postgresql_cohort_barrier_compatibility": False,
        "clean_boundary_source": str(CLEAN_BOUNDARY),
        "fresh_baseline_reused_without_remeasurement": True,
        "top15_calibration_reused_without_remeasurement": True,
        "model_quality_search_reused_without_recomputation": True,
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
    })
    return document


def validate_contract() -> dict[str, Any]:
    if not CONTRACT.is_file() or CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v36 Algorithm B contract absent: {CONTRACT}")
    contract = alg.load(CONTRACT)
    coordination = contract.get("coordination") or {}
    technical = contract.get("technical_candidate_failure_policy") or {}
    technical_resume = contract.get("technical_resume_policy") or {}
    if (
        contract.get("release_version") != "v36"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("scene_may_advance_immediately") is not True
        or contract.get("recalibration_required") is not False
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or coordination.get("prestart_authority_lease") != "shared"
        or coordination.get("sibling_mysql_scene_wait_required") is not False
        or technical.get("postgresql_outer_semantics") is not True
        or technical.get("failed_candidate_excluded_without_outer_state_mutation")
        is not True
        or technical.get("valid_parallel_sibling_retained") is not True
        or technical.get("xp_adaptive_stop_drain_timeout_requires_artifact_proof")
        is not True
        or technical_resume.get("resume_only_from_last_completed_outer_boundary")
        is not True
        or technical_resume.get("interrupted_outer_measurement_credit") is not False
    ):
        raise RuntimeError("v36 Algorithm B asynchronous contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v36 Algorithm B source binding differs: {path}")
    return contract


def install() -> None:
    v35.install()
    base.isolation.CONTRACT_PATH = COORDINATION_CONTRACT
    base.RESUME_SOURCE_VALIDATOR = validate_clean_boundary
    base.REUSE_VERIFIED_BASELINE = True
    base.REUSE_HISTORICAL_OUTER_EVIDENCE = False
    base.REUSE_COMPLETED_MODEL_GATE = True
    base.ASYNC_COORDINATION_SOURCE_BINDINGS = {
        "device_scoped_coordination_sha256": alg.sha(
            ROOT / "scripts/mysql_cross_project_wave_coordination_v2.py"
        ),
    }
    base.PRESTART_AUTHORITY_TIMEOUT_SECONDS = 900.0
    base.RESUME_OUTER_ATTEMPT = 0
    base.RESUME_VALID_RANKS = ()
    base.RESUME_INVALID_RANKS = ()
    base.SCENE_SCOPE_INSTALLER = install_scene
    base.fresh_authority = fresh_authority_shared
    base.state_document = state_document_v36
    runtime.alg.measure_batch = single_measurement.measure_batch


def main() -> int:
    validate_contract()
    validate_clean_boundary(CLEAN_BOUNDARY)
    v35.validate_stop_drain()
    install()
    prepared_override_value = os.environ.get("MYSQL_B_TECHNICAL_PREPARED")
    accounts_override_value = os.environ.get("MYSQL_B_TECHNICAL_ACCOUNTS")
    if prepared_override_value or accounts_override_value:
        if not (prepared_override_value and accounts_override_value):
            raise RuntimeError("technical live authority overrides must be paired")
        prepared_override = Path(prepared_override_value).resolve()
        accounts_override = Path(accounts_override_value).resolve()
        prepared_document = alg.load(prepared_override)
        accounts_document = alg.load(accounts_override)
        if (
            prepared_override.parent.parent != base.HISTORY.resolve()
            or accounts_override.parent != prepared_override.parent
            or prepared_document.get("status") != "PREPARED"
            or prepared_document.get("freeze_sha256") != alg.sha(base.FREEZE)
            or len(prepared_document.get("workers") or []) != 8
            or accounts_document.get("status") != "PASS"
            or accounts_document.get("prepared_binding_sha256")
            != alg.sha(prepared_override)
            or len(accounts_document.get("workers") or []) != 8
        ):
            raise RuntimeError("technical live authority override differs")
        base.PREPARED = prepared_override
        base.ACCOUNTS = accounts_override
    try:
        resume_index = sys.argv.index("--resume-from") + 1
        requested_source = Path(sys.argv[resume_index]).expanduser().resolve()
    except (ValueError, IndexError):
        requested_source = CLEAN_BOUNDARY.resolve()
    requested_state_path = requested_source / "run_state.json"
    if requested_state_path.is_file():
        requested_state = alg.load(requested_state_path)
        if (
            requested_state.get("status") == "FAILED_CLOSED"
            and (
                str(requested_state.get("error") or "").startswith(
                    "RuntimeError: Algorithm B outer "
                )
                or str(requested_state.get("error") or "").startswith(
                    "CalledProcessError: Command '['docker', 'start'"
                )
            )
        ):
            completed_outer = int(requested_state.get("outer_attempt") or 0)
            if completed_outer < 1:
                raise RuntimeError(
                    "v36 technical resume lacks a completed outer boundary"
                )
            base.CONTINUATION_COMPLETED_OUTER_ATTEMPT = completed_outer
            base.RESUME_SOURCE_VALIDATOR = validate_completed_scene_technical_resume
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
