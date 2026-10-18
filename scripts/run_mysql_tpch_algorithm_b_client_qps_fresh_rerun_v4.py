#!/usr/bin/env python3
"""Fresh TPC-H B rerun with a current-run normalization baseline.

The predecessor can start a fresh outer campaign while reusing an old
calibration baseline.  That is insufficient for a new performance comparison:
candidate QPS would be normalized to a different time cohort.  This successor
replays the baseline and all 15 calibration points, derives normalization from
that new baseline, and reuses no accepted/rejected outer state.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping, MutableMapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization
from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v26 as scheduler_221
from scripts import run_mysql_tpch_algorithm_b_client_qps_continue_v2 as predecessor


CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_b_fresh_rerun_20260830_v6"
    / "algorithm_b_fresh_rerun_contract.json"
)
SCENE = "tpch_sf4_memory"


def install_runtime() -> Any:
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_validate_resume_source = impl["validate_resume_source"]
    original_measurement_payload = impl["measurement_payload"]
    original_state_document = impl["state_document"]
    measurement_module = impl["single_measurement"]
    base_measure_batch = measurement_module.measure_batch
    normalization_contract: dict[str, Any] = {}
    baseline_context = {"active": False}

    def validate_contract() -> dict[str, Any]:
        value = alg.load(CONTRACT)
        if (
            value.get("schema_version")
            != "mysql_tpch_algorithm_b_fresh_rerun_v1"
            or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or value.get("formal_scope") != "mysql_only"
            or value.get("cross_engine_comparison_prohibited") is not True
            or value.get("scene") != SCENE
            or value.get("normalization_schema_version")
            != normalization.SCHEMA_VERSION
            or value.get("fresh_baseline_capture_required") is not True
            or value.get("fresh_top15_calibration_required") is not True
            or value.get("accepted_or_rejected_outer_state_reused") is not False
            or value.get("algorithm_a_used_as_search_prior") is not False
        ):
            raise RuntimeError("TPC-H B fresh-rerun contract differs")
        for binding in (value.get("source_bindings") or {}).values():
            if not isinstance(binding, Mapping):
                raise RuntimeError("TPC-H B fresh-rerun binding is not an object")
            path = ROOT / str(binding.get("path") or "")
            if (
                not path.is_file()
                or path.is_symlink()
                or alg.sha(path) != binding.get("sha256")
            ):
                raise RuntimeError(f"TPC-H B fresh-rerun binding differs: {path}")
        return value

    def validate_resume_source(source: Path) -> dict[str, Any]:
        # The source supplies only the frozen calibration design and default
        # parameter/range authority.  No baseline, calibration measurement, or
        # outer state is reused below.
        validated = original_validate_resume_source(source)
        # The two-worker continuation validator recognizes the source's
        # completed baseline/calibration and enables reuse as a side effect.
        # This successor intentionally consumes only its design authority, so
        # restore the fresh-campaign controls after that validator returns.
        impl["REUSE_VERIFIED_BASELINE"] = False
        impl["RESUME_CALIBRATION_WAVES"] = 0
        return validated

    def current_baseline_measure_batch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        output = Path(args[3] if len(args) > 3 else kwargs["output"])
        is_baseline = output.name == "formal_independent_baseline"
        baseline_context["active"] = is_baseline
        try:
            measured = base_measure_batch(*args, **kwargs)
        finally:
            baseline_context["active"] = False
        if is_baseline:
            document = alg.load(output / "batch_result.json")
            normalization_contract.clear()
            normalization_contract.update(normalization.baseline_contract(
                document, scene=SCENE,
            ))
            state_path_value = kwargs.get("state_path")
            if state_path_value is not None:
                state_path = Path(state_path_value)
                state = alg.load(state_path)
                state["same_worker_qps_normalization"] = copy.deepcopy(
                    normalization_contract
                )
                state["fresh_baseline_normalization_captured_in_current_run"] = True
                alg.atomic(state_path, state)
        return measured

    def measurement_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        if not normalization_contract:
            if baseline_context["active"]:
                # The fresh baseline defines the worker normalization factors;
                # it cannot itself require those factors to exist already.
                return original_measurement_payload(row)
            raise RuntimeError("TPC-H B current-run normalization baseline absent")
        if not isinstance(row, MutableMapping):
            raise RuntimeError("TPC-H B candidate row must be mutable")
        normalization.normalize_candidate_in_place(row, normalization_contract)
        return original_measurement_payload(row)

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "fresh_rerun_contract_path": str(CONTRACT),
            "fresh_rerun_contract_sha256": alg.sha(CONTRACT),
            "same_worker_qps_normalization": copy.deepcopy(
                normalization_contract
            ),
            "fresh_baseline_capture_required": True,
            "fresh_top15_calibration_required": True,
            "completed_calibration_measurements_reused": 0,
            "accepted_or_rejected_outer_state_reused": False,
            "outer_state_reused": False,
            "algorithm_a_parameter_or_measurement_read": False,
            "algorithm_a_used_as_search_prior": False,
        })
        document.setdefault("technical_continuation", {}).update({
            "kind": "fresh_current_baseline_and_top15_rerun_v1",
            "baseline_reused": False,
            "completed_calibration_waves_reused": [],
            "all_calibration_wave_results_reused": False,
            "accepted_or_rejected_outer_credit_reused": False,
            "historical_outer_rankings_or_batches_reused": False,
        })
        return document

    validate_contract()
    scheduler_221.SCENES = (SCENE,)
    scheduler_221._pending.clear()
    scheduler_221._rank_cache.clear()
    # The implementation's main calls install_scene after this successor is
    # installed.  That hook snapshots the then-current base functions before
    # binding this scheduler.  Binding it here as well makes that snapshot
    # self-referential and recurses during the fresh baseline.
    impl["scheduler"] = scheduler_221
    measurement_module.measure_batch = current_baseline_measure_batch
    impl["validate_resume_source"] = validate_resume_source
    impl["measurement_payload"] = measurement_payload
    impl["state_document"] = state_document
    impl["REUSE_VERIFIED_BASELINE"] = False
    impl["RESUME_CALIBRATION_WAVES"] = 0
    impl["REUSE_COMPLETED_MODEL_GATE"] = False
    impl["REUSE_HISTORICAL_OUTER_EVIDENCE"] = False
    impl["RESUME_OUTER_ATTEMPT"] = 0
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
