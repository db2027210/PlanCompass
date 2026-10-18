#!/usr/bin/env python3
"""Run a sealed TPC-C v44 diagnostic through the corrected v45 main path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    import build_tpcc_v44_plan_regime_score_separation as v44
    import run_tpcc_v43_exact_plan_nonstationary_strict_serial as v43_runner
except ModuleNotFoundError:
    from tools import build_tpcc_v44_plan_regime_score_separation as v44
    from tools import run_tpcc_v43_exact_plan_nonstationary_strict_serial as v43_runner


REQUIRED_FIELDS = {
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
REQUIRED_PINS = {"selection", "ledger", "events", "summary", "gate", "formal_state"}
OLD_FRESH_UNIQUE = '''    fresh_unique_v38 = (
        selection_contract
        == "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
    )
    fresh_serial = (
'''
NEW_FRESH_UNIQUE = '''    fresh_unique_v38 = (
        selection_contract
        == "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
    )
    v44_score_acquisition = (
        selection_contract
        == "tpcc_v44_plan_regime_score_separation_probe5_v1"
    )
    fresh_serial = (
'''
OLD_FRESH_SERIAL_BLOCK = '''    fresh_serial = (
        fresh_discovery
        or corrected_round13
        or robust_round14
        or exact_drift_v37
        or fresh_unique_v38
    )
    if selection_contract not in {
'''
NEW_FRESH_SERIAL_BLOCK = '''    fresh_serial = (
        fresh_discovery
        or corrected_round13
        or robust_round14
        or exact_drift_v37
        or fresh_unique_v38
        or v44_score_acquisition
    )
    if selection_contract not in {
'''
OLD_ALLOWED_TAIL = '''        "tpcc_v37_exact_plan_param_drift_lcb_trusted3_probe2_v1",
        "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1",
    }:
'''
NEW_ALLOWED_TAIL = '''        "tpcc_v37_exact_plan_param_drift_lcb_trusted3_probe2_v1",
        "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1",
        "tpcc_v44_plan_regime_score_separation_probe5_v1",
    }:
'''
OLD_DISCOVERY_CONDITION = (
    "    if corrected_round13 or robust_round14 or exact_drift_v37 or fresh_unique_v38:\n"
)
NEW_DISCOVERY_CONDITION = (
    "    if (corrected_round13 or robust_round14 or exact_drift_v37 "
    "or fresh_unique_v38 or v44_score_acquisition):\n"
)
OLD_EXPECTED_ROLE = '''                "uncertain_probe"
                if fresh_discovery or expected_rank > 3
                else "trusted"
'''
NEW_EXPECTED_ROLE = '''                "uncertain_probe"
                if fresh_discovery or v44_score_acquisition or expected_rank > 3
                else "trusted"
'''
OLD_MAIN_FRESH_UNIQUE = '''    fresh_unique_v38 = (
        analysis.get("selection_contract")
        == "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
    )
    fresh_serial = (
'''
NEW_MAIN_FRESH_UNIQUE = '''    fresh_unique_v38 = (
        analysis.get("selection_contract")
        == "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
    )
    v44_score_acquisition = (
        analysis.get("selection_contract")
        == "tpcc_v44_plan_regime_score_separation_probe5_v1"
    )
    fresh_serial = (
'''
OLD_MAIN_FRESH_SERIAL = '''    fresh_serial = (
        fresh_discovery
        or corrected_round13
        or robust_round14
        or exact_drift_v37
        or fresh_unique_v38
    )
    expected_replay_prefix = (
'''
NEW_MAIN_FRESH_SERIAL = '''    fresh_serial = (
        fresh_discovery
        or corrected_round13
        or robust_round14
        or exact_drift_v37
        or fresh_unique_v38
        or v44_score_acquisition
    )
    expected_replay_prefix = (
'''
OLD_MAIN_REPLAY_PREFIX = '''    expected_replay_prefix = (
        "top5_real_replay_round12"
        if fresh_discovery
        else (
'''
NEW_MAIN_REPLAY_PREFIX = '''    expected_replay_prefix = (
        "top5_real_replay_round16"
        if v44_score_acquisition
        else (
            "top5_real_replay_round12"
            if fresh_discovery
            else (
'''
OLD_MAIN_REPLAY_PREFIX_TAIL = '''                )
            )
        )
    )
    if replay_root.parent != generation_root or not replay_root.name.startswith(
'''
NEW_MAIN_REPLAY_PREFIX_TAIL = '''                )
            )
        )
        )
    )
    if replay_root.parent != generation_root or not replay_root.name.startswith(
'''
OLD_MAIN_LEDGER_PATH = '''    ledger_path = generation_root / (
        "strict_top5_real_replay_round12_ledger_v1.json"
        if fresh_discovery
        else (
'''
NEW_MAIN_LEDGER_PATH = '''    ledger_path = generation_root / (
        "strict_top5_real_replay_round16_v45_ledger_v1.json"
        if v44_score_acquisition
        else (
            "strict_top5_real_replay_round12_ledger_v1.json"
            if fresh_discovery
            else (
'''
OLD_MAIN_LEDGER_PATH_TAIL = '''                )
            )
        )
    )
    ledger_contract = (
'''
NEW_MAIN_LEDGER_PATH_TAIL = '''                )
            )
        )
        )
    )
    ledger_contract = (
'''
OLD_MAIN_LEDGER_CONTRACT = '''    ledger_contract = (
        "tpcc_round12_current_plan_discovery_strict_serial_replay_v1"
        if fresh_discovery
        else (
'''
NEW_MAIN_LEDGER_CONTRACT = '''    ledger_contract = (
        "tpcc_round16_v45_plan_regime_score_separation_strict_serial_replay_v1"
        if v44_score_acquisition
        else (
            "tpcc_round12_current_plan_discovery_strict_serial_replay_v1"
            if fresh_discovery
            else (
'''
OLD_MAIN_LEDGER_CONTRACT_TAIL = '''                )
            )
        )
    )
    successful: List[Dict[str, Any]] = []
'''
NEW_MAIN_LEDGER_CONTRACT_TAIL = '''                )
            )
        )
        )
    )
    successful: List[Dict[str, Any]] = []
'''


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _common(analysis: dict, candidates: list[dict]) -> tuple[dict, list[str]]:
    contract = analysis.get("v44_build_contract") or {}
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in candidates
    ]
    if (
        contract.get("contract") != v44.BUILD_CONTRACT
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
        or contract.get("threshold_changed") is not False
        or float(contract.get("score_mae_max", -1)) != 0.05
        or contract.get("resource_mae_pp_max")
        != {"cpu": 5.0, "io": 5.0, "memory": 2.0}
        or contract.get("manual_candidate_selection") is not False
        or contract.get("state_neutral") is not True
        or int(contract.get("physical_workload_calls_added", -1)) != 0
        or len(candidates) != 5
        or len(identifiers) != 5
        or "" in identifiers
        or len(set(identifiers)) != 5
        or any(item.get("boundary_corner") is not False for item in candidates)
        or any(REQUIRED_FIELDS - set(item) for item in candidates)
        or any(item.get("diagnostic_only") is not True for item in candidates)
        or any(item.get("incumbent_update_eligible") is not False for item in candidates)
        or any(item.get("used_for_gradient") is not False for item in candidates)
        or any(item.get("used_for_inner_incumbent_update") is not False for item in candidates)
    ):
        raise RuntimeError("TPC-C v44 common diagnostic contract mismatch")
    return contract, identifiers


def _validate_pins(pins: dict) -> None:
    if set(pins) != REQUIRED_PINS:
        raise RuntimeError("TPC-C v44 sealed authority pin set mismatch")
    for pin in pins.values():
        path = Path(str(pin.get("path") or "")).resolve()
        if not path.is_file() or _sha256(path) != pin.get("sha256"):
            raise RuntimeError("TPC-C v44 sealed authority SHA mismatch")


def _validate_acquisition(analysis: dict, candidates: list[dict]) -> None:
    contract, identifiers = _common(analysis, candidates)
    _validate_pins(contract.get("sealed_authority") or {})
    keys = [str(item.get("exact_point_key") or "") for item in candidates]
    plans = [str(item.get("workload_plan_fingerprint") or "") for item in candidates]
    if (
        contract.get("mode") != "score_separation_acquisition"
        or contract.get("acquisition_contract") != v44.ACQUISITION_CONTRACT
        or int(contract.get("maximum_physical_calls", -1)) != 5
        or contract.get("selection_algorithm")
        != "score_extrema_then_score_parameter_maximin_v1"
        or contract.get("selected_exact_point_keys") != keys
        or contract.get("selected_fingerprint_ids") != identifiers
        or contract.get("target_plan_regime") not in plans
        or len(set(plans)) != 1
        or len(set(keys)) != 5
        or contract.get("all_candidates_probe_only") is not True
        or contract.get("unknown_plan_probe_only") is not True
        or contract.get("single_clean_probe_prediction_only") is not True
        or contract.get("two_independent_clean_probes_promote_calibration_trust")
        is not True
        or contract.get("probe_promotion_contract")
        != v44.PROBE_PROMOTION_CONTRACT
        or contract.get("promoted_probe_formal_state_eligible") is not False
        or contract.get(
            "trusted_eligibility_requires_historical_trusted_rows_or_two_clean_probe_batches"
        )
        is not True
        or contract.get("candidate_generation") is not False
        or [item.get("selection_role") for item in candidates]
        != ["uncertain_probe"] * 5
    ):
        raise RuntimeError("TPC-C v44 score-separation acquisition mismatch")
    for candidate in candidates:
        calibration = candidate.get("plan_regime_calibration") or {}
        provenance = candidate.get("source_provenance") or {}
        if (
            candidate.get("trusted") is not False
            or candidate.get("uncertain_probe") is not True
            or candidate.get("trusted_eligible_v43") is not False
            or candidate.get("trusted_eligible_v44") is not False
            or candidate.get("exact_plan_regime_validated") is not False
            or candidate.get("probe_kind")
            != "v44_plan_regime_score_separation"
            or calibration.get("contract")
            != "tpcc_v44_score_separation_acquisition_prediction_only_v1"
            or calibration.get("trusted_eligible") is not False
            or calibration.get("probe_evidence_affects_formal_or_gradient") is not False
            or not isinstance(provenance.get("all_rows_prediction_only"), bool)
            or provenance.get("trusted_history_establishes_candidate_role") is not False
            or int(provenance.get("formal_state_update_rows", -1)) != 0
            or int(provenance.get("gradient_update_rows", -1)) != 0
            or int(provenance.get("incumbent_update_rows", -1)) != 0
        ):
            raise RuntimeError("TPC-C v44 acquisition role isolation mismatch")


def _validate_trusted3(analysis: dict, candidates: list[dict]) -> None:
    contract, identifiers = _common(analysis, candidates)
    promoted_total = int(
        contract.get("acquisition_probe_rows_used_to_establish_trust", -1)
    )
    if (
        contract.get("mode") != "trusted3_probe2"
        or contract.get("unknown_plan_probe_only") is not True
        or promoted_total < 0
        or contract.get("clean_evidence_routes_automatically") is not True
        or contract.get("roles")
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or contract.get("fingerprint_ids") != identifiers
        or [item.get("selection_role") for item in candidates]
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
    ):
        raise RuntimeError("TPC-C v44 trusted3/probe2 contract mismatch")
    historical: set[str] = set()
    for descriptor in analysis.get("source_batches") or []:
        path = Path(str(descriptor.get("selection_path") or "")).resolve()
        if not path.is_file() or _sha256(path) != descriptor.get("selection_sha256"):
            raise RuntimeError("TPC-C v44 historical selection SHA mismatch")
        source = json.loads(path.read_text(encoding="utf-8"))
        historical.update(
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in source.get("top5") or []
        )
    if set(identifiers) & historical:
        raise RuntimeError("TPC-C v44 trusted3/probe2 fingerprints are not fresh")
    promoted_selected = 0
    for rank, candidate in enumerate(candidates, 1):
        trusted = rank <= 3
        calibration = candidate.get("plan_regime_calibration") or {}
        provenance = candidate.get("source_provenance") or {}
        trusted_history = provenance.get("trusted_observations") or []
        promoted_rows = [
            row
            for row in trusted_history
            if row.get("calibration_trusted_eligible") is True
        ]
        promoted_selected += len(promoted_rows)
        for row in promoted_rows:
            certificate = row.get("calibration_trust_certificate") or {}
            if (
                certificate.get("contract") != v44.PROBE_PROMOTION_CONTRACT
                or certificate.get("exact_point_key")
                != candidate.get("exact_point_key")
                or certificate.get("validated_plan")
                != candidate.get("workload_plan_fingerprint")
                or int(certificate.get("independent_clean_probe_batch_count", 0))
                < 2
                or certificate.get("source_selection_role_preserved")
                != "uncertain_probe"
                or certificate.get("formal_state_update_eligible") is not False
                or certificate.get("gradient_update_eligible") is not False
                or certificate.get("incumbent_update_eligible") is not False
                or certificate.get("range_update_eligible") is not False
            ):
                raise RuntimeError(
                    f"TPC-C v45 promoted probe certificate mismatch: rank={rank}"
                )
        if (
            candidate.get("trusted_eligible_v43") is not trusted
            or calibration.get("trusted_eligible") is not trusted
            or calibration.get("probe_evidence_affects_formal_or_gradient") is not False
            or int(
                provenance.get(
                    "v44_acquisition_probe_rows_used_to_establish_trust", 0
                )
            )
            != len(promoted_rows)
            or (trusted and len(trusted_history) < 2)
        ):
            raise RuntimeError(f"TPC-C v45 trusted role isolation mismatch: rank={rank}")
    if promoted_total < promoted_selected:
        raise RuntimeError("TPC-C v45 promoted probe accounting mismatch")


def _validate_v44(analysis: dict, candidates: list[dict]) -> None:
    mode = (analysis.get("v44_build_contract") or {}).get("mode")
    if mode == "score_separation_acquisition":
        _validate_acquisition(analysis, candidates)
    elif mode == "trusted3_probe2":
        _validate_trusted3(analysis, candidates)
    else:
        raise RuntimeError("TPC-C v44 unknown diagnostic mode")


def load_runtime() -> dict:
    source = v43_runner.SOURCE.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != v43_runner.EXPECTED_SOURCE_SHA256:
        raise RuntimeError("TPC-C v44 sealed runner source SHA mismatch")
    replacements = (
        (v43_runner.OLD, v43_runner.NEW),
        (OLD_FRESH_UNIQUE, NEW_FRESH_UNIQUE),
        (OLD_FRESH_SERIAL_BLOCK, NEW_FRESH_SERIAL_BLOCK),
        (OLD_ALLOWED_TAIL, NEW_ALLOWED_TAIL),
        (OLD_DISCOVERY_CONDITION, NEW_DISCOVERY_CONDITION),
        (OLD_EXPECTED_ROLE, NEW_EXPECTED_ROLE),
        (OLD_MAIN_FRESH_UNIQUE, NEW_MAIN_FRESH_UNIQUE),
        (OLD_MAIN_FRESH_SERIAL, NEW_MAIN_FRESH_SERIAL),
        (OLD_MAIN_REPLAY_PREFIX, NEW_MAIN_REPLAY_PREFIX),
        (OLD_MAIN_REPLAY_PREFIX_TAIL, NEW_MAIN_REPLAY_PREFIX_TAIL),
        (OLD_MAIN_LEDGER_PATH, NEW_MAIN_LEDGER_PATH),
        (OLD_MAIN_LEDGER_PATH_TAIL, NEW_MAIN_LEDGER_PATH_TAIL),
        (OLD_MAIN_LEDGER_CONTRACT, NEW_MAIN_LEDGER_CONTRACT),
        (OLD_MAIN_LEDGER_CONTRACT_TAIL, NEW_MAIN_LEDGER_CONTRACT_TAIL),
    )
    if any(source.count(old) != 1 for old, _new in replacements):
        raise RuntimeError("TPC-C v44 runner patch anchor mismatch")
    transformed = source
    for old, new in replacements:
        transformed = transformed.replace(old, new, 1)
    namespace = {
        "__name__": "tpcc_v44_score_separation_runtime",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
    }
    exec(compile(transformed, str(v43_runner.SOURCE), "exec"), namespace)
    original_validate = namespace["validate_generation_and_top5"]

    def validate_generation_and_top5(analysis_path: Path, replay_root: Path):
        result = original_validate(analysis_path, replay_root)
        _validate_v44(result[0], result[2])
        return result

    namespace["validate_generation_and_top5"] = validate_generation_and_top5
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
