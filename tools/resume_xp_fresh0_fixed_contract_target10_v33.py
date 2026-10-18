#!/usr/bin/env python3
"""Launch or resume the XP v33 plan-quarantined algorithm."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


RESOURCES = ("cpu", "io", "memory")


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    output = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and math.isclose(
            values[order[cursor]], values[order[end]], rel_tol=0.0, abs_tol=1e-12
        ):
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index in order[cursor:end]:
            output[index] = rank
        cursor = end
    return output


def spearman(predicted: list[float], actual: list[float]) -> float | None:
    left = ranks(predicted)
    right = ranks(actual)
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left[index] - left_mean) * (right[index] - right_mean)
        for index in range(len(left))
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return numerator / (left_norm * right_norm)


def gate(selection: Mapping[str, Any], replay: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = selection.get("top5") or []
    successful = replay.get("successful") or []
    if len(candidates) != 5 or len(successful) != 5:
        raise RuntimeError("XP seed gate requires Top-5 clean5 evidence")
    if (
        int(replay.get("physical_workload_invocation_count", -1)) != 5
        or int(replay.get("successful_real_replay_count", -1)) != 5
        or int(replay.get("technical_failure_count", -1)) != 0
        or replay.get("baseline_restore_verified") is not True
    ):
        raise RuntimeError("XP seed replay is not clean5 with baseline restored")
    for field in (
        "accepted_update_applied",
        "gradient_update_applied",
        "incumbent_updated",
        "range_updated",
        "state_mutations_applied_by_this_bundle",
    ):
        if replay.get(field) is not False:
            raise RuntimeError(f"XP seed mutated formal state: {field}")

    observations = {
        str(item.get("fingerprint_id") or ""): item for item in successful
    }
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in candidates
    ]
    if len(set(identifiers)) != 5 or "" in identifiers:
        raise RuntimeError("XP seed fingerprints are incomplete or duplicated")
    rows = []
    for candidate, identifier in zip(candidates, identifiers):
        observation = observations.get(identifier)
        if observation is None:
            raise RuntimeError(f"XP seed observation missing: {identifier}")
        predicted_score = float(candidate["candidate_score"])
        actual_score = float(observation["actual_score"])
        factors = {
            name: max(
                float(candidate["predicted_resource"][name])
                / float(observation["actual_resource"][name]),
                float(observation["actual_resource"][name])
                / float(candidate["predicted_resource"][name]),
            )
            for name in RESOURCES
        }
        rows.append(
            {
                "role": str(candidate.get("selection_role") or ""),
                "predicted_score": predicted_score,
                "actual_score": actual_score,
                "absolute_score_error": abs(actual_score - predicted_score),
                "resource_factor_error": factors,
            }
        )
    trusted = [item for item in rows if item["role"].startswith("trusted")]
    probes = [item for item in rows if "probe" in item["role"]]
    trusted_rho = spearman(
        [item["predicted_score"] for item in trusted],
        [item["actual_score"] for item in trusted],
    )
    score_mae = sum(item["absolute_score_error"] for item in rows) / len(rows)
    noise_bound = 2.0 * score_mae
    score_span = max(item["predicted_score"] for item in rows) - min(
        item["predicted_score"] for item in rows
    )
    actual_best_predicted_rank = max(
        range(len(rows)), key=lambda index: rows[index]["actual_score"]
    ) + 1
    maximum_resource_factor = max(
        value for item in trusted for value in item["resource_factor_error"].values()
    )
    checks = {
        "five_unique_three_level_fingerprints": len(set(identifiers)) == 5,
        "trusted3_probe2": len(trusted) == 3 and len(probes) == 2,
        "top5_no_technical_failure": replay.get("technical_failure_count") == 0,
        "trusted_only_spearman_positive": trusted_rho is not None and trusted_rho > 0.0,
        "actual_best_in_predicted_top3": actual_best_predicted_rank <= 3,
        "trusted_resource_no_order_of_magnitude_error": maximum_resource_factor < 10.0,
        "score_span_exceeds_replay_noise_bound": score_span > noise_bound,
        "baseline_restored": replay.get("baseline_restore_verified") is True,
    }
    contract_check_names = (
        "five_unique_three_level_fingerprints",
        "trusted3_probe2",
        "top5_no_technical_failure",
        "trusted_only_spearman_positive",
        "actual_best_in_predicted_top3",
        "trusted_resource_no_order_of_magnitude_error",
        "score_span_exceeds_replay_noise_bound",
        "baseline_restored",
    )
    return {
        "schema_version": "xp_fresh0_entry_gate_score_span_hard_v3",
        "passed": all(checks[name] for name in contract_check_names),
        "checks": checks,
        "contract_check_names": list(contract_check_names),
        "audit_only_checks": [],
        "trusted_only_spearman": trusted_rho,
        "actual_best_predicted_rank": actual_best_predicted_rank,
        "score_mae": score_mae,
        "score_noise_bound_2mae": noise_bound,
        "predicted_score_span": score_span,
        "maximum_trusted_resource_factor_error": maximum_resource_factor,
        "selection_sha256": sha256(args_global.selection),
        "replay_sha256": sha256(args_global.replay),
    }


def training_pair_gate(
    selection: Mapping[str, Any], replay: Mapping[str, Any]
) -> Dict[str, Any]:
    """Validate a prior clean diagnostic as training/audit evidence only."""
    candidates = list(selection.get("top5") or [])
    successful = list(replay.get("successful") or [])
    selected_ids = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in candidates
    }
    observed_ids = {
        str(item.get("fingerprint_id") or "") for item in successful
    }
    checks = {
        "five_unique_noncorner_fingerprints": bool(
            len(candidates) == 5
            and len(selected_ids) == 5
            and "" not in selected_ids
            and all(item.get("boundary_corner") is False for item in candidates)
        ),
        "five_clean_replays": bool(
            len(successful) == 5
            and int(replay.get("physical_workload_invocation_count", -1)) == 5
            and int(replay.get("successful_real_replay_count", -1)) == 5
            and int(replay.get("technical_failure_count", -1)) == 0
            and observed_ids == selected_ids
        ),
        "baseline_restored": replay.get("baseline_restore_verified") is True,
        "state_neutral": all(
            replay.get(field) is False
            for field in (
                "accepted_update_applied",
                "gradient_update_applied",
                "incumbent_updated",
                "range_updated",
                "state_mutations_applied_by_this_bundle",
            )
        ),
    }
    return {
        "contract": "xp_v33_prior_diagnostic_training_only_gate_v1",
        "passed": all(checks.values()),
        "checks": checks,
    }


def proven_bad_plan_regimes(
    evidence_pairs: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]],
    incumbent_score: float,
    *,
    baseline_plan_level: str = "",
    protocol_environment_signature_id: str = "",
) -> Dict[str, Any]:
    """Quarantine bad plans, but keep baseline-plan failures parameter-local."""
    from xp_v33_runtime_regime import (  # type: ignore
        exact_parameter_fingerprint,
        local_regime_id,
    )

    records = []
    quarantined = set()
    quarantined_params = set()
    quarantined_local = set()
    for source_label, selection, replay in evidence_pairs:
        gate_result = training_pair_gate(selection, replay)
        if gate_result["passed"] is not True:
            raise RuntimeError(
                f"unclean plan quarantine evidence: {source_label}"
            )
        candidates = {
            str((item.get("three_level_fingerprint") or {}).get("id") or ""):
            item
            for item in selection.get("top5") or []
        }
        for observation in replay.get("successful") or []:
            identifier = str(observation.get("fingerprint_id") or "")
            candidate = candidates.get(identifier)
            if candidate is None:
                raise RuntimeError(
                    f"quarantine candidate missing: {source_label}:{identifier}"
                )
            selected_plan = str(
                (candidate.get("three_level_fingerprint") or {}).get(
                    "plan_level"
                )
                or ""
            )
            observed_plan = str(
                observation.get("workload_plan_fingerprint") or ""
            )
            if not selected_plan or observed_plan != selected_plan:
                raise RuntimeError(
                    "quarantine requires exact selected/workload plan match: "
                    f"{source_label}:{identifier}"
                )
            actual_score = float(observation["actual_score"])
            score_gap = float(incumbent_score) - actual_score
            utilization = observation.get("actual_utilization") or {}
            cpu = float(utilization["cpu"])
            io = float(utilization["io"])
            resource_failure = cpu >= 0.95 or io >= 1.05
            strong_failure = score_gap >= 10.0 and resource_failure
            parameter_fingerprint = exact_parameter_fingerprint(
                candidate.get("candidate_params")
                or candidate.get("optimized_params")
                or {}
            )
            baseline_plan_blind_spot = bool(
                strong_failure and selected_plan == baseline_plan_level
            )
            quarantine = bool(strong_failure and not baseline_plan_blind_spot)
            local_id = ""
            if baseline_plan_blind_spot:
                local_id = local_regime_id(
                    selected_plan,
                    parameter_fingerprint,
                    protocol_environment_signature_id,
                )
                quarantined_params.add(parameter_fingerprint)
                quarantined_local.add(local_id)
                reason = (
                    "baseline_plan_fingerprint_blind_spot_parameter_local_"
                    "quarantine"
                )
            elif quarantine:
                reason = (
                    "clean_exact_plan_score_gap_ge_10_and_cpu_ge_095_or_io_ge_105"
                )
            else:
                reason = "not_proven_bad_under_strong_quarantine_gate"
            records.append(
                {
                    "source": source_label,
                    "fingerprint_id": identifier,
                    "plan_level": selected_plan,
                    "exact_parameter_fingerprint": parameter_fingerprint,
                    "protocol_environment_signature_id": (
                        protocol_environment_signature_id
                    ),
                    "local_regime_id": local_id,
                    "actual_score": actual_score,
                    "incumbent_score": float(incumbent_score),
                    "score_gap": score_gap,
                    "actual_utilization": {
                        "cpu": cpu,
                        "io": io,
                        "memory": float(utilization["memory"]),
                    },
                    "quarantined": quarantine,
                    "parameter_local_quarantined": baseline_plan_blind_spot,
                    "whole_baseline_plan_quarantine_forbidden": bool(
                        selected_plan == baseline_plan_level
                    ),
                    "reason": reason,
                }
            )
            if quarantine:
                quarantined.add(selected_plan)
    return {
        "contract": "xp_v33_clean_exact_plan_quality_quarantine_v1",
        "score_gap_minimum": 10.0,
        "resource_gate": "cpu>=0.95 or io>=1.05",
        "quarantined_plan_regimes": sorted(quarantined),
        "quarantined_plan_regime_count": len(quarantined),
        "quarantined_exact_parameter_fingerprints": sorted(quarantined_params),
        "quarantined_local_regimes": sorted(quarantined_local),
        "baseline_plan_level": baseline_plan_level,
        "baseline_plan_quarantined": baseline_plan_level in quarantined,
        "protocol_environment_signature_id": protocol_environment_signature_id,
        "records": records,
        "formal_state_mutation": False,
        "score_admission_eligible": False,
    }


def run_fresh_runtime_baseline_bootstrap(
    *,
    bundle: Path,
    campaign: Path,
    state_store: Any,
    config: Path,
    accepted_incumbent: Path,
    formal_state: Path,
    query_snapshot: Path,
    restore_script: Path,
) -> Dict[str, Any]:
    """Run one exact-default diagnostic and replace only campaign R0 authority."""
    state = state_store.load()
    existing = state.get("fresh_runtime_baseline_revalidation") or {}
    if existing.get("status") == "complete":
        return state
    output_dir = campaign / "batches" / "r0fresh000"
    candidate_path = output_dir / "selection.json"
    replay_dir = output_dir / "replay"
    summary_path = replay_dir / "summary.json"
    if output_dir.exists() and replay_dir.exists() and not summary_path.is_file():
        raise RuntimeError(
            "partial fresh R0 replay has uncertain physical-call state; "
            f"fail closed without a new call: {replay_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    builder = (
        bundle
        / "safe_frontier_tools_v23"
        / "build_xp_incumbent_revalidation_candidate.py"
    )
    runner = (
        bundle
        / "safe_frontier_tools_v23"
        / "run_xp_incumbent_revalidation_once.py"
    )
    driver = bundle / "xp_legacy_replay_driver.py"
    build_command = [
        sys.executable,
        str(builder),
        "--bundle-root",
        str(bundle),
        "--config",
        str(config),
        "--selection",
        str(bundle / "authoritative_round9" / "selection.json"),
        "--accepted-incumbent",
        str(accepted_incumbent),
        "--query-snapshot",
        str(query_snapshot),
        "--formal-root",
        str(bundle),
        "--output",
        str(candidate_path),
    ]
    if not candidate_path.is_file():
        subprocess.run(build_command, check=True, timeout=900)
    candidate_sha = sha256(candidate_path)
    run_command = [
        sys.executable,
        str(runner),
        "--candidate",
        str(candidate_path),
        "--candidate-sha256",
        candidate_sha,
        "--accepted-incumbent",
        str(accepted_incumbent),
        "--accepted-incumbent-sha256",
        sha256(accepted_incumbent),
        "--formal-state",
        str(formal_state),
        "--formal-state-sha256",
        sha256(formal_state),
        "--config",
        str(config),
        "--config-sha256",
        sha256(config),
        "--legacy-driver",
        str(driver),
        "--legacy-driver-sha256",
        sha256(driver),
        "--output-dir",
        str(replay_dir),
    ]
    if not summary_path.is_file():
        completed = subprocess.run(run_command, timeout=1800)
        if completed.returncode != 0 or not summary_path.is_file():
            raise RuntimeError(
                "fresh exact-default R0 diagnostic failed; no candidate batch may run"
            )
    summary = load(summary_path)
    successful = summary.get("successful") or []
    if not (
        summary.get("contract")
        == "xp_v33_exact_default_runtime_revalidation_summary_v1"
        and summary.get("status") == "complete"
        and summary.get("candidate_sha256") == candidate_sha
        and int(summary.get("real_workload_invocation_count", -1)) == 1
        and int(summary.get("valid_replay_count", -1)) == 1
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_snapshot_restored") is True
        and summary.get("baseline_params_restored") is True
        and summary.get("baseline_seven_params_restored") is True
        and summary.get("formal_state_unchanged") is True
        and summary.get("accepted_incumbent_unchanged") is True
        and summary.get("summary_postcondition_verified") is True
        and len(successful) == 1
    ):
        raise RuntimeError("fresh exact-default R0 postcondition failed")
    observation = successful[0]
    candidate = load(candidate_path)["candidate"]
    exact_defaults = load(config)["initial_params"]
    if candidate.get("candidate_params") != exact_defaults:
        raise RuntimeError("fresh R0 did not measure exact PostgreSQL defaults")
    old_score = float(
        (state.get("initial_authority") or {})["old_sealed_baseline_score"]
    )
    current_score = float(observation["actual_score"])
    old_baseline_obsolete = abs(current_score - old_score) >= 10.0
    bootstrap_batch = {
        "batch_id": "r0fresh000",
        "mode": "fresh_runtime_baseline_revalidation",
        "status": "complete",
        "physical_calls": 1,
        "valid_replays": 1,
        "accepted": 0,
        "technical_failures": 0,
        "rejected": 0,
        "diagnostic_only": True,
        "formal_state_mutation": False,
        "candidate_sha256": candidate_sha,
        "summary_sha256": sha256(summary_path),
        "exact_parameter_fingerprint": observation[
            "exact_parameter_fingerprint"
        ],
        "protocol_environment_signature_id": observation[
            "protocol_environment_signature_id"
        ],
        "local_regime_id": observation["local_regime_id"],
    }
    state["incumbent_score"] = current_score
    state["incumbent_params"] = copy.deepcopy(exact_defaults)
    state["incumbent"] = {
        "source": "v33_fresh_runtime_exact_postgresql_defaults",
        "score": current_score,
        "params": copy.deepcopy(exact_defaults),
        "actual_resource": copy.deepcopy(observation["actual_resource"]),
        "actual_utilization": copy.deepcopy(observation["actual_utilization"]),
        "independent_actual_scores": [current_score],
        "exact_parameter_fingerprint": observation[
            "exact_parameter_fingerprint"
        ],
        "protocol_environment_signature_id": observation[
            "protocol_environment_signature_id"
        ],
        "local_regime_id": observation["local_regime_id"],
        "plan_level": observation["workload_plan_fingerprint"],
    }
    authority = copy.deepcopy(state.get("initial_authority") or {})
    authority.update(
        {
            "current_runtime_baseline_revalidated": True,
            "current_runtime_baseline_score": current_score,
            "old_sealed_baseline_score": old_score,
            "old_sealed_baseline_obsolete": old_baseline_obsolete,
            "old_sealed_baseline_role": "training_and_audit_only",
            "baseline_change_classification": (
                "protocol_or_environment_epoch_change_not_algorithm_regression"
                if old_baseline_obsolete
                else "same_runtime_score_epoch"
            ),
            "protocol_environment_signature": copy.deepcopy(
                observation["protocol_environment_signature"]
            ),
        }
    )
    state["initial_authority"] = authority
    state["fresh_runtime_baseline_revalidation"] = {
        "status": "complete",
        "physical_calls": 1,
        "accepted_added": 0,
        "candidate": str(candidate_path),
        "summary": str(summary_path),
        "old_baseline_obsolete": old_baseline_obsolete,
    }
    state["batch_history"] = list(state.get("batch_history") or []) + [
        bootstrap_batch
    ]
    state["last_batch"] = bootstrap_batch
    state["accepted_count"] = 0
    state["accepted_fingerprints"] = []
    state["gradient_state"]["update_count"] = 0
    state_store.commit(state)
    return state_store.load()


def main() -> int:
    global args_global
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--training-selection", type=Path, required=True)
    parser.add_argument("--training-replay", type=Path, required=True)
    parser.add_argument("--prior-training-selection", type=Path, required=True)
    parser.add_argument("--prior-training-replay", type=Path, required=True)
    parser.add_argument("--training-state", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--formal-manifest", type=Path, required=True)
    parser.add_argument("--authority-counter", type=Path, required=True)
    parser.add_argument("--authority-history", type=Path, required=True)
    parser.add_argument("--canonical-state", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--acceptance-manifest", type=Path, required=True)
    parser.add_argument("--query-snapshot", type=Path, required=True)
    parser.add_argument("--restore-script", type=Path, required=True)
    parser.add_argument(
        "--historical-accepted-excluded", type=int, required=True
    )
    parser.add_argument(
        "--initialize-only",
        action="store_true",
        help="seal and verify fresh0 state without candidate generation or workload",
    )
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="resume an existing fresh0 campaign without reinitializing state",
    )
    args_global = parser.parse_args()
    args = args_global

    bundle = args.bundle_root.resolve()
    sys.path.insert(0, str(bundle))
    from xp_unattended_adapters import (  # type: ignore
        FormalPaths,
        generate_selection,
        restore_baseline,
        run_batch,
    )
    from xp_unattended_continuation import (  # type: ignore
        AtomicStateStore,
        UnattendedContinuationController,
        enforce_process_cpuset,
        new_state,
    )

    enforce_process_cpuset("32-51,288-307")
    campaign = args.campaign_root.resolve()
    seed_dir = campaign / "batches" / "seed000"
    state_path = campaign / "state.json"
    store = AtomicStateStore(state_path)
    if campaign.exists():
        if args.initialize_only:
            raise RuntimeError("initialize-only requires a missing campaign")
        if not args.resume_existing:
            raise RuntimeError(f"fresh XP campaign already exists: {campaign}")
        if not state_path.is_file() or not (campaign / "entry_gate.json").is_file():
            raise RuntimeError("existing XP campaign lacks sealed state/entry gate")
        state = store.load()
        if (
            int(state.get("accepted_target", -1)) != 10
            or int(state.get("accepted_count", -1)) < 0
            or int(state.get("historical_accepted_excluded", -1)) < 0
            or len(state.get("batch_history") or []) < 1
            or not isinstance(state.get("quarantined_plan_regimes"), list)
            or not isinstance(state.get("plan_regime_quarantine_audit"), dict)
        ):
            raise RuntimeError("existing XP fresh0 state contract mismatch")
    else:
        if args.resume_existing:
            raise RuntimeError("cannot resume a missing XP campaign")
        seed_gate = gate(load(args.selection), load(args.replay))
        if seed_gate["passed"] is not True:
            raise RuntimeError(f"XP fresh0 entry gate failed: {seed_gate['checks']}")
        training_gate = training_pair_gate(
            load(args.training_selection), load(args.training_replay)
        )
        if training_gate["passed"] is not True:
            raise RuntimeError(
                "XP v33 primary diagnostic training gate failed: "
                f"{training_gate['checks']}"
            )
        prior_training_gate = training_pair_gate(
            load(args.prior_training_selection),
            load(args.prior_training_replay),
        )
        if prior_training_gate["passed"] is not True:
            raise RuntimeError(
                "XP v33 prior diagnostic training gate failed: "
                f"{prior_training_gate['checks']}"
            )
        training = load(args.training_state)
        quarantine_audit = {
            "contract": "xp_v33_baseline_plan_quarantine_deferred_v1",
            "status": "pending_exact_default_runtime_revalidation",
            "quarantined_plan_regimes": [],
            "quarantined_plan_regime_count": 0,
            "quarantined_exact_parameter_fingerprints": [],
            "quarantined_local_regimes": [],
            "formal_state_mutation": False,
            "score_admission_eligible": False,
        }
        seed_dir.mkdir(parents=True, exist_ok=False)
        seed_selection = load(args.training_selection)
        seed_selection.update(
            {
                "score_regime": "historical_pre_v33_training_only",
                "score_admission_eligible": False,
                "historical_scores_used_for_fresh_admission": False,
            }
        )
        seed_replay = load(args.training_replay)
        seed_replay.update(
            {
                "score_regime": "historical_pre_v33_training_only",
                "score_admission_eligible": False,
                "historical_scores_used_for_fresh_admission": False,
            }
        )
        (seed_dir / "selection.json").write_text(
            json.dumps(seed_selection, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (seed_dir / "replay_summary.json").write_text(
            json.dumps(seed_replay, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (campaign / "entry_gate.json").write_text(
            json.dumps(
                {
                    "formal_entry_training_gate": seed_gate,
                    "prior_diagnostic_training_gate": training_gate,
                    "older_diagnostic_training_gate": prior_training_gate,
                    "plan_regime_quarantine_audit": quarantine_audit,
                    "passed": True,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if not (
            int(training.get("accepted_count", -1)) == 0
            and int(training.get("accepted_target", -1)) == 10
            and int(training.get("historical_accepted_excluded", -1))
            == args.historical_accepted_excluded
            and args.historical_accepted_excluded == 4
            and training.get("status") == "sealed_fresh0_initial_authority"
            and (training.get("initial_authority") or {}).get(
                "old_accepted4_state_used_for_incumbent_or_ranges"
            )
            is False
            and (training.get("initial_authority") or {}).get(
                "historical_scores_formal_acceptance_eligible"
            )
            is False
            and (training.get("initial_authority") or {}).get(
                "fresh_score_regime"
            )
            == "xp_v33_fresh0_band_target_v1"
        ):
            raise RuntimeError("XP v33 fresh0 initial authority is not clean")
        state = new_state(accepted_target=10)
        for field in (
            "incumbent",
            "incumbent_score",
            "incumbent_params",
            "parameter_ranges",
            "range_state",
            "gradient_state",
            "initial_authority",
        ):
            state[field] = copy.deepcopy(training[field])
        seed_ids = [
            str(item["fingerprint_id"])
            for item in load(args.training_replay)["successful"]
        ]
        prior_seed_ids = [
            str(item["fingerprint_id"])
            for item in load(args.prior_training_replay)["successful"]
        ]
        seed_batch = {
            "batch_id": "seed000",
            "mode": "historical_training_seed",
            "status": "complete",
            "physical_calls": 5,
            "accepted": 0,
            "technical_failures": 0,
            "rejected": 0,
            "fingerprint_digest": hashlib.sha256(
                json.dumps(sorted(seed_ids), separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "training_seed_only": True,
            "prior_diagnostic_resource_and_plan_training": True,
            "score_regime": "historical_pre_v33_training_only",
            "score_admission_eligible": False,
            "historical_scores_used_for_fresh_admission": False,
        }
        prior_seed_batch = {
            "batch_id": "priorseed000",
            "mode": "historical_training_seed",
            "status": "complete",
            "physical_calls": 5,
            "accepted": 0,
            "technical_failures": 0,
            "rejected": 0,
            "fingerprint_digest": hashlib.sha256(
                json.dumps(
                    sorted(prior_seed_ids), separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
            "training_seed_only": True,
            "prior_diagnostic_resource_and_plan_training": True,
            "score_regime": "historical_pre_v33_training_only",
            "score_admission_eligible": False,
            "historical_scores_used_for_fresh_admission": False,
        }
        state.update(
            {
                "accepted_count": 0,
                "accepted_fingerprints": [],
                "replayed_fingerprints": sorted(
                    set(seed_ids) | set(prior_seed_ids)
                ),
                "batch_history": [seed_batch, prior_seed_batch],
                "last_batch": prior_seed_batch,
                "next_batch_index": 1,
                "status": "initialized_fresh0",
                "historical_accepted_excluded": int(
                    args.historical_accepted_excluded
                ),
                "training_state_sha256": sha256(args.training_state),
                "entry_gate_sha256": sha256(campaign / "entry_gate.json"),
                "quarantined_plan_regimes": quarantine_audit[
                    "quarantined_plan_regimes"
                ],
                "quarantined_exact_parameter_fingerprints": [],
                "quarantined_local_regimes": [],
                "plan_regime_quarantine_audit": quarantine_audit,
            }
        )
        store.commit(state)

        if args.initialize_only:
            sealed = store.load()
            if not (
                sealed.get("accepted_count") == 0
                and sealed.get("accepted_target") == 10
                and sealed.get("historical_accepted_excluded") == 4
                and (sealed.get("gradient_state") or {}).get("update_count") == 0
                and (sealed.get("initial_authority") or {}).get(
                    "old_accepted4_state_used_for_incumbent_or_ranges"
                )
                is False
            ):
                raise RuntimeError("fresh0 initialize-only postcondition failed")
            print(
                json.dumps(
                    {
                        "status": "fresh0_initialized_without_workload",
                        "accepted_count": 0,
                        "historical_accepted_excluded": 4,
                        "campaign_root": str(campaign),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0

    paths = FormalPaths(
        root=bundle,
        state=state_path,
        batch_root=campaign / "batches",
        formal_manifest=args.formal_manifest.resolve(),
        authority_counter=args.authority_counter.resolve(),
        authority_history=args.authority_history.resolve(),
        canonical_state=args.canonical_state.resolve(),
        canonical_manifest=args.canonical_manifest.resolve(),
        acceptance_manifest=args.acceptance_manifest.resolve(),
        query_snapshot=args.query_snapshot.resolve(),
        replay_audit=(seed_dir / "replay_summary.json").resolve(),
        rank1_evidence=(seed_dir / "selection.json").resolve(),
        restore_script=args.restore_script.resolve(),
    )
    paths.validate()
    state = run_fresh_runtime_baseline_bootstrap(
        bundle=bundle,
        campaign=campaign,
        state_store=store,
        config=paths.config,
        accepted_incumbent=paths.incumbent,
        formal_state=args.canonical_state.resolve(),
        query_snapshot=paths.query_snapshot,
        restore_script=paths.restore_script,
    )
    if (
        (state.get("plan_regime_quarantine_audit") or {}).get("status")
        == "pending_exact_default_runtime_revalidation"
    ):
        incumbent = state.get("incumbent") or {}
        quarantine_audit = proven_bad_plan_regimes(
            [
                (
                    "primary_training_only",
                    load(args.training_selection),
                    load(args.training_replay),
                ),
                (
                    "prior_training_only",
                    load(args.prior_training_selection),
                    load(args.prior_training_replay),
                ),
            ],
            float(state["incumbent_score"]),
            baseline_plan_level=str(incumbent.get("plan_level") or ""),
            protocol_environment_signature_id=str(
                incumbent.get("protocol_environment_signature_id") or ""
            ),
        )
        state["quarantined_plan_regimes"] = quarantine_audit[
            "quarantined_plan_regimes"
        ]
        state["quarantined_exact_parameter_fingerprints"] = (
            quarantine_audit["quarantined_exact_parameter_fingerprints"]
        )
        state["quarantined_local_regimes"] = quarantine_audit[
            "quarantined_local_regimes"
        ]
        state["plan_regime_quarantine_audit"] = quarantine_audit
        state["status"] = "initialized_fresh0_runtime_anchor_ready"
        entry_gate_path = campaign / "entry_gate.json"
        entry_gate = load(entry_gate_path)
        entry_gate["plan_regime_quarantine_audit"] = quarantine_audit
        entry_gate["fresh_runtime_baseline_revalidation"] = copy.deepcopy(
            state["fresh_runtime_baseline_revalidation"]
        )
        temporary_entry = entry_gate_path.with_suffix(".json.tmp")
        temporary_entry.write_text(
            json.dumps(entry_gate, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_entry, entry_gate_path)
        state["entry_gate_sha256"] = sha256(entry_gate_path)
        store.commit(state)
    controller = UnattendedContinuationController(
        state_store=store,
        batch_root=campaign / "batches",
        candidate_factory=lambda batch_id, current, batch_dir: generate_selection(
            paths, batch_id, current, batch_dir
        ),
        batch_runner=lambda selection, batch_dir, environment: run_batch(
            paths, selection, batch_dir, environment
        ),
        baseline_restore=lambda batch_dir, phase: restore_baseline(
            paths, batch_dir, phase
        ),
    )
    final_state = controller.run()
    print(
        json.dumps(
            {
                "status": final_state["status"],
                "accepted_count": final_state["accepted_count"],
                "accepted_target": final_state["accepted_target"],
                "campaign_root": str(campaign),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if final_state["status"] == "completed" else 2


args_global: argparse.Namespace


if __name__ == "__main__":
    raise SystemExit(main())
