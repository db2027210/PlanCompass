#!/usr/bin/env python3
"""Prepare, but never launch, a SHA-bound trusted3-only formal handoff."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import audit_tpcc_v43_postrun_gate as gate_module
    import build_tpcc_unique_exact_validation_v6 as v6
    from tpcc_score_consistent_representative import score_consistent_representative
except ModuleNotFoundError:
    from tools import audit_tpcc_v43_postrun_gate as gate_module
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools.tpcc_score_consistent_representative import score_consistent_representative


SCHEMA = "tpcc_v43_trusted3_sha_bound_handoff_seal_v1"


def _require_sha(path: Path, expected: str, label: str) -> None:
    if (
        len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
        or not path.is_file()
        or v6._sha256(path) != expected
    ):
        raise RuntimeError(f"TPC-C v43 {label} SHA mismatch")


def _descriptor(path: Path) -> Dict[str, str]:
    return {"path": str(path.resolve()), "sha256": v6._sha256(path.resolve())}


def _maximum_median(scores: list[float]) -> float:
    ordered = sorted(scores)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return ordered[midpoint]
    return (ordered[midpoint] + ordered[midpoint + 1]) / 2.0


def _promote_trusted3(
    diagnostic_selection: Mapping[str, Any],
    diagnostic_ledger: Mapping[str, Any],
    gate: Mapping[str, Any],
    selection_path: Path,
    incumbent_score: float,
) -> Dict[str, Any]:
    if (
        gate.get("passed") is not True
        or gate.get("formal_start_authorized") is not True
        or gate.get("selection_sha256") != v6._sha256(selection_path)
    ):
        raise RuntimeError("TPC-C v43 gate does not authorize trusted3 promotion")
    observations = {
        int(item["rank"]): item for item in diagnostic_ledger.get("successful") or []
    }
    if (
        int(diagnostic_ledger.get("real_workload_invocation_count", -1)) != 5
        or set(observations) != {1, 2, 3, 4, 5}
        or diagnostic_ledger.get("technical_failures") not in ([], None)
    ):
        raise RuntimeError("TPC-C v43 formal promotion requires clean5 evidence")
    promoted = copy.deepcopy(dict(diagnostic_selection))
    trusted = copy.deepcopy((diagnostic_selection.get("top5") or [])[:3])
    feasibility = []
    for candidate in trusted:
        rank = int(candidate["rank"])
        observation = observations[rank]
        if (
            candidate.get("selection_role") != "trusted"
            or observation.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or (observation.get("three_level_fingerprint") or {}).get("id")
            != (candidate.get("three_level_fingerprint") or {}).get("id")
        ):
            raise RuntimeError(f"TPC-C v43 trusted promotion identity mismatch: rank={rank}")
        prior = list((candidate.get("source_provenance") or {}).get("trusted_observations") or [])
        if len(prior) < 2 or any(
            row.get("trusted_training_eligible") is not True for row in prior
        ):
            raise RuntimeError(f"TPC-C v43 trusted rank lacks two prior exact rows: rank={rank}")
        scores = [float(row["actual_score"]) for row in prior]
        resources = [
            {name: float(row["actual_resource"][name]) for name in v6.RESOURCES}
            for row in prior
        ]
        scores.append(float(observation["actual_score"]))
        resources.append(
            {name: float(observation["actual_resource"][name]) for name in v6.RESOURCES}
        )
        representative, robust_score, representative_audit = (
            score_consistent_representative(scores, resources, v6._score)
        )
        utilization = {
            name: representative[name] / v6.repeat_v2.CAPABILITY[name]
            for name in v6.RESOURCES
        }
        fingerprint = v6._make_fingerprint(candidate, representative, utilization)
        lower = min(scores)
        upper = max(scores)
        maximum_after_one = _maximum_median(scores)
        candidate.update(
            {
                "independent_actual_scores": scores,
                "independent_actual_resources": resources,
                "predicted_resource": representative,
                "final_resource": copy.deepcopy(representative),
                "uncalibrated_predicted_resource": copy.deepcopy(representative),
                "utilization": utilization,
                "predicted_score": robust_score,
                "candidate_score": robust_score,
                "candidate_score_recomputed": robust_score,
                "final_score": robust_score,
                "robust_acceptance_score": robust_score,
                "formal_lcb_score": lower,
                "predicted_score_interval": {
                    "lower": lower,
                    "center": robust_score,
                    "upper": upper,
                    "source": "v43_trusted_exact_evidence_only",
                },
                "maximum_robust_acceptance_score_after_one_observation": maximum_after_one,
                "three_level_fingerprint": fingerprint,
                "selection_role": "trusted",
                "trusted": True,
                "uncertain_probe": False,
                "diagnostic_only": True,
            }
        )
        candidate["confidence"] = {
            "contract": "tpcc_v43_formal_trusted_exact_confidence_v1",
            "measurement_count": len(scores),
            "independent_actual_scores": scores,
            "independent_repeat_complete": 1.0,
            "score_lower": lower,
            "score_median": robust_score,
            "score_upper": upper,
            "probe_rows_used_for_trusted_calibration": 0,
            "score_consistent_representative_audit": representative_audit,
        }
        calibration = copy.deepcopy(candidate.get("plan_regime_calibration") or {})
        calibration.update(
            {
                "contract": "tpcc_v43_formal_trusted_exact_calibration_v1",
                "actual_resource": representative,
                "actual_score": robust_score,
                "observation_count": len(scores),
                "trusted_eligible": True,
                "probe_rows_used_for_trusted_calibration": 0,
            }
        )
        candidate["plan_regime_calibration"] = calibration
        feasible = maximum_after_one > incumbent_score + 1e-12
        feasibility.append(
            {
                "rank": rank,
                "selection_role": "trusted",
                "three_level_fingerprint": fingerprint["id"],
                "sealed_scores": scores,
                "maximum_median_after_one_observation": maximum_after_one,
                "trusted_eligible": True,
                "feasible": feasible,
            }
        )
    if len({item["three_level_fingerprint"]["id"] for item in trusted}) != 3:
        raise RuntimeError("TPC-C v43 promoted trusted fingerprints are not unique")
    feasible_ranks = [row["rank"] for row in feasibility if row["feasible"]]
    if not feasible_ranks:
        raise RuntimeError("TPC-C v43 no trusted candidate can beat incumbent median")
    promoted.update(
        {
            "schema_version": "tpcc_v43_promoted_formal_trusted3_selection_v1",
            "diagnostic_only": False,
            "top5": trusted,
            "formal_acceptance_policy": "median_of_at_least_three_independent_exact_point_actual_scores_v3",
            "formal_acceptance_feasibility_gate": {
                "contract": "trusted_only_one_additional_observation_median_upper_bound_v2",
                "incumbent_score": incumbent_score,
                "candidate_bounds": feasibility,
                "feasible_ranks": feasible_ranks,
                "passed": True,
                "probe_upper_bounds_affect_gate": False,
            },
            "formal_expected_improvement_required": False,
            "formal_non_improving_candidate_policy": "real_replay_rejected_without_state_update",
            "formal_admission_score": "predicted_score_interval.lower",
            "probe_observations_affect_formal_acceptance": False,
            "promoted_from_diagnostic_validation": {
                "selection_path": str(selection_path.resolve()),
                "selection_sha256": v6._sha256(selection_path),
                "quality_gate": copy.deepcopy(dict(gate)),
                "observation_count_per_candidate": min(
                    len(item["independent_actual_scores"]) for item in trusted
                ),
            },
            "v43_formal_trusted3_contract": {
                "contract": "tpcc_v43_formal_trusted3_only_v1",
                "formal_trusted_ranks": [1, 2, 3],
                "probe_source_ranks_excluded": [4, 5],
                "probe_formal_call_count": 0,
                "probe_formal_eligible": False,
                "probe_gradient_eligible": False,
                "probe_incumbent_eligible": False,
                "historical_probe_roles_rewritten": False,
            },
        }
    )
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "ledger", "events", "summary"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--expected-{name}-sha256", required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--formal-runner", type=Path, required=True)
    parser.add_argument("--expected-formal-runner-sha256", required=True)
    parser.add_argument("--diagnostic-runner", type=Path, required=True)
    parser.add_argument("--expected-diagnostic-runner-sha256", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--expected-baseline-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--formal-batch-root", type=Path, required=True)
    args = parser.parse_args()
    for label in (
        "selection", "ledger", "events", "summary", "formal_state",
        "formal_runner", "diagnostic_runner", "baseline",
    ):
        _require_sha(
            getattr(args, label).resolve(),
            getattr(args, f"expected_{label}_sha256"),
            label,
        )
    output_root = args.output_root.resolve()
    if output_root.exists() or args.formal_batch_root.resolve().exists():
        raise FileExistsError("TPC-C v43 handoff/formal batch root already exists")
    output_root.mkdir(parents=True)
    gate = gate_module.audit_v43(
        args.selection.resolve(), args.ledger.resolve(), args.events.resolve(),
        args.summary.resolve(), args.formal_state.resolve(),
        args.expected_formal_state_sha256,
    )
    gate_path = output_root / "postrun_gate.json"
    gate_module.durable._atomic_json(gate_path, gate)
    if gate.get("passed") is not True:
        print(json.dumps({"status": "gate_failed", "gate_path": str(gate_path)}, sort_keys=True), flush=True)
        return 3
    formal_state = v6._load(args.formal_state.resolve())
    selection = _promote_trusted3(
        v6._load(args.selection.resolve()), v6._load(args.ledger.resolve()), gate,
        args.selection.resolve(), float(formal_state["incumbent_score"]),
    )
    formal_selection_path = output_root / "formal_trusted3.json"
    v6.repeat_v2.import_base().atomic_json(formal_selection_path, selection)
    formal_selection_sha = v6._sha256(formal_selection_path)
    formal_gate = {
        "schema_version": "tpcc_v43_trusted3_sha_bound_formal_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "selection_sha256": formal_selection_sha,
        "source_diagnostic_selection_sha256": gate["selection_sha256"],
        "expected_formal_state_sha256": args.expected_formal_state_sha256,
        "formal_runner_sha256": args.expected_formal_runner_sha256,
        "diagnostic_runner_sha256": args.expected_diagnostic_runner_sha256,
        "baseline_sha256": args.expected_baseline_sha256,
        "formal_trusted_ranks": [1, 2, 3],
        "probe_formal_call_count": 0,
        "quality_gate": copy.deepcopy(gate["quality_gate"]),
        "selection_algorithm_gate": copy.deepcopy(gate["selection_algorithm_gate"]),
        "score_span_gate": copy.deepcopy(gate["score_span_gate"]),
        "qps_role": "outer_audit_only_not_inner_score",
    }
    formal_gate_path = output_root / "formal_gate.json"
    gate_module.durable._atomic_json(formal_gate_path, formal_gate)
    seal = {
        "schema_version": SCHEMA,
        "prepared_not_launched": True,
        "workload_calls_added_during_preparation": 0,
        "expected_formal_state_sha256": args.expected_formal_state_sha256,
        "formal_batch_root": str(args.formal_batch_root.resolve()),
        "formal_trusted_ranks": [1, 2, 3],
        "probe_formal_call_count": 0,
        "files": {
            "diagnostic_selection": _descriptor(args.selection),
            "diagnostic_ledger": _descriptor(args.ledger),
            "diagnostic_events": _descriptor(args.events),
            "diagnostic_summary": _descriptor(args.summary),
            "formal_state": _descriptor(args.formal_state),
            "postrun_gate": _descriptor(gate_path),
            "formal_selection": _descriptor(formal_selection_path),
            "formal_gate": _descriptor(formal_gate_path),
            "formal_runner": _descriptor(args.formal_runner),
            "diagnostic_runner": _descriptor(args.diagnostic_runner),
            "baseline": _descriptor(args.baseline),
        },
    }
    seal_path = output_root / "handoff_seal.json"
    gate_module.durable._atomic_json(seal_path, seal)
    for label, descriptor in seal["files"].items():
        _require_sha(Path(descriptor["path"]), descriptor["sha256"], f"sealed {label}")
    print(json.dumps({"status": "prepared_not_launched", "seal": str(seal_path)}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
