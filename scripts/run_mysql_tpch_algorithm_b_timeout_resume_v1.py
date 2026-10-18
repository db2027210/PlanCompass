#!/usr/bin/env python3
"""Resume normalized TPC-H B at Outer 2 after a candidate-local timeout."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_client_qps_measurement_v2 as metric
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_v1 as fresh
from scripts import run_mysql_tpch_algorithm_b_client_qps_normalized_v3 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v1125_to_b_tpch_client_qps_disk2_normalized_20260830_tpch"
BOOTSTRAP_SOURCE = HISTORY / "v1117_to_b_tpch_client_qps_disk2_fresh_20260830_tpch"
EVIDENCE = HISTORY / "v1128_tpch_b_outer2_timeout_recovery_evidence_20260830"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_b_timeout_resume_20260830_v2"
    / "algorithm_b_timeout_resume_contract.json"
)
SCENE = metric.SCENE


def _validate_successor_contract(alg: Any) -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version") != "mysql_tpch_algorithm_b_timeout_resume_v2"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != SCENE
        or value.get("qps_metric_contract") != metric.contract()
        or value.get("completed_outer_state_reused_through") != 1
        or value.get("partial_outer") != 2
        or value.get("valid_partial_ranks_reused") != [1]
        or value.get("invalid_timeout_ranks_preserved_without_measurement_credit") != [2]
        or value.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or value.get("algorithm_a_parameter_or_measurement_read") is not False
    ):
        raise RuntimeError("TPC-H B timeout-resume contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H B timeout-resume binding differs: {path}")
    for key in ("manifest", "valid_rank1", "invalid_rank2"):
        evidence = (value.get("recovery_evidence") or {})
        path = ROOT / str(evidence.get(f"{key}_path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != evidence.get(f"{key}_sha256")
        ):
            raise RuntimeError(f"TPC-H B recovery evidence differs: {path}")
    return value


def install_runtime() -> Any:
    fresh.client_qps = metric
    fresh.CONTRACT = CONTRACT
    fresh._validate_successor_contract = _validate_successor_contract
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_validate_resume = impl["validate_resume_source"]
    original_state_document = impl["state_document"]

    def verified(path: Path) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular continuation evidence required: {path}")
        sidecar = path.with_name(path.name + ".sha256")
        if (
            not sidecar.is_file() or sidecar.is_symlink()
            or sidecar.read_text(encoding="ascii").split()[0] != alg.sha(path)
        ):
            raise RuntimeError(f"continuation sidecar differs: {path}")
        value = alg.load(path)
        if not isinstance(value, Mapping):
            raise RuntimeError(f"JSON object required: {path}")
        return dict(value)

    def reusable_batch(path: Path, rank: int) -> dict[str, dict[str, Any]]:
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
                or row.get("eligible_for_outer_acceptance") is not True
                for row in rows.values()
            )
        ):
            raise RuntimeError(f"completed rank {rank} differs: {path}")
        return rows

    def validate_resume_source(source: Path) -> dict[str, Any]:
        source = source.resolve()
        if source != SOURCE.resolve():
            raise RuntimeError("TPC-H B timeout-resume source differs")

        # Initialize the predecessor's private same-worker normalization
        # contract from the bit-identical frozen bootstrap baseline.
        bootstrap = original_validate_resume(BOOTSTRAP_SOURCE)
        state_path = source / "run_state.json"
        state = verified(state_path)
        if not (
            state.get("algorithm") == "B"
            and state.get("async_scene") == SCENE
            and state.get("status") == "FAILED_CLOSED"
            and state.get("stage") == "FAILED"
            and state.get("error")
            == "RuntimeError: full-suite client QPS must be finite and positive"
            and int(state.get("outer_attempt") or 0) == 1
            and int((state.get("accepted_rounds") or {}).get(SCENE) or 0) == 0
            and int((state.get("rejected_outer_attempts") or {}).get(SCENE) or 0) == 1
        ):
            raise RuntimeError("TPC-H B committed Outer-1 state differs")

        baseline_path = source / "formal_independent_baseline/batch_result.json"
        baseline = verified(baseline_path)
        baseline_rows = copy.deepcopy(dict(baseline.get("measurements") or {}))
        if (
            alg.sha(baseline_path) != bootstrap["baseline_sha256"]
            or baseline.get("status") != "PASS"
            or baseline.get("failures") not in ([], None)
            or len(baseline_rows) != 2
        ):
            raise RuntimeError("TPC-H B continuation baseline differs")

        design_path = source / "calibration_design.json"
        design = verified(design_path)
        wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
        wave_evidence: list[dict[str, Any]] = []
        for rank in range(1, 16):
            path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
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
                raise RuntimeError(f"TPC-H B calibration wave {rank} differs")
            wave_rows[rank] = rows
            wave_evidence.append({
                "rank": rank, "path": str(path), "sha256": alg.sha(path),
                "role": "completed_calibration_measurement_reused",
            })

        decision_path = source / f"outer_001/{SCENE}.decision.json"
        decision = verified(decision_path)
        evaluations = list(decision.get("evaluations") or [])
        update = verified(source / "outer_001/online_resource_model_update.json")
        if (
            decision.get("outer_attempt") != 1
            or decision.get("accepted") is not False
            or len(evaluations) != 5
            or int(((update.get("scene_updates") or {}).get(SCENE) or {}).get("added_count") or 0) != 5
        ):
            raise RuntimeError("TPC-H B committed Outer-1 decision differs")

        rankings_path = source / "outer_002/predicted_rankings.json"
        rankings = verified(rankings_path)
        if (
            rankings.get("outer_attempt") != 2
            or len((rankings.get("selected_top_k") or {}).get(SCENE) or []) != 5
        ):
            raise RuntimeError("TPC-H B partial Outer-2 rankings differ")
        valid_path = EVIDENCE / "rank_1/batch_result.json"
        invalid_path = EVIDENCE / "rank_2/batch_result.json"
        valid_rows = reusable_batch(valid_path, 1)
        invalid_batch = verified(invalid_path)
        invalid_rows = dict(invalid_batch.get("measurements") or {})
        if (
            not impl["formally_infeasible_candidate_batch"](invalid_batch)
            or len(invalid_rows) != 1
            or any(
                row.get("scene") != SCENE
                or int(row.get("rank") or 0) != 2
                or row.get("eligible_for_outer_acceptance") is not False
                or float(row.get("db_qps") or 0.0) != 0.0
                for row in invalid_rows.values()
            )
        ):
            raise RuntimeError("TPC-H B preserved timeout rank differs")

        initial_params, initial_ranges, initial_active = impl["parameter_state"]()
        impl["REUSE_VERIFIED_BASELINE"] = True
        impl["RESUME_CALIBRATION_WAVES"] = 15
        impl["REUSE_HISTORICAL_OUTER_EVIDENCE"] = True
        impl["RESUME_OUTER_ATTEMPT"] = 2
        impl["RESUME_VALID_RANKS"] = (1,)
        impl["RESUME_INVALID_RANKS"] = (2,)
        impl["INITIAL_OUTER_ATTEMPT"] = 1
        return {
            "source": source,
            "state": {
                "params_by_scene": initial_params,
                "ranges_by_scene": initial_ranges,
                "active_by_scene": initial_active,
            },
            "committed_state": state,
            "committed_decisions": {1: decision},
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
            "valid_batch_path": valid_path,
            "valid_batch_sha256": alg.sha(valid_path),
            "valid_batch_rows": valid_rows,
            "invalid_batch_path": invalid_path,
            "invalid_batch_sha256": alg.sha(invalid_path),
            "invalid_batch": invalid_batch,
            "failure_evidence_path": invalid_path,
            "failure_evidence_sha256": alg.sha(invalid_path),
            "companion_path": state_path,
            "companion_sha256": alg.sha(state_path),
        }

    def restore_committed_state(**kwargs: Any) -> None:
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
            source_state.get("soft_negative_candidate_counts_by_scene", {}).get(SCENE, {})
        ))
        evaluations = sorted(
            source_resume["committed_decisions"][1]["evaluations"],
            key=lambda row: int(row["rank"]),
        )
        for row in evaluations:
            payload = impl["measurement_payload"](row)
            models[SCENE].add(row["params"], payload["resource"], payload)
        impl["update_resource_model_from_formal_evaluations"](
            scene=SCENE, model=models[SCENE], evaluations=evaluations,
            outer_attempt=1,
        )
        state["outer_attempt"] = 1
        state["params_by_scene"] = copy.deepcopy(params)
        state["ranges_by_scene"] = copy.deepcopy(ranges)
        state["active_by_scene"] = copy.deepcopy(active)
        state["incumbents"] = copy.deepcopy(incumbents)
        state["replayed_param_keys_by_scene"] = {SCENE: sorted(replayed[SCENE])}
        state["soft_negative_candidate_counts_by_scene"] = copy.deepcopy(negatives)
        state["last_online_resource_model_update"] = copy.deepcopy(
            source_state["last_online_resource_model_update"]
        )
        state["completed_timeout_boundary_continuation"] = {
            "source_run_root": str(SOURCE),
            "committed_outer_state_reused_through": 1,
            "partial_outer": 2,
            "valid_rank_reused": 1,
            "invalid_timeout_rank_preserved_without_measurement_credit": 2,
            "candidate_replayed_due_to_adapter_failure": False,
        }
        alg.atomic(kwargs["state_path"], state)

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "timeout_resume_contract_path": str(CONTRACT),
            "timeout_resume_contract_sha256": alg.sha(CONTRACT),
            "qps_metric_contract": metric.contract(),
            "outer_state_reused": True,
            "outer_state_reused_through": 1,
            "partial_outer_valid_rank_reused": 1,
            "partial_outer_invalid_timeout_rank_preserved": 2,
            "invalid_timeout_measurement_credit": False,
        })
        return value

    impl["validate_resume_source"] = validate_resume_source
    impl["validate_intermediate_failure"] = lambda: {
        "state_path": SOURCE / "run_state.json",
        "state_sha256": alg.sha(SOURCE / "run_state.json"),
        "state": {},
    }
    impl["state_document"] = state_document
    impl["REUSE_VERIFIED_BASELINE"] = True
    impl["RESUME_CALIBRATION_WAVES"] = 15
    impl["REUSE_HISTORICAL_OUTER_EVIDENCE"] = True
    impl["RESUME_OUTER_ATTEMPT"] = 2
    impl["RESUME_VALID_RANKS"] = (1,)
    impl["RESUME_INVALID_RANKS"] = (2,)
    impl["INITIAL_OUTER_ATTEMPT"] = 1
    impl["POST_CALIBRATION_STATE_TRANSFORM"] = restore_committed_state
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
