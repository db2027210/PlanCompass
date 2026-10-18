#!/usr/bin/env python3
"""Load the sealed no-retry runner with the v42 IO role-separation gate."""

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
NEW = """            v42_io_role_separated = (
                (analysis.get(\"v42_build_contract\") or {}).get(\"contract\")
                == \"tpcc_v42_exact_plan_io_role_separated_recalibration_v1\"
            )
            if (exact_drift_v37 or fresh_unique_v38) and not v42_io_role_separated:
                calibration = candidate.get(\"plan_regime_calibration\") or {}
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_v42(analysis: dict, candidates: list[dict]) -> None:
    contract = analysis.get("v42_build_contract") or {}
    pins = contract.get("sealed_v40_authority") or {}
    required_pins = {"selection", "ledger", "events", "summary", "gate", "formal_state"}
    ids = [str((item.get("three_level_fingerprint") or {}).get("id") or "") for item in candidates]
    historical: set[str] = set()
    for descriptor in analysis.get("source_batches") or []:
        path = Path(str(descriptor.get("selection_path") or "")).resolve()
        if not path.is_file() or _sha256(path) != descriptor.get("selection_sha256"):
            raise RuntimeError("TPC-C v42 historical selection SHA mismatch")
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
    if (
        contract.get("contract")
        != "tpcc_v42_exact_plan_io_role_separated_recalibration_v1"
        or set(pins) != required_pins
        or any(
            not Path(str(pin.get("path") or "")).is_file()
            or _sha256(Path(str(pin["path"]))) != pin.get("sha256")
            for pin in pins.values()
        )
        or contract.get("source_gate_failures")
        != ["actual_best_in_predicted_top3", "trusted_io_mae_within_gate"]
        or contract.get("threshold_changed") is not False
        or float(contract.get("score_mae_max", -1)) != 0.05
        or contract.get("resource_mae_pp_max")
        != {"cpu": 5.0, "io": 5.0, "memory": 2.0}
        or contract.get("io_calibration")
        != "exact_point_then_role_separated_plan_regime_offset"
        or contract.get("trusted_plan_anchor_ranks") != [1, 2, 3]
        or int(contract.get("trusted_plan_probe_rows_used", -1)) != 0
        or contract.get("rank4_probe_calibration_domain") != "probe_only"
        or contract.get("current_probe_exact_points_replayed") is not False
        or int(contract.get("rank4_probe_formal_state_update_rows", -1)) != 0
        or int(contract.get("rank4_probe_gradient_update_rows", -1)) != 0
        or int(contract.get("rank4_probe_incumbent_update_rows", -1)) != 0
        or contract.get("same_rank_retry_allowed") is not False
        or contract.get("technical_call_advances_to_next_unique_rank") is not True
        or contract.get("five_new_noncorner_fingerprints") is not True
        or contract.get("state_neutral") is not True
        or int(contract.get("physical_workload_calls_added", -1)) != 0
        or contract.get("manual_candidate_selection") is not False
        or contract.get("roles")
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or contract.get("fingerprint_ids") != ids
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
        or len(ids) != 5
        or "" in ids
        or len(set(ids)) != 5
        or bool(set(ids) & historical)
        or any(item.get("boundary_corner") is not False for item in candidates)
        or any(required_fields - set(item) for item in candidates)
    ):
        raise RuntimeError("TPC-C v42 IO recalibration runner contract mismatch")
    for rank, candidate in enumerate(candidates, 1):
        calibration = candidate.get("plan_regime_calibration") or {}
        provenance = candidate.get("source_provenance") or {}
        if (
            calibration.get("contract")
            != "tpcc_v42_exact_point_plan_regime_io_calibration_v1"
            or int(calibration.get("probe_rows_used_for_trusted_calibration", -1)) != 0
            or calibration.get("rank4_probe_used_for_formal_state") is not False
            or calibration.get("rank4_probe_used_for_gradient") is not False
            or calibration.get("rank4_probe_used_for_incumbent") is not False
            or int(provenance.get("rank4_probe_formal_state_update_rows", -1)) != 0
            or int(provenance.get("rank4_probe_gradient_update_rows", -1)) != 0
            or int(provenance.get("rank4_probe_incumbent_update_rows", -1)) != 0
            or candidate.get("diagnostic_only") is not True
            or candidate.get("incumbent_update_eligible") is not False
            or candidate.get("used_for_gradient") is not False
            or candidate.get("used_for_inner_incumbent_update") is not False
            or (rank <= 3 and candidate.get("trusted_eligible_v42") is not True)
            or (rank > 3 and candidate.get("trusted_eligible_v42") is not False)
        ):
            raise RuntimeError(f"TPC-C v42 candidate role isolation mismatch: rank={rank}")


def load_runtime() -> dict:
    source = SOURCE.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("TPC-C v42 sealed runner source SHA mismatch")
    if source.count(OLD) != 1:
        raise RuntimeError("TPC-C v42 runner patch anchor mismatch")
    transformed = source.replace(OLD, NEW, 1)
    namespace = {
        "__name__": "tpcc_v42_io_role_separated_runtime",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
    }
    exec(compile(transformed, str(SOURCE), "exec"), namespace)
    original_validate = namespace["validate_generation_and_top5"]

    def validate_generation_and_top5(analysis_path: Path, replay_root: Path):
        result = original_validate(analysis_path, replay_root)
        _validate_v42(result[0], result[2])
        return result

    namespace["validate_generation_and_top5"] = validate_generation_and_top5
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
