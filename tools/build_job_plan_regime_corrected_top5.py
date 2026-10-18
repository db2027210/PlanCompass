#!/usr/bin/env python3
"""Build a JOB diagnostic Top-5 from exact validated plan regimes.

The three trusted points are replayed calibration anchors selected to maximize
the minimum observed-score separation.  The two probes come from previously
unseen exact plan regimes.  The output is diagnostic-only and therefore cannot
update accepted, incumbent, range, or gradient state.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


RESOURCE_NAMES = ("cpu", "io", "memory")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def fingerprint(candidate: Mapping[str, Any]) -> Tuple[str, str]:
    value = candidate.get("three_level_fingerprint")
    if not isinstance(value, Mapping):
        raise ValueError("candidate lacks three_level_fingerprint")
    identifier = str(value.get("id") or "")
    plan = str(value.get("plan_level") or "")
    if not identifier.startswith("three-level-sha256:"):
        raise ValueError("candidate has invalid three-level fingerprint")
    if not plan.startswith("ratio-plan-sha256:"):
        raise ValueError("candidate has invalid exact plan regime")
    return identifier, plan


def choose_trusted(rows: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    if len(rows) < 3:
        raise ValueError("at least three successful probe rows are required")
    candidates = []
    for combination in itertools.combinations(rows, 3):
        scores = sorted(finite(row["actual_score"], "actual_score") for row in combination)
        minimum_gap = min(scores[1] - scores[0], scores[2] - scores[1])
        spread = scores[2] - scores[0]
        identifiers = tuple(sorted(str(row["fingerprint_id"]) for row in combination))
        candidates.append((minimum_gap, spread, identifiers, combination))
    _, _, _, selected = max(candidates, key=lambda item: (item[0], item[1], item[2]))
    return sorted(selected, key=lambda row: -finite(row["actual_score"], "actual_score"))


def build(args: argparse.Namespace) -> Dict[str, Any]:
    bundle_root = args.bundle_root.resolve()
    sys.path.insert(0, str(bundle_root))
    import job_round8_autonomous_controller as candidate_tools  # pylint: disable=import-outside-toplevel
    from job_round8_strict_replay_contract import (  # pylint: disable=import-outside-toplevel
        REPLAY_CONTRACT,
        SELECTION_CONTRACT,
        validate_selection,
    )

    ranking_path = args.ranking.resolve()
    previous_selection_path = args.previous_selection.resolve()
    replay_summary_path = args.replay_summary.resolve()
    state_path = args.state.resolve()
    exclusion_path = args.exclusion.resolve()
    ranking = read_json(ranking_path)
    previous_selection = read_json(previous_selection_path)
    replay_summary = read_json(replay_summary_path)
    state = read_json(state_path)
    exclusion = read_json(exclusion_path)
    ranked = ranking.get("ranked_candidates")
    previous_top5 = previous_selection.get("top5")
    successful = replay_summary.get("successful")
    if not isinstance(ranked, list) or not isinstance(previous_top5, list):
        raise ValueError("ranking or previous selection lacks candidate lists")
    if not isinstance(successful, list) or len(successful) != 5:
        raise ValueError("replay summary must contain exactly five successful rows")
    if replay_summary.get("technical_failure_count") != 0:
        raise ValueError("replay summary contains a technical failure")
    if replay_summary.get("baseline_params_restored") is not True:
        raise ValueError("baseline was not restored")
    if replay_summary.get("diagnostic_only") is not True:
        raise ValueError("source replay was not diagnostic-only")
    expected_flags = {"accepted": False, "incumbent": False, "range": False, "gradient": False}
    if replay_summary.get("formal_update_flags") != expected_flags:
        raise ValueError("source replay formal update flags are unsafe")
    if state.get("accepted_count") != 0:
        raise ValueError("JOB authoritative accepted_count must remain zero")

    selected_by_rank = {int(item["rank"]): item for item in previous_top5}
    calibration_rows = []
    for row in successful:
        rank = int(row["rank"])
        candidate = selected_by_rank.get(rank)
        if candidate is None or row.get("technical_failure") is not False:
            raise ValueError(f"successful rank {rank} is inconsistent")
        identifier, plan = fingerprint(candidate)
        actual_resource = row.get("actual_resource")
        predicted_resource = row.get("predicted_resource")
        if not isinstance(actual_resource, Mapping) or not isinstance(predicted_resource, Mapping):
            raise ValueError(f"rank {rank} lacks resource evidence")
        scalar = {
            name: finite(actual_resource[name], f"rank {rank} actual {name}")
            / finite(predicted_resource[name], f"rank {rank} predicted {name}")
            for name in RESOURCE_NAMES
        }
        calibration_rows.append(
            {
                "source_rank": rank,
                "fingerprint_id": identifier,
                "exact_plan_regime": plan,
                "actual_score": finite(row["actual_score"], f"rank {rank} actual_score"),
                "actual_resource": {name: finite(actual_resource[name], name) for name in RESOURCE_NAMES},
                "predicted_resource": {
                    name: finite(predicted_resource[name], name) for name in RESOURCE_NAMES
                },
                "resource_scalar": scalar,
            }
        )

    trusted_rows = choose_trusted(calibration_rows)
    trusted_ids = {row["fingerprint_id"] for row in trusted_rows}
    validated_plans = {row["exact_plan_regime"] for row in calibration_rows}
    replayed_ids = {row["fingerprint_id"] for row in calibration_rows}
    excluded_ids = set()
    if isinstance(exclusion, Mapping):
        for key in (
            "fingerprints",
            "round7_replayed_three_level_fingerprints",
        ):
            values = exclusion.get(key)
            if isinstance(values, list):
                excluded_ids.update(str(value) for value in values)
    ranking_sha = sha256_file(ranking_path)

    selected: List[Dict[str, Any]] = []
    for rank, calibration in enumerate(trusted_rows, 1):
        source = next(
            item for item in previous_top5
            if fingerprint(item)[0] == calibration["fingerprint_id"]
        )
        candidate = copy.deepcopy(source)
        candidate["rank"] = rank
        candidate["selection_role"] = "trusted"
        candidate["used_for_gradient"] = True
        candidate["used_for_inner_incumbent_update"] = True
        candidate["incumbent_update_eligible"] = True
        candidate["calibrated_candidate_score"] = calibration["actual_score"]
        candidate["selection_score"] = calibration["actual_score"]
        candidate["selection_reason"] = "exact_validated_plan_regime_replay_anchor"
        candidate["exact_plan_regime_calibration"] = copy.deepcopy(calibration)
        selected.append(candidate)

    probes = []
    for source_rank, raw in enumerate(ranked, 1):
        try:
            candidate = candidate_tools.normalize_candidate(raw, source_rank)
            identifier, plan = fingerprint(candidate)
        except (ValueError, RuntimeError):
            continue
        if identifier in replayed_ids or identifier in excluded_ids or plan in validated_plans:
            continue
        if candidate.get("boundary_plateau") is True or candidate.get("offline_plateau") is True:
            continue
        candidate["candidate_pool_binding"] = {
            "candidate_id": identifier,
            "candidate_rank": int(raw.get("rank", source_rank)),
            "source_contract": "ranking_analysis.json.ranked_candidates",
            "source_path": str(ranking_path),
            "source_sha256": ranking_sha,
        }
        candidate["predicted_rank"] = int(raw.get("rank", source_rank))
        candidate["initial_params"] = copy.deepcopy(state["incumbent_params"])
        candidate["initial_score"] = finite(state["incumbent_score"], "state.incumbent_score")
        candidate["calibrated_candidate_score"] = finite(
            candidate.get("candidate_score"), "probe candidate_score"
        )
        candidate["selection_score"] = candidate["calibrated_candidate_score"]
        candidate["selection_role"] = "probe"
        candidate["probe_kind"] = "unknown_exact_plan_regime"
        candidate["selection_reason"] = "uncertain_probe_unknown_exact_plan_regime"
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
        candidate["incumbent_update_eligible"] = False
        probes.append(candidate)
        if len(probes) == 2:
            break
    if len(probes) != 2:
        raise ValueError("fewer than two unknown-regime non-corner probes remain")

    for candidate in probes:
        candidate["rank"] = len(selected) + 1
        selected.append(candidate)
    identifiers = [fingerprint(item)[0] for item in selected]
    if len(set(identifiers)) != 5:
        raise ValueError("Top-5 fingerprints are not mutually distinct")

    selection = {
        "selection_contract": SELECTION_CONTRACT,
        "calibration_contract": "job_exact_validated_plan_regime_calibration_v1",
        "replay_contract": REPLAY_CONTRACT,
        "diagnostic_only": True,
        "diagnostic_role_evaluation_only": True,
        "formal_update_flags": expected_flags,
        "probe_updates_gradient": False,
        "probe_updates_incumbent": False,
        "probe_updates_range": False,
        "probe_updates_accepted": False,
        "qps_used_for_inner_scoring": False,
        "trusted_count": 3,
        "probe_count": 2,
        "top5": selected,
        "ranked_candidates": copy.deepcopy(selected),
    }
    validate_selection(selection)
    write_json(args.output.resolve(), selection)

    calibration = {
        "contract": "job_exact_validated_plan_regime_calibration_v1",
        "ranking_path": str(ranking_path),
        "ranking_sha256": ranking_sha,
        "previous_selection_path": str(previous_selection_path),
        "previous_selection_sha256": sha256_file(previous_selection_path),
        "replay_summary_path": str(replay_summary_path),
        "replay_summary_sha256": sha256_file(replay_summary_path),
        "state_path": str(state_path),
        "state_sha256": sha256_file(state_path),
        "exclusion_path": str(exclusion_path),
        "exclusion_sha256": sha256_file(exclusion_path),
        "validated_regime_count": len(calibration_rows),
        "rows": calibration_rows,
        "trusted_fingerprint_ids": sorted(trusted_ids),
        "probe_fingerprint_ids": [fingerprint(item)[0] for item in probes],
        "top5_fingerprint_ids": identifiers,
        "formal_update_flags": expected_flags,
        "qps_used_for_inner_scoring": False,
    }
    write_json(args.calibration_output.resolve(), calibration)
    return {
        "selection_path": str(args.output.resolve()),
        "selection_sha256": sha256_file(args.output.resolve()),
        "calibration_path": str(args.calibration_output.resolve()),
        "calibration_sha256": sha256_file(args.calibration_output.resolve()),
        "trusted_source_ranks": [row["source_rank"] for row in trusted_rows],
        "trusted_scores": [row["actual_score"] for row in trusted_rows],
        "probe_predicted_ranks": [item["predicted_rank"] for item in probes],
        "fingerprints": identifiers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--previous-selection", type=Path, required=True)
    parser.add_argument("--replay-summary", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args), indent=2, sort_keys=True), flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
