#!/usr/bin/env python3
"""Build a fresh TPC-C v38 diagnostic Top-5 after quarantined v37 evidence."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict

try:
    import build_tpcc_v37_exact_drift_validation as v37
    from tpcc_v38_replay_policy import CORRECTNESS_POLICY_SHA256
except ModuleNotFoundError:
    from tools import build_tpcc_v37_exact_drift_validation as v37
    from tools.tpcc_v38_replay_policy import CORRECTNESS_POLICY_SHA256


SELECTION_CONTRACT = "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
BUILD_CONTRACT = "tpcc_v38_zero_call_fresh_unique_build_v1"


def load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def build(
    source_selection: Dict[str, Any],
    *,
    source_selection_path: Path,
    source_ledger_path: Path,
    quarantined_selection_path: Path,
    quarantined_ledger_path: Path,
    quarantine_report_path: Path,
) -> Dict[str, Any]:
    quarantined_selection = load_object(quarantined_selection_path)
    quarantined_ledger = load_object(quarantined_ledger_path)
    quarantine_report = load_object(quarantine_report_path)
    prior_top5 = quarantined_selection.get("top5") or []
    prior_ids = {
        str(item["three_level_fingerprint"]["id"])
        for item in prior_top5
    }
    if len(prior_top5) != 5 or len(prior_ids) != 5:
        raise RuntimeError("quarantined v37 selection is not a unique Top-5")
    if (
        quarantine_report.get("status") != "reclassified"
        or int(quarantine_report.get("physical_call_count", -1)) != 5
        or int(quarantine_report.get("unique_valid_replay_count", -1)) != 3
        or int(quarantine_report.get("technical_failure_count", -1)) != 0
        or quarantine_report.get("formal_start_authorized") is not False
        or int(quarantine_report.get("new_real_workloads_executed", -1)) != 0
        or v37.v6._sha256(quarantined_ledger_path)
        != quarantine_report.get("ledger_sha256")
    ):
        raise RuntimeError("v37 quarantine report does not match the sealed failed batch")

    result = v37.build(
        source_selection,
        source_selection_path=source_selection_path,
        source_ledger_path=source_ledger_path,
        excluded_fingerprint_ids=sorted(prior_ids),
        drift_selection=quarantined_selection,
        drift_ledger=quarantined_ledger,
        defer_trusted_fingerprint_novelty=True,
    )
    result = copy.deepcopy(result)
    prior_candidates = {
        v37.v6._point_key(
            v37.v6._params(item), str(item["workload_plan_fingerprint"])
        ): item
        for item in prior_top5[:3]
    }
    prior_observations = {
        int(item["rank"]): item
        for item in quarantined_ledger.get("successful") or []
    }
    if set(prior_observations) != {1, 2, 3}:
        raise RuntimeError("v37 quarantine lacks canonical trusted ranks 1..3")
    current_exact_observations: Dict[str, Dict[str, Any]] = {}
    for rank, observation in prior_observations.items():
        candidate = prior_top5[rank - 1]
        key = v37.v6._point_key(
            v37.v6._params(candidate), str(candidate["workload_plan_fingerprint"])
        )
        if key not in prior_candidates:
            raise RuntimeError("v37 trusted observation is not exact-point bound")
        current_exact_observations[key] = observation

    for candidate in result["top5"][:3]:
        key = str(candidate["exact_point_key"])
        observation = current_exact_observations.get(key)
        if observation is None:
            raise RuntimeError("v38 trusted point lacks current exact canonical evidence")
        latest_resource = {
            name: float(observation["actual_resource"][name])
            for name in v37.RESOURCES
        }
        latest_utilization = {
            name: latest_resource[name] / v37.v6.repeat_v2.CAPABILITY[name]
            for name in v37.RESOURCES
        }
        latest_score = v37.v6._score(latest_resource)
        old_interval = candidate.get("predicted_score_interval") or {}
        old_center = float(old_interval.get("center", candidate["candidate_score"]))
        old_lower = float(old_interval.get("lower", old_center))
        margin = max(old_center - old_lower, abs(latest_score - old_center))
        candidate.update(
            {
                "candidate_score": latest_score,
                "candidate_score_recomputed": latest_score,
                "predicted_score": latest_score,
                "final_score": latest_score,
                "formal_lcb_score": latest_score - margin,
                "robust_acceptance_score": latest_score - margin,
                "predicted_resource": latest_resource,
                "final_resource": copy.deepcopy(latest_resource),
                "utilization": latest_utilization,
                "three_level_fingerprint": v37.v6._make_fingerprint(
                    candidate, latest_resource, latest_utilization
                ),
                "prediction_source": (
                    "current_exact_canonical_observation_with_historical_loo_v38"
                ),
                "predicted_score_interval": {
                    "lower": latest_score - margin,
                    "center": latest_score,
                    "upper": latest_score + margin,
                    "source": "current_exact_canonical_plus_historical_loo_v38",
                },
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v38_current_exact_canonical_confidence_v1",
                "current_exact_canonical_observation": True,
                "current_observation_rank": int(observation["rank"]),
                "current_observation_summary_sha256": (
                    observation.get("measurement_protocol") or {}
                ).get("validated_summary_sha256"),
                "score_lower": latest_score - margin,
                "score_center": latest_score,
                "score_upper": latest_score + margin,
            }
        )
        candidate["confidence"] = confidence
        candidate["source_provenance"]["v37_current_exact_canonical_observation"] = {
            "rank": int(observation["rank"]),
            "actual_score": float(observation["actual_score"]),
            "actual_resource": latest_resource,
            "summary_sha256": (
                observation.get("measurement_protocol") or {}
            ).get("validated_summary_sha256"),
        }

    trusted = sorted(
        result["top5"][:3],
        key=lambda item: (-float(item["formal_lcb_score"]), str(item["exact_point_key"])),
    )
    result["top5"] = trusted + result["top5"][3:]
    for rank, candidate in enumerate(result["top5"], 1):
        candidate["rank"] = rank
    result["schema_version"] = "tpcc_fresh_unique_validation_v38"
    result["selection_contract"] = SELECTION_CONTRACT
    result["diagnostic_only"] = True
    result["maximum_real_workload_invocations"] = 5
    result["state_mutation_contract"] = {
        "accepted_update": False,
        "gradient_update": False,
        "incumbent_update": False,
        "range_update": False,
    }
    selected_ids = {
        str(item["three_level_fingerprint"]["id"])
        for item in result["top5"]
    }
    all_prior_ids = v37._historical_fingerprint_ids(result["source_batches"]) | prior_ids
    roles = [item["selection_role"] for item in result["top5"]]
    if (
        len(selected_ids) != 5
        or selected_ids.intersection(all_prior_ids)
        or roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or any(item.get("boundary_corner") is True for item in result["top5"])
    ):
        raise RuntimeError("v38 fresh unique role/fingerprint gate failed")
    result["v38_build_contract"] = {
        "contract": BUILD_CONTRACT,
        "source_selection_path": str(source_selection_path.resolve()),
        "source_selection_sha256": v37.v6._sha256(source_selection_path.resolve()),
        "source_ledger_path": str(source_ledger_path.resolve()),
        "source_ledger_sha256": v37.v6._sha256(source_ledger_path.resolve()),
        "quarantined_selection_path": str(quarantined_selection_path.resolve()),
        "quarantined_selection_sha256": v37.v6._sha256(
            quarantined_selection_path.resolve()
        ),
        "quarantined_ledger_path": str(quarantined_ledger_path.resolve()),
        "quarantined_ledger_sha256": v37.v6._sha256(
            quarantined_ledger_path.resolve()
        ),
        "quarantine_report_path": str(quarantine_report_path.resolve()),
        "quarantine_report_sha256": v37.v6._sha256(quarantine_report_path.resolve()),
        "excluded_v37_fingerprint_ids": sorted(prior_ids),
        "all_historical_fingerprint_count": len(all_prior_ids),
        "new_v38_fingerprint_ids": sorted(selected_ids),
        "five_new_noncorner_fingerprints": True,
        "roles": roles,
        "correctness_policy_sha256": CORRECTNESS_POLICY_SHA256,
        "same_rank_retry_allowed": False,
        "technical_call_advances_to_next_unique_rank": True,
        "qps_role": "outer_audit_only_not_inner_score",
        "state_neutral": True,
        "physical_workload_calls_added": 0,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-selection", type=Path, required=True)
    parser.add_argument("--source-ledger", type=Path, required=True)
    parser.add_argument("--quarantined-selection", type=Path, required=True)
    parser.add_argument("--quarantined-ledger", type=Path, required=True)
    parser.add_argument("--quarantine-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        load_object(args.source_selection.resolve()),
        source_selection_path=args.source_selection.resolve(),
        source_ledger_path=args.source_ledger.resolve(),
        quarantined_selection_path=args.quarantined_selection.resolve(),
        quarantined_ledger_path=args.quarantined_ledger.resolve(),
        quarantine_report_path=args.quarantine_report.resolve(),
    )
    v37.v6.repeat_v2.import_base().atomic_json(args.output.resolve(), result)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "roles": [item["selection_role"] for item in result["top5"]],
                "fingerprints": [
                    item["three_level_fingerprint"]["id"] for item in result["top5"]
                ],
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
