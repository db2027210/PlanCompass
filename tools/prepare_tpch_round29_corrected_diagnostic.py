#!/usr/bin/env python3
"""Prepare a fail-closed TPC-H round29 diagnostic Top-5 package.

This tool is intentionally offline-only.  It never connects to PostgreSQL,
starts a workload, mutates formal state, or performs SSH.  Round28 trusted
measurements may update only their exact structural plan-regime calibration;
probe measurements are retained for audit and never become calibration
evidence.  Every emitted round29 candidate is diagnostic-only.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import tpch_round19_plan_regime_select as selector


RESOURCES: Tuple[str, ...] = ("cpu", "io", "memory")
EXPECTED_ACTUAL_SCORES = (
    4.7439008,
    4.5708987,
    4.4237270,
    4.7859400,
    4.7344076,
)
EXPECTED_INCUMBENT_SCORE = 4.77702526499285
EXPECTED_ACCEPTED_COUNT = 1
REQUIRED_AUDIT_FIELDS = (
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
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"{label} must be finite")
    return result


def exact_regime(candidate: Mapping[str, Any]) -> str:
    computed = selector.structural_plan_regime_id(candidate)
    declared = candidate.get("structural_plan_regime_id")
    if declared is not None and declared != computed:
        raise RuntimeError(
            f"declared/computed exact-regime mismatch: {declared} != {computed}"
        )
    return computed


def validate_round28_selection(
    selection: Mapping[str, Any],
) -> Dict[int, Mapping[str, Any]]:
    top5 = selection.get("top5")
    if not isinstance(top5, list) or len(top5) != 5:
        raise RuntimeError("round28 selection must contain exactly five candidates")
    by_rank: Dict[int, Mapping[str, Any]] = {}
    fingerprints: set[str] = set()
    parameter_keys: set[str] = set()
    for candidate in top5:
        rank = int(candidate["rank"])
        if rank in by_rank:
            raise RuntimeError(f"duplicate round28 rank: {rank}")
        by_rank[rank] = candidate
        expected_role = "trusted" if rank <= 3 else "probe"
        if candidate.get("selection_role") != expected_role:
            raise RuntimeError(f"round28 rank {rank} role mismatch")
        exact_regime(candidate)
        missing = [
            field for field in REQUIRED_AUDIT_FIELDS if field not in candidate
        ]
        if missing:
            raise RuntimeError(
                f"round28 rank {rank} lacks required audit fields: {missing}"
            )
        fingerprints.add(selector.fingerprint_id(candidate))
        parameter_keys.add(selector.parameter_key(candidate))
    if set(by_rank) != {1, 2, 3, 4, 5}:
        raise RuntimeError("round28 ranks must be 1..5")
    if len(fingerprints) != 5 or len(parameter_keys) != 5:
        raise RuntimeError("round28 candidates are not fingerprint/parameter unique")
    return by_rank


def validate_round28_results(
    results: Mapping[str, Any],
    source_by_rank: Mapping[int, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    if (
        int(results.get("physical_call_count", -1)) != 5
        or int(results.get("valid_replay_count", -1)) != 5
        or int(results.get("technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("round28 result counters must be phys5/valid5/tech0")
    rows = results.get("results")
    if not isinstance(rows, list) or len(rows) != 5:
        raise RuntimeError("round28 results must contain exactly five rows")

    normalized: List[Dict[str, Any]] = []
    seen_ranks: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise RuntimeError("round28 result row must be an object")
        rank = int(row["rank"])
        source = source_by_rank.get(rank)
        if source is None or rank in seen_ranks:
            raise RuntimeError(f"invalid or duplicate round28 result rank: {rank}")
        seen_ranks.add(rank)
        if row.get("technical_failure") is not False:
            raise RuntimeError(f"round28 rank {rank} is a technical failure")
        if row.get("valid_replay") is not True:
            raise RuntimeError(f"round28 rank {rank} is not a valid replay")
        if row.get("selection_role") != source.get("selection_role"):
            raise RuntimeError(f"round28 rank {rank} role mismatch")
        if selector.parameter_key(row) != selector.parameter_key(source):
            raise RuntimeError(f"round28 rank {rank} parameter binding mismatch")
        if selector.fingerprint_id(row) != selector.fingerprint_id(source):
            raise RuntimeError(f"round28 rank {rank} fingerprint binding mismatch")
        actual_resource = row.get("actual_resource")
        if not isinstance(actual_resource, Mapping):
            raise RuntimeError(f"round28 rank {rank} lacks actual_resource")
        normalized_resource = {
            name: finite(actual_resource[name], f"rank{rank}.actual_resource.{name}")
            for name in RESOURCES
        }
        if any(value <= 0.0 for value in normalized_resource.values()):
            raise RuntimeError(f"round28 rank {rank} actual_resource must be positive")
        actual_score = finite(row["actual_score"], f"rank{rank}.actual_score")
        if not math.isclose(
            actual_score,
            EXPECTED_ACTUAL_SCORES[rank - 1],
            rel_tol=0.0,
            abs_tol=5e-7,
        ):
            raise RuntimeError(
                f"round28 rank {rank} actual score mismatch: {actual_score}"
            )
        normalized.append(
            {
                **copy.deepcopy(dict(row)),
                "actual_resource": normalized_resource,
                "actual_score": actual_score,
            }
        )
    if seen_ranks != {1, 2, 3, 4, 5}:
        raise RuntimeError("round28 results ranks must be 1..5")
    return sorted(normalized, key=lambda item: int(item["rank"]))


def validate_round28_state(state: Mapping[str, Any]) -> None:
    if (
        state.get("baseline_restored") is not True
        or int(state.get("technical_failure_count", -1)) != 0
        or int(state.get("accepted_count", -1)) != EXPECTED_ACCEPTED_COUNT
        or not math.isclose(
            finite(state.get("incumbent_score"), "state.incumbent_score"),
            EXPECTED_INCUMBENT_SCORE,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError(
            "round28 state must prove baseline restored, tech0, accepted1, "
            "and unchanged incumbent"
        )


def update_exact_regime_registry(
    selection: Mapping[str, Any],
    source_by_rank: Mapping[int, Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    results_sha256: str,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    registry = copy.deepcopy(selection.get("validated_plan_regimes") or {})
    audit: List[Dict[str, Any]] = []
    for result in results:
        rank = int(result["rank"])
        source = source_by_rank[rank]
        role = str(source["selection_role"])
        regime = exact_regime(source)
        raw_prediction = source.get("precalibration_predicted_resource")
        if not isinstance(raw_prediction, Mapping):
            raw_prediction = source.get("predicted_resource")
        if not isinstance(raw_prediction, Mapping):
            raise RuntimeError(f"round28 rank {rank} lacks prediction")
        actual = result["actual_resource"]
        scalar = {
            name: finite(actual[name], f"rank{rank}.actual.{name}")
            / finite(raw_prediction[name], f"rank{rank}.prediction.{name}")
            for name in RESOURCES
        }
        used = role == "trusted"
        audit.append(
            {
                "rank": rank,
                "selection_role": role,
                "structural_plan_regime_id": regime,
                "used_for_exact_regime_calibration": used,
                "probe_updates_forbidden": not used,
                "actual_score": float(result["actual_score"]),
                "actual_resource": copy.deepcopy(actual),
                "raw_predicted_resource": {
                    name: float(raw_prediction[name]) for name in RESOURCES
                },
                "scalar_by_resource": scalar,
            }
        )
        if not used:
            continue
        if regime not in registry:
            raise RuntimeError(
                f"trusted round28 regime is absent from validated registry: {regime}"
            )
        evidence = {
            "event": "tpch_round28_real_replay_complete",
            "source_rank": rank,
            "source_fingerprint": selector.fingerprint_id(source),
            "source_parameter_key": selector.parameter_key(source),
            "ratio_plan_level": selector.plan_level(source),
            "predicted_resource": {
                name: float(raw_prediction[name]) for name in RESOURCES
            },
            "actual_resource": copy.deepcopy(actual),
            "scalar_by_resource": scalar,
            "actual_score": float(result["actual_score"]),
            "source_results_sha256": results_sha256,
        }
        registry[regime].setdefault("evidence", []).append(evidence)

    for regime_id, calibration in registry.items():
        evidence = calibration.get("evidence") or []
        if not evidence:
            raise RuntimeError(f"validated regime has no evidence: {regime_id}")
        calibration["evidence_count"] = len(evidence)
        calibration["scalar_by_resource"] = {
            name: statistics.median(
                finite(item["scalar_by_resource"][name], name)
                for item in evidence
            )
            for name in RESOURCES
        }
        calibration["actual_score_median"] = statistics.median(
            finite(item["actual_score"], "actual_score") for item in evidence
        )
        calibration["evidence_contract"] = (
            "exact_workload_structural_plan_regime_componentwise_median_v3_"
            "round28_trusted_feedback"
        )
        calibration["global_scalar_forbidden"] = True
        calibration["unknown_regime_calibration_forbidden"] = True
        calibration["structural_plan_regime_id"] = regime_id
    return registry, audit


def calibrated_candidate(
    raw: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> Dict[str, Any]:
    item = copy.deepcopy(dict(raw))
    raw_resource = item.get("predicted_resource")
    if not isinstance(raw_resource, Mapping):
        raise RuntimeError("candidate lacks predicted_resource")
    corrected = {
        name: finite(raw_resource[name], f"prediction.{name}")
        * finite(calibration["scalar_by_resource"][name], f"scalar.{name}")
        for name in RESOURCES
    }
    score, utilization = selector.score_resource(corrected)
    item.update(
        {
            "precalibration_predicted_resource": copy.deepcopy(raw_resource),
            "predicted_resource": corrected,
            "final_resource": copy.deepcopy(corrected),
            "candidate_score": score,
            "final_score": score,
            "selection_score": float(calibration["actual_score_median"]),
            "utilization": utilization,
            "structural_plan_regime_id": exact_regime(item),
            "regime_calibration": copy.deepcopy(calibration),
            "three_level_fingerprint": selector.recalibrated_fingerprint(
                item, corrected, calibration
            ),
        }
    )
    return item


def select_diagnostic_top5(
    expanded: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]],
    excluded_ids: set[str],
    excluded_parameter_keys: set[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    eligible: List[Mapping[str, Any]] = []
    for candidate in expanded:
        if candidate.get("boundary_corner") is True:
            continue
        if candidate.get("boundary_plateau") is True:
            continue
        if selector.fingerprint_id(candidate) in excluded_ids:
            continue
        if selector.parameter_key(candidate) in excluded_parameter_keys:
            continue
        exact_regime(candidate)
        eligible.append(candidate)

    trusted_pool: List[Dict[str, Any]] = []
    unknown_by_regime: Dict[str, List[Mapping[str, Any]]] = {}
    for raw in eligible:
        regime = exact_regime(raw)
        if regime in registry:
            trusted_pool.append(calibrated_candidate(raw, registry[regime]))
        else:
            unknown_by_regime.setdefault(regime, []).append(raw)
    trusted_pool.sort(
        key=lambda item: (
            -float(item["selection_score"]),
            -float(item["candidate_score"]),
            selector.fingerprint_id(item),
        )
    )
    if len(trusted_pool) < 3:
        raise RuntimeError(
            "round29 candidate expansion has fewer than three candidates in "
            f"validated exact regimes: trusted={len(trusted_pool)}, "
            f"unknown={sum(len(group) for group in unknown_by_regime.values())}"
        )

    trusted: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_params: set[str] = set()
    for item in trusted_pool:
        fingerprint = selector.fingerprint_id(item)
        parameter_key = selector.parameter_key(item)
        if fingerprint in seen_ids or parameter_key in seen_params:
            continue
        trusted.append(item)
        seen_ids.add(fingerprint)
        seen_params.add(parameter_key)
        if len(trusted) == 3:
            break
    if len(trusted) != 3:
        raise RuntimeError("round29 trusted candidates are not uniquely selectable")

    unknown_representatives = [
        max(
            group,
            key=lambda item: (
                float(item["candidate_score"]),
                selector.fingerprint_id(item),
            ),
        )
        for group in unknown_by_regime.values()
    ]
    if len(unknown_representatives) < 2:
        raise RuntimeError("round29 expansion has fewer than two unknown regimes")
    probes = selector.maximin_probes(unknown_representatives, trusted, 2)

    selected: List[Dict[str, Any]] = []
    for rank, raw in enumerate([*trusted, *probes], 1):
        item = copy.deepcopy(raw)
        trusted_role = rank <= 3
        if not trusted_role:
            _, item["utilization"] = selector.score_resource(
                item["predicted_resource"]
            )
        item.update(
            {
                "predicted_rank": int(item.get("rank", rank)),
                "rank": rank,
                "acquisition_role": (
                    "trusted_validation" if trusted_role else "uncertain_probe"
                ),
                "selection_role": "trusted" if trusted_role else "probe",
                "selection_basis": (
                    "validated_exact_regime_round28_trusted_feedback"
                    if trusted_role
                    else "unknown_exact_regime_noncorner_maximin"
                ),
                "structural_plan_regime_id": exact_regime(item),
                "candidate_trust_confidence": 1.0 if trusted_role else 0.0,
                "candidate_trust_basis": (
                    "validated_exact_plan_regime"
                    if trusted_role
                    else "unknown_plan_regime_probe_only"
                ),
                "excluded_from_spearman": not trusted_role,
                "eligible_for_best_candidate": False,
                "incumbent_update_eligible": False,
                "used_for_inner_incumbent_update": False,
                "diagnostic_only": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "gradient_update_allowed": False,
                "range_update_allowed": False,
                "qps_used_for_inner_scoring": False,
                "boundary_corner": False,
                "boundary_plateau": False,
            }
        )
        missing = [
            field for field in REQUIRED_AUDIT_FIELDS if field not in item
        ]
        if missing:
            raise RuntimeError(f"rank {rank} lacks audit fields: {missing}")
        selected.append(item)

    identifiers = [selector.fingerprint_id(item) for item in selected]
    parameter_keys = [selector.parameter_key(item) for item in selected]
    probe_regimes = [exact_regime(item) for item in selected[3:]]
    if len(set(identifiers)) != 5 or any(
        value in excluded_ids for value in identifiers
    ):
        raise RuntimeError("round29 selected fingerprints are not novel and unique")
    if len(set(parameter_keys)) != 5 or any(
        value in excluded_parameter_keys for value in parameter_keys
    ):
        raise RuntimeError("round29 selected parameters are not novel and unique")
    if len(set(probe_regimes)) != 2 or any(
        value in registry for value in probe_regimes
    ):
        raise RuntimeError("round29 probe exact regimes are not distinct/unknown")

    audit = {
        "expanded_candidate_count": len(expanded),
        "eligible_novel_noncorner_count": len(eligible),
        "eligible_trusted_candidate_count": len(trusted_pool),
        "eligible_unknown_candidate_count": sum(
            len(group) for group in unknown_by_regime.values()
        ),
        "eligible_unknown_regime_count": len(unknown_by_regime),
        "selected_unique_fingerprint_count": len(set(identifiers)),
        "selected_unique_parameter_count": len(set(parameter_keys)),
        "trusted_count": 3,
        "probe_count": 2,
    }
    return selected, audit


def build(args: argparse.Namespace) -> Dict[str, Any]:
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f"refuse to overwrite output: {output}")
    paths = {
        "round28_selection": Path(args.round28_selection),
        "round28_results": Path(args.round28_results),
        "round28_state": Path(args.round28_state),
        "round29_expanded_candidates": Path(args.expanded_candidates),
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required round29 inputs missing: {missing}")

    selection = load(paths["round28_selection"])
    source_by_rank = validate_round28_selection(selection)
    results_document = load(paths["round28_results"])
    results = validate_round28_results(results_document, source_by_rank)
    state = load(paths["round28_state"])
    validate_round28_state(state)
    expansion = load(paths["round29_expanded_candidates"])
    expanded = expansion.get("ranked_candidates")
    if not isinstance(expanded, list) or not expanded:
        raise RuntimeError("round29 expansion has no ranked_candidates")

    registry, feedback_audit = update_exact_regime_registry(
        selection,
        source_by_rank,
        results,
        sha256(paths["round28_results"]),
    )
    replay_exclusion = selection.get("replay_exclusion") or {}
    excluded_ids = set(replay_exclusion.get("fingerprints") or [])
    excluded_parameter_keys = set(replay_exclusion.get("parameter_keys") or [])
    for candidate in selection["top5"]:
        excluded_ids.add(selector.fingerprint_id(candidate))
        excluded_parameter_keys.add(selector.parameter_key(candidate))

    selected, selection_audit = select_diagnostic_top5(
        expanded,
        registry,
        excluded_ids,
        excluded_parameter_keys,
    )
    package = {
        "schema_version": "tpch_round29_corrected_diagnostic_selection_v1",
        "selection_contract": (
            "exact_structural_plan_regime_trusted3_unknown_probe2_top5_"
            "diagnostic_v1"
        ),
        "calibration_contract": (
            "exact_workload_structural_plan_regime_componentwise_median_v3_"
            "round28_trusted_feedback"
        ),
        "formal_continuation": False,
        "diagnostic_only": True,
        "accepted_count_start": EXPECTED_ACCEPTED_COUNT,
        "accepted_shrink_target": 10,
        "maximum_real_replay_invocations": 5,
        "serial_real_replay_required": True,
        "trusted_slots": 3,
        "probe_slots": 2,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "rejected_updates_forbidden": True,
        "qps_used_for_inner_scoring": False,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "round28_feedback_audit": feedback_audit,
        "validated_plan_regimes": registry,
        "selection_audit": selection_audit,
        "novel_unique_fingerprint_count": 5,
        "replay_exclusion": {
            "fingerprint_count": len(excluded_ids),
            "parameter_count": len(excluded_parameter_keys),
            "fingerprints": sorted(excluded_ids),
            "parameter_keys": sorted(excluded_parameter_keys),
        },
        "source_evidence": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in paths.items()
        },
        "top5": selected,
        "ranked_candidates": selected,
    }
    output.mkdir(parents=True)
    atomic_json(output / "top5.json", package)
    manifest = {
        "schema_version": "tpch_round29_corrected_diagnostic_manifest_v1",
        "offline_preparation_only": True,
        "new_ssh_calls": 0,
        "new_workload_calls": 0,
        "diagnostic_state_updates": {
            "accepted": False,
            "incumbent": False,
            "range": False,
            "gradient": False,
        },
        "package_file_sha256": {
            "top5.json": sha256(output / "top5.json"),
        },
        "input_file_sha256": {
            name: sha256(path) for name, path in paths.items()
        },
    }
    atomic_json(output / "manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--round28-selection",
        default="/tmp/tpch_round28_output/top5.json",
    )
    parser.add_argument(
        "--round28-results",
        default="/tmp/tpch_round29_input/round28_merged_results.json",
    )
    parser.add_argument(
        "--round28-state",
        default="/tmp/tpch_round29_input/round28_formal_state.json",
    )
    parser.add_argument(
        "--expanded-candidates",
        default="/tmp/tpch_round29_input/round29_expanded_candidates.json",
    )
    parser.add_argument(
        "--output",
        default="/tmp/tpch_round29_corrected_output",
    )
    return parser.parse_args()


def main() -> int:
    manifest = build(parse_args())
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
