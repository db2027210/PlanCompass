#!/usr/bin/env python3
"""Build a TPC-H probe5 batch that avoids known zero-completion Q1 plans."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import tpch_round19_plan_regime_select as selector


SOURCE_SHA256 = (
    "1f838c18899dea7911c3966141d02085fc2af3fa6ce2a6f4d290f536aec780bf"
)
FAILED_Q1_PLAN = (
    "exact-structure-sha256:"
    "dfdac3493224a6124df9c645d4a8a01c06264f0317e8a985d80b8c48772abe2c"
)
SELECTED_INDICES = (1, 19, 7, 22, 23)
MAX_Q1_IO_PAGE_PROXY = 300_000.0
# Changes below one millionth of the configured range are serialization/
# floating-point residue, not a distinct optimizer action.
SEMANTIC_NOOP_NORMALIZED_DELTA = 1e-6
REQUIRED_FIELDS = (
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


def is_actual_non_corner(
    candidate: Mapping[str, Any],
    param_ranges: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Recompute parameter-space corner status without conflating plateaus."""
    params = candidate.get("candidate_params") or candidate.get(
        "optimized_params"
    )
    initial = candidate.get("initial_params") or {}
    if not isinstance(params, Mapping) or not isinstance(initial, Mapping):
        return False, {"reason": "missing_parameter_mapping"}
    changed: dict[str, Any] = {}
    ignored_noops: dict[str, Any] = {}
    for name, bounds in param_ranges.items():
        if name not in params or name not in initial:
            return False, {"reason": f"missing_parameter:{name}"}
        lower = float(bounds["min_value"])
        upper = float(bounds["max_value"])
        value = float(params[name])
        start = float(initial[name])
        if not all(math.isfinite(item) for item in (lower, upper, value, start)):
            return False, {"reason": f"non_finite_parameter:{name}"}
        if not lower < upper or not lower <= value <= upper:
            return False, {"reason": f"parameter_out_of_range:{name}"}
        if name in selector.LOG_TRANSFORM_PARAMS:
            if lower <= 0.0 or value <= 0.0 or start <= 0.0:
                return False, {"reason": f"non_positive_log_parameter:{name}"}
            span = math.log(upper) - math.log(lower)
            fraction = (math.log(value) - math.log(lower)) / span
            normalized_delta = abs(math.log(value) - math.log(start)) / span
        else:
            span = upper - lower
            fraction = (value - lower) / span
            normalized_delta = abs(value - start) / span
        if normalized_delta <= SEMANTIC_NOOP_NORMALIZED_DELTA:
            ignored_noops[name] = {
                "normalized_delta": normalized_delta,
                "value": value,
                "initial": start,
            }
            continue
        changed[name] = {
            "normalized_delta": normalized_delta,
            "range_fraction": fraction,
            "value": value,
            "initial": start,
        }
        if not (
            selector.NON_CORNER_MARGIN
            <= fraction
            <= 1.0 - selector.NON_CORNER_MARGIN
        ):
            return False, {
                "reason": f"changed_parameter_near_boundary:{name}",
                "changed": changed,
                "ignored_numerical_noops": ignored_noops,
            }
    return bool(changed), {
        "reason": "actual_parameter_space_noncorner" if changed else "no_change",
        "margin": selector.NON_CORNER_MARGIN,
        "semantic_noop_normalized_delta": SEMANTIC_NOOP_NORMALIZED_DELTA,
        "changed": changed,
        "ignored_numerical_noops": ignored_noops,
        "source_boundary_plateau": candidate.get("boundary_plateau") is True,
        "source_boundary_corner": candidate.get("boundary_corner") is True,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("prior_selection", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if sha256(args.source) != SOURCE_SHA256:
        raise RuntimeError("source expansion SHA mismatch")
    source = load_json(args.source)
    prior = load_json(args.prior_selection)
    config = load_json(args.config)
    pool = source.get("ranked_candidates") or []
    if len(pool) <= max(SELECTED_INDICES):
        raise RuntimeError("source candidate pool is incomplete")
    failed_ids = {
        item["three_level_fingerprint"]["id"]
        for item in prior.get("top5") or []
    }
    selected = []
    risk_audit = []
    for rank, index in enumerate(SELECTED_INDICES, 1):
        item = copy.deepcopy(pool[index])
        query_features = (
            item.get("final_estimation_metadata", {})
            .get("query_features", [])
        )
        if len(query_features) != 22:
            raise RuntimeError(f"candidate {index} query coverage mismatch")
        first = query_features[0]
        io = first.get("io") or {}
        risk = sum(
            float(io.get(name) or 0.0)
            for name in (
                "io_rand_pages",
                "io_seq_pages",
                "io_spill_pages",
            )
        )
        plan = str(first.get("plan_fingerprint") or "")
        fingerprint = item["three_level_fingerprint"]["id"]
        actual_non_corner, non_corner_audit = is_actual_non_corner(
            item,
            config["param_ranges"],
        )
        if (
            plan == FAILED_Q1_PLAN
            or risk > MAX_Q1_IO_PAGE_PROXY
            or fingerprint in failed_ids
            or not actual_non_corner
        ):
            raise RuntimeError(
                f"candidate {index} violates Q1 completion-risk gate"
            )
        if item.get("utilization") is None:
            predicted = item.get("predicted_resource") or {}
            capability = config.get("capability") or {}
            item["utilization"] = {
                resource: float(predicted[resource])
                / float(capability[resource])
                for resource in ("cpu", "io", "memory")
            }
            item["utilization_derivation"] = {
                "contract": "predicted_resource_divided_by_capability_v1",
                "capability": {
                    resource: float(capability[resource])
                    for resource in ("cpu", "io", "memory")
                },
            }
        missing = [field for field in REQUIRED_FIELDS if field not in item]
        if missing:
            raise RuntimeError(
                f"candidate {index} missing diagnostics: {missing}"
            )
        item.update(
            {
                "rank": rank,
                "predicted_rank": int(item.get("rank", index + 1)),
                "selection_role": "probe",
                "acquisition_role": "uncertain_probe",
                "selection_basis": (
                    "q1_completion_risk_gated_noncorner_probe"
                ),
                "candidate_trust_confidence": 0.0,
                "candidate_trust_basis": (
                    "current_22core_envelope_unvalidated_probe_only"
                ),
                "structural_plan_regime_id": (
                    selector.structural_plan_regime_id(item)
                ),
                "excluded_from_spearman": True,
                "eligible_for_best_candidate": False,
                "incumbent_update_eligible": False,
                "used_for_inner_incumbent_update": False,
                "diagnostic_only": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "qps_used_for_inner_scoring": False,
                "boundary_corner": False,
                "q1_completion_risk": {
                    "plan_fingerprint": plan,
                    "io_page_proxy": risk,
                    "maximum_allowed": MAX_Q1_IO_PAGE_PROXY,
                    "known_zero_completion_plan_excluded": True,
                    "resource_envelope": {
                        "cpu_cores": 22,
                        "memory_bytes": 1_730_000_000,
                        "io_read_bytes_per_second": 16_777_216,
                        "io_write_bytes_per_second": 20_971_520,
                    },
                },
                "actual_parameter_noncorner_audit": non_corner_audit,
            }
        )
        selected.append(item)
        risk_audit.append(
            {
                "rank": rank,
                "source_index": index,
                "fingerprint": fingerprint,
                "q1_plan": plan,
                "q1_io_page_proxy": risk,
                "actual_parameter_noncorner_audit": non_corner_audit,
                "structural_plan_regime_id": item[
                    "structural_plan_regime_id"
                ],
            }
        )
    identifiers = [
        item["three_level_fingerprint"]["id"] for item in selected
    ]
    parameters = [selector.parameter_key(item) for item in selected]
    if len(set(identifiers)) != 5 or len(set(parameters)) != 5:
        raise RuntimeError("probe5 fingerprints or parameters are not unique")
    output = {
        "schema_version": "tpch_q1_completion_risk_probe5_v1",
        "selection_contract": (
            "tpch_q1_completion_risk_probe5_discovery_v1"
        ),
        "formal_continuation": False,
        "trusted_slots": 0,
        "probe_slots": 5,
        "maximum_real_replay_invocations": 5,
        "serial_real_replay_required": True,
        "diagnostic_only": True,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "accepted_shrink_target": 10,
        "accepted_count_start": 1,
        "novel_unique_fingerprint_count": 5,
        "source_evidence": {
            "expansion_path": str(args.source.resolve()),
            "expansion_sha256": sha256(args.source),
            "prior_selection_path": str(args.prior_selection.resolve()),
            "prior_selection_sha256": sha256(args.prior_selection),
        },
        "q1_completion_risk_policy": {
            "failed_plan_fingerprint": FAILED_Q1_PLAN,
            "failed_plan_observation": (
                "two physical calls, zero valid replays, zero completed "
                "queries in stable 180-second windows"
            ),
            "maximum_io_page_proxy": MAX_Q1_IO_PAGE_PROXY,
            "risk_reduction_vs_failed_plan": "at_least_500x",
            "selection_indices": list(SELECTED_INDICES),
            "audit": risk_audit,
        },
        "validated_plan_regimes": copy.deepcopy(
            prior.get("validated_plan_regimes") or {}
        ),
        "top5": selected,
        "ranked_candidates": copy.deepcopy(selected),
    }
    atomic_json(args.output, output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": sha256(args.output),
                "roles": [
                    item["selection_role"] for item in selected
                ],
                "fingerprints": identifiers,
                "q1_io_page_proxy": [
                    row["q1_io_page_proxy"] for row in risk_audit
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
