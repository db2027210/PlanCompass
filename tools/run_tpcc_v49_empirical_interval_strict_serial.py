#!/usr/bin/env python3
"""Strict serial TPC-C runner with v49 calibration-contract validation."""

from __future__ import annotations

from pathlib import Path

try:
    import run_tpcc_v48_clean5_successor_strict_serial as v48
except ModuleNotFoundError:
    from tools import run_tpcc_v48_clean5_successor_strict_serial as v48


BUILD_CONTRACT = "tpcc_v49_empirical_repeat_interval_calibration_v1"
_validate_v44 = v48._validate_v44


def _validate_v49(analysis: dict, candidates: list[dict]) -> None:
    contract = analysis.get("v49_build_contract") or {}
    if (
        contract.get("contract") != BUILD_CONTRACT
        or contract.get("exact_point_repeat_uses_empirical_center") is not True
        or contract.get("distance_zero_residual_applied") is not False
        or float(contract.get("nonexact_alpha_lower", -1.0)) != 0.0
        or float(contract.get("nonexact_alpha_upper", -1.0)) != 1.0
        or contract.get("nonexact_loo_persisted") is not True
        or contract.get("prediction_inside_calibration_interval_required") is not True
        or contract.get("state_neutral") is not True
        or contract.get("manual_candidate_selection") is not False
        or int(contract.get("physical_workload_calls_added", -1)) != 0
    ):
        raise RuntimeError("TPC-C v49 build contract mismatch")
    for candidate in candidates:
        predicted = candidate.get("predicted_resource") or {}
        interval = candidate.get("predicted_resource_interval") or {}
        model = (candidate.get("plan_regime_calibration") or {}).get(
            "nonstationarity_model"
        ) or {}
        if (
            candidate.get("prediction_inside_calibration_interval") is not True
            or candidate.get("distance_zero_residual_applied") is not False
            or set(predicted) != {"cpu", "io", "memory"}
            or set(interval) != set(predicted)
        ):
            raise RuntimeError("TPC-C v49 candidate interval certificate missing")
        for name, value in predicted.items():
            bounds = interval[name]
            if not float(bounds["lower"]) <= float(value) <= float(bounds["upper"]):
                raise RuntimeError("TPC-C v49 candidate prediction outside interval")
        alpha = float(model.get("all_resource_reconciliation_alpha", -1.0))
        if not 0.0 <= alpha <= 1.0:
            raise RuntimeError("TPC-C v49 candidate alpha outside [0,1]")
        if model.get("exact_point_repeat") is True:
            if alpha != 0.0 or model.get("distance_zero_residual_applied") is not False:
                raise RuntimeError("TPC-C v49 exact repeat residual duplication")
        elif model.get("nonexact_loo") is None:
            raise RuntimeError("TPC-C v49 nonexact candidate lacks LOO audit")


def load_runtime() -> dict:
    runtime = v48.load_runtime()
    original = runtime["validate_generation_and_top5"]

    def validate_generation_and_top5(analysis_path: Path, replay_root: Path):
        result = original(analysis_path, replay_root)
        _validate_v49(result[0], result[2])
        return result

    runtime["validate_generation_and_top5"] = validate_generation_and_top5
    return runtime


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
