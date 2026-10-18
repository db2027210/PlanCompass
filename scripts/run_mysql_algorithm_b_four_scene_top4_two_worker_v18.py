#!/usr/bin/env python3
"""Resume TPC-H Top4-B at Outer 6 after a control-plane gate race.

Outer 1-5 state and the completed Outer-6 ranks 1-2 are verified and reused.
No candidate is remeasured merely because the predecessor failed before rank 3.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v12 as gate_v12
from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v17 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v434_to_b_tpch_gate_continuation_20260826_tpch"
SCENE = "tpch_sf4_memory"
COMMITTED_OUTER = 5
PARTIAL_OUTER = 6
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v61"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_configure = impl["configure_runtime"]
    original_state_document = impl["state_document"]

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
            raise RuntimeError(f"completed outer rank {rank} differs")
        return batch, rows

    def validate_resume_source(source: Path) -> dict[str, Any]:
        source = source.resolve()
        if source != SOURCE.resolve():
            raise RuntimeError("TPC-H Outer-6 continuation source differs")
        state_path = source / "run_state.json"
        source_state = verified(state_path)
        if not (
            source_state.get("algorithm") == "B"
            and source_state.get("async_scene") == SCENE
            and source_state.get("status") == "FAILED_CLOSED"
            and source_state.get("stage") == "FAILED"
            and source_state.get("error")
            == "RuntimeError: dynamic non-interference gate failed closed"
            and int(source_state.get("outer_attempt") or 0) == COMMITTED_OUTER
            and int((source_state.get("accepted_rounds") or {}).get(SCENE) or 0) == 2
            and int((source_state.get("rejected_outer_attempts") or {}).get(SCENE) or 0) == 3
        ):
            raise RuntimeError("TPC-H committed Outer-5 state differs")

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
            raise RuntimeError("TPC-H continuation baseline differs")

        design_path = source / "calibration_design.json"
        design = verified(design_path)
        wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
        wave_evidence: list[dict[str, Any]] = []
        for rank in range(1, 16):
            path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
            batch, rows = reusable_batch(path, rank)
            wave_rows[rank] = rows
            wave_evidence.append({
                "rank": rank,
                "path": str(path),
                "sha256": alg.sha(path),
                "status": batch["status"],
                "role": "completed_calibration_measurement_reused",
            })

        decisions: dict[int, dict[str, Any]] = {}
        for outer in range(1, COMMITTED_OUTER + 1):
            path = source / f"outer_{outer:03d}/{SCENE}.decision.json"
            decision = verified(path)
            evaluations = list(decision.get("evaluations") or [])
            if (
                int(decision.get("outer_attempt") or 0) != outer
                or decision.get("scene") != SCENE
                or len(evaluations) != int(decision.get("valid_real_evaluation_count") or 0)
                or not evaluations
                or any(
                    row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                    for row in evaluations
                )
            ):
                raise RuntimeError(f"TPC-H committed decision Outer {outer} differs")
            update_path = source / f"outer_{outer:03d}/online_resource_model_update.json"
            update = verified(update_path)
            scene_update = (update.get("scene_updates") or {}).get(SCENE) or {}
            if int(scene_update.get("added_count") or 0) != len(evaluations):
                raise RuntimeError(f"TPC-H online model update Outer {outer} differs")
            decisions[outer] = decision

        rankings_path = source / f"outer_{PARTIAL_OUTER:03d}/predicted_rankings.json"
        rankings = verified(rankings_path)
        selected = (rankings.get("selected_top_k") or {}).get(SCENE) or []
        if (
            int(rankings.get("outer_attempt") or 0) != PARTIAL_OUTER
            or len(selected) != 5
            or [int(row.get("rank") or 0) for row in selected] != [1, 2, 3, 4, 5]
        ):
            raise RuntimeError("TPC-H Outer-6 predicted ranking differs")
        rank1_path = source / f"outer_{PARTIAL_OUTER:03d}/top5_batch_1/batch_result.json"
        rank2_path = source / f"outer_{PARTIAL_OUTER:03d}/top5_batch_2/batch_result.json"
        rank1_batch, rank1_rows = reusable_batch(rank1_path, 1)
        rank2_batch, rank2_rows = reusable_batch(rank2_path, 2)
        failed_gate = source / f"outer_{PARTIAL_OUTER:03d}/top5_batch_3_dynamic_noninterference_gate.json"
        gate = verified(failed_gate)
        if gate.get("status") != "BLOCKED_FAIL_CLOSED" or gate.get("errors") != [
            "dynamic_noninterference_window_timeout"
        ]:
            raise RuntimeError("TPC-H Outer-6 failed gate evidence differs")

        initial_params, initial_ranges, initial_active = impl["parameter_state"]()
        return {
            "source": source,
            "state": {
                "params_by_scene": initial_params,
                "ranges_by_scene": initial_ranges,
                "active_by_scene": initial_active,
            },
            "committed_state": copy.deepcopy(source_state),
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
            "valid_batch_path": rank1_path,
            "valid_batch_sha256": alg.sha(rank1_path),
            "valid_batch_rows": rank1_rows,
            # The base continuation slot named "invalid" is a generic second
            # premeasured slot.  This source rank is explicitly valid and PASS.
            "invalid_batch_path": rank2_path,
            "invalid_batch_sha256": alg.sha(rank2_path),
            "invalid_batch": rank2_batch,
            "second_completed_rank_rows": rank2_rows,
            "failure_evidence_path": failed_gate,
            "failure_evidence_sha256": alg.sha(failed_gate),
            "companion_path": state_path,
            "companion_sha256": alg.sha(state_path),
        }

    def restore_committed_state(**kwargs: Any) -> None:
        source_resume = kwargs["source_resume"]
        source_state = copy.deepcopy(source_resume["committed_state"])
        scene = SCENE
        state = kwargs["state"]
        params_by_scene = kwargs["params_by_scene"]
        ranges_by_scene = kwargs["ranges_by_scene"]
        active_by_scene = kwargs["active_by_scene"]
        incumbents = kwargs["incumbents"]
        models = kwargs["models"]
        replayed = kwargs["replayed"]
        soft_negative_counts = kwargs["soft_negative_counts"]

        params_by_scene[scene] = copy.deepcopy(source_state["params_by_scene"][scene])
        ranges_by_scene[scene] = copy.deepcopy(source_state["ranges_by_scene"][scene])
        active_by_scene[scene] = copy.deepcopy(source_state["active_by_scene"][scene])
        incumbents[scene] = copy.deepcopy(source_state["incumbents"][scene])
        for key in (
            "accepted_rounds",
            "rejected_outer_attempts",
            "post_target_stability_confirmation_counts",
            "acceptance_tier",
            "consecutive_failed_rounds",
            "interval_stable_rounds",
            "stable_intervals",
            "converged",
            "incumbent_centered_forced_shrinks",
        ):
            state[key][scene] = copy.deepcopy(source_state[key][scene])
        replayed[scene].update(
            source_state.get("replayed_param_keys_by_scene", {}).get(scene, [])
        )
        soft_negative_counts[scene].update(
            copy.deepcopy(
                source_state.get("soft_negative_candidate_counts_by_scene", {}).get(
                    scene, {}
                )
            )
        )

        reconstructed_counts: dict[str, int] = {}
        for outer in range(1, COMMITTED_OUTER + 1):
            evaluations = sorted(
                source_resume["committed_decisions"][outer]["evaluations"],
                key=lambda row: int(row["rank"]),
            )
            for row in evaluations:
                payload = impl["measurement_payload"](row)
                models[scene].add(row["params"], payload["resource"], payload)
            update = impl["update_resource_model_from_formal_evaluations"](
                scene=scene,
                model=models[scene],
                evaluations=evaluations,
                outer_attempt=outer,
            )
            reconstructed_counts[str(outer)] = int(update["added_count"])

        state["outer_attempt"] = COMMITTED_OUTER
        state["params_by_scene"] = copy.deepcopy(params_by_scene)
        state["ranges_by_scene"] = copy.deepcopy(ranges_by_scene)
        state["active_by_scene"] = copy.deepcopy(active_by_scene)
        state["incumbents"] = copy.deepcopy(incumbents)
        state["replayed_param_keys_by_scene"] = {
            scene: sorted(replayed[scene])
        }
        state["soft_negative_candidate_counts_by_scene"] = copy.deepcopy(
            soft_negative_counts
        )
        state["last_online_resource_model_update"] = copy.deepcopy(
            source_state["last_online_resource_model_update"]
        )
        state["outer6_gate_continuation"] = {
            "source_run_root": str(SOURCE),
            "source_state_sha256": source_resume["state_sha256"],
            "committed_outer_state_reused_through": COMMITTED_OUTER,
            "completed_partial_outer_ranks_reused": [1, 2],
            "failed_gate_measurement_reused": False,
            "candidate_replayed_due_to_gate_failure": False,
            "accepted_or_rejected_credit_for_partial_outer": 0,
            "online_model_rows_reconstructed_by_outer": reconstructed_counts,
            "external_process_mutation_performed": False,
        }
        state["stage"] = "ALGORITHM_B_OUTER_005_STATE_RESTORED_VERIFIED"
        alg.atomic(kwargs["state_path"], state)

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        alg.r0mod.dynamic_gate = gate_v12
        impl["no_lease_runtime"].coordination = gate_v12
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["container_scoped_pg_statistics_gate_release"] = {
            "source_path": str(Path(gate_v12.__file__).resolve()),
            "source_sha256": alg.sha(Path(gate_v12.__file__).resolve()),
            "exact_read_only_probe_only": True,
            "live_container_cpu_storage_memory_identity_readback_required": True,
            "actual_workload_overlap_permitted": False,
            "external_process_mutation_performed": False,
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
    impl["REUSE_VERIFIED_BASELINE"] = True
    impl["RESUME_CALIBRATION_WAVES"] = 15
    impl["REUSE_HISTORICAL_OUTER_EVIDENCE"] = True
    impl["RESUME_OUTER_ATTEMPT"] = PARTIAL_OUTER
    impl["RESUME_VALID_RANKS"] = (1, 2)
    impl["RESUME_INVALID_RANKS"] = ()
    impl["INITIAL_OUTER_ATTEMPT"] = COMMITTED_OUTER
    impl["POST_CALIBRATION_STATE_TRANSFORM"] = restore_committed_state
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
