#!/usr/bin/env python3
"""Run a v40 SHA-sealed clean5 recalibration diagnostic, strictly serial."""

from __future__ import annotations

from pathlib import Path

try:
    import run_tpcc_v39_plan_regime_strict_serial as v39_runner
except ModuleNotFoundError:
    from tools import run_tpcc_v39_plan_regime_strict_serial as v39_runner


def _validate_v40(analysis: dict, candidates: list[dict]) -> None:
    contract = analysis.get("v40_build_contract") or {}
    pins = contract.get("sealed_authority") or {}
    ids = [str(item["three_level_fingerprint"]["id"]) for item in candidates]
    required_pins = {"selection", "ledger", "events", "summary", "gate", "formal_state"}
    if (
        contract.get("contract") != "tpcc_v40_sha_sealed_clean5_exact_recalibration_v1"
        or set(pins) != required_pins
        or any(
            not Path(str(pin.get("path") or "")).is_file()
            or v39_runner._sha256(Path(str(pin["path"]))) != pin.get("sha256")
            for pin in pins.values()
        )
        or contract.get("source_gate_only_failure") != "trusted_score_mae_within_gate"
        or contract.get("threshold_changed") is not False
        or float(contract.get("score_residual_limit", -1)) != 0.05
        or contract.get("trusted_order") != "descending_recent_exact_lcb"
        or int(contract.get("current_clean_trusted_rows_absorbed", -1)) != 3
        or contract.get("direct_formal_promotion") is not False
        or contract.get("five_new_noncorner_fingerprints") is not True
        or contract.get("state_neutral") is not True
        or int(contract.get("physical_workload_calls_added", -1)) != 0
        or contract.get("manual_candidate_selection") is not False
        or contract.get("roles")
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or contract.get("fingerprint_ids") != ids
        or any(item.get("boundary_corner") is not False for item in candidates)
        or any(
            (item.get("plan_regime_calibration") or {}).get("contract")
            != "tpcc_v40_recent_exact_clean5_recalibration_v1"
            or (item.get("confidence") or {}).get("contract")
            != "tpcc_v40_clean5_recent_exact_confidence_v1"
            for item in candidates
        )
    ):
        raise RuntimeError("TPC-C v40 clean5 recalibration runner contract mismatch")


def load_runtime() -> dict:
    namespace = v39_runner.load_runtime()
    original = namespace["validate_generation_and_top5"]

    def validate_generation_and_top5(analysis_path: Path, replay_root: Path):
        result = original(analysis_path, replay_root)
        _validate_v40(result[0], result[2])
        return result

    namespace["validate_generation_and_top5"] = validate_generation_and_top5
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
