#!/usr/bin/env python3
"""Run the sealed v43 nonstationarity Top-5 strictly serial and state-neutral."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


SOURCE = Path(
    "/home/anonymous/program/resource_adaptive/"
    "tools/run_tpcc_v36_unique_exact_strict_serial.py"
)
EXPECTED_SOURCE_SHA256 = (
    "eb4f20679e0c8f2dec49520874ba40aebc0cdb7f30ec9939d98d6234692fa3eb"
)
OLD = """            if exact_drift_v37 or fresh_unique_v38:
                calibration = candidate.get(\"plan_regime_calibration\") or {}
"""
NEW = """            v43_nonstationary = (
                (analysis.get(\"v43_build_contract\") or {}).get(\"contract\")
                == \"tpcc_v43_exact_plan_nonstationary_score_resource_recalibration_v1\"
            )
            if (exact_drift_v37 or fresh_unique_v38) and not v43_nonstationary:
                calibration = candidate.get(\"plan_regime_calibration\") or {}
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_v43(analysis: dict, candidates: list[dict]) -> None:
    contract = analysis.get("v43_build_contract") or {}
    pins = contract.get("sealed_v42_authority") or {}
    required_pins = {"selection", "ledger", "events", "summary", "gate", "formal_state"}
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in candidates
    ]
    historical: set[str] = set()
    for descriptor in analysis.get("source_batches") or []:
        path = Path(str(descriptor.get("selection_path") or "")).resolve()
        if not path.is_file() or _sha256(path) != descriptor.get("selection_sha256"):
            raise RuntimeError("TPC-C v43 historical selection SHA mismatch")
        source = json.loads(path.read_text(encoding="utf-8"))
        historical.update(
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in source.get("top5") or []
        )
    required_fields = {
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    }
    allowed_source_failures = {
        "actual_best_in_predicted_top3",
        "trusted_spearman_positive",
        "trusted_score_mae_within_gate",
        "trusted_cpu_mae_within_gate",
        "trusted_io_mae_within_gate",
        "trusted_memory_mae_within_gate",
        "trusted_resource_no_order_of_magnitude_error",
        "trusted_score_span_resolvable",
    }
    source_failures = set(contract.get("source_gate_failures") or [])
    if (
        contract.get("contract")
        != "tpcc_v43_exact_plan_nonstationary_score_resource_recalibration_v1"
        or set(pins) != required_pins
        or any(
            not Path(str(pin.get("path") or "")).is_file()
            or _sha256(Path(str(pin["path"]))) != pin.get("sha256")
            for pin in pins.values()
        )
        or not source_failures
        or not source_failures.issubset(allowed_source_failures)
        or contract.get("threshold_changed") is not False
        or float(contract.get("score_mae_max", -1)) != 0.05
        or contract.get("resource_mae_pp_max")
        != {"cpu": 5.0, "io": 5.0, "memory": 2.0}
        or contract.get("calibration")
        != "distance_weighted_exact_plan_all_resource_plus_score_nonstationarity"
        or contract.get("io_only_scalar_calibration") is not False
        or contract.get("current_probe_exact_points_replayed") is not False
        or contract.get("v42_rank4_probe_role_rewritten") is not False
        or int(contract.get("v42_probe_formal_state_update_rows", -1)) != 0
        or int(contract.get("v42_probe_gradient_update_rows", -1)) != 0
        or int(contract.get("v42_probe_incumbent_update_rows", -1)) != 0
        or contract.get("same_rank_retry_allowed") is not False
        or contract.get("technical_call_advances_to_next_unique_rank") is not True
        or contract.get("five_new_noncorner_fingerprints") is not True
        or contract.get("state_neutral") is not True
        or int(contract.get("physical_workload_calls_added", -1)) != 0
        or contract.get("manual_candidate_selection") is not False
        or contract.get("roles")
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or contract.get("fingerprint_ids") != identifiers
        or analysis.get("diagnostic_only") is not True
        or int(analysis.get("maximum_real_workload_invocations", -1)) != 5
        or analysis.get("qps_role") != "outer_audit_only_not_inner_score"
        or analysis.get("state_mutation_contract")
        != {
            "accepted_update": False,
            "gradient_update": False,
            "incumbent_update": False,
            "range_update": False,
        }
        or [item.get("selection_role") for item in candidates]
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(identifiers) != 5
        or "" in identifiers
        or len(set(identifiers)) != 5
        or bool(set(identifiers) & historical)
        or any(item.get("boundary_corner") is not False for item in candidates)
        or any(required_fields - set(item) for item in candidates)
    ):
        raise RuntimeError("TPC-C v43 nonstationarity runner contract mismatch")
    for rank, candidate in enumerate(candidates, 1):
        calibration = candidate.get("plan_regime_calibration") or {}
        model = calibration.get("nonstationarity_model") or {}
        provenance = candidate.get("source_provenance") or {}
        trusted = rank <= 3
        anchor_rows = model.get("anchor_rows") or []
        if (
            calibration.get("contract")
            != "tpcc_v43_exact_plan_nonstationarity_calibration_v1"
            or calibration.get("probe_evidence_affects_formal_or_gradient") is not False
            or int(calibration.get("probe_rows_used_for_trusted_calibration", -1)) != 0
            or int(provenance.get("v42_probe_formal_state_update_rows", -1)) != 0
            or int(provenance.get("v42_probe_gradient_update_rows", -1)) != 0
            or int(provenance.get("v42_probe_incumbent_update_rows", -1)) != 0
            or candidate.get("diagnostic_only") is not True
            or candidate.get("incumbent_update_eligible") is not False
            or candidate.get("used_for_gradient") is not False
            or candidate.get("used_for_inner_incumbent_update") is not False
            or candidate.get("trusted_eligible_v43") is not trusted
            or (trusted and model.get("contract")
                != "tpcc_v43_distance_weighted_exact_plan_nonstationarity_v1")
            or (trusted and any(row.get("selection_role") != "trusted" for row in anchor_rows))
            or (trusted and not anchor_rows)
        ):
            raise RuntimeError(f"TPC-C v43 candidate role isolation mismatch: rank={rank}")


def load_runtime() -> dict:
    source = SOURCE.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("TPC-C v43 sealed runner source SHA mismatch")
    if source.count(OLD) != 1:
        raise RuntimeError("TPC-C v43 runner patch anchor mismatch")
    transformed = source.replace(OLD, NEW, 1)
    namespace = {
        "__name__": "tpcc_v43_nonstationary_runtime",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
    }
    exec(compile(transformed, str(SOURCE), "exec"), namespace)
    original_validate = namespace["validate_generation_and_top5"]

    def validate_generation_and_top5(analysis_path: Path, replay_root: Path):
        result = original_validate(analysis_path, replay_root)
        _validate_v43(result[0], result[2])
        return result

    namespace["validate_generation_and_top5"] = validate_generation_and_top5
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
